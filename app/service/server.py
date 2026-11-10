"""Authenticated Core read RPC, or a separate browser status service."""

import argparse
import base64
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
from socketserver import ThreadingMixIn
import threading

from .rpc import CookieCore, CoreUnavailable, GatewayClient, GENESIS, ReadRefused, ReadRpcError, status, validate_read, wire_json
from .network import NetworkClient, NetworkPolicy, bridge_gateway

ASSETS = Path(__file__).parent / "static"
MAX_REQUEST = 64 * 1024


def secret(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("A per-install read token is required")
    return value


def unique_object(pairs):
    value = {}
    for name, item in pairs:
        if name in value:
            raise ValueError("Duplicate JSON field")
        value[name] = item
    return value


def parse_requests(raw):
    def invalid_constant(_):
        raise ValueError("Invalid JSON number")
    value = json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid_constant)
    batch = isinstance(value, list)
    items = value if batch else [value]
    if not 1 <= len(items) <= 16:
        raise ReadRefused("Read batch limit exceeded")
    for item in items:
        if (not isinstance(item, dict) or set(item) - {"jsonrpc", "id", "method", "params"}
                or item.get("jsonrpc") not in (None, "1.0", "2.0")
                or "id" not in item or type(item["id"]) not in (int, str, type(None))
                or len(str(item["id"])) > 64):
            raise ReadRefused("Invalid read request")
        validate_read(item.get("method"), item.get("params", []))
    return items, batch


class BoundedServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, address, handler):
        self.slots = threading.BoundedSemaphore(getattr(handler, "request_concurrency", 2))
        super().__init__(address, handler)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


