#!/usr/bin/env python3
"""Controlled HTTP provider for native sleep ownership verification.

This is test infrastructure. Background responses send an incomplete PNG
prefix and a one-byte heartbeat until released; actual TCP closure is observed.
It never changes guest Wi-Fi or USB configuration.
"""

from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import select
import socket
import threading
import time
from urllib.parse import urlsplit


TOKEN = "fixture-token"


class SleepNetworkFixture:
    def __init__(self, output: Path, images: tuple[Path, Path], guest_host: str = "192.168.15.201", pages: int = 8, hold_foreground: bool = False):
        self.output = output
        output.mkdir(parents=True, exist_ok=False)
        self.payloads = tuple(path.read_bytes() for path in images)
        self.pages = pages
        self.guest_host = guest_host
        self.hold_foreground = hold_foreground
        self.lock = threading.Lock()
        self.release_background = threading.Event()
        self.stopping = threading.Event()
        self.started = time.monotonic()
        self.requests: list[dict] = []
        self.active_background = 0
        self.active_foreground = 0
        self.peak_background = 0
        self.peak_total = 0
        self.foreground_with_two_background = False
        fixture = self

        class Server(ThreadingHTTPServer):
            daemon_threads = True

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, _format, *_args):
                pass

            def do_GET(self):
                path = urlsplit(self.path).path
                image = re.fullmatch(r"/api/v1/books/(c\d+)/pages/(\d+)", path)
                metadata = re.fullmatch(r"/api/v1/books/(c\d+)/pages", path)
                role = "metadata"
                if image:
                    role = "foreground" if image.group(1) == "c05" and image.group(2) == "1" else "background"
                with fixture.lock:
                    record = {"sequence": len(fixture.requests), "path": path, "role": role, "started_ms": round((time.monotonic() - fixture.started) * 1000, 3), "status": None, "bytes_sent": 0, "client_closed_while_held": False}
                    fixture.requests.append(record)
                    if image:
                        if role == "foreground":
                            fixture.active_foreground += 1
                            if fixture.active_background >= 2:
                                fixture.foreground_with_two_background = True
                        else:
                            fixture.active_background += 1
                        fixture.peak_background = max(fixture.peak_background, fixture.active_background)
                        fixture.peak_total = max(fixture.peak_total, fixture.active_background + fixture.active_foreground)
                status, sent = 200, 0
                try:
                    if self.headers.get("X-API-Key") != TOKEN:
                        status, media, body = 401, "application/json", b'{"error":"fixture token required"}'
                    elif image:
                        chapter, page = image.group(1), int(image.group(2))
                        body = fixture.payloads[1 if chapter == "c05" else (page - 1) % 2]
                        media = "image/png"
                    elif metadata:
                        count = 3 if metadata.group(1) == "c05" else fixture.pages
                        body = json.dumps([{"number": page, "mediaType": "image/png"} for page in range(1, count + 1)]).encode()
                        media = "application/json"
                    else:
                        status, media, body = 404, "application/json", b'{"error":"unknown sleep fixture"}'
                    self.send_response(status)
                    self.send_header("Content-Type", media)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    hold = bool(image and (image.group(1) in {"c01", "c02"} or fixture.hold_foreground and role == "foreground") and status == 200 and not fixture.release_background.is_set())
                    if hold:
                        prefix = max(1, min(1024, len(body) // 8))
                        self.wfile.write(body[:prefix])
                        self.wfile.flush()
                        sent = prefix
                        last_heartbeat = time.monotonic()
                        while not fixture.release_background.wait(0.05):
                            if fixture.stopping.is_set():
                                self.close_connection = True
                                return
                            # Keep the production 30-second idle cap from
                            # expiring while SSH evidence is collected. The
                            # image remains incomplete, its bytes stay exact,
                            # and the production total deadline remains active.
                            if time.monotonic() - last_heartbeat >= 1 and sent < len(body) - 1:
                                self.wfile.write(body[sent:sent + 1])
                                self.wfile.flush()
                                sent += 1
                                last_heartbeat = time.monotonic()
                            readable, _, _ = select.select([self.connection], [], [], 0)
                            if readable and self.connection.recv(1, socket.MSG_PEEK) == b"":
                                with fixture.lock:
                                    record["client_closed_while_held"] = True
                                self.close_connection = True
                                return
                    for offset in range(sent, len(body), 65536):
                        block = body[offset:offset + 65536]
                        self.wfile.write(block)
                        self.wfile.flush()
                        sent += len(block)
                except (BrokenPipeError, ConnectionResetError, OSError) as error:
                    with fixture.lock:
                        record["error"] = type(error).__name__
                        if image and (image.group(1) in {"c01", "c02"} or fixture.hold_foreground and role == "foreground") and not fixture.release_background.is_set():
                            record["client_closed_while_held"] = True
                    self.close_connection = True
                finally:
                    with fixture.lock:
                        record.update(status=status, bytes_sent=sent, completed_ms=round((time.monotonic() - fixture.started) * 1000, 3))
                        if image:
                            if role == "foreground": fixture.active_foreground -= 1
                            else: fixture.active_background -= 1

        self.server = Server(("0.0.0.0", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, name="sleep-http-fixture", daemon=True)
        self.thread.start()
        self.base_url = f"http://{guest_host}:{self.server.server_address[1]}"
        (output / "fixture.json").write_text(json.dumps({"base_url": self.base_url, "pages": pages, "images": [{"source": str(path), "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)} for path, payload in zip(images, self.payloads)], "scope": "Controlled HTTP over guest USB NAT; no RF or USB ownership manipulation."}, indent=2) + "\n")

    def snapshot(self) -> dict:
        with self.lock:
            return {"requests": [dict(record) for record in self.requests], "request_count": len(self.requests), "active_background": self.active_background, "active_foreground": self.active_foreground, "peak_background": self.peak_background, "peak_total": self.peak_total, "foreground_with_two_background": self.foreground_with_two_background, "closed_while_held": sum(record["client_closed_while_held"] for record in self.requests), "background_released": self.release_background.is_set()}

    def release(self):
        self.release_background.set()

    def close(self):
        self.stopping.set()
        self.release_background.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = self.snapshot()
            if not state["active_background"] and not state["active_foreground"]:
                break
            time.sleep(0.05)
        (self.output / "requests.json").write_text(json.dumps(self.snapshot(), indent=2) + "\n")
