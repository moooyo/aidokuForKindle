#!/usr/bin/env python3
"""Check hardware JPEG/PNG admission without opening any video device.

Run on ssh test-env. A preload guard terminates the process before a video
device open or any ioctl; baseline cases must reach that guard, while rejected
inputs must produce a structured failure before reaching it.
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import zlib

from PIL import Image


GUARD = r'''
#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <stdarg.h>
#include <string.h>
#include <unistd.h>

int open(const char *path, int flags, ...) {
    if (!strncmp(path, "/dev/video", 10)) {
        static const char message[] = "VIDEO_OPEN_INTERCEPTED\n";
        write(STDERR_FILENO, message, sizeof(message) - 1);
        _exit(97);
    }
    int (*next)(const char *, int, ...) = dlsym(RTLD_NEXT, "open");
    mode_t mode = 0;
    if (flags & O_CREAT) {
        va_list arguments;
        va_start(arguments, flags);
        mode = va_arg(arguments, int);
        va_end(arguments);
    }
    return next(path, flags, mode);
}

int ioctl(int fd, unsigned long request, ...) {
    (void)fd; (void)request;
    static const char message[] = "IOCTL_INTERCEPTED\n";
    write(STDERR_FILENO, message, sizeof(message) - 1);
    _exit(98);
}
'''


def encoded(mode: str, progressive: bool = False, subsampling: int = 1) -> bytes:
    image = Image.new(mode, (64, 64))
    if mode == "L":
        image.putdata([(x * 11 + y * 7) % 256 for y in range(64) for x in range(64)])
    else:
        image.putdata([(x * 4, y * 4, (x + y) * 2) for y in range(64) for x in range(64)])
    output = io.BytesIO()
    options = {"subsampling": subsampling} if mode == "RGB" else {}
    image.save(output, format="JPEG", quality=90, progressive=progressive, **options)
    return output.getvalue()


def encoded_png(mode: str, transparent: bool = False) -> bytes:
    image = Image.new(mode, (64, 64), 73 if mode == "L" else (17, 93, 201))
    output = io.BytesIO()
    options = {"transparency": 73 if mode == "L" else (17, 93, 201)} if transparent else {}
    image.save(output, format="PNG", **options)
    return output.getvalue()


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def main() -> None:
    if sys.platform != "linux":
        raise SystemExit("Run verification through ssh test-env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path,
                        default=Path(__file__).with_name("native-codec-probe.c"))
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    binary = args.output_dir / "codec-admission"
    guard_source = args.output_dir / "no-video-open.c"
    guard = args.output_dir / "no-video-open.so"
    guard_source.write_text(GUARD)
    subprocess.run(["gcc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                    str(args.source), "-o", str(binary)], check=True)
    subprocess.run(["gcc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                    "-shared", "-fPIC", str(guard_source), "-ldl", "-o", str(guard)], check=True)

    baseline = encoded("RGB")
    progressive = encoded("RGB", True)
    metadata = b"Embedded thumbnail marker: \xff\xc2\x00\x11"
    with_metadata = baseline[:2] + b"\xff\xe2" + struct.pack(">H", len(metadata) + 2) + metadata + baseline[2:]
    Image.open(io.BytesIO(with_metadata)).load()
    sof = baseline.index(b"\xff\xc0")
    sof_length = int.from_bytes(baseline[sof + 2:sof + 4], "big")
    progressive_sof = progressive.index(b"\xff\xc2")
    cases = [
        ("baseline_422_rgb_admitted", baseline, None, 64),
        ("baseline_444_rgb_admitted", encoded("RGB", subsampling=0), None, 64),
        ("baseline_gray_admitted", encoded("L"), None, 64),
        ("baseline_420_rgb_rejected", encoded("RGB", subsampling=2),
         "unsupported_jpeg_sampling", 64),
        ("progressive_rgb_rejected", progressive, "unsupported_progressive_jpeg", 64),
        ("progressive_gray_rejected", encoded("L", True), "unsupported_progressive_jpeg", 64),
        ("metadata_marker_is_not_progressive", with_metadata, None, 64),
        ("truncated_frame_header", baseline[:sof + 5], "invalid_jpeg_header", 64),
        ("short_segment_length", b"\xff\xd8\xff\xe1\x00\x01", "invalid_jpeg_header", 64),
        ("segment_exceeds_input", b"\xff\xd8\xff\xe1\xff\xff", "invalid_jpeg_header", 64),
        ("missing_frame", b"\xff\xd8\xff\xd9", "invalid_jpeg_header", 64),
        ("wrong_dimensions", baseline, "jpeg_dimensions_mismatch", 128),
        ("unqualified_sequential_coding", baseline[:sof + 1] + b"\xc1" + baseline[sof + 2:],
         "unsupported_jpeg_coding", 64),
        ("duplicate_frame_header", baseline[:sof] + baseline[sof:sof + 2 + sof_length] + baseline[sof:],
         "invalid_jpeg_header", 64),
    ]
    for marker in (0xC6, 0xCA, 0xCE):
        changed = progressive[:progressive_sof + 1] + bytes([marker]) + progressive[progressive_sof + 2:]
        cases.append((f"progressive_marker_{marker:02x}", changed, "unsupported_progressive_jpeg", 64))
    for label, sampling in [("411", 0x41), ("440", 0x12), ("410", 0x42)]:
        changed = baseline[:sof + 11] + bytes([sampling]) + baseline[sof + 12:]
        cases.append((f"unqualified_sampling_{label}", changed, "unsupported_jpeg_sampling", 64))

    cases = [(*case, "jpeg") for case in cases]
    plain_png = encoded_png("RGB")
    png_metadata = (plain_png[:33] + png_chunk(b"tEXt", b"note\0embedded tRNS marker")
                    + plain_png[33:])
    Image.open(io.BytesIO(png_metadata)).load()
    palette = Image.new("P", (64, 64))
    palette.putpalette([channel for value in range(256) for channel in (value, 255 - value, value)])
    palette_buffer = io.BytesIO()
    palette.save(palette_buffer, format="PNG", transparency=bytes(range(256)))
    cases.extend([
        ("png_rgb_admitted", plain_png, None, 64, "png"),
        ("png_gray_admitted", encoded_png("L"), None, 64, "png"),
        ("png_palette_trns_admitted", palette_buffer.getvalue(), None, 64, "png"),
        ("png_gray_key_rejected", encoded_png("L", True),
         "unsupported_png_color_key_transparency", 64, "png"),
        ("png_rgb_key_rejected", encoded_png("RGB", True),
         "unsupported_png_color_key_transparency", 64, "png"),
        ("png_embedded_trns_is_not_chunk", png_metadata, None, 64, "png"),
        ("png_truncated_chunk_rejected", plain_png[:37], "invalid_png_header", 64, "png"),
        ("png_wrong_dimensions_rejected", plain_png, "invalid_png_header", 128, "png"),
    ])

    environment = dict(os.environ, AIDOKU_REVIEWED_CODEC_TRIAL="1", LD_PRELOAD=str(guard))
    results = []
    for name, payload, expected, width, codec in cases:
        path = args.output_dir / f"{name}.{'jpg' if codec == 'jpeg' else 'png'}"
        path.write_bytes(payload)
        prefix = args.output_dir / f"{name}-output"
        result = subprocess.run([str(binary), "--reviewed-single-frame", codec, str(path),
                                 str(prefix), str(width), "64", "gray" if codec == "jpeg" else "auto"], env=environment,
                                capture_output=True, text=True, timeout=10)
        if expected is None:
            assert result.returncode == 97 and "VIDEO_OPEN_INTERCEPTED" in result.stderr, (
                name, result.stdout, result.stderr)
            stage = "admitted_video_open_intercepted"
        else:
            assert result.returncode == 1, (name, result.stdout, result.stderr)
            report = json.loads(result.stdout)
            assert report["stage"] == expected and report["ioctl_completed"] is False, report
            expected_errno = errno.ENOTSUP if expected.startswith("unsupported_") else errno.EINVAL
            assert report["errno"] == expected_errno, report
            assert "VIDEO_OPEN_INTERCEPTED" not in result.stderr and "IOCTL_INTERCEPTED" not in result.stderr
            stage = report["stage"]
        assert not prefix.with_suffix(".plane0.bin").exists()
        results.append({"case": name, "passed": True, "stage": stage})
    report = {"schema": 1, "source_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
              "physical_device_opened": False, "ioctl_executed": False,
              "passed": len(results), "checks": results}
    (args.output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
