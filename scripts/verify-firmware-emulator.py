#!/usr/bin/env python3
"""Exercise the native ARM app through an isolated Aidoku QEMU guest and evdev.

Run only on the designated remote Linux worker. Logical action replay is not
used: the QMP touch device feeds the guest kernel and native input backend.
Firmware screenshots, app snapshots, and exported app state remain separate
evidence. Synthetic panel waveforms cannot establish physical Kindle quality.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import dataclasses
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import subprocess
import sys
import time
from typing import Any

from PIL import Image, ImageChops


STARTUP_CASE = "startup-library"
STARTUP_GEOMETRY = {"scribe": (1860, 2480), "pw6": (1272, 1696)}
STARTUP_BOOKS = ("Firmware fixture one", "Firmware fixture two")
SNAPSHOT_EVENTS = ["startup_toolbar_probe_started", "startup_toolbar_probe_ready", "startup_toolbar_snapshot_used"]
LIBRARY_SUBMISSION = re.compile(
    r"^aidoku: startup_library_submitted boottime_us=(\d+) sequence=(\d+) marker=(\d+) "
    r"layout=(large|compact) library=nonempty grid=nonempty$", re.MULTILINE
)


@dataclasses.dataclass
class Result:
    name: str
    passed: bool = False
    duration_seconds: float = 0
    artifacts: list[str] = dataclasses.field(default_factory=list)
    checks: dict[str, Any] = dataclasses.field(default_factory=dict)
    error: str | None = None


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def difference(first: Path, second: Path) -> float:
    with Image.open(first) as left, Image.open(second) as right:
        require(left.size == right.size, "Compared native frames have different geometry")
        histogram = ImageChops.difference(left.convert("L"), right.convert("L")).histogram()
        return sum(histogram[17:]) / (left.width * left.height)


def startup_library_fixture() -> bytes:
    with closing(sqlite3.connect(":memory:")) as database:
        database.executescript("""
            CREATE TABLE books (
                id TEXT PRIMARY KEY, source_id TEXT NOT NULL, remote_id TEXT NOT NULL,
                title TEXT NOT NULL, in_library INTEGER NOT NULL, category TEXT NOT NULL,
                updated_at INTEGER NOT NULL, document TEXT NOT NULL, UNIQUE(source_id, remote_id)
            );
            CREATE TABLE preferences (key TEXT PRIMARY KEY, document TEXT NOT NULL);
            INSERT INTO preferences VALUES ('application', '{"language":"en"}');
        """)
        for index, title in enumerate(STARTUP_BOOKS, 1):
            remote_id = f"firmware-{index}"
            book = dict(id=f"5:local:{remote_id}", source_id="local", remote_id=remote_id,
                        title=title, author="Fixture", description="", status="", cover="",
                        chapter_count=0, unread=0, category="", in_library=True,
                        updated_at=1700000000 + index)
            database.execute("INSERT INTO books VALUES (?, ?, ?, ?, 1, '', ?, ?)",
                             (book["id"], "local", remote_id, title, book["updated_at"], json.dumps(book)))
        database.commit()
        return database.serialize()


def startup_evidence(log: str, trace: dict[str, Any], profile: str) -> dict[str, Any]:
    stages = ("observer_start", "observer_wait_begin", "observer_ready", "portrait_begin",
              "portrait_ready", "display_begin", "display_ready", "app_spawn_begin", "app_spawn_returned")
    launcher = re.findall(r"^aidoku: launcher-diag v1 (\w+) (\d+\.\d+)$", log, re.MULTILINE)
    require([stage for stage, _ in launcher if stage in stages] == list(stages),
            "Launcher evidence did not put observer admission before portrait, display and GUI")
    require(trace["success"] is True and trace["finished"] is True and trace["dropped_events"] == 0,
            "The native app did not complete and flush its whole startup trace")
    events = trace["events"]
    stages = ("startup_initial_frame_submitted", "startup_initial_input_ready",
              "startup_ready", "startup_library_frame_submitted")
    checkpoints = [event for event in events if event["name"] in stages]
    require([event["name"] for event in checkpoints] == list(stages),
            "The startup trace is missing, repeats or reorders a Library checkpoint")
    initial, fence, ready, library = checkpoints
    require(initial["full"] is True and initial["frame_sequence"] == fence["frame_sequence"]
            and initial["marker"] > 0 and initial["marker"] == fence["marker"]
            and initial["at_us"] <= fence["at_us"] <= ready["at_us"] <= library["at_us"]
            and initial["frame_sequence"] < library["frame_sequence"],
            "Native Library submission did not follow the initial full-frame/input barrier")
    submissions = LIBRARY_SUBMISSION.findall(log)
    expected_layout = "large" if profile == "scribe" else "compact"
    require(len(submissions) == 1 and submissions[0][1:] == (str(library["frame_sequence"]),
                                                             str(library["marker"]), expected_layout),
            "The populated Library submission did not match the trace and expected screen layout")
    toolbar = [event for event in events if event["name"].startswith("startup_toolbar_")]
    snapshot_used = [event["name"] for event in toolbar] == SNAPSHOT_EVENTS and (
        initial["at_us"] <= toolbar[0]["at_us"] <= toolbar[1]["at_us"] <= toolbar[2]["at_us"] <= ready["at_us"]
    )
    return {"launcher_uptime_seconds": dict(launcher), "layout": expected_layout, "initial_frame": initial,
            "input_ready": fence, "library_frame": library, "toolbar_events": toolbar,
            "toolbar_snapshot_used": snapshot_used}


class Verifier:
    def __init__(self, arguments: argparse.Namespace) -> None:
        self.arguments = arguments
        self.repository = Path(__file__).resolve().parents[1]
        self.binary = arguments.binary.resolve(strict=True)
        with self.binary.open("rb") as binary:
            header = binary.read(20)
        require(len(header) == 20 and header[:6] == b"\x7fELF\x01\x01", "A 32-bit little-endian ELF is required")
        require(int.from_bytes(header[18:20], "little") == 40, "The executable is not ARM")
        self.launcher = self.repository / "packaging/kindle/launch.sh"
        self.wrapper = self.repository / "packaging/tests/emulator-wrapper.sh"
        self.guest_ssh = arguments.guest_ssh.resolve(strict=True)
        self.emulator_root = arguments.emulator_root.resolve(strict=True)
        self.qmp_script = self.emulator_root / "scripts/eink-qmp.py"
        require(self.qmp_script.is_file(), "The emulator QMP helper is missing")
        require(re.fullmatch(r"aidoku-[a-z0-9_-]+", arguments.machine) is not None, "Only isolated Aidoku machines may be tested")
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.run_id = f"run-{stamp}-{os.getpid()}"
        self.output = arguments.output_dir.resolve() / self.run_id
        self.output.mkdir(parents=True, exist_ok=False)
        self.guest_root = f"/mnt/us/aidoku-native-tests/{self.run_id}"
        self.results: list[Result] = []
        self.reader_baseline: int | None = None
        self.reader_initial_menu: bool | None = None
        self.environment: dict[str, Any] = {}
        self.tap_map = read_json(arguments.tap_map) if arguments.tap_map else {}

    def guest(self, command: str, *, payload: bytes | None = None, timeout: float = 15) -> bytes:
        process = subprocess.run(
            [str(self.guest_ssh), command],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        require(process.returncode == 0, f"Guest command failed ({process.returncode}): {process.stderr.decode(errors='replace')[-2000:]}")
        return process.stdout

    def qmp(self, *arguments: str) -> dict[str, Any]:
        process = subprocess.run(
            [sys.executable, str(self.qmp_script), "--machine", self.arguments.machine, *arguments],
            cwd=self.emulator_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
            check=False,
        )
        require(process.returncode == 0, f"QMP command failed: {process.stderr[-2000:]}")
        return json.loads(process.stdout) if process.stdout.strip() else {}

    def put(self, source: Path, destination: str, mode: str = "644") -> None:
        self.guest(f"cat >{shlex.quote(destination)} && chmod {mode} {shlex.quote(destination)}", payload=source.read_bytes(), timeout=120)

    def retrieve(self, guest_path: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.guest(f"cat {shlex.quote(guest_path)}", timeout=60))

    def inspect_guest(self) -> None:
        marker = self.guest("cat /var/local/aidoku-emulator-id").decode().strip()
        require(marker == self.arguments.machine, "The guest identity marker does not match the selected Aidoku QMP machine")
        firmware = self.guest("cat /etc/prettyversion.txt").decode().strip()
        require(re.search(rf"\b{re.escape(self.arguments.firmware)}\b", firmware) is not None, f"Unexpected guest firmware: {firmware}")
        require(self.qmp("status").get("running") is True, "The isolated emulator is not running")
        mount = self.guest("awk '$2 == \"/mnt/us\" { mount = $0 } END { print mount }' /proc/mounts").decode().strip()
        require(len(mount.split()) >= 4 and "noexec" not in mount.split()[3].split(","),
                "The effective guest userstore mount is missing or not executable")
        self.environment = {
            "machine": marker,
            "firmware": firmware,
            "kernel": self.guest("uname -a").decode().strip(),
            "mount": mount,
            "identity_marker": "/var/local/aidoku-emulator-id",
            "qmp": self.qmp("display"),
        }
        require(self.guest("if [ -e /tmp/aidoku-launcher.lock ]; then printf busy; else printf free; fi").decode() == "free", "An existing Aidoku launcher owns the display; it will not be interrupted")

    def capture_stock(self) -> None:
        self.qmp("screendump", str(self.output / "stock-before.png"))
        with Image.open(self.output / "stock-before.png") as frame:
            self.native_size = frame.size
        self.logical_size = (self.native_size[0] // 2, self.native_size[1] // 2)

    def prepare(self) -> None:
        self.inspect_guest()
        self.binary_sha256 = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.launcher_sha256 = hashlib.sha256(self.launcher.read_bytes()).hexdigest()
        self.wrapper_sha256 = hashlib.sha256(self.wrapper.read_bytes()).hexdigest()
        self.guest(f"mkdir -p {shlex.quote(self.guest_root + '/app')} {shlex.quote(self.guest_root + '/data/logs')}")
        self.put(self.binary, self.guest_root + "/app/aidoku.native", "755")
        self.put(self.wrapper, self.guest_root + "/app/aidoku", "755")
        self.put(self.launcher, self.guest_root + "/launch.sh", "755")
        self.guest(f"printf '%s\\n' {shlex.quote(self.arguments.profile)} >{shlex.quote(self.guest_root + '/data/device-profile')}")
        observed_hash = self.guest(f"sha256sum {shlex.quote(self.guest_root + '/app/aidoku.native')}").decode().split()[0]
        require(observed_hash == self.binary_sha256, "The deployed ARM binary hash does not match the tested artifact")
        self.capture_stock()

    def touch(self, name: str, default: tuple[float, float]) -> None:
        logical = self.tap_map.get(name, default)
        require(isinstance(logical, (list, tuple)) and len(logical) == 2, f"Invalid logical touch point: {name}")
        x, y = round(float(logical[0]) * 2), round(float(logical[1]) * 2)
        require(0 <= x < self.native_size[0] and 0 <= y < self.native_size[1], f"Touch point is outside the actual framebuffer: {name}")
        self.qmp("tap", str(x), str(y), "--hold", "0.10")
        time.sleep(0.9)

    def drag(self, name: str, start: tuple[float, float], end: tuple[float, float]) -> dict[str, Any]:
        coordinates = self.tap_map.get(name, [*start, *end])
        require(isinstance(coordinates, (list, tuple)) and len(coordinates) == 4, f"Invalid logical drag points: {name}")
        physical = [round(float(value) * 2) for value in coordinates]
        for x, y in ((physical[0], physical[1]), (physical[2], physical[3])):
            require(0 <= x < self.native_size[0] and 0 <= y < self.native_size[1], f"Drag point is outside the visible framebuffer: {name}")
        if str(self.emulator_root) not in sys.path:
            sys.path.insert(0, str(self.emulator_root))
        from eink_emulator.qmp import QMPClient

        def position(x: int, y: int) -> list[dict[str, Any]]:
            # Match the pinned emulator helper's inverse-ceiling conversion.
            max_x, max_y = self.native_size[0] - 1, self.native_size[1] - 1
            return [
                {"type": "abs", "data": {"axis": "x", "value": (x * 0x7FFF + max_x - 1) // max_x}},
                {"type": "abs", "data": {"axis": "y", "value": (y * 0x7FFF + max_y - 1) // max_y}},
            ]

        socket = self.emulator_root / "machines" / self.arguments.machine / f"{self.arguments.machine}.qmp.sock"
        with QMPClient(socket, 5) as client:
            started = time.monotonic()
            try:
                client.execute("input-send-event", {"events": [
                    *position(physical[0], physical[1]),
                    {"type": "btn", "data": {"button": "left", "down": True}},
                ]})
                for step in range(1, 7):
                    time.sleep(0.04)
                    x = round(physical[0] + (physical[2] - physical[0]) * step / 6)
                    y = round(physical[1] + (physical[3] - physical[1]) * step / 6)
                    client.execute("input-send-event", {"events": position(x, y)})
            finally:
                client.execute("input-send-event", {"events": [
                    {"type": "btn", "data": {"button": "left", "down": False}},
                ]})
            elapsed = time.monotonic() - started
        require(elapsed <= 0.9, f"The QMP drag exceeded the application swipe duration: {elapsed:.3f}s")
        time.sleep(0.9)
        return {"name": name, "logical": list(coordinates), "physical": physical, "duration_seconds": round(elapsed, 4), "transport": "One QMP connection; six absolute moves while the physical touch remains down"}

    def stop_owned_launcher(self) -> None:
        # Match our unique full launcher path before signaling a recorded PID.
        root = shlex.quote(self.guest_root + "/launch.sh")
        command = (
            "if [ -r /tmp/aidoku-launcher.lock/pid ]; then "
            "aidoku_pid=$(cat /tmp/aidoku-launcher.lock/pid); "
            "case \"$aidoku_pid\" in ''|*[!0-9]*) exit 0;; esac; "
            f"if tr '\\000' '\\n' </proc/$aidoku_pid/cmdline | grep -Fx {root} >/dev/null; then "
            "kill -TERM \"$aidoku_pid\"; fi; fi"
        )
        try:
            self.guest(command)
        except Exception:
            pass

    def service_state(self) -> dict[str, Any]:
        raw = self.guest(
            "printf 'STATUSBAR='; status statusbar 2>/dev/null || :; printf '\\n'; "
            "for aidoku_pid in $(pidof awesome 2>/dev/null); do "
            "printf 'AWESOME=%s:' \"$aidoku_pid\"; "
            "sed -n 's/^State:[[:space:]]*//p' /proc/$aidoku_pid/status; done"
        ).decode()
        return {
            "raw": raw,
            "statusbar_running": "start/running" in raw,
            "awesome_stopped": any(re.search(r"^AWESOME=\d+:[Tt]", line) for line in raw.splitlines()),
        }

    def run_startup_case(self, helper: bool) -> Result:
        name = f"{STARTUP_CASE}-with-helper" if helper else STARTUP_CASE
        result = Result(name=name)
        started = time.monotonic()
        root = shlex.quote(self.guest_root)
        log_path = self.output / f"{name}.log"
        result.artifacts.append(str(log_path))
        process: subprocess.Popen[bytes] | None = None
        paths = {label: self.output / f"{name}.{label}" for label in ("trace.json", "metadata.json", "state.json", "app.png")}
        guest_artifacts = {"trace.json": "startup-trace.json", "metadata.json": "native-library.metadata.json",
                           "state.json": "native-library.state.json", "app.png": "native-library.png"}
        try:
            baseline_services = self.service_state()
            require(baseline_services["statusbar_running"] and not baseline_services["awesome_stopped"]
                    and "AWESOME=" in baseline_services["raw"], "The stock guest desktop is not ready")
            profile = shlex.quote(self.arguments.profile)
            self.guest(f"set -eu; mkdir {root}/data; mkdir {root}/data/logs; "
                       f"printf '%s\\n' {profile} > {root}/data/device-profile; "
                       f"printf '%s\\n' {self.arguments.duration_ms} > {root}/data/logs/test-duration-ms; "
                       f": > {root}/data/logs/test-startup-library")
            self.guest(f"cat > {root}/data/library.sqlite3", payload=startup_library_fixture())
            stock = self.output / f"{name}.stock-before.png"
            self.qmp("screendump", str(stock))
            result.artifacts.append(str(stock))
            command = ("unset AIDOKU_LOCK_DIR AIDOKU_FRAMEBUFFER AIDOKU_CATALOG_NOTIFY_HELPER AIDOKU_CATALOG_NOTIFY_SHA256; "
                       f"export AIDOKU_HOME={root} AIDOKU_MANAGED_LIBRARY=1 AIDOKU_LAUNCHER_DIAGNOSTICS=launcher-v1; "
                       f"exec sh {root}/launch.sh --foreground")
            with log_path.open("wb") as log:
                process = subprocess.Popen([str(self.guest_ssh), command], stdout=log, stderr=subprocess.STDOUT)
                deadline = time.monotonic() + self.arguments.startup_seconds
                while not LIBRARY_SUBMISSION.search(log_path.read_text(errors="replace")):
                    require(process.poll() is None, "The launcher exited before the populated Library submitted a frame")
                    require(time.monotonic() < deadline, "Timed out waiting for the populated Library submission")
                    time.sleep(0.1)
                frame = self.output / f"{name}.framebuffer.png"
                capture_deadline = time.monotonic() + 5
                while True:
                    self.qmp("screendump", str(frame))
                    changed = difference(stock, frame)
                    if changed > 0.0005 or time.monotonic() >= capture_deadline or process.poll() is not None:
                        break
                    time.sleep(0.1)
                result.artifacts.append(str(frame))
                require(process.poll() is None, "The launcher exited before QMP captured the app framebuffer")
                require(process.wait(timeout=self.arguments.duration_ms / 1000 + 15) == 0,
                        "The launcher did not exit successfully after the controlled application deadline")
            for label, path in paths.items():
                self.retrieve(f"{self.guest_root}/data/logs/{guest_artifacts[label]}", path)
                result.artifacts.append(str(path))
            metadata = read_json(paths["metadata.json"])
            state = read_json(paths["state.json"])
            trace = read_json(paths["trace.json"])
            require(metadata["backend"] == "kindle" and metadata["profile"] == self.arguments.profile
                    and tuple(metadata["window_size"]) == self.native_size and metadata["scale_factor"] == 2
                    and tuple(metadata["logical_size"]) == self.logical_size
                    and metadata["process_id"] == trace["process_id"],
                    "The application capture does not match the native guest geometry and traced process")
            require(state["route"] == "library" and state["demo"] is False and state["offline"] is True
                    and state["database"] == f"{self.guest_root}/data/library.sqlite3"
                    and state["library_count"] == len(STARTUP_BOOKS)
                    and sorted(book["title"] for book in state["visible_books"]) == sorted(STARTUP_BOOKS),
                    "The native app did not project the isolated non-demo Library fixture")
            with Image.open(frame) as scanout, Image.open(paths["app.png"]) as snapshot:
                require(scanout.size == self.native_size and scanout.convert("L").getextrema()[0] < 85
                        and snapshot.size == tuple(metadata["snapshot_size"]), "Invalid native framebuffer or app snapshot")
            require(changed > 0.0005, "QMP did not observe a visible change from the stock framebuffer")
            result.checks = startup_evidence(log_path.read_text(errors="replace"), trace, self.arguments.profile)
            result.checks.update({"baseline_services": baseline_services, "metadata": metadata,
                                  "framebuffer_change_ratio": changed, "visible_books": state["visible_books"]})
            if helper and self.arguments.require_toolbar_snapshot:
                require(result.checks["toolbar_snapshot_used"], "The first Library did not use a toolbar snapshot")
        except Exception as error:
            result.error = str(error)
        finally:
            if process is not None and process.poll() is None:
                self.stop_owned_launcher()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            if process is not None and result.error:
                paths["portrait.stderr"] = self.output / f"{name}.portrait.stderr"
                guest_artifacts["portrait.stderr"] = "native-portrait.stderr"
                for label, path in paths.items():
                    if not path.exists():
                        try:
                            self.retrieve(f"{self.guest_root}/data/logs/{guest_artifacts[label]}", path)
                        except Exception:
                            continue
                        result.artifacts.append(str(path))
            try:
                self.guest(f"test ! -e /tmp/aidoku-launcher.lock && rm -rf {root}/data")
                result.checks["launcher_lock_removed"] = True
                after = self.service_state()
                result.checks["restored_services"] = after
                require(after["statusbar_running"] and not after["awesome_stopped"] and "AWESOME=" in after["raw"],
                        "The launcher did not restore the stock guest desktop")
                result.checks["desktop_restored"] = True
            except Exception as error:
                result.error = "; ".join(filter(None, (result.error, f"Cleanup verification: {error}")))
        result.passed = result.error is None
        result.duration_seconds = round(time.monotonic() - started, 3)
        print(f"{'PASS' if result.passed else 'FAIL'} {name}" + (f": {result.error}" if result.error else ""), flush=True)
        return result

    def run_startup_library(self) -> int:
        self.inspect_guest()
        self.capture_stock()
        require(self.native_size == STARTUP_GEOMETRY[self.arguments.profile],
                f"Unexpected visible firmware framebuffer: {self.native_size}")
        root = shlex.quote(self.guest_root)
        self.guest(f"set -eu; umask 077; mkdir -p /mnt/us/aidoku-native-tests; "
                   f"test ! -L /mnt/us/aidoku-native-tests; mkdir {root}")
        cleaned = False
        error = None
        try:
            self.guest(f"mkdir {root}/app")
            files = [(self.binary, "app/aidoku.native"), (self.wrapper, "app/aidoku"), (self.launcher, "launch.sh")]
            for source, target in files:
                destination = f"{self.guest_root}/{target}"
                self.put(source, destination, "755")
                self.guest(f"cmp -s - {shlex.quote(destination)}", payload=source.read_bytes(), timeout=120)
            self.guest(f"test ! -e {root}/app/aidoku-native-query")
            baseline = self.run_startup_case(False)
            self.results.append(baseline)
            if self.arguments.native_query and baseline.checks.get("desktop_restored"):
                destination = f"{self.guest_root}/app/aidoku-native-query"
                self.put(self.arguments.native_query, destination, "755")
                self.guest(f"cmp -s - {shlex.quote(destination)}", payload=self.arguments.native_query.read_bytes())
                self.results.append(self.run_startup_case(True))
        except Exception as failure:
            error = str(failure)
        finally:
            try:
                self.guest(f"test ! -e /tmp/aidoku-launcher.lock && rm -rf {root} && test ! -e {root}")
                cleaned = True
            except Exception as failure:
                error = "; ".join(filter(None, (error, f"Fixture cleanup: {failure}")))
        passed = sum(result.passed for result in self.results)
        required = 2 if self.arguments.native_query else 1
        report = {"environment": self.environment, "binary": str(self.binary), "profile": self.arguments.profile,
                  "launcher": str(self.launcher), "guest_fixture_cleaned": cleaned, "error": error,
                  "native_size": self.native_size, "logical_size": self.logical_size,
                  "passed": passed, "failed": required - passed, "cases": [dataclasses.asdict(result) for result in self.results],
                  "scope": "Managed foreground launcher in full-system QEMU; no stock card tap or physical timing."}
        report_path = self.output / "report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"report": str(report_path), "passed": passed, "required": required, "error": error}), flush=True)
        return 0 if passed == required and error is None and cleaned else 1

    def run_case(self, name: str, screen: str, touches: list[tuple[str, tuple[float, float]]], expected_route: str, *, menu: bool | None = None, page_advance: bool = False, drags: list[tuple[str, tuple[float, float], tuple[float, float]]] = (), expected_page: int | None = None) -> None:
        result = Result(name=name)
        started = time.monotonic()
        process: subprocess.Popen[bytes] | None = None
        log_path = self.output / f"{name}.log"
        capture_stem = self.guest_root + f"/data/logs/native-{screen}"
        baseline_services = self.service_state()
        drag_checks = []
        drag_changes = []
        try:
            self.guest(
                f"printf '%s\\n' {shlex.quote(screen)} >{shlex.quote(self.guest_root + '/data/logs/test-screen')}; "
                f"printf '%s\\n' {self.arguments.duration_ms} >{shlex.quote(self.guest_root + '/data/logs/test-duration-ms')}; "
                f"rm -f {shlex.quote(capture_stem + '.png')} {shlex.quote(capture_stem + '.metadata.json')} {shlex.quote(capture_stem + '.state.json')}"
            )
            command = f"AIDOKU_HOME={shlex.quote(self.guest_root)} sh {shlex.quote(self.guest_root + '/launch.sh')} --foreground"
            with log_path.open("wb") as log:
                process = subprocess.Popen([str(self.guest_ssh), command], stdout=log, stderr=subprocess.STDOUT)
                deadline = time.monotonic() + self.arguments.startup_seconds
                ready = False
                while time.monotonic() < deadline:
                    require(process.poll() is None, "The application exited before native input could be injected")
                    if "aidoku: native window ready" in log_path.read_text(errors="replace"):
                        ready = True
                        break
                    time.sleep(0.25)
                require(ready, "The native app did not announce its initialized window before the startup deadline")
                # The ready marker precedes the first event-loop draw. Let that
                # draw and the first background image result reach the panel.
                time.sleep(0.7)
                before = self.output / f"{name}.framebuffer-before.png"
                self.qmp("screendump", str(before))
                result.artifacts.append(str(before))
                for touch_name, default in touches:
                    self.touch(touch_name, default)
                previous = before
                for index, (drag_name, start, end) in enumerate(drags):
                    drag_checks.append(self.drag(drag_name, start, end))
                    intermediate = self.output / f"{name}.drag-{index + 1}.png"
                    self.qmp("screendump", str(intermediate))
                    result.artifacts.append(str(intermediate))
                    drag_changes.append(difference(previous, intermediate))
                    previous = intermediate
                frame = self.output / f"{name}.framebuffer-after.png"
                self.qmp("screendump", str(frame))
                result.artifacts.append(str(frame))
                returncode = process.wait(timeout=self.arguments.duration_ms / 1000 + 15)
            require(returncode == 0, f"The native app/launcher exited with {returncode}; see {log_path}")
            snapshot = self.output / f"{name}.app.png"
            metadata_path = self.output / f"{name}.metadata.json"
            state_path = self.output / f"{name}.state.json"
            for suffix, destination in ((".png", snapshot), (".metadata.json", metadata_path), (".state.json", state_path)):
                self.retrieve(capture_stem + suffix, destination)
                result.artifacts.append(str(destination))
            metadata, state = read_json(metadata_path), read_json(state_path)
            require("kindle" in metadata["backend"].lower(), "The capture did not use the native Kindle backend")
            require(metadata["profile"] == self.arguments.profile, "The captured profile differs from the selected profile")
            require(tuple(metadata["window_size"]) == self.native_size, "The native app window differs from the visible framebuffer")
            require(tuple(metadata["logical_size"]) == self.logical_size, "The native logical viewport does not match scale 2")
            with Image.open(snapshot) as image:
                require(image.size == tuple(metadata["snapshot_size"]), "The Slint snapshot has incorrect geometry metadata")
                require(image.convert("L").getextrema()[0] < 85, "The snapshot contains no readable dark content")
            require(state["route"] == expected_route, f"Native touches ended at {state['route']!r}, expected {expected_route!r}")
            if menu is not None:
                require(state["menu"] is menu, "The physical-input menu state did not match the requested state")
            if name == "reader-baseline":
                self.reader_baseline = int(state["reader_page"])
                self.reader_initial_menu = bool(state["menu"])
            if page_advance:
                require(self.reader_baseline is not None, "The baseline reader state was not captured")
                require(int(state["reader_page"]) > self.reader_baseline, "The QMP touch did not advance the native reader")
            if expected_page is not None:
                require(int(state["page"]) == expected_page, f"The native list swipe ended on page {state['page']}, expected {expected_page}")
                require(not state.get("selected"), "The list swipe activated the cover's long-press selection")
                require(state.get("modal", "") == "", "The list swipe opened a child action panel")
            changed = difference(before, frame)
            if touches or drags:
                require(max([changed, *drag_changes]) > 0.0005, "The native framebuffer did not visibly respond to the injected touch")
            if drags:
                require(all(change > 0.0005 for change in drag_changes), "A hardware list drag did not update the actual framebuffer")
            after_services = self.service_state()
            require(not after_services["awesome_stopped"], "The launcher left a stock window-manager process paused")
            if baseline_services["statusbar_running"]:
                require(after_services["statusbar_running"], "The launcher did not restore the previously running status bar")
            lock = self.guest("if [ -e /tmp/aidoku-launcher.lock ]; then printf busy; else printf free; fi").decode()
            require(lock == "free", "The launcher left its display lock behind")
            result.checks = {
                "route": state["route"],
                "menu": state.get("menu"),
                "reader_page": state.get("reader_page"),
                "metadata": metadata,
                "framebuffer_change_ratio": changed,
                "qmp_drags": drag_checks,
                "qmp_drag_frame_changes": drag_changes,
                "input": "QMP emulated touch controller -> guest kernel -> native evdev backend",
                "baseline_services": baseline_services,
                "restored_services": after_services,
                "launcher_lock_removed": True,
            }
            result.passed = True
        except Exception as error:
            result.error = str(error)
            self.stop_owned_launcher()
            if process is not None:
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=5)
        result.duration_seconds = round(time.monotonic() - started, 3)
        result.artifacts.append(str(log_path))
        self.results.append(result)
        print(f"{'PASS' if result.passed else 'FAIL'} {name}" + (f": {result.error}" if result.error else ""), flush=True)

    def run(self) -> int:
        self.prepare()
        width, height = self.logical_size
        cases = [
            ("library", "library", [], "library", {}),
            ("library-navigation", "library", [("settings_nav", (width * 0.9, height - 33))], "settings", {}),
            ("library-detail", "library", [("library_first_cover", (width * 0.14, 375))], "detail", {}),
            ("detail-reader", "detail", [("detail_continue", (width * 0.3, 340))], "reader", {}),
            ("reader-baseline", "reader", [], "reader", {}),
            ("reader-page", "reader", [("reader_next", (width * 0.12, height * 0.53))], "reader", {"page_advance": True}),
            ("reader-menu", "reader", [("reader_menu", (width * 0.5, height * 0.46))], "reader", {}),
        ]
        if self.arguments.include_pagination:
            cases.extend([
                ("library-swipe", "library", [], "library", {"drags": [("library_swipe_next", (118, 375), (40, 375))], "expected_page": 2}),
                ("library-swipe-roundtrip", "library", [], "library", {"drags": [("library_swipe_next", (118, 375), (40, 375)), ("library_swipe_previous", (40, 375), (118, 375))], "expected_page": 1}),
            ])
        if self.arguments.cases:
            selected = set(self.arguments.cases.split(","))
            cases = [case for case in cases if case[0] in selected]
            require(bool(cases), "No native cases match the requested selection")
            if selected.intersection({"reader-page", "reader-menu"}) and "reader-baseline" not in selected:
                cases.insert(0, ("reader-baseline", "reader", [], "reader", {}))
        for name, screen, touches, expected_route, options in cases:
            if name == "reader-page" and self.reader_initial_menu:
                touches = [("reader_menu", (width * 0.5, height * 0.46)), *touches]
            if name == "reader-menu" and self.reader_initial_menu is not None:
                options = {"menu": not self.reader_initial_menu}
            self.run_case(name, screen, touches, expected_route, **options)
        report = {
            "environment": "Isolated Kindle firmware guest on the designated remote Linux worker",
            "emulator": self.environment,
            "binary_sha256": self.binary_sha256,
            "launcher_sha256": self.launcher_sha256,
            "test_wrapper_sha256": self.wrapper_sha256,
            "binary": str(self.binary),
            "guest_fixture_root": self.guest_root,
            "native_size": self.native_size,
            "logical_size": self.logical_size,
            "run_options": {"startup_deadline_seconds": self.arguments.startup_seconds, "event_loop_exit_after_ms": self.arguments.duration_ms, "include_pagination": self.arguments.include_pagination, "selected_cases": self.arguments.cases},
            "passed": sum(result.passed for result in self.results),
            "failed": sum(not result.passed for result in self.results),
            "hardware_list_swipes_requested": self.arguments.include_pagination,
            "cases": [dataclasses.asdict(result) for result in self.results],
            "coverage_limits": [
                "These are firmware-emulator results, not physical Kindle acceptance.",
                "Synthetic waveforms do not verify ghosting, flicker, contrast, refresh latency, or power consumption.",
                "Offline demo data does not verify authenticated sources or source availability.",
                "Timed application exit tests launcher restoration; voluntary UI application exit and physical sleep/wake remain separate checks.",
                "App snapshots and QMP framebuffer screenshots observe separate rendering paths and are not assumed pixel-identical.",
                "Hardware list swipes are covered only when include-pagination is explicitly enabled against the unified-input-observer build.",
            ],
        }
        report_path = self.output / "report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"report": str(report_path), "passed": report["passed"], "failed": report["failed"]}), flush=True)
        return 0 if report["failed"] == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--emulator-root", type=Path, default=Path("/root/aidoku-emulator/eink-emulator"))
    parser.add_argument("--guest-ssh", type=Path, default=Path("/root/aidoku-emulator/guest-ssh"))
    parser.add_argument("--machine", default="aidoku-pw6-5201")
    parser.add_argument("--firmware", default="5.20.1")
    parser.add_argument("--profile", default="pw6", choices=("pw5", "pw6", "scribe", "scribe2", "scribe3", "scribe3-nofrontlight"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/native"))
    parser.add_argument("--duration-ms", type=int, default=18000)
    parser.add_argument("--startup-seconds", type=float, default=90)
    parser.add_argument("--cases", help="Comma-separated demo case names, or startup-library by itself")
    parser.add_argument("--native-query", type=Path, help="Repeat startup-library with this native AQ1 helper")
    parser.add_argument("--require-toolbar-snapshot", action="store_true", help="Require snapshot use in the startup-library run with --native-query")
    parser.add_argument("--include-pagination", action="store_true", help="Add hardware list drags; requires the unified input-observer ARM build")
    parser.add_argument("--tap-map", type=Path, help="Optional logical-coordinate overrides keyed by touch name")
    arguments = parser.parse_args()
    if os.name != "posix":
        parser.error("Run this verifier on the designated remote Linux worker through ssh test-env")
    if not 1200 <= arguments.duration_ms <= 180000:
        parser.error("The native case duration must be between 1200 and 180000 milliseconds")
    if not 1 <= arguments.startup_seconds <= 90:
        parser.error("The native startup deadline must be between 1 and 90 seconds")
    startup = arguments.cases == STARTUP_CASE
    if arguments.cases and STARTUP_CASE in arguments.cases.split(",") and not startup:
        parser.error("Run startup-library separately from the other firmware cases")
    if arguments.native_query and not startup:
        parser.error("--native-query requires --cases startup-library")
    if arguments.require_toolbar_snapshot and not arguments.native_query:
        parser.error("--require-toolbar-snapshot requires --native-query")
    if startup and (arguments.profile not in STARTUP_GEOMETRY or arguments.duration_ms < 4000):
        parser.error("startup-library requires scribe or pw6 and at least 4000 milliseconds for QMP capture")
    try:
        verifier = Verifier(arguments)
        return verifier.run_startup_library() if startup else verifier.run()
    except Exception as error:
        print(f"Native verification setup failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
