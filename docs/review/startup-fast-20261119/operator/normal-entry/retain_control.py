"""Retain only the three installed Aidoku programs in one read-only SSH connection."""

import argparse
from contextlib import contextmanager
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "clean-worker"))
from run_hardware_smoke import LEASE, exchange, ssh_argv


FILES = {"app/aidoku": 96 * 1024**2, "launch.sh": 96 * 1024, "app/aidoku-startup-portrait": 256 * 1024}
SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


@contextmanager
def transition():
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, SIGNALS)
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def capture(lease, request, remote, record):
    acquired = False
    try:
        with transition():
            record["acquisition"] = lease.acquire(request)
            acquired = True
        if not lease.status(request)["ownership_verified"]:
            raise RuntimeError("lease_ownership_changed")
        status, stdout, stderr = remote()
        record["ssh_exit"] = status
        if status:
            reason = re.findall(rb"^refused ([a-z_]{1,32})$", stderr, re.M)
            raise RuntimeError(reason[-1].decode() if reason else "ssh_or_transfer_error")
        return stdout
    finally:
        if acquired:
            with transition():
                record["release"] = lease.release(request, native_restored_and_connections_closed=True)
                if not record["release"].get("owner_and_token_checked") or not record["release"].get("only_owned_owner_json_removed_then_rmdir"):
                    raise RuntimeError("unverified_release")


def arm(data):
    return len(data) >= 52 and data[:7] == b"\x7fELF\x01\x01\x01" and data[18:20] == b"\x28\x00"


def reference(path, limit):
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= limit:
        raise ValueError("invalid_reference")
    return path.read_bytes()


def accept(payload, launcher, portrait, batch):
    stream = io.BytesIO(payload)
    if stream.readline(32) != b"AIDOKU-READ-1\n":
        raise ValueError("invalid_stream")
    selected, modes = {}, {}
    for name, limit in FILES.items():
        match = re.fullmatch(re.escape(name.encode()) + rb" ([1-9][0-9]{0,8}) ([0-7]{3})\n", stream.readline(80))
        if not match or int(match[1]) > limit or not int(match[2], 8) & 0o111:
            raise ValueError("invalid_file_header")
        size = int(match[1])
        selected[name] = stream.read(size)
        if len(selected[name]) != size:
            raise ValueError("truncated_file")
        modes[name] = match[2].decode()
    if (stream.read(17) != b"AIDOKU-READ-END\n" or not arm(selected["app/aidoku"])
            or not arm(selected["app/aidoku-startup-portrait"]) or not selected["launch.sh"].startswith(b"#!/bin/sh\n")):
        raise ValueError("incomplete_or_invalid_observation")
    mismatches = []
    if selected["launch.sh"] != launcher:
        mismatches.append("control_launcher_differs")
    if selected["app/aidoku-startup-portrait"] != portrait:
        mismatches.append("selected_portrait_helper_differs")
    destination = batch / ("not-admitted" if mismatches else "control")
    staging = Path(tempfile.mkdtemp(dir=batch, prefix=".publishing-"))
    try:
        (staging / "app").mkdir(mode=0o700)
        for name, contents in selected.items():
            with (staging / name).open("xb") as target:
                target.write(contents)
        (staging / "modes.json").write_text(json.dumps(modes, indent=2) + "\n")
        if destination.exists() or destination.is_symlink():
            raise ValueError("existing_control")
        staging.rename(destination)
    finally:
        if staging.is_dir():
            shutil.rmtree(staging)
    if mismatches:
        raise ValueError(",".join(mismatches))
    return {name: {"bytes": len(selected[name]), "original_mode": modes[name]} for name in selected}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("work", "lease-tool", "launcher", "portrait"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if sys.platform != "linux" or not Path("/opt/kindle-toolchain/ubuntu-run").is_file():
        parser.error("Use the test-env host, not the chroot")
    os.umask(0o077)
    work = args.work
    if (work.resolve(strict=True) != work or not work.is_dir() or work.stat().st_mode & 0o077
            or shutil.disk_usage(work).free < 128 * 1024**2):
        parser.error("Use an existing private absolute worker directory with sufficient space")
    launcher = reference(args.launcher, FILES["launch.sh"])
    portrait = reference(args.portrait, FILES["app/aidoku-startup-portrait"])
    if not launcher.startswith(b"#!/bin/sh\n") or not arm(portrait):
        parser.error("Invalid reference launcher or ARM helper")
    cfg = Path("/root/.config/kindle-physical")
    ssh = ssh_argv(cfg / "ssh_config", "kindle-physical", cfg / "known_hosts")
    program = (HERE / "read_control.sh").read_bytes()
    spec = importlib.util.spec_from_file_location("device_lease", args.lease_tool.resolve(strict=True))
    lease = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = lease
    spec.loader.exec_module(lease)
    lease.PROJECT = "Aidoku"
    if args.check_only:
        print("Local references, strict SSH configuration and lease module ready; no device operation.")
        return 0
    batch = Path(tempfile.mkdtemp(dir=work, prefix="observed-"))
    request = lease.LeaseRequest(LEASE, batch / "token", args.owner, "aidoku-" + batch.name.replace("_", "-"))
    record = {"scope": "Read-only capture of installed program bytes and modes, not proof of an R3 release"}
    def interrupted(*_):
        raise KeyboardInterrupt
    for signum in SIGNALS:
        signal.signal(signum, interrupted)
    try:
        data = capture(lease, request, lambda: exchange([*ssh, "exec sh -s"], program, 90), record)
        record["files"] = accept(data, launcher, portrait, batch)
    except (Exception, KeyboardInterrupt) as error:
        record["failure"] = getattr(error, "code", str(error) if type(error) in (ValueError, RuntimeError) else type(error).__name__)
    finally:
        (batch / "receipt.json").write_text(json.dumps(record, indent=2) + "\n")
    print(batch / "receipt.json")
    return 0 if "files" in record else 1


if __name__ == "__main__":
    raise SystemExit(main())
