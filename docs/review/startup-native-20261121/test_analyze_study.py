import io
import json
from pathlib import Path
import shutil
import struct
import tarfile
import tempfile
import unittest

import analyze_study as study
from observer import observer


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        private = tempfile.TemporaryDirectory()
        self.addCleanup(private.cleanup)
        self.work = Path(private.name)
        self.ref_plan, self.reference = self.plan("reference", "1" * 16)
        self.plan_dir, self.study = self.plan("study", "2" * 16)
        self.write_receipt(self.reference, ["reference"])
        self.write_receipt(self.study, study.schedule())

    def plan(self, mode, label, schedule_name=None):
        plan = self.work / "plans" / ("run-" + label)
        batch = self.work / "batches" / (mode + "-" + label)
        plan.mkdir(parents=True)
        batch.mkdir(parents=True)
        first = schedule_name == "pictures-first"
        document = {"label": label, "mode": mode, "origin": "stock-tap" if first else "indexed-uri",
                    "tap_files": {"input/artwork.guard": "frozen fixture"} if first else {},
                    "cases": ["reference"] if mode == "reference" else study.schedule(first),
                    "reference": None if mode == "reference" else str(self.reference)}
        if schedule_name is not None:
            document["schedule"] = schedule_name
            (plan / "schedule").write_text(schedule_name + "\n")
        (plan / "plan.json").write_text(json.dumps(document))
        return plan, batch

    def write_receipt(self, batch, names, schedule_name=None):
        mode = batch.name.split("-")[0]
        identity = {"project": "Aidoku", "run_id": "aidoku-" + batch.name, "owner_thread": "owner",
                    "lease_token_sha256": "fixture", "ok": True}
        day = "01" if mode == "reference" else "02"
        events = []
        def append(name, tag=None):
            event = {"operation": name, "status": 0, "reason": None}
            if tag is not None:
                event["observed"] = [tag]
            events.append(event)
        append("admit", "guard_admitted")
        append("prepare", "device_prepared")
        for case in names:
            append(case, "device_reference" if case == "reference" else "device_picture" if case.endswith("-picture") else "device_case")
            append("collect-" + case)
            append("discard-case", "device_discarded")
        append("close", "device_closed")
        acquired = dict(identity, operation="acquire", state="held", acquired_at_utc=f"2026-01-{day}T08:00:00+00:00")
        released = dict(identity, operation="release", state="released", released_at_utc=f"2026-01-{day}T08:12:00+00:00",
                        owner_and_token_checked=True, lease_directory_absent_after_release=True,
                        only_owned_owner_json_removed_then_rmdir=True, native_restored_and_connections_closed=True)
        receipt = dict(mode=mode, ok=True, operations=events, acquisition=acquired, release=released, completed=names)
        if schedule_name is not None:
            receipt["schedule"] = schedule_name
        (batch / "receipt.json").write_text(json.dumps(receipt))

    @staticmethod
    def frame(raw, start):
        return f"AFR2 1 1872 4960 0 {start} {start + 200}\n".encode() + raw

    def samples(self):
        stock = bytearray(b"\xf3" * observer.FRAME_BYTES)
        text = stock.copy()
        art = stock.copy()
        regions = observer.regions(1)
        for group, rects in enumerate(regions):
            for rect in rects:
                for pos in observer.indexes(rect, 0):
                    row, col = divmod(pos, len(observer.XS))
                    offset = observer.YS[row] * observer.WIDTH + observer.XS[col]
                    art[offset] = pos % 160
                    if group == 0:
                        text[offset] = art[offset]
        for row in range(12, 27):
            for col in range(observer.WIDTH - 120, observer.WIDTH - 80):
                offset = observer.WIDTH * row + col
                stock[offset] = text[offset] = art[offset] = 4
        return [bytes(data) for data in (stock, text, art)]

    def pack(self, batch, case, lower, images, origin="indexed-uri", qualified=None):
        members = {"origin": (origin + "\n").encode(), "qualified": (f"{case} {origin}\n" if qualified is None else qualified).encode(),
                   "anchor": f"{lower} {lower + 100}\n".encode(), **images}
        with tarfile.open(batch / (case + ".tar.gz"), "w:gz", compresslevel=1) as archive:
            for name, data in members.items():
                member = tarfile.TarInfo("out/" + case + "/" + name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))

    def evidence(self):
        stock, text, art = self.samples()
        first = self.frame(art, 800_000)
        second = self.frame(art, 1_300_000)
        ref_frames = {"stock.frame": self.frame(stock, 100_000), "library-a.frame": first, "library-b.frame": second}
        self.pack(self.reference, "reference", 200_000, ref_frames)
        for key, data in ref_frames.items():
            (self.reference / key).write_bytes(data)
        observer.freeze(*(self.reference / key for key in ref_frames), 1, 0, self.reference / "rule")
        shutil.copyfile(self.reference / "rule", self.plan_dir / "rule")
        shutil.copyfile(observer.__file__, self.plan_dir / "observer.py")
        raster_values = tuple(map(observer.sample, (stock, text, art)))
        plan = json.loads((self.plan_dir / "plan.json").read_bytes())
        for index, case in enumerate(plan["cases"], 1):
            begin = index * 30_000_000
            lower = begin + observer.PERIOD * 5 + 50_000
            images = {"stock.frame": self.frame(stock, begin - 100_000)}
            if case.endswith("-picture"):
                images["final.frame"] = self.frame(art, begin + 12_500_000)
            else:
                text_index = 12 if case.startswith("candidate") else 15
                art_index = text_index + 4
                trace_data = bytearray()
                for slot in range(observer.SLOTS):
                    sample = raster_values[0 if slot < text_index else 1 if slot < art_index else 2]
                    start = begin + slot * observer.PERIOD
                    trace_data.extend(struct.pack("<QQI", start, start + 300, 0) + sample)
                end = begin + (observer.SLOTS - 1) * observer.PERIOD + 300
                images["trace"] = f"AFW2 1 1872 4960 {begin} {end} 1000 {observer.SLOTS} 0\n".encode() + trace_data
            self.pack(self.study, case, lower, images, origin=plan["origin"])

    def test_end_to_end_and_missing_case_keeps_only_diagnostics(self):
        self.evidence()
        result = study.analyze(self.work, self.plan_dir, self.reference)
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(len(result["cases"]), 16)
        self.assertNotIn("schedule", result)
        self.assertFalse(result["diagnostic_only"])
        for endpoint in study.ENDPOINTS:
            verdict = result["cohort"]["endpoints"][endpoint]
            self.assertEqual(verdict["resolved_faster"], 6)
            self.assertEqual(verdict["median_midpoint_saving_ms"], 225)
            self.assertEqual(verdict["within_resolution"], 0)
        self.assertTrue(result["cohort"]["consistent_warm_screen_gain"])
        self.assertFalse(result["cohort"]["warm_300_ms_target"])
        (self.study / "candidate-03.tar.gz").unlink()
        result = study.analyze(self.work, self.plan_dir, self.reference)
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["cases"]), 15)
        self.assertIn("candidate-03", result["errors"])
        self.assertIsNone(result["cohort"])

    def test_picture_first_needs_all_original_samples_and_separate_pictures(self):
        self.plan_dir, self.study = self.plan("study", "3" * 16, "pictures-first")
        expected = ["control-picture", "candidate-picture", *study.schedule()[:-2]]
        self.write_receipt(self.study, expected, "pictures-first")
        self.evidence()
        result = study.analyze(self.work, self.plan_dir, self.reference)
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual((result["schedule"], list(result["cases"])), ("pictures-first", expected))
        self.assertTrue(result["qualification"]["independent_full_body_and_right_status"])
        for endpoint in study.ENDPOINTS:
            self.assertEqual(result["cohort"]["endpoints"][endpoint]["median_midpoint_saving_ms"], 225)
            self.assertEqual(result["cohort"]["endpoints"][endpoint]["resolved_faster"], 6)
        self.write_receipt(self.study, study.schedule(), "pictures-first")
        with self.assertRaisesRegex(ValueError, "incomplete_transaction"):
            study.transaction(study.validate_plan(self.plan_dir, "study"), self.study)
        self.write_receipt(self.study, expected, "pictures-first")
        (self.study / "candidate-06.tar.gz").unlink()
        result = study.analyze(self.work, self.plan_dir, self.reference)
        self.assertIsNone(result["cohort"])
        self.assertIn("candidate-06", result["errors"])

    def test_only_named_stock_study_can_move_pictures_and_receipt_must_agree(self):
        plan_dir, batch = self.plan("study", "3" * 16, "pictures-first")
        path = plan_dir / "plan.json"
        original = json.loads(path.read_bytes())
        expected = original["cases"]
        for changes in ({"origin": "indexed-uri", "tap_files": {}}, {"schedule": None}, {"schedule": "anything"},
                        {"cases": study.schedule()}, {"cases": expected[1:]}, {"cases": [*expected[:-1], expected[-2]]}):
            path.write_text(json.dumps(original | changes))
            with self.assertRaises(ValueError):
                study.validate_plan(plan_dir, "study")
        path.write_text(json.dumps(original))
        for name in (None, "pictures-last"):
            self.write_receipt(batch, expected, name)
            with self.assertRaisesRegex(ValueError, "receipt_schedule_mismatch"):
                study.transaction(study.validate_plan(plan_dir, "study"), batch)
        renamed_legacy_plan, _ = self.plan("study", "4" * 16, "pictures-last")
        self.assertEqual(study.validate_plan(renamed_legacy_plan, "study")["cases"], study.schedule())

    def test_changed_qualifier_origin_and_archive_scope_are_rejected(self):
        private = self.work / "private"
        private.mkdir()
        case = "candidate-picture"
        data = {"stock.frame": b"data", "final.frame": b"data"}
        for origin, qual, images, reason in [
                ("stock-tap", None, data, "wrong_case_or_trigger"), ("indexed-uri", "control-picture indexed-uri\n", data, "wrong_case_or_trigger"),
                ("indexed-uri", None, dict(data, unrelated=b"user"), "wrong_case_archive_members"),
                ("indexed-uri", None, {"stock.frame": b"data"}, "missing_case_member")]:
            with self.subTest(reason=reason):
                self.pack(self.study, case, 200_000, images, origin, qual)
                with tempfile.TemporaryDirectory(dir=private) as temporary, self.assertRaisesRegex(ValueError, reason):
                    study.unpack(self.study, case, "indexed-uri", Path(temporary))

    def test_unqualified_close_foreign_lease_and_changed_schedule(self):
        plan = study.validate_plan(self.plan_dir, "study")
        path = self.study / "receipt.json"
        for mutation in ("not_closed", "changed_lease", "broken_schedule"):
            self.write_receipt(self.study, study.schedule())
            receipt = json.loads(path.read_text())
            if mutation == "not_closed":
                receipt["operations"][-1]["observed"] = ["stage_absent"]
            elif mutation == "changed_lease":
                receipt["release"]["owner_thread"] = "elsewhere"
            else:
                receipt["completed"][3], receipt["completed"][4] = receipt["completed"][4], receipt["completed"][3]
            path.write_text(json.dumps(receipt))
            with self.assertRaises(ValueError):
                study.transaction(plan, self.study)

    def test_resolved_slower_touching_brackets_and_opposing_guard(self):
        results = {}
        for pair in range(1, 7):
            for side in ("control", "candidate"):
                first = [200, 300] if side == "control" else [0, 100] if pair < 6 else [100, 200]
                other = [0, 100] if side == "control" else [200, 300]
                results[f"{side}-{pair:02}"] = {"brackets_ms": dict(zip(study.ENDPOINTS, (first, other)))}
        result = study.summarize(results)
        self.assertEqual(result["endpoints"]["text_and_navigation"]["resolved_faster"], 5)
        self.assertEqual(result["endpoints"]["text_and_navigation"]["within_resolution"], 1)
        self.assertEqual(result["endpoints"]["complete_library"]["resolved_slower"], 6)
        self.assertFalse(result["consistent_warm_screen_gain"])


if __name__ == "__main__":
    unittest.main()
