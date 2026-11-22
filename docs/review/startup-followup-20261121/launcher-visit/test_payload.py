import io
import json
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest

import payload


def seed_relative(index: int) -> str:
    first = format(index, "x") * 64
    second = format(index + 9, "x") * 64
    return f"local/content/{first}/{second}/000001.jpg"


def launcher_fixture() -> bytes:
    return b"""#!/bin/sh
set -eu
AIDOKU_HOME=/mnt/us/aidoku
APP_DIR=$AIDOKU_HOME/app
DATA_DIR=$AIDOKU_HOME/data
ordinary_exit() { return 0; }
run_application() {
    if [ "$1" = "resume" ]; then
        "$APP_DIR/aidoku" --kindle --device "$PROFILE" --data-dir "$DATA_DIR" --resume 3>&- &
    else
        "$APP_DIR/aidoku" --kindle --device "$PROFILE" --data-dir "$DATA_DIR" 3>&- &
    fi
}
while :; do
    case "$event" in
        'app-exited '*)
            set -- $event
            app_exit_status=$5
            app_exit_phase=$APP_PHASE
            if ordinary_exit "$3"; then exit "$app_exit_status"; fi
            ;;
    esac
done
"""


def build_seed_database(database_path: Path, label: str, relatives: list[str]) -> None:
    database = sqlite3.connect(database_path)
    try:
        database.execute(
            "CREATE TABLE books (title TEXT, source_id TEXT, in_library INTEGER, document TEXT)"
        )
        for index, relative in enumerate(relatives, start=1):
            document = {
                "title": f"Fixture {index:02d}",
                "cover": f"/mnt/us/.aidoku-native-{label}/seed/{relative}",
            }
            database.execute(
                "INSERT INTO books (title, source_id, in_library, document) VALUES (?, 'local', 1, ?)",
                (f"Fixture {index:02d}", json.dumps(document)),
            )
    finally:
        database.commit()
        database.close()


