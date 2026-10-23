"""Constants for Samsung Frame Control."""

DOMAIN = "samsung_frame_control"
DEFAULT_NAME = "Frame TV"
CLIENT_NAME = "HA Frame Control"
CONF_BROADCAST = "broadcast_address"
DEFAULT_BROADCAST = "255.255.255.255"
PORT = 8002

# Media player sources. Samsung's own input names are only available from the
# SmartThings cloud, so labels are user-configurable integration options.
SOURCE_ART = "Art Mode"
# Input id -> remote key. "tv" is the TV source: Samsung TV Plus or the tuner.
INPUTS = {"tv": "KEY_TV", **{f"hdmi{n}": f"KEY_HDMI{n}" for n in range(1, 5)}}
DEFAULT_INPUT_NAMES = {
    "tv": "Samsung TV",
    **{f"hdmi{n}": f"HDMI {n}" for n in range(1, 5)},
}
# IP Control getTVStates.inputSource -> input id (measured on a QN55LS03T).
REPORTED_INPUTS = {"TV": "tv", **{f"HDMI{n}": f"hdmi{n}" for n in range(1, 5)}}

CONF_IPC_TOKEN = "ip_control_token"
# Options: {"2110": "Pluto News"} names for TV-source channel numbers.
CONF_CHANNEL_NAMES = "channel_names"
CONF_IPC_PORT = "ip_control_port"

# Samsung TV Plus channel list from Samsung's public web API (opt-in).
CONF_CHANNEL_DOWNLOAD = "channel_list_download"
CONF_CHANNEL_REGION = "channel_list_region"
DEFAULT_CHANNEL_REGION = "US"
# Countries whose line-up Samsung's API returned (checked October 2026;
# AR, CL, CO, JP, PE and PL returned none).
CHANNEL_REGIONS = {
    "AU": "Australia",
    "AT": "Austria",
    "BE": "Belgium",
    "BR": "Brazil",
    "CA": "Canada",
    "DK": "Denmark",
    "FI": "Finland",
    "FR": "France",
    "DE": "Germany",
    "IN": "India",
    "IE": "Ireland",
    "IT": "Italy",
    "MX": "Mexico",
    "NL": "Netherlands",
    "NZ": "New Zealand",
    "NO": "Norway",
    "PH": "Philippines",
    "PT": "Portugal",
    "SG": "Singapore",
    "KR": "South Korea",
    "ES": "Spain",
    "SE": "Sweden",
    "CH": "Switzerland",
    "GB": "United Kingdom",
    "US": "United States",
}

# What a running command is doing right now (activity sensor / attribute).
ACTIVITIES = (
    "idle",
    "waking",
    "starting_art_app",
    "switching_to_art",
    "switching_to_tv",
    "confirming_mode",
    "retrying",
    "settling",
    "switching_input",
    "checking_input",
    "powering_off",
    "sending_key",
    "changing_channel",
    "changing_setting",
)
