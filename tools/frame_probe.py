#!/usr/bin/env python3
"""Read-only probe of a Samsung Frame's local interfaces. Standard library only.

Checks what your TV actually answers before the integration relies on it:
  * REST device info / PowerState      (ports 8001/8002, already used)
  * UPnP RenderingControl volume/mute  (port 9197, used since 2.0.0)
  * Samsung IP Control JSON-RPC        (port 1516, or 1515 on older TVs)
    -> current input source, Art Mode flag, volume, picture mode

check, pair and watch never change a TV setting: every IP Control call they
make is a getter. `pair` shows an authorization prompt on screen.

`thumbnail` is read-only too: it asks the Art channel which artwork is showing
and saves that piece's thumbnail next to this script, to check whether the
media player can show it:
  python frame_probe.py thumbnail 192.168.1.50            current artwork
  python frame_probe.py thumbnail 192.168.1.50 MY_F0001   a specific piece
The first Art channel connection may show an "allow Frame Probe" prompt.
`slideshow` is read-only as well: it asks for the slideshow status both ways
the integration does, waits up to 10 s each and records every Art message:
  python frame_probe.py slideshow 192.168.1.50

These commands DO act on the TV, to find out what works on your firmware:
  python frame_probe.py try-input 192.168.1.50 HDMI2   try each input method
  python frame_probe.py try-power-off 192.168.1.50     IP Control power-off
  python frame_probe.py try-channel 192.168.1.50 2111  tune a channel on Samsung TV
  python frame_probe.py time-channel 192.168.1.50 2111 --gap 0.3 --enter
                                                       time typed digits (+ Enter)
  python frame_probe.py hold-channel 192.168.1.50 1114,2110,1114,2110 --idle 10,60,300
                                                       one remote connection kept open
  python frame_probe.py key 192.168.1.50 KEY_SOURCE    send one remote key
Remote keys use the TV's remote channel; the first one shows an "allow
Frame Probe" prompt on the TV.

Usage (Python 3.10+):
  python frame_probe.py check 192.168.1.50     one-shot report
  python frame_probe.py pair  192.168.1.50     one-time IP Control pairing
  python frame_probe.py watch 192.168.1.50     print changes while you use the remote

Before `pair`: on the TV enable Settings > All Settings > Connections > Network >
Expert Settings > IP Remote (the menu path varies by firmware), turn the TV on
in normal viewing, NOT Art Mode, then run pair and accept the prompt.

The token is saved next to this script in frame_ipcontrol_token.json. Treat it
like a password. Reports redact the token, MAC addresses and device IDs.
"""

from __future__ import annotations

import argparse
import base64
import http.client
import json
import os
import socket
import ssl
import sys
import time
import urllib.parse
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOKEN_FILE = HERE / "frame_ipcontrol_token.json"
REPORT_FILE = HERE / "frame_probe_report.json"
WATCH_LOG = HERE / "frame_probe_watch.jsonl"
THUMB_REPORT = HERE / "frame_probe_thumbnail.json"
SLIDESHOW_REPORT = HERE / "frame_probe_slideshow.json"

REST_PORTS = ((8002, "https"), (8001, "http"))
UPNP_PORT = 9197
IPC_PORTS = (1516, 1515)
TIMEOUT = 5
PAIR_TIMEOUT = 40

# Getter-only IP Control calls (no parameters besides the token).
IPC_READS = (
    "powerControl",
    "artModeControl",
    "getTVStates",
    "directVolumeControl",
    "muteControl",
    "getDeviceInformation",
    "directChannelControl",
    "directAccessControl",
)
ERRORS = {
    -32000: "unknown error",
    -32001: "not supported",
    -32002: "failed (often transient)",
    -32003: "invalid operation (on some models: method unusable)",
    -32010: "unauthorized: token invalid, pair again",
    -32601: "method not found (or not available in the current mode, e.g. Art Mode)",
    -32602: "invalid params",
    -32700: "parse error (on some firmware: stale token)",
}
REDACT = {"wifiMac", "mac", "id", "duid", "udn", "ip", "AccessToken", "token"}


def redact(value):
    if isinstance(value, dict):
        return {
            k: ("<redacted>" if k in REDACT else redact(v)) for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def tls_context(legacy=False):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # the TV's certificate is self-signed
    if legacy:
        # Some ~2020 Frames negotiate a DH group OpenSSL rejects by default.
        ctx.set_ciphers("DEFAULT@SECLEVEL=0")
    return ctx


def port_open(host, port):
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


# -- REST ---------------------------------------------------------------------


def rest_info(host):
    for port, scheme in REST_PORTS:
        try:
            if scheme == "https":
                conn = http.client.HTTPSConnection(
                    host, port, timeout=TIMEOUT, context=tls_context()
                )
            else:
                conn = http.client.HTTPConnection(host, port, timeout=TIMEOUT)
            try:
                conn.request("GET", "/api/v2/")
                response = conn.getresponse()
                body = response.read()
            finally:
                conn.close()
            if response.status != 200:
                continue
            data = json.loads(body)
            device = data.get("device", {}) if isinstance(data, dict) else {}
            return {
                "port": port,
                "PowerState": device.get("PowerState", "<not reported>"),
                "model": device.get("modelName"),
                "FrameTVSupport": device.get("FrameTVSupport"),
                "firmware": device.get("firmwareVersion")
                or device.get("FirmwareVersion"),
                "OS": device.get("OS"),
                "device": redact(device),
            }
        except (OSError, ValueError, ssl.SSLError):
            continue
    return {"error": "no answer on 8002 or 8001"}


# -- UPnP ---------------------------------------------------------------------


def upnp(host, action):
    service = "urn:schemas-upnp-org:service:RenderingControl:1"
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        f'<s:Body><u:{action} xmlns:u="{service}"><InstanceID>0</InstanceID>'
        f"<Channel>Master</Channel></u:{action}></s:Body></s:Envelope>"
    ).encode()
    conn = http.client.HTTPConnection(host, UPNP_PORT, timeout=TIMEOUT)
    try:
        conn.request(
            "POST",
            "/upnp/control/RenderingControl1",
            body,
            {
                "SOAPAction": f'"{service}#{action}"',
                "Content-Type": 'text/xml; charset="utf-8"',
            },
        )
        response = conn.getresponse()
        text = response.read()
    finally:
        conn.close()
    if response.status != 200:
        return {"error": f"HTTP {response.status}"}
    field = "CurrentVolume" if action == "GetVolume" else "CurrentMute"
    for element in ET.fromstring(text).iter():
        if element.tag.rsplit("}", 1)[-1] == field:
            return {"value": (element.text or "").strip()}
    return {"error": f"no {field} in response"}


