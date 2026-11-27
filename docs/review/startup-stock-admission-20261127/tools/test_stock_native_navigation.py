#!/usr/bin/env python3
"""Exercise the actual ARM OCR and XTEST tools on a private synthetic display."""

import os
from pathlib import Path
import re
import runpy
import select
import subprocess
import tempfile
import time
import unittest

from PIL import Image, ImageDraw, ImageFont


ROOT = Path('/work')
LABEL = 'bbbbbbbbbbbbbbbb'


def replace(source, before, after):
    if source.count(before) != 1:
        raise ValueError(f'Missing unique fixture anchor: {before}')
    return source.replace(before, after, 1)


class ArmNavigation(unittest.TestCase):
    def setUp(self):
        if Path.cwd() != ROOT or os.environ.get('AIDOKU_XVFB_FIXTURE') != '1' or not os.environ.get('DISPLAY', '').startswith(':'):
            self.fail('Run only in the isolated Xvfb fixture on test-env')
        fixtures = runpy.run_path(str(ROOT/'tooling/public_fixture.py'))
        self.display = fixtures['FixtureDisplay']()
        self.addCleanup(self.display.close)
        self.font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 33)
        work = tempfile.TemporaryDirectory(prefix='native-recovery-xvfb-')
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        self.state, self.stage = self.root/'state', self.root/'stage'
        kit = self.stage/'input'
        self.state.mkdir(mode=0o700)
        kit.mkdir(parents=True)
        (self.stage/'owner').write_text(LABEL+'\n')
        (self.state/'owner').write_text(LABEL+'\n')
        (self.state/'armed').write_text(LABEL+'\n')
        (self.state/'tap-intent').write_text('control-01\n')
        (self.state/'input-original').write_text('library 93 122\n')
        prepare = runpy.run_path(str(ROOT/'prepare_stock_generation.py'))
        native = prepare['measure_navigation']((ROOT/'tooling/navigation.sh').read_bytes()).decode('ascii')
        native = replace(native, ' DISPLAY=:0', '')
        native = replace(native, 'stage=/mnt/us/.aidoku-native-$label', 'stage=$FIXTURE/stage')
        native = replace(native, 'state=/var/tmp/aidoku-native-$label', 'state=$FIXTURE/state')
        start, end = native.index('native() {'), native.index('probe() {')
        native = native[:start]+'native() { same "$state/owner" "$label" && same "$stage/owner" "$label"; }\n'+native[end:]
        native = replace(native, 'native && timeout -s KILL 13 9<&- "$kit/lib/ld-linux-armhf.so.3"',
                         'native && timeout -s KILL 13 9<&- qemu-arm -cpu cortex-a9 "$kit/lib/ld-linux-armhf.so.3"')
        (kit/'navigation.sh').write_text(native)
        runtime = ROOT/'tooling'
        # Only the loader and card-input must pass the production regular-file
        # guard. The shared ARM libraries and OCR models can be linked here.
        (kit/'lib').mkdir()
        for leaf in (runtime/'lib').iterdir():
            if leaf.name != 'ld-linux-armhf.so.3':
                (kit/'lib'/leaf.name).symlink_to(leaf)
        for leaf in ('lib/ld-linux-armhf.so.3', 'card-input', 'native-target'):
            (kit/leaf).write_bytes((runtime/leaf).read_bytes())
            (kit/leaf).chmod(0o700)
        (kit/'models').symlink_to(runtime/'models')
        recovery = (ROOT/'stock_navigation_recovery.sh').read_text(encoding='ascii')
        recovery = replace(recovery, 'DISPLAY=:0 OMP_THREAD_LIMIT=1 timeout -s KILL 13',
                           'OMP_THREAD_LIMIT=1 timeout -s KILL 13 qemu-arm -cpu cortex-a9')
        fence = (ROOT/'stock_lock_fence.sh').read_text(encoding='ascii')
        self.script = self.root/'case.sh'
        self.script.write_text(r'''#!/bin/sh
set -efu
export LC_ALL=C
state=$FIXTURE/state stage=$FIXTURE/stage label=bbbbbbbbbbbbbbbb
absent() { [ ! -e "$1" ] && [ ! -L "$1" ]; }
regular() { [ -f "$1" ] && [ ! -L "$1" ] && [ "$(readlink -f "$1")" = "$1" ]; }
line() { regular "$1" && printf '%s\n' "$2" | cmp -s - "$1"; }
put() { (set -C; printf '%s\n' "$2" >"$1"); }
number() { case "$1" in ''|*[!0-9]*) return 1;; esac; }
inode() { stat -Lc '%d:%i' "$1"; }
state_owned() { line "$state/owner" "$label"; }
stage_owned() { line "$stage/owner" "$label"; }
same_boot() { :; }
close_scope() { stock_lock_is_current; }
stock_original() { :; }
no_overlay() { :; }
issued_owned_tap() { line "$state/tap-intent" control-01; }
'''+fence+'\n'+recovery+r'''
exec 9<"$state"
flock -n 9 || exit 1
case "$1" in
    inspect) stock_navigation_view;;
    recover) restore_owned_navigation;;
    exact) stock_navigation_exact;;
    *) exit 1;;
esac
''')
        self.env = dict(os.environ, FIXTURE=str(self.root), OMP_THREAD_LIMIT='1')

    def paint(self, selected):
        screen = Image.new('L', (1860, 2480), 'white')
        pen = ImageDraw.Draw(screen)
        if selected != 'unknown':
            pen.text((186, 2398), 'Home', fill=0, font=self.font)
            pen.text((572, 2398), 'Library', fill=0, font=self.font)
            if selected in ('home', 'ambiguous'):
                pen.rectangle((186, 2440, 275, 2442), fill=0)
            if selected in ('library', 'ambiguous'):
                pen.rectangle((572, 2440, 679, 2442), fill=0)
        self.display.paint(screen)

    def run_case(self, mode, expect=0):
        result = subprocess.run(['sh', self.script, mode], env=self.env, capture_output=True, timeout=35)
        self.assertEqual(result.returncode, expect, result.stderr)
        return result.stdout

    def run_with_xbutton_observer(self, *, change_tab):
        with subprocess.Popen(['stdbuf', '-oL', 'xev', '-root', '-event', 'button'],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as watcher:
            with subprocess.Popen(['sh', self.script, 'recover'], env=self.env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) as repair:
                events = b''
                repainted = False
                try:
                    deadline = time.monotonic()+65
                    while repair.poll() is None and time.monotonic() < deadline:
                        if select.select([watcher.stdout], [], [], .1)[0]:
                            events += os.read(watcher.stdout.fileno(), 8192)
                            if change_tab and b'ButtonRelease event' in events and not repainted:
                                self.paint('library')
                                repainted = True
                    out, err = repair.communicate(timeout=2)
                    self.assertEqual((repair.returncode, out), (0, b''), err)
                    self.assertEqual(repainted, change_tab, events)
                finally:
                    if repair.poll() is None:
                        repair.terminate()
                        repair.communicate(timeout=5)
                    watcher.terminate()
                    tail, _ = watcher.communicate(timeout=5)
                return events+tail

    def move(self, x, y):
        subprocess.run(['xdotool', 'mousemove', str(x), str(y)], check=True, timeout=5)

    def test_home_uses_only_the_library_button_then_restores_exact_pointer(self):
        self.paint('home')
        self.move(120, 240)
        self.assertEqual(self.run_case('inspect'), b'home 120 240\n')
        events = self.run_with_xbutton_observer(change_tab=True)
        self.assertEqual(events.count(b'ButtonPress event'), 1, events)
        self.assertEqual(events.count(b'ButtonRelease event'), 1, events)
        positions = re.findall(rb'root:\((\d+),(\d+)\)', events)
        self.assertEqual(len(positions), 2, events)
        self.assertTrue(all(572 <= int(x) <= 679 and 2398 <= int(y) <= 2440 for x,y in positions), events)
        self.assertEqual(self.run_case('inspect'), b'library 93 122\n')
        self.run_case('exact')
        self.assertEqual((self.state/'input-restore-intent').read_text(), 'home library 93 122\n')

    def test_already_on_library_moves_the_pointer_without_a_button_event(self):
        self.paint('library')
        self.move(221, 420)
        events = self.run_with_xbutton_observer(change_tab=False)
        self.assertNotIn(b'ButtonPress', events)
        self.assertNotIn(b'ButtonRelease', events)
        self.assertEqual(self.run_case('inspect'), b'library 93 122\n')
        self.assertEqual((self.state/'input-restore-intent').read_text(), 'library library 93 122\n')

    def test_unknown_ambiguous_or_held_buttons_never_authorize_navigation(self):
        self.move(221, 420)
        for mode in ('unknown', 'ambiguous', 'held'):
            with self.subTest(mode=mode):
                self.paint('home' if mode == 'held' else mode)
                if mode == 'held':
                    subprocess.run(['xdotool', 'mousedown', '1'], check=True, timeout=5)
                try:
                    self.run_case('recover', expect=1)
                    self.assertFalse((self.state/'input-restore-intent').exists())
                finally:
                    if mode == 'held':
                        subprocess.run(['xdotool', 'mouseup', '1'], check=True, timeout=5)


if __name__ == '__main__':
    unittest.main(verbosity=2)
