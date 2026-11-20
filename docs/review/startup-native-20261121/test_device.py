import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent
LABEL = "1234567890abcdef"
GET_NATIVE = """case "$*" in
    '-s com.lab126.powerd state')
        if [ -e "$MOCK_ROOT/asleep" ]; then echo screenSaver; else echo active; fi;;
    '-s com.lab126.appmgrd activeApp')
        if [ -e "$MOCK_ROOT/other-app" ]; then echo another; else echo com.lab126.KPPMainApp; fi;;
    *) exit 99;; esac"""
SET_NATIVE = """case "$*" in
    '-i com.lab126.powerd touchScreenSaverTimeout 1')
        printf 'idle\\n' >>"$MOCK_ROOT/touches"
        test ! -f "$MOCK_ROOT/reject-touch";;
    'com.lab126.appmgrd start file:///mnt/us/documents/Aidoku.sh') echo open >>"$MOCK_ROOT/requests";;
    *) exit 99;; esac"""
GUARD = """#!/bin/sh
set -eu
case "$1" in
capture) mkdir "$MOCK_STATE/guard"; printf '%s\\n' "$2" >"$MOCK_STATE/guard/ready";;
restore) test ! -f "$MOCK_ROOT/foreign";;
esac
"""
PROGRAMS = """#!/bin/sh
set -eu
case "$1" in
candidate) printf candidate >"$MOCK_ROOT/tuple";;
restore|control) test ! -f "$MOCK_ROOT/changed"; printf control >"$MOCK_ROOT/tuple";;
esac
"""
VISIT = """#!/bin/sh
set -eu
out="$MOCK_STATE/visit-$3"; live="$MOCK_STATE/visit-live"
case "$1" in
prepare) mkdir "$out" "$live"; printf '%s %s\\n' "$2" "$3" >"$out/owner";;
arm) echo "$4" >"$out/request";;
bind) test ! -f "$MOCK_ROOT/no-bind";;
recover|exit) test ! -f "$MOCK_ROOT/foreign"; rmdir "$live";;
esac
"""
FRAME = """#!/bin/sh
set -eu
case "$1" in
capture) test ! -f "$MOCK_ROOT/no-frame"; echo captured >"$2";;
watch) echo READY >&2; /bin/sleep 0.1; echo trace >"$2";;
clock) echo 40001;;
esac
"""
NAVIGATION = """#!/bin/sh
set -eu
case "$1" in
library)
    test ! -f "$MOCK_ROOT/unknown"
    if test ! -f "$MOCK_STATE/input-original"; then cat "$MOCK_ROOT/pointer" >"$MOCK_STATE/input-original"; fi
    echo 'library 200 300' >"$MOCK_ROOT/pointer"; echo navigated >>"$MOCK_ROOT/input-events";;
restore)
    test -f "$MOCK_STATE/input-original" || exit 0
    test ! -f "$MOCK_ROOT/foreign"; cat "$MOCK_STATE/input-original" >"$MOCK_ROOT/pointer"
    echo returned >>"$MOCK_ROOT/input-events";;
esac
"""
TILE = """#!/bin/sh
set -eu
case "$1" in
target) test ! -f "$MOCK_ROOT/unknown-card";;
tap) test ! -f "$MOCK_ROOT/unknown-card"; echo tile >>"$MOCK_ROOT/input-events"; echo '51000 51200';;
esac
"""


