import io
import os
from pathlib import Path
import shlex
import subprocess
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent
DEVICE = ROOT / "device.sh"

GUARD = """#!/bin/sh
set -eu
case "$1" in
capture)
    test ! -f "$MOCK_ROOT/guard-capture-fail"
    mkdir -p "$MOCK_STATE/guard"
    printf '%s\\n' "$2" >"$MOCK_STATE/guard/ready"
    printf 'capture\\n' >>"$MOCK_ROOT/guard-events"
    printf 'guard_captured\\n';;
verify)
    test ! -f "$MOCK_ROOT/guard-verify-fail"
    printf 'verify\\n' >>"$MOCK_ROOT/guard-events"
    printf 'guard_verified\\n';;
restore)
    test ! -f "$MOCK_ROOT/guard-restore-fail"
    printf 'restore\\n' >>"$MOCK_ROOT/guard-events"
    printf 'guard_verified\\n';;
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
    printf 'stock-tap\\n' >"$out/dispatched"
    printf '101 202 303 404\\n' >"$out/pointer-brackets"
    printf '701 702\\n';;
*) exit 1;;
esac
"""

RENDER_WATCH = """#!/bin/sh
set -eu
rule=${1:?}
test -f "$rule"
name=render-watch
: >"$MOCK_ROOT/$name.active"
trap 'rm -f "$MOCK_ROOT/$name.active"' EXIT
if [ -e "/proc/$$/fd/7" ]; then : >"$MOCK_ROOT/$name-fd7"; fi
printf 'READY\\n' >&2
if [ -f "$MOCK_ROOT/render-bad" ]; then
    printf 'ARL1 10 bad 30 MATCH MATCH\\n'
    exit 0
fi
if [ -f "$MOCK_ROOT/render-timeout" ]; then
    printf 'ARL1_ERR TIMEOUT\\n' >&2
    exit 1
fi
printf '%s\\n' "${MOCK_RENDER_LINE:-ARL1 1000 2000 2500 MATCH DIFFER}"
"""

TILE_INPUT = "#!/bin/sh\nexit 0\n"


class DeviceRenderedPilotFixture(unittest.TestCase):
    def setUp(self):
        private = tempfile.TemporaryDirectory(prefix="aidoku-rendered-pilot-")
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
            "control/program-modes": "755\n755\n755\n",
            "control/launch.sh": "fixture launch control\n",
            "control/app/aidoku": "fixture aidoku control\n",
            "control/app/aidoku-startup-portrait": "fixture portrait control\n",
            "guard/guard.sh": GUARD,
            "input/navigation.sh": NAVIGATION,
            "input/dispatch.sh": DISPATCH,
            "input/artwork.guard": "fixture artwork guard\n",
            "input/tile-input": TILE_INPUT,
            "input/render-watch": RENDER_WATCH,
            "input/reference.rule": "fixture reference rule\n",
        }
        shell = DEVICE.read_text(encoding="ascii")
        shell = shell.replace("media=/mnt/us", f"media={self.media}")
        shell = shell.replace("private=/var/tmp", f"private={self.private}")
        shell = shell.replace('"/tmp/$reader-launcher.lock"', f'"{self.locks}/$reader-launcher.lock"')
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
        self._shim("tar", f"#!/bin/sh\nexec {shlex.quote(os.sys.executable)} \"$MOCK_ROOT/tar_helper.py\" \"$@\"\n")
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
        self._shim("flock", "#!/bin/sh\nexec /usr/bin/flock \"$@\"\n")
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

    def assert_no_logger(self):
        self.assertFalse((self.root / "render-watch.active").exists())
        self.assertFalse((self.root / "render-watch-fd7").exists())

    def test_normal_visit_collects_log_only_members(self):
        self.assertEqual(self.prepare(), b"render_prepared\n")
        self.assertEqual(self.command("visit", "control-01"), b"render_completed\n")
        archive_bytes = self.command("collect", "control-01")
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
            members = {item.name: archive.extractfile(item).read() for item in archive}
        prefix = "out/control-01/"
        required = {
            "origin",
            "anchor",
            "pointer-brackets",
            "qualified",
            "rendered-library",
        }
        self.assertEqual({name.removeprefix(prefix) for name in members}, required)
        self.assertEqual(members[prefix + "pointer-brackets"], b"101 202 303 404\n")
        self.assertEqual(members[prefix + "anchor"], b"101 202 303 404\n")
        self.assertEqual(members[prefix + "rendered-library"], b"ARL1 1000 2000 2500 MATCH DIFFER\n")
        self.assertEqual(self.close(), b"render_closed\n")
        self.assertEqual(self.command("absent"), b"render_absent\n")
        self.assertEqual(self.pointer.read_text(encoding="ascii"), "home 99 104\n")
        self.assert_no_logger()

    def test_invalid_case_name_is_rejected(self):
        self.prepare()
        self.command("visit", "case-b", expect=False)
        self.close()
        self.assert_no_logger()

    def test_bad_render_line_fails_closed_then_recovers(self):
        self.prepare()
        (self.root / "render-bad").touch()
        self.command("visit", "control-02", expect=False)
        self.assertTrue(self.stage.is_dir() and self.state.is_dir())
        self.close()
        self.assertFalse(self.stage.exists() or self.state.exists())
        self.assert_no_logger()

    def test_close_restores_guard_even_without_ready_marker(self):
        self.prepare()
        (self.state / "ready").unlink()
        self.assertEqual(self.close(), b"render_closed\n")
        events = (self.root / "guard-events").read_text(encoding="ascii")
        self.assertIn("capture\n", events)
        self.assertIn("restore\n", events)
        self.assertIn("verify\n", events)
        self.assertEqual(self.command("absent"), b"render_absent\n")
        self.assert_no_logger()

    def test_close_succeeds_after_prepare_failure_without_snapshot(self):
        self.modules.pop("control/program-modes")
        self.command("prepare", "tapcheck", data=self.payload(), expect=False)
        self.assertTrue(self.stage.is_dir() and self.state.is_dir())
        self.assertEqual(self.close(), b"render_closed\n")
        self.assertEqual(self.command("absent"), b"render_absent\n")
        self.assert_no_logger()

    def test_logger_failure_uses_recover_on_close(self):
        self.prepare()
        (self.root / "render-timeout").touch()
        self.command("visit", "control-03", expect=False)
        self.assertTrue(self.stage.is_dir() and self.state.is_dir())
        self.close()
        events = (self.root / "visit-events").read_text(encoding="ascii")
        self.assertIn("recover\n", events)
        self.assertFalse(self.stage.exists() or self.state.exists())
        self.assert_no_logger()


if __name__ == "__main__":
    unittest.main(verbosity=2)
