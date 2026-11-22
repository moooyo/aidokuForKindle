import os
from pathlib import Path
import subprocess
import tempfile
import unittest


PROBE = Path(__file__).with_name('stock_admission_probe.sh')
LABEL = '0123456789abcdef'
GUARD = b'''[ "$#:$1:$2" = "2:admit:0123456789abcdef" ] || exit 64
printf '%s' "$GUARD_REPLY" >&2
exit "$GUARD_STATUS"
'''


class StockAdmissionProbeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.calls = root / 'calls'
        self.calls.touch()
        source = '''#!/bin/sh
printf '%s\\n' "$*" >>"$CALLS"
printf 'private fixture diagnostic\\n' >&2
case "$*" in
    '-s com.lab126.powerd state') printf '%s' "$POWER"; exit "$POWER_STATUS" ;;
    '-s com.lab126.appmgrd activeApp') printf '%s' "$APP"; exit "$APP_STATUS" ;;
    'statusbar') printf '%s' "$BAR"; exit "$BAR_STATUS" ;;
    *) exit 64 ;;
esac
'''
        for name in ('lipc-get-prop', 'status'):
            path = root / name
            path.write_text(source)
            path.chmod(0o700)
        self.env = dict(os.environ, PATH=f'{root}:/usr/bin:/bin', CALLS=str(self.calls),
                        GUARD_REPLY='refused guard_admission\n', GUARD_STATUS='1',
                        POWER='active', APP='com.lab126.KPPMainApp', BAR='statusbar start/running',
                        POWER_STATUS='0', APP_STATUS='0', BAR_STATUS='0')

    def check(self, expected, calls, **changes):
        self.calls.write_text('')
        result = subprocess.run(['sh', str(PROBE), LABEL], input=GUARD, env=dict(self.env, **changes),
                                capture_output=True, timeout=2, check=True)
        self.assertEqual((result.stdout, result.stderr), (f'STOCK_DIAG1 {expected}\n'.encode(), b''))
        self.assertEqual(self.calls.read_text().splitlines(), [
            '-s com.lab126.powerd state', '-s com.lab126.appmgrd activeApp', 'statusbar'][:calls])

    def test_no_getters_on_success_lights_or_ambiguous_reply(self):
        self.check('guard_passed', 0, GUARD_REPLY='guard_admitted\n', GUARD_STATUS='0')
        self.check('guard_lights', 0, GUARD_REPLY='refused guard_lights\n')
        self.check('guard_protocol_unknown', 0, GUARD_REPLY='private detail\nrefused guard_admission\n')
        self.check('guard_protocol_unknown', 0, GUARD_STATUS='255')
        self.check('guard_protocol_unknown', 0, GUARD_REPLY='refused guard_admission')

    def test_failed_or_unavailable_predicates_never_disclose_values(self):
        self.check('power_not_active', 1, POWER='private-power-value')
        self.check('power_unobserved', 1, POWER_STATUS='1')
        self.check('foreground_not_stock', 2, APP='private-app-name')
        self.check('foreground_unobserved', 2, APP='')
        self.check('statusbar_not_running', 3, BAR='private-service-value')
        self.check('statusbar_unobserved', 3, BAR_STATUS='3')
        self.check('other_native_or_protection_or_state_changed', 3)

    def test_invalid_label_stops_before_reading_guard(self):
        result = subprocess.run(['sh', str(PROBE), 'bad'], input=b'printf unsafe', capture_output=True,
                                env=self.env, timeout=2)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (64, b'', b''))
        self.assertEqual(self.calls.read_text(), '')


if __name__ == '__main__':
    unittest.main()
