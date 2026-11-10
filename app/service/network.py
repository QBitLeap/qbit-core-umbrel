"""A separate, narrowly scoped networking control; never an indexer read method."""

import base64
import hashlib
import hmac
from http.cookies import SimpleCookie, CookieError
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile
import threading
import time
from urllib.request import Request

from .rpc import CoreUnavailable, GatewayClient


class NetworkPolicy:
    def __init__(self, core, chain, configured="1", path=Path("/settings/network-active")):
        if configured not in {"0", "1"}:
            raise ValueError("Invalid networking default")
        self.core, self.chain, self.configured, self.path = core, chain, configured, Path(path)
        self.lock = threading.Lock()

    def save(self, enabled):
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise CoreUnavailable("Networking settings are unavailable")
        if self.path.exists():
            info = self.path.lstat()
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
                raise CoreUnavailable("Networking settings are unavailable")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, prefix=".network-active-", delete=False) as stream:
                temporary = stream.name
                stream.write(f"{self.chain}:{self.configured}:{int(enabled)}\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
            descriptor = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            if temporary is not None:
                os.unlink(temporary)

    def set(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("Explicit networking boolean required")
        with self.lock:
            # This verifies chain and genesis using the existing read boundary.
            self.core.call("getnetworkinfo", [])
            # Save the owner's intent before applying it. A timeout can leave the
            # result uncertain; a restart still follows the explicitly saved choice.
            self.save(enabled)
            self.core._call("setnetworkactive", [enabled])
            observed = self.core.call("getnetworkinfo", []).get("networkactive")
            if observed is not enabled:
                raise CoreUnavailable("Networking change is not yet confirmed")
            return {"network": self.chain, "peerNetworkingEnabled": observed, "saved": True}


class NetworkClient:
    def __init__(self, endpoint, token):
        # Reuse the endpoint/redirect/transport restrictions, not its read method API.
        self.transport = GatewayClient(endpoint, "controller", token)

    def set(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("Explicit networking boolean required")
        request = Request(self.transport.endpoint.rstrip("/") + "/control/network", method="POST",
                          data=json.dumps({"enabled": enabled}).encode(),
                          headers={"Content-Type": "application/json",
                                   "Authorization": "Basic " + base64.b64encode(
                                       ("controller:" + self.transport.token).encode()).decode()})
        try:
            with self.transport.opener.open(request, timeout=30) as response:
                raw = response.read(4097)
                if len(raw) > 4096:
                    raise CoreUnavailable("Networking response is unavailable")
                value = json.loads(raw)
            if not isinstance(value, dict) or value.get("peerNetworkingEnabled") is not enabled or value.get("saved") is not True:
                raise CoreUnavailable("Networking change is not yet confirmed")
            return value
        except Exception:
            raise CoreUnavailable("Networking change could not be confirmed; refresh status before trying again") from None


class ControlSessions:
    def __init__(self, password):
        self.password = password
        self.cookie_name = "qbitleap_control_" + hashlib.sha256(password.encode()).hexdigest()[:12]
        self.lock = threading.Lock()
        self.attempts, self.window = 0, time.monotonic()

    def mac(self, value):
        return hmac.new(self.password.encode(), value.encode(), hashlib.sha256).hexdigest()

    def login(self, password):
        with self.lock:
            now = time.monotonic()
            if now - self.window >= 300:
                self.attempts, self.window = 0, now
            if self.attempts >= 10:
                raise PermissionError("Wait before trying the control password again")
            self.attempts += 1
            if not isinstance(password, str) or not hmac.compare_digest(password.encode(), self.password.encode()):
                return None
            self.attempts = 0
        payload = f"{int(time.time()) + 8 * 3600}.{secrets.token_hex(16)}"
        return payload + "." + self.mac("session:" + payload)

    def session(self, header):
        if not isinstance(header, str) or len(header) > 4096:
            return None
        try:
            cookies = SimpleCookie(header)
            value = cookies[self.cookie_name].value
            expiry, nonce, signature = value.split(".")
            if (len(value) > 160 or not nonce or len(nonce) != 32 or int(expiry) <= time.time()
                    or int(expiry) > time.time() + 8 * 3600 + 5
                    or not hmac.compare_digest(signature, self.mac("session:" + expiry + "." + nonce))):
                return None
            return value
        except (CookieError, KeyError, ValueError, TypeError):
            return None

    def csrf(self, session):
        return self.mac("csrf:" + session)
