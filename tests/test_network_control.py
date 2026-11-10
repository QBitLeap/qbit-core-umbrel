import base64
from http.client import HTTPConnection
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from service.network import NetworkPolicy
from service.rpc import CoreUnavailable, ReadRefused, validate_read
from service.server import BoundedServer, handler


class Core:
    def __init__(self):
        self.active = True
        self.mutations = []
        self.refuse_identity = False

    def call(self, method, params):
        if self.refuse_identity:
            raise CoreUnavailable("Wrong genesis")
        return {"networkactive": self.active}

    def _call(self, method, params):
        self.mutations.append((method, params))
        self.active = params[0]


class NetworkPolicyTests(unittest.TestCase):
    def test_dashboard_choice_survives_startup_and_changed_default_resets_it(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            auth, data, settings = root / "auth", root / "data", root / "settings"
            auth.mkdir(); data.mkdir(); settings.mkdir()
            binary, script, argv = root / "qbitd", root / "start.sh", root / "argv"
            binary.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$QBIT_TEST_ARGV"\n')
            binary.chmod(0o755)
            source = (ROOT / "app/start-core.sh").read_text()
            script.write_text(source.replace("/auth", str(auth)).replace("/data", str(data))
                              .replace("/settings", str(settings)).replace("/opt/qbit/bin/qbitd", str(binary)))
            environment = {**os.environ, "QBIT_CHAIN": "main", "QBIT_TEST_ARGV": str(argv)}
            environment.pop("QBIT_NETWORK_ACTIVE", None)
            def start():
                subprocess.run(["sh", str(script), "serve"], env=environment, check=True)
                return argv.read_text().splitlines()
            self.assertIn("-networkactive=1", start())
            core = Core()
            policy = NetworkPolicy(core, "main", "1", settings / "network-active")
            policy.set(False)
            self.assertEqual(core.mutations, [("setnetworkactive", [False])])
            self.assertIn("-networkactive=0", start())
            self.assertEqual((settings / "network-active").read_text(), "main:1:0\n")
            environment["QBIT_NETWORK_ACTIVE"] = "0"
            self.assertIn("-networkactive=0", start())
            self.assertEqual((settings / "network-active").read_text(), "main:0:0\n")
            environment["QBIT_NETWORK_ACTIVE"] = "1"
            self.assertIn("-networkactive=1", start())
            # An uninstalled app may leave its volume behind. A fresh data
            # directory must start with the default rather than an old pause.
            (settings / "network-active").write_text("main:1:0\n")
            (data / ".network").unlink()
            self.assertIn("-networkactive=1", start())

    def test_invalid_choice_identity_or_link_never_mutates_core(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            core = Core()
            policy = NetworkPolicy(core, "main", "1", root / "network-active")
            for value in (1, 0, "true", None):
                with self.assertRaises(ValueError): policy.set(value)
            core.refuse_identity = True
            with self.assertRaises(CoreUnavailable): policy.set(False)
            self.assertFalse(policy.path.exists())
            core.refuse_identity = False
            other = root / "preserve"
            other.write_text("Unrelated")
            policy.path.symlink_to(other)
            with self.assertRaises(CoreUnavailable): policy.set(False)
            self.assertEqual(other.read_text(), "Unrelated")
            self.assertEqual(core.mutations, [])
            with self.assertRaises(ReadRefused): validate_read("setnetworkactive", [True])


class ControlHttpTests(unittest.TestCase):
    def setUp(self):
        self.changes = []
        changes = self.changes
        class Control:
            def set(self, enabled):
                changes.append(enabled)
                return {"network": "main", "peerNetworkingEnabled": enabled, "saved": True}
        self.gateway = "127.0.0.1"
        self.ui = BoundedServer(("127.0.0.1", 0), handler("ui", None, "main",
                              network_control=Control(), gateway_address=lambda: self.gateway))
        self.broker = BoundedServer(("127.0.0.1", 0), handler("broker", None, "main",
                                  {"reader": "a" * 64, "ui": "b" * 64, "controller": "c" * 64}, Control()))
        self.threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (self.ui, self.broker)]
        for thread in self.threads: thread.start()

    def tearDown(self):
        for server in (self.ui, self.broker): server.shutdown(); server.server_close()
        for thread in self.threads: thread.join()

    def post(self, server, path, value, headers=None):
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
        connection.request("POST", path, json.dumps(value), {"Content-Type": "application/json", **(headers or {})})
        response = connection.getresponse()
        result = response.status, json.loads(response.read()), dict(response.getheaders())
        connection.close()
        return result

    def test_browser_control_requires_gateway_and_same_origin(self):
        host = "127.0.0.1:" + str(self.ui.server_port)
        route = "/api/control/network"
        headers = {"Origin": f"http://{host}", "X-Forwarded-Host": host,
                   "X-Forwarded-Proto": "http", "X-Qbit-Control": "1"}
        self.assertEqual(self.post(self.ui, route, {"enabled": False})[0], 403)
        self.gateway = "10.0.0.1"
        self.assertEqual(self.post(self.ui, route, {"enabled": False}, headers)[0], 403)
        self.gateway = "127.0.0.1"
        self.assertEqual(self.post(self.ui, route, {"enabled": False},
                                   {**headers, "Origin": "http://other.example"})[0], 403)
        self.assertEqual(self.post(self.ui, route, {"enabled": 1}, headers)[0], 400)
        self.assertEqual(self.changes, [])
        self.assertEqual(self.post(self.ui, route, {"enabled": False}, headers)[0], 200)
        self.assertEqual(self.changes, [False])

    def test_read_roles_never_gain_network_control_and_control_has_no_general_rpc(self):
        for role, token in (("reader", "a" * 64), ("ui", "b" * 64)):
            headers = {"Authorization": "Basic " + base64.b64encode((role + ":" + token).encode()).decode()}
            self.assertEqual(self.post(self.broker, "/control/network", {"enabled": True}, headers)[0], 403)
        headers = {"Authorization": "Basic " + base64.b64encode(("controller:" + "c" * 64).encode()).decode()}
        self.assertEqual(self.post(self.broker, "/", {"id": 1, "method": "getblockchaininfo"}, headers)[0], 403)
        self.assertEqual(self.post(self.broker, "/control/network", {"enabled": True, "method": "stop"}, headers)[0], 400)
        self.assertEqual(self.changes, [])
        self.assertEqual(self.post(self.broker, "/control/network", {"enabled": True}, headers)[0], 200)
        self.assertEqual(self.changes, [True])


if __name__ == "__main__": unittest.main()
