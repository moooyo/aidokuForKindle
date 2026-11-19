#!/bin/sh
# Run the private process, lifecycle, and transport tests on the Linux worker.
set -eu
dir=$(CDPATH= cd "$(dirname "$0")" && pwd)
observer=${1:-$dir/../native-library-visit.rs}
work=$(mktemp -d)
trap 'rm -rf "$work"' 0
trap 'exit 1' HUP INT TERM
cc -std=c11 -O2 -Wall -Wextra -Werror -fstack-protector-strong -fPIE -pie \
    -Wl,-z,relro,-z,now -s "$dir/launch_guardian.c" -o "$work/aidoku-launch-guardian"
python3 "$dir/launch_guardian_test.py" "$work/aidoku-launch-guardian" -v
cp "$dir/Cargo.toml" "$dir/probe_lib.rs" "$dir/native_library_idle.rs" "$dir/native_library_wait.rs" "$work/"
cp "$observer" "$work/native_library_visit.rs"
cd "$work"
CARGO_BUILD_JOBS=6 cargo test --offline
CARGO_BUILD_JOBS=6 cargo check --offline --target armv7-unknown-linux-musleabihf
if [ "${AIDOKU_TEST_FULL_IDLE_TTL:-0}" = 1 ]; then
    CARGO_BUILD_JOBS=6 cargo test --offline production_home_deadline_expires_after_45_seconds -- --ignored --nocapture
fi
