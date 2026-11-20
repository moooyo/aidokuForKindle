import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest


LABEL = "1234567890abcdef"
RUNTIME = "/var/local/kindle-catalog-entry/v1-2db100d0bcb60a76571cd8de96bed7ea6c016e6ab73e388a885f692955cacd86"
HANDLER = "tech.hackerdude.shell_integration.launcher"


class GuardTest(unittest.TestCase):
    def setUp(self):
        private = tempfile.TemporaryDirectory()
        self.addCleanup(private.cleanup)
        self.root = Path(private.name)
        self.bin = self.root / "tools"
        self.bin.mkdir()
        paths = ("/mnt/us", "/var/tmp", "/var/local", "/usr/lib/ccat", "/proc", "/sys", "/tmp")
        script = Path(__file__).with_name("guard.sh").read_text()
        for index, path in enumerate(paths):
            for start in (' ', '"', "'", '='):
                script = script.replace(start + path, start + f"@{index}@")
        for index, path in enumerate(paths):
            script = script.replace(f"@{index}@", str(self.at(path)))
        for location in ("Aidoku.sh", "weread.sh", "bilicomics.sh"):
            path = "/mnt/us/documents/" + location
            script = script.replace("'" + str(self.at(path)) + "'", "'" + path + "'")
        self.script = self.root / "guard.sh"
        self.script.write_text(script)
        self.stub("uname", "echo armv7l")
        self.stub("status", "cat \"$FIXTURE/statusbar\"")
        self.stub("pidof", "case \"$1\" in awesome) echo 19;; *) [ ! -e \"$FIXTURE/on-$1\" ] && exit 1; echo 77;; esac")
        self.stub("lipc-get-prop", "cat \"$FIXTURE/$3\"")
        self.stub("lipc-set-prop", "case \"$3\" in flIntensity|currentAmberLevel) echo \"$4\" >\"$FIXTURE/$3\";; *) exit 99;; esac")
        self.stub("sqlite3", '''import sqlite3,sys
assert sys.argv[1:11] == ["-batch","-readonly","-noheader","-cmd",".timeout 1500","-cmd","PRAGMA query_only=ON","-cmd",".mode quote",sys.argv[-2]]
db=sqlite3.connect("file:"+sys.argv[-2]+"?mode=ro",uri=True)
db.execute("PRAGMA query_only=ON")
for statement in sys.argv[-1].split(";"):
    if statement.strip():
        for row in db.execute(statement):
            print(",".join(db.execute("SELECT quote(?)",(field,)).fetchone()[0] for field in row))
''', "python3")
        for path, text in {
            "/proc/sys/kernel/random/boot_id": "boot", "/proc/19/status": "State:\tS (sleep)",
            "/proc/19/stat": " ".join(["1"] * 21 + ["812333"]), "/sys/class/graphics/fb0/stride": "1872",
            "/sys/class/graphics/fb0/bits_per_pixel": "8", "/sys/class/graphics/fb0/rotate": "1",
            "/sys/class/graphics/fb0/virtual_size": "1872,4960", "/sys/class/input/event0": "fixture",
            "/state": "active", "/activeApp": "com.lab126.KPPMainApp", "/flIntensity": "9",
            "/currentAmberLevel": "5", "/statusbar": "statusbar start/running, process 9",
            f"/mnt/us/.aidoku-native-{LABEL}/owner": LABEL, f"/var/tmp/aidoku-native-{LABEL}/owner": LABEL,
        }.items():
            self.write(path, text)
        self.at("/tmp").mkdir()
        for path in [
            "/mnt/us/documents/Aidoku.sh", "/mnt/us/aidoku/app/aidoku-startup-portrait",
            "/mnt/us/aidoku/app/aidoku-native-query", "/mnt/us/aidoku/app/kindle-jpeg-worker",
            "/mnt/us/aidoku/assets/aidoku-cover-v2.png", "/mnt/us/aidoku/assets/aidoku-cover.png",
            "/mnt/us/documents/weread.sh", "/mnt/us/weread/launch.sh", "/mnt/us/weread/app/weread",
            "/mnt/us/weread/app/weread-frame-guard", "/mnt/us/weread/app/weread-native-query",
            RUNTIME + "/aidoku-library-dispatch", RUNTIME + "/aidoku-entry-hooks.so",
            RUNTIME + "/aidoku-catalog-client", RUNTIME + "/protocol", "/usr/lib/ccat/change.lua",
            "/var/local/real-kmc/bin/sh_integration_launcher",
        ]:
            self.write(path, "public")
        self.at("/var/local/kmc").symlink_to(self.at("/var/local/real-kmc"))
        with self.at("/mnt/us/weread/app/weread").open("ab") as stream:
            stream.truncate(95137176)
        self.reg = self.at("/var/local/appreg.db")
        self.catalog = self.at("/var/local/cc.db")
        with sqlite3.connect(self.reg) as database:
            database.executescript("CREATE TABLE properties(handlerId,name,value); CREATE TABLE associations(handlerId,contentId);")
            database.execute("INSERT INTO properties VALUES(?,?,?)",
                             (HANDLER, "command", str(self.at(RUNTIME + "/aidoku-library-dispatch"))))
            database.execute("INSERT INTO associations VALUES(?,?)", (HANDLER, "MT:text/x-shellscript"))
        with sqlite3.connect(self.catalog) as database:
            database.execute("CREATE TABLE Entries(p_uuid,p_location,p_mimeType,p_thumbnail,p_modificationTime,p_contentSize,p_lastAccess)")
            database.executemany("INSERT INTO Entries VALUES('private-uuid',?,'text/x-shellscript','private-thumb',12,16,30)",
                                 [(f"/mnt/us/documents/{doc}",) for doc in ("weread.sh", "Aidoku.sh", "irrelevant")])

    def at(self, path):
        return self.root / path.lstrip("/")

    def write(self, name, text):
        path = self.at(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n")

    def stub(self, name, text, interpreter="sh"):
        path = self.bin / name
        path.write_text(f"#!/usr/bin/env {interpreter}\n{text}\n")
        path.chmod(0o700)

    def invoke(self, operation, expected=True):
        result = subprocess.run(["sh", self.script, operation, LABEL], capture_output=True, text=True,
                                env=dict(os.environ, PATH=str(self.bin) + ":" + os.environ["PATH"], FIXTURE=str(self.root)))
        self.assertEqual(result.returncode == 0, expected, (operation, result.stdout, result.stderr))
        self.assertNotIn("private", result.stdout + result.stderr)
        return result.stdout.strip()

    def test_roundtrip_restores_only_lights_and_tolerates_access_times(self):
        self.assertEqual(self.invoke("admit"), "guard_admitted")
        self.assertEqual(self.invoke("capture"), "guard_captured")
        self.invoke("capture", False)
        self.invoke("verify")
        self.write("/flIntensity", "12")
        self.invoke("verify", False)
        with sqlite3.connect(self.catalog) as database:
            database.execute("UPDATE Entries SET p_lastAccess=57")
            database.execute("UPDATE Entries SET p_uuid='unrelated' WHERE p_location LIKE '%irrelevant'")
        self.invoke("restore")
        self.assertEqual(self.at("/flIntensity").read_text(), "9\n")
        self.invoke("verify")

    def test_rejects_changed_scope_without_touching_peers_or_lights(self):
        self.invoke("capture")
        self.write("/currentAmberLevel", "11")
        self.write("/mnt/us/weread/launch.sh", "new independent release")
        self.invoke("restore", False)
        self.assertEqual(self.at("/currentAmberLevel").read_text(), "11\n")
        self.assertEqual(self.at("/mnt/us/weread/launch.sh").read_text(), "new independent release\n")

    def test_current_bili_or_absent_new_weread_are_not_cleanup_targets(self):
        self.at("/mnt/us/documents/BiliComics.sdr").symlink_to(self.root / "dangling")
        self.invoke("admit", False)
        self.at("/mnt/us/documents/BiliComics.sdr").unlink()
        self.at("/mnt/us/weread/launch.sh").unlink()
        self.invoke("admit", False)
        self.write("/mnt/us/weread/launch.sh", "public")
        with sqlite3.connect(self.catalog) as database:
            database.execute("INSERT INTO Entries(p_location) VALUES('/mnt/us/documents/BILICOMICS.sh')")
        self.invoke("admit", False)

    def test_registration_association_projection_and_exact_mime_namespace(self):
        self.invoke("capture")
        with sqlite3.connect(self.reg) as database:
            database.execute("INSERT INTO associations VALUES('unrelated','MT:image/jpeg')")
        self.invoke("verify")
        with sqlite3.connect(self.reg) as database:
            database.execute("INSERT INTO associations VALUES('secondary','MT:text/x-shellscript')")
        self.invoke("verify", False)
        with sqlite3.connect(self.reg) as database:
            database.execute("UPDATE associations SET contentId='text/x-shellscript'")
        self.invoke("admit", False)

    def test_leaf_link_owner_and_native_gate(self):
        handler = self.at("/var/local/real-kmc/bin/sh_integration_launcher")
        self.invoke("admit")
        handler.rename(handler.with_suffix(".original"))
        handler.symlink_to(handler.with_suffix(".original"))
        self.invoke("admit", False)
        handler.unlink()
        handler.with_suffix(".original").rename(handler)
        for reader in ("aidoku", "weread", "bilicomics"):
            self.write(f"/tmp/{reader}-launcher.lock", "other")
            self.invoke("admit", False)
            self.at(f"/tmp/{reader}-launcher.lock").unlink()
            self.write(f"/on-{reader}", "other")
            self.invoke("admit", False)
            self.at(f"/on-{reader}").unlink()
        self.write(f"/var/tmp/aidoku-native-{LABEL}/owner", "other")
        self.invoke("capture", False)
        self.write("/statusbar", "statusbar stop/waiting")
        self.invoke("admit", False)


if __name__ == "__main__":
    unittest.main()
