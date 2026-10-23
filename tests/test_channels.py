"""Samsung TV channel selector, tuning by typed digits, media browser (2.7.0).

The fake TV behaves like the user's 2020 Frame: IP Control reports the
channel, its channel setter is refused, and four typed digits tune.
"""

import json
from pathlib import Path

import pytest
from frame_fakes import refresh
from homeassistant.components.media_player import MediaPlayerEntityFeature as F
from homeassistant.components.media_player.const import (
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
)
from homeassistant.components.websocket_api import const as ws_const
from homeassistant.exceptions import HomeAssistantError

from custom_components.samsung_frame_control.async_art import ArtError
from custom_components.samsung_frame_control.channels import (
    CHANNELS_FILE,
    load_channels,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

PLAYER = "media_player.frame_tv"
SELECT = "select.frame_tv_channel"
CHANNEL = F.PLAY_MEDIA | F.BROWSE_MEDIA


def on_samsung_tv(tv):
    tv.art, tv.ipc_input = "off", "TV"


async def select(hass, option):
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": SELECT, "option": option},
        blocking=True,
    )
    await hass.async_block_till_done()


async def play(hass, number, kind="channel"):
    await hass.services.async_call(
        "media_player",
        "play_media",
        {
            "entity_id": PLAYER,
            ATTR_MEDIA_CONTENT_TYPE: kind,
            ATTR_MEDIA_CONTENT_ID: number,
        },
        blocking=True,
    )
    await hass.async_block_till_done()


# -- built-in line-up -----------------------------------------------------------


def test_shipped_lineup_is_complete_and_well_formed():
    raw = json.loads(CHANNELS_FILE.read_text("utf-8"))
    assert raw["lineup"] == "U.S. Channel Lineup"
    assert raw["effective"]
    channels = load_channels()["channels"]
    assert len(channels) == len(raw["channels"]) > 500
    assert all(number.isdigit() for number in channels)
    assert all(info["name"] and info["genre"] for info in channels.values())
    assert channels["2110"] == {
        "name": "America's Test Kitchen",
        "genre": "Home & Food",
    }
    # A name printed over two lines in Samsung's PDF.
    assert channels["3544"]["name"] == "Modern Marvels Presented by History"


def test_missing_or_broken_lineup_file_means_no_names(tmp_path):
    assert load_channels(tmp_path / "missing.json")["channels"] == {}
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert load_channels(broken)["channels"] == {}
    odd = tmp_path / "odd.json"
    odd.write_text(json.dumps({"channels": {"12a": {"name": "x"}, "5": {"name": ""}}}))
    assert load_channels(odd)["channels"] == {}


# -- select entity ------------------------------------------------------------------


async def test_select_lists_channels_and_follows_the_tv(hass, setup, tv, entry):
    on_samsung_tv(tv)
    await setup(ip_control=True)
    state = hass.states.get(SELECT)
    assert state.state == "2110 · America's Test Kitchen"
    assert "1114 · NBC News NOW" in state.attributes["options"]
    assert len(state.attributes["options"]) > 500
    tv.ipc_channel = "2111"  # changed with the remote
    await refresh(hass, entry)
    assert hass.states.get(SELECT).state == "2111 · BuzzFeed Tasty"


async def test_selecting_a_channel_types_its_number(hass, setup, tv):
    on_samsung_tv(tv)
    await setup(ip_control=True)
    await select(hass, "1114 · NBC News NOW")
    # One connection, so the TV sees one typed number.
    assert tv.key_batches == [["KEY_1", "KEY_1", "KEY_1", "KEY_4", "KEY_ENTER"]]
    assert tv.ipc_channel == "1114"
    assert hass.states.get(SELECT).state == "1114 · NBC News NOW"
    assert hass.states.get(PLAYER).attributes["media_title"] == "1114 · NBC News NOW"
    assert tv.ipc_calls  # read back through IP Control


async def test_select_unavailable_off_the_samsung_tv_source(hass, setup, tv, entry):
    tv.art, tv.ipc_input = "off", "HDMI1"
    await setup(ip_control=True)
    assert hass.states.get(SELECT).state == "unavailable"
    on_samsung_tv(tv)
    await refresh(hass, entry)
    assert hass.states.get(SELECT).state != "unavailable"
    tv.art = "on"  # Art Mode in front of the TV source
    entry.runtime_data._last_art_refresh = 0
    await refresh(hass, entry)
    assert hass.states.get(SELECT).state == "unavailable"


async def test_no_select_without_ip_control(hass, setup, tv):
    on_samsung_tv(tv)
    await setup()
    assert hass.states.get(SELECT) is None


async def test_user_names_in_the_select(hass, setup, tv):
    on_samsung_tv(tv)
    await setup(
        options={"channel_names": {"2110": "Cooking", "7": "Antenna 7"}},
        ip_control=True,
    )
    options = hass.states.get(SELECT).attributes["options"]
    assert hass.states.get(SELECT).state == "2110 · Cooking"
    assert "7 · Antenna 7" in options
    assert not any("America's Test Kitchen" in option for option in options)


