"""Build non-root amd64 images and optionally publish explicit version tags."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys


def stage(root, version):
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-.][a-z0-9][a-z0-9.-]*)?", version) is None or len(version) > 80:
        raise ValueError("An explicit version such as 0.1.1 is required")
    if sys.platform != "linux" or os.uname().machine != "x86_64":
        raise ValueError("Build on a Linux amd64 Docker host")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("Committed source required")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip():
        raise ValueError("Release source must be clean")
    output = root / ".release" / version
    if (root / ".release").is_symlink() or output.exists() or output.is_symlink():
        raise ValueError("Release stage exists; retain and inspect its result")
    output.mkdir(parents=True)
    return output, revision


def build(root, context, component, version, revision, publish):
    repository = "ghcr.io/qbitleap/" + component
    tag = repository + ":" + version
    subprocess.run(["docker", "build", "--platform", "linux/amd64", "--tag", tag,
        "--label", "org.opencontainers.image.title=" + component,
        "--label", 'org.opencontainers.image.source=https://github.com/QBitLeap/qbit-core',
        "--label", "org.opencontainers.image.revision=" + revision,
        "--label", "org.opencontainers.image.authors=LEAP contributors", str(context)], check=True)
    info = json.loads(subprocess.check_output(["docker", "image", "inspect", tag], text=True))[0]
    if info["Config"].get("User") != "1000:1000" or info.get("Architecture") != "amd64":
        raise ValueError("Release image architecture or runtime user differs")
    subprocess.run(["docker", "run", "--rm", "--init", "--network", "none", "--read-only",
        "--user", "1000:1000", "--cap-drop", "ALL", "--security-opt", "no-new-privileges=true",
        "--pids-limit", "64", "--memory", "256m", tag,
        "--version" if component == "qbit-core" else "--help"], check=True)
    if component == "qord-indexer":
        subprocess.run(["docker", "run", "--rm", "--network", "none", "--read-only",
            "--user", "1000:1000", "--cap-drop", "ALL", "--security-opt", "no-new-privileges=true",
            "--memory", "256m", "--entrypoint", "/usr/local/bin/qord", tag, "--version"], check=True)
        inventory = json.loads(subprocess.check_output(["docker", "run", "--rm", "--network", "none",
            "--read-only", "--user", "1000:1000", "--cap-drop", "ALL", "--security-opt", "no-new-privileges=true",
            "--memory", "128m", "--entrypoint", "cat", tag,
            "/usr/share/doc/qord/dependencies/inventory.json"], text=True))
        (context.parent / "dependency-notices.json").write_text(json.dumps(inventory, indent=2) + "\n")
        if (not isinstance(inventory.get("packages"), list) or not inventory["packages"]
                or not isinstance(inventory.get("packagesWithoutNoticeFiles"), list)):
            raise ValueError("Dependency notice inventory is incomplete")
        if publish and inventory["packagesWithoutNoticeFiles"]:
            raise ValueError("Review missing dependency notices before publishing; see dependency-notices.json")
    reference = None
    if publish:
        subprocess.run(["docker", "push", tag], check=True)
        pushed = json.loads(subprocess.check_output(["docker", "image", "inspect", tag], text=True))[0]
        references = [x for x in pushed.get("RepoDigests", []) if x.startswith(repository + "@sha256:")]
        if len(references) != 1 or re.fullmatch(re.escape(repository) + r"@sha256:[0-9a-f]{64}", references[0]) is None:
            raise ValueError("Published registry digest could not be verified")
        reference = references[0]
    return {"component": component, "platform": "linux/amd64", "sourceRevision": revision,
            "localImageId": info["Id"], "published": publish, "image": reference,
            "version": version, "fullStackAcceptanceRequired": True}


def write_report(output, reports):
    path = output / "published-images.json"
    path.write_text(json.dumps({"format": "qbitleap-image-release-v1", "images": reports}, indent=2) + "\n")
    print("Image release receipt: " + str(path))
