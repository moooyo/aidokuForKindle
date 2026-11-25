#!/usr/bin/env python3
"""Capture the UI handoff on an already-running, isolated Kindle firmware guest.

Run on test-env with Python 3.11+ and Pillow. The packaged launcher runs a
single ARM demo app. QMP touches go through native evdev; scene images are QMP
screendumps, not Slint snapshots. A separate app snapshot is saved at exit.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import gzip
import importlib.util
import json
from pathlib import Path
import re
import shlex
import sqlite3
import subprocess
import sys
import time

from PIL import Image, ImageChops


def load_firmware_helpers():
    path = Path(__file__).resolve().with_name("verify-firmware-emulator.py")
    spec = importlib.util.spec_from_file_location("_aidoku_firmware_verifier", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    # Loading the neighboring verifier should leave no test bytecode in the checkout.
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


firmware = load_firmware_helpers()
require = firmware.require
PROFILES = {
    "scribe": ((930, 1240), "aidoku-scribe1-5201", "guest-ssh-scribe1"),
    "pw6": ((618, 824), "aidoku-pw6-5201", "guest-ssh"),
}
EMULATOR_HOME = Path("/root/aidoku-emulator")
READY = re.compile(r"^aidoku: native window ready; profile=([\w-]+), logical=(\d+)x(\d+), scale=(\d+(?:\.\d+)?)$", re.MULTILINE)
LOCK = "/tmp/aidoku-launcher.lock"
SCALE = 2
PRIMARY = "8:mangadex:demo-00"


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def dark_ratio(image):
    return sum(image.histogram()[:85]) / (image.width * image.height)


def changed_ratio(before, after):
    return sum(ImageChops.difference(before, after).histogram()[17:]) / (before.width * before.height)


class Walkthrough(firmware.Verifier):
    def __init__(self, args):
        super().__init__(args)
        self.large = args.profile == "scribe"
        self.status, self.toolbar, self.dock, self.margin, self.touch_size = (
            (36, 64, 80, 40, 56) if self.large else (28, 56, 72, 28, 48)
        )
        self.reader_header, self.reader_toolbar, self.reader_panel = (72, 72, 272) if self.large else (64, 64, 232)
        self.root = shlex.quote(self.guest_root)
        self.database = f"{self.guest_root}/data/demo/library.sqlite3"
        self.log = self.output / "launcher.log"
        self.process = None
        self.app_pid = None
        self.fixture_owned = False
        self.launch_requested = False
        self.scenes = []
        self.inputs = []
        self.last_frame = None
        self.next_scene = "setup"
        self.checks = {}
        self.artifacts = {}

    def prepare_walkthrough(self):
        self.inspect_guest()
        self.checks["stock_before"] = self.require_stock_services()
        self.capture_stock()
        self.artifacts["stock_before"] = "stock-before.png"
        expected = firmware.STARTUP_GEOMETRY[self.arguments.profile]
        require(self.native_size == expected, f"Expected QMP framebuffer {expected}, got {self.native_size}")
        self.width, self.height = self.logical_size
        self.guest(
            f"set -eu; umask 077; mkdir -p /mnt/us/aidoku-native-tests; "
            f"test ! -L /mnt/us/aidoku-native-tests; mkdir {self.root}"
        )
        self.fixture_owned = True
        profile = shlex.quote(self.arguments.profile)
        self.guest(
            f"set -eu; mkdir {self.root}/app {self.root}/data {self.root}/data/logs; "
            f"printf '%s\\n' {profile} > {self.root}/data/device-profile; "
            f"printf '%s\\n' {self.arguments.duration_ms} > {self.root}/data/logs/test-duration-ms"
        )
        self.guest(
            f"set -eu; gzip -dc > {self.root}/app/aidoku.native; chmod 755 {self.root}/app/aidoku.native",
            payload=gzip.compress(self.binary.read_bytes(), compresslevel=1),
            timeout=420,
        )
        for source, target in (
            (self.wrapper, "app/aidoku"), (self.launcher, "launch.sh"),
            (self.arguments.native_portrait, "app/aidoku-startup-portrait"),
            (self.arguments.native_query, "app/aidoku-native-query"),
        ):
            if source is not None:
                self.put(source, f"{self.guest_root}/{target}", "755")

    def require_stock_services(self):
        state = self.service_state()
        require(state["statusbar_running"] and not state["awesome_stopped"] and "AWESOME=" in state["raw"],
                "The stock desktop does not have active statusbar and awesome processes")
        return state

    def running(self):
        require(self.process is not None and self.process.poll() is None,
                f"The foreground launcher is not running; see {self.log}")

    def owned_app(self, command):
        # All touch batches and the normal stop are gated on the same process.
        # Never signal an application found outside this run's unique fixture.
        launcher = shlex.quote(f"{self.guest_root}/launch.sh")
        native = shlex.quote(f"{self.guest_root}/app/aidoku.native")
        same = f'test "$application" = {self.app_pid}; ' if self.app_pid is not None else ""
        return self.guest(
            f"set -eu; lock={LOCK}; "
            'owner=$(cat "$lock/pid"); application=$(cat "$lock/application-pid"); '
            'case "$owner" in \'\'|*[!0-9]*) exit 1;; esac; '
            'case "$application" in \'\'|*[!0-9]*) exit 1;; esac; '
            f"tr '\\000' '\\n' </proc/$owner/cmdline | grep -Fx {launcher} >/dev/null; "
            f'test "$(readlink /proc/$application/exe)" = {native}; '
            'test "$(cat "$lock/phase")" = active; '
            'case "$(cat "$lock/generation")" in 1:*) ;; *) exit 1;; esac; '
            f"{same}{command}"
        )

    def start(self):
        self.next_scene = "startup"
        command = (
            "unset AIDOKU_LOCK_DIR AIDOKU_FRAMEBUFFER AIDOKU_MANAGED_LIBRARY AIDOKU_STARTUP_PORTRAIT "
            "AIDOKU_CATALOG_NOTIFY_HELPER AIDOKU_CATALOG_NOTIFY_SHA256; "
            f"export AIDOKU_HOME={self.root} AIDOKU_LAUNCHER_DIAGNOSTICS=launcher-v1; "
            + ("export AIDOKU_STARTUP_PORTRAIT=native; " if self.arguments.native_portrait else "")
            + f"exec sh {self.root}/launch.sh --foreground"
        )
        self.artifacts["launcher_log"] = self.log.name
        with self.log.open("wb") as log:
            self.process = subprocess.Popen([str(self.guest_ssh), command], stdout=log, stderr=subprocess.STDOUT)
            self.launch_requested = True
        deadline = time.monotonic() + self.arguments.startup_seconds
        waiting_for = "the native window announcement"
        while time.monotonic() < deadline:
            self.running()
            match = READY.search(self.log.read_text(errors="replace"))
            if match:
                profile, width, height, scale = match.groups()
                require(profile == self.arguments.profile and (int(width), int(height)) == self.logical_size
                        and float(scale) == SCALE, "The native window announced unexpected profile or geometry")
                try:
                    state = json.loads(self.owned_app('cat "$lock/lifecycle-state.json"'))
                except (AssertionError, ValueError) as error:
                    waiting_for = f"the owned lifecycle record ({error})"
                else:
                    app = state.get("application") or {}
                    waiting_for = "the initialized Library lifecycle record"
                    if state.get("startup") is False and app.get("route") == "library":
                        self.app_pid = int(state["pid"])
                        self.owned_app(":")
                        require(app.get("current_book") == PRIMARY, "The demo continuation was not initialized")
                        self.checks["startup"] = {"native_process_id": self.app_pid, "application": app}
                        # The announcement can precede the first Library panel update.
                        time.sleep(1.4)
                        return
            time.sleep(0.25)
        raise AssertionError(f"Timed out waiting for {waiting_for}")

    def tap(self, name, default):
        self.next_scene = name
        self.running()
        self.owned_app(":")
        super().touch(name, default)
        logical = self.tap_map.get(name, default)
        self.inputs.append({"kind": "tap", "name": name, "logical": list(logical),
                            "physical": [round(float(value) * SCALE) for value in logical]})
        # Navigation checkpoints are throttled to one save per second.
        time.sleep(0.3)

    def swipe(self, name, start, end):
        self.next_scene = name
        self.running()
        self.owned_app(":")
        self.inputs.append({"kind": "drag", **self.drag(name, start, end)})
        time.sleep(0.3)

    def database_state(self):
        # This isolated database uses rollback journals. Read it after the touch
        # settles, not while its owner is in the middle of a transaction. No
        # shell SQLite, guest writes, or database artifact are needed.
        data = self.guest(
            f"set -eu; db={shlex.quote(self.database)}; "
            'test ! -e "$db-journal"; test ! -e "$db-wal"; cat "$db"; test ! -e "$db-journal"; test ! -e "$db-wal"'
        )
        with closing(sqlite3.connect(":memory:")) as database:
            database.deserialize(data)
            rows = dict(database.execute(
                "SELECT key, document FROM preferences WHERE key IN ('navigation-session', 'ui:interaction-v1')"
            ))
            session = json.loads(rows.get("navigation-session", "{}"))
            interactions = json.loads(rows.get("ui:interaction-v1", "{}"))
            count = database.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        evidence = {key: session.get(key) for key in ("route", "page", "current_book_id", "current_source", "query", "selection_mode", "selected")}
        # The checkpoint deliberately omits live modal and reader chrome state.
        reader = session.get("reader")
        if reader:
            evidence["reader"] = {key: reader[key] for key in ("book_id", "chapter_id", "page", "total", "landscape")}
        evidence["catalog_records"] = len(session.get("catalog_book_ids", []))
        evidence["installed_sources"] = count
        evidence["settings_section"] = interactions.get("settings_section", "")
        return evidence

    def crop(self, image, box):
        return image.crop(tuple(round(coord * SCALE) for coord in box))

    def dark_band(self, image, y, centers, *, inset=13):
        return [dark_ratio(self.crop(image, (x - inset, y, x + inset, y + 1))) for x in centers]

    def marker(self, image, y, centers, active):
        ratios = self.dark_band(image, y, centers)
        require(all(value > 0.9 if index == active else value < 0.1 for index, value in enumerate(ratios)),
                f"Unexpected active marker at logical y={y}: {ratios}, expected index {active}")
        return ratios

    def dock_rule(self, image):
        return dark_ratio(self.crop(image, (0, self.height - self.dock, self.width, self.height - self.dock + 1)))

    def dock_point(self, index):
        return self.width * (index + 0.5) / 5, self.height - self.dock / 2

    def frame(self, name, *, expected=None, dock=None, inspect=None):
        self.next_scene = name
        path = self.output / f"{name}.png"
        deadline = time.monotonic() + self.arguments.scene_seconds
        previous = self.last_frame
        while True:
            self.running()
            try:
                self.qmp("screendump", str(path))
                with Image.open(path) as screen:
                    require(screen.size == self.native_size, f"Unexpected QMP screenshot size {screen.size}")
                    image = screen.convert("L")
                require(image.getextrema()[0] < 85 and image.getextrema()[1] > 200,
                        "QMP screenshot does not show a readable range of tones")
                if previous is None:
                    delta = firmware.difference(self.output / "stock-before.png", path)
                else:
                    delta = changed_ratio(previous, image)
                require(delta > 0.0005, "No visible scene transition in the QMP framebuffer")
                checks = {"frame": path.name, "changed_pixel_fraction": round(delta, 6), "input_count": len(self.inputs),
                          "dock_rule_ink_at_expected_y": round(self.dock_rule(image), 6)}
                if dock is not None:
                    centers = [self.dock_point(index)[0] for index in range(5)]
                    checks["dock_markers"] = self.marker(image, self.height - self.dock + 2, centers, dock)
                if inspect is not None:
                    checks.update(inspect(image))
                if expected is not None:
                    state = self.database_state()
                    for key, value in expected.items():
                        require(state.get(key) == value, f"Persisted {key}: expected {value!r}, got {state.get(key)!r}")
                    checks["navigation_checkpoint"] = state
                break
            except (AssertionError, OSError, sqlite3.Error, ValueError) as error:
                if time.monotonic() >= deadline:
                    raise AssertionError(f"{name}: {error}; last frame: {path}") from error
                time.sleep(0.4)
        self.scenes.append(checks)
        self.last_frame = image
        print(f"PASS {self.arguments.profile}/{name}", flush=True)
        return checks

    def reader_chrome(self, artwork, tool=None):
        tool_y = self.height - self.reader_toolbar
        panel_y = tool_y - self.reader_panel
        centers = [self.width * (index + 0.5) / 4 for index in range(4)]

        def inspect(image):
            # Chrome must not redraw, dim, or shift the unobscured page.
            box = (0, self.reader_header + 3, self.width, panel_y - 3)
            delta = changed_ratio(self.crop(artwork, box), self.crop(image, box))
            require(delta <= 0.002, f"Reader artwork shifted or changed under chrome ({delta:.4%})")
            header_dark = self.dark_band(image, self.reader_header - 1, centers)
            require(min(header_dark) > 0.85, "The Reader header rule is missing")
            checks = {"unobscured_artwork_change": round(delta, 6), "reader_tool_markers": self.marker(image, tool_y + 2, centers, tool)}
            if tool is not None:
                panel_dark = self.dark_band(image, panel_y, centers)
                require(min(panel_dark) > 0.85, "The Reader panel does not occupy its fixed footprint")
                checks["reader_panel_top_rule"] = panel_dark
            return checks

        return inspect

    def reader_walkthrough(self):
        center = (self.width / 2, self.height / 2)
        controls_y = self.height - self.reader_toolbar / 2
        start = (self.margin + 120, self.status + self.toolbar + 440 + self.touch_size / 2) if self.large else (
            self.width * 0.43, self.status + self.toolbar + 128 + self.touch_size / 2
        )
        self.tap("detail_continue", start)

        def reader_artwork(image):
            body = self.crop(image, (0, self.reader_header + 3, self.width, self.height - self.reader_toolbar - self.reader_panel - 3))
            ink = dark_ratio(body)
            require(ink > 0.01, "The fullscreen Reader has not shown its demo page artwork")
            centers = [self.width * (index + 0.5) / 4 for index in range(4)]
            header = self.dark_band(image, self.reader_header - 1, centers)
            require(min(header) < 0.85, "The closed Reader still has a full-width header rule")
            return {"reader_artwork_ink": round(ink, 6), "reader_header_rule_ink": header}

        checkpoint = {"route": "reader", "current_book_id": PRIMARY, "reader": {
            "book_id": PRIMARY, "chapter_id": f"{PRIMARY}-chapter-034", "page": 12, "total": 40, "landscape": False,
        }}
        self.frame("R01-reader-closed", expected=checkpoint, inspect=reader_artwork)
        artwork = self.last_frame
        self.tap("reader_menu", center)
        self.frame("R02-reader-menu", inspect=self.reader_chrome(artwork))
        for name, scene, tool in (("reading", "R04", 1), ("picture", "R05", 2), ("brightness", "R06", 3)):
            self.tap(f"reader_{name}", (self.width * (tool + 0.5) / 4, controls_y))
            self.frame(f"{scene}-reader-{name}", inspect=self.reader_chrome(artwork, tool))
        self.tap("reader_panel_close", center)
        self.frame("R02b-reader-menu-restored", inspect=self.reader_chrome(artwork))
        self.tap("reader_back", ((20 if self.large else 10) + self.touch_size / 2, self.reader_header / 2))
        self.frame("L01b-library-return", expected={"route": "library"}, dock=0)

    def browse_walkthrough(self):
        self.tap("browse_dock", self.dock_point(1))
        if self.large:
            catalog = self.frame("B01-browse-catalog", expected={"route": "catalog", "current_source": "mangadex"}, dock=1)
            state = catalog["navigation_checkpoint"]
            require(state["catalog_records"] > 0, "The Scribe Browse catalog has no demo records")
            self.tap("catalog_source_picker", (self.margin + 28, self.status + self.toolbar / 2))
            self.frame("B01b-source-picker", dock=1)
            self.tap("picker_manage_sources", (100, self.status + self.toolbar + 4 + min(10, state["installed_sources"]) * 62 + 31))
            self.frame("B03-sources", expected={"route": "sources"}, dock=1)
            self.tap("sources_back", (30 + self.touch_size / 2, self.status + self.toolbar / 2))
            self.frame("B02-catalog", expected={"route": "catalog", "current_source": "mangadex"}, dock=1)
        else:
            self.frame("B01-browse-sources", expected={"route": "sources"}, dock=1)
            # MangaDex is the first extension after four built-in rows, in the
            # first installed-source page on both compact portrait widths.
            self.tap("sources_mangadex_open", (self.width - self.margin - 48, self.status + self.toolbar + 2 * 36 + 4 * 72 + 36))
            catalog = self.frame("B02-catalog", expected={"route": "catalog", "current_source": "mangadex"})
            require(catalog["navigation_checkpoint"]["catalog_records"] > 0, "The PW6 Browse catalog has no demo records")

    def search_walkthrough(self):
        self.tap("search_dock", self.dock_point(2))
        self.frame("B04-search", expected={"route": "search", "query": ""}, dock=2)
        self.tap("search_field", (self.width / 3, self.status + (40 if self.large else 32)))
        height, candidate, gap, pad, bottom = (370, 60, 7, 12, 10) if self.large else (300, 50, 5, 6, 8)
        row = (height - 2 - candidate - bottom - gap * 4) / 4
        footer_y = self.height - bottom - row / 2
        footer_unit = (self.width - 2 * pad - gap * 5) / 10.5
        key_width = (self.width - 2 * pad - gap * 9) / 10

        def keyboard_rule(image):
            band = self.dark_band(image, self.height - height, (self.width / 4, self.width / 2, self.width * 3 / 4))
            require(min(band) > 0.85, "The search keyboard is missing its full-width top rule")
            return {"keyboard_top_rule": band}

        self.frame("B05-search-keyboard", inspect=keyboard_rule)
        self.tap("keyboard_numbers", (pad + footer_unit * 0.75, footer_y))
        self.tap("keyboard_one", (pad + key_width / 2, self.height - height + 2 + candidate + gap + row / 2))
        self.frame("B05b-search-keyboard-typed", inspect=keyboard_rule)
        self.tap("keyboard_submit", (self.width - pad - footer_unit, footer_y))
        self.frame("B04b-search-submitted", expected={"route": "search", "query": "1"}, dock=2)

    def walkthrough(self):
        self.frame("L01-library", dock=0)
        continuation = 136 if self.large else 124
        if self.arguments.include_pagination:
            # Begin on the first cover so that drag interception must cancel it.
            y = self.status + self.toolbar + continuation + (26 if self.large else 22) + 65
            right, left = (self.margin + 95, y), (self.margin + 12, y)
            self.swipe("library_swipe_next", right, left)
            self.frame("L02-library-page-two", expected={"route": "library", "page": 2, "selection_mode": False, "selected": []}, dock=0)
            self.swipe("library_swipe_previous", left, right)
            self.frame("L02b-library-page-one-restored", expected={"route": "library", "page": 1, "selection_mode": False, "selected": []}, dock=0)
        self.tap("library_continuation_cover", (self.margin + (38 if self.large else 32), self.status + self.toolbar + continuation / 2))
        self.frame("D01-detail", expected={"route": "detail", "current_book_id": PRIMARY})
        self.reader_walkthrough()
        self.browse_walkthrough()
        self.search_walkthrough()
        self.tap("history_dock", self.dock_point(3))
        self.frame("B06-history", expected={"route": "history"}, dock=3)
        self.tap("settings_dock", self.dock_point(4))
        self.frame("S01-settings-home", expected={"route": "settings", "settings_section": ""}, dock=4)
        home = self.last_frame
        refresh_row = (120, self.status + 76 + 44 + 2 * 60 + 30) if self.large else (
            self.width / 4, self.status + self.toolbar + 34 + 90 + 45
        )
        self.tap("settings_screen_refresh", refresh_row)

        def refresh_layout(image):
            split = 320 if self.large else self.width / 2
            left = (0, self.status, split, self.height - self.dock)
            right = (split, self.status, self.width, self.height - self.dock)
            left_delta = changed_ratio(self.crop(home, left), self.crop(image, left))
            right_delta = changed_ratio(self.crop(home, right), self.crop(image, right))
            require(right_delta > 0.005, "The Settings detail pane did not change")
            if self.large:
                require(left_delta < 0.02, "The Scribe Settings rail was replaced instead of retained")
            else:
                require(left_delta > 0.005, "The compact Settings subpage did not replace the home grid")
            return {"settings_pane_change_left": round(left_delta, 6), "settings_pane_change_right": round(right_delta, 6)}

        self.frame("S04-settings-screen-refresh", expected={"route": "settings", "settings_section": "screen-refresh"},
                   dock=4, inspect=refresh_layout)

    def finish(self):
        self.next_scene = "final native capture"
        self.running()
        self.owned_app('kill -TERM "$application"')
        require(self.process.wait(timeout=24) == 0, f"The app/launcher did not exit successfully; see {self.log}")
        stem = f"{self.guest_root}/data/logs/native-library"
        for label, suffix in (("app_snapshot", ".png"), ("app_metadata", ".metadata.json"), ("app_state", ".state.json")):
            path = self.output / ("final-app" + suffix)
            self.retrieve(stem + suffix, path)
            self.artifacts[label] = path.name
        metadata = firmware.read_json(self.output / self.artifacts["app_metadata"])
        state = firmware.read_json(self.output / self.artifacts["app_state"])
        require(metadata["backend"] == "kindle" and metadata["profile"] == self.arguments.profile
                and metadata["process_id"] == self.app_pid and metadata["scale_factor"] == SCALE
                and tuple(metadata["window_size"]) == self.native_size and tuple(metadata["logical_size"]) == self.logical_size
                and metadata["landscape"] is False, "The final capture does not match the one native portrait process")
        require(state["demo"] is True and state["offline"] is True and state["database"] == self.database
                and state["route"] == "settings" and state["settings_section"] == "screen-refresh" and state["modal"] == ""
                and state["large_screen"] is self.large and state["history_count"] > 0 and state["library_count"] > 0
                and state["query"] == "1", "The final app state does not match the isolated walkthrough")
        with Image.open(self.output / self.artifacts["app_snapshot"]) as snapshot:
            require(snapshot.size == tuple(metadata["snapshot_size"]), "The app snapshot geometry disagrees with its metadata")
        self.checks["final_native_metadata"] = metadata

    def restore(self):
        errors = []
        if self.process is not None and self.process.poll() is None:
            self.stop_owned_launcher()
            try:
                self.process.wait(timeout=18)
            except subprocess.TimeoutExpired:
                self.process.kill()  # The local SSH helper, never an unverified guest process.
                errors.append("The owned launcher did not return after cleanup was requested")
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    errors.append("The local SSH helper also did not exit")
        if self.launch_requested:
            try:
                self.guest(f"test ! -e {LOCK}")
                self.checks["launcher_lock_removed"] = True
                self.checks["stock_after"] = self.require_stock_services()
                path = self.output / "stock-after.png"
                self.qmp("screendump", str(path))
                self.artifacts["stock_after"] = path.name
                self.checks["stock_before_after_change"] = firmware.difference(self.output / "stock-before.png", path)
                if self.scenes:
                    delta = firmware.difference(self.output / self.scenes[-1]["frame"], path)
                    self.checks["last_app_to_stock_change"] = delta
                    require(delta > 0.0005, "The QMP buffer did not change from the last app scene")
            except Exception as error:
                errors.append(f"Stock restoration: {error}")
        if self.fixture_owned:
            try:
                path = f"{self.guest_root}/data/logs/native-portrait.stderr"
                portrait = self.guest(f"if [ -s {shlex.quote(path)} ]; then cat {shlex.quote(path)}; fi")
                if portrait:
                    destination = self.output / "native-portrait.stderr"
                    destination.write_bytes(portrait)
                    self.artifacts["portrait_error"] = destination.name
            except Exception as error:
                errors.append(f"Portrait diagnostic retrieval: {error}")
            try:
                machine = shlex.quote(self.arguments.machine)
                self.guest(f"set -eu; test \"$(cat /var/local/aidoku-emulator-id)\" = {machine}; "
                           f"test ! -e {LOCK}; test ! -L /mnt/us/aidoku-native-tests; rm -rf {self.root}; test ! -e {self.root}")
                self.checks["fixture_removed"] = True
            except Exception as error:
                errors.append(f"Isolated fixture cleanup: {error}")
        return errors

    def run_walkthrough(self):
        errors = []
        started = time.monotonic()
        print(f"RUN {self.arguments.profile}: {self.output}", flush=True)
        try:
            self.prepare_walkthrough()
            self.start()
            self.walkthrough()
            self.finish()
        except (Exception, KeyboardInterrupt) as error:
            errors.append(f"{self.next_scene}: {str(error) or type(error).__name__}")
        finally:
            errors.extend(self.restore())
        report = self.output / "report.json"
        write_json(report, {
            "passed": not errors, "errors": errors, "profile": self.arguments.profile, "emulator": self.environment,
            "binary": str(self.binary), "launcher": str(self.launcher), "guest_fixture": self.guest_root,
            "native_portrait": str(self.arguments.native_portrait) if self.arguments.native_portrait else None,
            "native_query": str(self.arguments.native_query) if self.arguments.native_query else None,
            "prototype_logical_size": PROFILES[self.arguments.profile][0],
            "native_logical_size": getattr(self, "logical_size", None), "native_frame_size": getattr(self, "native_size", None),
            "scale_factor": SCALE, "duration_seconds": round(time.monotonic() - started, 3),
            "inputs": self.inputs, "scenes": self.scenes, "checks": self.checks, "artifacts": self.artifacts,
            "scope": [
                "One packaged foreground launcher and one ARM app generation in the isolated firmware emulator.",
                "Coordinates use the native logical viewport; QMP receives physical pixels at scale 2.",
                "QMP scans and durable route checkpoints are independent evidence. Live panel and keyboard layout use QMP pixels.",
                "The final app snapshot comes from a controlled app-only TERM, not a simulated stock launcher-card tap.",
                "Offline demo and synthetic e-ink waveforms do not qualify physical hardware, lighting, ghosting, or latency.",
            ],
        })
        print(json.dumps({"report": str(report), "scenes": len(self.scenes), "errors": errors}), flush=True)
        return int(bool(errors))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=PROFILES, default="pw6")
    parser.add_argument("--machine", help="Aidoku guest name; defaults to the selected profile's known instance")
    parser.add_argument("--emulator-root", type=Path, default=EMULATOR_HOME / "eink-emulator")
    parser.add_argument("--guest-ssh", type=Path, help="Local helper for SSH to the selected isolated guest")
    parser.add_argument("--binary", type=Path, required=True, help="Built 32-bit ARM Aidoku ELF")
    parser.add_argument("--native-portrait", type=Path, help="Optional built startup portrait supervisor")
    parser.add_argument("--native-query", type=Path, help="Optional built firmware LIPC query helper")
    parser.add_argument("--output-dir", type=Path, required=True, help="Parent for a fresh run directory")
    parser.add_argument("--firmware", default="5.20.1")
    parser.add_argument("--startup-seconds", type=float, default=180)
    parser.add_argument("--scene-seconds", type=float, default=12)
    parser.add_argument("--duration-ms", type=int, default=180000, help="Wrapper watchdog; the app normally exits earlier")
    parser.add_argument("--include-pagination", action="store_true", help="Also verify a QMP Library drag round trip")
    parser.add_argument("--tap-map", type=Path, help="Override named logical tap points or drag endpoints with JSON")
    arguments = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Run on the designated worker through ssh test-env, not the workstation or a physical Kindle")
    if not 1 <= arguments.startup_seconds <= 600 or not 1 <= arguments.scene_seconds <= 60:
        parser.error("Keep startup and per-scene timeouts within 1..600 and 1..60 seconds, respectively")
    if not 1200 <= arguments.duration_ms <= 180000:
        parser.error("The wrapper duration must be between 1200 and 180000 milliseconds")
    _, machine, helper = PROFILES[arguments.profile]
    arguments.machine = arguments.machine or machine
    arguments.guest_ssh = arguments.guest_ssh or EMULATOR_HOME / helper
    try:
        return Walkthrough(arguments).run_walkthrough()
    except Exception as error:
        print(f"Firmware walkthrough setup or cleanup failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
