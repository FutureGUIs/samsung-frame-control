"""Config flow through Home Assistant's real flow manager (TV calls simulated)."""

from unittest.mock import patch

import pytest
from frame_fakes import DOMAIN, ENTRY_DATA, MAC, PKG
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.ssdp import SsdpServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.samsung_frame_control.frame import FrameError

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

USER = {
    "name": "Frame TV",
    "host": "192.168.1.25",
    "mac": "AA-BB-CC-DD-EE-FF",
    "broadcast_address": "255.255.255.255",
}


@pytest.fixture(autouse=True)
def no_setup():
    with (
        patch(f"{PKG}.async_setup_entry", return_value=True),
        patch(f"{PKG}.async_unload_entry", return_value=True),
    ):
        yield


def worker(*, info=None, token="paired", error=None):
    """Simulate the isolated worker's discover/pair actions."""
    calls = []

    async def run(host, token_in, action, callback, **options):
        calls.append((host, action))
        if error:
            raise error
        if action == "discover":
            options["on_info"](info or {})
        elif action == "pair":
            callback(token)
        return True

    return patch(f"{PKG}.config_flow.run_worker", side_effect=run), calls


async def start(hass, source=config_entries.SOURCE_USER, data=None):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": source}, data=data
    )


async def submit(hass, user_input, **worker_options):
    result = await start(hass)
    patcher, calls = worker(**worker_options)
    with patcher:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input
        )
    return result, calls


async def test_create_pairs_and_stores_token(hass):
    result, calls = await submit(hass, USER, token="new-token")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert calls == [("192.168.1.25", "pair")]
    assert result["data"]["mac"] == MAC
    assert result["data"]["token"] == "new-token"
    assert result["result"].unique_id == MAC


async def test_manual_host_lookup_obtains_mac_before_pairing(hass):
    data = {k: v for k, v in USER.items() if k != "mac"}
    result, calls = await submit(hass, data, info={"wifiMac": "AA-BB-CC-DD-EE-FF"})
    assert calls == [("192.168.1.25", "discover"), ("192.168.1.25", "pair")]
    assert result["data"]["mac"] == MAC


async def test_missing_mac_returns_override_error_without_pairing(hass):
    data = {k: v for k, v in USER.items() if k != "mac"}
    result, calls = await submit(hass, data, info={})
    assert result["errors"] == {"mac": "mac_unavailable"}
    assert calls == [("192.168.1.25", "discover")]


async def test_mac_lookup_timeout_is_recoverable(hass):
    result, _ = await submit(hass, {**USER, "mac": ""}, error=FrameError("timeout"))
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"mac": "mac_unavailable"}


async def test_mac_field_is_optional(hass):
    result = await start(hass)
    data = result["data_schema"]({"host": "192.168.1.25"})
    assert data["mac"] == ""


async def test_duplicate_never_contacts_tv(hass):
    MockConfigEntry(domain=DOMAIN, unique_id=MAC, data=dict(ENTRY_DATA)).add_to_hass(
        hass
    )
    result, calls = await submit(hass, USER)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert calls == []


async def test_invalid_fields_do_not_contact_tv(hass):
    bad = {**USER, "host": "http://tv/", "mac": "invalid", "broadcast_address": "x"}
    result, calls = await submit(hass, bad)
    assert set(result["errors"]) == {"host", "mac", "broadcast_address"}
    assert calls == []


@pytest.mark.parametrize(
    ("code", "error"), [("invalid_auth", "invalid_auth"), ("timeout", "cannot_connect")]
)
async def test_pairing_failures_return_form_errors(hass, code, error):
    result, _ = await submit(hass, USER, error=FrameError(code))
    assert result["errors"] == {"base": error}


async def test_reconfigure_retains_identity_replaces_token(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=MAC, title="Frame TV", data=dict(ENTRY_DATA)
    )
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    patcher, _ = worker(token="renewed")
    with patcher:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"host": "192.168.1.50", "broadcast_address": "255.255.255.255"},
        )
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["mac"] == MAC
    assert entry.data["token"] == "renewed"
    assert entry.data["host"] == "192.168.1.50"


def ssdp(location="http://192.168.1.25:9197/dmr", udn="uuid:frame-123"):
    return SsdpServiceInfo(
        ssdp_usn=f"{udn}::urn:samsung.com:device:RemoteControlReceiver:1",
        ssdp_st="urn:samsung.com:device:RemoteControlReceiver:1",
        ssdp_location=location,
        upnp={"UDN": udn} if udn else {},
    )


async def discover(hass, info=None, error=None, **ssdp_options):
    patcher, calls = worker(info=info, error=error)
    with patcher:
        result = await start(hass, config_entries.SOURCE_SSDP, ssdp(**ssdp_options))
    return result, calls


async def test_discovered_frame_prefills_form_without_pairing(hass):
    result, calls = await discover(
        hass,
        {"modelName": "QN55LS03T", "wifiMac": "AABBCCDDEEFF", "name": "[TV] Living"},
    )
    assert result["step_id"] == "discovery_confirm"
    assert calls == [("192.168.1.25", "discover")]
    defaults = result["data_schema"]({})
    assert defaults["host"] == "192.168.1.25"
    assert defaults["name"] == "Living"
    flow = hass.config_entries.flow.async_get(result["flow_id"])
    assert flow["context"]["unique_id"] == MAC


async def test_non_frame_samsung_is_ignored(hass):
    result, _ = await discover(
        hass, {"modelName": "QN55Q80T", "FrameTVSupport": "false"}
    )
    assert result["reason"] == "not_supported"


async def test_capability_flag_identifies_frame(hass):
    result, _ = await discover(
        hass, {"modelName": "Other", "FrameTVSupport": True, "wifiMac": "AABBCCDDEEFF"}
    )
    assert result["type"] is FlowResultType.FORM


async def test_missing_mac_uses_udn_and_asks_user(hass):
    result, _ = await discover(hass, {"modelName": "QN55LS03T", "wifiMac": "none"})
    flow = hass.config_entries.flow.async_get(result["flow_id"])
    assert flow["context"]["unique_id"] == "uuid:frame-123"
    assert result["data_schema"]({})["mac"] == ""


async def test_existing_host_avoids_probe(hass):
    MockConfigEntry(
        domain=DOMAIN, unique_id=MAC, data={**ENTRY_DATA, "host": "192.168.1.25"}
    ).add_to_hass(hass)
    result, calls = await discover(hass, {})
    assert result["reason"] == "already_configured"
    assert calls == []


async def test_existing_mac_updates_changed_address(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MAC, data=dict(ENTRY_DATA))
    entry.add_to_hass(hass)
    result, _ = await discover(hass, {"modelName": "QN55LS03T", "wifiMac": MAC})
    assert result["reason"] == "already_configured"
    assert entry.data["host"] == "192.168.1.25"


async def test_discovery_confirmation_pairs(hass):
    result, _ = await discover(hass, {"modelName": "QN55LS03T", "wifiMac": MAC})
    patcher, calls = worker()
    with patcher:
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert calls == [("192.168.1.25", "pair")]


async def test_discovery_timeout_does_not_create_card(hass):
    result, _ = await discover(hass, error=FrameError("timeout"), udn=None)
    assert result["reason"] == "cannot_connect"
