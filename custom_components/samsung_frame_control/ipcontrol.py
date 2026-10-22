"""Samsung IP Control (local JSON-RPC over HTTPS, port 1516; 1515 on older TVs).

Used read-only for the observed input source, explicit power state and audio.
The TV must have "IP Remote" enabled and a token from a one-time on-screen
pairing. Calls are blocking (http.client) and run in Home Assistant's executor:
creating the TLS context reads certificate files from disk. Request format and
TLS fallback match what was verified on a QN55LS03T (2020 Frame).

Protocol notes from TheFab21/ha-samsungtv-smart IP_Control_Protocol_Reference.md
(Apache-2.0) and measurements on the user's TV.
"""

from __future__ import annotations

import http.client
import json
import socket
import ssl
import threading

PORTS = (1516, 1515)
TIMEOUT = 5
PAIR_TIMEOUT = 40

UNAUTHORIZED = -32010
PARSE_ERROR = -32700  # Some firmware answers a stale token with this, flat form.
METHOD_NOT_FOUND = -32601


class IpcError(Exception):
    """Sanitized failure; never includes the token, address or response body.

    code: refused | timeout | cannot_connect | tls | unauthorized |
          unsupported | failed | invalid_response
    """

    def __init__(self, code, rpc_code=None):
        self.code, self.rpc_code = code, rpc_code
        super().__init__(code)


class IpControl:
    def __init__(self, host, port, token=None):
        self.host, self.port, self.token = host, port, token
        self._legacy_tls = False
        self._context = None
        # The TV serves one connection at a time on this port.
        self._lock = threading.Lock()

    def _ssl_context(self):
        if self._context is None:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE  # the TV's certificate is self-signed
            if self._legacy_tls:
                # ~2020 Frames can negotiate a DH group OpenSSL rejects by default.
                context.set_ciphers("DEFAULT@SECLEVEL=0")
            self._context = context
        return self._context

    def _post(self, payload, timeout):
        for attempt in (0, 1):
            conn = http.client.HTTPSConnection(
                self.host, self.port, timeout=timeout, context=self._ssl_context()
            )
            try:
                # Minimal headers; some firmware rejects requests with extras.
                conn.putrequest("POST", "/", skip_accept_encoding=True)
                conn.putheader("Accept", "application/json")
                conn.putheader("Content-Type", "application/json")
                conn.putheader("Content-Length", str(len(payload)))
                conn.endheaders()
                conn.send(payload)
                return conn.getresponse().read(64 * 1024)
            except ssl.SSLError as err:
                if (
                    attempt == 0
                    and not self._legacy_tls
                    and "dh key" in str(err).lower()
                ):
                    self._legacy_tls, self._context = True, None
                    continue
                raise IpcError("tls") from None
            except ConnectionRefusedError:
                raise IpcError("refused") from None
            except (TimeoutError, socket.timeout):
                raise IpcError("timeout") from None
            except OSError:
                raise IpcError("cannot_connect") from None
            finally:
                conn.close()
        raise IpcError("tls")

    def call(self, method, params=None, *, timeout=TIMEOUT, auth=True):
        """Blocking JSON-RPC call; returns the result dict or raises IpcError."""
        body = {"jsonrpc": "2.0", "id": 1, "method": method}
        if auth:
            if not self.token:
                raise IpcError("unauthorized")
            body["params"] = {"AccessToken": self.token, **(params or {})}
        elif params:
            body["params"] = params
        with self._lock:
            raw = self._post(json.dumps(body).encode(), timeout)
        try:
            data = json.loads(raw)
        except ValueError:
            raise IpcError("invalid_response") from None
        if not isinstance(data, dict):
            raise IpcError("invalid_response")
        error = data.get("error")
        if error is None and "code" in data and "result" not in data:
            error = data  # flat error form
        if error is not None:
            code = error.get("code") if isinstance(error, dict) else None
            if code == UNAUTHORIZED or (code == PARSE_ERROR and auth):
                raise IpcError("unauthorized", code)
            if code == METHOD_NOT_FOUND:
                raise IpcError("unsupported", code)
            raise IpcError("failed", code)
        result = data.get("result")
        return result if isinstance(result, dict) else {}

    def pair(self):
        """Show the TV's authorization prompt; return the token once accepted."""
        result = self.call("createAccessToken", timeout=PAIR_TIMEOUT, auth=False)
        token = result.get("AccessToken")
        if not isinstance(token, str) or not token:
            raise IpcError("invalid_response")
        self.token = token
        return token

    def read_state(self):
        """Power, the TV state snapshot and, on the TV source, the channel.

        directChannelControl answers -32002 when no channel is shown (e.g. on
        HDMI), so it is only asked on the TV source. Measured on a QN55LS03T on
        Samsung TV Plus: {"atvDtv": "TVPLUS", "channelNum": "2110", ...}.
        """
        power = self.call("powerControl").get("power")
        states = dict(self.call("getTVStates")) if power == "powerOn" else {}
        if power == "powerOn":
            # Art Mode is polled here because changes made with the TV remote
            # otherwise only show at the next full Art refresh (up to 60 s).
            try:
                art = self.call("artModeControl").get("artMode")
            except IpcError as err:
                if err.code not in ("failed", "unsupported"):
                    raise
            else:
                if art in ("artModeOn", "artModeOff"):
                    states["artMode"] = "on" if art == "artModeOn" else "off"
        if str(states.get("inputSource", "")).upper() == "TV":
            try:
                channel = self.call("directChannelControl")
            except IpcError as err:
                if err.code not in ("failed", "unsupported"):
                    raise
            else:
                number = str(channel.get("channelNum") or "").strip()
                if number:
                    states["channel"] = {
                        "number": number,
                        "kind": str(channel.get("atvDtv") or "").lower(),
                    }
        return power, states
