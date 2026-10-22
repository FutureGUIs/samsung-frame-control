"""The blocking IP Control client against a real local HTTPS JSON-RPC server."""

import datetime
import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from custom_components.samsung_frame_control.ipcontrol import IpcError, IpControl


@pytest.fixture(scope="module")
def certificate(tmp_path_factory):
    """A self-signed certificate, like the one the TV presents."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "frame-tv")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    folder = tmp_path_factory.mktemp("tls")
    (folder / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (folder / "key.pem").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return folder / "cert.pem", folder / "key.pem"


@pytest.fixture
def server(certificate, socket_enabled):
    """Minimal JSON-RPC endpoint with the TV's success and error shapes."""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((dict(self.headers), body))
            method = body["method"]
            token = body.get("params", {}).get("AccessToken")
            if method == "createAccessToken":
                out = {"result": {"AccessToken": "new-token"}}
            elif token != "good":
                out = {"code": -32700, "message": "Parse error"}  # flat form
            elif method == "powerControl":
                out = {"result": {"power": "powerOn"}}
            elif method == "getTVStates":
                out = {"result": {"inputSource": "HDMI2", "volume": 14}}
            elif method == "getDeviceInformation":
                out = {"error": {"code": -32601, "message": "Method not found"}}
            else:
                out = {"error": {"code": -32002, "message": "Failed"}}
            data = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(*certificate)
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1], seen
    httpd.shutdown()
    httpd.server_close()


def test_read_state_and_minimal_headers(server):
    port, seen = server
    power, states = IpControl("127.0.0.1", port, "good").read_state()
    assert power == "powerOn"
    assert states["inputSource"] == "HDMI2"
    headers, body = seen[0]
    assert body["params"] == {"AccessToken": "good"}
    assert "Accept-Encoding" not in headers
    assert headers["Content-Type"] == "application/json"


def test_pairing_sends_no_token_and_returns_one(server):
    port, seen = server
    client = IpControl("127.0.0.1", port)
    assert client.pair() == "new-token"
    assert "params" not in seen[0][1]
    assert client.token == "new-token"


def test_flat_parse_error_with_token_means_rejected_token(server):
    port, _ = server
    with pytest.raises(IpcError) as raised:
        IpControl("127.0.0.1", port, "stale-secret").call("powerControl")
    assert raised.value.code == "unauthorized"
    assert "stale-secret" not in str(raised.value)


@pytest.mark.parametrize(
    ("method", "code"), [("getDeviceInformation", "unsupported"), ("other", "failed")]
)
def test_nested_errors(server, method, code):
    port, _ = server
    with pytest.raises(IpcError) as raised:
        IpControl("127.0.0.1", port, "good").call(method)
    assert raised.value.code == code


def test_closed_port_is_refused(socket_enabled):
    with pytest.raises(IpcError) as raised:
        IpControl("127.0.0.1", 9, "good").call("powerControl")  # discard port
    assert raised.value.code == "refused"


def test_missing_token_never_sends(socket_enabled):
    with pytest.raises(IpcError) as raised:
        IpControl("127.0.0.1", 9).call("powerControl")
    assert raised.value.code == "unauthorized"


class Scripted(IpControl):
    """read_state logic with scripted answers (no network)."""

    def __init__(self, answers):
        super().__init__("192.0.2.1", 1516, "good")
        self.answers, self.calls = answers, []

    def call(self, method, params=None, **kwargs):
        self.calls.append(method)
        answer = self.answers.get(method, IpcError("unsupported", -32601))
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_channel_read_only_on_tv_source():
    measured = {
        "RFtype": "CABLE",
        "airCable": "",
        "atvDtv": "TVPLUS",
        "channelNum": "2110",
    }
    tv = Scripted(
        {
            "powerControl": {"power": "powerOn"},
            "getTVStates": {"inputSource": "TV"},
            "directChannelControl": measured,
        }
    )
    assert tv.read_state()[1]["channel"] == {"number": "2110", "kind": "tvplus"}

    hdmi = Scripted(
        {"powerControl": {"power": "powerOn"}, "getTVStates": {"inputSource": "HDMI1"}}
    )
    assert "channel" not in hdmi.read_state()[1]
    assert "directChannelControl" not in hdmi.calls


def test_channel_failure_does_not_fail_the_poll():
    tv = Scripted(
        {
            "powerControl": {"power": "powerOn"},
            "getTVStates": {"inputSource": "TV"},
            "directChannelControl": IpcError("failed", -32002),
        }
    )
    power, states = tv.read_state()
    assert power == "powerOn" and "channel" not in states


def test_art_mode_read_in_every_poll():
    tv = Scripted(
        {
            "powerControl": {"power": "powerOn"},
            "getTVStates": {"inputSource": "HDMI1"},
            "artModeControl": {"artMode": "artModeOn"},
        }
    )
    assert tv.read_state()[1]["artMode"] == "on"
    off = Scripted({"powerControl": {"power": "powerOff"}})
    assert off.read_state() == ("powerOff", {})
    assert off.calls == ["powerControl"]  # an off TV keeps a remembered flag
