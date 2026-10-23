"""Art compatibility information without credentials or network identifiers."""


async def async_get_config_entry_diagnostics(hass, entry):
    controller = entry.runtime_data
    controls = controller.controls
    return {
        "media_player": {
            "last_mode": controller.last_mode,
            "last_selected_input": controller.last_input,
            "volume_readable": controller.volume is not None,
            "input_names_configured": sorted(
                key for key in entry.options if key != "channel_names"
            ),
            "named_channels": len(entry.options.get("channel_names", {})),
            "channel": getattr(controller, "channel", None),
            "channel_lineup": {
                "effective": getattr(controller, "channel_list", {}).get("effective"),
                "channels": len(
                    getattr(controller, "channel_list", {}).get("channels", {})
                ),
            },
            "channel_download": {
                "enabled": getattr(controller, "channel_download", False),
                "region": getattr(controller, "channel_region", None),
                "downloaded_region": (
                    getattr(controller, "channel_lineup", None) or {}
                ).get("region"),
                "downloaded": (getattr(controller, "channel_lineup", None) or {}).get(
                    "fetched"
                ),
                "channels": len(
                    (getattr(controller, "channel_lineup", None) or {}).get(
                        "channels", {}
                    )
                ),
                "last_error": getattr(controller, "channel_list_error", None),
            },
            "explicit_off_reported": controller.off_reported,
            "last_command": dict(getattr(controller, "last_command", {})),
            "remote_connection_open": getattr(
                getattr(controller, "remote", None), "connected", None
            ),
        },
        "ip_control": {
            "paired": controller.ipc is not None,
            "port": getattr(controller.ipc, "port", None),
            "rejected_token": controller.ipc_auth_failed,
            "last_error": controller.ipc_error,
            "reported_input": controller.input_reported,
            "input_confirmed": controller.input_confirmed,
        },
        "power_state": controls.power_state,
        "power_source": controls.power_source,
        "art_online": controls.online,
        "supported_controls": sorted(controls.supported),
        "slideshow_api": controls.slideshow_api,
        "slideshow_values": {
            k: v for k, v in controls.values.items() if k.startswith("slideshow_")
        },
        "slideshow_next_start": controls.slideshow_next_start,
        "slideshow_resume_interval": getattr(controls, "resume_interval", None),
        "slideshow_items": len(getattr(controls, "slideshow_items", ())),
        "artwork_picture": dict(getattr(controller, "thumbnail_status", {})),
        "mat_values": {
            k: v for k, v in controls.values.items() if k.startswith("mat_")
        },
        "mat_options": {
            k: list(v) for k, v in controls.options.items() if k.startswith("mat_")
        },
        "mat_ids": controls.mat_ids,
        "last_art_operation": dict(controls.last_operation),
    }
