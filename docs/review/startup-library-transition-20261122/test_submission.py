import importlib.util
import os
from pathlib import Path
import re
import subprocess
import unittest


HERE = Path(__file__).resolve().parent
PRIOR = HERE.parent / "startup-card-stages-20261122"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generator = load_module("transition_submission_generator", HERE / "submission_generate.py")
prior = load_module("prior_submission_tests", PRIOR / "test_submission.py")


def upstream_source():
    path = os.environ.get("STAGE_SUBMISSION_SOURCE")
    if path:
        return Path(path).read_text()
    return prior.generator.generate((prior.OLD / "submission.sh").read_text())


def install_collector(fixture):
    source = generator.generate(upstream_source())
    source = source.replace("media=/mnt/us", f"media={fixture.media}")
    source = source.replace("private=/var/tmp", f"private={fixture.private}")
    source = source.replace("proc=/proc", f"proc={fixture.proc}")
    fixture.runner.write_text(source)


class OriginalRegressionTests(prior.OriginalRegressionTests):
    def setUp(self):
        super().setUp()
        install_collector(self)

    def call(self, operation, *, success=True):
        output = prior.original.SubmissionCollectorTests.call(self, operation, success=success)
        if operation == "collect" and success:
            lines = output.splitlines(keepends=True)
            self.assertEqual(len(lines), 3)
            self.assertEqual(lines[1:], ["STAGES unavailable\n", "TRANSITION unavailable\n"])
            return lines[0]
        return output


class StageRegressionTests(prior.StageSubmissionTests):
    def setUp(self):
        super().setUp()
        install_collector(self.fixture)

    def collect(self):
        output = super().collect()
        lines = output.splitlines(keepends=True)
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[2], "TRANSITION unavailable\n")
        return "".join(lines[:2])


class TransitionSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = prior.original.SubmissionCollectorTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        install_collector(self.fixture)
        stub = self.fixture.stubs / "getconf"
        stub.write_text("#!/bin/sh\nprintf '100\\n'\n")
        stub.chmod(0o755)

    @staticmethod
    def transition(**changes):
        fields = {
            "sequence": "3", "marker": "5", "submitted_us": "3900000",
            "fence_us": "3250000", "metadata_ready_us": "3150000",
            "metadata_received_us": "3350000", "fonts_ready_us": "3100000",
            "fonts_received_us": "3360000", "fonts_registered_us": "3400000",
            "attach_enter_us": "3450000", "attach_exit_us": "3500000",
            "view_enter_us": "3600000", "view_exit_us": "3700000",
            "scene_requested_us": "3800000", "render_us": "10", "settle_us": "0",
            "write_us": "20", "send_us": "30",
        }
        fields.update(changes)
        return "aidoku: startup_transition " + " ".join(f"{key}={value}" for key, value in fields.items()) + "\n"

    def launch(self, *, initial="3200000", diag=True, transition=True, changes=None):
        fixture = self.fixture
        fixture.call("prepare")
        base_event = fixture.event
        def event():
            result = base_event(stamp=3900000)
            if diag:
                result += f"aidoku: startup_stage_diag main_us=3100000 initial_us={initial} submitted_us=3900000\n"
            if transition:
                result += self.transition(**(changes or {}))
            return result
        fixture.event = event
        fixture.launched()
        for leaf in ("anchor", "pointer-brackets"):
            (fixture.out / leaf).write_text("1500000 1500001 1500020 1500021\n")
        fixture.call("bind")
        fixture.finish()

    def collect(self):
        return self.fixture.call("collect")

    def reject(self):
        self.fixture.call("collect", success=False)
        self.assertFalse((self.fixture.state / ("submission-" + self.fixture.name) / "collected").exists())

    def test_exact_relative_transition_and_legitimate_overlap(self):
        self.launch()
        self.assertTrue(self.collect().endswith(
            "TRANSITION -700000 -650000 -750000 -550000 -800000 -540000 -500000 "
            "-450000 -400000 -300000 -200000 -100000 10 0 20 30\n"))

    def test_fence_can_follow_attachment_and_independent_workers_overlap(self):
        self.launch(changes={"fence_us": "3600000", "metadata_ready_us": "3400000",
                             "metadata_received_us": "3450000", "fonts_ready_us": "3100000"})
        self.assertIn("TRANSITION -700000 -300000 -500000 -450000", self.collect())

    def test_equal_clock_samples_are_reported_as_zero(self):
        names = ("fence_us", "metadata_ready_us", "metadata_received_us", "fonts_ready_us",
                 "fonts_received_us", "fonts_registered_us", "attach_enter_us", "attach_exit_us",
                 "view_enter_us", "view_exit_us", "scene_requested_us")
        self.launch(initial="3900000", changes={name: "3900000" for name in names})
        self.assertIn("TRANSITION " + "0 " * 12 + "10 0 20 30\n", self.collect())

    def test_duration_sum_does_not_invalidate_distinct_clock_samples(self):
        self.launch(changes={"scene_requested_us": "3899990"})
        self.assertTrue(self.collect().endswith(" -10 10 0 20 30\n"))

    def test_large_absolute_clock_keeps_exact_relative_microseconds(self):
        fixture = self.fixture
        fixture.call("prepare")
        base_event = fixture.event
        offset = 900000000000000
        line = self.transition()
        names = ("submitted_us", "fence_us", "metadata_ready_us", "metadata_received_us",
                 "fonts_ready_us", "fonts_received_us", "fonts_registered_us", "attach_enter_us",
                 "attach_exit_us", "view_enter_us", "view_exit_us", "scene_requested_us")
        for name in names:
            line = re.sub(rf"\b{name}=([0-9]+)",
                          lambda match: f"{name}={int(match.group(1)) + offset}", line)
        fixture.event = lambda: (base_event(stamp=3900000 + offset) +
                                 f"aidoku: startup_stage_diag main_us={3100000 + offset} "
                                 f"initial_us={3200000 + offset} submitted_us={3900000 + offset}\n" + line)
        fixture.launched()
        for leaf in ("anchor", "pointer-brackets"):
            (fixture.out / leaf).write_text("1500000 1500001 1500020 1500021\n")
        fixture.call("bind")
        fixture.finish()
        self.assertIn("TRANSITION -700000 -650000 -750000 -550000 ", self.collect())

    def test_absence_is_explicit_and_partial_is_not_zero(self):
        self.launch(initial="none", changes={"fence_us": "none", "metadata_ready_us": "none",
                                            "render_us": "none", "settle_us": "none",
                                            "write_us": "none", "send_us": "none"})
        self.assertTrue(self.collect().endswith(
            "TRANSITION none none none -550000 -800000 -540000 -500000 -450000 -400000 "
            "-300000 -200000 -100000 none none none none\n"))

    def test_missing_transition_and_missing_prior_diagnostic_are_unavailable(self):
        self.launch(transition=False)
        self.assertTrue(self.collect().endswith("TRANSITION unavailable\n"))

    def test_missing_prior_diagnostic_does_not_invent_initial(self):
        self.launch(diag=False)
        self.assertTrue(self.collect().endswith("STAGES unavailable\nTRANSITION unavailable\n"))

    def test_missing_rate_does_not_erase_same_clock_initial(self):
        self.launch()
        path = self.fixture.stubs / "getconf"
        path.write_text("#!/bin/sh\nexit 1\n")
        uname = self.fixture.stubs / "uname"
        uname.write_text("#!/bin/sh\nprintf 'x86_64\\n'\n")
        uname.chmod(0o755)
        result = self.collect()
        self.assertIn("STAGES unavailable\nTRANSITION -700000 -650000 ", result)

    def test_wrong_identity_or_send_timestamp_is_rejected(self):
        self.launch()
        original_log = self.fixture.log.read_text()
        for field, replacement in (("sequence=3", "sequence=4"), ("marker=5", "marker=6"),
                                   ("submitted_us=3900000", "submitted_us=3900001")):
            transition_line = self.transition()
            self.fixture.log.write_text(original_log.replace(transition_line, transition_line.replace(field, replacement)))
            self.reject()

    def test_duplicate_and_invalid_transition_are_rejected(self):
        self.launch()
        original_log = self.fixture.log.read_text()
        line = self.transition()
        for replacement in (line + line, line.replace("fence_us=3250000", "fence_us=03250000"),
                            line.replace("render_us=10", "render_us=none"),
                            line.replace("send_us=30", "send_us=-1"),
                            line.replace("render_us=10", "render_us=1000000000000")):
            self.fixture.log.write_text(original_log.replace(line, replacement))
            self.reject()

    def test_fence_requires_initial_and_order(self):
        self.launch(initial="none")
        self.reject()

    def test_impossible_partial_orders_and_future_points_are_rejected(self):
        self.launch()
        original_log = self.fixture.log.read_text()
        line = self.transition()
        cases = ({"metadata_ready_us": "3400000", "metadata_received_us": "3300000"},
                 {"fonts_ready_us": "3500000", "fonts_received_us": "none"},
                 {"attach_enter_us": "none", "attach_exit_us": "3650000"},
                 {"fence_us": "3199999"}, {"metadata_ready_us": "3099999"},
                 {"scene_requested_us": "3900001"})
        for changes in cases:
            self.fixture.log.write_text(original_log.replace(line, self.transition(**changes)))
            self.reject()

    def test_bad_line_is_not_hidden_by_unavailable_prior_diagnostic(self):
        self.launch(diag=False, changes={"marker": "6"})
        self.reject()

    def test_transition_after_worker_exit_is_rejected(self):
        self.launch(transition=False)
        with self.fixture.log.open("a") as stream:
            stream.write(self.transition())
        self.reject()

    def test_generator_refuses_drift_and_overwrite(self):
        with self.assertRaises(ValueError):
            generator.generate("unrelated source")
        source = self.fixture.root / "source.sh"
        source.write_text(upstream_source())
        result = subprocess.run(["python3", str(HERE / "submission_generate.py"), str(source), str(source)],
                                capture_output=True)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
