"""Run and replay the fixed, counterbalanced first-Library software-render comparison."""

import argparse
import json
import os
from pathlib import Path
import shutil
from statistics import median
import subprocess
import tempfile


PROFILES = {"scribe": (930, 1240), "pw6": (632, 840)}
ARMS = ("control", "candidate")
PAIRS = 8
TEST = "app::jpeg_probe_trial::render_first_library"
COLUMNS = ["arm", "platform_us", "attached_us", "rendered_us", "helper_us", "frame_probes",
           "final_probes", "frame_reason", "dark_pixels", "sort_pointer_opened_and_closed"]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def schedule(pair):
    profiles = list(PROFILES)
    if pair % 2 == 0:
        profiles.reverse()
    for profile in profiles:
        arms = ARMS if (pair + (profile == "pw6")) % 2 else ARMS[::-1]
        yield profile, arms


def summarize(visits):
    expected = [(pair, profile, arms) for pair in range(1, PAIRS + 1)
                for profile, arms in schedule(pair)]
    require(len(visits) == len(expected), "Partial or duplicate cohort")
    groups = {profile: [] for profile in PROFILES}
    for visit, (pair, profile, arms) in zip(visits, expected):
        width, height = PROFILES[profile]
        require((visit["pair"], visit["profile"]) == (pair, profile)
                and len(visit["arms"]) == 2 and visit["rgb_equal"] is True
                and visit["reference_equal"] is True, "Wrong pair, viewport or pixels")
        observed = {}
        for data, arm in zip(visit["arms"], arms):
            require(len(data) == len(COLUMNS), "Incomplete sample")
            sample = dict(zip(COLUMNS, data))
            require(sample["arm"] == arm and sample["frame_probes"] == int(arm == "control")
                    and sample["final_probes"] == 1 and sample["sort_pointer_opened_and_closed"] is True
                    and sample["frame_reason"] == ("available" if arm == "control" else "not_probed"),
                    "Invalid order, probe admission or shelf action")
            times = [sample[key] for key in ("platform_us", "attached_us", "rendered_us", "helper_us")]
            require(all(type(value) is int and value > 0 for value in times)
                    and times[0] < times[1] < times[2] and (arm != "control" or times[3] <= times[0])
                    and 1000 < sample["dark_pixels"] < width * height * 3 // 4, "Unqualified raster or clock")
            observed[arm] = sample
        control, candidate = observed["control"], observed["candidate"]
        require(control["dark_pixels"] == candidate["dark_pixels"], "Inconsistent pixel accounting")
        groups[profile].append((arms[0], control, candidate))
    result = {}
    for profile, group in groups.items():
        deltas = [control["rendered_us"] - candidate["rendered_us"] for _, control, candidate in group]
        result[profile] = {
            "pairs": len(group), "candidate_earlier": sum(delta > 0 for delta in deltas),
            "control_frame_median_us": median(control["rendered_us"] for _, control, _ in group),
            "candidate_frame_median_us": median(candidate["rendered_us"] for _, _, candidate in group),
            "paired_control_minus_candidate_us": deltas, "paired_median_us": median(deltas),
            "control_first_paired_median_us": median(delta for delta, (first, _, _) in zip(deltas, group) if first == "control"),
            "candidate_first_paired_median_us": median(delta for delta, (first, _, _) in zip(deltas, group) if first == "candidate"),
            "control_helper_median_us": median(control["helper_us"] for _, control, _ in group),
        }
    return result


def run_sample(binary, helper, profile, arm, directory):
    width, height = PROFILES[profile]
    with tempfile.TemporaryDirectory(dir=directory) as name:
        case = Path(name)
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("AIDOKU_", "SLINT_")) and key not in ("DISPLAY", "WAYLAND_DISPLAY")}
        env.update(HOME=name, TMPDIR=name, XDG_CACHE_HOME=str(case / "cache"),
                   XDG_CONFIG_HOME=str(case / "config"), XDG_DATA_HOME=str(case / "xdg-data"),
                   XDG_RUNTIME_DIR=name, LC_ALL="C", TZ="UTC", AIDOKU_JPEG_TRIAL_CASE=name,
                   AIDOKU_JPEG_TRIAL_PROFILE=profile, AIDOKU_JPEG_TRIAL_ARM=arm,
                   AIDOKU_JPEG_TRIAL_HELPER=str(helper))
        subprocess.run([str(binary), "--ignored", "--exact", TEST, "--test-threads=1"],
                       env=env, check=True, timeout=60)
        sample = json.loads((case / "sample.json").read_text())
        require((sample["arm"], sample["profile"], sample["width"], sample["height"])
                == (arm, profile, width, height), "Mismatched case receipt")
        pixels = (case / "shelf.rgb").read_bytes()
        require(len(pixels) == width * height * 3, "Short or surplus RGB buffer")
        require((case / "metadata.receipt").read_bytes() == b"metadata-only\n", "No successful exchange")
        return [sample[column] for column in COLUMNS], pixels


def binary_identity(binary):
    stat = binary.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def run(binary):
    binary = binary.resolve(strict=True)
    identity = binary_identity(binary)
    visits, references = [], {}
    with tempfile.TemporaryDirectory(prefix="jpeg-admission-") as directory:
        helper = Path(directory) / "metadata-worker"
        shutil.copyfile(Path(__file__).with_name("metadata_fixture.py"), helper)
        helper.chmod(0o700)
        for pair in range(PAIRS + 1):
            for profile, arms in schedule(pair):
                samples, frames = [], []
                for arm in arms:
                    require(binary_identity(binary) == identity, "Executable changed during trial")
                    sample, frame = run_sample(binary, helper, profile, arm, directory)
                    samples.append(sample)
                    frames.append(frame)
                equal = frames[0] == frames[1]
                reference_equal = frames[0] == references.setdefault(profile, frames[0])
                require(equal and reference_equal, "Full RGB buffers differ")
                if pair:
                    visits.append({"pair": pair, "profile": profile, "rgb_equal": equal,
                                   "reference_equal": reference_equal, "arms": samples})
    return {"schema": 1, "warmup_pairs_per_profile": 1, "sample_columns": COLUMNS,
            "visits": visits, "summary": summarize(visits)}


def write_report(result, path):
    with path.open("x", encoding="utf-8") as output:
        preface = {key: result[key] for key in ("schema", "warmup_pairs_per_profile", "sample_columns")}
        output.write(json.dumps(preface)[:-1] + ',\n"visits": [\n')
        output.write(",\n".join(json.dumps(visit) for visit in result["visits"]))
        output.write('\n],\n"summary": ' + json.dumps(result["summary"], indent=2) + "\n}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    collect = modes.add_parser("run")
    collect.add_argument("binary", type=Path)
    collect.add_argument("output", type=Path)
    modes.add_parser("check").add_argument("report", type=Path)
    args = parser.parse_args()
    if args.mode == "run":
        if args.output.exists():
            parser.error("The output already exists")
        result = run(args.binary)
        write_report(result, args.output)
    else:
        result = json.loads(args.report.read_text(encoding="utf-8"))
        require(result["schema"] == 1 and result["sample_columns"] == COLUMNS and result["warmup_pairs_per_profile"] == 1,
                "Incompatible fixture or sample schema")
        require(result["summary"] == summarize(result["visits"]), "Published summary differs from replay")
    print(json.dumps(result["summary"], indent=2))
