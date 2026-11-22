import copy
import gzip
from types import SimpleNamespace
import unittest

import lifecycle


class LeaseError(Exception):
    def __init__(self, code, details):
        self.code = code
        self.details = details


class Lease:
    LeaseError = LeaseError

    def __init__(self):
        self.held = False
        self.acquire_error = None
        self.fail_acquire_after_hold = False
        self.bad_release = False
        self.calls = []
        self.proof = {"project": "Aidoku", "owner_thread": "owner", "run_id": "run", "lease_token_sha256": "proof"}

    def acquire(self, request):
        self.calls.append("acquire")
        if self.acquire_error:
            raise self.acquire_error
        self.held = True
        if self.fail_acquire_after_hold:
            raise TimeoutError()
        return dict(self.proof, ok=True, operation="acquire", state="held")

    def status(self, request):
        self.calls.append("status")
        return dict(self.proof, ok=True, operation="status", state="held" if self.held else "absent",
                    ownership_verified=self.held)

    def release(self, request, *, native_restored_and_connections_closed):
        self.calls.append("release")
        if not self.held or not native_restored_and_connections_closed:
            raise AssertionError("unsafe release")
        self.held = False
        return dict(self.proof, ok=True, operation="release", state="released", owner_and_token_checked=True,
                    native_restored_and_connections_closed=True, lease_directory_absent_after_release=not self.bad_release)


class Remote:
    def __init__(self, lease):
        self.lease = lease
        self.calls = []
        self.faults = {}
        self.data = gzip.compress(b"private archive")
        self.after = None

    def __call__(self, operation):
        if not self.lease.held or self.lease.calls[-1] != "status":
            raise AssertionError("operation without ownership check")
        self.calls.append(operation)
        if self.after:
            self.after(operation)
        failures = self.faults.get(operation, [])
        if failures:
            failure = failures.pop(0)
            if isinstance(failure, BaseException):
                raise failure
            return failure
        return (0, self.data if operation == "collect" else lifecycle.MARKERS[operation], b"")


