"""IP Control: observed input, explicit off and standby, on real Home Assistant."""

import asyncio

import pytest
from frame_fakes import DOMAIN, refresh
from homeassistant.components.media_player import ATTR_INPUT_SOURCE
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import State
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import (
    mock_restore_cache_with_extra_data,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

ENTITY = "media_player.frame_tv"


def source(hass):
    state = hass.states.get(ENTITY)
    return (
        state.attributes.get(ATTR_INPUT_SOURCE),
        state.attributes.get("source_confirmed"),
    )


async def call(hass, service, **data):
    await hass.services.async_call(
        "media_player", service, {"entity_id": ENTITY, **data}, blocking=True
    )
    await hass.async_block_till_done()


# -- observed input -------------------------------------------------------


async def test_input_changed_with_remote_is_observed(hass, setup, tv, entry):
    tv.art = "off"
    await setup(ip_control=True)
    assert source(hass) == ("HDMI 1", True)

    tv.ipc_input = "HDMI2"  # changed with the physical remote
    await refresh(hass, entry)
    assert source(hass) == ("HDMI 2", True)
    assert entry.runtime_data.last_input == "hdmi2"

    tv.ipc_input = "TV"  # Samsung TV Plus
    await refresh(hass, entry)
    assert source(hass) == ("Samsung TV", True)


async def test_art_mode_shows_art_while_input_behind_it_is_remembered(
    hass, setup, tv, entry
):
    tv.ipc_input = "HDMI2"
    await setup(ip_control=True)
    assert source(hass) == ("Art Mode", True)
    # Measured on a QN55LS03T: the input behind the art is still reported.
    assert entry.runtime_data.last_input == "hdmi2"


async def test_renamed_and_unlisted_inputs(hass, setup, tv):
    tv.art, tv.ipc_input = "off", "HDMI3"
    await setup(options={"hdmi3": "Apple TV"}, ip_control=True)
    assert source(hass) == ("Apple TV", True)


async def test_unknown_reported_input_is_shown_as_reported(hass, setup, tv):
    tv.art, tv.ipc_input = "off", "USB"
    await setup(ip_control=True)
    assert source(hass) == ("USB", True)


async def test_hidden_input_still_shows_when_tv_is_on_it(hass, setup, tv):
    tv.art, tv.ipc_input = "off", "HDMI4"
    await setup(options={"hdmi4": ""}, ip_control=True)
    assert source(hass) == ("HDMI 4", True)


async def test_select_samsung_tv_sends_tv_key(hass, setup, tv):
    await setup()
    await call(hass, "select_source", source="Samsung TV")
    assert tv.art == "off"
    assert tv.keys == ["KEY_TV"]


async def test_selected_input_is_verified_from_the_tv(hass, setup, tv, entry):
    await setup(ip_control=True)  # TV in Art Mode on HDMI1
    await call(hass, "select_source", source="HDMI 3")
    assert tv.art == "off"
    assert tv.keys == ["KEY_HDMI3"]
    assert source(hass) == ("HDMI 3", True)


async def test_ignored_input_key_is_resent_once_then_reported(hass, setup, tv):
    from homeassistant.exceptions import HomeAssistantError

    tv.art = "off"
    tv.keys_switch_input = False
    await setup(ip_control=True)
    with pytest.raises(HomeAssistantError, match="still reports a different input"):
        await call(hass, "select_source", source="HDMI 2")
    assert tv.keys == ["KEY_HDMI2", "KEY_HDMI2"]
    assert source(hass) == ("HDMI 1", True)


async def test_ip_control_input_setter_is_used_when_supported(hass, setup, tv):
    tv.art = "off"
    tv.ipc_input_setter = True
    await setup(ip_control=True)
    await call(hass, "select_source", source="HDMI 4")
    assert ("inputSourceControl", {"inputSource": "HDMI4"}) in tv.ipc_commands
    assert tv.keys == []
    assert source(hass) == ("HDMI 4", True)


async def test_unsupported_input_setter_is_not_retried(hass, setup, tv, entry):
    tv.art = "off"
    await setup(ip_control=True)
    await call(hass, "select_source", source="HDMI 2")
    await call(hass, "select_source", source="HDMI 3")
    assert tv.keys == ["KEY_HDMI2", "KEY_HDMI3"]
    assert entry.runtime_data.ipc_input_setter is False


async def test_already_on_requested_input_sends_nothing(hass, setup, tv):
    tv.art = "off"
    await setup(ip_control=True)
    await call(hass, "select_source", source="HDMI 1")
    assert tv.keys == []


async def test_input_after_wake_unverifiable_is_not_an_error(hass, setup, tv, entry):
    await setup(ip_control=True)
    tv.ipc_error = "refused"  # IP Control still starting after wake
    await call(hass, "select_source", source="HDMI 2")
    assert tv.keys == ["KEY_HDMI2"]
    assert source(hass) == ("HDMI 2", False)


async def test_input_without_ip_control_waits_after_leaving_art(hass, setup, tv, entry):
    from custom_components.samsung_frame_control import frame

    await setup()
    await call(hass, "select_source", source="HDMI 2")
    sleeps = [c.args[0] for c in frame.asyncio.sleep.await_args_list]
    assert frame.INPUT_SETTLE in sleeps
    assert tv.keys == ["KEY_HDMI2"]


async def test_ip_control_starting_after_wake_keeps_last_input(hass, setup, tv, entry):
    tv.art, tv.ipc_input = "off", "HDMI2"
    await setup(ip_control=True)
    assert source(hass) == ("HDMI 2", True)
    # Measured: after power-on REST answers ~18 s before IP Control does.
    tv.ipc_error = "refused"
    await refresh(hass, entry)
    assert hass.states.get(ENTITY).state == STATE_ON
    assert source(hass) == ("HDMI 2", False)
    tv.ipc_error = None
    await refresh(hass, entry)
    assert source(hass) == ("HDMI 2", True)


async def test_unpaired_behaviour_is_unchanged(hass, setup, tv):
    tv.art = "off"
    await setup()
    assert source(hass) == (None, None)
    assert tv.ipc_calls == 0


# -- power and standby ----------------------------------------------------


async def test_explicit_off_then_refused_connections_is_off(hass, setup, tv, entry):
    await setup(ip_control=True)
    # Measured: IP Control says powerOff while REST still says on.
    tv.ipc_power = "powerOff"
    await refresh(hass, entry)
    assert hass.states.get(ENTITY).state == STATE_OFF
    assert hass.states.get(ENTITY).attributes["power_source"] == "ip_control"

    # ~20 s later every port refuses: standby.
    tv.reachable, tv.refuses = False, True
    await refresh(hass, entry)
    assert hass.states.get(ENTITY).state == STATE_OFF
    assert hass.states.get(ENTITY).attributes["power_source"] == "standby"

    # Deep standby (no answer at all): keep the last known off.
    tv.refuses = False
    await refresh(hass, entry)
    assert hass.states.get(ENTITY).state == STATE_OFF
    assert hass.states.get(ENTITY).attributes["power_source"] == "last_known"


async def test_refused_without_explicit_off_is_unknown(hass, setup, tv, entry):
    await setup(ip_control=True)
    tv.reachable, tv.refuses = False, True
    await refresh(hass, entry)
    assert hass.states.get(ENTITY).state == STATE_UNKNOWN


async def test_rest_on_with_ipc_timeout_does_not_cancel_shutdown(
    hass, setup, tv, entry
):
    await setup(ip_control=True)
    tv.ipc_power = "powerOff"
    await refresh(hass, entry)
    tv.ipc_error = "timeout"  # IP Control stops first; REST still says on
    await refresh(hass, entry)
    tv.reachable, tv.refuses = False, True
    await refresh(hass, entry)
    assert hass.states.get(ENTITY).state == STATE_OFF


async def test_ha_power_off_then_standby_is_off_without_ip_control(
    hass, setup, tv, entry
):
    await setup()
    await call(hass, "turn_off")
    tv.reachable, tv.refuses = False, True
    await refresh(hass, entry)
    assert hass.states.get(ENTITY).state == STATE_OFF


async def test_power_on_clears_explicit_off(hass, setup, tv, entry):
    await setup(ip_control=True)
    tv.ipc_power = "powerOff"
    await refresh(hass, entry)
    tv.ipc_power = "powerOn"
    await refresh(hass, entry)
    assert hass.states.get(ENTITY).state == "playing"  # Art Mode, slideshow running
    # A later refusal (e.g. a network blip) is then unknown, not off.
    tv.reachable, tv.refuses = False, True
    await refresh(hass, entry)
    assert hass.states.get(ENTITY).state == STATE_UNKNOWN


async def test_standby_survives_home_assistant_restart(hass, setup, tv, entry):
    mock_restore_cache_with_extra_data(
        hass,
        (
            (
                State(ENTITY, STATE_OFF),
                {"last_mode": "art", "last_input": "hdmi2", "off_reported": True},
            ),
        ),
    )
    tv.reachable, tv.refuses = False, True
    await setup(ip_control=True)
    await refresh(hass, entry)
    assert hass.states.get(ENTITY).state == STATE_OFF


async def test_turn_on_from_standby(hass, setup, tv, entry):
    await setup(ip_control=True)
    tv.ipc_power = "powerOff"
    await refresh(hass, entry)
    tv.reachable, tv.refuses, tv.power = False, True, "off"
    await refresh(hass, entry)
    await call(hass, "turn_on")
    assert tv.wol_packets >= 1
    await refresh(hass, entry)
    assert hass.states.get(ENTITY).state == "playing"  # Art Mode, slideshow running
    assert source(hass)[0] == "Art Mode"


# -- token rejection, audio fallback ------------------------------------


async def test_rejected_token_raises_repair_issue_and_falls_back(
    hass, setup, tv, entry
):
    tv.art = "off"
    tv.ipc_error = "unauthorized"
    await setup(ip_control=True)
    issue_id = f"ip_control_unauthorized_{entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    calls = tv.ipc_calls
    await refresh(hass, entry)
    assert tv.ipc_calls == calls  # stops polling with a rejected token
    assert hass.states.get(ENTITY).state == STATE_ON


async def test_volume_falls_back_to_ip_control_reading(hass, setup, tv):
    tv.audio_available = False
    tv.volume = 27
    await setup(ip_control=True)
    assert hass.states.get(ENTITY).attributes["volume_level"] == 0.27


# -- options flow pairing -----------------------------------------------


async def open_menu(hass, entry, step):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step}
    )


