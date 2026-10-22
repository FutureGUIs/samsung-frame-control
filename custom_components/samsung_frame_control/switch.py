"""Automatic brightness in Art Mode."""

from homeassistant.components.switch import SwitchEntity

from .entity import ArtEntity, add_supported


async def async_setup_entry(hass, entry, async_add_entities):
    add_supported(
        entry,
        async_add_entities,
        (
            (
                "brightness_sensor_setting",
                "Art automatic brightness",
                "mdi:brightness-auto",
            ),
        ),
        ArtSwitch,
    )


class ArtSwitch(ArtEntity, SwitchEntity):
    @property
    def is_on(self):
        value = self.state_data.values.get(self.key)
        return None if value is None else value == "on"

    async def async_turn_on(self, **kwargs):
        await self.set_value("on")

    async def async_turn_off(self, **kwargs):
        await self.set_value("off")
