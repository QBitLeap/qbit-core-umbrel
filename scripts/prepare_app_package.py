"""Render one independent Core Umbrel instance from pinned app images.

Instance IDs, network and UI port are operator choices, not website defaults.
This prepares files only; it never installs, starts or publishes an app.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
IMAGE = re.compile(r"[a-z0-9][a-z0-9./:_-]*@sha256:[0-9a-f]{64}\Z")


def prepare(output, app_id, chain, port, core_image, service_image, local_fixture=False, icon_url=None, package_version=None):
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,45}", app_id) or chain not in {"main", "testnet4"}:
        raise ValueError("Invalid app ID or Qbit network")
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("Invalid app UI port")
    package_version = package_version or "1.0.0"
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-.][a-z0-9][a-z0-9.-]*)?", package_version) is None:
        raise ValueError("Explicit app package version required")
    for image in (core_image, service_image):
        if not IMAGE.fullmatch(image) and not (local_fixture and re.fullmatch(r"sha256:[0-9a-f]{64}", image)):
            raise ValueError("Pinned image references are required")
    icon_url = icon_url or "https://raw.githubusercontent.com/QBitLeap/qbit-core-umbrel/main/icon.svg"
    icon = urlsplit(icon_url)
    if icon.scheme not in {"http", "https"} or not icon.hostname or icon.username or icon.password or icon.fragment:
        raise ValueError("Expected a hosted icon URL without credentials")
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("Output exists; preserve it")
    prefix = "APP_" + app_id.upper().replace("-", "_")
    shared = {"user": "1000:1000", "restart": "on-failure", "read_only": True,
              "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"],
              "pids_limit": 128, "init": True,
              "tmpfs": ["/tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777"]}
    core = {**shared, "image": core_image, "command": ["serve"],
            "environment": {"QBIT_CHAIN": chain, "QBIT_NETWORK_ACTIVE": "${QBIT_NETWORK_ACTIVE:-1}"},
            "volumes": ["${APP_DATA_DIR}/data/core:/data", "${APP_DATA_DIR}/data/auth:/auth", "control:/settings"],
            "stop_grace_period": "2m", "mem_limit": "1536m", "cpus": 2,
            "healthcheck": {"test": ["CMD", "/opt/qbit/bin/qbit-cli", "-chain=" + chain,
                          "-datadir=/data", "-rpcport=18443", "-rpccookiefile=/auth/.cookie",
                          "getblockchaininfo"], "interval": "10s", "timeout": "5s",
                          "retries": 12, "start_period": "30s"}}
    health = {"test": ["CMD", "python", "-c",
              "from urllib.request import urlopen; urlopen('http://127.0.0.1:8081/healthz',timeout=2).close()"],
              "interval": "15s", "timeout": "4s", "retries": 4}
    broker = {**shared, "image": service_image, "command": ["broker"],
              "network_mode": "service:core", "mem_limit": "1024m", "cpus": 2,
              "environment": {"QBIT_CHAIN": chain,
                              "QBIT_READ_TOKEN": "${" + prefix + "_READ_TOKEN}",
                              "QBIT_UI_TOKEN": "${" + prefix + "_UI_TOKEN}",
                              "QBIT_CONTROL_TOKEN": "${" + prefix + "_CONTROL_TOKEN}",
                              "QBIT_NETWORK_ACTIVE": "${QBIT_NETWORK_ACTIVE:-1}"},
              "volumes": ["${APP_DATA_DIR}/data/auth:/auth:ro", "control:/settings"],
              "depends_on": {"core": {"condition": "service_healthy", "restart": True}},
              "healthcheck": health}
    ui = {**shared, "image": service_image, "command": ["ui"],
          "mem_limit": "128m", "cpus": 0.5,
          "environment": {"QBIT_CHAIN": chain,
                          "QBIT_READ_URL": "${" + prefix + "_READ_URL}",
                          "QBIT_UI_TOKEN": "${" + prefix + "_UI_TOKEN}",
                          "QBIT_CONTROL_TOKEN": "${" + prefix + "_CONTROL_TOKEN}",
                          "QBIT_CONTROL_PASSWORD": "${APP_PASSWORD}"},
          "depends_on": {"broker": {"condition": "service_healthy", "restart": True}},
          "healthcheck": {**health, "test": ["CMD", "python", "-c",
              "from urllib.request import urlopen; urlopen('http://127.0.0.1:8080/healthz',timeout=2).close()"]}}
    compose = {"services": {"app_proxy": {"environment": {"APP_HOST": app_id + "_ui_1", "APP_PORT": 8080}},
                            "core": core, "broker": broker, "ui": ui}, "volumes": {"control": {}}}
    manifest = {"manifestVersion": 2.0, "id": app_id, "storage": {"dataRoot": "data"},
                "environment": [{"name": "QBIT_NETWORK_ACTIVE", "services": ["core", "broker"], "default": "1", "options": ["0", "1"],
                  "note": "1 starts peer synchronization. The dashboard switch saves a choice across restarts; changing this setting resets that choice."}],
                "category": "finance", "name": "Qbit Core" + (" · Testnet4" if chain == "testnet4" else ""),
                "version": package_version, "tagline": "Your own Qbit node and authenticated indexer reads",
                "description": "Run an independent Qbit Core node with a browser status page. "
                    "Peer synchronization starts automatically. Pause or resume it with the dashboard switch. "
                    "Wallets are disabled. Compatible indexers use authenticated read RPC; Core cookies stay private. "
                    "Each instance keeps its own network-specific data. This is an amd64 package candidate.",
                "releaseNotes": "Configurable minute refresh, explicit sync progress, saved networking switch and automatic peer synchronization for new nodes.", "developer": "QBitLeap", "website": "https://github.com/Qbit-Org/qbit",
                "dependencies": [], "repo": "https://github.com/QBitLeap/qbit-core-umbrel",
                "support": "https://github.com/QBitLeap/qbit-core-umbrel/issues", "port": port,
                "icon": icon_url, "gallery": [], "path": "", "defaultUsername": "operator", "deterministicPassword": True,
                "submitter": "QBitLeap"}
    output.mkdir(parents=True)
    for directory in ("core", "auth"):
        path = output / "data" / directory
        path.mkdir(parents=True)
        (path / ".gitkeep").write_text("")
    for name, data in (("umbrel-app.yml", manifest), ("docker-compose.yml", compose)):
        (output / name).write_text(json.dumps(data, indent=2) + "\n")
    exports = (f'export {prefix}_READ_URL="http://{app_id}_core_1:8081/"\n'
               f'export {prefix}_READ_USER="reader"\n'
               f'export {prefix}_READ_TOKEN="$(derive_entropy "${{app_entropy_identifier}}-read-rpc")"\n'
               f'export {prefix}_UI_TOKEN="$(derive_entropy "${{app_entropy_identifier}}-status-ui")"\n'
               f'export {prefix}_CONTROL_TOKEN="$(derive_entropy "${{app_entropy_identifier}}-network-control")"\n')
    (output / "exports.sh").write_text(exports)
    shutil.copyfile(ROOT / "icon.svg", output / "icon.svg")
    report = {"format": "qbit-core-app-package-v1", "appId": app_id, "chain": chain,
              "coreImage": core_image, "serviceImage": service_image,
              "localFixtureImages": local_fixture, "fullUmbrelAppAccepted": False,
              "publicImagePublicationAccepted": False,
              "files": {p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in sorted(output.rglob("*")) if p.is_file()}}
    (output / "package-receipt.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--app-id", required=True)
    parser.add_argument("--chain", choices=("main", "testnet4"), required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--core-image", required=True)
    parser.add_argument("--service-image", required=True)
    parser.add_argument("--local-fixture-images", action="store_true")
    parser.add_argument("--icon-url")
    parser.add_argument("--package-version")
    args = parser.parse_args()
    result = prepare(args.output, args.app_id, args.chain, args.port, args.core_image,
                     args.service_image, args.local_fixture_images, args.icon_url, args.package_version)
    print(json.dumps({"appId": result["appId"], "chain": result["chain"],
                      "files": len(result["files"]), "installed": False}))


if __name__ == "__main__":
    main()