async def test_pairing_stores_token_and_enables_observed_input(hass, setup, tv, entry):
    tv.art, tv.ipc_input = "off", "HDMI2"
    await setup()
    result = await open_menu(hass, entry, "ip_control_pair")
    assert result["step_id"] == "ip_control_pair"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()  # reload with the new token
    assert entry.data["ip_control_token"] == "ipc-token"
    assert entry.data["ip_control_port"] == 1516
    assert source(hass) == ("HDMI 2", True)


async def test_pairing_uses_1515_on_older_tvs(hass, setup, tv, entry):
    tv.ipc_ports = {1515}
    await setup()
    result = await open_menu(hass, entry, "ip_control_pair")
    await hass.config_entries.options.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert entry.data["ip_control_port"] == 1515


@pytest.mark.parametrize(
    ("ports", "pair_result", "error"),
    [
        (set(), "ipc-token", "ip_control_closed"),
        ({1516}, "timeout", "ip_control_timeout"),
        ({1516}, "failed", "ip_control_failed"),
    ],
)
async def test_pairing_errors(hass, setup, tv, entry, ports, pair_result, error):
    tv.ipc_ports, tv.pair_result = ports, pair_result
    await setup()
    result = await open_menu(hass, entry, "ip_control_pair")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] == "form"
    assert result["errors"] == {"base": error}
    assert "ip_control_token" not in entry.data


