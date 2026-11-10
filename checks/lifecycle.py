"""Rehearse the actual Core image using owned, isolated empty-chain fixtures."""
import argparse
import json
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid

GENESIS = {
    'main': '0000000000004d60aa5d46013991d0a0e2995d89ee98e53068ae196d763e79f2',
    'testnet4': '000000000000796fe86bbc0bf1b66a07e4b4c0676f74b54cf7e5ce8b3f1a0090',
}
RPC_METHODS = {'getblockchaininfo', 'getnetworkinfo', 'getindexinfo', 'getblockhash', 'stop', 'listwallets'}
PURPOSE = 'core-lifecycle-fixture'


def validate_chain(network, chain, network_info, genesis, indexes):
    if (chain.get('chain') != network or genesis != GENESIS[network]
            or chain.get('bestblockhash') != genesis or chain.get('blocks') != 0
            or chain.get('headers') != 0 or chain.get('pruned') is not False
            or network_info.get('connections') != 0
            or network_info.get('networkactive') is not False
            or 'txindex' not in indexes):
        raise RuntimeError('Empty-chain network, genesis, index or peer isolation differs')


def validate_container(info, run_id, volume):
    config, host = info['Config'], info['HostConfig']
    mounts = info.get('Mounts', [])
    if (config.get('Labels', {}).get('qbitleap.run') != run_id
            or config.get('Labels', {}).get('qbitleap.purpose') != PURPOSE
            or config.get('User') != '1000:1000'
            or host.get('NetworkMode') != 'none' or not host.get('ReadonlyRootfs')
            or host.get('Privileged') or host.get('Binds')
            or host.get('PortBindings') or 'ALL' not in host.get('CapDrop', [])
            or not any(str(value).startswith('no-new-privileges') for value in host.get('SecurityOpt', []))
            or len(mounts) != 1 or mounts[0].get('Type') != 'volume'
            or mounts[0].get('Name') != volume or mounts[0].get('Destination') != '/data'
            or mounts[0].get('RW') is not True):
        raise RuntimeError('Unexpected fixture ownership, networking, privilege or mounts')


