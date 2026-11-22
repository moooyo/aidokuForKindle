import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import payload as payload_module
import run_visit as runner


def launcher_fixture():
    return b"""#!/bin/sh
set -eu
AIDOKU_HOME=/mnt/us/aidoku
APP_DIR=$AIDOKU_HOME/app
DATA_DIR=$AIDOKU_HOME/data
ordinary_exit() { return 0; }
run_application() {
    if [ "$1" = "resume" ]; then
        "$APP_DIR/aidoku" --kindle --device "$PROFILE" --data-dir "$DATA_DIR" --resume 3>&- &
    else
        "$APP_DIR/aidoku" --kindle --device "$PROFILE" --data-dir "$DATA_DIR" 3>&- &
    fi
}
while :; do
    case "$event" in
        'app-exited '*)
            set -- $event
            app_exit_status=$5
            app_exit_phase=$APP_PHASE
            if ordinary_exit "$3"; then exit "$app_exit_status"; fi
            ;;
    esac
done
"""


class WorkerGateTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.root = Path(root.name)
        self.index = self.root / 'index.json'
        self.index.write_text(json.dumps({'batch': str(self.root)}))
        self.admin_root = self.root / 'admin-root'
        self.admin_root.mkdir(mode=0o700)
        self.admin_public = self.root / 'admin-closure.json'
        self.admin_root_patch = patch.object(runner, 'ADMIN_ROOT', self.admin_root)
        self.admin_public_patch = patch.object(runner, 'ADMIN_CLOSURE', self.admin_public)
        self.admin_root_patch.start()
        self.admin_public_patch.start()
        self.addCleanup(self.admin_public_patch.stop)
        self.addCleanup(self.admin_root_patch.stop)
        self.identity = dict(project='Aidoku', owner_thread='previous-owner', run_id='previous-run',
                             lease_token_sha256='a' * 64)
        self.proof = dict(self.identity, ok=True, operation='release', state='released', owner_and_token_checked=True,
                          lease_directory_absent_after_release=True, native_restored_and_connections_closed=True)
        self.acquired_at = '2026-11-21T00:00:00+00:00'
        (self.root / 'receipt.json').write_text(json.dumps({
            'acquisition': dict(self.identity, ok=True, operation='acquire', state='held',
                                acquired_at_utc=self.acquired_at), 'ok': False}))

    def recovery(self, observed='device_closed', status=0, **changes):
        record = {'ok': False, 'operations': [{'operation': 'close', 'status': status, 'observed': [observed]}],
                  'release': {**self.proof, **changes}}
        (self.root / 'recovery-check.json').write_text(json.dumps(record))

    def private_record(self, path, value):
        path.write_text(json.dumps(value), encoding='utf-8')
        path.chmod(0o600)

    def administrative_closure(self):
        workspace_name = 'aidoku-admin-reconcile-20261121-ULgrWuP2'
        attempt_name = 'repair-run-abc012_3'
        workspace = self.admin_root / workspace_name
        attempt = workspace / attempt_name
        workspace.mkdir(mode=0o700)
        attempt.mkdir(mode=0o700)
        acquisition = dict(project='Aidoku', owner_thread='repair-owner', run_id='repair-run',
                           lease_token_sha256='b' * 64, ok=True, operation='acquire', state='held')
        public = dict(kind='administrative_native_closure', admin_workspace=workspace_name,
                      attempt=attempt_name, previous_run_id=self.identity['run_id'],
                      maintenance_run_id=acquisition['run_id'], native_state_verified=True,
                      maintenance_lease_released=True, ordinary_acquisition_unblocked=True,
                      original_owner_release=False)
        intent = dict(action='retire_orphan_lease_host_only', authorized_operator='administrator',
                      authorization='User confirmed that the previous operator ended and directed stale lease cleanup.',
                      old={**{key: self.identity[key] for key in ('project', 'owner_thread', 'run_id')},
                           'acquired_at_utc': self.acquired_at, 'state': 'held'})
        retired = dict(kind='administrator_orphan_retirement', previous_project='Aidoku',
                       previous_owner_thread=self.identity['owner_thread'], previous_run_id=self.identity['run_id'],
                       administrator='administrator', old_active_lease_removed=True,
                       old_record_quarantined_intact=True, normal_release=False)
        native = dict(kind='admin_repair_confirmed', verified=True, previous_run_id=self.identity['run_id'],
                      attempt=attempt_name, acquisition=acquisition)
        release = dict(**{key: acquisition[key] for key in runner.IDENTITY}, ok=True, operation='release',
                       state='released', native_restored_and_connections_closed=True, owner_and_token_checked=True,
                       only_owned_owner_json_removed_then_rmdir=True, lease_directory_absent_after_release=True)
        paths = {'public': self.admin_public, 'intent': workspace / 'intent.json',
                 'retired': workspace / 'retired.json', 'native': attempt / 'native-closed.json',
                 'release': attempt / 'release.json'}
        for key, value in (('public', public), ('intent', intent), ('retired', retired),
                           ('native', native), ('release', release)):
            self.private_record(paths[key], value)
        return paths

    def test_only_a_closed_original_batch_can_admit_the_next_operator(self):
        self.assertFalse(runner.prior_returned(self.index))
        self.recovery('stage_absent')
        self.assertFalse(runner.prior_returned(self.index))
        self.recovery(status=False)
        self.assertFalse(runner.prior_returned(self.index))
        for field in ('owner_thread', 'run_id', 'lease_token_sha256', 'owner_and_token_checked',
                      'lease_directory_absent_after_release', 'native_restored_and_connections_closed'):
            with self.subTest(field=field):
                self.recovery(**{field: False})
                self.assertFalse(runner.prior_returned(self.index))
        self.recovery()
        self.assertTrue(runner.prior_returned(self.index))

    def test_only_bound_independent_administrative_repair_can_close_the_predecessor(self):
        paths = self.administrative_closure()
        self.assertTrue(runner.prior_returned(self.index))
        mutations = (
            ('public', 'kind', 'administrator_orphan_retirement'),
            ('public', 'native_state_verified', False),
            ('public', 'maintenance_lease_released', False),
            ('public', 'ordinary_acquisition_unblocked', False),
            ('public', 'original_owner_release', True),
            ('public', 'previous_run_id', 'other-run'),
            ('public', 'maintenance_run_id', 'other-run'),
            ('public', 'admin_workspace', '../aidoku-admin-reconcile-20261121-ULgrWuP2'),
            ('public', 'admin_workspace', 'file:///var/tmp/aidoku-admin-reconcile-20261121-ULgrWuP2'),
            ('public', 'attempt', '../repair-run-abc012_3'),
            ('intent', 'action', 'other-action'),
            ('intent', 'authorization', 'not-authorized'),
            ('intent', 'authorized_operator', self.identity['owner_thread']),
            ('retired', 'kind', 'other-kind'),
            ('retired', 'old_active_lease_removed', False),
            ('retired', 'old_record_quarantined_intact', False),
            ('retired', 'normal_release', True),
            ('retired', 'previous_project', 'Other'),
            ('retired', 'previous_owner_thread', 'other-owner'),
            ('retired', 'previous_run_id', 'other-run'),
            ('retired', 'administrator', 'other-admin'),
            ('native', 'kind', 'admin_native_close'),
            ('native', 'verified', False),
            ('native', 'previous_run_id', 'other-run'),
            ('native', 'attempt', 'repair-run-other___'),
            ('release', 'operation', 'status'),
            ('release', 'state', 'held'),
            ('release', 'ok', False),
            ('release', 'native_restored_and_connections_closed', False),
            ('release', 'owner_and_token_checked', False),
            ('release', 'only_owned_owner_json_removed_then_rmdir', False),
            ('release', 'lease_directory_absent_after_release', False),
            ('release', 'project', 'Other'),
            ('release', 'owner_thread', 'other-owner'),
            ('release', 'run_id', 'other-run'),
            ('release', 'lease_token_sha256', 'c' * 64),
        )
        for record, field, value in mutations:
            path = paths[record]
            original = json.loads(path.read_bytes())
            altered = dict(original, **{field: value})
            self.private_record(path, altered)
            with self.subTest(record=record, field=field, value=value):
                self.assertFalse(runner.prior_returned(self.index))
            self.private_record(path, original)
        self.assertTrue(runner.prior_returned(self.index))

    def test_rejects_replaced_old_or_reused_repair_identity(self):
        paths = self.administrative_closure()
        for field in ('project', 'owner_thread', 'run_id', 'acquired_at_utc', 'state'):
            path = paths['intent']
            original = json.loads(path.read_bytes())
            changed = json.loads(path.read_bytes())
            changed['old'][field] = 'different'
            self.private_record(path, changed)
            with self.subTest(old_field=field):
                self.assertFalse(runner.prior_returned(self.index))
            self.private_record(path, original)
        for field in ('owner_thread', 'run_id', 'lease_token_sha256'):
            native = json.loads(paths['native'].read_bytes())
            release = json.loads(paths['release'].read_bytes())
            public = json.loads(paths['public'].read_bytes())
            changed_native = json.loads(paths['native'].read_bytes())
            changed_release = json.loads(paths['release'].read_bytes())
            changed_public = json.loads(paths['public'].read_bytes())
            changed_native['acquisition'][field] = self.identity[field]
            changed_release[field] = self.identity[field]
            if field == 'run_id':
                changed_public['maintenance_run_id'] = self.identity[field]
            for key, value in (('native', changed_native), ('release', changed_release), ('public', changed_public)):
                self.private_record(paths[key], value)
            with self.subTest(reused_field=field):
                self.assertFalse(runner.prior_returned(self.index))
            for key, value in (('native', native), ('release', release), ('public', public)):
                self.private_record(paths[key], value)

    def test_admin_evidence_must_be_private_regular_and_complete(self):
        paths = self.administrative_closure()
        for key in ('intent', 'retired', 'native', 'release'):
            path = paths[key]
            content = path.read_bytes()
            path.unlink()
            with self.subTest(missing=key):
                self.assertFalse(runner.prior_returned(self.index))
            path.write_bytes(content)
            path.chmod(0o600)
        self.admin_public.chmod(0o644)
        self.assertFalse(runner.prior_returned(self.index))
        self.admin_public.chmod(0o600)
        hardlink = self.root / 'hardlink.json'
        os.link(paths['native'], hardlink)
        self.assertFalse(runner.prior_returned(self.index))
        hardlink.unlink()
        workspace = paths['intent'].parent
        workspace.chmod(0o755)
        self.assertFalse(runner.prior_returned(self.index))
        workspace.chmod(0o700)
        paths['public'].write_text('{"kind":"a","kind":"b"}', encoding='utf-8')
        self.assertFalse(runner.prior_returned(self.index))

    def test_public_receipt_alone_or_symlinked_records_do_not_close_the_predecessor(self):
        self.private_record(self.admin_public, {'kind': 'administrative_native_closure',
                                                'native_state_verified': True})
        self.assertFalse(runner.prior_returned(self.index))
        self.admin_public.unlink()
        paths = self.administrative_closure()
        content = paths['native'].read_bytes()
        outside = self.root / 'outside.json'
        outside.write_bytes(content)
        outside.chmod(0o600)
        paths['native'].unlink()
        paths['native'].symlink_to(outside)
        self.assertFalse(runner.prior_returned(self.index))
        paths['native'].unlink()
        paths['native'].write_bytes(content)
        paths['native'].chmod(0o600)
        workspace = paths['intent'].parent
        moved = self.root / 'moved-workspace'
        workspace.rename(moved)
        workspace.symlink_to(moved, target_is_directory=True)
        self.assertFalse(runner.prior_returned(self.index))

    def test_administrative_closure_does_not_override_a_live_lease_gate(self):
        self.administrative_closure()
        for folder in ('plan', 'sealed-operator'):
            path = self.root / folder
            path.mkdir()
            (path / 'plan.json').write_bytes(b'{}')
        (self.root / 'sealed-operator/payload.tar.gz').write_bytes(b'example')
        live_lease = self.root / 'active-lease'
        live_lease.mkdir()
        runner_original = runner.prior_returned
        fake_tools = {'device_lease': SimpleNamespace(), 'run_hardware_smoke': SimpleNamespace(ssh_argv=lambda *_: [])}
        with (patch.dict(sys.modules, fake_tools), patch.object(runner, 'WORKER_RUNNER', self.index),
              patch.object(runner, 'LEASE', live_lease),
              patch.object(runner, 'prior_returned', side_effect=lambda: runner_original(self.index)),
              patch.object(runner, 'check_operator', return_value=('sample', b'script')),
              patch.object(runner, 'load', return_value=({'label': 'sample'}, b'example')),
              patch.object(runner, 'channel', side_effect=AssertionError('No transport is permitted')),
              redirect_stdout(io.StringIO()) as output):
            self.assertEqual(runner.main(SimpleNamespace(work=self.root, action='preflight')), 3)
        gates = json.loads(output.getvalue())
        self.assertTrue(gates['prior_close_verified'])
        self.assertFalse(gates['lease_free'])
        self.assertFalse((self.root / 'attempt').exists())

    def test_operator_connection_is_exclusive_and_checkpoint_is_readable(self):
        with runner.operator_gate(self.root):
            with self.assertRaises(BlockingIOError), runner.operator_gate(self.root):
                self.fail('Concurrent use of the same operator workspace')
        with runner.operator_gate(self.root):
            path = self.root / 'operations.json'
            runner.checkpoint(path, {'phase': 'started'})
            runner.checkpoint(path, {'phase': 'closed'})
            self.assertEqual(json.loads(path.read_bytes()), {'phase': 'closed'})
            self.assertEqual(path.stat().st_mode & 0o077, 0)

    def test_bounded_transport_reads_both_streams_and_cancels_on_overflow_or_timeout(self):
        with tempfile.TemporaryFile() as incoming:
            incoming.write(b'fixture')
            code, out, err = runner.exchange([sys.executable, '-c',
                'import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()); sys.stderr.write("note")'], incoming, 3, 7)
            self.assertEqual((code, out, err), (0, b'fixture', b'note'))
            with self.assertRaises(ValueError):
                runner.exchange([sys.executable, '-c', 'print("12345678")'], incoming, 3, 7)
            with self.assertRaises(TimeoutError):
                runner.exchange([sys.executable, '-c', 'import time; time.sleep(3)'], incoming, 0.05, 7)

    def test_same_size_is_not_an_identity_check(self):
        self.assertFalse(runner.same_bytes(io.BytesIO(b'ab'), io.BytesIO(b'ac')))
        self.assertTrue(runner.same_bytes(io.BytesIO(b'ab'), io.BytesIO(b'ab')))

    def test_device_command_framing_keeps_binary_upload_out_of_the_script(self):
        label = '0123456789abcdef'
        script = b'''set -eu
[ "$0:$2" = visit:0123456789abcdef ]
printf '%s\\n' "$1"
cat
'''
        upload = bytes(range(256)) * 8
        with runner.channel(self.root, ['/bin/sh', '-c'], label, script, upload) as remote:
            self.assertEqual(remote('prepare'), (0, b'prepare\n' + upload, b''))
            self.assertEqual(remote('admit'), (0, b'admit\n', b''))
            self.assertEqual(remote('close'), (0, b'close\n', b''))

    def test_run_with_missing_predecessor_cannot_open_a_channel_or_consume_the_plan(self):
        for folder in ('plan', 'sealed-operator'):
            path = self.root / folder
            path.mkdir()
            (path / 'plan.json').write_bytes(b'{}')
        (self.root / 'sealed-operator/payload.tar.gz').write_bytes(b'example')
        fake_tools = {'device_lease': SimpleNamespace(), 'run_hardware_smoke': SimpleNamespace(ssh_argv=lambda *_: [])}
        with (patch.dict(sys.modules, fake_tools), patch.object(runner, 'WORKER_RUNNER', self.index),
              patch.object(runner, 'LEASE', self.root / 'unused-lock'), patch.object(runner, 'prior_returned', return_value=False),
              patch.object(runner, 'check_operator', return_value=('sample', b'script')),
              patch.object(runner, 'load', return_value=({'label': 'sample'}, b'example')),
              patch.object(runner, 'channel', side_effect=AssertionError('No transport is permitted')),
              redirect_stdout(io.StringIO()) as output):
            self.assertEqual(runner.main(SimpleNamespace(work=self.root, action='run')), 3)
        self.assertFalse(json.loads(output.getvalue())['prior_close_verified'])
        self.assertFalse((self.root / 'attempt').exists())

    def test_check_operator_seal_requires_approved_tool_sources(self):
        tools = self.root / 'tools'
        approved_tools = self.root / 'review' / 'approved' / 'tools'
        tools.mkdir(parents=True)
        approved_tools.mkdir(parents=True)
        (self.root / 'label').write_text('0123456789abcdef\n', encoding='ascii')
        (tools / 'device_lease.py').write_text('DEVICE = 1\n', encoding='utf-8')
        (tools / 'run_hardware_smoke.py').write_text('SMOKE = 1\n', encoding='utf-8')
        (approved_tools / 'device_lease.py').write_text('DEVICE = 2\n', encoding='utf-8')
        (approved_tools / 'run_hardware_smoke.py').write_text('SMOKE = 1\n', encoding='utf-8')

        review = self.root / 'review' / 'launcher-visit'
        review.mkdir(parents=True)
        for name in ('run_visit.py', 'device.sh', 'lifecycle.py', 'evidence.py', 'payload.py'):
            (review / name).write_text(f'{name}\n', encoding='utf-8')
        (self.root / 'review' / 'launcher_stages.py').write_text('launcher_stages\n', encoding='utf-8')

        owner = '12345678-1234-1234-1234-123456789abc'
        with patch.object(runner, 'HERE', review):
            with self.assertRaisesRegex(ValueError, 'Prepared host tools differ from approved sources'):
                runner.check_operator(self.root, owner, seal=True)
        self.assertFalse((self.root / 'sealed-operator').exists())

    def test_seal_package_requires_approved_payload_inputs(self):
        label = '0123456789abcdef'
        owner = '12345678-1234-1234-1234-123456789abc'
        plan = {'label': label}
        (self.root / 'label').write_text(label + '\n', encoding='ascii')
        (self.root / 'plan').mkdir(parents=True)
        (self.root / 'plan' / 'plan.json').write_text('{}\n', encoding='utf-8')

        inputs = self.root / 'inputs'
        approved = self.root / 'review' / 'approved'
        (inputs / 'app').mkdir(parents=True)
        (approved / 'app').mkdir(parents=True)
        (self.root / 'work/mock-mnt/us' / f'.aidoku-native-{label}' / 'seed').mkdir(parents=True)
        (inputs / 'launcher.sh').write_bytes(launcher_fixture() + b'# tampered\n')
        (inputs / 'guard.sh').write_bytes(b'approved-guard')
        (inputs / 'app' / 'aidoku').write_bytes(b'approved-aidoku')
        (inputs / 'app' / 'aidoku-startup-portrait').write_bytes(b'approved-portrait')
        (approved / 'launcher.sh').write_bytes(launcher_fixture())
        (approved / 'guard.sh').write_bytes(b'approved-guard')
        (approved / 'app' / 'aidoku').write_bytes(b'approved-aidoku')
        (approved / 'app' / 'aidoku-startup-portrait').write_bytes(b'approved-portrait')

        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode='w:gz') as output:
            entry = tarfile.TarInfo('launch.sh')
            body = payload_module.rewrite_launcher((approved / 'launcher.sh').read_bytes())
            entry.size = len(body)
            output.addfile(entry, io.BytesIO(body))
        with patch.object(runner, 'check_operator', side_effect=AssertionError('seal must stop before check_operator')):
            with self.assertRaisesRegex(ValueError, 'Prepared launcher inputs differ from approved sources'):
                runner.seal_package(self.root, plan, archive.getvalue(), owner)
        self.assertFalse((self.root / 'sealed-operator').exists())


if __name__ == '__main__':
    unittest.main()
