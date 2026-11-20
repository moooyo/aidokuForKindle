#!/bin/sh
set -efu
export LC_ALL=C OMP_THREAD_LIMIT=1 DISPLAY=:0
umask 077
fail() { printf 'refused %s\n' "$1" >&2; exit 1; }
[ "$#" -eq 2 ] || fail arguments
operation=$1; label=$2
case "$label" in ''|*[!0-9a-f]*) fail label;; esac
[ "${#label}" -eq 16 ] || fail label
stage=/mnt/us/.aidoku-native-$label
state=/var/tmp/aidoku-native-$label
kit=$stage/input
plain() { [ -f "$1" ] && [ ! -L "$1" ] && [ "$(readlink -f "$1")" = "$1" ]; }
same() { plain "$1" && printf '%s\n' "$2" | cmp -s - "$1"; }
put() { (set -C; printf '%s\n' "$2" >"$1"); }
native() {
    same "$state/owner" "$label" && same "$stage/owner" "$label" &&
        [ "$(lipc-get-prop -s com.lab126.powerd state)" = active ] &&
        [ "$(lipc-get-prop -s com.lab126.appmgrd activeApp)" = com.lab126.KPPMainApp ] || return 1
    case "$(status statusbar)" in *start/running*) ;; *) return 1;; esac
    pids=$(pidof awesome) && [ -n "$pids" ] || return 1
    for pid in $pids; do
        case "$(sed -n 's/^State:[[:space:]]*//p' "/proc/$pid/status")" in R*|S*) ;; *) return 1;; esac
    done
    for reader in aidoku weread bilicomics; do
        [ ! -e "/tmp/$reader-launcher.lock" ] && [ ! -L "/tmp/$reader-launcher.lock" ] || return 1
        if pidof "$reader" >/dev/null; then return 1; else [ "$?" = 1 ] || return 1; fi
    done
}
probe() {
    native && "$kit/lib/ld-linux-armhf.so.3" --inhibit-cache --library-path "$kit/lib" \
        "$kit/$1" "$2" "$kit/models"
}
inspect() {
    result=$(probe native-target navigation) || return 1
    printf '%s\n' "$result" | awk '
    /^\{"screen":\[1860,2480\],"pointer":\[[0-9]+,[0-9]+\],"buttons_clear":true,"home":\{"count":1,"box":\[[0-9]+,[0-9]+,[0-9]+,[0-9]+\],"underline_pct":[0-9]+\},"library":\{"count":1,"box":\[[0-9]+,[0-9]+,[0-9]+,[0-9]+\],"underline_pct":[0-9]+\},"title":\{"count":0\}\}$/ {
        gsub(/[^0-9]+/," "); if (NF!=17) exit 1;
        if ($3 >= 1860 || $4 >= 2480 || $6 >= $8 || $7 >= $9 || $8 >= $12 ||
            $12 >= $14 || $13 >= $15 || $7 < 2108 || $13 < 2108 || $8 >= 1860 ||
            $14 >= 1860 || $9 >= 2480 || $15 >= 2480) exit 1;
        good=1;
    } END {if(!good) exit 1}' >/dev/null || return 1
    printf '%s\n' "$result" | sed -n 's/^{"screen":\[1860,2480\],"pointer":\[\([0-9][0-9]*\),\([0-9][0-9]*\)\],"buttons_clear":true,"home":{"count":1,"box":\[[0-9,]*\],"underline_pct":\([0-9][0-9]*\)},"library":{"count":1,"box":\[[0-9,]*\],"underline_pct":\([0-9][0-9]*\)},"title":{"count":0}}$/\1 \2 \3 \4/p' |
        awk 'NF==4 && $1<1860 && $2<2480 {
            if ($3>=60 && $3<=100 && $4<=20) {print "home",$1,$2; good=1}
            if ($4>=60 && $4<=100 && $3<=20) {print "library",$1,$2; good=1}
        } END {if(!good) exit 1}'
}
await_tab() {
    tries=5
    until here=$(inspect) && [ "${here%% *}" = "$1" ]; do
        tries=$((tries - 1)); [ "$tries" -gt 0 ] || return 1; sleep 1
    done
}
move_to() {
    here=$(inspect) || return 1
    [ "${here%% *}" != "$1" ] || return 0
    probe card-input "nav-$1" >/dev/null && await_tab "$1"
}
native || fail native
memo=$state/input-original
case "$operation" in
    inspect) inspect || fail stock_tabs;;
    library)
        if [ ! -e "$memo" ] && [ ! -L "$memo" ]; then
            start=$(inspect) && put "$memo" "$start" || fail save_original
        fi
        plain "$memo" || fail original
        move_to library || fail library
        printf 'stock_library\n';;
    restore)
        if [ ! -e "$memo" ] && [ ! -L "$memo" ]; then printf 'no_input_owned\n'; exit 0; fi
        plain "$memo" || fail original
        saved=$(cat "$memo"); set -- $saved
        [ "$#" = 3 ] && same "$memo" "$saved" || fail original
        case "$1" in home|library) ;; *) fail original;; esac
        case "$2:$3" in *[!0-9:]*) fail original;; esac
        [ "$2" -lt 1860 ] && [ "$3" -lt 2480 ] || fail original
        move_to "$1" && native || fail restored_tab
        "$kit/lib/ld-linux-armhf.so.3" --inhibit-cache --library-path "$kit/lib" \
            "$kit/card-input" restore-pointer "$2" "$3" >/dev/null || fail restored_pointer
        [ "$(inspect)" = "$saved" ] || fail final_state
        printf 'stock_restored\n';;
    *) fail operation;;
esac
