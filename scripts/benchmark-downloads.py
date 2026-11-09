#!/usr/bin/env python3
"""Run repeatable download measurements against a local, authenticated HTTP fixture.

Run this script on the designated remote verification host, never on a developer
workstation without explicit permission. The Rust benchmark executable must be
built from the same source snapshot as this script.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import random
import re
import signal
import subprocess
import sys
import threading
import time
import traceback
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image, __version__ as PILLOW_VERSION


TOKEN = "fixture-token"
SCHEMA_VERSION = 1
MEASUREMENT_LAUNCHER = r"""#define _GNU_SOURCE
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/resource.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc < 4) return 125;
    pid_t child = fork();
    if (child < 0) return 125;
    if (child == 0) {
        execv(argv[3], &argv[3]);
        perror("execv benchmark");
        _exit(127);
    }
    FILE *pid_file = fopen(argv[2], "w");
    if (pid_file != NULL) { fprintf(pid_file, "%ld\n", (long)child); fclose(pid_file); }
    int status;
    struct rusage usage;
    pid_t waited;
    do { waited = wait4(child, &status, 0, &usage); } while (waited < 0 && errno == EINTR);
    if (waited < 0) return 125;
    int code = WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
    FILE *result = fopen(argv[1], "w");
    if (result == NULL) return 125;
    fprintf(result, "{\"pid\":%ld,\"exit_code\":%d,\"peak_rss_kib\":%ld,"
        "\"user_cpu_seconds\":%.6f,\"system_cpu_seconds\":%.6f}\n",
        (long)child, code, usage.ru_maxrss,
        usage.ru_utime.tv_sec + usage.ru_utime.tv_usec / 1000000.0,
        usage.ru_stime.tv_sec + usage.ru_stime.tv_usec / 1000000.0);
    fclose(result);
    return code;
}
"""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def make_fixture(seed: int, chapter: int, page: int) -> dict:
    width = 640 + (page % 8) * 32 + chapter * 7
    height = 960 + (page % 6) * 48 + chapter * 11
    pixels = random.Random(seed + chapter * 1009 + page * 9176).randbytes(width * height)
    image = Image.frombytes("L", (width, height), pixels)
    encoded = io.BytesIO()
    image_format = "PNG" if page % 2 else "JPEG"
    if image_format == "PNG":
        image.save(encoded, format=image_format, compress_level=3)
    else:
        image.save(encoded, format=image_format, quality=85, optimize=False)
    data = encoded.getvalue()
    return {
        "bytes": data,
        "width": width,
        "height": height,
        "media_type": "image/png" if image_format == "PNG" else "image/jpeg",
        "sha256": sha256(data),
        "size_bytes": len(data),
    }


class RequestStatistics:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.idle = threading.Condition(self.lock)
        self.started = time.perf_counter()
        self.phase = "setup"
        self.requests: list[dict] = []
        self.active_images = 0
        self.active_images_by_phase: Counter = Counter()
        self.active_requests = 0
        self.peaks: Counter = Counter()
        self.connection_ids: dict[tuple, int] = {}

    def begin(self, path: str, kind: str, address: tuple, variant: str | None, phase: str | None = None) -> dict:
        with self.lock:
            phase = phase or self.phase
            self.active_requests += 1
            connection = self.connection_ids.setdefault(address, len(self.connection_ids) + 1)
            if kind == "image":
                self.active_images += 1
                self.active_images_by_phase[phase] += 1
                self.peaks[phase] = max(self.peaks[phase], self.active_images_by_phase[phase])
            request = {
                "sequence": len(self.requests),
                "phase": phase,
                "kind": kind,
                "path": path,
                "connection_id": connection,
                "started_ms": round((time.perf_counter() - self.started) * 1000, 3),
                "active_images_at_start": self.active_images_by_phase[phase],
                "global_active_images_at_start": self.active_images,
                "overlapping_image_phases": [name for name, count in self.active_images_by_phase.items() if count and name != phase],
                "completed_ms": None,
                "status": None,
                "bytes_sent": 0,
                "fixture_variant": variant,
            }
            self.requests.append(request)
            return request

    def finish(self, request: dict, status: int, sent: int, error: str | None) -> None:
        with self.lock:
            request.update(
                completed_ms=round((time.perf_counter() - self.started) * 1000, 3),
                status=status,
                bytes_sent=sent,
            )
            if error:
                request["error"] = error
            if request["kind"] == "image":
                self.active_images -= 1
                self.active_images_by_phase[request["phase"]] -= 1
            self.active_requests -= 1
            self.idle.notify_all()

    def wait_idle(self, timeout: float = 5.0) -> None:
        with self.idle:
            if not self.idle.wait_for(lambda: self.active_requests == 0, timeout=timeout):
                raise TimeoutError("The fixture still has active HTTP responses at a measurement boundary")

    def set_phase(self, phase: str) -> None:
        self.wait_idle()
        with self.lock:
            if self.active_images:
                raise AssertionError("A measurement phase still has active HTTP responses")
            self.phase = phase

    def snapshot(self, phase: str, wait_for_idle: bool = True) -> dict:
        if wait_for_idle:
            self.wait_idle()
        with self.lock:
            requests = [dict(request) for request in self.requests if request["phase"] == phase]
            images = [request for request in requests if request["kind"] == "image"]
            return {
                "http_requests": len(requests),
                "image_requests": len(images),
                "metadata_requests": sum(request["kind"] == "metadata" for request in requests),
                "image_bytes_sent": sum(request["bytes_sent"] for request in images),
                "all_bytes_sent": sum(request["bytes_sent"] for request in requests),
                "peak_image_concurrency": self.peaks[phase],
                "connections": len({request["connection_id"] for request in requests}),
                "image_requests_by_path": dict(sorted(Counter(request["path"] for request in images).items())),
                "requests": requests,
            }


class FixtureServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, fixtures: dict[str, dict], arguments: argparse.Namespace, bind_address: str = "127.0.0.1") -> None:
        super().__init__((bind_address, 0), FixtureHandler)
        self.fixtures = fixtures
        self.arguments = arguments
        self.statistics = RequestStatistics()
        self.priority_lock = threading.Lock()
        self.priority_started = 0
        self.priority_ready = threading.Event()
        self.priority_release = threading.Event()


class FixtureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        pass

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        variant = self.headers.get("X-Fixture-Variant")
        fixture_key = f"{path}#{variant}" if path == "/images/headers" else path
        fixture = self.server.fixtures.get(fixture_key)
        metadata = re.fullmatch(r"/api/v1/books/(c\d+)/pages", path)
        control = path == "/__control/background-ready"
        kind = "image" if fixture else "metadata" if metadata else "control" if control else "unknown"
        request = self.server.statistics.begin(path, kind, self.client_address, variant, getattr(self, "fixture_phase", None))
        request["original_path"] = getattr(self, "fixture_original_path", self.path)
        status, sent, error = 200, 0, None
        try:
            if self.headers.get("X-API-Key") != TOKEN:
                status, content_type, body = 401, "application/json", b'{"error":"fixture authorization required"}'
            elif control:
                ready = self.server.priority_ready.wait(timeout=5)
                status, content_type, body = (200 if ready else 503), "application/json", json.dumps({"background_ready": ready}).encode()
            elif fixture:
                status, content_type, body = 200, fixture["media_type"], fixture["bytes"]
                if request["phase"] == "foreground-priority":
                    if path.startswith("/images/background-"):
                        with self.server.priority_lock:
                            self.server.priority_started += 1
                            if self.server.priority_started >= 2:
                                self.server.priority_ready.set()
                        if not self.server.priority_release.wait(timeout=10):
                            status, content_type, body = 503, "application/json", b'{"error":"foreground did not receive reserved capacity"}'
                    elif path == "/images/front":
                        self.server.priority_release.set()
                time.sleep(self.server.arguments.latency_ms / 1000)
            elif metadata:
                chapter = metadata.group(1)
                pages = []
                for page in range(1, self.server.arguments.pages + 1):
                    item = self.server.fixtures.get(f"/api/v1/books/{chapter}/pages/{page}")
                    if item:
                        pages.append({"number": page, "mediaType": item["media_type"]})
                status, content_type, body = 200, "application/json", json.dumps(pages).encode()
            else:
                status, content_type, body = 404, "application/json", b'{"error":"unknown fixture"}'
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            for offset in range(0, len(body), self.server.arguments.chunk_bytes):
                block = body[offset : offset + self.server.arguments.chunk_bytes]
                self.wfile.write(block)
                self.wfile.flush()
                sent += len(block)
                if fixture and self.server.arguments.chunk_delay_ms and offset + len(block) < len(body):
                    time.sleep(self.server.arguments.chunk_delay_ms / 1000)
        except (BrokenPipeError, ConnectionResetError) as exception:
            error = type(exception).__name__
        finally:
            self.server.statistics.finish(request, status, sent, error)


def source_manifest(root: Path) -> dict:
    paths = [root / name for name in ("Cargo.toml", "Cargo.lock", "build.rs", ".cargo/config.toml")]
    for directory, suffixes in [("src", {".rs"}), ("ui", {".slint"}), ("examples", {".rs"}), ("vendor", {".rs", ".toml", ".slint", ".json"})]:
        paths.extend(path for path in (root / directory).rglob("*") if path.suffix in suffixes)
    paths.extend(path for path in (root / "assets").rglob("*") if path.is_file())
    paths.append(Path(__file__).resolve())
    digests = {str(path.relative_to(root)).replace(os.sep, "/"): sha256(path.read_bytes()) for path in sorted(set(paths)) if path.is_file()}
    combined = "\n".join(f"{name} {digest}" for name, digest in sorted(digests.items())).encode()
    return {"sha256": sha256(combined), "files": digests}


def environment_metadata() -> dict:
    model = next((line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines() if line.startswith("model name")), None)
    memory = next((line.split(":", 1)[1].strip() for line in Path("/proc/meminfo").read_text().splitlines() if line.startswith("MemTotal:")), None)
    return {
        "platform": platform.uname()._asdict(),
        "python": sys.version,
        "pillow": PILLOW_VERSION,
        "cpu_model": model,
        "logical_cpus": os.cpu_count(),
        "memory_total": memory,
        "cpu_affinity": sorted(os.sched_getaffinity(0)),
    }


def read_process_memory(pid: int, executable: Path) -> dict:
    try:
        if Path(f"/proc/{pid}/exe").resolve() != executable:
            return {}
        lines = Path(f"/proc/{pid}/status").read_text().splitlines()
    except (FileNotFoundError, ProcessLookupError):
        return {}
    return {name: int(value.split()[0]) for line in lines if ":" in line for name, value in [line.split(":", 1)] if name in {"VmRSS", "VmHWM"}}


def make_measurement_launcher(output: Path) -> tuple[Path, dict]:
    directory = output / "measurement-launcher"
    directory.mkdir()
    source, binary = directory / "measure.c", directory / "measure"
    source.write_text(MEASUREMENT_LAUNCHER, encoding="utf-8")
    compile_command = ["cc", "-O2", "-std=c11", "-Wall", "-Wextra", str(source), "-o", str(binary)]
    built = subprocess.run(compile_command, capture_output=True, text=True, check=True)
    (directory / "compile.log").write_text(built.stdout + built.stderr, encoding="utf-8")
    return binary, {"source_sha256": sha256(source.read_bytes()), "binary_sha256": sha256(binary.read_bytes()), "command": compile_command}


def run_process(command: list[str], output: Path, timeout: float, launcher: Path) -> dict:
    stdout_path, stderr_path = output / "stdout.json", output / "stderr.log"
    output.mkdir(parents=True, exist_ok=False)
    usage_path, pid_path = output / "child-rusage.json", output / "child.pid"
    started, samples, peak_sample = time.perf_counter(), 0, {"VmRSS": 0, "VmHWM": 0}
    process = None
    try:
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = subprocess.Popen([str(launcher), str(usage_path), str(pid_path), *command], stdout=stdout, stderr=stderr, start_new_session=True)
            child_pid = None
            while True:
                if child_pid is None and pid_path.is_file():
                    child_text = pid_path.read_text().strip()
                    if child_text:
                        child_pid = int(child_text)
                memory = read_process_memory(child_pid, Path(command[0])) if child_pid else {}
                if memory:
                    samples += 1
                for key, value in memory.items():
                    peak_sample[key] = max(peak_sample[key], value)
                pid, status, usage = os.wait4(process.pid, os.WNOHANG)
                if pid:
                    process.returncode = os.waitstatus_to_exitcode(status)
                    break
                if time.perf_counter() - started > timeout:
                    raise TimeoutError(f"Benchmark process exceeded {timeout} seconds: {command}")
                time.sleep(0.01)
    except BaseException:
        if process is not None and process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                _, status, _ = os.wait4(process.pid, 0)
                process.returncode = os.waitstatus_to_exitcode(status)
            except ChildProcessError:
                pass
        write_json(output / "interrupted.json", {"command": command, "wall_seconds": time.perf_counter() - started, "proc_peak_sample_kib": peak_sample if samples else None, "failure": traceback.format_exc()})
        raise
    child_usage = json.loads(usage_path.read_text()) if usage_path.is_file() else {}
    measurement = {
        "command": command,
        "exit_code": process.returncode,
        "wall_seconds": time.perf_counter() - started,
        "user_cpu_seconds": child_usage.get("user_cpu_seconds"),
        "system_cpu_seconds": child_usage.get("system_cpu_seconds"),
        "peak_rss_kib": child_usage.get("peak_rss_kib"),
        "proc_peak_sample_kib": peak_sample if samples else None,
        "proc_samples": samples,
        "rss_method": "Linux wait4 of the Rust child forked by a small native launcher; post-exec /proc/PID/status sampled every 10 ms",
        "launcher_wait4_rss_kib": usage.ru_maxrss,
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
    }
    write_json(output / "process.json", measurement)
    if process.returncode:
        raise RuntimeError(f"Benchmark exited {process.returncode}: {stderr_path.read_text()[-8000:]}")
    if child_usage.get("exit_code") != 0 or not child_usage.get("peak_rss_kib"):
        raise RuntimeError("The native measurement launcher did not record successful child resource usage")
    measurement["result"] = json.loads(stdout_path.read_text())
    return measurement


def assert_result_files(result: dict, fixtures: dict[str, dict], base_url: str) -> None:
    for file in result.get("files", []):
        path = urlsplit(file["url"]).path
        fixture_key = f"{path}#{file['headers'].get('X-Fixture-Variant')}" if path == "/images/headers" else path
        expected = fixtures[fixture_key]
        for field in ["sha256", "width", "height", "size_bytes"]:
            if file[field] != expected[field]:
                raise AssertionError(f"Invalid downloaded file {file['url']}: {field}")
        if not file["url"].startswith(base_url):
            raise AssertionError("A benchmark file came from outside the fixture server")
    if not all(result.get("checks", {}).values()):
        raise AssertionError(f"Rust benchmark correctness assertion failed: {result.get('checks')}")


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def serve_native(arguments: argparse.Namespace) -> None:
    output = arguments.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    fixtures = {f"/api/v1/books/c{chapter:02}/pages/{page}": make_fixture(arguments.seed, chapter, page) for chapter in range(1, arguments.chapters + 1) for page in range(1, arguments.pages + 1)}
    manifest = {path: {key: value for key, value in item.items() if key != "bytes"} for path, item in fixtures.items()}
    write_json(output / "fixtures.json", manifest)
    config = vars(arguments).copy()
    config.update(binary=str(arguments.binary.resolve()), output=str(output))
    write_json(output / "config.json", config)
    report = {"schema_version": SCHEMA_VERSION, "purpose": "Native guest HTTP fixture only; guest process measurements are recorded separately", "build_profile": arguments.build_profile, "binary_sha256": sha256(arguments.binary.read_bytes()), "source": source_manifest(Path(__file__).resolve().parent.parent), "host_environment": environment_metadata(), "config": config}

    class NativeHandler(FixtureHandler):
        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path in {"/__control/status", "/__control/stop"}:
                authorized = self.headers.get("X-API-Key") == TOKEN
                try:
                    status = 200 if authorized else 401
                    try:
                        payload = {"authorized": True, "phases": {phase: server.statistics.snapshot(phase) for phase in ["native-serial", "native-concurrent"]}} if authorized else {"error": "fixture authorization required"}
                    except Exception as exception:
                        status, payload = 503, {"error": str(exception), "phases": {phase: server.statistics.snapshot(phase, wait_for_idle=False) for phase in ["native-serial", "native-concurrent"]}}
                    body = json.dumps(payload).encode()
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    self.wfile.flush()
                finally:
                    if authorized and path == "/__control/stop":
                        threading.Thread(target=server.shutdown, daemon=True).start()
                return
            prefix = re.match(r"^/(serial|concurrent)(/.*)$", self.path)
            if prefix:
                self.fixture_phase = f"native-{prefix.group(1)}"
                self.fixture_original_path = self.path
                self.path = prefix.group(2)
            super().do_GET()

    server = FixtureServer(fixtures, arguments, bind_address="0.0.0.0")
    server.RequestHandlerClass = NativeHandler
    port = server.server_address[1]
    ready = {"listen_address": "0.0.0.0", "host_base_url": f"http://127.0.0.1:{port}", "guest_host_address": arguments.guest_host_address, "guest_serial_base_url": f"http://{arguments.guest_host_address}:{port}/serial", "guest_concurrent_base_url": f"http://{arguments.guest_host_address}:{port}/concurrent", "binary_sha256": report["binary_sha256"], "output": str(output), "expires_after_seconds": arguments.serve_seconds}
    write_json(output / "fixture-ready.json", ready)
    print(json.dumps(ready), flush=True)
    timer = threading.Timer(arguments.serve_seconds, server.shutdown)
    timer.daemon = True
    timer.start()
    try:
        server.serve_forever()
    finally:
        timer.cancel()
        server.server_close()
        idle_failure = None
        try:
            server.statistics.wait_idle()
        except Exception as exception:
            idle_failure = str(exception)
            report["failure"] = idle_failure
        phases = {phase: server.statistics.snapshot(phase, wait_for_idle=False) for phase in ["native-serial", "native-concurrent"]}
        expected_pages = arguments.chapters * arguments.pages
        expected_bytes = sum(item["size_bytes"] for item in fixtures.values())
        report["http"] = phases
        report["checks"] = {phase: idle_failure is None and measurement["image_requests"] == expected_pages and measurement["image_bytes_sent"] == expected_bytes and measurement["metadata_requests"] == arguments.chapters and measurement["peak_image_concurrency"] == concurrency and all(count == 1 for count in measurement["image_requests_by_path"].values()) and all(request["status"] == 200 and not request.get("error") and not request.get("overlapping_image_phases") for request in measurement["requests"]) for phase, concurrency in [("native-serial", 1), ("native-concurrent", 2)] for measurement in [phases[phase]]}
        report["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        write_json(output / "results.json", report)
        write_json(output / "requests.json", server.statistics.requests)


def benchmark(arguments: argparse.Namespace) -> dict:
    output = arguments.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    fixtures: dict[str, dict] = {}
    for chapter in range(1, arguments.chapters + 1):
        for page in range(1, arguments.pages + 1):
            fixtures[f"/api/v1/books/c{chapter:02}/pages/{page}"] = make_fixture(arguments.seed, chapter, page)
    for page in range(1, 25):
        fixtures[f"/images/background-{page}"] = make_fixture(arguments.seed, 20, page)
    for name in ["front", "singleflight", "low-space", "lru-old", "lru-new", "lru-pin", "headers#first", "headers#second"]:
        fixtures[f"/images/{name}"] = make_fixture(arguments.seed, 21, len(fixtures) + 1)
    fixture_manifest = {path: {key: value for key, value in item.items() if key != "bytes"} for path, item in fixtures.items()}
    write_json(output / "fixtures.json", fixture_manifest)
    config = vars(arguments).copy()
    config.update(binary=str(arguments.binary.resolve()), output=str(output))
    write_json(output / "config.json", config)
    launcher, launcher_manifest = make_measurement_launcher(output)
    report = {
        "schema_version": SCHEMA_VERSION,
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": environment_metadata(),
        "binary_sha256": sha256(arguments.binary.read_bytes()),
        "binary_size_bytes": arguments.binary.stat().st_size,
        "build_profile": arguments.build_profile,
        "measurement_launcher": launcher_manifest,
        "source": source_manifest(Path(__file__).resolve().parent.parent),
        "baseline_definition": "The same binary and production DownloadWorker, page_concurrency=1 versus page_concurrency=2, with fresh data directories and identical authenticated image bytes.",
        "config": config,
        "measurements": {},
        "checks": {},
    }
    server = FixtureServer(fixtures, arguments)
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def run(name: str, scenario: str, data: str | None = None, extra: list[str] | None = None) -> dict:
        print(f"Running {name}", flush=True)
        server.statistics.set_phase(name)
        command = [str(arguments.binary.resolve()), "--scenario", scenario, "--base-url", base_url, "--data-dir", str(output / "data" / (data or name)), "--chapters", str(arguments.chapters), "--pages", str(arguments.pages)]
        command.extend(extra or [])
        load_before = os.getloadavg()
        measurement = run_process(command, output / name, arguments.timeout_seconds, launcher)
        measurement["http"] = server.statistics.snapshot(name)
        measurement["host_loadavg_before"] = load_before
        measurement["host_loadavg_after"] = os.getloadavg()
        report["measurements"][name] = measurement
        write_json(output / name / "http.json", measurement["http"])
        write_json(output / "results.json", report)
        check(measurement["result"].get("build_profile") == arguments.build_profile, "The executable build profile does not match the recorded benchmark profile")
        assert_result_files(measurement["result"], fixtures, base_url)
        return measurement

    try:
        expected_pages = arguments.chapters * arguments.pages
        expected_bytes = sum(item["size_bytes"] for path, item in fixtures.items() if path.startswith("/api/"))
        if arguments.only in {"all", "throughput"}:
            for repetition in range(1, arguments.repetitions + 1):
                modes = [("serial", 1), ("concurrent", 2)]
                if repetition % 2 == 0:
                    modes.reverse()
                for mode, concurrency in modes:
                    result = run(f"throughput-{mode}-{repetition}", "throughput", extra=["--concurrency", str(concurrency)])
                    check(result["http"]["image_requests"] == expected_pages, "A throughput image was fetched more than once or omitted")
                    check(result["http"]["image_bytes_sent"] == expected_bytes, "Throughput trials used different response bytes")
                    check(result["result"]["verified_pages"] == expected_pages, "Throughput output did not contain every valid image")
                    check(result["http"]["peak_image_concurrency"] <= concurrency, "The scheduling concurrency bound was exceeded")
                    if arguments.latency_ms >= 50:
                        check(result["http"]["peak_image_concurrency"] == concurrency, "A throughput trial did not exercise its configured page concurrency")
            report["checks"]["same_workload_throughput"] = True
        if arguments.only in {"all", "regressions"}:
            batch = run("worker-batch", "worker-batch", data="worker", extra=["--duplicate-enqueues", "3"])
            check(batch["http"]["image_requests"] == expected_pages, "Duplicate batch enqueues caused duplicate image requests")
            check(all(count == 1 for count in batch["http"]["image_requests_by_path"].values()), "A batch image URL was downloaded more than once")
            check(batch["result"]["verified_pages"] == expected_pages, "Batch download files were incomplete")
            report["checks"]["duplicate_enqueue_single_get"] = True
            hit = run("worker-cache-hit", "worker-batch", data="worker", extra=["--duplicate-enqueues", "2"])
            check(hit["http"]["http_requests"] == 0, "A completed cache hit caused extra HTTP requests")
            report["checks"]["completed_cache_hit_zero_http"] = True
            shared = run("reader-download-shared", "reader-download-shared")
            check(shared["http"]["image_requests"] == arguments.pages, "Reading and downloading fetched the same image twice")
            check(all(count == 1 for count in shared["http"]["image_requests_by_path"].values()), "Reader and downloader did not share cached bytes")
            report["checks"]["reader_download_share_cache"] = True
            paused = run("worker-paused", "worker-pause", data="resume")
            partial = paused["result"]["verified_pages"]
            check(0 < partial < arguments.pages, "Pause did not leave a partial chapter")
            resumed = run("worker-resumed", "worker-resume", data="resume")
            paused_paths = set(paused["http"]["image_requests_by_path"])
            persisted_paths = {urlsplit(file["url"]).path for file in paused["result"]["files"]}
            resumed_paths = set(resumed["http"]["image_requests_by_path"])
            check(not persisted_paths.intersection(resumed_paths), "Restart fetched a page already validated and persisted before pausing")
            check(all(count == 1 for count in resumed["http"]["image_requests_by_path"].values()), "Restart fetched a missing page more than once")
            check(len(paused_paths | resumed_paths) == arguments.pages, "Pause and restart did not cover exactly one chapter")
            check(resumed["result"]["verified_pages"] == arguments.pages, "Restart did not finish and persist all pages")
            report["checks"]["pause_restart_only_missing_pages"] = True
            native_report = output / "singleflight" / "native-rusage.json"
            singleflight = run("singleflight", "singleflight", extra=["--resource-report", str(native_report)])
            check(singleflight["http"]["image_requests"] == 1, "Concurrent identical requests were not coalesced")
            native_usage = json.loads(native_report.read_text())
            check(native_usage["exit_code"] == 0 and native_usage["peak_rss_kib"] > 0 and native_usage["wall_seconds"] > 0, "The native resource-report launcher did not record its child")
            singleflight["native_resource_report"] = native_usage
            report["checks"]["singleflight_identical_url_headers"] = True
            report["checks"]["native_resource_launcher"] = True
            headers = run("header-identity", "header-identity")
            check(headers["http"]["image_requests"] == 2, "Different request headers shared one cached response")
            variants = Counter(request["fixture_variant"] for request in headers["http"]["requests"] if request["kind"] == "image")
            check(variants == {"first": 1, "second": 1}, "The client did not send both request header identities")
            check(len({file["sha256"] for file in headers["result"]["files"]}) == 2, "Header-specific response bytes were not kept separate")
            report["checks"]["headers_are_part_of_cache_identity"] = True
            priority = run("foreground-priority", "foreground-priority")
            images = [request for request in priority["http"]["requests"] if request["kind"] == "image"]
            front = next(request for request in images if request["path"] == "/images/front")
            front_position = images.index(front)
            check(front_position == 2 and front["active_images_at_start"] == 3, "A foreground request did not use reserved capacity while two background responses were blocked")
            check(priority["http"]["peak_image_concurrency"] <= 3, "The total download concurrency limit was exceeded")
            report["checks"]["foreground_reserved_capacity"] = True
            report["foreground_start_position"] = front_position + 1
            lru = run("cache-lru-pins", "cache-lru-pins")
            check(lru["result"]["checks"].get("download_pin_survives_lru", False), "LRU removed a pinned download")
            report["checks"]["download_pins_survive_lru"] = True
            space = run("cache-low-space", "cache-low-space")
            check(space["result"]["checks"].get("low_space_preserves_existing", False), "Low-space failure damaged existing files")
            report["checks"]["low_space_preserves_existing"] = True
        report["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        write_json(output / "results.json", report)
        print(json.dumps({"output": str(output), "checks": report["checks"]}, indent=2), flush=True)
        return report
    except Exception:
        report["failure"] = traceback.format_exc()
        write_json(output / "results.json", report)
        raise
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        write_json(output / "requests.json", server.statistics.requests)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--build-profile", choices=["dev", "release"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chapters", type=int, default=6)
    parser.add_argument("--pages", type=int, default=12)
    parser.add_argument("--seed", type=int, default=20261105)
    parser.add_argument("--latency-ms", type=float, default=120.0)
    parser.add_argument("--chunk-bytes", type=int, default=65536)
    parser.add_argument("--chunk-delay-ms", type=float, default=0.0)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--only", choices=["all", "throughput", "regressions"], default="all")
    parser.add_argument("--serve-native", action="store_true")
    parser.add_argument("--guest-host-address")
    parser.add_argument("--serve-seconds", type=float, default=900.0)
    arguments = parser.parse_args()
    if sys.platform != "linux":
        parser.error("This verification harness requires the designated Linux remote environment")
    if arguments.chapters < 1 or arguments.pages < 6 or arguments.chunk_bytes < 1 or arguments.repetitions < 1:
        parser.error("Use at least one chapter, six pages, one repetition, and a positive chunk size")
    if arguments.latency_ms < 0 or arguments.chunk_delay_ms < 0:
        parser.error("Fixture delays cannot be negative")
    if arguments.serve_native and (not arguments.guest_host_address or arguments.serve_seconds <= 0):
        parser.error("Native fixtures require an observed guest host address and a positive lifetime")
    if arguments.serve_native:
        serve_native(arguments)
    else:
        benchmark(arguments)


if __name__ == "__main__":
    main()
