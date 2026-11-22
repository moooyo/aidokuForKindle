import io
import json
import tarfile
import unittest

import evidence


def event(name, at_us, **fields):
    return {"name": name, "at_us": at_us, "boottime_us": 5_000_000 + at_us, **fields}


def visit():
    stamps = ("1.99", "2.02", "2.04", "2.04", "2.05", "2.33", "2.33", "2.43", "2.45", "2.45")
    log = b"".join(f"aidoku: launcher-diag v1 {phase} {stamp}\n".encode()
                   for phase, stamp in zip(evidence.launcher_stages.PHASES, stamps))
    log += evidence.ORDINARY_EXIT
    initial = {"marker": 11, "frame_sequence": 1, "full": True}
    library = {"marker": 12, "frame_sequence": 2, "full": False,
               "cover_slots": 7, "cover_images": 2, "cover_pending": True}
    settled = {"marker": 13, "frame_sequence": 3, "full": False,
               "cover_slots": 7, "cover_images": 7, "cover_pending": False}
    trace = {
        "schema_version": 1, "clock": "monotonic_rust_main", "process_id": 987654,
        "enabled": True, "finished": True, "success": True, "dropped_events": 0,
        "events": [
            {"name": "process_entry", "at_us": 0},
            event("startup_initial_frame_submitted", 100, **initial),
            event("startup_library_frame_submitted", 110, **library),
            event("startup_cover_frame_submitted", 111, **library),
            event("startup_initial_input_ready", 120, **initial),
            event("startup_library_input_ready", 130, **library),
            event("startup_cover_frame_submitted", 150, **settled),
            event("startup_visible_covers_settled", 160, **settled),
            event("shutdown_started", 300),
            event("shutdown_complete", 330),
            {"name": "process_finished", "at_us": 340},
        ],
        "private": "private-source-key",
    }
    state = {
        "route": "library", "offline": True, "demo": False,
        "diagnostics_available": True, "performance_timing_enabled": True,
        "capability_declaration": {"profile": "scribe", "backend": "kindle"},
        "library_count": 6, "cover_visible_count": 6, "cover_visible_failure_count": 0,
        "cover_pending_count": 0, "cover_queued_count": 0, "cover_inflight": False,
        "cover_work_pending": False, "capture_projection_pending": False,
        "database": "/private/book-name", "current_book": "private-book-title",
    }
    return {"launcher.log": log, "application.json": json.dumps(trace).encode(),
            "state.json": json.dumps(state).encode()}


def archive(files, extra=None):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as target:
        for name, content in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            target.addfile(member, io.BytesIO(content))
        if extra is not None:
            target.addfile(extra)
    return output.getvalue()


def mutate_event(files, event_name, **updates):
    trace = json.loads(files["application.json"])
    next(event for event in trace["events"] if event["name"] == event_name).update(updates)
    files["application.json"] = json.dumps(trace).encode()


