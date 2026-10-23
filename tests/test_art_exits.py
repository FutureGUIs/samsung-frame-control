"""Art Mode exits no Home Assistant command explains (2.8.2 diagnostics).

Reported: the TV sometimes leaves Art Mode for Samsung TV by itself, every
30-60 minutes. The integration records each such exit with what it had sent
the TV in the two minutes before, to show whether it was involved.
"""

import pytest
from frame_fakes import refresh

from custom_components.samsung_frame_control.diagnostics import (
    async_get_config_entry_diagnostics,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

ENTITY = "media_player.frame_tv"


async def exits(hass, entry):
    return (await async_get_config_entry_diagnostics(hass, entry))["art_mode_exits"]


async def test_unexplained_exit_is_recorded_with_recent_requests(
    hass, setup, tv, entry, caplog
):
    tv.ipc_input = "TV"  # Samsung TV behind the art
    await setup(ip_control=True)
    assert await exits(hass, entry) == []
    tv.art = "off"  # the TV leaves Art Mode on its own
    await refresh(hass, entry)
    [record] = await exits(hass, entry)
    assert record["detected_by"] == "ip_control"
    assert record["input_after"] == "TV"
    assert record["channel_after"] == "2110"
    kinds = {r["kind"] for r in record["requests_before"]}
    assert "ip_control" in kinds and "art" in kinds
    assert all(r["seconds_before"] <= 120 for r in record["requests_before"])
    assert any(r["detail"] == "read_state" for r in record["requests_before"])
    assert "left Art Mode without a command from Home Assistant" in caplog.text
    # Nothing secret in the record.
    assert "ipc-token" not in str(record) and "stored-token" not in str(record)
    await refresh(hass, entry)
    assert len(await exits(hass, entry)) == 1  # recorded once


async def test_home_assistant_commands_are_not_recorded(hass, setup, tv, entry):
    await setup(ip_control=True)  # Art Mode on HDMI1
    await hass.services.async_call(
        "media_player",
        "select_source",
        {"entity_id": ENTITY, "source": "HDMI 2"},
        blocking=True,
    )
    await refresh(hass, entry)
    await refresh(hass, entry)
    assert await exits(hass, entry) == []
    await hass.services.async_call(
        "media_player",
        "select_source",
        {"entity_id": ENTITY, "source": "Art Mode"},
        blocking=True,
    )
    await refresh(hass, entry)
    entry.runtime_data._command_end = 0  # the 20 s after the command passed
    tv.art = "off"  # now by itself
    await refresh(hass, entry)
    assert len(await exits(hass, entry)) == 1
    record = (await exits(hass, entry))[0]
    assert any(r["kind"] == "command" for r in record["requests_before"])


async def test_tv_turning_off_is_not_an_exit(hass, setup, tv, entry):
    await setup(ip_control=True)
    tv.power, tv.ipc_power = "off", "powerOff"
    await refresh(hass, entry)
    await refresh(hass, entry)
    assert await exits(hass, entry) == []


async def test_exit_seen_by_the_art_channel_without_ip_control(hass, setup, tv, entry):
    await setup()
    controller = entry.runtime_data
    controller._event({"event": "art_mode_changed", "value": "off"})
    [record] = await exits(hass, entry)
    assert record["detected_by"] == "art_event"
    assert {"kind": "art_event", "detail": "art_mode_changed"}.items() <= next(
        r for r in record["requests_before"] if r["kind"] == "art_event"
    ).items()


async def test_only_the_last_ten_exits_are_kept(hass, setup, tv, entry):
    await setup()
    controller = entry.runtime_data
    for _ in range(12):
        controller._event({"event": "art_mode_changed", "value": "on"})
        controller._event({"event": "art_mode_changed", "value": "off"})
    assert len(await exits(hass, entry)) == 10


async def test_update_button_icon_exists(hass, setup, tv):
    """mdi:playlist-refresh does not exist in Material Design Icons; the
    button showed no icon in 2.8.0 and 2.8.1."""
    from unittest.mock import patch

    from frame_fakes import PKG

    from custom_components.samsung_frame_control.channels import ChannelListError

    with patch(
        f"{PKG}.frame.fetch_lineup", side_effect=ChannelListError("cannot_connect")
    ):
        await setup(
            options={"channel_list_download": True, "channel_list_region": "US"}
        )
    state = hass.states.get("button.frame_tv_update_channel_list")
    assert state.attributes["icon"] == "mdi:cloud-download-outline"


async def test_standby_from_art_mode_is_not_an_exit(hass, setup, tv, entry):
    await setup()  # no IP Control: the Art channel reports standby
    entry.runtime_data._event({"event": "go_to_standby"})
    entry.runtime_data._event({"event": "art_mode_changed", "value": "off"})
    assert await exits(hass, entry) == []


async def test_only_the_last_two_minutes_of_requests_are_kept(hass, setup, tv, entry):
    import time

    await setup()
    controller = entry.runtime_data
    controller._trace.appendleft((time.monotonic() - 300, "art", "old_request"))
    controller._event({"event": "art_mode_changed", "value": "off"})
    [record] = await exits(hass, entry)
    assert all(r["detail"] != "old_request" for r in record["requests_before"])
