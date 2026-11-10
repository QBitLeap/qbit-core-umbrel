import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

BUILD = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('core_component', BUILD / 'scripts/prepare_core_context.py')
component = importlib.util.module_from_spec(spec)
spec.loader.exec_module(component)
smoke_spec = importlib.util.spec_from_file_location('core_smoke', BUILD / 'checks/smoke.py')
smoke = importlib.util.module_from_spec(smoke_spec)
smoke_spec.loader.exec_module(smoke)
life_spec = importlib.util.spec_from_file_location('core_lifecycle', BUILD / 'checks/lifecycle.py')
lifecycle = importlib.util.module_from_spec(life_spec)
life_spec.loader.exec_module(lifecycle)


class CoreComponentTests(unittest.TestCase):
    def fixture(self, root):
        source = root / 'source'; source.mkdir()
        for name in component.FILES:
            shutil.copyfile(component.SOURCE / name, source / name)
        artifact = root / component.ARTIFACT; artifact.write_bytes(b'fixture release, never executed')
        sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
        lock = json.loads((source / 'runtime.lock.json').read_text())
        lock['artifact']['sha256'] = sha
        (source / 'runtime.lock.json').write_text(json.dumps(lock))
        (source / 'private.key').write_text('excluded; not a component input')
        return source, artifact, sha

    def test_exact_component_scope_and_preserved_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source, artifact, sha = self.fixture(root)
            before = {p.name: p.read_bytes() for p in source.iterdir()}
            with patch.object(component, 'SOURCE', source), patch.object(component, 'ARTIFACT_SHA256', sha):
                report = component.prepare(root / 'context', artifact)
                self.assertEqual(set(report['files']), set(component.FILES) | {'release/' + component.ARTIFACT})
                self.assertFalse((root / 'context/private.key').exists())
                self.assertEqual({p.name: p.read_bytes() for p in source.iterdir()}, before)

    def test_bad_release_and_existing_output_fail_before_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source, artifact, sha = self.fixture(root)
            with patch.object(component, 'SOURCE', source):
                with self.assertRaisesRegex(ValueError, 'checksum'):
                    component.prepare(root / 'absent', artifact)
            self.assertFalse((root / 'absent').exists())
            target = root / 'existing'; target.mkdir(); (target / 'keep').write_text('preserve')
            with self.assertRaisesRegex(ValueError, 'Output exists'):
                component.prepare(target, artifact)
            self.assertEqual((target / 'keep').read_text(), 'preserve')

    def test_linked_source_and_release_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source, artifact, sha = self.fixture(root)
            link = root / 'artifact-link'; link.symlink_to(artifact)
            with patch.object(component, 'SOURCE', source), patch.object(component, 'ARTIFACT_SHA256', sha):
                with self.assertRaises(ValueError): component.prepare(root / 'a', link)
                recipe = source / 'Dockerfile'; recipe.unlink(); recipe.symlink_to(BUILD / 'Dockerfile')
                with self.assertRaises(ValueError): component.prepare(root / 'b', artifact)
            self.assertFalse((root / 'a').exists()); self.assertFalse((root / 'b').exists())

    def test_context_tampering_missing_files_and_extras_are_rejected(self):
        for kind in ('tamper', 'missing', 'extra'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); source, artifact, sha = self.fixture(root)
                with patch.object(component, 'SOURCE', source), patch.object(component, 'ARTIFACT_SHA256', sha):
                    target = root / 'context'; component.prepare(target, artifact)
                    if kind == 'tamper': (target / 'Dockerfile').write_text('unreviewed')
                    elif kind == 'missing': (target / 'COPYING.qbit').unlink()
                    else: (target / 'state.sqlite').write_bytes(b'not allowed')
                    with self.assertRaises(ValueError): component.verify(target)

    def test_floating_or_changed_base_cannot_replace_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source, artifact, sha = self.fixture(root)
            lock = json.loads((source / 'runtime.lock.json').read_text()); lock['baseImage'] = 'debian:latest'
            (source / 'runtime.lock.json').write_text(json.dumps(lock))
            with patch.object(component, 'SOURCE', source), patch.object(component, 'ARTIFACT_SHA256', sha):
                with self.assertRaisesRegex(ValueError, 'lock'):
                    component.prepare(root / 'context', artifact)

    def image_info(self, user='1000:1000'):
        return {'Id': 'sha256:' + '1' * 64, 'Architecture': 'amd64', 'Os': 'linux',
                'Config': {'User': user, 'Entrypoint': ['/opt/qbit/bin/qbitd'],
                           'Cmd': ['--version'], 'Volumes': None, 'ExposedPorts': None}}

    def test_smoke_refuses_root_image_before_any_container_start(self):
        info = self.image_info(user='0:0')
        with patch.object(smoke.subprocess, 'run', return_value=SimpleNamespace(stdout=json.dumps([info]))) as run:
            with self.assertRaisesRegex(ValueError, 'Unexpected image'):
                smoke.run_smoke(info['Id'], ['docker'])
            self.assertEqual(run.call_count, 1, 'An unsafe image must never reach docker run')

    def test_smoke_wrong_binary_version_cannot_report_success(self):
        info = self.image_info()
        responses = [SimpleNamespace(stdout=json.dumps([info])),
                     SimpleNamespace(returncode=0, stdout='Qbit Core version v2.0.0\n', stderr='')]
        with patch.object(smoke.subprocess, 'run', side_effect=responses):
            with self.assertRaisesRegex(RuntimeError, 'pinned release version'):
                smoke.run_smoke(info['Id'], ['docker'])

    def test_lifecycle_rejects_wrong_chain_genesis_and_network_activity(self):
        genesis = lifecycle.GENESIS['main']
        chain = {'chain': 'main', 'bestblockhash': genesis, 'blocks': 0, 'headers': 0, 'pruned': False}
        peers = {'connections': 0, 'networkactive': False}
        lifecycle.validate_chain('main', chain, peers, genesis, {'txindex': {}})
        for bad_chain, bad_peers, bad_genesis in [
            ({**chain, 'chain': 'testnet4'}, peers, genesis),
            (chain, peers, lifecycle.GENESIS['testnet4']),
            (chain, {**peers, 'networkactive': True}, genesis)]:
            with self.assertRaises(RuntimeError):
                lifecycle.validate_chain('main', bad_chain, bad_peers, bad_genesis, {'txindex': {}})

    def test_lifecycle_rejects_host_mounts_or_published_rpc(self):
        info = {'Config': {'User': '1000:1000', 'Labels': {'qbitleap.run': 'fixture', 'qbitleap.purpose': lifecycle.PURPOSE}},
                'HostConfig': {'NetworkMode': 'none', 'ReadonlyRootfs': True, 'Privileged': False,
                               'Binds': None, 'PortBindings': None, 'CapDrop': ['ALL'],
                               'SecurityOpt': ['no-new-privileges=true']},
                'Mounts': [{'Type': 'volume', 'Name': 'owned-fixture', 'Destination': '/data', 'RW': True}]}
        lifecycle.validate_container(info, 'fixture', 'owned-fixture')
        for field, value in [('Binds', ['/private/operator:/data']), ('PortBindings', {'18443/tcp': [{'HostPort': '18443'}]})]:
            info['HostConfig'][field] = value
            with self.assertRaises(RuntimeError): lifecycle.validate_container(info, 'fixture', 'owned-fixture')
            info['HostConfig'][field] = None

    def test_lifecycle_never_removes_unowned_volume(self):
        fixture = lifecycle.FixtureRun('sha256:' + '1' * 64, ['docker'])
        fixture.volumes.append('unowned')
        result = SimpleNamespace(returncode=0, stdout=json.dumps([{'Labels': {'qbitleap.run': 'another-run'}}]), stderr='')
        with patch.object(fixture, 'command', return_value=result) as command:
            errors = fixture.cleanup()
            self.assertEqual(len(errors), 1)
            self.assertEqual(command.call_count, 1, 'Ownership failure must not reach volume rm')

    def test_lifecycle_transaction_rpc_is_rejected_before_docker(self):
        fixture = lifecycle.FixtureRun('sha256:' + '1' * 64, ['docker'])
        with patch.object(fixture, 'command') as command:
            with self.assertRaises(ValueError): fixture.rpc('fixture', 'main', 'sendrawtransaction', 'deadbeef')
            command.assert_not_called()

    def test_lifecycle_parses_literal_cli_hash_and_rejects_invalid_hash(self):
        fixture = lifecycle.FixtureRun('sha256:' + '1' * 64, ['docker'])
        genesis = lifecycle.GENESIS['main']
        with patch.object(fixture, 'rpc', return_value=SimpleNamespace(stdout=genesis + '\n')):
            self.assertEqual(fixture.json_rpc('fixture', 'main', 'getblockhash', 0), genesis)
        with patch.object(fixture, 'rpc', return_value=SimpleNamespace(stdout='not a hash')):
            with self.assertRaises(ValueError): fixture.json_rpc('fixture', 'main', 'getblockhash', 0)


if __name__ == '__main__':
    unittest.main()
