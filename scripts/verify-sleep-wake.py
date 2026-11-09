#!/usr/bin/env python3
"""Verify retained-process sleep/wake in an isolated Aidoku firmware guest.

Run on the designated remote Linux worker during an exclusive VM window.
Execution intentionally toggles that guest's powerButton/wakeUp properties.
It never changes real USB state or reads the product power-events FIFO.
Firmware screenshots, exported state, and stopped-process accounting are
separate evidence; none measures physical suspend, battery, RF, or panel quality.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
from typing import Any, Callable, IO

from PIL import Image, ImageChops


# Test-only wrapper; the executable and all test inputs stay in the round HOME.
# A fallback uses --resume and must not replay setup actions such as page.turn.
WRAPPER = r'''#!/bin/sh
set -eu
AIDOKU_HOME=${AIDOKU_HOME:?}
LOG_DIR=$AIDOKU_HOME/data/logs
NATIVE=$AIDOKU_HOME/app/aidoku.native
SCREEN=$(sed -n '1p' "$LOG_DIR/test-screen")
CASE=$(sed -n '1p' "$LOG_DIR/test-case")
DEMO=$(sed -n '1p' "$LOG_DIR/test-demo")
OFFLINE=$(sed -n '1p' "$LOG_DIR/test-offline")
case "$SCREEN" in reader|library) ;; *) exit 2 ;; esac
case "$CASE" in ''|*[!a-z0-9_-]*) exit 2 ;; esac
case "$DEMO:$OFFLINE" in 0:0|0:1|1:0|1:1) ;; *) exit 2 ;; esac
RESUME=0
for argument do [ "$argument" != --resume ] || RESUME=1; done
set -- "$@" --screen "$SCREEN" --capture "$LOG_DIR/native-$CASE-$$.png" --exit-after-ms 180000
if [ "$DEMO" -eq 1 ]; then set -- "$@" --demo; fi
if [ "$OFFLINE" -eq 1 ]; then set -- "$@" --offline; fi
if [ "$RESUME" -eq 0 ] && [ -s "$LOG_DIR/test-actions.json" ]; then
    set -- "$@" --actions "$LOG_DIR/test-actions.json"
fi
if [ "$RESUME" -eq 0 ] && [ "${AIDOKU_TEST_LANDSCAPE:-0}" = 1 ]; then
    set -- "$@" --landscape
fi
exec "$NATIVE" "$@"
'''

STABLE_FIELDS = (
    "route", "page", "current_book", "current_chapter", "reader_page",
    "reader_offset", "reader_zoom", "landscape", "spread", "source_id",
    "library_filter", "source_filter", "catalog_filter", "history_filter",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def action(at_ms: int, section: str, name: str, value: str = "") -> dict[str, Any]:
    return {"at_ms": at_ms, "section": section, "action": name, "value": value}


@dataclasses.dataclass
class Capture:
    label: str
    image: Path
    state: dict[str, Any] | None
    phase: str
    power: str
    process: dict[str, Any] | None
    epoch: int | None
    sleep_ready: str
    resume_request: str
    resume_ready: str
    state_path: Path


@dataclasses.dataclass
class Session:
    name: str
    home: str
    lock: str
    output: Path
    screen: str
    process: subprocess.Popen[bytes]
    log_file: IO[bytes]
    log_path: Path
    actions: list[dict[str, Any]]
    pid: int = 0
    birth: int = 0
    runner_pid: int = 0
    launcher_pid: int = 0
    launcher_birth: int = 0
    nonce: str = ""
    closed: bool = False
    observer: subprocess.Popen[bytes] | None = None
    observer_log: IO[bytes] | None = None
    observer_pid: int = 0
    observer_birth: int = 0
    observer_path: Path | None = None
    artifacts: list[str] = dataclasses.field(default_factory=list)
    demo: bool = True
    offline: bool = True
    startup_health_checked: bool = False

    @property
    def data_dir(self) -> str:
        return self.home + "/data"

    @property
    def effective_data_dir(self) -> str:
        return self.data_dir + ("/demo" if self.demo else "")

    @property
    def lock_dir(self) -> str:
        return self.lock

    @property
    def log(self) -> Path:
        return self.log_path


@dataclasses.dataclass(frozen=True)
class StartContext:
    """Paths available to fixture seeders before any application process starts."""

    name: str
    home: str
    data_dir: str
    effective_data_dir: str
    lock_dir: str
    output: Path
    demo: bool
    offline: bool


@dataclasses.dataclass
class ParkEvidence:
    before: Capture
    parked: Capture
    held: Capture
    hold_seconds: float


@dataclasses.dataclass
class Result:
    name: str
    round: int
    passed: bool = False
    duration_seconds: float = 0
    checks: dict[str, Any] = dataclasses.field(default_factory=dict)
    artifacts: list[str] = dataclasses.field(default_factory=list)
    error: str | None = None
    cleanup_error: str | None = None


class SleepVerifier:
    """Reusable capture/start/park/wake/stop primitives for later case matrices."""

    def __init__(self, arguments: argparse.Namespace) -> None:
        self.arguments = arguments
        source_snapshot = getattr(arguments, "source_snapshot_sha256", None)
        require(source_snapshot is None or re.fullmatch(r"[0-9a-f]{64}", source_snapshot) is not None, "A declared source snapshot must be a lowercase SHA-256 value")
        self.repository = Path(__file__).resolve().parents[1]
        self.engine = arguments.emulator_root.resolve(strict=True)
        self.guest_ssh = arguments.guest_ssh.resolve(strict=True)
        self.qmp_script = self.engine / "scripts/eink-qmp.py"
        require(self.qmp_script.is_file(), "The QMP helper is missing")
        require(re.fullmatch(r"aidoku-[a-z0-9_-]+", arguments.machine) is not None, "Only an isolated Aidoku machine may be tested")
        self.binary = arguments.binary.resolve(strict=True)
        self.binary_bytes = self.binary.read_bytes()
        require(len(self.binary_bytes) >= 20 and self.binary_bytes[:6] == b"\x7fELF\x01\x01", "A 32-bit little-endian ELF is required")
        require(int.from_bytes(self.binary_bytes[18:20], "little") == 40, "The executable is not ARM")
        self.launcher_bytes = (self.repository / "packaging/kindle/launch.sh").read_bytes()
        self.wrapper_bytes = WRAPPER.encode("utf-8")
        self.hashes = {"binary": digest(self.binary_bytes), "launcher": digest(self.launcher_bytes), "wrapper": digest(self.wrapper_bytes)}
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.run_id = f"run-{stamp}-{os.getpid()}"
        self.output = arguments.output_dir.resolve() / self.run_id
        self.output.mkdir(parents=True, exist_ok=False)
        inputs = self.output / "inputs"
        inputs.mkdir()
        (inputs / f"aidoku-{self.hashes['binary'][:12]}.elf").write_bytes(self.binary_bytes)
        (inputs / "launch.sh").write_bytes(self.launcher_bytes)
        (inputs / "emulator-wrapper.sh").write_bytes(self.wrapper_bytes)
        self.command_number = 0
        self.commands: list[dict[str, Any]] = []
        self.results: list[Result] = []
        self.environment: dict[str, Any] = {}
        self.round_number = 0
        self.home = ""
        self.lock = ""
        self.round_output = self.output
        self.current: Session | None = None
        self.guard = "/tmp/aidoku-sleep-suite.guard"
        self.guard_owned = False
        self.boot_id: str | None = None
        self.screensaver_policy: dict[str, Any] | None = None
        self.prepared_namespaces: list[dict[str, Any]] = []
        self.suite_error: str | None = None
        self.suite_cleanup_error: str | None = None

    def command(self, scope: str, command: str, *, payload: bytes | None = None, timeout: float = 15) -> bytes:
        self.command_number += 1
        directory = self.output / "commands"
        directory.mkdir(exist_ok=True)
        stem = f"{self.command_number:05}-{scope}"
        stdout_path, stderr_path = directory / f"{stem}.stdout", directory / f"{stem}.stderr"
        record: dict[str, Any] = {"scope": scope, "command": command, "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "stdout": str(stdout_path), "stderr": str(stderr_path)}
        if payload is not None:
            record["stdin_bytes"] = len(payload)
            record["stdin_sha256"] = digest(payload)
        self.commands.append(record)
        try:
            completed = subprocess.run([str(self.guest_ssh), command], input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as error:
            stdout_path.write_bytes(error.stdout or b"")
            stderr_path.write_bytes(error.stderr or b"")
            record["error"] = "transport timeout"
            raise AssertionError(f"Guest command exceeded {timeout}s: {command}") from error
        stdout_path.write_bytes(completed.stdout)
        stderr_path.write_bytes(completed.stderr)
        record["returncode"] = completed.returncode
        require(completed.returncode == 0, f"Guest command failed ({completed.returncode}); see {stderr_path}")
        return completed.stdout

    def guest(self, command: str, **kwargs: Any) -> bytes:
        return self.command("guest", command, **kwargs)

    def identity(self) -> None:
        script = "aidoku_marker=$(cat /var/local/aidoku-emulator-id); read -r aidoku_boot </proc/sys/kernel/random/boot_id; printf '%s\\n%s\\n' \"$aidoku_marker\" \"$aidoku_boot\""
        if self.guard_owned:
            script += f"; if [ -r {shlex.quote(self.guard + '/owner')} ]; then cat {shlex.quote(self.guard + '/owner')}; fi"
        lines = self.command("identity", script).decode().splitlines()
        require(len(lines) >= 2 and lines[0] == self.arguments.machine, f"Guest identity does not match the selected Aidoku QMP machine: {lines}")
        require(re.fullmatch(r"[0-9a-f-]{36}", lines[1]) is not None, "The guest boot identity is unavailable")
        if self.boot_id is None:
            self.boot_id = lines[1]
        require(lines[1] == self.boot_id, f"The isolated guest rebooted during this test: expected boot {self.boot_id}, observed {lines[1]}")
        if self.guard_owned:
            require(len(lines) == 3 and lines[2] == self.run_id, "The suite no longer owns its guard; no global guest mutation is authorized")

    def owned_script(self, command: str) -> str:
        require(self.guard_owned and self.boot_id is not None, "A live owned suite is required for a guest mutation")
        return (
            "read -r aidoku_test_boot </proc/sys/kernel/random/boot_id || exit 96\n"
            f"[ \"$aidoku_test_boot\" = {shlex.quote(self.boot_id)} ] || exit 96\n"
            f"read -r aidoku_test_owner <{shlex.quote(self.guard + '/owner')} || exit 96\n"
            f"[ \"$aidoku_test_owner\" = {shlex.quote(self.run_id)} ] || exit 96\n"
            + command
        )

    def mutate(self, scope: str, command: str, **kwargs: Any) -> bytes:
        self.identity()
        return self.command(scope, self.owned_script(command), **kwargs)

    def qmp(self, *arguments: str) -> dict[str, Any]:
        if arguments and arguments[0] not in {"status", "display", "screendump"}:
            require(self.guard_owned, "An owned suite is required for QMP input")
            self.identity()
        self.command_number += 1
        record: dict[str, Any] = {"scope": "qmp", "arguments": list(arguments), "machine": self.arguments.machine, "started_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
        self.commands.append(record)
        completed = subprocess.run([sys.executable, str(self.qmp_script), "--machine", self.arguments.machine, *arguments], cwd=self.engine, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25, check=False)
        record.update(returncode=completed.returncode, stdout=completed.stdout.decode(errors="replace"), stderr=completed.stderr.decode(errors="replace"))
        require(completed.returncode == 0, f"QMP command failed: {record['stderr']}")
        return json.loads(completed.stdout) if completed.stdout.strip() else {}

    def read(self, path: str) -> str:
        # A control record can be unlinked during atomic restart/exit. Only an
        # ENOENT observation for an absent non-symlink becomes an empty snapshot;
        # permission/I/O failures retain their status and diagnostic text.
        target = shlex.quote(path)
        script = f"""aidoku_record_attempt=0