def upnp_audio(host):
    result = {}
    for action in ("GetVolume", "GetMute"):
        try:
            result[action] = upnp(host, action)
        except (OSError, ET.ParseError) as err:
            result[action] = {"error": type(err).__name__}
    return result


# -- IP Control ---------------------------------------------------------------


class IpControl:
    def __init__(self, host, port, token=None):
        self.host, self.port, self.token = host, port, token
        self.legacy = False

    def call(self, method, timeout=TIMEOUT, auth=True, params=None):
        body = {"jsonrpc": "2.0", "id": 1, "method": method}
        if auth:
            body["params"] = {"AccessToken": self.token, **(params or {})}
        payload = json.dumps(body).encode()
        for attempt in (0, 1):
            conn = http.client.HTTPSConnection(
                self.host, self.port, timeout=timeout, context=tls_context(self.legacy)
            )
            try:
                # Minimal headers; some firmware rejects extra ones.
                conn.putrequest("POST", "/", skip_accept_encoding=True)
                conn.putheader("Accept", "application/json")
                conn.putheader("Content-Type", "application/json")
                conn.putheader("Content-Length", str(len(payload)))
                conn.endheaders()
                conn.send(payload)
                raw = conn.getresponse().read()
                break
            except ssl.SSLError as err:
                if attempt == 0 and not self.legacy and "dh key" in str(err).lower():
                    self.legacy = True
                    continue
                return {"error": f"TLS: {err.reason or err}"}
            except (TimeoutError, socket.timeout):
                return {"error": "timeout (no answer)"}
            except OSError as err:
                return {"error": f"{type(err).__name__}: {err.strerror or err}"}
            finally:
                conn.close()
        try:
            data = json.loads(raw)
        except ValueError:
            return {
                "error": "non-JSON response",
                "raw": raw[:200].decode(errors="replace"),
            }
        error = data.get("error")
        if error is None and "code" in data and "result" not in data:
            error = data  # flat error form used by some firmware
        if error is not None:
            code = error.get("code") if isinstance(error, dict) else None
            return {
                "error": f"{code}: {ERRORS.get(code, error.get('message', 'error'))}",
                "code": code,
            }
        return {"result": data.get("result")}


def find_ipc_port(host):
    for port in IPC_PORTS:
        if port_open(host, port):
            return port
    return None


def load_saved():
    try:
        saved = json.loads(TOKEN_FILE.read_text())
    except (OSError, ValueError):
        return {}
    return saved if isinstance(saved, dict) else {}


def load_token(host):
    try:
        saved = json.loads(TOKEN_FILE.read_text())
    except (OSError, ValueError):
        return None, None
    if saved.get("host") != host:
        return None, None
    return saved.get("token"), saved.get("port")


def ipc_reads(client):
    return {method: redact(client.call(method)) for method in IPC_READS}


# -- commands -----------------------------------------------------------------


def cmd_check(host):
    report = {
        "probe_version": 1,
        "time": datetime.now().isoformat(timespec="seconds"),
        "ports": {p: port_open(host, p) for p in (8001, 8002, UPNP_PORT, *IPC_PORTS)},
    }
    print(f"\nSamsung Frame probe: {host}\n")
    print(
        "Open ports:",
        ", ".join(str(p) for p, ok in report["ports"].items() if ok) or "none",
    )

    report["rest"] = rest_info(host)
    rest = report["rest"]
    if "error" in rest:
        print("REST device info: ", rest["error"])
    else:
        print(
            f"REST device info:  port {rest['port']}, model {rest['model']}, "
            f"PowerState {rest['PowerState']}, Frame {rest['FrameTVSupport']}"
        )

    report["upnp"] = upnp_audio(host)
    vol, mute = report["upnp"]["GetVolume"], report["upnp"]["GetMute"]
    print(
        f"UPnP volume/mute:  {vol.get('value', vol.get('error'))} / "
        f"{mute.get('value', mute.get('error'))}"
    )

    port = find_ipc_port(host)
    token, saved_port = load_token(host)
    report["ip_control"] = {"port": port, "paired": bool(token)}
    if port is None:
        print(
            "IP Control:        port 1516/1515 closed. Enable IP Remote on the TV "
            "(Connections > Network > Expert Settings)."
        )
    elif not token:
        print(
            f"IP Control:        port {port} open, not paired yet. "
            f"Run: python {Path(__file__).name} pair {host}"
        )
    else:
        client = IpControl(host, saved_port or port, token)
        reads = ipc_reads(client)
        report["ip_control"]["reads"] = reads
        print(f"IP Control:        port {client.port}, paired")
        for method, answer in reads.items():
            shown = answer.get("result", answer.get("error"))
            print(f"  {method:22} {json.dumps(shown)}")
        states = reads["getTVStates"].get("result") or {}
        art = (reads["artModeControl"].get("result") or {}).get("artMode")
        if "inputSource" in states:
            print(f"\n  -> Current input reported locally: {states['inputSource']!r}")
            if art == "artModeOn" or states.get("pictureMode") == "Ambient":
                print(
                    "     (Art Mode is on: Frames report 'TV' here while showing "
                    "art. Switch to an HDMI input and check again.)"
                )
        if any(a.get("code") in (-32010, -32700) for a in reads.values()):
            print("\n  Token rejected: run pair again.")

    REPORT_FILE.write_text(json.dumps(report, indent=2))
    print(f"\nReport saved (no token/MAC/IDs): {REPORT_FILE}")
    return 0


