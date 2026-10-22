"""UI setup and reconfiguration; pairing does not change the displayed mode."""

import ipaddress
import re
from urllib.parse import urlparse

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_TOKEN
from homeassistant.core import callback
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig

from .const import (
    CONF_BROADCAST,
    CONF_CHANNEL_NAMES,
    CONF_IPC_PORT,
    CONF_IPC_TOKEN,
    DEFAULT_BROADCAST,
    DEFAULT_INPUT_NAMES,
    DEFAULT_NAME,
    DOMAIN,
    SOURCE_ART,
)
from .frame import FrameError, normalize_mac, port_open, run_worker
from .ipcontrol import PORTS, IpcError, IpControl


class FrameConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return FrameOptionsFlow()

    async def async_step_ssdp(self, discovery_info):
        """Identify a Frame without pairing, waking it, or sending commands."""
        host = urlparse(discovery_info.ssdp_location or "").hostname
        if not host:
            return self.async_abort(reason="not_supported")
        # Also catches existing entries whose user-supplied Ethernet MAC differs
        # from Samsung's wifiMac field. Do not change that WOL MAC automatically.
        for entry in self._async_current_entries():
            if entry.data.get(CONF_HOST) == host:
                return self.async_abort(reason="already_configured")
        info = {}
        try:
            await run_worker(
                host,
                None,
                "discover",
                lambda _: None,
                on_info=info.update,
                operation_timeout=12,
            )
        except (FrameError, OSError):
            return self.async_abort(reason="cannot_connect")
        model = str(info.get("modelName") or "").upper()
        if str(info.get("FrameTVSupport")).lower() != "true" and "LS03" not in model:
            return self.async_abort(reason="not_supported")
        try:
            mac = normalize_mac(str(info.get("wifiMac") or ""))
            if mac in ("00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"):
                mac = ""
        except ValueError:
            mac = ""
        udn = discovery_info.upnp.get("UDN")
        if not mac and not udn:
            return self.async_abort(reason="not_supported")
        await self.async_set_unique_id(mac or str(udn))
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        self._discovered = {
            CONF_HOST: host,
            CONF_MAC: mac,
            CONF_NAME: str(info.get("name") or DEFAULT_NAME).removeprefix("[TV] "),
        }
        self.context["title_placeholders"] = {"name": self._discovered[CONF_NAME]}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(self, user_input=None):
        return await self._configure(user_input, discovered=True)

    async def async_step_user(self, user_input=None):
        return await self._configure(user_input)

    async def async_step_reconfigure(self, user_input=None):
        return await self._configure(user_input, self._get_reconfigure_entry())

    async def _configure(self, user_input, entry=None, discovered=False):
        errors = {}
        defaults = dict(entry.data) if entry else dict(getattr(self, "_discovered", {}))
        if user_input is not None:
            defaults.update(user_input)
            data = dict(user_input)
            if entry:
                data[CONF_MAC] = entry.data[CONF_MAC]
                data[CONF_NAME] = entry.title
            supplied_mac = data.get(CONF_MAC, "").strip()
            if supplied_mac:
                try:
                    data[CONF_MAC] = normalize_mac(supplied_mac)
                except ValueError:
                    errors[CONF_MAC] = "invalid_mac"
            host = data[CONF_HOST].strip()
            if not host or any(c in host for c in "/\\?#@ "):
                errors[CONF_HOST] = "invalid_host"
            data[CONF_HOST] = host
            try:
                data[CONF_BROADCAST] = str(
                    ipaddress.IPv4Address(data[CONF_BROADCAST].strip())
                )
            except ValueError:
                errors[CONF_BROADCAST] = "invalid_broadcast"
            if not errors and not supplied_mac:
                info = {}
                try:
                    await run_worker(
                        host,
                        None,
                        "discover",
                        lambda _: None,
                        on_info=info.update,
                        operation_timeout=12,
                    )
                    mac = normalize_mac(str(info.get("wifiMac") or ""))
                    if mac in ("00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"):
                        raise ValueError("Unusable MAC")
                    data[CONF_MAC] = mac
                except (FrameError, OSError, ValueError):
                    errors[CONF_MAC] = "mac_unavailable"
            if not errors:
                data[CONF_TOKEN] = None
                await self.async_set_unique_id(data[CONF_MAC])
                if not entry:
                    self._abort_if_unique_id_configured()
                try:
                    await run_worker(
                        host,
                        None,
                        "pair",
                        lambda token: data.update({CONF_TOKEN: token}),
                        operation_timeout=65,
                    )
                    if entry:
                        return self.async_update_reload_and_abort(
                            entry, data_updates=data, reason="reconfigure_successful"
                        )
                    return self.async_create_entry(
                        title=data[CONF_NAME].strip() or DEFAULT_NAME, data=data
                    )
                except FrameError as err:
                    errors["base"] = (
                        "invalid_auth"
                        if err.code == "invalid_auth"
                        else "cannot_connect"
                    )
                except OSError:
                    errors["base"] = "cannot_connect"
        schema = {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Required(
                CONF_BROADCAST, default=defaults.get(CONF_BROADCAST, DEFAULT_BROADCAST)
            ): str,
        }
        if not entry:
            schema = {
                vol.Required(
                    CONF_NAME, default=defaults.get(CONF_NAME, DEFAULT_NAME)
                ): str,
                vol.Optional(CONF_MAC, default=defaults.get(CONF_MAC, "")): str,
                **schema,
            }
        return self.async_show_form(
            step_id="reconfigure"
            if entry
            else "discovery_confirm"
            if discovered
            else "user",
            data_schema=vol.Schema(schema),
            errors=errors,
        )


