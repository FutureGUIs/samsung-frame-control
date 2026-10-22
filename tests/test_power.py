"""Power is observed separately from the remembered Art Mode setting."""

import importlib
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
from test_frame import frame

power = importlib.import_module("frame_under_test.power")


class Response:
    def __init__(self, payload=None, error=None):
        self.payload, self.error = payload, error

    async def __aenter__(self):
        if self.error:
            raise self.error
        return self

    async def __aexit__(self, *args):
        pass

    def raise_for_status(self):
        pass

    async def json(self, **kwargs):
        return self.payload


class PowerReaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_reported_power_is_independent_of_art_setting(self):
        for state in ("on", "off"):
            session = SimpleNamespace(
                get=Mock(
                    return_value=Response(
                        {"device": {"PowerState": state, "ArtMode": "on"}}
                    )
                )
            )
            self.assertEqual(
                await power.read_power(session, "192.0.2.1"), (state, "local_rest")
            )
            url = session.get.call_args.args[0]
            self.assertEqual(url.path, "/api/v2/")
            self.assertEqual(url.port, 8002)
            self.assertFalse(url.query)
            self.assertFalse(session.get.call_args.kwargs["allow_redirects"])

    async def test_fallback_to_8001(self):
        session = SimpleNamespace(
            get=Mock(
                side_effect=[
                    Response(error=aiohttp.ClientConnectionError()),
                    Response({"device": {"PowerState": "off"}}),
                ]
            )
        )
        self.assertEqual(await power.read_power(session, "tv"), ("off", "local_rest"))
        self.assertEqual(session.get.call_args.args[0].port, 8001)

    async def test_unreachable_is_unknown_not_off(self):
        session = SimpleNamespace(get=Mock(return_value=Response(error=TimeoutError())))
        self.assertEqual(await power.read_power(session, "tv"), (None, "unreachable"))

    async def test_missing_or_invalid_field_is_not_inferred(self):
        for payload in (
            {"device": {}},
            {"device": {"PowerState": "unknown"}},
            {"device": []},
            [],
        ):
            session = SimpleNamespace(get=Mock(return_value=Response(payload)))
            self.assertEqual(
                await power.read_power(session, "tv"), (None, "not_reported")
            )


class PowerControllerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.controller = frame.FrameController(
            "tv", "aa:bb:cc:dd:ee:ff", None, Mock(), AsyncMock(), session=Mock()
        )
        self.controls = self.controller.controls
        self.controls.online = True
        self.controls.values = {"art_mode": "on", "brightness": 4}
        self.reader = patch.object(
            frame, "read_power", AsyncMock(return_value=("on", "local_rest"))
        )
        self.read = self.reader.start()
        self.addCleanup(self.reader.stop)

    async def asyncTearDown(self):
        await self.controller.async_close()

    async def test_off_clears_stale_art_and_does_not_query_or_wake(self):
        self.read.return_value = ("off", "local_rest")
        with patch.object(self.controls, "refresh", AsyncMock()) as refresh:
            await self.controller.async_refresh()
        self.assertEqual(self.controls.power_state, "off")
        self.assertEqual(self.controls.values, {"art_mode": "off"})
        self.assertFalse(self.controls.online)
        refresh.assert_not_called()
        self.controller._executor.assert_not_called()

    async def test_unreachable_clears_stale_art_without_inventing_off(self):
        self.read.return_value = (None, "unreachable")
        await self.controller.async_refresh()
        self.assertIsNone(self.controls.power_state)
        self.assertFalse(self.controls.values)
        self.controller._executor.assert_not_called()

    async def test_cached_art_event_cannot_override_power_off(self):
        self.controls.set_power("off", "local_rest")
        self.controls.push({"event": "artmode_status", "value": "on"})
        self.controls.push({"event": "art_mode_changed", "status": "on"})
        self.assertEqual(self.controls.values["art_mode"], "off")

    async def test_standby_event_updates_power_immediately(self):
        self.controls.push({"event": "go_to_standby"})
        self.assertEqual(self.controls.power_state, "off")
        self.assertEqual(self.controls.power_source, "standby_event")
        self.assertEqual(self.controls.values["art_mode"], "off")

    async def test_art_socket_failure_does_not_mean_tv_powered_off(self):
        self.controls.set_power("on", "local_rest")
        self.controls.push({"event": "disconnected"})
        self.assertEqual(self.controls.power_state, "on")
        self.assertFalse(self.controls.online)

    async def test_power_checked_each_poll_art_settings_throttled(self):
        self.controls.set_power("on", "local_rest")
        with (
            patch.object(frame, "port_open", AsyncMock(return_value=True)),
            patch.object(self.controls, "refresh", AsyncMock()) as refresh,
        ):
            await self.controller.async_refresh()
            await self.controller.async_refresh()
        self.assertEqual(self.read.await_count, 2)
        self.assertEqual(refresh.await_count, 1)

    async def test_power_off_prevents_setting_write_despite_cached_art_on(self):
        self.read.return_value = ("off", "local_rest")
        with patch.object(self.controller.art, "request", AsyncMock()) as request:
            with self.assertRaises(frame.FrameError) as raised:
                await self.controller.async_set_art_value("brightness", 5)
        self.assertEqual(raised.exception.code, "art_inactive")
        request.assert_not_called()
        self.controller._executor.assert_not_called()
