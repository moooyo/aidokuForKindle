"""Analyze one closed staged launcher visit without accessing a device."""

import gzip
import io
import json
from pathlib import Path
import sys
import tarfile
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import launcher_stages


MEMBERS = {"launcher.log", "application.json", "state.json"}
ARCHIVE_LIMIT = 8 * 1024 * 1024
UNPACKED_LIMIT = 24 * 1024 * 1024
FILE_LIMIT = 8 * 1024 * 1024
ORDINARY_EXIT = b"aidoku: launcher-visit ordinary_exit\n"


def need(condition, reason):
    if not condition:
        raise ValueError(reason)


def archive_members(payload):
    need(isinstance(payload, bytes) and 0 < len(payload) <= ARCHIVE_LIMIT, "Invalid archive size")
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(payload), mode="rb") as compressed:
            data = compressed.read(UNPACKED_LIMIT + 1)
    except (OSError, EOFError, zlib.error) as error:
        raise ValueError("Invalid compressed archive") from error
    need(len(data) <= UNPACKED_LIMIT, "Unpacked archive is too large")
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
            members = archive.getmembers()
            need(len(members) == len(MEMBERS), "Unexpected archive members")
            found = {}
            end = 0
            for member in members:
                need(member.offset == end and member.offset_data == end + 512
                     and member.name in MEMBERS and member.name not in found
                     and member.type in (tarfile.REGTYPE, tarfile.AREGTYPE)
                     and not member.pax_headers and not member.sparse
                     and 0 < member.size <= FILE_LIMIT, "Invalid archive member")
                stream = archive.extractfile(member)
                need(stream is not None, "Missing archive member")
                content = stream.read(member.size + 1)
                need(len(content) == member.size, "Truncated archive member")
                found[member.name] = content
                end = member.offset_data + ((member.size + 511) // 512) * 512
            need(set(found) == MEMBERS and len(data) >= end + 1024
                 and not any(data[end:]), "Invalid archive ending")
            return found
    except (tarfile.TarError, OSError, EOFError) as error:
        raise ValueError("Invalid tar archive") from error


def json_object(content):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            need(key not in value, "Duplicate JSON field")
            value[key] = item
        return value

    def invalid_constant(_value):
        raise ValueError("Invalid JSON number")

    try:
        value = json.loads(content, object_pairs_hook=unique, parse_constant=invalid_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("Invalid JSON") from error
    need(isinstance(value, dict), "Expected a JSON object")
    return value


def natural(value):
    return type(value) is int and value >= 0


def positive(value):
    return natural(value) and value > 0


def analyze_application(trace):
    need(trace.get("schema_version") == 1 and trace.get("clock") == "monotonic_rust_main"
         and all(trace.get(key) is True for key in ("enabled", "finished", "success"))
         and type(trace.get("dropped_events")) is int and trace["dropped_events"] == 0,
         "Incomplete application trace")
    events = trace.get("events")
    need(isinstance(events, list) and events
         and all(isinstance(event, dict) and isinstance(event.get("name"), str)
                 and natural(event.get("at_us")) for event in events), "Invalid application events")
    need(all(left["at_us"] <= right["at_us"] for left, right in zip(events, events[1:])),
         "Application clock moved backwards")

    def single(name):
        matches = [(index, event) for index, event in enumerate(events) if event["name"] == name]
        need(len(matches) == 1, "Missing or repeated application boundary")
        return matches[0]

    entry_index, entry = single("process_entry")
    finished_index, finished = single("process_finished")
    initial_index, initial = single("startup_initial_frame_submitted")
    initial_ready_index, initial_ready = single("startup_initial_input_ready")
    library_index, library = single("startup_library_frame_submitted")
    library_ready_index, library_ready = single("startup_library_input_ready")
    settled_index, settled = single("startup_visible_covers_settled")
    stop_index, stop = single("shutdown_started")
    complete_index, complete = single("shutdown_complete")
    need(not any(event["name"] == "shutdown_timeout" for event in events)
         and entry_index == 0 and entry["at_us"] == 0 and finished_index == len(events) - 1
         and initial_index < initial_ready_index < library_ready_index < stop_index < complete_index < finished_index
         and initial_index <= library_index < library_ready_index
         and settled_index < stop_index,
         "Invalid startup or shutdown order")
    for event in (initial, initial_ready, library, library_ready, settled, stop, complete):
        need(positive(event.get("boottime_us")), "Missing application boot clock")
    need(initial["boottime_us"] <= initial_ready["boottime_us"] <= library_ready["boottime_us"]
         and initial["boottime_us"] <= library["boottime_us"] <= library_ready["boottime_us"]
         and library_ready["boottime_us"] <= stop["boottime_us"] <= complete["boottime_us"],
         "Invalid application boot clock order")
    need(initial.get("full") is True and initial_ready.get("full") is True
         and positive(initial.get("marker"))
         and positive(initial.get("frame_sequence"))
         and initial_ready.get("marker") == initial["marker"]
         and initial_ready.get("frame_sequence") == initial["frame_sequence"],
         "Initial input fence is not bound to the full frame")

    frame_entries = [(index, event) for index, event in enumerate(events)
                     if event["name"] == "startup_cover_frame_submitted"]
    need(frame_entries and library_index < frame_entries[0][0], "Missing submitted Library frames")
    frames = [event for _, event in frame_entries]
    for frame in [*frames, library, library_ready, settled]:
        need(positive(frame.get("marker")) and positive(frame.get("frame_sequence"))
             and type(frame.get("cover_slots")) is int and frame["cover_slots"] == 7
             and natural(frame.get("cover_images")) and frame["cover_images"] <= 7
             and type(frame.get("cover_pending")) is bool and positive(frame.get("boottime_us")),
             "Invalid Library frame")
    need(all(left["frame_sequence"] < right["frame_sequence"]
             and left["marker"] != right["marker"] and left["boottime_us"] <= right["boottime_us"]
             for left, right in zip(frames, frames[1:])), "Invalid Library frame sequence")
    fields = ("marker", "frame_sequence", "cover_slots", "cover_images", "cover_pending")
    need(all(library[key] == frames[0][key] for key in fields)
         and any(index < library_ready_index
                 and (library_ready["marker"], library_ready["frame_sequence"])
                 == (frame["marker"], frame["frame_sequence"])
                 and frame["at_us"] <= library_ready["at_us"]
                 and frame["boottime_us"] <= library_ready["boottime_us"]
                 for index, frame in frame_entries),
         "Library input fence is not bound to a submitted frame")
    need(frame_entries[-1][0] < settled_index < stop_index
         and all(settled[key] == frames[-1][key] for key in fields)
         and settled["cover_images"] == 7 and settled["cover_pending"] is False
         and frames[-1]["boottime_us"] <= settled["boottime_us"] <= stop["boottime_us"],
         "Settled covers are not bound to the final Library frame")
    return {
        "initial_frame_to_input_ready_us": initial_ready["at_us"] - initial["at_us"],
        "library_frame_to_input_ready_us": library_ready["at_us"] - library["at_us"],
        "shutdown_started_to_complete_us": complete["at_us"] - stop["at_us"],
        "full_initial_frame_and_input_ready": True,
        "library_frame_and_input_ready": True,
        "visible_covers_frame_settled": True,
        "shutdown_complete": True,
    }


def analyze_state(state):
    capability = state.get("capability_declaration")
    need(isinstance(capability, dict) and capability.get("profile") == "scribe"
         and capability.get("backend") == "kindle" and state.get("route") == "library"
         and state.get("offline") is True and state.get("demo") is False
         and state.get("diagnostics_available") is True
         and state.get("performance_timing_enabled") is True,
         "Incorrect application state")
    need(type(state.get("library_count")) is int and state["library_count"] == 6
         and type(state.get("cover_visible_count")) is int and state["cover_visible_count"] == 6
         and all(type(state.get(key)) is int and state[key] == 0
                 for key in ("cover_visible_failure_count", "cover_pending_count", "cover_queued_count"))
         and all(state.get(key) is False for key in
                 ("cover_inflight", "cover_work_pending", "capture_projection_pending")),
         "Library covers are incomplete")
    return {"profile": "scribe", "backend": "kindle", "offline": True,
            "library_count": 6, "visible_covers": 6, "covers_settled": True}


def analyze_archive(payload: bytes) -> dict:
    members = archive_members(payload)
    lines = members["launcher.log"].splitlines(keepends=True)
    exits = [line for line in lines if line.startswith(b"aidoku: launcher-visit ")]
    need(exits == [ORDINARY_EXIT], "Missing or repeated ordinary supervisor exit")
    try:
        launcher = launcher_stages.summarize(lines)
    except ValueError as error:
        raise ValueError("Invalid launcher diagnostic") from error
    spawn = next((index for index, line in enumerate(lines)
                  if line.startswith(b"aidoku: launcher-diag v1 app_spawn_returned ")), None)
    need(spawn is not None and spawn < lines.index(ORDINARY_EXIT), "Invalid supervisor exit order")
    return {
        "scope": "One staged diagnostic; software boundaries, not a speed or physical-display qualification",
        "launcher": launcher,
        "application": analyze_application(json_object(members["application.json"])),
        "state": analyze_state(json_object(members["state.json"])),
        "ordinary_supervisor_exit": True,
    }
