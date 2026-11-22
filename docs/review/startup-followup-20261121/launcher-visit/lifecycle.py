"""Run or recover one owned diagnostic visit using a durable host checkpoint."""

MAX_ARCHIVE_BYTES = 64 * 1024**2
IDENTITY = ("project", "owner_thread", "run_id", "lease_token_sha256")
MARKERS = {
    "admit": b"admitted\n",
    "prepare": b"prepared\n",
    "visit": b"visit_completed\n",
    "restore": b"restored\n",
    "close": b"stage_absent\n",
    "absent": b"stage_absent\n",
}
FLAGS = ("admitted", "prepare_started", "prepare_not_started", "prepared", "visit_started", "visited",
         "collected", "restored", "close_started", "closed", "absent", "release_started", "released")


class Stop(Exception):
    def __init__(self, reason):
        self.reason = reason


class CheckpointFailure(Stop):
    pass


def identity(proof, request):
    if not isinstance(proof, dict) or any(not isinstance(proof.get(key), str) or not proof[key] for key in IDENTITY):
        raise Stop("invalid_lease_proof")
    if proof["owner_thread"] != request.owner_thread or proof["run_id"] != request.run_id:
        raise Stop("lease_identity_mismatch")
    return {key: proof[key] for key in IDENTITY}


