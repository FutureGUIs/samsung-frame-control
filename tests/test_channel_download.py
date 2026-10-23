"""Samsung TV Plus channel list from Samsung's web API (2.8.0).

The API shapes match what Samsung's Samsung TV Plus page loads (checked in a
browser and with tools/frame_probe.py channels US on the user's network):
tvplusGenre, tvplusGenreOrder and tvplusApp per genre.
"""

import asyncio
from unittest.mock import patch

import pytest
from frame_fakes import DOMAIN, PKG, refresh
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError

from custom_components.samsung_frame_control import channels
from custom_components.samsung_frame_control.channels import (
    API,
    ChannelListError,
    fetch_lineup,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

PLAYER = "media_player.frame_tv"
BUTTON = "button.frame_tv_update_channel_list"
LOGO = "https://tvpdotcomdynamiclogopus.samsungcloud.tv/resize?url=atk.png&h=250"


def app(number, name, genre, logo=None, hide="N"):
    return {
        "CHANNELNUMBER": number,
        "CHANNELNAME": name,
        "GENRENAME": genre,
        "LOGOURL": logo,
        "HIDE": hide,
        "COUNTRY": "US",
        "DESCRIPTION": "...",
    }


def us_api(aioclient_mock, *, extra=0):
    """Register a small U.S. line-up, padded to pass the minimum size."""
    aioclient_mock.get(
        API + "tvplusGenre?country=US",
        json={
            "tvPlusGenre": [
                {"GENREID": "CHG015", "GENRENAME": "Home & Food", "GENRENAMECOUNT": 3},
                {
                    "GENREID": "CHG001",
                    "GENRENAME": "News & Opinion",
                    "GENRENAMECOUNT": 9,
                },
            ]
        },
    )
    aioclient_mock.get(
        API + "tvplusGenreOrder?country=US",
        json=[
            {"GENREORDER": "1", "GENRENAME": "News & Opinion"},
            {"GENREORDER": "8", "GENRENAME": "Home & Food"},
        ],
    )
    aioclient_mock.get(
        API
        + "tvplusApp?country=US&genre=Home%20%26%20Food&order=asc&offset=0&size=999",
        json=[
            app("2110", "America's Test Kitchen", "Home & Food", LOGO),
            app("2111", "BuzzFeed Tasty", "Home & Food", "http://insecure/logo.png"),
            app("2999", "Hidden", "Home & Food", hide="Y"),
            app("abc", "No number", "Home & Food"),
            app("2110", "Same number listed again", "Home & Food"),
        ],
    )
    aioclient_mock.get(
        API
        + "tvplusApp?country=US&genre=News%20%26%20Opinion&order=asc&offset=0&size=999",
        json=[
            app(str(1110 + n), f"News {n}", "News & Opinion") for n in range(9 + extra)
        ],
    )


# -- downloader ---------------------------------------------------------------


async def test_lineup_is_read_from_samsung(hass, aioclient_mock):
    us_api(aioclient_mock)
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    lineup = await fetch_lineup(session, "US")
    assert lineup["region"] == "US"
    assert lineup["genre_order"] == ["News & Opinion", "Home & Food"]
    found = lineup["channels"]
    assert found["2110"] == {
        "name": "America's Test Kitchen",
        "genre": "Home & Food",
        "logo": LOGO,
    }
    assert found["2111"]["logo"] is None  # only https logos are used
    assert "2999" not in found  # hidden by Samsung
    assert "abc" not in found
    assert list(found) == sorted(found, key=int)
    assert len(found) == 11
    # Genre lists are fetched together, not one after another.
    assert aioclient_mock.call_count == 4


@pytest.mark.parametrize(
    ("genres", "code"),
    [({"tvPlusGenre": []}, "no_lineup"), ({"oops": 1}, "no_lineup")],
)
async def test_country_without_lineup(hass, aioclient_mock, genres, code):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    aioclient_mock.get(API + "tvplusGenre?country=JP", json=genres)
    with pytest.raises(ChannelListError) as raised:
        await fetch_lineup(async_get_clientsession(hass), "JP")
    assert raised.value.code == code


async def test_short_list_is_refused(hass, aioclient_mock):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    us_api(aioclient_mock, extra=-8)
    with pytest.raises(ChannelListError) as raised:
        await fetch_lineup(async_get_clientsession(hass), "US")
    assert raised.value.code == "too_few_channels"


async def test_http_error_and_bad_json(hass, aioclient_mock):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    aioclient_mock.get(API + "tvplusGenre?country=US", status=503)
    with pytest.raises(ChannelListError) as raised:
        await fetch_lineup(session, "US")
    assert raised.value.code == "http_503"
    aioclient_mock.clear_requests()
    aioclient_mock.get(API + "tvplusGenre?country=US", text="<html>")
    with pytest.raises(ChannelListError) as raised:
        await fetch_lineup(session, "US")
    assert raised.value.code == "invalid_response"
    aioclient_mock.clear_requests()
    aioclient_mock.get(API + "tvplusGenre?country=US", exc=asyncio.TimeoutError())
    with pytest.raises(ChannelListError) as raised:
        await fetch_lineup(session, "US")
    assert raised.value.code == "cannot_connect"


async def test_missing_genre_order_keeps_samsungs_list(hass, aioclient_mock):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    us_api(aioclient_mock)
    aioclient_mock._mocks = [
        m for m in aioclient_mock._mocks if "tvplusGenreOrder" not in str(m.url)
    ]
    aioclient_mock.get(API + "tvplusGenreOrder?country=US", status=500)
    lineup = await fetch_lineup(async_get_clientsession(hass), "US")
    assert lineup["genre_order"] == ["Home & Food", "News & Opinion"]


# -- in Home Assistant ----------------------------------------------------------


def lineup(region, channels_by_number, order=None):
    return {
        "region": region,
        "fetched": "2026-10-23T18:00:00+00:00",
        "genre_order": order
        or sorted({c["genre"] for c in channels_by_number.values()}),
        "channels": channels_by_number,
    }


US = lineup(
    "US",
    {
        "1000": {"name": "ION Mystery", "genre": "Crime", "logo": None},
        "2110": {
            "name": "America's Test Kitchen",
            "genre": "Home & Food",
            "logo": LOGO,
        },
    },
    order=["Home & Food", "Crime"],
)
GB = lineup("GB", {"4564": {"name": "Al Jazeera", "genre": "News", "logo": None}})


@pytest.fixture
def samsung():
    """Samsung's API as seen by the integration: lists per country."""
    state = {"calls": [], "lists": {"US": US, "GB": GB}, "error": None}

    async def fake(session, country):
        state["calls"].append(country)
        if state["error"]:
            raise ChannelListError(state["error"])
        if country not in state["lists"]:
            raise ChannelListError("no_lineup")
        return state["lists"][country]

    with patch(f"{PKG}.frame.fetch_lineup", side_effect=fake):
        yield state


def on_samsung_tv(tv):
    tv.art, tv.ipc_input = "off", "TV"


async def test_downloaded_at_first_setup_and_saved(
    hass, setup, tv, entry, samsung, hass_storage
):
    on_samsung_tv(tv)
    await setup(
        options={"channel_list_download": True, "channel_list_region": "US"},
        ip_control=True,
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert samsung["calls"] == ["US"]
    controller = entry.runtime_data
    assert controller.channel_lineup is US
    saved = hass_storage[f"{DOMAIN}.channels.{entry.entry_id}"]["data"]
    assert saved["channels"]["2110"]["name"] == "America's Test Kitchen"
    # Downloaded names win over the built-in list (1000 is not in the PDF).
    await refresh(hass, entry)
    assert (
        hass.states.get(PLAYER).attributes["media_title"]
        == "2110 · America's Test Kitchen"
    )
    tv.ipc_channel = "1000"
    await refresh(hass, entry)
    assert hass.states.get(PLAYER).attributes["media_title"] == "1000 · ION Mystery"


async def test_saved_list_is_used_without_downloading_again(
    hass, setup, tv, entry, samsung, hass_storage
):
    on_samsung_tv(tv)
    hass_storage[f"{DOMAIN}.channels.{entry.entry_id}"] = {
        "version": 1,
        "key": f"{DOMAIN}.channels.{entry.entry_id}",
        "data": US,
    }
    await setup(
        options={"channel_list_download": True, "channel_list_region": "US"},
        ip_control=True,
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert samsung["calls"] == []
    assert (
        entry.runtime_data.channel_lineup["channels"]["1000"]["name"] == "ION Mystery"
    )


async def test_new_country_downloads_its_list(
    hass, setup, tv, entry, samsung, hass_storage
):
    on_samsung_tv(tv)
    hass_storage[f"{DOMAIN}.channels.{entry.entry_id}"] = {
        "version": 1,
        "key": f"{DOMAIN}.channels.{entry.entry_id}",
        "data": US,
    }
    tv.ipc_channel = "4564"
    await setup(
        options={"channel_list_download": True, "channel_list_region": "GB"},
        ip_control=True,
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert samsung["calls"] == ["GB"]
    await refresh(hass, entry)
    assert hass.states.get(PLAYER).attributes["media_title"] == "4564 · Al Jazeera"


async def test_disabled_never_downloads_and_has_no_button(hass, setup, tv, samsung):
    on_samsung_tv(tv)
    await setup(
        options={"channel_list_download": False, "channel_list_region": "US"},
        ip_control=True,
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert samsung["calls"] == []
    assert hass.states.get(BUTTON) is None
    # Built-in U.S. names still apply.
    assert (
        hass.states.get(PLAYER).attributes["media_title"]
        == "2110 · America's Test Kitchen"
    )


async def test_existing_installs_default_to_no_download(hass, setup, tv, samsung):
    on_samsung_tv(tv)
    await setup(ip_control=True)  # created before 2.8.0: no channel options
    await hass.async_block_till_done(wait_background_tasks=True)
    assert samsung["calls"] == []
    assert hass.states.get(BUTTON) is None


async def test_built_in_us_names_are_not_used_for_another_country(
    hass, setup, tv, samsung
):
    on_samsung_tv(tv)
    await setup(
        options={"channel_list_download": False, "channel_list_region": "GB"},
        ip_control=True,
    )
    assert hass.states.get(PLAYER).attributes["media_title"] == "2110"


async def test_failed_first_download_keeps_working(
    hass, setup, tv, entry, samsung, caplog
):
    on_samsung_tv(tv)
    samsung["error"] = "cannot_connect"
    await setup(
        options={"channel_list_download": True, "channel_list_region": "US"},
        ip_control=True,
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert "could not be downloaded" in caplog.text
    assert entry.runtime_data.channel_list_error == "cannot_connect"
    # The built-in U.S. list covers it.
    assert (
        hass.states.get(PLAYER).attributes["media_title"]
        == "2110 · America's Test Kitchen"
    )
    assert hass.states.get(BUTTON).attributes["last_error"] == "cannot_connect"


async def test_update_button_downloads_again(hass, setup, tv, entry, samsung):
    on_samsung_tv(tv)
    await setup(
        options={"channel_list_download": True, "channel_list_region": "US"},
        ip_control=True,
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    await hass.services.async_call(
        "button", "press", {"entity_id": BUTTON}, blocking=True
    )
    assert samsung["calls"] == ["US", "US"]
    attributes = hass.states.get(BUTTON).attributes
    assert attributes["country"] == "United States"
    assert attributes["channels"] == 2
    samsung["error"] = "no_lineup"
    with pytest.raises(HomeAssistantError, match="no Samsung TV Plus channel list"):
        await hass.services.async_call(
            "button", "press", {"entity_id": BUTTON}, blocking=True
        )
    # The previous list stays in use.
    assert entry.runtime_data.channel_lineup is US


async def test_channel_logo_is_the_picture_on_samsung_tv(
    hass, hass_client, setup, tv, samsung, aioclient_mock
):
    on_samsung_tv(tv)
    aioclient_mock.get(
        LOGO, content=b"\x89PNG logo", headers={"Content-Type": "image/png"}
    )
    await setup(
        options={"channel_list_download": True, "channel_list_region": "US"},
        ip_control=True,
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    picture = hass.states.get(PLAYER).attributes.get("entity_picture")
    assert picture and picture.startswith("/api/media_player_proxy/")
    response = await (await hass_client()).get(picture)
    assert response.status == 200
    assert await response.read() == b"\x89PNG logo"


async def test_no_logo_without_download_or_logo(hass, setup, tv, entry, samsung):
    on_samsung_tv(tv)
    await setup(
        options={"channel_list_download": False, "channel_list_region": "US"},
        ip_control=True,
    )
    assert hass.states.get(PLAYER).attributes.get("entity_picture") is None


async def test_browse_follows_samsungs_genre_order_with_logos(
    hass, hass_ws_client, setup, tv, samsung
):
    on_samsung_tv(tv)
    await setup(
        options={"channel_list_download": True, "channel_list_region": "US"},
        ip_control=True,
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    client = await hass_ws_client()
    await client.send_json(
        {"id": 1, "type": "media_player/browse_media", "entity_id": PLAYER}
    )
    root = (await client.receive_json())["result"]
    assert root["title"] == "Samsung TV channels (United States)"
    assert [c["title"] for c in root["children"]] == ["Home & Food (1)", "Crime (1)"]
    await client.send_json(
        {
            "id": 2,
            "type": "media_player/browse_media",
            "entity_id": PLAYER,
            "media_content_type": "channels",
            "media_content_id": "genre:Home & Food",
        }
    )
    folder = (await client.receive_json())["result"]
    assert folder["children"][0]["thumbnail"] == LOGO


async def test_saved_list_removed_with_the_device(
    hass, setup, tv, entry, samsung, hass_storage
):
    on_samsung_tv(tv)
    await setup(
        options={"channel_list_download": True, "channel_list_region": "US"},
        ip_control=True,
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert f"{DOMAIN}.channels.{entry.entry_id}" in hass_storage
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert f"{DOMAIN}.channels.{entry.entry_id}" not in hass_storage


# -- setup form and options ---------------------------------------------------------


async def test_setup_form_offers_download_and_country(hass):
    hass.config.country = "GB"
    with patch(f"{PKG}.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        fields = {str(k): k for k in result["data_schema"].schema}
        assert fields["channel_list_download"].default() is True
        assert fields["channel_list_region"].default() == "GB"

        async def worker(host, token, action, callback, **options):
            if action == "pair":
                callback("paired")
            return True

        with patch(f"{PKG}.config_flow.run_worker", side_effect=worker):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    "name": "Frame TV",
                    "host": "192.168.1.25",
                    "mac": "AA-BB-CC-DD-EE-FF",
                    "broadcast_address": "255.255.255.255",
                    "channel_list_download": False,
                    "channel_list_region": "DE",
                },
            )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {
        "channel_list_download": False,
        "channel_list_region": "DE",
    }
    assert "channel_list_download" not in result["data"]


async def test_unsupported_home_country_defaults_to_us(hass):
    hass.config.country = "JP"
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    fields = {str(k): k for k in result["data_schema"].schema}
    assert fields["channel_list_region"].default() == "US"


async def test_options_change_country_and_reload(hass, setup, tv, entry, samsung):
    on_samsung_tv(tv)
    await setup(
        options={"channel_list_download": True, "channel_list_region": "US"},
        ip_control=True,
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "channel_list"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"channel_list_download": True, "channel_list_region": "GB"},
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.options["channel_list_region"] == "GB"
    assert entry.options["channel_list_download"] is True
    assert samsung["calls"] == ["US", "GB"]  # reloaded and downloaded


async def test_diagnostics_show_the_download(hass, setup, tv, entry, samsung):
    from custom_components.samsung_frame_control.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    on_samsung_tv(tv)
    await setup(
        options={"channel_list_download": True, "channel_list_region": "US"},
        ip_control=True,
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    data = (await async_get_config_entry_diagnostics(hass, entry))["media_player"]
    assert data["channel_download"] == {
        "enabled": True,
        "region": "US",
        "downloaded_region": "US",
        "downloaded": US["fetched"],
        "channels": 2,
        "last_error": None,
    }


def test_regions_are_country_codes():
    from custom_components.samsung_frame_control.const import CHANNEL_REGIONS

    assert len(CHANNEL_REGIONS) == 25
    assert all(len(code) == 2 and code.isupper() for code in CHANNEL_REGIONS)
    assert channels.API.startswith("https://vdapi.samsung.com/")


def saved(hass_storage, entry, data):
    hass_storage[f"{DOMAIN}.channels.{entry.entry_id}"] = {
        "version": 1,
        "key": f"{DOMAIN}.channels.{entry.entry_id}",
        "data": data,
    }


async def test_saved_list_of_another_country_is_not_used(
    hass, setup, tv, entry, samsung, hass_storage
):
    on_samsung_tv(tv)
    saved(hass_storage, entry, US)
    samsung["error"] = "cannot_connect"  # the new country's download fails
    tv.ipc_channel = "1000"
    await setup(
        options={"channel_list_download": True, "channel_list_region": "GB"},
        ip_control=True,
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(PLAYER).attributes["media_title"] == "1000"


async def test_saved_list_not_used_after_turning_download_off(
    hass, setup, tv, entry, samsung, hass_storage
):
    on_samsung_tv(tv)
    saved(hass_storage, entry, US)
    tv.ipc_channel = "1000"  # only in the downloaded list
    await setup(
        options={"channel_list_download": False, "channel_list_region": "US"},
        ip_control=True,
    )
    assert hass.states.get(PLAYER).attributes["media_title"] == "1000"
    tv.ipc_channel = "2110"
    await refresh(hass, entry)
    assert hass.states.get(PLAYER).attributes.get("entity_picture") is None
