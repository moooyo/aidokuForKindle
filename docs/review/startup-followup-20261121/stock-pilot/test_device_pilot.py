import io
import os
from pathlib import Path
import shlex
import subprocess
import tarfile
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parent
DEVICE = ROOT / "device.sh"
DEFAULT_CASE = "control-warmup"

GUARD = """#!/bin/sh
set -eu
case "$1" in
admit)
    test ! -f "$MOCK_ROOT/guard-admit-fail"
    test ! -f "$MOCK_ROOT/guard-drift"
    printf 'admit\\n' >>"$MOCK_ROOT/guard-events"
    printf 'guard_admitted\\n';;
*) exit 1;;
esac
"""

VISIT = """#!/bin/sh
set -eu
out="$MOCK_STATE/visit-$3"
live="$MOCK_STATE/visit-live"
restore_lights() {
    for light in flIntensity currentAmberLevel; do
        old=$(cat "$out/$light")
        now=$(lipc-get-prop -i com.lab126.powerd "$light")
        if [ "$now" != "$old" ]; then
            lipc-set-prop -i com.lab126.powerd "$light" "$old"
        fi
        test "$(lipc-get-prop -i com.lab126.powerd "$light")" = "$old"
    done
}
case "$1" in
prepare)
    mkdir "$out" "$live"
    printf '%s %s\\n' "$2" "$3" >"$out/owner"
    printf '%s %s\\n' "$2" "$3" >"$live/case"
    for light in flIntensity currentAmberLevel; do
        lipc-get-prop -i com.lab126.powerd "$light" >"$out/$light"
    done
    printf 'prepare\\n' >>"$MOCK_ROOT/visit-events";;
arm)
    test ! -f "$MOCK_ROOT/arm-fail"
    printf '%s\\n' "$4" >"$out/request"
    printf 'arm\\n' >>"$MOCK_ROOT/visit-events";;
bind)
    test ! -f "$MOCK_ROOT/bind-fail"
    : >"$MOCK_ROOT/reader-active"
    printf 'bind\\n' >>"$MOCK_ROOT/visit-events";;
check)
    test -f "$MOCK_ROOT/reader-active"
    printf 'check\\n' >>"$MOCK_ROOT/visit-events";;
exit)
    test ! -f "$MOCK_ROOT/visit-exit-fail"
    restore_lights
    rm -f "$MOCK_ROOT/reader-active"
    rm -f "$live/case"
    rmdir "$live"
    printf 'exit\\n' >>"$MOCK_ROOT/visit-events";;
recover)
    test ! -f "$MOCK_ROOT/visit-recover-fail"
    restore_lights
    rm -f "$MOCK_ROOT/reader-active"
    if [ -d "$live" ]; then rm -f "$live/case"; rmdir "$live"; fi
    printf 'recover\\n' >>"$MOCK_ROOT/visit-events";;
*) exit 1;;
esac
"""

NAVIGATION = """#!/bin/sh
set -eu
case "$1" in
library)
    test ! -f "$MOCK_ROOT/navigation-fail"
    if [ ! -f "$MOCK_STATE/input-original" ]; then
        cat "$MOCK_ROOT/pointer" >"$MOCK_STATE/input-original"
    fi
    printf 'library 200 300\\n' >"$MOCK_ROOT/pointer"
    printf 'library\\n' >>"$MOCK_ROOT/input-events";;
restore)
    if [ -f "$MOCK_STATE/input-original" ]; then
        cat "$MOCK_STATE/input-original" >"$MOCK_ROOT/pointer"
        rm "$MOCK_STATE/input-original"
    fi
    printf 'restore\\n' >>"$MOCK_ROOT/input-events";;
*) exit 1;;
esac
"""

DISPATCH = """#!/bin/sh
set -eu
out="$MOCK_STATE/out/$3"
case "$1" in
target)
    test ! -f "$MOCK_ROOT/unknown-target"
    printf '%s\\n' "$3" >"$out/unique-tile";;
tap)
    test -f "$out/unique-tile"
    if [ -f "$MOCK_ROOT/dispatch-fail" ]; then
        : >"$MOCK_ROOT/reader-active"
        exit 1
    fi
    printf '101 202 303 404\\n' >"$out/pointer-brackets"
    printf '701 702\\n';;
*) exit 1;;
esac
"""

