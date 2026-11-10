"""A separate, narrowly scoped networking control; never an indexer read method."""

import base64
import json
import os
from pathlib import Path
import socket
import stat
import tempfile
import threading
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


def bridge_gateway():
    """The host reaches the UI from the Docker bridge gateway."""
    try:
        routes = Path("/proc/net/route").read_text().splitlines()[1:]
        for route in routes:
            _, destination, gateway, flags, *_ = route.split()
            if destination == "00000000" and (int(flags, 16) & 3) == 3:
                return socket.inet_ntoa(bytes.fromhex(gateway)[::-1])
    except (OSError, ValueError):
        return None
    return None
