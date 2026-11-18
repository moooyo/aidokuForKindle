"""Verify the two-patch product tree on test-env, without building or contacting a device."""

import argparse
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tarfile
import tempfile


BASE = "78c5b2bc09a469133c886eb0d73aaf9ed63195b2"
BASE_MANIFEST = "0cae90b40762d6bb6b46da48dcbadc82999726b308dd7a7cbea6ba28bf08782c"
COLD_MANIFEST = "73f360f06b112c7f34a27b6f2ff90e5237d2146b9444aff0871bfcbafcea4549"
FINAL_MANIFEST = "1c7909c4efefd88d80cdacab12f1e832fa0533f3b62ef094cd956f16ca75f768"
ELF = "9d92dc4cd01005a6ba2f48fe61786e5df5570c43feb5709fe3159bb164807d0f"
COLD = {"src/cache.rs", "src/imaging.rs", "src/render_cache.rs"}
TOOLBAR = {"src/app/view.rs", "src/bootstrap_metrics.rs", "src/main.rs", "src/platform.rs", "src/platform_startup_toolbar.rs"}
PATCHES = (
    ("cold", "../cold-trial/candidate.patch", "68245c90746815240c33958a7b717cf8dc636aa94593372828ea3268d17d9af4", COLD, COLD_MANIFEST),
    ("toolbar", "status.patch", "248cae78c6ac12d7811ef120e8649a50808c60a86b6241241aab53a3a20f8613", TOOLBAR, FINAL_MANIFEST),
)


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def manifest(directory):
    # This is the same canonical ordered-file algorithm used by the package verifier.
    verifier = runpy.run_path(str(directory / "scripts/verify-kindle-package.py"))
    value = verifier["source_manifest"](directory)
    value["algorithm"] = verifier["SOURCE_ALGORITHM"]
    return value


def git(directory, *args):
    return subprocess.check_output(["git", "-C", str(directory), *args], text=True)


def index(value):
    return {entry["path"]: entry for entry in value["files"]}


def different(first, second):
    left, right = index(first), index(second)
    return {key for key in left.keys() | right.keys() if left.get(key) != right.get(key)}


def write(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--tree", type=Path, help="Check the final source fingerprint without modifying it")
    args.add_argument("--base-archive", type=Path, help="git archive with the base commit and all production inputs")
    args.add_argument("--cold-patch", type=Path)
    args.add_argument("--status-patch", type=Path)
    args.add_argument("--build-root", type=Path)
    args.add_argument("--output", type=Path)
    opts = args.parse_args()
    require(sys.platform == "linux", "Run on test-env")
    sys.dont_write_bytecode = True
    if opts.tree:
        final = manifest(opts.tree.resolve(strict=True))
        require(final["sha256"] == FINAL_MANIFEST and final["file_count"] == 360, "Unexpected product tree")
        print(final["sha256"])
        return
    require(all((opts.base_archive, opts.cold_patch, opts.status_patch, opts.build_root, opts.output)), "Missing replay inputs")
    with opts.base_archive.open("rb") as archive:
        commit = subprocess.check_output(["git", "get-tar-commit-id"], stdin=archive, text=True).strip()
    require(commit == BASE, "Archive lacks the required immutable base revision")
    opts.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=opts.output) as tmp:
        work = Path(tmp)
        with tarfile.open(opts.base_archive) as archive:
            archive.extractall(work, filter="data")
        git(work, "init", "-q")
        git(work, "add", ".")
        base = manifest(work)
        require(base["file_count"] == 359 and base["sha256"] == BASE_MANIFEST, "Unexpected base source")
        previous = base
        chain = []
        for (stage, name, sha, allowed, expected), patch in zip(PATCHES, (opts.cold_patch, opts.status_patch)):
            patch = patch.resolve(strict=True)
            require(digest(patch) == sha, "Patch bytes differ: " + stage)
            git(work, "apply", "--check", "--whitespace=error-all", str(patch))
            git(work, "apply", "--whitespace=error-all", str(patch))
            current = manifest(work)
            require(current["sha256"] == expected and different(previous, current) == allowed,
                    "Unexpected production change set: " + stage)
            chain.append({"stage": stage, "patch_relative_to_this_directory": name, "patch_sha256": sha,
                          "input_source_sha256": previous["sha256"], "output_source_sha256": expected})
            previous = current
        final = previous
        changed_worktree = set(git(work, "ls-files", "--modified", "--others", "--exclude-standard").splitlines())
        require(changed_worktree == COLD | TOOLBAR, "Unexpected worktree content beyond product changes")
        supplied = manifest(opts.build_root / "source")
        require(supplied == final, "Replayed source differs from the original ARM supplier")
        builds = [path for path in (opts.build_root / "reports").glob("arm-*/identity.json")
                  if json.loads(path.read_text())["artifact_sha256"] == ELF]
        require(len(builds) == 1, "Missing or ambiguous measured ARM build")
        build = builds[0].parent
        identity = json.loads(builds[0].read_text())
        require(identity["source_manifest_sha256"] == FINAL_MANIFEST and identity["source_file_count"] == 360,
                "ARM build used other source")
        require((build / "source-before.json").read_bytes() == (build / "source-after.json").read_bytes(),
                "Build inputs changed during actual compilation")
        require(identity["compiler_artifact_event_log_sha256"] == digest(build / "cargo.jsonl")
                and identity["build_log_sha256"] == digest(build / "build.log"), "Compiler logs changed")
        actual = opts.build_root / "artifacts/candidate-arm"
        require(digest(actual) == ELF and actual.stat().st_size == identity["artifact_bytes"], "ELF differs")
        write(opts.output / "combined-source-manifest.json", final)
        old, new = index(base), index(final)
        result = {"base_commit": BASE, "source_files_before": base["file_count"], "source_files_after": final["file_count"],
                  "patches_in_order": chain, "changed_files": [
                    {"path": f, "base_sha256": old.get(f, {}).get("sha256"), "candidate_sha256": new[f]["sha256"]}
                    for f in sorted(changed_worktree)], "source_sha256": final["sha256"],
                  "elf_sha256": ELF, "elf_bytes": actual.stat().st_size, "build_record_sha256": digest(builds[0]),
                  "actual_compiler_input_and_output_verified": True, "replayed_all_product_source_bytes_match_supplier": True,
                  "scope": "Independent test-env replay from base Git archive, not a rebuild or device operation."}
        write(opts.output / "verified-source-chain.json", result)
        print(json.dumps({"files": len(changed_worktree), "source_files": final["file_count"], "source": FINAL_MANIFEST, "elf": ELF}))


if __name__ == "__main__":
    main()
