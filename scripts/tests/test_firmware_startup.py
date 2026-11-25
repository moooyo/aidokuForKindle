"""Check firmware startup evidence and the offline Library fixture."""

from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "verify-firmware-emulator.py"
SPECIFICATION = importlib.util.spec_from_file_location("firmware_verifier", SCRIPT)
assert SPECIFICATION is not None and SPECIFICATION.loader is not None
HARNESS = importlib.util.module_from_spec(SPECIFICATION)
sys.modules[SPECIFICATION.name] = HARNESS
SPECIFICATION.loader.exec_module(HARNESS)

LAUNCHER_STAGES = (
    "observer_start", "observer_wait_begin", "observer_ready", "portrait_begin",
    "portrait_ready", "display_begin", "display_ready", "app_spawn_begin", "app_spawn_returned",
)
LAUNCHER_LOG = "".join(
    f"aidoku: launcher-diag v1 {stage} 10.{index:03d}\n"
    for index, stage in enumerate(LAUNCHER_STAGES)
)
SUBMISSION = (
    "aidoku: startup_library_submitted boottime_us=10020000 sequence=2 marker=8 "
    "layout=large library=nonempty grid=nonempty\n"
)
LOG = LAUNCHER_LOG + SUBMISSION


def startup_trace(*toolbar_events: tuple[str, int]) -> dict:
    events = [
        {"name": "startup_initial_frame_submitted", "at_us": 100, "frame_sequence": 1, "marker": 7, "full": True},
        {"name": "startup_initial_input_ready", "at_us": 200, "frame_sequence": 1, "marker": 7},
        {"name": "startup_ready", "at_us": 300},
        {"name": "startup_library_frame_submitted", "at_us": 400, "frame_sequence": 2, "marker": 8},
    ]
    events.extend({"name": f"startup_toolbar_{name}", "at_us": timestamp} for name, timestamp in toolbar_events)
    return {"success": True, "finished": True, "dropped_events": 0,
            "events": sorted(events, key=lambda event: event["at_us"])}


class StartupEvidenceTests(unittest.TestCase):
    def test_library_accepts_a_matching_startup_without_toolbar_events(self) -> None:
        evidence = HARNESS.startup_evidence(LOG, startup_trace(), "scribe")
        self.assertEqual(evidence["layout"], "large")
        self.assertEqual(evidence["initial_frame"]["marker"], evidence["input_ready"]["marker"])
        self.assertEqual(evidence["library_frame"]["frame_sequence"], 2)
        self.assertEqual(evidence["toolbar_events"], [])
        self.assertFalse(evidence["toolbar_snapshot_used"])

    def test_library_accepts_fallback_before_the_probe_finishes(self) -> None:
        trace = startup_trace(("probe_started", 150), ("sync_fallback", 250), ("probe_unavailable", 350))
        evidence = HARNESS.startup_evidence(LOG.replace("layout=large", "layout=compact"), trace, "pw6")
        self.assertEqual(evidence["layout"], "compact")
        self.assertEqual([event["at_us"] for event in evidence["toolbar_events"]], [150, 250, 350])
        self.assertFalse(evidence["toolbar_snapshot_used"])

    def test_snapshot_evidence_requires_the_successful_probe_and_use_sequence(self) -> None:
        trace = startup_trace(("probe_started", 150), ("probe_ready", 220), ("snapshot_used", 250))
        self.assertTrue(HARNESS.startup_evidence(LOG, trace, "scribe")["toolbar_snapshot_used"])
        trace = startup_trace(("probe_started", 150), ("snapshot_used", 250))
        self.assertFalse(HARNESS.startup_evidence(LOG, trace, "scribe")["toolbar_snapshot_used"])
        trace = startup_trace(("probe_started", 150), ("probe_ready", 220), ("snapshot_used", 450))
        self.assertFalse(HARNESS.startup_evidence(LOG, trace, "scribe")["toolbar_snapshot_used"])

    def test_library_rejects_reordered_checkpoints(self) -> None:
        trace = startup_trace()
        trace["events"][1:3] = reversed(trace["events"][1:3])
        with self.assertRaisesRegex(AssertionError, "reorders a Library checkpoint"):
            HARNESS.startup_evidence(LOG, trace, "scribe")

    def test_library_rejects_inconsistent_input_fence_and_submission(self) -> None:
        for field, value in (("marker", 9), ("frame_sequence", 2), ("at_us", 50)):
            with self.subTest(field=field):
                trace = startup_trace()
                trace["events"][1][field] = value
                with self.assertRaisesRegex(AssertionError, "full-frame/input barrier"):
                    HARNESS.startup_evidence(LOG, trace, "scribe")
        with self.assertRaisesRegex(AssertionError, "did not match the trace"):
            HARNESS.startup_evidence(LOG.replace("marker=8", "marker=9"), startup_trace(), "scribe")


class StartupFixtureTests(unittest.TestCase):
    def test_serialized_database_contains_two_offline_library_books(self) -> None:
        with closing(sqlite3.connect(":memory:")) as database:
            database.deserialize(HARNESS.startup_library_fixture())
            books = database.execute(
                "SELECT id, source_id, remote_id, title, in_library, document FROM books ORDER BY id"
            ).fetchall()
            preferences, = database.execute("SELECT document FROM preferences WHERE key = 'application'").fetchone()
        self.assertEqual([row[3] for row in books], ["Firmware fixture one", "Firmware fixture two"])
        for book_id, source, remote_id, title, in_library, document in books:
            self.assertEqual(source, "local")
            self.assertEqual(book_id, f"5:local:{remote_id}")
            self.assertEqual(in_library, 1)
            book = json.loads(document)
            self.assertEqual((book["id"], book["title"], book["in_library"]), (book_id, title, True))
        self.assertEqual(json.loads(preferences), {"language": "en"})


if __name__ == "__main__":
    unittest.main()
