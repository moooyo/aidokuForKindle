#!/usr/bin/env python3
"""Measure reader snapshot tradeoffs only on the designated remote worker."""

import argparse
import json
import os
import platform
import statistics
import subprocess
import time
from pathlib import Path


def run_json(binary, *arguments):
    started = time.perf_counter_ns()
    result = subprocess.run(
        [str(binary), *map(str, arguments)],
        check=True,
        text=True,
        capture_output=True,
    )
    elapsed = (time.perf_counter_ns() - started) // 1000
    record = json.loads(result.stdout)
    record["external_process_us"] = elapsed
    if result.stderr:
        record["stderr"] = result.stderr
    return record


def distribution(values):
    ordered = sorted(values)
    return {
        "samples": len(ordered),
        "median": statistics.median(ordered),
        "p95": ordered[min(len(ordered) - 1, (len(ordered) * 95 + 99) // 100 - 1)],
        "min": ordered[0],
        "max": ordered[-1],
    }


def rotated_order(configurations, sample):
    offset = sample % len(configurations)
    order = configurations[offset:] + configurations[:offset]
    return list(reversed(order)) if sample % 2 else order


def zstd_supplement(args):
    if args.reference_report is None:
        raise ValueError("The zstd supplement requires the preserved reference report")
    if args.output.resolve() == args.reference_report.resolve():
        raise ValueError("The zstd supplement must not overwrite the preserved report")
    reference = json.loads(args.reference_report.read_text(encoding="utf-8"))
    report = {
        "kind": "host-only-zstd-supplement",
        "environment": {"platform": platform.platform(), "machine": platform.machine(), "logical_cpus": os.cpu_count()},
        "build_metadata": json.loads(args.metadata.read_text(encoding="utf-8")) if args.metadata else None,
        "system_zstd": run_json(args.binary, "zstd-info"),
        "method": {
            "execution": "Serial interleaved codec order, one fresh process per operation sample; no concurrent build or benchmark",
            "storage": "Warm OS page cache on the designated x86_64 host; no physical Kindle flash measurement",
            "codec_only": "File opening/reading, exact bounded identity, advertised output length, one complete frame, required frame checksum, decompression and context release",
            "initialization": "dlopen, public symbol resolution, dladdr and version inspection happen before the codec timer and are reported separately for every fresh process; context creation/release remain inside the codec timer",
            "initialization_inclusive": "elapsed_plus_library_init_us is the per-sample sum of codec-only elapsed_us and that process's initialization_us; process wall time also includes oracle hashing, RGB expansion and process lifetime",
            "encode": "encode-only times encoding and context creation/release; its exact-pixel roundtrip oracle runs after the encoder timer",
            "sync_write": "sync-write reads and verifies the preencoded payload before timing only file write, file sync, atomic rename and directory sync",
            "identity": "Same source SHA-256, full render metadata, dimensions and exact production pixel oracle for PNG and zstd",
            "persistence_schedule": "A displayed-frame snapshot can be encoded when closing or serializing; these per-snapshot costs are not assumed to block every page turn",
            "scope": "The existing processed PNG remains present. This supplements zlib/packed measurements and does not by itself qualify another codec or snapshot lifecycle for production.",
        },
        "reference_report": str(args.reference_report),
        "preparation": run_json(args.binary, "prepare-zstd", args.data, args.source, args.jpeg_fixtures),
        "audit": run_json(args.binary, "audit-zstd", args.data),
        "reopen": [],
        "encoding": [],
        "sync_writing": [],
    }
    cases = report["preparation"]["cases"]
    reference_cases = {case["name"]: case for case in reference["preparation"]["cases"]}
    for case in cases:
        previous = reference_cases[case["name"]]
        for field in ["dimensions", "pixel_sha256", "source_sha256", "source_bytes", "pixels"]:
            if case[field] != previous[field]:
                raise ValueError(f"Reference fixture differs: {case['name']}/{field}")
        if case["codec_bytes"]["png"] != previous["codec_bytes"]["png"]:
            raise ValueError(f"Reference PNG representation differs: {case['name']}")
    report["checks"] = {"same_reference_source_and_pixel_hashes": True, "same_reference_png_bytes": True}
    if args.qualify_only:
        report["qualification_only"] = True
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"output": str(args.output), "audit": report["audit"], "checks": report["checks"]}, indent=2))
        return
    codecs = ["png", "zstd1", "zstd3"]
    configurations = [(case["name"], codec) for case in cases for codec in codecs]
    for name, codec in configurations:
        run_json(args.binary, "reopen", args.data, name, codec)
        run_json(args.binary, "encode-only", args.data, name, codec)
        run_json(args.binary, "sync-write", args.data, name, codec)
    for sample in range(args.reopen_samples):
        for name, codec in rotated_order(configurations, sample):
            record = run_json(args.binary, "reopen", args.data, name, codec)
            initialization = record.get("codec_initialization") or {}
            record["library_init_us"] = initialization.get("initialization_us", 0)
            record["elapsed_plus_library_init_us"] = record["elapsed_us"] + record["library_init_us"]
            record["sample"] = sample
            report["reopen"].append(record)
    for sample in range(args.write_samples):
        for name, codec in rotated_order(configurations, sample):
            record = run_json(args.binary, "encode-only", args.data, name, codec)
            initialization = record.get("codec_initialization") or {}
            record["library_init_us"] = initialization.get("initialization_us", 0)
            record["encode_plus_library_init_us"] = record["encode_us"] + record["library_init_us"]
            record["sample"] = sample
            report["encoding"].append(record)
    for sample in range(args.write_samples):
        for name, codec in rotated_order(configurations, sample):
            record = run_json(args.binary, "sync-write", args.data, name, codec)
            record["sample"] = sample
            report["sync_writing"].append(record)
    report["summary"] = {}
    for case in cases:
        name = case["name"]
        summary = {"dimensions": case["dimensions"], "pixels": case["pixels"], "codec_bytes": case["codec_bytes"], "reopen_us": {}, "encode_us": {}, "sync_write_us": {}}
        for codec in codecs:
            samples = [row for row in report["reopen"] if row["case"] == name and row["mode"] == codec]
            summary["reopen_us"][codec] = {field: distribution([row[field] for row in samples]) for field in ["elapsed_us", "library_init_us", "elapsed_plus_library_init_us", "external_process_us", "rgb_expansion_us"]}
            samples = [row for row in report["encoding"] if row["case"] == name and row["codec"] == codec]
            summary["encode_us"][codec] = {field: distribution([row[field] for row in samples]) for field in ["encode_us", "library_init_us", "encode_plus_library_init_us", "post_timing_oracle_us"]}
            samples = [row for row in report["sync_writing"] if row["case"] == name and row["codec"] == codec]
            summary["sync_write_us"][codec] = distribution([row["sync_write_us"] for row in samples])
        report["summary"][name] = summary
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "summary": report["summary"], "audit": report["audit"], "checks": report["checks"]}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--jpeg-fixtures", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--zstd-supplement", action="store_true")
    parser.add_argument("--reference-report", type=Path)
    parser.add_argument("--qualify-only", action="store_true")
    parser.add_argument("--reopen-samples", type=int, default=21)
    parser.add_argument("--write-samples", type=int, default=11)
    args = parser.parse_args()
    if args.reopen_samples < 5 or args.write_samples < 5:
        parser.error("At least five independent process samples are required")
    if args.zstd_supplement:
        zstd_supplement(args)
        return
    report = {
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "logical_cpus": os.cpu_count(),
            "clock": str(time.get_clock_info("perf_counter")),
        },
        "build_metadata": json.loads(args.metadata.read_text(encoding="utf-8")) if args.metadata else None,
        "method": {
            "execution": "Serial, interleaved codec order, one fresh process per operation sample",
            "storage": "OS page cache is warm; cache dropping and physical Kindle flash are not measured",
            "production": "Fresh CacheManager open, manifest local_path validation, production rendered PNG lookup and decode",
            "validated-fingerprint": "Fresh CacheManager open and existing render() without eager full source-image validation; render() still validates the canonical source SHA-256 before a processed PNG hit, and decodes that source on a miss",
            "alias": "Excluded from formal timing: the cover alias API does not enforce the same reader source and navigation identity contract",
            "codec_only": "File open, metadata bounds and identity validation, decompression, and terminal integrity checks; excludes shared cache index and fingerprint checks",
            "writing": "Encoding, a full output roundtrip oracle, file sync, atomic rename, and directory sync; excludes a shared cache index transaction",
            "oracle": "Every measured output SHA-256 must equal production pixels; packing must preserve all sixteen gray levels exactly",
            "memory": "No total application RSS claim; raw snapshot decoder output has a strict 12 MiB limit",
            "scope": "An extra snapshot duplicates an existing processed PNG. Same-process wake already reuses the retained RGB scene.",
        },
        "preparation": run_json(args.binary, "prepare", args.data, args.source, args.jpeg_fixtures),
        "audit": None,
        "reopen": [],
        "writing": [],
    }
    report["audit"] = run_json(args.binary, "audit", args.data)
    cases = report["preparation"]["cases"]
    configurations = []
    writing = []
    for case in cases:
        modes = ["production", "validated-fingerprint", "png", "zlib1", "zlib6", "packed1", "packed6"]
        configurations.extend((case["name"], mode) for mode in modes)
        writing.extend((case["name"], codec) for codec in ["png", "zlib1", "zlib6", "packed1", "packed6"])
    for case, mode in configurations:
        run_json(args.binary, "reopen", args.data, case, mode)
    for sample in range(args.reopen_samples):
        order = configurations[sample % len(configurations):] + configurations[:sample % len(configurations)]
        if sample % 2:
            order = list(reversed(order))
        for case, mode in order:
            record = run_json(args.binary, "reopen", args.data, case, mode)
            record["sample"] = sample
            report["reopen"].append(record)
    for sample in range(args.write_samples):
        order = writing[sample % len(writing):] + writing[:sample % len(writing)]
        if sample % 2:
            order = list(reversed(order))
        for case, codec in order:
            record = run_json(args.binary, "write", args.data, case, codec)
            record["sample"] = sample
            report["writing"].append(record)
    report["summary"] = {}
    for case in cases:
        name = case["name"]
        summary = {"dimensions": case["dimensions"], "pixels": case["pixels"], "codec_bytes": case["codec_bytes"], "reopen_us": {}, "writing_us": {}}
        for case_name, mode in configurations:
            if case_name != name:
                continue
            samples = [row for row in report["reopen"] if row["case"] == name and row["mode"] == mode]
            summary["reopen_us"][mode] = {
                field: distribution([row[field] for row in samples])
                for field in ["elapsed_us", "external_process_us", "rgb_expansion_us"]
            }
            summary["reopen_us"][mode]["stages"] = {
                stage: distribution([row["stages"][stage] for row in samples])
                for stage in samples[0]["stages"]
            }
        for codec in ["png", "zlib1", "zlib6", "packed1", "packed6"]:
            samples = [row for row in report["writing"] if row["case"] == name and row["codec"] == codec]
            summary["writing_us"][codec] = {
                field: distribution([row[field] for row in samples])
                for field in ["encode_us", "validate_us", "durable_write_us", "total_us"]
            }
        report["summary"][name] = summary
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "summary": report["summary"], "audit": report["audit"]}, indent=2))


if __name__ == "__main__":
    main()
