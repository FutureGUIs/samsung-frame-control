# Changelog

Newest first. Measurements refer to the author's QN55LS03T (2020 Frame) unless
noted. Technical validation for each release is in [VALIDATION.md](VALIDATION.md).

## 2.8.0 — Samsung TV Plus channel list by country, with logos

- **Download from Samsung (optional):** when adding a TV, choose whether to
  download the Samsung TV Plus channel list from Samsung's public website and
  for which country (25 countries; the default is Home Assistant's own
  country, else the United States). It is the same data Samsung's Samsung TV
  Plus page shows: number, name, genre and logo for every channel (591 for the
  U.S., including channels the PDF leaves out). Change it later under
  **Configure → Channel list**. TVs added before 2.8.0 start with the download
  off.
- **When it downloads:** at first setup, when the country changes, and with
  the new **Update channel list** button (shows the country, channel count,
  download time and last error). It runs in the background; if it fails, the
  previous or built-in list stays in use. The list is saved in Home
  Assistant's storage and removed with the device.
- **Channel logo** as the media player's picture on the Samsung TV source,
  and logos plus Samsung's genre order in **Browse media**.
- **Which names apply:** your own channel names, then the downloaded list for
  the chosen country, then the built-in U.S. list (only when the country is
  the United States).
- Measured from the user's network with `tools/frame_probe.py channels US`:
  Samsung's API answers without a browser (plain Python, Home Assistant's
  client and a browser agent alike, 0.7–0.9 s per request); 591 channels in
  20 genres, all numbered. The integration requests the genres five at a time.

## 2.7.2 — Remote connection kept open

The remote-key connection (channel numbers, channel up/down, input and
volume keys) is now kept open between commands instead of being opened for
each one. Measured on a QN55LS03T with `tools/frame_probe.py hold-channel`:
the TV kept an idle connection for 5 minutes, answering every 30-second
keep-alive, and four channel changes over it took 2.8–3.3 s each, without
the 1.5 s connect. The one-second pause after each key, which only kept a
per-command connection from closing too soon, is gone too.

The connection is closed when the TV turns off, on unload, and after 15
minutes unused. If the kept connection has died, a command that had not sent
anything yet reconnects once; a retyped channel number always uses a new
connection. Diagnostics show `remote_connection_open`.

## 2.7.1 — Faster channel changes

Choosing a channel took about 8–9 seconds: digits were typed one second
apart and the TV then waited about 3 seconds for more digits. Measured on a
QN55LS03T with `tools/frame_probe.py time-channel`: digits 0.3 s apart or
less lost a digit in 4 of 5 tries (1114 became 114), while digits 0.5 s
apart followed by Enter tuned 0.35 s after Enter, 3.7 s in total. Now digits
are typed 0.6 s apart, followed by Enter, and the new channel is checked
every quarter second. If a finished entry still lands on another channel, the
number is typed once more (not after the TV left the Samsung TV source).

## 2.7.0 — Samsung TV channel names and channel selector

- **Channel names built in:** the Samsung TV Plus U.S. line-up (590 channels,
  effective September 2026) ships with the integration as
  `channels_us.json`, so the media player shows *2110 · America's Test
  Kitchen* without setup, with the genre as `channel_genre`. Names are read
  from that file only; nothing goes online. Your own **Channel names** still
  take priority. `tools/stvp_channels.py` regenerates the file from Samsung's
  published PDF (`pip install pypdf`, then run it).
- **Channel selector** (`select.frame_tv_channel`, with IP Control paired):
  lists every channel by number and name, follows the current channel, and is
  available only on the Samsung TV source. Choosing one types its number on
  the TV's remote channel (one connection, about a second per digit) and
  confirms the new channel through IP Control. Measured on a QN55LS03T: IP
  Control's own channel command is refused (-32602), typed digits tune
  without Enter.
- **Media player:** `media_player.play_media` with `media_content_type:
  channel` and the number tunes a channel, and **Browse media** lists the
  channels by genre (Samsung TV source only).
- A number that is not in your TV's line-up is reported as an error after it
  was typed; a partly typed number is never typed again.
- New activity: *Changing channel*.

## 2.6.2 — Channel buttons on Samsung TV