class Checkpoint:
    def __init__(self):
        self.saved = None
        self.fail_when = None

    def __call__(self, report):
        if self.fail_when and self.fail_when(report):
            raise OSError("private data must not be reported")
        self.saved = copy.deepcopy(report)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.lease = Lease()
        self.remote = Remote(self.lease)
        self.checkpoint = Checkpoint()
        self.request = SimpleNamespace(owner_thread="owner", run_id="run")
        self.report = {}

    def run_visit(self, recover=False):
        return lifecycle.operate(lease=self.lease, request=self.request, remote=self.remote, report=self.report,
                                 checkpoint=self.checkpoint, recover=recover)

    def test_success_checks_ownership_and_returns_only_raw_archive(self):
        output = self.run_visit()
        self.assertEqual(self.remote.calls, ["admit", "prepare", "visit", "collect", "restore", "close", "absent"])
        self.assertEqual(output, self.remote.data)
        self.assertTrue(self.report["operations_passed"])
        self.assertTrue(self.report["state"]["released"])
        self.assertFalse(self.report["recovery_required"])
        self.assertNotIn("private archive", str(self.report))
        self.assertEqual(self.checkpoint.saved, self.report)

    def test_read_only_admission_failure_releases_without_mutation(self):
        self.remote.faults["admit"] = [(1, b"refused", b"private")]
        self.assertIsNone(self.run_visit())
        self.assertEqual(self.remote.calls, ["admit"])
        self.assertIn("release", self.lease.calls)
        self.assertFalse(self.report["operations_passed"])
        self.assertEqual(self.report["error"], "admit_failed")

    def test_explicit_not_started_and_absence_allow_release(self):
        self.remote.faults["prepare"] = [(0, b"not_started\n", b"")]
        self.run_visit()
        self.assertEqual(self.remote.calls, ["admit", "prepare", "absent"])
        self.assertTrue(self.report["state"]["released"])
        self.assertTrue(self.checkpoint.saved["state"]["prepare_not_started"])
        self.assertFalse(self.report["state"]["visit_started"])
        self.assertFalse(self.report["operations_passed"])

    def test_unknown_prepare_never_uses_absence_as_proof_of_no_mutation(self):
        self.remote.faults["prepare"] = [TimeoutError()]
        self.remote.faults["restore"] = [(0, b"stage_absent\n", b"")]
        self.run_visit()
        self.assertEqual(self.remote.calls, ["admit", "prepare", "restore"])
        self.assertNotIn("release", self.lease.calls)
        self.assertFalse(self.report["state"]["restored"])
        self.assertTrue(self.report["recovery_required"])
        self.report = copy.deepcopy(self.checkpoint.saved)
        self.run_visit(recover=True)
        self.assertEqual(self.remote.calls[-3:], ["restore", "close", "absent"])
        self.assertNotIn("visit", self.remote.calls)
        self.assertTrue(self.report["state"]["released"])

    def test_unknown_prepare_restores_before_close_even_if_absence_would_succeed(self):
        self.remote.faults["prepare"] = [TimeoutError()]
        self.run_visit()
        self.assertEqual(self.remote.calls, ["admit", "prepare", "restore", "close", "absent"])
        self.assertTrue(self.report["state"]["released"])

    def test_not_started_requires_exact_success_and_durable_checkpoint(self):
        for reply in ((1, b"not_started\n", b""), (0, b"not_started", b""),
                      (0, b"not_started\nextra\n", b"")):
            with self.subTest(reply=reply):
                self.setUp()
                self.remote.faults["prepare"] = [reply]
                self.remote.faults["restore"] = [(0, b"stage_absent\n", b"")]
                self.run_visit()
                self.assertFalse(self.report["state"]["prepare_not_started"])
                self.assertNotIn("release", self.lease.calls)
        self.setUp()
        self.remote.faults["prepare"] = [(0, b"not_started\n", b"")]
        self.checkpoint.fail_when = lambda report: report["state"]["prepare_not_started"]
        self.run_visit()
        self.assertFalse(self.checkpoint.saved["state"]["prepare_not_started"])
        self.assertNotIn("absent", self.remote.calls)
        self.assertNotIn("release", self.lease.calls)
        self.report = copy.deepcopy(self.checkpoint.saved)
        self.checkpoint.fail_when = None
        self.remote.faults["restore"] = [(0, b"stage_absent\n", b"")]
        self.run_visit(recover=True)
        self.assertEqual(self.remote.calls[-1], "restore")
        self.assertNotIn("release", self.lease.calls)
        self.assertTrue(self.report["recovery_required"])

    def test_not_started_still_requires_absence_proof(self):
        self.remote.faults["prepare"] = [(0, b"not_started\n", b"")]
        self.remote.faults["absent"] = [TimeoutError()]
        self.run_visit()
        self.assertEqual(self.remote.calls, ["admit", "prepare", "absent"])
        self.assertNotIn("release", self.lease.calls)
        self.report = copy.deepcopy(self.checkpoint.saved)
        self.run_visit(recover=True)
        self.assertEqual(self.remote.calls[-1], "absent")
        self.assertTrue(self.report["state"]["released"])

    def test_uncertain_visit_is_never_retried_and_always_restored(self):
        for error in (TimeoutError(), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                self.setUp()
                self.remote.faults["visit"] = [error]
                self.run_visit()
                self.assertEqual(self.remote.calls, ["admit", "prepare", "visit", "restore", "close", "absent"])
                self.assertTrue(self.report["state"]["released"])
                self.assertFalse(self.report["operations_passed"])

    def test_failed_restore_never_closes_or_releases_then_recovers(self):
        self.remote.faults["restore"] = [(1, b"", b"protection mismatch")]
        output = self.run_visit()
        self.assertEqual(output, self.remote.data)
        self.assertNotIn("close", self.remote.calls)
        self.assertNotIn("release", self.lease.calls)
        self.assertTrue(self.report["recovery_required"])
        self.report = copy.deepcopy(self.checkpoint.saved)
        self.run_visit(recover=True)
        self.assertEqual(self.remote.calls[-3:], ["restore", "close", "absent"])
        self.assertEqual(self.remote.calls.count("visit"), 1)
        self.assertTrue(self.report["state"]["released"])
        self.assertFalse(self.report["operations_passed"])

    def test_owner_change_prevents_close_and_release(self):
        self.remote.after = lambda operation: setattr(self.lease, "held", False) if operation == "visit" else None
        self.run_visit()
        self.assertNotIn("close", self.remote.calls)
        self.assertNotIn("release", self.lease.calls)
        self.assertTrue(self.report["recovery_required"])

    def test_absence_failure_after_confirmed_close_recovers_without_second_close(self):
        self.remote.faults["absent"] = [TimeoutError()]
        self.run_visit()
        self.assertTrue(self.checkpoint.saved["state"]["closed"])
        self.assertNotIn("release", self.lease.calls)
        self.report = copy.deepcopy(self.checkpoint.saved)
        self.run_visit(recover=True)
        self.assertEqual(self.remote.calls.count("close"), 1)
        self.assertEqual(self.remote.calls[-1], "absent")
        self.assertTrue(self.report["state"]["released"])

    def test_lost_close_checkpoint_with_deleted_stage_fails_closed(self):
        device = {"stage_present": True, "transition_present": True}

        def guarded_close(operation):
            if operation == "close":
                if device["stage_present"] and device["transition_present"]:
                    device["stage_present"] = False
                    device["transition_present"] = False
                else:
                    self.remote.faults["close"] = [(1, b"", b"missing stage or native proof")]

        self.remote.after = guarded_close
        self.checkpoint.fail_when = lambda report: report["state"]["closed"]
        self.run_visit()
        self.assertEqual(self.remote.calls[-1], "close")
        self.assertFalse(device["stage_present"])
        self.assertFalse(device["transition_present"])
        self.assertNotIn("release", self.lease.calls)
        self.assertTrue(self.checkpoint.saved["state"]["close_started"])
        self.assertFalse(self.checkpoint.saved["state"]["closed"])
        self.report = copy.deepcopy(self.checkpoint.saved)
        self.checkpoint.fail_when = None
        self.run_visit(recover=True)
        self.assertEqual(self.remote.calls[-2:], ["close", "close"])
        self.assertNotIn("absent", self.remote.calls)
        self.assertNotIn("release", self.lease.calls)
        self.assertEqual(self.remote.calls.count("visit"), 1)
        self.assertFalse(self.report["state"]["released"])
        self.assertTrue(self.report["recovery_required"])
        self.assertEqual(self.report["error"], "close_failed")

    def test_uncertain_close_rechecks_intact_stage_before_release(self):
        device = {"stage_present": True, "transition_present": True, "close_attempts": 0, "rechecked": False}

        def guarded_close(operation):
            if operation == "close":
                device["close_attempts"] += 1
                if device["close_attempts"] == 2:
                    device["rechecked"] = device["stage_present"] and device["transition_present"]
                    if device["rechecked"]:
                        device["stage_present"] = False
                        device["transition_present"] = False
                    else:
                        self.remote.faults["close"] = [(1, b"", b"missing stage or native proof")]

        self.remote.after = guarded_close
        self.remote.faults["close"] = [TimeoutError()]
        self.run_visit()
        self.assertEqual(self.remote.calls[-1], "close")
        self.assertNotIn("release", self.lease.calls)
        self.report = copy.deepcopy(self.checkpoint.saved)
        self.run_visit(recover=True)
        self.assertTrue(device["rechecked"])
        self.assertEqual(self.remote.calls[-2:], ["close", "absent"])
        self.assertEqual(self.remote.calls.count("visit"), 1)
        self.assertTrue(self.report["state"]["released"])
        self.assertFalse(self.report["operations_passed"])

    def test_recovery_rejects_another_token_or_request(self):
        self.remote.faults["restore"] = [TimeoutError()]
        self.run_visit()
        self.report = copy.deepcopy(self.checkpoint.saved)
        self.lease.proof["lease_token_sha256"] = "different"
        previous = list(self.remote.calls)
        self.run_visit(recover=True)
        self.assertEqual(self.remote.calls, previous)
        self.assertNotIn("release", self.lease.calls)
        self.assertEqual(self.report["error"], "lease_identity_mismatch")

    def test_unknown_acquisition_can_recover_only_own_lease_without_device_calls(self):
        self.lease.fail_acquire_after_hold = True
        self.run_visit()
        self.assertEqual(self.remote.calls, [])
        self.assertNotIn("release", self.lease.calls)
        self.report = copy.deepcopy(self.checkpoint.saved)
        self.run_visit(recover=True)
        self.assertEqual(self.remote.calls, [])
        self.assertTrue(self.report["acquisition_unconfirmed"])
        self.assertTrue(self.report["state"]["released"])

    def test_definitely_busy_lease_does_not_require_recovery_or_touch_device(self):
        details = dict(lease_directory_created=False, private_token_created=False, owner_record_created=False)
        self.lease.acquire_error = LeaseError("lease_exists", details)
        self.assertIsNone(self.run_visit())
        self.assertEqual(self.lease.calls, ["acquire"])
        self.assertEqual(self.remote.calls, [])
        self.assertEqual(self.report["error"], "lease_busy")
        self.assertFalse(self.report["operations_passed"])
        self.assertFalse(self.report["recovery_required"])
        self.assertEqual(self.checkpoint.saved, self.report)

    def test_partial_or_ambiguous_acquisition_still_requires_recovery(self):
        details = dict(lease_directory_created=False, private_token_created=False, owner_record_created=False)
        errors = [LeaseError("lease_exists", dict(details, lease_directory_created=True)),
                  LeaseError("lease_exists", dict(details, private_token_created=0)),
                  LeaseError("lease_exists", {"lease_directory_created": False}),
                  LeaseError("other", details), TimeoutError()]
        for error in errors:
            with self.subTest(error=type(error).__name__, details=getattr(error, "details", None)):
                self.setUp()
                self.lease.acquire_error = error
                self.run_visit()
                self.assertEqual(self.lease.calls, ["acquire"])
                self.assertEqual(self.remote.calls, [])
                self.assertEqual(self.report["error"], "acquisition_unknown")
                self.assertTrue(self.report["recovery_required"])

    def test_unverified_release_is_never_reported_as_released(self):
        self.lease.bad_release = True
        self.run_visit()
        self.assertFalse(self.report["state"]["released"])
        self.assertTrue(self.report["recovery_required"])
        self.assertEqual(self.report["error"], "unverified_release")

    def test_bad_collect_is_not_returned_but_still_restores(self):
        self.remote.faults["collect"] = [(0, b"not gzip", b"")]
        self.assertIsNone(self.run_visit())
        self.assertEqual(self.remote.calls[-3:], ["restore", "close", "absent"])
        self.assertTrue(self.report["state"]["released"])
        self.assertFalse(self.report["operations_passed"])


if __name__ == "__main__":
    unittest.main()
