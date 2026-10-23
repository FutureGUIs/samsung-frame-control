"""Serialized local TV control: Art/TV mode, inputs, volume, power-off worker."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import socket
import sys
import time
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path

from . import upnp
from .art_controls import STEP_KEYS, ArtControls
from .async_art import ArtError, AsyncArtClient
from .channels import ChannelListError, fetch_lineup
from .const import DEFAULT_BROADCAST, INPUTS, PORT, REPORTED_INPUTS
from .ipcontrol import IpcError
from .power import read_power
from .remote import AsyncRemoteClient

WORKER = Path(__file__).with_name("worker.py")
MODE_STARTUP_TIMEOUT = 75
MODE_ATTEMPT_TIMEOUT = 20
KEY_TIMEOUT = 20
KEY_ATTEMPTS = 3
# After leaving Art Mode the TV needs a moment before it accepts input keys.
INPUT_SETTLE = 2
# After IP Control powerOff the TV stays reachable; wake it explicitly.
IPC_WAKE_CHECKS = 20
IPC_WAKE_DELAY = 1.5
# Art/TV mode through IP Control (no Art app needed): checks, 1 s apart.
IPC_MODE_CHECKS = 8
IPC_MODE_DELAY = 1.0
INPUT_CHECKS = 4
INPUT_CHECK_DELAY = 1.5
AUDIO_READBACK_ATTEMPTS = 3
CHANNEL_READBACK = 4  # IP Control reads, 1 s apart, after a channel key
# Typed channel numbers, measured on a QN55LS03T (tools/frame_probe.py
# time-channel): digits 0.3 s apart or less lost a digit in 4 of 5 tries;
# 0.5 s apart plus Enter tuned 0.35 s after Enter (3.7 s in total).
DIGIT_GAP = 0.6
TUNE_POLL = 0.25  # seconds between IP Control reads while the TV tunes
TUNE_WAIT = 4.0  # seconds to wait for the new channel per entry
TUNE_ENTRIES = 2  # a finished entry that tuned the wrong channel is retyped once
THUMBNAIL_CACHE = 8  # artwork thumbnails kept (about 30 KB each)
ARTWORK_WRITES = ("random_artwork", *STEP_KEYS)
_LOGGER = logging.getLogger(__name__)


class FrameError(Exception):
    """A command failed, or its delivery could not be established."""

    def __init__(
        self, code: str = "cannot_connect", *, stage="unknown", kind="", sent=False
    ):
        self.code = code
        self.stage, self.kind, self.sent = stage, kind, sent
        super().__init__(code)


def normalize_mac(value: str) -> str:
    compact = re.sub(r"[:.\-]", "", value.strip())
    if not re.fullmatch(r"[0-9a-fA-F]{12}", compact):
        raise ValueError("Invalid MAC address")
    return ":".join(compact[i : i + 2].lower() for i in range(0, 12, 2))


def send_magic_packet(mac: str, broadcast: str) -> None:
    """Called in HA's executor; never blocks the event loop."""
    packet = b"\xff" * 6 + bytes.fromhex(normalize_mac(mac).replace(":", "")) * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(2)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (broadcast, 9))


async def port_open(host: str, port: int = PORT) -> bool:
    writer = None
    try:
        async with asyncio.timeout(2):
            _, writer = await asyncio.open_connection(host, port)
        return True
    except (OSError, TimeoutError):
        return False
    finally:
        if writer is not None:
            writer.close()
            try:
                async with asyncio.timeout(1):
                    await writer.wait_closed()
            except (OSError, TimeoutError):
                pass


async def run_worker(
    host: str,
    token: str | None,
    action: str,
    on_token: Callable[[str], None],
    *,
    worker_path=WORKER,
    operation_timeout: float = 20,
    on_info: Callable[[dict], None] | None = None,
    response_timeout: float = 3,
) -> bool:
    """Return False only for a sent mode command with no response before timeout.

    The three-second grace period starts AFTER actual send, never before pairing
    or handshake. Every exit path kills/reaps the process if necessary.
    """
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-u",
        str(worker_path),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    sent = False
    stage = "worker_start"
    try:
        payload = dict(host=host, token=token, action=action, python_path=sys.path)
        process.stdin.write((json.dumps(payload) + "\n").encode())
        await process.stdin.drain()
        process.stdin.close()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + operation_timeout
        while True:
            try:
                async with asyncio.timeout_at(deadline):
                    line = await process.stdout.readline()
            except TimeoutError as err:
                if action in ("art", "tv") and sent:
                    return False
                raise FrameError("timeout", stage=stage) from err
            if not line:
                raise FrameError("worker_failed", stage=stage, sent=sent)
            try:
                event = json.loads(line)
            except (ValueError, TypeError) as err:
                raise FrameError("worker_failed") from err
            if event["event"] == "token":
                on_token(str(event["token"]))
            elif event["event"] == "phase":
                stage = event["phase"]
            elif event["event"] == "device_info" and on_info is not None:
                on_info(event["data"])
            elif event["event"] == "art_sent" and action in ("art", "tv") and not sent:
                sent = True
                stage = "mode_response"
                deadline = loop.time() + response_timeout
            elif event["event"] == "done":
                return True
            elif event["event"] == "error":
                raise FrameError(
                    event.get("code", "cannot_connect"),
                    stage=stage,
                    kind=event.get("kind", ""),
                    sent=sent,
                )
    finally:
        if process.returncode is None:
            try:
                # Permit a normal close, but never wait indefinitely for it.
                async with asyncio.timeout(0.5):
                    await process.wait()
            except (TimeoutError, asyncio.CancelledError) as err:
                if process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                await process.wait()
                if isinstance(err, asyncio.CancelledError):
                    raise


