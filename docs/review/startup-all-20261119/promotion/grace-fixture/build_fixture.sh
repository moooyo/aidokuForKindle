#!/bin/sh
# Compile a test-only adapter on test-env, with a private writable Cargo overlay.
set -eu
root=/root/aidoku-all-grace-qualification-20261119
case "${1:-}" in /root/aidoku-all-grace-qualification-20261119/private/grace-*) ;; *) exit 64;; esac
batch=$1
if [ "${2:-}" != mounted ]; then
    exec unshare --mount --propagation private sh "$0" "$batch" mounted
fi
test "$(readlink -f "$batch")" = "$batch" && test -f "$batch/source/Cargo.toml"
umask 077
exec 8>"$root/build.lock"; flock -w 300 8
exec 9>/root/aidoku-all-build-20261119/build.lock; flock -w 300 9
exec 7>/root/aidoku-input-discovery-20261116/startup-build.lock; flock -w 300 7
for dir in target upper work tmp; do mkdir "$batch/$dir"; done
lower=/root/aidoku-all-build-20261119/upper:/root/aidoku-next-status-20261118/upper:/root/aidoku-next-cold-20261118/upper:/root/aidoku-input-discovery-20261116/target-upper:/root/aidoku-display-modes-20261114-01a1da18/target-upper:/root/aidoku-y4-remaining-20261113-yg7wh3iy/target
# Six immutable lower directories exceed the fsconfig string limit. Use
# util-linux's traditional mount syscall for this host-only build namespace.
LIBMOUNT_FORCE_MOUNT2=always mount -t overlay overlay -o "lowerdir=$lower,upperdir=$batch/upper,workdir=$batch/work" "$batch/target"
trap 'umount "$batch/target"' EXIT
. /root/.cargo/env
export CARGO_TARGET_DIR="$batch/target" CARGO_BUILD_JOBS=6 TMPDIR="$batch/tmp"
export CARGO_PROFILE_RELEASE_LTO=false CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16
cd "$batch/source"
cargo test -j6 --release --locked --offline --no-default-features --features kindle --lib --no-run \
  --message-format=json-render-diagnostics >"$batch/cargo.jsonl" 2>"$batch/cargo.log"
python3 -B -I - "$batch" <<'PY'
import json,shutil,sys
from pathlib import Path
base=Path(sys.argv[1]);out=[]
for raw in (base/'cargo.jsonl').read_text().splitlines():
    row=json.loads(raw)
    if row.get('reason')=='compiler-artifact' and row.get('manifest_path')==str(base/'source/Cargo.toml') and row.get('profile',{}).get('test') and row.get('executable') and 'lib' in row['target']['kind']:
        out.append(Path(row['executable']))
if len(out)!=1 or not out[0].is_relative_to(base/'target') or not out[0].is_file():
    raise SystemExit('No unique source-bound test executable')
shutil.copyfile(out[0],base/'probe');(base/'probe').chmod(0o700)
PY
