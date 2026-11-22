import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import replay_cold_costs as replay
from cohort import CONFIRMATION, ORIGIN, schedule


def trace_for_jobs():
    events = [{"name": "unrelated", "at_us": 0, "private": "PRIVATE_SENTINEL"}]
    timestamp = 10
    for number in range(6):
        for index, choices in enumerate(replay.MARKS):
            name = choices[1] if index == 4 and number == 1 else choices[0]
            events.append({"name": name, "at_us": timestamp})
            timestamp += (index + 1) * 10
        timestamp += 100
    return {"events": events}


def trace_for_common_jobs(intervals=(30, 40, 50, 60, 70), include_cpu=False):
    events = [{"name": "unrelated", "at_us": 0, "private": "PRIVATE_SENTINEL"}]
    timestamp = 10
    for _ in range(6):
        events.append({"name": "cover_preparation_started", "at_us": timestamp})
        if include_cpu:
            events.append({"name": "cover_cpu_original_read", "at_us": timestamp})
        timestamp += intervals[0]
        events.append({"name": "cover_cold_validation_combined_started", "at_us": timestamp})
        if include_cpu:
            events.append({"name": "cover_cpu_source_bytes_verified", "at_us": timestamp})
        timestamp += intervals[1]
        events.append({"name": "cover_cold_decode_validated", "at_us": timestamp})
        if include_cpu:
            events.append({"name": "cover_cpu_source_decoded", "at_us": timestamp})
        timestamp += intervals[2]
        events.append({"name": "cover_local_derivative_prepared", "at_us": timestamp})
        if include_cpu:
            events.append({"name": "cover_cpu_quantized", "at_us": timestamp})
        timestamp += intervals[3]
        events.append({"name": "cover_render_ready", "at_us": timestamp})
        timestamp += intervals[4]
        events.append({"name": "cover_preparation_finished", "at_us": timestamp})
        timestamp += 100
    return {"events": events}


def plan_for_confirmation():
    return {"mode": "confirmation", "regime": "cold", "origin": ORIGIN,
            "label": "fixture", "cases": schedule("cold", "confirmation"), "pairs": 10,
            "decision": dict(CONFIRMATION)}


def add_member(archive, name, data):
    member = tarfile.TarInfo(name)
    member.size = len(data)
    archive.addfile(member, io.BytesIO(data))


