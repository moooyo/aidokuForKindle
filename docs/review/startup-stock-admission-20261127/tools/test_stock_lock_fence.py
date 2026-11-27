#!/usr/bin/env python3
"""Verify the proposed stock lock fence with actual directory flocks on Linux."""

import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest


FENCE = Path(__file__).with_name('stock_lock_fence.sh').read_bytes()
SIMULATION = br'''#!/bin/sh
set -efu
root=$1; mode=$2
state=$root/state
absent() { [ ! -e "$1" ] && [ ! -L "$1" ]; }
state_owned() { [ -d "$state" ] && [ ! -L "$state" ] && [ "$(stat -c %a "$state")" = 700 ] && cmp -s "$root/owner" "$state/owner"; }
same_boot() { cmp -s "$root/boot" "$state/boot"; }
inode() { stat -Lc '%d:%i' "$1"; }
'''+FENCE+br'''
state_owned && same_boot || exit 4
exec 9<"$state"
case "$mode" in
    delayed)
        : >"$root/open"
        for n in $(seq 1 150); do
            [ ! -e "$root/resume" ] || break
            sleep 0.03
        done
        [ -f "$root/resume" ] || exit 8
        flock -n 9 || exit 5
        stock_run_may_stage || exit 6
        : >"$root/staged" ;;
    close)
        flock -n 9 && stock_lock_is_current || exit 5
        : >"$state/closed" ;;
    finalize)
        flock -n 9 && stock_lock_is_current && [ -f "$state/closed" ] || exit 5
        rm -rf "$state" ;;
esac
'''


class OperationRaceTests(unittest.TestCase):
    def trial(self, action, expected):
        with tempfile.TemporaryDirectory(prefix='aidoku-gen-flock-') as path:
            root = Path(path)
            state = root/'state'
            script = root/'fixture.sh'
            script.write_bytes(SIMULATION)
            (root/'owner').write_text('fictional-owner\n')
            (root/'boot').write_text('fictional-boot\n')

            def create_state():
                state.mkdir(mode=0o700)
                for key in ('boot', 'owner'):
                    (state/key).write_bytes((root/key).read_bytes())

            def call(mode):
                subprocess.run(['sh', str(script), str(root), mode], check=True, capture_output=True, timeout=4)

            create_state()
            with subprocess.Popen(['sh', str(script), str(root), 'delayed'], stderr=subprocess.PIPE) as late:
                try:
                    deadline = time.monotonic() + 4
                    while not (root/'open').is_file() and late.poll() is None and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertTrue((root/'open').is_file())
                    if action in ('closed', 'unlinked', 'replaced'):
                        call('close')
                    if action in ('unlinked', 'replaced'):
                        call('finalize')
                    if action == 'replaced':
                        create_state()
                    if action == 'boot_changed':
                        (state/'boot').write_text('different-boot\n')
                    if action == 'owner_changed':
                        (state/'owner').write_text('different-owner\n')
                    (root/'resume').touch()
                    _, stderr = late.communicate(timeout=6)
                    self.assertEqual((late.returncode, stderr), (expected, b''))
                    self.assertEqual((root/'staged').exists(), expected == 0)
                finally:
                    if late.poll() is None:
                        (root/'resume').touch(exist_ok=True)
                        late.communicate(timeout=6)

    def test_current_open_directory_can_be_used(self):
        self.trial('current', 0)

    def test_closed_named_directory_cannot_stage(self):
        self.trial('closed', 6)

    def test_unlinked_locked_inode_cannot_stage(self):
        self.trial('unlinked', 6)

    def test_replacement_with_same_owner_and_boot_cannot_stage(self):
        self.trial('replaced', 6)

    def test_same_inode_with_changed_boot_or_owner_cannot_stage(self):
        for word in ('boot', 'owner'):
            with self.subTest(word=word):
                self.trial(word+'_changed', 6)


if __name__ == '__main__':
    if os.name != 'posix':
        raise SystemExit('Use the designated Linux test host')
    unittest.main(verbosity=2)