On the Samsung TV source (input confirmed by IP Control) the media player
state is now `playing` instead of `on`. Home Assistant's dashboards only show
Previous/Next while a player is playing or paused, so the channel down/up
buttons were supported but hidden. HDMI sources stay `on`; Play/Pause is still
not offered on Samsung TV.

## 2.6.1 — No volume in Art Mode; first GitHub / HACS release

First release published on GitHub as a HACS custom repository: `hacs.json`,
LGPL-3.0 license, manifest documentation, issue tracker and code owner, and
GitHub checks (HACS, hassfest, tests).

In Art Mode the media player no longer offers volume set, mute or volume
step, so the card shows the artwork with Next/Previous and Play/Pause but no
volume slider or mute button. Volume calls made in Art Mode are refused by
Home Assistant. On Samsung TV and HDMI the volume controls are unchanged.

## 2.6.0 — Artwork picture and media buttons

In Art Mode the media player's picture is the artwork on screen, read locally
from the TV as a thumbnail (320x180 JPEG on a 2020 Frame). It changes with the
artwork, is cached per piece (one request to the TV per artwork, not per page
view), and is shown only in Art Mode. A piece the TV will not give a thumbnail
for shows no picture; diagnostics `artwork_picture` shows why.

The media player's buttons depend on the source:

| Source | Next / Previous | Play / Pause |
| --- | --- | --- |
| Art Mode | next / previous artwork, in the slideshow's order (else the Artwork list), starting from what shows now | Pause turns the slideshow off; Play turns it on again with the last interval (15 min if never seen running) |
| Samsung TV | channel up / down (remote keys), new channel read back at once with IP Control | none |
| HDMI | none | none |

In Art Mode the media player state is `playing` while the slideshow runs and
`paused` while it is off (it was `on` before 2.6.0); on other sources it stays
`on`. Automations that test for `on` in Art Mode should also accept `playing`
and `paused`.

Two read-only probe commands helped check this on the TV:
`python tools/frame_probe.py thumbnail <ip>` saves the current artwork's
thumbnail, and `python tools/frame_probe.py slideshow <ip>` shows which
slideshow request the TV answers.

## 2.5.1 — Remote-control Art Mode

With IP Control paired, every 15 s poll now also reads `artModeControl`, so a
change made with the TV's own remote (for example, pressing power on Samsung
TV and the TV dropping into Art Mode) shows on the media player within one
poll instead of after the 60 s Art refresh. Entering Art Mode re-reads the
artwork and Art settings in the same poll; leaving it clears them so stale
values are not shown. Without IP Control the Art channel still decides, as
before.

## 2.5.0 — Samsung TV channel

With IP Control paired, the media player shows the channel number while the
Samsung TV source is on (Samsung TV Plus or the tuner): `media_channel` and
`media_title`, with `media_content_type: channel`. It is read locally from IP
Control's `directChannelControl` (measured on a QN55LS03T on Samsung TV Plus:
`atvDtv: TVPLUS, channelNum: 2110`, following channel changes made with the
remote). It is only asked on the TV source and is not shown in Art Mode.

The TV does not report channel names locally. Add your own under **Configure →
Channel names**, one per line:

```
2110 = Pluto News
2111 = Movies
```

Named channels show as *2110 · Pluto News*; others show their number. Saving
input names or channel names now keeps the other settings (before, saving input
names replaced all options).

## 2.4.0 — Device page layout

- **Configuration:** Art brightness, color temperature, automatic brightness,
  motion sensitivity, sleep after no motion, slideshow interval/order/category,
  artwork mat style/color and Refresh Art controls now appear under
  Configuration on the device page. They work as before in automations,
  scripts and dashboards; entity IDs are unchanged. Auto-generated dashboards
  no longer show them.
- **Controls** keep the media player, Artwork select and Random artwork button.
- **Removed:** the Power state and Art Mode status sensors; the media player
  already shows power and Art Mode (as its source). They are removed from the
  entity registry on start. Automations using them should use
  `media_player.frame_tv` instead: its state is on/off/unknown, and its source
  is `Art Mode` while Art is showing. The power-reading detail moved to the
  media player's `power_source` attribute (`local_rest`, `ip_control`,
  `standby`, `last_known`, `unreachable`, ...).

