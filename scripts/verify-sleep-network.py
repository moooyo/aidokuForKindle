#!/usr/bin/env python3
"""Verify native sleep download ownership with real controlled HTTP traffic.

Powerd transitions are real. Only the application's wifid.cmState observation
is replaced with a process-local test shim. Real Wi-Fi/USB settings are untouched.
Run only on the designated remote worker after all performance windows finish.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shlex
import sys
import time

from sleep_network_fixture import SleepNetworkFixture


SPEC = importlib.util.spec_from_file_location("aidoku_sleep_verifier", Path(__file__).with_name("verify-sleep-wake.py"))
base = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = base
SPEC.loader.exec_module(base)

SHIM = r'''#!/bin/sh
set -eu
if [ "$#" -eq 2 ] && [ "$1" = com.lab126.wifid ] && [ "$2" = cmState ]; then
    cat "$AIDOKU_TEST_NETWORK_READY"
    exit 0
fi
if [ "$#" -eq 3 ] && [ "$1" = -s ] && [ "$2" = com.lab126.wifid ] && [ "$3" = cmState ]; then
    cat "$AIDOKU_TEST_NETWORK_READY"
    exit 0
fi
exec /usr/bin/lipc-get-prop "$@"
'''


class NetworkVerifier(base.SleepVerifier):
    def __init__(self, arguments):
        super().__init__(arguments)
        self.fixture_binary = arguments.fixture_binary.resolve(strict=True)
        self.fixture_bytes = self.fixture_binary.read_bytes()
        base.require(self.fixture_bytes[:6] == b"\x7fELF\x01\x01" and int.from_bytes(self.fixture_bytes[18:20], "little") == 40, "The fixture helper must be a native ARM ELF")
        self.hashes["fixture_helper"] = base.digest(self.fixture_bytes)
        self.hashes["network_probe_shim"] = base.digest(SHIM.encode())
        (self.output / "inputs" / "sleep-fixture.elf").write_bytes(self.fixture_bytes)
        (self.output / "inputs" / "lipc-get-prop-test-shim.sh").write_text(SHIM)

    def inspect(self, session, label):
        payload = self.guest(shlex.join([session.home + "/app/sleep-fixture", "--data-dir", session.data_dir, "--inspect"]))
        value = json.loads(payload)
        path = session.output / f"{label}-store.json"
        path.write_bytes(payload)
        session.artifacts.append(str(path))
        return value

    @staticmethod
    def jobs(state):
        return {job["chapter_id"]: job for job in state["jobs"]}

    @staticmethod
    def close_fixture(fixture, result):
        operations = (
            ("Close HTTP fixture", fixture.close),
            ("Collect HTTP fixture artifacts", lambda: result.artifacts.extend(str(path) for path in fixture.output.iterdir())),
        )
        for description, operation in operations:
            try:
                operation()
            except Exception as error:
                result.cleanup_error = "; ".join(value for value in (result.cleanup_error, f"{description}: {error}") if value)
                result.passed = False

    def network_case(self):
        result = base.Result("network-owned-pause", self.round_number)
        started = time.monotonic()
        fixture = None
        session = None
        description = {}
        network_file = self.home + "/data/logs/test-network-ready"
        try:
            provider_output = self.round_output / "network-provider"
            fixture = SleepNetworkFixture(provider_output, (self.repository / "assets/demo/page-00.png", self.repository / "assets/demo/page-01.png"))

            def seed(context):
                self.put(self.fixture_bytes, context.home + "/app/sleep-fixture", "755")
                observed = self.guest("sha256sum " + shlex.quote(context.home + "/app/sleep-fixture")).decode().split()[0]
                base.require(observed == self.hashes["fixture_helper"], "The fixture helper changed in transit")
                self.guest("mkdir -p " + shlex.quote(context.home + "/test-bin"))
                self.put(SHIM.encode(), context.home + "/test-bin/lipc-get-prop", "755")
                self.put(b"CONNECTED\n", network_file)
                payload = self.guest(shlex.join([context.home + "/app/sleep-fixture", "--data-dir", context.data_dir, "--base-url", fixture.base_url, "--pages", "8"]))
                description.update(json.loads(payload))
                (context.output / "fixture-created.json").write_bytes(payload)

            session = self.start("network-owned-pause", "reader", demo=False, offline=False, before_start=seed, extra_env={"PATH": self.home + "/test-bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/var/local/kmc/bin", "AIDOKU_TEST_NETWORK_READY": network_file})
            ids = {chapter["remote_id"]: chapter["id"] for chapter in description["bulk_chapters"]}
            self.wait("two real background image streams", lambda: fixture.snapshot()["active_background"] == 2)
            # c00 has exactly one local page, so there is no initial same-chapter
            # prefetch. A real left-side touch enters uncached c05 using FG capacity.
            before = self.capture(session, "before-foreground")
            with base.Image.open(before.image) as image:
                x, y = round(image.width * 0.12), round(image.height * 0.5)
            self.qmp("tap", str(x), str(y), "--hold", "0.10")
            self.wait("foreground receives the reserved third slot", lambda: fixture.snapshot()["foreground_with_two_background"])

            def displayed():
                value = self.inspect(session, "foreground-progress")
                return value if any(progress["chapter_id"] == description["reader_next"]["id"] and progress["page"] == 1 for progress in value["progress"]) else None

            self.wait("uncached foreground page accepted and persisted", displayed)
            self.put(b"DISCONNECTED\n", network_file)
            parked = self.park(session, "network-sleep")
            paused = self.inspect(session, "paused")
            paused_jobs = self.jobs(paused)
            expected_owned = {ids["c01"], ids["c02"]}
            base.require(set(paused["owned"]) == expected_owned, "Sleep did not own exactly the previously queued/downloading chapters")
            for name in ("c01", "c02", "c03"):
                base.require(paused_jobs[ids[name]]["state"] == "paused", f"{name} did not preserve its paused state")
            base.require(paused_jobs[ids["c04"]]["state"] == "cancelled", "Sleep changed a manually cancelled job")
            base.require(all(job["state"] != "failed" for job in paused_jobs.values()), "Old interrupted TCP results marked a paused job failed")
            self.wait("old background TCP streams close after self-process abort", lambda: fixture.snapshot()["active_background"] == 0 and fixture.snapshot()["closed_while_held"] >= 2)
            asleep_http = fixture.snapshot()
            time.sleep(2)
            still_asleep_http = fixture.snapshot()
            base.require(still_asleep_http["request_count"] == asleep_http["request_count"], "The stopped application dispatched HTTP while stock owned the screensaver")
            awake = self.wake(session, "network-wake", baseline=parked.parked, reference_image=parked.before.image)
            retained = self.compare_application(parked.parked.state["application"], awake.state["application"])
            time.sleep(2.5)
            disconnected = self.inspect(session, "wake-disconnected")
            disconnected_http = fixture.snapshot()
            base.require(disconnected_http["request_count"] == still_asleep_http["request_count"], "Background HTTP restarted during sleep or wake before the controlled network-ready observation")
            base.require(set(disconnected["owned"]) == expected_owned, "Wake without network lost automatic pause ownership")
            for name in ("c01", "c02", "c03"):
                base.require(self.jobs(disconnected)[ids[name]]["state"] == "paused", f"Offline wake changed {name}")
            fixture.release()
            self.put(b"CONNECTED\n", network_file)

            def completed():
                value = self.inspect(session, "connected-progress")
                jobs = self.jobs(value)
                return value if all(jobs[ids[name]]["state"] == "completed" and jobs[ids[name]]["document"]["completed"] == 8 for name in ("c01", "c02")) else None

            finished = self.wait("only automatically paused downloads resume after network-ready", completed)
            final_jobs = self.jobs(finished)
            base.require(not finished["owned"], "Completed automatic jobs retained sleep ownership")
            base.require(final_jobs[ids["c03"]]["state"] == "paused" and final_jobs[ids["c04"]]["state"] == "cancelled", "Network recovery resumed a user-paused or cancelled chapter")
            wire = fixture.snapshot()
            base.require(wire["peak_background"] <= 2 and wire["peak_total"] <= 3 and wire["foreground_with_two_background"], "The actual image request concurrency violated the two-background/one-reserved-foreground policy")
            exited = self.stop(session)
            result.checks = {"sleep": self.park_checks(parked), "retained_state": retained, "queued_download_ids": sorted(expected_owned), "paused": paused, "disconnected_wake": disconnected, "completed_after_ready": finished, "http_asleep": asleep_http, "http_still_asleep": still_asleep_http, "http_after_disconnected_wake": disconnected_http, "http_final": wire, "exit": exited, "network_ready_adapter": "Test-only process-local wifid.cmState shim; all other LIPC calls use the actual stock binary", "transport": "Actual HTTP over observed USB NAT host; real USB/Wi-Fi state unchanged", "powerd": "Actual firmware powerButton/wakeUp and observed stock events"}
            result.passed = True
        except Exception as error:
            result.error = str(error)
            session = session or self.current
            if fixture is not None:
                result.checks["http_at_failure"] = fixture.snapshot()
            if session is not None:
                try: self.capture(session, "failure", allow_missing=True)
                except Exception as capture_error: result.checks["failure_capture_error"] = str(capture_error)
        finally:
            session = session or self.current
            if session is not None:
                try: self.cleanup(session)
                except Exception as cleanup_error: result.cleanup_error = str(cleanup_error); result.passed = False
                result.artifacts = list(dict.fromkeys(session.artifacts))
            if fixture is not None:
                self.close_fixture(fixture, result)
            result.duration_seconds = round(time.monotonic() - started, 3)
            self.results.append(result)
            self.write_report()
            print(f"{'PASS' if result.passed else 'FAIL'} round-{self.round_number}/network-owned-pause" + (f": {result.error or result.cleanup_error}" if not result.passed else ""), flush=True)

    def pending_case(self, portrait: bool):
        name = "pending-portrait" if portrait else "pending-mode"
        result = base.Result(name, self.round_number)
        started = time.monotonic()
        fixture = None
        session = None
        description = {}
        timing = {"mode_action_at_active_ms": 60000}
        network_file = self.home + "/data/logs/test-network-ready"
        try:
            fixture = SleepNetworkFixture(self.round_output / "network-provider", (self.repository / "assets/demo/page-00.png", self.repository / "assets/demo/page-01.png"), hold_foreground=True)

            def seed(context):
                self.put(self.fixture_bytes, context.home + "/app/sleep-fixture", "755")
                self.guest("mkdir -p " + shlex.quote(context.home + "/test-bin"))
                self.put(SHIM.encode(), context.home + "/test-bin/lipc-get-prop", "755")
                self.put(b"CONNECTED\n", network_file)
                payload = self.guest(shlex.join([context.home + "/app/sleep-fixture", "--data-dir", context.data_dir, "--base-url", fixture.base_url, "--pages", "8"]))
                description.update(json.loads(payload))
                (context.output / "fixture-created.json").write_bytes(payload)
                timing["before_launch_guest_uptime"] = float(self.guest("awk '{print $1}' /proc/uptime"))

            setting = "landscape=false" if portrait else "reading_direction=vertical"
            environment = {"PATH": self.home + "/test-bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/var/local/kmc/bin", "AIDOKU_TEST_NETWORK_READY": network_file}
            if portrait: environment["AIDOKU_TEST_LANDSCAPE"] = "1"
            session = self.start(name, "reader", [base.action(timing["mode_action_at_active_ms"], "settings", "set", setting)], demo=False, offline=False, before_start=seed, extra_env=environment, wait_for_actions=False)
            timing["app_clock_started_before_guest_uptime"] = float(self.guest("awk '{print $1}' /proc/uptime"))
            self.wait("two held background image streams", lambda: fixture.snapshot()["active_background"] == 2)
            # One-page c00 does not prefetch. c05's foreground response stays
            # incomplete, so the desired next chapter can never be accepted.
            input_reference = self.capture(session, "pending-mode-input-reference")
            with base.Image.open(input_reference.image) as image:
                width, height = image.size
            # Invert the backend's 90-degree panel-to-logical conversion when
            # the initial Reader is landscape. Never use design-canvas pixels.
            point = (round(width * 0.5), round(height * 0.12)) if portrait else (round(width * 0.12), round(height * 0.5))
            timing["foreground_input_geometry"] = {"physical_size": [width, height], "physical_point": list(point), "initial_landscape": portrait}
            self.qmp("tap", str(point[0]), str(point[1]), "--hold", "0.10")
            self.wait("foreground response held without an accepted image", lambda: fixture.snapshot()["foreground_with_two_background"] and fixture.snapshot()["active_foreground"] == 1)
            timing["foreground_held_guest_uptime"] = float(self.guest("awk '{print $1}' /proc/uptime"))
            action_seconds = timing["mode_action_at_active_ms"] / 1000
            base.require(timing["foreground_held_guest_uptime"] < timing["before_launch_guest_uptime"] + action_seconds, "Pending-mode setup missed its action deadline before the foreground was held; this is a fixture-ordering failure")
            # The App clock starts strictly after the seeder and before start()
            # returns. These guest-uptime bounds establish that the real held
            # foreground precedes the replay, then wait until the replay must
            # have happened. SSH observations cannot silently reverse the setup.
            target_uptime = timing["app_clock_started_before_guest_uptime"] + action_seconds + 0.1
            print(f"SETUP {name}: foreground held before replay; waiting for the bounded mode-action window", flush=True)
            self.wait("pending-mode replay window after actual foreground admission", lambda: float(self.guest("awk '{print $1}' /proc/uptime")) >= target_uptime, timeout=action_seconds + 15, interval=0.5)
            base.require(fixture.snapshot()["active_foreground"] == 1, "The held foreground ended before the pending-mode sleep setup")
            (session.output / "pending-mode-timing.json").write_text(json.dumps(timing, indent=2) + "\n")
            session.artifacts.append(str(session.output / "pending-mode-timing.json"))
            self.put(b"DISCONNECTED\n", network_file)
            parked = self.park(session, name + "-sleep")
            before = parked.parked.state["application"]
            base.require(before["reader_repair_pending"] is True, "The desired rendering mode was not pending at the actual suspend checkpoint")
            base.require(before["current_chapter"] == description["reader_current"]["id"], "Suspend did not roll the unaccepted chapter back to its displayed predecessor")
            old_preferences = before["displayed_reader"]["preferences"]
            base.require(old_preferences["landscape"] is True if portrait else old_preferences["reading_direction"] == "rtl", "The intended old rendering mode was not actually displayed")
            if portrait: base.require(before["landscape"] is False, "The portrait preference was not applied before sleep")
            awake = self.wake(session, name + "-wake", baseline=parked.parked, reference_image=parked.before.image, compare_image=False)
            after = awake.state["application"]
            self.compare_application(before, after, stable_jobs=False)
            base.require(after["reader_image_jobs_started"] == before["reader_image_jobs_started"] + 1, "The pending rendering mode was not repaired exactly once after the retained full RGB submission")
            time.sleep(3)
            exited = self.stop(session)
            final = exited["capture"]["state"]
            base.require(final["displayed_reader"]["session"]["chapter_id"] == description["reader_current"]["id"], "Pending-mode repair jumped to the never-displayed HTTP chapter")
            base.require(final["displayed_reader"]["session"]["page"] == 1, "Pending-mode repair changed the actual reading page")
            effective = final["reader_effective"]
            base.require(effective["landscape"] is False if portrait else effective["reading_direction"] == "vertical", "The requested mode was lost after wake")
            accepted = final["displayed_reader"]["preferences"]
            base.require(accepted["landscape"] is False if portrait else accepted["reading_direction"] == "vertical", "The post-frame rendering repair was attempted but never accepted")
            base.require(final["reader_recovery"] is None, "The expected rendering repair ended in a recovery error")
            base.require(final["menu"] is False, "Pending-mode wake did not return to immersive Reader")
            result.checks = {"sleep": self.park_checks(parked), "retained_pid": awake.state["pid"], "same_pid": awake.state["pid"] == parked.parked.state["pid"], "mode_action": setting, "mode_action_timing": timing, "never_accepted_chapter": description["reader_next"]["id"], "displayed_chapter": description["reader_current"]["id"], "reader_image_jobs_before": before["reader_image_jobs_started"], "reader_image_jobs_after_full_frame_confirmation": after["reader_image_jobs_started"], "full_rgb_counter_before": parked.parked.state["platform"]["resume_full_frame_count"], "full_rgb_counter_after": awake.state["platform"]["resume_full_frame_count"], "scope": "The first retained RGB submission is acknowledged before the necessary asynchronous mode repair. Final pixels legitimately differ from the old rendering mode; this is not claimed as a stable-image/no-decode case.", "http": fixture.snapshot(), "exit": exited}
            result.passed = True
        except Exception as error:
            result.error = str(error)
            session = session or self.current
            if fixture is not None: result.checks["http_at_failure"] = fixture.snapshot()
            if session is not None:
                try: self.capture(session, "failure", allow_missing=True)
                except Exception as capture_error: result.checks["failure_capture_error"] = str(capture_error)
        finally:
            session = session or self.current
            if session is not None:
                try: self.cleanup(session)
                except Exception as cleanup_error: result.cleanup_error = str(cleanup_error); result.passed = False
                result.artifacts = list(dict.fromkeys(session.artifacts))
            if fixture is not None:
                self.close_fixture(fixture, result)
            result.duration_seconds = round(time.monotonic() - started, 3)
            self.results.append(result)
            self.write_report()
            print(f"{'PASS' if result.passed else 'FAIL'} round-{self.round_number}/{name}" + (f": {result.error or result.cleanup_error}" if not result.passed else ""), flush=True)

    def run(self):
        try:
            for number in range(1, self.arguments.rounds + 1):
                selected = set(self.arguments.cases.split(",")) if self.arguments.cases else {"network-owned-pause", "pending-mode", "pending-portrait"}
                base.require(selected <= {"network-owned-pause", "pending-mode", "pending-portrait"}, "Unknown native network case")
                for name in ("network-owned-pause", "pending-mode", "pending-portrait"):
                    if name not in selected: continue
                    self.prepare_round(number, variant=name)
                    if name == "network-owned-pause": self.network_case()
                    else: self.pending_case(portrait=name == "pending-portrait")
        except Exception as error:
            self.suite_error = str(error)
            raise
        finally:
            self.finish_suite()
        print(json.dumps({"report": str(self.output / "report.json"), "passed": sum(result.passed for result in self.results), "failed": sum(not result.passed for result in self.results)}), flush=True)
        return 0 if self.results and all(result.passed for result in self.results) else 1


def main():
    parser = base.build_parser()
    parser.description = __doc__
    parser.add_argument("--fixture-binary", type=Path, required=True)
    arguments = parser.parse_args()
    if os.name != "posix": parser.error("Run on the designated remote Linux worker")
    if not 1 <= arguments.rounds <= 10: parser.error("Use 1..10 fresh rounds")
    return NetworkVerifier(arguments).run()


if __name__ == "__main__":
    raise SystemExit(main())
