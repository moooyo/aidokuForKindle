#!/bin/sh
set -efu
export LC_ALL=C
home=/mnt/us/aidoku
main=$home/app/aidoku
launcher=$home/launch.sh
portrait=$home/app/aidoku-startup-portrait
fail() { printf 'refused %s\n' "$1" >&2; exit 1; }
plain() { [ -f "$1" ] && [ ! -L "$1" ] && [ -x "$1" ] && [ "$(readlink -f "$1")" = "$1" ]; }
paths() {
    for folder in "$home" "$home/app"; do
        [ -d "$folder" ] && [ ! -L "$folder" ] && [ "$(readlink -f "$folder")" = "$folder" ] || return 1
        stat -c '%d:%i' "$folder" || return 1
    done
    for file in "$main" "$launcher" "$portrait"; do
        plain "$file" && stat -c '%d:%i:%s:%a:%y:%z' "$file" || return 1
    done
}
native() {
    [ "$(uname -m)" = armv7l ] &&
        [ "$(cat /sys/class/graphics/fb0/stride)" = 1872 ] &&
        [ "$(cat /sys/class/graphics/fb0/bits_per_pixel)" = 8 ] &&
        [ "$(lipc-get-prop -s com.lab126.powerd state)" = active ] &&
        [ "$(lipc-get-prop -s com.lab126.appmgrd activeApp)" = com.lab126.KPPMainApp ] || return 1
    case "$(status statusbar)" in *start/running*) ;; *) return 1;; esac
    for reader in aidoku weread bilicomics; do
        if pidof "$reader" >/dev/null; then return 1; else [ "$?" = 1 ] || return 1; fi
        [ ! -e "/tmp/$reader-launcher.lock" ] && [ ! -L "/tmp/$reader-launcher.lock" ] || return 1
    done
    managers=$(pidof awesome) && [ -n "$managers" ] || return 1
    for manager in $managers; do
        case "$manager" in ''|*[!0-9]*) return 1;; esac
        case "$(sed -n 's/^State:[[:space:]]*//p' "/proc/$manager/status")" in R*|S*) ;; *) return 1;; esac
        birth=$(awk '{print $22}' "/proc/$manager/stat") || return 1
        case "$birth" in ''|*[!0-9]*) return 1;; esac
        printf '%s:%s\n' "$manager" "$birth"
    done
}
opened() {
    for descriptor in 7 8 9; do
        case "$descriptor" in 7) file=$main;; 8) file=$launcher;; 9) file=$portrait;; esac
        bound=/proc/$$/fd/$descriptor
        [ "$(readlink "$bound")" = "$file" ] || return 1
        actual=$(stat -L -c '%d:%i:%s:%a:%y:%z' "$bound") || return 1
        expected=$(stat -c '%d:%i:%s:%a:%y:%z' "$file") || return 1
        [ "$actual" = "$expected" ] || return 1
    done
}
frame() {
    bound=/proc/$$/fd/$1
    size=$(stat -L -c %s "$bound") && mode=$(stat -L -c %a "$bound") || return 1
    case "$size" in ''|*[!0-9]*) return 1;; esac
    case "$mode" in [0-7][0-7][0-7]) ;; *) return 1;; esac
    [ "$size" -gt 0 ] && [ "$size" -le "$3" ] || return 1
    printf '%s %s %s\n' "$2" "$size" "$mode" && head -c "$size"
}
initial_native=$(native) || fail native_admission
initial=$(paths) || fail program_admission
exec 7<"$main" 8<"$launcher" 9<"$portrait"
opened && [ "$(paths)" = "$initial" ] || fail changed_before_transfer
printf 'AIDOKU-READ-1\n'
frame 7 app/aidoku 100663296 <&7 &&
    frame 8 launch.sh 98304 <&8 &&
    frame 9 app/aidoku-startup-portrait 262144 <&9 || fail program_stream
current=$(paths) && [ "$current" = "$initial" ] && opened || fail program_changed
final_native=$(native) && [ "$final_native" = "$initial_native" ] || fail native_changed
printf 'AIDOKU-READ-END\n'
