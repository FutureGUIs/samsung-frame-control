"""Simulated TV at the network boundary, shared by the Home Assistant tests.

Everything above the transport (controller, coordinator, entities, flows) is
the real integration code running inside pytest-homeassistant-custom-component.
"""

from __future__ import annotations

import asyncio
import json
import types
from unittest.mock import AsyncMock

DOMAIN = "samsung_frame_control"
MAC = "aa:bb:cc:dd:ee:ff"
PKG = "custom_components.samsung_frame_control"
ENTRY_DATA = {
    "name": "Frame TV",
    "host": "192.0.2.10",
    "mac": MAC,
    "token": "stored-token",
    "broadcast_address": "255.255.255.255",
}


class FakeTV:
    """Local-protocol behaviour of a Frame TV, observable by tests."""

    def __init__(self):
        self.power = "on"  # REST PowerState: "on", "off" or None (not reported)
        self.reachable = True
        self.art = "on"
        self.volume = 20
        self.muted = False
        self.audio_available = True
        self.apply_audio = True
        self.wake_on_lan = True
        self.keys = []
        self.art_requests = []
        self.wol_packets = 0
        self.worker_actions = []
        self.power_reads = 0
        # Standby: the host rejects connections (vs. no answer at all).
        self.refuses = False
        # IP Control
        self.ipc_power = "powerOn"
        self.ipc_input = "HDMI1"
        self.ipc_picture = "Standard"
        self.ipc_error = None  # e.g. "refused", "timeout", "unauthorized"
        self.ipc_calls = 0
        self.pair_result = "ipc-token"
        self.ipc_ports = {1516}
        self.keys_switch_input = True  # remote input keys change the input
        self.ipc_input_setter = False  # inputSourceControl implemented?
        self.ipc_off_error = None  # error raised by powerControl powerOff
        self.ipc_commands = []
        # Measured: after IP Control powerOff the TV stays reachable, and the
        # Art channel keeps reporting the remembered Art setting.
        self.ipc_power_on_works = True
        self.ipc_starting_reads = 0  # reads that time out (IP Control starting)
        self.art_delay = 0.0  # seconds per Art request (slow firmware/timeouts)
        # IP Control artModeControl: None = not implemented, else number of
        # "failed" answers before the setter works (TV still starting).
        self.ipc_art_setter = None
        self.ipc_channel = "2110"  # Samsung TV Plus channel on the TV source
        self.ipc_art_flag = True  # artModeControl getter answers in polls
        self.remote_error = None
        self.settings = {
            "brightness": {"value": "5", "min": "1", "max": "10"},
            "brightness_sensor_setting": {"value": "on"},
        }
        self.content = [
            {"content_id": "MY-1", "title": "Flowers"},
            {"content_id": "MY-2", "title": "Flowers"},
        ]
        self.current = "MY-1"
        self.slideshow = {"value": "15", "type": "slideshow", "category_id": "MY-C0002"}
        # "slideshow" (older getter) or "auto_rotation": the 2020 Frame refuses
        # get_slideshow_status (-9) and answers auto_rotation with its pieces.
        self.slideshow_api = "slideshow"
        self.slideshow_items = None  # auto_rotation content_list order
        # Thumbnails served for the media player picture.
        self.thumbnails = {
            "MY-1": b"\xff\xd8\xff\xe0thumb-one",
            "MY-2": b"\xff\xd8\xff\xe0thumb-two",
            "MY-3": b"\xff\xd8\xff\xe0thumb-three",
        }
        self.thumbnail_calls = []
        self.thumbnail_error = None
        # Typed channel numbers: the TV tunes once this many digits arrive
        # (four on Samsung TV Plus), unless the number is not in the line-up.
        self.channel_digits = 4
        self.missing_channels = set()
        self._typed = ""
        self.key_batches = []  # keys sent over one connection
        self.key_timing = []  # (gap, hold) of each send_keys call
        self.fresh_connections = []  # send_keys asked for a new connection
        self.drop_first_digit = 0  # entries whose first digit the TV misses
        self.keys_error = None  # raised by send_keys (e.g. ArtError(sent=...))

    # -- REST power and TCP reachability --------------------------------
    async def read_power(self, session, host):
        self.power_reads += 1
        if not self.reachable:
            return None, "refused" if self.refuses else "unreachable"
        if self.power is None:
            return None, "not_reported"
        return self.power, "local_rest"

    async def port_open(self, host, port=8002):
        if port in (1515, 1516):
            return self.reachable and port in self.ipc_ports
        return self.reachable

    # -- IP Control (runs in HA's executor like the real blocking client) ----
    def ipc_read_state(self, client):
        from custom_components.samsung_frame_control.ipcontrol import IpcError

        self.ipc_calls += 1
        if not self.reachable:
            raise IpcError("refused" if self.refuses else "timeout")
        if self.ipc_error:
            raise IpcError(self.ipc_error)
        if self.ipc_starting_reads:
            # IP Control starts ~20 s after the rest of the TV wakes.
            self.ipc_starting_reads -= 1
            raise IpcError("timeout")
        if self.ipc_power != "powerOn":
            return self.ipc_power, {}
        states = {
            "inputSource": self.ipc_input,
            "pictureMode": "Ambient" if self.art == "on" else self.ipc_picture,
            "volume": self.volume,
            "mute": "muteOn" if self.muted else "muteOff",
        }
        if self.ipc_art_flag:
            states["artMode"] = "on" if self.art == "on" else "off"
        if self.ipc_input == "TV" and self.ipc_channel:
            states["channel"] = {"number": self.ipc_channel, "kind": "tvplus"}
        return self.ipc_power, states

    def ipc_call(self, client, method, params=None, **kwargs):
        from custom_components.samsung_frame_control.ipcontrol import IpcError

        if not self.reachable:
            raise IpcError("refused" if self.refuses else "timeout")
        if self.ipc_error:
            raise IpcError(self.ipc_error)
        if params:
            self.ipc_commands.append((method, params))
        if method == "powerControl" and params:
            if params["power"] == "powerOn":
                if self.ipc_power_on_works:
                    self.ipc_power, self.power = "powerOn", "on"
                return {}
            if self.ipc_off_error:
                raise IpcError(self.ipc_off_error)
            self.ipc_power, self.power = "powerOff", "off"
            return {}
        if method == "powerControl":
            return {"power": self.ipc_power}
        if method == "inputSourceControl":
            if not self.ipc_input_setter:
                raise IpcError("unsupported", -32601)
            self.ipc_input = params["inputSource"]
            return {}
        if method == "getTVStates":
            return self.ipc_read_state(client)[1]
        if method == "artModeControl" and self.ipc_art_setter is not None:
            if params:
                if self.ipc_art_setter > 0:
                    self.ipc_art_setter -= 1
                    raise IpcError("failed", -32002)
                self.art = "on" if params["artMode"] == "artModeOn" else "off"
                return {}
            # Like the Art channel, an off TV still reports its remembered setting.
            return {"artMode": "artModeOn" if self.art == "on" else "artModeOff"}
        raise IpcError("unsupported", -32601)

    def ipc_pair(self, client):
        from custom_components.samsung_frame_control.ipcontrol import IpcError

        if self.pair_result in ("timeout", "failed", "refused", "unauthorized"):
            raise IpcError(self.pair_result)
        client.token = self.pair_result
        return self.pair_result

    def magic_packet(self, mac, broadcast):
        self.wol_packets += 1
        if self.wake_on_lan:
            self.reachable, self.power, self.ipc_power = True, "on", "powerOn"

    async def run_worker(self, host, token, action, on_token, **kwargs):
        self.worker_actions.append(action)
        if action == "off":
            self.power, self.ipc_power = "off", "powerOff"
        return True

    # -- Art channel ------------------------------------------------------
    async def art_request(self, client, command, *, events=(), timeout=4, **params):
        from custom_components.samsung_frame_control.async_art import ArtError

        self.art_requests.append((command, params))
        if getattr(client, "on_request", None):
            client.on_request(command)  # like the real client's request()
        if self.art_delay:
            await asyncio.sleep(self.art_delay)  # real sleep, not the patched one
        if not self.reachable:
            raise ArtError("cannot_connect")
        if self.power == "off":
            if self.ipc_power != "powerOff":
                raise ArtError("cannot_connect")
            # Off after IP Control powerOff: answers with the remembered
            # setting and accepts commands without lighting the panel.
            if command == "get_artmode_status":
                return {"event": "artmode_status", "value": self.art}
            if command == "set_artmode_status":
                self.art = params["value"]
                return {"event": "artmode_status", "value": self.art}
            raise ArtError("rejected", sent=True)
        if command == "get_artmode_status":
            return {"event": "artmode_status", "value": self.art}
        if command == "set_artmode_status":
            self.art = params["value"]
            return {"event": "artmode_status", "value": self.art}
        if command == "get_artmode_settings":
            items = [{"item": k, **v} for k, v in self.settings.items()]
            return {"data": json.dumps(items)}
        if command.startswith("set_") and command[4:] in self.settings:
            self.settings[command[4:]]["value"] = params["value"]
            return {}
        if command == "get_content_list":
            return {"content_list": json.dumps(self.content)}
        if command == "get_current_artwork":
            return {"content_id": self.current}
        if command == "select_image":
            self.current = params["content_id"]
            return {}
        if command.endswith("_status") and command[4:-7] in (
            "slideshow",
            "auto_rotation",
        ):
            if command[4:-7] != self.slideshow_api:
                raise ArtError("rejected", sent=True)
        if command == "get_auto_rotation_status":
            reply = dict(self.slideshow)
            if self.slideshow_items is not None:
                reply["content_list"] = json.dumps(
                    [{"content_id": cid} for cid in self.slideshow_items]
                )
            return reply
        if command == "get_slideshow_status":
            return dict(self.slideshow)
        if command in ("set_slideshow_status", "set_auto_rotation_status"):
            self.slideshow = {
                "value": params["value"],
                "type": params["type"],
                "category_id": params["category_id"],
            }
            return {}
        raise ArtError("rejected", sent=True)

    def _tune(self):
        """Tune the typed number (like the TV: a dropped digit tunes another)."""
        if self.ipc_input == "TV" and self._typed not in self.missing_channels:
            self.ipc_channel = self._typed
        self._typed = ""

    async def send_keys(self, client, keys, gap=1.0, hold=0.0, fresh=False):
        self.key_batches.append(list(keys))
        self.key_timing.append((gap, hold))
        self.fresh_connections.append(fresh)
        if self.drop_first_digit and keys and keys[0][-1:].isdigit():
            self.drop_first_digit -= 1
            keys = keys[1:]
        if self.keys_error:
            error, self.keys_error = self.keys_error, None
            raise error
        for key in keys:
            await self.send_key(client, key)

    async def thumbnail(self, client, content_id):
        from custom_components.samsung_frame_control.async_art import ArtError

        self.thumbnail_calls.append(content_id)
        if not self.reachable or self.power != "on":
            raise ArtError("cannot_connect")
        if self.thumbnail_error:
            raise self.thumbnail_error
        if content_id not in self.thumbnails:
            raise ArtError("rejected", sent=True)
        return self.thumbnails[content_id], "image/jpeg"

    # -- Remote channel -------------------------------------------------
    async def send_key(self, client, key):
        if self.remote_error:
            raise self.remote_error
        self.keys.append(key)
        reported = {"KEY_TV": "TV", **{f"KEY_HDMI{n}": f"HDMI{n}" for n in range(1, 5)}}
        if self.keys_switch_input and key in reported:
            self.ipc_input = reported[key]
        if len(key) == 5 and key[4].isdigit():
            self._typed += key[4]
            if len(self._typed) == self.channel_digits:
                self._tune()
        if key == "KEY_ENTER" and self._typed:
            self._tune()
        if key in ("KEY_CHUP", "KEY_CHDOWN") and self.ipc_input == "TV":
            self.ipc_channel = str(
                int(self.ipc_channel) + (1 if key == "KEY_CHUP" else -1)
            )

    # -- UPnP RenderingControl ------------------------------------------
    def _audio(self):
        from custom_components.samsung_frame_control.upnp import UpnpError

        if not (self.reachable and self.power == "on" and self.audio_available):
            raise UpnpError()

    async def get_volume(self, session, host):
        self._audio()
        return self.volume

    async def get_mute(self, session, host):
        self._audio()
        return self.muted

    async def set_volume(self, session, host, volume):
        self._audio()
        if self.apply_audio:
            self.volume = volume

    async def set_mute(self, session, host, muted):
        self._audio()
        if self.apply_audio:
            self.muted = muted


def fast_asyncio():
    """frame.py's asyncio with instant sleeps; HA's own event loop is untouched."""
    fast = types.SimpleNamespace(
        **{name: getattr(asyncio, name) for name in dir(asyncio) if name[0] != "_"}
    )
    fast.sleep = AsyncMock()
    return fast


async def refresh(hass, entry):
    """Run one coordinator poll and wait for entity updates."""
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
