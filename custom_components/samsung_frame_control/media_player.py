"""Local media player: power, Art Mode / input source, and volume.

Everything here uses the TV's LAN interfaces only (Art and remote WebSocket
channels, the REST power status, UPnP RenderingControl, optional IP Control
and Wake-on-LAN).
"""

from __future__ import annotations

import hashlib
from typing import Any

from homeassistant.components.media_player import (
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

from .const import (
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
        controller = self._controller
        return (
            self._controls.power_state == "on"
            and self._controls.values.get("art_mode") != "on"
            and controller.input_confirmed
            and controller.input_reported == "TV"
        )

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
            features |= STEP_FEATURES
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
        content_id = self._artwork()
        if content_id is None:
            return None
        return hashlib.sha256(content_id.encode()).hexdigest()[:16]

    async def async_get_media_image(self) -> tuple[bytes | None, str | None]:
        content_id = self._artwork()
        if content_id is None:
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
    def media_title(self) -> str | None:
        number = self._channel()
        if number is None:
            return None
        name = self._channel_names.get(number)
        return f"{number} · {name}" if name else number

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