def handler(mode, client, chain, tokens=None, network_control=None, gateway_address=bridge_gateway):
    tokens = tokens or {}

    class Handler(BaseHTTPRequestHandler):
        request_concurrency = 8 if mode == "ui" else 2
        server_version = "QbitApp"
        sys_version = ""

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *args):
            pass

        def send(self, code, body, mime="application/json; charset=utf-8", headers=None):
            if not isinstance(body, bytes):
                body = wire_json(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.send_header("Connection", "close")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.close_connection = True
            self.wfile.write(body)

        def authenticate(self):
            try:
                header = self.headers.get("Authorization", "")
                if not header.startswith("Basic ") or len(header) > 512:
                    return None
                decoded = base64.b64decode(header[6:], validate=True).decode()
                username, token = decoded.split(":", 1)
                expected = tokens.get(username)
                if expected and hmac.compare_digest(token.encode(), expected.encode()):
                    return username
            except (ValueError, UnicodeError):
                pass
            return None

        def control_body(self):
            if self.headers.get("Transfer-Encoding") or self.headers.get_content_type() != "application/json":
                raise ValueError("Expected bounded JSON")
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 2048:
                raise ValueError("Control request limit exceeded")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("Incomplete control request")
            value = json.loads(raw, object_pairs_hook=unique_object)
            if not isinstance(value, dict):
                raise ValueError("Expected an object")
            return value

        def gateway_request(self):
            host = self.headers.get("Host")
            return (self.client_address[0] == gateway_address()
                    and bool(host) and self.headers.get("X-Forwarded-Host") == host
                    and self.headers.get("X-Forwarded-Proto") in {"http", "https"})

        def browser_control(self):
            if network_control is None:
                self.send(405, {"error": "Networking control is unavailable"})
                return
            host = self.headers.get("Host")
            scheme = self.headers.get("X-Forwarded-Proto")
            if (not self.gateway_request() or self.headers.get("Origin") != f"{scheme}://{host}"
                    or self.headers.get("X-Qbit-Control") != "1"):
                self.send(403, {"error": "Open Qbit Core through Umbrel to change networking"})
                return
            try:
                value = self.control_body()
                if set(value) != {"enabled"} or type(value["enabled"]) is not bool:
                    raise ValueError("Explicit networking choice required")
                self.send(200, network_control.set(value["enabled"]))
            except (ValueError, TypeError, KeyError, RecursionError):
                self.send(400, {"error": "Networking control request refused"})
            except (CoreUnavailable, OSError):
                self.send(503, {"error": "Change could not be confirmed. Refresh status before trying again."})

        def do_POST(self):
            if mode == "ui" and self.path == "/api/control/network":
                self.browser_control()
                return
            if mode == "broker" and self.path == "/control/network":
                if self.headers.get("Origin") or self.authenticate() != "controller" or network_control is None:
                    self.send(403, {"error": "Networking control authentication required"})
                    return
                try:
                    value = self.control_body()
                    if set(value) != {"enabled"} or type(value["enabled"]) is not bool:
                        raise ValueError("Explicit networking choice required")
                    self.send(200, network_control.set(value["enabled"]))
                except (ValueError, TypeError, KeyError, RecursionError):
                    self.send(400, {"error": "Networking control request refused"})
                except (CoreUnavailable, OSError):
                    self.send(503, {"error": "Networking change is not confirmed"})
                return
            if mode != "broker":
                self.send(405, {"error": "Read-only status service"})
                return
            if self.path != "/" or self.headers.get("Origin"):
                self.send(403, {"error": "Read endpoint refused"})
                return
            role = self.authenticate()
            if role is None:
                self.send(401, {"error": "Read authentication required"})
                return
            if role == "controller":
                self.send(403, {"error": "Control credential is not a read credential"})
                return
            if self.headers.get("Transfer-Encoding") or self.headers.get_content_type() != "application/json":
                self.send(400, {"error": "Expected bounded JSON"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 1 <= length <= MAX_REQUEST:
                    raise ValueError("Request limit exceeded")
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError("Incomplete request")
                items, batch = parse_requests(raw)
                if role == "ui" and any(i["method"] not in {
                        "getblockchaininfo", "getindexinfo", "getnetworkinfo"} for i in items):
                    raise ReadRefused("Status credential cannot read this method")
            except (ValueError, TypeError, KeyError, RecursionError):
                self.send(400, {"result": None, "error": {"code": -32602,
                          "message": "Read method or request refused"}, "id": None})
                return
            answers = []
            for item in items:
                try:
                    result = client.call(item["method"], item.get("params", []))
                    answer = {"result": result, "error": None, "id": item["id"]}
                except ReadRpcError as error:
                    answer = {"result": None, "error": {"code": error.code, "message": str(error)}, "id": item["id"]}
                except CoreUnavailable:
                    answer = {"result": None, "error": {"code": -32000,
                              "message": "Verified Core read is unavailable"}, "id": item["id"]}
                if item.get("jsonrpc") == "2.0":
                    answer["jsonrpc"] = "2.0"
                answers.append(answer)
            self.send(200, answers if batch else answers[0])

        def do_GET(self):
            if self.path == "/healthz":
                self.send(200, {"alive": True})
                return
            if mode != "ui":
                self.send(405, {"error": "Use authenticated read RPC"})
                return
            if self.path == "/api/status":
                try:
                    value = status(client, chain)
                    value["networkControlAvailable"] = network_control is not None and self.gateway_request()
                    self.send(200, value)
                except CoreUnavailable:
                    self.send(503, {"network": chain, "available": False,
                                   "message": "Waiting for verified Core reads"})
                return
            static = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                      "/refresh.js": ("refresh.js", "text/javascript; charset=utf-8"),
                      "/style.css": ("style.css", "text/css; charset=utf-8")}
            if self.path not in static:
                self.send(404, {"error": "Not found"})
                return
            filename, mime = static[self.path]
            self.send(200, (ASSETS / filename).read_bytes(), mime)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("broker", "ui"))
    args = parser.parse_args()
    chain = os.environ.get("QBIT_CHAIN")
    if chain not in GENESIS:
        parser.error("QBIT_CHAIN must be main or testnet4")
    if args.mode == "broker":
        client = CookieCore(chain)
        tokens = {"reader": secret(os.environ.get("QBIT_READ_TOKEN")),
                  "ui": secret(os.environ.get("QBIT_UI_TOKEN"))}
        port = 8081
        control_token = os.environ.get("QBIT_CONTROL_TOKEN")
        if control_token:
            tokens["controller"] = secret(control_token)
        control = NetworkPolicy(client, chain, os.environ.get("QBIT_NETWORK_ACTIVE", "1")) if control_token else None
    else:
        client = GatewayClient(os.environ["QBIT_READ_URL"], "ui",
                               secret(os.environ.get("QBIT_UI_TOKEN")))
        tokens, port = None, 8080
        control_token = os.environ.get("QBIT_CONTROL_TOKEN")
        control = NetworkClient(os.environ["QBIT_READ_URL"], secret(control_token)) if control_token else None
    server = BoundedServer(("0.0.0.0", port), handler(args.mode, client, chain, tokens, control))
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
