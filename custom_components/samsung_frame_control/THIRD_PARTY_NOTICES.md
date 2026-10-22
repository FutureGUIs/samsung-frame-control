# Async Art protocol attribution

The isolated `async_art.py` implementation adapts the asynchronous listener and
request/response approach described by Nick Waterton's `SamsungTVAsyncArt`,
reviewed at repository commit `fe95ef1d` (April 6, 2026):
https://github.com/NickWaterton/samsung-tv-ws-api

Original source notices:

- Copyright (C) 2019 DSR! <xchwarze@gmail.com>
- Copyright (C) 2021 Matthew Garrett <mjg59@srcf.ucam.org>
- Copyright (C) 2024,2025 Nick Waterton <n.waterton@outlook.com>
- Original source SPDX identifier: LGPL-3.0

This adaptation is distributed under LGPL-3.0-only. Full editable source is
included in this repository. License texts are in `licenses/LGPL-3.0.txt` and
`licenses/GPL-3.0.txt`.

Changes made September 25, 2026: use Home Assistant's shared aiohttp session;
remove synchronous construction/pairing side effects; implement bounded
handshake, request and cleanup operations; match both old/new request IDs;
discard timed-out sockets; sanitize errors; verify setters with reads; expose
only settings successfully returned by the TV. The fork itself is not installed
and its shared `samsungtvws` namespace is not replaced.

The original upstream package `samsungtvws==3.0.6` remains a separately installed
dependency for discovery, pairing and the existing full-power-off operation:
https://github.com/xchwarze/samsung-tv-ws-api
