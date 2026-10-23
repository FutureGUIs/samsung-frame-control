"""Native async remote-key channel (samsung.remote.control) on the local TV.

Used for input, channel and volume keys. The verified long power-key hold
stays in the isolated worker.

One connection is kept open between commands: measured on a QN55LS03T, the
TV kept an idle connection for 5 minutes (keep-alive pings answered every
30 s), and reusing it saved the 1.3-1.5 s connect. It is closed after
IDLE_CLOSE seconds unused, when the TV turns off, and on unload.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json

import aiohttp
from yarl import URL

from .async_art import HANDSHAKE_TIMEOUT, ArtError
from .const import CLIENT_NAME, PORT

# Default spacing between several keys in one call. (A per-command
# connection used to stay open this long after the key, as samsungtvws does;
# the kept connection makes that wait unnecessary.)
KEY_HOLD_OPEN = 1.0
IDLE_CLOSE = 15 * 60
HEARTBEAT = 30
SEND_ERRORS = (aiohttp.ClientError, OSError, RuntimeError)


class AsyncRemoteClient:
    def __init__(self, session, host, token, on_token):
        self.session, self.host, self.token = session, host, token
        self.on_token = on_token
        self._ws = None
        self._reader = None
        self._lock = asyncio.Lock()
        self._idle = None

    @property
    def connected(self) -> bool:
        return (
            self._ws is not None
            and not self._ws.closed
            and self._reader is not None
            and not self._reader.done()
        )

    async def _handshake(self, ws):
        while True:
            message = await ws.receive()
            if message.type != aiohttp.WSMsgType.TEXT:
                raise ArtError()
            try:
                frame = json.loads(message.data)
            except (ValueError, TypeError):
                raise ArtError("invalid_response") from None
            event = frame.get("event") if isinstance(frame, dict) else None
            if event == "ms.channel.unauthorized":
                raise ArtError("invalid_auth")
            if event in ("ms.channel.timeOut", "ms.channel.disconnect"):
                raise ArtError()
            if event == "ms.channel.connect":
                data = frame.get("data")
                token = data.get("token") if isinstance(data, dict) else None
                if token and str(token) != self.token:
                    self.token = str(token)
                    self.on_token(self.token)
                return

    async def _connect(self):
        query = {"name": base64.b64encode(CLIENT_NAME.encode()).decode()}
        if self.token:
            query["token"] = self.token
        url = URL.build(
            scheme="wss",
            host=self.host,
            port=PORT,
            path="/api/v2/channels/samsung.remote.control",
            query=query,
        )
        ws = None
        try:
            async with asyncio.timeout(HANDSHAKE_TIMEOUT):
                # Samsung uses a self-signed certificate on the local TV.
                ws = await self.session.ws_connect(url, ssl=False, heartbeat=HEARTBEAT)
                await self._handshake(ws)
        except BaseException:
            if ws is not None:
                await _close_ws(ws)
            raise
        self._ws = ws
        self._reader = asyncio.get_running_loop().create_task(self._listen(ws))

    async def _listen(self, ws):
        """Read (and discard) what the TV sends, so keep-alives are answered
        and a closed or timed-out connection is noticed at once."""
        try:
            while True:
                message = await ws.receive()
                if message.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                ):
                    return
                if message.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    event = json.loads(message.data).get("event")
                except (ValueError, TypeError, AttributeError):
                    continue
                if event in (
                    "ms.channel.timeOut",
                    "ms.channel.disconnect",
                    "ms.channel.unauthorized",
                ):
                    return
        except SEND_ERRORS:
            return
        finally:
            await _close_ws(ws)

    async def _drop(self):
        reader, ws = self._reader, self._ws
        self._reader = self._ws = None
        if self._idle is not None:
            self._idle.cancel()
            self._idle = None
        if reader is not None and reader is not asyncio.current_task():
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        if ws is not None:
            await _close_ws(ws)

    def _touch(self):
        """(Re)start the idle timer after use."""
        if self._idle is not None:
            self._idle.cancel()
        loop = asyncio.get_running_loop()
        self._idle = loop.call_later(IDLE_CLOSE, lambda: loop.create_task(self.close()))

    async def close(self) -> None:
        async with self._lock:
            await self._drop()

    async def send_key(self, key: str) -> None:
        """Send one Click. Delivery means the TV accepted the frame, nothing more."""
        await self.send_keys([key])

    async def send_keys(
        self,
        keys: list[str],
        gap: float = KEY_HOLD_OPEN,
        hold: float = 0.0,
        fresh: bool = False,
    ) -> None:
        """Send Clicks `gap` apart over the kept connection.

        `fresh` forces a new connection first. A kept connection that turns
        out to be dead before any key was sent is replaced once. `sent` on an
        error says whether any key reached the TV, so a partly typed number
        is never resent.
        """
        async with self._lock:
            if fresh:
                await self._drop()
            for attempt in (0, 1):
                reused = self.connected
                sent = False
                try:
                    if not reused:
                        await self._drop()
                        await self._connect()
                    for index, key in enumerate(keys):
                        if index:
                            await asyncio.sleep(gap)
                        async with asyncio.timeout(HANDSHAKE_TIMEOUT):
                            await self._ws.send_json(
                                {
                                    "method": "ms.remote.control",
                                    "params": {
                                        "Cmd": "Click",
                                        "DataOfCmd": key,
                                        "Option": "false",
                                        "TypeOfRemote": "SendRemoteKey",
                                    },
                                }
                            )
                        sent = True
                    if hold:
                        await asyncio.sleep(hold)
                    self._touch()
                    return
                except ArtError:
                    await self._drop()
                    raise
                except TimeoutError:
                    error = ArtError("timeout", sent=sent)
                except SEND_ERRORS:
                    error = ArtError(sent=sent)
                except asyncio.CancelledError:
                    await self._drop()
                    raise
                await self._drop()
                if reused and not sent and attempt == 0:
                    continue  # the kept connection had died; nothing was sent
                raise error


async def _close_ws(ws):
    if ws.closed:
        return
    with contextlib.suppress(TimeoutError, *SEND_ERRORS):
        async with asyncio.timeout(2):
            await ws.close()
