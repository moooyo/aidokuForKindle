#!/bin/sh
# Build and test a disposable backend copy inside a private mount namespace.
set -eu
ROOT=${OWNER_PROBE_WORKSPACE:?}
BACKEND=${OWNER_PROBE_BACKEND:?}
LOWERS=${OWNER_PROBE_CARGO_LOWERS:?}
LOCK=${OWNER_PROBE_CARGO_LOCK:?}
if [ "${1:-}" != --mounted ]; then
    exec unshare --mount --propagation private sh "$0" --mounted
fi
umask 077
mkdir -p "$ROOT/cargo-upper" "$ROOT/cargo-work" "$ROOT/cargo-target" "$ROOT/tmp"
exec 9>"$LOCK"
flock -w 480 9
mount -t overlay overlay -o "lowerdir=$LOWERS,upperdir=$ROOT/cargo-upper,workdir=$ROOT/cargo-work" "$ROOT/cargo-target"
trap 'umount "$ROOT/cargo-target"' EXIT
. "$HOME/.cargo/env"
export CARGO_TARGET_DIR="$ROOT/cargo-target" CARGO_BUILD_JOBS=6 TMPDIR="$ROOT/tmp"
cd "$BACKEND"
cargo test -j6 --locked --offline --lib --no-run --message-format=json-render-diagnostics >"$ROOT/build.jsonl"
BIN=$(python3 -c 'import json,sys; print(next(x["executable"] for x in map(json.loads,open(sys.argv[1])) if x.get("executable") and x["profile"]["test"]))' "$ROOT/build.jsonl")
flock -u 9
TEST=platform::resident_session_tests::a_single_real_slint_window_survives_two_disposable_presentations
timeout 15 "$BIN" --exact "$TEST" --nocapture --test-threads=1 >"$ROOT/session-tests.log" 2>&1 || { cat "$ROOT/session-tests.log"; exit 1; }
cat "$ROOT/session-tests.log"
timeout 70 "$BIN" --skip "$TEST" --test-threads=6 >"$ROOT/existing-backend.log" 2>&1 || { tail -30 "$ROOT/existing-backend.log"; exit 1; }
tail -2 "$ROOT/existing-backend.log"
python3 -c 'import json,sys; lines=open(sys.argv[1]).read().splitlines(); data=next(json.loads(l.split("SESSION_OBSERVATION ",1)[1]) for l in lines if "SESSION_OBSERVATION " in l); print(json.dumps({"schema":1,"scope":"Linux host; real Slint software window, test-backed native owners; not Kindle firmware, EPDC, evdev or a real Library admission", "existing_backend_tests":next(l.strip() for l in open(sys.argv[2]) if l.startswith("test result: ok.")),"evidence":data},indent=2,sort_keys=True))' "$ROOT/session-tests.log" "$ROOT/existing-backend.log" >"$ROOT/session-results.json"
