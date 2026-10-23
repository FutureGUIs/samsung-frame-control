"""Home Assistant fixtures for tests that run the real integration.

Mark a module with ``pytestmark = pytest.mark.usefixtures("enable_custom_integrations")``.
The older protocol tests are plain unittest cases and need no HA instance.
"""

from unittest.mock import patch

import pytest
from frame_fakes import DOMAIN, ENTRY_DATA, MAC, PKG, FakeTV, fast_asyncio
from pytest_homeassistant_custom_component.common import MockConfigEntry

# Import before the hass fixture mounts HA's own testing_config/custom_components.
import custom_components.samsung_frame_control  # noqa: F401
from custom_components.samsung_frame_control import (
    art_controls,
    async_art,
    config_flow,
    ipcontrol,
    remote,
)


@pytest.fixture
def tv():
    fake = FakeTV()

    with (
        patch(f"{PKG}.frame.read_power", side_effect=fake.read_power),
        patch(f"{PKG}.frame.port_open", side_effect=fake.port_open),
        patch(f"{PKG}.frame.send_magic_packet", side_effect=fake.magic_packet),
        patch(f"{PKG}.frame.run_worker", side_effect=fake.run_worker),
        patch(f"{PKG}.frame.asyncio", fast_asyncio()),
        patch.object(art_controls, "asyncio", fast_asyncio()),
        # Plain functions so the client instance is passed like a real method.
        patch.object(
            async_art.AsyncArtClient,
            "request",
            lambda client, *args, **kwargs: fake.art_request(client, *args, **kwargs),
        ),
        patch.object(
            async_art.AsyncArtClient,
            "thumbnail",
            lambda client, content_id: fake.thumbnail(client, content_id),
        ),
        patch.object(
            remote.AsyncRemoteClient,
            "send_key",
            lambda client, key: fake.send_key(client, key),
        ),
        patch.object(
            remote.AsyncRemoteClient,
            "send_keys",
            lambda client, keys: fake.send_keys(client, keys),
        ),
        patch.object(
            ipcontrol.IpControl, "read_state", lambda c: fake.ipc_read_state(c)
        ),
        patch.object(ipcontrol.IpControl, "pair", lambda c: fake.ipc_pair(c)),
        patch.object(
            ipcontrol.IpControl,
            "call",
            lambda c, method, params=None, **kw: fake.ipc_call(c, method, params, **kw),
        ),
        patch.object(config_flow, "port_open", side_effect=fake.port_open),
        patch(f"{PKG}.upnp.get_volume", side_effect=fake.get_volume),
        patch(f"{PKG}.upnp.get_mute", side_effect=fake.get_mute),
        patch(f"{PKG}.upnp.set_volume", side_effect=fake.set_volume),
        patch(f"{PKG}.upnp.set_mute", side_effect=fake.set_mute),
    ):
        yield fake


@pytest.fixture(autouse=True)
def no_ssdp_listener(request):
    """HA's ssdp dependency would open multicast sockets; not under test here."""
    if "hass" not in request.fixturenames:
        yield
        return
    with patch("homeassistant.components.ssdp.async_setup", return_value=True):
        yield


@pytest.fixture
def entry(hass):
    config_entry = MockConfigEntry(
        domain=DOMAIN, title="Frame TV", unique_id=MAC, data=dict(ENTRY_DATA)
    )
    config_entry.add_to_hass(hass)
    return config_entry


@pytest.fixture
def setup(hass, tv, entry):
    """Return a coroutine that loads the integration and waits for it."""

    async def _setup(options=None, ip_control=False):
        if options is not None:
            hass.config_entries.async_update_entry(entry, options=options)
        if ip_control:
            hass.config_entries.async_update_entry(
                entry,
                data={
                    **entry.data,
                    "ip_control_token": "ipc-token",
                    "ip_control_port": 1516,
                },
            )
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        return entry

    return _setup