async def test_unpair_removes_token_and_repair_issue(hass, setup, tv, entry):
    tv.ipc_error = "unauthorized"
    await setup(ip_control=True)
    issue_id = f"ip_control_unauthorized_{entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    result = await open_menu(hass, entry, "ip_control_unpair")
    await hass.config_entries.options.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert "ip_control_token" not in entry.data
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_unpair_only_offered_when_paired(hass, setup, entry):
    await setup()
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["menu_options"] == ["inputs", "channels", "ip_control_pair"]


# -- power-off ------------------------------------------------------------


async def test_turn_off_uses_explicit_ip_control_power_off(hass, setup, tv, entry):
    await setup(ip_control=True)  # in Art Mode
    await call(hass, "turn_off")
    assert ("powerControl", {"power": "powerOff"}) in tv.ipc_commands
    assert tv.worker_actions == []  # no power-key toggle through TV mode
    assert tv.art == "on"
    assert hass.states.get(ENTITY).state == STATE_OFF


async def test_turn_off_falls_back_to_power_key_when_not_delivered(
    hass, setup, tv, entry
):
    tv.ipc_off_error = "refused"
    await setup(ip_control=True)
    await call(hass, "turn_off")
    assert tv.worker_actions == ["off"]


async def test_turn_off_timeout_never_adds_a_power_key(hass, setup, tv, entry):
    from homeassistant.exceptions import HomeAssistantError

    tv.ipc_off_error = "timeout"
    await setup(ip_control=True)
    with pytest.raises(HomeAssistantError):
        await call(hass, "turn_off")
    assert tv.worker_actions == []


