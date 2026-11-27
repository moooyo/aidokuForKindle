#!/usr/bin/env python3
"""Send the real transport frames to local shells; drop actual reply streams."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import runpy
import shlex
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
FIX = runpy.run_path(str(HERE/'test_stock_generation_recovery.py'))
RUN, BASE, BOOT, LABEL, HOME, VIEW = (FIX[name] for name in ('RUN','BASE','BOOT','LABEL','HOME','VIEW'))


class TransportReplay(unittest.TestCase):
    def test_admission_text_is_not_trusted_when_transport_never_completes(self):
        for accepted in (True, False):
            with self.subTest(device_gate_passed=accepted), tempfile.TemporaryDirectory(prefix='stock-admit-wire-') as directory:
                root = Path(directory)
                model = FIX['DeviceModel'](root, 'measure')
                if not accepted:
                    (root/'native_busy').touch()
                host = root/'host'
                host.mkdir(mode=0o700)
                attempt = host/f'attempt-{LABEL}'
                attempt.mkdir(mode=0o700)
                source = model.script.read_bytes()
                log = root/'admit-frame'
                endpoint = root/'timeout-endpoint.sh'
                endpoint.write_text(f'''#!/bin/sh
set -eu
[ "$#" = 1 ]
case "$1" in *' admit {LABEL} stock '*) ;; *) exit 91;; esac
/bin/sh -c "$1" >{shlex.quote(str(log))} || [ "$?" = 1 ]
cat {shlex.quote(str(log))}
exec sleep 20
''')
                endpoint.chmod(0o700)
                lease = BASE['load_lease']()
                base = BASE | {'LEASE': root/'fixture-lease'}
                def factory(req, operations):
                    exchange = RUN['ssh_transport'](lease, req, [str(endpoint)], source, 'measure', LABEL,
                                                    dict(os.environ), operations)
                    return lambda action, timeout, **params: exchange(action, 2 if action == 'admit' else timeout, **params)
                def reject(_raw, _kind):
                    self.fail('An unfinished admission transport must not launch a visit')
                r = RUN['operate'](host, attempt, 'measure', source, model.payload, 500, base, lease, factory, reject,
                                   operation_lock=lambda: FIX['fixture_operation_lock'](host/'fixture-operation.lock'),
                                   not_after=datetime.now(timezone.utc)+timedelta(seconds=800))
                self.assertTrue(log.read_bytes().startswith(b'AB_ADMITTED ' if accepted else b'AB_ADMISSION_REFUSED '))
                row, = r['operations']
                self.assertEqual((row['action'],row['status'],row['admission_state']), ('admit','timeout','transport_uncertain'))
                self.assertNotIn('admission_reason',row)
                self.assertFalse(r['complete'] or r['recovery_required'] or base['LEASE'].exists() or model.state.exists() or model.stage.exists())
                self.assertFalse((attempt/'admitted-boot.json').exists() or (host/'measure-sent.json').exists())
                self.assertTrue(json.loads((attempt/'released.json').read_bytes())['owner_and_token_checked'])

    def test_measure_pipe_loses_run_stdout_then_recovers_with_same_lease(self):
        with tempfile.TemporaryDirectory(prefix='stock-pipe-replay-') as directory:
            root = Path(directory)
            model = FIX['DeviceModel'](root, 'measure')
            host = root/'host'
            host.mkdir(mode=0o700)
            attempt = host/f'attempt-{LABEL}'
            attempt.mkdir(mode=0o700)
            source = model.script.read_bytes()
            model.payload.write_bytes(source)
            endpoint = root/'local-endpoint.sh'
            hook = shlex.quote(str(root/'wire-actions'))
            # This local endpoint executes the bounded stdin bootstrap. Only the
            # stock hardware calls inside the independently generated fixture
            # are mocked. Lose stdout after a real script has finished a run.
            endpoint.write_text(f'''#!/bin/sh
set -e
[ "$#" = 1 ]
case "$1" in *' {LABEL} stock '*) ;; *) exit 91;; esac
case "$1" in
    *' run {LABEL} stock '*)
        printf 'dropped run reply\\n' >>{hook}
        /bin/sh -c "$1" >/dev/null
        exit 255 ;;
    *) exec /bin/sh -c "$1" ;;
esac
''')
            endpoint.chmod(0o700)
            lease = BASE['load_lease']()
            base = BASE | {'LEASE': root/'fixture-lease'}
            env = dict(os.environ)
            factory = lambda req, operations: RUN['ssh_transport'](lease, req, [str(endpoint)],source,
                                                                   'measure',LABEL,env,operations)
            def reject(_raw, _kind):
                self.fail('A transport-interrupted response is not a sample')
            receipt = RUN['operate'](host, attempt, 'measure', source, model.payload,500,base,lease,factory,reject,
                                     operation_lock=lambda: FIX['fixture_operation_lock'](host/'fixture-operation.lock'),
                                     not_after=datetime.now(timezone.utc)+timedelta(seconds=800))
            self.assertEqual([step['action'] for step in receipt['operations']],
                             ['admit','reserve','run','readback','finalize','absent'])
            self.assertEqual(receipt['operations'][2]['status'],255)
            self.assertFalse(receipt['complete'],receipt)
            self.assertFalse(receipt['recovery_required'],receipt)
            self.assertEqual((root/'wire-actions').read_text(),'dropped run reply\n')
            self.assertEqual((root/'clicks').read_text(),'one click\n')
            self.assertTrue(json.loads((attempt/'released.json').read_bytes())['owner_and_token_checked'])
            self.assertFalse(model.stage.exists() or model.state.exists() or base['LEASE'].exists())

    def replay_close_timeout(self, with_effects):
        with tempfile.TemporaryDirectory(prefix='stock-close-reply-') as directory:
            root = Path(directory)
            model = FIX['DeviceModel'](root, 'measure')
            host = root/'host'
            host.mkdir(mode=0o700)
            attempt = host/f'attempt-{LABEL}'
            attempt.mkdir(mode=0o700)
            source = model.script.read_bytes()
            model.payload.write_bytes(source)
            (root/'skip_run_close').touch()
            (root/'after-open-view').write_bytes(HOME)
            if with_effects:
                # Input motion takes effect, but the helper itself fails before
                # the device can write a positive closeout record.
                (root/'pointer_effect_no_reply').touch()
            else:
                (root/'nav_no_effect').touch()
            endpoint = root/'timeout-endpoint.sh'
            hook = shlex.quote(str(root/'wire-actions'))
            endpoint.write_text(f'''#!/bin/sh
set -eu
[ "$#" = 1 ]
case "$1" in *' {LABEL} stock '*) ;; *) exit 91;; esac
case "$1" in
    *' close {LABEL} stock '*)
        if /bin/sh -c "$1"; then exit 93; fi
        printf 'closed wire without receipt\\n' >>{hook}
        exec sleep 20;;
    *) exec /bin/sh -c "$1";;
esac
''')
            endpoint.chmod(0o700)
            lease = BASE['load_lease']()
            base = BASE | {'LEASE': root/'fixture-lease'}
            def factory(req, operations):
                exchange = RUN['ssh_transport'](lease, req, [str(endpoint)], source, 'measure', LABEL,
                                                dict(os.environ), operations)
                return lambda action, timeout, **params: exchange(action, 4 if action == 'close' else timeout, **params)
            def reject(_raw, _kind):
                self.fail('No timing decoder is allowed after the failed run')
            operation_lock = lambda: FIX['fixture_operation_lock'](host/'fixture-operation.lock')
            report = RUN['operate'](host, attempt, 'measure', source, model.payload, 500, base, lease, factory, reject,
                                    operation_lock=operation_lock, not_after=datetime.now(timezone.utc)+timedelta(seconds=800))
            self.assertEqual((root/'wire-actions').read_text(),'closed wire without receipt\n')
            self.assertEqual((report['operations'][4]['action'], report['operations'][4]['status']), ('close', 'timeout'))
            self.assertEqual((root/'clicks').read_text(),'one click\n')
            self.assertFalse(report['complete'], report)
            self.assertEqual(report['sample_count'], 0)
            self.assertEqual(report['recovery_required'], not with_effects, report)
            expected = ['admit', 'reserve', 'run', 'readback', 'close', 'readback']
            if with_effects:
                expected += ['confirm-close', 'finalize', 'absent']
                self.assertEqual(report['operations'][5]['readback_state'], 'exact')
                self.assertEqual(model.view.read_bytes(), VIEW)
                self.assertEqual(model.inputs(), ['nav-library', 'restore-pointer'])
                self.assertFalse(model.stage.exists() or model.state.exists() or base['LEASE'].exists())
                self.assertTrue(json.loads((attempt/'released.json').read_bytes())['owner_and_token_checked'])
            else:
                self.assertEqual(model.view.read_bytes(), HOME)
                self.assertTrue(model.stage.exists() and model.state.exists() and base['LEASE'].exists())
                self.assertFalse((attempt/'positive-device-closeout.json').exists() or (attempt/'released.json').exists())
                recovered = RUN['operate'](host, attempt, 'measure', source, model.payload, 500, base, lease,
                                           factory, reject, operation_lock=operation_lock, recover=True)
                self.assertTrue(recovered['recovery_required'], recovered)
                self.assertEqual([row['action'] for row in recovered['operations']], ['readback'])
                self.assertEqual(model.inputs(), ['nav-library'])
            self.assertEqual([step['action'] for step in report['operations']], expected)

    def test_close_timeout_after_full_restoration_uses_readback_not_more_input(self):
        self.replay_close_timeout(True)

    def test_close_timeout_without_restoration_retains_custody_and_does_not_click_twice(self):
        self.replay_close_timeout(False)


if __name__ == '__main__':
    unittest.main(verbosity=2)