FRAMEWATCH = """#!/bin/sh
set -eu
case "$1" in
capture)
    printf 'fixture frame\\n' >"$2";;
watch)
    name=watcher
    : >"$MOCK_ROOT/$name.active"
    trap 'rm -f "$MOCK_ROOT/$name.active"' EXIT
    if [ -e "/proc/$$/fd/7" ]; then : >"$MOCK_ROOT/$name-fd7"; fi
    printf 'READY\\n' >&2
    if [ -f "$MOCK_ROOT/slow-children" ]; then /bin/sleep 0.7; else /bin/sleep 0.04; fi
    printf 'fixture trace\\n' >"$2";;
clock)
    if [ -f "$MOCK_ROOT/clock-started" ]; then
        printf '60000\\n'
    else
        : >"$MOCK_ROOT/clock-started"
        printf '50000\\n'
    fi;;
*) exit 1;;
esac
"""

TOUCH_WITNESS = """#!/bin/sh
set -eu
[ "$1" = watch ] && [ "$2" = auto ] && [ "$3" = 12000 ] || exit 2
name=touch-witness
: >"$MOCK_ROOT/$name.active"
trap 'rm -f "$MOCK_ROOT/$name.active"' EXIT
if [ -e "/proc/$$/fd/7" ]; then : >"$MOCK_ROOT/$name-fd7"; fi
printf 'READY\\n' >&2
if [ -f "$MOCK_ROOT/slow-children" ]; then /bin/sleep 0.7; else /bin/sleep 0.04; fi
if [ -f "$MOCK_ROOT/no-finger" ]; then
    printf 'ATW1 TIMEOUT 1000 12000 0 0 0 0 8\\n'
else
    printf 'ATW1 SINGLE 1000 12000 2000 3000 4000 5000 8\\n'
fi
"""

TILE_INPUT = "#!/bin/sh\nexit 0\n"


