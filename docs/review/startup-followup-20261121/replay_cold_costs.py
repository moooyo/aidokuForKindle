"""Replay wall-time cover intervals from a closed cold confirmation attempt."""

import argparse
import json
from pathlib import Path, PurePosixPath
from statistics import median
import sys
import tarfile

PILOT = Path(__file__).resolve().parent.parent / "startup-fast-20261119" / "operator" / "clean-worker" / "staged-pilot"
sys.path.insert(0, str(PILOT))
from analyze_pilot import analyze
from cohort import check_protocol


MARKS = (
    ("cover_preparation_started",),
    ("cover_cpu_original_read",),
    ("cover_cold_validation_combined_started",),
    ("cover_cpu_source_bytes_verified",),
    ("cover_cpu_neon_started", "cover_cpu_scalar_started"),
    ("cover_cpu_source_decoded",),
    ("cover_cpu_gray_ready",),
    ("cover_cpu_resize_finished",),
    ("cover_cpu_quantized",),
    ("cover_cold_decode_validated",),
    ("cover_local_derivative_prepared",),
    ("cover_render_ready",),
    ("cover_preparation_finished",),
)
INTERVALS = (
    "start_to_original_read",
    "original_read_to_combined",
    "combined_to_bytes_verified",
    "bytes_verified_to_decoder_start",
    "decoder_start_to_source_decoded",
    "source_decoded_to_gray_ready",
    "gray_ready_to_resize_finished",
    "resize_finished_to_quantized",
    "quantized_to_cold_decode_validated",
    "cold_decode_validated_to_derivative_prepared",
    "derivative_prepared_to_render_ready",
    "render_ready_to_finished",
)
PAIRED_COMMON_MARKS = (
    ("cover_preparation_started",),
    ("cover_cold_validation_combined_started",),
    ("cover_cold_decode_validated",),
    ("cover_local_derivative_prepared",),
    ("cover_render_ready",),
    ("cover_preparation_finished",),
)
PAIRED_COMMON_INTERVALS = (
    "start_to_combined",
    "combined_to_cold_decode_validated",
    "cold_decode_validated_to_derivative_prepared",
    "derivative_prepared_to_render_ready",
    "render_ready_to_finished",
)
MAX_MEMBER = 16 * 1024**2
MAX_ARCHIVE_CONTENTS = 256 * 1024**2


def archive_members(archive):
    """Reject ambiguous or unsafe archives before the original analyzer sees them."""
    members = {}
    canonical_names = set()
    total = 0
    for member in archive:
        name = member.name
        canonical = name.rstrip("/") if member.isdir() else name
        path = PurePosixPath(canonical)
        if (not name or "\\" in name or ":" in name or path.is_absolute()
                or any(part in ("", ".", "..") for part in canonical.split("/"))
                or canonical in canonical_names or member.sparse or not (member.isdir() or member.isreg())):
            raise ValueError("unsafe_archive")
        total += member.size
        if len(members) >= 1000 or member.size < 0 or total > MAX_ARCHIVE_CONTENTS:
            raise ValueError("oversized_archive")
        members[name] = member
        canonical_names.add(canonical)
    return members


def read_trace(archive, members, case):
    member = members.get(f"out/{case}/application.json")
    if member is None or not member.isreg() or not 0 < member.size <= MAX_MEMBER:
        raise ValueError("invalid_trace_member")
    stream = archive.extractfile(member)
    if stream is None:
        raise ValueError("invalid_trace_member")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result

    def invalid_constant(_value):
        raise ValueError("invalid_json_constant")

    data = stream.read(MAX_MEMBER + 1)
    if len(data) != member.size:
        raise ValueError("incomplete_trace_member")
    try:
        return json.loads(data, object_pairs_hook=unique_object, parse_constant=invalid_constant)
    except (UnicodeDecodeError, ValueError):
        raise ValueError("invalid_trace_json") from None


def jobs_from_trace(trace):
    """Parse complete, nonoverlapping jobs without inventing job identifiers."""
    return _jobs_from_trace(trace, MARKS, INTERVALS, capture_kernel=True, ignore_extra_cpu=False)


def _jobs_from_trace(trace, marks, intervals, capture_kernel, ignore_extra_cpu):
    """Parse jobs from ordered markers, optionally ignoring extra CPU-only markers."""
    if not isinstance(trace, dict) or not isinstance(trace.get("events"), list):
        raise ValueError("invalid_events")
    jobs = []
    stamps = []
    selected_kernel = None
    wanted = {name for choices in marks for name in choices}
    previous = -1
    for event in trace["events"]:
        if not isinstance(event, dict) or not isinstance(event.get("name"), str):
            raise ValueError("invalid_event")
        timestamp = event.get("at_us")
        if type(timestamp) is not int or timestamp < 0 or timestamp < previous:
            raise ValueError("invalid_clock_order")
        previous = timestamp
        name = event["name"]
        if not name.startswith("cover_"):
            continue
        if ignore_extra_cpu and name.startswith("cover_cpu_") and name not in wanted:
            continue
        if name not in wanted or len(stamps) >= len(marks) or name not in marks[len(stamps)]:
            raise ValueError("unexpected_or_overlapping_cover_marker")
        stamps.append(timestamp)
        if capture_kernel and len(stamps) == 5:
            selected_kernel = "neon" if name == "cover_cpu_neon_started" else "scalar"
        if len(stamps) == len(marks):
            measured = {label: right - left for label, left, right in zip(intervals, stamps[:-1], stamps[1:], strict=True)}
            parsed = {"interval_us": measured, "total_wall_us": stamps[-1] - stamps[0]}
            if capture_kernel:
                parsed["kernel_available"] = selected_kernel
            jobs.append(parsed)
            stamps = []
    if stamps or len(jobs) != 6:
        raise ValueError("incomplete_six_serial_misses")
    return jobs


