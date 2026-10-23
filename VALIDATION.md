# Validation for version 2.8.3

Executed October 23, 2026, Python 3.14.6, Home Assistant 2026.10.2:

- **321 automated tests passed** (2 new).
- Requested: a sensor for when the channel list was last downloaded.
- New tests: the sensor is a diagnostic timestamp, unknown before any
  download and showing the failed download's error; after Update channel list
  it shows the list's download time, country and channel count; a later
  failed update keeps that time and shows the error; a saved list for another
  country is not shown; no sensor when downloading is disabled.

# Validation for version 2.8.2

Executed October 23, 2026, Python 3.14.6, Home Assistant 2026.10.2:

- **319 automated tests passed** (8 new), five consecutive full runs.
- Reported: the Update channel list button showed no icon; the user's TV
  still drops from Art Mode to Samsung TV every 30–60 minutes. Checked
  against Material Design Icons (Templarian/MaterialDesign-SVG): no
  `playlist-refresh`; every other icon the integration uses exists.
- New tests: an exit the TV makes by itself is recorded once, with the input
  and channel after, IP Control and Art requests from the last two minutes,
  a log warning, and no tokens; Home Assistant's own source changes are not
  recorded, but a later exit is (with the command in its history); turning
  off, by IP Control or by the Art channel's standby event, is not an exit;
  exits seen through Art channel events without IP Control are recorded; only
  the last 10 are kept; requests older than two minutes are left out; the
  button's icon.
- Mutation checks (each caught): using the poll lock as "command running"
  (no exit was ever recorded from polls); not adopting a command's result;
  counting power-off; not checking Art events; no time window; the old icon.

# Validation for version 2.8.1

Executed October 23, 2026, Python 3.14.6, Home Assistant 2026.10.2:

- **311 automated tests passed** (6 new), three consecutive full runs.
- Reported on the user's TV with 2.8.0: on the Samsung TV source, a channel
  change failed with "does not support action media_player.play_media" and
  worked on a second try. Cause: `_read_ipc` cleared `input_confirmed` before
  awaiting the TV, so the source looked unknown during every read.
- New tests: the source stays confirmed while an IP Control read is in
  flight; play/browse offered on HDMI and in Art Mode with IP Control and not
  without it; playing a channel from HDMI sends KEY_TV first, then the digits;
  from Art Mode leaves Art; on Samsung TV does not switch; after switching,
  waits while IP Control is still starting (5 timed-out reads) and then tunes.
- Mutation checks (each caught): the 2.8.0 read gap; no switch to Samsung TV;
  no wait for the source; channel actions only on the Samsung TV source; a
  failed read keeping the confirmation.
- Intermittent test failure found and fixed: the activity tests recorded
  sensor changes with a plain-function event listener, which Home Assistant
  runs in worker threads, so under load the recorded order could be wrong
  (`test_activity_for_power_off_and_failures_returns_to_idle`, seen once each
  in the 2.5.1 and 2.8.0 runs and once here). It is now a `@callback`. A test
  issue only; the integration's activity reporting was correct.

# Validation for version 2.8.0

Executed October 23, 2026, Python 3.14.6, Home Assistant 2026.10.2:

- **305 automated tests passed** (25 new). 23 of 24 full runs passed; one
  run had a single failure that did not recur in the next 20 runs and could
  not be identified (see also the unexplained run noted for 2.6.1).
- Samsung's channel API, observed in a browser on samsung.com/us/tvs/
  smart-tv/samsung-tv-plus: `tvplusGenre`, `tvplusGenreOrder` and
  `tvplusApp?genre=…` per genre on vdapi.samsung.com, each with `country=`.
  Fields per channel: CHANNELNUMBER, CHANNELNAME, GENRENAME, LOGOURL,
  DESCRIPTION, CHANNELID, HIDE. Line-ups for 25 of 31 countries tried (none
  for AR, CL, CO, JP, PE, PL); sampled countries all number their channels.
