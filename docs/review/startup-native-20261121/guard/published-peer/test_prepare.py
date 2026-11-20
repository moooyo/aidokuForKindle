import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

import prepare

sys.path.insert(0, str(prepare.HERE.parent))
import test_guard


RUN = "bili-prod-1234abcd"
BOOT = "11111111-2222-4333-8444-555555555555"


def save(path, data):
    path.write_text(json.dumps(data))


def witness(folder, mode, volume, lower, upper):
    documents = {"run": RUN, "doc_alias_mode": mode, "kindle_boot": BOOT, "doc_volume": volume,
                 "doc_lower_id": lower, "doc_upper_id": upper}
    save(folder / "document-view.json", documents)
    if (folder / "public-rollout.json").is_file():
        final = json.loads((folder / "public-rollout.json").read_text())
        final["casefold_observed"] = mode
        save(folder / "public-rollout.json", final)


def receipts(folder):
    folder.mkdir()
    save(folder / "admission.json", dict(run=RUN, protocol="production-library-rollout-1", measured_run="lib-p-b4a63eed",
         expected_document=prepare.DOCUMENT, source_id=prepare.SOURCE, profile="scribe", firmware="5.19.5", owner="fixture-owner"))
    save(folder / "smoke.json", dict.fromkeys(("ordinary_library_click", "production_runtime_verified", "exact_production_arguments",
         "matching_lifecycle_ready", "framebuffer_and_input_fds", "managed_exit", "no_user_data_exported"), True))
    save(folder / "click.json", dict(target="open-card", ok=True, buttons_clear_after=True))
    identity = dict(run_id=RUN, owner_thread="fixture-owner", project="BiliComics", lease_token_sha256="fixture", ok=True)
    save(folder / "started.json", dict(identity, state="held", operation="acquire", acquired_at_utc="2026-01-03T10:00:00+00:00"))
    save(folder / "released.json", dict(identity, state="released", operation="release", released_at_utc="2026-01-03T10:05:00+00:00",
         native_restored_and_connections_closed=True, owner_and_token_checked=True, only_owned_owner_json_removed_then_rmdir=True,
         lease_directory_absent_after_release=True))
    save(folder / "public-rollout.json", dict(run=RUN, measured_source_id=prepare.SOURCE, measured_comparison="lib-p-b4a63eed",
         production_installation_retained=True, staging_removed=True, native_restored_and_connections_closed=True, restored_home_and_pointer=True,
         exact_lease_released_utc="2026-01-03T10:05:00+00:00", initial_selected_rows=0, final_selected_rows=1,
         sole_actual_document="/mnt/us/documents/bilicomics.sh", casefold_observed="casefold-proven", initial_observe_attempt_failed=False,
         normal_smoke=json.loads((folder / "smoke.json").read_text())))
    for leaf in ("keep-intended", "recovery-verified"):
        (folder / leaf).touch()
    witness(folder, "casefold-proven", "72", "72:174:1", "72:652:1")


