"""Current artwork (observed, never inferred) and the live activity of a
running command. Power and Art Mode are shown by the media player."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import CONF_MAC, EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import ACTIVITIES, CHANNEL_REGIONS, CONF_CHANNEL_DOWNLOAD
from .entity import ArtEntity, add_supported, device_info


async def async_setup_entry(hass, entry, async_add_entities):
    # Always present, even when HA starts with an unreachable TV.
    async_add_entities([ActivitySensor(entry)])
    if entry.options.get(CONF_CHANNEL_DOWNLOAD):
        async_add_entities([ChannelListUpdatedSensor(entry)])
    add_supported(
        entry,
        async_add_entities,
        (("current_artwork", "Current artwork", "mdi:image"),),
        ArtSensor,
    )


class ArtSensor(ArtEntity, SensorEntity):
    """The artwork the TV reports; power and Art Mode are on the media player."""

    @property
    def available(self):
        return (
            self.state_data.online
            and self.state_data.power_state != "off"
            and "artwork" in self.state_data.values
        )

    @property
    def native_value(self):
        return self.state_data.values.get("artwork")

    @property
    def extra_state_attributes(self):
        item = self.state_data.artwork.get(self.native_value, {})
        return {
            key: item[key]
            for key in ("title", "name", "category_id", "matte_id")
            if key in item
        }


class ActivitySensor(CoordinatorEntity, SensorEntity):
    """What the integration is doing for the TV right now (Idle when nothing).

    Updated at each step of a command (waking, switching mode, input...), so a
    dashboard shows progress during commands that take several seconds.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "activity"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(ACTIVITIES)
    _attr_icon = "mdi:progress-clock"

    def __init__(self, entry):
        super().__init__(entry.runtime_data.coordinator)
        self._controller = entry.runtime_data
        self._attr_unique_id = f"{entry.data[CONF_MAC]}_activity"
        self._attr_device_info = device_info(entry)

    @property
    def available(self):
        return True

    @property
    def native_value(self):
        return self._controller.activity

    @property
    def extra_state_attributes(self):
        command = self._controller.last_command
        return {"command": command.get("action")} if command else None


class ChannelListUpdatedSensor(CoordinatorEntity, SensorEntity):
    """When the Samsung TV Plus channel list in use was downloaded.

    Unknown until a list for the chosen country has been downloaded. A failed
    update keeps the previous time; the failure is in the last_error attribute.
    """

    _attr_has_entity_name = True
    _attr_name = "Channel list updated"
    _attr_icon = "mdi:television-guide"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry):
        super().__init__(entry.runtime_data.coordinator)
        self._controller = entry.runtime_data
        self._attr_unique_id = f"{entry.data[CONF_MAC]}_channel_list_updated"
        self._attr_device_info = device_info(entry)

    @property
    def available(self):
        return True

    def _lineup(self):
        lineup = self._controller.channel_lineup or {}
        if lineup.get("region") != self._controller.channel_region:
            return {}
        return lineup

    @property
    def native_value(self):
        fetched = self._lineup().get("fetched")
        return dt_util.parse_datetime(fetched) if fetched else None

    @property
    def extra_state_attributes(self):
        region = self._controller.channel_region
        return {
            "country": CHANNEL_REGIONS.get(region, region),
            "channels": len(self._lineup().get("channels") or {}),
            "last_error": self._controller.channel_list_error,
        }
