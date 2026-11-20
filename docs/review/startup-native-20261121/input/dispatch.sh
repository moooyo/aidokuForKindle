#!/bin/sh
set -efu
export LC_ALL=C DISPLAY=:0
umask 077
fail() { printf 'refused %s\n' "$1" >&2; exit 1; }
[ "$#" = 3 ] || fail tap_arguments
operation=$1; label=$2; name=$3
case "$label" in ''|*[!0-9a-f]*) fail tap_label;; esac
[ "${#label}" = 16 ] || fail tap_label
case "$name" in control-warmup|candidate-warmup|control-picture|candidate-picture|control-0[1-9]|candidate-0[1-9]) ;; *) fail tap_case;; esac
stage=/mnt/us/.aidoku-native-$label
state=/var/tmp/aidoku-native-$label
out=$state/out/$name
kit=$stage/input
plain() { [ -f "$1" ] && [ ! -L "$1" ] && [ "$(readlink -f "$1")" = "$1" ]; }
same() { plain "$1" && printf '%s\n' "$2" | cmp -s - "$1"; }
put() { (set -C; printf '%s\n' "$2" >"$1"); }
native() {
    same "$state/owner" "$label" && same "$stage/owner" "$label" && same "$state/current" "$name" &&
        same "$state/pointer-intent" "$label" && same "$out/origin" stock-tap &&
        plain "$state/input-original" && plain "$kit/artwork.guard" &&
        [ "$(lipc-get-prop -s com.lab126.powerd state)" = active ] &&
        [ "$(lipc-get-prop -s com.lab126.appmgrd activeApp)" = com.lab126.KPPMainApp ] || return 1
    case "$(status statusbar)" in *start/running*) ;; *) return 1;; esac
    for reader in aidoku weread bilicomics; do
        [ ! -e "/tmp/$reader-launcher.lock" ] && [ ! -L "/tmp/$reader-launcher.lock" ] || return 1
        if pidof "$reader" >/dev/null; then return 1; else [ "$?" = 1 ] || return 1; fi
    done
}
probe() {
    "$kit/lib/ld-linux-armhf.so.3" --inhibit-cache --library-path "$kit/lib" "$kit/tile-input" "$1" "$kit/artwork.guard"
}
native || fail tile_native_owner
case "$operation" in
    target)
        status=$(sh "$kit/navigation.sh" inspect "$label") && [ "${status%% *}" = library ] ||
            fail selected_library
        [ "$(probe match)" = unique_library_artwork ] &&
            put "$out/unique-tile" "$name" || fail verify_tile
        printf 'unique_library_artwork\n';;
    tap)
        same "$out/unique-tile" "$name" && put "$out/dispatched" stock-tap || fail duplicate_tap
        times=$(probe tap) || fail tap_not_confirmed
        set -- $times
        [ "$#" = 4 ] && put "$out/pointer-brackets" "$times" || fail tap_receipt
        printf '%s %s\n' "$1" "$2";;
    *) fail tap_operation;;
esac