async def test_turn_off_without_ip_control_uses_power_key(hass, setup, tv):
    await setup()
    await call(hass, "turn_off")
    assert tv.worker_actions == ["off"]
    assert tv.ipc_commands == []


# -- turning on after an IP Control power-off ---------------------------------


async def test_turn_on_after_ip_control_power_off(hass, setup, tv, entry):
    await setup(ip_control=True)
    await call(hass, "turn_off")
    await refresh(hass, entry)
    assert hass.states.get(ENTITY).state == STATE_OFF
    assert tv.reachable  # measured: still answering on the network
    await call(hass, "turn_on")
    assert ("powerControl", {"power": "powerOn"}) in tv.ipc_commands
    assert tv.power == "on"
    assert hass.states.get(ENTITY).state == "playing"  # Art Mode, slideshow running
    assert source(hass)[0] == "Art Mode"


async def test_remembered_art_setting_is_not_a_successful_turn_on(
    hass, setup, tv, entry
):
    from homeassistant.exceptions import HomeAssistantError

    await setup(ip_control=True)
    await call(hass, "turn_off")
    await refresh(hass, entry)
    tv.ipc_power_on_works = False
    tv.wake_on_lan = False
    with pytest.raises(HomeAssistantError):
        await call(hass, "turn_on")
    assert tv.power == "off"
    await refresh(hass, entry)
    assert hass.states.get(ENTITY).state == STATE_OFF


async def test_select_input_from_ip_control_off_wakes_first(hass, setup, tv, entry):
    await setup(ip_control=True)
    await call(hass, "turn_off")
    await refresh(hass, entry)
    await call(hass, "select_source", source="HDMI 2")
    assert tv.power == "on"
    assert tv.art == "off"
    assert source(hass) == ("HDMI 2", True)


async def test_reported_off_with_open_port_still_sends_wake_on_lan(
    hass, setup, tv, entry
):
    await setup()  # no IP Control
    tv.power = "off"  # REST reports off while the port is open
    await refresh(hass, entry)
    tv.power = "on"
    await call(hass, "turn_on")
    assert tv.wol_packets >= 1