def operate(*, lease, request, remote, report, checkpoint, recover=False):
    """Return collected bytes, if any; never put raw device output in report.

    The caller must atomically persist report on every checkpoint and bound the
    transport before returning its bytes. Recovery uses the same request and
    persisted report, never acquires a lease or invokes admit, prepare or visit.
    """
    archive = None
    owner = {"owner_thread": request.owner_thread, "run_id": request.run_id}

    def save():
        try:
            checkpoint(report)
        except (Exception, KeyboardInterrupt):
            raise CheckpointFailure("checkpoint_failed") from None

    def set_error(reason):
        report["error"] = reason
        report["operations_passed"] = False
        report["recovery_required"] = True

    def status():
        try:
            proof = lease.status(request)
        except (Exception, KeyboardInterrupt):
            raise Stop("lease_status_unknown") from None
        if (not isinstance(proof, dict) or proof.get("ok") is not True
                or proof.get("operation") != "status" or proof.get("state") != "held"
                or proof.get("ownership_verified") is not True):
            raise Stop("lease_not_owned")
        observed = identity(proof, request)
        if report.get("identity") is not None and observed != report["identity"]:
            raise Stop("lease_identity_mismatch")
        return observed

    def call(operation):
        state["pending"] = operation
        if operation in ("prepare", "visit", "close"):
            state[operation + "_started"] = True
        save()
        status()
        try:
            code, stdout, stderr = remote(operation)
        except (Exception, KeyboardInterrupt):
            report["operations"].append({"operation": operation, "outcome": "unknown"})
            save()
            raise Stop(operation + "_unknown") from None
        valid = type(code) is int and isinstance(stdout, bytes) and isinstance(stderr, bytes) and code == 0
        if operation == "prepare" and valid and stdout == b"not_started\n":
            state["prepare_not_started"] = True
            state["pending"] = None
            report["operations"].append({"operation": operation, "outcome": "not_started"})
            save()
            raise Stop("prepare_not_started")
        if operation == "collect":
            valid = valid and 10 <= len(stdout) <= MAX_ARCHIVE_BYTES and stdout.startswith(b"\x1f\x8b\x08")
        else:
            valid = valid and stdout == MARKERS[operation]
        report["operations"].append({"operation": operation, "outcome": "ok" if valid else "failed"})
        if valid:
            flag = {"admit": "admitted", "prepare": "prepared", "visit": "visited", "collect": "collected",
                    "restore": "restored", "close": "closed", "absent": "absent"}[operation]
            state[flag] = True
            state["pending"] = None
        save()
        if not valid:
            raise Stop(operation + "_failed")
        return stdout

    def release():
        state["pending"] = "release"
        state["release_started"] = True
        save()
        status()
        try:
            proof = lease.release(request, native_restored_and_connections_closed=True)
        except (Exception, KeyboardInterrupt):
            raise Stop("release_unknown") from None
        flags = ("ok", "owner_and_token_checked", "lease_directory_absent_after_release",
                 "native_restored_and_connections_closed")
        if (not isinstance(proof, dict) or proof.get("operation") != "release" or proof.get("state") != "released"
                or any(proof.get(key) is not True for key in flags) or identity(proof, request) != report["identity"]):
            raise Stop("unverified_release")
        report["release"] = proof
        state["released"] = True
        state["pending"] = None
        report["recovery_required"] = False
        save()

    try:
        if recover:
            state = report.get("state")
            if (report.get("schema") != 1 or report.get("request") != owner or not isinstance(state, dict)
                    or not isinstance(report.get("operations"), list)
                    or any(type(state.get(flag)) is not bool for flag in FLAGS)
                    or (state["visited"] and not state["visit_started"])
                    or (state["visit_started"] and not state["prepare_started"])
                    or (state["prepare_not_started"] and (not state["prepare_started"]
                                                           or state["prepared"] or state["visit_started"]))
                    or (state["closed"] and not state["restored"])
                    or state["released"]):
                raise Stop("invalid_recovery_checkpoint")
            report["operations_passed"] = False
            observed = status()
            if report.get("identity") is None:
                if state["prepare_started"] or state["visit_started"]:
                    raise Stop("missing_acquisition_identity")
                report["identity"] = observed
                report["acquisition_unconfirmed"] = True
            save()
        else:
            if report:
                raise Stop("report_already_used")
            state = {flag: False for flag in FLAGS}
            state["pending"] = "acquire"
            report.update(schema=1, request=owner, state=state, operations=[], operations_passed=False,
                          recovery_required=True, identity=None)
            save()
            try:
                proof = lease.acquire(request)
            except (Exception, KeyboardInterrupt) as error:
                lease_error = getattr(lease, "LeaseError", None)
                details = getattr(error, "details", None)
                untouched = ("lease_directory_created", "private_token_created", "owner_record_created")
                if (isinstance(lease_error, type) and isinstance(error, lease_error)
                        and getattr(error, "code", None) == "lease_exists" and isinstance(details, dict)
                        and all(details.get(key) is False for key in untouched)):
                    state["pending"] = None
                    report["error"] = "lease_busy"
                    report["operations_passed"] = False
                    report["recovery_required"] = False
                    save()
                    return None
                raise Stop("acquisition_unknown") from None
            if (not isinstance(proof, dict) or proof.get("ok") is not True
                    or proof.get("operation") != "acquire" or proof.get("state") != "held"):
                raise Stop("unverified_acquisition")
            report["identity"] = identity(proof, request)
            report["acquisition"] = proof
            state["pending"] = None
            save()
            status()
            try:
                call("admit")
                call("prepare")
                call("visit")
                archive = call("collect")
            except CheckpointFailure:
                raise
            except Stop as error:
                set_error(error.reason)
                save()

        if state["prepare_started"]:
            if state["prepare_not_started"]:
                if not state["absent"]:
                    call("absent")
            else:
                if not state["restored"]:
                    call("restore")
                if not state["closed"]:
                    call("close")
                if not state["absent"]:
                    call("absent")
        release()
        report["operations_passed"] = (not recover and state["visited"] and state["collected"]
                                       and state["restored"] and state["closed"] and state["absent"]
                                       and state["released"] and "error" not in report)
        save()
    except CheckpointFailure as error:
        set_error(error.reason)
    except Stop as error:
        set_error(error.reason)
        try:
            save()
        except CheckpointFailure:
            set_error("checkpoint_failed")
    except (Exception, KeyboardInterrupt):
        set_error("unexpected_interruption")
        try:
            save()
        except CheckpointFailure:
            set_error("checkpoint_failed")
    return archive
