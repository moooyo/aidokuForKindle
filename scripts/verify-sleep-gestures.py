#!/usr/bin/env python3
"""Exercise real Kindle input immediately before retained-process sleep.

Run only in an exclusive isolated Aidoku firmware-VM window on the remote
Linux worker. Setup uses timed settings intents; the tap/seek actions use
the QMP touch device and the guest receives a real powerButton request.
Reported timing brackets command submission, not internal Slint timers.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import select
import shlex
import subprocess
import sys
import time
from typing import Any

from PIL import Image, ImageChops


def load_base():
    name = "aidoku_sleep_gesture_base"
    path = Path(__file__).with_name("verify-sleep-wake.py")
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError("The shared sleep/wake driver is unavailable")
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


base = load_base()


def reader_menu_features(path: Path, page: int | None, total: int | None) -> dict[str, Any]:
    """Recognize the frozen portrait Paperwhite menu and seek-thumb geometry."""
    with Image.open(path) as source:
        image = source.convert("L")
        width, height = image.size
        base.require(width % 2 == height % 2 == 0, "Menu features require an unchanged native scale-2 viewport")
        scale = 2
        bottom = height - 188 * scale
        header = round(89.5 * scale)
        controls = bottom + 119 * scale
        track_x = 20 * scale
        track_end = width - track_x
        track_y = bottom + (39 + 16) * scale

        def fraction(rectangle, predicate):
            left, top, right, lower = rectangle
            base.require(0 <= left < right <= width and 0 <= top < lower <= height, "A menu feature escaped the original physical viewport")
            values = image.crop(rectangle).tobytes()
            return sum(predicate(value) for value in values) / len(values)

        def rule(y):
            return max(fraction((0, row, width, row + 1), lambda value: value <= 64) for row in range(y - 3, y + 3))

        features = {
            "physical_size": [width, height], "scale_factor": scale,
            "geometry_source": "ui/reader.slint::ReaderMetrics portrait Paperwhite",
            "header_separator_y": header, "bottom_separator_y": bottom,
            "toolbar_separator_y": controls, "seek_track_y": track_y,
            "header_separator_dark_ratio": rule(header),
            "bottom_separator_dark_ratio": rule(bottom),
            "toolbar_separator_dark_ratio": rule(controls),
            "seek_track_nonpaper_ratio": max(fraction((track_x, row, track_end, row + 1), lambda value: value < 224) for row in range(track_y + 2, track_y + 6)),
        }
        structure = all(features[key] >= 0.98 for key in ("header_separator_dark_ratio", "bottom_separator_dark_ratio", "toolbar_separator_dark_ratio")) and features["seek_track_nonpaper_ratio"] >= 0.95
        features["menu_structure"] = structure
        features["closed_structure"] = all(features[key] < 0.98 for key in ("header_separator_dark_ratio", "bottom_separator_dark_ratio", "toolbar_separator_dark_ratio")) and features["seek_track_nonpaper_ratio"] < 0.95
        features["thresholds"] = {"separator_dark_ratio": 0.98, "seek_track_nonpaper_ratio": 0.95, "thumb_inner_dark_ratio": 0.90, "thumb_paper_gap_ratio": 0.85}
        if page is not None and total is not None:
            base.require(1 <= page <= total, "The menu thumb oracle needs a valid displayed page")
            center = scale * (20 + (width / scale - 40) * (1 - page / total))
            left = round(center - 25)
            top = round(track_y - 10.5 * scale)
            inner = (left + 11, top + 11, left + 39, top + 39)
            gap = (left + 5, top + 5, left + 45, top + 8)
            features.update(expected_page=page, expected_total=total, thumb_inner_roi=list(inner), thumb_gap_roi=list(gap),
                            thumb_inner_dark_ratio=fraction(inner, lambda value: value <= 64),
                            thumb_paper_gap_ratio=fraction(gap, lambda value: value >= 224))
            features["expected_thumb"] = features["thumb_inner_dark_ratio"] >= 0.90 and features["thumb_paper_gap_ratio"] >= 0.85
        else:
            features["expected_thumb"] = True
        features["ready"] = structure and features["expected_thumb"]
        return features


class PreparedPowerPulse:
    """Prepare SSH/identity before the critical tap-to-power timing window."""

    def __init__(self, verifier: "GestureVerifier", session) -> None:
        self.verifier = verifier
        self.session = session
        self.process: subprocess.Popen[bytes] | None = None
        self.pid = 0
        self.birth = 0
        self.buffer = b""
        self.stderr_path = session.output / "prepared-power.stderr"
        self.transcript_path = session.output / "prepared-power.json"
        self.stderr_file = None
        self.records: list[dict[str, Any]] = []

    def line(self, prefix: str, timeout: float = 10) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            while b"\n" in self.buffer:
                raw, self.buffer = self.buffer.split(b"\n", 1)
                value = raw.decode(errors="replace").strip()
                self.records.append({"received_line": value, "host_received_monotonic": time.monotonic()})
                if value.startswith(prefix + " ") or value == prefix:
                    return value
            ready, _, _ = select.select([self.process.stdout], [], [], max(0.01, deadline - time.monotonic()))
            if not ready:
                continue
            data = os.read(self.process.stdout.fileno(), 4096)
            if not data:
                raise AssertionError(f"The prepared power channel ended before {prefix}; see {self.stderr_path}")
            self.buffer += data
        raise AssertionError(f"The prepared power channel timed out waiting for {prefix}")

    def send(self, command: str, prefix: str) -> dict[str, Any]:
        base.require(self.process is not None and self.process.poll() is None, "The prepared power channel is not live")
        record: dict[str, Any] = {"scope": "prepared-power", "command": command, "host_sent_monotonic": time.monotonic()}
        self.records.append(record)
        self.verifier.commands.append(record)
        self.process.stdin.write((self.verifier.owned_script(command) + "\n").encode())
        self.process.stdin.flush()
        record["response"] = self.line(prefix)
        record["host_completed_monotonic"] = time.monotonic()
        return record

    def open(self) -> None:
        self.verifier.identity()
        self.stderr_file = self.stderr_path.open("wb")
        self.process = subprocess.Popen([str(self.verifier.guest_ssh), "sh"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr_file)
        command = (
            "set -eu; aidoku_marker=$(cat /var/local/aidoku-emulator-id); "
            f"[ \"$aidoku_marker\" = {shlex.quote(self.verifier.arguments.machine)} ]; "
            "read -r aidoku_uptime aidoku_rest </proc/uptime; "
            "printf 'BRIDGE_READY %s %s\\n' \"$$\" \"$aidoku_uptime\""
        )
        response = self.send(command, "BRIDGE_READY")["response"].split()
        base.require(len(response) == 3 and response[1].isdigit(), "The prepared power channel did not identify its process")
        self.pid = int(response[1])
        observed = self.verifier.process(self.pid)
        base.require(observed is not None, "The prepared guest shell exited before the gesture")
        self.birth = observed["birth"]
        self.session.artifacts.extend([str(self.stderr_path), str(self.transcript_path)])

    def sample_before_gesture(self) -> dict[str, Any]:
        return self.send("read -r aidoku_uptime aidoku_rest </proc/uptime; printf 'BEFORE_GESTURE %s\\n' \"$aidoku_uptime\"", "BEFORE_GESTURE")

    def trigger_sleep(self) -> dict[str, Any]:
        # No identity query, screenshot, new SSH handshake, or process probe is
        # inserted here. The prepared connection is already bound to this VM.
        command = (
            "read -r aidoku_before aidoku_rest </proc/uptime; "
            "lipc-set-prop -i com.lab126.powerd powerButton 1; "
            "read -r aidoku_after aidoku_rest </proc/uptime; "
            "printf 'POWER_SUBMITTED %s %s\\n' \"$aidoku_before\" \"$aidoku_after\"; exit 0"
        )
        record = self.send(command, "POWER_SUBMITTED")
        fields = record["response"].split()
        base.require(len(fields) == 3, "The power submission uptime samples are incomplete")
        record["guest_started_uptime"] = float(fields[1])
        record["guest_finished_uptime"] = float(fields[2])
        return record

    def close(self) -> None:
        try:
            if self.process is not None and self.process.poll() is None:
                try:
                    self.process.stdin.write(b"exit 0\n")
                    self.process.stdin.flush()
                    self.process.stdin.close()
                except BrokenPipeError:
                    pass
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    if self.pid:
                        self.verifier.signal_owned(self.pid, self.birth, "TERM", contains="sh")
                    try:
                        self.process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        if self.pid:
                            self.verifier.signal_owned(self.pid, self.birth, "KILL", contains="sh")
                        self.process.kill()
                        self.process.wait(timeout=3)
        finally:
            if self.stderr_file is not None:
                self.stderr_file.close()
            self.transcript_path.write_text(json.dumps(self.records, indent=2) + "\n")


class PreparedQuickPulse(PreparedPowerPulse):
    """Test-only STOP accumulator; only the product supervisor performs wake."""

    def __init__(self, verifier, session) -> None:
        super().__init__(verifier, session)
        self.bound = False
        self.status_path = session.home + "/data/logs/quick-watchdog-status"
        self.safety: dict[str, Any] = {}

    def bind_target(self) -> None:
        record = self.verifier.process(self.session.pid)
        base.require(record is not None and record["birth"] == self.session.birth and record["ppid"] == self.session.runner_pid, "Quick fixture target does not belong to the recorded runner lifetime")
        comm = self.verifier.guest(f"cat /proc/{self.session.pid}/comm").decode().strip()
        base.require(comm and " " not in comm and "(" not in comm and ")" not in comm, "Quick fixture requires an unambiguous ELF comm field")
        script = r'''
q_app=@PID@; q_birth=@BIRTH@; q_parent=@PARENT@; q_nonce=@NONCE@
q_lock=@LOCK@; q_comm=@COMM@; q_status=@STATUS@; q_child_record=@CHILD@
q_boot=@BOOT@; q_suite_guard=@SUITE_GUARD@; q_suite_owner=@SUITE_OWNER@
q_shell=$$; q_watchdog=''; q_watchdog_birth=''; q_timer=''
q_stat() {
    [ -r /proc/$1/stat ] || return 1
    read -r q_v_pid q_v_comm q_v_state q_v_ppid q_05 q_06 q_07 q_08 q_09 q_10 q_11 q_12 q_13 q_14 q_15 q_16 q_17 q_18 q_19 q_20 q_21 q_v_birth q_rest </proc/$1/stat
}
q_suite_owned() {
    read -r q_current_boot </proc/sys/kernel/random/boot_id || return 1
    read -r q_current_owner <"$q_suite_guard/owner" || return 1
    [ "$q_current_boot" = "$q_boot" ] && [ "$q_current_owner" = "$q_suite_owner" ]
}
q_owned() {
    q_suite_owned || return 1
    q_stat "$q_app" || return 1
    [ "$q_v_pid" = "$q_app" ] && [ "$q_v_comm" = "$q_comm" ] && [ "$q_v_birth" = "$q_birth" ] && [ "$q_v_ppid" = "$q_parent" ] || return 1
    case "$q_v_state" in Z|X) return 1 ;; esac
    read -r q_lock_pid <"$q_lock/application-pid" || return 1
    read -r q_lock_nonce <"$q_lock/nonce" || return 1
    [ "$q_lock_pid" = "$q_app" ] && [ "$q_lock_nonce" = "$q_nonce" ]
}
q_watchdog_owned() {
    [ -n "$q_watchdog" ] && q_stat "$q_watchdog" || return 1
    [ "$q_v_ppid" = "$q_shell" ] && [ "$q_v_birth" = "$q_watchdog_birth" ] || return 1
    case "$q_v_state" in Z|X) return 1 ;; esac
}
q_cleanup() {
    set +e
    if q_watchdog_owned; then
        kill -TERM "$q_watchdog" 2>/dev/null
        q_remaining=2
        while q_watchdog_owned && [ "$q_remaining" -gt 0 ]; do sleep 1; q_remaining=$((q_remaining - 1)); done
        if q_watchdog_owned; then
            if read -r q_timer_pid q_timer_birth <"$q_child_record"; then
                if q_stat "$q_timer_pid" && [ "$q_v_ppid" = "$q_watchdog" ] && [ "$q_v_birth" = "$q_timer_birth" ]; then kill -KILL "$q_timer_pid" 2>/dev/null; fi
            fi
            kill -KILL "$q_watchdog" 2>/dev/null
        fi
    fi
    if [ -n "$q_watchdog" ]; then wait "$q_watchdog" 2>/dev/null; q_watchdog=''; fi
    q_final_cont=0
    if q_owned; then kill -CONT "$q_app" 2>/dev/null; q_final_cont=1; fi
    [ -f "$q_status" ] || printf '%s\n' cancelled >"$q_status"
    printf 'QUICK_CLEANED %s\n' "$q_final_cont"
}
trap 'q_cleanup' 0
trap 'exit 143' HUP INT TERM
q_owned || exit 94
rm -f "$q_status" "$q_child_record"
printf 'TARGET_BOUND %s %s\n' "$q_app" "$q_birth"
'''
        replacements = {"@PID@": str(self.session.pid), "@BIRTH@": str(self.session.birth), "@PARENT@": str(self.session.runner_pid), "@NONCE@": shlex.quote(self.session.nonce), "@LOCK@": shlex.quote(self.session.lock), "@COMM@": shlex.quote(f"({comm})"), "@STATUS@": shlex.quote(self.status_path), "@CHILD@": shlex.quote(self.session.home + "/data/logs/quick-watchdog-child"), "@BOOT@": shlex.quote(self.verifier.boot_id), "@SUITE_GUARD@": shlex.quote(self.verifier.guard), "@SUITE_OWNER@": shlex.quote(self.verifier.run_id)}
        for key, value in replacements.items():
            script = script.replace(key, value)
        self.send(script.strip(), "TARGET_BOUND")
        self.bound = True

    def trigger_quick(self) -> dict[str, Any]:
        base.require(self.bound, "Quick fixture target is not bound")
        script = r'''
q_owned || exit 95
read -r q_stop_before q_rest </proc/uptime
kill -STOP "$q_app"
read -r q_stop_after q_rest </proc/uptime
(
    read -r q_watchdog_self q_rest </proc/self/stat
    q_timer=''; q_timer_birth=''
    q_stop_watchdog() {
        set +e
        if [ -n "$q_timer" ] && q_stat "$q_timer" && [ "$q_v_ppid" = "$q_watchdog_self" ] && [ "$q_v_birth" = "$q_timer_birth" ]; then kill -TERM "$q_timer" 2>/dev/null; wait "$q_timer" 2>/dev/null; fi
        exit 0
    }
    trap - 0
    trap q_stop_watchdog HUP INT TERM
    sleep 6 & q_timer=$!
    if q_stat "$q_timer"; then q_timer_birth=$q_v_birth; fi
    printf '%s %s\n' "$q_timer" "$q_timer_birth" >"$q_child_record"
    wait "$q_timer" || :
    if q_owned; then
        read -r q_watchdog_time q_rest </proc/uptime
        if [ "$q_v_state" = T ] || [ "$q_v_state" = t ]; then
            printf 'triggered %s %s %s\n' "$q_watchdog_time" "$q_app" "$q_birth" >"$q_status"
            kill -CONT "$q_app"
        else
            printf 'checked-running %s\n' "$q_watchdog_time" >"$q_status"
        fi
    else
        printf '%s\n' target-gone >"$q_status"
    fi
) </dev/null >/dev/null 2>&1 &
q_watchdog=$!
if q_stat "$q_watchdog"; then q_watchdog_birth=$q_v_birth; fi
read -r q_down_before q_rest </proc/uptime
q_suite_owned || exit 96
lipc-set-prop -i com.lab126.powerd powerButton 1
read -r q_down_after q_rest </proc/uptime
usleep 200000
read -r q_wake_before q_rest </proc/uptime
q_suite_owned || exit 96
lipc-set-prop -i com.lab126.powerd wakeUp 1
read -r q_wake_after q_rest </proc/uptime
printf 'QUICK_SUBMITTED %s %s %s %s %s %s %s\n' "$q_watchdog" "$q_stop_before" "$q_stop_after" "$q_down_before" "$q_down_after" "$q_wake_before" "$q_wake_after"
'''
        record = self.send(script.strip(), "QUICK_SUBMITTED")
        fields = record["response"].split()
        base.require(len(fields) == 8, "Quick accumulator submission telemetry is incomplete")
        record.update(watchdog_pid=int(fields[1]), guest_stop_started_uptime=float(fields[2]), guest_stop_finished_uptime=float(fields[3]), guest_power_started_uptime=float(fields[4]), guest_power_finished_uptime=float(fields[5]), guest_wake_started_uptime=float(fields[6]), guest_wake_finished_uptime=float(fields[7]), watchdog_limit_seconds=6)
        return record

    def finish_safety(self) -> dict[str, Any]:
        if self.bound and self.process is not None and self.process.poll() is None:
            self.safety["cleanup"] = self.send("q_cleanup; trap - 0 HUP INT TERM; printf 'QUICK_FINISHED\\n'; exit 0", "QUICK_FINISHED")
            self.bound = False
        self.safety["watchdog_status"] = self.verifier.read(self.status_path)
        self.safety["watchdog_triggered"] = self.safety["watchdog_status"].startswith("triggered ")
        return self.safety

    def close(self) -> None:
        try:
            self.finish_safety()
        finally:
            # If the channel died, an EXIT trap still attempted the same owned
            # CONT. A final independent guarded command cannot authorize resume
            # success; it is cleanup only, after evidence/verdict collection.
            try:
                self.verifier.identity()
                guard = f"[ \"$(awk '{{print $22}}' /proc/{self.session.pid}/stat 2>/dev/null)\" = {self.session.birth} ] && [ \"$(cat {shlex.quote(self.session.lock + '/nonce')} 2>/dev/null)\" = {shlex.quote(self.session.nonce)} ] && [ \"$(cat {shlex.quote(self.session.lock + '/application-pid')} 2>/dev/null)\" = {self.session.pid} ] && [ \"$(sed -n 's/^PPid:[[:space:]]*//p' /proc/{self.session.pid}/status 2>/dev/null)\" = {self.session.runner_pid} ]"
                self.safety["finally_cont"] = self.verifier.mutate("quick-finally-safety", f"if {guard}; then kill -CONT {self.session.pid}; printf continued; else printf no-owned-target; fi").decode().strip()
            finally:
                super().close()


class NativeQuickNotificationGuard:
    """Optional test-only notification scheduler; it never creates tokens."""

    def __init__(self, verifier: "GestureVerifier", session, epoch: int) -> None:
        self.verifier = verifier
        self.session = session
        self.epoch = epoch
        self.destination = session.home + "/app/quick-input-guard"
        self.output = session.home + "/data/logs/quick-native-guard.json"
        self.host_output = session.output / "quick-native-guard.json"
        self.log_path = session.output / "quick-native-guard.log"
        self.process: subprocess.Popen[bytes] | None = None
        self.log_file = None
        self.pid = 0
        self.birth = 0
        self.parent = 0
        self.observation: dict[str, Any] = {"enabled": True, "input_sha256": verifier.quick_guard_hash, "host_input": str(verifier.quick_guard_path), "guest_binary": self.destination, "guest_output": self.output, "expected_epoch": epoch, "scope": "Test-only duplicate of the supervisor's already authorized USR2 while the owned ELF is stopped; normal CONT remains the real supervisor's responsibility."}

    def start(self) -> None:
        self.verifier.identity()
        base.require(not self.verifier.read(self.output) and not self.verifier.read(self.output + ".ready"), "The optional guard output already exists; it will not be overwritten")
        self.verifier.put(self.verifier.quick_guard_bytes, self.destination, "755")
        actual = self.verifier.guest(f"sha256sum {shlex.quote(self.destination)}").decode().split()[0]
        base.require(actual == self.verifier.quick_guard_hash, "The deployed notification guard hash differs from its frozen input")
        arguments = [self.destination, str(self.session.pid), str(self.session.birth), str(self.session.runner_pid), self.session.nonce, self.session.lock, str(self.epoch), self.output]
        command = self.verifier.owned_script("exec " + " ".join(shlex.quote(value) for value in arguments))
        self.log_file = self.log_path.open("wb")
        self.process = subprocess.Popen([str(self.verifier.guest_ssh), command], stdout=self.log_file, stderr=subprocess.STDOUT)
        self.verifier.commands.append({"scope": "native-notification-guard", "command": command, "log": str(self.log_path), "binary_sha256": actual})
        self.session.artifacts.extend([str(self.log_path), str(self.host_output)])

        def ready() -> str:
            base.require(self.process.poll() is None, "The optional notification guard rejected setup or exited; see its log")
            return self.verifier.read(self.output + ".ready")

        value = self.verifier.wait("optional native notification guard ready", ready, timeout=15)
        base.require(value.isdigit(), "The guard did not publish its actual helper PID")
        process = self.verifier.process(int(value))
        base.require(process is not None, "The guard PID is no longer live")
        command_line = self.verifier.guest(f"cat /proc/{value}/cmdline").replace(b"\0", b" ").decode(errors="replace")
        base.require(self.destination in command_line and self.output in command_line, "The guard PID does not identify this test deployment")
        self.pid, self.birth, self.parent = int(value), process["birth"], process["ppid"]
        self.observation.update(helper_pid=self.pid, helper_birth=self.birth, helper_parent=self.parent, ready=True)

    def collect(self) -> dict[str, Any]:
        if self.process is not None:
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.observation["completion_timeout"] = True
        value = self.verifier.read(self.output)
        if value:
            self.host_output.write_text(value + "\n")
            result = json.loads(value)
            base.require(result["pid"] == self.session.pid and result["birth"] == self.session.birth and result["parent"] == self.session.runner_pid and result["nonce"] == self.session.nonce and result["epoch"] == self.epoch, "The native guard JSON belongs to another target or epoch")
            self.observation["result"] = result
        else:
            self.observation["result_missing"] = True
        self.observation["returncode"] = self.process.poll() if self.process is not None else None
        return self.observation

    def close(self) -> None:
        try:
            if self.process is not None and self.process.poll() is None:
                if not self.pid:
                    value = self.verifier.read(self.output + ".ready")
                    if value.isdigit():
                        process = self.verifier.process(int(value))
                        if process is not None:
                            command = self.verifier.guest(f"cat /proc/{value}/cmdline").replace(b"\0", b" ").decode(errors="replace")
                            base.require(self.destination in command and self.output in command, "A partial guard PID belongs to another command")
                            self.pid, self.birth, self.parent = int(value), process["birth"], process["ppid"]
                if self.pid:
                    self.verifier.signal_owned(self.pid, self.birth, "TERM", contains=self.destination)
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    if self.pid:
                        self.verifier.signal_owned(self.pid, self.birth, "KILL", contains=self.destination)
                    self.process.kill()
                    self.process.wait(timeout=3)
        finally:
            if self.log_file is not None:
                self.log_file.close()


class HardwarePointer:
    """Use short QMP transactions without retaining the shared monitor socket."""

    def __init__(self, verifier: "GestureVerifier", session, reference) -> None:
        self.verifier = verifier
        self.session = session
        with Image.open(reference.image) as image:
            self.width, self.height = image.size
        base.require(self.height > self.width, "Gesture coordinates require a portrait physical scanout")
        base.require(self.width % 2 == 0 and self.height % 2 == 0, "Gesture coordinates require the native scale-2 viewport")
        self.logical_width, self.logical_height = self.width / 2, self.height / 2
        # These portrait Paperwhite metrics match the frozen ReaderMetrics in
        # ui/reader.slint. Physical coordinates still use the observed scanout.
        self.track_inset = 20.0
        self.track_width = self.logical_width - self.track_inset * 2
        self.bottom_height = 188.0
        self.range_top = 39.0
        self.range_height = 36.0
        self.seek_y = self.logical_height - self.bottom_height + self.range_top + self.range_height / 2
        base.require(self.track_width > 0 and 0 <= self.seek_y < self.logical_height, "Reader seek geometry is outside the actual viewport")
        self.reader_geometry = {"source": "ui/reader.slint::ReaderMetrics (portrait Paperwhite)", "physical_size": [self.width, self.height], "logical_size": [self.logical_width, self.logical_height], "scale_factor": 2, "track_inset": self.track_inset, "track_width": self.track_width, "bottom_height": self.bottom_height, "range_top": self.range_top, "range_height": self.range_height, "seek_y": self.seek_y, "page_mapping": "clamp(round(directional_fraction * total), 1, total)"}
        self.held = False
        self.records: list[dict[str, Any]] = []
        self.path = session.output / "hardware-input.json"
        session.artifacts.append(str(self.path))

    def client(self):
        base.require(self.verifier.guard_owned, "An owned suite is required for QMP pointer input")
        self.verifier.identity()
        if str(self.verifier.engine) not in sys.path:
            sys.path.insert(0, str(self.verifier.engine))
        from eink_emulator.qmp import QMPClient
        socket = self.verifier.engine / "machines" / self.verifier.arguments.machine / f"{self.verifier.arguments.machine}.qmp.sock"
        return QMPClient(socket, 5)

    def position(self, x: float, y: float) -> list[dict[str, Any]]:
        physical_x, physical_y = round(x * 2), round(y * 2)
        base.require(0 <= physical_x < self.width and 0 <= physical_y < self.height, "Gesture point is outside the actual physical scanout")
        return [
            {"type": "abs", "data": {"axis": "x", "value": (physical_x * 32767 + self.width - 2) // (self.width - 1)}},
            {"type": "abs", "data": {"axis": "y", "value": (physical_y * 32767 + self.height - 2) // (self.height - 1)}},
        ]

    def execute(self, client, events: list[dict[str, Any]]) -> dict[str, Any]:
        self.verifier.identity()
        record = {"scope": "qmp-input", "events": events, "host_sent_monotonic": time.monotonic()}
        self.records.append(record)
        self.verifier.commands.append(record)
        record["response"] = client.execute("input-send-event", {"events": events})
        record["host_completed_monotonic"] = time.monotonic()
        return record

    def tap(self, x: float, y: float, hold: float = 0.01) -> dict[str, Any]:
        with self.client() as client:
            self.held = True
            down = self.execute(client, [*self.position(x, y), {"type": "btn", "data": {"button": "left", "down": True}}])
            time.sleep(hold)
            up = self.execute(client, [{"type": "btn", "data": {"button": "left", "down": False}}])
            self.held = False
        lower = up["host_sent_monotonic"] - down["host_completed_monotonic"]
        upper = up["host_completed_monotonic"] - down["host_sent_monotonic"]
        timing = {"scope": "qmp-tap-timing", "requested_hold_seconds": hold, "observed_hold_lower_seconds": lower, "observed_hold_upper_seconds": upper, "timing_scope": "QMP submission/completion brackets including per-event SSH ownership checks; not direct evdev or Slint timestamps."}
        self.records.append(timing)
        base.require(upper < 0.5, f"fixture-ordering: the short tap may have crossed the 500 ms long-press boundary ({upper:.3f}s)")
        return {"logical": [x, y], "physical": [round(x * 2), round(y * 2)], **timing, "down": down, "up": up}

    def hold_seek(self, start: tuple[float, float], end: tuple[float, float]) -> dict[str, Any]:
        with self.client() as client:
            self.held = True
            down = self.execute(client, [*self.position(*start), {"type": "btn", "data": {"button": "left", "down": True}}])
            time.sleep(0.04)
            move = self.execute(client, self.position(*end))
        # The global QEMU button remains down after the monitor connection closes.
        # Other connections can now capture scanout while the seek stays held.
        return {"logical_start": start, "logical_end": end, "physical_start": [round(value * 2) for value in start], "physical_end": [round(value * 2) for value in end], "down": down, "move": move, "released_before_sleep": False}

    def release(self) -> dict[str, Any] | None:
        if not self.held:
            return None
        with self.client() as client:
            record = self.execute(client, [{"type": "btn", "data": {"button": "left", "down": False}}])
        self.held = False
        return record

    def save(self) -> None:
        self.path.write_text(json.dumps({"events": self.records, "reader_geometry": self.reader_geometry, "global_button_still_held": self.held, "scope": "Actual QMP absolute/left-button input to the emulated touch controller; not scripted Slint intents."}, indent=2) + "\n")

    def page_position(self, page: int, total: int, direction: str) -> float:
        base.require(1 <= page <= total, "The seek target must belong to the displayed chapter")
        fraction = page / total
        return self.track_inset + self.track_width * (1 - fraction if direction == "rtl" else fraction)

    def preview_roi(self) -> tuple[int, int, int, int]:
        top = self.logical_height - self.bottom_height
        return (round(self.track_inset * 2), round(top * 2), round((self.logical_width - self.track_inset) * 2), round((top + self.range_top + self.range_height) * 2))


class GestureVerifier(base.SleepVerifier):
    def __init__(self, arguments) -> None:
        super().__init__(arguments)
        self.resource_errors: list[dict[str, Any]] = []
        self.unsafe_input = False
        guard = getattr(arguments, "quick_notification_guard", None)
        self.quick_guard_path = Path(guard).resolve(strict=True) if guard else None
        self.quick_guard_bytes = self.quick_guard_path.read_bytes() if self.quick_guard_path else None
        self.quick_guard_hash = base.digest(self.quick_guard_bytes) if self.quick_guard_bytes is not None else None
        if self.quick_guard_bytes is not None:
            header = self.quick_guard_bytes[:20]
            base.require(len(header) == 20 and header[:6] == b"\x7fELF\x01\x01" and int.from_bytes(header[18:20], "little") == 40, "The optional notification guard must be a 32-bit little-endian ARM ELF")
            self.hashes["quick_notification_guard"] = self.quick_guard_hash
            (self.output / "inputs" / f"quick-input-guard-{self.quick_guard_hash[:12]}.elf").write_bytes(self.quick_guard_bytes)
        probe = getattr(arguments, "touch_state_probe", None)
        self.touch_probe_path = Path(probe).resolve(strict=True) if probe else None
        self.touch_probe_bytes = self.touch_probe_path.read_bytes() if self.touch_probe_path else None
        if self.touch_probe_bytes is not None:
            header = self.touch_probe_bytes[:20]
            base.require(len(header) == 20 and header[:6] == b"\x7fELF\x01\x01" and int.from_bytes(header[18:20], "little") == 40, "The touch-state probe must be a native ARM ELF")
            self.hashes["touch_state_probe"] = base.digest(self.touch_probe_bytes)
            (self.output / "inputs" / "touch-state-probe.elf").write_bytes(self.touch_probe_bytes)

    @staticmethod
    def settings(double_tap_zoom: bool) -> list[dict[str, Any]]:
        return [
            base.action(500, "settings", "set", "landscape=false"),
            base.action(500, "settings", "set", "reading_direction=rtl"),
            base.action(500, "settings", "set", f"double_tap_zoom={'true' if double_tap_zoom else 'false'}"),
        ]

    def warmup(self, session):
        parked = self.park(session, "warmup")
        awake = self.wake(session, "warmup-wake", baseline=parked.parked, reference_image=parked.before.image)
        self.compare_application(parked.parked.state["application"], awake.state["application"])
        base.require(awake.state["application"]["landscape"] is False, "The gesture fixture is not portrait")
        return parked, awake

    def preserve_final(self, reference: dict[str, Any], final: dict[str, Any], double_tap_zoom: bool) -> dict[str, Any]:
        fields = ("route", "page", "current_book", "current_chapter", "reader_page", "reader_offset", "reader_zoom")
        for field in fields:
            base.require(field in final and final[field] == reference[field], f"A retained input changed the final Reader field: {field}")
        base.require(final["menu"] is False, "The resumed Reader menu was reopened by old input")
        preferences = final["preferences"]
        base.require(preferences["landscape"] is False and preferences["reading_direction"] == "rtl" and preferences["double_tap_zoom"] is double_tap_zoom, "The configured portrait/RTL/input fixture did not persist")
        return {"stable_fields": list(fields), "menu": final["menu"], "portrait": True, "reading_direction": preferences["reading_direction"], "double_tap_zoom": preferences["double_tap_zoom"]}

    @staticmethod
    def frame_change(first_path: Path, second_path: Path, roi: tuple[int, int, int, int] | None = None) -> dict[str, Any]:
        with Image.open(first_path) as first, Image.open(second_path) as second:
            base.require(first.size == second.size, "Input calibration changed physical scanout geometry")
            first, second = first.convert("L"), second.convert("L")
            if roi is not None:
                first, second = first.crop(roi), second.crop(roi)
            histogram = ImageChops.difference(first, second).histogram()
            return {"roi": roi, "changed_ratio_above_16": sum(histogram[17:]) / (first.width * first.height)}

    def wait_reader_control_frame(self, session, reference: Path, acknowledgement: dict[str, Any], label: str, *, page: int | None, total: int | None, opening: bool = True, preview_roi=None):
        """Observe one input under one fixed deadline; never toggle or re-arm it."""
        ack = acknowledgement.get("up", acknowledgement)
        requested = ack["host_completed_monotonic"]
        deadline = requested + 5.0
        threshold = 0.001 if preview_roi is not None else 0.002
        evidence = {"input_acknowledgement": acknowledgement, "request_monotonic": requested,
                    "deadline_monotonic": deadline, "deadline_seconds": 5.0,
                    "opening": opening, "expected_page": page, "expected_total": total,
                    "required_delta_above_16": threshold, "preview_roi": preview_roi,
                    "reference": str(reference), "polls": [], "selected_frame": None}
        path = session.output / f"{label}-readiness.json"
        session.artifacts.append(str(path))
        try:
            while time.monotonic() < deadline:
                self.menu_observation_identity(deadline)
                poll_request = time.monotonic()
                if poll_request >= deadline:
                    break
                image_path = session.output / f"{label}-poll-{len(evidence['polls']) + 1}.framebuffer.png"
                self.menu_observation_screendump(image_path, deadline)
                session.artifacts.append(str(image_path))
                capture_completed = time.monotonic()
                whole = self.frame_change(reference, image_path)
                delta = self.frame_change(reference, image_path, preview_roi) if preview_roi is not None else whole
                features = reader_menu_features(image_path, page if opening else None, total if opening else None)
                condition = features["ready"] if opening else features["closed_structure"]
                completed = time.monotonic()
                selected = completed <= deadline and delta["changed_ratio_above_16"] > threshold and condition
                evidence["polls"].append({"frame": str(image_path), "request_monotonic": poll_request,
                                          "completion_monotonic": completed, "request_elapsed_seconds": poll_request - requested,
                                          "completion_elapsed_seconds": completed - requested,
                                          "capture_completion_elapsed_seconds": capture_completed - requested,
                                          "whole_delta": whole, "required_delta": delta,
                                          "geometry_features": features, "selected": selected})
                path.write_text(json.dumps(evidence, indent=2) + "\n")
                if selected:
                    evidence["selected_frame"] = str(image_path)
                    with Image.open(image_path) as frame:
                        bottom = frame.height - 188 * 2
                        crop = session.output / f"{label}-selected-page-label.png"
                        frame.crop((max(0, frame.width - 240), bottom + 10, frame.width - 20, bottom + 80)).save(crop)
                        session.artifacts.append(str(crop))
                    return image_path, evidence
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    time.sleep(min(0.1, remaining))
            raise AssertionError(f"fixture-readiness: {label} did not meet the unchanged delta and actual menu/seek geometry within one five-second deadline")
        finally:
            path.write_text(json.dumps(evidence, indent=2) + "\n")

    def menu_observation_identity(self, deadline: float) -> None:
        remaining = deadline - time.monotonic()
        base.require(remaining > 0 and self.guard_owned, "The menu observation deadline or owned suite has ended")
        script = "aidoku_menu_marker=$(cat /var/local/aidoku-emulator-id); read -r aidoku_menu_boot </proc/sys/kernel/random/boot_id; " + f"read -r aidoku_menu_owner <{shlex.quote(self.guard + '/owner')}; " + "printf '%s\\n%s\\n%s\\n' \"$aidoku_menu_marker\" \"$aidoku_menu_boot\" \"$aidoku_menu_owner\""
        values = self.command("menu-readiness-identity", script, timeout=remaining).decode().splitlines()
        base.require(values == [self.arguments.machine, self.boot_id, self.run_id], "The menu observer lost its machine, boot or suite owner")

    def menu_observation_screendump(self, path: Path, deadline: float) -> None:
        remaining = deadline - time.monotonic()
        base.require(remaining > 0, "The menu observation deadline ended before capture")
        record = {"scope": "menu-readiness-qmp", "arguments": ["screendump", str(path)], "machine": self.arguments.machine,
                  "started_monotonic": time.monotonic(), "remaining_timeout_seconds": remaining}
        self.commands.append(record)
        try:
            result = subprocess.run([sys.executable, str(self.qmp_script), "--machine", self.arguments.machine, "screendump", str(path)], cwd=self.engine, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=remaining)
        except subprocess.TimeoutExpired as error:
            record.update(transport_timeout=True, stdout=(error.stdout or b"").decode(errors="replace"), stderr=(error.stderr or b"").decode(errors="replace"))
            raise AssertionError("The menu capture exhausted its remaining observation deadline") from error
        record.update(returncode=result.returncode, stdout=result.stdout.decode(errors="replace"), stderr=result.stderr.decode(errors="replace"))
        base.require(result.returncode == 0, "The menu QMP observation failed")

    def calibrate_tap(self, session, pointer: HardwarePointer, initial):
        # A missed short touch must not masquerade as successful cancellation.
        # First demonstrate that the exact tap advances a stable displayed page
        # without sleep, then obtain a new park/resume diagnostic baseline.
        tap = pointer.tap(pointer.logical_width * 0.12, pointer.logical_height * 0.5, hold=0.01)
        time.sleep(1)
        observed = self.capture(session, "tap-calibration-visible")
        changed = self.frame_change(initial.image, observed.image)
        base.require(changed["changed_ratio_above_16"] > 0.002, "The calibrated short tap did not visibly advance the App; timer cancellation is not assessed")
        parked = self.park(session, "tap-calibration", before_capture=observed)
        expected = initial.state["application"]
        actual = parked.parked.state["application"]
        base.require(actual["current_chapter"] == expected["current_chapter"] and actual["reader_page"] == expected["reader_page"] + 1, "The calibration tap did not produce exactly one next page in the same chapter")
        awake = self.wake(session, "tap-calibration-wake", baseline=parked.parked, reference_image=observed.image)
        self.compare_application(actual, awake.state["application"])
        return {"hardware_tap": tap, "post_tap_observation_seconds": 1, "visible_change": changed, "page_before": expected["reader_page"], "page_after": actual["reader_page"], "sleep": self.park_checks(parked), "scope": "Positive hardware-input control; it establishes tap acceptance, not direct observation of the internal 300 ms timer."}, awake

    def calibrate_seek(self, session, pointer: HardwarePointer, initial):
        # Establish preview hit-testing outside the critical sleep sequence.
        # Release, restore the original page with a real track tap, and warm up
        # again so the later held release would target a different page.
        application = initial.state["application"]
        page = int(application["reader_page"])
        total = int(application["displayed_reader"]["session"]["total"])
        base.require(page == 12 and total == 40, "fixture-ordering: held-seek calibration must start from the frozen 12/40 Reader fixture")
        menu_tap = pointer.tap(pointer.logical_width * 0.5, pointer.logical_height * 0.5)
        menu_image, menu_ready = self.wait_reader_control_frame(session, initial.image, menu_tap, "seek-calibration-menu", page=page, total=total)
        seek_y = pointer.seek_y
        held = pointer.hold_seek((min(500.0, pointer.logical_width - 40), seek_y), (100.0, seek_y))
        roi = pointer.preview_roi()
        preview_page = max(1, min(total, round((1 - (100.0 - pointer.track_inset) / pointer.track_width) * total)))
        preview_image, preview_ready = self.wait_reader_control_frame(session, menu_image, held["move"], "seek-calibration-preview", page=preview_page, total=total, preview_roi=roi)
        changed = self.frame_change(menu_image, preview_image, roi)
        base.require(changed["changed_ratio_above_16"] > 0.001, "The held drag did not change the progress/number preview; seek cancellation is not assessed")
        released = pointer.release()
        closed_image, closed_ready = self.wait_reader_control_frame(session, preview_image, released, "seek-calibration-commit-closed", page=None, total=None, opening=False)
        # Committing a seek closes the menu in the product. Reopen it before
        # tapping the track that restores the original displayed page.
        restore_menu_tap = pointer.tap(pointer.logical_width * 0.5, pointer.logical_height * 0.5)
        restore_menu_image, restore_menu_ready = self.wait_reader_control_frame(session, closed_image, restore_menu_tap, "seek-calibration-restore-menu", page=preview_page, total=total)
        base.require(1 < page < total and total > 1, "The seek fixture requires an interior displayed page")
        direction = application["displayed_reader"]["preferences"]["reading_direction"]
        restore_x = pointer.page_position(page, total, direction)
        restore_tap = pointer.tap(restore_x, seek_y)
        _, restore_closed_ready = self.wait_reader_control_frame(session, restore_menu_image, restore_tap, "seek-calibration-restore-closed", page=None, total=None, opening=False)
        restored = self.capture(session, "seek-calibration-restored")
        parked = self.park(session, "seek-calibration", before_capture=restored)
        actual = parked.parked.state["application"]
        for field in ("current_chapter", "reader_page", "reader_offset"):
            base.require(actual[field] == application[field], f"Seek calibration did not restore its starting {field}")
        awake = self.wake(session, "seek-calibration-wake", baseline=parked.parked, reference_image=initial.image)
        self.compare_application(actual, awake.state["application"])
        return {"menu_tap": menu_tap, "menu_readiness": menu_ready, "held_seek": held, "reader_geometry": pointer.reader_geometry, "preview_change": changed, "preview_readiness": preview_ready, "preview_page": preview_page, "release": released, "commit_closed_readiness": closed_ready, "restore_menu_tap": restore_menu_tap, "restore_menu_readiness": restore_menu_ready, "restore_tap": restore_tap, "restore_closed_readiness": restore_closed_ready, "restore_target": {"page": page, "total": total, "direction": direction, "logical_x": restore_x, "logical_y": seek_y}, "restored_page": page, "sleep": self.park_checks(parked), "scope": "Positive preview hit-test and page restoration, performed before the critical held-seek/sleep sequence."}, awake

    def close_resources(self, session, pointer: HardwarePointer | None, pulse: PreparedPowerPulse | None, primary_error: Exception | None, notification_guard: NativeQuickNotificationGuard | None = None) -> None:
        errors = []
        if notification_guard is not None:
            try:
                notification_guard.close()
            except Exception as error:
                errors.append(f"Close optional native notification guard: {error}")
        if pointer is not None:
            try:
                pointer.release()
            except Exception as error:
                errors.append(f"Release global QMP button: {error}")
                self.unsafe_input = pointer.held
            try:
                pointer.save()
            except Exception as error:
                errors.append(f"Save hardware input evidence: {error}")
        if pulse is not None:
            try:
                pulse.close()
            except Exception as error:
                errors.append(f"Close prepared power channel: {error}")
        if errors:
            record = {"round": self.round_number, "case": session.name, "errors": errors, "primary_error": str(primary_error) if primary_error is not None else None}
            self.resource_errors.append(record)
            path = session.output / "gesture-cleanup-errors.json"
            path.write_text(json.dumps(record, indent=2) + "\n")
            session.artifacts.append(str(path))
            if primary_error is None:
                raise AssertionError("; ".join(errors))

    def tap_then_sleep(self, session) -> dict[str, Any]:
        warmup, initial = self.warmup(session)
        pointer = HardwarePointer(self, session, initial)
        pulse = None
        primary_error = None
        try:
            calibration, baseline = self.calibrate_tap(session, pointer, initial)
            reference = self.capture(session, "tap-reference")
            pulse = PreparedPowerPulse(self, session)
            pulse.open()
            guest_before = pulse.sample_before_gesture()
            tap = pointer.tap(pointer.logical_width * 0.12, pointer.logical_height * 0.5, hold=0.01)
            time.sleep(0.1)
            power = pulse.trigger_sleep()
            gap = power["host_sent_monotonic"] - tap["up"]["host_completed_monotonic"]
            timing = {"tap": tap, "power": power, "guest_before_gesture": guest_before, "tap_release_ack_to_power_command_seconds": gap, "guest_before_gesture_to_power_command_seconds": power["guest_started_uptime"] - float(guest_before["response"].split()[1]), "scope": "QMP acceptance and guest CLI submission brackets; internal Slint timer creation/firing was not directly observed."}
            (session.output / "tap-power-timing.json").write_text(json.dumps(timing, indent=2) + "\n")
            session.artifacts.append(str(session.output / "tap-power-timing.json"))
            base.require(0.075 <= gap < 0.3, f"The input/power fixture missed its submission window ({gap:.3f}s); timer cancellation is not assessed")
            parked = self.park(session, "tap-sleep", before_capture=reference, trigger=False)
            # A natural powerd transition can outlast the 300 ms delay. One next
            # page already displayed at freeze is a legitimate accepted intent.
            before_app = baseline.state["application"]
            frozen_app = parked.parked.state["application"]
            for field in ("current_book", "current_chapter"):
                base.require(frozen_app[field] == before_app[field], f"The natural tap changed {field} before freeze")
            page_before = before_app["reader_page"]
            page_frozen = frozen_app["reader_page"]
            base.require(page_frozen in {page_before, page_before + 1}, "The natural tap displayed more than one next page before freeze")
            advanced_at_freeze = page_frozen == page_before + 1
            stable_sleep = {"same_book_and_chapter": True, "displayed_page_before": page_before, "displayed_page_at_freeze": page_frozen, "next_page_already_displayed_at_freeze": advanced_at_freeze, "reader_jobs_before": before_app["reader_image_jobs_started"], "reader_jobs_at_freeze": frozen_app["reader_image_jobs_started"], "acceptance_scope": "This establishes display admission before completed freeze; the exact ordering of internal acceptance and the raw Going payload is not directly timestamped."}
            awake = self.wake(session, "tap-wake", baseline=parked.parked, reference_image=reference.image, compare_image=not advanced_at_freeze)
            stable_wake = self.compare_application(parked.parked.state["application"], awake.state["application"])
            time.sleep(1.2)
            exited = self.stop(session)
            final = self.preserve_final(frozen_app, exited["capture"]["state"], True)
            return {"warmup": self.park_checks(warmup), "input_calibration": calibration, "timing": timing, "sleep": self.park_checks(parked), "retained_during_sleep": stable_sleep, "natural_freeze_scope": "A visible page is retained. The test does not require the pending timer to survive until freeze; source work may have started before powerd quiescence.", "retained_after_wake": stable_wake, "late_input_observation_seconds": 1.2, "final": final, "exit": exited}
        except Exception as error:
            primary_error = error
            raise
        finally:
            session = session or self.current
            if session is not None:
                self.close_resources(session, pointer, pulse, primary_error)

    def held_seek_then_sleep(self, session) -> dict[str, Any]:
        warmup, initial = self.warmup(session)
        pointer = HardwarePointer(self, session, initial)
        pulse = None
        primary_error = None
        try:
            calibration, baseline = self.calibrate_seek(session, pointer, initial)
            touch_states: dict[str, Any] = {}
            probe_path = session.home + "/app/touch-state"
            if self.touch_probe_bytes is not None:
                self.put(self.touch_probe_bytes, probe_path, "755")
                observed = self.guest("sha256sum " + shlex.quote(probe_path)).decode().split()[0]
                base.require(observed == self.hashes["touch_state_probe"], "The touch-state probe changed in transit")

            def sample_touch(label: str) -> None:
                if self.touch_probe_bytes is None:
                    return
                raw = self.guest(shlex.quote(probe_path))
                touch_states[label] = json.loads(raw)
                destination = session.output / (label + ".touch.json")
                destination.write_bytes(raw)
                session.artifacts.append(str(destination))

            pulse = PreparedPowerPulse(self, session)
            pulse.open()
            menu_tap = pointer.tap(pointer.logical_width * 0.5, pointer.logical_height * 0.5)
            menu_image, menu_ready = self.wait_reader_control_frame(session, baseline.image, menu_tap, "seek-menu-before", page=baseline.state["application"]["reader_page"], total=baseline.state["application"]["displayed_reader"]["session"]["total"])
            before_menu = self.capture(session, "seek-menu-qualified")
            with Image.open(baseline.image) as pure, Image.open(menu_image) as menu:
                base.require(pure.size == menu.size, "Opening the Reader menu changed scanout geometry")
                histogram = ImageChops.difference(pure.convert("L"), menu.convert("L")).histogram()
                menu_change = sum(histogram[17:]) / (pure.width * pure.height)
            base.require(menu_change > 0.002, "The actual framebuffer did not show the requested Reader menu")
            guest_before = pulse.sample_before_gesture()
            # Use the center of the current portrait Paperwhite seek band.
            seek_y = pointer.seek_y
            held = pointer.hold_seek((min(500.0, pointer.logical_width - 40), seek_y), (100.0, seek_y))
            sample_touch("held-before-power")
            power = pulse.trigger_sleep()
            sample_touch("power-return")
            timing = {"menu_tap": menu_tap, "menu_readiness": menu_ready, "menu_framebuffer_change_ratio": menu_change, "held_seek": held, "power": power, "guest_before_gesture": guest_before, "move_ack_to_power_command_seconds": power["host_sent_monotonic"] - held["move"]["host_completed_monotonic"], "scope": "Accepted hardware press/move and power command timestamps. The internal seeking/preview object is not directly observed."}
            path = session.output / "seek-power-timing.json"
            path.write_text(json.dumps(timing, indent=2) + "\n")
            session.artifacts.append(str(path))
            parked = self.park(session, "held-seek-sleep", before_capture=before_menu, trigger=False)
            stable_sleep = self.compare_application(baseline.state["application"], parked.parked.state["application"], stable_jobs=False)
            pre_freeze_jobs = parked.parked.state["application"]["reader_image_jobs_started"] - baseline.state["application"]["reader_image_jobs_started"]
            def fts_state(label: str) -> dict[str, Any] | None:
                return next((device for device in touch_states.get(label, {}).get("devices", []) if device.get("name") == "fts_ts"), None)
            down, lifted = fts_state("held-before-power"), fts_state("power-return")
            kernel_lift = bool(down and lifted and down.get("key_query_available") and lifted.get("key_query_available") and down["btn_touch"] == 1 and any(value >= 0 for value in down["slot_tracking_ids"]) and lifted["btn_touch"] == 0 and lifted["slot_tracking_ids"] and all(value < 0 for value in lifted["slot_tracking_ids"]))
            awake = self.wake(session, "held-seek-wake", baseline=parked.parked, reference_image=baseline.image)
            stable_wake = self.compare_application(parked.parked.state["application"], awake.state["application"])
            sample_touch("awake-before-real-release")
            released = pointer.release()
            sample_touch("after-real-release")
            time.sleep(1.2)
            exited = self.stop(session)
            final = self.preserve_final(parked.parked.state["application"], exited["capture"]["state"], False)
            final_stable = self.compare_application(parked.parked.state["application"], exited["capture"]["state"]["lifecycle"], stable_jobs=True)
            evidence = {"warmup": self.park_checks(warmup), "input_calibration": calibration, "timing": timing, "sleep": self.park_checks(parked), "qmp_button_kept_down_until_wake": True, "kernel_contact_lift_observed": kernel_lift, "pre_freeze_job_delta": pre_freeze_jobs, "touch_states": touch_states, "release_after_wake": released, "retained_during_sleep": stable_sleep, "retained_after_wake": stable_wake, "retained_after_late_release": final_stable, "late_release_observation_seconds": 1.2, "final": final, "exit": exited, "contract": "Freeze preserves the last displayed page/chapter/offset/zoom. Pre-freeze image work is recorded only when read-only kernel state proves a system-forced contact lift; every post-freeze navigation, image-job, PID/font, full-frame, and pixel assertion remains strict."}
            evidence_path = session.output / "held-seek-final-evidence.json"
            evidence_path.write_text(json.dumps(evidence, indent=2) + "\n")
            session.artifacts.append(str(evidence_path))
            base.require(pre_freeze_jobs >= 0 and (pre_freeze_jobs == 0 or kernel_lift), "A pre-freeze reader job appeared without a proven system-forced kernel contact lift")
            return evidence
        except Exception as error:
            primary_error = error
            raise
        finally:
            session = session or self.current
            if session is not None:
                self.close_resources(session, pointer, pulse, primary_error)

    def quick_tap_then_wake(self, session) -> dict[str, Any]:
        warmup, initial = self.warmup(session)
        pointer = HardwarePointer(self, session, initial)
        pulse = None
        notification_guard = None
        primary_error = None
        evidence: dict[str, Any] = {}
        path = session.output / "quick-final-evidence.json"
        session.artifacts.append(str(path))
        try:
            calibration, baseline = self.calibrate_tap(session, pointer, initial)
            reference = self.capture(session, "quick-active-reference")
            before_app = reference.state["application"]
            before_platform = reference.state["platform"]
            pulse = PreparedQuickPulse(self, session)
            pulse.open()
            pulse.bind_target()
            if self.quick_guard_bytes is not None:
                notification_guard = NativeQuickNotificationGuard(self, session, reference.epoch + 1)
                notification_guard.start()
            guest_before = pulse.sample_before_gesture()
            tap = pointer.tap(pointer.logical_width * 0.12, pointer.logical_height * 0.5, hold=0.01)
            time.sleep(0.1)
            submitted = pulse.trigger_quick()
            evidence.update({"warmup": self.park_checks(warmup), "input_calibration": calibration, "reference": {"state": reference.state, "image": str(reference.image)}, "tap": tap, "quick_submission": submitted, "guest_before_gesture": guest_before, "tap_release_ack_to_stop_command_seconds": submitted["host_sent_monotonic"] - tap["up"]["host_completed_monotonic"], "requested_power_gap_seconds": 0.2, "expected_epoch": reference.epoch + 1, "coverage_contract": "An instrumented build must increment quick_cleanup_count exactly once and publish last_resume_kind=quick-cleanup. Legacy negative controls require unchanged native pause_count. Application generation/resume and native pause counters are recorded; a corrected quick path may legitimately quiesce application work."})
            path.write_text(json.dumps(evidence, indent=2) + "\n")
            submitted_capture = self.capture(session, "quick-after-submission", allow_missing=True)
            evidence["after_submission"] = {"state": submitted_capture.state, "phase": submitted_capture.phase, "epoch": submitted_capture.epoch, "process": submitted_capture.process, "image": str(submitted_capture.image)}
            wake_error = None
            try:
                awake = self.wake(session, "quick-tap-wake", baseline=reference, reference_image=reference.image, trigger=False, epoch=reference.epoch + 1, compare_image=False)
            except Exception as error:
                wake_error = str(error)
                awake = self.capture(session, "quick-wake-gate-failure", allow_missing=True)
            evidence["wake_gate_error"] = wake_error
            evidence["resume_observation"] = {"state": awake.state, "phase": awake.phase, "epoch": awake.epoch, "process": awake.process, "image": str(awake.image)}
            time.sleep(1.2)
            late = self.capture(session, "quick-late-observation", allow_missing=True)
            evidence["late_observation"] = {"state": late.state, "phase": late.phase, "epoch": late.epoch, "process": late.process, "image": str(late.image)}
            evidence["late_qmp_comparison"] = self.image_difference(reference.image, late.image, "reader")
            exit_error = None
            try:
                exited = self.stop(session)
            except Exception as error:
                exit_error = str(error)
                exited = None
            evidence["exit_error"] = exit_error
            evidence["exit"] = exited
            # Preserve the final actual App export even if optional helper
            # collection or its ordering validation subsequently fails.
            path.write_text(json.dumps(evidence, indent=2) + "\n")
            safety = pulse.finish_safety()
            evidence["test_safety"] = safety
            guard_observation = notification_guard.collect() if notification_guard is not None else {"enabled": False}
            evidence["notification_delivery_guard"] = guard_observation
            guard_result = guard_observation.get("result", {})
            guard_problem = notification_guard is not None and (
                guard_observation.get("returncode") != 0
                or guard_result.get("late_ordering") is True
                or guard_result.get("safety_cont_sent") is True
                or not all(guard_result.get(key) is True for key in ("saw_real_sleep_request", "saw_real_resume_request", "duplicate_usr2_sent_while_stopped", "resume_acknowledged"))
            )
            after_state = awake.state
            final_state = exited["capture"]["state"] if exited is not None else None
            final_app = final_state.get("lifecycle") if isinstance(final_state, dict) else None
            pause_delta = after_state["platform"]["pause_count"] - before_platform["pause_count"] if after_state is not None else None
            explicit_cleanup = "quick_cleanup_count" in reference.state
            cleanup_before = reference.state.get("quick_cleanup_count")
            cleanup_after = after_state.get("quick_cleanup_count") if after_state else None
            cleanup_delta = cleanup_after - cleanup_before if isinstance(cleanup_after, int) and isinstance(cleanup_before, int) else None
            resume_kind = after_state.get("last_resume_kind") if after_state else None
            branch_qualified = cleanup_delta == 1 and resume_kind == "quick-cleanup" if explicit_cleanup else pause_delta == 0
            evidence["branch_counters"] = {
                "native_pause_count_before": before_platform["pause_count"],
                "native_pause_count_after": after_state["platform"]["pause_count"] if after_state else None,
                "native_pause_count_delta": pause_delta,
                "quick_cleanup_count_before": cleanup_before,
                "quick_cleanup_count_after": cleanup_after,
                "quick_cleanup_count_delta": cleanup_delta,
                "last_resume_kind_after": resume_kind,
                "branch_qualification": "explicit-quick-cleanup" if explicit_cleanup else "legacy-native-active-without-cleanup-counter",
                "application_generation_before": before_app["lifecycle_generation"],
                "application_generation_after": final_app.get("lifecycle_generation") if isinstance(final_app, dict) else None,
                "same_process_resumes_before": reference.state["same_process_resumes"],
                "same_process_resumes_after": after_state.get("same_process_resumes") if after_state else None,
                "reader_jobs_before": before_app["reader_image_jobs_started"],
                "reader_jobs_final": final_app.get("reader_image_jobs_started") if isinstance(final_app, dict) else None,
            }
            evidence["classification"] = "fixture-ordering" if guard_problem or safety["watchdog_triggered"] or not branch_qualified else "quick-merge-covered"
            evidence["observed_final_differences"] = {field: {"before": before_app.get(field), "final": final_app.get(field)} for field in base.STABLE_FIELDS if isinstance(final_app, dict) and before_app.get(field) != final_app.get(field)}
            # Save raw final page/jobs/phase evidence before coverage or product
            # assertions, so a failed gate cannot conceal a late flip.
            path.write_text(json.dumps(evidence, indent=2) + "\n")
            base.require(not guard_problem, f"fixture-ordering: the optional notification guard did not establish an authorized stopped delivery ({guard_result.get('reason', 'result-missing')})")
            base.require(not safety["watchdog_triggered"], "fixture-ordering: the six-second test watchdog continued the ELF; supervisor-only recovery was not established")
            base.require(branch_qualified, f"fixture-ordering: Active quick-merge coverage is unassessed (quick_cleanup_delta={cleanup_delta}, resume_kind={resume_kind}, native_pause_delta={pause_delta})")
            base.require(wake_error is None and exit_error is None, f"Quick wake or final export failed: {wake_error or exit_error}")
            base.require(isinstance(final_app, dict), "The final export lacks actual lifecycle/page/job telemetry")
            final = self.preserve_final(before_app, final_state, True)
            stable = self.compare_application(before_app, final_app, stable_jobs=True)
            base.require(evidence["late_qmp_comparison"]["matches"], "The quick wake produced a different late QMP Reader frame")
            evidence.update(final=final, retained_state=stable, passed=True)
            path.write_text(json.dumps(evidence, indent=2) + "\n")
            return evidence
        except Exception as error:
            primary_error = error
            evidence["error"] = str(error)
            path.write_text(json.dumps(evidence, indent=2) + "\n")
            raise
        finally:
            session = session or self.current
            if session is not None:
                self.close_resources(session, pointer, pulse, primary_error, notification_guard=notification_guard)

    def write_report(self, setup_error: str | None = None) -> None:
        super().write_report(setup_error)
        path = self.output / "report.json"
        report = json.loads(path.read_text())
        report.update({"kind": "native-retained-sleep-gesture-regressions", "test_script_sha256": base.digest(Path(__file__).read_bytes()), "shared_driver_sha256": base.digest(Path(__file__).with_name("verify-sleep-wake.py").read_bytes()), "gesture_resource_errors": self.resource_errors, "global_qmp_input_cleanup_incomplete": self.unsafe_input})
        report["coverage_limits"].append("These cases observe resulting page/menu/frame state after hardware input and powerd transitions; they do not directly expose internal Timer or seek-preview execution.")
        path.write_text(json.dumps(report, indent=2) + "\n")

    def run(self) -> int:
        matrix = [("tap-then-sleep", True, self.tap_then_sleep), ("held-seek-then-sleep", False, self.held_seek_then_sleep), ("quick-tap-then-wake", True, self.quick_tap_then_wake)]
        if self.arguments.cases:
            requested = set(self.arguments.cases.split(","))
            base.require(requested <= {case[0] for case in matrix}, "An unknown gesture case was selected")
            matrix = [case for case in matrix if case[0] in requested]
        try:
            self.acquire_guard()
            first_round = getattr(self.arguments, "start_round", 1)
            for number in range(first_round, first_round + self.arguments.rounds):
                for name, double_tap_zoom, callback in matrix:
                    self.prepare_round(number, variant=f"gesture-{name}")
                    self.run_case(name, "reader", self.settings(double_tap_zoom), callback)
                    base.require(self.current is None or self.current.closed, "The gesture case did not close its owned session")
                    base.require(not self.unsafe_input, "The virtual input button could not be released; no further cases will start")
        except Exception as error:
            self.suite_error = str(error)
            raise
        finally:
            self.finish_suite(release=not self.unsafe_input)
        print(json.dumps({"report": str(self.output / 'report.json'), "passed": sum(case.passed for case in self.results), "failed": sum(not case.passed for case in self.results)}), flush=True)
        return 0 if all(case.passed for case in self.results) else 1


def build_parser():
    parser = base.build_parser()
    parser.description = __doc__
    parser.set_defaults(output_dir=Path("artifacts/sleep-gestures"))
    parser.add_argument("--start-round", type=int, default=1, help="Logical first round when aggregating this scope with other native matrices")
    parser.add_argument("--quick-notification-guard", type=Path, help="Optional host ARM guard ELF for test-only stopped USR2 scheduling after real supervisor tokens")
    parser.add_argument("--touch-state-probe", type=Path, help="Optional read-only ARM evdev ioctl probe to establish a system-forced lift during the held seek case")
    return parser


def main() -> int:
    parser = build_parser()
    arguments = parser.parse_args()
    if os.name != "posix":
        parser.error("Run this script on the designated remote Linux worker")
    if arguments.profile not in {"pw5", "pw6"}:
        parser.error("The base gesture coordinates currently require a portrait Paperwhite profile")
    if not 1 <= arguments.rounds <= 10 or not 1 <= arguments.start_round <= 10 or arguments.start_round + arguments.rounds - 1 > 10 or arguments.timeout < 30:
        parser.error("Use 1..10 rounds and a timeout of at least 30 seconds")
    if not 0 <= arguments.max_difference <= 1 or arguments.max_mean_error < 0:
        parser.error("Pixel difference limits must be nonnegative and the ratio at most one")
    verifier = None
    try:
        verifier = GestureVerifier(arguments)
        return verifier.run()
    except Exception as error:
        print(f"Gesture sleep/wake verification failed: {error}", file=sys.stderr)
        if verifier is not None:
            verifier.write_report(setup_error=str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
