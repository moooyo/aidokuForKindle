"""Validate the bounded catalog client against an isolated loopback service."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SOURCE = Path(__file__).resolve().parent
IDENTITY = "00000000-0000-4000-8000-000000000001"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        server = self.server
        length = int(self.headers["Content-Length"])
        request = json.loads(self.rfile.read(length))
        server.requests.append((self.path, request))
        server.headers_valid &= self.headers.get("AuthToken") == "test-session-token"
        if server.scenario == "timeout":
            time.sleep(1.0)
        if self.path == "/query":
            entries = [copy.deepcopy(server.entry)]
            if server.scenario == "duplicate": entries *= 2
            if server.scenario == "missing": entries = []
            if server.scenario == "readback" and any(p == "/change" for p, _ in server.requests):
                entries[0]["uuid"] = "00000000-0000-4000-8000-000000000002"
            response = {"type": "QueryResponse", "values": entries}
        elif self.path == "/change":
            update = request["commands"][0]["update"]
            server.update_shape_valid &= (
                set(request).issubset({"type", "commands", "noNotify"})
                and {"type", "commands"}.issubset(request)
                and len(request["commands"]) == 1
                and set(update) == {"uuid", "lastAccess", "percentFinished"}
                and update["uuid"] == IDENTITY
                and update["percentFinished"] == 0
            )
            if server.scenario != "rejected":
                server.entry["lastAccess"] = update["lastAccess"]
            response = {"type": "ChangeResponse", "ok": server.scenario != "rejected", "changes": 1}
        else:
            self.send_error(404)
            return
        data = json.dumps(response).encode()
        if server.scenario == "trailing": data += b" {}"
        if server.scenario == "oversize": data = b" " * 40000
        status = 503 if server.scenario == "unavailable" else 200
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data) + (1 if server.scenario == "short-body" else 0)))
        if server.scenario == "duplicate-length": self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass


@unittest.skipUnless(sys.platform.startswith("linux"), "Run only through ssh test-env")
class CatalogClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="aidoku-catalog-client-")
        cls.root = Path(cls.temporary.name)
        cls.document = cls.root / "Aidoku.sh"
        cls.binary = cls.root / "client"
        cls.library = cls.root / "token.so"
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        source = cls.root / "token.c"
        source.write_text('#include <stdlib.h>\nchar *get_lab126_session_token(void){char *v=getenv("TEST_TOKEN");return v?v:"test-session-token";}\n')
        common = ["cc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror"]
        subprocess.run([*common, "-fPIC", "-shared", str(source), "-o", str(cls.library)], check=True)
        subprocess.run([*common, "-DENTRY_DOCUMENT_PATH=" + json.dumps(str(cls.document)),
                        "-DCATALOG_UTILS_LIBRARY=" + json.dumps(str(cls.library)),
                        "-DCATALOG_PORT=" + str(cls.server.server_port),
                        str(SOURCE / "catalog-client.c"), "-ldl", "-o", str(cls.binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temporary.cleanup()

    def setUp(self):
        if self.document.is_symlink(): self.document.unlink()
        self.document.write_text("#!/bin/sh\n# Catalog client fixture\nexit 0\n")
        os.utime(self.document, (30, 30))
        self.server.entry = {
            "uuid": IDENTITY, "location": str(self.document), "mimeType": "text/x-shellscript",
            "modificationTime": 30, "contentSize": self.document.stat().st_size,
            "lastAccess": 20, "displayTags": [],
        }
        self.server.requests = []
        self.server.scenario = "normal"
        self.server.headers_valid = True
        self.server.update_shape_valid = True

    def execute(self, operation="--touch", success=True, environment=None):
        result = subprocess.run([str(self.binary), operation], capture_output=True, text=True,
                                timeout=3, env=environment)
        output = json.loads(result.stdout)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        self.assertEqual(output["success"], success)
        self.assertNotIn("test-session-token", result.stdout + result.stderr)
        self.assertNotIn(IDENTITY, result.stdout + result.stderr)
        self.assertTrue(self.server.headers_valid)
        self.assertTrue(self.server.update_shape_valid)
        return output

    def assert_no_change(self):
        self.assertNotIn("/change", [path for path, _ in self.server.requests])

    def test_probe_is_read_only(self):
        self.execute("--probe")
        self.assert_no_change()

    def test_quiet_diagnostic_only_changes_notification_flag(self):
        self.execute("--touch-quiet")
        request = next(body for path, body in self.server.requests if path == "/change")
        self.assertIs(request["noNotify"], True)
        self.assertGreater(self.server.entry["lastAccess"], 20)
        self.assertEqual(self.document.stat().st_mtime, 30)

    def test_touch_updates_only_recent_access_and_checks_readback(self):
        self.execute()
        self.assertEqual([p for p, _ in self.server.requests], ["/query", "/change", "/query"])
        self.assertEqual(self.document.stat().st_mtime, 30)
        self.assertGreater(self.server.entry["lastAccess"], 20)
        request = next(body for path, body in self.server.requests if path == "/change")
        self.assertNotIn("noNotify", request)

    def test_duplicate_identity_is_rejected(self):
        self.server.scenario = "duplicate"
        self.execute(success=False)
        self.assert_no_change()

    def test_missing_identity_is_rejected(self):
        self.server.scenario = "missing"
        self.execute(success=False)
        self.assert_no_change()

    def test_mismatched_entry_or_file_is_rejected(self):
        for field, value in [("location", "/unrelated.sh"), ("mimeType", "application/pdf"),
                             ("uuid", "invalid"), ("modificationTime", 31), ("contentSize", 1),
                             ("lastAccess", "not-a-number")]:
            with self.subTest(field=field):
                original = self.server.entry[field]
                self.server.entry[field] = value
                self.execute(success=False)
                self.assert_no_change()
                self.server.entry[field] = original

    def test_new_tag_retains_legacy_first_open_behavior(self):
        self.server.entry["displayTags"] = ["NEW"]
        self.execute(success=False)
        self.assert_no_change()

    def test_failed_acknowledgement_is_not_success(self):
        self.server.scenario = "rejected"
        self.execute(success=False)

    def test_changed_identity_at_readback_is_not_success(self):
        self.server.scenario = "readback"
        self.execute(success=False)

    def test_unavailable_service_preserves_existing_entry(self):
        self.server.scenario = "unavailable"
        self.execute(success=False)
        self.assert_no_change()

    def test_malformed_http_or_json_is_rejected(self):
        for scenario in ["trailing", "short-body", "duplicate-length", "oversize"]:
            with self.subTest(scenario=scenario):
                self.server.scenario = scenario
                self.execute(success=False)
                self.assert_no_change()

    def test_slow_service_obeys_whole_operation_deadline(self):
        self.server.scenario = "timeout"
        started = time.monotonic()
        self.execute(success=False)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assert_no_change()
        time.sleep(0.3)

    def test_token_header_injection_is_rejected(self):
        self.execute(success=False, environment=dict(os.environ, TEST_TOKEN="bad\r\nheader"))
        self.assertEqual(self.server.requests, [])

    def test_symlink_document_is_rejected(self):
        target = self.root / "other.sh"
        target.write_bytes(self.document.read_bytes())
        self.document.unlink()
        self.document.symlink_to(target)
        self.execute(success=False)
        self.assert_no_change()


if __name__ == "__main__":
    unittest.main(verbosity=2)
