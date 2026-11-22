"""Freeze and load one staged launcher-visit payload and private plan."""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
import tarfile
import tempfile
from typing import Any


PROTOCOL = "launcher-visit-freeze-v1"
PLAN_NAME = "plan.json"
PAYLOAD_NAME = "payload.tar.gz"
EXPECTED_BOOKS = 6
EXIT_AFTER_MS = 8000
MAX_COMPRESSED_PAYLOAD = 128 * 1024 * 1024
MAX_MEMBER_SIZE = 96 * 1024 * 1024
MAX_TOTAL_CONTENT = 256 * 1024 * 1024

REQUIRED_INPUTS = (
    "launcher.sh",
    "guard.sh",
    "aidoku-desktop",
    "app/aidoku",
    "app/aidoku-startup-portrait",
    "01.jpg",
    "02.jpg",
    "03.jpg",
    "04.jpg",
    "05.jpg",
    "06.jpg",
)

FIXED_MODES = {
    "launch.sh": 0o755,
    "guard.sh": 0o755,
    "app/aidoku": 0o755,
    "app/aidoku-startup-portrait": 0o755,
    "seed/library.sqlite3": 0o600,
}

SAFE_LABEL = re.compile(r"[0-9a-f]{16}\Z")
SAFE_COMPONENT = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")
SAFE_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
SOURCE_SLOTS = tuple(f"{index:02d}.jpg" for index in range(1, EXPECTED_BOOKS + 1))
DISALLOWED_PAYLOAD_TYPES = tuple(
    item
    for item in (
        getattr(tarfile, "SYMTYPE", None),
        getattr(tarfile, "LNKTYPE", None),
        getattr(tarfile, "GNUTYPE_LONGLINK", None),
        getattr(tarfile, "GNUTYPE_LONGNAME", None),
        getattr(tarfile, "XHDTYPE", None),
        getattr(tarfile, "XGLTYPE", None),
    )
    if item is not None
)