# -- responsiveness -------------------------------------------------------


async def test_turn_off_does_not_wait_for_a_slow_art_refresh(hass, setup, tv, entry):
    await setup(ip_control=True)
    controller = entry.runtime_data
    tv.art_delay = 0.4  # a full Art refresh is now several seconds
    controller._last_art_refresh = 0
    refresh = hass.async_create_task(controller.coordinator.async_refresh())
    await asyncio.sleep(0.1)  # the refresh is inside its first Art request
    await call(hass, "turn_off")
    await refresh
    assert controller.last_command["action"] == "off"
    assert controller.last_command["path"] == "ip_control"
    assert controller.last_command["waited_s"] < 1.0
    assert hass.states.get(ENTITY).state == STATE_OFF


async def test_stopped_refresh_finishes_on_the_next_poll(hass, setup, tv, entry):
    await setup(ip_control=True)
    controller = entry.runtime_data
    controller._last_art_refresh = 0
    controller._waiting_commands = 1  # as if a command were queued
    await refresh(hass, entry)
    assert controller._last_art_refresh == 0  # not counted as a full refresh
    controller._waiting_commands = 0
    await refresh(hass, entry)
    assert controller._last_art_refresh > 0
    assert hass.states.get("number.frame_tv_art_brightness").state == "5.0"


async def test_off_to_input_skips_reassert_when_ip_control_confirms_on(
    hass, setup, tv, entry
):
    await setup(ip_control=True)
    await call(hass, "turn_off")
    await refresh(hass, entry)
    tv.art_requests.clear()
    await call(hass, "select_source", source="HDMI 2")
    sets = [p for c, p in tv.art_requests if c == "set_artmode_status"]
    assert sets == [{"value": "off"}]  # no 5 s reassert after a verified wake
    steps = entry.runtime_data.last_command["steps"]
    assert list(steps) == ["ip_control_power_on", "mode_tv", "input"]
    assert source(hass) == ("HDMI 2", True)


# -- live progress -------------------------------------------------------------


def record_activity(hass):
    seen = []

    def changed(event):
        new = event.data.get("new_state")
        if event.data.get("entity_id") == "sensor.frame_tv_activity" and new:
            if not seen or seen[-1] != new.state:
                seen.append(new.state)

    hass.bus.async_listen("state_changed", changed)
    return seen


async def test_activity_shows_each_step_from_off_to_input(hass, setup, tv, entry):
    await setup(ip_control=True)
    await call(hass, "turn_off")
    await refresh(hass, entry)
    assert hass.states.get("sensor.frame_tv_activity").state == "idle"
    seen = record_activity(hass)
    await call(hass, "select_source", source="HDMI 2")
    assert seen == [
        "waking",
        "switching_to_tv",  # IP Control mode first; unsupported here
        "starting_art_app",
        "switching_to_tv",
        "settling",
        "switching_input",
        "checking_input",
        "idle",
    ]
    assert hass.states.get(ENTITY).attributes["activity"] == "idle"


async def test_activity_for_power_off_and_failures_returns_to_idle(
    hass, setup, tv, entry
):
    from homeassistant.exceptions import HomeAssistantError

    await setup(ip_control=True)
    seen = record_activity(hass)
    await call(hass, "turn_off")
    assert seen == ["powering_off", "idle"]
    seen.clear()
    tv.ipc_power_on_works = False
    tv.wake_on_lan = False
    await refresh(hass, entry)
    with pytest.raises(HomeAssistantError):
        await call(hass, "turn_on")
    assert "waking" in seen and "retrying" in seen
    assert seen[-1] == "idle"
    assert hass.states.get("sensor.frame_tv_activity").state == "idle"


async def test_activity_sensor_exists_while_tv_unreachable(hass, setup, tv):
    tv.reachable = False
    await setup()
    state = hass.states.get("sensor.frame_tv_activity")
    assert state.state == "idle"
    assert state.attributes["options"][0] == "idle"


