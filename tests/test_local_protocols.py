"""UPnP volume over real local HTTP, and the remote-key channel framing."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import pytest
from aiohttp import web

from custom_components.samsung_frame_control import remote, upnp
from custom_components.samsung_frame_control.async_art import ArtError

RESPONSE = (
    '<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:{action}Response xmlns:u="urn:schemas-upnp-org:service:RenderingControl:1">'
    "{body}</u:{action}Response></s:Body></s:Envelope>"
)


@pytest.fixture
async def renderer(socket_enabled):
    """A local RenderingControl endpoint that records SOAP calls."""
    state = {"volume": 12, "mute": "0", "calls": [], "fault": False}

    async def control(request):
        action = request.headers["SOAPAction"].strip('"').rsplit("#", 1)[1]
        body = await request.text()
        state["calls"].append((action, body))
        if state["fault"]:
            return web.Response(status=500, text="<fault/>")
        if action == "GetVolume":
            payload = f"<CurrentVolume>{state['volume']}</CurrentVolume>"
        elif action == "GetMute":
            payload = f"<CurrentMute>{state['mute']}</CurrentMute>"
        else:
            payload = ""
        return web.Response(
            text=RESPONSE.format(action=action, body=payload), content_type="text/xml"
        )

    app = web.Application()
    app.router.add_post(upnp.CONTROL_PATH, control)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    async with aiohttp.ClientSession() as session:
        with patch.object(upnp, "PORT", port):
            yield session, state
    await runner.cleanup()


async def test_volume_and_mute_round_trip(renderer):
    session, state = renderer
    assert await upnp.get_volume(session, "127.0.0.1") == 12
    assert await upnp.get_mute(session, "127.0.0.1") is False
    await upnp.set_volume(session, "127.0.0.1", 40)
    await upnp.set_mute(session, "127.0.0.1", True)
    actions = [action for action, _ in state["calls"]]
    assert actions == ["GetVolume", "GetMute", "SetVolume", "SetMute"]
    assert "<DesiredVolume>40</DesiredVolume>" in state["calls"][2][1]
    assert "<Channel>Master</Channel>" in state["calls"][2][1]
    assert "<DesiredMute>1</DesiredMute>" in state["calls"][3][1]


async def test_soap_fault_is_rejection_and_garbage_is_invalid(renderer):
    session, state = renderer
    state["fault"] = True
    with pytest.raises(upnp.UpnpError) as raised:
        await upnp.get_volume(session, "127.0.0.1")
    assert raised.value.code == "rejected"
    state["fault"], state["volume"] = False, "loud"
    with pytest.raises(upnp.UpnpError) as raised:
        await upnp.get_volume(session, "127.0.0.1")
    assert raised.value.code == "invalid_response"


async def test_out_of_range_volume_never_sent(renderer):
    session, state = renderer
    with pytest.raises(upnp.UpnpError):
        await upnp.set_volume(session, "127.0.0.1", 101)
    assert state["calls"] == []


async def test_unreachable_renderer_is_sanitized(socket_enabled):
    async with aiohttp.ClientSession() as session:
        with patch.object(upnp, "PORT", 9):  # discard port: connection refused
            with pytest.raises(upnp.UpnpError) as raised:
                await upnp.get_volume(session, "127.0.0.1")
    assert raised.value.code == "cannot_connect"
    assert "127.0.0.1" not in str(raised.value)


class Socket:
    def __init__(self, *frames):
        self.closed = False
        self.sent = []
        self.queue = asyncio.Queue()
        for frame in frames:
            self.queue.put_nowait(
                SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(frame))
            )

    async def receive(self):
        return await self.queue.get()

    async def send_json(self, data):
        self.sent.append(data)

    async def close(self):
        self.closed = True


async def test_remote_key_waits_for_connect_and_saves_new_token():
    socket = Socket({"event": "ms.channel.connect", "data": {"token": "fresh"}})
    session = SimpleNamespace(ws_connect=AsyncMock(return_value=socket))
    saved = Mock()
    client = remote.AsyncRemoteClient(session, "192.0.2.1", "old", saved)
    await client.send_key("KEY_HDMI3")
    url = session.ws_connect.call_args.args[0]
    assert url.path == "/api/v2/channels/samsung.remote.control"
    assert url.query["token"] == "old"
    assert socket.sent == [
        {
            "method": "ms.remote.control",
            "params": {
                "Cmd": "Click",
                "DataOfCmd": "KEY_HDMI3",
                "Option": "false",
                "TypeOfRemote": "SendRemoteKey",
            },
        }
    ]
    saved.assert_called_once_with("fresh")
    assert socket.closed


async def test_remote_unauthorized_sends_nothing_and_closes():
    socket = Socket({"event": "ms.channel.unauthorized"})
    session = SimpleNamespace(ws_connect=AsyncMock(return_value=socket))
    client = remote.AsyncRemoteClient(session, "192.0.2.1", None, Mock())
    with pytest.raises(ArtError) as raised:
        await client.send_key("KEY_HDMI1")
    assert raised.value.code == "invalid_auth"
    assert socket.sent == []
    assert socket.closed


async def test_remote_handshake_timeout_is_bounded():
    socket = Socket()  # never sends ms.channel.connect
    session = SimpleNamespace(ws_connect=AsyncMock(return_value=socket))
    client = remote.AsyncRemoteClient(session, "192.0.2.1", None, Mock())
    with patch.object(remote, "HANDSHAKE_TIMEOUT", 0.05):
        with pytest.raises(ArtError) as raised:
            await client.send_key("KEY_HDMI1")
    assert (raised.value.code, raised.value.sent) == ("timeout", False)
    assert socket.closed


async def test_remote_key_connection_stays_open_after_sending():
    """The TV can drop a key if the socket closes right away (samsungtvws waits 1 s)."""
    socket = Socket({"event": "ms.channel.connect"})
    session = SimpleNamespace(ws_connect=AsyncMock(return_value=socket))
    events = []

    async def sleep(seconds):
        events.append(("sleep", seconds, bool(socket.sent), socket.closed))

    client = remote.AsyncRemoteClient(session, "192.0.2.1", "t", Mock())
    with patch.object(remote.asyncio, "sleep", sleep):
        await client.send_key("KEY_HDMI2")
    assert events == [("sleep", remote.KEY_HOLD_OPEN, True, False)]
    assert remote.KEY_HOLD_OPEN >= 1
    assert socket.closed


async def test_typed_number_is_spaced_on_one_connection():
    """Digits closer than ~0.5 s were dropped by a 2020 Frame; Enter tunes."""
    socket = Socket({"event": "ms.channel.connect"})
    session = SimpleNamespace(ws_connect=AsyncMock(return_value=socket))
    events = []

    async def sleep(seconds):
        events.append((len(socket.sent), seconds, socket.closed))

    client = remote.AsyncRemoteClient(session, "192.0.2.1", "t", Mock())
    with patch.object(remote.asyncio, "sleep", sleep):
        await client.send_keys(["KEY_1", "KEY_4", "KEY_ENTER"], gap=0.6, hold=0.5)
    assert [m["params"]["DataOfCmd"] for m in socket.sent] == [
        "KEY_1",
        "KEY_4",
        "KEY_ENTER",
    ]
    # Gap after each key but the last, then the hold; one connection.
    assert events == [(1, 0.6, False), (2, 0.6, False), (3, 0.5, False)]
    assert session.ws_connect.await_count == 1
    assert socket.closed


async def test_failure_after_a_digit_reports_it_was_sent():
    socket = Socket({"event": "ms.channel.connect"})
    session = SimpleNamespace(ws_connect=AsyncMock(return_value=socket))
    original = socket.send_json

    async def fail_second(data):
        if socket.sent:
            raise aiohttp.ClientError("gone")
        await original(data)

    socket.send_json = fail_second
    client = remote.AsyncRemoteClient(session, "192.0.2.1", "t", Mock())
    with patch.object(remote.asyncio, "sleep", AsyncMock()):
        with pytest.raises(ArtError) as raised:
            await client.send_keys(["KEY_1", "KEY_4"])
    assert raised.value.sent is True
