"""Prepare the Core app image from its pinned upstream binary release."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
from urllib.request import urlopen

from prepare_core_context import prepare, verify

ROOT = Path(__file__).resolve().parents[1]


def release(output):
    if output.exists() or output.is_symlink():
        raise ValueError("New release staging directory required")
    output.mkdir(parents=True)
    lock = json.loads((ROOT / "runtime.lock.json").read_text())
    artifact = output / lock["artifact"]["name"]
    digest, total = hashlib.sha256(), 0
    with urlopen(lock["artifact"]["url"], timeout=60) as response, artifact.open("xb") as stream:
        for chunk in iter(lambda: response.read(1024 * 1024), b""):
            total += len(chunk)
            if total > 256 * 1024 * 1024:
                raise ValueError("Core archive exceeds the reviewed release size budget")
            digest.update(chunk)
            stream.write(chunk)
    if digest.hexdigest() != lock["artifact"]["sha256"]:
        raise ValueError("Upstream Core archive checksum differs")
    context = output / "context"
    prepare(context, artifact)
    verify(context)
    # Extend the accepted binary-only recipe with the same app entrypoint used
    # by the independently tested runtime-component-plus-wrapper image.
    shutil.copyfile(ROOT / "app/start-core.sh", context / "start-core.sh")
    shutil.copyfile(ROOT / "LICENSE", context / "wrapper-LICENSE")
    shutil.copyfile(ROOT / "NOTICE", context / "wrapper-NOTICE")
    with (context / ".dockerignore").open("a") as stream:
        stream.write("\n!start-core.sh\n!wrapper-LICENSE\n!wrapper-NOTICE\n")
    recipe = (context / "Dockerfile").read_text()
    recipe += ("\nCOPY wrapper-LICENSE /usr/share/doc/qbit-app/LICENSE\nCOPY wrapper-NOTICE /usr/share/doc/qbit-app/NOTICE\n"
               "USER 0:0\nRUN mkdir -p /auth && chown 1000:1000 /auth && chmod 0700 /auth\n"
               "COPY --chmod=0555 start-core.sh /opt/qbit/start-core.sh\nUSER 1000:1000\n"
               'ENTRYPOINT ["/bin/sh", "/opt/qbit/start-core.sh"]\nCMD ["--version"]\n')
    (context / "Dockerfile").write_text(recipe)
    report = {"format": "qbit-core-app-release-context-v1", "artifactSha256": digest.hexdigest(),
              "files": {p.relative_to(context).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in sorted(context.rglob("*")) if p.is_file()}}
    (output / "release-context.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps({"archiveVerified": True, "files": len(release(args.output)["files"])}))
