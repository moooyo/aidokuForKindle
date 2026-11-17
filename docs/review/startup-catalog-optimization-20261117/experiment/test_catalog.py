"""Exercise the real interposition boundary with isolated host executables."""

import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parent
LEGACY = r'''
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <sys/time.h>
#include <sys/wait.h>
#include <unistd.h>
static void record(const char *event) {
    FILE *f=fopen(getenv("TEST_LOG"),"a");
    if(f){fprintf(f,"%s\n",event);fclose(f);}
}
int main(void) {
    const char *path=getenv("TEST_DOCUMENT");
    struct timeval explicit_time[2]={{4,0},{5,0}};
    if(getenv("TEST_CHILD_TOUCH")) {
        pid_t child=fork();
        if(child==0){utimes(path,NULL);_exit(0);}
        waitpid(child,NULL,0);
    } else {
        utimes(path,getenv("TEST_EXPLICIT")?explicit_time:NULL);
        if(getenv("TEST_SECOND_TOUCH"))utimes(path,NULL);
    }
    record("scan");
    pid_t child=fork();
    if(child==0){execl("/bin/sh","sh",path,(char *)NULL);_exit(71);}
    waitpid(child,NULL,0);record("reaped");
    system("/usr/bin/xrefresh -d :0.0");
    system("preserve unrelated system command");
    record("complete");return 0;
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
class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="aidoku-catalog-")
        cls.root = Path(cls.temporary.name)
        cls.legacy = cls.root / "shared-handler"
        cls.document = cls.root / "Aidoku.sh"
        cls.wrapper = cls.root / "aidoku-library-dispatch"
        cls.hooks = cls.root / "aidoku-entry-hooks.so"
        cls.prior = cls.root / "prior.so"
        (cls.root / "legacy.c").write_text(LEGACY)
        (cls.root / "prior.c").write_text(PRIOR)
        common = ["cc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror"]
        subprocess.run([*common, str(cls.root / "legacy.c"), "-o", str(cls.legacy)], check=True)
        subprocess.run([*common, "-fPIC", "-shared", str(cls.root / "prior.c"), "-o", str(cls.prior)], check=True)
        cls.original = cls.legacy.read_bytes()
        definitions = [
            "-DENTRY_LEGACY_PATH=" + json.dumps(str(cls.legacy)),
            "-DENTRY_LEGACY_SHA256=" + json.dumps(hashlib.sha256(cls.original).hexdigest()),
            "-DENTRY_DOCUMENT_PATH=" + json.dumps(str(cls.document)),
        ]
        subprocess.run([*common, *definitions, "-fPIC", "-shared", str(SOURCE / "hooks.c"),
                        "-ldl", "-o", str(cls.hooks)], check=True)
        cls.original_hooks = cls.hooks.read_bytes()
        subprocess.run([*common, *definitions,
                        "-DENTRY_HOOKS_SHA256=" + json.dumps(hashlib.sha256(cls.original_hooks).hexdigest()),
                        str(SOURCE / "dispatch.c"), str(SOURCE / "sha256.c"),
                        "-o", str(cls.wrapper)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.directory = self.root / self.id().split(".")[-1]
        self.directory.mkdir()
        self.log = self.directory / "operations"
        self.log.write_text("")
        self.policy = self.directory / "policy"
        self.policy.write_text("preserve\n")
        self.environment = dict(os.environ, LD_PRELOAD=str(self.prior),
                                TEST_LOG=str(self.log), TEST_DOCUMENT=str(self.document))
        self.legacy.write_bytes(self.original)
        self.legacy.chmod(0o755)
        self.hooks.write_bytes(self.original_hooks)
        self.write_document()

    def write_document(self, *, declaration=True, fbink=False, hooks=False, late=False, crlf=False):
        header = ["#!/bin/sh", "# Name: Aidoku"]
        if not fbink: header.append("# DontUseFBInk")
        if hooks: header.append("# UseHooks")
        if late: header.extend(["# filler"] * 6)
        if declaration: header.append("# AidokuCatalogDiagnostic: v1")
        body = "printf '%s\\n' script >>" + shlex.quote(str(self.log)) + "\n"
        body += "printf '%s' \"${LD_PRELOAD-unset}\" >" + shlex.quote(str(self.directory / "child-preload")) + "\n"
        body += "env | sed -n '/^AIDOKU_ENTRY_/p' >" + shlex.quote(str(self.directory / "child-markers")) + "\n"
        body += "cat /proc/$$/maps >" + shlex.quote(str(self.directory / "child-maps")) + "\n"
        body += "for f in /proc/$$/fd/*; do readlink \"$f\" || :; done >" + shlex.quote(str(self.directory / "child-fds")) + "\n"
        self.document.write_text(("\r\n" if crlf else "\n").join(header) + "\n" + body)
        os.utime(self.document, (1, 1))

    def execute(self):
        result = subprocess.run([str(self.wrapper), "--trace-dir", str(self.directory),
                                 "--policy-file", str(self.policy)], env=self.environment,
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text().splitlines(),
                         ["scan", "script", "reaped", "redraw", "other-system", "complete"])
        return "".join(p.read_text() for p in self.directory.glob("native-entry.*.tsv"))

    def test_preserve_only_changes_the_selected_timestamp(self):
        trace = self.execute()
        self.assertEqual(self.document.stat().st_mtime_ns, 1_000_000_000)
        self.assertIn("catalog_timestamp_preserved", trace)

    def test_control_uses_the_same_hooks_and_original_timestamp(self):
        self.policy.write_text("original\n")
        trace = self.execute()
        self.assertGreater(self.document.stat().st_mtime_ns, 1_000_000_000)
        self.assertIn("catalog_hooks_ready", trace)
        self.assertIn("catalog_timestamp_original", trace)

    def test_unrelated_document_is_unchanged_by_policy(self):
        other = self.root / "Other.sh"
        other.write_bytes(self.document.read_bytes())
        os.utime(other, (1, 1))
        self.environment["TEST_DOCUMENT"] = str(other)
        trace = self.execute()
        self.assertGreater(other.stat().st_mtime_ns, 1_000_000_000)
        self.assertNotIn("catalog_timestamp_preserved", trace)

    def test_explicit_times_delegate(self):
        self.environment["TEST_EXPLICIT"] = "1"
        self.execute()
        self.assertEqual(self.document.stat().st_mtime_ns, 5_000_000_000)

    def test_forked_process_cannot_apply_policy(self):
        self.environment["TEST_CHILD_TOUCH"] = "1"
        trace = self.execute()
        self.assertGreater(self.document.stat().st_mtime_ns, 1_000_000_000)
        self.assertNotIn("catalog_timestamp_preserved", trace)

    def test_second_request_delegates(self):
        self.environment["TEST_SECOND_TOUCH"] = "1"
        trace = self.execute()
        self.assertEqual(trace.count("catalog_timestamp_preserved"), 1)
        self.assertGreater(self.document.stat().st_mtime_ns, 1_000_000_000)

    def test_child_environment_and_descriptors_restore(self):
        self.execute()
        self.assertEqual((self.directory / "child-preload").read_text(), str(self.prior))
        self.assertEqual((self.directory / "child-markers").read_text(), "")
        self.assertNotIn("aidoku-entry-hooks", (self.directory / "child-maps").read_text())
        self.assertNotIn("native-entry.", (self.directory / "child-fds").read_text())

    def test_changed_handler_falls_back(self):
        self.legacy.write_bytes(self.original + b"version change")
        trace = self.execute()
        self.assertIn("unqualified_handler_fallback", trace)
        self.assertGreater(self.document.stat().st_mtime_ns, 1_000_000_000)

    def test_changed_hook_falls_back(self):
        self.hooks.write_bytes(self.original_hooks + b"version change")
        trace = self.execute()
        self.assertIn("hooks_unavailable_fallback", trace)
        self.assertGreater(self.document.stat().st_mtime_ns, 1_000_000_000)

    def test_unknown_policy_falls_back(self):
        self.policy.write_text("preserve\nextra")
        self.execute()
        self.assertGreater(self.document.stat().st_mtime_ns, 1_000_000_000)

    def test_fifo_policy_does_not_block(self):
        self.policy.unlink()
        os.mkfifo(self.policy)
        self.execute()
        self.assertGreater(self.document.stat().st_mtime_ns, 1_000_000_000)

    def test_symlink_policy_falls_back(self):
        other = self.directory / "other-policy"
        other.write_text("preserve\n")
        self.policy.unlink()
        self.policy.symlink_to(other)
        self.execute()
        self.assertGreater(self.document.stat().st_mtime_ns, 1_000_000_000)

    def test_crlf_header_is_qualified(self):
        self.write_document(crlf=True)
        self.execute()
        self.assertEqual(self.document.stat().st_mtime_ns, 1_000_000_000)

    def test_unqualified_headers_delegate(self):
        for options in [dict(declaration=False), dict(fbink=True), dict(hooks=True), dict(late=True)]:
            with self.subTest(options=options):
                self.write_document(**options)
                self.log.write_text("")
                self.execute()
                self.assertGreater(self.document.stat().st_mtime_ns, 1_000_000_000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
