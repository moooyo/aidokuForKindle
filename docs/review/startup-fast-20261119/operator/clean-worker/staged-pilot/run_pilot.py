"""Operate one frozen staged-foreground pilot from the test-env host."""

import argparse
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shlex
import signal
import sys
import tarfile
import tempfile

from cohort import check_protocol

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from run_hardware_smoke import LEASE, exchange, ssh_argv


FIXED = {"candidate/app/aidoku", "candidate/app/aidoku-startup-portrait", "control-launcher",
         "baseline/launch.sh", "candidate/launch.sh", "cases", "regime", "seed/library.sqlite3"}


def transport_input(shell, payload):
    header = shell.encode("utf-8")
    if not 0 < len(header) < 32768:
        raise ValueError("Invalid pilot shell size")
    return f'exec sh -c "$(dd bs=1 count={len(header)} 2>/dev/null)"', header + payload


def frozen(batch):
    plan = json.loads((batch / "plan.json").read_bytes())
    label, regime = plan["label"], plan["regime"]
    if not re.fullmatch(r"[a-f0-9]{16}", label) or regime not in ("warm", "cold") or batch.name != f"{regime}-{label}":
        raise ValueError("Invalid pilot identity")
    cases = check_protocol(plan)
    if (batch / "started").exists():
        raise ValueError("Previously operated plan")
    with (batch / "payload.tar.gz").open("rb") as stream:
        payload = stream.read(64 * 1024**2 + 1)
    if len(payload) > 64 * 1024**2:
        raise ValueError("Oversized upload")
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        for member in archive:
            if (not member.isreg() or member.name in seen or member.mode & 0o7000 or not 0 < member.size <= 64 * 1024**2
                    or (member.name not in FIXED and not re.fullmatch(r"seed/(?:[A-Za-z0-9_-]+/)+[A-Za-z0-9_.-]+\.jpg", member.name))):
                raise ValueError("Unexpected pilot payload")
            seen.add(member.name)
        if not FIXED <= seen or len(seen - FIXED) != 6:
            raise ValueError("Incomplete pilot")
        for leaf, expected in [("cases", " ".join(cases)), ("regime", regime)]:
            if archive.extractfile(leaf).read() != (expected + "\n").encode():
                raise ValueError("Payload and plan differ")
    return plan, payload


