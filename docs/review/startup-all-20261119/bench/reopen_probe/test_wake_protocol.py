"""Exercise the bound reopen/sleep qualification with Linux host fixtures."""

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('wake_source', HERE/'reopened_wake.py');w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)


class Wake(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.base=Path(tmp.name);self.stage=self.base/'stage';self.stack=self.base/'lock'
        self.out=self.stage/'out/candidate-qualify/second';self.out.mkdir(parents=True);self.stack.mkdir()
        (self.out/'gui-owner').write_text('123 101 124\n');(self.stage/'case').write_text('candidate-qualify-second\n');(self.stack/'epoch').write_text('1\n');(self.stack/'phase').write_text('active\n');(self.stack/'nonce').write_text('host-only\n')
        self.fds=self.base/'proc/123/fd';self.fds.mkdir(parents=True);(self.fds/'4').symlink_to('/dev/input/event1');(self.fds/'5').symlink_to('/dev/input/event2')
        (self.stage/'capture').write_text('#!/bin/sh\nprintf "test-frame\\n"\n');(self.stage/'capture').chmod(0o755)

    def shell(self,code, mode='ok'):
        source=(HERE/'wake_visit.sh').read_text().replace('/proc/$wp/fd',str(self.base/'proc')+'/$wp/fd')
        shell=f'''set -eu
STAGE={self.stage}; OUT={self.out}; LOCK={self.stack}; C=candidate-qualify-second; variant=candidate; FDS={self.fds}; MODE={mode}
owner() {{ :; }}
native_wait() {{ :; }}
nonlink() {{ [ -f "$1" ] && [ ! -L "$1" ]; }}
is_app() {{ [ "$1:$2" = 123:candidate ] && [ "$MODE" != foreign ]; }}
reopen_proc() {{ r_birth=101;r_session=124; }}
lipc-get-prop() {{ if [ "$(cat "$LOCK/phase")" = parked ]; then echo screenSaver;else echo active;fi; }}
lipc-set-prop() {{
  case "$3" in powerButton) label=parked;v=true;ack=sleep-ready;rm "$FDS/5";; wakeUp) label=active;v=false;ack=resume-ready;ln -s /dev/input/event2 "$FDS/5";; *) return 1;;esac
  printf '%s\\n' "$3" >>"$STAGE/commands";echo "$label" >"$LOCK/phase";echo '123 host-only 1' >"$LOCK/$ack"
  printf '{{\\n  "platform": {{\\n    "input_released": %s,\\n    "render_paused": %s,\\n    "parked": %s\\n  }}\\n}}\\n' "$v" "$v" "$v" >"$LOCK/lifecycle-state.json"
}}
sleep() {{ :; }}
usleep() {{ :; }}
'''
        script=(shell+source+'\n'+code+'\n').replace('lipc-get-prop','mock_get').replace('lipc-set-prop','mock_set')
        return subprocess.run(['sh','-s'],input=script,text=True,capture_output=True,timeout=4)

    def test_exact_owner_and_platform_cycle(self):
        done=self.shell('reopened_sleep');self.assertEqual(done.returncode,0,done.stderr)
        self.assertEqual((self.out/'wake-owner').read_text(),'123 101 124\n'*2);self.assertEqual((self.out/'wake-input.tsv').read_text(),'active 2\nparked 1\nwake 2\n')
        self.assertEqual((self.out/'wake-platform.tsv').read_text(),'parked true true true\nwake false false false\n')
        self.assertEqual((self.stage/'commands').read_text(),'powerButton\nwakeUp\n');self.assertFalse((self.stage/'reopen-wake-pending').exists())
        self.assertEqual((self.out/'post-wake.ppm').read_bytes(),(self.out/'pre-wake.ppm').read_bytes())

    def test_foreign_identity_cannot_issue_sleep_or_wake(self):
        self.assertNotEqual(self.shell('reopened_sleep','foreign').returncode,0);self.assertFalse((self.stage/'commands').exists())
        (self.stage/'reopen-wake-pending').write_text('candidate-qualify-second\n');(self.stack/'phase').write_text('parked\n')
        self.assertNotEqual(self.shell('recover_reopened_sleep','foreign').returncode,0);self.assertFalse((self.stage/'commands').exists())

    def test_owned_recovery_wakes_once_and_is_idempotent(self):
        (self.stage/'reopen-wake-pending').write_text('candidate-qualify-second\n');(self.stack/'phase').write_text('parked\n');(self.fds/'5').unlink()
        r=self.shell('recover_reopened_sleep; recover_reopened_sleep');self.assertEqual(r.returncode,0,r.stderr);self.assertEqual((self.stage/'commands').read_text(),'wakeUp\n')

    def test_frozen_central_inputs_are_unchanged(self):
        h,d=w.derived();s=h.decode();self.assertIn("'reopened_powerd_wake':True",s);self.assertIn('if [ "$leg" = second ]; then delay=18000',s)
        self.assertIn('if [ "$role:$leg" = qualify:second ]',d.decode());self.assertNotIn(b'powerButton', (w.BASE/'bench_device.sh').read_bytes())


if __name__=='__main__':unittest.main()
