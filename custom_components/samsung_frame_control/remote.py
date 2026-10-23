"""Native async remote-key channel (samsung.remote.control) on the local TV.

Used only for input-selection keys. The verified long power-key hold stays in
the isolated worker. A connection is opened per command and always closed.
"""

from __future__ import annotations

import asyncio
import base64
import json

import aiohttp
from yarl import URL

from .async_art import HANDSHAKE_TIMEOUT, ArtError
from .const import CLIENT_NAME, PORT

# The TV can drop a key whose connection closes immediately; samsungtvws (the
# library verified on the user's TV) waits one second after every send.
KEY_HOLD_OPEN = 1.0


class AsyncRemoteClient:
    def __init__(self, session, host, token, on_token):
        self.session, self.host, self.token = session, host, token
        self.on_token = on_token

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

    async def send_key(self, key: str) -> None:
        """Send one Click. Delivery means the TV accepted the frame, nothing more."""
        await self.send_keys([key])

    async def send_keys(
        self, keys: list[str], gap: float = KEY_HOLD_OPEN, hold: float = KEY_HOLD_OPEN
    ) -> None:
        """Send Clicks over one connection, `gap` apart, open `hold` after.

        Used for typed channel numbers. `sent` on an error says whether any
        key reached the TV, so a partly typed number is never resent.
        """
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
        sent = False
        try:
            async with asyncio.timeout(HANDSHAKE_TIMEOUT):
                # Samsung uses a self-signed certificate on the local TV.
                ws = await self.session.ws_connect(url, ssl=False, heartbeat=30)
                await self._handshake(ws)
            for index, key in enumerate(keys):
                if index:
                    await asyncio.sleep(gap)
                async with asyncio.timeout(HANDSHAKE_TIMEOUT):
                    await ws.send_json(
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
            await asyncio.sleep(hold)
        except ArtError:
            raise
        except TimeoutError:
            raise ArtError("timeout", sent=sent) from None
        except (aiohttp.ClientError, OSError):
            raise ArtError(sent=sent) from None
        finally:
            if ws is not None and not ws.closed:
                try:
                    async with asyncio.timeout(2):
                        await ws.close()
                except (TimeoutError, aiohttp.ClientError):
                    pass