def operate(lease, request, batch, plan, remote, report):
    report["acquisition"] = lease.acquire(request)
    staged = False
    prepared = False
    cleared = False
    completed = []
    archive = None

    def call(mode, name="-"):
        if not lease.status(request)["ownership_verified"]:
            raise RuntimeError("Device lease ownership changed")
        code, stdout, stderr = remote(mode, name)
        reasons = re.findall(rb"^refused ([a-z_]{1,48})$", stderr, re.M)
        if not reasons and code == 255:
            reasons = [next((tag for text, tag in [
                (b"exec request failed", b"ssh_exec_refused"), (b"Permission denied", b"ssh_auth"),
                (b"Host key verification failed", b"ssh_pin"), (b"timed out", b"ssh_timeout"),
                (b"Connection refused", b"ssh_unavailable")] if text in stderr), b"ssh_transport")]
        lines = [] if mode == "collect" else stdout.decode("ascii", "replace").splitlines()
        observed = [line if re.fullmatch(r"[a-z_]{1,48}", line) else "unexpected_output" for line in lines[:8]]
        report["operations"].append({"mode": mode, "case": name, "status": code, "observed": observed,
                                     "reason": reasons[0].decode() if reasons else None})
        if code:
            raise RuntimeError(f"{mode}/{name} did not complete")
        return stdout if mode == "collect" else observed

    try:
        with (batch / "started").open("x") as stream:
            stream.write(request.run_id + "\n")
        if call("admit") != ["admitted"]:
            raise RuntimeError("Native preflight not accepted")
        staged = True
        if call("prepare") != ["prepared"]:
            raise RuntimeError("Stage not accepted")
        prepared = True
        for name in plan["cases"]:
            if call("case", name) != ["case_completed"]:
                raise RuntimeError("Incomplete case")
            completed.append(name)
    except (Exception, KeyboardInterrupt) as error:
        report["case_error"] = str(error) if type(error) is RuntimeError else type(error).__name__
    finally:
        try:
            if staged:
                try:
                    try:
                        report["restoration"] = call("restore")
                    except (Exception, KeyboardInterrupt) as error:
                        report["restore_error"] = type(error).__name__
                    if prepared:
                        try:
                            archive = call("collect")
                        except (Exception, KeyboardInterrupt) as error:
                            report["collect_error"] = type(error).__name__
                finally:
                    cleared = call("close")[-1:] == ["stage_absent"]
                    if cleared:
                        try:
                            report["final_native_idle"] = call("admit") == ["admitted"]
                        except (Exception, KeyboardInterrupt):
                            report["final_native_idle"] = False
        finally:
            if not staged or cleared:
                report["release"] = lease.release(request, native_restored_and_connections_closed=True)
            else:
                report["recovery_required"] = True
    report["completed"] = completed
    report["operations_passed"] = (archive is not None and completed == plan["cases"] and report.get("final_native_idle") is True
                                   and report.get("restoration") == ["protected_state_equal", "stock_body_equal", "native_restored"])
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", type=Path)
    for key in ("lease-tool", "ssh-config", "known-hosts"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--alias", required=True)
    parser.add_argument("--owner", required=True)
    args = parser.parse_args()
    if sys.platform != "linux" or not Path("/opt/kindle-toolchain/ubuntu-run").is_file():
        parser.error("Use the worker host, not the chroot")
    os.umask(0o077)
    batch = args.batch.resolve(strict=True)
    plan, upload = frozen(batch)
    ssh = ssh_argv(args.ssh_config.resolve(), args.alias, args.known_hosts.resolve())
    here = Path(__file__).resolve().parent
    body = (here / "device.sh").read_text()
    stock = (here.parent / "hardware-smoke.sh").read_text()
    spec = importlib.util.spec_from_file_location("device_lease", args.lease_tool.resolve(strict=True))
    lease = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = lease
    spec.loader.exec_module(lease)
    lease.PROJECT = "Aidoku"
    existing = [path for path in batch.glob("attempt-*") if (path / "token").is_file()]
    if existing:
        raise ValueError("A previous owner already operated this plan")
    attempt = Path(tempfile.mkdtemp(dir=batch, prefix="attempt-"))
    for path in (here / "device.sh", here / "run_pilot.py", here.parent / "hardware-smoke.sh", here.parent / "run_hardware_smoke.py"):
        (attempt / path.name).write_bytes(path.read_bytes())
    request = lease.LeaseRequest(LEASE, attempt / "token", args.owner, "aidoku-pilot-" + plan["label"])
    report = {"scope": plan["origin"], "regime": plan["regime"], "operations": []}

    def remote(mode, name):
        command = shlex.join(["sh", "-c", body, "pilot", mode, plan["label"], name, stock])
        timeout = {"prepare": 150, "case": 50, "collect": 100, "restore": 45, "close": 45, "admit": 15}[mode]
        request, data = transport_input(command, upload if mode == "prepare" else b"")
        outcome = exchange([*ssh, request], data, timeout)
        if mode in ("prepare", "case", "close"):
            print(mode, name, outcome[0], flush=True)
        return outcome

    def interrupted(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        archive = operate(lease, request, batch, plan, remote, report)
        if archive is not None:
            if len(archive) > 48 * 1024**2:
                raise ValueError("Unexpected output size")
            (attempt / "output.tar.gz").write_bytes(archive)
    except (Exception, KeyboardInterrupt) as error:
        report["error"] = getattr(error, "code", type(error).__name__)
    finally:
        (attempt / "operations.json").write_text(json.dumps(report, indent=2) + "\n")
    print(attempt / "operations.json")
    return 0 if report.get("operations_passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
