#!/usr/bin/env python3
"""Verify selective dispatch hooks with private executables on test-env."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest

SOURCE = Path(__file__).resolve().parents[2] / "src/native_entry"
LEGACY = r'''
#define _GNU_SOURCE
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>
#include <sys/wait.h>
#include <unistd.h>
static atomic_int child;
static void record(const char *event) {
    FILE *f=fopen(getenv("TEST_LOG"),"a");if(f){fprintf(f,"%s\n",event);fclose(f);}
}
static void *request(void *unused) {
    (void)unused;usleep(25000);
    const char *path=getenv("TEST_DOCUMENT");
    utimes(path,NULL);record("scan");
    if(getenv("TEST_SECOND_REQUEST")){utimes(path,NULL);record("scan");}
    pid_t pid=fork();
    if(pid==0){execl("/bin/sh","sh",path,(char *)NULL);_exit(71);}
    atomic_store(&child,pid);return NULL;
}
int main(void) {
    pthread_t thread;pthread_create(&thread,NULL,request,NULL);
    do {sleep(1);} while(atomic_load(&child)==0);
    waitpid(atomic_load(&child),NULL,0);record("reaped");
    system("/usr/bin/xrefresh -d :0.0");
    system("preserve unrelated system command");
    pthread_join(thread,NULL);record("complete");return 0;
}
'''
PRIOR = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int system(const char *command) {
    FILE *f=fopen(getenv("TEST_LOG"),"a");
    if(f){fprintf(f,"%s\n",command && strcmp(command,"/usr/bin/xrefresh -d :0.0")==0?"redraw":"other-system");fclose(f);}
    return 0;
}
'''


@unittest.skipUnless(sys.platform.startswith("linux"), "Run only through ssh test-env")
class NativeDispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="aidoku-dispatch-")
        cls.root = Path(cls.temporary.name)
        cls.legacy = cls.root / "shared-handler"
        cls.document = cls.root / "Aidoku.sh"
        cls.wrapper = cls.root / "aidoku-library-dispatch"
        cls.hooks = cls.root / "aidoku-entry-hooks.so"
        cls.prior = cls.root / "prior.so"
        (cls.root / "legacy.c").write_text(LEGACY)
        (cls.root / "prior.c").write_text(PRIOR)
        common = ["cc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror"]
        subprocess.run([*common, str(cls.root / "legacy.c"), "-lpthread", "-o", str(cls.legacy)], check=True)
        subprocess.run([*common, "-fPIC", "-shared", str(cls.root / "prior.c"), "-o", str(cls.prior)], check=True)
        cls.original = cls.legacy.read_bytes()
        digest = hashlib.sha256(cls.original).hexdigest()
        definitions = ["-DENTRY_LEGACY_PATH=" + json.dumps(str(cls.legacy)),
                       "-DENTRY_LEGACY_SHA256=" + json.dumps(digest),
                       "-DENTRY_DOCUMENT_PATH=" + json.dumps(str(cls.document))]
        subprocess.run([*common, *definitions, "-fPIC", "-shared", str(SOURCE / "hooks.c"), "-ldl", "-o", str(cls.hooks)], check=True)
        cls.original_hooks = cls.hooks.read_bytes()
        hook_digest = hashlib.sha256(cls.original_hooks).hexdigest()
        subprocess.run([*common, *definitions, "-DENTRY_HOOKS_SHA256=" + json.dumps(hook_digest),
                        str(SOURCE / "dispatch.c"), str(SOURCE / "sha256.c"), "-o", str(cls.wrapper)], check=True)
        cls.hash = cls.root / "hash"
        (cls.root / "hash.c").write_text('''#include "sha256.h"
#include <stdio.h>
int main(void){struct entry_sha256 s;entry_sha256_init(&s);unsigned char b[137];size_t n;
while((n=fread(b,1,sizeof(b),stdin))!=0)entry_sha256_update(&s,b,n);
unsigned char out[32];entry_sha256_finish(&s,out);for(unsigned i=0;i<32;i++)printf("%02x",out[i]);return 0;}
''')
        subprocess.run([*common, "-I", str(SOURCE), str(cls.root / "hash.c"), str(SOURCE / "sha256.c"), "-o", str(cls.hash)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.directory = self.root / self.id().split(".")[-1]
        self.directory.mkdir()
        self.log = self.directory / "operations"
        self.log.write_text("")
        self.environment = dict(os.environ, LD_PRELOAD=str(self.prior), TEST_LOG=str(self.log),
                                TEST_DOCUMENT=str(self.document))
        self.legacy.write_bytes(self.original)
        self.legacy.chmod(0o755)
        self.hooks.write_bytes(self.original_hooks)
        self.write_document()

    def write_document(self, *, declaration=True, fbink=False, hooks=False, late=False, exit_code=0):
        header = ["#!/bin/sh", "# Name: Aidoku"]
        if not fbink:
            header.append("# DontUseFBInk")
        if hooks:
            header.append("# UseHooks")
        if late:
            header.extend(["# filler"] * 6)
        if declaration:
            header.append("# AidokuNativeEntry: v1")
        body = "printf '%s\\n' script >>" + shlex.quote(str(self.log)) + "\n"
        body += "printf '%s' \"${LD_PRELOAD-unset}\" >" + shlex.quote(str(self.directory / "child-preload")) + "\n"
        body += "env | sed -n '/^AIDOKU_ENTRY_/p' >" + shlex.quote(str(self.directory / "child-markers")) + "\n"
        body += "cat /proc/$$/maps >" + shlex.quote(str(self.directory / "child-maps")) + "\n"
        body += "for f in /proc/$$/fd/*; do readlink \"$f\" || :; done >" + shlex.quote(str(self.directory / "child-fds")) + "\n"
        self.document.write_text("\n".join(header) + "\n" + body + f"exit {exit_code}\n")

    def execute(self, arguments=()):
        start = time.monotonic()
        result = subprocess.run([str(self.wrapper), "--trace-dir", str(self.directory), *arguments],
                                env=self.environment, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        trace = "".join(p.read_text() for p in self.directory.glob("native-entry.*.tsv"))
        events = self.log.read_text().splitlines()
        self.assertEqual(events.count("script"), 1)
        self.assertEqual(events.count("other-system"), 1)
        return events, trace, time.monotonic() - start

    def test_hash_vectors_and_block_boundaries(self):
        for data in [b"", b"abc", b"a" * 1000000, *[bytes(range(256)) * 32 + b"x" * n for n in [0,1,55,56,63,64,65]]]:
            result = subprocess.run([str(self.hash)], input=data, capture_output=True, check=True)
            self.assertEqual(result.stdout.decode(), hashlib.sha256(data).hexdigest())

    def test_successful_opted_in_handoff_wakes_once_and_retains_owned_restoration(self):
        events, trace, duration = self.execute()
        self.assertEqual(events.count("scan"), 1)
        self.assertNotIn("redraw", events)
        self.assertIn("managed_child_completed", trace)
        self.assertIn("owned_restoration_retained", trace)
        self.assertLess(duration, 0.8)

    def test_child_environment_and_descriptors_do_not_keep_the_hooks(self):
        self.execute()
        self.assertEqual((self.directory / "child-preload").read_text(), str(self.prior))
        self.assertEqual((self.directory / "child-markers").read_text(), "")
        self.assertNotIn("aidoku-entry-hooks", (self.directory / "child-maps").read_text())
        self.assertNotIn("native-entry.", (self.directory / "child-fds").read_text())

    def test_unrelated_document_keeps_legacy_wait_scan_and_redraw(self):
        other = self.root / "Other.sh"
        other.write_bytes(self.document.read_bytes())
        self.environment["TEST_DOCUMENT"] = str(other)
        events, trace, duration = self.execute()
        self.assertIn("legacy_scriptlet_selected", trace)
        self.assertEqual(events.count("scan"), 1)
        self.assertEqual(events.count("redraw"), 1)
        self.assertGreater(duration, 0.85)

    def test_missing_declaration_keeps_legacy_restoration(self):
        self.write_document(declaration=False)
        events, trace, _ = self.execute()
        self.assertIn("redraw", events)
        self.assertNotIn("aidoku_handoff_selected", trace)

    def test_hooks_and_fbink_require_the_unmodified_path(self):
        self.write_document(hooks=True)
        events, _, _ = self.execute()
        self.assertIn("redraw", events)

    def test_fbink_without_opt_out_is_not_intercepted(self):
        self.write_document(fbink=True)
        events, _, _ = self.execute()
        self.assertIn("redraw", events)

    def test_declaration_outside_six_header_lines_has_no_effect(self):
        self.write_document(late=True)
        events, _, _ = self.execute()
        self.assertIn("redraw", events)

    def test_failed_child_preserves_the_original_refresh(self):
        self.write_document(exit_code=7)
        events, trace, _ = self.execute()
        self.assertIn("managed_child_failed", trace)
        self.assertIn("redraw", events)

    def test_multiple_requests_fall_back_without_changing_request_count(self):
        self.environment["TEST_SECOND_REQUEST"] = "1"
        events, trace, _ = self.execute()
        self.assertEqual(events.count("scan"), 2)
        self.assertIn("redraw", events)
        self.assertNotIn("owned_restoration_retained", trace)

    def test_changed_shared_binary_runs_without_hooks(self):
        self.legacy.write_bytes(self.original + b"changed version")
        events, trace, _ = self.execute()
        self.assertIn("unqualified_handler_fallback", trace)
        self.assertIn("redraw", events)
        self.assertNotIn("hooks_ready", trace)

    def test_missing_hook_library_runs_original_handler(self):
        saved = self.hooks.with_suffix(".saved")
        self.hooks.rename(saved)
        try:
            events, trace, _ = self.execute()
            self.assertIn("hooks_unavailable_fallback", trace)
            self.assertIn("redraw", events)
        finally:
            saved.rename(self.hooks)

    def test_changed_hook_library_is_not_loaded_or_inherited(self):
        self.hooks.write_bytes(self.original_hooks + b"changed hooks")
        events, trace, _ = self.execute()
        self.assertIn("hooks_unavailable_fallback", trace)
        self.assertIn("redraw", events)
        self.assertEqual((self.directory / "child-preload").read_text(), str(self.prior))

    def test_explicit_diagnostic_policy_can_disable_without_registry_changes(self):
        policy = self.directory / "policy"
        policy.write_text("off\n")
        events, trace, _ = self.execute(("--policy-file", str(policy)))
        self.assertIn("diagnostic_policy_disabled", trace)
        self.assertIn("redraw", events)


if __name__ == "__main__":
    unittest.main(verbosity=2)
