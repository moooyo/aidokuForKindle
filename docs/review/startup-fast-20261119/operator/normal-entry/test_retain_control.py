import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from retain_control import HERE, accept, capture


HEADER = b"\x7fELF\x01\x01\x01" + bytes(11) + b"\x28\x00" + bytes(32)
LAUNCH = b"#!/bin/sh\nexit 0\n"


class Observation(unittest.TestCase):
    def setUp(self):
        private = TemporaryDirectory()
        self.addCleanup(private.cleanup)
        self.root = Path(private.name)
        self.home = self.root / "installed"
        (self.home / "app").mkdir(parents=True)
        for name, data, mode in (("app/aidoku", HEADER + b"test-main", 0o755),
                                 ("launch.sh", LAUNCH, 0o751),
                                 ("app/aidoku-startup-portrait", HEADER, 0o711)):
            path = self.home / name
            path.write_bytes(data)
            path.chmod(mode)
        for name, value in (("stride", "1872"), ("bits_per_pixel", "8")):
            (self.root / name).write_text(value + "\n")
        self.script = ((HERE / "read_control.sh").read_text().replace("/mnt/us/aidoku", str(self.home))
                       .replace("/sys/class/graphics/fb0", str(self.root)).replace("/tmp/$reader-launcher.lock", str(self.root) + "/$reader.lock"))
        for name, body in (("uname", "echo armv7l"), ("status", "echo statusbar start/running"),
                           ("pidof", '[ "$1" = awesome ] && echo 1'),
                           ("lipc-get-prop", 'if [ "$3" = state ]; then echo active; else echo com.lab126.KPPMainApp; fi')):
            self.stub(name, body)

    def stub(self, name, body):
        path = self.root / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o700)

    def observe(self):
        return subprocess.run(["sh", "-s"], input=self.script.encode(), capture_output=True, timeout=5,
                              env=dict(os.environ, PATH=str(self.root) + ":" + os.environ["PATH"]))

    def test_exact_files_and_original_modes(self):
        result = self.observe()
        self.assertEqual((result.returncode, result.stderr), (0, b""))
        report = accept(result.stdout, LAUNCH, HEADER, self.root)
        self.assertEqual(report["launch.sh"], {"bytes": len(LAUNCH), "original_mode": "751"})
        for name in report:
            self.assertEqual((self.root / "control" / name).read_bytes(), (self.home / name).read_bytes())

    def test_read_only_admission_and_inflight_change(self):
        (self.root / "weread.lock").touch()
        self.assertEqual(self.observe().stdout, b"")
        (self.root / "weread.lock").unlink()
        self.stub("head", f'/usr/bin/head "$@" && chmod 600 "{self.home}/launch.sh"')
        changed = self.observe()
        self.assertNotEqual(changed.returncode, 0)
        self.assertNotIn(b"AIDOKU-READ-END", changed.stdout)
        (self.home / "launch.sh").unlink()
        (self.home / "launch.sh").symlink_to(self.home / "app" / "aidoku")
        self.assertEqual(self.observe().stdout, b"")

    def test_truncated_oversized_and_unmatched(self):
        observation = self.observe().stdout
        for invalid in (observation[:-1], observation + b"!", observation.replace(b" 61 ", b" 999999999 ", 1)):
            with self.assertRaises(ValueError):
                accept(invalid, LAUNCH, HEADER, self.root)
        with self.assertRaisesRegex(ValueError, "selected_portrait_helper_differs"):
            accept(observation, LAUNCH, HEADER + b"another", self.root)
        self.assertFalse((self.root / "control").exists())
        self.assertEqual((self.root / "not-admitted/app/aidoku-startup-portrait").read_bytes(), HEADER)

    def test_release_follows_transport_even_on_error_or_interrupt(self):
        for outcome in ((0, b"frame", b""), (1, b"", b"refused native_admission\n"), KeyboardInterrupt()):
            events, report = [], {}
            lease = Mock()
            lease.acquire.side_effect = lambda _: events.append("acquired")
            lease.status.return_value = {"ownership_verified": True}
            def close(*_, **kwargs):
                self.assertTrue(kwargs["native_restored_and_connections_closed"])
                events.append("released")
                return {"owner_and_token_checked": True, "only_owned_owner_json_removed_then_rmdir": True}
            lease.release.side_effect = close
            def remote():
                events.append("connected")
                try:
                    if isinstance(outcome, BaseException):
                        raise outcome
                    return outcome
                finally:
                    events.append("closed")
            if outcome == (0, b"frame", b""):
                self.assertEqual(capture(lease, object(), remote, report), b"frame")
            else:
                with self.assertRaises((RuntimeError, KeyboardInterrupt)):
                    capture(lease, object(), remote, report)
            self.assertEqual(events, ["acquired", "connected", "closed", "released"])
        lease.acquire.side_effect = RuntimeError("foreign")
        calls = lease.release.call_count
        with self.assertRaisesRegex(RuntimeError, "foreign"):
            capture(lease, object(), remote, {})
        self.assertEqual(lease.release.call_count, calls)


if __name__ == "__main__":
    unittest.main()