async def test_tv_last_seen_on_that_stops_answering_is_unknown(hass, setup, tv, entry):
    await setup(ip_control=True)
    tv.reachable = False  # no answer, no off report: network or mains problem
    await refresh(hass, entry)
    assert hass.states.get(ENTITY).state == STATE_UNKNOWN
    assert hass.states.get(ENTITY).attributes["power_source"] == "unreachable"


# -- Art/TV mode through IP Control ---------------------------------------------


async def test_off_to_input_uses_ip_control_mode_without_art_app(
    hass, setup, tv, entry
):
    tv.ipc_art_setter = 0
    await setup(ip_control=True)
    await call(hass, "turn_off")
    await refresh(hass, entry)
    tv.art_requests.clear()
    tv.art_delay = 3  # the Art channel would be slow; it must not be needed
    await call(hass, "select_source", source="HDMI 2")
    assert ("artModeControl", {"artMode": "artModeOff"}) in tv.ipc_commands
    assert not [c for c, _ in tv.art_requests if c == "set_artmode_status"]
    steps = entry.runtime_data.last_command["steps"]
    assert list(steps) == ["ip_control_power_on", "ip_control_mode", "mode_tv", "input"]
    assert entry.runtime_data.last_command["took_s"] < 2
    assert source(hass) == ("HDMI 2", True)


async def test_ip_control_mode_retried_while_tv_starts(hass, setup, tv, entry):
    tv.ipc_art_setter = 2  # answers "failed" twice, then works
    tv.art = "off"
    await setup(ip_control=True)
    tv.art_requests.clear()
    await call(hass, "select_source", source="Art Mode")
    assert tv.art == "on"
    assert entry.runtime_data.ipc_art_setter is True
    assert not [c for c, _ in tv.art_requests if c == "set_artmode_status"]
    assert source(hass) == ("Art Mode", True)


async def test_unsupported_ip_control_mode_falls_back_to_art_channel(
    hass, setup, tv, entry
):
    await setup(ip_control=True)  # fake: artModeControl not implemented
    await call(hass, "select_source", source="Art Mode")
    await call(hass, "select_source", source="HDMI 2")
    assert entry.runtime_data.ipc_art_setter is False
    assert [p for c, p in tv.art_requests if c == "set_artmode_status"]
    assert source(hass) == ("HDMI 2", True)


async def test_remembered_ip_control_art_flag_is_not_success_while_off(
    hass, setup, tv, entry
):
    from homeassistant.exceptions import HomeAssistantError

    tv.ipc_art_setter = 0
    await setup(ip_control=True)  # Art Mode on
    await call(hass, "turn_off")
    await refresh(hass, entry)
    tv.ipc_power_on_works = False
    tv.wake_on_lan = False
    with pytest.raises(HomeAssistantError):
        await call(hass, "turn_on")
    assert tv.power == "off"


# -- Samsung TV channel -------------------------------------------------------


def media(hass):
    attributes = hass.states.get(ENTITY).attributes
    return attributes.get("media_channel"), attributes.get("media_title")


async def test_channel_shown_on_samsung_tv_and_follows_changes(hass, setup, tv, entry):
    tv.art, tv.ipc_input = "off", "TV"
    await setup(ip_control=True)
    assert source(hass) == ("Samsung TV", True)
    # Names from the built-in Samsung TV Plus line-up.
    assert media(hass) == ("2110", "2110 · America's Test Kitchen")
    assert hass.states.get(ENTITY).attributes["media_content_type"] == "channel"
    assert hass.states.get(ENTITY).attributes["channel_genre"] == "Home & Food"
    tv.ipc_channel = "2111"  # changed with the remote
    await refresh(hass, entry)
    assert media(hass) == ("2111", "2111 · BuzzFeed Tasty")
    tv.ipc_channel = "9"  # not in the line-up: number only
    await refresh(hass, entry)
    assert media(hass) == ("9", "9")
    assert "channel_genre" not in hass.states.get(ENTITY).attributes


