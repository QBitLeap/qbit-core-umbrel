"""Render the real dashboard with labelled synthetic status, using an isolated browser."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from service.server import ASSETS, BoundedServer, handler


class Fixture:
    def call(self, method, params):
        return {"getblockchaininfo": {"chain": "main", "blocks": 120000, "headers": 250000,
                "bestblockhash": "a" * 64, "initialblockdownload": True, "verificationprogress": 0.42},
                "getindexinfo": {"txindex": {"synced": False, "best_block_height": 119900}},
                "getnetworkinfo": {"networkactive": True, "connections": 8}}[method]


class Control:
    def set(self, enabled):
        return {"network": "main", "peerNetworkingEnabled": enabled, "saved": True}


def main():
    browser = shutil.which("google-chrome") or shutil.which("chromium")
    if not browser:
        raise RuntimeError("Chrome/Chromium required for the isolated UI preview")
    base = handler("ui", Fixture(), "main", network_control=Control())
    class Preview(base):
        def gateway_request(self):
            return True

        def do_GET(self):
            if self.path == "/":
                body = (ASSETS / "index.html").read_bytes().replace(b"YOUR QBIT NODE", b"SYNTHETIC UI TEST FIXTURE")
                self.send(200, body, "text/html; charset=utf-8")
                return
            super().do_GET()
    server = BoundedServer(("127.0.0.1", 0), Preview)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    output = ROOT / ".release/ui-preview.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="qbit-ui-preview-") as profile:
            result = subprocess.run([browser, "--headless", "--no-sandbox", "--disable-gpu",
                "--disable-background-networking", "--no-first-run", "--no-default-browser-check",
                "--user-data-dir=" + profile, "--window-size=1280,1600", "--virtual-time-budget=2000",
                "--dump-dom", "--screenshot=" + str(output), "http://127.0.0.1:" + str(server.server_port)],
                capture_output=True, text=True, timeout=35)
            if result.returncode or not output.exists() or output.stat().st_size < 10000:
                raise RuntimeError("Isolated dashboard rendering failed: " + result.stderr[-1500:])
            for expected in ('Current block: 120000', '42.00%', 'Refresh interval (minutes)', 'Peer networking'):
                if expected not in result.stdout:
                    raise RuntimeError("Dashboard failed to render " + expected)
            print("PASS: isolated browser rendered live fixture counts, progress, networking and minute refresh controls.")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__": main()