class EvidenceTests(unittest.TestCase):
    def test_real_schema_and_private_fields_do_not_escape(self):
        result = evidence.analyze_archive(archive(visit()))
        self.assertEqual(result["application"]["initial_frame_to_input_ready_us"], 20)
        self.assertEqual(result["application"]["library_frame_to_input_ready_us"], 20)
        self.assertTrue(result["ordinary_supervisor_exit"])
        self.assertTrue(result["application"]["visible_covers_frame_settled"])
        self.assertEqual(result["state"]["visible_covers"], 6)
        public = json.dumps(result)
        for secret in ("private-source-key", "private-book-title", "/private/book-name", "987654"):
            self.assertNotIn(secret, public)

    def test_rejects_bad_or_incomplete_archive(self):
        payload = archive(visit())
        for invalid in (b"", b"not gzip", payload[:-1], payload[:len(payload) // 2],
                        b"x" * (evidence.ARCHIVE_LIMIT + 1)):
            with self.subTest(length=len(invalid)), self.assertRaises(ValueError):
                evidence.analyze_archive(invalid)
        files = visit()
        del files["state.json"]
        with self.assertRaises(ValueError):
            evidence.analyze_archive(archive(files))
        link = tarfile.TarInfo("other")
        link.type = tarfile.SYMTYPE
        link.linkname = "launcher.log"
        with self.assertRaises(ValueError):
            evidence.analyze_archive(archive(visit(), link))
        files = visit()
        files["launcher.log"] = b"x" * (evidence.FILE_LIMIT + 1)
        with self.assertRaises(ValueError):
            evidence.analyze_archive(archive(files))

    def test_rejects_unbound_reordered_or_missing_fences(self):
        for name, changes in (
            ("startup_initial_frame_submitted", {"full": False}),
            ("startup_initial_input_ready", {"marker": 99}),
            ("startup_initial_input_ready", {"frame_sequence": 99}),
            ("startup_library_input_ready", {"marker": 99}),
            ("startup_library_input_ready", {"at_us": 115}),
        ):
            files = visit()
            mutate_event(files, name, **changes)
            with self.subTest(name=name, changes=changes), self.assertRaises(ValueError):
                evidence.analyze_archive(archive(files))
        files = visit()
        trace = json.loads(files["application.json"])
        trace["events"] = [event for event in trace["events"] if event["name"] != "startup_cover_frame_submitted"]
        files["application.json"] = json.dumps(trace).encode()
        with self.assertRaises(ValueError):
            evidence.analyze_archive(archive(files))

    def test_rejects_abnormal_or_ambiguous_exit(self):
        files = visit()
        files["launcher.log"] = files["launcher.log"].replace(evidence.ORDINARY_EXIT, b"")
        with self.assertRaises(ValueError):
            evidence.analyze_archive(archive(files))
        files = visit()
        files["launcher.log"] += evidence.ORDINARY_EXIT
        with self.assertRaises(ValueError):
            evidence.analyze_archive(archive(files))
        files = visit()
        mutate_event(files, "shutdown_complete", name="shutdown_timeout")
        with self.assertRaises(ValueError):
            evidence.analyze_archive(archive(files))
        files = visit()
        trace = json.loads(files["application.json"])
        trace["success"] = False
        files["application.json"] = json.dumps(trace).encode()
        with self.assertRaises(ValueError):
            evidence.analyze_archive(archive(files))

    def test_rejects_unsettled_or_unbound_covers(self):
        for changes in ({"marker": 99}, {"frame_sequence": 99}, {"cover_slots": 6},
                        {"cover_images": 6}, {"cover_pending": True}, {"at_us": 350}):
            files = visit()
            mutate_event(files, "startup_visible_covers_settled", **changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                evidence.analyze_archive(archive(files))
        files = visit()
        trace = json.loads(files["application.json"])
        trace["events"] = [event for event in trace["events"]
                           if event["name"] != "startup_visible_covers_settled"]
        files["application.json"] = json.dumps(trace).encode()
        with self.assertRaises(ValueError):
            evidence.analyze_archive(archive(files))
        files = visit()
        trace = json.loads(files["application.json"])
        index = next(index for index, event in enumerate(trace["events"])
                     if event["name"] == "startup_visible_covers_settled")
        trace["events"].insert(index + 1, {**trace["events"][index], "at_us": 170,
                                          "boottime_us": 5_000_170})
        files["application.json"] = json.dumps(trace).encode()
        with self.assertRaises(ValueError):
            evidence.analyze_archive(archive(files))
        files = visit()
        trace = json.loads(files["application.json"])
        index = next(index for index, event in enumerate(trace["events"])
                     if event["name"] == "startup_visible_covers_settled")
        trace["events"].insert(index + 1, event("startup_cover_frame_submitted", 170, marker=14,
                                              frame_sequence=4, full=False, cover_slots=7,
                                              cover_images=7, cover_pending=False))
        files["application.json"] = json.dumps(trace).encode()
        with self.assertRaises(ValueError):
            evidence.analyze_archive(archive(files))

    def test_rejects_wrong_state_and_duplicate_json_fields(self):
        for field, value in (("offline", False), ("cover_pending_count", 1),
                             ("cover_visible_failure_count", 1), ("capture_projection_pending", True)):
            files = visit()
            state = json.loads(files["state.json"])
            state[field] = value
            files["state.json"] = json.dumps(state).encode()
            with self.subTest(field=field), self.assertRaises(ValueError):
                evidence.analyze_archive(archive(files))
        files = visit()
        files["state.json"] = b'{"route":"library","route":"reader"}'
        with self.assertRaises(ValueError):
            evidence.analyze_archive(archive(files))


if __name__ == "__main__":
    unittest.main()
