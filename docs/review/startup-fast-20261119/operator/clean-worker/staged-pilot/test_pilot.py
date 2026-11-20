from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

from analyze_pilot import POLICY, confirmation_gate, recorded_confirmation, row, screen_gate
from cohort import CONFIRMATION, ORIGIN, check_protocol, schedule
from run_pilot import operate, transport_input


class TraceTests(unittest.TestCase):
    def confirmation_plan(self):
        return {"mode": "confirmation", "regime": "cold", "origin": ORIGIN, "label": "1234567890abcdef",
                "cases": schedule("cold", "confirmation"), "pairs": 10, "decision": dict(CONFIRMATION)}

    def test_confirmation_is_independent_balanced_and_cold_only(self):
        plan = self.confirmation_plan()
        cases = check_protocol(plan)
        expected = ["baseline-warmup", "candidate-warmup"]
        for odd in (1, 3, 5, 7, 9):
            expected += [f"baseline-{odd:02}", f"candidate-{odd:02}", f"candidate-{odd + 1:02}", f"baseline-{odd + 1:02}"]
        self.assertEqual(cases, expected + ["baseline-picture", "candidate-picture"])
        for changes in [{"regime": "warm"}, {"pairs": 6}, {"cases": cases[:5] + cases[6:]}, {"decision": {}},
                        {"mode": "screen"}, {"decision": dict(CONFIRMATION, min_faster_pairs=8)}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                check_protocol(dict(plan, **changes))

    def test_confirmation_enforces_pair_count_wins_median_guard_and_pictures(self):
        rows = [{"usable_ms": -50, "covers_ms": 100} for number in range(10)]
        rows[0]["covers_ms"] = -120
        self.assertTrue(confirmation_gate(rows, True))
        self.assertFalse(confirmation_gate(rows[:9], True))
        self.assertFalse(confirmation_gate(rows, False))
        self.assertFalse(confirmation_gate([dict(sample, covers_ms=99) for sample in rows], True))
        self.assertFalse(confirmation_gate([dict(sample, usable_ms=-51) for sample in rows], True))
        rows[1]["covers_ms"] = 0
        self.assertFalse(confirmation_gate(rows, True))

    def test_device_accepts_tenth_pair_only_in_a_frozen_schedule(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = Path(__file__).with_name("device.sh").read_text().split('case "$mode" in\n    admit)', 1)[0]
            command = ["sh", "-c", script + '\nstage=$5; listed_case "$6"', "batch", "check", "1234567890abcdef", "-", ":", temporary]
            for mode in ("screen", "confirmation"):
                allowed = schedule("cold", mode)
                (root / "cases").write_text(" ".join(allowed) + "\n")
                for name in ("candidate-07", "baseline-10", "candidate-10", "candidate-11", "baseline-1", "prime", "bad/name"):
                    self.assertEqual(subprocess.run([*command, name], capture_output=True).returncode == 0, name in allowed)

    def test_confirmation_requires_matching_lease_and_complete_operation_sequence(self):
        plan = self.confirmation_plan()
        request = SimpleNamespace(run_id="aidoku-pilot-" + plan["label"])
        credentials = dict(project="Aidoku", owner_thread="fixture", run_id=request.run_id, lease_token_sha256="fixture")
        report = {"scope": ORIGIN, "regime": "cold", "operations": []}
        lease = SimpleNamespace(acquire=lambda _: credentials.copy(), status=lambda _: {"ownership_verified": True},
                                release=lambda *args, **kwargs: credentials.copy())
        outputs = {"admit": b"admitted", "prepare": b"prepared", "case": b"case_completed",
                   "restore": b"protected_state_equal\nstock_body_equal\nnative_restored", "collect": b"archive", "close": b"stage_absent"}
        with tempfile.TemporaryDirectory() as temporary:
            operate(lease, request, Path(temporary), plan, lambda mode, name: (0, outputs[mode], b""), report)
        self.assertTrue(recorded_confirmation(plan, report))
        self.assertFalse(recorded_confirmation(plan, dict(report, operations=report["operations"][1:])))
        self.assertFalse(recorded_confirmation(plan, dict(report, restoration=["native_restored"])))
        self.assertFalse(recorded_confirmation(plan, dict(report, release=dict(report["release"], owner_thread="other"))))

    def test_six_pair_screen_requires_wins_guard_and_valid_pictures(self):
        pairs = [{"usable_ms": -50, "covers_ms": 100} for number in range(6)]
        self.assertTrue(screen_gate(pairs, "cold", True))
        self.assertFalse(screen_gate(pairs[:2], "cold", True))
        self.assertFalse(screen_gate(pairs, "cold", False))
        self.assertFalse(screen_gate(pairs, "warm", True))
        for item in pairs[:2]:
            item["covers_ms"] = -1
        self.assertFalse(screen_gate(pairs, "cold", True))
        for item in pairs:
            item.update(usable_ms=-51, covers_ms=101)
        self.assertFalse(screen_gate(pairs, "cold", True))

    def test_portrait_scanout_reads_only_the_active_page(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, value in {"stride":"1872", "bits_per_pixel":"8", "rotate":"1", "virtual_size":"1872,4960"}.items():
                (root / name).write_text(value)
            (root / "pixels").write_bytes(b"x" * (1872 * 2480) + b"y" * (1872 * 2480))
            script = Path(__file__).with_name("device.sh").read_text().split('case "$mode" in\n    admit)', 1)[0]
            script = script.replace("/sys/class/graphics/fb0", str(root)).replace("/dev/fb0", str(root / "pixels"))
            command = ["sh", "-c", script + '\npanel_rows "$5" 1 1', "test", "check", "1234567890abcdef", "-", ":", str(root / "sample")]
            for pan, byte in [("0,0", b"x"), ("0,2480", b"y")]:
                (root / "pan").write_text(pan)
                self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
                self.assertEqual((root / "sample").read_bytes(), byte * 1872)
            (root / "pan").write_text("0,1")
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)

    def test_settled_frames_and_regime(self):
        state = dict(route="library", library_count=6, cover_visible_count=6, cover_visible_failure_count=0,
                     cover_inflight=False, cover_work_pending=False, cover_pending_count=0, cover_queued_count=0)
        first = dict(marker=11, frame_sequence=1, cover_slots=7, cover_images=0, cover_pending=True)
        last = dict(marker=12, frame_sequence=2, cover_slots=7, cover_images=7, cover_pending=False)
        events = []
        def append(name, **values):
            events.append(dict(name=name, at_us=len(events)*1000, boottime_us=1100000+len(events)*1000, **values))
        append("process_entry")
        for flag in sorted(POLICY):
            append(flag)
        append("startup_initial_frame_submitted", full=True, marker=10)
        append("startup_initial_input_ready", marker=10)
        append("startup_library_frame_submitted", **first); append("startup_cover_frame_submitted", **first)
        append("startup_library_input_ready", **first)
        for number in range(6):
            for kind in ("cover_preparation_started", "cover_local_derivative_reused", "cover_preparation_finished"):
                append(kind)
        append("startup_cover_frame_submitted", **last); append("startup_visible_covers_settled", **last)
        append("shutdown_started")
        trace = dict(clock="monotonic_rust_main", enabled=True, finished=True, success=True, dropped_events=0, events=events)
        marks = b"1.00 staged_foreground_requested\n1.07 launcher_worker\n1.50 stock_returned\n"
        self.assertEqual(row(trace, state, marks, "warm", "baseline-01")["hits"], 6)
        with self.assertRaisesRegex(ValueError, "wrong_cache_regime"):
            row(trace, state, marks, "cold", "baseline-01")
        events[-2]["cover_images"] = 6
        with self.assertRaisesRegex(ValueError, "unbound_library_frame"):
            row(trace, state, marks, "warm", "baseline-01")


class LeaseTests(unittest.TestCase):
    def test_short_exec_request_preserves_binary_stdin(self):
        blob = bytes(range(256)) * 100
        command, data = transport_input(" " * 15000 + "exec cat", blob)
        self.assertLess(len(command), 100)
        self.assertEqual(subprocess.check_output(["sh", "-c", command], input=data), blob)

    def run_batch(self, fault=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        calls = []
        lease = SimpleNamespace(acquire=lambda _: calls.append("acquired") or {},
                                status=lambda _: {"ownership_verified": True},
                                release=lambda *args, **kwargs: calls.append("released") or {})
        def remote(mode, name):
            calls.append(mode)
            if mode == fault:
                return 1, b"", b"refused test_failure\n"
            outputs = {"admit": b"admitted", "prepare": b"prepared", "case": b"case_completed",
                       "restore": b"protected_state_equal\nstock_body_equal\nnative_restored", "collect": b"archive",
                       "close": b"stage_absent"}
            return 0, outputs[mode], b""
        report = {"operations": []}
        try:
            operate(lease, SimpleNamespace(run_id="fixture"), Path(temp.name), {"cases":["baseline-01"]}, remote, report)
        except RuntimeError:
            pass
        return calls, report

    def test_real_work_then_immediate_release(self):
        calls, report = self.run_batch()
        self.assertEqual(calls, ["acquired", "admit", "prepare", "case", "restore", "collect", "close", "admit", "released"])
        self.assertTrue(report["operations_passed"])

    def test_refusal_or_failed_case_does_not_hold_recovered_device(self):
        self.assertEqual(self.run_batch("admit")[0], ["acquired", "admit", "released"])
        for mode in ("prepare", "case", "collect"):
            calls, report = self.run_batch(mode)
            self.assertEqual(calls[-3:], ["close", "admit", "released"])
            self.assertFalse(report.get("operations_passed"))

    def test_never_release_when_own_stage_is_unresolved(self):
        calls, report = self.run_batch("close")
        self.assertNotIn("released", calls)
        self.assertTrue(report["recovery_required"])


if __name__ == "__main__":
    unittest.main()
