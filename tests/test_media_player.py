"""Media player behaviour on a real Home Assistant core with a simulated TV."""

import pytest
from frame_fakes import DOMAIN, MAC, refresh
from homeassistant.components.media_player import (
    ATTR_INPUT_SOURCE,
    ATTR_INPUT_SOURCE_LIST,
    ATTR_MEDIA_VOLUME_LEVEL,
    ATTR_MEDIA_VOLUME_MUTED,
)
from homeassistant.const import STATE_OFF, STATE_UNKNOWN
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    mock_restore_cache_with_extra_data,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

ENTITY = "media_player.frame_tv"


async def open_inputs(hass, entry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "inputs"}
    )


async def call(hass, service, **data):
    await hass.services.async_call(
        "media_player", service, {"entity_id": ENTITY, **data}, blocking=True
    )
    await hass.async_block_till_done()


async def test_setup_replaces_legacy_mode_buttons(hass, setup, entry):
    registry = er.async_get(hass)
    for key in ("art", "tv", "off", "refresh_art"):
        registry.async_get_or_create(
            "button", DOMAIN, f"{MAC}_{key}", config_entry=entry
        )
    await setup()
    for key in ("art", "tv", "off"):
        assert registry.async_get_entity_id("button", DOMAIN, f"{MAC}_{key}") is None
    assert registry.async_get_entity_id("button", DOMAIN, f"{MAC}_refresh_art")
    assert (
        registry.async_get_entity_id("media_player", DOMAIN, f"{MAC}_media_player")
        == ENTITY
    )

    state = hass.states.get(ENTITY)
    assert state.state == "playing"  # Art Mode, slideshow running
    assert state.attributes[ATTR_INPUT_SOURCE] == "Art Mode"
    assert state.attributes[ATTR_INPUT_SOURCE_LIST] == [
        "Art Mode",
        "Samsung TV",
        "HDMI 1",
        "HDMI 2",
        "HDMI 3",
        "HDMI 4",
    ]
    assert state.attributes[ATTR_MEDIA_VOLUME_LEVEL] == 0.2
    assert state.attributes[ATTR_MEDIA_VOLUME_MUTED] is False
    assert state.attributes["source_confirmed"] is True


async def test_reported_off_is_kept_while_tv_does_not_answer(hass, setup, tv, entry):
    await setup()
    tv.power = "off"
    await refresh(hass, entry)
    state = hass.states.get(ENTITY)
    assert state.state == STATE_OFF
    assert ATTR_INPUT_SOURCE not in state.attributes
    assert ATTR_MEDIA_VOLUME_LEVEL not in state.attributes

    # Off was the last known state: it is kept while the TV does not answer.
    tv.reachable = False
    await refresh(hass, entry)
    assert hass.states.get(ENTITY).state == STATE_OFF
    assert hass.states.get(ENTITY).attributes["power_source"] == "last_known"


async def test_tv_last_seen_on_that_disappears_is_unknown(hass, setup, tv, entry):
    await setup()
    tv.reachable = False
    await refresh(hass, entry)
    assert hass.states.get(ENTITY).state == STATE_UNKNOWN


async def test_select_hdmi_leaves_art_mode_then_sends_input_key(hass, setup, tv):
    await setup()
    await call(hass, "select_source", source="HDMI 2")
    assert tv.art == "off"
    assert tv.keys == ["KEY_HDMI2"]
    assert tv.wol_packets == 0
    state = hass.states.get(ENTITY)
    assert state.attributes[ATTR_INPUT_SOURCE] == "HDMI 2"
    # The current input cannot be read locally, so it is never claimed as fact.
    assert state.attributes["source_confirmed"] is False


async def test_select_art_mode_sends_no_input_key(hass, setup, tv, entry):
    tv.art = "off"
    await setup()
    await call(hass, "select_source", source="Art Mode")
    assert tv.art == "on"
    assert tv.keys == []
    assert hass.states.get(ENTITY).attributes[ATTR_INPUT_SOURCE] == "Art Mode"


async def test_unknown_source_is_rejected_without_contacting_tv(hass, setup, tv):
    await setup()
    tv.art_requests.clear()
    with pytest.raises(HomeAssistantError):
        await call(hass, "select_source", source="Netflix")
    assert tv.keys == []
    assert not [c for c in tv.art_requests if c[0] == "set_artmode_status"]


async def test_turn_on_from_full_power_off_wakes_into_art_mode(hass, setup, tv):
    tv.reachable, tv.power = False, "off"
    await setup()
    await call(hass, "turn_on")
    assert tv.wol_packets >= 1
    assert tv.art == "on"
    assert hass.states.get(ENTITY).attributes[ATTR_INPUT_SOURCE] == "Art Mode"


async def test_turn_on_restores_tv_mode_across_restart(hass, setup, tv):
    mock_restore_cache_with_extra_data(
        hass,
        ((State(ENTITY, STATE_OFF), {"last_mode": "tv", "last_input": "hdmi3"}),),
    )
    tv.reachable, tv.power, tv.art = False, "off", "on"
    await setup()
    await call(hass, "turn_on")
    assert tv.art == "off"
    # The TV resumes its own last input, which also tracks the physical remote.
    assert tv.keys == []
    assert hass.states.get(ENTITY).attributes[ATTR_INPUT_SOURCE] == "HDMI 3"