## 2.3.0 — Faster mode changes and last-known off

- **Art/TV mode through IP Control.** Measured on a QN55LS03T going from off to
  HDMI 1: IP Control confirmed power-on after 2.9 s, but switching to TV mode
  through the Art channel took another ~20 s while the TV's Art app started.
  With IP Control paired, the mode is now set with IP Control's own Art Mode
  on/off command (retried while the TV finishes starting, confirmed by reading
  it back together with the power state). If a TV does not implement that
  command, the Art channel is used as before.
- **Off stays off.** When the TV was last reported off (by the TV, by HA's own
  Turn off, or by a standby event) and then stops answering on the network,
  the media player stays **off**, with `power_source: last_known` (the Power
  state sensor carried this until 2.4.0). A TV last seen **on** that stops answering
  is still **unknown**, because that can be a network problem or lost mains
  power.

## 2.2.0 — Live progress

Commands such as turning on into an input take several seconds because the TV
has to wake, start its Art app and change mode. The new **Activity** sensor
(normally `sensor.frame_tv_activity`) shows what is happening at each step:
Waking TV, Starting Art app, Switching to Art Mode / TV mode, Waiting for TV,
Switching input, Checking input, Powering off, Retrying, then Idle. The media
player carries the same value as its `activity` attribute. It reports what the
integration is doing, never a guessed TV state, and always returns to Idle,
including after an error.

A card that shows it under the TV controls:

```yaml
type: vertical-stack
cards:
  - type: media-control
    entity: media_player.frame_tv
  - type: tile
    entity: sensor.frame_tv_activity
```

Also fixed: selecting an input while the TV was off skipped the short wait
after leaving Art Mode, because the TV's stored state from before it woke said
"not in Art Mode". A TV that is off now gets that wait, since it wakes into its
last mode.

## 2.1.3 — Responsiveness

- **Commands no longer wait for background polling.** A poll that reads Art
  settings, the artwork catalog and slideshow state can take several seconds,
  longer when the TV leaves some requests unanswered. Turn off, source
  selection and setting changes now make a running poll stop at its next step;
  the rest of it runs on the following poll.
- **Faster start from off:** when IP Control has confirmed the TV is on, the
  Art/TV mode is no longer sent a second time after a 5 s pause.
- **Timing in diagnostics:** `media_player.last_command` shows how long the
  last command waited, how long it took, the power-off path used, and when each
  step finished (power-on, mode, input).
- Selecting an HDMI input from Art Mode can briefly show the TV's last regular
  source (for example Samsung TV): leaving Art Mode returns there before the
  input command arrives.

## 2.1.2 — Fixes

- **Turn on after Turn off:** after IP Control's power-off the TV stays
  reachable on the network, and its Art channel keeps reporting the remembered
  Art Mode setting. 2.1.1 therefore skipped waking it and accepted that
  remembered setting as success, so the TV stayed dark. Now, when the TV
  reports itself off (IP Control, the REST power status, or a standby event),
  Turn on and source selection wake it first with IP Control's explicit
  power-on (Wake-on-LAN as a backup), and a mode only counts as confirmed when
  IP Control no longer reports the TV off.
- **Input switching needs IP Control on some models:** on a QN55LS03T (2020)
  HDMI selection only worked once IP Control was paired.

## 2.1.1 — Fixes

- **Input switching:** the remote-key connection now stays open for one second
  after a key, as the samsungtvws library does; closing it immediately could
  make the TV drop the key. After leaving Art Mode the TV gets two seconds to
  settle before the input key. With IP Control paired, the TV's own
  `inputSourceControl` is tried first when the model supports it, and the
  result is verified: an ignored key is resent once, then reported as an error
  while the media player keeps showing the input the TV actually reports.
  Right after waking, before IP Control answers, the key is sent unverified.
- **Power off:** with IP Control paired, Turn off sends IP Control's explicit
  power-off, which turns the TV off directly, also from Art Mode. The two-second
  power-key hold could first act as a short press (switching Art Mode to TV)
  before turning off. The key hold is still used without IP Control, or when
  the IP Control command could not be delivered; never after an IP Control
  timeout, because the TV may already be shutting down.
