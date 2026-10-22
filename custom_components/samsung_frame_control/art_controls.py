"""Capability discovery and verified Art settings, independent of HA entities."""

from __future__ import annotations

import asyncio
import json
import random
import time

from .async_art import ArtError

SETTINGS = {
    "brightness": (0, 10),
    "color_temperature": (-5, 5),
    "motion_sensitivity": (1, 3),
    "motion_timer": None,
    "brightness_sensor_setting": None,
}
TIMERS = ["off", "5", "15", "30", "60", "120", "240"]
INTERVALS = [
    "off",
    "3",
    "5",
    "10",
    "15",
    "30",
    "60",
    "120",
    "240",
    "360",
    "720",
    "1440",
    "10080",
]
SLIDESHOW_KEYS = ("slideshow_interval", "slideshow_order", "slideshow_category")
# Media player Next/Previous in Art Mode; written like an artwork selection.
STEP_KEYS = {"next_artwork": 1, "previous_artwork": -1}
MAT_KEYS = ("mat_style", "mat_color")
READBACK_ATTEMPTS = 3
READBACK_DELAY = 0.5


def decoded(value, kind):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return kind()
    return value if isinstance(value, kind) else kind()


class ArtControls:
    def __init__(self, client):
        self.client = client
        self.values = {}
        self.ranges = {}
        self.options = {}
        self.supported = set()
        self.online = False
        self.power_state = None
        self.power_source = "not_checked"
        self.artwork = {}
        # None until first read: monotonic() starts at host boot, so a fixed
        # 0 would skip the catalog when HA starts within 5 minutes of booting.
        self._catalog_time = None
        self._slideshow_api = None
        # Last running slideshow interval, so Play resumes what Pause stopped.
        self.resume_interval = None
        # Pieces in the slideshow, in the TV's order (auto_rotation reply).
        self.slideshow_items = []
        self._slideshow_pending = {}
        self._mat_ids = {}
        self.last_operation = {}

    # -- read-only views for entities and diagnostics ------------------

    @property
    def slideshow_api(self):
        """Detected slideshow getter family ("slideshow"/"auto_rotation")."""
        return self._slideshow_api

    @property
    def slideshow_next_start(self):
        """Order/category chosen while stopped; applied on the next start."""
        return dict(self._slideshow_pending)

    @property
    def mat_ids(self):
        """Complete mat IDs reported by the TV catalog."""
        return sorted(set(self._mat_ids.values()))

    def mat_colors_for(self, style):
        """Colors the catalog lists for a style; empty when not enumerated."""
        return [color for s, color in self._mat_ids if s == style and color]

    def offline(self):
        self.online = False
        self.values.clear()

    def set_power(self, state, source):
        self.power_state, self.power_source = state, source
        if state == "off" or source == "unreachable":
            self.offline()
        if state == "off":
            self.supported.add("art_mode")
            self.values["art_mode"] = "off"

    def push(self, data):
        """Accept observed states only, never turn send acknowledgements into state."""
        event = data.get("event")
        if event == "go_to_standby":
            self.set_power("off", "standby_event")
        elif event == "disconnected":
            self.offline()
        elif self.power_state == "off":
            # Samsung can continue reporting the remembered Art setting while
            # the panel is in standby. It cannot override observed power-off.
            return
        elif event in ("artmode_status", "art_mode_changed"):
            value = data.get("value", data.get("status"))
            if value in ("on", "off"):
                self.values["art_mode"] = value
        elif event in ("current_artwork", "artwork_changed") and data.get("content_id"):
            self.values["artwork"] = str(data["content_id"])
            # A new picture can have a different mat. Invalidate until read back.
            self.values.pop("mat_style", None)
            self.values.pop("mat_color", None)
        elif event in SETTINGS and "value" in data:
            self._setting(event, data)

    async def _optional(self, request, events, **params):
        try:
            return await self.client.request(
                request, events=events, timeout=2, **params
            )
        except ArtError as err:
            if err.code not in ("timeout", "rejected"):
                raise
            return None

    def _setting(self, key, item):
        value = item.get("value")
        if value is None:
            return
        if SETTINGS[key]:
            try:
                low, high = SETTINGS[key]
                low, high = float(item.get("min", low)), float(item.get("max", high))
                number = float(value)
                if not low <= number <= high:
                    return
                self.ranges[key] = (low, high)
                self.values[key] = number
            except (TypeError, ValueError):
                return
        elif key == "brightness_sensor_setting":
            if str(value).lower() not in ("on", "off"):
                return
            self.values[key] = str(value).lower()
        else:
            choices = decoded(item.get("valid_values", []), list)
            self.options[key] = [str(v) for v in choices] if choices else list(TIMERS)
            self.values[key] = str(value)
            if str(value) not in self.options[key]:
                self.options[key].append(str(value))
        self.supported.add(key)

    async def read_settings(self):
        for key in SETTINGS:
            self.values.pop(key, None)
        data = await self._optional("get_artmode_settings", ("artmode_settings",))
        if data:
            for item in decoded(data.get("data"), list):
                if isinstance(item, dict) and item.get("item") in SETTINGS:
                    self._setting(item["item"], item)
        # Older Frames expose individual brightness/color-temperature getters.
        for key in ("brightness", "color_temperature"):
            if key not in self.values:
                data = await self._optional("get_" + key, (key,))
                if data:
                    self._setting(key, data)

    async def read_current(self):
        for key in ("artwork", "mat_style", "mat_color"):
            self.values.pop(key, None)
        data = await self._optional("get_current_artwork", ("current_artwork",))
        if not data or not data.get("content_id"):
            return
        cid = str(data["content_id"])
        self.values["artwork"] = cid
        self.supported.add("current_artwork")
        matte = data.get("matte_id")
        if matte is None:
            matte = self.artwork.get(cid, {}).get("matte_id")
        if isinstance(matte, str):
            style, color = self._split_mat(matte)
            self.values["mat_style"] = style
            if color:
                self.values["mat_color"] = color

    async def read_slideshow(self):
        for key in SLIDESHOW_KEYS:
            self.values.pop(key, None)
        apis = list(
            dict.fromkeys(
                ([self._slideshow_api] if self._slideshow_api else [])
                + ["slideshow", "auto_rotation"]
            )
        )
        for api in apis:
            data = await self._optional("get_" + api + "_status", (api + "_status",))
            if not data or "value" not in data:
                continue
            self._slideshow_api = api
            self.values["slideshow_interval"] = str(data["value"])
            if str(data["value"]) != "off":
                self.resume_interval = str(data["value"])
            items = decoded(data.get("content_list"), list)
            self.slideshow_items = [
                str(item["content_id"])
                for item in items
                if isinstance(item, dict) and item.get("content_id")
            ]
            self.options["slideshow_interval"] = list(
                dict.fromkeys(INTERVALS + [str(data["value"])])
            )
            if data.get("type") in ("slideshow", "shuffleslideshow"):
                self.values["slideshow_order"] = data["type"]
            if data.get("category_id"):
                self.values["slideshow_category"] = str(data["category_id"])
            if str(data["value"]) == "off":
                # Older Frames omit category/type while stopped. Keep only
                # explicitly chosen next-start preferences, not invented state.
                for key, value in self._slideshow_pending.items():
                    self.values.setdefault(key, value)
            self.options["slideshow_order"] = ["slideshow", "shuffleslideshow"]
            self.options["slideshow_category"] = list(
                dict.fromkeys(
                    ["MY-C0002", "MY-C0004"]
                    + ([str(data["category_id"])] if data.get("category_id") else [])
                )
            )
            self.supported.update(SLIDESHOW_KEYS)
            return

    def _split_mat(self, matte):
        if matte == "none":
            return "none", ""
        for (style, color), full_id in self._mat_ids.items():
            if matte == full_id:
                return style, color
        for style in sorted(self.options.get("mat_style", []), key=len, reverse=True):
            if matte.startswith(style + "_"):
                return style, matte[len(style) + 1 :]
        style, _, color = matte.rpartition("_")
        return (style, color) if style else (matte, "")

    async def read_content(self):
        data = await self._optional(
            "get_content_list", ("content_list",), category=None
        )
        if data and "content_list" in data:
            self.artwork = {
                str(item["content_id"]): item
                for item in decoded(data["content_list"], list)
                if isinstance(item, dict) and item.get("content_id")
            }
            self.options["artwork"] = list(self.artwork)
            self.supported.update(("artwork", "random_artwork", *STEP_KEYS))

    async def read_catalog(self):
        await self.read_content()
        data = await self._optional("get_matte_list", ("matte_list",))
        if data:
            self._mat_ids = {}
            legacy = "matte_type_list" not in data and "matte_list" in data
            for key, field in (
                ("mat_color", "matte_color_list"),
                ("mat_style", "matte_list" if legacy else "matte_type_list"),
            ):
                choices = []
                for item in decoded(data.get(field), list):
                    value = (
                        item.get(
                            "matte_type",
                            item.get(
                                "matte_color",
                                item.get("color", item.get("matte_id", item.get("id"))),
                            ),
                        )
                        if isinstance(item, dict)
                        else item
                    )
                    if isinstance(value, str):
                        full_id = (
                            item.get("matte_id") if isinstance(item, dict) else None
                        )
                        if key == "mat_style" and (
                            full_id
                            or (legacy and "_" in value)
                            or any(
                                value.endswith("_" + c)
                                for c in self.options.get("mat_color", [])
                            )
                        ):
                            full_id = full_id or value
                            style, _, color = full_id.rpartition("_")
                            if style and color:
                                self._mat_ids[(style, color)] = full_id
                                self.options.setdefault("mat_color", [])
                                if color not in self.options["mat_color"]:
                                    self.options["mat_color"].append(color)
                                self.supported.add("mat_color")
                                value = style
                            elif full_id:
                                self._mat_ids[(full_id, "")] = full_id
                        elif (
                            key == "mat_style"
                            and legacy
                            and not data.get("matte_color_list")
                        ):
                            self._mat_ids[(value, "")] = value
                        choices.append(value)
                if choices:
                    self.options[key] = list(dict.fromkeys(choices))
                    self.supported.add(key)
        self._catalog_time = time.monotonic()

    async def refresh(self, *, catalog=False, stop=None):
        """Read Art state. Returns False if stopped early for a user command.

        Each step is complete on its own, so stopping between steps leaves
        values consistent; the remaining steps run on the next poll.
        """
        stop = stop or (lambda: False)
        # Required status query distinguishes a working Art API from an open port.
        data = await self.client.request(
            "get_artmode_status", events=("artmode_status",)
        )
        if self.power_state == "off":
            self.set_power("off", self.power_source)
            return True
        mode = data.get("value", data.get("status"))
        if mode not in ("on", "off"):
            raise ArtError("invalid_response")
        self.values["art_mode"] = mode
        self.supported.add("art_mode")
        self.online = True
        if mode != "on":
            self.values = {"art_mode": mode}
            return True
        steps = [self.read_settings]
        if (
            catalog
            or self._catalog_time is None
            or time.monotonic() - self._catalog_time > 300
        ):
            steps.append(self.read_catalog)
        steps += [self.read_current, self.read_slideshow]
        for step in steps:
            if stop():
                return False
            await step()
        return True

    # ------------------------------------------------------------------
    # Writes
    #
    # Every write follows the same contract:
    #   1. validate against TV-reported state (no command sent on failure),
    #   2. send once via _write (a timeout after sending is tolerated),
    #   3. read back, and succeed only if the observed value matches.
    # A writer returns (observed_key, expected_value) for _confirm, or None
    # when the request was satisfied without sending anything.
    # ------------------------------------------------------------------

    def _writer_for(self, key):
        if key in SETTINGS:
            return self._write_setting
        if key in ("artwork", "random_artwork", *STEP_KEYS):
            return self._write_artwork
        if key in SLIDESHOW_KEYS:
            return self._write_slideshow
        if key in MAT_KEYS:
            return self._write_mat
        return None

    async def set_value(self, key, value):
        self.last_operation = {
            "setting": key,
            "requested": value,
            "phase": "preparing",
            "sent": False,
        }
        if not self.online or self.values.get("art_mode") != "on":
            raise ArtError("art_inactive")
        if key not in self.supported:
            raise ArtError("unsupported")
        writer = self._writer_for(key)
        if writer is None:
            raise ArtError("unsupported")
        result = await writer(key, value)
        if result is not None:
            self._confirm(*result)

    async def _write(self, command, events, **params):
        self.last_operation.update(command=command, phase="sending", sent=False)
        try:
            await self.client.request(command, events=events, **params)
        except ArtError as err:
            self.last_operation.update(sent=err.sent, error=err.code)
            # Some firmware omits setter acknowledgements. Only subsequent
            # observed readback can turn this into success.
            if not (err.code == "timeout" and err.sent):
                raise
        self.last_operation.update(phase="readback", sent=True)

    async def _readback(self, key, expected, read):
        """Poll a slow-to-apply value without ever resending the mutation."""
        for attempt in range(READBACK_ATTEMPTS):
            if attempt:
                await asyncio.sleep(READBACK_DELAY)
            await read()
            if self.values.get(key) == expected:
                return

    def _confirm(self, key, expected):
        actual = self.values.get(key)
        self.last_operation.update(observed=actual)
        if not _same(actual, expected):
            raise ArtError("unconfirmed", sent=True)
        self.last_operation.update(phase="confirmed")

    # -- brightness, color temperature, motion, auto-brightness ---------

    def _validate_setting(self, key, value):
        if SETTINGS[key]:
            low, high = self.ranges[key]
            value = float(value)
            if not low <= value <= high or not value.is_integer():
                raise ArtError("invalid_value")
            return str(int(value))
        if key == "brightness_sensor_setting":
            if value not in ("on", "off"):
                raise ArtError("invalid_value")
        elif value not in self.options[key]:
            raise ArtError("invalid_value")
        return value

    async def _write_setting(self, key, value):
        value = self._validate_setting(key, value)
        await self._write("set_" + key, (key,), value=value)
        await self.read_settings()
        return key, value

    # -- artwork selection ----------------------------------------------

    def _step_order(self):
        """Slideshow order when the current piece is in it, else the catalog."""
        current = self.values.get("artwork")
        if current in self.slideshow_items:
            return [cid for cid in self.slideshow_items if cid in self.artwork] or list(
                self.slideshow_items
            )
        return list(self.artwork)

    def _pick_artwork(self, key, value):
        if key in STEP_KEYS:
            order = self._step_order()
            if not order:
                raise ArtError("unsupported")
            current = self.values.get("artwork")
            if current not in order:
                return order[0]
            return order[(order.index(current) + STEP_KEYS[key]) % len(order)]
        if key == "random_artwork":
            current = self.values.get("artwork")
            choices = [cid for cid in self.artwork if cid != current]
            choices = choices or list(self.artwork)
            if not choices:
                raise ArtError("unsupported")
            return random.choice(choices)
        if value not in self.artwork:
            raise ArtError("invalid_value")
        return value

    async def _write_artwork(self, key, value):
        if key in STEP_KEYS:
            # The slideshow may have moved on since the last poll.
            await self.read_current()
        value = self._pick_artwork(key, value)
        await self._write(
            "select_image",
            ("artwork_changed", "current_artwork"),
            content_id=value,
            show=True,
        )
        await self.read_current()
        return "artwork", value

    # -- slideshow -------------------------------------------------------

    async def _write_slideshow(self, key, value):
        # Do not overwrite a change made using the physical remote since
        # the last poll when merging interval/order/category fields.
        await self.read_slideshow()
        if value not in self.options[key]:
            raise ArtError("invalid_value")
        if "slideshow_interval" not in self.values:
            raise ArtError("state_unavailable")
        if self.values["slideshow_interval"] == "off" and key != "slideshow_interval":
            self._slideshow_pending[key] = value
            self.values[key] = value
            self.last_operation.update(phase="saved_for_next_start")
            return None
        settings = {
            "slideshow_interval": self.values["slideshow_interval"],
            "slideshow_order": self.values.get(
                "slideshow_order",
                self._slideshow_pending.get("slideshow_order", "slideshow"),
            ),
            "slideshow_category": self.values.get(
                "slideshow_category",
                self._slideshow_pending.get("slideshow_category", "MY-C0002"),
            ),
            key: value,
        }
        api = self._slideshow_api
        await self._write(
            "set_" + api + "_status",
            (api + "_status",),
            value=settings["slideshow_interval"],
            type=settings["slideshow_order"],
            category_id=settings["slideshow_category"],
        )
        await self._readback(key, value, self.read_slideshow)
        return key, value

    # -- mat of the current artwork -------------------------------------

    def _resolve_matte(self, key, value):
        """Build the matte_id for a style/color change; raise before sending."""
        style = value if key == "mat_style" else self.values.get("mat_style")
        color = value if key == "mat_color" else self.values.get("mat_color")
        if key == "mat_style" and style != "none" and not color:
            colors = self.options.get("mat_color", [])
            color = colors[0] if colors else None
        exact_style = any(s == style for s, _ in self._mat_ids)
        if not style or (style != "none" and not color and not exact_style):
            raise ArtError("state_unavailable")
        if key == "mat_color" and style == "none":
            raise ArtError("invalid_value")
        if style == "none":
            return "none"
        if not exact_style:
            return f"{style}_{color}"
        matte = self._mat_ids.get((style, color or ""))
        if not matte and key == "mat_style":
            # Legacy catalogs enumerate exact supported combinations.
            matte = next((v for (s, _), v in self._mat_ids.items() if s == style), None)
        if not matte:
            raise ArtError("invalid_value")
        return matte

    async def _reread_mat(self):
        # Older TVs report mat IDs only in the catalog, so refresh it too.
        for item in self.artwork.values():
            item.pop("matte_id", None)
        await self.read_content()
        await self.read_current()

    async def _write_mat(self, key, value):
        await self.read_current()
        if value not in self.options[key] or not self.values.get("artwork"):
            raise ArtError("invalid_value")
        matte = self._resolve_matte(key, value)
        await self._write(
            "change_matte",
            ("matte_changed",),
            content_id=self.values["artwork"],
            matte_id=matte,
        )
        await self._readback(key, value, self._reread_mat)
        return key, value


def _same(actual, expected):
    """Compare readback to request, tolerating '5' vs 5.0 from the TV."""
    if str(actual) == str(expected):
        return True
    try:
        return float(actual) == float(expected)
    except (TypeError, ValueError):
        return False
