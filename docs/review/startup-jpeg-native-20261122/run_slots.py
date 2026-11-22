"""Drive consecutive independent visits; never hold or wait for a device lease here."""

import argparse
import getpass
import json
from pathlib import Path
import resource
import subprocess
import sys
import warnings


def run(work, first, last, secret, invoke=subprocess.run):
    if not 1 <= first <= last <= 14:
        raise ValueError("Invalid slot range")
    host = work / "plan" / "host.py"
    owner = json.loads((work / "plan" / "plan.json").read_text())["owner"]
    for slot in range(first, last + 1):
        result = invoke([sys.executable, "-B", str(host), "--run", str(work), "--owner", owner,
                         "--slot", str(slot), "--secret-fd", "0"], input=secret,
                        cwd=work, check=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("work", type=Path)
    parser.add_argument("first", type=int)
    parser.add_argument("last", type=int)
    args = parser.parse_args()
    if not 1 <= args.first <= args.last <= 14 or not sys.stdin.isatty():
        parser.error("A valid slot range and private terminal are required")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        secret = bytearray(getpass.getpass("Device authentication: ").encode())
    try:
        if not 0 < len(secret) <= 1024 or any(value in secret for value in (0, 10, 13)):
            parser.error("Invalid device authentication")
        secret.append(10)
        sys.exit(run(args.work.resolve(strict=True), args.first, args.last, secret))
    finally:
        secret[:] = b"\x00" * len(secret)