def need(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        need(key not in value, "duplicate_json_field")
        value[key] = item
    return value


def read_json(path: Path) -> dict[str, Any]:
    data = path.read_text(encoding="utf-8")
    value = json.loads(data, object_pairs_hook=unique_object)
    need(isinstance(value, dict), "plan_not_object")
    return value


def regular_file(path: Path, reason: str) -> None:
    need(path.exists(), reason)
    need(path.is_file() and not path.is_symlink(), reason)


def parse_relative_seed_path(relative: str) -> tuple[str, ...]:
    need(isinstance(relative, str) and relative and "\\" not in relative, "seed_cover_relative_invalid")
    need(not relative.startswith("/"), "seed_cover_relative_invalid")
    normalized = str(PurePosixPath(relative))
    need(relative == normalized, "seed_cover_relative_noncanonical")
    parts = tuple(relative.split("/"))
    need(len(parts) == 5, "seed_cover_relative_shape")
    for part in parts:
        need(part not in ("", ".", "..") and SAFE_COMPONENT.fullmatch(part) is not None, "seed_cover_component_unsafe")
    need(parts[0] == "local" and parts[1] == "content", "seed_cover_relative_shape")
    need(SAFE_HEX64.fullmatch(parts[2]) is not None and SAFE_HEX64.fullmatch(parts[3]) is not None,
         "seed_cover_relative_shape")
    need(parts[4] == "000001.jpg", "seed_cover_relative_shape")
    return parts


def parse_absolute_seed_path(cover: str, label: str) -> str:
    need(isinstance(cover, str) and cover and "\\" not in cover, "seed_cover_not_absolute")
    root = f"/mnt/us/.aidoku-native-{label}/seed/"
    need(cover.startswith(root), "seed_cover_outside_stage")
    relative = cover[len(root):]
    parse_relative_seed_path(relative)
    need(cover == root + relative, "seed_cover_noncanonical")
    return relative


def safe_archive_name(name: str) -> bool:
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        return False
    if name.startswith("/"):
        return False
    normalized = str(PurePosixPath(name))
    if name != normalized:
        return False
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    return all(SAFE_COMPONENT.fullmatch(part) is not None for part in parts)


def replace_once(source: str, old: str, new: str, reason: str) -> str:
    need(source.count(old) == 1, reason)
    return source.replace(old, new, 1)


def rewrite_launcher(original: bytes) -> bytes:
    try:
        text = original.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("launcher_not_utf8") from error
    need("\r" not in text, "launcher_not_canonical")
    text = replace_once(
        text,
        "DATA_DIR=$AIDOKU_HOME/data",
        "AIDOKU_RESEARCH_DATA=${AIDOKU_RESEARCH_DATA:?AIDOKU_RESEARCH_DATA required}\n"
        "AIDOKU_RESEARCH_OUT=${AIDOKU_RESEARCH_OUT:?AIDOKU_RESEARCH_OUT required}\n"
        f"AIDOKU_RESEARCH_DWELL=${{AIDOKU_RESEARCH_DWELL:-{EXIT_AFTER_MS}}}\n"
        "DATA_DIR=$AIDOKU_RESEARCH_DATA",
        "launcher_data_dir_anchor_missing",
    )
    tail = (
        ' --offline --screen library --performance-trace "$AIDOKU_RESEARCH_OUT/application.json"'
        ' --state-output "$AIDOKU_RESEARCH_OUT/state.json"'
        ' --exit-after-ms "$AIDOKU_RESEARCH_DWELL"'
    )
    lines = text.split("\n")
    rewritten = 0
    for index, line in enumerate(lines):
        marker = '"$APP_DIR/aidoku" --kindle --device "$PROFILE" --data-dir "$DATA_DIR"'
        if marker not in line:
            continue
        line = replace_once(
            line,
            '--data-dir "$DATA_DIR"',
            '--data-dir "$DATA_DIR"' + tail,
            "launcher_command_data_dir_anchor_missing",
        )
        line = replace_once(
            line,
            "3>&- &",
            '3>&- 2>"$AIDOKU_RESEARCH_OUT/native.log" &',
            "launcher_command_redirect_anchor_missing",
        )
        lines[index] = line
        rewritten += 1
    need(rewritten == 2, "launcher_launch_mode_anchor_count")
    text = "\n".join(lines)

    ordinary_old = '            if ordinary_exit "$3"; then exit "$app_exit_status"; fi'
    ordinary_new = (
        '            if ordinary_exit "$3"; then\n'
        '                if [ "$app_exit_phase" = active ] && [ "$app_exit_status" -eq 0 ]; then\n'
        "                    printf '%s\\n' 'aidoku: launcher-visit ordinary_exit' >&2\n"
        "                fi\n"
        '                exit "$app_exit_status"\n'
        "            fi"
    )
    text = replace_once(text, ordinary_old, ordinary_new, "launcher_ordinary_exit_anchor_missing")
    need(text.count("aidoku: launcher-visit ordinary_exit") == 1, "launcher_ordinary_exit_marker_count")
    return text.encode("utf-8")


def read_local_books(seed_database: Path, label: str) -> list[str]:
    with sqlite3.connect(f"file:{seed_database}?mode=ro", uri=True) as database:
        rows = database.execute(
            "SELECT document FROM books WHERE source_id='local' AND in_library=1 ORDER BY title"
        ).fetchall()
    need(len(rows) == EXPECTED_BOOKS, "seed_local_book_count")
    covers: list[str] = []
    for row in rows:
        need(isinstance(row, tuple) and len(row) == 1 and isinstance(row[0], str), "seed_document_column")
        document = json.loads(row[0], object_pairs_hook=unique_object)
        need(isinstance(document, dict), "seed_document_not_object")
        cover = document.get("cover")
        covers.append(parse_absolute_seed_path(cover, label))
    need(len(set(covers)) == EXPECTED_BOOKS, "seed_cover_not_distinct")
    return covers


def resolve_seed_path(seed_dir: Path, relative: str) -> Path:
    parts = parse_relative_seed_path(relative)
    path = seed_dir.joinpath(*parts)
    regular_file(path, "missing_seed_cover")
    resolved = path.resolve(strict=True)
    need(resolved.is_relative_to(seed_dir.resolve(strict=True)), "seed_cover_outside_seed_dir")
    return path


def cover_binding(inputs_dir: Path, seed_dir: Path, relatives: list[str]) -> list[dict[str, Any]]:
    sources: list[tuple[str, bytes]] = []
    seen_source = set()
    for slot in SOURCE_SLOTS:
        source = inputs_dir / slot
        regular_file(source, "missing_input_cover")
        content = source.read_bytes()
        need(content not in seen_source, "input_cover_not_distinct")
        seen_source.add(content)
        sources.append((slot, content))

    bindings: list[dict[str, Any]] = []
    used_slots = set()
    for relative in relatives:
        local = resolve_seed_path(seed_dir, relative)
        content = local.read_bytes()
        matches = [slot for slot, source in sources if source == content]
        need(len(matches) == 1, "seed_cover_not_one_to_one")
        slot = matches[0]
        need(slot not in used_slots, "seed_cover_slot_reused")
        used_slots.add(slot)
        bindings.append({"member": f"seed/{relative}", "input": slot, "size": len(content)})
    need(len(used_slots) == EXPECTED_BOOKS, "seed_cover_binding_incomplete")
    return bindings


def tar_entry(name: str, mode: int, content: bytes) -> tuple[tarfile.TarInfo, io.BytesIO]:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    info.mode = mode
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    return info, io.BytesIO(content)


def make_payload(members: list[tuple[str, int, bytes]]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz", compresslevel=2, format=tarfile.USTAR_FORMAT) as archive:
        for name, mode, content in members:
            need(safe_archive_name(name), "unsafe_payload_path")
            info, stream = tar_entry(name, mode, content)
            archive.addfile(info, stream)
    payload = output.getvalue()
    need(0 < len(payload) <= MAX_COMPRESSED_PAYLOAD, "payload_size")
    return payload


def parse_payload(payload: bytes, expected: dict[str, int]) -> dict[str, tuple[tarfile.TarInfo, bytes]]:
    need(isinstance(payload, bytes) and 0 < len(payload) <= MAX_COMPRESSED_PAYLOAD, "invalid_payload_size")
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            members: dict[str, tuple[tarfile.TarInfo, bytes]] = {}
            total = 0
            for item in archive:
                need(safe_archive_name(item.name), "unsafe_payload_path")
                need(item.name in expected and item.name not in members, "unexpected_payload_member")
                need(item.type not in DISALLOWED_PAYLOAD_TYPES, "payload_member_type")
                need(item.type in (tarfile.REGTYPE, tarfile.AREGTYPE), "payload_member_type")
                need(not item.pax_headers and not item.linkname and not getattr(item, "sparse", None), "payload_member_flags")
                mode = item.mode & 0o777
                need(mode == expected[item.name] and item.mode == mode, "payload_member_mode")
                need(0 < item.size <= MAX_MEMBER_SIZE, "payload_member_size")
                stream = archive.extractfile(item)
                need(stream is not None, "payload_member_missing")
                content = stream.read(item.size + 1)
                need(len(content) == item.size, "payload_member_truncated")
                total += item.size
                need(total <= MAX_TOTAL_CONTENT, "payload_total_size")
                members[item.name] = (item, content)
    except (tarfile.TarError, OSError, EOFError) as error:
        raise ValueError("invalid_payload_archive") from error
    need(set(members) == set(expected), "payload_member_set")
    return members


def validate_transformed_launcher(content: bytes) -> None:
    text = content.decode("utf-8")
    need("DATA_DIR=$AIDOKU_RESEARCH_DATA" in text, "launcher_data_dir_missing")
    need("AIDOKU_RESEARCH_DATA=${AIDOKU_RESEARCH_DATA:?AIDOKU_RESEARCH_DATA required}" in text,
         "launcher_research_data_contract")
    need(text.count("--performance-trace") == 2, "launcher_trace_flags_count")
    need(text.count("--state-output") == 2, "launcher_state_flags_count")
    need(text.count("--exit-after-ms") == 2, "launcher_exit_flags_count")
    need(text.count("--offline --screen library") == 2, "launcher_offline_library_flags_count")
    need(text.count('2>"$AIDOKU_RESEARCH_OUT/native.log"') == 2, "launcher_native_log_count")
    need(text.count('if ordinary_exit "$3"; then') == 1, "launcher_app_exited_anchor_count")
    need(text.count("aidoku: launcher-visit ordinary_exit") == 1, "launcher_ordinary_exit_marker_count")
    expected = (
        'if ordinary_exit "$3"; then\n'
        '                if [ "$app_exit_phase" = active ] && [ "$app_exit_status" -eq 0 ]; then\n'
        "                    printf '%s\\n' 'aidoku: launcher-visit ordinary_exit' >&2\n"
    )
    need(expected in text, "launcher_ordinary_exit_branch_shape")


def load_plan_contract(plan: dict[str, Any]) -> tuple[str, dict[str, int], list[dict[str, Any]]]:
    expected = {
        "protocol",
        "label",
        "visit_count",
        "deployment",
        "timeout_ms",
        "payload",
        "payload_members",
        "seed_cover_binding",
        "notes",
    }
    need(set(plan) == expected, "plan_fields")
    need(plan["protocol"] == PROTOCOL, "plan_protocol")
    need(isinstance(plan["label"], str) and SAFE_LABEL.fullmatch(plan["label"]) is not None, "plan_label")
    need(plan["visit_count"] == 1 and plan["deployment"] is False and plan["timeout_ms"] == EXIT_AFTER_MS,
         "plan_visit_contract")
    need(plan["payload"] == PAYLOAD_NAME, "plan_payload_name")
    need(plan["notes"] == "single staged launcher visit with synthetic cold shelf only", "plan_notes")

    bindings = plan["seed_cover_binding"]
    need(isinstance(bindings, list) and len(bindings) == EXPECTED_BOOKS, "plan_seed_binding_count")
    seen_slot = set()
    seen_member = set()
    for binding in bindings:
        need(isinstance(binding, dict) and set(binding) == {"member", "input", "size"}, "plan_seed_binding_shape")
        member = binding["member"]
        slot = binding["input"]
        size = binding["size"]
        need(isinstance(member, str) and member.startswith("seed/"), "plan_seed_binding_member")
        parse_relative_seed_path(member[len("seed/"):])
        need(member not in seen_member, "plan_seed_binding_member")
        need(isinstance(slot, str) and slot in SOURCE_SLOTS and slot not in seen_slot, "plan_seed_binding_slot")
        need(type(size) is int and size > 0, "plan_seed_binding_size")
        seen_member.add(member)
        seen_slot.add(slot)
    need(len(seen_slot) == EXPECTED_BOOKS, "plan_seed_binding_slots")

    payload_members = plan["payload_members"]
    need(isinstance(payload_members, list) and payload_members, "plan_payload_members")
    expected_seed = set(seen_member)
    expected_names = set(FIXED_MODES) | expected_seed
    need(len(payload_members) == len(expected_names), "plan_payload_members_count")
    seen = set()
    modes: dict[str, int] = {}
    for member in payload_members:
        need(isinstance(member, dict) and set(member) == {"name", "mode", "size"}, "plan_payload_member_shape")
        name = member["name"]
        mode = member["mode"]
        size = member["size"]
        need(isinstance(name, str) and safe_archive_name(name) and name not in seen, "plan_payload_member_name")
        need(type(mode) is int and 0 <= mode <= 0o777, "plan_payload_member_mode")
        need(type(size) is int and 0 < size <= MAX_MEMBER_SIZE, "plan_payload_member_size")
        expected_mode = FIXED_MODES.get(name, 0o600 if name in expected_seed else None)
        need(expected_mode is not None and mode == expected_mode, "plan_payload_member_whitelist")
        seen.add(name)
        modes[name] = mode
    need(seen == expected_names, "plan_payload_member_set")
    return plan["label"], modes, bindings


def freeze(plan_dir: Path, inputs_dir: Path, seed_dir: Path, label: str) -> Path:
    need(SAFE_LABEL.fullmatch(label) is not None, "label_format")
    need(not plan_dir.exists(), "plan_dir_reuse")
    regular_file(seed_dir / "library.sqlite3", "missing_seed_database")
    for required in REQUIRED_INPUTS:
        regular_file(inputs_dir / required, "missing_required_input")

    launcher = rewrite_launcher((inputs_dir / "launcher.sh").read_bytes())
    guard = (inputs_dir / "guard.sh").read_bytes()
    aidoku = (inputs_dir / "app" / "aidoku").read_bytes()
    portrait = (inputs_dir / "app" / "aidoku-startup-portrait").read_bytes()
    database = (seed_dir / "library.sqlite3").read_bytes()

    covers = read_local_books(seed_dir / "library.sqlite3", label)
    bindings = cover_binding(inputs_dir, seed_dir, covers)
    members: list[tuple[str, int, bytes]] = [
        ("launch.sh", FIXED_MODES["launch.sh"], launcher),
        ("guard.sh", FIXED_MODES["guard.sh"], guard),
        ("app/aidoku", FIXED_MODES["app/aidoku"], aidoku),
        ("app/aidoku-startup-portrait", FIXED_MODES["app/aidoku-startup-portrait"], portrait),
        ("seed/library.sqlite3", FIXED_MODES["seed/library.sqlite3"], database),
    ]
    for item in bindings:
        relative = item["member"][len("seed/"):]
        members.append((item["member"], 0o600, resolve_seed_path(seed_dir, relative).read_bytes()))

    payload = make_payload(members)
    expected_modes = {name: mode for name, mode, _ in members}
    parsed = parse_payload(payload, expected_modes)
    validate_transformed_launcher(parsed["launch.sh"][1])

    plan = {
        "protocol": PROTOCOL,
        "label": label,
        "visit_count": 1,
        "deployment": False,
        "timeout_ms": EXIT_AFTER_MS,
        "payload": PAYLOAD_NAME,
        "payload_members": [{"name": name, "mode": mode, "size": len(content)} for name, mode, content in members],
        "seed_cover_binding": bindings,
        "notes": "single staged launcher visit with synthetic cold shelf only",
    }
    plan_dir.mkdir(parents=True, exist_ok=False)
    (plan_dir / PLAN_NAME).write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    (plan_dir / PAYLOAD_NAME).write_bytes(payload)
    return plan_dir / PLAN_NAME


def load(plan_dir: Path) -> tuple[dict[str, Any], bytes]:
    root = plan_dir.resolve()
    need(root.is_dir(), "plan_dir_missing")
    plan_path = root / PLAN_NAME
    payload_path = root / PAYLOAD_NAME
    regular_file(plan_path, "plan_file_missing")
    regular_file(payload_path, "payload_file_missing")

    plan = read_json(plan_path)
    label, expected_modes, bindings = load_plan_contract(plan)
    payload = payload_path.read_bytes()
    members = parse_payload(payload, expected_modes)

    ordered = [item["name"] for item in plan["payload_members"]]
    need(list(members) == ordered, "payload_member_order")
    for item in plan["payload_members"]:
        name = item["name"]
        info, data = members[name]
        need(info.mode == item["mode"] and len(data) == item["size"], "payload_member_contract")

    validate_transformed_launcher(members["launch.sh"][1])

    with tempfile.TemporaryDirectory(prefix="launcher-visit-load-") as folder:
        database_path = Path(folder) / "library.sqlite3"
        database_path.write_bytes(members["seed/library.sqlite3"][1])
        covers = read_local_books(database_path, label)
    expected_cover_members = [item["member"] for item in bindings]
    actual_cover_members = [f"seed/{relative}" for relative in covers]
    need(actual_cover_members == expected_cover_members, "payload_seed_order_or_names")

    seen_slots = set()
    for item in bindings:
        member = item["member"]
        slot = item["input"]
        content = members[member][1]
        need(slot not in seen_slots and slot in SOURCE_SLOTS, "payload_seed_slot_binding")
        need(len(content) == item["size"], "payload_seed_member_size")
        seen_slots.add(slot)
    need(len(seen_slots) == EXPECTED_BOOKS, "payload_seed_slot_count")
    return plan, payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    command = parser.add_subparsers(dest="command", required=True)
    freeze_parser = command.add_parser("freeze")
    freeze_parser.add_argument("new_plan_dir", type=Path)
    freeze_parser.add_argument("inputs_dir", type=Path)
    freeze_parser.add_argument("seed_dir", type=Path)
    freeze_parser.add_argument("label")
    args = parser.parse_args()
    if args.command == "freeze":
        plan_path = freeze(args.new_plan_dir, args.inputs_dir, args.seed_dir, args.label)
        print(plan_path)
        return 0
    raise AssertionError("Unsupported command")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError, sqlite3.DatabaseError, tarfile.TarError) as error:
        raise SystemExit(f"payload freeze failed: {error}")
