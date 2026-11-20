#!/bin/sh
set -eu
target=${CARGO_TARGET_DIR:?Set the candidate target directory}
arm=$target/armv7-unknown-linux-musleabihf/release
set -- "$arm"/build/aidoku-kindle-*/out/libaidoku_cover_neon.a
if [ "$#" -ne 1 ] || [ ! -f "$1" ] || [ ! -x "$arm/aidoku" ]; then
    echo 'Build the candidate ARM binary in a fresh project target first.' >&2
    exit 1
fi
scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT
zig cc -target arm-linux-musleabihf -mcpu=cortex_a8 -mfpu=vfpv3-d16 \
    -ffp-contract=off -fno-fast-math -fno-vectorize -fno-slp-vectorize \
    -O3 -std=c11 -static scripts/tests/cover-neon-parity.c "$1" -lm -o "$scratch/parity"
qemu-arm -cpu cortex-a7 "$scratch/parity"
