"""Artwork thumbnail transfer: the TV's one-off local socket and inline frames.

Shapes measured on a 2020 Frame (QN55LS03T): get_thumbnail answers with
conn_info {ip, port, secured: false}; the socket sends a 4-byte header length,
a JSON header {fileLength, fileType: "jpg", fileID, num, total}, then the JPEG.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest

from custom_components.samsung_frame_control import async_art
from custom_components.samsung_frame_control.async_art import ArtError, AsyncArtClient

JPEG = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 100 + b"\xff\xd9"


def packet(data=JPEG, **header):
    head = json.dumps(
        {
            "num": "0",
            "total": "1",
            "fileLength": len(data),
            "fileType": "jpg",
            "fileID": "MY_F0001",
            **header,
        }
    ).encode()
    return len(head).to_bytes(4, "big") + head + data


@pytest.fixture
async def d2d(socket_enabled):
    """A local one-shot file socket like the TV's; returns (port, state)."""
    state = {"send": packet(), "connections": 0}

    async def serve(reader, writer):
        state["connections"] += 1
        writer.write(state["send"])
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield port, state
    server.close()
    await server.wait_closed()


def client(reply):
    art = AsyncArtClient(None, "127.0.0.1", None, lambda token: None)
    art.request = AsyncMock(side_effect=reply if isinstance(reply, list) else [reply])
    return art


def conn_info(port, ip="127.0.0.1"):
    # The TV sends conn_info as a JSON string.
    return {"conn_info": json.dumps({"ip": ip, "port": port, "secured": False})}


async def test_thumbnail_read_from_the_tv_socket(d2d):
    port, state = d2d
    art = client(conn_info(port))
    assert await art.thumbnail("MY_F0001") == (JPEG, "image/jpeg")
    command = art.request.call_args.args[0]
    params = art.request.call_args.kwargs
    assert command == "get_thumbnail"
    assert params["content_id"] == "MY_F0001"
    assert params["conn_info"]["d2d_mode"] == "socket"
    assert state["connections"] == 1


async def test_reply_cannot_redirect_the_download_elsewhere(d2d):
    port, state = d2d
    # A reply naming another address is still fetched from the TV's own host.
    art = client(conn_info(port, ip="203.0.113.9"))
    assert (await art.thumbnail("MY_F0001"))[0] == JPEG
    assert state["connections"] == 1


async def test_refused_get_thumbnail_falls_back_to_the_list_request(d2d):
    port, _ = d2d
    art = client([ArtError("rejected", sent=True), conn_info(port)])
    assert (await art.thumbnail("MY_F0001"))[0] == JPEG
    second = art.request.call_args_list[1]
    assert second.args[0] == "get_thumbnail_list"
    assert second.kwargs["content_id_list"] == [{"content_id": "MY_F0001"}]


async def test_both_refused_is_rejected():
    art = client([ArtError("rejected", sent=True), ArtError("rejected", sent=True)])
    with pytest.raises(ArtError) as raised:
        await art.thumbnail("MY_F0001")
    assert raised.value.code == "rejected"


async def test_timeout_is_not_retried_with_the_list_request():
    art = client([ArtError("timeout", sent=True)])
    with pytest.raises(ArtError) as raised:
        await art.thumbnail("MY_F0001")
    assert raised.value.code == "timeout"
    assert art.request.await_count == 1


@pytest.mark.parametrize(
    "send",
    [
        packet(fileLength=50 * 1024 * 1024),  # far larger than any thumbnail
        b"\x7f\xff\xff\xff",  # absurd header length
        packet(data=b"<html>not an image</html>", fileType="html"),
    ],
    ids=["huge-file", "huge-header", "not-an-image"],
)
async def test_implausible_transfer_is_refused(d2d, send):
    port, state = d2d
    state["send"] = send
    with pytest.raises(ArtError) as raised:
        await client(conn_info(port)).thumbnail("MY_F0001")
    assert raised.value.code == "invalid_response"


async def test_transfer_cut_short_is_a_connection_error(d2d):
    port, state = d2d
    state["send"] = packet()[:-500]
    with pytest.raises(ArtError) as raised:
        await client(conn_info(port)).thumbnail("MY_F0001")
    assert raised.value.code == "cannot_connect"


async def test_missing_conn_info_is_invalid():
    with pytest.raises(ArtError) as raised:
        await client({"event": "get_thumbnail"}).thumbnail("MY_F0001")
    assert raised.value.code == "invalid_response"


async def test_inline_image_from_older_art_api():
    art = client({"event": "get_thumbnail", "_binary": JPEG})
    assert await art.thumbnail("MY_F0001") == (JPEG, "image/jpeg")


# -- binary WebSocket frames (older Art API) ----------------------------------


def test_binary_frame_splits_json_and_image():
    body = {"event": "d2d_service_message", "data": json.dumps({"id": "x", "é": 1})}
    frame, tail = async_art.split_json_tail(json.dumps(body).encode() + JPEG)
    assert frame == body
    assert tail == JPEG


class BinarySocket:
    """Art WebSocket whose thumbnail reply arrives as JSON + image bytes."""

    def __init__(self):
        self.closed = False
        self.queue = asyncio.Queue()
        for event in ("ms.channel.connect", "ms.channel.ready"):
            self.text({"event": event, "data": {}})

    def text(self, data):
        self.queue.put_nowait(
            SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(data))
        )

    async def receive(self):
        return await self.queue.get()

    async def send_json(self, message):
        request = json.loads(message["params"]["data"])
        reply = {
            "event": "d2d_service_message",
            "data": json.dumps({"event": "get_thumbnail", "id": request["id"]}),
        }
        self.queue.put_nowait(
            SimpleNamespace(
                type=aiohttp.WSMsgType.BINARY, data=json.dumps(reply).encode() + JPEG
            )
        )

    async def close(self):
        self.closed = True


async def test_inline_thumbnail_through_the_art_channel():
    socket = BinarySocket()
    session = SimpleNamespace(ws_connect=AsyncMock(return_value=socket))
    art = AsyncArtClient(session, "192.0.2.1", None, lambda token: None)
    try:
        assert await art.thumbnail("MY_F0001") == (JPEG, "image/jpeg")
    finally:
        await art.close()