class FrameOptionsFlow(config_entries.OptionsFlowWithReload):
    """Input names, and optional IP Control pairing for the observed input."""

    async def async_step_init(self, user_input=None):
        options = ["inputs", "channels", "ip_control_pair"]
        if self.config_entry.data.get(CONF_IPC_TOKEN):
            options.append("ip_control_unpair")
        return self.async_show_menu(step_id="init", menu_options=options)

    async def async_step_inputs(self, user_input=None):
        """Name the inputs shown as media player sources.

        The TV's own input labels are only published through SmartThings
        (cloud), so they are entered here. A blank name hides that input.
        """
        errors = {}
        current = {**DEFAULT_INPUT_NAMES, **self.config_entry.options}
        if user_input is not None:
            names = {
                key: str(user_input.get(key, "")).strip() for key in DEFAULT_INPUT_NAMES
            }
            visible = [name for name in names.values() if name]
            if len(visible) != len(set(visible)):
                errors["base"] = "duplicate_source"
            elif SOURCE_ART in visible:
                errors["base"] = "reserved_source"
            else:
                # Keep the other options (channel names) when saving.
                return self.async_create_entry(
                    data={**self.config_entry.options, **names}
                )
            current.update(names)
        schema = vol.Schema(
            {
                vol.Optional(key, default=current.get(key, "")): str
                for key in DEFAULT_INPUT_NAMES
            }
        )
        return self.async_show_form(step_id="inputs", data_schema=schema, errors=errors)

    async def async_step_channels(self, user_input=None):
        """Names for channel numbers reported on the Samsung TV source."""
        errors, placeholders = {}, {"line": ""}
        saved = self.config_entry.options.get(CONF_CHANNEL_NAMES, {})
        text = "\n".join(f"{number} = {name}" for number, name in saved.items())
        if user_input is not None:
            text = user_input.get(CONF_CHANNEL_NAMES, "")
            names, bad = parse_channel_names(text)
            if bad is None:
                return self.async_create_entry(
                    data={**self.config_entry.options, CONF_CHANNEL_NAMES: names}
                )
            errors["base"], placeholders["line"] = "invalid_channel_line", bad
        schema = vol.Schema(
            {
                vol.Optional(CONF_CHANNEL_NAMES, default=text): TextSelector(
                    TextSelectorConfig(multiline=True)
                )
            }
        )
        return self.async_show_form(
            step_id="channels",
            data_schema=schema,
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_ip_control_pair(self, user_input=None):
        """One-time pairing; the TV shows an authorization prompt."""
        errors = {}
        if user_input is not None:
            host = self.config_entry.data[CONF_HOST]
            port = None
            for candidate in PORTS:
                if await port_open(host, candidate):
                    port = candidate
                    break
            if port is None:
                errors["base"] = "ip_control_closed"
            else:
                client = IpControl(host, port)
                try:
                    token = await self.hass.async_add_executor_job(client.pair)
                except IpcError as err:
                    errors["base"] = (
                        "ip_control_timeout"
                        if err.code == "timeout"
                        else "ip_control_failed"
                    )
                else:
                    self.hass.config_entries.async_update_entry(
                        self.config_entry,
                        data={
                            **self.config_entry.data,
                            CONF_IPC_TOKEN: token,
                            CONF_IPC_PORT: port,
                        },
                    )
                    # Options are unchanged, so reload explicitly to use the token.
                    self.hass.config_entries.async_schedule_reload(
                        self.config_entry.entry_id
                    )
                    return self.async_create_entry(data=dict(self.config_entry.options))
        return self.async_show_form(
            step_id="ip_control_pair", data_schema=vol.Schema({}), errors=errors
        )

    async def async_step_ip_control_unpair(self, user_input=None):
        if user_input is not None:
            data = dict(self.config_entry.data)
            data.pop(CONF_IPC_TOKEN, None)
            data.pop(CONF_IPC_PORT, None)
            self.hass.config_entries.async_update_entry(self.config_entry, data=data)
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
            return self.async_create_entry(data=dict(self.config_entry.options))
        return self.async_show_form(
            step_id="ip_control_unpair", data_schema=vol.Schema({})
        )


CHANNEL_LINE = re.compile(r"^\s*([0-9][0-9.\-]*)\s*[=:]\s*(.+?)\s*$")


def parse_channel_names(text):
    """Parse "2110 = Pluto News" lines; returns (names, first bad line)."""
    names = {}
    for line in str(text).splitlines():
        if not line.strip():
            continue
        match = CHANNEL_LINE.match(line)
        if not match:
            return {}, line.strip()
        names[match.group(1)] = match.group(2)
    return names, None
