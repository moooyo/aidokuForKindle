"""Retain exact same-case source chains for the three C15 reader overlays.

The frozen remote harness owns execution, source identity and native captures.
Each overlay has an independent fresh closed-reader prerequisite. This helper
never builds the application, edits application state, or grants manual credit.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import struct
from typing import Any
import zlib

from PIL import Image, ImageChops, ImageDraw


CASE_IDS = ("sc-reader--layout", "sc-reader--light", "sc-reader--mode")
PREREQUISITE_IDS = tuple(
    case_id + "--same-case-closed-reader-prerequisite" for case_id in CASE_IDS
)
PANELS = {
    "sc-reader--layout": ("layout", "choices", "reader-layout", 180.0),
    "sc-reader--light": ("brightness", "brightness", "reader-brightness", 156.0),
    "sc-reader--mode": ("mode", "choices", "reader-mode", 242.5),
}
MAX_PAGE_BYTES = 32 * 1024 * 1024
MAX_PAGE_PIXELS = 16 * 1024 * 1024
GEOMETRY_FIELDS = (
    "profile", "backend", "logical_size", "window_size", "snapshot_size",
    "scale_factor", "landscape", "rotation_degrees",
)
ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _api(api: Any, name: str) -> Any:
    value = api.get(name) if isinstance(api, dict) else getattr(api, name, None)
    _require(value is not None, f"The frozen artwork API is missing {name}")
    return value


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _register(case: dict[str, Any], label: str, path: Path) -> None:
    case.setdefault("evidence_hashes", {})[f"c15-artwork:{label}"] = {
        "path": str(path), "sha256": _file_hash(path), "bytes": path.stat().st_size,
    }


def _retain_json(suite: Any, api: Any, case: dict[str, Any], label: str, payload: Any) -> Path:
    path = Path(suite.output) / "fixtures" / f"{case['name']}--c15-artwork-{label}.json"
    _api(api, "save_json")(path, payload)
    _register(case, label, path)
    return path


def _retain_failure(suite: Any, api: Any, case: dict[str, Any], stage: str,
                    failure: dict[str, Any]) -> None:
    failures = case.setdefault("c15_artwork_failures", [])
    ordinal = len(failures) + 1
    while True:
        label = f"{stage}-failure-{ordinal:02}"
        path = Path(suite.output) / "fixtures" / f"{case['name']}--c15-artwork-{label}.json"
        if not path.exists() and f"c15-artwork:{label}" not in case.get("evidence_hashes", {}):
            break
        ordinal += 1
    retained = _retain_json(suite, api, case, label, failure)
    record = {**failure, "failure_evidence": str(retained),
              "failure_evidence_sha256": case["evidence_hashes"][f"c15-artwork:{label}"]["sha256"]}
    failures.append(record)
    case.setdefault("c15_artwork", record)


def _stat(path: Path) -> dict[str, int]:
    value = path.stat()
    return {
        "bytes": value.st_size, "mtime_ns": max(0, value.st_mtime_ns),
        "device": value.st_dev, "inode": value.st_ino,
        "ctime_seconds": value.st_ctime_ns // 1_000_000_000,
        "ctime_nanoseconds": value.st_ctime_ns % 1_000_000_000,
    }


def _fingerprint(value: dict[str, int]) -> str:
    return ":".join(str(value[key]) for key in (
        "bytes", "mtime_ns", "device", "inode", "ctime_seconds", "ctime_nanoseconds",
    ))


def _read_source(path: Path) -> tuple[bytes, dict[str, int]]:
    _require(path.is_file(), "A retained source is not an actual regular file")
    before = _stat(path)
    _require(0 < before["bytes"] <= MAX_PAGE_BYTES, "A source exceeds the bounded PNG byte contract")
    with path.open("rb") as stream:
        payload = stream.read(MAX_PAGE_BYTES + 1)
    _require(len(payload) == before["bytes"], "A source changed size while being retained")
    _require(_stat(path) == before, "A source changed identity while being retained")
    return payload, before


def _hash_part(digest: Any, payload: bytes) -> None:
    digest.update(len(payload).to_bytes(8, "little"))
    digest.update(payload)


def _page_keys(page: dict[str, Any], position: int, raw: Path,
               raw_stat: dict[str, int]) -> tuple[str, str, str]:
    url, headers = page.get("url"), page.get("headers", {})
    _require(isinstance(url, str) and isinstance(headers, dict), "The Store page request is invalid")
    _require(all(isinstance(key, str) and isinstance(value, str) for key, value in headers.items()),
             "The Store page headers are invalid")
    request = hashlib.sha256()
    _hash_part(request, url.encode("utf-8"))
    canonical_headers = sorted((key.translate(ASCII_LOWER), value) for key, value in headers.items())
    for key, value in canonical_headers:
        _hash_part(request, key.encode("utf-8"))
        _hash_part(request, value.encode("utf-8"))
    if not url:
        _hash_part(request, os.fsencode(raw))
        _hash_part(request, _fingerprint(raw_stat).encode("utf-8"))
    manifest = hashlib.sha256()
    manifest.update(position.to_bytes(8, "little"))
    _hash_part(manifest, url.encode("utf-8"))
    if not url:
        manifest.update(page["local_path"].encode("utf-8"))
    for key, value in sorted(headers.items()):
        _hash_part(manifest, key.encode("utf-8"))
        _hash_part(manifest, value.encode("utf-8"))
    header_hash = _sha256(json.dumps(canonical_headers, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8"))
    return request.hexdigest(), manifest.hexdigest(), header_hash


def _png_structure(payload: bytes) -> tuple[dict[str, int], list[bytes]]:
    _require(payload[:8] == b"\x89PNG\r\n\x1a\n", "Artwork input is not an actual PNG")
    cursor, header, texts, ended = 8, None, [], False
    while cursor + 12 <= len(payload):
        length = struct.unpack_from(">I", payload, cursor)[0]
        kind = payload[cursor + 4:cursor + 8]
        end = cursor + 12 + length
        _require(end <= len(payload), "A PNG chunk exceeds its actual retained bytes")
        data = payload[cursor + 8:cursor + 8 + length]
        crc = struct.unpack_from(">I", payload, cursor + 8 + length)[0]
        _require((zlib.crc32(kind + data) & 0xFFFFFFFF) == crc, "A retained PNG chunk checksum is invalid")
        if kind == b"IHDR":
            _require(header is None and cursor == 8 and length == 13, "PNG geometry is ambiguous")
            width, height, depth, color, compression, filtering, interlaced = struct.unpack(">IIBBBBB", data)
            header = {"width": width, "height": height, "depth": depth, "color": color,
                      "compression": compression, "filtering": filtering, "interlaced": interlaced}
        elif kind == b"tEXt" and data.startswith(b"AidokuRender\0"):
            _require(length <= 2_048 + len(b"AidokuRender\0"), "Processed geometry metadata exceeds its bound")
            texts.append(data.split(b"\0", 1)[1])
        elif kind == b"acTL":
            raise ValueError("The artwork oracle does not substitute an animated PNG decoder")
        elif kind == b"IEND":
            _require(length == 0 and end == len(payload), "PNG completion has trailing or invalid bytes")
            ended = True
            break
        cursor = end
    _require(header is not None and ended, "PNG geometry or completion is missing")
    _require(header["width"] > 0 and header["height"] > 0, "The PNG has no source pixels")
    _require(header["compression"] == 0 and header["filtering"] == 0, "The PNG codec fields are unsupported")
    return header, texts


def _raw_crop(payload: bytes) -> dict[str, Any]:
    header, _ = _png_structure(payload)
    width, height = header["width"], header["height"]
    _require(header["depth"] == 8 and width <= 32_768 and height <= 131_072
             and width * height <= MAX_PAGE_PIXELS,
             "The raw source needs an unsupported decoder; no approximate crop is accepted")
    with Image.open(io.BytesIO(payload)) as original:
        _require(original.format == "PNG" and original.size == (width, height), "Raw decoder geometry differs")
        red, green, blue, alpha = original.convert("RGBA").split()
    _require(ImageChops.difference(red, green).getbbox() is None
             and ImageChops.difference(red, blue).getbbox() is None,
             "The raw source is not monochrome; generic RGB-to-L conversion is not an accepted oracle")
    paper = bytes((value * opacity + 255 * (255 - opacity) + 127) // 255
                  for value, opacity in zip(red.tobytes(), alpha.tobytes()))
    gray = Image.frombytes("L", (width, height), paper)
    bounds = gray.point(lambda value: 255 if value < 247 else 0).getbbox()
    crop = [0, 0, width, height]
    if bounds is not None:
        left = min(max(0, bounds[0] - 2), width // 5)
        top = min(max(0, bounds[1] - 2), height // 5)
        right = min(max(bounds[2] - 1 + 2, width - width // 5 - 1), width - 1)
        bottom = min(max(bounds[3] - 1 + 2, height - height // 5 - 1), height - 1)
        crop = [left, top, right - left + 1, bottom - top + 1]
    return {
        "source_size": [width, height], "raw_crop_xywh": crop,
        "ink_bounds_ltrb": list(bounds) if bounds is not None else None,
        "decoder_contract": "Actual eight-bit monochrome PNG samples and integer paper alpha composition",
        "crop_contract": "imaging::crop_white_borders and bounded_crop; threshold 247, paper edge 2, per-edge cap 1/5",
        "derived_from_actual_raw_bytes": True,
    }


def _geometry_sources(suite: Any) -> list[dict[str, Any]]:
    root = Path(suite.args.source_root).resolve(strict=True)
    frozen = {item["path"]: item["sha256"] for item in suite.source["files"]}
    contracts = (
        ("ui/reader.slint", (
            r"header-height:\s*State\.large-screen\s*\?\s*78px\s*:\s*89\.5px",
            r"bottom-height:\s*State\.large-screen\s*\?\s*210px\s*:\s*188px",
        )),
        ("ui/panels-reader.slint", (
            r'reader-mode"\s*\?\s*\(State\.large-screen\s*\?\s*242\.5px\s*:\s*316\.5px\)',
            r'reader-layout"\s*\?\s*\(State\.large-screen\s*\?\s*180px\s*:\s*163\.5px\)',
            r'reader-brightness"\s*\?\s*\(!State\.has-frontlight\s*\?\s*0px\s*:\s*State\.large-screen\s*\?\s*156px',
            r"height:\s*root\.content-height;\s*background:\s*Theme\.paper;",
        )),
        ("ui/panels.slint", (
            r"if root\.reader-panel:\s*ReaderPanel\s*\{\s*x:\s*0px;\s*y:\s*parent\.height\s*-\s*ReaderMetrics\.bottom-height\s*-\s*self\.height;\s*width:\s*parent\.width;",
            r"export component Panels inherits Rectangle\s*\{\s*x:\s*0px;\s*y:\s*0px;\s*visible:\s*State\.modal\s*!=\s*\"\";\s*background:\s*transparent;",
        )),
    )
    records = []
    for relative, patterns in contracts:
        path = root / relative
        payload = path.read_bytes()
        _require(len(payload) <= 2 * 1024 * 1024 and _sha256(payload) == frozen.get(relative),
                 "Reader mask source differs from the actual frozen production source")
        text = payload.decode("utf-8-sig")
        lines = []
        for pattern in patterns:
            match = re.search(pattern, text)
            _require(match is not None, "Reader geometry changed; the fixed mask must not be expanded")
            lines.append(text.count("\n", 0, match.start()) + 1)
        records.append({"path": str(path), "sha256": _sha256(payload), "contract_lines": lines})
    return records


def _reference_entry(suite: Any, case_id: str) -> dict[str, Any]:
    entries = [entry for board in suite.scope["pageStateMatrix"]
               for entry in [board["default"], *board["states"]] if entry["id"] == case_id]
    _require(len(entries) == 1, "The overlay requires its unique actual original reference entry")
    return entries[0]


def _inventory(suite: Any, case: dict[str, Any], case_id: str) -> dict[str, Any]:
    state, metadata = case["state"], case["metadata"]
    displayed = state.get("displayed_reader")
    _require(isinstance(displayed, dict), "The native case has no committed displayed reader")
    session, preferences = displayed.get("session"), displayed.get("preferences")
    _require(isinstance(session, dict) and isinstance(preferences, dict), "The displayed reader has no actual inputs")
    lifecycle = state.get("lifecycle")
    _require(state.get("route") == "reader" and state.get("reader_image_ready") is True
             and all(state.get(key) is False for key in ("reader_inflight", "reader_dirty", "busy",
                                                         "capture_projection_pending"))
             and isinstance(lifecycle, dict)
             and all(lifecycle.get(key) is False for key in ("reader_frame_pending", "reader_repair_pending",
                                                            "network_resume_pending")),
             "The same-case artwork frame is not fully committed at capture")
    _require(all(session.get(key) == state.get(state_key) for key, state_key in (
        ("book_id", "current_book"), ("chapter_id", "current_chapter"), ("page", "reader_page"),
        ("total", "reader_total"), ("vertical_offset", "reader_offset"), ("zoom", "reader_zoom"),
    )), "The displayed source session differs from the actual Reader checkpoint")
    _require(state.get("visible_reader_page") == session["page"]
             and state.get("visible_reader_total") == session["total"]
             and state.get("visible_reader_chapter_id") == session["chapter_id"],
             "The native UI page projection differs from the committed source session")
    _require(state.get("reader_visible_pages") == [12, 13] and session["page"] == 12
             and session["total"] == 40 and session["vertical_offset"] == 0 and session["zoom"] == 1
             and session.get("landscape") is True and session.get("spread") is True,
             "The native source scene is not the actual default Scribe page 12/13 frame")
    _require(preferences == state.get("reader_effective") and preferences.get("reading_direction") == "rtl"
             and preferences.get("landscape") is True and preferences.get("spread") is True
             and preferences.get("crop_borders") is True and preferences.get("dither") is True
             and preferences.get("contrast") == 0 and preferences.get("night_mode") is False
             and preferences.get("cover_alone") is False and preferences.get("reverse_landscape") is False,
             "The committed preferences do not describe the actual accepted default artwork chain")
    _require(metadata.get("profile") == "scribe" and metadata.get("backend") == "desktop"
             and metadata.get("scale_factor") == 1 and metadata.get("landscape") is True
             and metadata.get("rotation_degrees") == 90
             and all(metadata.get(key) == [1240, 930] for key in ("logical_size", "window_size", "snapshot_size"))
             and state.get("display_rotation_degrees") == 90 and state.get("display_landscape") is True,
             "The native frame does not have the source-defined landscape geometry")
    _require(case.get("source_sha256") == suite.source["sha256"]
             and case.get("binary_sha256") == suite.binary_hash, "Native capture identity differs from the frozen tuple")
    cli_root = Path(case["data_dir"]).resolve(strict=True)
    _require(state.get("demo") is True and state.get("offline") is True, "This artwork source chain requires actual offline demo data")
    data_root = (cli_root / "demo").resolve(strict=True)
    database = Path(state["database"]).resolve(strict=True)
    _require(data_root.is_relative_to(cli_root) and database == data_root / "library.sqlite3",
             "The Store is outside this case's own dataRoot")
    namespace = (data_root / "cache" / "shared-v1").resolve(strict=True)
    _require(namespace.is_relative_to(data_root), "The cache namespace escapes this same case dataRoot")
    files = {record["path"]: record["sha256"] for record in suite.source["files"]}
    result: dict[str, Any] = {
        "case_id": case["name"], "overlay_case_id": case_id, "data_root": str(data_root),
        "database": str(database), "cache_namespace": str(namespace), "session": session,
        "committed_preferences": preferences, "application_preferences": state.get("preferences"),
        "frame_metadata": {key: metadata.get(key) for key in GEOMETRY_FIELDS},
        "source_sha256": case["source_sha256"], "binary_sha256": case["binary_sha256"],
        "design_sha256": suite.design["sha256"], "pages": [],
    }
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        chapter_row = connection.execute("SELECT document FROM chapters WHERE id=?", (session["chapter_id"],)).fetchone()
        total_row = connection.execute("SELECT total FROM chapter_page_stats WHERE chapter_id=?", (session["chapter_id"],)).fetchone()
        preference_row = connection.execute("SELECT document FROM preferences WHERE key=?", ("application",)).fetchone()
        _require(chapter_row is not None and total_row is not None and preference_row is not None,
                 "The actual Store source chapter, page total or application preferences are missing")
        chapter = json.loads(chapter_row[0])
        _require(chapter.get("id") == session["chapter_id"] and chapter.get("book_id") == session["book_id"]
                 and isinstance(chapter.get("source_id"), str) and total_row[0] == session["total"],
                 "The Store source chapter differs from the displayed session")
        _require(json.loads(preference_row[0]) == state.get("preferences"), "Durable application preferences differ from native capture")
        result["source_id"] = chapter["source_id"]
        positions = [page - 1 for page in state["reader_visible_pages"]]
        rows = connection.execute(
            "SELECT position,document FROM pages WHERE chapter_id=? AND position>=? AND position<? ORDER BY position LIMIT ?",
            (session["chapter_id"], positions[0], positions[-1] + 1, len(positions)),
        ).fetchall()
        _require([row[0] for row in rows] == positions, "The Store source-page window is missing, reordered or discontinuous")
    index_path = namespace / "index.sqlite3"
    with sqlite3.connect(index_path.as_uri() + "?mode=ro", uri=True, timeout=5) as index:
        index.execute("PRAGMA query_only=ON")
        index.execute("BEGIN")
        for slot_index, (position, document) in enumerate(rows):
            field = "first_path" if slot_index == 0 else "second_path"
            page = json.loads(document)
            _require(isinstance(page.get("local_path"), str) and page["local_path"], "The Store does not retain its actual raw page path")
            raw = Path(page["local_path"]).resolve(strict=True)
            _require(raw.is_relative_to(data_root), "The raw page belongs to a different case dataRoot")
            raw_bytes, raw_stat = _read_source(raw)
            raw_hash = _sha256(raw_bytes)
            directory = Path(suite.output) / "fixtures" / "c15-artwork" / case["name"]
            directory.mkdir(parents=True, exist_ok=True)
            raw_copy, processed_copy = directory / f"{field}.raw.png", directory / f"{field}.processed.png"
            raw_copy.write_bytes(raw_bytes)
            _register(case, f"{field}:raw", raw_copy)
            asset = f"assets/demo/page-{(position + 1) % 2:02}.png"
            _require(raw_hash == files.get(asset), "The actual source page differs from its frozen demo asset and page order")
            source_key, manifest_key, headers_hash = _page_keys(page, position, raw, raw_stat)
            processed_value = displayed.get(field)
            _require(isinstance(processed_value, str) and Path(processed_value).is_absolute(), "A committed processed path is missing or relative")
            processed = Path(processed_value).resolve(strict=True)
            processed_bytes, processed_stat = _read_source(processed)
            processed_hash = _sha256(processed_bytes)
            processed_copy.write_bytes(processed_bytes)
            _register(case, f"{field}:processed", processed_copy)
            header, texts = _png_structure(processed_bytes)
            _require(header["depth"] == 8 and header["color"] == 0 and header["interlaced"] == 0
                     and header["width"] * header["height"] <= MAX_PAGE_PIXELS and len(texts) == 1,
                     "The committed processed PNG does not expose one bounded actual grayscale geometry record")
            geometry_metadata = json.loads(texts[0].decode("latin-1"))
            _require(set(geometry_metadata) == {"version", "key", "render_key", "scaled_height", "viewport_height"},
                     "The processed geometry schema differs from the accepted rendering contract")
            render_key = {
                "source_key": f"{source_key}:{raw_hash}", "width": 1240, "height": 1860,
                "crop_borders": preferences["crop_borders"], "dither": preferences["dither"],
                "contrast": preferences["contrast"], "vertical": False, "offset": session["vertical_offset"],
                "zoom_milli": 1000,
            }
            key_hash = _sha256(json.dumps(render_key, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            _require(geometry_metadata["version"] == 1 and geometry_metadata["render_key"] == render_key
                     and geometry_metadata["key"] == key_hash and geometry_metadata["scaled_height"] == 0
                     and geometry_metadata["viewport_height"] == 0,
                     "The committed processed image does not bind its actual raw bytes and complete processing options")
            _require(processed == namespace / "processed" / key_hash[:2] / f"{key_hash}.png",
                     "The displayed processed path differs from its real canonical RenderKey")
            cache_row = index.execute(
                "SELECT id,kind,cache_key,size,checksum,fingerprint,valid FROM artifacts WHERE id=?",
                (f"processed:{key_hash}",),
            ).fetchone()
            _require(cache_row is not None and tuple(cache_row[:6]) == (
                f"processed:{key_hash}", "processed", key_hash, len(processed_bytes), processed_hash, _fingerprint(processed_stat),
            ) and cache_row[6] == 1, "The real processed cache index does not bind this exact retained PNG")
            crop = _raw_crop(raw_bytes)
            crop_width, crop_height = crop["raw_crop_xywh"][2:]
            fit_scale = min(1240 / crop_width, 1860 / crop_height)
            fitted = [min(1240, max(1, math.floor(crop_width * fit_scale + 0.5))),
                      min(1860, max(1, math.floor(crop_height * fit_scale + 0.5)))]
            _require([header["width"], header["height"]] == fitted,
                     "The processed geometry differs from the accepted raw crop and contain-processing fit")
            _require(_stat(raw) == raw_stat and _stat(processed) == processed_stat,
                     "An actual source changed before completion of its same-case inventory")
            result["pages"].append({
                "field": field, "source_page": position + 1, "store_position": position,
                "manifest_identity_sha256": manifest_key, "request_key_sha256": source_key,
                "request_url_sha256": _sha256(page["url"].encode("utf-8")), "canonical_headers_sha256": headers_hash,
                "raw_application_path": str(raw), "raw_retained_path": str(raw_copy),
                "raw_sha256": raw_hash, "raw_stat": raw_stat, "raw_fingerprint": _fingerprint(raw_stat),
                "frozen_asset": asset, "frozen_asset_sha256": files[asset],
                "processed_application_path": str(processed), "processed_retained_path": str(processed_copy),
                "processed_sha256": processed_hash, "processed_stat": processed_stat,
                "processed_cache_index": dict(zip(("id", "kind", "cache_key", "size", "checksum", "fingerprint", "valid"), cache_row)),
                "aidoku_render": geometry_metadata, "raw_geometry": crop, "processing_fit_size": fitted,
                "processing_contract": "decode_grayscale -> crop_white_borders -> Lanczos3 fit -> contrast -> sixteen-level quantize -> zoom_image -> processed PNG",
            })
    return result


def _artwork(suite: Any, api: Any, case: dict[str, Any], inventory: dict[str, Any],
             case_id: str, masked: bool, sources: list[dict[str, Any]]) -> dict[str, Any]:
    oracle = _api(api, "oracle")
    expected = Image.new("L", (1240, 930), 255)
    for page, slot in zip(inventory["pages"], ((620, 0, 620, 930), (0, 0, 620, 930))):
        payload = Path(page["processed_retained_path"]).read_bytes()
        _require(_sha256(payload) == page["processed_sha256"], "A retained processed input changed before oracle composition")
        page["display_geometry"] = oracle.draw_processed(expected, oracle.opaque_grayscale_png(payload), slot)
    with Image.open(case["screenshot"]) as original:
        _require(original.format == "PNG" and original.size == expected.size, "The native artwork viewport differs")
        actual = original.convert("RGBA")
    _require(actual.getchannel("A").getextrema() == (255, 255), "The native artwork capture is not fully opaque")
    mask = Image.new("L", expected.size, 0)
    rectangles = []
    panel_height = PANELS[case_id][3]
    panel_top = 930.0 - 210.0 - panel_height
    if masked:
        rectangles = [[0, 0, 1240, 78], [0, oracle.rounded_edge(panel_top), 1240, 930]]
        drawing = ImageDraw.Draw(mask)
        for left, top, right, bottom in rectangles:
            drawing.rectangle((left, top, right - 1, bottom - 1), fill=255)
    difference = ImageChops.difference(actual.convert("RGB"), expected.convert("RGB"))
    difference.paste((0, 0, 0), (0, 0), mask)
    red, green, blue = difference.split()
    maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    masked_pixels = mask.histogram()[255]
    changed = expected.width * expected.height - maximum.histogram()[0]
    metrics = {
        "exact_match": changed == 0, "changed_pixels": changed,
        "maximum_channel_difference": maximum.getextrema()[1],
        "difference_bbox": list(maximum.getbbox()) if maximum.getbbox() else None,
        "pixel_error_tolerance": 0, "masked_ui_pixels": masked_pixels,
        "compared_pixels": expected.width * expected.height - masked_pixels,
        "ignored_artwork_pixels_outside_ui_mask": 0,
    }
    directory = Path(suite.output) / "oracle"
    directory.mkdir(parents=True, exist_ok=True)
    paths = {label: directory / f"{case['name']}.c15-artwork.{label}.png"
             for label in ("expected", "mask", "diff")}
    for label, image in (("expected", expected), ("mask", mask), ("diff", difference)):
        image.save(paths[label])
        _register(case, label, paths[label])
    record = {
        "sampler_contract": oracle.SAMPLER_CONTRACT, "sampler_sources": oracle.SAMPLER_SOURCES,
        "metrics": metrics, "expected": str(paths["expected"]), "mask": str(paths["mask"]),
        "difference": str(paths["diff"]), "mask_rectangles_ltrb_half_open": rectangles,
        "logical_panel_ltrb": [0, panel_top, 1240, 720],
        "mask_source_files": sources, "mask_rounding_contract": "Existing Slint f32 nearest absolute endpoints; mode row 477 remains compared",
        "original_capture_unchanged": True, "manual_review_credit": 0, "clean_round_credit": 0,
    }
    suite.check(case, "Every panel-uncovered native RGB pixel equals its own committed processed artwork at zero tolerance",
                metrics["exact_match"], metrics)
    return record


def _collect_prerequisite(suite: Any, api: Any, prerequisite: dict[str, Any], case_id: str) -> None:
    """Retain the actual closed source transaction before opening its overlay."""
    try:
        pure = prerequisite.get("pure_reader_oracle", {}).get("metrics", {})
        _require(prerequisite.get("passed") is True and pure.get("exact_match") is True
                 and pure.get("pixel_error_tolerance") == 0 and pure.get("ignored_pixels") == 0,
                 "The original same-case pure-reader oracle did not pass its complete zero-tolerance viewport")
        inventory = _inventory(suite, prerequisite, case_id)
        retained_pure = prerequisite["pure_reader_oracle"].get("processed_inputs", [])
        _require(len(retained_pure) == 2 and all(
            pure_input.get("field") == page["field"] and pure_input.get("sha256") == page["processed_sha256"]
            and _file_hash(Path(pure_input["retained_path"])) == page["processed_sha256"]
            for pure_input, page in zip(retained_pure, inventory["pages"])
        ), "The source inventory differs from the prerequisite's original strict processed input copies")
        artwork = _artwork(suite, api, prerequisite, inventory, case_id, False, _geometry_sources(suite))
        inventory_path = _retain_json(suite, api, prerequisite, "inventory", inventory)
        artwork.update({"inventory": str(inventory_path), "overlay_case_id": case_id,
                        "prerequisite_case_id": prerequisite["name"], "retained_before_overlay_capture": True})
        provenance_path = _retain_json(suite, api, prerequisite, "provenance", artwork)
        prerequisite["c15_artwork"] = {**artwork, "provenance": str(provenance_path)}
    except Exception as error:
        failure = {"status": "failed", "error": f"{type(error).__name__}: {error}",
                   "overlay_case_id": case_id, "prerequisite_case_id": prerequisite["name"],
                   "manual_review_credit": 0, "clean_round_credit": 0}
        suite.check(prerequisite, "The closed source inventory was retained before its same-case overlay", False, failure)
        _retain_failure(suite, api, prerequisite, "closed-prerequisite", failure)
    suite.persist_case(prerequisite)


def collect_overlay(suite: Any, api: Any, overlay: dict[str, Any],
                    prerequisite: dict[str, Any]) -> bool:
    """Collect an original overlay using evidence retained before its opening."""
    case_id = overlay["name"]
    try:
        _require(os.name != "nt" and case_id in CASE_IDS, "Run this exact artwork helper only on the authorized Linux worker")
        panel, modal, context, _ = PANELS[case_id]
        _require(prerequisite["name"] == case_id + "--same-case-closed-reader-prerequisite"
                 and prerequisite.get("prerequisite") is True and prerequisite.get("manual_review_required") is False
                 and prerequisite["data_dir"] == overlay["data_dir"], "The closed prerequisite is not exclusive to this same overlay case")
        _require(overlay.get("reference_id") == case_id and overlay.get("prerequisite") is False
                 and overlay.get("manual_review_required") is True, "The overlay does not retain its original exact-case reference")
        _require(prerequisite["state"].get("menu") is False and prerequisite["state"].get("modal") == ""
                 and overlay["state"].get("menu") is True and overlay["state"].get("modal") == modal
                 and overlay["state"].get("modal_context") == context and overlay["state"].get("reader_panel") == panel,
                 "The actual closed and open native Reader states differ from this case's panel contract")
        pure = prerequisite.get("pure_reader_oracle", {}).get("metrics", {})
        _require(prerequisite.get("passed") is True and pure.get("exact_match") is True
                 and pure.get("pixel_error_tolerance") == 0 and pure.get("ignored_pixels") == 0,
                 "The original same-case pure-reader oracle did not pass its complete zero-tolerance viewport")
        sources = _geometry_sources(suite)
        prior = prerequisite.get("c15_artwork", {})
        _require(prior.get("retained_before_overlay_capture") is True,
                 "The prerequisite's source bytes were not retained before the overlay capture")
        inventory_path = Path(prior["inventory"])
        prior_hash = prerequisite["evidence_hashes"]["c15-artwork:inventory"]["sha256"]
        _require(_file_hash(inventory_path) == prior_hash, "The prerequisite inventory changed after its closed capture")
        before = json.loads(inventory_path.read_text(encoding="utf-8"))
        after = _inventory(suite, overlay, case_id)
        shared = ("data_root", "database", "cache_namespace", "session", "committed_preferences",
                  "application_preferences", "frame_metadata", "source_sha256", "binary_sha256",
                  "design_sha256", "source_id")
        _require(all(before[key] == after[key] for key in shared), "The same-case prerequisite and overlay changed source, preferences or frame identity")
        page_fields = ("field", "source_page", "store_position", "manifest_identity_sha256", "request_key_sha256",
                       "raw_application_path", "raw_sha256", "raw_stat", "raw_fingerprint", "processed_application_path",
                       "processed_sha256", "processed_stat", "aidoku_render", "raw_geometry", "processing_fit_size")
        _require(len(before["pages"]) == len(after["pages"]) == 2 and all(
            all(first[key] == second[key] for key in page_fields) for first, second in zip(before["pages"], after["pages"])
        ), "The same-case prerequisite and overlay changed their ordered raw or processed source inputs")
        artwork = _artwork(suite, api, overlay, after, case_id, True, sources)
        overlay_inventory = _retain_json(suite, api, overlay, "inventory", after)
        artwork.update({"inventory": str(overlay_inventory), "overlay_case_id": case_id,
                        "prerequisite_case_id": prerequisite["name"], "same_case_source_chain_equal": True,
                        "prerequisite_inventory": str(inventory_path), "prerequisite_inventory_sha256": prior_hash})
        provenance_path = _retain_json(suite, api, overlay, "provenance", artwork)
        overlay["c15_artwork"] = {**artwork, "provenance": str(provenance_path)}
        suite.check(overlay, "The independent same-case closed prerequisite and complete artwork evidence pass",
                    prerequisite.get("passed") is True, prerequisite["name"])
    except Exception as error:
        failure = {"status": "failed", "error": f"{type(error).__name__}: {error}",
                   "overlay_case_id": case_id, "prerequisite_case_id": prerequisite.get("name"),
                   "manual_review_credit": 0, "clean_round_credit": 0}
        for case in (prerequisite, overlay):
            suite.check(case, "The exact same-case source and pixel artwork contract completed", False, failure)
            _retain_failure(suite, api, case, "overlay-pair", failure)
            suite.persist_case(case)
    suite.persist_case(overlay)
    return prerequisite.get("passed") is True and overlay.get("passed") is True


def capture_case(suite: Any, api: Any, case_id: str) -> dict[str, Any]:
    """Capture one original reference and exactly one fresh same-case prerequisite."""
    _require(os.name != "nt" and case_id in CASE_IDS, "Run only the exact authorized remote artwork cases")
    prerequisite_id = case_id + "--same-case-closed-reader-prerequisite"
    _require(not any(case.get("name") in (case_id, prerequisite_id) for case in suite.cases),
             "This exact artwork reference or prerequisite was already captured; do not duplicate it")
    entry = _reference_entry(suite, case_id)
    panel, modal, context, _ = PANELS[case_id]
    data = suite.fresh(case_id)
    prerequisite = suite.capture(prerequisite_id, "reader", device="scribe", landscape=True,
                                 data=data, pure=True, prerequisite=True,
                                 expected={"menu": False}, modal="", capture_delay=1600)
    _collect_prerequisite(suite, api, prerequisite, case_id)
    if prerequisite.get("passed") is not True or prerequisite.get("c15_artwork", {}).get("retained_before_overlay_capture") is not True:
        return {"completed": False, "passed": False, "case_id": case_id,
                "prerequisite_case_id": prerequisite_id}
    replay = _api(api, "Replay")(start=2200).intent("reader", "menu").intent("reader", panel)
    overlay = suite.capture(case_id, "reader", device="scribe", landscape=True, data=data,
                            replay=replay, expected={"menu": True}, modal=modal, context=context,
                            reference=entry, note=entry.get("intentionalDifference"), capture_delay=1600)
    overlay.setdefault("visible_ui_requirements", []).append(
        "Review the original native panel glyphs and controls against this exact reference; artwork metadata grants no interface or manual acceptance.")
    passed = collect_overlay(suite, api, overlay, prerequisite)
    return {"completed": True, "passed": passed, "case_id": case_id,
            "prerequisite_case_id": prerequisite_id}


def run_case(suite: Any, api: Any, board: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    """Intercept one full-matrix reference without recapturing the original case."""
    case_id = entry.get("id")
    _require(case_id in CASE_IDS and board.get("route") == "reader"
             and board.get("orientation") == "landscape" and board.get("profile") == "sc"
             and entry == _reference_entry(suite, case_id),
             "The full-matrix artwork interceptor requires its original exact Scribe reference")
    result = capture_case(suite, api, case_id)
    return {"completed": result["completed"], "passed": result["passed"],
            "case_ids": [case_id] if result["completed"] else [],
            "prerequisite_case_ids": [result["prerequisite_case_id"]]}


def run(suite: Any, api: Any) -> dict[str, Any]:
    """Capture exactly six native frames; the outer harness owns the 72-case gate."""
    _api(api, "oracle")
    _api(api, "Replay")
    _api(api, "save_json")
    start = len(suite.cases)
    results = []
    for case_id in CASE_IDS:
        result = capture_case(suite, api, case_id)
        results.append(result)
        if not result["completed"]:
            break
    actual = [case["name"] for case in suite.cases[start:]]
    required = [name for case_id, prerequisite_id in zip(CASE_IDS, PREREQUISITE_IDS)
                for name in (prerequisite_id, case_id)]
    completed = actual == required and all(result["completed"] for result in results)
    return {"completed": completed, "passed": completed and all(result["passed"] for result in results),
            "case_ids": [name for name in actual if name in CASE_IDS],
            "prerequisite_case_ids": [name for name in actual if name in PREREQUISITE_IDS]}