def paired_common_jobs_from_trace(trace):
    """Parse coarse paired markers while ignoring candidate-only CPU detail markers."""
    return _jobs_from_trace(trace, PAIRED_COMMON_MARKS, PAIRED_COMMON_INTERVALS,
                            capture_kernel=False, ignore_extra_cpu=True)


def replay(attempt, paired_common=False):
    plan = json.loads((attempt.parent / "plan.json").read_text(encoding="utf-8"))
    check_protocol(plan)
    if (plan.get("mode") != "confirmation" or plan.get("regime") != "cold"
            or plan.get("pairs") != 10):
        raise ValueError("not_cold_confirmation")
    with tarfile.open(attempt / "output.tar.gz", "r:gz") as archive:
        members = archive_members(archive)
        verdict = analyze(attempt)
        if (verdict.get("regime") != "cold" or verdict.get("pairs_expected") != 10
                or verdict.get("valid_staged_cohort") is not True
                or verdict.get("confirmed_staged_benefit") is not True):
            raise ValueError("confirmation_not_accepted")
        if paired_common:
            pairs = []
            for number in range(1, 11):
                baseline_case = f"baseline-{number:02}"
                candidate_case = f"candidate-{number:02}"
                try:
                    baseline_jobs = paired_common_jobs_from_trace(read_trace(archive, members, baseline_case))
                    candidate_jobs = paired_common_jobs_from_trace(read_trace(archive, members, candidate_case))
                    baseline_sum = {label: sum(job["interval_us"][label] for job in baseline_jobs) for label in PAIRED_COMMON_INTERVALS}
                    candidate_sum = {label: sum(job["interval_us"][label] for job in candidate_jobs) for label in PAIRED_COMMON_INTERVALS}
                    interval_diff = {label: baseline_sum[label] - candidate_sum[label] for label in PAIRED_COMMON_INTERVALS}
                    pairs.append({"pair": number, "attributable": True, "baseline_case": baseline_case, "candidate_case": candidate_case,
                                  "baseline_interval_sum_us": baseline_sum, "candidate_interval_sum_us": candidate_sum,
                                  "interval_diff_us": interval_diff,
                                  "total_wall_diff_us": sum(job["total_wall_us"] for job in baseline_jobs) - sum(job["total_wall_us"] for job in candidate_jobs)})
                except ValueError as error:
                    pairs.append({"pair": number, "attributable": False, "baseline_case": baseline_case,
                                  "candidate_case": candidate_case, "reason": str(error)})
            complete = all(pair["attributable"] for pair in pairs)
            summary = None
            if complete:
                summary = {
                    "median_pair_interval_diff_us": {label: median(pair["interval_diff_us"][label] for pair in pairs)
                                                     for label in PAIRED_COMMON_INTERVALS},
                    "interval_candidate_wins": {label: sum(pair["interval_diff_us"][label] > 0 for pair in pairs)
                                                for label in PAIRED_COMMON_INTERVALS},
                    "median_pair_total_job_diff_us": median(pair["total_wall_diff_us"] for pair in pairs),
                }
            return {
                "measurement": "paired elapsed wall time, baseline minus candidate",
                "scope": "ten baseline/candidate visit pairs; each arm sums six serial cold jobs from shared coarse markers only",
                "interpretation": "coarse attribution only; do not infer NEON/hash execution from these windows",
                "kernel_note": "candidate-only CPU markers are ignored for coarse attribution",
                "all_pairs_attributable": complete,
                "pairs": pairs,
                "summary": summary,
            }
        visits = []
        for number in range(1, 11):
            case = f"candidate-{number:02}"
            try:
                jobs = jobs_from_trace(read_trace(archive, members, case))
                sums = {label: sum(job["interval_us"][label] for job in jobs) for label in INTERVALS}
                visits.append({"case": case, "attributable": True, "jobs": jobs,
                               "interval_sum_us": sums, "total_wall_us": sum(job["total_wall_us"] for job in jobs)})
            except ValueError as error:
                visits.append({"case": case, "attributable": False, "reason": str(error)})
    complete = all(visit["attributable"] for visit in visits)
    summary = None
    if complete:
        summary = {
            "median_job_interval_us": {label: median(job["interval_us"][label] for visit in visits
                                                     for job in visit["jobs"]) for label in INTERVALS},
            "median_visit_interval_sum_us": {label: median(visit["interval_sum_us"][label] for visit in visits)
                                              for label in INTERVALS},
        }
        summary["intervals_ranked_by_median_visit_sum"] = sorted(
            INTERVALS, key=lambda label: (-summary["median_visit_interval_sum_us"][label], label))
    return {
        "measurement": "elapsed wall time, not CPU time",
        "scope": "ten timed candidate visits; sums cover six serial cold jobs, not inter-job gaps or the entire launch",
        "interpretation": "descriptive workload only; mixed intervals and totals are not A/B savings",
        "kernel_note": "neon marks available kernels, not proof that every operation used them",
        "all_visits_attributable": complete,
        "visits": visits,
        "summary": summary,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("attempt", type=Path)
    parser.add_argument("--paired-common", action="store_true",
                        help="report paired baseline-minus-candidate sums from shared coarse markers")
    args = parser.parse_args()
    try:
        result = replay(args.attempt, paired_common=args.paired_common)
    except (OSError, ValueError, KeyError, TypeError, EOFError, tarfile.TarError):
        print("Could not validate the closed cold confirmation attempt.", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result.get("all_visits_attributable", result.get("all_pairs_attributable")) else 2


if __name__ == "__main__":
    sys.exit(main())
