"""Read the TV's local PowerState without opening a control connection."""

import asyncio

import aiohttp
from yarl import URL


def _refused(err):
    """True when the TV's host actively rejected the connection (port closed)."""
    if isinstance(err, ConnectionRefusedError):
        return True
    return isinstance(err, aiohttp.ClientConnectorError) and isinstance(
        err.os_error, ConnectionRefusedError
    )


async def read_power(session, host):
    """Return (on/off/None, source).

    source: local_rest, not_reported (answered without PowerState), refused (the
    host rejected every connection: its network stack is up but the TV's
    services are not, as in standby) or unreachable (no answer at all). Neither
    refused nor unreachable alone is proof of standby.
    """
    responded = False
    refused = True
    for scheme, port in (("https", 8002), ("http", 8001)):
        url = URL.build(scheme=scheme, host=host, port=port, path="/api/v2/")
        try:
            async with asyncio.timeout(3):
                async with session.get(
                    url, ssl=False, allow_redirects=False
                ) as response:
                    response.raise_for_status()
                    data = await response.json(content_type=None)
            responded = True
            device = data.get("device") if isinstance(data, dict) else None
            value = device.get("PowerState") if isinstance(device, dict) else None
            if isinstance(value, str) and value.lower() in ("on", "off"):
                return value.lower(), "local_rest"
        except (aiohttp.ClientError, OSError, TimeoutError, ValueError) as err:
            refused = refused and _refused(err)
            continue
    if responded:
        return None, "not_reported"
    return None, "refused" if refused else "unreachable"