class JobParserTests(unittest.TestCase):
    def test_six_serial_jobs_and_exact_interval_boundaries(self):
        jobs = replay.jobs_from_trace(trace_for_jobs())
        self.assertEqual(len(jobs), 6)
        self.assertEqual(jobs[0]["kernel_available"], "neon")
        self.assertEqual(jobs[1]["kernel_available"], "scalar")
        self.assertEqual(list(jobs[0]["interval_us"].values()), list(range(10, 130, 10)))
        self.assertEqual(jobs[0]["total_wall_us"], sum(range(10, 130, 10)))

    def test_duplicate_missing_overlapping_and_wrong_order_are_rejected(self):
        for mutation in ("duplicate", "missing", "overlap", "swapped", "fallback", "extra"):
            with self.subTest(mutation=mutation):
                events = trace_for_jobs()["events"]
                if mutation == "duplicate":
                    events.insert(3, dict(events[2]))
                elif mutation == "missing":
                    del events[5]
                elif mutation == "overlap":
                    events[4]["name"] = "cover_preparation_started"
                elif mutation == "swapped":
                    events[3]["name"], events[4]["name"] = events[4]["name"], events[3]["name"]
                elif mutation == "fallback":
                    events[3]["name"] = "cover_cold_validation_fallback"
                else:
                    events.append({"name": "cover_render_ready", "at_us": events[-1]["at_us"] + 1})
                with self.assertRaises(ValueError):
                    replay.jobs_from_trace({"events": events})

    def test_incomplete_and_missing_cpu_boundaries_are_not_attributed(self):
        for changed in (trace_for_jobs()["events"][:-1],
                        [event for event in trace_for_jobs()["events"]
                         if not event["name"].startswith("cover_cpu_")]):
            with self.assertRaises(ValueError):
                replay.jobs_from_trace({"events": changed})

    def test_invalid_or_descending_timestamps_are_rejected(self):
        for value in (-1, True, 1.5, None):
            with self.subTest(value=value):
                events = trace_for_jobs()["events"]
                events[4]["at_us"] = value
                with self.assertRaises(ValueError):
                    replay.jobs_from_trace({"events": events})
        events = trace_for_jobs()["events"]
        events[4]["at_us"] = events[3]["at_us"] - 1
        with self.assertRaisesRegex(ValueError, "invalid_clock_order"):
            replay.jobs_from_trace({"events": events})

    def test_equal_microsecond_timestamps_are_allowed(self):
        trace = trace_for_jobs()
        trace["events"][4]["at_us"] = trace["events"][3]["at_us"]
        self.assertEqual(len(replay.jobs_from_trace(trace)), 6)

    def test_paired_common_parser_ignores_cpu_detail_and_keeps_six_jobs(self):
        jobs = replay.paired_common_jobs_from_trace(trace_for_common_jobs(include_cpu=True))
        self.assertEqual(len(jobs), 6)
        self.assertEqual(jobs[0]["interval_us"], {
            "start_to_combined": 30,
            "combined_to_cold_decode_validated": 40,
            "cold_decode_validated_to_derivative_prepared": 50,
            "derivative_prepared_to_render_ready": 60,
            "render_ready_to_finished": 70,
        })
        self.assertEqual(jobs[0]["total_wall_us"], 250)

    def test_paired_common_parser_rejects_hits_and_fallback(self):
        for marker in ("cover_resident_pixels_hit", "cover_cold_validation_fallback",
                       "cover_local_derivative_reused", "cover_local_derivative_memory_hit"):
            with self.subTest(marker=marker):
                trace = trace_for_common_jobs()
                trace["events"].insert(2, {"name": marker, "at_us": trace["events"][2]["at_us"]})
                with self.assertRaises(ValueError):
                    replay.paired_common_jobs_from_trace(trace)


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.attempt = self.root / "attempt"
        self.attempt.mkdir()
        (self.root / "plan.json").write_text(json.dumps(plan_for_confirmation()), encoding="utf-8")
        self.verdict = {"regime": "cold", "pairs_expected": 10, "valid_staged_cohort": True,
                        "confirmed_staged_benefit": True}

    def archive(self, overrides=None, extra=(), include_baseline=False):
        overrides = overrides or {}
        with tarfile.open(self.attempt / "output.tar.gz", "w:gz") as archive:
            for number in range(1, 11):
                case = f"candidate-{number:02}"
                data = overrides.get(case, json.dumps(trace_for_jobs()).encode())
                add_member(archive, f"out/{case}/application.json", data)
                if include_baseline:
                    baseline_case = f"baseline-{number:02}"
                    baseline_data = overrides.get(baseline_case, json.dumps(trace_for_common_jobs()).encode())
                    add_member(archive, f"out/{baseline_case}/application.json", baseline_data)
            for name, data in extra:
                add_member(archive, name, data)

    def test_complete_replay_returns_anonymous_visit_and_interval_statistics(self):
        self.archive(extra=[("out/candidate-warmup/application.json", b"not read"),
                            ("out/candidate-picture/application.json", b"not read")])
        with patch.object(replay, "analyze", return_value=self.verdict) as analyzer:
            result = replay.replay(self.attempt)
        analyzer.assert_called_once_with(self.attempt)
        self.assertTrue(result["all_visits_attributable"])
        self.assertEqual([visit["case"] for visit in result["visits"]],
                         [f"candidate-{number:02}" for number in range(1, 11)])
        self.assertEqual(result["summary"]["median_job_interval_us"][replay.INTERVALS[0]], 10)
        self.assertEqual(result["summary"]["median_visit_interval_sum_us"][replay.INTERVALS[0]], 60)
        self.assertEqual(result["summary"]["intervals_ranked_by_median_visit_sum"][0], replay.INTERVALS[-1])
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(result))

    def test_unattributable_visit_does_not_produce_selective_medians(self):
        trace = trace_for_jobs()
        del trace["events"][5]
        self.archive({"candidate-04": json.dumps(trace).encode()})
        with patch.object(replay, "analyze", return_value=self.verdict):
            result = replay.replay(self.attempt)
        self.assertFalse(result["all_visits_attributable"])
        self.assertFalse(result["visits"][3]["attributable"])
        self.assertIsNone(result["summary"])

    def test_unaccepted_confirmation_never_reads_candidate_trace(self):
        self.archive({"candidate-01": b"not json"})
        for key in ("valid_staged_cohort", "confirmed_staged_benefit"):
            with self.subTest(key=key), patch.object(replay, "analyze", return_value=dict(self.verdict, **{key: False})), \
                    patch.object(replay, "read_trace") as read_trace:
                with self.assertRaisesRegex(ValueError, "confirmation_not_accepted"):
                    replay.replay(self.attempt)
                read_trace.assert_not_called()

    def test_original_analyzer_rejects_an_unreleased_batch(self):
        self.archive()
        (self.attempt / "operations.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "missing_release"):
            replay.replay(self.attempt)

    def test_protocol_must_be_the_frozen_cold_confirmation(self):
        plan = plan_for_confirmation()
        plan["decision"]["median_min_saving_ms"] = 0
        (self.root / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        self.archive()
        with patch.object(replay, "analyze") as analyzer:
            with self.assertRaises(ValueError):
                replay.replay(self.attempt)
            analyzer.assert_not_called()

    def test_duplicate_members_and_traversal_are_rejected_before_analyzer(self):
        for name in ("out/candidate-01/application.json", "out/../private", "/absolute", "out\\private"):
            with self.subTest(name=name):
                self.archive(extra=[(name, b"private")])
                with patch.object(replay, "analyze") as analyzer:
                    with self.assertRaisesRegex(ValueError, "unsafe_archive"):
                        replay.replay(self.attempt)
                    analyzer.assert_not_called()

    def test_duplicate_json_keys_and_bad_json_are_unattributable(self):
        for data in (b'{"events":[],"events":[]}', b'{"events": NaN}', b'not-json'):
            with self.subTest(data=data):
                self.archive({"candidate-01": data})
                with patch.object(replay, "analyze", return_value=self.verdict):
                    result = replay.replay(self.attempt)
                self.assertFalse(result["visits"][0]["attributable"])
                self.assertIsNone(result["summary"])

    def test_paired_common_reports_baseline_minus_candidate_statistics(self):
        baseline = json.dumps(trace_for_common_jobs((30, 40, 50, 60, 70))).encode()
        candidate = json.dumps(trace_for_common_jobs((20, 35, 45, 55, 65), include_cpu=True)).encode()
        overrides = {}
        for number in range(1, 11):
            overrides[f"baseline-{number:02}"] = baseline
            overrides[f"candidate-{number:02}"] = candidate
        self.archive(overrides, include_baseline=True)
        with patch.object(replay, "analyze", return_value=self.verdict):
            result = replay.replay(self.attempt, paired_common=True)
        self.assertTrue(result["all_pairs_attributable"])
        self.assertEqual(result["summary"]["median_pair_interval_diff_us"], {
            "start_to_combined": 60,
            "combined_to_cold_decode_validated": 30,
            "cold_decode_validated_to_derivative_prepared": 30,
            "derivative_prepared_to_render_ready": 30,
            "render_ready_to_finished": 30,
        })
        self.assertEqual(result["summary"]["interval_candidate_wins"]["start_to_combined"], 10)
        self.assertEqual(result["summary"]["median_pair_total_job_diff_us"], 180)

    def test_paired_common_invalid_pair_disables_aggregate(self):
        overrides = {"candidate-03": json.dumps(trace_for_common_jobs()).encode(),
                     "baseline-03": json.dumps(trace_for_common_jobs()).encode()}
        broken = trace_for_common_jobs()
        broken["events"][3]["name"] = "cover_cold_validation_fallback"
        overrides["candidate-03"] = json.dumps(broken).encode()
        self.archive(overrides, include_baseline=True)
        with patch.object(replay, "analyze", return_value=self.verdict):
            result = replay.replay(self.attempt, paired_common=True)
        self.assertFalse(result["all_pairs_attributable"])
        self.assertFalse(result["pairs"][2]["attributable"])
        self.assertIsNone(result["summary"])


if __name__ == "__main__":
    unittest.main()