def cmd_pair(host):
    port = find_ipc_port(host)
    if port is None:
        print(
            "Port 1516/1515 is closed. On the TV enable IP Remote "
            "(Settings > Connections > Network > Expert Settings), then retry."
        )
        return 1
    print(f"IP Control answers on port {port}.")
    print("Make sure the TV is ON and in normal viewing (NOT Art Mode).")
    print(f"Accept the prompt on the TV within {PAIR_TIMEOUT} seconds...")
    answer = IpControl(host, port).call(
        "createAccessToken", timeout=PAIR_TIMEOUT, auth=False
    )
    token = (answer.get("result") or {}).get("AccessToken")
    if not token:
        print("Pairing failed:", answer.get("error", answer))
        if "timeout" in str(answer.get("error", "")):
            print(
                "A timeout usually means the TV was in Art Mode or the prompt "
                "was not accepted."
            )
        return 1
    saved = load_saved()
    saved.update(host=host, port=port, token=token)
    TOKEN_FILE.write_text(json.dumps(saved))
    print(f"Paired. Token saved to {TOKEN_FILE} (keep it private).")
    print(f"Now run: python {Path(__file__).name} check {host}")
    return 0


def snapshot(host, client):
    rest = rest_info(host)
    snap = {"PowerState": rest.get("PowerState", rest.get("error"))}
    audio = upnp_audio(host)
    snap["upnp_volume"] = audio["GetVolume"].get(
        "value", audio["GetVolume"].get("error")
    )
    snap["upnp_mute"] = audio["GetMute"].get("value", audio["GetMute"].get("error"))
    if client:
        for method, keys in (
            ("powerControl", ("power",)),
            ("artModeControl", ("artMode",)),
            ("getTVStates", ("inputSource", "pictureMode", "volume", "mute")),
        ):
            answer = client.call(method)
            result = answer.get("result")
            for key in keys:
                snap[f"ipc_{key}"] = (
                    result.get(key) if isinstance(result, dict) else answer.get("error")
                )
    return snap


def cmd_watch(host, interval):
    token, saved_port = load_token(host)
    client = (
        IpControl(host, saved_port or find_ipc_port(host) or 1516, token)
        if token
        else None
    )
    if not client:
        print("Not paired for IP Control: watching REST and UPnP only.")
    print(
        f"Polling every {interval}s. Change inputs, enter/leave Art Mode, change "
        "volume and power off/on with the remote. Ctrl+C to stop.\n"
    )
    previous = {}
    with WATCH_LOG.open("a") as log:
        try:
            while True:
                snap = snapshot(host, client)
                changed = {k: v for k, v in snap.items() if previous.get(k) != v}
                if changed:
                    stamp = datetime.now().strftime("%H:%M:%S")
                    print(stamp, "  ".join(f"{k}={v}" for k, v in changed.items()))
                    log.write(json.dumps({"time": stamp, **snap}) + "\n")
                    log.flush()
                previous = snap
                time.sleep(interval)
        except KeyboardInterrupt:
            print(f"\nStopped. Log saved to {WATCH_LOG}")
    return 0


# -- remote keys (minimal WebSocket client, standard library only) -----------


class RemoteError(Exception):
    pass


