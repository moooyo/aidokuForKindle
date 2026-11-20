"""Run one non-UI batch on test-env using the shared Kindle owner/token lease."""

import argparse
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import signal
import subprocess
import sys
import tarfile
import tempfile


LEASE = Path("/tmp/codex-kindle-physical-test.lock")
FILES = {"aidoku-arm", "cover-parity", "r3-launch.sh"}
EXPECTED = ["installed_launcher_matches", "candidate_cli defaults_ok",
            "matched 16777216 RGB triples, 128 tails and 1400 rows (2798527 f32 sums)", "stage_removed"]


def load_payload(path):
    with path.open("rb") as stream:
        payload = stream.read(48 * 1024**2 + 1)
    if not 0 < len(payload) <= 48 * 1024**2:
        raise ValueError("Invalid compressed payload size")
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        for member in archive:
            if (member.name not in FILES or member.name in seen or not member.isreg()
                    or not 0 < member.size <= 64 * 1024**2 or member.mode & 0o7000):
                raise ValueError("Unexpected payload entry")
            seen.add(member.name)
    if seen != FILES:
        raise ValueError("Incomplete payload")
    return payload


def ssh_argv(config, alias, known_hosts):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", alias):
        raise ValueError("Expected the configured SSH host name")
    for path in (config, known_hosts):
        if not path.is_file() or path.stat().st_mode & 0o022:
            raise ValueError("A trusted SSH configuration and known-hosts file are required")
    flags = ["-F", str(config), "-T", "-o", "StrictHostKeyChecking=yes", "-o", "UpdateHostKeys=no",
             "-o", f"UserKnownHostsFile={known_hosts}", "-o", "GlobalKnownHostsFile=/dev/null",
             "-o", "BatchMode=" + ("no" if os.environ.get("SSH_ASKPASS_REQUIRE") == "force"
                                  and os.environ.get("SSH_ASKPASS") else "yes"),
             "-o", "NumberOfPasswordPrompts=1", "-o", "ConnectionAttempts=1", "-o", "ConnectTimeout=6",
             "-o", "ServerAliveInterval=4", "-o", "ServerAliveCountMax=2", "-o", "ForwardAgent=no",
             "-o", "ClearAllForwardings=yes", "-o", "ControlMaster=no", "-o", "ControlPath=none"]
    values = subprocess.check_output(["ssh", "-G", *flags, alias], text=True, timeout=5)
    resolved = dict(line.split(" ", 1) for line in values.splitlines() if " " in line)
    hostname = resolved["hostname"]
    if resolved["port"] != "22":
        hostname = f"[{hostname}]:{resolved['port']}"
    lookup = resolved.get("hostkeyalias", hostname)
    pin = subprocess.run(["ssh-keygen", "-F", lookup, "-f", str(known_hosts)], timeout=5,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if pin.returncode != 0:
        raise ValueError("The configured endpoint has no trusted host-key entry")
    return ["ssh", *flags, alias]


def exchange(command, data, timeout):
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          start_new_session=True) as child:
        try:
            stdout, stderr = child.communicate(data, timeout=timeout)
        except BaseException:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
            raise
    return child.returncode, stdout, stderr


def request_device(command, data, timeout):
    status, stdout, _ = exchange(command, data, timeout)
    lines = stdout.decode("ascii", "replace").splitlines()
    allowed = {*EXPECTED, "stage_absent", "native_idle stage_absent"}
    safe = [line if line in allowed or re.fullmatch(r"refused [a-z_]{1,40}", line)
            else "unexpected_output" for line in lines[:12]]
    return status, safe


def operate(lease, request, device, report):
    report["acquisition"] = lease.acquire(request)
    attempted = False
    cleared = False

    def call(mode):
        if not lease.status(request)["ownership_verified"]:
            raise RuntimeError("Device lease ownership changed")
        status, lines = device(mode)
        report["operations"].append({"operation": mode, "status": status, "observed": lines})
        return status, lines

    try:
        if call("check") != (0, ["native_idle stage_absent"]):
            return False
        attempted = True
        passed = call("run") == (0, EXPECTED)
    finally:
        try:
            if attempted:
                cleared = call("close") == (0, ["stage_absent"])
                if cleared:
                    final_idle = call("check") == (0, ["native_idle stage_absent"])
        finally:
            if not attempted or cleared:
                report["release"] = lease.release(request, native_restored_and_connections_closed=True)
            else:
                report["recovery_required"] = True
    return passed and cleared and final_idle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("work", "lease-tool", "ssh-config", "known-hosts"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--alias", required=True)
    parser.add_argument("--owner", required=True)
    args = parser.parse_args()
    if sys.platform != "linux" or not Path("/opt/kindle-toolchain/ubuntu-run").is_file():
        parser.error("Use the test-env host, not ubuntu-run's private /tmp")
    os.umask(0o077)
    ssh = ssh_argv(args.ssh_config.resolve(), args.alias, args.known_hosts.resolve())
    work = args.work.resolve(strict=True)
    payload = load_payload(work / "hardware-smoke.tar.gz")
    body = Path(__file__).with_name("hardware-smoke.sh").read_text()
    spec = importlib.util.spec_from_file_location("device_lease", args.lease_tool.resolve(strict=True))
    lease = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = lease
    spec.loader.exec_module(lease)
    lease.PROJECT = "Aidoku"
    label = secrets.token_hex(8)
    batch = Path(tempfile.mkdtemp(dir=work / "reports", prefix="physical-" + label + "-"))
    request = lease.LeaseRequest(LEASE, batch / "token", args.owner, "aidoku-hw-" + label)
    report = {"scope": "Physical CLI and pixel-kernel smoke, no UI or timing", "label": label,
              "operations": [], "passed": False}

    def device(mode):
        command = shlex.join(["sh", "-c", body, "aidoku-hw", mode, label])
        return request_device([*ssh, command], payload if mode == "run" else b"", 90 if mode == "run" else 15)

    def interrupted(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        report["passed"] = operate(lease, request, device, report)
    except (Exception, KeyboardInterrupt) as error:
        report["error"] = getattr(error, "code", type(error).__name__)
    finally:
        (batch / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(batch / "result.json")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
