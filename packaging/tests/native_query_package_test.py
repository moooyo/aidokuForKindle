#!/usr/bin/env python3
"""Keep the firmware-ABI helper separate from the static application contract."""

import importlib.util
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("package_verifier", ROOT / "scripts/verify-kindle-package.py")
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


def executable(dynamic=False):
    data = bytearray(320)
    data[:7] = b"\x7fELF\x01\x01\x01"
    programs = [1, 2, 3] if dynamic else [1]
    struct.pack_into("<HHIIIIIHHHHHH", data, 16, 3 if dynamic else 2, 40, 1,
                     0x1000, 52, 0, 0x5000400, 52, 32, len(programs), 0, 0, 0)
    interpreter = b"/lib/ld-linux-armhf.so.3\0"
    for index, kind in enumerate(programs):
        struct.pack_into("<IIIIIIII", data, 52 + 32 * index, kind, 256, 0, 0,
                         len(interpreter) if kind == 3 else 1, 1, 5, 4096)
    data[256:256 + len(interpreter)] = interpreter
    return data


@unittest.skipUnless(sys.platform.startswith("linux"), "Run on test-env")
class QueryPackageTests(unittest.TestCase):
    def test_main_application_and_jpeg_worker_still_require_static_linking(self):
        self.assertTrue(VERIFIER.elf_identity(executable())["static"])
        with self.assertRaises(ValueError):
            VERIFIER.elf_identity(executable(dynamic=True))

    def test_only_the_query_helper_accepts_the_firmware_loader(self):
        identity = VERIFIER.elf_identity(executable(dynamic=True), static=False)
        self.assertFalse(identity["static"])
        self.assertEqual(identity["interpreter"], "/lib/ld-linux-armhf.so.3")
        self.assertEqual(identity["float_abi"], "hard")
        with self.assertRaises(ValueError):
            VERIFIER.elf_identity(executable(), static=False)

    def test_query_helper_rejects_another_interpreter_or_float_abi(self):
        data = executable(dynamic=True)
        data[261] = ord("X")
        with self.assertRaises(ValueError):
            VERIFIER.elf_identity(data, static=False)
        data = executable(dynamic=True)
        struct.pack_into("<I", data, 36, 0x5000200)
        with self.assertRaises(ValueError):
            VERIFIER.elf_identity(data, static=False)

    def test_interpreter_and_program_headers_must_fit_the_file(self):
        data = executable(dynamic=True)
        struct.pack_into("<I", data, 52 + 2 * 32 + 4, len(data) + 1)
        with self.assertRaises(ValueError):
            VERIFIER.elf_identity(data, static=False)
        data = executable(dynamic=True)
        struct.pack_into("<I", data, 28, len(data))
        with self.assertRaises(ValueError):
            VERIFIER.elf_identity(data, static=False)


if __name__ == "__main__":
    unittest.main()
