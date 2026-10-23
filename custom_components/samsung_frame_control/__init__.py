"""Samsung Frame local control: media player plus capability-discovered Art controls."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_TOKEN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .button import LEGACY_BUTTON_KEYS
from .channels import load_channels
from .const import (
    CONF_BROADCAST,
    CONF_CHANNEL_DOWNLOAD,
    CONF_CHANNEL_REGION,
    CONF_IPC_PORT,
    CONF_IPC_TOKEN,
    DEFAULT_BROADCAST,
    DEFAULT_CHANNEL_REGION,
    DOMAIN,
)
from .coordinator import ArtCoordinator, ipc_issue_id
from .frame import FrameController, FrameError
from .ipcontrol import PORTS, IpControl

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.MEDIA_PLAYER,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.SENSOR,
]


# Entities replaced by the media player: Art Mode / TV Mode / Power off buttons
# (2.0.0) and the Power state / Art Mode status sensors (2.4.0).
RETIRED_ENTITIES = (
    *(("button", key) for key in LEGACY_BUTTON_KEYS),
    ("sensor", "power_state"),
    ("sensor", "art_mode"),
)


def _remove_retired_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    registry = er.async_get(hass)
    for platform, key in RETIRED_ENTITIES:
        unique_id = f"{entry.data[CONF_MAC]}_{key}"
        if entity_id := registry.async_get_entity_id(platform, DOMAIN, unique_id):
            registry.async_remove(entity_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    def save_token(token: str) -> None:
        if token != entry.data.get(CONF_TOKEN):
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_TOKEN: token}
            )

    _remove_retired_entities(hass, entry)
    ipc = None
    if entry.data.get(CONF_IPC_TOKEN):
        ipc = IpControl(
            entry.data[CONF_HOST],
            entry.data.get(CONF_IPC_PORT, PORTS[0]),
            entry.data[CONF_IPC_TOKEN],
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, ipc_issue_id(entry))
    entry.runtime_data = FrameController(
        entry.data[CONF_HOST],
        entry.data[CONF_MAC],
        entry.data.get(CONF_TOKEN),
        save_token,
        hass.async_add_executor_job,
        entry.data.get(CONF_BROADCAST, DEFAULT_BROADCAST),
        session=async_get_clientsession(hass),
        ipc=ipc,
    )
    controller = entry.runtime_data
    controller.channel_list = await hass.async_add_executor_job(load_channels)
    controller.channel_download = bool(entry.options.get(CONF_CHANNEL_DOWNLOAD))
    controller.channel_region = entry.options.get(
        CONF_CHANNEL_REGION, DEFAULT_CHANNEL_REGION
    )
    controller.channel_store = Store(hass, 1, f"{DOMAIN}.channels.{entry.entry_id}")
    stored = await controller.channel_store.async_load()
    if isinstance(stored, dict) and isinstance(stored.get("channels"), dict):
        controller.channel_lineup = stored
    controller.coordinator = ArtCoordinator(hass, entry, controller)
    # An offline TV must not prevent loading the media player.
    try:
        await controller.coordinator.async_refresh()
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        if controller.channel_download and (
            (controller.channel_lineup or {}).get("region") != controller.channel_region
        ):
            # First setup or a new country: download in the background.
            entry.async_create_background_task(
                hass, _initial_channel_list(controller), f"{DOMAIN} channel list"
            )
    except BaseException:
        await controller.async_close()
        raise
    return True


async def _initial_channel_list(controller) -> None:
    try:
        await controller.async_update_channel_list()
    except FrameError as err:
        _LOGGER.warning(
            "Samsung TV Plus channel list for %s could not be downloaded (%s); "
            "using the previous or built-in list. Press Update channel list to retry.",
            controller.channel_region,
            err.code,
        )


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await Store(hass, 1, f"{DOMAIN}.channels.{entry.entry_id}").async_remove()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_close()
        return True
    return False