class PayloadTests(unittest.TestCase):
    def prepare_tree(self, root: Path, label: str = "0123456789abcdef"):
        plan_dir = root / "private" / "visit-plan"
        inputs = root / "inputs"
        seed = root / "seed"
        (inputs / "app").mkdir(parents=True)
        seed.mkdir()
        (inputs / "launcher.sh").write_bytes(launcher_fixture())
        (inputs / "guard.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (inputs / "aidoku-desktop").write_bytes(b"desktop executable")
        (inputs / "app" / "aidoku").write_bytes(b"aidoku binary")
        (inputs / "app" / "aidoku-startup-portrait").write_bytes(b"portrait helper")

        relatives = [seed_relative(index) for index in range(1, payload.EXPECTED_BOOKS + 1)]
        source_by_slot: dict[str, bytes] = {}
        for index in range(1, payload.EXPECTED_BOOKS + 1):
            slot = f"{index:02d}.jpg"
            data = f"fixture-source-{index}".encode("ascii")
            (inputs / slot).write_bytes(data)
            source_by_slot[slot] = data

        permutation = ["03.jpg", "01.jpg", "06.jpg", "02.jpg", "05.jpg", "04.jpg"]
        for relative, slot in zip(relatives, permutation, strict=True):
            target = seed.joinpath(*relative.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source_by_slot[slot])
        build_seed_database(seed / "library.sqlite3", label, relatives)
        return plan_dir, inputs, seed, label, relatives, permutation

    @staticmethod
    def clone_member(member: tarfile.TarInfo) -> tarfile.TarInfo:
        copied = tarfile.TarInfo(member.name)
        copied.size = member.size
        copied.mode = member.mode
        copied.mtime = member.mtime
        copied.uid = member.uid
        copied.gid = member.gid
        copied.uname = member.uname
        copied.gname = member.gname
        copied.type = member.type
        copied.linkname = member.linkname
        copied.devmajor = member.devmajor
        copied.devminor = member.devminor
        copied.pax_headers = dict(member.pax_headers)
        return copied

    def mutate_payload(self, plan_dir: Path, edit, *, archive_format: int = tarfile.USTAR_FORMAT) -> None:
        original = (plan_dir / payload.PAYLOAD_NAME).read_bytes()
        output = io.BytesIO()
        with tarfile.open(fileobj=io.BytesIO(original), mode="r:gz") as source:
            records = []
            for member in source.getmembers():
                stream = source.extractfile(member)
                records.append((member, b"" if stream is None else stream.read()))
        with tarfile.open(fileobj=output, mode="w:gz", format=archive_format) as target:
            edit(target, records)
        (plan_dir / payload.PAYLOAD_NAME).write_bytes(output.getvalue())

    def write_records(self, target: tarfile.TarFile, records) -> None:
        for member, content in records:
            copied = self.clone_member(member)
            target.addfile(copied, io.BytesIO(content))

    def test_freeze_and_load_valid_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan_dir, inputs, seed, label, relatives, permutation = self.prepare_tree(root)
            payload.freeze(plan_dir, inputs, seed, label)
            plan, blob = payload.load(plan_dir)
            self.assertEqual(plan["protocol"], payload.PROTOCOL)
            self.assertEqual(plan["label"], label)
            self.assertEqual(plan["visit_count"], 1)
            self.assertFalse(plan["deployment"])
            self.assertGreater(len(blob), 0)
            self.assertEqual([item["input"] for item in plan["seed_cover_binding"]], permutation)
            self.assertEqual(
                [item["member"] for item in plan["seed_cover_binding"]],
                [f"seed/{relative}" for relative in relatives],
            )
            self.assertTrue(
                all(item["member"].startswith("seed/local/content/") for item in plan["seed_cover_binding"])
            )
            self.assertTrue(all(item["member"].endswith("/000001.jpg") for item in plan["seed_cover_binding"]))
            self.assertTrue(any(len(item["name"]) > 100 for item in plan["payload_members"]))

    def test_label_requires_16_lower_hex(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan_dir, inputs, seed, _, _, _ = self.prepare_tree(root)
            with self.assertRaises(ValueError):
                payload.freeze(plan_dir, inputs, seed, "BAD-LABEL")

    def test_cover_mapping_requires_one_to_one_match(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan_dir, inputs, seed, label, relatives, _ = self.prepare_tree(root)
            duplicate = (inputs / "01.jpg").read_bytes()
            (seed / Path(*relatives[0].split("/"))).write_bytes(duplicate)
            (seed / Path(*relatives[1].split("/"))).write_bytes(duplicate)
            with self.assertRaises(ValueError):
                payload.freeze(plan_dir, inputs, seed, label)

    def test_cover_paths_must_stay_inside_seed_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan_dir, inputs, seed, label, relatives, _ = self.prepare_tree(root)
            database = sqlite3.connect(seed / "library.sqlite3")
            try:
                document = {"cover": f"/mnt/us/.aidoku-native-{label}/seed/../{relatives[0]}"}
                database.execute("UPDATE books SET document=? WHERE title='Fixture 01'", (json.dumps(document),))
            finally:
                database.commit()
                database.close()
            with self.assertRaises(ValueError):
                payload.freeze(plan_dir, inputs, seed, label)

    def test_cover_paths_accept_only_jpg(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan_dir, inputs, seed, label, _, _ = self.prepare_tree(root)
            database = sqlite3.connect(seed / "library.sqlite3")
            try:
                document = {"cover": f"/mnt/us/.aidoku-native-{label}/seed/local/content/{'a' * 64}/{ 'b' * 64}/000001.jpeg"}
                database.execute("UPDATE books SET document=? WHERE title='Fixture 01'", (json.dumps(document),))
            finally:
                database.commit()
                database.close()
            with self.assertRaises(ValueError):
                payload.freeze(plan_dir, inputs, seed, label)

    def test_rewrite_requires_expected_anchors(self):
        broken = launcher_fixture().replace(
            b'if ordinary_exit "$3"; then exit "$app_exit_status"; fi', b"exit 0"
        )
        with self.assertRaises(ValueError):
            payload.rewrite_launcher(broken)

    def test_rewrite_inserts_staged_ordinary_exit_once(self):
        rewritten = payload.rewrite_launcher(launcher_fixture()).decode("utf-8")
        self.assertEqual(rewritten.count("aidoku: launcher-visit ordinary_exit"), 1)
        self.assertIn('if [ "$app_exit_phase" = active ] && [ "$app_exit_status" -eq 0 ]; then', rewritten)
        self.assertIn('if ordinary_exit "$3"; then', rewritten)
        self.assertIn('--resume 3>&-', rewritten)
        self.assertIn('AIDOKU_RESEARCH_DATA=${AIDOKU_RESEARCH_DATA:?AIDOKU_RESEARCH_DATA required}', rewritten)
        self.assertNotIn("AIDOKU_RESEARCH_DATA:-$AIDOKU_HOME/data", rewritten)
        self.assertIn('--offline --screen library --performance-trace "$AIDOKU_RESEARCH_OUT/application.json"', rewritten)
        self.assertIn('2>"$AIDOKU_RESEARCH_OUT/native.log"', rewritten)

    def test_load_rejects_duplicate_path_traversal_symlink_and_bad_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan_dir, inputs, seed, label, _, _ = self.prepare_tree(root)
            payload.freeze(plan_dir, inputs, seed, label)

            with self.subTest(case="duplicate"):
                self.mutate_payload(
                    plan_dir,
                    lambda target, records: self.write_records(target, records)
                    or target.addfile(self.clone_member(records[0][0]), io.BytesIO(records[0][1])),
                )
                with self.assertRaises(ValueError):
                    payload.load(plan_dir)

            safe_dir = plan_dir.parent / "safe"
            payload.freeze(safe_dir, inputs, seed, label)
            with self.subTest(case="path_traversal"):
                def write_escape(target, records):
                    self.write_records(target, records)
                    escape = tarfile.TarInfo("../escape")
                    escape.size = 1
                    escape.mode = 0o600
                    target.addfile(escape, io.BytesIO(b"x"))

                self.mutate_payload(safe_dir, write_escape)
                with self.assertRaises(ValueError):
                    payload.load(safe_dir)

            safe2 = plan_dir.parent / "safe2"
            payload.freeze(safe2, inputs, seed, label)
            with self.subTest(case="symlink"):
                def write_symlink(target, records):
                    self.write_records(target, records)
                    link = tarfile.TarInfo("launch.sh")
                    link.type = tarfile.SYMTYPE
                    link.linkname = "guard.sh"
                    target.addfile(link)

                self.mutate_payload(safe2, write_symlink)
                with self.assertRaises(ValueError):
                    payload.load(safe2)

            safe3 = plan_dir.parent / "safe3"
            payload.freeze(safe3, inputs, seed, label)
            with self.subTest(case="high_mode_bits"):
                def write_bad_mode(target, records):
                    for member, content in records:
                        copied = self.clone_member(member)
                        if copied.name == "launch.sh":
                            copied.mode = 0o1755
                        target.addfile(copied, io.BytesIO(content))

                self.mutate_payload(safe3, write_bad_mode)
                with self.assertRaises(ValueError):
                    payload.load(safe3)

    def test_load_rejects_pax_extended_headers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan_dir, inputs, seed, label, _, _ = self.prepare_tree(root)
            payload.freeze(plan_dir, inputs, seed, label)

            def write_pax(target, records):
                self.write_records(target, records)

            self.mutate_payload(plan_dir, write_pax, archive_format=tarfile.PAX_FORMAT)
            with self.assertRaises(ValueError):
                payload.load(plan_dir)

    def test_freeze_rejects_plan_directory_reuse(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan_dir, inputs, seed, label, _, _ = self.prepare_tree(root)
            payload.freeze(plan_dir, inputs, seed, label)
            with self.assertRaises(ValueError):
                payload.freeze(plan_dir, inputs, seed, label)

    def test_load_rejects_invalid_plan_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan_dir, inputs, seed, label, _, _ = self.prepare_tree(root)
            payload.freeze(plan_dir, inputs, seed, label)
            plan = json.loads((plan_dir / payload.PLAN_NAME).read_text(encoding="utf-8"))
            plan["visit_count"] = 2
            (plan_dir / payload.PLAN_NAME).write_text(json.dumps(plan), encoding="utf-8")
            with self.assertRaises(ValueError):
                payload.load(plan_dir)

    def test_load_rejects_non_whitelist_plan_member(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan_dir, inputs, seed, label, _, _ = self.prepare_tree(root)
            payload.freeze(plan_dir, inputs, seed, label)
            plan = json.loads((plan_dir / payload.PLAN_NAME).read_text(encoding="utf-8"))
            plan["payload_members"].append({"name": "extra.bin", "mode": 0o600, "size": 1})
            (plan_dir / payload.PLAN_NAME).write_text(json.dumps(plan), encoding="utf-8")
            with self.assertRaises(ValueError):
                payload.load(plan_dir)


if __name__ == "__main__":
    unittest.main()
