#!/bin/sh
# Rebuild only inside the candidate's private Cargo overlay on test-env.
set -eu
ROOT=/root/aidoku-next-status-20261118
if [ "${1:-}" != --mounted ]; then
    case "${1:-}" in arm|host|desktop|check) ;; *) exit 64;; esac
    exec unshare --mount --propagation private sh "$0" --mounted "$1"
fi
MODE=$2
cd "$ROOT/source"
umask 077
exec 8>"$ROOT/build.lock"
flock -w 300 8
exec 9>/root/aidoku-input-discovery-20261116/startup-build.lock
flock -w 300 9
LOWERS=/root/aidoku-next-cold-20261118/upper:/root/aidoku-input-discovery-20261116/target-upper:/root/aidoku-display-modes-20261114-01a1da18/target-upper:/root/aidoku-y4-remaining-20261113-yg7wh3iy/target
mount -t overlay overlay -o "lowerdir=$LOWERS,upperdir=$ROOT/upper,workdir=$ROOT/work" "$ROOT/target"
trap 'umount "$ROOT/target"' EXIT
. /root/.cargo/env
export PATH="/root/weread-build/tools/zig-x86_64-linux-0.15.2:$PATH"
export CARGO_TARGET_DIR="$ROOT/target" CARGO_BUILD_JOBS=6 TMPDIR="$ROOT/tmp"
export CARGO_PROFILE_RELEASE_LTO=false CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16
if [ "$MODE" = check ]; then
    python3 -I "$ROOT/tools/build_inputs.py" "$ROOT/source" "$ROOT/reports/source-check.json"
    rustc -Vv
    cargo -V
    zig version
    exit 0
fi
OUT=$(mktemp -d "$ROOT/reports/$MODE-XXXXXXXX")
python3 -I "$ROOT/tools/build_inputs.py" "$ROOT/source" "$OUT/source-before.json"
if [ "$MODE" = arm ]; then
    TARGET=armv7-unknown-linux-musleabihf
    cargo clean --profile release --target "$TARGET" --offline -p aidoku-kindle -p slint-backend-kindle >"$OUT/clean.log" 2>&1
    cargo zigbuild -j6 --locked --offline --release --no-default-features --features kindle --target "$TARGET" --bin aidoku --message-format=json-render-diagnostics >"$OUT/cargo.jsonl" 2>"$OUT/build.log"
    python3 -I "$ROOT/tools/build_inputs.py" "$ROOT/source" "$OUT/source-after.json"
    cmp "$OUT/source-before.json" "$OUT/source-after.json"
    python3 -I "$ROOT/tools/build_inputs.py" --record "$OUT" arm "$ROOT/artifacts/candidate-arm"
elif [ "$MODE" = host ]; then
    cargo clean --profile release --offline -p aidoku-kindle >"$OUT/clean.log" 2>&1
    for kind in lib bin; do
        case "$kind" in lib) set -- --lib ;; bin) set -- --bin aidoku ;; esac
        cargo test -j6 --release --locked --offline --no-default-features --features kindle "$@" --no-run --message-format=json-render-diagnostics >"$OUT/$kind.jsonl" 2>"$OUT/$kind-build.log"
        exe=$(python3 -I "$ROOT/tools/build_inputs.py" --test-executable "$OUT/$kind.jsonl" "$kind")
        "$exe" --test-threads=6 >"$OUT/$kind-tests.log" 2>&1
        tail -2 "$OUT/$kind-tests.log"
    done
    python3 -I "$ROOT/tools/build_inputs.py" "$ROOT/source" "$OUT/source-after.json"
    cmp "$OUT/source-before.json" "$OUT/source-after.json"
elif [ "$MODE" = desktop ]; then
    cargo clean --profile release --offline -p aidoku-kindle >"$OUT/clean.log" 2>&1
    cargo build -j6 --release --locked --offline --bin aidoku --message-format=json-render-diagnostics >"$OUT/cargo.jsonl" 2>"$OUT/build.log"
    python3 -I "$ROOT/tools/build_inputs.py" "$ROOT/source" "$OUT/source-after.json"
    cmp "$OUT/source-before.json" "$OUT/source-after.json"
    python3 -I "$ROOT/tools/build_inputs.py" --record "$OUT" desktop "$ROOT/artifacts/candidate-desktop"
fi
printf '%s\n' "$OUT"
