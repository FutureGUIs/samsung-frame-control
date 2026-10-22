"""Art entities on a real Home Assistant core with a simulated TV."""

from datetime import timedelta

import pytest
from frame_fakes import refresh
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

BRIGHTNESS = "number.frame_tv_art_brightness"
ARTWORK = "select.frame_tv_artwork"
AUTO = "switch.frame_tv_art_automatic_brightness"
RANDOM = "button.frame_tv_random_artwork"
REFRESH = "button.frame_tv_refresh_art_controls"
ORDER = "select.frame_tv_art_slideshow_order"
PLAYER = "media_player.frame_tv"


async def call(hass, domain, service, entity_id, **data):
    await hass.services.async_call(
        domain, service, {"entity_id": entity_id, **data}, blocking=True
    )
    await hass.async_block_till_done()


async def test_entities_appear_once_after_offline_start(hass, setup, tv, entry):
    tv.reachable = False
    await setup()
    assert hass.states.get(BRIGHTNESS) is None
    assert hass.states.get(PLAYER).state == STATE_UNKNOWN

    tv.reachable = True
    await refresh(hass, entry)
    await refresh(hass, entry)
    assert hass.states.get(BRIGHTNESS).state == "5.0"
    assert len(hass.states.async_entity_ids("number")) == 1


async def test_number_uses_reported_limits_and_verified_write(hass, setup, tv):
    await setup()
    state = hass.states.get(BRIGHTNESS)
    assert (state.attributes["min"], state.attributes["max"]) == (1, 10)
    await call(hass, "number", "set_value", BRIGHTNESS, value=7)
    assert tv.settings["brightness"]["value"] == "7"
    assert hass.states.get(BRIGHTNESS).state == "7.0"


async def test_artwork_labels_are_unique_and_map_back_to_ids(hass, setup, tv):
    await setup()
    state = hass.states.get(ARTWORK)
    assert state.attributes["options"] == ["Flowers [MY-1]", "Flowers [MY-2]"]
    assert state.state == "Flowers [MY-1]"
    await call(hass, "select", "select_option", ARTWORK, option="Flowers [MY-2]")
    assert tv.current == "MY-2"


async def test_auto_brightness_switch(hass, setup, tv):
    await setup()
    assert hass.states.get(AUTO).state == STATE_ON
    await call(hass, "switch", "turn_off", AUTO)
    assert tv.settings["brightness_sensor_setting"]["value"] == "off"
    assert hass.states.get(AUTO).state == STATE_OFF


async def test_random_artwork_avoids_current_picture(hass, setup, tv):
    await setup()
    await call(hass, "button", "press", RANDOM)
    assert tv.current == "MY-2"


async def test_refresh_button_never_changes_mode_or_wakes(hass, setup, tv):
    await setup()
    tv.art_requests.clear()
    await call(hass, "button", "press", REFRESH)
    assert ("get_content_list", {"category": None}) in tv.art_requests
    assert not [c for c in tv.art_requests if c[0] == "set_artmode_status"]
    assert tv.wol_packets == 0


async def test_controls_unavailable_in_tv_mode(hass, setup, tv, entry):
    await setup()
    tv.art = "off"
    entry.runtime_data._last_art_refresh = 0
    await refresh(hass, entry)
    assert hass.states.get(BRIGHTNESS).state == STATE_UNAVAILABLE
    assert hass.states.get(RANDOM).state == STATE_UNAVAILABLE
    assert hass.states.get(PLAYER).attributes.get("source") != "Art Mode"


async def test_stopped_slideshow_order_is_a_next_start_choice(hass, setup, tv):
    tv.slideshow = {"value": "off"}
    await setup()
    state = hass.states.get(ORDER)
    assert state.state == STATE_UNKNOWN
    assert state.attributes["applies_on_next_start"] is True
    tv.art_requests.clear()
    await call(hass, "select", "select_option", ORDER, option="Shuffle")
    assert hass.states.get(ORDER).state == "Shuffle"
    assert not [c for c in tv.art_requests if c[0] == "set_slideshow_status"]


async def test_reported_power_off_overrides_remembered_art_mode(hass, setup, tv, entry):
    await setup()
    assert hass.states.get(PLAYER).attributes["source"] == "Art Mode"
    tv.power = "off"  # Samsung keeps reporting the remembered Art setting.
    await refresh(hass, entry)
    assert hass.states.get(PLAYER).state == STATE_OFF
    assert "source" not in hass.states.get(PLAYER).attributes
    assert hass.states.get(BRIGHTNESS).state == STATE_UNAVAILABLE


async def test_unknown_power_cannot_show_art_on(hass, setup, tv, entry):
    await setup()
    tv.power = None
    entry.runtime_data._last_art_refresh = 0
    await refresh(hass, entry)
    state = hass.states.get(PLAYER)
    assert state.state == STATE_UNKNOWN
    assert "source" not in state.attributes  # never Art Mode without power
    assert state.attributes["power_source"] == "not_reported"


async def test_push_events_do_not_postpone_power_poll(hass, setup, tv, entry):
    await setup()
    reads = tv.power_reads
    for _ in range(10):
        entry.runtime_data._event({"event": "art_mode_changed", "status": "on"})
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=16))
    await hass.async_block_till_done()
    assert tv.power_reads > reads


async def test_settings_are_configuration_and_artwork_stays_a_control(hass, setup):
    from homeassistant.const import EntityCategory
    from homeassistant.helpers import entity_registry as er

    await setup()
    registry = er.async_get(hass)
    for entity_id in (BRIGHTNESS, AUTO, ORDER, REFRESH):
        assert registry.async_get(entity_id).entity_category is EntityCategory.CONFIG
    for entity_id in (ARTWORK, RANDOM, PLAYER, "sensor.frame_tv_activity"):
        assert registry.async_get(entity_id).entity_category is None


async def test_power_and_art_mode_sensors_are_retired(hass, setup, entry):
    from frame_fakes import DOMAIN, MAC
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    for key in ("power_state", "art_mode"):
        registry.async_get_or_create(
            "sensor", DOMAIN, f"{MAC}_{key}", config_entry=entry
        )
    await setup()
    for key in ("power_state", "art_mode"):
        assert registry.async_get_entity_id("sensor", DOMAIN, f"{MAC}_{key}") is None
    assert hass.states.get("sensor.frame_tv_current_artwork").state == "MY-1"