class Enrollment(unittest.TestCase):
    def test_scoped_recovered_original_launch_or_refusal(self):
        run = "bili-prod-f3690b72"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / run
            receipts(folder)
            for name in ("admission.json", "released.json", "started.json", "public-rollout.json", "document-view.json"):
                path = folder / name
                path.write_text(path.read_text().replace(RUN, run))
            smoke = json.loads((folder / "smoke.json").read_text())
            (folder / "smoke.json").unlink()
            smoke.pop("production_runtime_verified")
            smoke.update(new_click_sent=False, verified_on_original_lease=True, original_observer_rejected_tmp_parent_alias=True)
            save(folder / "failure.json", dict(type="ValueError", detail="Scoped rollout command failed (status 1); output withheld"))
            final = json.loads((folder / "public-rollout.json").read_text())
            final.update(initial_observe_attempt_failed=True, normal_smoke=smoke)
            save(folder / "public-rollout.json", final)
            save(folder / "recovered-smoke.json", smoke)
            self.assertEqual(prepare.accepted(folder, run)["ALIAS_MODE"], "casefold-proven")
            for key, wrong in (("new_click_sent", True), ("verified_on_original_lease", False), ("original_observer_rejected_tmp_parent_alias", False)):
                altered = dict(smoke, **{key: wrong})
                save(folder / "public-rollout.json", dict(final, normal_smoke=altered))
                save(folder / "recovered-smoke.json", altered)
                with self.assertRaises(ValueError):
                    prepare.accepted(folder, run)

    def test_incomplete_or_contradictory_owner_receipt_refuses(self):
        changes = [(None, "keep-intended", None), (None, "recovery-verified", None),
                   (None, "failure.json", {}), ("released.json", "owner_thread", "other"),
                   ("released.json", "lease_directory_absent_after_release", False),
                   ("smoke.json", "production_runtime_verified", False), ("click.json", "target", "other"),
                   ("admission.json", "source_id", "wrong-selection"), ("document-view.json", "run", "bili-prod-11111111"),
                   ("document-view.json", "doc_alias_mode", "same-inode"), ("document-view.json", "doc_lower_id", "71:174:1"),
                   ("document-view.json", "doc_upper_id", "72:174:1")]
        for record, key, value in changes:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                folder = root / RUN
                receipts(folder)
                if record:
                    path = folder / record
                    fields = json.loads(path.read_text())
                    fields[key] = value
                    save(path, fields)
                elif value is None:
                    (folder / key).unlink()
                else:
                    save(folder / key, value)
                with self.assertRaises((ValueError, FileNotFoundError)):
                    prepare.produce(prepare.HERE.parent / "guard.sh", folder, RUN, root)
                self.assertEqual(list(root.glob("frozen-*")), [])

    def test_each_emitted_mode_is_fixed_and_output_never_overwrites(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / RUN
            receipts(folder)
            base = prepare.HERE.parent / "guard.sh"
            original = base.read_bytes()
            for mode, upper in (("absent", "absent"), ("same-inode", "72:174:1"), ("casefold-proven", "72:652:1")):
                witness(folder, mode, "72", "72:174:1", upper)
                target = prepare.produce(base, folder, RUN, root)
                self.assertEqual(target.stat().st_mode & 0o777, 0o700)
                shell = (target / "guard.sh").read_text()
                self.assertIn("case '" + mode + "' in", shell)
                self.assertNotIn("@BOOT@", shell)
                self.assertEqual(subprocess.run(["sh", "-n", target / "guard.sh"]).returncode, 0)
            self.assertEqual(base.read_bytes(), original)
            self.assertEqual(len(list(root.glob("frozen-*"))), 3)

    def test_distinct_lookup_inodes_but_one_dirent_and_one_correct_catalog_row(self):
        fixture = test_guard.GuardTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        root = fixture.root
        folder = root / RUN
        receipts(folder)
        fixture.write("/proc/sys/kernel/random/boot_id", BOOT)
        home = fixture.at("/mnt/us/bilicomics")
        for name in ("app", "data", "notices"):
            (home / name).mkdir(parents=True)
        for name in ("data/device-allowlist", "data/device-profile", "README.md", "THIRD_PARTY_NOTICES.md", "notices/Cargo.lock"):
            (home / name).write_bytes(b"fixture")
        for name, size in (("app/bilicomics", 74340832), ("app/kindle-jpeg-worker", 426672), ("launch.sh", 80185)):
            with (home / name).open("wb") as stream:
                stream.truncate(size)
            (home / name).chmod(0o755)
        lower = fixture.at("/mnt/us/documents/bilicomics.sh")
        lower.write_text(prepare.DOCUMENT)
        lower.chmod(0o755)
        alias = root / "upper-lookup-only"
        alias.write_text(prepare.DOCUMENT)
        self.assertNotEqual(lower.stat().st_ino, alias.stat().st_ino)
        witness(folder, "casefold-proven", "72", "72:174:1", "72:652:1")
        with sqlite3.connect(fixture.catalog) as database:
            database.execute("ALTER TABLE Entries ADD COLUMN p_titles_0_nominal")
            database.execute("INSERT INTO Entries VALUES('fixture-bili','/mnt/us/documents/bilicomics.sh','text/x-shellscript','art',9,95,19,'BiliComics for Kindle')")
        generated = prepare.produce(prepare.HERE.parent / "guard.sh", folder, RUN, root)
        code = (generated / "guard.sh").read_text()
        parts = ("/mnt/us", "/var/tmp", "/var/local", "/usr/lib/ccat", "/proc", "/sys", "/tmp")
        for index, path in enumerate(parts):
            for start in (' ', '"', "'", '='):
                code = code.replace(start + path, start + f"@{index}@")
        for index, path in enumerate(parts):
            code = code.replace(f"@{index}@", str(fixture.at(path)))
        for name in ("Aidoku.sh", "weread.sh", "bilicomics.sh"):
            path = "/mnt/us/documents/" + name
            code = code.replace("'" + str(fixture.at(path)) + "'", "'" + path + "'")
        code = code.replace("exec sh " + str(fixture.at("/mnt/us/bilicomics/launch.sh")), "exec sh /mnt/us/bilicomics/launch.sh")
        code = code.replace("upper=$documents/BiliComics.sh", "upper=" + str(alias))
        fixture.script.write_text(code)
        fixture.invoke("capture")
        fixture.invoke("verify")
        fixture.write("/mnt/us/bilicomics/data/private-user-notes", "private account material")
        fixture.invoke("verify")
        alias.write_text(prepare.DOCUMENT.replace("--library", "--changed"))
        fixture.invoke("verify", False)
        alias.write_text(prepare.DOCUMENT)
        next_alias = root / "new-virtual-lookup"
        next_alias.write_text(prepare.DOCUMENT)
        next_alias.replace(alias)
        fixture.invoke("verify")
        unexpected = fixture.at("/mnt/us/documents/BiLiComics.Sh")
        unexpected.write_text(prepare.DOCUMENT)
        fixture.invoke("verify", False)
        unexpected.unlink()
        sidecar = fixture.at("/mnt/us/documents/BiLiComics.SH.SdR")
        sidecar.mkdir()
        fixture.invoke("verify", False)
        sidecar.rmdir()
        fixture.invoke("verify")
        with sqlite3.connect(fixture.catalog) as database:
            database.execute("INSERT INTO Entries(p_location) VALUES('/mnt/us/documents/BiLiComics.Sh')")
        fixture.invoke("verify", False)
        with sqlite3.connect(fixture.catalog) as database:
            database.execute("DELETE FROM Entries WHERE p_uuid IS NULL")
            database.execute("UPDATE Entries SET p_titles_0_nominal='Wrong title' WHERE p_uuid='fixture-bili'")
        fixture.invoke("verify", False)


if __name__ == "__main__":
    unittest.main()