class FixtureRun:
    def __init__(self, image, docker):
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
            raise ValueError('An exact local image ID is required')
        self.image, self.docker = image, docker
        self.run_id = uuid.uuid4().hex
        self.volumes, self.containers = [], []

    def command(self, arguments, *, timeout=15, check=True):
        result = subprocess.run(self.docker + arguments, capture_output=True, text=True, timeout=timeout)
        if check and result.returncode:
            raise RuntimeError('Docker fixture command failed: ' + result.stderr.strip())
        return result

    def inspect(self, kind, name):
        return json.loads(self.command([kind, 'inspect', name]).stdout)[0]

    def rpc(self, name, network, method, *params, check=True):
        if method not in RPC_METHODS or network not in GENESIS:
            raise ValueError('Fixture RPC method/network not allowed')
        return self.command(['exec', '--user', '1000:1000', name, '/opt/qbit/bin/qbit-cli',
                             '-chain=' + network, '-datadir=/data', '-rpcport=18443',
                             '-rpcclienttimeout=3', method] + list(map(str, params)),
                            timeout=8, check=check)

    def json_rpc(self, name, network, method, *params):
        value = self.rpc(name, network, method, *params).stdout.strip()
        # Core's CLI prints scalar string results literally, not JSON-quoted.
        if method == 'getblockhash':
            if not re.fullmatch(r'[0-9a-f]{64}', value):
                raise ValueError('Unexpected genesis hash response')
            return value
        return json.loads(value)

    def start(self, network):
        name = 'qbit-fixture-' + self.run_id + '-' + network
        volume = name + '-data'
        if self.command(['volume', 'inspect', volume], check=False).returncode == 0:
            raise RuntimeError('Fixture volume unexpectedly exists; preserve it')
        self.command(['volume', 'create', '--label', 'qbitleap.run=' + self.run_id,
                      '--label', 'qbitleap.purpose=' + PURPOSE, volume])
        info = self.inspect('volume', volume)
        if info.get('Labels', {}).get('qbitleap.run') != self.run_id:
            raise RuntimeError('New fixture volume ownership differs')
        self.volumes.append(volume)
        arguments = ['run', '--pull=never', '--detach', '--name', name, '--network', 'none', '--read-only',
                     '--user', '1000:1000', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges=true',
                     '--pids-limit', '128', '--cpus', '2', '--memory', '512m', '--memory-swap', '512m',
                     '--mount', 'type=volume,src=' + volume + ',dst=/data',
                     '--label', 'qbitleap.run=' + self.run_id, '--label', 'qbitleap.purpose=' + PURPOSE,
                     self.image, '-chain=' + network, '-datadir=/data', '-server=1', '-daemon=0',
                     '-printtoconsole=1', '-disablewallet=1', '-txindex=1', '-prune=0',
                     '-dbcache=16', '-maxmempool=32', '-networkactive=0', '-connect=0',
                     '-dnsseed=0', '-fixedseeds=0', '-listen=0', '-discover=0',
                     '-rpcbind=127.0.0.1', '-rpcallowip=127.0.0.1', '-rpcport=18443']
        self.containers.append(name)
        self.command(arguments)
        validate_container(self.inspect('container', name), self.run_id, volume)
        return name, volume

    def ready(self, name, network):
        deadline = time.monotonic() + 40
        last_error = ''
        while time.monotonic() < deadline:
            info = self.inspect('container', name)
            if not info['State']['Running']:
                logs = self.command(['logs', '--tail', '30', name], check=False)
                raise RuntimeError('Fixture exited before RPC: ' + logs.stdout + logs.stderr)
            try:
                chain = self.json_rpc(name, network, 'getblockchaininfo')
                peers = self.json_rpc(name, network, 'getnetworkinfo')
                genesis = self.json_rpc(name, network, 'getblockhash', 0)
                indexes = self.json_rpc(name, network, 'getindexinfo')
                validate_chain(network, chain, peers, genesis, indexes)
                return {'chain': network, 'genesis': genesis, 'blocks': chain['blocks'],
                        'headers': chain['headers'], 'connections': peers['connections'],
                        'networkactive': peers['networkactive'], 'txindexPresent': True,
                        'initialblockdownload': chain.get('initialblockdownload'),
                        'readyForTransactions': False}
            except (RuntimeError, ValueError, KeyError) as exc:
                last_error = str(exc)
                time.sleep(0.5)
        raise RuntimeError('Fixture RPC readiness timed out: ' + last_error)

    def stop(self, name, network, mode):
        if mode == 'rpc':
            self.rpc(name, network, 'stop')
            self.command(['wait', name], timeout=30)
        else:
            self.command(['stop', '--time', '30', name], timeout=40)
        state = self.inspect('container', name)['State']
        if state['Running'] or state['ExitCode'] != 0 or state.get('OOMKilled'):
            raise RuntimeError('Fixture did not shut down cleanly')

    def cleanup(self):
        errors = []
        for kind, names in [('container', self.containers), ('volume', self.volumes)]:
            for name in names:
                try:
                    result = self.command([kind, 'inspect', name], check=False)
                    if result.returncode:
                        if 'no such' in result.stderr.lower():
                            continue
                        raise RuntimeError(result.stderr.strip())
                    info = json.loads(result.stdout)[0]
                    labels = info.get('Labels', {}) if kind == 'volume' else info['Config'].get('Labels', {})
                    if labels.get('qbitleap.run') != self.run_id or labels.get('qbitleap.purpose') != PURPOSE:
                        raise RuntimeError('Refusing cleanup of a resource without this run ownership')
                    arguments = ['rm', '--force', name] if kind == 'container' else ['volume', 'rm', name]
                    self.command(arguments)
                except Exception as exc:
                    errors.append({'kind': kind, 'name': name, 'error': str(exc)})
        return errors

    def run(self):
        report = {'passed': False, 'imageId': self.image, 'runId': self.run_id,
                  'productionServicesStarted': False, 'operationalDataMounted': False,
                  'peerNetworkingEnabled': False, 'fullUmbrelAppAccepted': False,
                  'crossAppRpcPolicyAccepted': False, 'networks': {}}
        try:
            image = self.inspect('image', self.image)
            if (image['Id'] != self.image or image['Config']['User'] != '1000:1000'
                    or image.get('Architecture') != 'amd64' or image.get('Os') != 'linux'
                    or image['Config'].get('Entrypoint') != ['/opt/qbit/bin/qbitd']
                    or image['Config'].get('Cmd') != ['--version']
                    or image['Config'].get('Volumes') or image['Config'].get('ExposedPorts')):
                raise RuntimeError('Unexpected fixture image defaults')
            print('Fixture run: ' + self.run_id + '; only owned empty-chain resources', flush=True)
            nodes = {network: self.start(network) for network in GENESIS}
            for network, (name, volume) in nodes.items():
                print('Checking isolated ' + network + ' RPC, identity and restart', flush=True)
                state = self.ready(name, network)
                wrong_network = 'testnet4' if network == 'main' else 'main'
                if self.rpc(name, wrong_network, 'getblockchaininfo', check=False).returncode == 0:
                    raise RuntimeError('Wrong-chain CLI unexpectedly reused RPC credentials')
                wallet = self.rpc(name, network, 'listwallets', check=False)
                if wallet.returncode == 0 or '-32601' not in wallet.stderr:
                    raise RuntimeError('Wallet RPC was not disabled as expected')
                cookie = '/data/' + ('testnet4/' if network == 'testnet4' else '') + '.cookie'
                mode = self.command(['exec', '--user', '1000:1000', name, '/usr/bin/stat', '-c', '%a', cookie]).stdout.strip()
                if mode != '600':
                    raise RuntimeError('Fixture cookie mode is not private')
                marker = '/data/.lifecycle-' + self.run_id
                self.command(['exec', '--user', '1000:1000', name, '/usr/bin/touch', marker])
                first = 'rpc' if network == 'main' else 'signal'
                self.stop(name, network, first)
                self.command(['start', name])
                self.ready(name, network)
                self.command(['exec', '--user', '1000:1000', name, '/usr/bin/test', '-f', marker])
                validate_container(self.inspect('container', name), self.run_id, volume)
                self.stop(name, network, 'signal' if first == 'rpc' else 'rpc')
                state.update({'wrongChainCookieReuseRejected': True, 'walletRpcDisabled': True,
                              'cookieMode': mode, 'fixtureDataSurvivedRestart': True,
                              'rpcStopAndSigtermExitCodes': [0, 0]})
                report['networks'][network] = state
            report['passed'] = True
        except BaseException as exc:
            report['error'] = str(exc) or type(exc).__name__
        finally:
            errors = self.cleanup()
            report['fixtureCleanupComplete'] = not errors
            report['cleanupErrors'] = errors
            report['fixtureResources'] = {'containers': self.containers, 'volumes': self.volumes}
            if errors:
                report['passed'] = False
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--sudo', action='store_true')
    args = parser.parse_args()
    if args.receipt.exists() or args.receipt.is_symlink():
        parser.error('Receipt already exists; preserve it')
    def interrupted(signum, frame):
        raise KeyboardInterrupt('Fixture interrupted by signal ' + str(signum))
    for name in ('SIGINT', 'SIGTERM', 'SIGHUP'):
        if hasattr(signal, name): signal.signal(getattr(signal, name), interrupted)
    docker = ['sudo', '-n', 'docker', '--host', 'unix:///var/run/docker.sock'] if args.sudo else ['docker']
    report = FixtureRun(args.image, docker).run()
    with args.receipt.open('x') as stream:
        json.dump(report, stream, indent=2); stream.write('\n')
    print(json.dumps(report, indent=2))
    print('QBIT_CORE_LIFECYCLE_PASSED=1' if report['passed'] else 'QBIT_CORE_LIFECYCLE_FAILED=1')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
