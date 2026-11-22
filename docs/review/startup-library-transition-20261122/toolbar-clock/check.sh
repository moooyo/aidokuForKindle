#!/bin/sh
set -eu

directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT HUP INT TERM
mkdir -p "$scratch/probe/src"
cp "$directory/Cargo.toml" "$directory/evaluate.py" "$scratch/probe/"
cp "$directory/src/main.rs" "$scratch/probe/src/"
export CARGO_TARGET_DIR="$scratch/target"
cargo build -j6 --release --offline --manifest-path "$scratch/probe/Cargo.toml"
cargo zigbuild -j6 --release --offline --target armv7-unknown-linux-musleabihf \
    --manifest-path "$scratch/probe/Cargo.toml"
python3 -B "$scratch/probe/evaluate.py" "$scratch/target/release/toolbar-clock-probe" --bench 30
python3 -B "$scratch/probe/evaluate.py" \
    "$scratch/target/armv7-unknown-linux-musleabihf/release/toolbar-clock-probe" --qemu --bench 0
