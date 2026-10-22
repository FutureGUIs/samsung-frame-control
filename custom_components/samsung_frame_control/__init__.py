"""Samsung Frame local control: media player plus capability-discovered Art controls."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_TOKEN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .button import LEGACY_BUTTON_KEYS
from .const import (
    CONF_BROADCAST,
    CONF_IPC_PORT,
    CONF_IPC_TOKEN,
    DEFAULT_BROADCAST,
    DOMAIN,
)
from .coordinator import ArtCoordinator, ipc_issue_id
from .frame import FrameController
from .ipcontrol import PORTS, IpControl

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
    controller.coordinator = ArtCoordinator(hass, entry, controller)
    # An offline TV must not prevent loading the media player.
    try:
        await controller.coordinator.async_refresh()
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await controller.async_close()
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_close()
        return True
    return False