while :; do
    if aidoku_record_text=$(LC_ALL=C cat {target} 2>&1); then
        printf '%s' "$aidoku_record_text"
        exit 0
    else
        aidoku_record_status=$?
        case "$aidoku_record_text" in
            *'No such file or directory')
                if [ ! -L {target} ]; then
                    if [ ! -e {target} ]; then exit 0; fi
                    if [ "$aidoku_record_attempt" -eq 0 ]; then
                        aidoku_record_attempt=1
                        continue
                    fi
                fi ;;
        esac
        printf '%s\\n' "$aidoku_record_text" >&2
        exit "$aidoku_record_status"
    fi
done"""
        return self.guest(script).decode(errors="replace").strip()

    def put(self, payload: bytes, path: str, mode: str = "644") -> None:
        self.mutate("fixture-write", f"cat >{shlex.quote(path)} && chmod {mode} {shlex.quote(path)}", payload=payload, timeout=120)

    def wait(self, description: str, probe: Callable[[], Any], *, timeout: float | None = None, interval: float = 0.3) -> Any:
        deadline = time.monotonic() + (self.arguments.timeout if timeout is None else timeout)
        while time.monotonic() < deadline:
            value = probe()
            if value:
                return value
            time.sleep(interval)
        raise AssertionError(f"Timed out waiting for {description}")

    def process(self, pid: int) -> dict[str, Any] | None:
        require(pid > 1, "A positive owned process PID is required")
        raw = self.guest(f"aidoku_stat=$(cat /proc/{pid}/stat 2>/dev/null) || exit 0; aidoku_io=$(cat /proc/{pid}/io 2>/dev/null) || exit 0; printf '%s\\n%s\\n' \"$aidoku_stat\" \"$aidoku_io\"").decode(errors="replace")
        if not raw.strip():
            return None
        lines = raw.splitlines()
        stat = lines[0].rsplit(") ", 1)[1].split()
        io = {key: int(value.strip()) for key, value in (line.split(":", 1) for line in lines[1:] if ":" in line)}
        require("write_bytes" in io, "The guest did not expose per-process write_bytes")
        return {"pid": pid, "state": stat[0], "ppid": int(stat[1]), "utime": int(stat[11]), "stime": int(stat[12]), "cpu_ticks": int(stat[11]) + int(stat[12]), "birth": int(stat[19]), "rss_pages": int(stat[21]), "write_bytes": io["write_bytes"]}

    def control(self, session: Session) -> dict[str, Any]:
        values = {name: self.read(f"{session.lock}/{name}") for name in ("pid", "nonce", "phase", "epoch", "application-pid", "sleep-ready", "resume-request", "resume-ready", "lifecycle-state.json")}
        state = json.loads(values["lifecycle-state.json"]) if values["lifecycle-state.json"] else None
        return {"launcher_pid": int(values["pid"]) if values["pid"].isdigit() else 0, "pid": int(values["application-pid"]) if values["application-pid"].isdigit() else 0, "nonce": values["nonce"], "phase": values["phase"], "epoch": int(values["epoch"]) if values["epoch"].isdigit() else None, "sleep_ready": values["sleep-ready"], "resume_request": values["resume-request"], "resume_ready": values["resume-ready"], "state": state}

    def state(self) -> str:
        return self.guest("lipc-get-prop -s com.lab126.powerd state").decode().strip()

    def power(self, property_name: str) -> None:
        require(property_name in {"powerButton", "wakeUp"}, "Only isolated guest sleep/wake properties may be written")
        if property_name == "powerButton":
            self.enable_screensaver()
        self.mutate("powerd", f"lipc-set-prop -i com.lab126.powerd {property_name} 1")

    def protect_cold_start(self, context: str, *, allow_parked: bool = False) -> dict[str, Any]:
        self.identity()
        require(self.screensaver_policy is not None, "The suite did not capture its original emulator policy")
        power = self.state()
        require(power == "active" or allow_parked and power == "screenSaver", "Stock must be active or in an authorized fallback sleep before protecting cold initialization")
        previous = self.guest("lipc-get-prop -i com.lab126.powerd preventScreenSaver").decode().strip()
        require(previous in {"0", "1"}, "The emulator screensaver policy is not observable before startup")
        observation = {"context": context, "powerd_state_before": power, "before": previous, "target": "1", "write_attempted": previous != "1", "completed": False}
        self.screensaver_policy["cold_start_protection"].append(observation)
        self.screensaver_policy["activated"] = False
        self.screensaver_policy["protected"] = False
        if previous != "1":
            # A property write can succeed even if its SSH reply is lost. Record
            # restoration responsibility before making the first mutation.
            self.screensaver_policy["restore_needed"] = True
            self.screensaver_policy["changed"] = True
            self.mutate("startup-inhibit", "lipc-set-prop -i com.lab126.powerd preventScreenSaver 1")
        observed = self.guest("lipc-get-prop -i com.lab126.powerd preventScreenSaver").decode().strip()
        observation["after"] = observed
        require(observed == "1", "Cold initialization is not protected from the stock idle deadline")
        self.screensaver_policy["protected"] = True
        observation["completed"] = True
        return observation

    def enable_screensaver(self) -> None:
        require(self.screensaver_policy is not None, "The suite did not capture its original emulator policy")
        if self.screensaver_policy.get("activated"):
            return
        require(self.current is not None and not self.current.closed and self.current.startup_health_checked, "A protected startup and healthy download controller are required before enabling sleep")
        require(self.screensaver_policy.get("protected"), "The current cold startup was not protected")
        require(self.state() == "active", "Stock must be active before enabling a controlled sleep transition")
        observation = {"case": self.current.name, "pid": self.current.pid, "activity_reset_attempted": True, "inhibition_clear_attempted": False, "completed": False}
        self.screensaver_policy["sleep_enable_observations"].append(observation)
        # SSH launch has no preceding KUAL touch. One explicit setup activity
        # prevents an old idle deadline from entering sleep during cold fonts.
        self.mutate("startup-activity-reset", "lipc-set-prop -i com.lab126.powerd touchScreenSaverTimeout 1")
        self.screensaver_policy["restore_needed"] = True
        self.screensaver_policy["changed"] = True
        observation["inhibition_clear_attempted"] = True
        self.mutate("emulator-policy", "lipc-set-prop -i com.lab126.powerd preventScreenSaver 0")
        require(self.guest("lipc-get-prop -i com.lab126.powerd preventScreenSaver").decode().strip() == "0", "The isolated emulator still inhibits real screensaver transitions")
        self.screensaver_policy["activated"] = True
        self.screensaver_policy["protected"] = False
        self.screensaver_policy["setup_activity_reset"] = True
        observation["completed"] = True

    def prepare_round(self, number: int, variant: str = "") -> None:
        require(not variant or re.fullmatch(r"[a-z0-9][a-z0-9_-]*", variant) is not None, "A namespace variant must be a simple lowercase identifier")
        self.identity()
        if not self.guard_owned:
            self.acquire_guard()
        require(self.read(self.guard + "/owner") == self.run_id, "The exclusive suite guard is not owned by this verifier")
        require(self.qmp("status").get("running") is True, "The isolated VM is not running")
        firmware = self.read("/etc/prettyversion.txt")
        require(re.search(rf"\b{re.escape(self.arguments.firmware)}\b", firmware) is not None, f"Unexpected firmware: {firmware}")
        existing = self.guest(f"for p in /tmp/aidoku-launcher.lock /tmp/aidoku-sleep-*; do if [ -d \"$p\" ] && [ \"$p\" != {shlex.quote(self.guard)} ]; then printf '%s\\n' \"$p\"; fi; done").decode().strip()
        require(not existing, f"Another launcher/test owns a lock; it will not be stolen: {existing}")
        self.protect_cold_start(f"fixture-round-{number}" + (f"-{variant}" if variant else ""))
        mount = self.guest("cat /proc/mounts | awk '$2 == \"/mnt/us\" {print}'").decode().strip()
        require(mount and "noexec" not in mount, "The guest userstore is not executable")
        self.round_number = number
        suffix = f"-{variant}" if variant else ""
        tag = f"{self.run_id}-r{number}{suffix}"
        self.home = f"/mnt/us/aidoku-sleep-tests/{tag}"
        self.lock = f"/tmp/aidoku-sleep-{tag}"
        self.round_output = self.output / f"round-{number}{suffix}"
        self.round_output.mkdir()
        require(not self.read(self.home + "/.test-owner"), "The round fixture already exists")
        # mkdir without -p for the HOME prevents accidental reuse of an old
        # normal database, including a partial fixture without its owner marker.
        self.guest(f"mkdir -p /mnt/us/aidoku-sleep-tests && mkdir {shlex.quote(self.home)} && mkdir -p {shlex.quote(self.home + '/app')} {shlex.quote(self.home + '/data/logs')}")
        self.put(tag.encode() + b"\n", self.home + "/.test-owner")
        pool = getattr(self.arguments, "guest_binary_pool", None)
        if pool:
            pool = str(pool).rstrip("/")
            require(pool.startswith("/"), "The guest binary pool must be an absolute guest directory")
            manifest = json.loads(self.read(pool + "/inputs.json"))
            declared = manifest.get("binary_sha256", manifest.get("binary"))
            if isinstance(declared, dict):
                declared = declared.get("sha256")
            require(declared == self.hashes["binary"], "The guest pool manifest does not identify the frozen main binary")
            self.command("guest-pool-copy", f"cp {shlex.quote(pool + '/aidoku.native')} {shlex.quote(self.home + '/app/aidoku.native')} && chmod 755 {shlex.quote(self.home + '/app/aidoku.native')}")
        else:
            self.put(self.binary_bytes, self.home + "/app/aidoku.native", "755")
        self.put(self.launcher_bytes, self.home + "/launch.sh", "755")
        self.put(self.wrapper_bytes, self.home + "/app/aidoku", "755")
        self.put((self.arguments.profile + "\n").encode(), self.home + "/data/device-profile")
        for key, path in (("binary", "/app/aidoku.native"), ("launcher", "/launch.sh"), ("wrapper", "/app/aidoku")):
            actual = self.guest(f"sha256sum {shlex.quote(self.home + path)}").decode().split()[0]
            require(actual == self.hashes[key], f"The deployed {key} hash differs from the frozen input")
        self.prepared_namespaces.append({"round": number, "variant": variant, "home": self.home, "lock": self.lock, "output": str(self.round_output), "binary_transfer": "guest-pool-copy" if pool else "ssh-upload", "guest_binary_pool": pool, "verified_binary_sha256": self.hashes["binary"]})
        if not self.environment:
            self.environment = {"machine": self.arguments.machine, "boot_id": self.boot_id, "firmware": firmware, "kernel": self.guest("uname -srmo").decode().strip(), "mount": mount, "display": self.qmp("display"), "usleep": self.guest("command -v usleep || :").decode().strip()}

    def start_observer(self, session: Session) -> None:
        path = session.home + f"/data/logs/power-observer-{session.name}-pid"
        self.guest(f"rm -f {shlex.quote(path)}")
        script = f"printf '%s\\n' \"$$\" >{shlex.quote(path)}; exec lipc-wait-event -m -t -s 0 com.lab126.powerd goingToScreenSaver,outOfScreenSaver,exitingScreenSaver,usbConfigured"
        session.observer_path = session.output / "power-events.log"
        session.observer_log = session.observer_path.open("wb")
        session.observer = subprocess.Popen([str(self.guest_ssh), "sh -c " + shlex.quote(script)], stdout=session.observer_log, stderr=subprocess.STDOUT)
        def observer_started() -> str:
            require(session.observer.poll() is None, "The independent power observer exited during startup")
            value = self.read(path)
            if not value.isdigit():
                return ""
            return value if self.read(f"/proc/{int(value)}/comm") == "lipc-wait-event" else ""

        pid = self.wait("independent power observer", observer_started)
        require(str(pid).isdigit(), "The observer did not report a PID")
        record = self.process(int(pid))
        require(record is not None, "The independent observer exited during startup")
        session.observer_pid, session.observer_birth = int(pid), record["birth"]
        session.artifacts.append(str(session.observer_path))

    def signal_owned(self, pid: int, birth: int, signal: str, *, parent: int | None = None, contains: str | None = None) -> None:
        self.identity()
        record = self.process(pid)
        if record is None or record["state"] in {"Z", "X"}:
            return
        require(record["birth"] == birth, "A recorded PID was reused; it will not be signaled")
        if parent is not None:
            require(record["ppid"] == parent, "The process no longer belongs to the recorded parent")
        if contains is not None:
            command = self.guest(f"cat /proc/{pid}/cmdline").replace(b"\0", b" ").decode(errors="replace")
            require(contains in command, "The process command line does not identify this fixture")
        require(signal in {"TERM", "KILL", "INT"}, "Unsupported test signal")
        guard = f"[ \"$(awk '{{print $22}}' /proc/{pid}/stat 2>/dev/null)\" = {birth} ]"
        self.mutate("signal", f"if {guard}; then kill -{signal} {pid}; fi")

    def start(self, name: str, screen: str = "reader", actions: list[dict[str, Any]] | None = None, *, demo: bool = True, offline: bool = True, before_start: Callable[[StartContext], None] | None = None, extra_env: dict[str, str] | None = None, wait_for_actions: bool = True) -> Session:
        self.identity()
        require(self.round_number > 0 and self.home.startswith("/mnt/us/aidoku-sleep-tests/") and self.lock.startswith("/tmp/aidoku-sleep-"), "prepare_round must establish an isolated namespace before start")
        require(self.guard_owned and self.read(self.guard + "/owner") == self.run_id, "The verifier does not own its exclusive suite guard")
        require(re.fullmatch(r"[a-z0-9_-]+", name) is not None, "A test case name must be a simple lowercase identifier")
        require(screen in {"reader", "library"}, "The base test wrapper supports Reader and Library entry points")
        require(self.current is None or self.current.closed, "The preceding owned session is still running")
        self.current = None
        require(not self.read(self.lock + "/pid"), "A round launcher still owns its lock")
        output = self.round_output / name
        output.mkdir()
        startup_policy = self.protect_cold_start(f"application-{name}")
        actions = list(actions or [])
        self.put((screen + "\n").encode(), self.home + "/data/logs/test-screen")
        self.put((name + "\n").encode(), self.home + "/data/logs/test-case")
        self.put(("1\n" if demo else "0\n").encode(), self.home + "/data/logs/test-demo")
        self.put(("1\n" if offline else "0\n").encode(), self.home + "/data/logs/test-offline")
        action_bytes = json.dumps(actions, indent=2).encode()
        self.put(action_bytes, self.home + "/data/logs/test-actions.json")
        (output / "actions.json").write_bytes(action_bytes)
        context = StartContext(name, self.home, self.home + "/data", self.home + ("/data/demo" if demo else "/data"), self.lock, output, demo, offline)
        if before_start is not None:
            before_start(context)
        log_path = output / "launcher.log"
        environment = dict(extra_env or {})
        for key in environment:
            require(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) is not None, "An extra environment key is invalid")
            require(key not in {"AIDOKU_HOME", "AIDOKU_LOCK_DIR", "AIDOKU_LAUNCHER_DIR", "AIDOKU_LAUNCH_NONCE"}, "Extra environment cannot replace ownership identity")
        environment.update(AIDOKU_HOME=self.home, AIDOKU_LOCK_DIR=self.lock)
        assignments = " ".join(f"{key}={shlex.quote(str(value))}" for key, value in environment.items())
        require(self.state() == "active", "The stock system must be active before cold application startup")
        command = self.owned_script(f"{assignments} sh {shlex.quote(self.home + '/launch.sh')} --foreground")
        log = log_path.open("wb")
        launch_before_epoch = time.time()
        launch_before_monotonic = time.monotonic()
        launch_before_utc = dt.datetime.now(dt.timezone.utc).isoformat()
        process = subprocess.Popen([str(self.guest_ssh), command], stdout=log, stderr=subprocess.STDOUT)
        launch_after_epoch = time.time()
        startup_policy.update(launch_before_utc=launch_before_utc, launch_after_utc=dt.datetime.now(dt.timezone.utc).isoformat())
        session = Session(name, self.home, self.lock, output, screen, process, log, log_path, actions, demo=demo, offline=offline)
        self.current = session
        self.commands.append({"scope": "launch", "command": command, "log": str(log_path), "started_utc": launch_before_utc, "launch_after_utc": startup_policy["launch_after_utc"]})
        self.start_observer(session)

        def initialized() -> dict[str, Any] | None:
            self.identity()
            require(process.poll() is None, "The test launcher exited before the native app was ready")
            self.reject_worker_error(session)
            if "aidoku: native window ready" not in log_path.read_text(errors="replace"):
                return None
            if not startup_policy.get("software_ready_observed_utc"):
                marker_time = log_path.stat().st_mtime
                ready = {"event": "NATIVE_WINDOW_READY", "case": name,
                         "observed_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                         "launch_before_utc": launch_before_utc,
                         "host_observed_elapsed_seconds": time.monotonic() - launch_before_monotonic,
                         "marker_log_mtime_elapsed_lower_seconds": marker_time - launch_after_epoch,
                         "marker_log_mtime_elapsed_upper_seconds": marker_time - launch_before_epoch,
                         "scope": "First host observation of the software window-ready marker; actual scanout and panel timing are separate"}
                startup_policy.update(software_ready_observed_utc=ready["observed_utc"], software_ready=ready)
                print(json.dumps(ready), flush=True)
            control = self.control(session)
            return control if control["phase"] == "active" and control["pid"] and control["state"] else None

        control = self.wait("native app initialization", initialized)
        require(re.fullmatch(r"[a-fA-F0-9]{32}", control["nonce"]) is not None, "The launcher nonce is missing or malformed")
        record = self.process(control["pid"])
        launcher = self.process(control["launcher_pid"])
        require(record is not None and launcher is not None, "The owned process chain is missing")
        runner = self.process(record["ppid"])
        require(runner is not None and runner["ppid"] == control["launcher_pid"], "The actual ELF is not owned by this launcher's runner")
        session.pid, session.birth, session.runner_pid = control["pid"], record["birth"], record["ppid"]
        session.launcher_pid, session.launcher_birth, session.nonce = control["launcher_pid"], launcher["birth"], control["nonce"]
        self.validate_state(session, control["state"])
        # Initial diagnostics predate asynchronous images and replay actions.
        # By default wait for their setup budget; pending-work cases may retain
        # future actions so their caller can inject input before those execute.
        # Fresh application assertions still use the park ACK, not this snapshot.
        setup_ms = max((item["at_ms"] for item in actions if not (item["section"] == "app" and item["action"] == "exit")), default=0) if wait_for_actions else 0
        setup_seconds = max(3.0, setup_ms / 1000 + 1)
        time.sleep(setup_seconds)
        self.worker_health(session)
        session.startup_health_checked = True
        startup_policy.update(case=name, pid=session.pid, native_window_ready=True, download_controller_healthy=True, setup_budget_seconds=setup_seconds)
        policy_path = session.output / "startup-policy.json"
        policy_path.write_text(json.dumps(startup_policy, indent=2) + "\n")
        session.artifacts.append(str(policy_path))
        return session

    def reject_worker_error(self, session: Session) -> None:
        lines = session.log_path.read_text(errors="replace").splitlines()
        errors = [line for line in lines if "download worker failed:" in line or "aidoku-downloads" in line and "panicked" in line]
        require(not errors, "Native download controller terminated before park: " + " | ".join(errors))

    def worker_health(self, session: Session, *, label: str = "startup") -> None:
        self.reject_worker_error(session)
        command = f"for t in /proc/{session.pid}/task/*; do [ -r \"$t/comm\" ] || continue; read -r n <\"$t/comm\"; printf '%s %s\\n' \"${{t##*/}}\" \"$n\"; done"
        payload = self.guest(command).decode(errors="replace")
        threads = [{"tid": int(line.split(" ", 1)[0]), "comm": line.split(" ", 1)[1]} for line in payload.splitlines() if " " in line]
        candidates = [thread for thread in threads if thread["comm"] == "aidoku-download"]
        record = {"pid": session.pid, "threads": threads, "download_thread_candidates": candidates, "scope": "Read-only startup check. Linux truncates the controller/page/manifest names to the same prefix; terminal-error stderr remains the authoritative failure cause."}
        path = session.output / f"{label}-download-thread-health.json"
        path.write_text(json.dumps(record, indent=2) + "\n")
        session.artifacts.append(str(path))
        self.reject_worker_error(session)
        require(candidates, "The native download thread is absent after startup; inspect the preserved launcher log and thread inventory before attempting sleep")

    def validate_state(self, session: Session, state: dict[str, Any], *, pid: int | None = None) -> None:
        require(state["pid"] == (session.pid if pid is None else pid), "Lifecycle state belongs to another App PID")
        require(state["nonce"] == session.nonce, "Lifecycle state belongs to another launch")
        require(state["font_initializations"] == 1, "The native process did not initialize its fonts exactly once")
        require(isinstance(state["application"]["reader_image_jobs_started"], int), "Reader job telemetry is absent")

    def capture(self, session: Session, label: str, *, allow_missing: bool = False) -> Capture:
        image_path = session.output / f"{label}.framebuffer.png"
        self.qmp("screendump", str(image_path))
        control = self.control(session)
        state = control["state"]
        process = self.process(session.pid) if session.pid else None
        power = self.state()
        path = session.output / f"{label}.json"
        record = {**control, "process": process, "powerd_state": power, "framebuffer": str(image_path), "observed_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
        path.write_text(json.dumps(record, indent=2) + "\n")
        session.artifacts.extend([str(image_path), str(path)])
        # Preserve evidence before rejecting an unexpected PID/nonce or a missing
        # state. Failure collection is observational and does not adopt ownership.
        if not allow_missing:
            require(state is not None, "The lifecycle state record is unavailable")
            self.validate_state(session, state)
        return Capture(label, image_path, state, control["phase"], power, process, control["epoch"], control["sleep_ready"], control["resume_request"], control["resume_ready"], path)

    def park(self, session: Session, label: str = "sleep", hold_seconds: float = 2, *, before_capture: Capture | None = None, trigger: bool = True) -> ParkEvidence:
        # Gesture-timing cases can supply a prior image and trigger powerd before
        # entering this method, avoiding another capture in a 300 ms tap window.
        before = before_capture if before_capture is not None else self.capture(session, label + "-before")
        if trigger:
            self.power("powerButton")

        def parked() -> dict[str, Any] | None:
            self.reject_worker_error(session)
            control = self.control(session)
            state = control["state"]
            process = self.process(session.pid)
            power = self.state()
            if control["phase"] != "parked" or state is None or process is None or process["state"] != "T" or power != "screenSaver":
                return None
            self.validate_state(session, state)
            expected = f"{session.pid} {session.nonce} {control['epoch']}"
            require(control["sleep_ready"] == expected, "The sleep ACK does not match PID/nonce/epoch")
            require(state["epoch"] == control["epoch"] and state["phase"] == "suspended", "The App did not publish the matching suspended phase")
            require(state["platform"]["render_paused"] and state["platform"]["input_released"] and state["platform"]["parked"], "The park ACK does not establish released native ownership")
            require(state["application"]["suspended"] is True, "The application controller is not paused")
            return control

        self.wait("real powerd screensaver and retained-process park", parked)
        frozen = self.capture(session, label + "-parked")
        time.sleep(hold_seconds)
        held = self.capture(session, label + "-held")
        require(frozen.process is not None and held.process is not None and held.process["state"] == "T", "The App did not remain stopped")
        for field in ("birth", "cpu_ticks", "write_bytes"):
            require(frozen.process[field] == held.process[field], f"Stopped App accounting changed: {field}")
        require(frozen.state["platform"]["frame_write_count"] == held.state["platform"]["frame_write_count"], "The suspended diagnostic frame counter changed")
        return ParkEvidence(before, frozen, held, hold_seconds)

    def compare_application(self, before: dict[str, Any], after: dict[str, Any], *, stable_jobs: bool = True) -> dict[str, Any]:
        changes = {key: {"before": before[key], "after": after[key]} for key in STABLE_FIELDS if before[key] != after[key]}
        require(not changes, f"Retained navigation changed: {json.dumps(changes)}")
        if before["route"] == "reader":
            require(before["reader_page"] is not None and before["current_chapter"] is not None and before["displayed_reader"] is not None, "The reference Reader frame was never displayed")
        if stable_jobs:
            require(before["reader_image_jobs_started"] == after["reader_image_jobs_started"], "Stable wake scheduled a new Reader image job")
        return {"stable_fields": list(STABLE_FIELDS), "reader_image_jobs_before": before["reader_image_jobs_started"], "reader_image_jobs_after": after["reader_image_jobs_started"], "same_navigation": True}

    def image_difference(self, before: Path, after: Path, route: str) -> dict[str, Any]:
        with Image.open(before) as first, Image.open(after) as second:
            require(first.size == second.size, "Wake changed the physical screenshot geometry")
            width, height = first.size
            roi = (0, 0, width, height) if route == "reader" else (0, 60, width, max(61, height - 144))
            difference = ImageChops.difference(first.convert("RGB").crop(roi), second.convert("RGB").crop(roi))
            red, green, blue = difference.split()
            maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
            histogram = maximum.histogram()
            area = maximum.width * maximum.height
            mean = sum(value * count for value, count in enumerate(histogram)) / area
            changed = sum(histogram[17:]) / area
            different_pixels = area - histogram[0]
            exact = different_pixels == 0
            return {"roi": roi, "viewport_size": [width, height], "comparison": "full-viewport-rgb-exact" if route == "reader" else "library-body-rgb-tolerance", "ignored_pixels": width * height - area, "pixel_error_tolerance": 0 if route == "reader" else None, "different_pixels": different_pixels, "maximum_absolute_error": maximum.getextrema()[1], "mean_absolute_error": mean, "changed_ratio_above_16": changed, "exact_match": exact, "matches": exact if route == "reader" else mean <= self.arguments.max_mean_error and changed <= self.arguments.max_difference}

    def wake(self, session: Session, label: str = "wake", *, baseline: Capture, reference_image: Path | None = None, allow_restart: bool = False, trigger: bool = True, epoch: int | None = None, compare_image: bool = True) -> Capture:
        expected_epoch = baseline.epoch if epoch is None else epoch
        fallback_policy = None
        if allow_restart:
            control = self.control(session)
            require(trigger and control["phase"] == "fallback" and control["nonce"] == session.nonce and self.process(session.pid) is None, "Fallback startup protection requires this launcher's lost process and an explicit wake")
            session.startup_health_checked = False
            # The supervisor may relaunch immediately after stock becomes active.
            # Establish inhibition before waking the authorized fallback epoch.
            fallback_policy = self.protect_cold_start(f"fallback-{session.name}", allow_parked=True)
        if trigger:
            self.power("wakeUp")
        old_pid = session.pid

        def awake() -> dict[str, Any] | None:
            control = self.control(session)
            state = control["state"]
            require(control["nonce"] == session.nonce, "Wake changed the launcher nonce")
            if self.state() != "active" or control["phase"] != "active" or not state:
                return None
            if allow_restart:
                if not control["pid"] or control["pid"] == old_pid or state.get("pid") != control["pid"]:
                    return None
                self.validate_state(session, state, pid=control["pid"])
                return control
            require(control["pid"] == old_pid, "A required same-PID wake restarted the App")
            expected = f"{old_pid} {session.nonce} {expected_epoch}"
            if control["resume_ready"] != expected:
                return None
            self.validate_state(session, state)
            process = self.process(old_pid)
            require(process is not None and process["birth"] == session.birth and process["ppid"] == session.runner_pid, "Same-PID wake changed process lifetime or runner ancestry")
            require(state["epoch"] == expected_epoch, "Wake ACK belongs to a different sleep epoch")
            require(state["platform"]["resume_full_frame_count"] > baseline.state["platform"]["resume_full_frame_count"], "Wake did not submit a complete RGB rewrite and full refresh")
            require(not state["platform"]["render_paused"] and not state["platform"]["input_released"], "Wake did not reclaim native resources")
            return control

        control = self.wait("active powerd and completed native wake", awake)
        if allow_restart:
            process = self.process(control["pid"])
            require(process is not None, "The fallback ELF is not running")
            runner = self.process(process["ppid"])
            require(runner is not None and runner["ppid"] == session.launcher_pid, "Fallback escaped the owned runner chain")
            command = self.guest(f"cat /proc/{control['pid']}/cmdline").split(b"\0")
            require(b"--resume" in command, "The fallback process did not receive --resume")
            session.pid, session.birth, session.runner_pid = control["pid"], process["birth"], process["ppid"]
            self.validate_state(session, control["state"])
            time.sleep(3)
            self.worker_health(session, label="fallback-startup")
            session.startup_health_checked = True
            fallback_policy.update(case=session.name, pid=session.pid, native_window_ready=True, download_controller_healthy=True, setup_budget_seconds=3)
            policy_path = session.output / "fallback-startup-policy.json"
            policy_path.write_text(json.dumps(fallback_policy, indent=2) + "\n")
            session.artifacts.append(str(policy_path))
        after = self.capture(session, label + "-ready")
        route = baseline.state["application"]["route"]
        # The park ACK is fresh state, but its QMP image is the stock screensaver.
        # Use the independent pre-sleep App scanout for normal wake comparisons.
        image_reference = reference_image or baseline.image
        observations = []

        def rendered() -> Capture | None:
            nonlocal after
            measurement = self.image_difference(image_reference, after.image, route)
            observations.append({"capture": str(after.image), **measurement})
            if measurement["matches"]:
                return after
            after = self.capture(session, f"{label}-settling-{len(observations)}")
            return None

        if compare_image:
            after = self.wait("retained frame in actual QMP scanout", rendered, interval=0.5)
        else:
            # Pending-mode cases may legitimately repair their image after the
            # retained frame. Keep the actual capture and one diagnostic metric;
            # only pixel equality is waived, never PID/ACK/full-frame/input gates.
            with Image.open(image_reference) as reference_frame, Image.open(after.image) as observed_frame:
                reference_size, observed_size = reference_frame.size, observed_frame.size
            metric = self.image_difference(image_reference, after.image, route) if reference_size == observed_size else {"reference_size": reference_size, "observed_size": observed_size, "metric_unavailable": "Image geometry differs; pixel identity is explicitly not required for this repair case."}
            observations.append({"capture": str(after.image), **metric})
        (session.output / f"{label}-image-comparison.json").write_text(json.dumps({"reference": str(image_reference), "state_reference": str(baseline.state_path), "comparison_required": compare_image, "observations": observations, "note": "Rendering-settling observations never restart the application. When comparison_required is false, expected image repair must be checked separately and this record makes no stable/no-decode claim."}, indent=2) + "\n")
        session.artifacts.append(str(session.output / f"{label}-image-comparison.json"))
        return after

    def stop_observer(self, session: Session) -> None:
        if session.observer is None:
            return
        if session.observer.poll() is None and session.observer_pid:
            self.signal_owned(session.observer_pid, session.observer_birth, "INT", contains="lipc-wait-event")
        try:
            session.observer.wait(timeout=3)
        except subprocess.TimeoutExpired:
            if session.observer_pid:
                self.signal_owned(session.observer_pid, session.observer_birth, "KILL", contains="lipc-wait-event")
            try:
                session.observer.wait(timeout=3)
            except subprocess.TimeoutExpired:
                session.observer.kill()
                session.observer.wait(timeout=3)
        if session.observer_log is not None:
            session.observer_log.close()

    def final_capture(self, session: Session) -> dict[str, Any]:
        stem = f"{session.home}/data/logs/native-{session.name}-{session.pid}"
        result: dict[str, Any] = {}
        for extension, key in (("png", "image"), ("metadata.json", "metadata"), ("state.json", "state")):
            source = f"{stem}.{extension}"
            present = self.guest(f"if [ -f {shlex.quote(source)} ]; then printf present; fi").decode()
            require(present == "present", f"The App did not export its final {extension} capture")
            destination = session.output / f"final-app-{session.pid}.{extension}"
            destination.write_bytes(self.guest(f"cat {shlex.quote(source)}", timeout=60))
            session.artifacts.append(str(destination))
            result[key] = str(destination) if key == "image" else json.loads(destination.read_text())
        require(result["metadata"]["process_id"] == session.pid and result["metadata"]["font_initializations"] == 1, "Final capture belongs to the wrong process or font lifetime")
        return result

    def stop(self, session: Session, reason: str = "term", *, require_capture: bool = True) -> dict[str, Any]:
        self.identity()
        # A normal UI exit may already have removed the lock while the final
        # SSH channel bytes are still draining. Observation needs no live lock.
        if session.process.poll() is None and reason != "ui-exit":
            control = self.control(session)
            if reason == "cleanup" and not control["launcher_pid"] and not control["nonce"]:
                session.process.wait(timeout=10)
                control = None
            if control is None:
                pass
            elif not session.nonce or not session.launcher_pid:
                # Startup can fail before the App announces readiness. The
                # unique deployed script path and process birth prove only our
                # partial supervisor; never adopt a different launcher's lock.
                require(control["launcher_pid"] > 1 and re.fullmatch(r"[a-fA-F0-9]{32}", control["nonce"]) is not None, "The partial owned launcher did not publish its identity")
                launch_record = self.process(control["launcher_pid"])
                require(launch_record is not None, "The partial launcher process is missing")
                command = self.guest(f"cat /proc/{control['launcher_pid']}/cmdline").replace(b"\0", b" ").decode(errors="replace")
                require(session.home + "/launch.sh" in command, "The partial lock belongs to another script")
                session.launcher_pid, session.launcher_birth, session.nonce = control["launcher_pid"], launch_record["birth"], control["nonce"]
                if control["pid"]:
                    app_record = self.process(control["pid"])
                    if app_record is not None:
                        runner = self.process(app_record["ppid"])
                        require(runner is not None and runner["ppid"] == session.launcher_pid, "The partial ELF escaped its runner ancestry")
                        session.pid, session.birth, session.runner_pid = control["pid"], app_record["birth"], app_record["ppid"]
            if control is not None:
                require(control["nonce"] == session.nonce and control["launcher_pid"] == session.launcher_pid, "The owned launcher lock was replaced")
            if control is not None and reason in {"term", "cleanup"}:
                self.signal_owned(session.launcher_pid, session.launcher_birth, "TERM", contains=session.home + "/launch.sh")
            elif control is not None and reason == "synthetic-usb":
                self.mutate("synthetic-usb", f"printf '%s\\n' usbConfigured >{shlex.quote(session.lock + '/power-events')}")
            elif control is not None and reason != "ui-exit":
                raise AssertionError(f"Unknown stop reason: {reason}")
        code = session.process.wait(timeout=self.arguments.timeout)
        session.log_file.close()
        if reason != "cleanup":
            require(code == (0 if reason == "ui-exit" else 143), f"The owned launcher returned {code} during {reason}")
        require(not self.read(session.lock + "/pid"), "The supervisor left its lock behind")
        if session.pid:
            require(self.process(session.pid) is None, "The actual ELF remained alive after launcher exit")
        session.closed = True
        capture = self.final_capture(session) if require_capture else {}
        self.stop_observer(session)
        stock = session.output / "stock-after-exit.png"
        self.qmp("screendump", str(stock))
        session.artifacts.extend([str(stock), str(session.log_path)])
        return {"exit_code": code, "reason": reason, "same_app_pid": session.pid, "capture": capture, "raw_power_events": session.observer_path.read_text(errors="replace").splitlines() if session.observer_path else [], "stock_framebuffer": str(stock)}

    def cleanup(self, session: Session) -> None:
        errors = []
        if not session.closed:
            try:
                self.stop(session, reason="cleanup", require_capture=False)
            except Exception as error:
                errors.append(str(error))
        try:
            self.stop_observer(session)
        except Exception as error:
            errors.append(str(error))
        try:
            if self.state() != "active":
                self.power("wakeUp")
                self.wait("stock powerd active after cleanup", lambda: self.state() == "active", timeout=20)
        except Exception as error:
            errors.append(str(error))
        if session.process.poll() is None:
            session.process.terminate()
            try:
                session.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                session.process.kill()
                session.process.wait(timeout=3)
        if not session.log_file.closed:
            session.log_file.close()
        if errors:
            raise AssertionError("; ".join(errors))

    def park_checks(self, evidence: ParkEvidence) -> dict[str, Any]:
        return {
            "pid": evidence.parked.process["pid"],
            "epoch": evidence.parked.epoch,
            "hold_seconds": evidence.hold_seconds,
            "process_state": evidence.held.process["state"],
            "cpu_ticks_before": evidence.parked.process["cpu_ticks"],
            "cpu_ticks_after": evidence.held.process["cpu_ticks"],
            "write_bytes_before": evidence.parked.process["write_bytes"],
            "write_bytes_after": evidence.held.process["write_bytes"],
            "park_ack_frame_write_count": evidence.parked.state["platform"]["frame_write_count"],
            "held_snapshot_frame_write_count": evidence.held.state["platform"]["frame_write_count"],
            "counter_scope": "Frame counters are from the published park ACK snapshot. Independent retained-process freeze evidence is /proc T state and unchanged CPU ticks/write_bytes.",
        }

    def quick_power_cycle(self) -> dict[str, Any]:
        self.identity()
        require(bool(self.environment.get("usleep")), "The 0.2-second case requires guest usleep; fractional BusyBox sleep is not assumed")
        # Both property writes use one guest shell connection. Timestamps are
        # /proc/uptime samples immediately before/after each CLI invocation.
        script = ("""
