"""Async protocol regressions, including a real local aiohttp WebSocket server."""

import asyncio
import importlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import pytest
from aiohttp import web
from test_frame import frame

api = importlib.import_module("frame_under_test.async_art")
controls_module = importlib.import_module("frame_under_test.art_controls")


class Socket:
    def __init__(self):
        self.closed = False
        self.queue = asyncio.Queue()
        self.sent = []
        self.send_error = False
        self.feed({"event": "ms.channel.connect", "data": {"token": "private-token"}})
        self.feed({"event": "ms.channel.ready"})

    def feed(self, data):
        self.queue.put_nowait(
            SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(data))
        )

    def reply(self, data):
        self.feed({"event": "d2d_service_message", "data": json.dumps(data)})

    async def receive(self):
        return await self.queue.get()

    async def send_json(self, data):
        if self.send_error:
            raise OSError("do-not-leak-token")
        self.sent.append(json.loads(data["params"]["data"]))

    async def close(self):
        self.closed = True


class AsyncArtTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.socket = Socket()
        self.session = SimpleNamespace(ws_connect=AsyncMock(return_value=self.socket))
        self.token = Mock()
        self.events = Mock()
        self.client = api.AsyncArtClient(
            self.session, "192.0.2.1", "old-token", self.token, self.events
        )

    async def asyncTearDown(self):
        await self.client.close()

    async def request(self, **kwargs):
        task = asyncio.create_task(
            self.client.request("get_brightness", events=("brightness",), **kwargs)
        )
        for _ in range(30):
            if self.socket.sent:
                return task, self.socket.sent[-1]
            await asyncio.sleep(0)
        self.fail("request not sent")

    async def test_constructor_does_no_io_and_token_saved_on_handshake(self):
        self.session.ws_connect.assert_not_called()
        await self.client.connect()
        self.token.assert_called_once_with("private-token")
        url = self.session.ws_connect.call_args.args[0]
        self.assertEqual(url.path, "/api/v2/channels/com.samsung.art-app")
        self.assertEqual(url.query["token"], "old-token")

    async def test_both_request_ids_sent_and_wrong_id_cannot_complete(self):
        task, request = await self.request()
        self.assertEqual(request["id"], request["request_id"])
        self.socket.reply({"event": "brightness", "request_id": "stale", "value": "1"})
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.socket.reply(
            {"event": "brightness", "request_id": request["id"], "value": "7"}
        )
        self.assertEqual((await task)["value"], "7")
        self.assertFalse(self.client._pending)

    async def test_legacy_id_and_idless_expected_event(self):
        for use_id in (True, False):
            self.socket.sent.clear()
            task, request = await self.request()
            data = {"event": "brightness", "value": "5"}
            if use_id:
                data["id"] = request["id"]
            self.socket.reply(data)
            self.assertEqual((await task)["value"], "5")

    async def test_unsolicited_event_is_not_command_success(self):
        task, request = await self.request()
        self.socket.reply({"event": "art_mode_changed", "status": "off"})
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.events.assert_called_once()
        self.socket.reply({"event": "brightness", "id": request["id"], "value": "3"})
        await task

    async def test_timeout_closes_socket_and_clears_waiter(self):
        task, _ = await self.request(timeout=0.02)
        with self.assertRaises(api.ArtError) as raised:
            await task
        self.assertEqual(raised.exception.code, "timeout")
        self.assertTrue(raised.exception.sent)
        self.assertTrue(self.socket.closed)
        self.assertFalse(self.client._pending)
        self.assertIsNone(self.client._reader)

    async def test_cancel_and_unload_close_reader(self):
        task, _ = await self.request()
        reader = self.client._reader
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(reader.done())
        self.assertTrue(self.socket.closed)
        self.assertFalse(self.client._pending)

    async def test_send_failure_not_reported_delivered(self):
        self.socket.send_error = True
        with self.assertRaises(api.ArtError) as raised:
            await self.client.request("set_brightness", value="5")
        self.assertFalse(raised.exception.sent)
        self.assertNotIn("do-not-leak", str(raised.exception))

    async def test_error_correlated_through_request_data(self):
        task, request = await self.request()
        self.socket.reply(
            {"event": "error", "request_data": json.dumps(request), "error_code": -1}
        )
        with self.assertRaises(api.ArtError) as raised:
            await task
        self.assertEqual(raised.exception.code, "rejected")

    async def test_disconnect_fails_pending_request(self):
        task, _ = await self.request()
        self.socket.queue.put_nowait(SimpleNamespace(type=aiohttp.WSMsgType.CLOSED))
        with self.assertRaises(api.ArtError):
            await task
        self.assertTrue(self.socket.closed)
        self.events.assert_called_with({"event": "disconnected"})

    async def test_unauthorized_handshake_closes_socket(self):
        self.socket.queue = asyncio.Queue()
        self.socket.feed({"event": "ms.channel.unauthorized"})
        with self.assertRaises(api.ArtError) as raised:
            await self.client.connect()
        self.assertEqual(raised.exception.code, "invalid_auth")
        self.assertTrue(self.socket.closed)

    async def test_handshake_without_ready_has_deadline(self):
        self.socket.queue = asyncio.Queue()
        self.socket.feed({"event": "ms.channel.connect", "data": {}})
        with patch.object(api, "HANDSHAKE_TIMEOUT", 0.02):
            with self.assertRaises(api.ArtError) as raised:
                await self.client.connect()
        self.assertEqual(raised.exception.code, "timeout")
        self.assertTrue(self.socket.closed)

    async def test_reconnect_after_disconnect_uses_new_socket(self):
        await self.client.connect()
        first_reader = self.client._reader
        self.socket.queue.put_nowait(SimpleNamespace(type=aiohttp.WSMsgType.CLOSED))
        await first_reader
        replacement = Socket()
        self.session.ws_connect.return_value = replacement
        await self.client.connect()
        self.assertIs(self.client._ws, replacement)
        self.assertIsNot(self.client._reader, first_reader)

    async def test_mode_is_verified_after_ack_not_assumed(self):
        with patch.object(
            self.client, "request", AsyncMock(side_effect=[{}, {"status": "off"}])
        ) as request:
            self.assertFalse(await self.client.set_mode(True))
        self.assertEqual(request.call_args.args[0], "get_artmode_status")

    async def test_missing_setter_ack_can_succeed_only_by_readback(self):
        with patch.object(
            self.client,
            "request",
            AsyncMock(
                side_effect=[api.ArtError("timeout", sent=True), {"value": "on"}]
            ),
        ):
            self.assertTrue(await self.client.set_mode(True))

    @pytest.mark.enable_socket
    async def test_real_aiohttp_websocket_handshake_request_and_close(self):
        calls = []

        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.send_json(
                {"event": "ms.channel.connect", "data": {"token": "test"}}
            )
            await ws.send_json({"event": "ms.channel.ready"})
            async for message in ws:
                data = json.loads(json.loads(message.data)["params"]["data"])
                calls.append(data)
                await ws.send_json(
                    {
                        "event": "d2d_service_message",
                        "data": json.dumps(
                            {
                                "event": "artmode_status",
                                "request_id": data["id"],
                                "status": "on",
                            }
                        ),
                    }
                )
            return ws

        app = web.Application()
        app.router.add_get("/api/v2/channels/com.samsung.art-app", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with aiohttp.ClientSession() as session:

                async def connect(url, **kwargs):
                    return await session.ws_connect(
                        url.with_scheme("http").with_host("127.0.0.1").with_port(port),
                        **kwargs,
                    )

                client = api.AsyncArtClient(
                    SimpleNamespace(ws_connect=connect), "test", None, Mock()
                )
                try:
                    self.assertTrue(await client.set_mode(True))
                finally:
                    await client.close()
                self.assertIsNone(client._reader)
        finally:
            await runner.cleanup()
        self.assertEqual(
            [v["request"] for v in calls], ["set_artmode_status", "get_artmode_status"]
        )


class SettingsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.responses = {
            "get_artmode_status": {"status": "on"},
            "get_artmode_settings": {
                "data": json.dumps(
                    [
                        {"item": "brightness", "value": "5", "min": "1", "max": "10"},
                        {
                            "item": "color_temperature",
                            "value": "0",
                            "min": "-5",
                            "max": "5",
                        },
                        {"item": "brightness_sensor_setting", "value": "on"},
                        {"item": "motion_timer", "value": "15"},
                        {"item": "motion_sensitivity", "value": "2"},
                    ]
                )
            },
            "get_current_artwork": {"content_id": "MY-1", "matte_id": "modern_polar"},
            "get_content_list": {
                "content_list": json.dumps(
                    [
                        {"content_id": "MY-1", "title": "Flowers"},
                        {"content_id": "MY-2", "title": "Sea"},
                    ]
                )
            },
            "get_matte_list": {
                "matte_type_list": '[{"matte_type":"none"},{"matte_type":"modern"}]',
                "matte_color_list": '[{"matte_color":"polar"},{"matte_color":"black"}]',
            },
            "get_slideshow_status": {
                "value": "15",
                "type": "slideshow",
                "category_id": "MY-C0002",
            },
        }
        self.client = SimpleNamespace(request=AsyncMock(side_effect=self.request))
        self.controls = controls_module.ArtControls(self.client)

    async def request(self, command, **kwargs):
        value = self.responses.get(command, api.ArtError("rejected", sent=True))
        if isinstance(value, Exception):
            raise value
        return value

    async def test_capabilities_values_and_reported_ranges(self):
        await self.controls.refresh(catalog=True)
        self.assertEqual(self.controls.ranges["brightness"], (1, 10))
        self.assertEqual(self.controls.values["art_mode"], "on")
        self.assertEqual(self.controls.options["mat_color"], ["polar", "black"])
        self.assertIn("random_artwork", self.controls.supported)

    async def test_older_tv_fallback_and_unsupported_controls_not_invented(self):
        self.responses["get_artmode_settings"] = api.ArtError("rejected")
        self.responses["get_brightness"] = {"value": "7"}
        self.responses["get_color_temperature"] = {"value": "-2"}
        self.responses["get_slideshow_status"] = api.ArtError("rejected")
        self.responses["get_auto_rotation_status"] = {
            "value": "30",
            "type": "slideshow",
            "category_id": "MY-C0004",
        }
        await self.controls.refresh(catalog=True)
        self.assertEqual(self.controls.values["brightness"], 7)
        self.assertNotIn("motion_sensitivity", self.controls.supported)
        self.assertEqual(self.controls._slideshow_api, "auto_rotation")

    async def test_catalog_read_on_first_refresh_soon_after_host_boot(self):
        # time.monotonic() starts near zero when the host boots.
        with patch.object(controls_module.time, "monotonic", return_value=12.0):
            await self.controls.refresh()
        self.assertIn("artwork", self.controls.supported)
        self.assertIn("random_artwork", self.controls.supported)

    async def test_tv_mode_queries_no_art_settings(self):
        self.responses["get_artmode_status"] = {"value": "off"}
        await self.controls.refresh()
        self.client.request.assert_awaited_once()
        self.assertEqual(self.controls.values, {"art_mode": "off"})

    async def test_changed_setting_requires_readback(self):
        await self.controls.refresh()
        self.responses["set_brightness"] = {}
        with self.assertRaises(api.ArtError) as raised:
            await self.controls.set_value("brightness", 8)
        self.assertEqual(raised.exception.code, "unconfirmed")
        self.assertEqual(self.controls.values["brightness"], 5)

    async def test_verified_brightness_and_range_validation(self):
        await self.controls.refresh()
        self.responses["set_brightness"] = {}
        settings = json.loads(self.responses["get_artmode_settings"]["data"])
        settings[0]["value"] = "8"
        self.responses["get_artmode_settings"]["data"] = json.dumps(settings)
        await self.controls.set_value("brightness", 8)
        with self.assertRaises(api.ArtError):
            await self.controls.set_value("brightness", 11)
        with self.assertRaises(api.ArtError):
            await self.controls.set_value("brightness", 2.5)

    async def test_random_art_avoids_current_and_sends_explicit_selection(self):
        await self.controls.refresh()
        self.responses["select_image"] = {}
        self.responses["get_current_artwork"] = {"content_id": "MY-2"}
        await self.controls.set_value("random_artwork", None)
        call = next(
            c for c in self.client.request.call_args_list if c.args[0] == "select_image"
        )
        self.assertEqual(call.kwargs["content_id"], "MY-2")
        self.assertTrue(call.kwargs["show"])

    async def test_slideshow_preserves_other_fields_and_uses_discovered_api(self):
        await self.controls.refresh()
        self.responses["set_slideshow_status"] = {}
        self.responses["get_slideshow_status"]["type"] = "shuffleslideshow"
        await self.controls.set_value("slideshow_order", "shuffleslideshow")
        call = next(
            c
            for c in self.client.request.call_args_list
            if c.args[0] == "set_slideshow_status"
        )
        self.assertEqual(call.kwargs["value"], "15")
        self.assertEqual(call.kwargs["category_id"], "MY-C0002")

    async def test_no_write_when_art_inactive(self):
        await self.controls.refresh()
        self.controls.values["art_mode"] = "off"
        self.client.request.reset_mock()
        with self.assertRaises(api.ArtError):
            await self.controls.set_value("brightness", 4)
        self.client.request.assert_not_called()

    async def test_offline_invalidates_values_but_retains_entity_identity(self):
        await self.controls.refresh()
        self.controls.push({"event": "disconnected"})
        self.assertFalse(self.controls.online)
        self.assertFalse(self.controls.values)
        self.assertIn("brightness", self.controls.supported)

    async def test_push_new_art_invalidates_old_mat(self):
        await self.controls.refresh()
        self.controls.push({"event": "artwork_changed", "content_id": "MY-2"})
        self.assertEqual(self.controls.values["artwork"], "MY-2")
        self.assertNotIn("mat_style", self.controls.values)

    async def test_mat_change_targets_current_picture_and_verifies_readback(self):
        await self.controls.refresh()
        self.responses["get_current_artwork"]["content_id"] = "MY-2"
        self.responses["change_matte"] = {}
        original = self.client.request.side_effect

        async def change(command, **kwargs):
            if command == "change_matte":
                self.responses["get_current_artwork"]["matte_id"] = kwargs["matte_id"]
            return await original(command, **kwargs)

        self.client.request.side_effect = change
        await self.controls.set_value("mat_color", "black")
        call = next(
            c for c in self.client.request.call_args_list if c.args[0] == "change_matte"
        )
        self.assertEqual(call.kwargs["content_id"], "MY-2")
        self.assertEqual(call.kwargs["matte_id"], "modern_black")

    async def test_missed_settings_reply_invalidates_old_values(self):
        await self.controls.refresh()
        self.responses["get_artmode_settings"] = api.ArtError("timeout", sent=True)
        await self.controls.read_settings()
        self.assertNotIn("motion_timer", self.controls.values)
        self.assertIn("motion_timer", self.controls.supported)

    async def test_setter_timeout_with_verified_readback(self):
        await self.controls.refresh()
        self.responses["set_brightness"] = api.ArtError("timeout", sent=True)
        await self.controls.set_value("brightness", 5)
        self.assertEqual(self.controls.values["brightness"], 5)

    async def test_slideshow_starts_from_off_without_type_or_category(self):
        self.responses["get_slideshow_status"] = {"value": "off", "category_id": ""}
        await self.controls.refresh()
        original = self.client.request.side_effect

        async def start(command, **kwargs):
            if command == "set_slideshow_status":
                self.responses["get_slideshow_status"] = {
                    "value": kwargs["value"],
                    "type": kwargs["type"],
                    "category_id": kwargs["category_id"],
                }
                return {}
            return await original(command, **kwargs)

        self.client.request.side_effect = start
        await self.controls.set_value("slideshow_interval", "15")
        self.assertEqual(self.controls.values["slideshow_interval"], "15")
        self.assertEqual(self.controls.values["slideshow_category"], "MY-C0002")

    async def test_order_category_while_stopped_are_explicit_next_start_choices(self):
        self.responses["get_slideshow_status"] = {"value": "off"}
        await self.controls.refresh()
        self.assertNotIn("slideshow_order", self.controls.values)
        self.client.request.reset_mock()
        await self.controls.set_value("slideshow_order", "shuffleslideshow")
        await self.controls.set_value("slideshow_category", "MY-C0004")
        self.assertTrue(
            all(
                not c.args[0].startswith("set_")
                for c in self.client.request.call_args_list
            )
        )
        await self.controls.read_slideshow()
        self.assertEqual(self.controls.values["slideshow_order"], "shuffleslideshow")
        self.assertEqual(self.controls.last_operation["phase"], "saved_for_next_start")

    async def test_legacy_complete_mat_ids_are_not_concatenated_twice(self):
        self.responses["get_matte_list"] = {
            "matte_list": '[{"matte_id":"none"},{"matte_id":"modern_thin_polar"},{"matte_id":"flexible_black"}]'
        }
        await self.controls.refresh()
        self.assertEqual(
            self.controls.options["mat_style"], ["none", "modern_thin", "flexible"]
        )
        original = self.client.request.side_effect

        async def change(command, **kwargs):
            if command == "change_matte":
                self.responses["get_current_artwork"]["matte_id"] = kwargs["matte_id"]
                return {}
            return await original(command, **kwargs)

        self.client.request.side_effect = change
        await self.controls.set_value("mat_style", "modern_thin")
        call = next(
            c for c in self.client.request.call_args_list if c.args[0] == "change_matte"
        )
        self.assertEqual(call.kwargs["matte_id"], "modern_thin_polar")
        self.assertEqual(self.controls.values["mat_style"], "modern_thin")

    async def test_legacy_plain_mat_id_needs_no_color(self):
        self.responses["get_matte_list"] = {"matte_list": '["none", "flexible"]'}
        self.responses["get_current_artwork"]["matte_id"] = "none"
        await self.controls.refresh()
        original = self.client.request.side_effect

        async def change(command, **kwargs):
            if command == "change_matte":
                self.assertEqual(kwargs["matte_id"], "flexible")
                self.responses["get_current_artwork"]["matte_id"] = "flexible"
                return {}
            return await original(command, **kwargs)

        self.client.request.side_effect = change
        await self.controls.set_value("mat_style", "flexible")

    async def test_delayed_mat_readback_retries_without_resending(self):
        await self.controls.refresh()
        original = self.client.request.side_effect
        reads = 0

        async def change(command, **kwargs):
            nonlocal reads
            if command == "change_matte":
                return {}
            if command == "get_current_artwork":
                reads += 1
                if reads >= 3:
                    return {"content_id": "MY-1", "matte_id": "modern_black"}
            return await original(command, **kwargs)

        self.client.request.side_effect = change
        with patch.object(controls_module.asyncio, "sleep", AsyncMock()):
            await self.controls.set_value("mat_color", "black")
        self.assertEqual(
            sum(
                c.args[0] == "change_matte" for c in self.client.request.call_args_list
            ),
            1,
        )

    async def test_unreadable_slideshow_fails_before_send_with_accurate_error(self):
        await self.controls.refresh()
        self.responses["get_slideshow_status"] = api.ArtError("rejected")
        with self.assertRaises(api.ArtError) as raised:
            await self.controls.set_value("slideshow_interval", "30")
        self.assertEqual(raised.exception.code, "state_unavailable")
        self.assertFalse(raised.exception.sent)

    async def test_diagnostics_excludes_credentials_addresses_and_artwork_titles(self):
        await self.controls.refresh()
        diagnostics = importlib.import_module("frame_under_test.diagnostics")
        entry = SimpleNamespace(
            data={"host": "secret-host", "token": "secret-token", "mac": "secret-mac"},
            options={"hdmi1": "secret-input-name"},
            runtime_data=SimpleNamespace(
                controls=self.controls,
                last_mode="art",
                last_input=None,
                volume=9,
                off_reported=False,
                ipc=SimpleNamespace(port=1516, token="secret-ipc-token"),
                ipc_auth_failed=False,
                ipc_error=None,
                input_reported="HDMI2",
                input_confirmed=True,
            ),
        )
        output = await diagnostics.async_get_config_entry_diagnostics(None, entry)
        encoded = json.dumps(output)
        self.assertNotIn("secret", encoded)
        self.assertNotIn("Flowers", encoded)
        self.assertEqual(output["slideshow_api"], "slideshow")
        self.assertEqual(output["media_player"]["input_names_configured"], ["hdmi1"])


class NativeControllerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reader = patch.object(
            frame, "read_power", AsyncMock(return_value=(None, "not_reported"))
        )
        reader.start()
        self.addCleanup(reader.stop)
        self.executor = AsyncMock()
        self.controller = frame.FrameController(
            "test", "aa:bb:cc:dd:ee:ff", None, Mock(), self.executor
        )

    async def asyncTearDown(self):
        await self.controller.async_close()

    async def test_mode_uses_native_async_client_not_subprocess(self):
        with (
            patch.object(frame, "port_open", AsyncMock(return_value=True)),
            patch.object(frame, "run_worker", AsyncMock()) as worker,
            patch.object(
                self.controller.art, "set_mode", AsyncMock(return_value=True)
            ) as mode,
        ):
            await self.controller.async_command("art")
            await self.controller.async_command("tv")
        self.assertEqual([c.args[0] for c in mode.call_args_list], [True, False])
        worker.assert_not_called()
        self.executor.assert_not_called()

    async def test_open_standby_port_unconfirmed_mode_gets_wol_and_fresh_connection(
        self,
    ):
        with (
            patch.object(frame, "port_open", AsyncMock(return_value=True)),
            patch.object(frame.asyncio, "sleep", AsyncMock()),
            patch.object(self.controller.art, "close", AsyncMock()) as close,
            patch.object(
                self.controller.art,
                "set_mode",
                AsyncMock(side_effect=[False, False, True, True]),
            ) as mode,
        ):
            self.assertTrue(await self.controller.async_command("art"))
        self.assertEqual(self.executor.await_count, 6)
        self.assertEqual(close.await_count, 2)
        self.assertEqual([c.args for c in mode.call_args_list], [(True,)] * 4)

    async def test_disconnect_after_send_recovers_with_same_mode(self):
        with (
            patch.object(frame, "port_open", AsyncMock(return_value=True)),
            patch.object(frame.asyncio, "sleep", AsyncMock()),
            patch.object(
                self.controller.art,
                "set_mode",
                AsyncMock(side_effect=[api.ArtError(sent=True), True, True]),
            ) as mode,
        ):
            self.assertTrue(await self.controller.async_command("tv"))
        self.assertEqual(self.executor.await_count, 3)
        self.assertEqual([c.args for c in mode.call_args_list], [(False,)] * 3)

    async def test_explicit_tv_rejection_never_causes_wake_retry(self):
        with (
            patch.object(frame, "port_open", AsyncMock(return_value=True)),
            patch.object(
                self.controller.art,
                "set_mode",
                AsyncMock(side_effect=api.ArtError("rejected", sent=True)),
            ) as mode,
        ):
            with self.assertRaises(frame.FrameError):
                await self.controller.async_command("art")
        mode.assert_awaited_once()
        self.executor.assert_not_called()

    async def test_slow_attempt_leaves_budget_for_recovery(self):
        count = 0

        async def mode(enabled):
            nonlocal count
            count += 1
            if count == 1:
                await asyncio.Event().wait()
            return True

        with (
            patch.object(frame, "port_open", AsyncMock(return_value=True)),
            patch.object(frame, "MODE_ATTEMPT_TIMEOUT", 0.01),
            patch.object(frame.asyncio, "sleep", AsyncMock()),
            patch.object(self.controller.art, "set_mode", side_effect=mode),
        ):
            self.assertTrue(await self.controller.async_command("art"))
        self.assertEqual(count, 3)
        self.assertEqual(self.executor.await_count, 3)

    async def test_refresh_offline_never_wakes(self):
        with patch.object(frame, "port_open", AsyncMock(return_value=False)):
            with self.assertRaises(frame.FrameError):
                await self.controller.async_refresh()
        self.executor.assert_not_called()

    async def test_stale_art_state_cannot_switch_tv_mode(self):
        self.controller.controls.online = True
        self.controller.controls.values["art_mode"] = "on"
        with patch.object(
            self.controller.art, "request", AsyncMock(return_value={"value": "off"})
        ) as request:
            with self.assertRaises(frame.FrameError) as raised:
                await self.controller.async_set_art_value("brightness", 4)
        self.assertEqual(raised.exception.code, "art_inactive")
        self.assertEqual(request.await_count, 1)
        self.executor.assert_not_called()
