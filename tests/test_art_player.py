"""Media player picture and transport buttons (2.6.0), on real Home Assistant.

The fake TV answers like the user's 2020 Frame where it matters: the
slideshow getter is refused and auto_rotation answers with its pieces, the
thumbnail is served per artwork, and channel keys change the channel.
"""

import pytest
from frame_fakes import refresh
from homeassistant.components.media_player import MediaPlayerEntityFeature as F
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import (
    mock_restore_cache_with_extra_data,
)

from custom_components.samsung_frame_control.async_art import ArtError
from custom_components.samsung_frame_control.diagnostics import (
    async_get_config_entry_diagnostics,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

ENTITY = "media_player.frame_tv"
STEP = F.NEXT_TRACK | F.PREVIOUS_TRACK
SLIDESHOW = F.PLAY | F.PAUSE
VOLUME = F.VOLUME_SET | F.VOLUME_MUTE | F.VOLUME_STEP


def like_users_frame(tv):
    tv.slideshow_api = "auto_rotation"
    tv.slideshow = {
        "value": "10",
        "type": "shuffleslideshow",
        "category_id": "MY-C0002",
    }
    tv.content.append({"content_id": "MY-3", "title": "Lake"})
    # The TV lists its pieces newest first.
    tv.slideshow_items = ["MY-3", "MY-2", "MY-1"]


def features(hass):
    return F(hass.states.get(ENTITY).attributes["supported_features"])


def picture(hass):
    return hass.states.get(ENTITY).attributes.get("entity_picture")


async def call(hass, service):
    await hass.services.async_call(
        "media_player", service, {"entity_id": ENTITY}, blocking=True
    )
    await hass.async_block_till_done()


async def full_art_refresh(hass, entry):
    entry.runtime_data._last_art_refresh = 0
    await refresh(hass, entry)


# -- picture -------------------------------------------------------------------


async def test_art_mode_shows_the_artwork_thumbnail(hass, hass_client, setup, tv):
    like_users_frame(tv)
    await setup()
    url = picture(hass)
    assert url.startswith("/api/media_player_proxy/media_player.frame_tv?")
    client = await hass_client()
    response = await client.get(url)
    assert response.status == 200
    assert response.content_type == "image/jpeg"
    assert await response.read() == tv.thumbnails["MY-1"]
    # Every dashboard view asks again; the TV is asked once per artwork.
    assert (await client.get(url)).status == 200
    assert tv.thumbnail_calls == ["MY-1"]


async def test_picture_follows_the_artwork(hass, hass_client, setup, tv, entry):
    like_users_frame(tv)
    await setup()
    first = picture(hass)
    tv.current = "MY-2"  # the slideshow moved on
    await full_art_refresh(hass, entry)
    second = picture(hass)
    assert second != first
    response = await (await hass_client()).get(second)
    assert await response.read() == tv.thumbnails["MY-2"]


async def test_no_picture_outside_art_mode_or_when_off(hass, setup, tv, entry):
    like_users_frame(tv)
    tv.art, tv.ipc_input = "off", "HDMI1"
    await setup(ip_control=True)
    assert picture(hass) is None
    tv.ipc_input = "TV"
    await refresh(hass, entry)
    assert picture(hass) is None
    tv.power = "off"
    await refresh(hass, entry)
    assert picture(hass) is None
    assert tv.thumbnail_calls == []


async def test_refused_thumbnail_drops_the_picture(hass, hass_client, setup, tv, entry):
    like_users_frame(tv)
    tv.thumbnail_error = ArtError("rejected", sent=True)
    await setup()
    response = await (await hass_client()).get(picture(hass))
    assert response.status == 404
    await hass.async_block_till_done()
    # Not offered again for this piece, so the card shows no broken image.
    assert picture(hass) is None
    await refresh(hass, entry)
    assert picture(hass) is None
    assert tv.thumbnail_calls == ["MY-1"]
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["artwork_picture"] == {"content_id": "MY-1", "error": "rejected"}
    # Another piece is still tried.
    tv.thumbnail_error = None
    tv.current = "MY-2"
    await full_art_refresh(hass, entry)
    assert picture(hass) is not None


# -- Art Mode: slideshow and artwork steps ------------------------------------


async def test_art_mode_offers_steps_and_slideshow(hass, setup, tv):
    like_users_frame(tv)
    await setup()
    assert hass.states.get(ENTITY).state == "playing"
    assert STEP | SLIDESHOW in features(hass)
    # Art Mode is silent: the card shows no volume slider or mute.
    assert not features(hass) & VOLUME
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "media_player",
            "volume_set",
            {"entity_id": ENTITY, "volume_level": 0.5},
            blocking=True,
        )
    assert tv.volume != 50


