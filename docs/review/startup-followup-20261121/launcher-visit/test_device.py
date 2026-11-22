import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parent
LABEL = "1234567890abcdef"
FOREIGN = "fedcba0987654321"
FB_ROWS = 4960
FB_BYTES = FB_ROWS * 1872
PANE_ROWS = 2480
PANE_BYTES = PANE_ROWS * 1872
BODY_ROWS = 2336
BODY_BYTES = BODY_ROWS * 1872
SEED_HASHES = (
    "1111111111111111111111111111111111111111111111111111111111111111",
    "2222222222222222222222222222222222222222222222222222222222222222",
    "3333333333333333333333333333333333333333333333333333333333333333",
    "4444444444444444444444444444444444444444444444444444444444444444",
    "5555555555555555555555555555555555555555555555555555555555555555",
    "6666666666666666666666666666666666666666666666666666666666666666",
)
NESTED_LOCAL_CONTENT = (
    f"seed/local/content/{SEED_HASHES[0]}/{SEED_HASHES[0]}/000001.jpg",
    f"seed/local/content/{SEED_HASHES[1]}/{SEED_HASHES[1]}/000001.jpg",
    f"seed/local/content/{SEED_HASHES[2]}/{SEED_HASHES[2]}/000001.jpg",
    f"seed/local/content/{SEED_HASHES[3]}/{SEED_HASHES[3]}/000001.jpg",
    f"seed/local/content/{SEED_HASHES[4]}/{SEED_HASHES[4]}/000001.jpg",
    f"seed/local/content/{SEED_HASHES[5]}/{SEED_HASHES[5]}/000001.jpg",
)

GUARD = """#!/bin/sh
set -eu
action=$1
label=$2
printf '%s %s\\n' "$action" "$label" >>"$MOCK_ROOT/guard-actions"
case "$action" in
capture)
    mkdir -p "$MOCK_STATE/guard"
    printf '%s\\n' "$label" >"$MOCK_STATE/guard/ready"
    echo guard_captured
    ;;
verify)
    test ! -f "$MOCK_ROOT/guard-verify-fail"
    echo guard_verified
    ;;
restore)
    test ! -f "$MOCK_ROOT/guard-restore-fail"
    echo guard_verified
    ;;
*)
    exit 91
    ;;
esac
"""

LAUNCH = """#!/bin/sh
set -eu
out=${AIDOKU_RESEARCH_OUT:?}
power="$AIDOKU_HOME/power-events"
lock="__AIDOKU_LOCK__"
cleanup() {
    rm -f "$lock" "$power"
}
trap cleanup EXIT HUP INT TERM
rm -f "$power"
mkfifo "$power"
exec 3<>"$power"
printf '%s\\n' "$$" >"$lock"
printf '{"ok":true}\\n' >"$out/application.json"
printf '{"route":"library"}\\n' >"$out/state.json"
printf '%s\\n' 'aidoku: launcher-visit ordinary_exit'
/bin/sleep 0.05
"""

LIPC_GET_PROP = """#!/bin/sh
set -eu
case "$*" in
    '-s com.lab126.powerd state')
        if [ -e "$MOCK_ROOT/asleep" ]; then
            echo screenSaver
        else
            echo active
        fi
        ;;
    '-s com.lab126.appmgrd activeApp')
        if [ -e "$MOCK_ROOT/other-app" ]; then
            echo another
        else
            echo com.lab126.KPPMainApp
        fi
        ;;
    *)
        exit 99
        ;;
esac
"""

STATUS = """#!/bin/sh
set -eu
if [ "$1" != statusbar ]; then
    exit 99
fi
if [ -e "$MOCK_ROOT/status-down" ]; then
    echo "statusbar stop/waiting"
else
    echo "statusbar start/running"
fi
"""

PIDOF = """#!/bin/sh
set -eu
name=$1
if [ "$name" = "awesome" ]; then
    echo "$MOCK_AWESOME_PID"
    exit 0
fi
if [ -e "$MOCK_ROOT/pidof-$name" ]; then
    echo 4242
    exit 0
fi
exit 1
"""

