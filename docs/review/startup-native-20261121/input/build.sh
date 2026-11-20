#!/bin/sh
set -efu
[ "$(pwd -P)" = /work ] && [ -f /opt/kindle-toolchain/env.sh ]
lib=/work/host-ocr/lib
python3 -B mark_probes.py
set -- -target arm-linux-gnueabihf.2.35 -O2 -Wall -Wextra -Werror -std=c11 tile_input.c \
    "$lib/libX11.so.6" "$lib/libXtst.so.6" -Wl,--allow-shlib-undefined
zig cc "$@" -o tile-input
zig cc -DAIDOKU_TEST_FRAME "$@" -o fixture-input
readelf -h tile-input | grep -Eq 'Machine:.*ARM'
readelf -h tile-input | grep -Eq 'Flags:.*hard-float ABI'
qemu-arm -cpu cortex-a9 "$lib/ld-linux-armhf.so.3" --inhibit-cache --library-path "$lib" --list ./tile-input >tile-runtime.txt