- On the user's network, `tools/frame_probe.py channels US`: the API answered
  plain Python, a Home Assistant user agent and a browser agent alike
  (HTTP 200, 0.7–0.9 s); 591 channels in 20 genres, all numbered, none hidden,
  listed total 591; 16 s when read one genre at a time.
- New tests: downloader against Home Assistant's mocked web client (genre
  order applied, hidden and unnumbered channels skipped, only https logos,
  first listing of a number kept, sorted, genres fetched together; no line-up,
  short list, HTTP error, bad JSON and timeout each refused; missing genre
  order tolerated); first setup downloads in the background and saves; a saved
  list is reused; a new country downloads; disabled means no download and no
  button; installs from before 2.8.0 default to off; built-in U.S. names are
  not used for another country; a saved list of another country, or after
  turning the download off, is not used; a failed first download logs, keeps
  the built-in list and shows the error on the button; the button downloads
  again and keeps the previous list on failure; the channel logo is the
  player's picture through Home Assistant's image proxy; no logo without the
  download; browse follows Samsung's genre order with logos; the saved list is
  removed with the device; the setup form offers the download (on) and a
  country (Home Assistant's own, else U.S.) and saves them as options, not
  connection data; the options step changes the country and downloads it;
  diagnostics show the download.
- Mutation checks (each caught): hidden channels kept; insecure logos kept;
  short list accepted; genre order ignored; another country's list used;
  download setting ignored; U.S. names for every country; downloading at
  every setup; saved list ignored; list not saved; choice not saved as
  options; home country ignored; no logo picture; no logo hash; no browse
  thumbnails; button always created; list kept after removal; no update on a
  failed download.

# Validation for version 2.7.2

Executed October 23, 2026, Python 3.14.6, Home Assistant 2026.10.2:

- **280 automated tests passed** (7 new), three consecutive full runs.
- On the user's TV (QN55LS03T), `tools/frame_probe.py hold-channel
  1114,2110,1114,2110 --idle 10,60,300` over one remote connection: connect
  1.49 s once; changes after 0, 10, 60 and 300 s idle tuned in 2.93, 2.90,
  2.77 and 3.25 s (typing 2.4 s each, 0.6 s gap + Enter); keep-alive pings
  answered 1/1 and 9/9; no close or timeout event from the TV. All four
  landed on the right channel.
- New tests: the connection is kept and reused (one connect, no pause after a
  key, 30 s heartbeat); a close or ms.channel.timeOut from the TV ends it and
  the next command reconnects; a dead kept connection failing before any key
  is replaced once; a new connection failing is not retried; `fresh` forces a
  new connection; the idle timer closes it and restarts on use; a retyped
  channel number uses a new connection; the connection is closed when the TV
  turns off and not while it is on.
- Mutation checks (each caught): never reusing; no retry on a dead kept
  connection; retrying a new connection too; ignoring `fresh`; no idle close;
  ignoring ms.channel.timeOut; ignoring the hold; retyping on the kept
  connection; never closing the remote connection.

# Validation for version 2.7.1

Executed October 23, 2026, Python 3.14.6, Home Assistant 2026.10.2:

- **273 automated tests passed** (5 new), three consecutive full runs.
- On the user's TV (QN55LS03T), `tools/frame_probe.py time-channel`, eight
  runs: 0.3 s between digits without Enter tuned once (3.6 s after the last
  digit, 5.9 s total) and lost a digit twice (1114 → 114; 2111 unchanged from
  an invalid channel); 0.2 s lost a digit (1112 → 112); with Enter at 0.3,
  0.1 and 0 s the TV tuned 111, 12 and 2 (more leading digits lost the faster
  they came); 0.5 s plus Enter tuned 2110 correctly 0.35 s after Enter, 3.7 s
  in total (connect 1.3 s, typing 2.0 s).
- New tests: digits spaced ≥ 0.5 s with Enter last and a short hold; a dropped
  digit that tuned another channel is retyped once and then confirmed; no
  retype after the TV left the Samsung TV source; a number the TV does not
  have is typed twice, then reported; remote client spaces keys on one
  connection with the hold after the last key; a failure after a digit
  reports it was sent.
- Mutation checks (each caught): no Enter; 0.3 s gap; no retype; retype after
  leaving the source; remote ignores the gap; remote ignores the hold.

# Validation for version 2.7.0

Executed October 23, 2026, Python 3.14.6, Home Assistant 2026.10.2:

- **268 automated tests passed** (21 new), three consecutive full runs.
- On the user's TV (QN55LS03T), `tools/frame_probe.py try-channel` from 2111
  to 1114: IP Control `directChannelControl` with the reported fields and the
  new number, and with the number only, both answered -32602 (invalid
  params); remote keys KEY_1 KEY_1 KEY_1 KEY_4 tuned 1114 without Enter,
  confirmed by IP Control.
- Channel list: `tools/stvp_channels.py` on Samsung's "U.S. Channel Lineup,
  Effective: September 2026" PDF found 590 channels in 20 genres with no
  unplaced text; eight two-line names joined. Cross-checked against
  `pdftotext -layout`: same 590 numbers and names (three differences traced
  to the comparison itself: a "3000" inside a name, a missed pattern, and a
  wrapped "by History" belonging to channel 3544).
- New tests: shipped line-up well formed; missing or broken file gives no
  names; select lists channels, follows remote changes, types one number over
  one connection and reads it back; unavailable on HDMI and in Art Mode; not
  created without IP Control; user names win and add channels; a current
  channel outside the line-up stays selectable; a number the TV does not have
  is reported after typing once; a partly typed number is never retyped; a
  connection failing before any key is retried; no keys when already on the
  channel; refused when the TV left the Samsung TV source; play_media tunes,
  rejects non-numbers and other media types; play/browse only on Samsung TV;
  browse by genre over the websocket API; diagnostics show the line-up;
  scraper layout parsing (columns, two-line names, genre across pages,
  disclaimer, unplaced text and numbers).
- Mutation checks (each caught): retry after partial typing; no read-back;
  no Samsung TV source check; retyping the current channel; one connection
  per digit; built-in name over the user's; channel features on every source;
  no two-line join; genre reset per page; select always available.

# Validation for version 2.6.2

Executed October 23, 2026, Python 3.14.6, Home Assistant 2026.10.2:

- **247 automated tests passed** (1 new). Reported on the user's TV with
  2.6.1: on Samsung TV (channel number shown) the media player offered
  Previous/Next but the dashboard showed only volume. Home Assistant's frontend
  (`computeMediaControls`) shows Previous/Next only while the state is
  `playing` or `paused`.
- Changed tests: Samsung TV with IP Control reports `playing` and offers
  Previous/Next; HDMI stays `on`; Samsung TV without IP Control (input not
  observed) stays `on` without channel buttons.

# Validation for version 2.6.1

Executed October 22, 2026, Python 3.14.6, Home Assistant 2026.10.2:

- **246 automated tests passed**, ten consecutive full runs. (The first run
  after the change showed 12 failures across unrelated modules; they did not
  recur in the next twelve runs and were not reproduced.)
- Changed tests: Art Mode offers no volume set/mute/step and a volume call in
  Art Mode is refused without reaching the TV; volume returns with its level
  when the TV leaves Art Mode for HDMI. The existing volume tests now run on a
  TV source.

# Validation for version 2.6.0

Executed October 22, 2026, Python 3.14.6, Home Assistant 2026.10.2:

- **245 automated tests passed** (26 new), four consecutive full runs.
- Measured on the user's TV (QN55LS03T, 2020 Frame) with the probe before
  building: `get_thumbnail` for MY_F0001 answered with conn_info
  {secured: false, same IP as the TV}; the socket sent a 320x180 JPEG
  (28,615 bytes) in 0.48 s. `get_slideshow_status` was refused (error -9);
  `get_auto_rotation_status` answered in 0.15 s with value 10,
  shuffleslideshow, MY-C0002 and a 31-piece content_list. KEY_CHUP and
  KEY_CHDOWN changed the channel on Samsung TV.
- New tests (fake TV answering like that TV): picture in Art Mode served
  through Home Assistant's image proxy and fetched from the TV once per
  artwork; picture follows an artwork change; no picture on HDMI, Samsung TV
  or off; a refused thumbnail gives no picture, is not asked again, shows in
  diagnostics, and another piece is still tried; Pause/Play set
  `set_auto_rotation_status` off and back to the previous interval (10),
  keeping shuffle; default 15 when never seen running; interval restored after
  a restart; Next/Previous follow the slideshow order with wrap-around, start
  from the piece showing now, and fall back to the Artwork list; Samsung TV
  Next/Previous send channel keys and read the channel back at once; HDMI has
  no transport buttons and refuses them.
- Protocol tests against a real local socket: download from the TV's own
  address even if a reply names another; fallback to `get_thumbnail_list`
  only when refused; oversized file, oversized header, non-image and
  truncated transfers refused; missing conn_info; inline images from older Art
  APIs, including binary WebSocket frames through the Art channel.
- Mutation checks (each caught): thumbnail never cached; failed piece retried
  every view; following the reply's IP; no size cap; no list fallback; binary
  frames rejected; catalog order instead of slideshow order; stepping from a
  stale artwork; interval not remembered; no channel read-back; Next/Previous
  on every source; Art Mode state always `on`.

# Validation for version 2.5.1

Executed October 22, 2026, Python 3.14.6, Home Assistant 2026.10.2:

- **219 automated tests passed** (4 new), five consecutive full runs. Reported
  on the user's TV with 2.5.0: pressing power on the remote while on Samsung TV
  switched the TV to Art Mode, but the integration took about a minute to show
  it (Art Mode was only read in the 60 s Art refresh).
- New tests: a remote change to Art Mode shows in one poll with the artwork
  read in the same poll; leaving Art Mode by remote shows the input in one poll
  and clears the artwork and Art settings; without an IP Control Art flag the
  Art channel still decides; the client reads `artModeControl` on every poll
  while on and not while off.
- Mutation checks (each caught): ignoring the `artModeControl` answer; not
  forcing the Art refresh when Art Mode turns on; keeping the old Art settings
  when Art Mode turns off.

# Validation for version 2.5.0

Executed October 22, 2026, Python 3.14.6, Home Assistant 2026.10.2:

- **215 automated tests passed** (8 new). 2.4.0 reported working on the user's
  TV. `tools/frame_probe.py check` on Samsung TV Plus returned
  `directChannelControl` = {RFtype: CABLE, atvDtv: TVPLUS, channelNum: 2110},
  then 2111 after changing channel with the remote.
- New tests: channel number and title on the Samsung TV source, following a
  channel change; user channel names; no channel on HDMI or in Art Mode (with
  the TV source behind the art); channel names options step with `=` and `:`,
  blank lines and `7-1` style numbers; an unreadable line is reported; saving
  input names keeps channel names and the reverse; the IP Control client asks
  for the channel only on the TV source and a channel error does not fail the
  poll.
- Mutation checks: input names replacing all options, showing the channel in
  Art Mode, and asking for the channel on every input each made a test fail.
- Not included: changing channel from HA (digit keys are untested on this TV).

# Validation for version 2.4.0 (previous release)

Executed October 22, 2026, Python 3.14.6, Home Assistant 2026.10.2:

- **207 automated tests passed** (2 new, 7 updated to read power and Art Mode
  from the media player). Confirmed on the user's TV with 2.3.0: off → HDMI 1
  took 7.7 s (power-on 3.0 s, IP Control mode 5.0 s, input 7.7 s), down from
  28.3 s; the TV implements IP Control's Art Mode setter.
- New tests: Art settings and Refresh are in the Configuration category while
  the media player, artwork select, random artwork and Activity sensor are not;
  pre-existing Power state and Art Mode status registry entries are removed on
  setup while Current artwork remains.
- Mutation checks: dropping the configuration category and not retiring the Art
  Mode status sensor each made a test fail.

# Validation for version 2.3.0 (previous release)

Executed October 22, 2026, Python 3.14.6, Home Assistant 2026.10.2:

- **205 automated tests passed** (6 new or updated).
- Input from the user's TV with 2.2.0: Turn off took 0.7 s in HA (no wait
  behind polling; IP Control confirmed off). Off → HDMI 1 took 28.3 s: power-on
  confirmed at 2.9 s, TV mode at 22.7 s, input at 28.3 s. The user asked for
  off to be kept as the last known state instead of unknown.
- New tests: off → HDMI with IP Control's artModeControl sets the mode without
  any Art channel mode request and completes in under 2 s while the Art channel
  is slow; the setter is retried while the TV answers "failed"; an
  unimplemented setter falls back to the Art channel and is remembered; a
  remembered Art flag on a TV that is still off is not success; last known off
  is kept when the TV does not answer; a TV last seen on that stops answering
  is unknown.
- Mutation checks: trusting the remembered IP Control Art flag, removing the
  IP Control mode path, and restoring the old unknown rule each made tests fail.
- Not yet known: whether this TV implements artModeControl as a setter; the
  next off → HDMI diagnostics will show an `ip_control_mode` step if it does.

# Validation for version 2.2.0 (previous release)

Executed October 22, 2026, Python 3.14.6, Home Assistant 2026.10.2:

- **199 automated tests passed** (3 new). Request: better feedback while slow
  commands run.
- New tests record the Activity sensor's state changes as HA sees them: off →
  HDMI 2 shows waking, starting_art_app, switching_to_tv, settling,
  switching_input, checking_input, idle; Turn off shows powering_off, idle; a
  failed Turn on shows waking and retrying and still ends at idle; the sensor
  exists while the TV is unreachable.
- Found by the new test: after waking, the settle before the input command was
  skipped because the TV's pre-wake Art state was used. Fixed and covered.
- Mutation checks: not resetting activity after a command, and removing the
  settle after wake, each made tests fail.

# Validation for version 2.1.3 (previous release)

Executed October 19, 2026, Python 3.14.6, Home Assistant 2026.10.2:

- **196 automated tests passed** (3 new). Reported with 2.1.2 on the user's
  TV: Turn on works; Turn off from Art Mode goes dark without passing through
  Samsung TV but slowly; off → HDMI works but takes 30+ s and passes through
  Samsung TV.
- New tests: Turn off issued during a slow Art refresh (0.4 s per request)
  waited under 1 s and used IP Control; a refresh stopped for a queued command
  is completed on the next poll; off → HDMI with IP Control sends the Art mode
  once (no reassert) and records the steps.
- Mutation checks: disabling pre-emption and restoring the unconditional
  reassert each made tests fail.
- Not yet known: how much of the remaining delay is the TV itself; the new
  `last_command` timings in diagnostics will show it.

# Validation for version 2.1.2 (previous release)

Executed October 19, 2026, Python 3.14.6, Home Assistant 2026.10.2:

- **193 automated tests passed** (4 new). Reported with 2.1.1 on the user's
  QN55LS03TAFXZA: HDMI switching worked after pairing IP Control in HA; after
  Turn off the TV would not turn on from HA. Diagnostics showed power_state
  "off" from ip_control with the TV still answering on the network.
- New tests model that state (reachable, IP Control powerOff, Art channel
  answering with the remembered setting): Turn on sends IP Control powerOn and
  ends On in Art Mode; a remembered Art setting with the TV still off is not
  reported as success; source selection from that state wakes first; a REST
  "off" with an open port still sends Wake-on-LAN.
- Mutation checks: the 2.1.1 port-only wake check, trusting the remembered Art
  setting, and removing IP Control powerOn each made a test fail.

## On-TV check for 2.1.2

1. Turn off from HA (Art Mode). Wait 30 s. Turn on from HA: the TV lights up in
   Art Mode and the media player shows On.
2. Turn off again, then select HDMI 1 from HA: the TV wakes and switches.

# Validation for version 2.1.1 (previous release)

Executed October 19, 2026, Python 3.14.6, Home Assistant 2026.10.2:

- **189 automated tests passed** (12 new). Reported on the user's TV with
  2.1.0: HDMI selection did not switch inputs, Art Mode did, and Turn off went
  through Samsung TV before powering off.
- New tests: remote-key connection stays open after the send; settle delay after
  leaving Art Mode; verified switching (key, inputSourceControl when supported,
  unsupported setter remembered, already-on input sends nothing, ignored key
  resent once then an error while showing the observed input, unverifiable
  right after wake is not an error); IP Control power-off used in Art Mode with
  no power key, fallback to the key hold when not delivered, no key after an
  IP Control timeout, key hold without IP Control; artwork catalog read on the
  first refresh soon after host boot.
- Mutation checks: closing the key connection immediately, the old catalog
  timer, removing IP Control power-off, and removing the key resend each made
  tests fail.
- The cause of the HDMI failure on the TV is inferred, not yet confirmed: the
  key connection closed without the delay the verified library uses.

## On-TV check for 2.1.1

1. With IP Control paired, from Art Mode select HDMI 2: the TV switches, and the
   source shows HDMI 2 with `source_confirmed: true`. If it does not, HA shows
   an error; download diagnostics and check `ip_control` in the file.
2. Select Samsung TV and HDMI 1 from normal viewing.
3. From Art Mode press Turn off: the TV goes dark without showing TV first, and
   the media player shows Off.

# Validation for version 2.1.0 (previous release)

Executed October 19, 2026, Python 3.14.6, Home Assistant 2026.10.2
(`pytest-homeassistant-custom-component` 0.13.373):

- **177 automated tests passed** (32 new), ResourceWarning as error, HA socket
  blocking active, and no error logs during the run.
- Protocol facts were first measured on the user's QN55LS03TAFXZA with
  `tools/frame_probe.py`: IP Control on port 1516; `getTVStates.inputSource`
  followed HDMI1 ↔ HDMI2 and Samsung TV Plus (`TV`) changes made with the remote
  within one 3-second poll; the input behind Art Mode stays reported; picture
  mode is `Ambient` in Art Mode and `Standard`/`StandardHDR` otherwise; IP
  Control and UPnP volume agreed; on full power-off from Art Mode IP Control said
  `powerOff` while REST still said `on`, then all ports refused connections
  ~20 s later; on power-on REST, UPnP and IP Control answered after ~0, 10 and
  18 s; `getDeviceInformation` is not implemented on this model.
- New HA tests cover the observed input (remote changes, Samsung TV, renamed,
  hidden and unknown inputs, Art Mode with an input behind it, unconfirmed
  immediately after selection), startup grace, explicit off from IP Control,
  standby from refused connections, timeouts staying unknown, a REST "on" during
  shutdown not cancelling the off report, HA's own power-off, power-on clearing
  the off report, standby surviving an HA restart, turn-on from standby, token
  rejection (repair issue, polling stops, fallback), IP Control volume fallback,
  and the options menu (pairing on 1516 and 1515, closed port, timeout, refusal,
  unpair, menu contents).
- The blocking IP Control client was tested against a real local HTTPS
  JSON-RPC server with a self-signed certificate: minimal headers, pairing
  without a token, flat and nested error forms, refused connection, and no
  token in error text. The TLS security-level fallback for older Frames is not
  exercised by a test.
- Mutation checks: treating any refusal as off, letting REST "on" clear an off
  report, ignoring IP Control powerOff, marking observed input unconfirmed, and
  continuing to poll with a rejected token each made tests fail.

## On-TV check for 2.1.0

1. Configure → Pair IP Control with the TV in normal viewing; accept the prompt.
2. Change inputs with the TV remote; the media player source follows within
   about 15 seconds with `source_confirmed: true`.
3. Select Samsung TV in HA; the TV switches to Samsung TV Plus.
4. Hold the TV remote's power button; the media player becomes Off (not
   Unknown) within about 40 seconds and stays Off after an HA restart.
5. Turn on from HA; the TV wakes into its previous mode.

# Validation for version 2.0.0 (previous release)

Executed October 19, 2026, in a Linux container with Python 3.14.6 and
**Home Assistant 2026.10.2** (`pytest-homeassistant-custom-component` 0.13.373):

- **145 automated tests passed**, with ResourceWarning elevated to an error and
  Home Assistant's socket blocking and lingering-task checks active.
- HA-facing tests now run on a real Home Assistant core instead of framework
  doubles: integration setup/unload, entity registry, restore state, services,
  config flow (user, SSDP, reconfigure) and the new options flow. A simulated TV
  replaces only the network transports (REST power, Art and remote channels,
  UPnP, Wake-on-LAN, power-off worker).
- Media player tests cover legacy button removal, on/off/unknown state, HDMI
  selection (Art off verified before `KEY_HDMIn`), Art Mode selection, turn-on
  from full power-off into Art Mode, turn-on restoring normal viewing across an
  HA restart, mode changes observed from the TV, single power-off hold, verified
  volume/mute, unapplied volume reported as an error, remote-key volume fallback,
  refused volume while off, authorization errors, transient input-key retry,
  renamed/hidden inputs and invalid names.
- UPnP tests exercise a real local HTTP SOAP endpoint: request envelopes,
  volume/mute parsing, SOAP faults, malformed values, refused connections and
  out-of-range values that are never sent. Remote-channel tests cover framing,
  token refresh, unauthorized handshakes and the handshake deadline.
- The 1.4.0 protocol, wake-recovery, power, worker and Art settings tests still
  pass unchanged (one real-server test now opts in to sockets).
- `set_value` was refactored into per-control writers. A 7,776-scenario
  old-versus-new comparison produced identical requests, state, diagnostics and
  errors for every case.
- Mutation checks: skipping the HDMI key, claiming an HDMI source as confirmed,
  or skipping volume readback each made tests fail.
- Ruff error/import checks and formatting pass (16 pre-existing long-line
  warnings in user-facing message strings remain).

## Limits

No live TV was available. In particular these need checking on your TV:
`KEY_HDMI1`–`KEY_HDMI4` support on your firmware, UPnP volume on port 9197
(especially while in Art Mode), and remote-channel authorization with the
existing pairing token.

## On-TV check for 2.0.0

1. Existing entry loads without re-pairing. The three old buttons are gone and
   `media_player.frame_tv` exists; Art controls and sensors remain.
2. In Art Mode the media player shows On / Art Mode. Select HDMI 1: the TV leaves
   Art Mode and switches to HDMI 1. Select Art Mode again.
3. Fully power off with Turn off. State becomes Off (or Unknown if the TV stops
   answering). Turn on: the TV wakes into Art Mode.
4. Switch to normal viewing, turn off, restart HA, then Turn on: the TV wakes
   into normal viewing on its last input.
5. Set volume to 15%, mute and unmute; the TV and HA agree. Use volume up/down.
6. Under Configure, rename HDMI inputs and hide unused ones; the source list
   follows after the automatic reload.

# Validation for version 1.4.0 (previous release)

Executed locally October 19, 2026, on Windows with Python 3.12.14:

- **121 automated tests passed**, with ResourceWarning elevated to an error.
- Real local aiohttp WebSocket server exercised the async handshake, set-mode
  request, status readback and connection cleanup.
- Protocol tests cover both request-ID forms, wrong/late IDs, ID-less expected
  events, unsolicited events, API 5.x status fields, token updates, authorization
  denial, handshake deadline, request timeout, send failure, connection loss,
  reconnection and cancellation with reader cleanup.
- Direct power tests cover REST on/off, port 8001 fallback, unknown/malformed or
  missing power fields, unreachable TV handling, stale Art state invalidation,
  standby push events, disconnect versus power-off distinction, power checks on
  every poll with Art-query throttling, blocked settings writes while off, sensor
  registration at offline startup and prevention of push-induced poll starvation.
- Slideshow/mat regressions cover starting from an off response without category
  or type, next-start choices without changing playback, legacy complete/plain
  mat IDs, underscore-containing styles, delayed readback without repeated writes,
  truthful pre-send errors, stopped-state selector availability and diagnostics
  exclusion of credentials/network identifiers/artwork titles.
- Wake recovery regression tests cover an open standby port with repeated
  unconfirmed responses, connection loss after sending, fresh connections between
  recovery attempts, per-attempt timeout recovery, and no retry after explicit TV
  rejection. Existing authorization, overall timeout and single-power-off tests pass.
- Native controller tests verify mode buttons call the async client (not the
  subprocess), read-only offline refresh sends no WOL, and stale Art state cannot
  cause a settings write while the TV reports normal viewing mode.
- Settings tests verify capability/range parsing, older-API fallback, unsupported
  control omission, state invalidation, random artwork selection, verified writes,
  missing setter acknowledgement followed by readback, slideshow field retention,
  current-picture mat targeting, and failed readback errors.
- HA adapter tests use framework doubles to check dynamic registration after an
  offline start, unique IDs, availability, friendly artwork options, number/switch
  commands, observed sensors, refresh behavior and always-available wake buttons.
- Existing discovery/configuration, WOL, retry limits, serialization, full-power-
  off, pairing, and subprocess timeout/cancellation regressions still pass.
- Ruff import/error checks and formatting passed. The builder parses all Python,
  validates JSON and translations, and checks archive paths and integrity.
- Both brand icon PNGs were byte-compared with the previous 1.2.3 ZIP and retained.
- `samsungtvws==3.0.6` remains pinned. No fork is installed into its namespace.
  New async transport tests used aiohttp 3.14.3; production uses HA's aiohttp.

## Limits

These are protocol tests, local transport tests and HA framework-double tests,
not a full Home Assistant core integration test. No live TV or Home Assistant
instance was available. In particular, the new controls and native async transport
still need verification on your TV's exact firmware. Previously verified physical
button behavior does not establish that every newly exposed setting is supported.

## Direct-power hardware check

With the TV in Art Mode, confirm Power state = on and Art Mode status = on. Fully
power off using the physical remote and wait for a poll: expect Power state = off
when explicitly reported, otherwise unknown; Art Mode must not remain on. Unplug
networking to verify unknown rather than an inferred off. Return to normal TV
viewing and confirm power on / Art off. The test suite simulates these responses;
it cannot establish what this particular TV firmware reports while in standby.

## On-TV check after installing

1. Existing entry loads without re-pairing; existing three buttons and icon remain.
2. While already on: TV → Art, Art → TV, and Art → Art do not toggle power.
3. Fully off → Art and fully off → TV each work with one press after the startup
   wait. Full power-off is sent only once.
4. In Art Mode, press Refresh Art controls. Supported controls appear; unavailable
   features are omitted rather than assigned invented values.
5. Change brightness and color temperature; verify visible result and HA readback.
6. If reported: test automatic brightness, motion timer and sensitivity.
7. Select a different artwork and press Random artwork. Verify the sensor follows.
8. If reported: test slideshow interval/order/category and current artwork mat.
   Firmware or image-size rejections should surface as errors.
9. With the TV off or in normal TV mode, settings are unavailable and neither
   background refresh nor a setting action sends WOL or switches display modes.
10. Disconnect/reconnect networking and reload the integration. State recovers on
    the next refresh without stale values being shown as confirmed.

Previous 1.3.2, 1.3.1, 1.3.0 and 1.2.3 ZIPs are retained separately as rollback packages. To roll back,
replace the integration folder with that version and restart HA. Extra entities
from 1.3.0 may remain in HA's registry as unavailable and can be removed there.
