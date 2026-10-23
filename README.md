# Samsung Frame Control

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![Validate](https://github.com/FutureGUIs/samsung-frame-control/actions/workflows/validate.yml/badge.svg)](https://github.com/FutureGUIs/samsung-frame-control/actions/workflows/validate.yml)

A Home Assistant integration for the **Samsung Frame TV** that controls the TV
entirely over your local network: no SmartThings account, no cloud service, no
other integration and no YAML. (Optionally, it downloads the Samsung TV Plus
channel list for your country from Samsung's public website.)

- **Media player** with power, sources (Art Mode, Samsung TV, HDMI 1–4) and
  volume.
- **Art Mode:** the artwork on screen as the player's picture, Next/Previous
  artwork, and Play/Pause for the slideshow.
- **Samsung TV:** channel number, name and logo, a channel selector,
  browsing channels by genre, and channel up/down. The Samsung TV Plus channel
  list for your country can be downloaded from Samsung (optional; a U.S. list
  is built in).
- **Art settings:** artwork, brightness, color temperature, motion sensor,
  slideshow and mat controls, discovered from what your TV supports.
- **Live progress** of slow commands (waking, starting the Art app, switching
  input) in an Activity sensor.

Developed and tested on a 2020 Frame (QN55LS03T). Other Frame models and
firmware generations use the same local interfaces, but some settings differ;
controls the TV does not report are simply not created.

## Requirements

- Home Assistant 2026.3 or newer.
- A Samsung Frame on the same network as Home Assistant, ideally with a fixed IP
  address (a DHCP reservation in your router).
- To turn the TV on from Home Assistant, Wake-on-LAN enabled on the TV
  (often **Settings → General → Network → Expert Settings → Power On with
  Mobile**; the path differs by model).

## Installation

### HACS

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=FutureGUIs&repository=samsung-frame-control&category=integration)

Or manually in HACS:

1. **HACS → ⋮ → Custom repositories**.
2. Repository `https://github.com/FutureGUIs/samsung-frame-control`, type
   **Integration**, then **Add**.
3. Find **Samsung Frame Control** in HACS, **Download**, and restart Home
   Assistant.

### Manual

Copy `custom_components/samsung_frame_control` into your Home Assistant
`config/custom_components/` folder and restart Home Assistant.

## Setup

1. Turn the TV on with its remote.
2. Home Assistant usually discovers the TV. Otherwise use **Settings → Devices &
   services → Add integration → Samsung Frame Control**.
3. Enter the TV's IP address. Leave the MAC address blank to read it from the
   TV. If Wake-on-LAN later fails on a wired TV, enter its Ethernet MAC instead
   (the TV may report its Wi-Fi MAC).
4. Keep the broadcast address `255.255.255.255` unless your network needs a
   subnet broadcast for Wake-on-LAN.
5. Choose whether to download the Samsung TV Plus channel list (names, genres
   and logos) from Samsung, and for which country. You can change this later.
6. Accept **HA Frame Control** on the TV. More than one prompt can appear.

### Optional: IP Control (recommended)

Samsung's local IP Control interface lets the integration see what the TV is
actually doing, including changes made with the TV's own remote. It stays on
your network.

1. On the TV turn on **Settings → All Settings → Connections → Network → Expert
   Settings → IP Remote** (the path differs by firmware).
2. Turn the TV on in normal viewing, **not Art Mode**.
3. In Home Assistant open the integration, **Configure → Pair IP Control**,
   submit, and accept the prompt on the TV.

With IP Control:

- The current input is read from the TV every poll, including remote-control
  changes (`source_confirmed: true`).
- Art Mode changes made with the remote show within one poll (about 15 s).
- Off is reported explicitly, and turning on, switching mode and turning off
  are faster. On some models, HDMI selection only works with IP Control.
- On the Samsung TV source the channel number is shown.

If the TV later rejects the pairing, a repair notice asks you to pair again.
**Remove IP Control pairing** is in the same menu.

### Options

Under **Configure**:

- **Input names:** name or hide Samsung TV and HDMI 1–4. Samsung publishes the
  names you set on the TV only through its cloud, so they cannot be read
  locally. Clear a name to hide that input.
- **Channel list:** download the Samsung TV Plus channel list from Samsung
  (on/off) and the country (25 countries).
- **Channel names:** one per line, for example `2110 = Pluto News`. They take
  priority over the Samsung TV Plus names and can add channels the list does
  not have (antenna channels, for example).
- **Pair IP Control / Remove IP Control pairing.**

## The media player

Normally `media_player.frame_tv`.

- **Turn on** wakes the TV into the mode it was last in, including Art Mode.
- **Turn off** powers the TV off directly (IP Control), or with a power-key
  hold without it.
- **Source:** Art Mode, Samsung TV (Samsung TV Plus or the tuner) and HDMI 1–4.
  Selecting a source wakes the TV first when needed.
- **Volume** uses the TV's local UPnP renderer. It is hidden in Art Mode.

What the buttons do depends on the source:

| Source | Picture | Next / Previous | Play / Pause |
| --- | --- | --- | --- |
| Art Mode | the artwork on screen | next / previous artwork in the slideshow's order | Pause turns the slideshow off; Play turns it back on with its last interval |
| Samsung TV | — | channel up / down | — |
| HDMI | — | — | — |

**State:** `off`, `on`, or `unknown` when the TV cannot be observed. In Art
Mode the state is `playing` while the slideshow runs and `paused` while it is
off; on Samsung TV (with IP Control) it is `playing`, which is what makes
dashboards show the channel buttons. A TV last seen off stays `off` while it does not answer. The media player
never becomes unavailable, so Turn on always works.

**Attributes:** `activity` (what a running command is doing), `power_source`
(where the power state came from: `local_rest`, `ip_control`, `standby`,
`last_known`, `unreachable`, …) and `source_confirmed` (the source was read from
the TV in the latest poll).

## Samsung TV channels

With IP Control paired, on the Samsung TV source:

- The media player shows the channel as *2110 · America's Test Kitchen*, with
  its genre in the `channel_genre` attribute and, with the downloaded list,
  the channel's logo as its picture. A channel that is not in the list shows
  its number.
- **Where names come from:** your own channel names first, then the list
  downloaded from Samsung for your country (if enabled), then the U.S. list
  built into the integration (`channels_us.json`, used for the United States
  only). **Update channel list** (on the device, when downloading is enabled)
  downloads it again; the **Channel list updated** sensor shows when it was
  last downloaded.
- **Channel** select (`select.frame_tv_channel`): every channel by number and
  name, following the TV. Choosing one types its number on the TV (about 3
  seconds) and confirms the change.
- **Browse media** on the media player lists the channels by genre. Playing a
  channel from there, or with `media_player.play_media`, also works from HDMI,
  Art Mode or off: the TV switches to Samsung TV first.
- Previous / Next are channel down / up.
- In automations:

  ```yaml
  action: media_player.play_media
  target:
    entity_id: media_player.frame_tv
  data:
    media_content_type: channel
    media_content_id: "2110"
  ```

Samsung changes the line-up from time to time: press **Update channel list**,
or, without downloading, wait for the built-in list to be updated in a release
(`tools/stvp_channels.py` rebuilds it from Samsung's published PDF).

## Other entities

| Entity | Behavior |
| --- | --- |
| Artwork select | Chooses a piece from the TV's catalog |
| Random artwork button | Picks another piece from the catalog |
| Current artwork sensor | Content ID of the piece on screen, with mat and category |
| Channel select | Samsung TV channel by number and name (with IP Control) |
| Update channel list button | Downloads the Samsung TV Plus list again (when downloading is enabled) |
| Channel list updated sensor | When the Samsung TV Plus list in use was downloaded, with country, channel count and last error (diagnostic; when downloading is enabled) |
| Activity sensor | What a running command is doing: Waking TV, Starting Art app, Switching input, Changing channel, … then Idle |
| Art brightness, color temperature, motion sensitivity numbers | Ranges as reported by the TV |
| Art automatic brightness switch | The TV's Art brightness sensor |
| Sleep after no motion, slideshow interval / order / category, mat style / color selects | Values reported by the TV |
| Refresh Art controls button | Re-reads Art settings and the catalog |

Art settings are under **Configuration** on the device page. They are available
only in Art Mode, never wake the TV or change its mode, and every change is
read back from the TV; a change the TV does not apply is reported as an error.

## Examples

A card with live progress under the TV controls:

```yaml
type: vertical-stack
cards:
  - type: media-control
    entity: media_player.frame_tv
  - type: tile
    entity: sensor.frame_tv_activity
```

Switch to Art Mode:

```yaml
action: media_player.select_source
target:
  entity_id: media_player.frame_tv
data:
  source: Art Mode
```

Set Art brightness:

```yaml
action: number.set_value
target:
  entity_id: number.frame_tv_art_brightness
data:
  value: 5
```

## Troubleshooting

- **Missing Art controls:** enter Art Mode and press **Refresh Art controls**.
  Settings differ by model and firmware; first discovery can take up to about
  40 seconds.
- **The TV does not turn on:** check Wake-on-LAN on the TV, the MAC address
  (Ethernet vs Wi-Fi) and the broadcast address. With IP Control paired, the
  TV is woken with IP Control first.
- **Wrong input shown:** without IP Control the source is the one Home
  Assistant last selected. Pair IP Control to see remote-control changes.
- **IP Control pairing times out:** the TV must be in normal viewing (not Art
  Mode) with IP Remote enabled.
- **Authorization:** reconfigure with the TV on and accept the prompt. Remove a
  previously denied *HA Frame Control* entry from the TV's device list if
  needed.
- **The TV leaves Art Mode by itself:** Home Assistant's log shows a warning
  each time it happens without a command from Home Assistant, and diagnostics
  (`art_mode_exits`) list what the integration sent the TV in the two minutes
  before. Compare the times with other devices' history; an HDMI device
  waking the TV through HDMI-CEC (Anynet+) is a common cause.
- **Report a problem:** download diagnostics from the integration's menu right
  after the problem and attach them to an
  [issue](https://github.com/FutureGUIs/samsung-frame-control/issues).
  Diagnostics show timings, the last command's steps and which TV requests
  worked; they exclude the host, MAC address, pairing tokens and artwork titles.

### Probe tool

`tools/frame_probe.py` is a standalone script (Python 3.10+, no packages) for
checking what your TV answers before or alongside the integration:

```bash
python tools/frame_probe.py check 192.168.1.50      # ports, power, IP Control
python tools/frame_probe.py pair 192.168.1.50       # one-time IP Control pairing
python tools/frame_probe.py watch 192.168.1.50      # print changes as you use the remote
python tools/frame_probe.py thumbnail 192.168.1.50  # save the current artwork's thumbnail
python tools/frame_probe.py slideshow 192.168.1.50  # which slideshow request the TV answers
python tools/frame_probe.py try-channel 192.168.1.50 2111  # how the TV accepts a channel number
python tools/frame_probe.py time-channel 192.168.1.50 2111 --gap 0.5 --enter  # time typed digits
python tools/frame_probe.py hold-channel 192.168.1.50 1114,2110 --idle 60  # one connection kept open
python tools/frame_probe.py channels US  # Samsung TV Plus list from Samsung (no TV needed)
```

`check`, `pair`, `watch`, `thumbnail` and `slideshow` never change a TV
setting; `try-channel` changes the channel. Reports redact tokens, MAC addresses and device IDs; the saved token
file should be treated like a password.

## How it works

Everything runs on your LAN: the TV's Art and remote WebSocket channels (port
8002), its REST power status, UPnP RenderingControl (port 9197) for volume,
Samsung IP Control (port 1516, optional) and Wake-on-LAN. The only connection
outside your network is the optional Samsung TV Plus channel list download
(and its logos) from Samsung's public website. The remote-key
connection is kept open between commands (closed when the TV turns off or
after 15 minutes unused). Power is checked about
every 15 seconds; Art settings about every 60 seconds; the artwork catalog
about every five minutes. Polling only reads; only Turn on and source selection
can wake the TV.

## Development

Tests run on a real Home Assistant core via
`pytest-homeassistant-custom-component`, with the TV simulated at the network
boundary:

```bash
python3.14 -m venv venv && . venv/bin/activate
pip install -r tests/requirements.txt
pytest
```

See [CHANGELOG.md](CHANGELOG.md) for the release history and
[VALIDATION.md](VALIDATION.md) for what was tested in each release.

## Credits and license

The Art channel client (`async_art.py`) adapts the asynchronous approach of
Nick Waterton's fork of
[samsung-tv-ws-api](https://github.com/NickWaterton/samsung-tv-ws-api); the
upstream [samsungtvws](https://github.com/xchwarze/samsung-tv-ws-api) package is
used for discovery, pairing and the power-key hold. See
[THIRD_PARTY_NOTICES.md](custom_components/samsung_frame_control/THIRD_PARTY_NOTICES.md).

Licensed under the [GNU Lesser General Public License v3.0](LICENSE) (the GPL
text it builds on is in [COPYING](COPYING)).

Not affiliated with or endorsed by Samsung. Samsung and The Frame are trademarks
of Samsung Electronics.