async def test_mode_changed_on_tv_is_remembered_for_turn_on(hass, setup, tv, entry):
    await setup()
    entry.runtime_data._event({"event": "art_mode_changed", "status": "off"})
    await hass.async_block_till_done()
    await call(hass, "turn_off")
    assert tv.worker_actions == ["off"]
    await refresh(hass, entry)
    assert hass.states.get(ENTITY).state == STATE_OFF
    await call(hass, "turn_on")
    assert tv.art == "off"


async def test_turn_off_sends_single_power_hold(hass, setup, tv):
    await setup()
    await call(hass, "turn_off")
    assert tv.worker_actions == ["off"]
    assert tv.wol_packets == 0


async def test_volume_and_mute_are_set_then_read_back(hass, setup, tv):
    tv.art = "off"  # volume is offered outside Art Mode
    await setup()
    await call(hass, "volume_set", volume_level=0.35)
    assert tv.volume == 35
    await call(hass, "volume_mute", is_volume_muted=True)
    assert tv.muted is True
    state = hass.states.get(ENTITY)
    assert state.attributes[ATTR_MEDIA_VOLUME_LEVEL] == 0.35
    assert state.attributes[ATTR_MEDIA_VOLUME_MUTED] is True

    await call(hass, "volume_up")
    assert tv.volume == 36
    await call(hass, "volume_down")
    assert tv.volume == 35


async def test_volume_not_applied_by_tv_is_an_error(hass, setup, tv):
    tv.art = "off"  # volume is offered outside Art Mode
    await setup()
    tv.apply_audio = False
    with pytest.raises(HomeAssistantError, match="did not confirm"):
        await call(hass, "volume_set", volume_level=0.5)
    assert hass.states.get(ENTITY).attributes[ATTR_MEDIA_VOLUME_LEVEL] == 0.2


async def test_volume_step_uses_remote_key_when_level_unreadable(hass, setup, tv):
    tv.art = "off"  # volume is offered outside Art Mode
    tv.audio_available = False
    await setup()
    assert ATTR_MEDIA_VOLUME_LEVEL not in hass.states.get(ENTITY).attributes
    await call(hass, "volume_up")
    assert tv.keys == ["KEY_VOLUP"]


async def test_volume_change_refused_while_off(hass, setup, tv, entry):
    await setup()
    tv.power = "off"
    await refresh(hass, entry)
    with pytest.raises(HomeAssistantError, match="off or unreachable"):
        await call(hass, "volume_set", volume_level=0.5)


async def test_input_key_authorization_failure_is_actionable(hass, setup, tv):
    from custom_components.samsung_frame_control.async_art import ArtError

    await setup()
    tv.remote_error = ArtError("invalid_auth")
    with pytest.raises(HomeAssistantError, match="authorization"):
        await call(hass, "select_source", source="HDMI 1")


async def test_input_key_retries_transient_connection_loss(hass, setup, tv):
    from custom_components.samsung_frame_control import remote
    from custom_components.samsung_frame_control.async_art import ArtError

    await setup()
    failures = [ArtError("cannot_connect")]
    original = remote.AsyncRemoteClient.send_key

    async def flaky(client, key):
        if failures:
            raise failures.pop()
        await original(client, key)

    remote.AsyncRemoteClient.send_key = flaky
    try:
        await call(hass, "select_source", source="HDMI 4")
    finally:
        remote.AsyncRemoteClient.send_key = original
    assert tv.keys == ["KEY_HDMI4"]


async def test_input_names_and_visibility_come_from_options(hass, setup, tv, entry):
    await setup()
    result = await open_inputs(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "tv": "",
            "hdmi1": "Apple TV",
            "hdmi2": "HDMI 2",
            "hdmi3": "",
            "hdmi4": "Xbox",
        },
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()  # options flow reloads the entry
    state = hass.states.get(ENTITY)
    assert state.attributes[ATTR_INPUT_SOURCE_LIST] == [
        "Art Mode",
        "Apple TV",
        "HDMI 2",
        "Xbox",
    ]
    await call(hass, "select_source", source="Apple TV")
    assert tv.keys == ["KEY_HDMI1"]


@pytest.mark.parametrize(
    ("names", "error"),
    [
        ({"hdmi1": "Game", "hdmi2": "Game"}, "duplicate_source"),
        ({"hdmi1": "Art Mode"}, "reserved_source"),
    ],
)
async def test_invalid_input_names_are_rejected(hass, setup, entry, names, error):
    await setup()
    result = await open_inputs(hass, entry)
    blank = {"tv": "", "hdmi1": "", "hdmi2": "", "hdmi3": "", "hdmi4": ""}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**blank, **names}
    )
    assert result["type"] == "form"
    assert result["errors"] == {"base": error}


async def test_unload_closes_cleanly(hass, setup, entry):
    await setup()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state.value == "not_loaded"
