#!/bin/sh
# Execute on the designated remote Linux build worker, never on the operator PC.
set -eu

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd "$SCRIPT_DIR/.." && pwd)
TARGET=armv7-unknown-linux-musleabihf
CARGO_TARGET_DIR=${CARGO_TARGET_DIR:-$REPO_ROOT/target}
CARGO_BUILD_JOBS=${CARGO_BUILD_JOBS:-6}
STAGING_ROOT=$REPO_ROOT/dist/kindle

if [ -n "${ZIG_BIN_DIR:-}" ]; then
    [ -x "$ZIG_BIN_DIR/zig" ] || { printf '%s\n' 'ZIG_BIN_DIR must contain an executable zig.' >&2; exit 1; }
    PATH=$ZIG_BIN_DIR:$PATH
    export PATH
fi
command -v cargo >/dev/null 2>&1 || { printf '%s\n' 'Cargo is required.' >&2; exit 1; }
command -v zig >/dev/null 2>&1 || { printf '%s\n' 'Zig is required; add it to PATH or set ZIG_BIN_DIR.' >&2; exit 1; }
export CARGO_TARGET_DIR CARGO_BUILD_JOBS
cd "$REPO_ROOT"
cargo zigbuild --locked --release --no-default-features --features kindle --target "$TARGET"

mkdir -p "$STAGING_ROOT/aidoku/app" "$STAGING_ROOT/aidoku/licenses/backend" \
    "$STAGING_ROOT/aidoku/licenses/pinyin" "$STAGING_ROOT/aidoku/licenses/sources" \
    "$STAGING_ROOT/aidoku/licenses/icons" "$STAGING_ROOT/aidoku/licenses/demo" \
    "$STAGING_ROOT/extensions/aidoku" "$STAGING_ROOT/documents"
install -m 755 "$CARGO_TARGET_DIR/$TARGET/release/aidoku" "$STAGING_ROOT/aidoku/app/aidoku"
install -m 755 packaging/kindle/launch.sh "$STAGING_ROOT/aidoku/launch.sh"
install -m 755 packaging/kindle/document-launcher.sh "$STAGING_ROOT/documents/Aidoku.sh"
install -m 644 packaging/kindle/README.md "$STAGING_ROOT/aidoku/README.md"
install -m 644 LICENSE "$STAGING_ROOT/aidoku/LICENSE"
install -m 644 THIRD_PARTY_NOTICES.md "$STAGING_ROOT/aidoku/THIRD_PARTY_NOTICES.md"
install -m 644 packaging/kual/menu.json packaging/kual/config.xml "$STAGING_ROOT/extensions/aidoku/"
install -m 755 packaging/kual/set-profile.sh "$STAGING_ROOT/extensions/aidoku/"
for notice in LICENSE-MIT LICENSE-APACHE UPSTREAM_REVISION PATCHES.md AIDOKU_PROVENANCE.md; do
    install -m 644 "vendor/slint-backend-kindle/$notice" "$STAGING_ROOT/aidoku/licenses/backend/$notice"
done
# Fonts, dictionary data, and icons are embedded in the ELF. Retain their full
# license/provenance records without staging another copy of those payloads.
install -m 644 assets/pinyin/LICENSE-AOSP.txt assets/pinyin/README.md "$STAGING_ROOT/aidoku/licenses/pinyin/"
install -m 644 src/sources/aidoku-rs.LICENSE src/sources/README.md "$STAGING_ROOT/aidoku/licenses/sources/"
install -m 644 assets/icons/README.md "$STAGING_ROOT/aidoku/licenses/icons/README.md"
install -m 644 assets/demo/README.md assets/demo/manifest.json "$STAGING_ROOT/aidoku/licenses/demo/"
if [ -d assets/fonts ]; then
    mkdir -p "$STAGING_ROOT/aidoku/licenses/fonts"
    for notice in assets/fonts/*.txt assets/fonts/*.json assets/fonts/*.md; do
        [ -f "$notice" ] || continue
        install -m 644 "$notice" "$STAGING_ROOT/aidoku/licenses/fonts/$(basename "$notice")"
    done
fi
python3 scripts/package-kindle.py --staging "$STAGING_ROOT" --output dist/aidoku-kindle-development.zip
printf '%s\n' "Staged ARM development bundle: $STAGING_ROOT"
