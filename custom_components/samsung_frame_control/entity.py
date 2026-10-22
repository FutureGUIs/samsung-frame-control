"""Common identity, error messages and capability-driven entity registration."""

import logging

from homeassistant.const import CONF_MAC, EntityCategory
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .frame import FrameError

_LOGGER = logging.getLogger(__package__)

ERRORS = {
    "state_unavailable": "The TV did not return enough settings to prepare this change. No change command was sent. Refresh Art controls and try again.",
    "art_inactive": "Turn on Art Mode before changing artwork or Art settings.",
    "unsupported": "This Art control is not reported by this TV.",
    "invalid_value": "The TV does not report this value as a valid option.",
    "unconfirmed": "The command was sent, but the TV did not confirm the requested setting. Refresh Art controls and check the TV.",
    "rejected": "The TV rejected this Art setting. The value or artwork may not support it.",
    "invalid_auth": "TV authorization failed. Reconfigure the integration and allow access on the TV.",
}


COMMAND_ERRORS = {
    "startup_timeout": "TV did not finish waking or accepting mode commands within the startup time limit. Check its network wake setting, MAC, and broadcast address.",
    "invalid_auth": "TV authorization expired or was denied. Reconfigure Samsung Frame Control and allow HA Frame Control on the TV.",
    "unreachable_off": "TV is unreachable; it may already be off. No power command was sent.",
    "wake_failed": "TV did not become reachable after Wake-on-LAN. Check the MAC, network, broadcast address, and TV network wake setting.",
    "timeout": "TV connection timed out before command delivery was confirmed.",
    "cannot_connect": "Samsung connection or command failed. If this only happens with the TV off, check its active-interface MAC and Wake On Lan Broadcast Address. If it also happens with the TV on, try Reconfigure and approve pairing.",
    "worker_failed": "The isolated Samsung worker failed. Check that the integration folder was fully replaced and Home Assistant installed samsungtvws, then restart Home Assistant.",
    "unloaded": "The integration was unloaded while this command was queued. Retry after it finishes loading.",
    "unconfirmed": "The command was sent, but the TV did not confirm the result. Check the TV and try again.",
    "tv_off": "The TV is off or unreachable. Turn it on before changing volume.",
    "input_unconfirmed": "The input key was sent, but the TV still reports a different input. Your TV may not support selecting this input directly; check that a device is connected to it.",
    "rejected": "The TV rejected this command.",
    "invalid_value": "The TV does not accept this value.",
    "invalid_response": "The TV returned an unreadable response.",
}


def command_error(err: FrameError, action: str) -> HomeAssistantError:
    """Translate a FrameError into an actionable message, logging the detail."""
    detail = f"Code: {err.code}; step: {err.stage}"
    if err.kind:
        detail += f"; exception: {err.kind}"
    detail += f"; command sent: {err.sent}"
    _LOGGER.warning("Samsung Frame %s failed: %s", action, detail)
    return HomeAssistantError(
        COMMAND_ERRORS.get(
            err.code,
            "Samsung Frame command failed. Check TV connectivity and authorization.",
        )
        + f" ({detail})"
    )


# Set-and-forget Art settings: shown under Configuration on the device page.
CONFIG_KEYS = {
    "brightness",
    "color_temperature",
    "brightness_sensor_setting",
    "motion_sensitivity",
    "motion_timer",
    "slideshow_interval",
    "slideshow_order",
    "slideshow_category",
    "mat_style",
    "mat_color",
    "refresh_art",
}


def device_info(entry):
    return DeviceInfo(
        identifiers={(DOMAIN, entry.data[CONF_MAC])},
        connections={(CONNECTION_NETWORK_MAC, entry.data[CONF_MAC])},
        name=entry.title,
        manufacturer="Samsung",
        model="The Frame",
    )


class ArtEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, entry, key, name, icon):
        super().__init__(entry.runtime_data.coordinator)
        self.controller = entry.runtime_data
        self.key = key
        self._attr_unique_id = f"{entry.data[CONF_MAC]}_{key}"
        self._attr_name, self._attr_icon = name, icon
        self._attr_device_info = device_info(entry)
        if key in CONFIG_KEYS:
            self._attr_entity_category = EntityCategory.CONFIG

    @property
    def state_data(self):
        return self.controller.controls

    @property
    def available(self):
        return (
            self.state_data.online
            and self.state_data.power_state != "off"
            and self.coordinator.last_update_success
            and self.state_data.values.get("art_mode") == "on"
            and self.key in self.state_data.values
        )

    async def set_value(self, value=None):
        try:
            await self.controller.async_set_art_value(self.key, value)
        except FrameError as err:
            raise HomeAssistantError(
                ERRORS.get(err.code, f"Art command failed ({err.code}).")
            ) from None


def add_supported(entry, async_add_entities, descriptions, factory):
    """Create entities when first observed, including after an offline HA start."""
    added = set()

    def discover():
        entities = []
        for key, name, icon in descriptions:
            if key in entry.runtime_data.controls.supported and key not in added:
                added.add(key)
                entities.append(factory(entry, key, name, icon))
        if entities:
            async_add_entities(entities)

    entry.async_on_unload(entry.runtime_data.coordinator.async_add_listener(discover))
    discover()
