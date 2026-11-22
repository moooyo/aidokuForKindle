#!/usr/bin/python3
"""A metadata-only responder for the host experiment. No driver or added sleep."""

import os
from pathlib import Path
import sys


def main():
    if sys.stdin.buffer.read(33) != b"KJPR0001" + bytes(24):
        raise ValueError("Expected precisely one empty operation-zero request")
    with (Path(os.environ["AIDOKU_JPEG_TRIAL_CASE"]) / "metadata.receipt").open("xb") as receipt:
        receipt.write(b"metadata-only\n")
    sys.stdout.buffer.write(b"KJPG0001" + bytes(88))


if __name__ == "__main__":
    main()
