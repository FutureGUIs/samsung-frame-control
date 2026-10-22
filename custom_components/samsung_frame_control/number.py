"""Art brightness, warmth, and motion sensitivity."""

from homeassistant.components.number import NumberEntity, NumberMode

from .entity import ArtEntity, add_supported

NUMBERS = (
    ("brightness", "Art brightness", "mdi:brightness-6"),
    ("color_temperature", "Art color temperature", "mdi:thermometer"),
    ("motion_sensitivity", "Art motion sensitivity", "mdi:motion-sensor"),
)


async def async_setup_entry(hass, entry, async_add_entities):
    add_supported(entry, async_add_entities, NUMBERS, ArtNumber)


class ArtNumber(ArtEntity, NumberEntity):
    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1

    @property
    def native_value(self):
        return self.state_data.values.get(self.key)

    @property
    def native_min_value(self):
        return self.state_data.ranges[self.key][0]

    @property
    def native_max_value(self):
        return self.state_data.ranges[self.key][1]

    async def async_set_native_value(self, value):
        await self.set_value(value)
