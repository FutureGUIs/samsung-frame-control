"""Protocol, orchestration and real worker-lifecycle tests (no TV required)."""

import asyncio
import importlib
import io
import json
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

# Load hardware-independent modules without importing Home Assistant adapters.
ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "custom_components" / "samsung_frame_control"
pkg = types.ModuleType("frame_under_test")
pkg.__path__ = [str(PACKAGE)]
sys.modules[pkg.__name__] = pkg
frame = importlib.import_module("frame_under_test.frame")
worker = importlib.import_module("frame_under_test.worker")


class ProtocolTests(unittest.TestCase):
    def test_discovery_reads_metadata_without_opening_websocket(self):
        output = io.StringIO()
        with (
            patch(
                "samsungtvws.SamsungTVWS.rest_device_info",
                return_value={
                    "device": {"modelName": "QN55LS03T", "wifiMac": "AABBCCDDEEFF"}
                },
            ),
            patch("websocket.create_connection") as connect,
            redirect_stdout(output),
        ):
            worker.execute({"host": "frame.test", "action": "discover"})
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        info = next(event for event in events if event["event"] == "device_info")
        self.assertEqual(info["data"]["modelName"], "QN55LS03T")
        self.assertEqual(events[-1]["event"], "done")
        connect.assert_not_called()

    def run_command(self, action, fail_send=False, unauthorized=False):
        sockets = []

        class FakeSocket:
            connected = True

            def __init__(self, url):
                self.url = url
                self.sent = []
                self.frames = [
                    {
                        "event": "ms.channel.unauthorized"
                        if unauthorized
                        else "ms.channel.connect",
                        "data": {"token": "test-token"},
                    }
                ]
                if "art-app" in url:
                    self.frames.append({"event": "ms.channel.ready"})

            def recv(self):
                if self.frames:
                    return json.dumps(self.frames.pop(0))
                data = json.loads(self.sent[-1]["params"]["data"])
                return json.dumps(
                    {
                        "event": "d2d_service_message",
                        "data": json.dumps(
                            {
                                "event": "artmode_status",
                                "request_id": data["id"],
                                "value": "on",
                            }
                        ),
                    }
                )

            def send(self, payload):
                if fail_send:
                    raise OSError("secret-token-must-not-leak")
                self.sent.append(json.loads(payload))

            def close(self):
                self.connected = False

        def connect(url, *args, **kwargs):
            socket = FakeSocket(url)
            sockets.append(socket)
            return socket

        output = io.StringIO()
        with (
            patch("websocket.create_connection", side_effect=connect),
            patch("samsungtvws.connection.time.sleep") as sleep,
            redirect_stdout(output),
        ):
            worker.execute({"host": "frame.test", "action": action, "token": None})
        return (
            [
                event
                for line in output.getvalue().splitlines()
                if (event := json.loads(line))["event"] != "phase"
            ],
            sockets,
            sleep,
        )

    def test_art_sends_explicit_on_and_reports_delivery(self):
        events, sockets, _ = self.run_command("art")
        inner = json.loads(sockets[0].sent[0]["params"]["data"])
        self.assertEqual(
            (inner["request"], inner["value"]), ("set_artmode_status", "on")
        )
        self.assertEqual([e["event"] for e in events], ["token", "art_sent", "done"])
        self.assertFalse(sockets[0].connected)

    def test_tv_mode_sends_explicit_art_off_and_reports_delivery(self):
        events, sockets, _ = self.run_command("tv")
        inner = json.loads(sockets[0].sent[0]["params"]["data"])
        self.assertEqual(
            (inner["request"], inner["value"]), ("set_artmode_status", "off")
        )
        self.assertEqual([e["event"] for e in events], ["token", "art_sent", "done"])
        self.assertEqual(len(sockets[0].sent), 1)

    def test_failed_send_is_never_reported_as_sent(self):
        events, _, _ = self.run_command("art", fail_send=True)
        self.assertEqual([e["event"] for e in events], ["token", "error"])
        self.assertNotIn("secret-token", json.dumps(events))

    def test_long_power_hold_is_press_two_seconds_release(self):
        events, sockets, sleep = self.run_command("off")
        self.assertEqual(
            [p["params"]["Cmd"] for p in sockets[0].sent], ["Press", "Release"]
        )
        self.assertTrue(
            all(p["params"]["DataOfCmd"] == "KEY_POWER" for p in sockets[0].sent)
        )
        sleep.assert_any_call(2.0)
        self.assertEqual(events[-1]["event"], "done")

    def test_pair_checks_both_channels_without_control_commands(self):
        events, sockets, _ = self.run_command("pair")
        self.assertEqual(len(sockets), 2)
        self.assertTrue(all(not socket.sent for socket in sockets))
        self.assertIn("token=test-token", sockets[1].url)
        self.assertEqual(events[-1]["event"], "done")

    def test_auth_denial_is_specific(self):
        events, _, _ = self.run_command("pair", unauthorized=True)
        self.assertEqual(events[-1], {"event": "error", "code": "invalid_auth"})

    def test_wol_packet(self):
        socket = Mock()
        socket.__enter__ = Mock(return_value=socket)
        socket.__exit__ = Mock(return_value=False)
        with patch.object(frame.socket, "socket", return_value=socket):
            frame.send_magic_packet("AA-BB-CC-DD-EE-FF", "192.168.1.255")
        packet, address = socket.sendto.call_args.args
        self.assertEqual(packet, b"\xff" * 6 + bytes.fromhex("aabbccddeeff") * 16)
        self.assertEqual(address, ("192.168.1.255", 9))

    def test_mac_validation(self):
        self.assertEqual(frame.normalize_mac("AABB.CCDD.EEFF"), "aa:bb:cc:dd:ee:ff")
        for value in ("nope", "AABBCCDDEEFG", "AA:BB:CC"):
            with self.assertRaises(ValueError):
                frame.normalize_mac(value)


class WorkerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_error_preserves_stage_and_exception_type(self):
        with self.assertRaises(frame.FrameError) as raised:
            await self.run_script(
                'print(json.dumps({"event":"phase","phase":"art_handshake"}),flush=True)\nprint(json.dumps({"event":"error","code":"cannot_connect","kind":"ConnectionFailure"}),flush=True)\n'
            )
        self.assertEqual(raised.exception.stage, "art_handshake")
        self.assertEqual(raised.exception.kind, "ConnectionFailure")
        self.assertFalse(raised.exception.sent)

    async def test_post_send_error_preserves_delivery_flag(self):
        with self.assertRaises(frame.FrameError) as raised:
            await self.run_script(
                'print(json.dumps({"event":"art_sent"}),flush=True)\nprint(json.dumps({"event":"error","code":"cannot_connect","kind":"ResponseError"}),flush=True)\n'
            )
        self.assertTrue(raised.exception.sent)
        self.assertEqual(raised.exception.stage, "mode_response")

    async def run_script(self, text, action="art", **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "worker.py"
            path.write_text(
                "import json,sys,time\njson.loads(sys.stdin.readline())\n" + text,
                encoding="utf-8",
            )
            real_spawn = asyncio.create_subprocess_exec
            processes = []

            async def spawn(*args, **kw):
                process = await real_spawn(*args, **kw)
                processes.append(process)
                return process

            with patch.object(
                frame.asyncio, "create_subprocess_exec", side_effect=spawn
            ):
                try:
                    return await frame.run_worker(
                        "tv",
                        None,
                        action,
                        lambda _: None,
                        worker_path=path,
                        operation_timeout=1,
                        response_timeout=0.1,
                        **kwargs,
                    )
                finally:
                    self.assertIsNotNone(
                        processes[0].returncode, "worker must always be reaped"
                    )

    async def test_sent_but_hanging_worker_is_terminated(self):
        result = await self.run_script(
            'print(json.dumps({"event":"art_sent"}),flush=True)\ntime.sleep(60)\n'
        )
        self.assertFalse(result)

    async def test_tv_mode_hanging_response_is_terminated(self):
        result = await self.run_script(
            'print(json.dumps({"event":"art_sent"}),flush=True)\ntime.sleep(60)\n',
            action="tv",
        )
        self.assertFalse(result)

    async def test_timeout_before_send_is_error(self):
        with self.assertRaises(frame.FrameError) as raised:
            await self.run_script("time.sleep(60)\n")
        self.assertEqual(raised.exception.code, "timeout")

    async def test_crashed_worker_is_error(self):
        with self.assertRaises(frame.FrameError):
            await self.run_script("sys.exit(1)\n")

    async def test_cancellation_reaps_worker(self):
        task = asyncio.create_task(self.run_script("time.sleep(60)\n"))
        await asyncio.sleep(0.2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_tv_mode_wakes_before_sending(self):
        with (
            patch.object(frame, "port_open", AsyncMock(side_effect=[False, True])),
            patch.object(frame, "run_worker", AsyncMock(return_value=True)) as run,
            patch.object(frame.asyncio, "sleep", AsyncMock()),
        ):
            await self.controller.async_command("tv")
        self.executor.assert_awaited_once()
        self.assertEqual(run.call_args.args[2], "tv")

    async def test_tv_mode_already_on_does_not_wake(self):
        with (
            patch.object(frame, "port_open", AsyncMock(return_value=True)),
            patch.object(frame, "run_worker", AsyncMock(return_value=True)) as run,
        ):
            self.assertTrue(await self.controller.async_command("tv"))
        self.executor.assert_not_called()
        self.assertEqual(run.call_count, 1)

    def setUp(self):
        self.executor = AsyncMock()
        self.saved = Mock()
        self.controller = frame.FrameController(
            "tv", "aa:bb:cc:dd:ee:ff", None, self.saved, self.executor
        )

        async def mode_transport(action):
            return await frame.run_worker(
                "tv", self.controller.token, action, self.saved
            )

        self.controller._send_mode = mode_transport

    async def test_already_on_art_never_wakes(self):
        with (
            patch.object(frame, "port_open", AsyncMock(return_value=True)),
            patch.object(frame, "run_worker", AsyncMock(return_value=True)) as run,
        ):
            self.assertTrue(await self.controller.async_command("art"))
        self.executor.assert_not_called()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[2], "art")

    async def test_off_to_art_wakes_then_sends(self):
        with (
            patch.object(frame, "port_open", AsyncMock(side_effect=[False, True])),
            patch.object(frame, "run_worker", AsyncMock(return_value=True)),
            patch.object(frame.asyncio, "sleep", AsyncMock()),
        ):
            await self.controller.async_command("art")
        self.executor.assert_awaited_once()

    async def test_unreachable_power_off_never_wakes_or_sends(self):
        with (
            patch.object(frame, "port_open", AsyncMock(return_value=False)),
            patch.object(frame, "run_worker", AsyncMock()) as run,
        ):
            with self.assertRaises(frame.FrameError):
                await self.controller.async_command("off")
        run.assert_not_called()
        self.executor.assert_not_called()

    async def test_power_failure_is_not_retried(self):
        with (
            patch.object(frame, "port_open", AsyncMock(return_value=True)),
            patch.object(
                frame, "run_worker", AsyncMock(side_effect=frame.FrameError())
            ) as run,
        ):
            with self.assertRaises(frame.FrameError):
                await self.controller.async_command("off")
        self.assertEqual(run.call_count, 1)

    async def test_art_connection_retries_then_reasserts_after_wake(self):
        with (
            patch.object(frame, "port_open", AsyncMock(return_value=True)),
            patch.object(
                frame,
                "run_worker",
                AsyncMock(side_effect=[frame.FrameError(), True, True]),
            ) as run,
            patch.object(frame.asyncio, "sleep", AsyncMock()),
        ):
            self.assertTrue(await self.controller.async_command("art"))
        self.assertEqual(run.call_count, 3)
        self.assertEqual(self.executor.await_count, 3)

    async def test_cold_start_reasserts_same_mode_after_delay(self):
        for action in ("art", "tv"):
            with (
                patch.object(frame, "port_open", AsyncMock(side_effect=[False, True])),
                patch.object(frame, "run_worker", AsyncMock(return_value=True)) as run,
                patch.object(frame.asyncio, "sleep", AsyncMock()) as sleep,
            ):
                self.assertTrue(await self.controller.async_command(action))
            self.assertEqual(
                [call.args[2] for call in run.call_args_list], [action, action]
            )
            self.assertEqual(sum(call.args == (5,) for call in sleep.call_args_list), 2)

    async def test_unanswered_mode_reconnects_and_wakes_with_bounded_retries(self):
        with (
            patch.object(frame, "port_open", AsyncMock(return_value=True)),
            patch.object(frame, "run_worker", AsyncMock(return_value=False)) as run,
            patch.object(frame.asyncio, "sleep", AsyncMock()),
        ):
            with self.assertRaises(frame.FrameError) as raised:
                await self.controller.async_command("art")
            self.assertEqual(raised.exception.code, "unconfirmed")
        self.assertEqual(run.call_count, 4)
        self.assertEqual(self.executor.await_count, 9)

    async def test_slow_api_gets_more_than_two_attempts(self):
        with (
            patch.object(frame, "port_open", AsyncMock(return_value=True)),
            patch.object(
                frame,
                "run_worker",
                AsyncMock(
                    side_effect=[frame.FrameError(), frame.FrameError(), True, True]
                ),
            ) as run,
            patch.object(frame.asyncio, "sleep", AsyncMock()),
        ):
            self.assertTrue(await self.controller.async_command("tv"))
        self.assertEqual(run.call_count, 4)

    async def test_connection_retries_are_bounded(self):
        with (
            patch.object(frame, "port_open", AsyncMock(return_value=True)),
            patch.object(
                frame, "run_worker", AsyncMock(side_effect=frame.FrameError())
            ) as run,
            patch.object(frame.asyncio, "sleep", AsyncMock()),
        ):
            with self.assertRaises(frame.FrameError):
                await self.controller.async_command("art")
        self.assertEqual(run.call_count, 4)

    async def test_overall_startup_deadline(self):
        async def slow_worker(*args):
            await asyncio.sleep(60)

        with (
            patch.object(frame, "MODE_STARTUP_TIMEOUT", 0.01),
            patch.object(frame, "port_open", AsyncMock(return_value=True)),
            patch.object(frame, "run_worker", side_effect=slow_worker),
        ):
            with self.assertRaises(frame.FrameError) as raised:
                await self.controller.async_command("art")
        self.assertEqual(raised.exception.code, "startup_timeout")

    async def test_post_send_transport_failure_retries_only_explicit_mode(self):
        with (
            patch.object(frame, "port_open", AsyncMock(return_value=True)),
            patch.object(
                frame, "run_worker", AsyncMock(side_effect=frame.FrameError(sent=True))
            ) as run,
            patch.object(frame.asyncio, "sleep", AsyncMock()),
        ):
            with self.assertRaises(frame.FrameError):
                await self.controller.async_command("art")
        self.assertEqual(self.executor.await_count, 9)
        self.assertEqual(run.call_count, 4)

    async def test_worker_crash_does_not_trigger_wake_retry(self):
        with (
            patch.object(frame, "port_open", AsyncMock(return_value=True)),
            patch.object(
                frame,
                "run_worker",
                AsyncMock(side_effect=frame.FrameError("worker_failed")),
            ) as run,
        ):
            with self.assertRaises(frame.FrameError):
                await self.controller.async_command("art")
        self.executor.assert_not_called()
        self.assertEqual(run.call_count, 1)

    async def test_auth_is_not_retried(self):
        with (
            patch.object(frame, "port_open", AsyncMock(return_value=True)),
            patch.object(
                frame,
                "run_worker",
                AsyncMock(side_effect=frame.FrameError("invalid_auth")),
            ) as run,
        ):
            with self.assertRaises(frame.FrameError):
                await self.controller.async_command("art")
        self.assertEqual(run.call_count, 1)

    async def test_token_update_and_unload(self):
        self.controller._save_token("new-token")
        self.saved.assert_called_once_with("new-token")
        self.assertEqual(self.controller.token, "new-token")
        await self.controller.async_close()
        with self.assertRaises(frame.FrameError):
            await self.controller.async_command("art")

    async def test_commands_are_serialized(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def run(host, token, action, callback):
            calls.append(action)
            entered.set()
            await release.wait()
            return True

        with (
            patch.object(frame, "port_open", AsyncMock(return_value=True)),
            patch.object(frame, "run_worker", side_effect=run),
        ):
            first = asyncio.create_task(self.controller.async_command("art"))
            await entered.wait()
            second = asyncio.create_task(self.controller.async_command("off"))
            await asyncio.sleep(0)
            self.assertEqual(calls, ["art"])
            release.set()
            await asyncio.gather(first, second)
        self.assertEqual(calls, ["art", "off"])


if __name__ == "__main__":
    unittest.main()
