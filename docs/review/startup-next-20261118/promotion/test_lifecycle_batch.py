"""Exercise the post-run archival, lease and protection checks using private synthetic files only."""

import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch


def load(name):
    spec = importlib.util.spec_from_file_location('fixture_' + name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


audit = load('analyze_lifecycle')


def save(path, obj):
    path.write_text(json.dumps(obj))


@unittest.skipUnless(sys.platform == 'linux', 'Run on test-env')
class RawBatch(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(); self.addCleanup(scratch.cleanup)
        self.batch = Path(scratch.name)
        run = 'aidoku-next-life-11111111'
        self.plan = {'cases': audit.CASES, 'run_id': run, 'mode': 'default-lifecycle'}
        runner, device = audit.maker.runner_text(), audit.maker.device_text(True)
        self.plan.update(runner_sha256=audit.sha(runner), device_script_sha256=audit.sha(device), cache_regime='empty-cache-v1')
        (self.batch/'runner.py').write_bytes(runner); (self.batch/'device.sh').write_bytes(device)
        gates = ('success', 'lease_released', 'same_boot', 'native_restored', 'entry_restored',
                 'installed_and_user_unchanged', 'input_roster_unchanged', 'temporary_stage_absent', 'cases_completed')
        labels = ['before', 'create-stage', 'upload', 'prepare-device', 'original-program-backup', 'activate-bridge',
                  'case-baseline-default', 'case-candidate-default', 'owned-stage-status', 'repair-launcher', 'collect',
                  'restore-light', 'after', 'remove-owned-stage']
        self.report = dict(self.plan, **dict.fromkeys(gates, True), completed=audit.CASES, case_exception=None,
                           commands=[{'name': k, 'returncode': 0} for k in labels]); save(self.batch/'report.json', self.report)
        for case in audit.CASES:
            (self.batch/('case-'+case+'.stdout')).write_bytes(b'LIFECYCLE_OK\n')
            (self.batch/('case-'+case+'.stderr')).write_bytes(b'')
        (self.batch/'owned-stage-status.stdout').write_bytes(b'owned\n')
        (self.batch/'owned-stage-status.stderr').write_bytes(b'')
        (self.batch/'after.stderr').write_bytes(b'')
        for name in ('repair-launcher','restore-light','remove-owned-stage'):
            for suffix in ('.stdout','.stderr'): (self.batch/(name+suffix)).write_bytes(b'')
        protected = {p: 'b'*64 for p in ('/mnt/us/aidoku/data/library.sqlite3', '/mnt/us/aidoku/data/device-profile',
                                        audit.gate.REGISTRATION.rsplit('/',1)[0]+'/protocol')}
        protected['/mnt/us/documents/Aidoku.sh'] = '709cefdf00318d40a3ee7cb8a5cc8b47e4b8c7865677040779a61264ae086960'
        protected['/mnt/us/aidoku/launch.sh'] = audit.gate.DEPLOYED[audit.gate.LAUNCH]
        snap = ''.join(v+'  '+k+'\n' for k,v in protected.items()) + '10000000-0000-0000-0000-000000000001\nevent3 13:78\nreader-home\nactive\n15\n9\n'
        for name in ('before.stdout','after.stdout'):
            (self.batch/name).write_text(snap)
        own = {'ok': True, 'run_id': run, 'owner_thread': run, 'lease_token_sha256': 'd'*64}
        save(self.batch/'lease-acquired.json', dict(own, acquired_at_utc='2026-01-01T01:00:00Z'))
        self.release = dict(own, released_at_utc='2026-01-01T01:02:00Z', owner_and_token_checked=True,
                            native_restored_and_connections_closed=True, lease_directory_absent_after_release=True)
        save(self.batch/'lease-released.json', self.release)
        self.folder = self.batch/'outputs/out/candidate-default'; self.folder.mkdir(parents=True)
        (self.folder/'case-ok').write_bytes(b'ok\n')
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode='w:gz') as archive:
            item = tarfile.TarInfo('out/candidate-default/case-ok'); item.size = 3; archive.addfile(item, io.BytesIO(b'ok\n'))
        for name in ('collect.stdout','outputs.tar.gz'):
            (self.batch/name).write_bytes(buf.getvalue())

    def test_only_the_collected_and_restored_batch_passes(self):
        self.assertTrue(audit.check_operating_batch(self.batch, self.plan))
        def mutate_file(path, new):
            old = path.read_bytes(); path.write_bytes(new)
            try:
                with self.assertRaises(ValueError):
                    audit.check_operating_batch(self.batch, self.plan)
            finally:
                path.write_bytes(old)
        mutate_file(self.folder/'case-ok', b'changed\n')
        mutate_file(self.batch/'case-baseline-default.stdout', b'RECOVERED\n')
        mutate_file(self.batch/'repair-launcher.stdout', b'LIFECYCLE_RECOVERED\n')
        mutate_file(self.batch/'repair-launcher.stderr', b'failed-native-check\n')
        mutate_file(self.batch/'device.sh', (self.batch/'device.sh').read_bytes().replace(b'test "$result" = LIFECYCLE_RECOVERED', b'true'))
        mutate_file(self.batch/'report.json', json.dumps(dict(self.report, commands=[
            (dict(command,returncode=1) if command['name']=='repair-launcher' else command) for command in self.report['commands']])).encode())
        mutate_file(self.batch/'after.stdout', b'different native state\n')
        mutate_file(self.batch/'lease-released.json', json.dumps(dict(self.release, lease_token_sha256='0'*64)).encode())
        mutate_file(self.batch/'lease-released.json', json.dumps(dict(self.release, owner_and_token_checked=False)).encode())
        mutate_file(self.batch/'report.json', json.dumps(dict(self.report, completed=['baseline-default'])).encode())
        mutate_file(self.batch/'report.json', json.dumps(dict(self.report, commands=list(reversed(self.report['commands'])))).encode())
        (self.folder/'unsealed').write_bytes(b'additional record')
        with self.assertRaises(ValueError):
            audit.check_operating_batch(self.batch, self.plan)

    def test_device_wrapper_consumes_the_exact_child_marker_and_restores_the_original_file(self):
        code = (self.batch/'device.sh').read_text().split('restore_launcher() {',1)[1]
        original, bridge = b'original launcher\n', b'owned bridge\n'
        for marker,rc,expected in [('LIFECYCLE_RECOVERED',0,True),('WRONG',0,False),('LIFECYCLE_RECOVERED',17,False)]:
            with self.subTest(marker=marker,rc=rc), tempfile.TemporaryDirectory() as name:
                root=Path(name); (root/'owned').mkdir(); stage=root/'owned'
                (stage/'case').write_text('candidate-default\n'); (stage/'original-launcher').write_bytes(original)
                (stage/'lifecycle_device.sh').write_text(f'#!/bin/sh\nprintf "%s\\n" {marker}\nexit {rc}\n')
                installed=root/'installed'; installed.write_bytes(bridge)
                prefix = '''set -eu
RUN=run; STAGE=$PWD/owned; INSTALLED=$PWD/installed; LOCK=$PWD/lock
owner() { test -f "$STAGE/case"; }; native_wait() { test ! -e "$LOCK"; }; stable_entry() { return 0; }
same_hash() { got=$(sha256sum "$1"); test "${got%% *}" = "$2"; }
'''
                script=prefix+f'BRIDGE_SHA={audit.sha(bridge)}\nORIGINAL_SHA={audit.sha(original)}\nrestore_launcher() {{'+code+'\nrestore_launcher\n'
                observed=subprocess.run(['sh','-s'],cwd=root,input=script.encode(),capture_output=True)
                self.assertEqual(observed.returncode==0,expected)
                self.assertEqual(observed.stdout,b'')
                self.assertEqual(installed.read_bytes(),original if expected else bridge)


@unittest.skipUnless(sys.platform == 'linux', 'Run on test-env')
class FrozenDriver(unittest.TestCase):
    def test_the_actual_freezer_and_the_actual_analyzer_agree_on_payloads(self):
        fixture = load('test_default_lifecycle').FreezerChecks()
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        root = patch.object(audit.maker, 'ROOT', fixture.root/'lifecycle'); root.start(); self.addCleanup(root.stop)
        batch = fixture.prepare().parent
        plan = json.loads((batch/'plan.json').read_text())
        (batch/'original-program-backup.stdout').write_bytes((batch/'original-launcher').read_bytes())
        program = audit.gate.DEPLOYED | plan['program_sha256']
        audit.check_resources(batch, plan, program, True)
        with self.assertRaises(ValueError):
            audit.check_resources(batch, plan, program, False)
        with self.assertRaises(ValueError):
            audit.check_resources(batch, plan, program | {audit.gate.APP: 'f'*64}, True)
        (batch/'runner.py').write_bytes(b'different host runner')
        with self.assertRaises(ValueError):
            audit.check_resources(batch, plan, program, True)


if __name__ == '__main__':
    unittest.main()
