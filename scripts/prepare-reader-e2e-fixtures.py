#!/usr/bin/env python3
"""Prepare isolated Reader timing fixtures through normal application entry points.

Run only on the designated remote Linux worker. All imports, seeding, preference
changes, captures and snapshot restoration happen outside the measured process.
Each workload reuses its own absolute active path and immutable source identities
while mutable state is reset. Native operators seed at their own device path.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import zipfile


MARKER = "aidoku-reader-e2e-fixtures-v1"
BASELINE = "ffb341741930fea525b96474a81afe56be25ab17"
LONG_SIZE = (1200, 18000)
MAX_ORDINARY_PIXELS = 16 * 1024 * 1024


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def save_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inventory(directory: Path) -> list[dict]:
    result = []
    for path in sorted(directory.rglob("*")):
        require(not path.is_symlink(), "Fixture inputs cannot contain symbolic links")
        if path.is_file():
            result.append({"path": str(path.relative_to(directory)), "bytes": path.stat().st_size, "sha256": sha256(path)})
    return result


def owned_paths(root: Path, snapshot: Path, active: Path) -> tuple[Path, Path, Path]:
    root = root.resolve(strict=True)
    marker = root / ".reader-e2e-fixtures"
    require(marker.is_file() and marker.read_text(encoding="utf-8").strip() == MARKER, "The fixture root has no matching ownership marker")
    expected_active = root / "active"
    require(not active.is_symlink() and active.resolve() == expected_active, "Only this fixture root's exact active directory may be replaced")
    snapshot = snapshot.resolve(strict=True)
    require(snapshot.is_dir() and snapshot.is_relative_to(root / "snapshots"), "The snapshot must belong to this fixture root")
    require(snapshot != expected_active and expected_active not in snapshot.parents, "The active directory cannot be its own snapshot")
    inventory(snapshot)
    return root, snapshot, expected_active


def source_identity(path: Path, active: Path) -> dict:
    require(not path.is_symlink() and path.resolve().is_relative_to(active.resolve()), "An immutable source escapes its active fixture")
    before = path.stat()
    checksum = sha256(path)
    after = path.stat()
    fields = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    require(fields(before) == fields(after), "An immutable source changed while its identity was recorded")
    return {"path": path.relative_to(active).as_posix(), "device": after.st_dev, "inode": after.st_ino, "bytes": after.st_size, "mtime_ns": after.st_mtime_ns, "ctime_ns": after.st_ctime_ns, "sha256": checksum}


def remember_sources(root: Path) -> dict:
    active = root / "active"
    sources = set()
    for database in active.rglob("library.sqlite3"):
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            for (document,) in connection.execute("SELECT document FROM pages"):
                path = json.loads(document).get("local_path")
                if path:
                    sources.add(Path(path))
    require(bool(sources), "A prepared Reader fixture has no actual local source files")
    receipt = {"schema_version": 1, "active_path": str(active), "files": [source_identity(path, active) for path in sorted(sources)]}
    path = root / "immutable-sources.json"
    require(not path.exists(), "Preserve the original source-identity receipt")
    save_json(path, receipt)
    return receipt


def verify_sources(root: Path, snapshot: Path, active: Path, required: bool) -> int:
    path = root / "immutable-sources.json"
    if not path.exists():
        require(not required, "The required immutable-source receipt is missing")
        return 0
    receipt = json.loads(path.read_text(encoding="utf-8"))
    require(receipt.get("schema_version") == 1 and receipt.get("active_path") == str(active), "The source receipt belongs to a different active fixture")
    records = receipt.get("files")
    require(isinstance(records, list) and bool(records), "The immutable-source inventory is empty")
    for record in records:
        relative = Path(record["path"])
        require(not relative.is_absolute() and ".." not in relative.parts, "An immutable-source path is invalid")
        require(source_identity(active / relative, active) == record, "An original source identity or content changed; do not repair it into a fake warm hit")
        require((snapshot / relative).is_file() and sha256(snapshot / relative) == record["sha256"], "The snapshot does not contain its original immutable bytes")
    return len(records)


def restore(root: Path, snapshot: Path, active: Path, require_source_identity: bool = False) -> dict:
    root, snapshot, active = owned_paths(root, snapshot, active)
    if active.exists():
        require(active.is_dir(), "The owned active path must be a directory")
        inventory(active)
    originals = verify_sources(root, snapshot, active, require_source_identity)
    desired = {path.relative_to(snapshot) for path in snapshot.rglob("*") if path.is_file()}
    if active.exists():
        for path in sorted(active.rglob("*"), key=lambda value: len(value.parts), reverse=True):
            relative = path.relative_to(active)
            if path.is_file() and relative not in desired:
                path.unlink()
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
    active.mkdir(exist_ok=True)
    unchanged = 0
    for relative in sorted(desired):
        source, target = snapshot / relative, active / relative
        if target.is_file() and target.stat().st_size == source.stat().st_size and sha256(target) == sha256(source):
            # Even copy2/utime on equal bytes changes ctime and the local page key.
            unchanged += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    require(verify_sources(root, snapshot, active, require_source_identity) == originals, "Source identity changed during mutable-state restoration")
    return {"restored": True, "snapshot": str(snapshot), "active": str(active), "files": len(inventory(active)), "unchanged_files_not_rewritten": unchanged, "immutable_source_count": originals, "source_identity_preserved": bool(originals)}


def actions(path: Path, entries: list[tuple[int, str, str, str]]) -> Path:
    save_json(path, [{"at_ms": at, "section": section, "action": action, "value": value} for at, section, action, value in entries])
    return path


def create_long_archive(path: Path) -> dict:
    from PIL import Image, ImageDraw

    width, height = LONG_SIZE
    images = []
    with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for page in range(3):
            image = Image.new("L", LONG_SIZE, 255)
            drawing = ImageDraw.Draw(image)
            drawing.rectangle((10, 10, width - 11, height - 11), outline=0, width=6)
            for band, top in enumerate(range(48, height - 48, 160)):
                level = 24 + ((band * 37 + page * 71) % 192)
                inset = 36 + ((band * 29 + page * 43) % 170)
                drawing.rectangle((inset, top, width - inset - 1, top + 110), fill=level)
                drawing.line((36, top + 125, width - 37, top + 125), fill=0, width=3)
                drawing.text((44, top + 8), f"PAGE {page + 1} BAND {band:03}", fill=0)
            encoded = io.BytesIO()
            image.save(encoded, format="PNG", optimize=False, compress_level=6, interlace=0)
            payload = encoded.getvalue()
            name = f"long-{page + 1:02}.png"
            archive.writestr(name, payload)
            images.append({"name": name, "size": list(LONG_SIZE), "pixels": width * height, "png_bytes": len(payload), "png_sha256": hashlib.sha256(payload).hexdigest(), "luma_sha256": hashlib.sha256(image.tobytes()).hexdigest()})
        archive.writestr("ComicInfo.xml", "<ComicInfo><Series>Reader timing strip fixture</Series><Title>Streamed windows</Title><Number>1</Number></ComicInfo>")
    return {"archive": str(path), "sha256": sha256(path), "images": images, "expected_decoder": "noninterlaced PNG row decoder", "decoder_evidence": f"{width * height} pixels exceeds MAX_PAGE_PIXELS={MAX_ORDINARY_PIXELS}; source dimensions remain inside stream bounds", "scope": "Three synthetic grayscale strips with distinct horizontal bands; not representative of all comic content."}


def prepared_pngs(active: Path) -> list[dict]:
    from PIL import Image

    result = []
    for path in sorted(active.rglob("*.png")):
        if "processed" not in path.relative_to(active).parts:
            continue
        with Image.open(path) as image:
            pixels = image.convert("L")
            result.append({"path": str(path.relative_to(active)), "size": list(pixels.size), "png_sha256": sha256(path), "luma_sha256": hashlib.sha256(pixels.tobytes()).hexdigest()})
    return result


def preparation_runner():
    path = Path(__file__).with_name("benchmark-reader-e2e.py")
    specification = importlib.util.spec_from_file_location("reader_e2e_preparation_process", path)
    require(specification is not None and specification.loader is not None, "The adjacent bounded process runner could not be loaded")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module.run_process


def capture_process(binary: Path, root: Path, name: str, *, demo: bool, resume: bool, screen: str, replay: Path | None = None, import_path: Path | None = None, duration: int = 5500) -> dict:
    active = root / "active"
    capture = root / "preparation" / f"{name}.png"
    command = ["xvfb-run", "-a", "-s", "-screen 0 2200x3000x24", str(binary), "--device", "scribe", "--offline", "--data-dir", str(active), "--screen", screen, "--capture", str(capture), "--exit-after-ms", str(duration)]
    if demo:
        command.append("--demo")
    if resume:
        command.append("--resume")
    if replay:
        command.extend(["--actions", str(replay)])
    if import_path:
        command.extend(["--import", str(import_path)])
    environment = os.environ.copy()
    environment.update({"SLINT_SCALE_FACTOR": "1", "SLINT_BACKEND": "winit-software", "SLINT_RENDERER": "software", "XDG_RUNTIME_DIR": str(root / "runtime"), "TZ": "Asia/Shanghai"})
    process = preparation_runner()(command, capture.parent / f"{name}-process", 90, root, environment)
    require(process.get("succeeded") is True, f"Fixture preparation failed: {name}; retain its process record")
    state = json.loads(capture.with_suffix(".state.json").read_text(encoding="utf-8"))
    metadata = json.loads(capture.with_suffix(".metadata.json").read_text(encoding="utf-8"))
    if screen == "reader":
        require(state.get("route") == "reader" and state.get("reader_image_ready") is True, f"Fixture Reader never became ready: {name}")
        require(state.get("busy") is False and not state.get("reader_recovery"), f"Fixture Reader retained incomplete work: {name}")
        require(state.get("visible_reader_page") == state.get("committed_reader_page"), f"Fixture Reader progress does not match the displayed image: {name}")
    return {"name": name, "command": command, "exit_code": process["exit_code"], "process": process, "capture": str(capture), "state": state, "metadata": metadata}


def snapshot(root: Path, name: str) -> dict:
    source = root / "active"
    target = root / "snapshots" / name
    require(not target.exists(), "Preserve existing snapshots; choose a fresh fixture root")
    shutil.copytree(source, target, copy_function=shutil.copy2)
    return {"name": name, "path": str(target), "files": inventory(target), "prepared_pngs": prepared_pngs(target)}


def create(arguments) -> dict:
    require(sys.platform.startswith("linux"), "Fixture preparation belongs on the designated Linux verification worker")
    root = arguments.output_dir.resolve()
    require(not root.exists(), "Preserve prior fixture evidence; choose a fresh output directory")
    require(arguments.samples >= 21, "At least 21 fresh-process startup pairs are required")
    root.mkdir(parents=True)
    for name in ("workloads", "actions", "inputs", "runtime"):
        (root / name).mkdir()
    (root / "runtime").chmod(0o700)
    binary = arguments.candidate_binary.resolve(strict=True)
    helper = Path(__file__).resolve()
    preparations, snapshots, contexts = [], [], {}

    def context(name):
        child = root / "workloads" / name
        child.mkdir()
        (child / ".reader-e2e-fixtures").write_text(MARKER + "\n", encoding="utf-8")
        for directory in ("active", "snapshots", "preparation", "actions", "inputs", "runtime"):
            (child / directory).mkdir()
        (child / "runtime").chmod(0o700)
        contexts[name] = child
        return child

    for name in ("cached-startup", "cold-render-startup", "cached-turns", "chapter-boundary"):
        child = context(name)
        target_page = 40 if name == "chapter-boundary" else 1
        replay = actions(child / "actions" / "seed.json", [
            (400, "settings", "set", "page-layout-mode=single"),
            (600, "settings", "set", "split-wide=false"),
            (800, "settings", "set", "reading-direction=rtl"),
            (1200, "reader", "seek", str(target_page)),
        ])
        prepared = capture_process(binary, child, "seed", demo=True, resume=False, screen="reader", replay=replay)
        preparations.append(prepared)
        require(prepared["state"].get("reader_page") == target_page, "The normal seed did not display its requested source page")
        require(bool(prepared_pngs(child / "active")), "The normal seed generated no processed PNG")
        if name == "cold-render-startup":
            processed = child / "active/demo/cache/shared-v1/processed"
            require(processed.is_dir() and processed.resolve().is_relative_to(child / "active"), "The expected isolated processed cache is missing")
            shutil.rmtree(processed)
            processed.mkdir()
        originals = remember_sources(child)
        saved = snapshot(child, "seed")
        saved.update(workload=name, active_path=str(child / "active"), immutable_sources=originals)
        snapshots.append(saved)
        verify_sources(child, child / "snapshots/seed", child / "active", True)

    child = context("streamed-vertical-windows")
    strip_archive = root / "inputs/long-strip.cbz"
    strips = create_long_archive(strip_archive)
    preparations.append(capture_process(binary, child, "import", demo=False, resume=False, screen="library", import_path=strip_archive, duration=3500))
    vertical = actions(child / "actions/seed.json", [
        (400, "settings", "set", "page-layout-mode=single"),
        (600, "settings", "set", "split-wide=false"),
        (900, "settings", "set", "reading-direction=vertical"),
        (1400, "reader", "seek", "1"),
    ])
    prepared = capture_process(binary, child, "vertical", demo=False, resume=False, screen="reader", replay=vertical, duration=7000)
    preparations.append(prepared)
    state = prepared["state"]
    require(state.get("reading_direction") == "vertical", "The strip fixture did not enable the normal vertical mode")
    require(state.get("reader_image_height", 0) > state.get("reader_viewport_height", 0) > 0, "The imported strip has no scrollable vertical windows")
    originals = remember_sources(child)
    saved = snapshot(child, "seed")
    saved.update(workload="streamed-vertical-windows", active_path=str(child / "active"), immutable_sources=originals)
    snapshots.append(saved)
    verify_sources(child, child / "snapshots/seed", child / "active", True)
    long_turns = actions(root / "actions/turns.json", [(2000 + index * 700, "reader", "next", "") for index in range(21)])
    boundary_turns = actions(root / "actions/boundary-turns.json", [(2000 + index * 700, "reader", "next" if index % 2 == 0 else "previous", "") for index in range(21)])

    def workload(name, demo, replay_path=None, samples=None, boundary_actions=False):
        child = contexts[name]
        active = child / "active"
        command = ["xvfb-run", "-a", "-s", "-screen 0 2200x3000x24", "{binary}", "--device", "scribe", "--offline", "--resume", "--data-dir", str(active), "--screen", "reader", "--performance-trace", "{trace}", "--exit-after-ms", "18500" if replay_path else "2000"]
        if demo:
            command.append("--demo")
        if replay_path:
            command.extend(["--actions", str(replay_path)])
        cache_states = {"cold": 1} if name == "cold-render-startup" else {"disk": 1}
        if replay_path:
            cache_states["memory"] = 21
        expectation = {"scope": "desktop", "require_startup": True, "minimum_ui_requests": 22 if replay_path else 1, "required_cache_states": cache_states}
        if replay_path:
            expectation["required_successful_actions"] = {"next": 11, "previous": 10} if boundary_actions else {"next": 21}
        result = {
            "id": name,
            "prepare": [[sys.executable, str(helper), "restore", "--root", str(child), "--snapshot", str(child / "snapshots/seed"), "--active", str(active), "--require-source-identity"]],
            "command": command,
            "fixtures": [{"name": "active", "path": str(active)}, {"name": "source_identity", "path": str(child / "immutable-sources.json")}],
            "expectation": expectation,
            "cwd": str(child),
            "timeout_seconds": 75,
        }
        if samples is not None:
            result["samples"] = samples
        return result

    limits = [
        "Fresh processes use warm OS page caches; no machine cache drop is performed.",
        "The demo uses two alternating bundled pages and does not model broad image diversity.",
        "Each workload owns one fixed active path shared by its baseline and candidate trials.",
        "Original source bytes, device/inode and nanosecond mtime/ctime are verified before and after every mutable-state restore.",
        "Byte-identical files are never copied or touched during restore. Recreating local sources would correctly invalidate their processed cache keys.",
        "Warm PNGs are prepared against these final stable source identities; the cold-render workload removes only disposable processed files.",
        "Long strips exceed the production ordinary-pixel limit and select the existing noninterlaced PNG row decoder.",
        "Long-turn metrics come from one process per implementation and contain correlated actions; startup metrics use independent fresh processes.",
        "This host plan measures UI acceptance only, not a physical framebuffer or optical panel completion.",
    ]
    plan = {
        "schema_version": 1, "scope": "desktop", "samples": arguments.samples,
        "environment": {"SLINT_SCALE_FACTOR": "1", "SLINT_BACKEND": "winit-software", "SLINT_RENDERER": "software", "XDG_RUNTIME_DIR": str(root / "runtime"), "TZ": "Asia/Shanghai"},
        "control": {
            "baseline_commit": BASELINE,
            "baseline_observation_patch": str(arguments.baseline_observation_patch.resolve()),
            "baseline_review_receipt": str(arguments.baseline_review_receipt.resolve()),
            "shared_observation_files": [{"name": name, "baseline": str(arguments.baseline_source.resolve() / name), "candidate": str(arguments.candidate_source.resolve() / name)} for name in ("src/reader_timing.rs", "vendor/slint-backend-kindle/src/frame_observer.rs")],
        },
        "implementations": {"baseline": {"binary": str(arguments.baseline_binary.resolve())}, "candidate": {"binary": str(binary)}},
        "workloads": [
            workload("cached-startup", True), workload("cold-render-startup", True),
            workload("cached-turns", True, long_turns, 1),
            workload("chapter-boundary", True, boundary_turns, 1, True),
            workload("streamed-vertical-windows", False, long_turns, 1),
        ],
        "limits": limits,
    }
    receipt = {"schema_version": 1, "creation_binary": {"path": str(binary), "sha256": sha256(binary)}, "tools": {path.name: sha256(path) for path in (helper, helper.with_name("benchmark-reader-e2e.py"))}, "active_paths": {name: str(child / "active") for name, child in contexts.items()}, "preparation": preparations, "snapshots": snapshots, "long_strips": strips, "plan": str(root / "plan.json"), "limits": limits}
    save_json(root / "fixtures.json", receipt)
    save_json(root / "plan.json", plan)
    return {"prepared": True, "root": str(root), "plan": str(root / "plan.json"), "snapshot_count": len(snapshots), "startup_processes_per_implementation": arguments.samples * 2, "long_action_processes_per_implementation": 3}

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    prepare = modes.add_parser("create")
    for name in ("candidate-binary", "baseline-binary", "candidate-source", "baseline-source", "baseline-observation-patch", "baseline-review-receipt", "output-dir"):
        prepare.add_argument("--" + name, type=Path, required=True)
    prepare.add_argument("--samples", type=int, default=21)
    reset = modes.add_parser("restore")
    for name in ("root", "snapshot", "active"):
        reset.add_argument("--" + name, type=Path, required=True)
    reset.add_argument("--require-source-identity", action="store_true", help="Reject changed or recreated original sources instead of repairing cache identity")
    arguments = parser.parse_args()
    try:
        result = create(arguments) if arguments.mode == "create" else restore(arguments.root, arguments.snapshot, arguments.active, arguments.require_source_identity)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"reader-e2e-fixtures: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
