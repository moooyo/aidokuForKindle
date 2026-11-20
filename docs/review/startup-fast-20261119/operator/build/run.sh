#!/bin/sh
# All compilation writes to an independent overlay. Never use global cargo clean.
set -eu
root=/root/aidoku-launch-fast-20261119
if [ "${1:-}" != --mounted ]; then
    case "${2:-}" in arm|host|desktop|backend|probe) ;; *) exit 64;; esac
    exec unshare --mount --propagation private sh "$0" --mounted "$@"
fi
shift
umask 077
label=$1; mode=$2
exec 8>>"$root/build.lock"; flock -w 300 8
exec 9</root/aidoku-input-discovery-20261116/startup-build.lock; flock -w 300 9
lower=/root/aidoku-all-build-20261119/upper:/root/aidoku-next-status-20261118/upper:/root/aidoku-next-cold-20261118/upper:/root/aidoku-input-discovery-20261116/target-upper:/root/aidoku-display-modes-20261114-01a1da18/target-upper:/root/aidoku-y4-remaining-20261113-yg7wh3iy/target
mount -t overlay overlay -o "lowerdir=$lower,upperdir=$root/upper,workdir=$root/work" "$root/target"
trap 'umount "$root/target"' EXIT
. /root/.cargo/env
export PATH="/root/weread-build/tools/zig-x86_64-linux-0.15.2:$PATH"
export CARGO_TARGET_DIR="$root/target" CARGO_BUILD_JOBS=6 TMPDIR="$root/tmp"
export CARGO_PROFILE_RELEASE_LTO=false CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16
case "$mode" in
    probe)
        mountpoint -q "$CARGO_TARGET_DIR"
        test -f "$CARGO_TARGET_DIR/armv7-unknown-linux-musleabihf/release/aidoku"
        test -d "$CARGO_TARGET_DIR/release/deps"
        printf 'Bound isolated target with retained ARM and host dependency layers\n';;
    backend)
        python3 -B -I "$root/tools/integration.py" check "$label"
        dir="$root/reports/$label/backend"; mkdir "$dir"
        # Test in a private Cargo work dir to retain all original frozen source inputs.
        (cd "$root/sources/$label/vendor/slint-backend-kindle" && cargo test --release -j6 --locked --offline --lib -- --test-threads=6) >"$dir/tests.log" 2>&1
        python3 -B -I "$root/tools/integration.py" check "$label";;
    *) python3 -B -I "$root/tools/integration.py" build "$label" "$mode";;
esac
