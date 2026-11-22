import importlib.util
from pathlib import Path
import subprocess
import unittest


HERE = Path(__file__).resolve().parent
OLD = HERE.parent / "startup-jpeg-native-20261122"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generator = load_module("stage_submission_generator", HERE / "submission_generate.py")
original = load_module("original_submission_tests", OLD / "test_submission.py")


class OriginalRegressionTests(original.SubmissionCollectorTests):
    def setUp(self):
        super().setUp()
        source = generator.generate((OLD / "submission.sh").read_text())
        source = source.replace("media=/mnt/us", f"media={self.media}")
        source = source.replace("private=/var/tmp", f"private={self.private}")
        source = source.replace("proc=/proc", f"proc={self.proc}")
        self.runner.write_text(source)

    def call(self, operation, *, success=True):
        output = super().call(operation, success=success)
        if operation == "collect" and success:
            lines = output.splitlines(keepends=True)
            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[1], "STAGES unavailable\n")
            return lines[0]
        return output


class StageSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = original.SubmissionCollectorTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        fixture = self.fixture
        source = generator.generate((OLD / "submission.sh").read_text())
        source = source.replace("media=/mnt/us", f"media={fixture.media}")
        source = source.replace("private=/var/tmp", f"private={fixture.private}")
        source = source.replace("proc=/proc", f"proc={fixture.proc}")
        fixture.runner.write_text(source)
        self.stub("getconf", "printf '100\\n'")

    def stub(self, name, body):
        path = self.fixture.stubs / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)

    def launch(self, initial="3750000", main="3500000", submitted="3900000", diagnostic=True):
        fixture = self.fixture
        fixture.call("prepare")
        base_event = fixture.event
        def event():
            line = base_event(stamp=3900000)
            if diagnostic:
                line += (f"aidoku: startup_stage_diag main_us={main} initial_us={initial} "
                         f"submitted_us={submitted}\n")
            return line
        fixture.event = event
        fixture.launched()
        self.release("1500000 1500001 1500020 1500021")
        fixture.call("bind")
        fixture.finish()

    def release(self, values):
        for leaf in ("anchor", "pointer-brackets"):
            (self.fixture.out / leaf).write_text(values + "\n")

    def collect(self):
        return self.fixture.call("collect")

    def reject(self):
        self.fixture.call("collect", success=False)
        self.assertFalse((self.fixture.state / ("submission-" + self.fixture.name) / "collected").exists())

    def test_release_relative_stages_preserve_tick_and_anchor_uncertainty(self):
        self.launch()
        self.assertEqual(self.collect(),
                         "SUBMISSION 2399979 2399980 3 5 large nonempty nonempty\n"
                         "STAGES 100 499979 509980 1569979 1579980 1999979 1999980 "
                         "2249979 2249980 2399979 2399980\n")

    def test_missing_initial_is_explicit(self):
        self.launch(initial="none")
        self.assertIn(" none none 2399979 2399980\n", self.collect())

    def test_absent_diagnostic_is_explicitly_unavailable(self):
        self.launch(diagnostic=False)
        self.assertEqual(self.collect(),
                         "SUBMISSION 2399979 2399980 3 5 large nonempty nonempty\n"
                         "STAGES unavailable\n")

    def test_diagnostic_after_exit_is_rejected(self):
        self.launch(diagnostic=False)
        with self.fixture.log.open("a") as stream:
            stream.write("aidoku: startup_stage_diag main_us=3500000 initial_us=none submitted_us=3900000\n")
        self.reject()

    def test_duplicate_diagnostic_is_rejected_before_public_output(self):
        self.launch()
        data = self.fixture.log.read_text()
        line = "aidoku: startup_stage_diag main_us=3500000 initial_us=3750000 submitted_us=3900000\n"
        self.fixture.log.write_text(data.replace(line, line + line))
        self.reject()

    def test_invalid_or_mismatched_times_are_rejected(self):
        self.launch()
        base = self.fixture.log.read_text()
        for changed in (base.replace("submitted_us=3900000", "submitted_us=3900001"),
                        base.replace("main_us=3500000", "main_us=3900001"),
                        base.replace("initial_us=3750000", "initial_us=3400000"),
                        base.replace("initial_us=3750000", "initial_us=0003750000"),
                        base.replace("main_us=3500000", "main_us=3069999")):
            self.fixture.log.write_text(changed)
            self.reject()

    def test_same_bound_and_clock_rate_are_required(self):
        self.launch()
        bound = self.fixture.visit / "bound"
        original_bound = bound.read_text()
        bound.write_text(original_bound.replace(original.NONCE, "b" * 32))
        self.reject()
        bound.write_text(original_bound)
        self.stub("getconf", "printf '250\\n'")
        self.reject()

    def test_unavailable_rate_does_not_invalidate_primary(self):
        self.stub("getconf", "exit 1")
        self.stub("uname", "printf 'x86_64\\n'")
        self.launch()
        self.assertTrue(self.collect().endswith("STAGES unavailable\n"))

    def test_rate_lost_after_prepare_is_unavailable(self):
        self.launch()
        self.stub("getconf", "exit 1")
        self.stub("uname", "printf 'x86_64\\n'")
        self.assertTrue(self.collect().endswith("STAGES unavailable\n"))

    def test_unavailable_rate_does_not_hide_bad_diagnostic(self):
        self.stub("getconf", "exit 1")
        self.stub("uname", "printf 'x86_64\\n'")
        self.launch(submitted="3900001")
        self.reject()

    def test_negative_lower_bound_is_not_clamped(self):
        self.launch()
        self.release("1999970 1999971 1999999 2000001")
        result = self.collect()
        self.assertIn("STAGES 100 -1 10001 ", result)

    def test_nonintegral_tick_bins_are_rounded_outward(self):
        self.stub("getconf", "printf '300\\n'")
        self.launch()
        self.release("600000 600001 600020 600021")
        self.assertIn("STAGES 300 66645 69980 423312 426647 ", self.collect())

    def test_pre_release_birth_and_overflow_are_rejected(self):
        self.launch()
        self.release("2100000 2100001 2100020 2100021")
        self.reject()
        self.release("1500000 1500001 1500020 1500021")
        for path in (self.fixture.visit / "bound", self.fixture.state / ("submission-" + self.fixture.name) / "bound"):
            path.write_text(path.read_text().replace("111 200 ", "111 9999999999999 "))
        self.reject()

    def set_arm_auxv(self, content):
        self.stub("getconf", "exit 1")
        self.stub("uname", "printf 'armv7l\\n'")
        auxv = self.fixture.root / "auxv"
        auxv.write_text(content + "\n")
        self.stub("od", f'''case "$*" in
            *auxv*) cat "{auxv}";;
            *exe*) printf '127 69 76 70 1 1 1 0 0 0 0 0 0 0 0 0 0 0 40 0\\n';;
            *) exit 1;;
        esac''')

    def test_arm32_auxv_fallback(self):
        self.set_arm_auxv("17 100 6 4096 0 0")
        self.launch()
        self.assertIn("STAGES 100 ", self.collect())

    def test_auxv_rejects_duplicate_and_partial_tags(self):
        for content in ("17 100 17 100 0 0", "17 100 0"):
            with self.subTest(content=content):
                self.set_arm_auxv(content)
                self.fixture.call("prepare", success=False)
                directory = self.fixture.state / ("submission-" + self.fixture.name)
                subprocess.run(["rm", "-r", str(directory)], check=True)

    def test_auxv_unavailable_for_non_arm_and_non_elf32_helpers(self):
        self.set_arm_auxv("17 100 0 0")
        self.stub("uname", "printf 'aarch64\\n'")
        self.launch()
        self.assertTrue(self.collect().endswith("STAGES unavailable\n"))

    def test_non_elf32_helper_is_unavailable(self):
        self.set_arm_auxv("17 100 0 0")
        self.stub("od", "printf '127 69 76 70 2 1 1 0 0 0 0 0 0 0 0 0 0 0 40 0\\n'")
        self.launch()
        self.assertTrue(self.collect().endswith("STAGES unavailable\n"))

    def test_generation_refuses_drift_and_source_overwrite(self):
        with self.assertRaises(ValueError):
            generator.generate("unrelated source")
        source = self.fixture.root / "source.sh"
        source.write_text((OLD / "submission.sh").read_text())
        result = subprocess.run(["python3", str(HERE / "submission_generate.py"), str(source), str(source)],
                                capture_output=True)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
