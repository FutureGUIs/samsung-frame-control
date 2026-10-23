"""Art catalog refresh and random-artwork buttons.

Power and Art/TV mode moved to the media player in 2.0.0.
"""

from homeassistant.components.button import ButtonEntity
from homeassistant.const import CONF_MAC, EntityCategory
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CHANNEL_REGIONS, CONF_CHANNEL_DOWNLOAD
from .entity import ERRORS, ArtEntity, add_supported, device_info
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
    if entry.options.get(CONF_CHANNEL_DOWNLOAD):
        async_add_entities([ChannelListButton(entry)])


CHANNEL_LIST_ERRORS = {
    "cannot_connect": "Could not reach Samsung's website. Check Home Assistant's internet connection and try again.",
    "no_lineup": "Samsung has no Samsung TV Plus channel list for this country. Choose another country under Configure > Channel list.",
    "too_few_channels": "Samsung returned an unusually short channel list; it was not used.",
    "invalid_response": "Samsung returned an unreadable channel list; it was not used.",
}


class ChannelListButton(CoordinatorEntity, ButtonEntity):
    """Download the Samsung TV Plus line-up for the chosen country again."""

    _attr_has_entity_name = True
    _attr_name = "Update channel list"
    _attr_icon = "mdi:playlist-refresh"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry):
        super().__init__(entry.runtime_data.coordinator)
        self._controller = entry.runtime_data
        self._attr_unique_id = f"{entry.data[CONF_MAC]}_update_channel_list"
        self._attr_device_info = device_info(entry)

    @property
    def available(self):
        return True

    @property
    def extra_state_attributes(self):
        controller = self._controller
        lineup = controller.channel_lineup or {}
        current = lineup.get("region") == controller.channel_region
        return {
            "country": CHANNEL_REGIONS.get(
                controller.channel_region, controller.channel_region
            ),
            "channels": len(lineup.get("channels") or {}) if current else 0,
            "downloaded": lineup.get("fetched") if current else None,
            "last_error": controller.channel_list_error,
        }

    async def async_press(self):
        try:
            await self._controller.async_update_channel_list()
        except FrameError as err:
            raise HomeAssistantError(
                CHANNEL_LIST_ERRORS.get(
                    err.code,
                    f"The channel list could not be downloaded ({err.code}).",
                )
            ) from None
        finally:
            self.coordinator.push_update()


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
