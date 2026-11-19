#!/bin/sh
# Keep all Cargo writes in a private overlay; the operator runs this on test-env.
set -eu
root=/root/aidoku-all-build-20261119
if [ "${1:-}" != --mounted ]; then
    case "${2:-}" in arm|host|desktop) ;; *) exit 64;; esac
    exec unshare --mount --propagation private sh "$0" --mounted "$@"
fi
shift
umask 077
exec 8>"$root/build.lock"; flock -w 300 8
exec 9>/root/aidoku-input-discovery-20261116/startup-build.lock; flock -w 300 9
lower=/root/aidoku-next-status-20261118/upper:/root/aidoku-next-cold-20261118/upper:/root/aidoku-input-discovery-20261116/target-upper:/root/aidoku-display-modes-20261114-01a1da18/target-upper:/root/aidoku-y4-remaining-20261113-yg7wh3iy/target
mount -t overlay overlay -o "lowerdir=$lower,upperdir=$root/upper,workdir=$root/work" "$root/target"
trap 'umount "$root/target"' EXIT
. /root/.cargo/env
export PATH="/root/weread-build/tools/zig-x86_64-linux-0.15.2:$PATH"
export CARGO_TARGET_DIR="$root/target" CARGO_BUILD_JOBS=6 TMPDIR="$root/tmp"
export CARGO_PROFILE_RELEASE_LTO=false CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16
python3 -B -I "$root/tools/integration.py" build "$@"
