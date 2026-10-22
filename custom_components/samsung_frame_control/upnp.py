"""Local UPnP RenderingControl (port 9197) for volume level and mute.

Samsung Tizen TVs expose a DLNA renderer on the LAN; no pairing, token or
cloud service is involved. Values are read back, never assumed.
"""

from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET

import aiohttp
from yarl import URL

SERVICE = "urn:schemas-upnp-org:service:RenderingControl:1"
CONTROL_PATH = "/upnp/control/RenderingControl1"
PORT = 9197
TIMEOUT = 3
MAX_RESPONSE = 64 * 1024


class UpnpError(Exception):
    """Sanitized failure; never includes the TV address or response body."""

    def __init__(self, code="cannot_connect"):
        self.code = code
        super().__init__(code)


def _envelope(action, arguments):
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        f'<s:Body><u:{action} xmlns:u="{SERVICE}">'
        f"<InstanceID>0</InstanceID><Channel>Master</Channel>{arguments}"
        f"</u:{action}></s:Body></s:Envelope>"
    )


def _field(body, name):
    """Return the text of the first element whose local name matches."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise UpnpError("invalid_response") from None
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == name:
            return (element.text or "").strip()
    raise UpnpError("invalid_response")


async def _call(session, host, action, arguments=""):
    url = URL.build(scheme="http", host=host, port=PORT, path=CONTROL_PATH)
    headers = {
        "SOAPAction": f'"{SERVICE}#{action}"',
        "Content-Type": 'text/xml; charset="utf-8"',
    }
    try:
        async with asyncio.timeout(TIMEOUT):
            async with session.post(
                url, data=_envelope(action, arguments), headers=headers
            ) as response:
                body = await response.content.read(MAX_RESPONSE)
                if response.status >= 400:
                    # A SOAP fault (e.g. out-of-range volume) is a rejection.
                    raise UpnpError(
                        "rejected" if response.status == 500 else "cannot_connect"
                    )
                return body
    except (aiohttp.ClientError, OSError, TimeoutError):
        raise UpnpError() from None


async def get_volume(session, host) -> int:
    value = _field(await _call(session, host, "GetVolume"), "CurrentVolume")
    try:
        volume = int(value)
    except ValueError:
        raise UpnpError("invalid_response") from None
    if not 0 <= volume <= 100:
        raise UpnpError("invalid_response")
    return volume


async def get_mute(session, host) -> bool:
    value = _field(await _call(session, host, "GetMute"), "CurrentMute")
    if value not in ("0", "1"):
        raise UpnpError("invalid_response")
    return value == "1"


async def set_volume(session, host, volume: int) -> None:
    if not 0 <= int(volume) <= 100:
        raise UpnpError("invalid_value")
    await _call(
        session, host, "SetVolume", f"<DesiredVolume>{int(volume)}</DesiredVolume>"
    )


async def set_mute(session, host, muted: bool) -> None:
    await _call(
        session, host, "SetMute", f"<DesiredMute>{1 if muted else 0}</DesiredMute>"
    )
