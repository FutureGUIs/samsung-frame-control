"""Art catalog refresh and random-artwork buttons.

Power and Art/TV mode moved to the media player in 2.0.0.
"""

from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError

from .entity import ERRORS, ArtEntity, add_supported
from .frame import FrameError

# Replaced by the media player; removed from the entity registry on setup.
LEGACY_BUTTON_KEYS = ("art", "tv", "off")


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        [RefreshButton(entry, "refresh_art", "Refresh Art controls", "mdi:refresh")]
    )
    add_supported(
        entry,
        async_add_entities,
        (("random_artwork", "Random artwork", "mdi:shuffle-variant"),),
        RandomArtButton,
    )


class RandomArtButton(ArtEntity, ButtonEntity):
    @property
    def available(self):
        return (
            self.state_data.online
            and self.state_data.power_state != "off"
            and self.state_data.values.get("art_mode") == "on"
            and bool(self.state_data.artwork)
        )

    async def async_press(self):
        await self.set_value()


class RefreshButton(ArtEntity, ButtonEntity):
    @property
    def available(self):
        return True

    async def async_press(self):
        try:
            await self.controller.async_refresh(catalog=True)
        except FrameError as err:
            self.coordinator.push_update()
            raise HomeAssistantError(
                ERRORS.get(
                    err.code,
                    "Could not read Art controls. Turn on Art Mode and try again.",
                )
            ) from None
        self.coordinator.push_update()