- **Artwork controls after a host reboot:** the artwork catalog is now read on
  the first refresh. Before, if HA started within five minutes of its host
  booting, the Artwork select and Random artwork button appeared only after
  those five minutes.

## 2.1.0 — Current input and standby (optional IP Control)

Pair Samsung's local **IP Control** interface to let the media player show the
input the TV is actually on, including changes made with the physical remote.
It stays entirely on your network; no SmartThings account is used.

1. On the TV, turn on **Settings → All Settings → Connections → Network →
   Expert Settings → IP Remote** (the path can differ by firmware).
2. Turn the TV on in normal viewing, **not Art Mode**.
3. In HA open **Settings → Devices & services → Samsung Frame Control →
   Configure → Pair IP Control**, submit, and accept the prompt on the TV.

With IP Control paired:

- **Source** is read from the TV every poll (`source_confirmed: true`): HDMI 1–4
  or Samsung TV. While Art Mode is on the source is Art Mode; the TV still reports
  the input behind the art, so turning on into normal viewing returns there.
- **Off** is reported explicitly. Measured on a QN55LS03T: the REST power status
  keeps saying "on" while the TV shuts down, and about 20 seconds later the TV
  refuses all connections. The media player is **off** when the TV (or HA's own
  Turn off) reported power-off and the TV then refuses connections (standby).
  Since 2.3.0 that last known off is also kept when the TV stops answering
  entirely; a TV last seen on that stops answering is **unknown**. This also works without IP Control after HA's own Turn off
  or the Art channel's standby event, and survives HA restarts.
- After waking, IP Control starts about 20 seconds after the other services.
  Meanwhile the last observed input is shown with `source_confirmed: false`.
- If the TV rejects the token later, a repair notice asks you to pair again and
  the media player falls back to the 2.0.0 behavior. **Remove IP Control
  pairing** is in the same Configure menu.
- Volume still uses UPnP; IP Control's reading is used only if UPnP is
  unavailable.

**Samsung TV** is now a source (Samsung TV Plus or the antenna tuner, sent as
`KEY_TV`). Rename or hide it, like the HDMI inputs, under **Configure → Input
names**.

To check your TV before pairing in HA, `tools/frame_probe.py` is a standalone,
read-only script (Python 3.10+, no packages): `check`, `pair` and `watch`.

## 2.0.0 — Media player (breaking change)

The **Art Mode**, **TV Mode** and **Power off** buttons are replaced by one media
player, normally `media_player.frame_tv`. The three button entities are removed
from the entity registry on first start; update automations that pressed them:

| 1.x button | 2.0.0 action |
| --- | --- |
| `button.frame_tv_art_mode` | `media_player.select_source` with `source: Art Mode` |
| `button.frame_tv_tv_mode` | `media_player.select_source` with an HDMI source, or `media_player.turn_on` |
| `button.frame_tv_power_off` | `media_player.turn_off` |

- **Turn on** wakes the TV (Wake-on-LAN when needed) into the mode it was last
  in, including Art Mode. The last mode is observed from the TV and survives
  HA restarts. When that was normal viewing, the TV returns to its own last
  input, which also reflects changes made with the physical remote.
- **Turn off** is the existing two-second power-key hold, sent once.
- **Source** lists Art Mode and HDMI 1–4. Selecting an HDMI input leaves Art Mode
  (waking first if needed), verifies that, then sends the local `KEY_HDMIn`
  remote key. Selecting Art Mode turns Art Mode on.
- **Input names:** Samsung publishes the names you give inputs on the TV only
  through its SmartThings cloud, so they cannot be read locally. Name or hide
  inputs under **Settings → Devices & services → Samsung Frame Control →
  Configure**. Clear a name to hide that input.
- **Current input:** without IP Control (2.1.0, above) the source shows the
  input HA last selected, with attribute `source_confirmed: false`. Art Mode is
  always read from the TV (`source_confirmed: true`).
- **Volume** level and mute use the TV's local UPnP renderer on port 9197, with
  no pairing. Changes are read back; a value the TV does not apply is reported
  as an error. If the level cannot be read, volume up/down falls back to the
  local volume remote keys.
- **State** is `on`/`off` from the TV's local power report and `unknown` when it
  cannot be observed. The media player never becomes unavailable, so Turn on
  always works.

