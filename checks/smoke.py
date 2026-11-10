"""Run only non-mutating commands in an exact built Core image; never start a node."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid


def run_smoke(image, docker):
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
        raise ValueError('Smoke tests require an exact local image ID')
    result = subprocess.run(docker + ['image', 'inspect', image], check=True,
                            capture_output=True, text=True, timeout=15)
    info = json.loads(result.stdout)[0]
    config = info['Config']
    if (info['Id'] != image or info.get('Architecture') != 'amd64' or info.get('Os') != 'linux'
            or config['User'] != '1000:1000'
            or config['Entrypoint'] != ['/opt/qbit/bin/qbitd']
            or config['Cmd'] != ['--version'] or config.get('Volumes')
            or config.get('ExposedPorts')):
        raise ValueError('Unexpected image identity, default command or exposed resources')
    commands = [('daemon-version', None, ['--version']),
                ('cli-version', '/opt/qbit/bin/qbit-cli', ['--version']),
                ('runtime-uid', '/usr/bin/id', ['-u'])]
    outputs = {}
    for label, entrypoint, arguments in commands:
        name = 'qbit-core-smoke-' + uuid.uuid4().hex
        command = docker + ['run', '--rm', '--name', name, '--network', 'none',
                            '--read-only', '--user', '1000:1000', '--cap-drop', 'ALL',
                            '--security-opt', 'no-new-privileges=true', '--pids-limit', '64',
                            '--cpus', '2', '--memory', '512m', '--memory-swap', '512m',
                            '--label', 'qbitleap.purpose=core-component-smoke']
        if entrypoint:
            command += ['--entrypoint', entrypoint]
        try:
            result = subprocess.run(command + [image] + arguments,
                                    capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            # Only this run's exact ephemeral container may be stopped/removed.
            subprocess.run(docker + ['rm', '--force', name], capture_output=True,
                           text=True, timeout=15)
            raise
        outputs[label] = {'exitCode': result.returncode, 'stdout': result.stdout,
                          'stderr': result.stderr}
        if result.returncode:
            raise RuntimeError(label + ' failed: ' + result.stderr)
        if label == 'runtime-uid':
            if result.stdout.strip() != '1000':
                raise RuntimeError('Runtime UID differs')
        elif not re.search(r'\bv1\.0\.0\b', result.stdout + result.stderr):
            raise RuntimeError(label + ' did not report the pinned release version')
    return {'passed': True, 'imageId': image, 'checks': outputs,
            'productionServicesStarted': False, 'operationalDataMounted': False,
            'networkEnabled': False, 'fullUmbrelAppAccepted': False,
            'lifecycleAndRpcAccepted': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--receipt', required=True, type=Path)
    parser.add_argument('--sudo', action='store_true')
    args = parser.parse_args()
    if args.receipt.exists() or args.receipt.is_symlink():
        parser.error('Receipt already exists; preserve its result')
    try:
        docker = ['sudo', '-n', 'docker', '--host', 'unix:///var/run/docker.sock'] if args.sudo else ['docker']
        report = run_smoke(args.image, docker)
    except Exception as exc:
        report = {'passed': False, 'imageId': args.image, 'error': str(exc),
                  'productionServicesStarted': False, 'operationalDataMounted': False}
    with args.receipt.open('x') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
