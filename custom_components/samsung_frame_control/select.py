"""Artwork, slideshow, motion timer, and current artwork's mat."""

from homeassistant.components.select import SelectEntity
from homeassistant.exceptions import HomeAssistantError

from .entity import ArtEntity, add_supported

SELECTS = (
    ("artwork", "Artwork", "mdi:image"),
    ("motion_timer", "Art sleep after no motion", "mdi:timer-outline"),
    ("slideshow_interval", "Art slideshow interval", "mdi:timer-outline"),
    ("slideshow_order", "Art slideshow order", "mdi:shuffle"),
    ("slideshow_category", "Art slideshow category", "mdi:folder-image"),
    ("mat_style", "Artwork mat style", "mdi:image-frame"),
    ("mat_color", "Artwork mat color", "mdi:palette"),
)
LABELS = {
    "slideshow": "Sequential",
    "shuffleslideshow": "Shuffle",
    "MY-C0002": "My photos",
    "MY-C0004": "Favorites",
}


async def async_setup_entry(hass, entry, async_add_entities):
    add_supported(entry, async_add_entities, SELECTS, ArtSelect)


class ArtSelect(ArtEntity, SelectEntity):
    def label(self, value):
        if self.key == "artwork":
            item = self.state_data.artwork.get(value, {})
            title = item.get("title") or item.get("name")
            return f"{str(title)[:120]} [{value}]" if title else value
        if self.key in ("motion_timer", "slideshow_interval"):
            return "Off" if value == "off" else f"{value} minutes"
        return LABELS.get(value, value)

    @property
    def options(self):
        values = self.state_data.options.get(self.key, [])
        if self.key == "mat_color":
            # Legacy catalogs list exact style/color pairs; offer only those.
            matches = self.state_data.mat_colors_for(
                self.state_data.values.get("mat_style")
            )
            if matches:
                values = matches
        return [self.label(v) for v in values]

    @property
    def current_option(self):
        value = self.state_data.values.get(self.key)
        if value is None or value not in self.state_data.options.get(self.key, []):
            return None
        return self.label(value)

    @property
    def available(self):
        if not (
            self.state_data.online
            and self.state_data.power_state != "off"
            and self.coordinator.last_update_success
            and self.state_data.values.get("art_mode") == "on"
        ):
            return False
        if self.key.startswith("slideshow_"):
            return "slideshow_interval" in self.state_data.values and bool(self.options)
        if self.key in ("mat_style", "mat_color"):
            return bool(self.state_data.values.get("artwork")) and (
                self.key != "mat_color"
                or self.state_data.values.get("mat_style") != "none"
            )
        return super().available and bool(self.options)

    @property
    def extra_state_attributes(self):
        if self.key in ("slideshow_order", "slideshow_category"):
            return {
                "applies_on_next_start": self.state_data.values.get(
                    "slideshow_interval"
                )
                == "off"
            }
        return None

    async def async_select_option(self, option):
        for value in self.state_data.options.get(self.key, []):
            if self.label(value) == option:
                await self.set_value(value)
                return
        raise HomeAssistantError(
            "This Art option is no longer available. Refresh Art controls."
        )
