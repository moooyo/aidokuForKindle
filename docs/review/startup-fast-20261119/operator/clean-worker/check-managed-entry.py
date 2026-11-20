"""Check the staged Library launcher's child environment using only fake services."""

import os
from pathlib import Path
import runpy
import shutil
import sys
import unittest


def main(source, bench):
    launcher = runpy.run_path(str(source / "packaging/tests/launcher_test.py"))
    instrument = runpy.run_path(str(bench / "analyze.py"))["instrument"]

    class ManagedEntry(launcher["LauncherTests"]):
        def check_staged_child(self):
            staged = self.root / "staged-data"
            shutil.copytree(self.home / "data", staged)
            output = self.root / "research"
            output.mkdir()
            self.fixture_launcher.write_bytes(instrument(self.fixture_launcher.read_bytes()))
            self.environment.pop("AIDOKU_MANAGED_LIBRARY", None)
            self.environment.update(AIDOKU_RESEARCH_DATA=str(staged), AIDOKU_RESEARCH_OUT=str(output),
                                    AIDOKU_RESEARCH_DWELL="8000", AIDOKU_RESEARCH_CAPTURE_ARGS="",
                                    AIDOKU_FAKE_EXIT_AFTER="1")
            child = Path("/proc") / str(self.start_library_entry())
            self.assertIn(b"AIDOKU_MANAGED_LIBRARY=1", (child / "environ").read_bytes().split(b"\0"))
            args = (child / "cmdline").read_bytes().split(b"\0")
            self.assertEqual(args[args.index(b"--data-dir") + 1], os.fsencode(staged))
            self.assertEqual(args[args.index(b"--performance-trace") + 1], os.fsencode(output / "application.json"))
            self.assertEqual(self.complete(), 0)
            self.assert_recovered()
            markers = [line.split()[1] for line in (output / "shell.tsv").read_text().splitlines()]
            self.assertEqual(markers, ["launcher_entry", "launcher_worker"])

    result = unittest.TextTestRunner(verbosity=2).run(ManagedEntry("check_staged_child"))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    if sys.platform != "linux" or len(sys.argv) != 3:
        raise SystemExit("Run on test-env: check-managed-entry.py PRODUCT_SOURCE BENCH_TOOLS")
    raise SystemExit(main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()))
