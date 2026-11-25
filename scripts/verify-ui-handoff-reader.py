#!/usr/bin/env python3
"""Capture the portrait Reader handoff through real Slint pointer events on test-env."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import zipfile

from PIL import Image, ImageChops, ImageDraw


PROFILES = {"scribe": (930, 1240), "pw5": (618, 824), "pw6": (632, 840)}
CASES = ("core", "panels", "seek", "navigation", "bookmarks", "gestures", "night", "page-actions", "recovery")


class Replay:
    def __init__(self, start):
        self.at = start
        self.events = []

    def send(self, section, action, value="", wait=450):
        self.events.append({"at_ms": self.at, "section": section, "action": action, "value": value})
        self.at += wait
        return self

    def point(self, action, xy, wait=100):
        return self.send("pointer", action, f"{xy[0]:g},{xy[1]:g}", wait)

    def press(self, xy, wait=100):
        return self.point("move", xy).point("press", xy, wait)

    def tap(self, xy, wait=450):
        return self.press(xy).point("release", xy, wait)

    def hold(self, xy):
        return self.press(xy, 700).point("release", xy, 500)

    def drag(self, first, last, *, release=True):
        self.press(first).point("move", last, 250)
        return self.point("release", last, 450) if release else self

    def setting(self, key, value):
        return self.send("settings", "set", f"{key}={value}", 1000)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


class Capture:
    def __init__(self, png):
        self.png = png
        self.state = {}
        self.failures = []

    def check(self, ok, message):
        if not ok:
            self.failures.append(message)

    def expect(self, **expected):
        for key, value in expected.items():
            self.check(self.state.get(key) == value, f"{key}: expected {value!r}, got {self.state.get(key)!r}")

    def preference(self, key, value):
        actual = self.state.get("preferences", {}).get(key)
        self.check(actual == value, f"Preference {key}: expected {value!r}, got {actual!r}")

    def page(self, number, *, menu=False, modal=""):
        self.expect(route="reader", reader_image_ready=True, visible_reader_page=number,
                    reader_page=number, committed_reader_page=number, menu=menu, modal=modal)

    def image(self):
        with Image.open(self.png) as image:
            return image.convert("RGB")

    def compare(self, reference, box, *, same):
        if self.png.is_file() and reference.png.is_file():
            difference = ImageChops.difference(self.image().crop(box), reference.image().crop(box))
            self.check((difference.getbbox() is None) == same,
                       f"Pixels in {box} should {'match' if same else 'differ from'} {reference.png.name}")

    def tone(self, point, value):
        if self.png.is_file():
            rgb = self.image().getpixel(point)
            self.check(all(abs(channel - value) <= 3 for channel in rgb),
                       f"Pixel {point}: expected gray {value}, got {rgb}")


def run_capture(args, profile, output, data, name, replay, *, screen="reader", demo=True, import_path=None):
    data.mkdir(parents=True, exist_ok=True)
    runtime = data / "xdg-runtime"
    runtime.mkdir(mode=0o700, exist_ok=True)
    capture = Capture(output / f"{name}.png")
    actions = output / f"{name}.actions.json"
    write_json(actions, replay.events)
    command = [str(args.binary), "--device", profile, "--offline", "--screen", screen,
               "--data-dir", str(data), "--actions", str(actions), "--capture", str(capture.png),
               "--exit-after-ms", str(replay.at + args.settle_ms)]
    if demo:
        command.append("--demo")
    if import_path:
        command.extend(["--import", str(import_path)])
    if not os.environ.get("DISPLAY"):
        command[:0] = ["xvfb-run", "-a", "-s", "-screen 0 1600x1600x24"]
    env = dict(os.environ, SLINT_BACKEND="winit-software", SLINT_RENDERER="software",
               SLINT_SCALE_FACTOR="1", XDG_RUNTIME_DIR=str(runtime), TZ="Asia/Shanghai")
    print(f"RUN {profile}/{name}", flush=True)
    try:
        with (output / f"{name}.log").open("wb") as log, subprocess.Popen(
            command, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        ) as process:
            try:
                code = process.wait(timeout=args.timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                raise
            capture.check(code == 0, f"Application exit status: {code}")
        capture.state = read_json(capture.png.with_suffix(".state.json"))
        metadata = read_json(capture.png.with_suffix(".metadata.json"))
        capture.check(metadata.get("profile") == profile and metadata.get("backend") == "desktop",
                      "Capture must use the requested desktop profile")
        capture.check(metadata.get("scale_factor") == 1 and capture.image().size == PROFILES[profile],
                      "Snapshot must use the profile's unit-scale portrait geometry")
        capture.expect(busy=False)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        capture.failures.append(str(error))
    return capture


def create_cbz(path):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for page in range(1, 11):
            image = Image.new("L", (450, 600), 255)
            draw = ImageDraw.Draw(image)
            draw.rectangle((18, 22, 431, 577), outline=0, width=4)
            draw.rectangle((50, 114, 399, 526), fill=40 + page * 16)
            draw.text((54, 54), f"Reader recovery page {page}", fill=0)
            encoded = io.BytesIO()
            image.save(encoded, format="PNG")
            archive.writestr(f"page-{page:02}.png", encoded.getvalue())
        archive.writestr("ComicInfo.xml", "<ComicInfo><Series>Reader recovery fixture</Series>"
                         "<Title>One chapter</Title><Number>1</Number></ComicInfo>")


def verify_profile(args, profile, output, work):
    width, height = PROFILES[profile]
    large = profile == "scribe"
    touch, header, toolbar, progress, panel = (56, 72, 72, 124, 272) if large else (48, 64, 64, 108, 232)
    margin, inset, chapter_width = (40, 32, 110) if large else (28, 20, 88)
    progress_y = height - toolbar - progress
    panel_y = height - toolbar - panel
    progress_track_y = progress_y + (14 + 38 if large else 12 + 32) + 1.5 + touch / 2
    panel_inner_y = panel_y + (16 if large else 12) + 1.5
    slider_row = 60 if large else 52
    art = (0, header, width, progress_y)
    art_above_panel = (0, header, width, panel_y)
    center, outside = (width / 2, height / 2), (10, 300)
    left, right, lower_right = (width * .15, height / 2), (width * .85, height / 2), (width - 48, height - 30)
    bookmark = (width - (20 if large else 10) - touch / 2, header / 2)
    chapters, reading, picture, brightness = [(width * (index + .5) / 4, height - toolbar / 2) for index in range(4)]
    next_chapter = (width - inset - chapter_width / 2, progress_track_y)
    prev_chapter = (inset + chapter_width / 2, progress_track_y)
    captures = []

    def start():
        return Replay(args.start_ms)

    def menu():
        return start().tap(center)

    def bounds(value, low, high, y):
        return low + (high - low) * value, y

    def seek(page):
        edge = inset + chapter_width + 12
        return bounds((page - 1) / 39, edge, width - edge, progress_track_y)

    def record(name, replay, **kwargs):
        data = kwargs.pop("data", work / name)
        result = run_capture(args, profile, output, data, name, replay, **kwargs)
        captures.append(result)
        return result

    selected = set(args.case or CASES)
    if {"core", "night"} & selected:
        fullscreen = record("fullscreen", start())
        fullscreen.page(12)
    if {"core", "panels", "seek", "bookmarks", "night"} & selected:
        controls = record("controls", menu())
        controls.page(12, menu=True)
        controls.expect(reader_panel="")
    if "core" in selected:
        controls.compare(fullscreen, art, same=True)
        closed = record("menu-closed", menu().tap(center))
        closed.page(12)
        closed.compare(fullscreen, (0, 0, width, height), same=True)

    if "panels" in selected:
        for name, tool in (("mode", reading), ("picture", picture), ("brightness", brightness)):
            # Switch from a different tool so one fixed footprint is exercised.
            prior = reading if name != "mode" else picture
            shown = record(f"panel-{name}", menu().tap(prior).tap(tool))
            shown.expect(menu=True, reader_panel=name, modal_context=f"reader-{name}")
            shown.compare(controls, art_above_panel, same=True)
            shown.tone((4, panel_y), 17)
            shown.tone((4, height - toolbar), 187)
        reading_switches = record("reading-switches", menu().tap(reading)
                                  .tap((width - margin - 20, panel_inner_y + slider_row / 2))
                                  .tap((width - margin - 20, panel_inner_y + slider_row * 1.5)))
        reading_switches.expect(reader_panel="mode", reading_direction="ltr")
        reading_switches.preference("crop_borders", False)
        segment_height = (52 if large else 44) + 3
        interval_y = panel_inner_y + touch + 6
        mode_y = interval_y + segment_height + (18 + 26 if large else 14 + 24) + 8 + segment_height / 2
        for name, index, locked_index in (("fast", 0, 4), ("high_quality", 2, 0)):
            column = (width - 2 * margin) / 5
            locked = record(f"refresh-locked-{name}", menu().tap(picture)
                            .tap((margin + (index + .5) * (width - 2 * margin) / 4, mode_y), wait=1200)
                            .tap((margin + 1.5 * column, interval_y + segment_height / 2)))
            locked.expect(reader_panel="picture", display_mode=name)
            locked.preference("refresh_every", 6)
            locked.tone((int(margin + (locked_index + .5) * column), int(interval_y + 6)), 187)
        light_left = margin + (72 if large else 60) + 6 + touch + 12
        light_right = width - margin - (56 if large else 44) - 6 - touch - 12
        light_y = panel_inner_y + slider_row / 2
        for release in (False, True):
            light = record("brightness-release" if release else "brightness-preview", menu().tap(brightness)
                           .drag(bounds(.5, light_left, light_right, light_y),
                                 bounds(.75, light_left, light_right, light_y), release=release))
            light.expect(reader_panel="brightness")
            light.preference("brightness", 18 if release else 12)

    if "seek" in selected:
        seek_preview = record("seek-preview", menu().drag(seek(12), seek(30), release=False))
        seek_preview.page(12, menu=True)
        seek_preview.expect(reader_inflight=False)
        seek_preview.compare(controls, art, same=True)
        seek_preview.compare(controls, (width // 2 - 76, progress_y + 2, width // 2 + 76, int(progress_track_y - touch / 2)), same=False)
        released = record("seek-release", menu().drag(seek(12), seek(30)))
        released.page(30)
        canceled = record("seek-cancel", menu().drag(seek(12), seek(30), release=False).send("pointer", "cancel"))
        canceled.page(12, menu=True)
        canceled.compare(controls, (0, 0, width, height), same=True)

    if "navigation" in selected:
        toc = record("chapters", menu().tap(chapters))
        toc.expect(route="detail", modal="", detail_filter=0)
        returned = record("return-library", menu().tap(((20 if large else 10) + touch / 2, header / 2)))
        returned.expect(route="library", modal="")
        jumped = record("next-chapter-button", menu().tap(next_chapter, wait=1200))
        jumped.page(1)
        jumped.check(str(jumped.state.get("current_chapter")).endswith("chapter-035"), "Next chapter button must open chapter 35")
        previous = record("previous-chapter-button", menu().tap(prev_chapter, wait=1200))
        previous.page(1)
        previous.check(str(previous.state.get("current_chapter")).endswith("chapter-033"), "Previous chapter button must open the start of chapter 33")
        backward = record("previous-chapter-boundary", start().send("reader", "seek", "1", 1400).tap(right, wait=1000))
        backward.page(40)
        backward.check(str(backward.state.get("current_chapter")).endswith("chapter-033"), "Turning backward must open the end of chapter 33")
        crossed = record("chapter-boundary", start().send("reader", "seek", "40", 1400).tap(left, wait=1000))
        crossed.page(1)
        crossed.check(str(crossed.state.get("current_chapter")).endswith("chapter-035"), "Final page must enter chapter 35 directly")

    if "bookmarks" in selected:
        pressed = record("bookmark-pressed", menu().press(bookmark))
        pressed.page(12, menu=True)
        pressed.compare(controls, (0, 0, width, height), same=True)
        toggled = record("bookmark-toggle", menu().tap(bookmark))
        toggled.page(12, menu=True)
        toggled.expect(reader_bookmarked=not controls.state.get("reader_bookmarked"))
        toggled.compare(controls, (int(bookmark[0] - 20), 6, width, header - 6), same=False)

    if "gestures" in selected:
        turn_cases = (
            ("rtl-left", start().tap(left), 13, False),
            ("rtl-right", start().tap(right), 11, False),
            ("rtl-lower-right", start().tap(lower_right), 11, False),
            ("l-shaped-lower-right", start().setting("tap-layout", "l-shaped").tap(lower_right), 13, False),
            ("reversed-left", start().setting("reverse-taps", "true").tap(left), 11, False),
            ("swipe-only-tap", start().setting("tap-layout", "swipe-only").tap(lower_right), 12, False),
            ("swipe-only-drag", start().setting("tap-layout", "swipe-only").drag(left, right), 13, False),
            ("swipe-only-menu", start().setting("tap-layout", "swipe-only").tap(center), 12, True),
        )
        for name, replay, number, open_menu in turn_cases:
            record(name, replay).page(number, menu=open_menu)

    if "night" in selected:
        night_toggle = (width - margin - 24, panel_inner_y + slider_row * 3.5)
        night = record("night-controls", menu().tap(brightness).tap(night_toggle).tap(outside))
        night.page(12, menu=True)
        night.preference("night_mode", True)
        night.compare(controls, art, same=True)
        night.compare(controls, (0, 0, width, header), same=False)
        night.tone((4, 4), 17)
        night_closed = record("night-fullscreen", menu().tap(brightness).tap(night_toggle).tap(outside).tap(center))
        night_closed.page(12)
        night_closed.preference("night_mode", True)
        night_closed.compare(fullscreen, (0, 0, width, height), same=True)

    if "page-actions" in selected:
        hold = record("page-actions", start().hold(center))
        hold.page(12, modal="choices")
        hold.expect(modal_context="page-actions", modal_item_ids=["save-page", "set-cover", "reload-page"])
        done = (width / 2 + 250, 293) if large else (width - 62, height - 214)
        record("page-actions-done", start().hold(center).tap(done)).page(12)
        record("page-actions-back", start().hold(center).send("app", "back")).page(12)
        save = (width / 2 - 230, 360) if large else (100, height - 154)
        saved = record("page-actions-save", start().hold(center).tap(save))
        saved.page(12)
        database = saved.state.get("database")
        saved.check(bool(database) and any((Path(database).parent / "exports").glob("*-12.png")),
                    "Saving through the long-hold sheet must export the displayed page")

    if "recovery" in selected:
        recovery_data = work / "recovery-fixture"
        archive = work / "Reader fixture.cbz"
        create_cbz(archive)
        imported = record("recovery-import", start(), data=recovery_data, screen="library", demo=False, import_path=archive)
        imported.expect(route="library")
        try:
            database = Path(imported.state["database"]).resolve()
            if not database.is_relative_to(recovery_data.resolve()):
                raise ValueError("The fixture database is outside its isolated data directory")
            with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as connection:
                paths = connection.execute("SELECT position, document FROM pages ORDER BY position").fetchall()
            if len(paths) != 10:
                raise ValueError("The imported fixture should have ten pages")
            chapter_edges = record("disabled-chapter-arrows", menu().tap(prev_chapter).tap(next_chapter), data=recovery_data, demo=False)
            chapter_edges.page(1, menu=True)
            chapter_edges.expect(reader_has_previous_chapter=False, reader_has_next_chapter=False)
            failed_image = Path(json.loads(dict(paths)[7])["local_path"]).resolve()
            if not failed_image.is_relative_to(recovery_data.resolve()):
                raise ValueError("The test must not remove any image outside its own fixture")
            hidden = failed_image.with_name(f"{failed_image.name}.hidden")
            failed_image.rename(hidden)
            try:
                def fail_next():
                    return start().send("reader", "seek", "7", 1500).tap(left, wait=1800)

                failed = record("page-load-failure", fail_next(), data=recovery_data, demo=False)
                failed.page(7, modal="choices")
                failed.expect(modal_context="reader-recovery", modal_item_ids=["reader-retry", "reader-recovery-back"])
                recovery = failed.state.get("reader_recovery") or {}
                failed.check(recovery.get("target", {}).get("page") == 8, "Failure must retain target page 8")
                failed.check(recovery.get("reason") == "missing_file", "Failure should retain a safe missing-file reason")
                retry_row = (width / 2 - 230, 360) if large else (100, height - 102)
                retry = record("page-load-retry", fail_next().tap(retry_row, wait=1300), data=recovery_data, demo=False)
                retry.page(7, modal="choices")
                retry.expect(modal_context="reader-recovery")
                errors = (output / "page-load-retry.log").read_text(encoding="utf-8", errors="replace").splitlines()
                retry.check(sum(line.startswith("aidoku: reader request ") and " page 8:" in line for line in errors) == 2,
                            "Retry must request the failed page once more while keeping page 7 displayed")
                keep_reading = (width / 2 - 230, 420) if large else (100, height - 50)
                back = record("page-load-continue", fail_next().tap(keep_reading), data=recovery_data, demo=False)
                back.page(7)
                back.expect(reader_recovery=None)
            finally:
                hidden.rename(failed_image)
        except (KeyError, OSError, ValueError, sqlite3.Error) as error:
            imported.failures.append(f"Could not exercise the isolated CBZ recovery: {error}")

    results = [{"capture": str(case.png), "failures": case.failures} for case in captures]
    write_json(output / "report.json", results)
    for entry in results:
        for failure in entry["failures"]:
            print(f"FAIL {profile}/{Path(entry['capture']).stem}: {failure}", flush=True)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device", choices=PROFILES, action="append", help="Repeat to choose profiles; defaults to Scribe and PW5")
    parser.add_argument("--case", choices=CASES, action="append", help="Repeat to select groups; defaults to all groups")
    parser.add_argument("--start-ms", type=int, default=4500, help="First action time after the app starts")
    parser.add_argument("--settle-ms", type=int, default=2300, help="Wait after the final action before capturing")
    parser.add_argument("--timeout", type=int, default=90, help="Timeout per capture in seconds")
    parser.add_argument("--workers", type=int, default=2, choices=(1, 2, 3))
    parser.add_argument("--keep-data", action="store_true", help="Also retain temporary app databases and fixture images")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Run on the designated Linux test-env, not the local workstation or physical Kindle")
    if min(args.start_ms, args.settle_ms, args.timeout) < 1:
        parser.error("Timing values must be positive")
    args.binary = args.binary.resolve()
    if not args.binary.is_file() or not os.access(args.binary, os.X_OK):
        parser.error("--binary must be an executable desktop application")
    if not os.environ.get("DISPLAY") and not shutil.which("xvfb-run"):
        parser.error("Provide DISPLAY or install xvfb-run on test-env")
    profiles = list(dict.fromkeys(args.device or ("scribe", "pw5")))
    output = args.output_dir.resolve()
    try:
        for profile in profiles:
            (output / profile).mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error("Use a fresh output directory for each capture run")

    def run(profile):
        target = output / profile
        if args.keep_data:
            (target / "data").mkdir()
            return verify_profile(args, profile, target, target / "data")
        with tempfile.TemporaryDirectory(prefix=f"aidoku-handoff-reader-{profile}-") as directory:
            return verify_profile(args, profile, target, Path(directory))

    with ThreadPoolExecutor(max_workers=min(len(profiles), args.workers)) as executor:
        results = dict(zip(profiles, executor.map(run, profiles)))
    write_json(output / "report.json", results)
    passed = all(not case["failures"] for cases in results.values() for case in cases)
    print(f"Reader handoff {'passed' if passed else 'failed'}: {output / 'report.json'}", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
