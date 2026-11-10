#!/usr/bin/env python3
"""Prepare isolated Reader timing fixtures through normal application entry points.

Run only on the designated remote Linux worker. All imports, seeding, preference
changes, captures and snapshot restoration happen outside the measured process.
The generated host plan reuses one absolute active path so persisted local paths
remain valid. Native operators must import or seed at their own device path.
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


def restore(root: Path, snapshot: Path, active: Path) -> dict:
    _, snapshot, active = owned_paths(root, snapshot, active)
    if active.exists():
        require(active.is_dir(), "The owned active path must be a directory")
        shutil.rmtree(active)
    shutil.copytree(snapshot, active, copy_function=shutil.copy2)
    return {"restored": True, "snapshot": str(snapshot), "active": str(active), "files": len(inventory(active))}


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
    (root / ".reader-e2e-fixtures").write_text(MARKER + "\n", encoding="utf-8")
    for name in ("active", "snapshots", "preparation", "actions", "inputs", "runtime"):
        (root / name).mkdir()
    (root / "runtime").chmod(0o700)
    binary = arguments.candidate_binary.resolve(strict=True)
    helper = Path(__file__).resolve()
    replay = actions(root / "actions" / "seed-single.json", [(400, "settings", "set", "page-layout-mode=single"), (600, "settings", "set", "split-wide=false"), (800, "settings", "set", "reading-direction=rtl"), (1200, "reader", "seek", "1")])
    preparations = [capture_process(binary, root, "demo-warm", demo=True, resume=False, screen="reader", replay=replay)]
    require(preparations[-1]["state"].get("reader_page") == 1, "The normal seed did not seek to page one")
    snapshots = [snapshot(root, "demo-warm")]
    require(bool(snapshots[-1]["prepared_pngs"]), "The warm seed has no processed PNG")
    processed = root / "active" / "demo" / "cache" / "shared-v1" / "processed"
    require(processed.is_dir() and processed.resolve().is_relative_to(root / "active"), "The expected isolated processed cache is missing")
    shutil.rmtree(processed)
    processed.mkdir()
    snapshots.append(snapshot(root, "demo-cold-render"))
    restore(root, root / "snapshots" / "demo-warm", root / "active")
    boundary = actions(root / "actions" / "seed-boundary.json", [(700, "reader", "seek", "40")])
    preparations.append(capture_process(binary, root, "demo-boundary", demo=True, resume=True, screen="reader", replay=boundary))
    require(preparations[-1]["state"].get("reader_page") == 40, "The boundary seed did not display page forty")
    snapshots.append(snapshot(root, "demo-boundary"))
    shutil.rmtree(root / "active")
    (root / "active").mkdir()
    strip_archive = root / "inputs" / "long-strip.cbz"
    strips = create_long_archive(strip_archive)
    preparations.append(capture_process(binary, root, "strip-import", demo=False, resume=False, screen="library", import_path=strip_archive, duration=3500))
    vertical = actions(root / "actions" / "seed-vertical.json", [(400, "settings", "set", "page-layout-mode=single"), (600, "settings", "set", "split-wide=false"), (900, "settings", "set", "reading-direction=vertical"), (1400, "reader", "seek", "1")])
    preparations.append(capture_process(binary, root, "strip-vertical", demo=False, resume=False, screen="reader", replay=vertical, duration=7000))
    strip_state = preparations[-1]["state"]
    require(strip_state.get("reading_direction") == "vertical", "The strip fixture did not enable the normal vertical mode")
    require(strip_state.get("reader_image_height", 0) > strip_state.get("reader_viewport_height", 0) > 0, "The imported strip has no scrollable vertical windows")
    snapshots.append(snapshot(root, "strip-vertical"))
    long_turns = actions(root / "actions" / "turns.json", [(2000 + index * 700, "reader", "next", "") for index in range(21)])
    boundary_turns = actions(root / "actions" / "boundary-turns.json", [(2000 + index * 700, "reader", "next" if index % 2 == 0 else "previous", "") for index in range(21)])

    def workload(name: str, snapshot_name: str, *, demo: bool, replay_path: Path | None = None, samples: int | None = None, boundary_actions: bool = False) -> dict:
        command = ["xvfb-run", "-a", "-s", "-screen 0 2200x3000x24", "{binary}", "--device", "scribe", "--offline", "--resume", "--data-dir", str(root / "active"), "--screen", "reader", "--performance-trace", "{trace}", "--exit-after-ms", "18500" if replay_path else "2000"]
        if demo:
            command.append("--demo")
        if replay_path:
            command.extend(["--actions", str(replay_path)])
        expectation = {"scope": "desktop", "require_startup": True, "minimum_ui_requests": 22 if replay_path else 1}
        if replay_path:
            expectation["required_successful_actions"] = {"next": 11, "previous": 10} if boundary_actions else {"next": 21}
        result = {"id": name, "prepare": [[sys.executable, str(helper), "restore", "--root", str(root), "--snapshot", str(root / "snapshots" / snapshot_name), "--active", str(root / "active")]], "command": command, "fixtures": [{"name": "active", "path": str(root / "active")}], "expectation": expectation, "cwd": str(root), "timeout_seconds": 75}
        if samples is not None:
            result["samples"] = samples
        return result

    plan = {"schema_version": 1, "scope": "desktop", "samples": arguments.samples, "environment": {"SLINT_SCALE_FACTOR": "1", "SLINT_BACKEND": "winit-software", "SLINT_RENDERER": "software", "XDG_RUNTIME_DIR": str(root / "runtime"), "TZ": "Asia/Shanghai"}, "control": {"baseline_commit": BASELINE, "baseline_observation_patch": str(arguments.baseline_observation_patch.resolve()), "baseline_review_receipt": str(arguments.baseline_review_receipt.resolve()), "shared_observation_files": [{"name": name, "baseline": str(arguments.baseline_source.resolve() / name), "candidate": str(arguments.candidate_source.resolve() / name)} for name in ("src/reader_timing.rs", "vendor/slint-backend-kindle/src/frame_observer.rs")]}, "implementations": {"baseline": {"binary": str(arguments.baseline_binary.resolve())}, "candidate": {"binary": str(binary)}}, "workloads": [workload("cached-startup", "demo-warm", demo=True), workload("cold-render-startup", "demo-cold-render", demo=True), workload("cached-turns", "demo-warm", demo=True, replay_path=long_turns, samples=1), workload("chapter-boundary", "demo-boundary", demo=True, replay_path=boundary_turns, samples=1, boundary_actions=True), workload("streamed-vertical-windows", "strip-vertical", demo=False, replay_path=long_turns, samples=1)], "limits": ["Fresh processes use warm OS page caches; no machine cache drop is performed.", "The demo uses two alternating bundled pages and does not model broad image diversity.", "The cold-render fixture removes only disposable processed PNGs while retaining original images and database state.", "Long strips exceed the production ordinary-pixel limit and select the existing noninterlaced PNG row decoder.", "Long-turn metrics come from one process per implementation and contain correlated actions; startup metrics use independent fresh processes.", "This host plan measures UI acceptance only, not a physical framebuffer or optical panel completion."]}
    receipt = {"schema_version": 1, "creation_binary": {"path": str(binary), "sha256": sha256(binary)}, "tools": {path.name: sha256(path) for path in (helper, helper.with_name("benchmark-reader-e2e.py"))}, "active_path": str(root / "active"), "preparation": preparations, "snapshots": snapshots, "long_strips": strips, "plan": str(root / "plan.json"), "limits": plan["limits"]}
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
    arguments = parser.parse_args()
    try:
        result = create(arguments) if arguments.mode == "create" else restore(arguments.root, arguments.snapshot, arguments.active)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"reader-e2e-fixtures: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
