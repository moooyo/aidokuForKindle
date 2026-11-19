#!/bin/sh
# Build the optional startup portrait supervisor on the Linux worker.
set -eu
dir=$(CDPATH= cd "$(dirname "$0")" && pwd)
out=${1:?Pass the output path for the startup portrait supervisor}
mkdir -p "$(dirname "$out")"
"${ZIG_BIN_DIR:+$ZIG_BIN_DIR/}zig" cc -target arm-linux-gnueabihf.2.4 \
    -mcpu=cortex_a8 -mfpu=vfpv3-d16 -mfloat-abi=hard \
    -std=c11 -O2 -Wall -Wextra -Werror -fstack-protector-strong -fPIE -pie \
    -Wl,-z,relro,-z,now -s "$dir/../src/native_query/startup_portrait.c" -o "$out"
