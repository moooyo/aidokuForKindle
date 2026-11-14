#!/usr/bin/env python3
"""Build a traceable screenshot review package on the designated remote worker."""

from __future__ import annotations

import argparse
import base64
from datetime import datetime
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
from zoneinfo import ZoneInfo

from PIL import Image, ImageChops, ImageDraw, ImageFont


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_module(path):
    spec = importlib.util.spec_from_file_location("baseline_capture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build(args):
    root = args.workspace.resolve()
    tools = root / "tools"
    capture = load_module(tools / "capture-baseline.py")
    labels = json.loads((tools / "labels.zh-CN.json").read_text())
    strings = json.loads((tools / "board-strings.zh-CN.json").read_text())
    source = json.loads((root / "output/source-comparison.json").read_text())
    if digest(root / "aidoku") != source["binary_sha256"]:
        raise RuntimeError("The isolated executable no longer matches the captured source binding.")
    delivery = root / "review"
    delivery.mkdir(exist_ok=False)
    evidence = delivery / "evidence"
    evidence.mkdir()
    records, all_checks, corrections = [], [], []
    for label in labels:
        record = dict(label)
        record["devices"] = {}
        for device in ("scribe", "pw5"):
            folder = root / "output/captures" / device
            scene_id = label["id"]
            scene = next(item for item in capture.scenes_for(device) if item["id"] == scene_id)
            state = json.loads((folder / (scene_id + ".state.json")).read_text())
            metadata = json.loads((folder / (scene_id + ".metadata.json")).read_text())
            previous = json.loads((folder / (scene_id + ".checks.json")).read_text())
            checks = []
            for check in previous["checks"]:
                if check["description"].startswith("Application state reader_menu equals"):
                    corrections.append({"device":device,"scene":scene_id,
                                        "previous_field":"reader_menu","actual_field":"menu",
                                        "reason":"The original state sidecar names this field menu."})
                    continue
                checks.append(check)
            for key, expected in scene["expect"].items():
                checks.append({"description":f"Final state {key} equals {expected!r}",
                               "passed":state.get(key) == expected,"actual":state.get(key)})
            if scene.get("bookmarks"):
                checks.append({"description":"The newly added page 13 bookmark is visible",
                               "passed":any(row["id"].endswith("|13") for row in state.get("detail_bookmarks", []))})
            png = folder / (scene_id + ".png")
            png_hash = digest(png)
            checks.append({"description":"The original captured PNG bytes are unchanged",
                           "passed":png_hash == previous["png_sha256"],"actual":png_hash})
            with Image.open(png) as image:
                width, height = image.size
                checks.append({"description":"Original image dimensions match application metadata",
                               "passed":[width,height] == metadata["snapshot_size"] == metadata["window_size"]})
            failures = [check for check in checks if not check["passed"]]
            all_checks.append({"device":device,"scene":scene_id,"passed":not failures,"checks":checks})
            if failures:
                raise RuntimeError(json.dumps({"device":device,"scene":scene_id,"failures":failures}))
            raw = delivery / "raw" / device
            replay = delivery / "replay" / device
            raw.mkdir(parents=True, exist_ok=True)
            replay.mkdir(parents=True, exist_ok=True)
            for suffix in (".png", ".state.json", ".metadata.json"):
                shutil.copy2(folder / (scene_id + suffix), raw / (scene_id + suffix))
            action_path = folder / "actions" / (scene_id + ".json")
            shutil.copy2(action_path, replay / (scene_id + ".json"))
            actions = json.loads(action_path.read_text())
            record["devices"][device] = {
                "png":f"raw/{device}/{scene_id}.png", "width":width, "height":height,
                "route":state["route"], "backend":metadata["backend"], "scale_factor":metadata["scale_factor"],
                "sha256":png_hash, "actions":f"replay/{device}/{scene_id}.json",
                "pointer_events":sum(event["section"] == "pointer" for event in actions),
                "capture_time":datetime.fromtimestamp(png.stat().st_mtime, ZoneInfo("Asia/Shanghai")).isoformat(),
            }
        records.append(record)

    manifest = {"source_commit":source["source_commit"], "binary_sha256":source["binary_sha256"],
                "capture_environment":"test-env", "capture_kind":"desktop software rendering",
                "physical_device_used":False, "screens_modified":False,
                "native_scanout_captured":False, "fixture":"built-in offline demo", "scenes":records}
    save_json(delivery / "manifest.json", manifest)
    save_json(evidence / "capture-checks.json", all_checks)
    shutil.copy2(root / "output/source-comparison.json", evidence / "source-comparison.json")
    shutil.copy2(root / "output/source-files.json", evidence / "source-files.json")

    font_bytes = {}
    for weight in ("Regular", "Medium", "Bold"):
        font_bytes[weight] = gzip.decompress((root / "source/assets/fonts" / f"NotoSansSC-{weight}.otf.gz").read_bytes())
    font_cache = {}

    def font(size, weight="Regular"):
        key = size, weight
        if key not in font_cache:
            font_cache[key] = ImageFont.truetype(io.BytesIO(font_bytes[weight]), size)
        return font_cache[key]

    background, ink, secondary = (244,243,239), (34,37,32), (96,103,93)
    board_checks = []
    comparisons = delivery / "comparisons"
    comparisons.mkdir()
    for scene_id in ("library", "detail", "reader-controls", "settings"):
        row = next(item for item in records if item["id"] == scene_id)
        board = Image.new("RGB", (2040,1740), background)
        draw = ImageDraw.Draw(board)
        draw.text((96,58), strings["eyebrow"], font=font(23,"Medium"), fill=secondary)
        draw.text((96,102), row["title"] + " · " + strings["comparison_suffix"], font=font(47,"Medium"), fill=ink)
        draw.text((96,178), strings["subtitle"], font=font(23), fill=secondary)
        draw.text((96,222), "Kindle Scribe  /  930 × 1240", font=font(26,"Medium"), fill=ink)
        draw.text((1284,584), "Paperwhite 5", font=font(30,"Medium"), fill=ink)
        draw.text((1284,626), "618 × 824", font=font(23), fill=secondary)
        for device, position, bezel in [
            ("scribe", (186,300), (90,34,32,40)),
            ("pw5", (1320,706), (36,46,36,64)),
        ]:
            original = Image.open(delivery / row["devices"][device]["png"]).convert("RGB")
            x,y = position
            left,top,right,bottom = bezel
            shell = (x-left,y-top,x+original.width+right,y+original.height+bottom)
            draw.rounded_rectangle((shell[0]+5,shell[1]+7,shell[2]+5,shell[3]+7),radius=22,fill=(224,225,219))
            draw.rounded_rectangle(shell,radius=22,fill=(39,42,40),outline=(70,75,69),width=2)
            board.paste(original, position)
            bounds = (x,y,x+original.width,y+original.height)
            preserved = ImageChops.difference(board.crop(bounds),original).getbbox() is None
            if not preserved:
                raise RuntimeError("The comparison board changed a screenshot pixel.")
            board_checks.append({"scene":scene_id,"device":device,"screen_bounds":list(bounds),
                                 "source_png":row["devices"][device]["png"],"all_screen_pixels_preserved":True})
        draw.text((96,1650),strings["pixel_note"],font=font(23),fill=secondary)
        draw.text((96,1689),strings["source_note"],font=font(20),fill=secondary)
        board.save(comparisons / (scene_id + ".png"), optimize=True)
    shutil.copy2(comparisons / "library.png", delivery / "overview.png")

    contacts = delivery / "contact-sheets"
    contacts.mkdir()
    for device, name in (("scribe","Kindle Scribe"),("pw5","Paperwhite 5")):
        rows = (len(records) + 3) // 4
        sheet = Image.new("RGB", (1536,210 + rows*510), background)
        draw = ImageDraw.Draw(sheet)
        draw.text((48,40),name + " · " + strings["contact_suffix"],font=font(39,"Medium"),fill=ink)
        draw.text((48,102),strings["contact_note"],font=font(21),fill=secondary)
        draw.text((48,137),strings["source_note"],font=font(18),fill=secondary)
        for index, row in enumerate(records):
            x,y = 48+(index%4)*366, 200+(index//4)*510
            draw.text((x,y),row["code"] + "  " + row["title"],font=font(18,"Medium"),fill=ink)
            original = Image.open(delivery / row["devices"][device]["png"]).convert("RGB")
            original.thumbnail((330,440),Image.Resampling.LANCZOS)
            sheet.paste(original,(x,y+35))
            draw.rectangle((x-1,y+34,x+original.width,y+35+original.height),outline=(186,191,181),width=1)
        sheet.save(contacts / (device + ".png"),optimize=True)

    template = (tools / "viewer.template.html").read_text(encoding="utf-8")
    viewer = template.replace("__BASELINE_DATA__",json.dumps(manifest,ensure_ascii=True).replace("</","<\\/"))
    (delivery / "index.html").write_text(viewer,encoding="utf-8")
    inline_data = {}
    inline_checks = []
    for scene_id in ("library", "detail", "reader-controls", "settings"):
        row = next(item for item in records if item["id"] == scene_id)
        inline_data[scene_id] = {"title":row["title"]}
        for device in ("scribe","pw5"):
            original = Image.open(delivery / row["devices"][device]["png"]).convert("RGB")
            stream = io.BytesIO()
            original.save(stream,format="WEBP",lossless=True,method=6)
            decoded = Image.open(io.BytesIO(stream.getvalue())).convert("RGB")
            same = decoded.tobytes() == original.tobytes()
            if not same:
                raise RuntimeError("The embedded lossless image differs from the original PNG.")
            inline_checks.append({"scene":scene_id,"device":device,"lossless_pixel_match":same})
            inline_data[scene_id][device] = "data:image/webp;base64," + base64.b64encode(stream.getvalue()).decode("ascii")
    inline_template = (tools / "inline.template.html").read_text(encoding="utf-8")
    inline = inline_template.replace("__INLINE_DATA__",json.dumps(inline_data,ensure_ascii=True))
    inline_path = root / "kindle-ui-baseline.html"
    inline_path.write_text(inline,encoding="utf-8")
    if inline_path.stat().st_size >= 1_000_000:
        raise RuntimeError("The inline fragment exceeds its one-megabyte limit.")

    verification = {"passed":True,"environment":"test-env","source_commit":source["source_commit"],
                   "source_files_compared":source["compared_files"],"different_source_files":source["different_files"],
                   "binary_sha256_before_and_after":source["binary_sha256"],
                   "scenario_count":len(records)*2,"check_count":sum(len(row["checks"]) for row in all_checks),
                   "pointer_event_count":sum(entry["pointer_events"] for row in records for entry in row["devices"].values()),
                   "physical_device_used":False,"raw_pngs_unchanged":True,"composite_pixel_checks":board_checks,
                   "inline_pixel_checks":inline_checks,"inline_fragment_bytes":inline_path.stat().st_size,
                   "harness_field_corrections":corrections,
                   "initial_diagnostics_preserved":True,
                   "bookmark_replay":"The initial fixture already had a page 12 bookmark. The corrected replay turns to page 13 before adding a new bookmark."}
    save_json(evidence / "verification.json",verification)
    reproduced = delivery / "tools"
    reproduced.mkdir()
    for name in ("capture-baseline.py","verify-ui.py","build-delivery.py","labels.zh-CN.json","board-strings.zh-CN.json","viewer.template.html","inline.template.html"):
        shutil.copy2(tools/name,reproduced/name)
    readme = """# Kindle UI/UX baseline

Source: `433a1f6ba6478bc82abe21163aeff345d4acd621`.

Open `index.html` to compare 31 captured states for Kindle Scribe and Paperwhite 5.
The viewer supports scene selection, proportional or equal-width comparison,
optional illustrative device frames, and original-image inspection at 100%.

All 62 PNGs in `raw/` are unchanged outputs of the actual desktop application,
executed on `test-env` with the built-in offline demo fixture. Portrait geometry
is 930 x 1240 for Scribe and 618 x 824 for Paperwhite 5. Landscape swaps the axes.
The desktop backend enforces a scale factor of one. Native scanout, physical
panel response, ghosting, illumination, and touch hardware were not captured.
The status bar contains simulator values. No physical Kindle was accessed.

`comparisons/` embeds original screenshots at exactly one image pixel per output
pixel. The external device frames are illustrative, not measured hardware CAD.
`contact-sheets/` contains downsampled overview images. Original PNGs, not contact
sheets, are the reference for subsequent changes. `replay/` contains the actual
timed pointer and application actions. Every PNG retains application-generated
state and geometry sidecars. `manifest.json` binds paths, dimensions and hashes.

`evidence/verification.json` records source identity, capture checks and pixel
preservation. The initial harness used `reader_menu` instead of the actual `menu`
state field; saved state was rechecked with the correct field without modifying
image or state files. The bookmark replay was rerun after advancing to page 13,
because the initial fixture already contained a page 12 bookmark. Initial
diagnostics remain preserved in the task's isolated worker workspace.

All capture, image production and verification ran on `test-env`. The Windows
workstation was used only for editing, file transfers and viewing the results.
The production application and repository sources were not modified.

## Repeat the capture

Use the matching desktop executable and run `tools/capture-baseline.py` through
`ssh test-env`, supplying `--binary` and a new `--output-dir`. The helper imports
the adjacent project `verify-ui.py`. Each scene uses a fresh offline demo data
directory. Keep Cargo concurrency at six if a new executable must be built.
"""
    (delivery / "README.md").write_text(readme,encoding="utf-8")
    print(json.dumps({"delivery":str(delivery),"screenshots":len(records)*2,
                      "checks":verification["check_count"],"inline_bytes":inline_path.stat().st_size}),flush=True)


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--workspace",type=Path,required=True)
    build(parser.parse_args())
