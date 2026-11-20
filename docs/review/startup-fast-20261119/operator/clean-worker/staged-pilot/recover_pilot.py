"""Bounded closeout of an existing owned pilot; never acquire a lease or start an app."""

import argparse
import json
from pathlib import Path
import re
import shlex
import sys

from run_pilot import LEASE, exchange, ssh_argv, transport_input


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("attempt", type=Path)
    parser.add_argument("--owner", required=True)
    args = parser.parse_args()
    attempt = args.attempt.resolve(strict=True)
    batch = attempt.parent
    plan = json.loads((batch / "plan.json").read_text())
    work = batch.parent
    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(work / "tools"))
    import device_lease as lease
    lease.PROJECT = "Aidoku"
    label = plan["label"]
    assert re.fullmatch(r"[a-f0-9]{16}", label) and not sys.flags.optimize
    request = lease.LeaseRequest(LEASE, attempt / "token", args.owner, "aidoku-pilot-" + label)
    assert lease.status(request)["ownership_verified"]
    cfg = Path("/root/.config/kindle-physical")
    ssh = ssh_argv(cfg / "ssh_config", "kindle-physical", cfg / "known_hosts")
    body = (here / "device.sh").read_text()
    stock = (here.parent / "hardware-smoke.sh").read_text()
    steps = []

    def call(mode):
        assert lease.status(request)["ownership_verified"]
        command, data = transport_input(shlex.join(["sh", "-c", body, "pilot", mode, label, "-", stock]), b"")
        status, stdout, stderr = exchange([*ssh, command], data, 85 if mode == "collect" else 38)
        steps.append({"mode": mode, "status": status, "observed": [] if mode == "collect" else stdout.decode().splitlines(),
                      "reasons": re.findall(r"^refused ([a-z_]+)$", stderr.decode(errors="replace"), re.M)})
        if status:
            raise RuntimeError(mode)
        return stdout

    record = {"steps": steps}
    archive = None
    try:
        try:
            call("restore")
            archive = call("collect")
        finally:
            if call("close").splitlines()[-1:] == [b"stage_absent"]:
                try:
                    call("admit")
                finally:
                    record["release"] = lease.release(request, native_restored_and_connections_closed=True)
    finally:
        if archive is not None and not (attempt / "output.tar.gz").exists():
            (attempt / "output.tar.gz").write_bytes(archive)
        (attempt / "recovery.json").write_text(json.dumps(record, indent=2) + "\n")
        print([(step["mode"], step["status"], step["reasons"]) for step in steps], "released:", "release" in record)


if __name__ == "__main__":
    main()
