#!/bin/sh
# Rebuild the import tests using the shared six-job verification worker.
set -eu
root=/root/aidoku-all-import-20261119
if [ "${1:-}" != --mounted ]; then
    exec unshare --mount --propagation private sh "$0" --mounted
fi
mkdir -p "$root/upper" "$root/work" "$root/target" "$root/tmp" "$root/artifacts" "$root/reports"
exec 8>"$root/build.lock"; flock -w 600 8
exec 9>/root/aidoku-input-discovery-20261116/startup-build.lock; flock -w 600 9
lowers=/root/aidoku-next-status-20261118/upper:/root/aidoku-next-cold-20261118/upper:/root/aidoku-input-discovery-20261116/target-upper:/root/aidoku-display-modes-20261114-01a1da18/target-upper:/root/aidoku-y4-remaining-20261113-yg7wh3iy/target
mount -t overlay overlay -o "lowerdir=$lowers,upperdir=$root/upper,workdir=$root/work" "$root/target"
trap 'umount "$root/target"' EXIT
. /root/.cargo/env
cd "$root/source"
export CARGO_TARGET_DIR="$root/target" CARGO_BUILD_JOBS=6 TMPDIR="$root/tmp"
export CARGO_PROFILE_RELEASE_LTO=false CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16
cargo test -j6 --release --no-default-features --features kindle --locked --offline --lib --no-run --message-format=json >"$root/reports/cargo.jsonl" 2>"$root/reports/build.log" || { tail -14 "$root/reports/build.log"; exit 1; }
test_bin=$(python3 -I -c 'import json,sys; print(next(e["executable"] for row in open(sys.argv[1]) if (e:=json.loads(row)).get("executable") and e["target"]["name"]=="aidoku_kindle" and e["profile"]["test"]))' "$root/reports/cargo.jsonl")
cp "$test_bin" "$root/artifacts/host-tests"
if "$root/artifacts/host-tests" 'app::import_thumbnails::tests' --test-threads=1 >"$root/reports/import-tests.log" 2>&1; then
    cat "$root/reports/import-tests.log"
else
    cat "$root/reports/import-tests.log" >&2
    exit 1
fi