class DevicePilotFixture(unittest.TestCase):
    def setUp(self):
        private = tempfile.TemporaryDirectory(prefix="aidoku-stock-pilot-")
        self.addCleanup(private.cleanup)
        self.root = Path(private.name)
        self.label = "91c34a7d02be865f"
        self.media = self.root / "media"
        self.private = self.root / "private"
        self.bin = self.root / "bin"
        self.locks = self.root / "locks"
        for path in (self.media, self.private, self.bin, self.locks):
            path.mkdir(mode=0o700)
        self.stage = self.media / f".aidoku-native-{self.label}"
        self.state = self.private / f"aidoku-native-{self.label}"
        self.home = self.media / "aidoku"
        self.pointer = self.root / "pointer"
        self.pointer.write_text("home 99 104\n", encoding="ascii")
        self._create_installed_control()
        self.modules = {
            "visit/visit.sh": VISIT,
            "observer/framewatch": FRAMEWATCH,
            "control/program-modes": "755\n755\n755\n",
            "control/launch.sh": "fixture launch control\n",
            "control/app/aidoku": "fixture aidoku control\n",
            "control/app/aidoku-startup-portrait": "fixture portrait control\n",
            "guard/guard.sh": GUARD,
            "input/navigation.sh": NAVIGATION,
            "input/dispatch.sh": DISPATCH,
            "input/artwork.guard": "fixture artwork guard\n",
            "input/tile-input": TILE_INPUT,
            "input/touch-witness": TOUCH_WITNESS,
        }
        shell = DEVICE.read_text(encoding="ascii")
        shell = shell.replace("media=/mnt/us", f"media={self.media}")
        shell = shell.replace("private=/var/tmp", f"private={self.private}")
        shell = shell.replace('"/tmp/$reader-launcher.lock"', f'"{self.locks}/$reader-launcher.lock"')
        for dangerous in ("media=/mnt/us", "private=/var/tmp", '"/tmp/$reader-launcher.lock"'):
            if dangerous in shell:
                raise AssertionError(f"Unredirected device path: {dangerous}")
        self.shell = shell
        self._write_shims()
        self.environment = {
            **os.environ,
            "MOCK_ROOT": str(self.root),
            "MOCK_STATE": str(self.state),
            "PATH": f"{self.bin}:/usr/bin:/bin",
        }

    def _create_installed_control(self):
        for relative, content in (
            ("launch.sh", b"fixture launch control\n"),
            ("app/aidoku", b"fixture aidoku control\n"),
            ("app/aidoku-startup-portrait", b"fixture portrait control\n"),
        ):
            target = self.home / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            target.chmod(0o755)

    def _shim(self, name, contents):
        path = self.bin / name
        path.write_text(contents, encoding="ascii")
        path.chmod(0o755)

    def _write_shims(self):
        helper = self.root / "tar_helper.py"
        helper.write_text(
            """import pathlib
import sys
import tarfile

arguments = sys.argv[1:]
if arguments[:3] == ["-xzf", "-", "-C"] and len(arguments) == 4:
    with tarfile.open(fileobj=sys.stdin.buffer, mode="r:gz") as archive:
        archive.extractall(path=arguments[3], filter="data")
elif arguments[:3] == ["-czf", "-", "-C"] and len(arguments) >= 5:
    root = pathlib.Path(arguments[3])
    with tarfile.open(fileobj=sys.stdout.buffer, mode="w:gz") as archive:
        for relative in arguments[4:]:
            archive.add(root / relative, arcname=relative, recursive=False)
else:
    raise SystemExit(2)
""",
            encoding="ascii",
        )
        self._shim(
            "tar",
            f"#!/bin/sh\nexec {shlex.quote(os.sys.executable)} \"$MOCK_ROOT/tar_helper.py\" \"$@\"\n",
        )
        self._shim(
            "lipc-get-prop",
            """#!/bin/sh
case "$*" in
    *"powerd state") if [ -f "$MOCK_ROOT/asleep" ]; then echo screenSaver; else echo active; fi;;
    *"activeApp") if [ -f "$MOCK_ROOT/other-app" ]; then echo another; else echo com.lab126.KPPMainApp; fi;;
    *flIntensity) cat "$MOCK_ROOT/light-flIntensity";;
    *currentAmberLevel) cat "$MOCK_ROOT/light-currentAmberLevel";;
    *) exit 1;;
esac
""",
        )
        self._shim(
            "lipc-set-prop",
            """#!/bin/sh
case "$*" in
    *touchScreenSaverTimeout*) printf 'touch\\n' >>"$MOCK_ROOT/native-events";;
    *) printf '%s\\n' "$4" >"$MOCK_ROOT/light-$3"; printf 'light %s %s\\n' "$3" "$4" >>"$MOCK_ROOT/native-events";;
esac
""",
        )
        self._shim("status", "#!/bin/sh\nprintf 'statusbar start/running\\n'\n")
        self._shim(
            "pidof",
            """#!/bin/sh
case "${1:-}" in
    aidoku|weread|bilicomics)
        if [ -f "$MOCK_ROOT/reader-active" ]; then printf '32001\\n'; exit 0; else exit 1; fi;;
    *) exit 1;;
esac
""",
        )
        self._shim("usleep", "#!/bin/sh\n/bin/sleep 0.001\n")
        self._shim(
            "flock",
            """#!/bin/sh
exec /usr/bin/flock "$@"
""",
        )
        for name, value in (("light-flIntensity", "5\n"), ("light-currentAmberLevel", "0\n")):
            (self.root / name).write_text(value, encoding="ascii")

    def payload(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for name, source in self.modules.items():
                data = source.encode("ascii")
                member = tarfile.TarInfo(name)
                member.size = len(data)
                member.mode = 0o755
                archive.addfile(member, io.BytesIO(data))
        return buffer.getvalue()

    def command(self, operation, *args, data=b"", expect=True):
        result = subprocess.run(
            ["/bin/sh", "-c", self.shell, "device", operation, self.label, *args],
            input=data,
            capture_output=True,
            env=self.environment,
            timeout=30,
        )
        self.assertEqual(result.returncode == 0, expect, (operation, result.stdout, result.stderr))
        return result.stdout

    def prepare(self):
        return self.command("prepare", "tapcheck", data=self.payload())

    def close(self):
        return self.command("close")

    def assert_no_helper_children(self):
        deadline = time.monotonic() + 3
        active = (self.root / "watcher.active", self.root / "touch-witness.active")
        while any(path.exists() for path in active) and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(any(path.exists() for path in active))
        self.assertFalse((self.root / "watcher-fd7").exists())
        self.assertFalse((self.root / "touch-witness-fd7").exists())
        self.assertFalse((self.root / "reader-active").exists())

    def test_normal_pilot_collects_touch_and_independent_four_clock_brackets(self):
        self.assertEqual(self.prepare(), b"pilot_prepared\n")
        self.assertEqual(self.stage.parent, self.media)
        self.assertEqual(self.state.parent, self.private)
        self.assertNotEqual(self.label, "eba90e97ecba84cf")
        self.assertEqual(self.command("pilot", "500", "require-touch"), b"pilot_completed\n")
        archive_bytes = self.command("collect-case", DEFAULT_CASE)
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
            members = {item.name: archive.extractfile(item).read() for item in archive}
        prefix = f"out/{DEFAULT_CASE}/"
        required = {
            "origin",
            "anchor",
            "pointer-brackets",
            "qualified",
            "stock.frame",
            "trace",
            "control.before.frame",
            "control.after.frame",
            "control.anchor",
            "control.method",
            "touch",
        }
        self.assertEqual({name.removeprefix(prefix) for name in members}, required)
        self.assertEqual(members[prefix + "pointer-brackets"], b"101 202 303 404\n")
        self.assertEqual(members[prefix + "anchor"], b"101 202 303 404\n")
        self.assertNotEqual(
            members[prefix + "pointer-brackets"],
            (self.state / "out" / DEFAULT_CASE / "request.anchor").read_bytes(),
        )
        self.assertTrue(members[prefix + "touch"].startswith(b"ATW1 SINGLE "))
        self.assertEqual(
            self.command("close"),
            b"pilot_closed\n",
        )
        self.assertEqual(self.pointer.read_text(encoding="ascii"), "home 99 104\n")
        self.assertIn("admit", (self.root / "guard-events").read_text(encoding="ascii"))
        self.assertFalse(self.stage.exists() or self.state.exists())
        self.assert_no_helper_children()
        self.assertEqual(self.command("absent"), b"pilot_absent\n")

    def test_unrecognized_target_failure_closes_and_restores_owned_input(self):
        self.prepare()
        (self.root / "unknown-target").touch()
        self.command("pilot", "500", "require-touch", expect=False)
        self.assertTrue(self.state.is_dir() and self.stage.is_dir())
        self.close()
        self.assertEqual(self.pointer.read_text(encoding="ascii"), "home 99 104\n")
        self.assertIn("recover", (self.root / "visit-events").read_text(encoding="ascii"))
        self.assertFalse(self.stage.exists() or self.state.exists())
        self.assert_no_helper_children()

    def test_no_finger_timeout_fails_closed_but_can_close_cleanly(self):
        self.prepare()
        (self.root / "no-finger").touch()
        self.command("pilot", "500", "require-touch", expect=False)
        self.assertTrue(self.state.is_dir() and self.stage.is_dir())
        self.close()
        self.assertEqual(self.pointer.read_text(encoding="ascii"), "home 99 104\n")
        self.assertFalse(self.stage.exists() or self.state.exists())
        self.assert_no_helper_children()

    def test_arm_failure_can_close_without_nested_stage_lock(self):
        self.prepare()
        (self.root / "arm-fail").touch()
        self.command("pilot", "500", "require-touch", expect=False)
        started = time.monotonic()
        self.close()
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertFalse(self.stage.exists() or self.state.exists())
        self.assertEqual(self.pointer.read_text(encoding="ascii"), "home 99 104\n")
        self.assert_no_helper_children()

    def test_dispatch_failure_closes_reader_and_fd7_is_not_inherited(self):
        self.prepare()
        (self.root / "dispatch-fail").touch()
        (self.root / "slow-children").touch()
        self.command("pilot", "500", "require-touch", expect=False)
        self.assertTrue((self.root / "reader-active").exists())
        started = time.monotonic()
        self.close()
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertFalse(self.stage.exists() or self.state.exists())
        self.assertEqual(self.pointer.read_text(encoding="ascii"), "home 99 104\n")
        self.assert_no_helper_children()

    def test_guard_native_and_frontlight_rechecks_block_close_until_safe(self):
        self.prepare()
        (self.root / "unknown-target").touch()
        self.command("pilot", "500", "require-touch", expect=False)
        (self.root / "guard-admit-fail").touch()
        self.command("close", expect=False)
        self.assertTrue(self.stage.is_dir() and self.state.is_dir())
        (self.root / "guard-admit-fail").unlink()
        (self.root / "guard-drift").touch()
        self.command("close", expect=False)
        self.assertTrue(self.stage.is_dir() and self.state.is_dir())
        (self.root / "guard-drift").unlink()
        (self.root / "other-app").touch()
        (self.root / "light-flIntensity").write_text("12\n", encoding="ascii")
        (self.root / "light-currentAmberLevel").write_text("3\n", encoding="ascii")
        self.command("close", expect=False)
        self.assertTrue(self.stage.is_dir() and self.state.is_dir())
        self.assertEqual((self.root / "light-flIntensity").read_text(encoding="ascii"), "5\n")
        self.assertEqual((self.root / "light-currentAmberLevel").read_text(encoding="ascii"), "0\n")
        self.assertFalse((self.root / "reader-active").exists())
        (self.root / "other-app").unlink()
        self.close()
        self.assertFalse(self.stage.exists() or self.state.exists())
        self.assert_no_helper_children()


if __name__ == "__main__":
    unittest.main(verbosity=2)
