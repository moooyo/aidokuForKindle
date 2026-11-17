#!/usr/bin/env python3
"""Exercise the Library bridge on test-env with isolated fake LIPC services."""

import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.parse import quote

SOURCE = Path(__file__).resolve().parents[2] / "src/native_entry/bridge.c"
STUB = r'''
#define _GNU_SOURCE
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
typedef int (*Callback)(void *, const char *, void *, void *);
static Callback load_cb, go_cb, pause_cb, unload_cb;
static void *context;
static pthread_t worker;
static int started;
static void record(const char *event, const char *value) {
    FILE *file = fopen(getenv("ENTRY_TEST_LOG"), "a");
    if (file) { fprintf(file, "%s:%s\n", event, value ? value : ""); fclose(file); }
}
static const char *mode(void) { const char *value = getenv("ENTRY_TEST_MODE"); return value ? value : "normal"; }
static void *events(void *unused) {
    (void)unused;
    usleep(1000);
    load_cb((void *)1, "load", "1:", context);
    if (strcmp(mode(), "pause-first") == 0) pause_cb((void *)1, "pause", "2:", context);
    const char *uri = getenv("ENTRY_TEST_REQUEST");
    int result = go_cb((void *)1, "go", (void *)uri, context);
    if (strcmp(mode(), "duplicate") == 0) go_cb((void *)1, "go", (void *)uri, context);
    if (result != 0) unload_cb((void *)1, "unload", "9:", context);
    return NULL;
}
void *LipcOpenEx(const char *service, int *code) { record("open", service); *code=0; return (void *)1; }
void *LipcOpenNoName(void) { record("anonymous", ""); return (void *)1; }
void LipcClose(void *handle) {
    (void)handle;
    if (started && !pthread_equal(worker, pthread_self())) pthread_join(worker, NULL);
    record("closed", "");
}
int LipcRegisterStringProperty(void *handle, const char *name, Callback getter, Callback setter, void *data) {
    (void)handle; (void)getter; context=data;
    if (strcmp(name,"load")==0) load_cb=setter;
    if (strcmp(name,"go")==0) go_cb=setter;
    if (strcmp(name,"pause")==0) pause_cb=setter;
    if (strcmp(name,"unload")==0) unload_cb=setter;
    return 0;
}
int LipcSetStringProperty(void *handle, const char *service, const char *name, const char *value) {
    (void)handle; (void)service; record(name,value);
    if (strcmp(name,"runresult")==0) { started=1; pthread_create(&worker,NULL,events,NULL); }
    if (strcmp(name,"goresult")==0 && strcmp(mode(),"unload-before-ack")==0)
        unload_cb((void *)1,"unload","8:",context);
    if (strcmp(name,"stop")==0) {
        if (strcmp(mode(),"stop-failure")==0) return 7;
        if (strcmp(mode(),"hold-stop")!=0) unload_cb((void *)1,"unload","7:",context);
    }
    if (strcmp(name,"unloadresult")==0 && strcmp(mode(),"bad-unload-ack")==0) return 7;
    return 0;
}
int LipcSetIntProperty(void *handle, const char *service, const char *name, int value) {
    (void)handle; (void)service; (void)value; record(name, "1");
    return strcmp(mode(),"scan-failure")==0 ? 7 : 0;
}
'''