class Remote:
    """Samsung remote channel on wss://host:8002, matching samsungtvws."""

    def __init__(self, host):
        self.host = host
        self.sock = None
        self.buffer = b""

    def _recv_exact(self, size):
        while len(self.buffer) < size:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RemoteError("connection closed by the TV")
            self.buffer += chunk
        data, self.buffer = self.buffer[:size], self.buffer[size:]
        return data

    def _read_frame(self):
        first, second = self._recv_exact(2)
        opcode, length = first & 0x0F, second & 0x7F
        if length == 126:
            length = int.from_bytes(self._recv_exact(2), "big")
        elif length == 127:
            length = int.from_bytes(self._recv_exact(8), "big")
        mask = self._recv_exact(4) if second & 0x80 else None
        payload = self._recv_exact(length)
        if mask:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return opcode, payload

    def _send_frame(self, payload, opcode=1):
        mask = os.urandom(4)
        header = bytes([0x80 | opcode])
        if len(payload) < 126:
            header += bytes([0x80 | len(payload)])
        elif len(payload) < 65536:
            header += bytes([0x80 | 126]) + len(payload).to_bytes(2, "big")
        else:
            header += bytes([0x80 | 127]) + len(payload).to_bytes(8, "big")
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def connect(self, token, wait=40, channel="samsung.remote.control"):
        query = {"name": base64.b64encode(b"Frame Probe").decode()}
        if token:
            query["token"] = token
        raw = socket.create_connection((self.host, 8002), timeout=wait)
        self.sock = tls_context().wrap_socket(raw)
        key = base64.b64encode(os.urandom(16)).decode()
        path = f"/api/v2/channels/{channel}?" + urllib.parse.urlencode(query)
        self.sock.sendall(
            (
                f"GET {path} HTTP/1.1\r\nHost: {self.host}:8002\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        while b"\r\n\r\n" not in self.buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RemoteError("no WebSocket upgrade response")
            self.buffer += chunk
        head, self.buffer = self.buffer.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise RemoteError("upgrade refused: " + head.split(b"\r\n", 1)[0].decode())
        # The Art channel also sends ms.channel.ready before it takes requests.
        need_ready = channel != "samsung.remote.control"
        connected = False
        while True:  # wait for the user to allow the prompt on first use
            opcode, payload = self._read_frame()
            if opcode == 8:
                raise RemoteError("closed by the TV during handshake")
            if opcode != 1:
                continue
            event = json.loads(payload)
            name = event.get("event")
            if name == "ms.channel.unauthorized":
                raise RemoteError("not allowed on the TV (unauthorized)")
            if name in ("ms.channel.timeOut", "ms.channel.disconnect"):
                raise RemoteError(f"the TV ended the connection ({name})")
            if name == "ms.channel.connect":
                connected = True
                token = (event.get("data") or {}).get("token") or token
            elif name == "ms.channel.ready":
                need_ready = False
            if connected and not need_ready:
                self.sock.settimeout(TIMEOUT * 3)
                return token

    def art_collect(self, request, seconds=10.0, **params):
        """Send one Art request; record every Art message for up to `seconds`.

        Stops early once a reply carrying this request's id arrives.
        """
        request_id = str(uuid.uuid4())
        data = {"request": request, "id": request_id, "request_id": request_id}
        data.update(params)
        message = {
            "method": "ms.channel.emit",
            "params": {
                "event": "art_app_request",
                "to": "host",
                "data": json.dumps(data),
            },
        }
        started = time.monotonic()
        self._send_frame(json.dumps(message).encode())
        seen = []
        try:
            while True:
                left = seconds - (time.monotonic() - started)
                if left <= 0:
                    break
                self.sock.settimeout(left)
                try:
                    opcode, payload = self._read_frame()
                except (TimeoutError, socket.timeout, ssl.SSLError):
                    break
                if opcode == 8:
                    raise RemoteError("closed by the TV")
                if opcode == 9:
                    self._send_frame(payload, opcode=10)
                    continue
                if opcode not in (1, 2):
                    continue
                frame, _ = split_json_tail(payload)
                if not isinstance(frame, dict):
                    continue
                body = frame.get("data")
                if isinstance(body, str):
                    try:
                        body = json.loads(body)
                    except ValueError:
                        pass
                ours = (
                    isinstance(body, dict)
                    and (body.get("request_id") or body.get("id")) == request_id
                )
                seen.append(
                    {
                        "after_s": round(time.monotonic() - started, 2),
                        "event": frame.get("event"),
                        "ours": ours,
                        "data": redact(body),
                    }
                )
                if ours:
                    break
        finally:
            self.sock.settimeout(TIMEOUT * 3)
        return seen

    def art_request(self, request, **params):
        """One Art channel request; returns (reply data, binary tail or None)."""
        request_id = str(uuid.uuid4())
        data = {"request": request, "id": request_id, "request_id": request_id}
        data.update(params)
        message = {
            "method": "ms.channel.emit",
            "params": {
                "event": "art_app_request",
                "to": "host",
                "data": json.dumps(data),
            },
        }
        self._send_frame(json.dumps(message).encode())
        while True:
            opcode, payload = self._read_frame()
            if opcode == 8:
                raise RemoteError("closed by the TV")
            if opcode == 9:  # ping
                self._send_frame(payload, opcode=10)
                continue
            if opcode not in (1, 2):
                continue
            frame, tail = split_json_tail(payload)
            if not isinstance(frame, dict):
                continue
            if frame.get("event") != "d2d_service_message":
                continue
            reply = frame.get("data")
            if isinstance(reply, str):
                try:
                    reply = json.loads(reply)
                except ValueError:
                    continue
            if not isinstance(reply, dict):
                continue
            if (reply.get("request_id") or reply.get("id")) != request_id:
                continue
            if reply.get("event") == "error":
                raise RemoteError(
                    f"{request} refused by the TV: "
                    + str(reply.get("error_code") or reply.get("error") or reply)
                )
            return reply, tail

    def idle(self, seconds, ping_every=30.0):
        """Keep the connection open, answering pings and sending our own.

        Returns what happened: whether the TV closed it, and when.
        """
        report = {"seconds": seconds, "pings_sent": 0, "pongs": 0, "events": []}
        start = time.monotonic()
        next_ping = start + ping_every
        try:
            while (now := time.monotonic()) - start < seconds:
                if now >= next_ping:
                    self._send_frame(b"probe", opcode=9)
                    report["pings_sent"] += 1
                    next_ping = now + ping_every
                self.sock.settimeout(min(1.0, max(0.05, seconds - (now - start))))
                try:
                    opcode, payload = self._read_frame()
                except (TimeoutError, socket.timeout):
                    continue
                if opcode == 8:
                    raise RemoteError("TV sent close")
                if opcode == 9:
                    self._send_frame(payload, opcode=10)
                elif opcode == 10:
                    report["pongs"] += 1
                elif opcode == 1:
                    try:
                        event = json.loads(payload).get("event")
                    except ValueError:
                        event = "non-JSON text"
                    report["events"].append(
                        {"after_s": round(time.monotonic() - start, 1), "event": event}
                    )
                    if event in ("ms.channel.timeOut", "ms.channel.disconnect"):
                        raise RemoteError(event)
        except (RemoteError, OSError, ssl.SSLError) as err:
            report["closed"] = f"{type(err).__name__}: {err}"
            report["closed_after_s"] = round(time.monotonic() - start, 1)
        finally:
            if self.sock:
                self.sock.settimeout(TIMEOUT * 3)
        return report

    def send_key(self, key, hold_open=1.0):
        payload = {
            "method": "ms.remote.control",
            "params": {
                "Cmd": "Click",
                "DataOfCmd": key,
                "Option": "false",
                "TypeOfRemote": "SendRemoteKey",
            },
        }
        self._send_frame(json.dumps(payload).encode())
        time.sleep(hold_open)  # like samsungtvws: let the TV act before closing

    def close(self):
        if self.sock:
            try:
                self._send_frame(b"\x03\xe8", opcode=8)
            except OSError:
                pass
            self.sock.close()


def send_keys(host, keys, pause=0.0, after_each=None):
    """Send keys over one connection; saves the remote token for next time."""
    saved = load_saved()
    remote = Remote(host)
    try:
        print(
            "Connecting to the remote channel (allow 'Frame Probe' on the TV if asked)..."
        )
        token = remote.connect(
            saved.get("remote_token") if saved.get("host") == host else None
        )
        if token and token != saved.get("remote_token"):
            saved.update(host=host, remote_token=token)
            TOKEN_FILE.write_text(json.dumps(saved))
        for key in keys:
            remote.send_key(key)
            if after_each and after_each(key):
                break
            time.sleep(pause)
    finally:
        remote.close()


# -- artwork thumbnail ----------------------------------------------------------


def split_json_tail(payload):
    """A frame is JSON, or (older Art API) JSON followed by image bytes."""
    try:
        return json.loads(payload.decode("utf-8")), None
    except (UnicodeDecodeError, ValueError):
        pass
    start = payload.find(b"{")
    if start < 0:
        return None, None
    try:
        # latin-1 keeps one character per byte, so the end index is a byte offset.
        frame, end = json.JSONDecoder().raw_decode(payload[start:].decode("latin-1"))
    except ValueError:
        return None, None
    return frame, payload[start + end :] or None


def image_info(data):
    """(type, width, height) from PNG/JPEG headers; None if not recognised."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return (
            "png",
            int.from_bytes(data[16:20], "big"),
            int.from_bytes(data[20:24], "big"),
        )
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7 or marker == 0xFF:
                i += 1 if marker == 0xFF else 2
                continue
            length = int.from_bytes(data[i + 2 : i + 4], "big")
            if marker in (
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            ):
                height = int.from_bytes(data[i + 5 : i + 7], "big")
                width = int.from_bytes(data[i + 7 : i + 9], "big")
                return "jpeg", width, height
            i += 2 + length
        return "jpeg", None, None
    return None


def recv_exact(sock, size):
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(min(65536, size - len(data)))
        if not chunk:
            raise RemoteError(
                f"image connection closed after {len(data)} of {size} bytes"
            )
        data += chunk
    return bytes(data)


def d2d_download(host, reply):
    """Fetch the file the TV offers on its one-off local socket (D2D)."""
    info = reply.get("conn_info") or {}
    if isinstance(info, str):
        info = json.loads(info)
    ip, port = info.get("ip") or host, int(info["port"])
    raw = socket.create_connection((ip, port), timeout=TIMEOUT * 2)
    sock = tls_context().wrap_socket(raw) if info.get("secured") else raw
    try:
        header_len = int.from_bytes(recv_exact(sock, 4), "big")
        header = json.loads(recv_exact(sock, header_len))
        data = recv_exact(sock, int(header["fileLength"]))
    finally:
        sock.close()
    details = {
        "secured": bool(info.get("secured")),
        "same_ip_as_tv": ip == host,
        "file_type": header.get("fileType"),
        "file_id": header.get("fileID"),
        "num": header.get("num"),
        "total": header.get("total"),
    }
    return data, details


def cmd_thumbnail(host, content_id=None):
    saved = load_saved()
    art = Remote(host)
    report = {"probe_version": 1, "time": datetime.now().isoformat(timespec="seconds")}
    started = time.monotonic()
    try:
        print(
            "Connecting to the Art channel (allow 'Frame Probe' on the TV if asked)..."
        )
        token = art.connect(
            saved.get("remote_token") if saved.get("host") == host else None,
            channel="com.samsung.art-app",
        )
        if token and token != saved.get("remote_token"):
            saved.update(host=host, remote_token=token)
            TOKEN_FILE.write_text(json.dumps(saved))
        report["connect_s"] = round(time.monotonic() - started, 2)
        if not content_id:
            current, _ = art.art_request("get_current_artwork")
            content_id = current.get("content_id")
            report["current_artwork"] = {
                k: current.get(k)
                for k in ("content_id", "matte_id", "portrait_matte_id")
            }
            if not content_id:
                print("The TV did not report a current artwork (is Art Mode on?).")
                report["result"] = "no current artwork"
                THUMB_REPORT.write_text(json.dumps(report, indent=2))
                return 1
        report["content_id"] = content_id
        print(f"Asking for the thumbnail of {content_id}...")
        attempts = []
        data = None
        for request, params in (
            ("get_thumbnail", {"content_id": content_id}),
            ("get_thumbnail_list", {"content_id_list": [{"content_id": content_id}]}),
        ):
            t0 = time.monotonic()
            attempt = {"request": request}
            attempts.append(attempt)
            conn_id = int.from_bytes(os.urandom(4), "big")
            try:
                reply, tail = art.art_request(
                    request,
                    conn_info={
                        "d2d_mode": "socket",
                        "connection_id": conn_id,
                        "id": str(uuid.uuid4()),
                    },
                    **params,
                )
                if tail:
                    data, attempt["delivery"] = tail, "inline (older Art API)"
                else:
                    data, attempt["d2d"] = d2d_download(host, reply)
                    attempt["delivery"] = "separate local socket (D2D)"
            except (RemoteError, OSError, ValueError, KeyError) as err:
                attempt["error"] = f"{type(err).__name__}: {err}"
                print(f"  {request}: failed ({attempt['error']})")
                continue
            finally:
                attempt["took_s"] = round(time.monotonic() - t0, 2)
            break
    except (RemoteError, OSError, ValueError) as err:
        report["result"] = f"failed: {type(err).__name__}: {err}"
        print(report["result"])
        THUMB_REPORT.write_text(json.dumps(report, indent=2))
        return 1
    finally:
        art.close()
    report["attempts"] = attempts
    if not data:
        report["result"] = "no thumbnail"
        print("The TV did not return a thumbnail for this piece.")
        THUMB_REPORT.write_text(json.dumps(report, indent=2))
        return 1
    kind = image_info(data)
    ext = kind[0] if kind else "bin"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in content_id)
    out = HERE / f"frame_thumbnail_{safe}.{'jpg' if ext == 'jpeg' else ext}"
    out.write_bytes(data)
    report.update(
        result="ok",
        bytes=len(data),
        image_type=ext,
        width=kind[1] if kind else None,
        height=kind[2] if kind else None,
        saved_as=out.name,
        total_s=round(time.monotonic() - started, 2),
    )
    THUMB_REPORT.write_text(json.dumps(report, indent=2))
    size = (
        f"{report['width']}x{report['height']}" if report["width"] else "unknown size"
    )
    print(
        f"Saved {out.name}: {ext}, {size}, {len(data) // 1024} KB, "
        f"via {attempts[-1]['delivery']} in {report['total_s']} s."
    )
    print(f"Report: {THUMB_REPORT.name} (open the image to check it looks right).")
    return 0


def cmd_slideshow(host):
    saved = load_saved()
    art = Remote(host)
    report = {"probe_version": 1, "time": datetime.now().isoformat(timespec="seconds")}
    try:
        print(
            "Connecting to the Art channel (allow 'Frame Probe' on the TV if asked)..."
        )
        token = art.connect(
            saved.get("remote_token") if saved.get("host") == host else None,
            channel="com.samsung.art-app",
        )
        if token and token != saved.get("remote_token"):
            saved.update(host=host, remote_token=token)
            TOKEN_FILE.write_text(json.dumps(saved))
        for request in (
            "get_artmode_status",
            "get_slideshow_status",
            "get_auto_rotation_status",
        ):
            print(f"{request}: waiting up to 10 s...")
            seen = art.art_collect(request)
            reply = next((m for m in seen if m["ours"]), None)
            report[request] = {
                "reply": reply,
                "other_messages": [m for m in seen if not m["ours"]],
            }
            if reply:
                print(
                    f"  answered after {reply['after_s']} s: {json.dumps(reply['data'])}"
                )
            else:
                print(f"  no reply with our id ({len(seen)} other Art messages)")
    except (RemoteError, OSError, ValueError) as err:
        report["result"] = f"failed: {type(err).__name__}: {err}"
        print(report["result"])
        return 1
    finally:
        art.close()
        SLIDESHOW_REPORT.write_text(json.dumps(report, indent=2))
    print(f"Report: {SLIDESHOW_REPORT.name}")
    return 0


def ipc_client(host):
    token, port = load_token(host)
    if not token:
        print(f"Pair IP Control first: python {Path(__file__).name} pair {host}")
        return None
    return IpControl(host, port or find_ipc_port(host) or 1516, token)


def current_input(client):
    answer = client.call("getTVStates")
    result = answer.get("result")
    return (
        result.get("inputSource") if isinstance(result, dict) else answer.get("error")
    )


def wait_for_input(client, target, seconds=6):
    deadline = time.monotonic() + seconds
    seen = current_input(client)
    while seen != target and time.monotonic() < deadline:
        time.sleep(1.5)
        seen = current_input(client)
    return seen


def cmd_try_input(host, target):
    client = ipc_client(host)
    if not client:
        return 1
    target = target.upper().replace(" ", "")
    art = (client.call("artModeControl").get("result") or {}).get("artMode")
    if art == "artModeOn":
        print("Leave Art Mode first (normal viewing), then run this again.")
        return 1
    start = current_input(client)
    print(f"Current input: {start}. Target: {target}\n")
    if start == target:
        print("Already on the target; pick another input to test.")
        return 1
    results = {"start": start, "target": target}

    print("1. IP Control inputSourceControl ...")
    answer = client.call("inputSourceControl", params={"inputSource": target})
    seen = (
        wait_for_input(client, target) if "result" in answer else current_input(client)
    )
    results["ip_control_setter"] = {"answer": answer, "input_after": seen}
    print(f"   answer: {answer.get('result', answer.get('error'))}; input now: {seen}")
    if seen == target:
        return report_input_test(results, "ip_control_setter")

    direct = "KEY_TV" if target == "TV" else f"KEY_{target}"
    print(f"2. Remote key {direct} ...")
    try:
        send_keys(host, [direct])
        seen = wait_for_input(client, target)
        results["direct_key"] = {"key": direct, "input_after": seen}
    except (RemoteError, OSError) as err:
        results["direct_key"] = {"key": direct, "error": str(err)}
        print(f"   remote channel failed: {err}")
        return report_input_test(results, None)
    print(f"   input now: {seen}")
    if seen == target:
        return report_input_test(results, "direct_key")

    print("3. Remote key KEY_HDMI pressed repeatedly (cycles inputs on some TVs) ...")
    sequence = []

    def check(_key):
        time.sleep(3)
        sequence.append(current_input(client))
        print(f"   after press {len(sequence)}: {sequence[-1]}")
        return sequence[-1] == target

    try:
        send_keys(host, ["KEY_HDMI"] * 5, after_each=check)
    except (RemoteError, OSError) as err:
        results["hdmi_cycle"] = {"error": str(err)}
        return report_input_test(results, None)
    results["hdmi_cycle"] = {"inputs_after_each_press": sequence}
    return report_input_test(results, "hdmi_cycle" if target in sequence else None)


def current_channel(client):
    answer = client.call("directChannelControl")
    result = answer.get("result")
    if isinstance(result, dict):
        return str(result.get("channelNum") or "").strip(), result
    return None, answer.get("error")


def wait_for_channel(client, target, seconds=8):
    deadline = time.monotonic() + seconds
    seen, _ = current_channel(client)
    while seen != target and time.monotonic() < deadline:
        time.sleep(1.5)
        seen, _ = current_channel(client)
    return seen


def cmd_try_channel(host, target):
    client = ipc_client(host)
    if not client:
        return 1
    target = str(target).strip()
    if not target.isdigit():
        print("Give a channel number, for example 2111.")
        return 1
    if current_input(client) != "TV":
        print("Switch the TV to the Samsung TV source (normal viewing) first.")
        return 1
    start, details = current_channel(client)
    print(f"Current channel: {start} ({details}). Target: {target}\n")
    if start is None or start == target:
        print("Pick a channel other than the current one.")
        return 1
    results = {"start": start, "start_details": details, "target": target}
    setters = [
        ("ip_control_full", {**details, "channelNum": target}),
        ("ip_control_number", {"channelNum": target}),
    ]
    for step, (name, params) in enumerate(setters, 1):
        print(f"{step}. IP Control directChannelControl {params} ...")
        answer = client.call("directChannelControl", params=params)
        seen = (
            wait_for_channel(client, target)
            if "result" in answer
            else current_channel(client)[0]
        )
        results[name] = {"params": params, "answer": answer, "channel_after": seen}
        print(
            f"   answer: {answer.get('result', answer.get('error'))}; channel now: {seen}"
        )
        if seen == target:
            return report_channel_test(results, name)
        if seen != start:
            # Moved somewhere else: retune the start before the next method.
            start = seen
    digits = [f"KEY_{digit}" for digit in target]
    print(f"3. Remote keys {' '.join(digits)} ...")
    try:
        send_keys(host, digits)
        seen = wait_for_channel(client, target)
        results["digit_keys"] = {"keys": digits, "channel_after": seen}
        print(f"   channel now: {seen}")
        if seen == target:
            return report_channel_test(results, "digit_keys")
        print("4. Remote key KEY_ENTER (confirm the typed number) ...")
        send_keys(host, ["KEY_ENTER"])
        seen = wait_for_channel(client, target)
        results["digit_keys_enter"] = {"channel_after": seen}
        print(f"   channel now: {seen}")
        if seen == target:
            return report_channel_test(results, "digit_keys_enter")
    except (RemoteError, OSError) as err:
        results["digit_keys_error"] = str(err)
        print(f"   remote channel failed: {err}")
    return report_channel_test(results, None)


def cmd_time_channel(host, target, gap, enter):
    """Time a typed channel number: digit gap and optional Enter."""
    client = ipc_client(host)
    if not client:
        return 1
    target = str(target).strip()
    if not target.isdigit():
        print("Give a channel number, for example 2111.")
        return 1
    if current_input(client) != "TV":
        print("Switch the TV to the Samsung TV source (normal viewing) first.")
        return 1
    start, _ = current_channel(client)
    if start == target:
        print("Pick a channel other than the current one.")
        return 1
    keys = [f"KEY_{digit}" for digit in target] + (["KEY_ENTER"] if enter else [])
    saved = load_saved()
    remote = Remote(host)
    run = {"start": start, "target": target, "gap_s": gap, "enter": enter}
    try:
        began = time.monotonic()
        token = remote.connect(
            saved.get("remote_token") if saved.get("host") == host else None
        )
        if token and token != saved.get("remote_token"):
            saved.update(host=host, remote_token=token)
            TOKEN_FILE.write_text(json.dumps(saved))
        run["connect_s"] = round(time.monotonic() - began, 2)
        typing = time.monotonic()
        for index, key in enumerate(keys):
            remote.send_key(key, hold_open=gap if index + 1 < len(keys) else 0)
        typed = time.monotonic()
        run["typing_s"] = round(typed - typing, 2)
        # Keep the connection open while the TV tunes; poll IP Control.
        seen = start
        while time.monotonic() - typed < 12:
            seen, _ = current_channel(client)
            if seen == target:
                break
            time.sleep(0.25)
        done = time.monotonic()
    except (RemoteError, OSError) as err:
        print(f"Remote channel failed: {err}")
        return 1
    finally:
        remote.close()
    run.update(
        channel_after=seen,
        tuned=seen == target,
        after_last_key_s=round(done - typed, 2),
        total_s=round(done - began, 2),
    )
    print(
        f"{' '.join(keys)} with {gap} s between keys: "
        + (
            f"tuned {target} {run['after_last_key_s']} s after the last key, "
            f"{run['total_s']} s in total (connect {run['connect_s']} s, "
            f"typing {run['typing_s']} s)."
            if run["tuned"]
            else f"channel is {seen}, not {target} (a digit may have been dropped)."
        )
    )
    path = HERE / "frame_probe_channel_timing.json"
    try:
        runs = json.loads(path.read_text())
    except (OSError, ValueError):
        runs = []
    runs.append(run)
    path.write_text(json.dumps(runs, indent=2))
    print(f"Added to {path.name}")
    return 0


def cmd_hold_channel(host, targets, idles, gap):
    """Several channel changes over one remote connection, idle in between."""
    client = ipc_client(host)
    if not client:
        return 1
    numbers = [t.strip() for t in str(targets).split(",") if t.strip()]
    if not numbers or not all(n.isdigit() for n in numbers):
        print("Give channel numbers, for example 1114,2110,1114,2110.")
        return 1
    if current_input(client) != "TV":
        print("Switch the TV to the Samsung TV source (normal viewing) first.")
        return 1
    saved = load_saved()
    remote = Remote(host)
    report = {"gap_s": gap, "changes": []}
    print("Connecting once to the remote channel...")
    began = time.monotonic()
    try:
        token = remote.connect(
            saved.get("remote_token") if saved.get("host") == host else None
        )
    except (RemoteError, OSError) as err:
        print(f"Remote channel failed: {err}")
        return 1
    if token and token != saved.get("remote_token"):
        saved.update(host=host, remote_token=token)
        TOKEN_FILE.write_text(json.dumps(saved))
    report["connect_s"] = round(time.monotonic() - began, 2)
    print(f"Connected in {report['connect_s']} s.")
    try:
        for index, number in enumerate(numbers):
            change = {"target": number}
            if index:
                wait = idles[min(index - 1, len(idles) - 1)]
                print(f"Idle {wait:g} s with the connection open...")
                change["idle"] = remote.idle(wait)
                if "closed" in change["idle"]:
                    print(
                        f"  the connection closed after "
                        f"{change['idle']['closed_after_s']} s: {change['idle']['closed']}"
                    )
                    report["changes"].append(change)
                    break
                print(
                    f"  still open (pings {change['idle']['pings_sent']}, "
                    f"pongs {change['idle']['pongs']})"
                )
            start, _ = current_channel(client)
            change["start"] = start
            keys = [f"KEY_{digit}" for digit in number] + ["KEY_ENTER"]
            typing = time.monotonic()
            try:
                for position, key in enumerate(keys):
                    remote.send_key(
                        key, hold_open=gap if position + 1 < len(keys) else 0
                    )
            except (RemoteError, OSError) as err:
                change["error"] = f"{type(err).__name__}: {err}"
                print(f"  sending keys failed: {change['error']}")
                report["changes"].append(change)
                break
            typed = time.monotonic()
            seen = start
            while time.monotonic() - typed < 8:
                seen, _ = current_channel(client)
                if seen == number:
                    break
                time.sleep(0.25)
            done = time.monotonic()
            change.update(
                channel_after=seen,
                tuned=seen == number,
                typing_s=round(typed - typing, 2),
                total_s=round(done - typing, 2),
            )
            print(
                f"{number}: "
                + (
                    f"tuned in {change['total_s']} s (no connect needed)"
                    if change["tuned"]
                    else f"channel is {seen}, not {number}"
                )
            )
            report["changes"].append(change)
    finally:
        remote.close()
    path = HERE / "frame_probe_hold_channel.json"
    path.write_text(json.dumps(report, indent=2))
    print(f"Saved to {path.name}")
    return 0


def report_channel_test(results, worked):
    results["worked"] = worked
    path = HERE / "frame_probe_channel_test.json"
    path.write_text(json.dumps(redact(results), indent=2))
    print(f"\nResult: {worked or 'no method tuned the channel'}. Saved to {path}")
    return 0


def report_input_test(results, worked):
    results["worked"] = worked
    path = HERE / "frame_probe_input_test.json"
    path.write_text(json.dumps(redact(results), indent=2))
    print(f"\nResult: {worked or 'no method switched the input'}. Saved to {path}")
    return 0


def cmd_try_power_off(host):
    client = ipc_client(host)
    if not client:
        return 1
    before = client.call("powerControl")
    print("Power before:", before.get("result", before.get("error")))
    answer = client.call("powerControl", params={"power": "powerOff"})
    print("powerOff answer:", answer.get("result", answer.get("error")))
    start = time.monotonic()
    previous = None
    print("Watching for 40 s (watch the TV: does it go dark straight away?)")
    while time.monotonic() - start < 40:
        ipc = client.call("powerControl")
        snap = (
            (ipc.get("result") or {}).get("power", ipc.get("error")),
            rest_info(host).get("PowerState", "no answer"),
        )
        if snap != previous:
            print(
                f"  +{time.monotonic() - start:4.1f}s  ip_control={snap[0]}  rest={snap[1]}"
            )
            previous = snap
        time.sleep(2)
    return 0


def cmd_key(host, key):
    try:
        send_keys(host, [key])
    except (RemoteError, OSError) as err:
        print("Remote channel failed:", err)
        return 1
    print(f"Sent {key}.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Read-only probe of a Samsung Frame's local interfaces."
    )
    parser.add_argument(
        "command",
        choices=(
            "check",
            "pair",
            "watch",
            "thumbnail",
            "slideshow",
            "try-input",
            "try-power-off",
            "try-channel",
            "time-channel",
            "hold-channel",
            "key",
        ),
    )
    parser.add_argument("host", help="TV IP address")
    parser.add_argument(
        "arg",
        nargs="?",
        help="input (HDMI2, TV), key (KEY_SOURCE) or artwork id (MY_F0001)",
    )
    parser.add_argument("--interval", type=float, default=3, help="watch poll seconds")
    parser.add_argument(
        "--gap",
        type=float,
        default=None,
        help="time/hold-channel: seconds between keys",
    )
    parser.add_argument(
        "--idle",
        default="10,60,300",
        help="hold-channel: seconds idle before each later change, e.g. 10,60,300",
    )
    parser.add_argument(
        "--enter", action="store_true", help="time-channel: press Enter after digits"
    )
    args = parser.parse_args(argv)
    if args.command == "check":
        return cmd_check(args.host)
    if args.command == "pair":
        return cmd_pair(args.host)
    if args.command == "thumbnail":
        return cmd_thumbnail(args.host, args.arg)
    if args.command == "slideshow":
        return cmd_slideshow(args.host)
    if (
        args.command
        in ("try-input", "key", "try-channel", "time-channel", "hold-channel")
        and not args.arg
    ):
        parser.error(f"{args.command} needs an input or key, e.g. HDMI2 or KEY_SOURCE")
    if args.command == "try-input":
        return cmd_try_input(args.host, args.arg)
    if args.command == "try-channel":
        return cmd_try_channel(args.host, args.arg)
    if args.command == "time-channel":
        gap = 0.3 if args.gap is None else max(0.0, args.gap)
        return cmd_time_channel(args.host, args.arg, gap, args.enter)
    if args.command == "hold-channel":
        try:
            idles = [max(0.0, float(x)) for x in args.idle.split(",") if x.strip()]
        except ValueError:
            parser.error("--idle takes seconds separated by commas, e.g. 10,60,300")
        gap = 0.6 if args.gap is None else max(0.0, args.gap)
        return cmd_hold_channel(args.host, args.arg, idles or [10], gap)
    if args.command == "try-power-off":
        return cmd_try_power_off(args.host)
    if args.command == "key":
        return cmd_key(args.host, args.arg)
    return cmd_watch(args.host, max(1.0, args.interval))


if __name__ == "__main__":
    sys.exit(main())