read -r aidoku_down_start aidoku_rest </proc/uptime
lipc-set-prop -i com.lab126.powerd powerButton 1
read -r aidoku_down_end aidoku_rest </proc/uptime
usleep 200000
read -r aidoku_wake_start aidoku_rest </proc/uptime
""" + self.owned_script("lipc-set-prop -i com.lab126.powerd wakeUp 1") + """
read -r aidoku_wake_end aidoku_rest </proc/uptime
printf '%s\\n' "$aidoku_down_start" "$aidoku_down_end" "$aidoku_wake_start" "$aidoku_wake_end"
""").strip()
        values = [float(value) for value in self.mutate("quick-powerd", "set -eu; " + script).decode().splitlines()]
        require(len(values) == 4, "Quick power timing samples are incomplete")
        return {"requested_post_completion_gap_seconds": 0.2, "power_button_started_uptime": values[0], "power_button_finished_uptime": values[1], "wake_up_started_uptime": values[2], "wake_up_finished_uptime": values[3], "submission_gap_seconds": round(values[2] - values[0], 3), "post_completion_gap_seconds": round(values[2] - values[1], 3), "timestamp_scope": "Guest uptime samples bracket CLI calls; these are not internal powerd event timestamps."}

    @staticmethod
    def reader_actions() -> list[dict[str, Any]]:
        # A fresh Reader defaults to pure images. Never blindly toggle its menu.
        return []

    @staticmethod
    def library_actions() -> list[dict[str, Any]]:
        # The PW6 demo has twelve Reading titles and eight slots per page.
        return [action(1500, "library", "filter", "1"), action(2000, "page", "turn", "1")]

    def case_reader(self, session: Session) -> dict[str, Any]:
        parked = self.park(session)
        awake = self.wake(session, baseline=parked.parked, reference_image=parked.before.image)
        stable = self.compare_application(parked.parked.state["application"], awake.state["application"])
        exited = self.stop(session)
        require(exited["capture"]["state"]["menu"] is False, "The default pure-image Reader reopened its menu")
        return {"sleep": self.park_checks(parked), "same_pid_required": True, "same_pid_observed": awake.state["pid"] == parked.parked.state["pid"], "default_pure_reader": True, "retained_state": stable, "exit": exited}

    def case_library(self, session: Session) -> dict[str, Any]:
        parked = self.park(session)
        reference = parked.parked.state["application"]
        require(reference["route"] == "library" and reference["page"] == 2 and reference["library_filter"] == 1, "The library page-two Reading filter setup was not applied")
        awake = self.wake(session, baseline=parked.parked, reference_image=parked.before.image)
        stable = self.compare_application(reference, awake.state["application"])
        exited = self.stop(session)
        return {"sleep": self.park_checks(parked), "same_pid_required": True, "retained_state": stable, "exit": exited}

    def case_quick(self, session: Session) -> dict[str, Any]:
        # Initial active diagnostics precede image jobs. A normal warmup cycle
        # emits a fresh stable-frame record before testing consecutive quick wakes.
        warmup = self.park(session, "warmup")
        baseline = self.wake(session, "warmup-wake", baseline=warmup.parked, reference_image=warmup.before.image)
        self.compare_application(warmup.parked.state["application"], baseline.state["application"])
        observations = []
        for index in range(1, 3):
            timing = self.quick_power_cycle()
            expected_epoch = baseline.epoch + 1
            awake = self.wake(session, f"quick-{index}", baseline=baseline, trigger=False, epoch=expected_epoch)
            stable = self.compare_application(baseline.state["application"], awake.state["application"])
            time.sleep(1.2)
            process = self.process(session.pid)
            require(process is not None and process["birth"] == session.birth and process["state"] not in {"T", "t", "Z", "X"}, "The quick wake left a late-stopped or replaced process")
            observations.append({"expected_epoch": expected_epoch, "observed_epoch": awake.epoch, "timing": timing, "retained_state": stable, "process_after_late_ack_window": process, "font_initializations": awake.state["font_initializations"], "resume_full_frame_count": awake.state["platform"]["resume_full_frame_count"]})
            baseline = awake
        exited = self.stop(session)
        require(exited["capture"]["state"]["menu"] is False, "Quick wake reopened the default Reader menu")
        return {"warmup": self.park_checks(warmup), "quick_rounds": observations, "same_pid_required": True, "default_pure_reader": True, "note": "A valid quick cycle may cancel before park and need not increment same_process_resumes.", "exit": exited}

    def case_fallback(self, session: Session) -> dict[str, Any]:
        parked = self.park(session)
        old_pid = session.pid
        self.signal_owned(old_pid, session.birth, "KILL", parent=session.runner_pid, contains=session.home + "/app/aidoku.native")
        self.wait("sleep-time fallback after a deliberately killed ELF", lambda: self.control(session)["phase"] == "fallback" and self.process(old_pid) is None)
        self.capture(session, "killed-while-sleeping", allow_missing=True)
        awake = self.wake(session, "fallback-wake", baseline=parked.parked, reference_image=parked.before.image, allow_restart=True)
        require(session.pid != old_pid, "The required fallback did not create a new PID")
        exited = self.stop(session)
        final = exited["capture"]["state"]
        reference = parked.parked.state["application"]
        # A new process's initial lifecycle record may precede asynchronous
        # decoding. The final exported state and actual scanout establish its page.
        for field in ("route", "page", "current_book", "current_chapter", "reader_page", "reader_offset", "reader_zoom"):
            require(field in final and final[field] == reference[field], f"Fallback did not restore {field}")
        preferences = final["preferences"]
        for field in ("landscape", "spread"):
            require(preferences[field] == reference[field], f"Fallback changed orientation/layout: {field}")
        require(final["menu"] is False, "Fallback opened the default Reader menu")
        return {"sleep": self.park_checks(parked), "deliberate_signal": "SIGKILL while parked", "old_pid": old_pid, "new_pid": session.pid, "same_pid_required": False, "fallback_observed": True, "resume_argument_verified": True, "new_process_font_initializations": awake.state["font_initializations"], "final_page_restored": True, "exit": exited}

    def case_explicit_exit(self, session: Session) -> dict[str, Any]:
        parked = self.park(session)
        awake = self.wake(session, baseline=parked.parked, reference_image=parked.before.image)
        stable = self.compare_application(parked.parked.state["application"], awake.state["application"])
        exited = self.stop(session, "ui-exit")
        require(exited["capture"]["state"]["menu"] is False, "The explicit-exit Reader did not retain pure-image mode")
        observations = []
        for _ in range(3):
            observation = {"lock_pid": self.read(session.lock + "/pid"), "app_process": self.process(session.pid), "launcher_returncode": session.process.poll()}
            require(not observation["lock_pid"] and observation["app_process"] is None and observation["launcher_returncode"] == 0, "Explicit UI exit reopened an application")
            observations.append(observation)
            time.sleep(1)
        return {"sleep": self.park_checks(parked), "retained_state": stable, "exit_trigger": "Timed explicit App.dispatch('app','exit','') replay after the setup budget", "no_reopen_observations": observations, "exit": exited}

    def case_usb(self, session: Session) -> dict[str, Any]:
        parked = self.park(session)
        exited = self.stop(session, "synthetic-usb")
        time.sleep(1)
        require(self.process(session.pid) is None and not self.read(session.lock + "/pid"), "Synthetic USB policy reopened the App")
        return {"sleep": self.park_checks(parked), "usb_evidence_kind": "Synthetic usbConfigured line written only to this owned launcher's FIFO; no actual USB state/property changed", "no_reopen_observed": True, "exit": exited}

    def run_case(self, name: str, screen: str, actions: list[dict[str, Any]], callback: Callable[[Session], dict[str, Any]]) -> None:
        result = Result(name, self.round_number)
        started = time.monotonic()
        session = None
        try:
            session = self.start(name, screen, actions)
            result.checks = callback(session)
            result.passed = True
        except Exception as error:
            result.error = str(error)
            session = session or self.current
            if session is not None:
                try:
                    self.capture(session, "failure", allow_missing=True)
                except Exception as capture_error:
                    result.checks["failure_capture_error"] = str(capture_error)
        finally:
            session = session or self.current
            if session is not None:
                try:
                    self.cleanup(session)
                except Exception as cleanup_error:
                    result.cleanup_error = str(cleanup_error)
                    result.passed = False
                result.artifacts = list(dict.fromkeys(session.artifacts))
            result.duration_seconds = round(time.monotonic() - started, 3)
            self.results.append(result)
            self.write_report()
            print(f"{'PASS' if result.passed else 'FAIL'} round-{self.round_number}/{name}" + (f": {result.error or result.cleanup_error}" if not result.passed else ""), flush=True)

    def acquire_guard(self) -> None:
        self.identity()
        command = f"mkdir {shlex.quote(self.guard)} && printf '%s\\n' {shlex.quote(self.run_id)} >{shlex.quote(self.guard + '/owner')}"
        self.command("suite-lock", command)
        self.guard_owned = True
        try:
            previous = self.guest("lipc-get-prop -i com.lab126.powerd preventScreenSaver").decode().strip()
            require(previous in {"0", "1"}, "The emulator screensaver policy is not observable")
            self.screensaver_policy = {
                "saved_prevent_screensaver": previous,
                "usb_state_before": self.guest("lipc-get-prop -i com.lab126.powerd usbState").decode().strip(),
                "override_requested": bool(self.arguments.allow_screensaver),
                "changed": False,
                "restore_needed": False,
                "restore_attempted": False,
                "activated": False,
                "protected": False,
                "restored": False,
                "cold_start_protection": [],
                "sleep_enable_observations": [],
                "scope": "Save the original inhibition once; protect every fixture deployment and cold startup, then reset setup activity and enable that session's first controlled sleep. USB properties are never written.",
            }
            require(previous == "0" or self.arguments.allow_screensaver, "The emulator boot policy inhibits all power-button sleep. Pass --allow-screensaver to save, temporarily clear, and restore only preventScreenSaver.")
        except BaseException:
            self.release_guard()
            raise

    def release_guard(self) -> None:
        if not self.guard_owned:
            return
        self.identity()
        require(self.read(self.guard + "/owner") == self.run_id, "The suite guard changed owner; it will not be removed")
        require(self.current is None or self.current.closed, "The owned session is not closed; its suite guard is retained")
        require(not self.read(self.lock + "/pid") if self.lock else True, "An owned session remains; its suite guard is retained")
        if self.screensaver_policy is not None:
            previous = self.guest("lipc-get-prop -i com.lab126.powerd preventScreenSaver").decode().strip()
            require(previous in {"0", "1"}, "The emulator screensaver policy is not observable during restoration")
            self.screensaver_policy["before_restore"] = previous
            if previous != self.screensaver_policy["saved_prevent_screensaver"]:
                self.screensaver_policy["restore_needed"] = True
                self.screensaver_policy["restore_attempted"] = True
                self.mutate("emulator-policy-restore", "lipc-set-prop -i com.lab126.powerd preventScreenSaver " + self.screensaver_policy["saved_prevent_screensaver"])
            observed = self.guest("lipc-get-prop -i com.lab126.powerd preventScreenSaver").decode().strip()
            self.screensaver_policy["restored"] = observed == self.screensaver_policy["saved_prevent_screensaver"]
            self.screensaver_policy["after_restore"] = observed
            self.screensaver_policy["restore_needed"] = not self.screensaver_policy["restored"]
            self.screensaver_policy["usb_state_after"] = self.guest("lipc-get-prop -i com.lab126.powerd usbState").decode().strip()
            require(self.screensaver_policy["restored"], "The original emulator screensaver policy was not restored")
            require(self.screensaver_policy["usb_state_after"] == self.screensaver_policy["usb_state_before"], "The observed stock USB state changed during this suite")
        self.mutate("suite-unlock", f"rm -f {shlex.quote(self.guard + '/owner')}; rmdir {shlex.quote(self.guard)}")
        self.guard_owned = False

    def finish_suite(self, *, release: bool = True) -> None:
        try:
            if release:
                errors = []
                if self.current is not None and not self.current.closed:
                    try:
                        self.cleanup(self.current)
                    except Exception as error:
                        errors.append(f"Close owned session: {error}")
                try:
                    # release_guard independently requires a closed session and
                    # the original boot/owner before restoring or unlocking.
                    self.release_guard()
                except Exception as error:
                    errors.append(f"Restore policy and release suite: {error}")
                if errors:
                    raise AssertionError("; ".join(errors))
            else:
                self.suite_cleanup_error = "Global QMP input cleanup is incomplete; the suite guard and saved policy are retained"
        except Exception as error:
            self.suite_cleanup_error = str(error)
            raise
        finally:
            # Report both the original failure and restoration failure even if
            # ownership loss correctly prevents further guest mutations.
            self.write_report()

    def write_report(self, setup_error: str | None = None) -> None:
        report = {
            "environment": "Isolated Kindle firmware guest on the designated remote Linux worker",
            "run_id": self.run_id, "machine": self.arguments.machine,
            "observed_environment": self.environment, "frozen_input_sha256": self.hashes,
            "binary_source_path": str(self.binary), "requested_rounds": self.arguments.rounds,
            "source_snapshot_sha256_declared_by_root": getattr(self.arguments, "source_snapshot_sha256", None),
            "prepared_namespaces": self.prepared_namespaces,
            "screensaver_policy": self.screensaver_policy,
            "pixel_comparison_contract": {"reader": "full-viewport-rgb-exact", "reader_pixel_error_tolerance": 0, "reader_ignored_pixels": 0, "library": "body-region-with-explicit-tolerances", "pending_repair": "explicit-pixel-identity-waiver-with-PID-token-input-and-full-frame-gates"},
            "passed": sum(result.passed for result in self.results),
            "failed": sum(not result.passed for result in self.results),
            "cases": [dataclasses.asdict(result) for result in self.results],
            "commands": self.commands, "setup_error": setup_error or self.suite_error or self.suite_cleanup_error,
            "suite_error": self.suite_error, "suite_cleanup_error": self.suite_cleanup_error,
            "coverage_limits": [
                "These are firmware-emulator software and stopped-process results, not physical Kindle suspend, panel, battery, RF, or flash-performance acceptance.",
                "The default matrix is offline/demo and never opens real accounts or authenticated sources.",
                "Raw powerd payloads are only those actually recorded by the independent observer; state/ACK transitions do not prove every individual payload was seen.",
                "Frame-write counters are lifecycle publication snapshots. /proc stopped state and unchanged CPU/write accounting independently establish process retention during the hold.",
                "USB policy uses an explicitly synthetic event in the owned FIFO, not a physical USB transition.",
                "Stable Reader comparisons cover every RGB pixel in the real QMP scanout with zero tolerance and no exclusions. Library comparisons exclude dynamic system regions; pending-mode repair explicitly waives old-image identity. App snapshot PNGs remain separate evidence.",
            ],
        }
        (self.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")

    def run(self) -> int:
        matrix = [
            ("reader-stable", "reader", self.reader_actions(), self.case_reader),
            ("library-page-filter", "library", self.library_actions(), self.case_library),
            ("consecutive-quick-wake", "reader", self.reader_actions(), self.case_quick),
            ("killed-sleep-fallback", "reader", self.reader_actions(), self.case_fallback),
            # Firmware SSH/capture observations intentionally run outside the
            # application timer. Leave a bounded window for a complete real
            # sleep/wake before the explicit exit intent is replayed.
            ("explicit-exit", "reader", [*self.reader_actions(), action(90000, "app", "exit")], self.case_explicit_exit),
            ("synthetic-usb", "library", self.library_actions(), self.case_usb),
        ]
        if self.arguments.cases:
            selected = set(self.arguments.cases.split(","))
            require(selected <= {case[0] for case in matrix}, "An unknown base case was selected")
            matrix = [case for case in matrix if case[0] in selected]
            require(bool(matrix), "No sleep/wake cases were selected")
        try:
            self.acquire_guard()
            for number in range(1, self.arguments.rounds + 1):
                self.prepare_round(number)
                for name, screen, actions, callback in matrix:
                    self.run_case(name, screen, actions, callback)
                    require(self.current is None or self.current.closed, "Cleanup did not close the preceding owned session")
        except Exception as error:
            self.suite_error = str(error)
            raise
        finally:
            self.finish_suite()
        print(json.dumps({"report": str(self.output / 'report.json'), "passed": sum(case.passed for case in self.results), "failed": sum(not case.passed for case in self.results)}), flush=True)
        return 0 if all(case.passed for case in self.results) else 1


def build_parser(*, require_source_snapshot: bool = False) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--source-snapshot-sha256", required=require_source_snapshot, help="Root-frozen production source identity; recorded as a declaration rather than a recomputed tree hash")
    parser.add_argument("--emulator-root", type=Path, default=Path("/root/aidoku-emulator/eink-emulator"))
    parser.add_argument("--guest-ssh", type=Path, default=Path("/root/aidoku-emulator/guest-ssh"))
    parser.add_argument("--machine", default="aidoku-pw6-5201")
    parser.add_argument("--firmware", default="5.20.1")
    parser.add_argument("--profile", default="pw6", choices=("pw5", "pw6", "scribe", "scribe2", "scribe3", "scribe3-nofrontlight"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/sleep-wake"))
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--max-mean-error", type=float, default=1.5, help="Maximum per-pixel RGB channel error mean for non-Reader regions only; stable Reader requires exact identity")
    parser.add_argument("--max-difference", type=float, default=0.005, help="Maximum changed-pixel ratio above 16 for non-Reader regions only; stable Reader has zero tolerance")
    parser.add_argument("--cases", help="Optional comma-separated base matrix case names")
    parser.add_argument("--guest-binary-pool", help="Optional guest directory containing aidoku.native and inputs.json with binary_sha256")
    parser.add_argument("--allow-screensaver", action="store_true", help="Temporarily clear the isolated emulator's boot-time preventScreenSaver policy, then restore its saved value; never changes USB state")
    return parser


def main() -> int:
    parser = build_parser()
    arguments = parser.parse_args()
    if os.name != "posix":
        parser.error("Run this script on the designated remote Linux worker")
    if not 1 <= arguments.rounds <= 10 or arguments.timeout < 30:
        parser.error("Use 1..10 rounds and a timeout of at least 30 seconds")
    if not 0 <= arguments.max_difference <= 1 or arguments.max_mean_error < 0:
        parser.error("Pixel difference limits must be nonnegative and the ratio at most one")
    verifier = None
    try:
        verifier = SleepVerifier(arguments)
        return verifier.run()
    except Exception as error:
        print(f"Sleep/wake verification failed: {error}", file=sys.stderr)
        if verifier is not None:
            verifier.write_report(setup_error=str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