async def test_volume_comes_back_when_leaving_art_mode(hass, setup, tv, entry):
    like_users_frame(tv)
    tv.ipc_input = "HDMI1"
    await setup(ip_control=True)
    assert not features(hass) & VOLUME
    tv.art = "off"
    await refresh(hass, entry)
    assert VOLUME in features(hass)
    assert hass.states.get(ENTITY).attributes["volume_level"] == 0.2


async def test_pause_and_play_stop_and_resume_the_slideshow(hass, setup, tv):
    like_users_frame(tv)
    await setup()
    await call(hass, "media_pause")
    assert tv.slideshow["value"] == "off"
    assert hass.states.get(ENTITY).state == "paused"
    await call(hass, "media_play")
    assert tv.slideshow["value"] == "10"  # the interval it had before
    assert tv.slideshow["type"] == "shuffleslideshow"
    assert hass.states.get(ENTITY).state == "playing"
    sent = [c for c, _ in tv.art_requests if c.startswith("set_")]
    assert sent == ["set_auto_rotation_status", "set_auto_rotation_status"]
    await call(hass, "media_play_pause")
    assert tv.slideshow["value"] == "off"


async def test_play_never_seen_running_uses_default_interval(hass, setup, tv):
    like_users_frame(tv)
    tv.slideshow["value"] = "off"
    await setup()
    assert hass.states.get(ENTITY).state == "paused"
    await call(hass, "media_play")
    assert tv.slideshow["value"] == "15"


async def test_play_after_restart_resumes_the_stored_interval(hass, setup, tv):
    like_users_frame(tv)
    tv.slideshow["value"] = "off"
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                _state(),
                {
                    "last_mode": "art",
                    "last_input": None,
                    "off_reported": False,
                    "resume_interval": "60",
                },
            )
        ],
    )
    await setup()
    await call(hass, "media_play")
    assert tv.slideshow["value"] == "60"


def _state():
    from homeassistant.core import State

    return State(ENTITY, "on")


async def test_next_and_previous_follow_the_slideshow_order(hass, setup, tv):
    like_users_frame(tv)
    await setup()
    await call(hass, "media_previous_track")  # MY-1 is last; previous is MY-2
    assert tv.current == "MY-2"
    await call(hass, "media_next_track")
    assert tv.current == "MY-1"
    await call(hass, "media_next_track")  # wraps to the newest piece
    assert tv.current == "MY-3"


async def test_next_starts_from_what_shows_now(hass, setup, tv):
    like_users_frame(tv)
    await setup()
    tv.current = "MY-3"  # the slideshow moved on since the last poll
    await call(hass, "media_next_track")
    assert tv.current == "MY-2"


async def test_next_without_slideshow_list_uses_the_catalog(hass, setup, tv):
    tv.content.append({"content_id": "MY-3", "title": "Lake"})
    await setup()
    await call(hass, "media_next_track")
    assert tv.current == "MY-2"
    await call(hass, "media_next_track")
    assert tv.current == "MY-3"


# -- Samsung TV: channel; HDMI: no transport buttons --------------------------


async def test_samsung_tv_next_and_previous_change_channel(hass, setup, tv):
    tv.art, tv.ipc_input = "off", "TV"
    await setup(ip_control=True)
    assert hass.states.get(ENTITY).state == "on"
    assert STEP in features(hass)
    assert not features(hass) & SLIDESHOW
    await call(hass, "media_next_track")
    assert tv.keys[-1] == "KEY_CHUP"
    # Read back right away, not at the next 15 s poll.
    assert hass.states.get(ENTITY).attributes["media_channel"] == "2111"
    await call(hass, "media_previous_track")
    assert tv.keys[-1] == "KEY_CHDOWN"
    assert hass.states.get(ENTITY).attributes["media_channel"] == "2110"
    assert picture(hass) is None


async def test_hdmi_has_no_transport_buttons(hass, setup, tv):
    tv.art, tv.ipc_input = "off", "HDMI1"
    await setup(ip_control=True)
    assert not features(hass) & (STEP | SLIDESHOW)
    with pytest.raises(HomeAssistantError):
        await call(hass, "media_next_track")
    with pytest.raises(HomeAssistantError):
        await call(hass, "media_pause")
    assert tv.keys == []
