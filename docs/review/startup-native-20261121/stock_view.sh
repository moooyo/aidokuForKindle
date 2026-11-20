#!/bin/sh
set -efu
export LC_ALL=C
fail() { printf 'refused %s\n' "$1" >&2; exit 1; }
native() {
    [ "$(uname -m)" = armv7l ] && [ "$(lipc-get-prop -s com.lab126.powerd state)" = active ] &&
        [ "$(lipc-get-prop -s com.lab126.appmgrd activeApp)" = com.lab126.KPPMainApp ] || return 1
    case "$(status statusbar)" in *start/running*) ;; *) return 1;; esac
    for reader in aidoku weread bilicomics; do
        [ ! -e "/tmp/$reader-launcher.lock" ] && [ ! -L "/tmp/$reader-launcher.lock" ] || return 1
        if pidof "$reader" >/dev/null; then return 1; else [ "$?" = 1 ] || return 1; fi
    done
    wm=$(pidof awesome) && [ -n "$wm" ] || return 1
    for pid in $wm; do
        case "$(sed -n 's/^State:[[:space:]]*//p' "/proc/$pid/status")" in R*|S*) ;; *) return 1;; esac
    done
    printf '%s\n' "$wm"
}
geometry() {
    base=/sys/class/graphics/fb0
    [ "$(cat "$base/stride")" = 1872 ] && [ "$(cat "$base/bits_per_pixel")" = 8 ] || return 1
    turn=$(cat "$base/rotate"); pan=$(cat "$base/pan"); size=$(cat "$base/virtual_size")
    case "$turn" in 1|3) ;; *) return 1;; esac
    case "$size:$pan" in 1872,4960:0,0|1872,4960:0,2480|1872,2480:0,0|1860,2480:0,0) ;; *) return 1;; esac
    printf '%s %s %s\n' "$turn" "${pan#0,}" "$size"
}
initial=$(native) || fail native_idle
before=$(geometry) || fail panel_geometry
set -- $before
printf 'ASTOCK1 %s\n' "$before"
dd if=/dev/fb0 bs=1872 skip="$2" count=2480 2>/dev/null | gzip -1c
[ "$before" = "$(geometry)" ] && [ "$initial" = "$(native)" ] || fail changed_view
