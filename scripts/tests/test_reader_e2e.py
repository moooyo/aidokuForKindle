"""Causal counterexamples for the Reader timing analyzer.

Run only on the designated remote verification worker. These tests use no
application, framebuffer, network service, Kindle or third-party dependency.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "benchmark-reader-e2e.py"
SPECIFICATION = importlib.util.spec_from_file_location("reader_e2e",SCRIPT)
assert SPECIFICATION is not None and SPECIFICATION.loader is not None
HARNESS = importlib.util.module_from_spec(SPECIFICATION)
SPECIFICATION.loader.exec_module(HARNESS)


def event(name: str, at_us: int, request: int | None = None, action: int | None = None, **extra: object) -> dict:
    value = {"name":name,"at_us":at_us}
    if request is not None:
        value.update(request_id=request,reader_generation=request,lifecycle_generation=0)
        if action is not None:
            value["action_id"] = action
    value.update(extra)
    return value


def request_events(request: int, start: int, native: bool, action: int | None = None, frame: int | None = None) -> list[dict]:
    result = [
        event("request_created",start,request,action),
        event("request_queued",start+10,request,action),
        event("worker_started",start+20,request,action),
        event("fetch_finished",start+30,request,action,duration_us=10),
        event("render_finished",start+40,request,action,duration_us=10,cache_state="cold"),
        event("prepare_finished",start+50,request,action,duration_us=10),
        event("ui_image_ready",start+60,request,action),
    ]
    if native:
        # A constant driver marker is legitimate for the unoptimized baseline;
        # request and frame identity provide the causal observation contract.
        result.extend([
            event("framebuffer_written",start+70,request,action,frame_sequence=frame or request,full=True),
            event("send_accepted",start+80,request,action,frame_sequence=frame or request,marker=1),
        ])
    return result


def document(native: bool = False, action: bool = False) -> dict:
    events = [event("process_entry",0)] + request_events(1,100,native)
    if action:
        events.append(event("action_received",200,action_id=1,action_kind="next",boundary="app_intent_received",input_origin="scripted_intent"))
        events.extend(request_events(2,240,native,action=1))
    events.append(event("process_finished",500))
    return {"schema_version":1,"clock":"monotonic_rust_main","process_id":1234,"finished":True,"success":True,"dropped_events":0,"event_limit":1000,"events":events}


def expectation(native: bool = False, **extra: object) -> dict:
    value = {"scope":"native" if native else "desktop"}
    value.update(extra)
    return value


def find(trace: dict, name: str, request: int = 1) -> dict:
    return next(value for value in trace["events"] if value["name"] == name and value.get("request_id") == request)


class TraceCausalityTests(unittest.TestCase):
    def rejected(self, trace: dict, expected: dict | None = None) -> None:
        with self.assertRaises(HARNESS.TraceError):
            HARNESS.validate_trace(trace,expected or expectation())

    def test_desktop_acceptance_does_not_invent_native_metrics(self) -> None:
        report = HARNESS.validate_trace(document(),expectation())
        self.assertEqual(report["samples"]["rust_main_to_first_ui_ms"],[0.160])
        self.assertFalse(any("send_accepted" in metric or "framebuffer" in metric for metric in report["samples"]))

    def test_baseline_may_repeat_marker_but_not_frame_identity(self) -> None:
        report = HARNESS.validate_trace(document(native=True,action=True),expectation(True,required_successful_actions={"next":1}))
        self.assertEqual(report["native_request_count"],2)
        self.assertEqual(report["successful_actions"],{"next":1})

    def test_action_boundary_includes_pre_request_chapter_wait(self) -> None:
        report = HARNESS.validate_trace(document(action=True),expectation(required_successful_actions={"next":1}))
        self.assertEqual(report["samples"]["action_to_ui_ms"],[0.100])
        self.assertEqual(report["samples"]["request_to_ui_ms"],[0.060,0.060])

    def test_unassociated_first_image_after_reader_open_is_not_startup(self) -> None:
        trace = document()
        trace["events"].insert(1,event("action_received",50,action_id=1,action_kind="open",boundary="app_intent_received",input_origin="scripted_intent"))
        trace["events"].insert(2,event("action_cancelled",80,action_id=1,reason="restored"))
        self.rejected(trace,expectation(allowed_action_outcomes=["action_cancelled"]))

    def test_reader_open_is_eligible_and_uses_its_own_action_boundary(self) -> None:
        trace = document()
        for value in trace["events"]:
            if "request_id" in value:
                value["action_id"] = 1
        trace["events"].insert(1,event("action_received",50,action_id=1,action_kind="open",boundary="app_intent_received",input_origin="scripted_intent"))
        report = HARNESS.validate_trace(trace,expectation(require_startup=False))
        self.assertEqual(report["eligible_action_count"],1)
        self.assertEqual(report["successful_actions"],{"open":1})
        self.assertEqual(report["samples"]["action_open_to_ui_ms"],[0.110])
        self.assertNotIn("rust_main_to_first_ui_ms",report["samples"])

    def test_ordinary_navigation_other_does_not_spoil_startup(self) -> None:
        trace = document()
        trace["events"].insert(1,event("action_received",50,action_id=1,action_kind="other",boundary="app_intent_received",input_origin="scripted_intent"))
        report = HARNESS.validate_trace(trace,expectation())
        self.assertEqual(report["eligible_action_count"],0)
        self.assertEqual(report["samples"]["rust_main_to_first_ui_ms"],[0.160])

    def test_same_timestamp_open_after_first_ui_does_not_rewrite_its_origin(self) -> None:
        trace = document()
        trace["events"].insert(-1,event("action_received",160,action_id=1,action_kind="open",boundary="app_intent_received",input_origin="scripted_intent"))
        trace["events"].insert(-1,event("action_cancelled",170,action_id=1,reason="restored"))
        report = HARNESS.validate_trace(trace,expectation(allowed_action_outcomes=["action_cancelled"]))
        self.assertEqual(report["samples"]["rust_main_to_first_ui_ms"],[0.160])

    def test_lost_flush_and_dropped_events_are_rejected(self) -> None:
        for field,value in (("finished",False),("dropped_events",1),("clock","wall_clock"),("event_limit",2)):
            with self.subTest(field=field):
                trace = document()
                trace[field] = value
                self.rejected(trace)

    def test_missing_native_send_is_not_ui_success(self) -> None:
        trace = document(native=True)
        trace["events"].remove(find(trace,"send_accepted"))
        self.rejected(trace,expectation(True))

    def test_native_output_is_invalid_in_a_desktop_trace(self) -> None:
        self.rejected(document(native=True))

    def test_late_result_after_cancellation_is_rejected(self) -> None:
        trace = document()
        trace["events"].insert(3,event("request_cancelled",115,1,reason="superseded"))
        self.rejected(trace,expectation(allowed_outcomes=["request_cancelled"]))

    def test_reader_and_lifecycle_generation_must_both_match(self) -> None:
        for field in ("reader_generation","lifecycle_generation"):
            with self.subTest(field=field):
                trace = document(native=True)
                find(trace,"send_accepted")[field] += 1
                self.rejected(trace,expectation(True))

    def test_frame_and_request_identity_are_both_checked(self) -> None:
        trace = document(native=True,action=True)
        find(trace,"send_accepted",2)["frame_sequence"] = 1
        self.rejected(trace,expectation(True))
        trace = document(native=True,action=True)
        find(trace,"framebuffer_written",2)["frame_sequence"] = 1
        find(trace,"send_accepted",2)["frame_sequence"] = 1
        self.rejected(trace,expectation(True))

    def test_send_requires_a_real_write_with_nonzero_abi_marker(self) -> None:
        trace = document(native=True)
        trace["events"].remove(find(trace,"framebuffer_written"))
        self.rejected(trace,expectation(True))
        trace = document(native=True)
        find(trace,"send_accepted")["marker"] = 0
        self.rejected(trace,expectation(True))

    def test_a_missing_worker_cannot_become_an_accepted_image(self) -> None:
        trace = document()
        trace["events"] = [value for value in trace["events"] if value["name"] not in {"worker_started","fetch_finished","render_finished","prepare_finished"}]
        self.rejected(trace)

    def test_unknown_action_and_ambiguous_input_boundary_are_rejected(self) -> None:
        trace = document(action=True)
        next(value for value in trace["events"] if value["name"] == "action_received")["boundary"] = "script_scheduled"
        self.rejected(trace)
        trace = document(action=True)
        trace["events"] = [value for value in trace["events"] if value["name"] != "action_received"]
        self.rejected(trace)

    def test_action_without_a_request_cannot_disappear_from_the_denominator(self) -> None:
        trace = document()
        trace["events"].insert(-1,event("action_received",300,action_id=1,action_kind="next",boundary="app_intent_received",input_origin="scripted_intent"))
        self.rejected(trace)
        trace["events"].insert(-1,event("action_failed",400,action_id=1,reason="failed"))
        self.rejected(trace)
        report = HARNESS.validate_trace(trace,expectation(allowed_action_outcomes=["action_failed"],required_action_outcomes={"action_failed":1}))
        self.assertEqual(report["eligible_action_count"],1)
        self.assertEqual(report["action_outcomes"],{"action_failed":1})
        self.assertNotIn("action_to_ui_ms",report["samples"])

    def test_cancelled_deferred_action_is_an_explicit_regression_outcome(self) -> None:
        trace = document()
        trace["events"].insert(-1,event("action_received",300,action_id=1,action_kind="next_chapter",boundary="app_intent_received",input_origin="scripted_intent"))
        trace["events"].insert(-1,event("action_cancelled",400,action_id=1,reason="shutdown"))
        report = HARNESS.validate_trace(trace,expectation(allowed_action_outcomes=["action_cancelled"],required_action_outcomes={"action_cancelled":1}))
        self.assertEqual(report["action_outcomes"],{"action_cancelled":1})

    def test_non_reader_settings_do_not_become_a_page_turn_sample(self) -> None:
        trace = document()
        trace["events"].insert(-1,event("action_received",300,action_id=1,action_kind="settings",boundary="app_intent_received",input_origin="scripted_intent"))
        report = HARNESS.validate_trace(trace,expectation())
        self.assertEqual(report["eligible_action_count"],0)
        self.assertEqual(report["actions"][0]["outcome"],"not_measured")

    def test_repeated_spread_stage_keeps_its_own_duration_boundary(self) -> None:
        trace = document()
        trace["events"].insert(7,event("render_finished",155,1,duration_us=3,cache_state="disk"))
        report = HARNESS.validate_trace(trace,expectation())
        self.assertEqual(report["samples"]["render_stage_total_ms"],[0.013])

    def test_required_cache_state_uses_the_completed_render_result(self) -> None:
        self.rejected(document(),expectation(required_cache_states={"memory":1}))
        result = HARNESS.validate_trace(document(),expectation(required_cache_states={"cold":1}))
        self.assertEqual(result["cache_state_counts"],{"cold":1})
        trace = document()
        find(trace,"render_finished")["cache_state"] = "inconsistent"
        self.rejected(trace)

    def test_failed_or_missing_render_result_cannot_satisfy_cache_gate(self) -> None:
        trace = document()
        trace["events"].insert(-1,event("request_created",300,2))
        trace["events"].insert(-1,event("worker_started",310,2))
        trace["events"].insert(-1,event("render_finished",340,2,duration_us=20))
        trace["events"].insert(-1,event("request_failed",350,2,reason="failed"))
        report = HARNESS.validate_trace(trace,expectation(allowed_outcomes=["request_failed"],required_cache_states={"cold":1}))
        self.assertEqual(report["cache_state_counts"],{"cold":1})
        self.rejected(trace,expectation(allowed_outcomes=["request_failed"],required_cache_states={"cold":2}))
        trace = document()
        del find(trace,"render_finished")["cache_state"]
        self.rejected(trace)

    def test_time_cannot_move_backward_or_stage_predate_worker(self) -> None:
        trace = document()
        find(trace,"ui_image_ready")["at_us"] = 119
        self.rejected(trace)
        trace = document()
        find(trace,"render_finished")["duration_us"] = 1000
        self.rejected(trace)

    def test_native_unavailable_is_a_terminal_output_outcome(self) -> None:
        trace = document(native=True)
        trace["events"].insert(8,event("native_frame_unavailable",165,1,reason="no_write",frame_sequence=0))
        self.rejected(trace,expectation(True,allowed_outcomes=["native_frame_unavailable"]))

    def test_old_generation_cannot_complete_after_a_new_request_exists(self) -> None:
        trace = document()
        trace["events"].insert(5,event("request_created",135,2))
        self.rejected(trace,expectation(allowed_outcomes=["request_cancelled"]))

    def test_work_may_finish_after_cancellation_but_not_after_ui_acceptance(self) -> None:
        trace = document()
        trace["events"].insert(-1,event("request_created",300,2))
        trace["events"].insert(-1,event("worker_started",310,2))
        trace["events"].insert(-1,event("request_cancelled",320,2,reason="superseded"))
        trace["events"].insert(-1,event("render_finished",340,2,duration_us=20,cache_state="cold"))
        report = HARNESS.validate_trace(trace,expectation(allowed_outcomes=["request_cancelled"]))
        self.assertEqual(report["outcomes"],{"complete":1,"request_cancelled":1})
        trace = document()
        trace["events"].insert(-1,event("render_finished",180,1,duration_us=20,cache_state="cold"))
        self.rejected(trace)

    def test_retired_queued_worker_may_start_as_diagnostics_only(self) -> None:
        trace = document()
        for value in (
            event("request_created",200,2),
            event("request_queued",210,2),
            event("request_invalidated",220,2,reason="lifecycle"),
            event("worker_started",230,2),
            event("render_finished",240,2,duration_us=10,cache_state="cold"),
            event("worker_finished",250,2),
        ):
            trace["events"].insert(-1,value)
        report = HARNESS.validate_trace(trace,expectation(allowed_outcomes=["request_invalidated"]))
        self.assertEqual(report["outcomes"],{"complete":1,"request_invalidated":1})
        self.assertEqual(report["samples"]["request_to_ui_ms"],[0.060])

    def test_failure_after_ui_acceptance_is_a_contradictory_outcome(self) -> None:
        trace = document()
        trace["events"].insert(-1,event("request_failed",300,1,reason="failed"))
        self.rejected(trace,expectation(allowed_outcomes=["request_failed"]))

    def test_repeated_boundary_and_request_ids_are_rejected(self) -> None:
        trace = document()
        trace["events"].insert(-1,copy.deepcopy(find(trace,"ui_image_ready")))
        self.rejected(trace)
        trace = document(action=True)
        find(trace,"request_created",2)["request_id"] = 1
        self.rejected(trace)

    def test_expected_cancellation_is_retained_and_not_a_latency(self) -> None:
        trace = document()
        trace["events"].insert(-1,event("request_created",300,2))
        trace["events"].insert(-1,event("request_cancelled",310,2,reason="superseded"))
        report = HARNESS.validate_trace(trace,expectation(allowed_outcomes=["request_cancelled"],required_outcomes={"request_cancelled":1}))
        self.assertEqual(report["outcomes"],{"complete":1,"request_cancelled":1})
        self.assertEqual(report["samples"]["request_to_ui_ms"],[0.060])

    def test_completed_frame_can_be_retired_without_erasing_its_measurement(self) -> None:
        trace = document(native=True)
        trace["events"].insert(-1,event("request_invalidated",400,1,reason="lifecycle"))
        report = HARNESS.validate_trace(trace,expectation(True))
        self.assertEqual(report["outcomes"],{"complete":1})
        self.assertEqual(report["requests"][0]["retirement"],"request_invalidated")

    def test_rejected_native_output_never_becomes_a_success_sample(self) -> None:
        trace = document(native=True)
        trace["events"].remove(find(trace,"send_accepted"))
        trace["events"].insert(-1,event("native_frame_unavailable",180,1,reason="send_rejected",frame_sequence=1))
        self.rejected(trace,expectation(True))
        result = HARNESS.validate_trace(trace,expectation(True,require_startup=False,minimum_native_requests=0,allowed_outcomes=["native_frame_unavailable"],required_outcomes={"native_frame_unavailable":1}))
        self.assertEqual(result["samples"],{})

    def test_truncated_json_is_retained_as_a_failed_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/"truncated.json"
            path.write_text('{"schema_version":1,"events":[',encoding="utf-8")
            result = HARNESS.analyze_trace(path,expectation())
            self.assertFalse(result["valid"])
            self.assertEqual(result["error"]["type"],"JSONDecodeError")
            self.assertIn("trace_sha256",result)


class CohortTests(unittest.TestCase):
    def trial(self, label: str, iteration: int, valid: bool = True) -> dict:
        analysis = HARNESS.validate_trace(document(),expectation()) if valid else {"valid":False,"error":{"type":"Timeout"}}
        return {"implementation":label,"iteration":iteration,"workload":"startup","phase":"measure","analysis":analysis}

    def test_missing_or_failed_trials_disable_comparison_ratios(self) -> None:
        result = HARNESS.summarize_runs([self.trial("baseline",1),self.trial("candidate",1,False)],1)
        self.assertFalse(result["all_trials_valid"])
        self.assertEqual(result["comparisons"]["startup"]["ratios"],{})
        self.assertEqual(result["workloads"]["startup"]["candidate"]["failed_processes"],1)
        result = HARNESS.summarize_runs([self.trial("baseline",1),self.trial("candidate",1)],2)
        self.assertFalse(result["paired_samples_complete"])

    def test_percentile_exposes_small_samples_and_process_count(self) -> None:
        small = HARNESS.distribution([1,2,3],1)
        self.assertEqual(small["p95_ms"],3)
        self.assertEqual(small["independent_processes"],1)
        self.assertTrue(small["small_sample"])
        larger = HARNESS.distribution(list(range(1,22)),21)
        self.assertEqual(larger["p95_ms"],20)
        self.assertFalse(larger["small_sample"])

    def test_qualification_runs_are_not_timing_samples(self) -> None:
        warmup = self.trial("candidate",1)
        warmup["phase"] = "qualification"
        result = HARNESS.summarize_runs([self.trial("baseline",1),self.trial("candidate",1),warmup],1)
        self.assertEqual(result["workloads"]["startup"]["candidate"]["requested_processes"],1)

    def test_iteration_scope_and_expectation_mismatches_disable_ratios(self) -> None:
        baseline,candidate = self.trial("baseline",1),self.trial("candidate",2)
        self.assertFalse(HARNESS.summarize_runs([baseline,candidate])["paired_samples_complete"])
        candidate = self.trial("candidate",1)
        candidate["analysis"]["scope"] = "native"
        self.assertFalse(HARNESS.summarize_runs([baseline,candidate])["paired_samples_complete"])
        candidate = self.trial("candidate",1)
        candidate["expectation"] = {"scope":"desktop","minimum_ui_requests":3}
        self.assertFalse(HARNESS.summarize_runs([baseline,candidate])["paired_samples_complete"])

    def test_per_workload_process_count_does_not_inflate_action_samples(self) -> None:
        result = HARNESS.summarize_runs([self.trial("baseline",1),self.trial("candidate",1)],{"startup":1})
        self.assertTrue(result["paired_samples_complete"])
        result = HARNESS.summarize_runs([self.trial("baseline",1),self.trial("candidate",1)],{"startup":21})
        self.assertFalse(result["paired_samples_complete"])

    def test_missing_declared_workload_invalidates_other_complete_pairs(self) -> None:
        trials = [self.trial("baseline",1),self.trial("candidate",1)]
        result = HARNESS.summarize_runs(trials,{"startup":1,"cached-turns":1})
        self.assertFalse(result["paired_samples_complete"])
        self.assertEqual(result["missing_workloads"],["cached-turns"])
        self.assertFalse(result["comparisons"]["startup"]["qualified"])
        self.assertEqual(result["comparisons"]["startup"]["ratios"],{})

    def test_unexpected_workload_withholds_all_requested_pair_ratios(self) -> None:
        trials = [self.trial("baseline",1),self.trial("candidate",1)]
        trials += [dict(self.trial(label,1),workload="unexpected") for label in ("baseline","candidate")]
        result = HARNESS.summarize_runs(trials,{"startup":1})
        self.assertFalse(result["paired_samples_complete"])
        self.assertEqual(result["unexpected_workloads"],["unexpected"])
        self.assertTrue(all(not comparison["qualified"] and comparison["ratios"] == {} for comparison in result["comparisons"].values()))

    def test_baseline_review_binds_the_actual_observation_patch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory/"observation.patch").write_text("instrumentation-only patch\n",encoding="utf-8")
            (directory/"shared-before.rs").write_text("same observation source\n",encoding="utf-8")
            (directory/"shared-after.rs").write_text("same observation source\n",encoding="utf-8")
            contract = {"control":{"baseline_commit":HARNESS.BASELINE_COMMIT,"baseline_observation_patch":"observation.patch","baseline_review_receipt":"review.json","shared_observation_files":[{"baseline":"shared-before.rs","candidate":"shared-after.rs"}]}}
            review = {"schema_version":1,"baseline_commit":HARNESS.BASELINE_COMMIT,"baseline_observation_patch_sha256":HARNESS.sha256(directory/"observation.patch"),"review_passed":True,"checks":{"original_algorithms_preserved":True,"same_timing_boundaries":True,"candidate_optimizations_excluded":True}}
            HARNESS.save_json(directory/"review.json",review)
            self.assertTrue(HARNESS.controlled_inputs(contract,directory)["verified"])
            (directory/"observation.patch").write_text("changed patch\n",encoding="utf-8")
            with self.assertRaises(HARNESS.TraceError):
                HARNESS.controlled_inputs(contract,directory)

    def test_same_trace_cannot_be_relabelled_as_independent_processes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            HARNESS.save_json(directory/"trace.json",document())
            records = [{"implementation":label,"workload":"startup","iteration":1,"trace":"trace.json","expectation":expectation(),"process_exit_code":0} for label in ("baseline","candidate")]
            HARNESS.save_json(directory/"runs.json",{"schema_version":1,"runs":records})
            result = HARNESS.analyze_manifest(directory/"runs.json")
            self.assertFalse(result["summary"]["all_trials_valid"])
            self.assertFalse(result["comparison_qualified"])

    def test_analyze_requires_explicit_successful_process_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            HARNESS.save_json(directory/"trace.json",document())
            HARNESS.save_json(directory/"runs.json",{"schema_version":1,"runs":[{"implementation":"candidate","workload":"startup","iteration":1,"trace":"trace.json","expectation":expectation()}]})
            report = HARNESS.analyze_manifest(directory/"runs.json")
            self.assertFalse(report["summary"]["all_trials_valid"])
            self.assertFalse(report["comparison_qualified"])

    def test_fixture_inventory_uses_bytes_and_relative_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory/"source").mkdir()
            (directory/"source"/"page.raw").write_bytes(b"first-image")
            declarations = [{"name":"raw_pages","path":"source"}]
            before = HARNESS.inventory(declarations,directory)
            (directory/"source"/"page.raw").write_bytes(b"other-image")
            after = HARNESS.inventory(declarations,directory)
            self.assertNotEqual(before["raw_pages"]["sha256"],after["raw_pages"]["sha256"])


class RetainedContractTests(unittest.TestCase):
    def prepared_plan(self, directory: Path) -> dict:
        (directory/"observation.patch").write_text("observation-only patch\n",encoding="utf-8")
        for name in ("shared-before.rs","shared-after.rs"):
            (directory/name).write_text("same observation source\n",encoding="utf-8")
        for name in ("baseline","candidate"):
            (directory/name).write_bytes(name.encode())
        (directory/"fixture").mkdir()
        (directory/"fixture"/"state").write_bytes(b"frozen fixture")
        HARNESS.save_json(directory/"review.json",{"schema_version":1,"baseline_commit":HARNESS.BASELINE_COMMIT,"baseline_observation_patch_sha256":HARNESS.sha256(directory/"observation.patch"),"review_passed":True,"checks":{"original_algorithms_preserved":True,"same_timing_boundaries":True,"candidate_optimizations_excluded":True}})
        return {"schema_version":1,"scope":"desktop","samples":1,
                "control":{"baseline_commit":HARNESS.BASELINE_COMMIT,"baseline_observation_patch":"observation.patch","baseline_review_receipt":"review.json","shared_observation_files":[{"baseline":"shared-before.rs","candidate":"shared-after.rs"}]},
                "implementations":{label:{"binary":label} for label in ("baseline","candidate")},
                "workloads":[{"id":"startup","command":["{binary}","--offline","--data-dir",str(directory/"fixture"),"--performance-trace","{trace}"],"fixtures":[{"name":"state","path":"fixture"}],"expectation":expectation()}]}

    def collected_report(self, directory: Path, mutate_binary: bool = False) -> tuple[dict,Path]:
        plan = self.prepared_plan(directory)
        HARNESS.save_json(directory/"plan.json",plan)
        next_pid = 2000

        def fake_process(command: list[str], output: Path, *_: object) -> dict:
            nonlocal next_pid
            next_pid += 1
            output.mkdir(parents=True)
            trace = document()
            trace["process_id"] = next_pid
            HARNESS.save_json(Path(command[command.index("--performance-trace")+1]),trace)
            if mutate_binary and Path(command[0]).name == "candidate":
                Path(command[0]).write_bytes(b"changed during measurement")
            return {"exit_code":0,"supervisor_process_id":next_pid,"succeeded":True}

        with mock.patch.object(HARNESS.sys,"platform","linux"),mock.patch.object(HARNESS,"run_process",side_effect=fake_process):
            report = HARNESS.run_plan(directory/"plan.json",directory/"output")
        return report,directory/"output"/"runs.json"

    def test_run_manifest_can_be_reverified_without_losing_its_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report,path = self.collected_report(Path(temporary))
            self.assertTrue(report["comparison_qualified"])
            analyzed = HARNESS.analyze_manifest(path)
            self.assertTrue(analyzed["comparison_qualified"])
            self.assertTrue(analyzed["summary"]["comparisons"]["startup"]["qualified"])

    def test_failed_qualification_withholds_all_ratios(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _,path = self.collected_report(Path(temporary))
            manifest = HARNESS.read_json(path)
            failed = dict(manifest["runs"][0],phase="qualification",trace="missing-qualification.json",process_exit_code=1)
            manifest["runs"].append(failed)
            HARNESS.save_json(path,manifest)
            analyzed = HARNESS.analyze_manifest(path)
            self.assertFalse(analyzed["qualification_trials_valid"])
            self.assertFalse(analyzed["comparison_qualified"])
            self.assertFalse(analyzed["summary"]["comparisons"]["startup"]["qualified"])
            self.assertEqual(analyzed["summary"]["comparisons"]["startup"]["ratios"],{})

    def test_retained_binary_change_cannot_be_erased_by_reanalysis(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _,path = self.collected_report(Path(temporary))
            manifest = HARNESS.read_json(path)
            manifest["binaries_unchanged"]["candidate"] = False
            HARNESS.save_json(path,manifest)
            analyzed = HARNESS.analyze_manifest(path)
            self.assertFalse(analyzed["binary_identities_valid"])
            self.assertFalse(analyzed["comparison_qualified"])
            self.assertEqual(analyzed["summary"]["comparisons"]["startup"]["ratios"],{})

    def test_binary_change_during_run_withholds_inner_and_outer_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report,path = self.collected_report(Path(temporary),mutate_binary=True)
            self.assertFalse(report["comparison_qualified"])
            self.assertFalse(report["summary"]["comparisons"]["startup"]["qualified"])
            self.assertEqual(report["summary"]["comparisons"]["startup"]["ratios"],{})
            analyzed = HARNESS.analyze_manifest(path)
            self.assertFalse(analyzed["comparison_qualified"])

    def test_partial_retained_plan_cannot_be_requalified_from_one_complete_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _,path = self.collected_report(Path(temporary))
            manifest = HARNESS.read_json(path)
            manifest["expected_pairs"]["cached-turns"] = 1
            HARNESS.save_json(path,manifest)
            analyzed = HARNESS.analyze_manifest(path)
            self.assertTrue(analyzed["control"]["verified"])
            self.assertTrue(analyzed["binary_identities_valid"])
            self.assertFalse(analyzed["comparison_qualified"])
            self.assertEqual(analyzed["summary"]["missing_workloads"],["cached-turns"])
            self.assertEqual(analyzed["summary"]["comparisons"]["startup"]["ratios"],{})

    def test_unverified_process_group_aborts_with_the_complete_requested_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan = self.prepared_plan(directory)
            plan["workloads"].append(dict(plan["workloads"][0],id="future-workload"))
            HARNESS.save_json(directory/"plan.json",plan)
            with mock.patch.object(HARNESS.sys,"platform","linux"),mock.patch.object(HARNESS,"run_process",side_effect=HARNESS.ProcessGroupCleanupError("owned group still running")) as process:
                with self.assertRaises(HARNESS.ProcessGroupCleanupError):
                    HARNESS.run_plan(directory/"plan.json",directory/"output")
            self.assertEqual(process.call_count,1)
            retained = HARNESS.read_json(directory/"output"/"runs.json")
            self.assertEqual(retained["expected_pairs"],{"startup":1,"future-workload":1})
            self.assertEqual(len(retained["runs"]),1)
            self.assertTrue(retained["runs"][0]["fatal_process_group_cleanup_failure"])


@unittest.skipUnless(sys.platform.startswith("linux"),"Owned process-group probes require the designated Linux worker")
class OwnedProcessGroupTests(unittest.TestCase):
    def test_timeout_cleans_a_child_after_its_wrapper_has_exited(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            child_identity = directory/"child.json"
            child_code = "\n".join([
                "import json,os,signal,sys,time",
                "from pathlib import Path",
                "signal.signal(signal.SIGTERM,signal.SIG_IGN)",
                "tail=Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()",
                "Path(sys.argv[1]).write_text(json.dumps({'pid':os.getpid(),'group':os.getpgrp(),'session':os.getsid(0),'start_time_ticks':int(tail[19])}))",
                "while True: time.sleep(1)",
            ])
            wrapper_code = "\n".join([
                "import signal,subprocess,sys,time",
                "signal.signal(signal.SIGTERM,lambda *_:sys.exit(0))",
                "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]])",
                "while True: time.sleep(1)",
            ])
            record = None
            try:
                record = HARNESS.run_process([sys.executable,"-c",wrapper_code,child_code,str(child_identity)],directory/"process",2.0,directory,os.environ.copy())
                self.assertTrue(child_identity.is_file(),"The child did not initialize before the timeout")
                child = HARNESS.read_json(child_identity)
                self.assertEqual(child["group"],record["supervisor_process_id"])
                self.assertEqual(child["session"],record["supervisor_process_id"])
                self.assertEqual(record["exit_code"],0,"The wrapper must exit on TERM before its ignoring child")
                self.assertTrue(record["timed_out"])
                self.assertFalse(record["succeeded"])
                self.assertTrue(record["process_group_cleanup"]["kill_sent"])
                self.assertTrue(record["process_group_cleanup"]["verified_no_running_members"])
                self.assertFalse(HARNESS.running_group_members(HARNESS.owned_process_group_members(child["group"])))
            finally:
                # Bound teardown even when an intentionally regressed supervisor
                # fails an assertion. Signal only the exact child-owned session.
                if child_identity.is_file():
                    child = HARNESS.read_json(child_identity)
                    path = Path(f"/proc/{child['pid']}/stat")
                    try:
                        tail = path.read_text().rsplit(")",1)[1].split()
                    except FileNotFoundError:
                        tail = []
                    if tail and int(tail[19]) == child["start_time_ticks"] and int(tail[2]) == child["group"] and int(tail[3]) == child["session"] and child["group"] != os.getpgrp():
                        try:
                            os.killpg(child["group"],signal.SIGKILL)
                        except ProcessLookupError:
                            pass


if __name__ == "__main__":
    unittest.main()
