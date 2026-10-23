"""Local media player: power, Art Mode / input source, and volume.

Everything here uses the TV's LAN interfaces only (Art and remote WebSocket
channels, the REST power status, UPnP RenderingControl, optional IP Control
and Wake-on-LAN).
"""

from __future__ import annotations

import hashlib
from typing import Any

from homeassistant.components.media_player import (
    BrowseError,
    BrowseMedia,
    MediaClass,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.const import CONF_MAC
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .channels import channel_label, channel_names, downloaded, genre_order
from .const import (
    CHANNEL_REGIONS,
    CONF_CHANNEL_NAMES,
    DEFAULT_INPUT_NAMES,
    INPUTS,
    REPORTED_INPUTS,
    SOURCE_ART,
)
from .entity import command_error, device_info
from .frame import FrameError

FEATURES = (
    MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.TURN_OFF
    | MediaPlayerEntityFeature.SELECT_SOURCE
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.VOLUME_STEP
)
STEP_FEATURES = (
    MediaPlayerEntityFeature.NEXT_TRACK | MediaPlayerEntityFeature.PREVIOUS_TRACK
)
SLIDESHOW_FEATURES = MediaPlayerEntityFeature.PLAY | MediaPlayerEntityFeature.PAUSE
CHANNEL_FEATURES = (
    MediaPlayerEntityFeature.PLAY_MEDIA | MediaPlayerEntityFeature.BROWSE_MEDIA
)
BROWSE_ROOT = "channels"
BROWSE_GENRE = "genre:"
VOLUME_FEATURES = (
    MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.VOLUME_STEP
)
# Play with no remembered interval (never seen running): one piece per 15 min.
DEFAULT_RESUME_INTERVAL = "15"


def input_names(options) -> dict[str, str]:
    """Visible inputs in order; a blank name hides that input."""
    names = {}
    for input_id, default in DEFAULT_INPUT_NAMES.items():
        name = str(options.get(input_id, default)).strip()
        if name:
            names[input_id] = name
    return names


class StoredSource(ExtraStoredData):
    """Survives restarts so turn-on can return to the previous mode."""

    def __init__(self, last_mode, last_input, off_reported=False, resume_interval=None):
        self.last_mode, self.last_input = last_mode, last_input
        self.off_reported = off_reported
        self.resume_interval = resume_interval

    def as_dict(self) -> dict[str, Any]:
        return {
            "last_mode": self.last_mode,
            "last_input": self.last_input,
            "off_reported": self.off_reported,
            "resume_interval": self.resume_interval,
        }


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([FrameMediaPlayer(entry)])


class FrameMediaPlayer(CoordinatorEntity, RestoreEntity, MediaPlayerEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_device_class = MediaPlayerDeviceClass.TV
    _attr_supported_features = FEATURES
    # Samsung volume is 0-100; one step matches one remote press.
    _attr_volume_step = 0.01

    def __init__(self, entry):
        super().__init__(entry.runtime_data.coordinator)
        self._controller = entry.runtime_data
        self._attr_unique_id = f"{entry.data[CONF_MAC]}_media_player"
        self._attr_device_info = device_info(entry)
        self._inputs = input_names(entry.options)
        self._channel_names = dict(entry.options.get(CONF_CHANNEL_NAMES, {}))

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        stored = await self.async_get_last_extra_data()
        if stored is None:
            return
        data = stored.as_dict()
        resume = data.get("resume_interval")
        if self._controls.resume_interval is None and resume not in (None, "off"):
            self._controls.resume_interval = str(resume)
        if self._controller.last_mode is not None:
            return
        if data.get("last_mode") in ("art", "tv"):
            self._controller.last_mode = data["last_mode"]
        if data.get("last_input") in INPUTS:
            self._controller.last_input = data["last_input"]
        # A TV last seen explicitly off still reads as off (standby) after a
        # restart, as long as it keeps refusing connections.
        if data.get("off_reported") is True:
            self._controller.off_reported = True
            self.coordinator.schedule_refresh()

    @property
    def extra_restore_state_data(self) -> StoredSource:
        return StoredSource(
            self._controller.last_mode,
            self._controller.last_input,
            self._controller.off_reported,
            self._controls.resume_interval,
        )

    @property
    def available(self) -> bool:
        # Turning the TV on is the point of this entity, so it never goes
        # unavailable; unknown power is shown as an unknown state instead.
        return True

    @property
    def _controls(self):
        return self._controller.controls

    @property
    def state(self) -> MediaPlayerState | None:
        power = self._controls.power_state
        if power == "off":
            return MediaPlayerState.OFF
        if power != "on":
            return None
        interval = self._slideshow_interval()
        if interval is not None:
            # In Art Mode the slideshow is what plays: Pause stops it.
            return (
                MediaPlayerState.PAUSED
                if interval == "off"
                else MediaPlayerState.PLAYING
            )
        if self._on_tv_source():
            # Live TV plays; dashboards only show Previous/Next (channel
            # down/up) while a player is playing or paused.
            return MediaPlayerState.PLAYING
        return MediaPlayerState.ON

    def _in_art(self) -> bool:
        return self._controls.power_state == "on" and self._source()[0] == SOURCE_ART

    def _slideshow_interval(self) -> str | None:
        if not self._in_art():
            return None
        return self._controls.values.get("slideshow_interval")

    def _on_tv_source(self) -> bool:
        return self._controller.on_tv_source()

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        features = FEATURES
        supported = self._controls.supported
        if self._in_art():
            # Art Mode is silent: no volume slider or mute on the card.
            features &= ~VOLUME_FEATURES
            if "next_artwork" in supported:
                features |= STEP_FEATURES
            if self._slideshow_interval() is not None:
                features |= SLIDESHOW_FEATURES
        elif self._on_tv_source():
            # Confirmed only through IP Control, which tuning also needs.
            features |= STEP_FEATURES | CHANNEL_FEATURES
        return features

    def _artwork(self) -> str | None:
        """Artwork id whose thumbnail is the picture, while Art Mode shows."""
        if not self._in_art():
            return None
        content_id = self._controls.values.get("artwork")
        if not content_id or self._controller.thumbnail_unavailable(content_id):
            return None
        return content_id

    @property
    def media_image_hash(self) -> str | None:
        key = self._artwork() or self._channel_logo()
        if key is None:
            return None
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def _channel_logo(self) -> str | None:
        """Logo of the channel on the Samsung TV source (downloaded list)."""
        number = self._channel()
        if number is None:
            return None
        return self._channels.get(number, {}).get("logo")

    async def async_get_media_image(self) -> tuple[bytes | None, str | None]:
        content_id = self._artwork()
        if content_id is None:
            if logo := self._channel_logo():
                # Fetched by Home Assistant from Samsung and cached.
                return await self._async_fetch_image_from_cache(logo)
            return None, None
        image = await self._controller.async_thumbnail(content_id)
        if image is None:
            # The hash now reads None, so the card drops the broken picture.
            self.async_write_ha_state()
            return None, None
        return image

    @property
    def source_list(self) -> list[str]:
        return [SOURCE_ART, *self._inputs.values()]

    @property
    def source(self) -> str | None:
        return self._source()[0]

    def _name(self, input_id, fallback=None):
        return self._inputs.get(input_id) or DEFAULT_INPUT_NAMES.get(input_id, fallback)

    def _source(self) -> tuple[str | None, bool]:
        """(source, confirmed). Confirmed means read from the TV this poll."""
        controller = self._controller
        if self._controls.power_state != "on":
            return None, False
        mode = self._controls.values.get("art_mode")
        ambient = controller._ipc_states.get("pictureMode") == "Ambient"
        if mode == "on" or (mode is None and controller.input_confirmed and ambient):
            # While art shows, IP Control still reports the input behind it.
            return SOURCE_ART, True
        if controller.input_confirmed and controller.input_reported:
            reported = controller.input_reported
            return self._name(REPORTED_INPUTS.get(reported), reported), True
        if mode == "off" and controller.last_input:
            # Not observed now: IP Control unpaired, or still starting after wake.
            return self._name(controller.last_input), False
        return None, False

    def _channel(self) -> str | None:
        """Channel number while the TV source is confirmed (IP Control)."""
        controller = self._controller
        if self._controls.values.get("art_mode") == "on":
            return None
        if not (controller.input_confirmed and controller.input_reported == "TV"):
            return None
        return controller.channel

    @property
    def media_channel(self) -> str | None:
        return self._channel()

    @property
    def _channels(self) -> dict[str, dict]:
        return channel_names(self._controller, self._channel_names)

    @property
    def media_title(self) -> str | None:
        number = self._channel()
        if number is None:
            return None
        return channel_label(number, self._channels)

    async def async_play_media(self, media_type, media_id, **kwargs) -> None:
        if str(media_type) not in (MediaType.CHANNEL, "channel"):
            raise HomeAssistantError("Only Samsung TV channels can be played.")
        await self._run(
            "channel",
            self._controller.async_tune_channel(str(media_id)),
            refresh=False,
        )

    async def async_browse_media(
        self, media_content_type=None, media_content_id=None
    ) -> BrowseMedia:
        """Channels by genre, from the built-in line-up and your own names."""
        channels = self._channels
        grouped: dict[str, list[str]] = {}
        for number, info in channels.items():
            grouped.setdefault(info.get("genre") or "Other channels", []).append(number)
        # Samsung's own genre order when the list was downloaded.
        order = [g for g in genre_order(self._controller) if g in grouped]
        genres = {g: grouped[g] for g in order + [g for g in grouped if g not in order]}
        if media_content_id in (None, "", BROWSE_ROOT):
            title = "Samsung TV channels"
            if lineup := downloaded(self._controller):
                region = lineup.get("region")
                title += f" ({CHANNEL_REGIONS.get(region, region)})"
            elif effective := self._controller.channel_list.get("effective"):
                title += f" ({effective})"
            return BrowseMedia(
                media_class=MediaClass.DIRECTORY,
                media_content_id=BROWSE_ROOT,
                media_content_type=MediaType.CHANNELS,
                title=title,
                can_play=False,
                can_expand=True,
                children_media_class=MediaClass.DIRECTORY,
                children=[
                    BrowseMedia(
                        media_class=MediaClass.DIRECTORY,
                        media_content_id=BROWSE_GENRE + genre,
                        media_content_type=MediaType.CHANNELS,
                        title=f"{genre} ({len(numbers)})",
                        can_play=False,
                        can_expand=True,
                    )
                    for genre, numbers in genres.items()
                ],
            )
        genre = str(media_content_id).removeprefix(BROWSE_GENRE)
        if not str(media_content_id).startswith(BROWSE_GENRE) or genre not in genres:
            raise BrowseError(f"Unknown channel folder: {media_content_id}")
        return BrowseMedia(
            media_class=MediaClass.DIRECTORY,
            media_content_id=media_content_id,
            media_content_type=MediaType.CHANNELS,
            title=genre,
            can_play=False,
            can_expand=True,
            children_media_class=MediaClass.CHANNEL,
            children=[
                BrowseMedia(
                    media_class=MediaClass.CHANNEL,
                    media_content_id=number,
                    media_content_type=MediaType.CHANNEL,
                    title=channel_label(number, channels),
                    thumbnail=channels[number].get("logo"),
                    can_play=True,
                    can_expand=False,
                )
                for number in genres[genre]
            ],
        )

    @property
    def media_content_type(self) -> str | None:
        return MediaType.CHANNEL if self._channel() else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        source, confirmed = self._source()
        attributes = {
            "activity": self._controller.activity,
            # Where the power state came from: local_rest, ip_control,
            # standby, last_known (kept off while the TV does not answer)...
            "power_source": self._controls.power_source,
        }
        if source is not None:
            attributes["source_confirmed"] = confirmed
        number = self._channel()
        if number is not None and (
            genre := self._channels.get(number, {}).get("genre")
        ):
            attributes["channel_genre"] = genre
        return attributes

    @property
    def volume_level(self) -> float | None:
        if self._controls.power_state != "on" or self._controller.volume is None:
            return None
        return self._controller.volume / 100

    @property
    def is_volume_muted(self) -> bool | None:
        if self._controls.power_state != "on":
            return None
        return self._controller.muted

    async def _run(self, action, call, *, refresh=True):
        try:
            await call
        except FrameError as err:
            raise command_error(err, action) from err
        except OSError as err:
            raise HomeAssistantError(
                "Could not send Wake-on-LAN or start the Samsung worker."
            ) from err
        finally:
            self.coordinator.push_update()
        if refresh:
            self.coordinator.schedule_refresh()

    async def async_turn_on(self) -> None:
        await self._run("turn on", self._controller.async_turn_on())

    async def async_turn_off(self) -> None:
        await self._run(
            "turn off", self._controller.async_command("off"), refresh=False
        )

    async def async_select_source(self, source: str) -> None:
        if source == SOURCE_ART:
            await self._run(source, self._controller.async_command("art"))
            return
        for input_id, name in self._inputs.items():
            if name == source:
                await self._run(source, self._controller.async_select_input(input_id))
                return
        raise HomeAssistantError(f"Unknown source: {source}")

    async def async_set_volume_level(self, volume: float) -> None:
        await self._run(
            "volume",
            self._controller.async_set_volume(round(volume * 100)),
            refresh=False,
        )

    async def _step(self, direction: int) -> None:
        level = self.volume_level
        if level is None:
            # Level unread (UPnP unavailable): fall back to the remote key.
            key = "KEY_VOLUP" if direction > 0 else "KEY_VOLDOWN"
            await self._run("volume", self._controller.async_send_key(key))
            return
        await self.async_set_volume_level(
            min(1.0, max(0.0, level + direction * self.volume_step))
        )

    async def async_volume_up(self) -> None:
        await self._step(1)

    async def async_volume_down(self) -> None:
        await self._step(-1)

    async def async_media_next_track(self) -> None:
        await self._step_media(1)

    async def async_media_previous_track(self) -> None:
        await self._step_media(-1)

    async def _step_media(self, direction: int) -> None:
        if self._in_art():
            key = "next_artwork" if direction > 0 else "previous_artwork"
            await self._run(
                "next" if direction > 0 else "previous",
                self._controller.async_set_art_value(key),
                refresh=False,
            )
            return
        if self._on_tv_source():
            await self._run(
                "channel", self._controller.async_channel_step(direction), refresh=False
            )
            return
        raise HomeAssistantError(
            "Next and previous work in Art Mode (artwork) or on the Samsung TV "
            "source (channel)."
        )

    async def async_media_pause(self) -> None:
        if not self._in_art():
            raise HomeAssistantError("Pause stops the Art Mode slideshow.")
        await self._run(
            "pause",
            self._controller.async_set_art_value("slideshow_interval", "off"),
            refresh=False,
        )

    async def async_media_play(self) -> None:
        if not self._in_art():
            raise HomeAssistantError("Play starts the Art Mode slideshow.")
        interval = self._controls.resume_interval or DEFAULT_RESUME_INTERVAL
        await self._run(
            "play",
            self._controller.async_set_art_value("slideshow_interval", interval),
            refresh=False,
        )

    async def async_mute_volume(self, mute: bool) -> None:
        await self._run("mute", self._controller.async_set_mute(mute), refresh=False)
