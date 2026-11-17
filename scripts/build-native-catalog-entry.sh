#!/bin/sh
# Build a version-qualified runtime without bundling firmware libraries.
set -eu
CATALOG_SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
CATALOG_SOURCE=$(CDPATH= cd "$CATALOG_SCRIPT_DIR/../src/native_catalog_entry" && pwd)
CATALOG_OUTPUT=${1:?Pass the catalog runtime output directory}
CATALOG_ZIG=${ZIG_BIN_DIR:+$ZIG_BIN_DIR/}zig
mkdir -p "$CATALOG_OUTPUT"
"$CATALOG_ZIG" cc -target arm-linux-gnueabihf.2.4 \
    -mcpu=cortex_a8 -mfpu=vfpv3-d16 -mfloat-abi=hard \
    -std=c11 -O2 -Wall -Wextra -Werror -fstack-protector-strong -fPIE -pie \
    -Wl,-z,relro,-z,now -s "$CATALOG_SOURCE/catalog-client.c" \
    -ldl -lrt -o "$CATALOG_OUTPUT/aidoku-catalog-client"
CATALOG_CLIENT_HASH=$(sha256sum "$CATALOG_OUTPUT/aidoku-catalog-client" | cut -d ' ' -f 1)
"$CATALOG_ZIG" cc -target arm-linux-gnueabihf.2.4 \
    -mcpu=cortex_a8 -mfpu=vfpv3-d16 -mfloat-abi=hard \
    -std=c11 -O2 -Wall -Wextra -Werror -fstack-protector-strong -fPIC -shared \
    -Wl,-z,relro,-z,now -s "-DENTRY_CLIENT_SHA256=\"$CATALOG_CLIENT_HASH\"" \
    "$CATALOG_SOURCE/hooks.c" -ldl -lrt -o "$CATALOG_OUTPUT/aidoku-entry-hooks.so"
CATALOG_HOOK_HASH=$(sha256sum "$CATALOG_OUTPUT/aidoku-entry-hooks.so" | cut -d ' ' -f 1)
"$CATALOG_ZIG" cc -target arm-linux-gnueabihf.2.4 \
    -mcpu=cortex_a8 -mfpu=vfpv3-d16 -mfloat-abi=hard \
    -std=c11 -O2 -Wall -Wextra -Werror -fstack-protector-strong -fPIE -pie \
    -Wl,-z,relro,-z,now -s "-DENTRY_HOOKS_SHA256=\"$CATALOG_HOOK_HASH\"" \
    "-DENTRY_CLIENT_SHA256=\"$CATALOG_CLIENT_HASH\"" \
    "$CATALOG_SOURCE/dispatch.c" "$CATALOG_SOURCE/sha256.c" \
    -lrt -o "$CATALOG_OUTPUT/aidoku-library-dispatch"
(
    cd "$CATALOG_OUTPUT"
    sha256sum aidoku-library-dispatch aidoku-entry-hooks.so aidoku-catalog-client >SHA256SUMS
)
