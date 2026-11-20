#!/bin/bash
# Operator-side transfer only. Manifest, validation, build and tests run on test-env.
set -euo pipefail
label=${1:-}
[[ $label =~ ^[a-z][a-z0-9-]{0,40}$ ]] || exit 64
root=/root/aidoku-launch-fast-20261119
archive=$(mktemp -t aidoku-launch-fast.tar)
trap 'rm -f -- "$archive"' EXIT
items=(Cargo.toml Cargo.lock build.rs LICENSE THIRD_PARTY_NOTICES.md README.md AGENTS.md src ui assets vendor examples tests packaging scripts)
for opt in .cargo rust-toolchain.toml; do if [[ -e $opt ]]; then items+=("$opt"); fi; done
COPYFILE_DISABLE=1 /usr/bin/tar -czf "$archive" --no-mac-metadata --no-xattrs --no-acls --no-fflags \
    --exclude='__pycache__' --exclude='*.pyc' --exclude='target' --exclude='.DS_Store' "${items[@]}"
ssh -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 test-env "umask 077; dd of='$root/incoming/$label.tar' conv=excl status=none" <"$archive"
ssh test-env "cd '$root' && python3 -B -I tools/integration.py freeze '$label' 'incoming/$label.tar'"
