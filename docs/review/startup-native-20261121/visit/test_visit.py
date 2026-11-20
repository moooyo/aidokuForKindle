import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
LABEL = "0123456789abcdef"
NONCE = "a" * 32


class ProductionVisitTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.stage = self.path("/mnt/us/.aidoku-native-" + LABEL)
        self.state = self.path("/var/tmp/aidoku-native-" + LABEL)
        self.out = self.state / "visit-baseline"
        self.live = self.state / "visit-live"
        self.lock = self.path("/tmp/aidoku-launcher.lock")
        self.document = self.path("/mnt/us/documents/Aidoku.sh")
        self.app = self.path("/mnt/us/aidoku/app/aidoku")
        self.log = self.path("/mnt/us/aidoku/data/logs/launcher.log")
        self.bin = self.root / "stubs"
        self.bin.mkdir()
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", FIXTURE=str(self.root))
        for directory in (self.stage, self.state):
            directory.mkdir(parents=True, mode=0o700)
            (directory / "owner").write_text(LABEL + "\n")
        for leaf in (self.app, self.log, self.document, self.path("/proc/sys/kernel/random/boot_id")):
            leaf.parent.mkdir(parents=True, exist_ok=True)
            leaf.write_text("fixture\n")
        self.database = self.path("/var/local/cc.db")
        self.database.parent.mkdir()
        with sqlite3.connect(self.database) as conn:
            conn.execute("CREATE TABLE Entries(p_location,p_uuid,p_mimeType,p_thumbnail,p_modificationTime,p_contentSize,p_lastAccess)")
            conn.execute("INSERT INTO Entries VALUES(?,?,?,?,?,?,?)", (
                str(self.document), "00000000-0000-0000-0000-000000000001", "text/x-shellscript",
                "cover", int(self.document.stat().st_mtime), self.document.stat().st_size, 200))
        for name, value in [("flIntensity", 8), ("currentAmberLevel", 4)]:
            (self.root / name).write_text(str(value) + "\n")
        self.proc_stat(555, 2, 45, "S")
        self.stub("pidof", '[ "$1" = awesome ] && echo 555')
        self.stub("status", "echo 'statusbar start/running'")
        self.stub("usleep", "sleep .001")
        self.stub("lipc-get-prop", '''case "$3" in
            activeApp) echo com.lab126.KPPMainApp;; state) echo active;;
            *) cat "$FIXTURE/$3";; esac''')
        self.stub("lipc-set-prop", '''case "$1" in
            -i) printf '%s\\n' "$4" >"$FIXTURE/$3";; *) echo request >>"$FIXTURE/open";; esac''')
        self.stub("sqlite3", '''import sqlite3,sys
with sqlite3.connect("file:"+sys.argv[-2]+"?mode=ro",uri=True) as db:
    for row in db.execute(sys.argv[-1]): print("|".join("" if col is None else str(col) for col in row))
''', "/usr/bin/python3")
        self.stub("fixture-kill", '''import os,pathlib,subprocess,sys
root=pathlib.Path(os.environ["FIXTURE"]); lock=root/"tmp/aidoku-launcher.lock"
assert sys.argv[1:]==["-TERM","333"] and (lock/"application-pid").read_text()=="333\\n"
(root/"signals").open("a").write("term\\n")
(lock/"ordinary-exit").write_text("333 "+(lock/"nonce").read_text().strip()+" 0\\n")
subprocess.Popen([sys.executable,str(root/"finish.py")],stdin=subprocess.DEVNULL,
                 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
''', "/usr/bin/python3")
        (self.root / "finish.py").write_text('''import os, pathlib, shutil, sqlite3, time
root = pathlib.Path(os.environ["FIXTURE"])
time.sleep(.12)
with (root/"mnt/us/aidoku/data/logs/launcher.log").open("a") as out:
    out.write("aidoku: Catalog notification completed.\\naidoku: Launcher worker exited with status 0.\\n")
with sqlite3.connect(root/"var/local/cc.db") as db:
    db.execute("UPDATE Entries SET p_lastAccess=201")
shutil.rmtree(root/"tmp/aidoku-launcher.lock")
''')
        self.script = (HERE / "visit.sh").read_text()
        paths = ("/mnt/us", "/var/tmp", "/var/local", "/tmp/", "/proc/", "/dev/fb0", "/dev/input/event")
        self.script = re.sub("|".join(map(re.escape, paths)), lambda match: "FIXTURE_ROOT" + match[0], self.script)
        self.script = self.script.replace("kill -TERM", "fixture-kill -TERM")
        self.runner = self.root / "visit.sh"
        self.runner.write_text(self.script.replace("FIXTURE_ROOT", str(self.root)))

    def path(self, leaf):
        return self.root / leaf.lstrip("/")

    def stub(self, name, source, interpreter="/bin/sh"):
        path = self.bin / name
        path.write_text(f"#!{interpreter}\n{source}\n")
        path.chmod(0o755)

    def proc_stat(self, pid, parent, birth, state):
        path = self.path(f"/proc/{pid}/stat")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{pid} (role with ) in name) {state} {parent} " + "0 " * 17 + f"{birth} 0\n")

    def step(self, *args, good=True):
        result = subprocess.run(["dash", str(self.runner), args[0], LABEL, "baseline", *args[1:]],
                                capture_output=True, text=True, env=self.env, timeout=20)
        if good:
            self.assertEqual(result.returncode, 0, f"{args}: {result.stderr}")
        else:
            self.assertNotEqual(result.returncode, 0, (args, result.stdout))
        return result.stdout.strip()

    def launch(self, argv=None, extra_env=b""):
        self.lock.mkdir(parents=True, mode=0o700)
        for key, value in (("nonce", NONCE), ("application-pid", "333"), ("runner-command", "333"),
                           ("pid", "111"), ("epoch", "0"), ("phase", "active"), ("generation", "1:" + NONCE)):
            (self.lock / key).write_text(value + "\n")
        home = self.app.parent.parent
        common = f"AIDOKU_HOME={home}\0AIDOKU_MANAGED_LIBRARY=1\0"
        gui = common + f"AIDOKU_LAUNCHER_DIR={self.lock}\0AIDOKU_LAUNCH_GENERATION=1\0AIDOKU_LAUNCH_NONCE={NONCE}\0"
        for pid, parent, birth, args, image, env in (
                (111, 1, 200, ["sh", str(home / "launch.sh"), "--foreground"], Path("/bin/sh").resolve(), common),
                (222, 111, 301, [], Path("/bin/sh").resolve(), common),
                (333, 222, 307, argv or [str(self.app), "--kindle", "--device", "scribe", "--data-dir", str(home / "data")], self.app, gui)):
            self.proc_stat(pid, parent, birth, "S")
            directory = self.path(f"/proc/{pid}")
            (directory / "exe").symlink_to(image)
            (directory / "environ").write_bytes(env.encode() + (extra_env if pid == 333 else b""))
            (directory / "cmdline").write_bytes(b"".join(str(arg).encode() + b"\0" for arg in args))
        fds = self.path("/proc/333/fd")
        fds.mkdir()
        for number, name in [(3, "/dev/fb0"), (4, "/dev/input/event1")]:
            (fds / str(number)).symlink_to(self.path(name))
        with self.log.open("a") as out:
            out.write("aidoku: Launcher worker started (pid=111).\n")
            out.write("aidoku: native window ready; profile=scribe, logical=930x1240, scale=2\n")

    def test_close_checks_epoch_zero_projection_and_restores_light(self):
        self.step("prepare")
        self.step("arm", "indexed-uri")
        self.step("request", "indexed-uri")
        self.step("request", "indexed-uri", good=False)
        self.assertEqual((self.root / "open").read_text(), "request\n")
        self.launch()
        self.step("bind")
        self.step("check")
        (self.root / "flIntensity").write_text("14\n")
        self.assertIn("catalog_advanced", self.step("exit"))
        self.assertEqual((self.out / "ack").read_text(), "observed\n")
        self.assertFalse(self.live.exists())
        self.assertEqual((self.root / "flIntensity").read_text(), "8\n")
        self.step("recover")

    def test_tap_does_not_dispatch_uri_or_reuse_changed_birth(self):
        self.step("prepare")
        self.step("arm", "stock-tap")
        self.step("request", "indexed-uri", good=False)
        self.assertFalse((self.root / "open").exists())
        self.launch()
        self.step("bind")
        self.proc_stat(333, 222, 310, "S")
        self.step("exit", good=False)
        self.step("recover", good=False)
        self.assertFalse((self.root / "signals").exists())

    def test_duplicate_index_or_changed_owner_refuses_before_input(self):
        with sqlite3.connect(self.database) as conn:
            conn.execute("INSERT INTO Entries SELECT * FROM Entries")
        self.step("prepare", good=False)
        (self.state / "owner").write_text("other\n")
        self.step("recover", good=False)
        self.assertFalse((self.root / "open").exists())

    def test_read_only_probe_names_only_the_failing_guard(self):
        self.assertEqual(self.step("inspect"), "visit_inputs_admitted staged")
        self.assertFalse(self.out.exists() or self.live.exists() or (self.root / "open").exists())
        self.state.chmod(0o755)
        refused = self.command_error("inspect")
        self.assertEqual(refused, "refused visit_state_mode")
        self.state.chmod(0o700)
        with sqlite3.connect(self.database) as conn:
            conn.execute("UPDATE Entries SET p_modificationTime=p_modificationTime+1")
        self.assertEqual(self.command_error("inspect"), "refused visit_catalog_file_mtime")
        self.assertFalse(self.out.exists() or self.live.exists())
        self.stage.rename(self.stage.with_suffix(".removed"))
        self.state.rename(self.state.with_suffix(".removed"))
        self.assertEqual(self.command_error("inspect"), "refused visit_catalog_file_mtime")
        with sqlite3.connect(self.database) as conn:
            conn.execute("UPDATE Entries SET p_modificationTime=p_modificationTime-1")
        self.assertEqual(self.step("inspect"), "visit_inputs_admitted no_stage")
        self.log.unlink()
        self.assertEqual(self.command_error("inspect"), "refused visit_log_type")

    def command_error(self, *args):
        result = subprocess.run(["dash", str(self.runner), args[0], LABEL, "baseline", *args[1:]],
                                capture_output=True, text=True, env=self.env, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        return result.stderr.strip()

    def dispatch_receipt(self):
        guard = self.state / "guard"
        guard.mkdir()
        (guard / "ready").write_text(LABEL + "\n")
        (self.state / "current").write_text("baseline\n")
        (self.state / "ready").write_text(LABEL + "\n")
        folder = self.state / "out" / "baseline"
        folder.mkdir(parents=True)
        (folder / "origin").write_text("indexed-uri\n")
        (folder / "dispatched").write_text("indexed-uri\n")
        return folder

    def test_setgid_private_production_lock_is_recovered_without_qualification(self):
        self.step("prepare")
        self.step("arm", "indexed-uri")
        folder = self.dispatch_receipt()
        self.launch()
        self.lock.chmod(0o2700)
        (self.root / "flIntensity").write_text("13\n")
        self.assertEqual(self.step("recover-dispatched"), "restored_only")
        self.assertTrue((self.out / "recovered").exists() and (self.out / "ack").exists())
        self.assertFalse((folder / "qualified").exists() or (self.out / "complete").exists())
        self.assertEqual((self.root / "flIntensity").read_text(), "8\n")
        self.assertEqual((self.root / "signals").read_text(), "term\n")

    def test_fixed_tmp_alias_with_2700_lock_can_close_but_a_leaf_symlink_cannot(self):
        private_tmp = self.root / "tmpfs"
        private_tmp.mkdir()
        self.lock.parent.symlink_to(private_tmp, target_is_directory=True)
        self.step("prepare")
        self.step("arm", "indexed-uri")
        self.dispatch_receipt()
        self.launch()
        self.lock.chmod(0o2700)
        self.step("bind")
        moved = private_tmp / "different-leaf"
        self.lock.rename(moved)
        self.lock.symlink_to(moved, target_is_directory=True)
        self.step("check", good=False)
        self.step("recover-dispatched", good=False)
        self.assertFalse((self.root / "signals").exists())
        self.lock.unlink()
        moved.rename(self.lock)
        self.assertEqual(self.step("recover-dispatched"), "restored_only")
        self.assertFalse((self.out / "complete").exists())

    def test_extra_access_or_wrong_dispatched_visit_never_authorizes_a_signal(self):
        self.step("prepare")
        self.step("arm", "indexed-uri")
        folder = self.dispatch_receipt()
        self.launch()
        self.lock.chmod(0o2700)
        self.step("bind")
        self.lock.chmod(0o2710)
        self.step("recover-dispatched", good=False)
        self.assertEqual(self.lock.stat().st_mode & 0o7777, 0o2710)
        self.lock.chmod(0o2700)
        (folder / "dispatched").write_text("stock-tap\n")
        self.step("recover-dispatched", good=False)
        self.assertFalse((self.root / "signals").exists())

    def test_missing_input_or_diagnostic_environment_refuses_qualification(self):
        self.step("prepare")
        self.step("arm", "indexed-uri")
        self.launch(extra_env=b"AIDOKU_RESEARCH_OUT=/tmp/example\0")
        self.step("bind", good=False)
        self.assertFalse((self.out / "bound").exists())
        self.assertFalse((self.out / "ready").exists())
        environment = self.path("/proc/333/environ")
        environment.write_bytes(environment.read_bytes().split(b"AIDOKU_RESEARCH_OUT=")[0])
        self.path("/proc/333/fd/4").unlink()
        self.step("bind", good=False)
        self.assertFalse((self.out / "ready").exists())
        self.step("recover")
        self.assertFalse((self.out / "complete").exists())

    def test_unrelated_open_or_replaced_nonce_never_receives_term(self):
        self.step("prepare")
        self.step("arm", "indexed-uri")
        self.launch()
        self.step("bind")
        (self.lock / "nonce").write_text("b" * 32 + "\n")
        self.step("exit", good=False)
        self.step("recover", good=False)
        self.assertFalse((self.root / "signals").exists())

    def test_exit_rejects_reindex_notification_fallback(self):
        self.step("prepare")
        self.step("arm", "stock-tap")
        self.launch()
        self.step("bind")
        with self.log.open("a") as out:
            out.write("aidoku: Catalog notification unavailable; using standard reindex recovery.\n")
        self.step("exit", good=False)
        self.assertTrue((self.out / "ack").exists())
        self.assertFalse((self.out / "complete").exists())
        self.step("recover")

    def test_changed_log_or_symlinked_state_cannot_arm(self):
        self.step("prepare")
        self.log.rename(self.log.with_suffix(".old"))
        self.log.write_text("unrelated\n")
        self.step("arm", "stock-tap", good=False)
        self.step("recover")
        self.assertFalse((self.root / "signals").exists())
        self.out.rename(self.out.with_suffix(".saved"))
        self.out.symlink_to(self.out.with_suffix(".saved"))
        self.step("recover", good=False)


if __name__ == "__main__":
    unittest.main()
