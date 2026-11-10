"""Prepare/verify the standalone Core image context; no build, daemon or download."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat

BUILD = Path(__file__).resolve().parents[1]
SOURCE = BUILD
FILES = ('Dockerfile', '.dockerignore', 'COPYING.qbit', 'runtime.lock.json')
ARTIFACT = 'qbit-1.0.0-x86_64-linux-gnu.tar.gz'
ARTIFACT_SHA256 = 'ae121af03263b55d530e3f3e8719a71362d0950cba82f54a2bc2d6c437a029b5'
BASE_IMAGE = 'debian:bookworm-20261104-slim@sha256:c67e92c8099ba22853ab4cfda7cfb494d253d6012ca82dee98ba8976772af018'
LICENSE_SHA256 = 'b028769f3852a9368ab10bd754ff01ebb741f84a2fa658c9aff82a631bc6ecfc'


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def manifest(root):
    files = {}
    for parent, directories, names in os.walk(root, followlinks=False):
        for name in directories + names:
            path = Path(parent) / name
            mode = path.lstat().st_mode
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise ValueError('Context must not contain links or special files')
            if stat.S_ISREG(mode) and path != root / 'manifest.json':
                files[path.relative_to(root).as_posix()] = digest(path)
    return files


def verify(root):
    if root.is_symlink() or not root.is_dir() or (root / 'manifest.json').is_symlink():
        raise ValueError('Expected a regular Core build context')
    report = json.loads((root / 'manifest.json').read_text())
    expected = set(FILES) | {'release/' + ARTIFACT}
    if (report.get('format') != 'qbit-core-runtime-context-v1'
            or report.get('baseImage') != BASE_IMAGE
            or set(report.get('files', {})) != expected
            or manifest(root) != report['files']):
        raise ValueError('Core context missing, changed or contains unexpected files')
    lock = json.loads((root / 'runtime.lock.json').read_text())
    if lock['baseImage'] != BASE_IMAGE or lock['artifact']['sha256'] != ARTIFACT_SHA256:
        raise ValueError('Core dependency lock differs')
    if digest(root / 'release' / ARTIFACT) != ARTIFACT_SHA256:
        raise ValueError('Core release checksum differs')
    if digest(root / 'COPYING.qbit') != LICENSE_SHA256:
        raise ValueError('Core license checksum differs')
    return report


def prepare(output, artifact):
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise ValueError('Output exists; preserve it')
    for name in FILES:
        source = SOURCE / name
        if source.is_symlink() or not source.is_file():
            raise ValueError('Expected a regular component source: ' + name)
    if artifact.is_symlink() or not artifact.is_file() or digest(artifact) != ARTIFACT_SHA256:
        raise ValueError('Core release checksum differs or artifact is linked/missing')
    output.mkdir(parents=True, mode=0o700)
    for name in FILES:
        source = SOURCE / name
        before = digest(source)
        shutil.copyfile(source, output / name)
        if digest(source) != before or digest(output / name) != before:
            raise ValueError('Component source changed during copy')
        (output / name).chmod(0o644)
    (output / 'release').mkdir(mode=0o700)
    shutil.copyfile(artifact, output / 'release' / ARTIFACT)
    report = {'format': 'qbit-core-runtime-context-v1', 'baseImage': BASE_IMAGE,
              'platform': 'linux/amd64', 'files': manifest(output),
              'scope': 'Core binaries only; no operational state, website or private keys'}
    (output / 'manifest.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    return verify(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('context', type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--artifact', type=Path)
    group.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    report = verify(args.context) if args.verify else prepare(args.context, args.artifact)
    print('Verified Core component context: ' + str(len(report['files'])) + ' files; no service started')


if __name__ == '__main__':
    main()
