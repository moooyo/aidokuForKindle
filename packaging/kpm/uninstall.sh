#!/bin/sh
# Remove unchanged program files only; preserve data and device-side backups.
set -eu
umask 077
PACKAGE_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
MANIFEST=$PACKAGE_DIR/payload.tsv
HOME_DIR=/mnt/us/aidoku
fail() { printf '%s\n' "aidoku-kpm: $*" >&2; exit 64; }
regular() { [ -f "$1" ] && [ ! -L "$1" ]; }
hash() { [ "${#1}" -eq 64 ] && case "$1" in *[!0-9a-f]*) false ;; *) true ;; esac; }
sha() (
    regular "$1" || exit 1
    line=$(sha256sum "$1") || exit 1
    digest=${line%% *}; hash "$digest" || exit 1
    printf '%s\n' "$digest"
)
no_app() (
    p=''; if p=$(pidof aidoku 2>/dev/null); then exit 1; else c=$?; [ "$c" -eq 1 ] && [ -z "$p" ]; fi
)
safe_relative() {
    case "$1" in ''|/*|*/|*//*|*[!A-Za-z0-9_./-]*) return 1 ;; esac
    case "/$1/" in */../*|*/./*|*/data/*) return 1 ;; esac
    case "$1" in aidoku/*|documents/Aidoku.sh) ;; *) return 1 ;; esac
}
safe_destination() (
    safe_relative "$1" || exit 1
    path=/mnt/us; saved=$IFS; IFS=/; set -f; set -- $1; IFS=$saved
    for item do path=$path/$item; [ ! -L "$path" ] || exit 1; done
    [ ! -e "$path" ] || regular "$path"
)
# KPM removes its old package directory during upgrade; canonical data stays put.
if [ "${1:-}" = upgrade ]; then exit 0; fi
# KPM also invokes uninstall after a failed install hook. Such a hook already
# restored its program backups, which this cleanup must leave untouched.
if ! regular "$PACKAGE_DIR/.aidoku-installation-complete"; then exit 0; fi
regular "$MANIFEST" && [ "$(cat "$PACKAGE_DIR/.aidoku-installation-complete")" = "$(sha "$MANIFEST")" ] || fail 'The completed package marker differs.'
no_app && [ ! -e /tmp/aidoku-launcher.lock ] && [ ! -L /tmp/aidoku-launcher.lock ] || fail 'Exit Aidoku before uninstalling this package.'
regular "$MANIFEST" || fail 'The installed program allowlist is unavailable.'
while IFS='|' read -r expected size mode relative; do
    hash "$expected" && safe_relative "$relative" && safe_destination "$relative" || fail 'An uninstall destination is unsafe.'
    target=/mnt/us/$relative
    if regular "$target" && [ "$(sha "$target")" = "$expected" ]; then rm "$target"; fi
done <"$MANIFEST"
rmdir /mnt/us/aidoku/app 2>/dev/null || :
# User data, imports, progress and private rollback copies remain on the device.
