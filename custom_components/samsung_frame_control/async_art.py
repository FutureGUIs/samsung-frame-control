"""Isolated asynchronous Frame Art protocol, with no samsungtvws overrides.

Protocol and listener design informed by Nick Waterton's SamsungTVAsyncArt.
See THIRD_PARTY_NOTICES.md. SPDX-License-Identifier: LGPL-3.0-only

Upstream notices: Copyright (C) 2019 DSR! <xchwarze@gmail.com>
Copyright (C) 2021 Matthew Garrett <mjg59@srcf.ucam.org>
Copyright (C) 2024,2025 Nick Waterton <n.waterton@outlook.com>
"""

from __future__ import annotations

import asyncio
import base64
import json
import random
import ssl
import uuid

import aiohttp
from yarl import URL

HANDSHAKE_TIMEOUT = 15
THUMBNAIL_TIMEOUT = 10
# A Frame thumbnail is a few tens of KB (320x180 JPEG measured on a 2020
# Frame); refuse anything that does not look like one.
MAX_THUMBNAIL_BYTES = 4 * 1024 * 1024
MAX_D2D_HEADER = 64 * 1024
IMAGE_TYPES = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}


def split_json_tail(payload: bytes):
    """Older Art APIs send a binary frame: JSON followed by image bytes."""
    start = payload.find(b"{")
    if start < 0:
        raise ValueError
    # latin-1 maps one byte to one character, so `end` is a byte offset; the
    # JSON part itself is then decoded properly as UTF-8.
    _, end = json.JSONDecoder().raw_decode(payload[start:].decode("latin-1"))
    frame = json.loads(payload[start : start + end].decode("utf-8"))
    if not isinstance(frame, dict):
        raise ValueError
    return frame, payload[start + end :]


def sniff_image(data: bytes, file_type=None):
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return IMAGE_TYPES.get(str(file_type or "").lower())


def _no_verify_context():
    # The TV uses a self-signed certificate on its own LAN address.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


class ArtError(Exception):
    """Sanitized failure; never include a TV URL, token, or raw response."""

    def __init__(self, code="cannot_connect", *, sent=False):
        self.code, self.sent = code, sent
        super().__init__(code)


