#!/usr/bin/env python3
"""Verify native 90/270 Reader rotation outside the required sleep matrix.

Run only on test-env after the root agent freezes the final candidate and grants
an exclusive aidoku-pw6-5201 VM window. This script uses the shared boot/owner,
protected-startup, real powerd, process-retention, and restoration primitives.
It adds one independent report and never contributes a thirteenth case to the
required twelve-case selection. Keep its output outside the functional matrix.

The raw framebuffer oracle is a lossless 180-degree pixel permutation within
one process. The cached hardware launch compensation cancels from that relative
comparison. Pointer mapping follows touch.rs and inverts only application angle;
it must not add the renderer's hardware compensation a second time.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any

from PIL import Image, ImageChops


def load_base():
    name = "aidoku_native_orientation_base"
    path = Path(__file__).with_name("verify-sleep-wake.py")
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError("The shared sleep/wake driver is unavailable")
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


base = load_base()


def exact_rgb(first: Path, second: Path, roi=None) -> dict[str, Any]:
    with Image.open(first) as left, Image.open(second) as right:
        base.require(left.size == right.size, "Compared raw frames have different geometry")
        viewport = left.size
        region = roi or (0, 0, *viewport)
        difference = ImageChops.difference(left.convert("RGB").crop(region), right.convert("RGB").crop(region))
        red, green, blue = difference.split()
        maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
        histogram = maximum.histogram()
        area = maximum.width * maximum.height
        changed = area - histogram[0]
        return {
            "roi": list(region), "viewport_size": list(viewport),
            "different_pixels": changed, "exact_match": changed == 0,
            "maximum_absolute_error": maximum.getextrema()[1],
            "mean_absolute_error": sum(value * count for value, count in enumerate(histogram)) / area,
            "changed_ratio_above_16": sum(histogram[17:]) / area,
            "pixel_error_tolerance": 0, "ignored_pixels": viewport[0] * viewport[1] - area,
        }


def lists_accelerometer(devices: str) -> bool:
    """Mirror the vendored pressure-without-touch-X device predicate."""
    for line in devices.splitlines():
        if not line.startswith("B: ABS="):
            continue
        axes = 0
        for word in line.removeprefix("B: ABS=").split():
            try:
                value = int(word, 16)
            except ValueError:
                value = 0
            axes = ((axes << 32) | value) & ((1 << 64) - 1)
        if axes & (1 << 0x18) and not axes & (1 << 0x00) and not axes & (1 << 0x35):
            return True
    return False


class RotatedPointer:
    """Send one real QMP press/release, checking ownership per transaction."""

    def __init__(self, verifier, session, width: int, height: int) -> None:
        self.verifier, self.session = verifier, session
        self.width, self.height = width, height
        self.held = False
        self.records: list[dict[str, Any]] = []
        self.path = session.output / "orientation-hardware-input.json"
        session.artifacts.append(str(self.path))

    def client(self):
        self.verifier.identity()
        base.require(self.verifier.guard_owned, "An owned suite is required for QMP touch input")
        if str(self.verifier.engine) not in sys.path:
            sys.path.insert(0, str(self.verifier.engine))
        from eink_emulator.qmp import QMPClient
        socket = self.verifier.engine / "machines" / self.verifier.arguments.machine / f"{self.verifier.arguments.machine}.qmp.sock"
        return QMPClient(socket, 5)

    def send(self, client, events) -> dict[str, Any]:
        self.verifier.identity()
        record = {"scope": "orientation-qmp-input", "events": events, "host_sent_monotonic": time.monotonic()}
        self.records.append(record)
        self.verifier.commands.append(record)
        record["response"] = client.execute("input-send-event", {"events": events})
        record["host_completed_monotonic"] = time.monotonic()
        return record

    def tap_270(self, logical_x: float, logical_y: float, scale: float = 2.0) -> dict[str, Any]:
        # touch.rs: (u,v)=((H-y)/s,x/s). Coordinates are continuous W/H,
        # unlike the discrete W-1/H-1 permutation used by the pixel oracle.
        x, y = round(scale * logical_y), round(self.height - scale * logical_x)
        base.require(0 <= x < self.width and 0 <= y < self.height, "The 270-degree touch target is outside the actual panel")
        position = [
            {"type": "abs", "data": {"axis": "x", "value": (x * 32767 + self.width - 2) // (self.width - 1)}},
            {"type": "abs", "data": {"axis": "y", "value": (y * 32767 + self.height - 2) // (self.height - 1)}},
        ]
        with self.client() as client:
            self.held = True
            down = self.send(client, [*position, {"type": "btn", "data": {"button": "left", "down": True}}])
            time.sleep(0.01)
            up = self.send(client, [{"type": "btn", "data": {"button": "left", "down": False}}])
            self.held = False
        timing = {
            "logical_target": [logical_x, logical_y], "physical_target": [x, y],
            "physical_size": [self.width, self.height], "application_rotation_degrees": 270,
            "mapping_source": "vendor/slint-backend-kindle/src/touch.rs::panel_to_logical_position",
            "renderer_launch_offset_added_to_touch": False,
            "requested_hold_seconds": 0.01,
            "hold_lower_seconds": up["host_sent_monotonic"] - down["host_completed_monotonic"],
            "hold_upper_seconds": up["host_completed_monotonic"] - down["host_sent_monotonic"],
            "timing_scope": "QMP submission/completion brackets including per-event SSH ownership checks",
            "down": down, "up": up,
        }
        self.records.append(timing)
        base.require(timing["hold_upper_seconds"] < 0.5, "fixture-ordering: the short tap may have crossed the 500 ms long-press boundary")
        return timing

    def release(self) -> None:
        if self.held:
            with self.client() as client:
                self.send(client, [{"type": "btn", "data": {"button": "left", "down": False}}])
            self.held = False

    def save(self) -> None:
        self.path.write_text(json.dumps({"events": self.records, "global_button_still_held": self.held}, indent=2) + "\n")


class OrientationVerifier(base.SleepVerifier):
    def __init__(self, arguments) -> None:
        super().__init__(arguments)
        self.unsafe_input = False
        self.orientation: dict[str, Any] = {}
        self.contract_sources: dict[str, str] = {}

    def freeze_contract(self) -> None:
        # Keep fallible identity/source reads inside run() so a failed handoff
        # still receives a report without acquiring or modifying the guest.
        base.require(self.hashes["binary"] == self.arguments.expected_binary_sha256, "The ARM binary differs from the root-frozen final candidate")
        base.require(self.hashes["launcher"] == self.arguments.expected_launcher_sha256, "The launcher differs from the root-frozen final candidate")
        self.hashes["orientation_script"] = base.digest(Path(__file__).read_bytes())
        self.hashes["shared_driver"] = base.digest(Path(base.__file__).read_bytes())
        for relative in (
            "src/app/actions.rs", "src/app/orientation.rs", "src/app/diagnostics.rs",
            "src/main.rs", "src/platform.rs", "ui/reader.slint",
            "vendor/slint-backend-kindle/src/platform.rs",
            "vendor/slint-backend-kindle/src/touch.rs",
        ):
            self.contract_sources[relative] = base.digest((self.repository / relative).read_bytes())
        (self.output / "inputs" / "verify-native-orientation.py").write_bytes(Path(__file__).read_bytes())
        (self.output / "inputs" / "verify-sleep-wake.py").write_bytes(Path(base.__file__).read_bytes())

    def uptime(self) -> float:
        return float(self.guest("read -r aidoku_uptime aidoku_idle </proc/uptime; printf '%s\\n' \"$aidoku_uptime\""))

    def hardware_observation(self, label: str) -> dict[str, Any]:
        self.identity()
        raw = self.guest("cat /proc/bus/input/devices").decode()
        path = self.output / f"{label}-input-devices.txt"
        path.write_text(raw)
        accelerated = lists_accelerometer(raw)
        result = {
            "has_accelerometer_by_vendor_predicate": accelerated,
            "input_devices": str(path), "winmgr_accelerometer": None,
            "cached_backend_launch_offset_exported": False,
            "application_angle_metadata_excludes_launch_offset": True,
            "oracle_independent_of_cached_launch_offset": True,
            "input_contract": "Invert only application rotation; firmware already owns hardware touch compensation",
        }
        if accelerated:
            try:
                value = self.guest("timeout -s KILL 1 lipc-get-prop com.lab126.winmgr accelerometer", timeout=5).decode().strip()
                result["winmgr_accelerometer"] = value
                result["complete_vendor_token"] = value in {"U", "D"}
            except Exception as error:
                result["observation_error"] = str(error)
        else:
            result["offset_by_vendor_no_accelerometer_branch"] = 0
        return result

    def raw_frame(self, session, label: str) -> Path:
        self.identity()
        path = session.output / f"{label}.framebuffer.png"
        self.qmp("screendump", str(path))
        session.artifacts.append(str(path))
        return path

    def same_lifetime(self, session) -> dict[str, Any]:
        self.identity()
        process = self.process(session.pid)
        base.require(process is not None and process["birth"] == session.birth and process["ppid"] == session.runner_pid, "Orientation replaced or reparented the native Reader process")
        return process

    @staticmethod
    def requested_angle_evidence(state, expected: int) -> dict[str, Any]:
        application = state["application"]
        base.require(application.get("rotation_degrees") == expected, "The fresh C4 lifecycle state is missing the required requested angle")
        base.require(application.get("reader_menu") is False, "The fresh C4 lifecycle state did not establish a closed Reader")
        if "reverse_landscape" in application:
            base.require(application["reverse_landscape"] is (expected == 270), "The fresh lifecycle state lost the requested reverse-landscape preference")
        return {"fresh_lifecycle_angle_available": True, "lifecycle_angle_field": "rotation_degrees", "observed_application_angle": application["rotation_degrees"], "expected_application_angle": expected, "fresh_reader_menu": application["reader_menu"], "final_capture_metadata_required": True}

    def stable_frame(self, session, label: str) -> Path:
        first = self.raw_frame(session, label + "-first")
        observations = []
        def stable():
            nonlocal first
            time.sleep(0.25)
            second = self.raw_frame(session, label + f"-{len(observations) + 1}")
            metric = exact_rgb(first, second)
            observations.append({"before": str(first), "after": str(second), **metric})
            first = second
            return second if metric["exact_match"] else None
        try:
            return self.wait("a stable complete physical Reader frame", stable, timeout=15, interval=0.1)
        finally:
            path = session.output / f"{label}-stability.json"
            path.write_text(json.dumps(observations, indent=2) + "\n")
            session.artifacts.append(str(path))

    def wait_half_turn(self, session, reference: Path) -> Path:
        expected = session.output / "expected-270-from-raw-90.png"
        with Image.open(reference) as frame:
            frame.convert("RGB").transpose(Image.Transpose.ROTATE_180).save(expected)
        session.artifacts.append(str(expected))
        asymmetry = exact_rgb(reference, expected)
        width, height = asymmetry["viewport_size"]
        self.orientation["physical_half_turn"] = {"before": str(reference), "expected": str(expected), "baseline_asymmetry": asymmetry}
        base.require(asymmetry["different_pixels"] > width * height * 0.05, "fixture-ordering: the baseline is insufficiently asymmetric to qualify a physical half-turn")
        observations = []
        self.orientation["physical_half_turn"]["observations"] = observations
        def rotated():
            self.reject_worker_error(session)
            path = self.raw_frame(session, f"rotation-observation-{len(observations) + 1}")
            metric = exact_rgb(expected, path)
            observations.append({"frame": str(path), **metric})
            return path if metric["exact_match"] else None
        try:
            result = self.wait("the exact 180-degree raw-frame permutation", rotated, timeout=self.arguments.timeout, interval=0.5)
            self.orientation["physical_half_turn"].update(after=str(result), comparison=observations[-1])
            return result
        finally:
            path = session.output / "rotation-observations.json"
            path.write_text(json.dumps(observations, indent=2) + "\n")
            session.artifacts.append(str(path))

    def stock_restoration(self, session, before: Path, reader: Path) -> dict[str, Any]:
        with Image.open(before) as image:
            width, height = image.size
        base.require(height > 350, "The stock body comparison has no valid physical region")
        roi = (0, 240, width, height - 110)
        independence = exact_rgb(before, reader, roi)
        evidence = {"before": str(before), "roi": list(roi), "reader_independence": independence, "scope": "Stable stock body exact RGB identity; dynamic stock system bands are reported separately"}
        self.orientation["stock_restoration"] = evidence
        base.require(independence["changed_ratio_above_16"] >= 0.15, "The prelaunch stock reference is not independent of Reader")
        observations = []
        evidence["observations"] = observations
        def restored():
            path = self.raw_frame(session, f"stock-restoration-{len(observations) + 1}")
            metric = exact_rgb(before, path, roi)
            observations.append({"frame": str(path), **metric})
            return path if metric["exact_match"] else None
        try:
            after = self.wait("exact stable stock body restoration", restored, timeout=20, interval=0.5)
            evidence.update(after=str(after), stable_body=observations[-1], full_viewport_diagnostic_only=exact_rgb(before, after))
            return evidence
        finally:
            path = session.output / "stock-restoration-observations.json"
            path.write_text(json.dumps(observations, indent=2) + "\n")
            session.artifacts.append(str(path))

    def orientation_case(self, stock_before: Path) -> None:
        result = base.Result("reader-landscape-reverse", 1)
        started = time.monotonic()
        session = None
        pointer = None
        try:
            actions = [
                base.action(0, "settings", "set", "orientation=landscape"),
                base.action(500, "settings", "set", "page-layout-mode=single"),
                base.action(650, "settings", "set", "double-tap-zoom=false"),
                base.action(800, "settings", "set", "tap-layout=thirds"),
                base.action(950, "settings", "set", "reverse-taps=false"),
                base.action(1100, "settings", "set", "reading-direction=rtl"),
                base.action(1500, "reader", "seek", "2"),
                base.action(self.arguments.reverse_at_ms, "settings", "set", "orientation=reverse-landscape"),
            ]
            launch_before = self.uptime()
            session = self.start("reader-landscape-reverse", "reader", actions, extra_env={"AIDOKU_TEST_LANDSCAPE": "1"}, wait_for_actions=False)
            warmup = self.park(session, "landscape-90-warmup")
            baseline = self.wake(session, "landscape-90-wake", baseline=warmup.parked, reference_image=warmup.before.image)
            before_app = baseline.state["application"]
            frame = before_app["displayed_reader"]
            base.require(before_app["route"] == "reader" and before_app["landscape"] is True and before_app["spread"] is False, "The initial landscape single-page Reader was not established")
            base.require(frame is not None and 1 < before_app["reader_page"] < frame["session"]["total"], "fixture-ordering: an interior displayed page is required for the exact next-page check")
            prefs = frame["preferences"]
            base.require(prefs["reading_direction"] == "rtl" and prefs["double_tap_zoom"] is False and prefs["tap_layout"] == "thirds" and prefs["reverse_taps"] is False, "The controlled RTL tap preferences were not applied")
            raw_90 = self.stable_frame(session, "landscape-90-stable")
            baseline_uptime = self.uptime()
            earliest_reverse = launch_before + self.arguments.reverse_at_ms / 1000
            base.require(baseline_uptime < earliest_reverse, "fixture-ordering: the initial 90-degree frame missed the pre-action observation window")
            before_angle = self.requested_angle_evidence(baseline.state, 90)
            self.orientation["initial_90"] = {"raw_frame": str(raw_90), "state": str(baseline.state_path), "process": self.same_lifetime(session), "angle_evidence": before_angle, "requested_setup_page": 2, "observed_displayed_page": before_app["reader_page"], "launch_before_guest_uptime": launch_before, "baseline_guest_uptime": baseline_uptime, "earliest_reverse_guest_uptime": earliest_reverse, "timing_contract": "The application active clock starts after launch_before and excludes its sleep holds, so this baseline precedes the timed reverse action. All later assertions use the actual accepted interior page, including a checkpoint-cancelled setup seek"}
            self.wait("the earliest possible reverse-action window", lambda: self.uptime() >= earliest_reverse, timeout=self.arguments.reverse_at_ms / 1000 + 10, interval=2)
            raw_270 = self.wait_half_turn(session, raw_90)
            self.same_lifetime(session)
            at_270 = self.capture(session, "reverse-270-before-input")
            base.require(exact_rgb(raw_270, at_270.image)["exact_match"], "The reverse frame did not remain stable before its checkpoint")
            angle_sleep = self.park(session, "reverse-270-angle-checkpoint", before_capture=at_270)
            after_app = angle_sleep.parked.state["application"]
            self.orientation["angle_only"] = {"fresh_checkpoint": str(angle_sleep.parked.state_path), "reader_jobs_before": before_app["reader_image_jobs_started"], "reader_jobs_after": after_app["reader_image_jobs_started"], "sleep": self.park_checks(angle_sleep), "scope": "The no-new-image-job assertion is measured at a real checkpoint before any QMP tap"}
            angle_stable = self.compare_application(before_app, after_app, stable_jobs=True)
            after_angle = self.requested_angle_evidence(angle_sleep.parked.state, 270)
            self.orientation["angle_only"].update(retained_state=angle_stable, angle_evidence=after_angle)
            reverse_awake = self.wake(session, "reverse-270-angle-wake", baseline=angle_sleep.parked, reference_image=raw_270)
            self.compare_application(after_app, reverse_awake.state["application"], stable_jobs=True)
            self.orientation["angle_only"].update(font_initializations=reverse_awake.state["font_initializations"], same_pid=reverse_awake.state["pid"] == baseline.state["pid"])
            raw_before_tap = self.stable_frame(session, "reverse-270-before-tap")
            with Image.open(raw_before_tap) as image:
                width, height = image.size
            base.require(width % 2 == height % 2 == 0, "The native orientation check requires the observed scale-2 viewport")
            pointer = RotatedPointer(self, session, width, height)
            logical_width, logical_height = height / 2, width / 2
            tap = pointer.tap_270(logical_width * 0.12, logical_height * 0.5)
            tap_observations = []
            self.orientation["tap_270"] = {"input": tap, "raw_observations": tap_observations, "page_before": after_app["reader_page"], "reader_jobs_before": after_app["reader_image_jobs_started"]}
            def changed():
                path = self.raw_frame(session, f"reverse-270-after-tap-{len(tap_observations) + 1}")
                metric = exact_rgb(raw_before_tap, path)
                tap_observations.append({"frame": str(path), **metric})
                return path if metric["changed_ratio_above_16"] > 0.002 else None
            self.wait("a real visible result from the 270-degree QMP tap", changed, timeout=20, interval=0.25)
            raw_after_tap = self.stable_frame(session, "reverse-270-page-after-tap")
            after_tap = self.capture(session, "reverse-270-tap-reference")
            base.require(exact_rgb(raw_after_tap, after_tap.image)["exact_match"], "The post-tap image was not stable before sleep")
            tap_sleep = self.park(session, "reverse-270-page-sleep", before_capture=after_tap)
            tapped_app = tap_sleep.parked.state["application"]
            self.orientation["tap_270"].update(fresh_checkpoint=str(tap_sleep.parked.state_path), page_after=tapped_app["reader_page"], chapter=tapped_app["current_chapter"], reader_jobs_after=tapped_app["reader_image_jobs_started"])
            self.orientation["sleep_after_tap"] = self.park_checks(tap_sleep)
            base.require(tapped_app["current_chapter"] == after_app["current_chapter"] and tapped_app["reader_page"] == after_app["reader_page"] + 1, "The 270-degree hardware tap did not turn exactly one page in the same chapter")
            base.require(tapped_app["reader_offset"] == 0 and tapped_app["reader_zoom"] == 1.0, "The hardware tap changed zoom or entered a vertical offset")
            after_tap_angle = self.requested_angle_evidence(tap_sleep.parked.state, 270)
            self.orientation["tap_270"]["angle_evidence"] = after_tap_angle
            final_awake = self.wake(session, "reverse-270-page-wake", baseline=tap_sleep.parked, reference_image=after_tap.image)
            self.compare_application(tapped_app, final_awake.state["application"], stable_jobs=True)
            self.same_lifetime(session)
            exited = self.stop(session)
            final = exited["capture"]["state"]
            metadata = exited["capture"]["metadata"]
            self.orientation.update(final_rotation_metadata=metadata, final_snapshot_rotation_degrees=final.get("display_rotation_degrees"), exit=exited)
            base.require(metadata["rotation_degrees"] == 270 and final["display_rotation_degrees"] == 270, "Final metadata/snapshot did not preserve actual application rotation 270")
            base.require(metadata["process_id"] == session.pid and metadata["font_initializations"] == 1, "Rotation reinitialized the process or its fonts")
            base.require(metadata["logical_size"] == [height // 2, width // 2] and metadata["window_size"] == [height, width] and metadata["scale_factor"] == 2, "The requested landscape viewport was confused with raw portrait scanout geometry")
            with Image.open(exited["capture"]["image"]) as snapshot:
                base.require(list(snapshot.size) == metadata["snapshot_size"], "The Slint snapshot disagrees with its own geometry metadata")
            base.require(final["menu"] is False and final["route"] == "reader", "The final reverse Reader was not closed and image-only")
            base.require(final["preferences"]["landscape"] is True and final["preferences"]["reverse_landscape"] is True, "The manual reverse orientation preference was not retained")
            base.require(final["reader_effective"]["landscape"] is True and final["reader_effective"]["reverse_landscape"] is True, "The effective Reader lost its reverse orientation")
            base.require(final["current_chapter"] == tapped_app["current_chapter"] and final["reader_page"] == tapped_app["reader_page"], "Sleep/wake changed the accepted reverse Reader page")
            self.orientation["final_closed_reader"] = True
            self.stock_restoration(session, stock_before, raw_90)
            result.checks = self.orientation
            result.passed = True
        except Exception as error:
            result.error = str(error)
            result.checks = self.orientation
            session = session or self.current
            if session is not None:
                try:
                    self.capture(session, "orientation-failure", allow_missing=True)
                except Exception as capture_error:
                    result.checks["failure_capture_error"] = str(capture_error)
        finally:
            session = session or self.current
            errors = []
            if pointer is not None:
                try:
                    pointer.release()
                except Exception as error:
                    errors.append(f"Release global QMP touch: {error}")
                    self.unsafe_input = pointer.held
                finally:
                    try:
                        pointer.save()
                    except Exception as error:
                        errors.append(f"Save QMP input evidence: {error}")
            if session is not None:
                try:
                    self.cleanup(session)
                except Exception as error:
                    errors.append(f"Close owned orientation session: {error}")
                result.artifacts = list(dict.fromkeys(session.artifacts))
            if errors:
                result.cleanup_error = "; ".join(errors)
                result.passed = False
            result.duration_seconds = round(time.monotonic() - started, 3)
            self.results.append(result)
            self.write_report()
            print(f"{'PASS' if result.passed else 'FAIL'} {result.name}" + (f": {result.error or result.cleanup_error}" if not result.passed else ""), flush=True)

    def write_report(self, setup_error: str | None = None) -> None:
        super().write_report(setup_error)
        path = self.output / "report.json"
        report = json.loads(path.read_text())
        policy = self.screensaver_policy or {}
        accepted = len(self.results) == 1 and self.results[0].passed and policy.get("restored") is True and not report.get("setup_error") and not self.unsafe_input
        report.update({
            "kind": "native-reader-manual-orientation", "accepted": accepted,
            "counts_towards_required_twelve_cases": False,
            "planned_real_sleep_cycles": ["90-degree baseline warmup", "270-degree angle-only checkpoint", "270-degree next-page retention"],
            "candidate_label": self.arguments.candidate_label,
            "expected_input_sha256": {"binary": self.arguments.expected_binary_sha256, "launcher": self.arguments.expected_launcher_sha256},
            "source_snapshot_sha256_declared_by_root": self.arguments.source_snapshot_sha256,
            "source_contract_sha256": self.contract_sources,
            "orientation": self.orientation,
            "global_qmp_input_cleanup_incomplete": self.unsafe_input,
        })
        report["coverage_limits"].extend([
            "The relative raw-frame oracle is independent of the cached hardware launch offset; metadata reports application angle only.",
            "Initial 90-degree ownership is established by the confirmed startup action, pre-action deadline, and strict fresh lifecycle rotation/menu fields. Final capture metadata must independently report 270.",
            "Angle-only image-job counts are sampled at a real checkpoint before input; stale initial lifecycle records are never treated as fresh application snapshots.",
            "Closed-Reader state and complete raw-frame identity are checked here; the separate full-viewport processed-image oracle remains independent UI coverage.",
        ])
        path.write_text(json.dumps(report, indent=2) + "\n")

    def run(self) -> int:
        try:
            self.freeze_contract()
            self.acquire_guard()
            self.prepare_round(1, variant="native-orientation")
            self.orientation["hardware_before_launch"] = self.hardware_observation("before-launch")
            stock_before = self.output / "stock-before.png"
            self.qmp("screendump", str(stock_before))
            self.orientation_case(stock_before)
        except Exception as error:
            self.suite_error = str(error)
            raise
        finally:
            self.finish_suite(release=not self.unsafe_input)
        report = json.loads((self.output / "report.json").read_text())
        print(json.dumps({"report": str(self.output / 'report.json'), "accepted": report["accepted"], "counts_towards_required_twelve_cases": False}), flush=True)
        return 0 if report["accepted"] else 1


def main() -> int:
    parser = base.build_parser(require_source_snapshot=True)
    parser.description = __doc__
    parser.set_defaults(output_dir=Path("artifacts/phase3/native-orientation"), rounds=1)
    parser.add_argument("--expected-binary-sha256", required=True)
    parser.add_argument("--expected-launcher-sha256", required=True)
    parser.add_argument("--candidate-label", default="C4")
    parser.add_argument("--reverse-at-ms", type=int, default=90000, help="Application active-clock deadline for the actual reverse-landscape settings action")
    parser.add_argument("--functional-matrix-root", type=Path, default=Path("artifacts/phase3/native-matrix"), help="Reject placing the extra orientation report under the required twelve-case matrix")
    arguments = parser.parse_args()
    if os.name != "posix":
        parser.error("Run only on the designated remote Linux worker")
    if arguments.rounds != 1 or arguments.cases or arguments.timeout < 30:
        parser.error("Use one independent orientation case and a timeout of at least 30 seconds")
    if not 30000 <= arguments.reverse_at_ms <= 120000:
        parser.error("The reverse action must allow a bounded 30..120 second observation window")
    for field in ("expected_binary_sha256", "expected_launcher_sha256", "source_snapshot_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", getattr(arguments, field)):
            parser.error("Frozen input identities must be lowercase SHA-256 values")
    if arguments.output_dir.resolve().is_relative_to(arguments.functional_matrix_root.resolve()):
        parser.error("Orientation evidence must remain outside the required twelve-case matrix")
    verifier = None
    try:
        verifier = OrientationVerifier(arguments)
        return verifier.run()
    except Exception as error:
        print(f"Native orientation verification failed: {error}", file=sys.stderr)
        if verifier is not None:
            verifier.write_report(setup_error=str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