async def test_current_channel_outside_the_lineup_is_still_an_option(
    hass, setup, tv, entry
):
    on_samsung_tv(tv)
    await setup(ip_control=True)
    tv.ipc_channel = "9"
    await refresh(hass, entry)
    state = hass.states.get(SELECT)
    assert state.state == "9"
    assert state.attributes["options"][0] == "9"


# -- tuning failures ---------------------------------------------------------------------


async def test_number_not_in_the_tv_lineup_is_reported(hass, setup, tv):
    on_samsung_tv(tv)
    tv.missing_channels = {"1114"}
    await setup(ip_control=True)
    with pytest.raises(HomeAssistantError, match="still reports a different channel"):
        await select(hass, "1114 · NBC News NOW")
    # One retry after a finished entry, then reported.
    assert tv.key_batches == [["KEY_1", "KEY_1", "KEY_1", "KEY_4", "KEY_ENTER"]] * 2
    assert hass.states.get(SELECT).state == "2110 · America's Test Kitchen"


async def test_partly_typed_number_is_never_typed_again(hass, setup, tv):
    on_samsung_tv(tv)
    await setup(ip_control=True)
    tv.keys_error = ArtError("cannot_connect", sent=True)
    with pytest.raises(HomeAssistantError):
        await select(hass, "1114 · NBC News NOW")
    assert len(tv.key_batches) == 1


async def test_connection_failing_before_any_key_is_retried(hass, setup, tv):
    on_samsung_tv(tv)
    await setup(ip_control=True)
    tv.keys_error = ArtError("cannot_connect", sent=False)
    await select(hass, "1114 · NBC News NOW")
    assert len(tv.key_batches) == 2
    assert tv.ipc_channel == "1114"


async def test_already_on_the_channel_sends_nothing(hass, setup, tv):
    on_samsung_tv(tv)
    await setup(ip_control=True)
    await select(hass, "2110 · America's Test Kitchen")
    assert tv.key_batches == []


async def test_tuning_left_the_samsung_tv_source_is_refused(hass, setup, tv):
    on_samsung_tv(tv)
    await setup(ip_control=True)
    tv.ipc_input = "HDMI1"  # changed with the remote since the last poll
    with pytest.raises(HomeAssistantError, match="Samsung TV source"):
        await play(hass, "1114")
    assert tv.key_batches == []


# -- media player: play_media, browse, features -------------------------------------


async def test_play_media_tunes_a_channel(hass, setup, tv):
    on_samsung_tv(tv)
    await setup(ip_control=True)
    assert CHANNEL in F(hass.states.get(PLAYER).attributes["supported_features"])
    await play(hass, "1114")
    assert tv.ipc_channel == "1114"
    with pytest.raises(HomeAssistantError):
        await play(hass, "abc")
    with pytest.raises(HomeAssistantError):
        await play(hass, "1114", kind="music")
    assert len(tv.key_batches) == 1


async def test_play_and_browse_offered_from_any_state_with_ip_control(
    hass, setup, tv, entry
):
    tv.art, tv.ipc_input = "off", "HDMI1"
    await setup(ip_control=True)
    assert CHANNEL in F(hass.states.get(PLAYER).attributes["supported_features"])
    tv.art = "on"
    entry.runtime_data._last_art_refresh = 0
    await refresh(hass, entry)
    assert CHANNEL in F(hass.states.get(PLAYER).attributes["supported_features"])


async def test_no_play_or_browse_without_ip_control(hass, setup, tv):
    on_samsung_tv(tv)
    await setup()
    assert not F(hass.states.get(PLAYER).attributes["supported_features"]) & CHANNEL


async def test_play_channel_from_hdmi_switches_to_samsung_tv_first(hass, setup, tv):
    tv.art, tv.ipc_input = "off", "HDMI1"
    await setup(ip_control=True)
    await play(hass, "1114")
    assert tv.keys[0] == "KEY_TV"  # Samsung TV source first
    assert tv.key_batches[-1] == ["KEY_1", "KEY_1", "KEY_1", "KEY_4", "KEY_ENTER"]
    assert tv.ipc_input == "TV"
    assert tv.ipc_channel == "1114"


async def test_play_channel_from_art_mode_leaves_art(hass, setup, tv):
    tv.ipc_input = "HDMI2"  # Art Mode in front of HDMI 2
    await setup(ip_control=True)
    await play(hass, "1114")
    assert tv.art == "off"
    assert tv.ipc_input == "TV"
    assert tv.ipc_channel == "1114"


async def test_play_channel_on_samsung_tv_does_not_switch(hass, setup, tv):
    on_samsung_tv(tv)
    await setup(ip_control=True)
    await play(hass, "1114")
    assert "KEY_TV" not in tv.keys
    assert tv.ipc_channel == "1114"


async def test_source_stays_confirmed_while_ip_control_is_read(hass, setup, tv, entry):
    """2.8.0 cleared it at the start of every read: actions briefly vanished."""
    on_samsung_tv(tv)
    await setup(ip_control=True)
    controller = entry.runtime_data
    seen = []
    original = tv.ipc_read_state

    def watching(client):
        seen.append(controller.on_tv_source())  # while the read is in flight
        return original(client)

    tv.ipc_read_state = watching
    await refresh(hass, entry)
    await refresh(hass, entry)
    assert seen and all(seen)