DF = """#!/bin/sh
set -eu
available=999999
if [ -e "$MOCK_ROOT/low-space" ]; then
    available=1
fi
printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\\n'
printf 'mockfs 2000000 10 %s 1%% %s\\n' "$available" "${2:-/}"
"""

UNAME = """#!/bin/sh
set -eu
if [ "${1:-}" = "-m" ]; then
    echo armv7l
    exit 0
fi
exec /usr/bin/uname "$@"
"""

LIPC_SET_PROP = """#!/bin/sh
set -eu
printf '%s\\n' "$*" >>"$MOCK_ROOT/touch-calls"
if [ -e "$MOCK_ROOT/asleep" ]; then
    exit 88
fi
exit 0
"""


class DeviceVisitDispatch(unittest.TestCase):
    def _cleanup_process(self, process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

        self.media = self.root / "media"
        self.private = self.root / "private"
        self.locks = self.root / "locks"
        self.binary = self.root / "bin"
        self.fb = self.root / "dev" / "fb0"
        self.sys_fb = self.root / "sys" / "class" / "graphics" / "fb0"
        self.proc = self.root / "proc"
        self.mounts = self.proc / "mounts"
        for directory in (self.media, self.private, self.locks, self.binary, self.fb.parent, self.sys_fb, self.proc):
            directory.mkdir(parents=True, exist_ok=True)
        self.mounts.write_text(f"mockfs {self.media} ext4 rw,relatime 0 0\n", encoding="utf-8")

        self.installed = self.media / "aidoku"
        (self.installed / "app").mkdir(parents=True)
        (self.installed / "data").mkdir(parents=True)
        self._write_file(self.installed / "launch.sh", "#!/bin/sh\nexit 0\n", mode=0o755)
        self._write_file(self.installed / "app" / "aidoku", "installed-aidoku\n", mode=0o755)
        self._write_file(self.installed / "data" / "library.sqlite3", "db\n")
        self._write_file(self.installed / "app" / "aidoku-native-query", "helper-query\n", mode=0o755)
        self._write_file(self.installed / "app" / "kindle-jpeg-worker", "helper-jpeg\n", mode=0o755)

        self._write_file(self.sys_fb / "stride", "1872\n")
        self._write_file(self.sys_fb / "bits_per_pixel", "8\n")
        self._write_file(self.sys_fb / "rotate", "1\n")
        self._write_file(self.sys_fb / "pan", "0,0\n")
        self._write_file(self.sys_fb / "virtual_size", "1872,4960\n")
        self.fb.write_bytes(b"A" * FB_BYTES)

        self.awesome = subprocess.Popen(["/bin/sleep", "120"], env=os.environ.copy())
        self.addCleanup(lambda: self._cleanup_process(self.awesome))

        self._write_file(self.binary / "usleep", "#!/bin/sh\n/bin/sleep 0.001\n", mode=0o755)
        self._write_file(self.binary / "lipc-get-prop", LIPC_GET_PROP, mode=0o755)
        self._write_file(self.binary / "status", STATUS, mode=0o755)
        self._write_file(self.binary / "pidof", PIDOF, mode=0o755)
        self._write_file(self.binary / "lipc-set-prop", LIPC_SET_PROP, mode=0o755)
        self._write_file(self.binary / "df", DF, mode=0o755)
        self._write_file(self.binary / "uname", UNAME, mode=0o755)

        script = (ROOT / "device.sh").read_text(encoding="utf-8")
        replacements = {
            "media=/mnt/us": f"media={self.media}",
            "private=/var/tmp": f"private={self.private}",
            "fb=/dev/fb0": f"fb={self.fb}",
            "sysfb=/sys/class/graphics/fb0": f"sysfb={self.sys_fb}",
            "locks=/tmp": f"locks={self.locks}",
            "mounts=/proc/mounts": f"mounts={self.mounts}",
        }
        for source, target in replacements.items():
            script = script.replace(source, target)
        self.script = script

        self.stage = self.media / (".aidoku-native-" + LABEL)
        self.state = self.private / ("aidoku-native-" + LABEL)
        self.environment = dict(
            os.environ,
            PATH=f"{self.binary}:{os.environ['PATH']}",
            MOCK_ROOT=str(self.root),
            MOCK_STATE=str(self.state),
            MOCK_AWESOME_PID=str(self.awesome.pid),
        )

    def _write_file(self, path, content, mode=0o644):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        path.chmod(mode)

    def _proc_birth(self, pid):
        return Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()[21]

    def payload(self):
        launch = LAUNCH.replace("__AIDOKU_LOCK__", str(self.locks / "aidoku-launcher.lock"))
        members = {
            "launch.sh": launch,
            "guard.sh": GUARD,
            "app/aidoku": "#!/bin/sh\necho payload-aidoku\n",
            "app/aidoku-startup-portrait": "#!/bin/sh\necho payload-portrait\n",
            "seed/library.sqlite3": "seed-db\n",
        }
        for index, member in enumerate(NESTED_LOCAL_CONTENT, start=1):
            members[member] = f"cover-{index}\n"

        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as archive:
            for name, text in members.items():
                item = tarfile.TarInfo(name)
                data = text.encode("utf-8")
                item.size = len(data)
                item.mode = 0o755
                archive.addfile(item, io.BytesIO(data))
        return stream.getvalue()

    def command(self, operation, *, data=b"", expect=True, label=LABEL):
        result = subprocess.run(
            ["sh", "-c", self.script, "test-device", "visit", operation, label],
            input=data,
            capture_output=True,
            env=self.environment,
            timeout=30,
        )
        self.assertEqual(result.returncode == 0, expect, (operation, result.stderr.decode("utf-8", "replace")))
        return result

    def assert_refused(self, result, reason):
        self.assertIn(f"refused {reason}", result.stderr.decode("utf-8", "replace"))

    def prepare_and_visit(self):
        self.command("admit")
        self.command("prepare", data=self.payload())
        self.assertEqual(self.command("visit").stdout, b"visit_completed\n")

    def test_prepare_accepts_active_pan_and_strict_seed_payload(self):
        (self.sys_fb / "pan").write_text("0,2480\n", encoding="utf-8")
        hidden = b"H" * PANE_BYTES
        visible = b"V" * PANE_BYTES
        self.fb.write_bytes(hidden + visible)
        self.assertEqual(self.command("admit").stdout, b"admitted\n")
        prepared = self.command("prepare", data=self.payload())
        self.assertEqual(prepared.stdout, b"prepared\n")
        for member in NESTED_LOCAL_CONTENT:
            self.assertTrue((self.stage / member).is_file(), member)
        self.assertEqual((self.state / "stock.before").read_bytes(), b"V" * BODY_BYTES)

    def test_prepare_and_visit_touch_screensaver_timeout_when_active(self):
        self.command("admit")
        self.command("prepare", data=self.payload())
        self.assertTrue((self.root / "touch-calls").is_file())
        calls = (self.root / "touch-calls").read_text(encoding="utf-8").strip().splitlines()
        self.assertIn("-i com.lab126.powerd touchScreenSaverTimeout 1", calls)

        before = len(calls)
        self.command("visit")
        calls_after = (self.root / "touch-calls").read_text(encoding="utf-8").strip().splitlines()
        self.assertGreaterEqual(len(calls_after), before + 1)
        self.assertTrue(
            all(call == "-i com.lab126.powerd touchScreenSaverTimeout 1" for call in calls_after),
            calls_after,
        )

    def test_admit_rejects_peer_stage_and_prepare_returns_not_started(self):
        peer = self.media / ".aidoku-native-0000000000000001"
        peer.mkdir(parents=True)
        (peer / "owner").write_text("0000000000000001\n", encoding="utf-8")
        admit = self.command("admit", expect=False)
        self.assert_refused(admit, "native_admission")
        self.assertEqual(self.command("prepare", data=self.payload()).stdout, b"not_started\n")

    def test_prepare_refuses_owner_only_orphan_stage(self):
        self.stage.mkdir(parents=True, exist_ok=True)
        (self.stage / "owner").write_text(LABEL + "\n", encoding="utf-8")
        refused = self.command("prepare", data=self.payload(), expect=False)
        self.assert_refused(refused, "stage_present")

    def test_prepare_rejects_owned_orphan_stage_with_extra_members(self):
        self.stage.mkdir(parents=True, exist_ok=True)
        (self.stage / "owner").write_text(LABEL + "\n", encoding="utf-8")
        (self.stage / "unexpected").write_text("x\n", encoding="utf-8")
        refused = self.command("prepare", data=self.payload(), expect=False)
        self.assert_refused(refused, "stage_present")

    def test_prepare_does_not_send_touch_when_asleep(self):
        (self.root / "asleep").write_text("1\n", encoding="utf-8")
        refused = self.command("admit", expect=False)
        self.assert_refused(refused, "native_admission")
        self.assertEqual(self.command("prepare", data=self.payload()).stdout, b"not_started\n")
        self.assertFalse((self.root / "touch-calls").exists())

    def test_restore_accepts_owner_only_stage_then_close_removes_all(self):
        self.stage.mkdir(parents=True, exist_ok=True)
        (self.stage / "owner").write_text(LABEL + "\n", encoding="utf-8")
        self.assertEqual(self.command("restore").stdout, b"restored\n")
        self.assertTrue(self.state.is_dir())
        self.assertTrue((self.state / "restored").is_file())
        self.assertEqual(self.command("close").stdout, b"stage_absent\n")
        self.assertFalse(self.stage.exists())
        self.assertFalse(self.state.exists())

    def test_restore_rejects_foreign_or_extra_stage_only(self):
        self.stage.mkdir(parents=True, exist_ok=True)
        (self.stage / "owner").write_text(FOREIGN + "\n", encoding="utf-8")
        foreign = self.command("restore", expect=False)
        self.assert_refused(foreign, "stage_owner")

        shutil.rmtree(self.stage)
        self.stage.mkdir(parents=True, exist_ok=True)
        (self.stage / "owner").write_text(LABEL + "\n", encoding="utf-8")
        (self.stage / "unexpected").write_text("x\n", encoding="utf-8")
        partial = self.command("restore", expect=False)
        self.assert_refused(partial, "partial_stage")

    def test_stop_owned_matching_cmdline_without_fifo_evidence_times_out_without_term(self):
        self.command("admit")
        self.command("prepare", data=self.payload())
        term_flag = self.root / "term-launcher-no-fifo"
        launcher_lock = self.locks / "aidoku-launcher.lock"
        launcher_lock.mkdir(parents=True, exist_ok=True)
        launcher_body = (
            'term_flag="$TERM_FLAG"; '
            'trap \'printf "term\\n" >"$term_flag"; exit 0\' TERM; '
            'while :; do /bin/sleep 0.05; done'
        )
        self._write_file(self.stage / "launch.sh", launcher_body)
        launcher = subprocess.Popen(
            ["sh", str(self.stage / "launch.sh"), "--foreground"],
            env=dict(self.environment, TERM_FLAG=str(term_flag)),
        )
        self.addCleanup(lambda: self._cleanup_process(launcher))
        birth = self._proc_birth(launcher.pid)
        (launcher_lock / "pid").write_text(f"{launcher.pid}\n", encoding="utf-8")
        (self.state / "visit.intent").write_text(LABEL + "\n", encoding="utf-8")
        (self.state / "visit.pid").write_text(f"{launcher.pid} {birth} {self.stage / 'launch.sh'}\n", encoding="utf-8")
        refused = self.command("restore", expect=False)
        self.assert_refused(refused, "live_operation")
        self.assertIsNone(launcher.poll(), "restore should not terminate launcher without fifo evidence")
        self.assertFalse(term_flag.exists(), "restore should not TERM launcher without fifo evidence")

    def test_stop_owned_matching_cmdline_with_fifo_fd3_evidence_sends_term(self):
        self.command("admit")
        self.command("prepare", data=self.payload())
        term_flag = self.root / "term-launcher-owned"
        launcher_lock = self.locks / "aidoku-launcher.lock"
        launcher_lock.mkdir(parents=True, exist_ok=True)
        os.mkfifo(launcher_lock / "power-events")
        launcher_body = (
            'lock_dir="$MOCK_LOCK"; term_flag="$TERM_FLAG"; fifo_path="$lock_dir/power-events"; '
            'cleanup() { rm -f "$fifo_path" "$lock_dir/pid"; rmdir "$lock_dir" 2>/dev/null || :; }; '
            'trap \'printf "term\\n" >"$term_flag"; cleanup; exit 0\' TERM; '
            'exec 3<>"$fifo_path"; '
            'while :; do /bin/sleep 0.05; done'
        )
        self._write_file(self.stage / "launch.sh", launcher_body)
        launcher = subprocess.Popen(
            ["sh", str(self.stage / "launch.sh"), "--foreground"],
            env=dict(self.environment, MOCK_LOCK=str(launcher_lock), TERM_FLAG=str(term_flag)),
        )
        self.addCleanup(lambda: self._cleanup_process(launcher))
        birth = self._proc_birth(launcher.pid)
        (launcher_lock / "pid").write_text(f"{launcher.pid}\n", encoding="utf-8")
        (self.state / "visit.intent").write_text(LABEL + "\n", encoding="utf-8")
        (self.state / "visit.pid").write_text(f"{launcher.pid} {birth} {self.stage / 'launch.sh'}\n", encoding="utf-8")
        fd3_link = Path(f"/proc/{launcher.pid}/fd/3")
        for _ in range(50):
            if fd3_link.exists():
                break
            time.sleep(0.02)
        self.assertTrue(fd3_link.exists(), "launcher fd3 should be opened on fifo before restore")
        self.assertEqual(os.readlink(fd3_link), str(launcher_lock / "power-events"))
        cmdline = Path(f"/proc/{launcher.pid}/cmdline").read_bytes().split(b"\0")
        self.assertIn(str(self.stage / "launch.sh").encode("utf-8"), cmdline)
        self.assertIn(b"--foreground", cmdline)
        self.assertEqual(self._proc_birth(launcher.pid), birth)
        self.assertEqual(self.command("restore").stdout, b"restored\n")
        launcher.wait(timeout=3)
        self.assertTrue(term_flag.exists(), "restore should TERM launcher with fifo fd3 evidence")
        self.assertFalse(launcher_lock.exists(), "launcher cleanup should release lock evidence")

    def test_rejects_missing_ready_marker_and_foreign_owner(self):
        self.command("admit")
        self.command("prepare", data=self.payload())
        (self.state / "ready").unlink()
        missing = self.command("visit", expect=False)
        self.assert_refused(missing, "stage_owner")

        (self.state / "ready").write_text(LABEL + "\n", encoding="utf-8")
        (self.stage / "owner").write_text(FOREIGN + "\n", encoding="utf-8")
        foreign = self.command("restore", expect=False)
        self.assert_refused(foreign, "stage_owner")

    def test_visit_intent_is_exclusive_and_not_retried(self):
        self.command("admit")
        self.command("prepare", data=self.payload())
        (self.state / "visit.intent").write_text(LABEL + "\n", encoding="utf-8")

        first = self.command("visit", expect=False)
        second = self.command("visit", expect=False)

        self.assert_refused(first, "visit_intent")
        self.assert_refused(second, "visit_intent")
        self.assertFalse((self.state / "visit.done").exists())
        self.assertFalse((self.state / "out" / "launcher.log").exists())

    def test_restore_requires_baseline_and_stock_match_before_close(self):
        self.prepare_and_visit()
        (self.state / "stock.before").unlink()
        refused = self.command("restore", expect=False)
        self.assert_refused(refused, "recovery_required")
        self.assertFalse((self.state / "restored").exists())
        self.assertTrue(self.stage.is_dir())
        self.assertTrue(self.state.is_dir())
        self.assert_refused(self.command("close", expect=False), "close_requires_restore")

    def test_restore_stock_mismatch_blocks_close_and_keeps_stage(self):
        self.prepare_and_visit()
        self.fb.write_bytes(b"B" * FB_BYTES)
        restore = self.command("restore", expect=False)
        close = self.command("close", expect=False)
        self.assert_refused(restore, "stock_changed")
        self.assert_refused(close, "close_requires_restore")
        self.assertTrue(self.stage.is_dir())
        self.assertTrue(self.state.is_dir())

    def test_close_reports_checkpoint_when_both_absent(self):
        refused = self.command("close", expect=False)
        self.assert_refused(refused, "close_checkpoint")

    def test_close_reports_checkpoint_when_stage_missing(self):
        self.prepare_and_visit()
        self.assertEqual(self.command("restore").stdout, b"restored\n")
        shutil.rmtree(self.stage)
        (self.state / "close.intent").write_text(LABEL + "\n", encoding="utf-8")
        refused = self.command("close", expect=False)
        self.assert_refused(refused, "close_checkpoint")
        self.assertTrue(self.state.exists())

    def test_close_rechecks_guard_verification(self):
        self.prepare_and_visit()
        self.assertEqual(self.command("restore").stdout, b"restored\n")
        (self.root / "guard-verify-fail").write_text("1\n", encoding="utf-8")
        refused = self.command("close", expect=False)
        self.assert_refused(refused, "guard_verify")
        self.assertTrue(self.stage.exists())
        self.assertTrue(self.state.exists())

    def test_restore_is_idempotent_and_close_handles_partial_state(self):
        self.prepare_and_visit()
        self.assertEqual(self.command("restore").stdout, b"restored\n")
        self.assertEqual(self.command("restore").stdout, b"restored\n")

        (self.stage / "owner").write_text(FOREIGN + "\n", encoding="utf-8")
        self.assert_refused(self.command("close", expect=False), "stage_owner")
        (self.stage / "owner").write_text(LABEL + "\n", encoding="utf-8")

        shutil.rmtree(self.stage)
        self.assert_refused(self.command("close", expect=False), "close_checkpoint")
        (self.state / "close.intent").write_text(LABEL + "\n", encoding="utf-8")
        self.assert_refused(self.command("close", expect=False), "close_checkpoint")
        self.assertTrue(self.state.exists())

    def test_close_refuses_stage_without_restored_state(self):
        self.prepare_and_visit()
        self.assertEqual(self.command("restore").stdout, b"restored\n")
        shutil.rmtree(self.state)
        partial = self.command("close", expect=False)
        self.assert_refused(partial, "close_requires_restore")
        self.assertTrue(self.stage.exists())

    def test_restore_cleans_orphan_visit_process_state(self):
        self.command("admit")
        self.command("prepare", data=self.payload())
        (self.state / "visit.intent").write_text(LABEL + "\n", encoding="utf-8")
        (self.state / "visit.pid").write_text(f"999999 1 {self.stage / 'launch.sh'}\n", encoding="utf-8")
        self.assertEqual(self.command("restore").stdout, b"restored\n")
        self.assertFalse((self.state / "visit.pid").exists())
        self.assertEqual(self.command("close").stdout, b"stage_absent\n")
        self.assertFalse(self.stage.exists())
        self.assertFalse(self.state.exists())

    def test_concurrent_stage_lock_refuses_visit(self):
        self.command("admit")
        self.command("prepare", data=self.payload())
        marker = self.root / "lock-held"
        holder = subprocess.Popen(
            [
                "sh",
                "-c",
                'exec 9<"$1"; flock -x 9; : >"$2"; sleep 3',
                "lock-holder",
                str(self.stage),
                str(marker),
            ],
            env=self.environment,
        )
        try:
            for _ in range(50):
                if marker.exists():
                    break
                time.sleep(0.02)
            self.assertTrue(marker.exists(), "lock marker was not created")
            refused = self.command("visit", expect=False)
            self.assert_refused(refused, "live_operation")
        finally:
            holder.terminate()
            holder.wait(timeout=5)

    def test_success_flow_collects_exact_members_and_absent_after_close(self):
        self.prepare_and_visit()
        archive = self.command("collect").stdout
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
            self.assertEqual(set(bundle.getnames()), {"launcher.log", "application.json", "state.json"})

        self.assertEqual(self.command("restore").stdout, b"restored\n")
        self.assertEqual(self.command("close").stdout, b"stage_absent\n")
        self.assertEqual(self.command("absent").stdout, b"stage_absent\n")
        self.assertFalse(self.stage.exists())
        self.assertFalse(self.state.exists())


if __name__ == "__main__":
    unittest.main()