@unittest.skipUnless(sys.platform.startswith("linux"), "Run only through ssh test-env")
class NativeEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="aidoku-native-entry-")
        cls.root = Path(cls.temporary.name)
        cls.document = cls.root / "Aidoku ' percent%.sh"
        cls.binary = cls.root / "entry"
        cls.library = cls.root / "fixture.so"
        source = cls.root / "fixture.c"
        source.write_text(STUB)
        subprocess.run(["cc", "-shared", "-fPIC", "-O2", "-Wall", "-Wextra", "-Werror",
                        str(source), "-lpthread", "-o", str(cls.library)], check=True)
        subprocess.run(["cc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                        "-DENTRY_SERVICE_ID=\"org.aidoku.test\"",
                        "-DENTRY_DOCUMENT_PATH=" + json.dumps(str(cls.document)),
                        "-DENTRY_TEST_LIBRARY=" + json.dumps(str(cls.library)),
                        str(SOURCE), "-ldl", "-lpthread", "-o", str(cls.binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.directory = self.root / self.id().split(".")[-1]
        self.directory.mkdir()
        self.log = self.directory / "operations"
        self.marker = self.directory / "launched"
        self.log.write_text("")
        self.document.write_text("#!/bin/sh\n# Name: Aidoku\n# DontUseFBInk\n" +
                                 "printf 'script_launched:\\n' >>" + shlex.quote(str(self.log)) + "\n" +
                                 "printf complete >" + shlex.quote(str(self.marker)) + "\n")
        self.environment = dict(os.environ, ENTRY_TEST_LOG=str(self.log), ENTRY_TEST_MODE="normal",
                                ENTRY_TEST_REQUEST="3:app://org.aidoku.test/" + quote(str(self.document)))

    def run_entry(self, mode="normal", arguments=()):
        self.environment["ENTRY_TEST_MODE"] = mode
        result = subprocess.run([str(self.binary), "--trace-dir", str(self.directory), *arguments],
                                env=self.environment, capture_output=True, timeout=6)
        deadline = time.monotonic() + 1
        while result.returncode == 0 and not self.marker.exists() and time.monotonic() < deadline:
            if "start:app://" in self.log.read_text() or "--probe" in arguments:
                break
            time.sleep(0.01)
        return result, self.log.read_text().splitlines()

    def test_only_acknowledged_unload_releases_one_scanned_document(self):
        result, events = self.run_entry()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.marker.exists())
        self.assertEqual(events.count("doFullScan:1"), 1)
        self.assertLess(events.index("goresult:3:0:"), events.index("stop:org.aidoku.test"))
        self.assertLess(events.index("unloadresult:7:0:"), events.index("closed:"))
        self.assertLess(events.index("closed:"), events.index("script_launched:"))

    def test_duplicate_go_does_not_duplicate_scan_or_launch(self):
        result, events = self.run_entry("duplicate")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(events.count("doFullScan:1"), 1)
        self.assertEqual(events.count("script_launched:"), 1)

    def test_external_pause_or_unload_cancels_the_pending_launch(self):
        for mode in ["pause-first", "unload-before-ack"]:
            with self.subTest(mode=mode):
                directory = self.directory / mode
                directory.mkdir()
                result = subprocess.run([str(self.binary), "--trace-dir", str(directory)],
                                        env=dict(self.environment, ENTRY_TEST_MODE=mode), capture_output=True, timeout=6)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.marker.exists())

    def test_scan_or_stop_failure_never_commits_the_child(self):
        result, events = self.run_entry("scan-failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stop:org.aidoku.test", events)
        self.assertFalse(self.marker.exists())

    def test_stop_failure_closes_the_gate_without_launch(self):
        result, _ = self.run_entry("stop-failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.marker.exists())

    def test_unacknowledged_stop_times_out_without_launch(self):
        result, _ = self.run_entry("hold-stop")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.marker.exists())

    def test_invalid_unload_ack_does_not_release_the_gate(self):
        result, _ = self.run_entry("bad-unload-ack")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.marker.exists())

    def test_probe_mode_does_not_touch_scan_or_execute_document(self):
        before = self.document.stat().st_mtime_ns
        result, events = self.run_entry(arguments=("--probe",))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(before, self.document.stat().st_mtime_ns)
        self.assertNotIn("doFullScan:1", events)
        self.assertFalse(self.marker.exists())

    def test_hooks_retain_the_original_generic_handler(self):
        self.document.write_text("#!/bin/sh\n# DontUseFBInk\n# UseHooks\n")
        result, events = self.run_entry()
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("doFullScan:1", events)
        self.assertFalse(self.marker.exists())
        self.assertIn("start:app://tech.hackerdude.shell_integration.launcher/" + quote(str(self.document)), events)

    def test_unrelated_or_malformed_uris_are_rejected_without_scan(self):
        for index, value in enumerate(["3:app://org.aidoku.other/", "3:app://org.aidoku.test/tmp/other",
                                       "3:app://org.aidoku.test/%00", "3:app://org.aidoku.test/%ZZ", "bad token:uri"]):
            with self.subTest(request=value):
                directory = self.directory / str(index)
                directory.mkdir()
                result = subprocess.run([str(self.binary), "--trace-dir", str(directory)],
                                        env=dict(self.environment, ENTRY_TEST_REQUEST=value), capture_output=True, timeout=6)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.marker.exists())
        self.assertNotIn("doFullScan:1", self.log.read_text())

    def test_child_cannot_survive_an_interrupted_uncommitted_service(self):
        process = subprocess.Popen([str(self.binary), "--trace-dir", str(self.directory)],
                                   env=dict(self.environment, ENTRY_TEST_MODE="hold-stop"),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 2
        while "stop:" not in self.log.read_text() and time.monotonic() < deadline:
            time.sleep(0.01)
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=3)
        self.assertNotEqual(process.returncode, 0)
        self.assertFalse(self.marker.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