The Power state and Art Mode status sensors, Art settings, artwork, slideshow
and mat controls are unchanged. Existing pairing and unique IDs are preserved.

## 1.4.0 — Direct power status

Adds a **Power state** sensor, normally `sensor.frame_tv_power_state`. It reads
`device.PowerState` from the TV's local `/api/v2/` REST endpoint over HTTPS on
8002, falling back to HTTP on 8001. It does not infer power from the Art setting,
an open TCP port, or a button command. No SmartThings account is needed.

- `on`: the local API explicitly reports power on, whether watching TV or Art.
- `off`: the API explicitly reports off, or the Art channel sends a standby event.
- `unknown`: no valid power report is available, including network loss or a TV
  which completely shuts down its network service. Missing PowerState is also
  unknown; a timeout alone cannot distinguish standby from a network outage.

When power is reported off, **Art Mode status** is off even if Samsung remembers
Art Mode as enabled. If power is unknown, Art Mode status is unavailable instead
of claiming the panel is displaying Art. Reconnection and valid readings restore
state automatically. The power sensor's `source` attribute and diagnostics show
where the observation came from. Existing entity IDs and pairing are preserved.

Power is checked roughly every 15 seconds while idle. Active serialized commands
or a slow API response can delay a poll. Art settings still refresh roughly every
60 seconds and the catalog every five minutes. Incoming Art events no longer
restart the polling countdown, so they cannot indefinitely postpone power checks.
Polling only reads state; it never sends Wake-on-LAN or mode/power commands.

After installing, restart HA and inspect the Power state sensor in the device
page. Test switching from Art to full power-off with the TV remote: within a poll
cycle it should report Off if the TV supplies an off response, otherwise Unknown;
Art Mode should no longer remain On. Disconnecting the network should produce
Unknown, not a falsely confirmed Off. The behavior remains to be checked on your
specific firmware; a smart plug's power measurement would be a separate way to
infer physical display power when the TV provides no response.

## 1.3.2 — Slideshow and mat compatibility

Stopped slideshow responses can omit category and order. Starting an interval no
longer requires those missing fields: it uses explicitly chosen next-start
preferences, or Sequential / My photos by default. While stopped, choosing order
or category saves a next-start preference in memory; it does not start playback.
Those selectors expose `applies_on_next_start: true`. These preferences last until
integration reload/restart; they are not reported as observed running TV state.
The interval selector starts or stops playback. Slideshow API discovery retries
the alternate getter if the previously detected one no longer returns a value.

Mat discovery now accepts the legacy `matte_list` field and complete mat IDs,
including catalogs without separate colors. Complete IDs are preserved rather
than appending a color twice; styles with underscores are parsed using the
catalog. Legacy catalogs restrict colors to listed combinations. A style change
may choose that style's listed color if the old color is unavailable.

Slideshow and mat writes allow up to three readback attempts with short delays,
without resending the mutation. An unchanged or unreadable value still produces
an error. A preparation failure now explicitly says no change command was sent.
Brightness, temperature, artwork selection and wake logic are unchanged.

After updating, restart HA, enter Art Mode, and press Refresh Art controls. Try
starting slideshow at 15 minutes and selecting a mat style. If either still
fails, download diagnostics from the integration entry's menu immediately after
the failure. Diagnostics include the chosen API, mat IDs/options and last Art
operation phase/readback, excluding host, MAC, pairing token and artwork titles.
These fixes are locally tested but still require validation on your TV.

## 1.3.1 — Wake recovery

An open standby TCP port no longer prevents Wake-on-LAN recovery after an
unconfirmed mode result. Transient connection loss, including loss after sending,
now closes the Art socket, sends a three-packet WOL burst, waits for startup, and
retries the same explicit Art on/off request. Each mode attempt has a 20-second
limit within the existing 75-second overall budget, with at most four attempts.
Unconfirmed results use the same bounded recovery rather than stopping after two
tries. A successful result following wake is reasserted once when budget allows.

This retry applies only to explicit Art/TV mode commands. Full-power-off is still
sent once; authorization failures and explicit TV rejections are not retried.
Existing Art controls are unchanged. These fixes address identified retry gaps;
they have not yet been tested on the physical TV.
