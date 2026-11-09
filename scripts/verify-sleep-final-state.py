#!/usr/bin/env python3
"""Collect offline database and stock-screen evidence after native rounds.

Run only on the designated remote Linux worker. It never writes a guest database
or USB/power property. Every copied database is inspected with SQLite mode=ro.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import subprocess
import sys
from urllib.parse import quote

from PIL import Image, ImageChops


EXPECTED = {
    "reader-stable", "library-page-filter", "consecutive-quick-wake",
    "killed-sleep-fallback", "explicit-exit", "synthetic-usb",
    "network-owned-pause", "pending-mode", "pending-portrait",
    "tap-then-sleep", "held-seek-then-sleep", "quick-tap-then-wake",
}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round-root", type=Path, action="append", required=True)
    parser.add_argument("--rounds", type=int, required=True)
    parser.add_argument("--expected-binary-sha256", required=True)
    parser.add_argument("--expected-launcher-sha256", required=True)
    parser.add_argument("--expected-source-snapshot-sha256", help="Require the same declared root-frozen production source in the selected reports")
    parser.add_argument("--additional-report", type=Path, action="append", default=[], help="Accepted independent orientation report; include its database namespaces without changing the required twelve-case selection")
    parser.add_argument("--stock-before", type=Path, required=True)
    parser.add_argument("--app-reference", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--guest-ssh", type=Path, default=Path("/root/aidoku-emulator/guest-ssh"))
    parser.add_argument("--emulator-root", type=Path, default=Path("/root/aidoku-emulator/eink-emulator"))
    parser.add_argument("--machine", default="aidoku-pw6-5201")
    arguments = parser.parse_args()
    if arguments.additional_report and not arguments.expected_source_snapshot_sha256:
        parser.error("Independent reports require --expected-source-snapshot-sha256")
    if arguments.expected_source_snapshot_sha256 and not re.fullmatch(r"[0-9a-f]{64}", arguments.expected_source_snapshot_sha256):
        parser.error("The expected source snapshot must be a lowercase SHA-256 value")
    require(os.name == "posix", "Run on the designated remote Linux worker")
    require(1 <= arguments.rounds <= 10 and len(arguments.round_root) == arguments.rounds, "The explicit round count must match complete round roots")
    require(re.fullmatch(r"aidoku-[a-z0-9_-]+", arguments.machine), "Only an isolated Aidoku guest is supported")
    output = arguments.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {"machine": arguments.machine, "expected_rounds": arguments.rounds, "expected_case_total": len(EXPECTED) * arguments.rounds, "observed_case_total": 0, "rounds": [], "additional_reports": [], "expected_source_snapshot_sha256": arguments.expected_source_snapshot_sha256, "databases": [], "boot_observations": [], "passed": False}

    def guest(command):
        completed = subprocess.run([str(arguments.guest_ssh), command], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        require(completed.returncode == 0, f"Guest read failed: {completed.stderr.decode(errors='replace')}")
        return completed.stdout

    def verify_boot(stage):
        values = guest("aidoku_marker=$(cat /var/local/aidoku-emulator-id); read -r aidoku_boot </proc/sys/kernel/random/boot_id; printf '%s\\n%s\\n' \"$aidoku_marker\" \"$aidoku_boot\"").decode().splitlines()
        report["boot_observations"].append({"stage": stage, "observed": values})
        require(values == [arguments.machine, report["boot_id"]], "The guest identity or boot changed during final read-only collection")

    try:
        require(guest("cat /var/local/aidoku-emulator-id").decode().strip() == arguments.machine, "The guest identity does not match")
        report["boot_id"] = guest("cat /proc/sys/kernel/random/boot_id").decode().strip()
        require(guest("lipc-get-prop -s com.lab126.powerd state").decode().strip() == "active", "Stock is not active after native cleanup")
        report["prevent_screensaver_after"] = guest("lipc-get-prop -i com.lab126.powerd preventScreenSaver").decode().strip()
        report["usb_state_after"] = guest("lipc-get-prop -i com.lab126.powerd usbState").decode().strip()
        remaining = guest("for d in /proc/[0-9]*; do [ -r \"$d/comm\" ] || continue; read -r n <\"$d/comm\"; case \"$n\" in aidoku.native) printf '%s\\n' \"$d\" ;; esac; done").decode().strip()
        locks = guest("for p in /tmp/aidoku-launcher.lock /tmp/aidoku-sleep-*; do [ ! -d \"$p\" ] || printf '%s\\n' \"$p\"; done").decode().strip()
        require(not remaining and not locks, f"A native process or owned test lock remains: {remaining} {locks}")
        report["native_processes_and_locks_remaining"] = []
        namespaces = {}
        for root in arguments.round_root:
            cases = []
            files = sorted(root.resolve(strict=True).rglob("report.json"))
            require(len(files) == 3, f"Each round needs one basic, network, and gesture report: {root}")
            for path in files:
                value = json.loads(path.read_text())
                require(value["machine"] == arguments.machine, "A report belongs to another VM")
                require(value["observed_environment"]["boot_id"] == report["boot_id"], "The selected native reports or final collection mix guest boots")
                require(value["frozen_input_sha256"]["binary"] == arguments.expected_binary_sha256, "The clean rounds mix binary hashes")
                require(value["frozen_input_sha256"]["launcher"] == arguments.expected_launcher_sha256, "The clean rounds mix launcher hashes")
                if arguments.expected_source_snapshot_sha256:
                    require(value.get("source_snapshot_sha256_declared_by_root") == arguments.expected_source_snapshot_sha256, "The selected matrix reports mix declared production source snapshots")
                require(not value.get("setup_error") and value["failed"] == 0, f"A final round contains a failed or incomplete report: {path}")
                pixels = value.get("pixel_comparison_contract", {})
                require(pixels.get("reader") == "full-viewport-rgb-exact" and pixels.get("reader_pixel_error_tolerance") == 0 and pixels.get("reader_ignored_pixels") == 0, "A selected suite does not require full-viewport Reader RGB identity")
                policy = value["screensaver_policy"]
                require(policy["restored"] and policy["usb_state_before"] == policy["usb_state_after"], "A suite did not restore its saved emulator policy")
                startups = [item for item in policy.get("cold_start_protection", []) if item["context"].startswith("application-")]
                require(len(startups) == len(value["cases"]) and all(item.get("completed") and item.get("after") == "1" and item.get("native_window_ready") and item.get("download_controller_healthy") for item in startups), "A selected case lacks a protected cold startup and healthy native worker")
                require(report["prevent_screensaver_after"] == policy["saved_prevent_screensaver"], "The final boot policy differs from a suite's saved policy")
                require(report["usb_state_after"] == policy["usb_state_before"], "The final USB state differs from the observed initial state")
                for case in value["cases"]:
                    require(case["passed"] and not case.get("error") and not case.get("cleanup_error"), "A required case did not pass cleanly")
                    cases.append(case["name"])
                for namespace in value["prepared_namespaces"]:
                    namespaces[namespace["home"]] = namespace
            require(len(cases) == len(EXPECTED) and set(cases) == EXPECTED, f"The final round does not contain exactly the required {len(EXPECTED)} cases: {root}")
            report["rounds"].append({"root": str(root.resolve()), "reports": [str(path) for path in files], "passed_cases": cases})
            report["observed_case_total"] += len(cases)
        require(report["observed_case_total"] == report["expected_case_total"], "The complete native case total does not match the explicit requested rounds")
        for path in arguments.additional_report:
            value = json.loads(path.resolve(strict=True).read_text())
            require(value.get("kind") == "native-reader-manual-orientation" and value.get("counts_towards_required_twelve_cases") is False, "An additional report must be an explicitly independent orientation check")
            require(value.get("accepted") is True and not value.get("setup_error") and not value.get("suite_cleanup_error") and value.get("failed") == 0, "An additional orientation report was not accepted cleanly")
            require(len(value["cases"]) == 1 and value["cases"][0]["name"] == "reader-landscape-reverse" and value["cases"][0]["passed"] and not value["cases"][0].get("error") and not value["cases"][0].get("cleanup_error"), "The independent report does not contain its one successful orientation case")
            require(value["machine"] == arguments.machine and value["observed_environment"]["boot_id"] == report["boot_id"], "The independent orientation report belongs to another machine or boot")
            require(value["frozen_input_sha256"]["binary"] == arguments.expected_binary_sha256 and value["frozen_input_sha256"]["launcher"] == arguments.expected_launcher_sha256, "The independent orientation report mixes application or launcher hashes")
            require(value.get("source_snapshot_sha256_declared_by_root") == arguments.expected_source_snapshot_sha256, "The independent orientation report uses a different declared production source snapshot")
            policy = value["screensaver_policy"]
            require(policy["restored"] and policy["saved_prevent_screensaver"] == report["prevent_screensaver_after"] and policy["usb_state_before"] == policy["usb_state_after"] == report["usb_state_after"], "The independent orientation report did not restore the common stock policy")
            require(len(value["prepared_namespaces"]) == 1, "The independent orientation report must identify its one fresh namespace")
            for namespace in value["prepared_namespaces"]:
                require(namespace["home"] not in namespaces, "The independent orientation report reused an existing selected namespace")
                namespaces[namespace["home"]] = namespace
            report["additional_reports"].append({"path": str(path.resolve()), "kind": value["kind"], "accepted": True, "counts_towards_required_twelve_cases": False, "database_namespaces": [namespace["home"] for namespace in value["prepared_namespaces"]]})
        verify_boot("before-database-copies")

        for number, (home, namespace) in enumerate(sorted(namespaces.items()), 1):
            require(home.startswith("/mnt/us/aidoku-sleep-tests/"), "A report database is outside the isolated fixture namespace")
            paths = guest("find " + shlex.quote(home + "/data") + " -type f -name '*.sqlite3'").decode().splitlines()
            require(any(Path(path).name == "library.sqlite3" for path in paths), "A completed namespace has no library database")
            for index, source in enumerate(sorted(paths), 1):
                directory = output / "database-copies" / f"namespace-{number:02}" / f"database-{index:02}"
                directory.mkdir(parents=True)
                destination = directory / Path(source).name
                destination.write_bytes(guest("cat " + shlex.quote(source)))
                for suffix in ("-wal", "-shm", "-journal"):
                    sidecar = source + suffix
                    if guest("if [ -f " + shlex.quote(sidecar) + " ]; then printf present; fi") == b"present":
                        (directory / (Path(source).name + suffix)).write_bytes(guest("cat " + shlex.quote(sidecar)))
                original_sha = sha256(destination.read_bytes())
                connection = sqlite3.connect("file:" + quote(str(destination), safe="/") + "?mode=ro", uri=True)
                try:
                    journal = connection.execute("PRAGMA journal_mode").fetchone()[0]
                    quick = [row[0] for row in connection.execute("PRAGMA quick_check")]
                    integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
                    foreign = list(connection.execute("PRAGMA foreign_key_check"))
                    tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
                finally:
                    connection.close()
                require(journal == "delete" and quick == ["ok"] and integrity == ["ok"] and not foreign, f"Database integrity/journal check failed for {source}")
                require(original_sha == sha256(destination.read_bytes()), "Read-only inspection changed the copied database")
                report["databases"].append({"guest_source": source, "namespace": namespace, "copy": str(destination), "sha256": original_sha, "bytes": destination.stat().st_size, "journal_mode": journal, "quick_check": quick, "integrity_check": integrity, "foreign_key_check": foreign, "tables": tables})

        verify_boot("after-database-copies")
        stock_after = output / "stock-after.png"
        qmp = arguments.emulator_root.resolve() / "scripts/eink-qmp.py"
        subprocess.run([sys.executable, str(qmp), "--machine", arguments.machine, "screendump", str(stock_after)], cwd=arguments.emulator_root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        verify_boot("after-stock-screendump")
        with Image.open(arguments.stock_before) as first, Image.open(stock_after) as last, Image.open(arguments.app_reference) as app:
            require(first.size == last.size == app.size == (1272, 1696), "Stock comparison requires the observed PW6 firmware scanout geometry")
            width, height = first.size
            roi = (0, 240, width, height - 110)
            def comparison(a, b, region):
                difference = ImageChops.difference(a.convert("RGB").crop(region), b.convert("RGB").crop(region))
                red, green, blue = difference.split()
                maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
                histogram = maximum.histogram()
                area = maximum.width * maximum.height
                different_pixels = area - histogram[0]
                return {"metric": "maximum RGB channel difference per pixel", "mean_absolute_error": sum(value * count for value, count in enumerate(histogram)) / area, "maximum_absolute_error": maximum.getextrema()[1], "different_pixels": different_pixels, "changed_ratio_above_16": sum(histogram[17:]) / area, "exact_match": different_pixels == 0}
            stock_metric, app_metric = comparison(first, last, roi), comparison(first, app, roi)
            full_stock_metric = comparison(first, last, (0, 0, width, height))
            same_stock, different_app = stock_metric["changed_ratio_above_16"], app_metric["changed_ratio_above_16"]
        report["stock_restoration"] = {"before": str(arguments.stock_before.resolve()), "after": str(stock_after), "application_reference": str(arguments.app_reference.resolve()), "roi": roi, "comparison_scope": "documented-stable-stock-body-rgb-exact", "pixel_error_tolerance": 0, "stock_changed_ratio_above_16": same_stock, "application_difference_ratio_above_16": different_app, "stock_pixel_metrics": stock_metric, "application_pixel_metrics": app_metric, "full_viewport_pixel_metrics_diagnostic_only": full_stock_metric, "excluded_regions": "Rows 0..239 and the final 110 rows contain dynamic stock system content; their full-viewport differences are reported separately."}
        require(stock_metric["exact_match"] and stock_metric["mean_absolute_error"] == 0 and stock_metric["maximum_absolute_error"] == 0 and same_stock == 0, "The stable stock body was not restored with exact RGB identity")
        require(different_app >= 0.15, "The stock baseline was not independent of the Aidoku Reader reference")
        report["passed"] = True
        return 0
    except Exception as error:
        report["error"] = str(error)
        return 1
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({"report": str(output / "report.json"), "passed": report["passed"], "error": report.get("error"), "database_count": len(report["databases"])}), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