async def test_channel_name_from_options(hass, setup, tv):
    tv.art, tv.ipc_input = "off", "TV"
    await setup(options={"channel_names": {"2110": "Pluto News"}}, ip_control=True)
    # Your own name wins over the built-in one.
    assert media(hass) == ("2110", "2110 · Pluto News")


async def test_no_channel_on_hdmi_or_in_art_mode(hass, setup, tv, entry):
    tv.art, tv.ipc_input = "off", "HDMI1"
    await setup(ip_control=True)
    assert media(hass) == (None, None)
    tv.art, tv.ipc_input = "on", "TV"  # Samsung TV behind the art
    entry.runtime_data._last_art_refresh = 0
    await refresh(hass, entry)
    assert source(hass) == ("Art Mode", True)
    assert media(hass) == (None, None)


async def test_channel_names_options_step(hass, setup, entry):
    await setup(options={"hdmi1": "Apple TV"})
    result = await open_menu(hass, entry, "channels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"channel_names": "2110 = Pluto News\n\n 2111: Movies \n7-1=Local ABC"},
    )
    assert result["type"] == "create_entry"
    assert entry.options["channel_names"] == {
        "2110": "Pluto News",
        "2111": "Movies",
        "7-1": "Local ABC",
    }
    assert entry.options["hdmi1"] == "Apple TV"  # other options kept


async def test_invalid_channel_line_is_reported(hass, setup, entry):
    await setup()
    result = await open_menu(hass, entry, "channels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"channel_names": "2110 = News\nPluto"}
    )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_channel_line"}
    assert result["description_placeholders"]["line"] == "Pluto"


async def test_saving_input_names_keeps_channel_names(hass, setup, entry):
    await setup(options={"channel_names": {"2110": "Pluto News"}})
    result = await open_menu(hass, entry, "inputs")
    await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"tv": "Samsung TV", "hdmi1": "Xbox", "hdmi2": "", "hdmi3": "", "hdmi4": ""},
    )
    assert entry.options["channel_names"] == {"2110": "Pluto News"}
    assert entry.options["hdmi1"] == "Xbox"


# -- changes made with the TV remote ------------------------------------------


async def test_remote_power_button_to_art_mode_shows_in_one_poll(
    hass, setup, tv, entry
):
    tv.art, tv.ipc_input = "off", "TV"
    await setup(ip_control=True)
    assert source(hass) == ("Samsung TV", True)
    brightness = hass.states.get("number.frame_tv_art_brightness")
    assert brightness is None or brightness.state == "unavailable"
    tv.art = "on"  # short press on the TV remote; no Art channel push arrives
    await refresh(hass, entry)
    assert source(hass) == ("Art Mode", True)
    # Art settings are read in the same poll, not up to 60 s later.
    assert hass.states.get("number.frame_tv_art_brightness").state == "5.0"


async def test_remote_leaving_art_mode_shows_in_one_poll(hass, setup, tv, entry):
    tv.ipc_input = "HDMI2"
    await setup(ip_control=True)
    assert source(hass) == ("Art Mode", True)
    assert hass.states.get("sensor.frame_tv_current_artwork").state != "unavailable"
    tv.art = "off"
    await refresh(hass, entry)
    assert source(hass) == ("HDMI 2", True)
    assert hass.states.get("number.frame_tv_art_brightness").state == "unavailable"
    # Art settings from before the remote press must not linger.
    assert hass.states.get("sensor.frame_tv_current_artwork").state == "unavailable"


async def test_without_ip_control_art_flag_the_art_channel_still_decides(
    hass, setup, tv, entry
):
    tv.art, tv.ipc_input = "off", "TV"
    tv.ipc_art_flag = False
    await setup(ip_control=True)
    tv.art = "on"
    entry.runtime_data._last_art_refresh = 0  # next full Art refresh is due
    await refresh(hass, entry)
    assert source(hass) == ("Art Mode", True)
