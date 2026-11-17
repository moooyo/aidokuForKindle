#!/bin/sh
# Build only on the designated Linux worker; firmware libraries are not bundled.
set -eu
ENTRY_SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
ENTRY_REPO=$(CDPATH= cd "$ENTRY_SCRIPT_DIR/.." && pwd)
ENTRY_OUTPUT=${1:?Pass the native entry output directory}
shift
ENTRY_ZIG=${ZIG_BIN_DIR:+$ZIG_BIN_DIR/}zig
mkdir -p "$ENTRY_OUTPUT"
"$ENTRY_ZIG" cc -target arm-linux-gnueabihf.2.4 \
    -mcpu=cortex_a8 -mfpu=vfpv3-d16 -mfloat-abi=hard \
    -std=c11 -O2 -Wall -Wextra -Werror -fstack-protector-strong -fPIC -shared \
    -Wl,-z,relro,-z,now -s "$@" "$ENTRY_REPO/src/native_entry/hooks.c" \
    -ldl -lrt -o "$ENTRY_OUTPUT/aidoku-entry-hooks.so"
ENTRY_HOOK_HASH=$(sha256sum "$ENTRY_OUTPUT/aidoku-entry-hooks.so" | cut -d ' ' -f 1)
"$ENTRY_ZIG" cc -target arm-linux-gnueabihf.2.4 \
    -mcpu=cortex_a8 -mfpu=vfpv3-d16 -mfloat-abi=hard \
    -std=c11 -O2 -Wall -Wextra -Werror -fstack-protector-strong -fPIE -pie \
    -Wl,-z,relro,-z,now -s "$@" "-DENTRY_HOOKS_SHA256=\"$ENTRY_HOOK_HASH\"" \
    "$ENTRY_REPO/src/native_entry/dispatch.c" "$ENTRY_REPO/src/native_entry/sha256.c" \
    -lrt -o "$ENTRY_OUTPUT/aidoku-library-dispatch"