class FrameController:
    """Serialize commands per TV; only mode buttons may send Wake-on-LAN."""

    def __init__(
        self,
        host,
        mac,
        token,
        on_token,
        executor,
        broadcast=DEFAULT_BROADCAST,
        *,
        session=None,
        ipc=None,
    ):
        self.host, self.mac, self.token = host, mac, token
        self.broadcast = broadcast
        self._on_token, self._executor = on_token, executor
        self._lock = asyncio.Lock()
        self._active = None
        self._closed = False
        self._session = session
        self._last_art_refresh = 0
        self.art = AsyncArtClient(session, host, token, self._save_token, self._event)
        self.remote = AsyncRemoteClient(session, host, token, self._save_token)
        self.controls = ArtControls(self.art)
        self.on_update = None
        # Locally observable audio (UPnP); None means not currently read.
        self._audio_lock = asyncio.Lock()
        self.volume = None
        self.muted = None
        # Last display mode ("art"/"tv"), observed or commanded, used to turn
        # the TV back on the way it was. Restored by the media player entity.
        self.last_mode = None
        # Last input id ("tv", "hdmi1"...): observed via IP Control when paired,
        # otherwise the last one selected through HA.
        self.last_input = None
        # IP Control (optional, paired separately). Read-only here.
        self.ipc = ipc
        self.ipc_error = None
        self.ipc_auth_failed = False
        self.input_reported = None  # raw getTVStates.inputSource
        self.ipc_input_setter = None  # inputSourceControl support, once known
        self._mode_ipc_power = None
        self.ipc_art_setter = None  # artModeControl setter support, once known
        # User commands waiting for the lock; a running background refresh
        # stops at its next step instead of making them wait.
        self._waiting_commands = 0
        self.last_command = {}
        # Live progress of the running command, published as it changes.
        self.activity = "idle"
        self.input_confirmed = False  # True only when read in the latest poll
        self._ipc_states = {}
        self.channel = None  # channel number on the TV source, via IP Control
        # Built-in Samsung TV Plus line-up (channels.load_channels), set at setup.
        self.channel_list = {"lineup": None, "effective": None, "channels": {}}
        # Downloaded line-up (opt-in), its country, and where it is saved.
        self.channel_download = False
        self.channel_region = "US"
        self.channel_lineup = None
        self.channel_store = None
        self.channel_list_error = None
        self._channel_lock = asyncio.Lock()
        # The TV explicitly reported (or HA commanded) power-off since it was
        # last seen on. Only then does a refused connection mean "standby".
        self.off_reported = False
        # Artwork thumbnails for the media player picture, newest last.
        self._thumbnails = OrderedDict()  # content_id -> (bytes, content type)
        self._thumbnail_failed = {}  # content_id -> (monotonic time, retry after)
        self._thumbnail_lock = asyncio.Lock()
        self.thumbnail_status = {}

    def _event(self, data):
        self.controls.push(data)
        if self.controls.power_state == "off":
            self.off_reported = True
            self.input_confirmed = False
        if self.on_update:
            self.on_update()

    async def _send_mode(self, action):
        try:
            return await self.art.set_mode(action == "art")
        except ArtError as err:
            raise FrameError(err.code, stage="async_art", sent=err.sent) from None

    def _save_token(self, token):
        self.token = token
        self.art.token = token
        self.remote.token = token
        self._on_token(token)

    async def _read_ipc(self):
        """Return the IP Control power report, or None when unavailable.

        Also records the observed input and audio snapshot. Errors only make
        the input unconfirmed (the last value is kept, e.g. during the ~20 s
        after wake when IP Control starts later than the other services).
        """
        self.input_confirmed = False
        if self.ipc is None or self.ipc_auth_failed:
            return None
        try:
            power, states = await self._executor(self.ipc.read_state)
        except IpcError as err:
            self.ipc_error = err.code
            if err.code == "unauthorized":
                # Stop polling with a rejected token; a repair issue asks to re-pair.
                self.ipc_auth_failed = True
            return None
        self.ipc_error = None
        if power not in ("powerOn", "powerOff"):
            return None
        self._ipc_states = states
        channel = states.get("channel") if power == "powerOn" else None
        self.channel = channel.get("number") if isinstance(channel, dict) else None
        if power == "powerOn" and states.get("artMode") in ("on", "off"):
            self._apply_ipc_art_mode(states["artMode"])
        reported = states.get("inputSource")
        if power == "powerOn" and isinstance(reported, str) and reported:
            self.input_reported = reported
            self.input_confirmed = True
            if reported in REPORTED_INPUTS:
                self.last_input = REPORTED_INPUTS[reported]
        return power

    def _apply_ipc_art_mode(self, mode):
        """Art Mode from the IP Control poll; catches remote-control changes."""
        controls = self.controls
        if controls.values.get("art_mode") == mode:
            return
        controls.supported.add("art_mode")
        if mode == "on":
            controls.values["art_mode"] = "on"
            # Re-read Art settings and artwork in this poll, not up to 60 s later.
            self._last_art_refresh = 0
        else:
            # Art settings are only valid in Art Mode.
            controls.values = {"art_mode": "off"}

    async def _observe_power(self):
        """Combine REST and IP Control power reports.

        Off requires an explicit report: REST/IP Control "off", the Art
        channel's standby event, or HA's own power-off. While the TV then does
        not answer, off is kept as the last known state. A TV last seen on that
        stops answering is unknown (network problem or no mains power).
        """
        state, source = await read_power(self._session, self.host)
        ipc_power = None
        if source in ("local_rest", "not_reported"):
            ipc_power = await self._read_ipc()
            if ipc_power == "powerOff":
                # Measured: REST still says "on" while the TV shuts down.
                state, source = "off", "ip_control"
            elif ipc_power == "powerOn" and state is None:
                state, source = "on", "ip_control"
        else:
            self.input_confirmed = False
            if self.off_reported:
                # Last known state was off. A refused connection is standby;
                # no answer at all (deep standby) keeps showing the last known
                # off rather than unknown.
                state = "off"
                source = "standby" if source == "refused" else "last_known"
            else:
                source = "unreachable"
        if state == "on":
            # REST keeps saying "on" while the TV shuts down, so with IP Control
            # paired only its own powerOn clears an explicit off report.
            if ipc_power == "powerOn" or self.ipc is None or self.ipc_auth_failed:
                self.off_reported = False
        elif state == "off":
            self.off_reported = True
        self.controls.set_power(state, source)
        return state, source

    def remember_mode(self):
        """Record an observed Art/TV mode so turn-on can restore it."""
        if self.controls.power_state != "on":
            return
        mode = self.controls.values.get("art_mode")
        if mode in ("on", "off"):
            self.last_mode = "art" if mode == "on" else "tv"

    def _mark_mode(self, action):
        if self._mode_ipc_power == "powerOn":
            # Confirmed with IP Control reporting powerOn: show it on now
            # instead of after the next poll.
            self.controls.power_state, self.controls.power_source = "on", "ip_control"
            self.off_reported = False
        self.controls.values["art_mode"] = "on" if action == "art" else "off"
        self.controls.online = True
        self.controls.supported.add("art_mode")
        self.last_mode = action
        self._last_art_refresh = 0

    async def _async_mode(self, action: str) -> bool:
        """Allow one press to span startup and reassert the requested mode once."""
        async with asyncio.timeout(MODE_STARTUP_TIMEOUT):
            ipc_power = await self._ipc_power()
            # Measured: after IP Control powerOff the TV keeps answering on the
            # network, so an open port alone does not mean it is on.
            waking = (
                ipc_power == "powerOff"
                or self.controls.power_state == "off"
                or not await port_open(self.host)
            )
            if waking:
                self._set_activity("waking")
                if ipc_power == "powerOff" and await self._ipc_wake():
                    self._step("ip_control_power_on")
                else:
                    async with asyncio.timeout(45):
                        for attempt in range(30):
                            if attempt % 3 == 0:
                                await self._executor(
                                    send_magic_packet, self.mac, self.broadcast
                                )
                            await asyncio.sleep(1)
                            if await port_open(self.host):
                                break
                        else:
                            raise FrameError("wake_failed")
                    self._step("wake_on_lan")
            # Measured on a QN55LS03T: after wake the Art channel took ~20 s
            # (Art app start-up), while IP Control answered within 3 s.
            if await self._ipc_set_mode(action):
                self._mode_ipc_power = "powerOn"
                self._step("ip_control_mode")
                return True
            if waking:
                # TCP readiness often precedes the Art app's startup.
                self._set_activity("starting_art_app")
                await asyncio.sleep(5)
            reasserted = False
            for attempt in range(4):
                failure = None
                if not reasserted or attempt == 0:
                    self._set_activity(
                        "switching_to_art" if action == "art" else "switching_to_tv"
                    )
                try:
                    # A single slow handshake/readback must not use the whole
                    # startup budget and prevent another wake attempt.
                    async with asyncio.timeout(MODE_ATTEMPT_TIMEOUT):
                        confirmed = await self._send_mode(action)
                except TimeoutError:
                    confirmed = False
                    failure = FrameError("timeout", stage="mode_attempt", sent=True)
                except FrameError as err:
                    if err.code not in ("cannot_connect", "timeout"):
                        raise
                    confirmed = False
                    failure = err
                self._mode_ipc_power = await self._ipc_power() if confirmed else None
                if self._mode_ipc_power == "powerOff":
                    # Samsung reports the remembered Art setting while the
                    # panel is off; that is not a confirmed mode.
                    confirmed = False
                if not confirmed:
                    _LOGGER.debug(
                        "Mode %s attempt %s unconfirmed (%s); reconnecting before retry",
                        action,
                        attempt + 1,
                        failure.code if failure else "readback",
                    )
                    await self.art.close()
                    if attempt == 3:
                        if failure:
                            raise failure
                        return False
                    # An open TCP port (or a delivered command) does not mean
                    # the TV is awake. Explicit Art ON/OFF is safe to repeat;
                    # the non-idempotent power-key hold never enters this path.
                    waking = True
                    reasserted = False
                    self._set_activity("retrying")
                    if await self._ipc_power() == "powerOff":
                        await self._ipc_wake()
                    for _ in range(3):
                        await self._executor(
                            send_magic_packet, self.mac, self.broadcast
                        )
                        await asyncio.sleep(1)
                    await asyncio.sleep(3)
                    continue
                # The TV may accept a command before it can apply the mode.
                # ON/OFF here explicitly sets Art Mode; neither is a power toggle.
                # With IP Control confirming powerOn the TV is demonstrably up;
                # the 5 s reassert is only for unverified cold starts.
                if (
                    waking
                    and not reasserted
                    and attempt < 3
                    and self._mode_ipc_power != "powerOn"
                ):
                    reasserted = True
                    self._set_activity("confirming_mode")
                    await asyncio.sleep(5)
                    continue
                return confirmed
        raise FrameError("wake_failed")

    async def async_command(self, action: str) -> bool:
        async with self._command(action):
            try:
                if action in ("art", "tv"):
                    await self._confirmed_mode(action)
                    return True
                elif action == "off":
                    if not await port_open(self.host):
                        raise FrameError("unreachable_off")
                    await self._close_connections()
                    self.controls.offline()
                    self.controls.set_power(None, "shutdown_requested")
                    self.off_reported = True
                    self.input_confirmed = False
                    self.clear_audio()
                    self._last_art_refresh = 0
                    self._set_activity("powering_off")
                    if await self._ipc_power_off():
                        self.last_command["path"] = "ip_control"
                        self._step("power_off_confirmed")
                        return True
                    self.last_command["path"] = "power_key"
                    # Never retry a power hold: delivery may already have occurred.
                    return await run_worker(
                        self.host, self.token, action, self._save_token
                    )
                else:
                    raise ValueError("Unknown action")
            except TimeoutError as err:
                raise FrameError("startup_timeout") from err
            finally:
                self._active = None

    async def _confirmed_mode(self, action):
        if not await self._async_mode(action):
            raise FrameError("unconfirmed", stage="mode_readback", sent=True)
        self._mark_mode(action)
        self._step(f"mode_{action}")

    async def _send_key(self, key):
        """Input keys are idempotent, so transient failures may be resent."""
        for attempt in range(KEY_ATTEMPTS):
            if attempt:
                await asyncio.sleep(2)
            try:
                async with asyncio.timeout(KEY_TIMEOUT):
                    await self.remote.send_key(key)
                return
            except TimeoutError:
                failure = FrameError("timeout", stage="input_key", sent=True)
            except ArtError as err:
                failure = FrameError(err.code, stage="input_key", sent=err.sent)
                if err.code not in ("cannot_connect", "timeout"):
                    raise failure from None
        raise failure

    def _ipc_usable(self):
        return self.ipc is not None and not self.ipc_auth_failed

    async def _ipc_call(self, method, params=None):
        """One IP Control call in the executor; marks a rejected token."""
        try:
            return await self._executor(self.ipc.call, method, params)
        except IpcError as err:
            self.ipc_error = err.code
            if err.code == "unauthorized":
                self.ipc_auth_failed = True
            raise

    async def _ipc_input(self):
        """The input the TV reports now, or None if IP Control cannot say."""
        try:
            reported = (await self._ipc_call("getTVStates")).get("inputSource")
        except IpcError:
            return None
        return reported if isinstance(reported, str) and reported else None

    async def _ipc_set_input(self, target):
        """Try IP Control's own input setter; False if unavailable or unsupported."""
        if self.ipc_input_setter is False:
            return False
        try:
            await self._ipc_call("inputSourceControl", {"inputSource": target})
        except IpcError as err:
            if err.code in ("unsupported", "failed"):
                # -32601 (2024 Frames) or -32002: switch with remote keys.
                self.ipc_input_setter = False
            return False
        self.ipc_input_setter = True
        return True

    async def _await_input(self, target):
        """True when the TV reports target; False when it reports another
        input; None when IP Control never answered (e.g. still starting)."""
        self._set_activity("checking_input")
        answered = False
        for attempt in range(INPUT_CHECKS):
            if attempt:
                await asyncio.sleep(INPUT_CHECK_DELAY)
            reported = await self._ipc_input()
            if reported == target:
                return True
            answered = answered or reported is not None
        return False if answered else None

    async def _switch_input(self, input_id, key):
        """Select an input; verified whenever IP Control is answering."""
        target = next(r for r, i in REPORTED_INPUTS.items() if i == input_id)
        if self._ipc_usable():
            if await self._ipc_input() == target:
                self._mark_input(target)
                return
            if await self._ipc_set_input(target) and await self._await_input(target):
                self._mark_input(target)
                return
            for _ in range(2):  # the key once, then one resend
                self._set_activity("switching_input")
                await self._send_key(key)
                result = await self._await_input(target)
                if result:
                    self._mark_input(target)
                    return
                if result is None:
                    break  # IP Control not answering: cannot verify
            else:
                # Show what the TV reports, not the input that was requested.
                actual = await self._ipc_input()
                if actual in REPORTED_INPUTS:
                    self._mark_input(actual)
                else:
                    self.input_confirmed = False
                raise FrameError("input_unconfirmed", stage="input_readback", sent=True)
            self.input_confirmed = False
            return
        await self._send_key(key)
        # Unverifiable without IP Control: shown as unconfirmed.
        self.input_confirmed = False

    @contextlib.asynccontextmanager
    async def _command(self, action):
        """Serialize a user command, pre-empting background refreshes."""
        start = time.monotonic()
        self._waiting_commands += 1
        try:
            await self._lock.acquire()
        finally:
            self._waiting_commands -= 1
        try:
            if self._closed:
                raise FrameError("unloaded")
            self._active = asyncio.current_task()
            self._command_start = start
            self.last_command = {
                "action": action,
                "waited_s": round(time.monotonic() - start, 1),
                "steps": {},
            }
            yield
        finally:
            self.last_command["took_s"] = round(time.monotonic() - start, 1)
            self._active = None
            self._lock.release()
            self._set_activity("idle")

    def _step(self, name):
        """Record when a command step finished, for diagnostics."""
        if self.last_command:
            self.last_command["steps"][name] = round(
                time.monotonic() - self._command_start, 1
            )

    def _set_activity(self, activity):
        if activity != self.activity:
            self.activity = activity
            if self.on_update:
                self.on_update()

    def _command_waiting(self):
        return self._waiting_commands > 0

    async def _ipc_power(self):
        """powerOn/powerOff from IP Control, or None when it cannot say."""
        if not self._ipc_usable():
            return None
        try:
            power = (await self._ipc_call("powerControl")).get("power")
        except IpcError:
            return None
        return power if power in ("powerOn", "powerOff") else None

    async def _ipc_set_mode(self, action):
        """Set Art Mode on/off through IP Control; True once confirmed.

        Explicit on/off (not a toggle), so it is safe to resend while the TV
        finishes starting. False falls back to the Art channel.
        """
        if not self._ipc_usable() or self.ipc_art_setter is False:
            return False
        want = "artModeOn" if action == "art" else "artModeOff"
        self._set_activity("switching_to_art" if action == "art" else "switching_to_tv")
        sent = False
        for attempt in range(IPC_MODE_CHECKS):
            if attempt:
                await asyncio.sleep(IPC_MODE_DELAY)
            try:
                current = (await self._ipc_call("artModeControl")).get("artMode")
                if current == want:
                    # The flag alone is not enough: an off TV keeps its setting.
                    if await self._ipc_power() == "powerOn":
                        if sent:
                            self.ipc_art_setter = True
                        return True
                    continue
                await self._ipc_call("artModeControl", {"artMode": want})
                sent = True
            except IpcError as err:
                if err.code == "unsupported":
                    self.ipc_art_setter = False
                    return False
                if err.code == "unauthorized":
                    return False
                # failed/timeout while the TV is still starting: try again
        return False

    async def _ipc_wake(self):
        """Explicit IP Control powerOn; True once the TV reports powerOn."""
        try:
            await self._ipc_call("powerControl", {"power": "powerOn"})
        except IpcError:
            return False
        for _ in range(IPC_WAKE_CHECKS):
            await asyncio.sleep(IPC_WAKE_DELAY)
            if await self._ipc_power() == "powerOn":
                return True
        return False

    async def _ipc_power_off(self):
        """Explicit power-off; True when IP Control accepted it.

        The power-key hold is a toggle: from Art Mode the TV first acts on the
        press (switching to TV) before the hold turns it off. IP Control's
        powerControl turns it off directly. Falls back (False) only when the
        command was not delivered; a timeout may have been delivered, so it
        must not be followed by a second, opposite-acting power key.
        """
        if not self._ipc_usable():
            return False
        try:
            await self._ipc_call("powerControl", {"power": "powerOff"})
        except IpcError as err:
            if err.code == "timeout":
                raise FrameError("timeout", stage="ip_power_off", sent=True) from None
            return False  # refused/rejected/unsupported: nothing was done
        try:
            power = (await self._ipc_call("powerControl")).get("power")
        except IpcError:
            power = None
        if power == "powerOff":
            self.controls.set_power("off", "ip_control")
        return True

    def _mark_input(self, reported):
        self.input_reported, self.input_confirmed = reported, True
        self.last_input = REPORTED_INPUTS[reported]

    async def async_select_input(self, input_id: str) -> None:
        """Leave Art Mode (waking if needed), then select an input."""
        key = INPUTS[input_id]
        async with self._command(f"select_{input_id}"):
            try:
                # A TV that is off wakes into its last mode, often Art Mode,
                # so its stored "off" Art state does not mean TV mode.
                leaving_art = (
                    self.controls.values.get("art_mode") != "off"
                    or self.controls.power_state != "on"
                )
                await self._confirmed_mode("tv")
                if leaving_art:
                    self._set_activity("settling")
                    await asyncio.sleep(INPUT_SETTLE)
                self._set_activity("switching_input")
                self.last_input = input_id
                await self._switch_input(input_id, key)
                self._step("input")
            except TimeoutError as err:
                raise FrameError("startup_timeout") from err
            finally:
                self._active = None

    async def async_send_key(self, key: str) -> None:
        """Send a remote key to a TV that is already on; never wakes it."""
        async with self._command(key):
            if self.controls.power_state == "off" or not await port_open(self.host):
                raise FrameError("tv_off", stage="input_key")
            self._set_activity("sending_key")
            await self._send_key(key)

    async def async_channel_step(self, direction: int) -> None:
        """Channel up/down with the remote keys (verified on a 2020 Frame).

        With IP Control the new channel number is read back right away.
        """
        key = "KEY_CHUP" if direction > 0 else "KEY_CHDOWN"
        async with self._command("channel_up" if direction > 0 else "channel_down"):
            if self.controls.power_state == "off" or not await port_open(self.host):
                raise FrameError("tv_off", stage="input_key")
            before = self.channel
            self._set_activity("sending_key")
            await self._send_key(key)
            if not self._ipc_usable():
                return
            for _ in range(CHANNEL_READBACK):
                await self._read_ipc()
                if self.channel != before:
                    return
                await asyncio.sleep(1)

    def on_tv_source(self) -> bool:
        """The TV shows the Samsung TV source, as read by IP Control this poll."""
        controls = self.controls
        return (
            controls.power_state == "on"
            and controls.values.get("art_mode") != "on"
            and self.input_confirmed
            and self.input_reported == "TV"
        )

    async def async_tune_channel(self, number: str) -> None:
        """Type a channel number on the Samsung TV source, then read it back.

        IP Control's channel setter is refused by a 2020 Frame (-32602);
        typed digits plus Enter tune. Needs IP Control to confirm.
        """
        number = str(number).strip()
        if not number.isdigit() or not 0 < len(number) <= 5:
            raise FrameError("invalid_value", stage="channel")
        async with self._command("channel"):
            if not self._ipc_usable():
                raise FrameError("needs_ip_control", stage="channel")
            if self.controls.power_state == "off" or not await port_open(self.host):
                raise FrameError("tv_off", stage="channel")
            await self._read_ipc()
            if not self.on_tv_source():
                raise FrameError("not_tv_source", stage="channel")
            if self.channel == number:
                return
            self._set_activity("changing_channel")
            keys = [f"KEY_{digit}" for digit in number] + ["KEY_ENTER"]
            for entry in range(TUNE_ENTRIES):
                # A retype uses a new connection, in case the kept one had
                # silently stopped delivering keys.
                await self._type_channel(keys, fresh=entry > 0)
                self._step("keys" if not entry else f"keys_{entry + 1}")
                waited = 0.0
                while waited < TUNE_WAIT:
                    await asyncio.sleep(TUNE_POLL)
                    waited += TUNE_POLL
                    await self._read_ipc()
                    if self.channel == number:
                        self._step("confirmed")
                        return
                # The entry is finished (Enter was sent), so a dropped digit
                # that tuned another channel can safely be typed again.
                if not self.on_tv_source():
                    break
            raise FrameError("channel_unconfirmed", stage="channel", sent=True)

    async def _type_channel(self, keys, fresh=False):
        """Type one full entry; a partly typed one is never resent."""
        for attempt in range(KEY_ATTEMPTS):
            if attempt:
                await asyncio.sleep(2)
            try:
                async with asyncio.timeout(KEY_TIMEOUT + len(keys)):
                    await self.remote.send_keys(keys, gap=DIGIT_GAP, fresh=fresh)
                return
            except (TimeoutError, ArtError) as err:
                code = getattr(err, "code", "timeout")
                sent = getattr(err, "sent", True)
                if sent or code not in ("cannot_connect", "timeout"):
                    raise FrameError(code, stage="channel", sent=sent) from None
                if attempt + 1 == KEY_ATTEMPTS:
                    raise FrameError(code, stage="channel") from None

    async def async_update_channel_list(self) -> None:
        """Download the chosen country's line-up from Samsung and save it.

        Talks to Samsung's website only, never to the TV; on failure the
        previous list stays in use.
        """
        async with self._channel_lock:
            try:
                lineup = await fetch_lineup(self._session, self.channel_region)
            except ChannelListError as err:
                self.channel_list_error = err.code
                if self.on_update:
                    self.on_update()
                raise FrameError(err.code, stage="channel_list") from None
            self.channel_lineup = lineup
            self.channel_list_error = None
            if self.channel_store is not None:
                await self.channel_store.async_save(lineup)
            if self.on_update:
                self.on_update()

    def thumbnail_unavailable(self, content_id) -> bool:
        """True while a failed thumbnail should not be asked for again."""
        failed = self._thumbnail_failed.get(content_id)
        return failed is not None and time.monotonic() - failed[0] < failed[1]

    async def async_thumbnail(self, content_id):
        """(bytes, content type) of an artwork thumbnail, or None.

        Cached per artwork; never wakes the TV or changes its mode.
        """
        cached = self._thumbnails.get(content_id)
        if cached:
            self._thumbnails.move_to_end(content_id)
            return cached
        if self.thumbnail_unavailable(content_id):
            return None
        async with self._thumbnail_lock:
            cached = self._thumbnails.get(content_id)
            if cached:
                return cached
            controls = self.controls
            if (
                self._closed
                or not controls.online
                or controls.power_state == "off"
                or controls.values.get("art_mode") != "on"
            ):
                return None
            started = time.monotonic()
            try:
                data, content_type = await self.art.thumbnail(content_id)
            except ArtError as err:
                # A piece the TV will not serve is asked again only after an
                # hour; a connection problem after two minutes.
                retry = 3600 if err.code in ("rejected", "invalid_response") else 120
                self._thumbnail_failed[content_id] = (time.monotonic(), retry)
                self.thumbnail_status = {"content_id": content_id, "error": err.code}
                return None
            self._thumbnail_failed.pop(content_id, None)
            self._thumbnails[content_id] = (data, content_type)
            while len(self._thumbnails) > THUMBNAIL_CACHE:
                self._thumbnails.popitem(last=False)
            self.thumbnail_status = {
                "content_id": content_id,
                "bytes": len(data),
                "content_type": content_type,
                "took_s": round(time.monotonic() - started, 2),
            }
            return data, content_type

    async def async_turn_on(self) -> None:
        """Restore the last mode. In TV mode the TV returns to its own last
        input, which also reflects changes made with the physical remote."""
        await self.async_command("tv" if self.last_mode == "tv" else "art")

    def _audio_reachable(self):
        return (
            self._session is not None
            and self.controls.power_state != "off"
            and self.controls.power_source != "unreachable"
        )

    def clear_audio(self):
        self.volume = self.muted = None

    async def async_refresh_audio(self):
        """Read-only; failures leave volume unknown and never fail a refresh."""
        async with self._audio_lock:
            if not self._audio_reachable():
                self.clear_audio()
                return
            try:
                self.volume = await upnp.get_volume(self._session, self.host)
                self.muted = await upnp.get_mute(self._session, self.host)
            except upnp.UpnpError:
                self.clear_audio()
                self._ipc_audio()

    def _ipc_audio(self):
        """Read-only fallback from this poll's IP Control snapshot."""
        if not self.input_confirmed:
            return
        volume, mute = self._ipc_states.get("volume"), self._ipc_states.get("mute")
        try:
            volume = int(volume)
        except (TypeError, ValueError):
            return
        if 0 <= volume <= 100 and mute in ("muteOn", "muteOff"):
            self.volume, self.muted = volume, mute == "muteOn"

    async def _audio_write(self, setter, reader, attribute, value):
        async with self._audio_lock:
            if self._closed:
                raise FrameError("unloaded")
            if not self._audio_reachable():
                raise FrameError("tv_off", stage="audio")
            try:
                await setter(self._session, self.host, value)
            except upnp.UpnpError as err:
                raise FrameError(err.code, stage="audio") from None
            for attempt in range(AUDIO_READBACK_ATTEMPTS):
                if attempt:
                    await asyncio.sleep(0.5)
                try:
                    observed = await reader(self._session, self.host)
                except upnp.UpnpError:
                    continue
                setattr(self, attribute, observed)
                if observed == value:
                    return
            raise FrameError("unconfirmed", stage="audio_readback", sent=True)

    async def async_set_volume(self, volume: int) -> None:
        await self._audio_write(upnp.set_volume, upnp.get_volume, "volume", volume)

    async def async_set_mute(self, muted: bool) -> None:
        await self._audio_write(upnp.set_mute, upnp.get_mute, "muted", muted)

    async def async_refresh(self, *, catalog=False):
        """Read-only reconciliation; no WOL and no mode changes."""
        async with self._lock:
            if self._closed:
                raise FrameError("unloaded")
            self._active = asyncio.current_task()
            try:
                async with asyncio.timeout(40):
                    previous = self.controls.power_state
                    state, source = await self._observe_power()
                    if state == "off":
                        await self._close_connections()
                        return
                    if source == "unreachable":
                        self.controls.offline()
                        await self._close_connections()
                        return
                    # Power is reconciled every poll; the more expensive Art
                    # settings/catalog retain their existing slower cadence.
                    if (
                        not catalog
                        and self.controls.online
                        and previous == state
                        and time.monotonic() - self._last_art_refresh < 60
                    ):
                        return
                    if self._command_waiting():
                        return  # a user command is queued; Art state can wait
                    if not await port_open(self.host):
                        raise ArtError("cannot_connect")
                    if await self.controls.refresh(
                        catalog=catalog, stop=self._command_waiting
                    ):
                        self._last_art_refresh = time.monotonic()
            except (ArtError, TimeoutError) as err:
                self.controls.offline()
                await self.art.close()
                raise FrameError(
                    getattr(err, "code", "timeout"), stage="art_refresh"
                ) from None
            finally:
                self._active = None

    async def async_set_art_value(self, key, value=None):
        async with self._command(f"set_{key}"):
            self._set_activity("changing_setting")
            self.controls.last_operation = {
                "setting": key,
                "requested": value,
                "phase": "checking_art_mode",
                "sent": False,
            }
            try:
                async with asyncio.timeout(40):
                    state, source = await self._observe_power()
                    if state == "off" or source == "unreachable":
                        raise ArtError("art_inactive")
                    # Stale entity state must never cause a setting to switch modes.
                    data = await self.art.request(
                        "get_artmode_status", events=("artmode_status",)
                    )
                    if data.get("value", data.get("status")) != "on":
                        self.controls.values.clear()
                        raise ArtError("art_inactive")
                    await self.controls.set_value(key, value)
            except (ArtError, TimeoutError) as err:
                self.controls.last_operation.update(
                    error=getattr(err, "code", "timeout")
                )
                # Discard the affected state on uncertain delivery, preserving
                # other independently observed settings.
                self.controls.values.pop(
                    "artwork" if key in ARTWORK_WRITES else key, None
                )
                raise FrameError(
                    getattr(err, "code", "timeout"),
                    stage="art_setting",
                    sent=getattr(err, "sent", False),
                ) from None
            finally:
                self._active = None
                if self.on_update:
                    self.on_update()

    async def async_close(self) -> None:
        self._closed = True
        self.on_update = None
        if self._active and self._active is not asyncio.current_task():
            task = self._active
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._close_connections()

    async def _close_connections(self):
        await self.art.close()
        await self.remote.close()