class DeviceDispatch(unittest.TestCase):
    def setUp(self):
        private = tempfile.TemporaryDirectory()
        self.addCleanup(private.cleanup)
        self.root = Path(private.name)
        self.media = self.root / "media"
        self.var = self.root / "var"
        self.bin = self.root / "bin"
        for directory in (self.media, self.var, self.bin):
            directory.mkdir()
        self.stage = self.media / (".aidoku-native-" + LABEL)
        self.state = self.var / ("aidoku-native-" + LABEL)
        self.shell = (ROOT / "device.sh").read_text().replace("media=/mnt/us", f"media={self.media}").replace(
            "private=/var/tmp", f"private={self.var}")
        self.modules = {"guard/guard.sh": GUARD, "programs/programs.sh": PROGRAMS,
                        "rename-new": "unused", "visit/visit.sh": VISIT, "observer/framewatch": FRAME}
        self.modules.update({"input/navigation.sh": NAVIGATION, "input/dispatch.sh": TILE})
        self.environment = dict(os.environ, MOCK_ROOT=str(self.root), MOCK_STATE=str(self.state),
                                PATH=f"{self.bin}:{os.environ['PATH']}")
        for filename, body in {"usleep": "sleep 0.01", "sleep": ":", "lipc-get-prop": GET_NATIVE,
                               "lipc-set-prop": SET_NATIVE}.items():
            path = self.bin / filename
            path.write_text("#!/bin/sh\n" + body + "\n")
            path.chmod(0o755)
        self.events = self.root / "requests"
        self.pointer = self.root / "pointer"
        self.pointer.write_text("home 99 104\n")

    def payload(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for name, raw in self.modules.items():
                member = tarfile.TarInfo(name)
                data = raw.encode()
                member.size, member.mode = len(data), 0o755
                archive.addfile(member, io.BytesIO(data))
        return buffer.getvalue()

    def command(self, operation, *args, data=b"", expect=True):
        result = subprocess.run(["sh", "-c", self.shell, "device", operation, LABEL, *args], input=data,
                                capture_output=True, env=self.environment, timeout=30)
        self.assertEqual(result.returncode == 0, expect, (operation, result.stderr))
        return result.stdout

    def install_tapcheck_fixture(self):
        del self.modules["programs/programs.sh"], self.modules["rename-new"]
        installed = []
        for name in ("app/aidoku", "launch.sh", "app/aidoku-startup-portrait"):
            leaf = self.media / "aidoku" / name
            leaf.parent.mkdir(exist_ok=True, parents=True)
            leaf.write_text(name)
            leaf.chmod(0o755)
            self.modules["control/" + name] = name
            installed.append(leaf)
        self.modules["control/program-modes"] = "755\n" * len(installed)
        return installed

    def test_idle_deadline_is_touched_only_before_upload_and_first_navigation(self):
        order = self.root / "order"
        upload = self.bin / "tar"
        upload.write_text('#!/bin/sh\n[ "$1" != -xzf ] || echo upload >>"$MOCK_ROOT/order"\nexec /bin/tar "$@"\n')
        upload.chmod(0o755)
        call = self.bin / "lipc-set-prop"
        call.write_text(call.read_text().replace("printf 'idle", "echo touch >>\"$MOCK_ROOT/order\"; printf 'idle"))
        self.modules["input/navigation.sh"] = NAVIGATION.replace("library)", 'library) echo navigation >>"$MOCK_ROOT/order"')
        self.modules["observer/framewatch"] = FRAME.replace("watch)", 'watch) echo sampler >>"$MOCK_ROOT/order";')
        self.modules["input/dispatch.sh"] = TILE.replace("tap)", 'tap) echo down >>"$MOCK_ROOT/order";')
        self.command("prepare", "study", data=self.payload())
        self.command("case", "control-warmup", "stock-tap")
        self.command("case", "candidate-warmup", "stock-tap")
        self.command("close")
        self.assertEqual(order.read_text().split(), [
            "touch", "upload", "touch", "navigation", "sampler", "down", "navigation", "sampler", "down"])

    def test_idle_reset_refuses_sleep_or_other_foreground_without_waking_or_uploading(self):
        touches = self.root / "touches"
        for flag in ("asleep", "other-app"):
            marker = self.root / flag
            marker.touch()
            self.command("prepare", "study", data=self.payload(), expect=False)
            self.assertFalse((self.stage / "guard/guard.sh").exists() or touches.exists())
            self.command("close")
            marker.unlink()
        self.command("prepare", "study", data=self.payload())
        (self.root / "asleep").touch()
        self.command("picture", "control-picture", "stock-tap", expect=False)
        self.assertFalse((self.state / "pointer-intent").exists() or self.events.exists())
        self.command("close")
        self.assertEqual(touches.read_text(), "idle\n")

    def test_idle_signal_failure_is_not_retried_by_close(self):
        (self.root / "reject-touch").touch()
        self.command("prepare", "study", data=self.payload(), expect=False)
        self.assertFalse((self.stage / "guard/guard.sh").exists() or self.events.exists())
        self.command("close")
        self.assertEqual((self.root / "touches").read_text(), "idle\n")

    def test_tapcheck_only_clicks_once_and_never_switches(self):
        installed = self.install_tapcheck_fixture()
        def originals():
            return [(leaf.read_bytes(), leaf.stat().st_ino, leaf.stat().st_mode) for leaf in installed]
        initial = originals()
        self.command("prepare", "tapcheck", data=self.payload())
        self.assertEqual((self.state / "mode").read_text(), "tapcheck\n")
        for operation, name, origin in (("case", "candidate-warmup", "stock-tap"), ("case", "control-01", "stock-tap"),
                                        ("case", "control-warmup", "indexed-uri"), ("picture", "control-picture", "stock-tap")):
            self.command(operation, name, origin, expect=False)
        self.assertEqual(self.command("case", "control-warmup", "stock-tap"), b"device_case\n")
        self.command("case", "control-warmup", "stock-tap", expect=False)
        with tarfile.open(fileobj=io.BytesIO(self.command("collect-case", "control-warmup")), mode="r:gz") as archive:
            self.assertEqual(set(archive.getnames()), {
                "out/control-warmup/" + leaf for leaf in ("origin", "anchor", "qualified", "stock.frame", "trace")})
        self.command("discard-case", "control-warmup")
        self.assertFalse((self.state / "program-intent").exists())
        self.command("close")
        self.assertEqual((self.root / "input-events").read_text().split(), ["navigated", "tile", "returned"])
        self.assertFalse(self.events.exists() or (self.root / "tuple").exists() or self.state.exists() or self.stage.exists())
        self.assertEqual(originals(), initial)

    def test_tapcheck_refuses_changed_installed_bytes_or_mode_without_writing(self):
        installed = self.install_tapcheck_fixture()
        installed[0].write_text("x" * len(installed[0].read_bytes()))
        self.command("prepare", "tapcheck", data=self.payload(), expect=False)
        self.command("close")
        self.assertNotEqual(installed[0].read_text(), "app/aidoku")
        installed[0].write_text("app/aidoku")
        self.command("prepare", "tapcheck", data=self.payload())
        installed[1].chmod(0o700)
        self.command("case", "control-warmup", "stock-tap", expect=False)
        self.command("collect-case", "control-warmup", expect=False)
        self.command("close")
        self.assertEqual(installed[1].stat().st_mode & 0o777, 0o700)
        self.assertFalse((self.root / "input-events").exists() or (self.root / "tuple").exists())

    def test_reference_collect_and_close(self):
        self.assertEqual(self.command("prepare", "reference", data=self.payload()), b"device_prepared\n")
        self.assertEqual(self.command("reference"), b"device_reference\n")
        self.assertEqual(self.events.read_text(), "open\n")
        data = self.command("collect")
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            self.assertEqual(set(archive.getnames()), {
                "out/reference/" + name for name in
                ("stock.frame", "library-a.frame", "library-b.frame", "origin", "anchor", "qualified")})
        self.assertEqual(self.command("close"), b"device_closed\n")
        self.assertFalse(self.state.exists() or self.stage.exists())
        self.command("close")

    def test_failed_bind_recovers_but_does_not_count_or_repeat(self):
        self.command("prepare", "study", data=self.payload())
        (self.root / "no-bind").touch()
        self.command("case", "candidate-01", "indexed-uri", expect=False)
        self.assertEqual((self.root / "tuple").read_text(), "candidate")
        self.command("case", "candidate-01", "indexed-uri", expect=False)
        self.command("collect-case", "candidate-01", expect=False)
        self.assertEqual(self.events.read_text(), "open\n")
        self.command("close")
        self.assertEqual((self.root / "tuple").read_text(), "control")
        self.assertFalse(self.state.exists() or self.stage.exists())

    def test_foreign_state_is_not_deleted(self):
        self.command("prepare", "study", data=self.payload())
        self.command("case", "candidate-01", "indexed-uri")
        self.assertTrue(self.command("collect-case", "candidate-01"))
        self.command("discard-case", "candidate-01")
        self.assertFalse((self.state / "out/candidate-01/stock.frame").exists())
        self.assertTrue((self.state / "out/candidate-01/qualified").exists())
        (self.root / "changed").touch()
        self.command("close", expect=False)
        self.assertTrue(self.stage.is_dir() and self.state.is_dir())
        (self.root / "changed").unlink()
        (self.stage / "owner").write_text("another\n")
        self.command("close", expect=False)
        self.assertTrue(self.stage.is_dir() and self.state.is_dir())
        (self.stage / "owner").write_text(LABEL + "\n")
        self.command("close")

    def test_partial_prepare_and_invalid_case_do_not_open(self):
        self.command("prepare", "reference", data=b"bad upload", expect=False)
        self.command("close")
        self.command("prepare", "reference", data=self.payload())
        self.command("case", "candidate-01", "indexed-uri", expect=False)
        self.assertFalse(self.events.exists())
        self.command("close")

    def test_navigation_only_round_trip_and_exact_archive(self):
        del self.modules["visit/visit.sh"]
        del self.modules["programs/programs.sh"]
        self.command("prepare", "navigation", data=self.payload())
        self.assertEqual(self.command("navigation"), b"device_navigation\n")
        self.command("navigation", expect=False)
        with tarfile.open(fileobj=io.BytesIO(self.command("collect-case", "navigation")), mode="r:gz") as archive:
            self.assertEqual(set(archive.getnames()), {"out/navigation/library.frame", "out/navigation/qualified"})
        self.command("discard-case", "navigation")
        self.command("close")
        self.assertEqual(self.pointer.read_text(), "home 99 104\n")
        self.assertEqual((self.root / "input-events").read_text().split(), ["navigated", "returned"])
        self.assertFalse(self.events.exists() or (self.root / "tuple").exists())

    def test_navigation_capture_refusal_restores_only_owned_input(self):
        self.command("prepare", "navigation", data=self.payload())
        (self.root / "unknown").touch()
        self.command("navigation", expect=False)
        self.command("close")
        self.assertFalse((self.root / "input-events").exists())
        (self.root / "unknown").unlink()
        self.command("prepare", "navigation", data=self.payload())
        (self.root / "no-frame").touch()
        self.command("navigation", expect=False)
        self.assertNotEqual(self.pointer.read_text(), "home 99 104\n")
        (self.root / "foreign").touch()
        self.command("close", expect=False)
        self.assertTrue((self.state / "input-original").is_file())
        (self.root / "foreign").unlink()
        self.command("close")
        self.assertEqual(self.pointer.read_text(), "home 99 104\n")
        self.assertFalse(self.events.exists())

    def test_tile_policy_never_dispatches_unknown_target_or_falls_back_to_uri(self):
        self.command("prepare", "study", data=self.payload())
        (self.root / "unknown-card").touch()
        self.command("case", "control-warmup", "stock-tap", expect=False)
        self.command("close")
        self.assertFalse(self.events.exists())
        self.assertEqual((self.root / "input-events").read_text().split(), ["navigated", "returned"])
        (self.root / "unknown-card").unlink()
        self.command("prepare", "study", data=self.payload())
        self.command("picture", "control-picture", "stock-tap")
        self.command("case", "candidate-01", "stock-tap")
        self.command("close")
        self.assertEqual(self.pointer.read_text(), "home 99 104\n")
        self.assertEqual((self.root / "input-events").read_text().split().count("tile"), 2)
        self.assertFalse(self.events.exists())


if __name__ == "__main__":
    unittest.main()
