"""Isolated synchronous Samsung client. Invoked by frame.py, never by HA directly.

stdin carries configuration; stdout is a private JSON-lines protocol. No secrets
are passed as command-line arguments or written to logs. The parent kills/reaps
this process when Samsung does not reply, including during handshake/cleanup.
"""

from __future__ import annotations

import json
import logging
import sys


def emit(event: str, **data) -> None:
    print(json.dumps({"event": event, **data}), flush=True)


def execute(config: dict) -> None:
    # HA can install requirements in /config/deps instead of site-packages.
    sys.path[:0] = config.get("python_path", [])
    logging.disable(logging.CRITICAL)
    try:
        from samsungtvws import SamsungTVWS
        from samsungtvws.art import SamsungTVArt
        from samsungtvws.exceptions import UnauthorizedError
    except Exception as err:
        emit("error", code="worker_failed", kind=type(err).__name__)
        return

    class TokenEvents:
        def _set_token(self, token):
            self.token = str(token)
            emit("token", token=self.token)

    class Remote(TokenEvents, SamsungTVWS):
        pass

    class Art(TokenEvents, SamsungTVArt):
        pass

    class ObservedSocket:
        """Report delivery only AFTER the WebSocket send succeeds."""

        def __init__(self, socket):
            self.socket = socket

        def __getattr__(self, key):
            return getattr(self.socket, key)

        def send(self, payload, *args, **kwargs):
            result = self.socket.send(payload, *args, **kwargs)
            outer = json.loads(payload)
            params = outer.get("params", {})
            if params.get("event") == "art_app_request":
                inner = json.loads(params["data"])
                if inner.get("request") == "set_artmode_status" and inner.get(
                    "value"
                ) == ("off" if config["action"] == "tv" else "on"):
                    emit("art_sent")
            return result

    kwargs = dict(
        host=config["host"],
        port=8002,
        token=config.get("token"),
        name="HA Frame Control",
        timeout=30 if config["action"] == "pair" else 10,
        key_press_delay=1,
    )
    clients = []
    try:
        action = config["action"]
        if action == "discover":
            emit("phase", phase="device_info")
            remote = Remote(**kwargs)
            clients.append(remote)
            info = remote.rest_device_info().get("device", {})
            emit(
                "device_info",
                data={
                    key: info.get(key)
                    for key in ("name", "modelName", "FrameTVSupport", "wifiMac")
                },
            )
        elif action == "pair":
            emit("phase", phase="remote_pairing")
            remote = Remote(**kwargs)
            clients.append(remote)
            remote.open()
            kwargs["token"] = remote.token
            remote.close()
            art = Art(**kwargs)
            clients.append(art)
            emit("phase", phase="art_pairing")
            art.open()
        elif action in ("art", "tv"):
            emit("phase", phase="art_handshake")
            art = Art(**kwargs)
            clients.append(art)
            art.open()
            art.connection = ObservedSocket(art.connection)
            emit("phase", phase="mode_send")
            art.set_artmode("off" if action == "tv" else "on")
        elif action == "off":
            emit("phase", phase="remote_handshake")
            remote = Remote(**kwargs)
            clients.append(remote)
            remote.open()
            emit("phase", phase="power_hold")
            # Use the exact long-press behavior verified on the QN55LS03T.
            remote.hold_key("KEY_POWER", 2.0)
        else:
            raise ValueError("Unknown action")
        emit("done")
    except UnauthorizedError:
        emit("error", code="invalid_auth")
    except Exception as err:
        # Exception text can contain the URL/token; return the type only.
        emit("error", code="cannot_connect", kind=type(err).__name__)
    finally:
        for client in clients:
            try:
                client.close()
            except Exception:
                pass


if __name__ == "__main__":
    execute(json.loads(sys.stdin.readline()))
