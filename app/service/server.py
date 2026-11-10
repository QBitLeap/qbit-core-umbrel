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
        self.slots = threading.BoundedSemaphore(2)
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


def handler(mode, client, chain, tokens=None):
    tokens = tokens or {}

    class Handler(BaseHTTPRequestHandler):
        server_version = "QbitApp"
        sys_version = ""

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *args):
            pass

        def send(self, code, body, mime="application/json; charset=utf-8"):
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

        def do_POST(self):
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
                    self.send(200, status(client, chain))
                except CoreUnavailable:
                    self.send(503, {"network": chain, "available": False,
                                   "message": "Waiting for verified Core reads"})
                return
            static = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/app.js": ("app.js", "text/javascript; charset=utf-8"),
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
    else:
        client = GatewayClient(os.environ["QBIT_READ_URL"], "ui",
                               secret(os.environ.get("QBIT_UI_TOKEN")))
        tokens, port = None, 8080
    server = BoundedServer(("0.0.0.0", port), handler(args.mode, client, chain, tokens))
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
