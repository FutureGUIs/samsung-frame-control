"""Current artwork (observed, never inferred) and the live activity of a
running command. Power and Art Mode are shown by the media player."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import CONF_MAC
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ACTIVITIES
from .entity import ArtEntity, add_supported, device_info


async def async_setup_entry(hass, entry, async_add_entities):
    # Always present, even when HA starts with an unreachable TV.
    async_add_entities([ActivitySensor(entry)])
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
