"""Build Core and its private read/status services; publication requires --publish."""

import argparse
from pathlib import Path
import shutil

from prepare_release_context import release
from release_common import build, stage, write_report

ROOT = Path(__file__).resolve().parents[1]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    output, revision = stage(ROOT, args.version)
    release(output / "core")
    services = output / "services"
    services.mkdir()
    shutil.copytree(ROOT / "app/service", services / "service", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copyfile(ROOT / "app/Dockerfile.services", services / "Dockerfile")
    for name in ("LICENSE", "NOTICE"):
        shutil.copyfile(ROOT / name, services / name)
    reports = [build(ROOT, output / "core/context", "qbit-core", args.version, revision, args.publish),
               build(ROOT, services, "qbit-core-services", args.version, revision, args.publish)]
    write_report(output, reports)
