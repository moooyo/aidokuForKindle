#!/usr/bin/env python3
"""Exercise the native read-only bridge against an isolated fake LIPC library."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[2] / "src/native_query/worker.c"
STUB = r'''
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static void record(const char *event) {
    FILE *file = fopen(getenv("QUERY_STUB_LOG"), "a");
    if (file) { fprintf(file, "%s\n", event); fclose(file); }
}
void *LipcOpenNoName(void) { record("open"); return (void *)1; }
void LipcClose(void *handle) { (void)handle; record("close"); }
void LipcFreeString(char *value) { record("free"); free(value); }
int LipcGetStringProperty(void *handle, const char *service, const char *name, char **value) {
    (void)handle; record(service); record(name);
    const char *mode = getenv("QUERY_STUB_MODE");
    if (strcmp(name, "state") == 0 && mode && strcmp(mode, "error") == 0) return 7;
    if (strcmp(name, "state") == 0 && mode && strcmp(mode, "long") == 0) {
        *value = malloc(130); memset(*value, 'x', 129); (*value)[129] = 0; return 0;
    }
    if (strcmp(name, "state") == 0 && mode && strcmp(mode, "null") == 0) {
        *value = NULL; return 0;
    }
    const char *text = strcmp(name, "state") == 0 ? "active" :
        strcmp(name, "epdcMode") == 0 ? "normal" :
        strcmp(name, "accelerometer") == 0 ? "U" : "connected";
    *value = strdup(text); return 0;
}
int LipcGetIntProperty(void *handle, const char *service, const char *name, int *value) {
    (void)handle; record(service); record(name);
    *value = strcmp(name, "battLevel") == 0 ? 88 :
        strcmp(name, "isCharging") == 0 ? 0 :
        strcmp(name, "flIntensity") == 0 ? 7 : 3;
    return 0;
}
'''


@unittest.skipUnless(sys.platform.startswith("linux"), "Run on test-env")
class WorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="aidoku-native-query-")
        cls.root = Path(cls.temporary.name)
        cls.library = cls.root / "libfixture.so"
        stub = cls.root / "fixture.c"
        stub.write_text(STUB)
        subprocess.run(["cc", "-shared", "-fPIC", "-O2", "-Wall", "-Wextra", "-Werror",
                        str(stub), "-o", str(cls.library)], check=True)
        cls.worker = cls.root / "worker"
        subprocess.run(["cc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                        f'-DAIDOKU_QUERY_TEST_LIBRARY="{cls.library}"', str(SOURCE),
                        "-ldl", "-o", str(cls.worker)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_worker(self, requests, mode="", arguments=("--session",)):
        log = self.root / (self.id().split(".")[-1] + ".log")
        log.write_text("")
        environment = dict(os.environ, QUERY_STUB_LOG=str(log), QUERY_STUB_MODE=mode)
        result = subprocess.run([str(self.worker), *arguments], input=requests,
                                capture_output=True, timeout=7, env=environment)
        events = log.read_text().splitlines()
        if result.stdout:
            self.assertEqual(result.stdout[:4], b"AQ1\n")
        frames = []
        remaining = result.stdout[4:]
        while remaining:
            self.assertGreaterEqual(len(remaining), 4)
            opcode, status = remaining[:2]
            size = int.from_bytes(remaining[2:4], "big")
            self.assertLessEqual(size, 128)
            self.assertGreaterEqual(len(remaining), size + 4)
            frames.append((opcode, status, remaining[4:4 + size]))
            remaining = remaining[4 + size:]
        return result, frames, events

    def test_multiple_reads_share_one_handle_and_release_owned_strings(self):
        result, frames, events = self.run_worker(bytes(range(1, 9)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([frame[0] for frame in frames], list(range(1, 9)))
        self.assertTrue(all(frame[1] == 0 for frame in frames))
        self.assertEqual([frame[2] for frame in frames],
                         [b"active", b"normal", b"U", b"connected", b"88", b"0", b"7", b"3"])
        self.assertEqual(events.count("open"), 1)
        self.assertEqual(events.count("close"), 1)
        self.assertEqual(events.count("free"), 4)

    def test_native_error_does_not_fabricate_a_value_or_discard_later_properties(self):
        result, frames, events = self.run_worker(b"\x01\x05", "error")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(frames, [(1, 1, b""), (5, 0, b"88")])
        self.assertEqual(events.count("free"), 0)

    def test_oversized_string_is_rejected_and_freed(self):
        result, frames, events = self.run_worker(b"\x01", "long")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(frames, [(1, 2, b"")])
        self.assertEqual(events.count("free"), 1)

    def test_null_string_is_not_a_successful_empty_value(self):
        result, frames, _ = self.run_worker(b"\x01", "null")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(frames, [(1, 2, b"")])

    def test_unknown_opcode_and_arbitrary_arguments_are_rejected(self):
        for opcode in [0, 9, 255]:
            result, frames, events = self.run_worker(bytes([opcode]))
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(frames, [])
            self.assertEqual(events, ["open", "close"])
        result, frames, events = self.run_worker(b"", arguments=("--set",))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(frames, [])
        self.assertEqual(events, [])

    def test_eof_and_request_limit_close_the_session(self):
        result, frames, events = self.run_worker(b"")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(frames, [])
        self.assertEqual(events, ["open", "close"])
        result, frames, events = self.run_worker(b"\x05" * 33)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(frames), 32)
        self.assertEqual(events.count("close"), 1)


if __name__ == "__main__":
    unittest.main()
