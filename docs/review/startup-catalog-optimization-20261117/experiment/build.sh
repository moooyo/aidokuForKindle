#!/bin/sh
# Build this research-only artifact on test-env.
set -eu
CATALOG_SOURCE=$(CDPATH= cd "$(dirname "$0")" && pwd)
CATALOG_OUTPUT=${1:?Pass the diagnostic output directory}
CATALOG_ZIG=${ZIG_BIN_DIR:+$ZIG_BIN_DIR/}zig
mkdir -p "$CATALOG_OUTPUT"
"$CATALOG_ZIG" cc -target arm-linux-gnueabihf.2.4 \
    -mcpu=cortex_a8 -mfpu=vfpv3-d16 -mfloat-abi=hard \
    -std=c11 -O2 -Wall -Wextra -Werror -fstack-protector-strong -fPIC -shared \
    -Wl,-z,relro,-z,now -s "$CATALOG_SOURCE/hooks.c" \
    -ldl -lrt -o "$CATALOG_OUTPUT/aidoku-entry-hooks.so"
CATALOG_HASH=$(sha256sum "$CATALOG_OUTPUT/aidoku-entry-hooks.so" | cut -d ' ' -f 1)
"$CATALOG_ZIG" cc -target arm-linux-gnueabihf.2.4 \
    -mcpu=cortex_a8 -mfpu=vfpv3-d16 -mfloat-abi=hard \
    -std=c11 -O2 -Wall -Wextra -Werror -fstack-protector-strong -fPIE -pie \
    -Wl,-z,relro,-z,now -s "-DENTRY_HOOKS_SHA256=\"$CATALOG_HASH\"" \
    "$CATALOG_SOURCE/dispatch.c" "$CATALOG_SOURCE/sha256.c" \
    -lrt -o "$CATALOG_OUTPUT/aidoku-library-dispatch"
