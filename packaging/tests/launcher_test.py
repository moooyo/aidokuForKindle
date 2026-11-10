#!/usr/bin/env python3
"""Exercise the persistent launcher with fake powerd and a signal-aware fake app.

Run only on the designated remote Linux worker. All services, framebuffers,
checkpoints, IPC files, and window-manager processes belong to each fixture.
No Kindle sleep, input event, or stock system service is modified.
The portrait commands below model the launcher's CLI publication contract;
they do not exercise framebuffer calibration or physical sensor acceptance.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


REPOSITORY = Path(__file__).resolve().parents[2]
LAUNCHER = REPOSITORY / "packaging/kindle/launch.sh"
MEMORY_STATE = {"route": "reader", "book": "fixture-book", "chapter": "fixture-chapter", "page": 12, "offset": 29}

FAKE_APP = r'''
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

root = Path(os.environ["AIDOKU_TEST_ROOT"])
pid = os.getpid()
started = time.monotonic()
if "--native-power-state" in sys.argv:
    # This early branch models the native CLI contract without initializing an
    # application generation, checkpoint, font lifetime or launcher App record.
    try:
        timeout_ms = int(sys.argv[sys.argv.index("--native-power-state-timeout-ms") + 1]) if "--native-power-state-timeout-ms" in sys.argv else 700
    except (IndexError, ValueError):
        raise SystemExit(2)
    if not 1 <= timeout_ms <= 700:
        raise SystemExit(2)
    probe_cancelled = False

    def cancel_probe(*_):
        global probe_cancelled
        probe_cancelled = True

    def probe_trace(event, **fields):
        entry = {"event": event, "pid": pid, "parent": os.getppid(), "time": time.monotonic(), **fields}
        descriptor = os.open(root / "power-probe.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(descriptor, (json.dumps(entry) + "\n").encode())
        finally:
            os.close(descriptor)

    signal.signal(signal.SIGTERM, cancel_probe)
    signal.signal(signal.SIGINT, cancel_probe)
    query = subprocess.Popen(["lipc-get-prop", "-s", "com.lab126.powerd", "state"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    probe_trace("started", child=query.pid, timeout_ms=timeout_ms)
    deadline = started + timeout_ms / 1000
    while query.poll() is None and not probe_cancelled and time.monotonic() < deadline:
        time.sleep(0.01)
    if probe_cancelled:
        result, reason = 143, "cancelled"
    elif query.poll() is None:
        result, reason = 124, "timeout"
    else:
        result, reason = query.returncode, "completed"
    if query.poll() is None:
        query.kill()
    output = query.communicate()[0].decode()
    override = root / "power-probe-exit-code"
    if result == 0 and override.exists():
        result = int(override.read_text())
        if result:
            reason = "forced-error"
    probe_trace("completed", child=query.pid, child_reaped=query.poll() is not None, result=result, reason=reason, state=output.strip(), elapsed=time.monotonic() - started)
    if result == 0 or os.environ.get("AIDOKU_FAKE_PROBE_STDOUT_ON_ERROR") == "1":
        sys.stdout.write(output)
        sys.stdout.flush()
    raise SystemExit(result)

if "--capture-native-portrait" in sys.argv or "--authorize-native-portrait-resume" in sys.argv:
    # Native portrait readers are short-lived helpers, not App generations.
    # The real native record schema and device I/O have separate Rust/VM tests.
    import argparse

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--kindle", action="store_true", required=True)
    parser.add_argument("--device", choices=("pw5", "pw6", "scribe", "scribe2", "scribe3", "scribe3-nofrontlight"), required=True)
    commands = parser.add_mutually_exclusive_group(required=True)
    commands.add_argument("--capture-native-portrait", action="store_true")
    commands.add_argument("--authorize-native-portrait-resume")
    arguments = parser.parse_args(sys.argv[1:])
    epoch = arguments.authorize_native_portrait_resume
    if epoch is not None:
        if not epoch.isascii() or not epoch.isdigit() or not 1 <= int(epoch) <= 2**64 - 1:
            raise SystemExit(2)
        epoch = str(int(epoch))
    directory = Path(os.environ.get("AIDOKU_LAUNCHER_DIR", ""))
    nonce = os.environ.get("AIDOKU_LAUNCH_NONCE", "")
    generation = os.environ.get("AIDOKU_LAUNCH_GENERATION", "")
    try:
        valid = (
            directory == root / "launcher.lock"
            and len(nonce) == 32
            and all(value in "0123456789abcdefABCDEF" for value in nonce)
            and generation.isascii() and generation.isdigit() and int(generation) > 0
            and (directory / "nonce").read_text() == nonce + "\n"
            and (directory / "generation").read_text() == generation + ":" + nonce + "\n"
        )
        if epoch is not None:
            application = (directory / "application-pid").read_text().strip()
            valid = (
                valid
                and application.isascii() and application.isdigit() and int(application) > 1
                and (directory / "epoch").read_text() == epoch + "\n"
                and (directory / "sleep-request").read_text() == application + " " + nonce + " " + epoch + "\n"
            )
    except (OSError, ValueError):
        valid = False
    if not valid:
        raise SystemExit(1)
    sys.stdout.write("PUBLISHED\n")
    sys.stdout.flush()
    raise SystemExit(0)

directory = Path(os.environ["AIDOKU_LAUNCHER_DIR"])
nonce = os.environ["AIDOKU_LAUNCH_NONCE"]
state = {"route": "reader", "book": "fixture-book", "chapter": "fixture-chapter", "page": 12, "offset": 29}
if "--resume" in sys.argv and (root / "checkpoint.json").exists():
    state = json.loads((root / "checkpoint.json").read_text())
sleep_flag = False
resume_flag = False
term_flag = False
term_at = None
ack_at = None
ack_record = None
resumed_epoch = None
exit_prepared_at = None
resume_at = None
resume_record = None
parked_record = None
drive_hold_until = None
delayed_epochs = set()

def trace(event, **fields):
    entry = {"event": event, "pid": pid, "time": time.monotonic(), **fields}
    descriptor = os.open(root / "app-trace.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(descriptor, (json.dumps(entry) + "\n").encode())
    finally:
        os.close(descriptor)

def write_record(name, value):
    path = directory / name
    pending = path.with_suffix(".pending")
    pending.write_text(value + "\n")
    pending.replace(path)

def current_record():
    try:
        value = (directory / "sleep-request").read_text().strip()
        fields = value.split()
        if len(fields) == 3 and fields[0] == str(pid) and fields[1] == nonce:
            return value
    except FileNotFoundError:
        pass
    return f"{pid} {nonce} 0"

def ordinary_exit():
    value = current_record()
    try:
        fallback = (directory / "fallback-sleep").read_text().strip() == value
    except FileNotFoundError:
        fallback = False
    if not fallback:
        write_record("ordinary-exit", value)
    return fallback

def on_sleep(*_):
    global sleep_flag
    sleep_flag = True

def on_resume(*_):
    global resume_flag
    resume_flag = True

def on_term(*_):
    global term_flag
    term_flag = True

signal.signal(signal.SIGUSR1, on_sleep)
signal.signal(signal.SIGUSR2, on_resume)
signal.signal(signal.SIGTERM, on_term)
signal.signal(signal.SIGINT, on_term)
(root / "app-pid").write_text(str(pid))
trace("started", resume="--resume" in sys.argv, state=state)

while True:
    now = time.monotonic()
    if sleep_flag and current_record().endswith(" 2") and 2 not in delayed_epochs:
        delay = float(os.environ.get("AIDOKU_FAKE_SECOND_DRIVE_DELAY", "0"))
        if delay:
            delayed_epochs.add(2)
            drive_hold_until = now + delay
            trace("drive-delayed", record=current_record())
    if drive_hold_until is not None and now < drive_hold_until and not term_flag:
        time.sleep(0.02)
        continue
    if sleep_flag:
        sleep_flag = False
        resume_at = None
        value = current_record()
        trace("sleep-requested", record=value, state=state)
        (root / "checkpoint.json").write_text(json.dumps(state))
        mode = os.environ.get("AIDOKU_FAKE_SLEEP_EXIT", "")
        if mode == "ordinary":
            ordinary_exit()
            trace("ordinary-exit")
            raise SystemExit(0)
        if mode == "crash":
            trace("crashed")
            os._exit(73)
        if exit_prepared_at is None and os.environ.get("AIDOKU_FAKE_NO_ACK") != "1":
            delay_key = "AIDOKU_FAKE_SECOND_ACK_DELAY" if value.endswith(" 2") else "AIDOKU_FAKE_ACK_DELAY"
            ack_at = now + float(os.environ.get(delay_key, os.environ.get("AIDOKU_FAKE_ACK_DELAY", "0")))
            ack_record = value
    if resume_flag:
        resume_flag = False
        value = current_record()
        try:
            bound_resume = (directory / "resume-request").read_text().strip() == value
        except FileNotFoundError:
            bound_resume = False
        if not bound_resume:
            trace("stale-resume-ignored", record=value)
            if parked_record == value:
                os.kill(pid, signal.SIGSTOP)
        elif os.environ.get("AIDOKU_FAKE_IGNORE_RESUME") == "1":
            trace("resume-ignored", record=value)
        else:
            parked_record = None
            if os.environ.get("AIDOKU_FAKE_LATE_ACK") != "1":
                ack_at = None
            if resumed_epoch == value:
                write_record("resume-ready", value)
            elif resume_at is None:
                resume_at = now + float(os.environ.get("AIDOKU_FAKE_RESUME_DELAY", "0"))
                resume_record = value
                trace("resume-requested", record=value)
    if resume_at is not None and now >= resume_at:
        resume_at = None
        resumed_epoch = resume_record
        write_record("resume-ready", resume_record)
        trace("resumed", record=resume_record, state=state)
    if ack_at is not None and now >= ack_at:
        ack_at = None
        write_record("sleep-ready", ack_record)
        if resumed_epoch == ack_record:
            # A deliberately obsolete ACK must not make the supervisor STOP us.
            trace("late-ack", record=ack_record)
        else:
            trace("parked", record=ack_record)
            parked_record = ack_record
            os.kill(pid, signal.SIGSTOP)
    if term_flag and term_at is None:
        term_at = now + float(os.environ.get("AIDOKU_FAKE_TERM_DELAY", "0"))
        trace("term-requested")
    if term_at is not None and now >= term_at:
        fallback = ordinary_exit()
        (root / "graceful-exit").write_text("done")
        trace("terminated", fallback=fallback)
        raise SystemExit(0)
    exit_after = os.environ.get("AIDOKU_FAKE_EXIT_AFTER")
    if exit_after is not None and now - started >= float(exit_after):
        if exit_prepared_at is None:
            ordinary_exit()
            exit_prepared_at = now + float(os.environ.get("AIDOKU_FAKE_EXIT_PREPARE_DELAY", "0"))
            trace("exit-prepared")
        if now >= exit_prepared_at:
            trace("ordinary-exit")
            raise SystemExit(int(os.environ.get("AIDOKU_FAKE_EXIT_CODE", "0")))
    time.sleep(0.02)
'''

FAKE_POWER_EVENTS = r'''
import os
from pathlib import Path
import signal

root = Path(os.environ["AIDOKU_TEST_ROOT"])
signal.signal(signal.SIGINT, lambda *_: exit(0))
signal.signal(signal.SIGTERM, lambda *_: exit(0))
(root / "event-pid").write_text(str(os.getpid()))
with open(root / "event-input", "r") as stream:
    for line in stream:
        if line.strip() == "observer-exit":
            raise SystemExit(7)
        print(line.rstrip("\n"), flush=True)
'''


def wait_for(predicate, seconds: float = 10, description: str = "fixture condition") -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.025)
    raise AssertionError(f"Timed out waiting for {description}")


class LauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="aidoku-launcher-test-")
        self.root = Path(self.temporary.name)
        self.home = self.root / "aidoku"
        self.bin = self.root / "bin"
        self.lock = self.root / "launcher.lock"
        self.bin.mkdir()
        (self.home / "app").mkdir(parents=True)
        (self.home / "data").mkdir()
        (self.home / "data/device-profile").write_text("pw6\n")
        self.operations = self.root / "operations"
        self.operations.write_text("")
        self.statusbar = self.root / "statusbar"
        self.statusbar.write_text("running")
        (self.root / "power-state").write_text("active")
        os.mkfifo(self.root / "event-input", 0o600)
        self.event_descriptor = os.open(self.root / "event-input", os.O_RDWR | os.O_NONBLOCK)
        self.wm = subprocess.Popen(["sleep", "120"], start_new_session=True)
        self.launcher: subprocess.Popen | None = None
        self.log = None
        self.environment = os.environ.copy()
        self.environment.update({
            "PATH": str(self.bin) + ":" + self.environment["PATH"],
            "AIDOKU_HOME": str(self.home),
            "AIDOKU_LOCK_DIR": str(self.lock),
            "AIDOKU_FRAMEBUFFER": "/dev/null",
            "AIDOKU_TEST_ROOT": str(self.root),
            "AIDOKU_FAKE_WM": str(self.wm.pid),
        })
        self.shell(self.bin / "status", 'if [ "$(cat "$AIDOKU_TEST_ROOT/statusbar")" = running ]; then printf "%s\\n" "statusbar start/running, process 123"; else printf "%s\\n" "statusbar stop/waiting"; fi')
        self.shell(self.bin / "start", 'printf "%s\\n" "start:$1" >>"$AIDOKU_TEST_ROOT/operations"; printf running >"$AIDOKU_TEST_ROOT/statusbar"')
        self.shell(self.bin / "stop", 'printf "%s\\n" "stop:$1" >>"$AIDOKU_TEST_ROOT/operations"; printf stopped >"$AIDOKU_TEST_ROOT/statusbar"')
        self.shell(self.bin / "pidof", '[ "$1" = awesome ] && printf "%s\\n" "$AIDOKU_FAKE_WM"')
        self.shell(self.bin / "xrefresh", 'printf "%s\\n" "xrefresh:$*" >>"$AIDOKU_TEST_ROOT/operations"; if [ "${AIDOKU_FAKE_REDRAW_HANG:-0}" = 1 ]; then exec sleep 120; fi')
        self.shell(self.bin / "lipc-get-prop", 'if [ "${AIDOKU_FAKE_LIPC_HANG:-0}" = 1 ] && [ ! -f "$AIDOKU_TEST_ROOT/release-power-query" ]; then exec sleep 120; fi; cat "$AIDOKU_TEST_ROOT/power-state"')
        # GNU hosts may lack the BusyBox usleep command available on Kindle.
        # This replacement belongs only to the fixture's private PATH.
        self.shell(self.bin / "usleep", "exec perl -e '@ARGV == 1 && $ARGV[0] =~ /\\A[0-9]+\\z/ or exit 2; select undef, undef, undef, $ARGV[0] / 1000000;' \"$@\"")
        self.python(self.bin / "lipc-wait-event", FAKE_POWER_EVENTS)
        self.python(self.home / "app/aidoku", FAKE_APP)

    @staticmethod
    def shell(path: Path, source: str) -> None:
        path.write_text("#!/bin/sh\nset -eu\n" + source + "\n", encoding="utf-8")
        path.chmod(0o755)

    @staticmethod
    def python(path: Path, source: str) -> None:
        path.write_text("#!/usr/bin/env python3\n" + source.lstrip(), encoding="utf-8")
        path.chmod(0o755)

    def traces(self, event: str | None = None, *, file_name: str = "app-trace.jsonl") -> list[dict]:
        path = self.root / file_name
        if not path.exists():
            return []
        values = []
        for line in path.read_text().splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event is None or value["event"] == event:
                values.append(value)
        return values

    def power_queries(self, event: str | None = None) -> list[dict]:
        return self.traces(event, file_name="power-probe.jsonl")

    def phase(self) -> str:
        try:
            return (self.lock / "phase").read_text().strip()
        except FileNotFoundError:
            return "closed"

    @staticmethod
    def process_state(pid: int) -> str:
        try:
            text = Path(f"/proc/{pid}/status").read_text()
        except FileNotFoundError:
            return "gone"
        return next(line.split(":", 1)[1].strip() for line in text.splitlines() if line.startswith("State:"))

    def spawn_launcher(self) -> None:
        self.log = (self.root / "launcher.log").open("wb")
        self.launcher = subprocess.Popen(["sh", str(LAUNCHER), "--foreground"], env=self.environment, stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True)

    def start_launcher(self) -> int:
        self.spawn_launcher()
        wait_for(lambda: bool(self.traces("started")) and self.phase() == "active", description="initialized app")
        return self.traces("started")[0]["pid"]

    def event(self, event: str, *, state: str | None = None) -> None:
        if state is not None:
            (self.root / "power-state").write_text(state)
        os.write(self.event_descriptor, (event + "\n").encode())

    def complete(self, timeout: float = 20) -> int:
        assert self.launcher is not None
        result = self.launcher.wait(timeout=timeout)
        if self.log is not None:
            self.log.close()
        processes = subprocess.check_output(["ps", "-eo", "pid=,pgid=,stat="], text=True)
        leftovers = [line.strip() for line in processes.splitlines() if len(line.split()) >= 3 and line.split()[1] == str(self.launcher.pid) and not line.split()[2].startswith("Z")]
        self.assertEqual(leftovers, [], "The launcher left fixture processes running")
        return result

    def stop_launcher(self) -> None:
        self.launcher.terminate()
        self.assertEqual(self.complete(), 143)

    def assert_recovered(self, *, redraw: bool = True) -> None:
        self.assertFalse(self.lock.exists(), (self.root / "launcher.log").read_text())
        self.assertFalse(self.process_state(self.wm.pid).startswith(("T", "t")))
        self.assertEqual(self.statusbar.read_text(), "running")
        operations = self.operations.read_text()
        self.assertIn("stop:statusbar", operations)
        self.assertIn("start:statusbar", operations)
        if redraw:
            self.assertIn("xrefresh:-display :0 -none", operations)
            self.assertGreater(operations.rindex("xrefresh:-display :0 -none"), operations.index("start:statusbar"))

    def park(self, pid: int) -> None:
        self.event("goingToScreenSaver", state="screenSaver")
        wait_for(lambda: self.phase() == "parked" and self.process_state(pid).startswith("T"), description="same-process parking")
        wait_for(lambda: self.statusbar.read_text() == "running", description="stock display restoration")

    def wake(self) -> None:
        self.event("outOfScreenSaver", state="active")
        self.event("exitingScreenSaver")
        wait_for(lambda: bool(self.traces("resumed")) and self.phase() == "active", description="same-process resume")

    def test_exit_code_and_stock_repaint_are_preserved(self) -> None:
        self.environment.update(AIDOKU_FAKE_EXIT_AFTER="0.2", AIDOKU_FAKE_EXIT_CODE="7")
        self.start_launcher()
        self.assertEqual(self.complete(), 7)
        self.assert_recovered()

    def test_preexisting_stopped_resources_are_not_started(self) -> None:
        self.statusbar.write_text("stopped")
        os.kill(self.wm.pid, signal.SIGSTOP)
        wait_for(lambda: self.process_state(self.wm.pid).startswith("T"))
        self.environment["AIDOKU_FAKE_EXIT_AFTER"] = "0.2"
        self.start_launcher()
        self.assertEqual(self.complete(), 0)
        self.assertTrue(self.process_state(self.wm.pid).startswith("T"))
        self.assertEqual(self.statusbar.read_text(), "stopped")
        self.assertNotIn("start:statusbar", self.operations.read_text())
        self.assertFalse(self.lock.exists())

    def test_signal_preserves_the_five_second_grace_period(self) -> None:
        self.environment["AIDOKU_FAKE_TERM_DELAY"] = "3"
        self.start_launcher()
        self.stop_launcher()
        self.assertTrue((self.root / "graceful-exit").is_file())
        self.assert_recovered()

    def test_sleep_wake_preserves_pid_and_exact_in_memory_page(self) -> None:
        pid = self.start_launcher()
        self.park(pid)
        self.assertEqual(len(self.traces("started")), 1)
        self.wake()
        resumed = self.traces("resumed")[-1]
        self.assertEqual(resumed["pid"], pid)
        self.assertEqual(resumed["state"], MEMORY_STATE)
        self.assertEqual(len(self.traces("started")), 1)
        self.stop_launcher()
        self.assert_recovered()

    def test_fast_wake_cancels_ack_and_ignores_a_late_ack(self) -> None:
        # Leave time for the bounded cancel and active-state helpers, ensuring
        # this case exercises an obsolete ACK rather than an already parked app.
        self.environment.update(AIDOKU_FAKE_ACK_DELAY="4", AIDOKU_FAKE_LATE_ACK="1")
        pid = self.start_launcher()
        self.event("goingToScreenSaver", state="screenSaver")
        wait_for(lambda: bool(self.traces("sleep-requested")))
        self.wake()
        wait_for(lambda: bool(self.traces("late-ack")), seconds=6)
        self.assertFalse(self.process_state(pid).startswith("T"))
        self.assertEqual(len(self.traces("started")), 1)
        self.assertEqual(self.traces("resumed")[-1]["pid"], pid)
        self.stop_launcher()
        self.assert_recovered()

    def test_wake_requires_native_active_state(self) -> None:
        pid = self.start_launcher()
        self.park(pid)
        self.event("outOfScreenSaver")
        time.sleep(1.3)
        self.assertEqual(self.traces("resumed"), [])
        self.assertTrue(self.process_state(pid).startswith("T"))
        self.wake()
        self.assertEqual(self.traces("resumed")[-1]["pid"], pid)
        self.stop_launcher()

    def test_active_power_query_has_no_unconditional_backoff(self) -> None:
        # Record the sleep caller rather than imposing a host wall-time limit
        # on unrelated supervisor cleanup or fake application scheduling.
        self.shell(self.bin / "sleep", 'printf "%s %s\\n" "$PPID" "$1" >>"$AIDOKU_TEST_ROOT/sleep-trace"; exec /bin/sleep "$@"')
        pid = self.start_launcher()
        self.park(pid)
        self.wake()
        queries = self.power_queries("started")
        self.assertEqual(len(queries), 1)
        self.assertTrue(1 <= queries[0]["timeout_ms"] <= 700)
        completed = self.power_queries("completed")
        self.assertEqual([(row["result"], row["state"]) for row in completed], [(0, "active")])
        sleep_parents = [int(line.split()[0]) for line in (self.root / "sleep-trace").read_text().splitlines()]
        self.assertNotIn(queries[0]["parent"], sleep_parents, "An immediately active native query slept before sending wake-active")
        self.assertEqual(self.traces("resumed")[-1]["pid"], pid)
        self.assertEqual(len(self.traces("started")), 1, "A power query was counted as an App generation")
        self.stop_launcher()
        self.assert_recovered()

    def test_failed_power_query_cannot_resume_from_active_stdout(self) -> None:
        # Inject a deliberately malformed failed CLI result: even an active
        # payload must not override its nonzero exit status.
        (self.root / "power-probe-exit-code").write_text("7")
        self.environment["AIDOKU_FAKE_PROBE_STDOUT_ON_ERROR"] = "1"
        pid = self.start_launcher()
        self.park(pid)
        self.event("outOfScreenSaver", state="active")
        wait_for(lambda: bool(self.power_queries("completed")), description="failed native query")
        self.assertEqual(self.power_queries("completed")[0]["result"], 7)
        self.assertEqual(self.traces("resumed"), [])
        self.assertTrue(self.process_state(pid).startswith("T"))
        (self.root / "power-probe-exit-code").write_text("0")
        self.wake()
        self.assertEqual(self.traces("resumed")[-1]["pid"], pid)
        self.assertEqual(len(self.traces("started")), 1)
        self.stop_launcher()

    def test_native_power_query_timeout_retries_without_restarting_app(self) -> None:
        self.environment["AIDOKU_FAKE_LIPC_HANG"] = "1"
        pid = self.start_launcher()
        self.park(pid)
        self.event("outOfScreenSaver", state="active")
        wait_for(lambda: bool(self.power_queries("completed")), seconds=4, description="bounded native query timeout")
        query = self.power_queries("completed")[0]
        self.assertEqual(query["reason"], "timeout")
        self.assertTrue(query["child_reaped"])
        self.assertEqual(self.process_state(query["child"]), "gone")
        self.assertEqual(self.traces("resumed"), [])
        self.assertTrue(self.process_state(pid).startswith("T"))
        (self.root / "release-power-query").write_text("released")
        self.wake()
        self.assertEqual(self.traces("resumed")[-1]["pid"], pid)
        self.assertEqual(len(self.traces("started")), 1)
        self.stop_launcher()

    def test_inactive_power_queries_keep_the_overall_wake_budget(self) -> None:
        pid = self.start_launcher()
        self.park(pid)
        self.event("outOfScreenSaver")
        wait_for(lambda: "Waiting for native screen-saver dismissal or unlock." in (self.root / "launcher.log").read_text(), seconds=14, description="overall native wake deadline")
        queries = self.power_queries("started")
        completed = self.power_queries("completed")
        self.assertGreater(len(queries), 1)
        self.assertLessEqual(len(queries), 10)
        self.assertTrue(all(1 <= row["timeout_ms"] <= 700 for row in queries))
        self.assertLess(completed[-1]["time"] - queries[0]["time"], 10.5)
        self.assertEqual(self.traces("resumed"), [])
        self.assertTrue(self.process_state(pid).startswith("T"))
        self.wake()
        self.assertEqual(self.traces("resumed")[-1]["pid"], pid)
        self.assertEqual(len(self.traces("started")), 1)
        self.stop_launcher()

    def test_new_sleep_during_resume_starts_a_new_epoch(self) -> None:
        self.environment["AIDOKU_FAKE_RESUME_DELAY"] = "2"
        pid = self.start_launcher()
        self.park(pid)
        self.event("outOfScreenSaver", state="active")
        wait_for(lambda: self.phase() == "resuming" and bool(self.traces("resume-requested")))
        self.event("goingToScreenSaver", state="screenSaver")
        wait_for(lambda: self.phase() == "parked" and (self.lock / "epoch").read_text().strip() == "2")
        self.assertTrue(self.process_state(pid).startswith("T"))
        self.assertEqual(self.traces("resumed"), [])
        self.wake()
        self.assertEqual(self.traces("resumed")[-1]["pid"], pid)
        self.assertTrue(self.traces("resumed")[-1]["record"].endswith(" 2"))
        self.assertEqual(len(self.traces("started")), 1)
        self.stop_launcher()

    def test_stale_first_resume_cannot_cancel_second_sleep(self) -> None:
        self.environment["AIDOKU_FAKE_RESUME_DELAY"] = "2"
        pid = self.start_launcher()
        self.park(pid)
        self.event("outOfScreenSaver", state="active")
        wait_for(lambda: self.phase() == "resuming" and bool(self.traces("resume-requested")))
        old_resume = (self.lock / "resume-request").read_text()
        self.event("goingToScreenSaver", state="screenSaver")
        wait_for(lambda: self.phase() == "parked" and (self.lock / "epoch").read_text().strip() == "2")
        self.assertFalse((self.lock / "resume-request").exists())
        # Replay an old bound request plus a late CONT/USR2 from the first wake.
        (self.lock / "resume-request").write_text(old_resume)
        os.kill(pid, signal.SIGCONT)
        os.kill(pid, signal.SIGUSR2)
        wait_for(lambda: bool(self.traces("stale-resume-ignored")) and self.process_state(pid).startswith("T"))
        self.assertEqual(self.traces("resumed"), [])
        self.wake()
        self.assertTrue(self.traces("resumed")[-1]["record"].endswith(" 2"))
        self.assertEqual(len(self.traces("started")), 1)
        self.stop_launcher()

    def test_true_quick_wake_of_second_epoch_uses_current_request(self) -> None:
        self.environment.update(AIDOKU_FAKE_SECOND_DRIVE_DELAY="2", AIDOKU_FAKE_SECOND_ACK_DELAY="2", AIDOKU_FAKE_LATE_ACK="1")
        pid = self.start_launcher()
        self.park(pid)
        self.wake()
        self.event("goingToScreenSaver", state="screenSaver")
        wait_for(lambda: bool(self.traces("drive-delayed")))
        self.event("outOfScreenSaver", state="active")
        self.event("exitingScreenSaver")
        wait_for(lambda: len(self.traces("resumed")) == 2 and self.phase() == "active")
        current = (self.lock / "resume-request").read_text().strip()
        self.assertEqual(current, self.traces("resumed")[-1]["record"])
        self.assertTrue(current.endswith(" 2"))
        self.assertEqual(self.traces("resumed")[-1]["pid"], pid)
        wait_for(lambda: any(row.get("record", "").endswith(" 2") for row in self.traces("late-ack")), seconds=6)
        self.assertFalse(self.process_state(pid).startswith("T"))
        self.assertEqual(len(self.traces("started")), 1)
        self.stop_launcher()

    def test_ack_timeout_restarts_only_after_valid_wake(self) -> None:
        self.environment["AIDOKU_FAKE_NO_ACK"] = "1"
        original = self.start_launcher()
        self.event("goingToScreenSaver", state="screenSaver")
        wait_for(lambda: self.phase() == "fallback", seconds=14, description="bounded ACK fallback")
        self.assertEqual(len(self.traces("started")), 1)
        self.assertTrue(self.traces("terminated")[-1]["fallback"])
        self.event("outOfScreenSaver", state="active")
        self.event("exitingScreenSaver")
        wait_for(lambda: len(self.traces("started")) == 2 and self.phase() == "active", description="checkpoint restart")
        restarted = self.traces("started")[-1]
        self.assertNotEqual(restarted["pid"], original)
        self.assertTrue(restarted["resume"])
        self.assertEqual(restarted["state"], MEMORY_STATE)
        self.stop_launcher()
        self.assert_recovered()

    def test_unexpected_sleep_exit_waits_for_wake_before_fallback(self) -> None:
        self.environment["AIDOKU_FAKE_SLEEP_EXIT"] = "crash"
        self.start_launcher()
        self.event("goingToScreenSaver", state="screenSaver")
        wait_for(lambda: self.phase() == "fallback")
        self.assertEqual(len(self.traces("started")), 1)
        self.event("outOfScreenSaver", state="active")
        wait_for(lambda: len(self.traces("started")) == 2)
        self.assertTrue(self.traces("started")[-1]["resume"])
        self.stop_launcher()

    def test_explicit_exit_during_pause_wins_over_pending_wake(self) -> None:
        self.environment["AIDOKU_FAKE_SLEEP_EXIT"] = "ordinary"
        self.start_launcher()
        self.event("goingToScreenSaver", state="screenSaver")
        wait_for(lambda: bool(self.traces("ordinary-exit")))
        self.event("outOfScreenSaver", state="active")
        self.assertEqual(self.complete(), 0)
        self.assertEqual(len(self.traces("started")), 1)
        self.assert_recovered()

    def test_explicit_exit_prepared_before_sleep_epoch_is_not_restarted(self) -> None:
        self.environment.update(AIDOKU_FAKE_EXIT_AFTER="0.2", AIDOKU_FAKE_EXIT_PREPARE_DELAY="1")
        self.start_launcher()
        wait_for(lambda: bool(self.traces("exit-prepared")))
        self.event("goingToScreenSaver", state="screenSaver")
        wait_for(lambda: self.phase() == "pausing")
        self.event("outOfScreenSaver", state="active")
        self.assertEqual(self.complete(), 0)
        self.assertEqual(len(self.traces("started")), 1)
        self.assert_recovered()

    def test_unacknowledged_resume_uses_valid_wake_fallback(self) -> None:
        self.environment["AIDOKU_FAKE_IGNORE_RESUME"] = "1"
        original = self.start_launcher()
        self.park(original)
        self.event("outOfScreenSaver", state="active")
        self.event("exitingScreenSaver")
        wait_for(lambda: len(self.traces("started")) == 2 and self.phase() == "active", seconds=18, description="failed-resume fallback")
        self.assertNotEqual(self.traces("started")[-1]["pid"], original)
        self.assertTrue(self.traces("started")[-1]["resume"])
        self.assertTrue(self.traces("terminated")[-1]["fallback"])
        self.stop_launcher()
        self.assert_recovered()

    def test_explicit_exit_intent_wins_when_the_sleep_ack_times_out(self) -> None:
        self.environment.update(AIDOKU_FAKE_EXIT_AFTER="0.2", AIDOKU_FAKE_EXIT_PREPARE_DELAY="8")
        self.start_launcher()
        wait_for(lambda: bool(self.traces("exit-prepared")))
        self.event("goingToScreenSaver", state="screenSaver")
        self.assertEqual(self.complete(), 0)
        self.assertEqual(len(self.traces("started")), 1)
        self.assertFalse(any(row.get("fallback") for row in self.traces("terminated")))
        self.assert_recovered()

    def test_term_while_parked_continues_child_before_exit(self) -> None:
        pid = self.start_launcher()
        self.park(pid)
        self.stop_launcher()
        self.assertTrue((self.root / "graceful-exit").exists())
        self.assertEqual(len(self.traces("started")), 1)
        self.assert_recovered()

    def test_usb_while_active_never_restarts_or_repaints_usb(self) -> None:
        self.start_launcher()
        self.event("usbConfigured")
        self.assertEqual(self.complete(), 143)
        self.assertEqual(len(self.traces("started")), 1)
        self.assertNotIn("xrefresh:", self.operations.read_text())
        self.assert_recovered(redraw=False)

    def test_usb_while_parked_continues_then_terminates_without_restart(self) -> None:
        pid = self.start_launcher()
        sleep_redraw_count = self.operations.read_text().count("xrefresh:")
        self.park(pid)
        # Parking and the restored statusbar precede submission of the sleep
        # repaint. Establish that repaint before measuring subsequent USB work.
        wait_for(lambda: self.operations.read_text().count("xrefresh:") > sleep_redraw_count,
                 description="sleep repaint submission before USB takeover")
        redraw_count = self.operations.read_text().count("xrefresh:")
        self.event("usbConfigured")
        self.assertEqual(self.complete(), 143)
        self.assertEqual(self.operations.read_text().count("xrefresh:"), redraw_count)
        self.assertEqual(len(self.traces("started")), 1)
        self.assertTrue((self.root / "graceful-exit").exists())
        self.assert_recovered(redraw=False)

    def test_stale_epoch_pid_and_nonce_events_do_not_resume(self) -> None:
        pid = self.start_launcher()
        self.park(pid)
        nonce = (self.lock / "nonce").read_text().strip()
        self.event(f"wake-active 0 1 {pid} {nonce}")
        self.event(f"wake-active 1 1 {self.wm.pid} {nonce}")
        self.event(f"wake-active 1 1 {pid} invalid-nonce")
        time.sleep(0.2)
        self.assertTrue(self.process_state(pid).startswith("T"))
        self.assertEqual(self.traces("resumed"), [])
        self.stop_launcher()

    def test_hung_power_query_does_not_block_usb_cleanup(self) -> None:
        self.environment["AIDOKU_FAKE_LIPC_HANG"] = "1"
        pid = self.start_launcher()
        self.park(pid)
        self.event("outOfScreenSaver", state="active")
        wait_for(lambda: bool(self.power_queries("started")), description="native probe with a blocked LIPC child")
        query = self.power_queries("started")[0]
        self.event("usbConfigured")
        self.assertEqual(self.complete(), 143)
        completed = [row for row in self.power_queries("completed") if row["pid"] == query["pid"]]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["reason"], "cancelled")
        self.assertTrue(completed[0]["child_reaped"])
        self.assertEqual(self.process_state(query["child"]), "gone")
        self.assertEqual(len(self.traces("started")), 1)
        self.assert_recovered(redraw=False)

    def test_hung_stock_redraw_is_bounded_and_can_be_cancelled(self) -> None:
        self.environment["AIDOKU_FAKE_REDRAW_HANG"] = "1"
        pid = self.start_launcher()
        self.event("goingToScreenSaver", state="screenSaver")
        wait_for(lambda: self.phase() == "parked" and "xrefresh:" in self.operations.read_text())
        self.stop_launcher()
        self.assertTrue((self.root / "graceful-exit").exists())
        self.assert_recovered()

    def test_integer_only_sleep_supports_guest_cleanup(self) -> None:
        self.shell(self.bin / "sleep", 'case "$1" in *.*) printf "%s\\n" "Fractional sleep is unsupported." >&2; exit 2 ;; esac; exec /bin/sleep "$@"')
        self.environment.update(AIDOKU_FAKE_EXIT_AFTER="0.2", AIDOKU_FAKE_EXIT_CODE="7")
        self.start_launcher()
        self.assertEqual(self.complete(), 7)
        self.assert_recovered()

    def test_existing_lock_is_not_stolen(self) -> None:
        self.lock.mkdir()
        marker = self.lock / "owner"
        marker.write_text("another-fixture-owner")
        self.spawn_launcher()
        self.assertEqual(self.complete(), 1)
        self.assertEqual(marker.read_text(), "another-fixture-owner")
        self.assertEqual(self.traces(), [])

    def test_native_portrait_cli_rejects_malformed_or_missing_epochs(self) -> None:
        executable = str(self.home / "app/aidoku")
        base = [executable, "--kindle", "--device", "pw6"]
        for arguments in (
            ["--authorize-native-portrait-resume"],
            ["--authorize-native-portrait-resume", "0"],
            ["--authorize-native-portrait-resume", "-1"],
            ["--authorize-native-portrait-resume", "not-an-epoch"],
            ["--authorize-native-portrait-resume", str(2**64)],
            ["--capture-native-portrait", "--authorize-native-portrait-resume", "1"],
        ):
            with self.subTest(arguments=arguments):
                result = subprocess.run(base + arguments, env=self.environment, capture_output=True, timeout=3)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b"")
        self.assertEqual(self.traces(), [])

    def test_native_portrait_cli_publishes_only_for_the_current_fixture_epoch(self) -> None:
        nonce = "a" * 32
        self.lock.mkdir()
        for name, value in {
            "nonce": nonce,
            "generation": "1:" + nonce,
            "application-pid": "12345",
            "epoch": "2",
            "sleep-request": "12345 " + nonce + " 2",
        }.items():
            (self.lock / name).write_text(value + "\n")
        environment = dict(self.environment, AIDOKU_LAUNCHER_DIR=str(self.lock), AIDOKU_LAUNCH_NONCE=nonce, AIDOKU_LAUNCH_GENERATION="1")
        base = [str(self.home / "app/aidoku"), "--kindle", "--device", "pw6"]
        for arguments in (["--capture-native-portrait"], ["--authorize-native-portrait-resume", "2"]):
            result = subprocess.run(base + arguments, env=environment, capture_output=True, timeout=3)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, b"PUBLISHED\n")
        stale = subprocess.run(base + ["--authorize-native-portrait-resume", "1"], env=environment, capture_output=True, timeout=3)
        self.assertEqual(stale.returncode, 1)
        self.assertEqual(stale.stdout, b"")
        self.assertEqual(self.traces(), [])

    def tearDown(self) -> None:
        if self.launcher is not None:
            if self.launcher.poll() is None:
                self.launcher.terminate()
                try:
                    self.launcher.wait(timeout=16)
                except subprocess.TimeoutExpired:
                    os.killpg(self.launcher.pid, signal.SIGKILL)
                    self.launcher.wait(timeout=3)
            # Only this fixture's new process group can be cleaned up here.
            try:
                os.killpg(self.launcher.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if self.log is not None and not self.log.closed:
            self.log.close()
        os.close(self.event_descriptor)
        os.kill(self.wm.pid, signal.SIGCONT)
        self.wm.terminate()
        self.wm.wait(timeout=3)
        self.temporary.cleanup()


if __name__ == "__main__":
    if os.name != "posix":
        raise SystemExit("Run launcher tests on the designated remote Linux worker through ssh test-env")
    unittest.main(verbosity=2)
