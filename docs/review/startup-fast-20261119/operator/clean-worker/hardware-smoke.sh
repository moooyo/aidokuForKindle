#!/bin/sh
set -eu
export LC_ALL=C
umask 077

refuse() { printf 'refused %s\n' "$1"; exit 1; }
[ "$#" = 2 ] || refuse arguments
mode=$1
case "$mode" in check|run|close) ;; *) refuse mode;; esac
label=$2
[ "${#label}" = 16 ] || refuse label
case "$label" in *[!0-9a-f]*) refuse label;; esac
stage=/mnt/us/.aidoku-hw-$label

remove_files() {
    for entry in "$stage"/* "$stage"/.[!.]* "$stage"/..?*; do
        [ -e "$entry" ] || [ -L "$entry" ] || continue
        case "${entry##*/}" in aidoku-arm|cover-parity|r3-launch.sh|owner) ;; *) return 1;; esac
    done
    rm -f "$stage/aidoku-arm" "$stage/cover-parity" "$stage/r3-launch.sh" &&
        rm "$stage/owner" && rmdir "$stage"
}

if [ "$mode" = close ]; then
    if [ -e "$stage" ] || [ -L "$stage" ]; then
        [ -d "$stage" ] && [ ! -L "$stage" ] && [ -f "$stage/owner" ] &&
            [ ! -L "$stage/owner" ] && [ "$(cat "$stage/owner")" = "$label" ] || refuse unowned_stage
        exec 9<"$stage"
        flock -n 9 || refuse operation_in_progress
        remove_files || refuse cleanup
    fi
    printf 'stage_absent\n'
    exit 0
fi

native_idle() {
    [ "$(uname -m)" = armv7l ] || refuse architecture
    [ "$(cat /sys/class/graphics/fb0/stride)" = 1872 ] &&
        [ "$(cat /sys/class/graphics/fb0/bits_per_pixel)" = 8 ] || refuse display_profile
    case "$(lipc-get-prop -s com.lab126.powerd state)" in
        active) ;; screenSaver) refuse stock_asleep;; *) refuse power_state;; esac
    [ "$(lipc-get-prop -s com.lab126.appmgrd activeApp)" = com.lab126.KPPMainApp ] || refuse foreground
    case "$(status statusbar)" in *start/running*) ;; *) refuse statusbar;; esac
    for process in aidoku weread bilicomics; do
        if pidof "$process" >/dev/null; then refuse active_reader; fi
    done
    for lock in /tmp/aidoku-launcher.lock /tmp/weread-launcher.lock /tmp/bilicomics-launcher.lock /mnt/us/.aidoku-hw-*; do
        [ ! -e "$lock" ] && [ ! -L "$lock" ] || refuse existing_operation
    done
    managers=$(pidof awesome) || refuse window_manager
    for manager in $managers; do
        case "$(sed -n 's/^State:[[:space:]]*//p' "/proc/$manager/status")" in R*|S*) ;; *) refuse window_manager;; esac
    done
}

[ ! -e "$stage" ] && [ ! -L "$stage" ] || refuse existing_stage
native_idle
if [ "$mode" = check ]; then printf 'native_idle stage_absent\n'; exit 0; fi
grep -qE '^(Features|flags)[[:space:]]*:.*[[:space:]]neon([[:space:]]|$)' /proc/cpuinfo || refuse neon
awk '$2=="/mnt/us" {mounted=1} END {exit !mounted}' /proc/mounts || refuse storage
df -Pk /mnt/us | awk 'END {exit !(NF>=6 && $4+0>=131072)}' || refuse free_space
installed=/mnt/us/aidoku/launch.sh
[ -f "$installed" ] && [ ! -L "$installed" ] || refuse installed_launcher
command -v flock >/dev/null || refuse staging_lock
mkdir "$stage" || refuse stage_creation
exec 9<"$stage"
flock -n 9 || { rmdir "$stage"; refuse staging_lock; }
printf '%s\n' "$label" >"$stage/owner" || { rmdir "$stage"; exit 74; }
finish() {
    result=$?
    trap - 0 HUP INT TERM
    remove_files || exit 74
    printf 'stage_removed\n'
    exit "$result"
}
trap finish 0
trap 'exit 1' HUP INT TERM
tar -xzf - -C "$stage" || refuse payload
cmp -s "$installed" "$stage/r3-launch.sh" || refuse different_launcher
printf 'installed_launcher_matches\n'
version=$("$stage/aidoku-arm" --version)
[ "$version" = 'aidoku 0.1.3' ] || refuse candidate
policy=$("$stage/aidoku-arm" --offline --device scribe --data-dir "$stage/absent" --print-startup-options)
[ "$policy" = 'portrait=shell
prewarm=0
last_cover_grace=off
import_thumbnail=off
first_layout=optimized
resident=off' ] && [ ! -e "$stage/absent" ] || refuse defaults
printf 'candidate_cli defaults_ok\n'
matches=$(ulimit -t 30; exec nice -n 10 "$stage/cover-parity")
[ "$matches" = 'matched 16777216 RGB triples, 128 tails and 1400 rows (2798527 f32 sums)' ] || refuse parity
printf '%s\n' "$matches"