async def test_browse_channels_by_genre(hass, hass_ws_client, setup, tv):
    on_samsung_tv(tv)
    await setup(options={"channel_names": {"7": "Antenna 7"}}, ip_control=True)
    client = await hass_ws_client()
    await client.send_json(
        {"id": 1, "type": "media_player/browse_media", "entity_id": PLAYER}
    )
    root = await client.receive_json()
    assert root["success"], root
    titles = [child["title"] for child in root["result"]["children"]]
    assert root["result"]["title"].startswith("Samsung TV channels (")
    assert any(title.startswith("Home & Food (") for title in titles)
    assert any(title.startswith("Other channels (1)") for title in titles)
    food = next(c for c in root["result"]["children"] if c["title"].startswith("Home"))
    await client.send_json(
        {
            "id": 2,
            "type": "media_player/browse_media",
            "entity_id": PLAYER,
            "media_content_type": food["media_content_type"],
            "media_content_id": food["media_content_id"],
        }
    )
    folder = await client.receive_json()
    assert folder["success"], folder
    channel = next(
        c for c in folder["result"]["children"] if c["media_content_id"] == "2110"
    )
    assert channel["title"] == "2110 · America's Test Kitchen"
    assert channel["can_play"] is True
    assert channel["media_content_type"] == "channel"
    await client.send_json(
        {
            "id": 3,
            "type": "media_player/browse_media",
            "entity_id": PLAYER,
            "media_content_type": "channels",
            "media_content_id": "genre:Nope",
        }
    )
    missing = await client.receive_json()
    assert not missing["success"]
    assert missing["error"]["code"] != ws_const.ERR_UNKNOWN_COMMAND


async def test_diagnostics_show_the_lineup(hass, setup, tv, entry):
    from custom_components.samsung_frame_control.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    on_samsung_tv(tv)
    await setup(ip_control=True)
    data = await async_get_config_entry_diagnostics(hass, entry)
    assert data["media_player"]["channel_lineup"]["effective"]
    assert data["media_player"]["channel_lineup"]["channels"] > 500


def test_file_is_next_to_the_integration():
    assert CHANNELS_FILE.parent.name == "samsung_frame_control"
    assert Path(CHANNELS_FILE).is_file()


async def test_digits_are_spaced_and_confirmed_with_enter(hass, setup, tv):
    on_samsung_tv(tv)
    await setup(ip_control=True)
    await select(hass, "1114 · NBC News NOW")
    # Measured: 0.3 s apart lost digits, 0.5 s apart plus Enter tuned.
    gap, hold = tv.key_timing[0]
    assert gap >= 0.5
    assert hold < 1.0
    assert tv.key_batches[0][-1] == "KEY_ENTER"


async def test_dropped_digit_tuning_another_channel_is_retyped_once(hass, setup, tv):
    on_samsung_tv(tv)
    tv.channel_digits = 99  # tune only on Enter, like a rushed entry
    await setup(ip_control=True)
    tv.drop_first_digit = 1  # 1114 arrives as 114 the first time
    await select(hass, "1114 · NBC News NOW")
    assert len(tv.key_batches) == 2
    # The retype goes over a new connection, in case the kept one was dead.
    assert tv.fresh_connections == [False, True]
    assert tv.ipc_channel == "1114"
    assert hass.states.get(SELECT).state == "1114 · NBC News NOW"


async def test_no_retype_after_leaving_the_samsung_tv_source(hass, setup, tv):
    on_samsung_tv(tv)
    tv.missing_channels = {"1114"}
    await setup(ip_control=True)
    original = tv.send_keys

    async def then_switch(client, keys, **timing):
        await original(client, keys, **timing)
        tv.ipc_input = "HDMI1"  # someone changed input meanwhile

    tv.send_keys = then_switch
    with pytest.raises(HomeAssistantError):
        await select(hass, "1114 · NBC News NOW")
    assert len(tv.key_batches) == 1


async def test_remote_connection_closed_when_the_tv_turns_off(hass, setup, tv, entry):
    from unittest.mock import AsyncMock, patch

    on_samsung_tv(tv)
    await setup(ip_control=True)
    controller = entry.runtime_data
    with patch.object(controller.remote, "close", AsyncMock()) as close:
        await refresh(hass, entry)
        close.assert_not_awaited()  # kept while the TV is on
        tv.power = "off"
        tv.ipc_power = "powerOff"
        await refresh(hass, entry)
        close.assert_awaited()


async def test_play_channel_waits_for_ip_control_after_switching(
    hass, setup, tv, entry
):
    tv.art, tv.ipc_input = "off", "HDMI1"
    await setup(ip_control=True)
    original = tv.send_key

    async def starting_after_tv_key(client, key):
        await original(client, key)
        if key == "KEY_TV":
            tv.ipc_starting_reads = 5  # IP Control slow to answer again

    tv.send_key = starting_after_tv_key
    await play(hass, "1114")
    assert tv.ipc_input == "TV"
    assert tv.ipc_channel == "1114"