class AsyncArtClient:
    """One reader, bounded requests, exact ID matching, explicit lifecycle."""

    def __init__(self, session, host, token, on_token, on_event=None):
        # Construction is entirely local: no REST calls or synchronous client.
        self.session, self.host, self.token = session, host, token
        self.on_token, self.on_event = on_token, on_event
        self._ws = None
        self._reader = None
        self._pending = {}
        self._connect_lock = asyncio.Lock()
        self._request_lock = asyncio.Lock()
        self.on_request = None  # diagnostics: called with each request name

    @property
    def connected(self):
        return self._ws is not None and not self._ws.closed

    async def connect(self):
        async with self._connect_lock:
            if self.connected and self._reader and not self._reader.done():
                return
            await self.close()
            query = {"name": base64.b64encode(b"HA Frame Control").decode()}
            if self.token:
                query["token"] = self.token
            url = URL.build(
                scheme="wss",
                host=self.host,
                port=8002,
                path="/api/v2/channels/com.samsung.art-app",
                query=query,
            )
            try:
                async with asyncio.timeout(HANDSHAKE_TIMEOUT):
                    # Samsung uses a self-signed certificate on the local TV.
                    self._ws = await self.session.ws_connect(
                        url, ssl=False, heartbeat=30, max_msg_size=4 * 1024 * 1024
                    )
                    connected = ready = False
                    while not (connected and ready):
                        frame = await self._receive()
                        event = frame.get("event")
                        if event == "ms.channel.unauthorized":
                            raise ArtError("invalid_auth")
                        if event in ("ms.channel.timeOut", "ms.channel.disconnect"):
                            raise ArtError()
                        if event == "ms.channel.connect":
                            connected = True
                            token = frame.get("data", {}).get("token")
                            if token:
                                self.token = str(token)
                                self.on_token(self.token)
                        elif event == "ms.channel.ready":
                            ready = True
                self._reader = asyncio.create_task(self._listen())
            except BaseException as err:
                await self.close()
                if isinstance(err, (asyncio.CancelledError, ArtError)):
                    raise
                raise ArtError(
                    "timeout" if isinstance(err, TimeoutError) else "cannot_connect"
                ) from None

    async def _receive(self):
        message = await self._ws.receive()
        if message.type == aiohttp.WSMsgType.BINARY:
            try:
                frame, tail = split_json_tail(message.data)
            except (ValueError, TypeError, UnicodeDecodeError):
                raise ArtError("invalid_response") from None
            if tail:
                frame["_binary"] = tail
            return frame
        if message.type != aiohttp.WSMsgType.TEXT:
            raise ArtError()
        try:
            frame = json.loads(message.data)
            if not isinstance(frame, dict):
                raise ValueError
            return frame
        except (ValueError, TypeError):
            raise ArtError("invalid_response") from None

    async def _listen(self):
        try:
            while True:
                frame = await self._receive()
                if frame.get("event") == "ms.channel.unauthorized":
                    raise ArtError("invalid_auth")
                if frame.get("event") in (
                    "ms.channel.disconnect",
                    "ms.channel.timeOut",
                ):
                    raise ArtError()
                if frame.get("event") != "d2d_service_message":
                    continue
                try:
                    data = frame.get("data", {})
                    data = json.loads(data) if isinstance(data, str) else data
                    if not isinstance(data, dict):
                        continue
                    if frame.get("_binary"):
                        data = {**data, "_binary": frame["_binary"]}
                except (ValueError, TypeError):
                    continue
                event = data.get("event", "")
                request_id = data.get("request_id") or data.get("id")
                if event == "error" and not request_id:
                    try:
                        request = data.get("request_data", {})
                        request = (
                            json.loads(request) if isinstance(request, str) else request
                        )
                        request_id = request.get("request_id") or request.get("id")
                    except (ValueError, TypeError, AttributeError):
                        pass
                matched = False
                for key, (future, events) in tuple(self._pending.items()):
                    # Never let a late response with a different ID satisfy a request.
                    if request_id != key and (request_id or event not in events):
                        continue
                    matched = True
                    if not future.done():
                        if event == "error":
                            future.set_exception(ArtError("rejected", sent=True))
                        else:
                            future.set_result(data)
                    break
                if self.on_event and not matched and not request_id:
                    self.on_event(data)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            code = err.code if isinstance(err, ArtError) else "cannot_connect"
            for future, _ in tuple(self._pending.values()):
                if not future.done():
                    future.set_exception(ArtError(code, sent=True))
            if self.on_event:
                self.on_event({"event": "disconnected"})
        finally:
            if self._ws and not self._ws.closed:
                try:
                    async with asyncio.timeout(2):
                        await self._ws.close()
                except (TimeoutError, aiohttp.ClientError):
                    pass

    async def request(self, command, *, events=(), timeout=4, **params):
        if self.on_request:
            self.on_request(command)
        async with self._request_lock:
            await self.connect()
            key = str(uuid.uuid4())
            future = asyncio.get_running_loop().create_future()
            self._pending[key] = (future, set(events))
            data = {"request": command, "id": key, "request_id": key, **params}
            sent = False
            try:
                async with asyncio.timeout(timeout):
                    await self._ws.send_json(
                        {
                            "method": "ms.channel.emit",
                            "params": {
                                "event": "art_app_request",
                                "to": "host",
                                "data": json.dumps(data),
                            },
                        }
                    )
                    sent = True
                    return await future
            except asyncio.CancelledError:
                # Discard the connection so late id-less replies cannot leak into
                # the next command after cancellation.
                await self.close()
                raise
            except TimeoutError:
                await self.close()
                raise ArtError("timeout", sent=sent) from None
            except (aiohttp.ClientError, OSError):
                await self.close()
                raise ArtError(sent=sent) from None
            finally:
                self._pending.pop(key, None)
                if not future.done():
                    future.cancel()
                elif not future.cancelled():
                    future.exception()  # consume failures if sending failed first

    async def thumbnail(self, content_id: str) -> tuple[bytes, str]:
        """(image bytes, content type) of one artwork's thumbnail, read locally.

        get_thumbnail first; some firmware only answers get_thumbnail_list.
        """
        requests = (
            ("get_thumbnail", {"content_id": content_id}),
            ("get_thumbnail_list", {"content_id_list": [{"content_id": content_id}]}),
        )
        for index, (command, params) in enumerate(requests):
            conn_info = {
                "d2d_mode": "socket",
                "connection_id": random.randrange(2**32),
                "id": str(uuid.uuid4()),
            }
            try:
                reply = await self.request(
                    command,
                    events=(command,),
                    timeout=THUMBNAIL_TIMEOUT,
                    conn_info=conn_info,
                    **params,
                )
            except ArtError as err:
                if err.code == "rejected" and index + 1 < len(requests):
                    continue
                raise
            return await self._thumbnail_data(reply)
        raise ArtError("rejected", sent=True)  # pragma: no cover

    async def _thumbnail_data(self, reply) -> tuple[bytes, str]:
        inline = reply.get("_binary")
        if inline:
            content_type = sniff_image(inline)
            if content_type is None or len(inline) > MAX_THUMBNAIL_BYTES:
                raise ArtError("invalid_response", sent=True)
            return bytes(inline), content_type
        try:
            info = reply.get("conn_info") or {}
            if isinstance(info, str):
                info = json.loads(info)
            port = int(info["port"])
            if not 0 < port < 65536:
                raise ValueError
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ArtError("invalid_response", sent=True) from None
        writer = None
        try:
            async with asyncio.timeout(THUMBNAIL_TIMEOUT):
                # Always the TV's own address: a reply never redirects us elsewhere.
                reader, writer = await asyncio.open_connection(
                    self.host,
                    port,
                    ssl=_no_verify_context() if info.get("secured") else None,
                )
                size = int.from_bytes(await reader.readexactly(4), "big")
                if not 0 < size <= MAX_D2D_HEADER:
                    raise ValueError
                header = json.loads(await reader.readexactly(size))
                length = int(header["fileLength"])
                if not 0 < length <= MAX_THUMBNAIL_BYTES:
                    raise ValueError
                data = await reader.readexactly(length)
        except TimeoutError:
            raise ArtError("timeout", sent=True) from None
        except (OSError, asyncio.IncompleteReadError):
            raise ArtError("cannot_connect", sent=True) from None
        except (ValueError, TypeError, KeyError):
            raise ArtError("invalid_response", sent=True) from None
        finally:
            if writer is not None:
                writer.close()
                try:
                    async with asyncio.timeout(2):
                        await writer.wait_closed()
                except (TimeoutError, OSError):
                    pass
        content_type = sniff_image(data, header.get("fileType"))
        if content_type is None:
            raise ArtError("invalid_response", sent=True)
        return data, content_type

    async def set_mode(self, enabled):
        desired = "on" if enabled else "off"
        try:
            await self.request(
                "set_artmode_status",
                value=desired,
                events=("artmode_status", "art_mode_changed"),
            )
        except ArtError as err:
            if not (err.code == "timeout" and err.sent):
                raise
        # An acknowledgement is not proof the display changed. Query it.
        try:
            status = await self.request(
                "get_artmode_status", events=("artmode_status",)
            )
            return status.get("value", status.get("status")) == desired
        except ArtError as err:
            if err.code in ("timeout", "cannot_connect"):
                return False
            raise

    async def close(self):
        reader, ws = self._reader, self._ws
        self._reader = self._ws = None
        if reader and reader is not asyncio.current_task():
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        for future, _ in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(ArtError("cannot_connect", sent=True))
        if ws and not ws.closed:
            try:
                async with asyncio.timeout(2):
                    await ws.close()
            except (TimeoutError, aiohttp.ClientError):
                pass
