#!/bin/sh
set -eu
export LC_ALL=C
umask 077
fail() { printf 'refused %s\n' "$1" >&2; exit 1; }
if [ "$#" = 3 ] && [ "$1" = visit ]; then shift; elif [ "$0" != visit ]; then fail arguments; fi
[ "$#" = 2 ] || fail arguments
operation=$1; label=$2
case "$label" in ''|*[!0-9a-f]*) fail label;; esac
[ "${#label}" = 16 ] || fail label
media=/mnt/us
private=/var/tmp
fb=/dev/fb0
sysfb=/sys/class/graphics/fb0
locks=/tmp
mounts=/proc/mounts
proc=/proc
installed=$media/aidoku
stage=$media/.aidoku-native-$label
state=$private/aidoku-native-$label
out=$state/out

absent() { [ ! -e "$1" ] && [ ! -L "$1" ]; }
plain() { [ -f "$1" ] && [ ! -L "$1" ] && [ "$(readlink -f "$1")" = "$1" ]; }
directory() { [ -d "$1" ] && [ ! -L "$1" ] && [ "$(readlink -f "$1")" = "$1" ]; }
marker() { plain "$1" && printf '%s\n' "$label" | cmp -s - "$1"; }
put() { (set -C; printf '%s\n' "$label" >"$1"); }
ensure_mark() { if absent "$1"; then put "$1"; else marker "$1"; fi; }
owned() { directory "$1" && marker "$1/owner"; }
both_absent() { absent "$stage" && absent "$state"; }
number() { case "$1" in ''|*[!0-9]*) return 1;; *) return 0;; esac; }
space() { df -Pk "$1" | awk -v need="$2" 'END {exit !(NF>=6 && $4+0>=need)}'; }
lock_stage() {
    owned "$stage" || fail stage_owner
    exec 9<"$stage" || fail live_operation
    flock -n 9 || fail live_operation
    owned "$stage" || fail stage_owner
}
lock_both() { lock_stage; owned "$state" || fail stage_owner; }
panel() {
    [ "$(cat "$sysfb/stride")" = 1872 ] && [ "$(cat "$sysfb/bits_per_pixel")" = 8 ] || return 1
    rotation=$(cat "$sysfb/rotate") && geometry=$(cat "$sysfb/virtual_size") && pan=$(cat "$sysfb/pan") || return 1
    case "$rotation:$geometry:$pan" in [13]:1872,4960:0,0|[13]:1872,4960:0,2480|[13]:1872,2480:0,0|[13]:1860,2480:0,0) ;; *) return 1;; esac
    printf '%s %s %s\n' "$rotation" "$geometry" "${pan#0,}"
}
body() {
    observed=$(panel) || return 1
    offset=${observed##* }
    dd if="$fb" of="$1" bs=1872 skip=$((offset + 72)) count=2336 2>/dev/null &&
        [ "$(wc -c <"$1")" -eq 4372992 ] && [ "$observed" = "$(panel)" ]
}
stock_equal() {
    stock_tries=6
    until body "$state/stock.after" && cmp -s "$state/stock.before" "$state/stock.after"; do
        stock_tries=$((stock_tries - 1)); [ "$stock_tries" -gt 0 ] || return 1; usleep 300000
    done
}
installed_state() {
    for path in "$installed/app/aidoku" "$installed/launch.sh" "$installed/data/library.sqlite3"; do
        plain "$path" && stat -c '%d:%i:%s:%a:%y:%z' "$path" || return 1
    done
}
no_peer_stage() {
    for item in "$media"/.aidoku-native-* "$media"/.aidoku-pilot-* "$media"/.aidoku-hw-* "$private"/aidoku-native-* \
        "$media"/.weread-orientation-* "$media"/.weread-indexed-* "$media"/.weread-rollout-* "$media"/.bilicomics-*; do
        absent "$item" || [ "$item" = "$stage" ] || [ "$item" = "$state" ] || return 1
    done
}
media_mounted() { awk -v media="$media" '$2==media {found=1} END {exit !found}' "$mounts"; }
touch_stock_idle() {
    [ "$(lipc-get-prop -s com.lab126.appmgrd activeApp)" = com.lab126.KPPMainApp ] &&
        [ "$(lipc-get-prop -s com.lab126.powerd state)" = active ] &&
        lipc-set-prop -i com.lab126.powerd touchScreenSaverTimeout 1 </dev/null >/dev/null 2>&1
}
native_idle() {
    [ "$(uname -m)" = armv7l ] && media_mounted || return 1
    [ "$(lipc-get-prop -s com.lab126.powerd state)" = active ] &&
        [ "$(lipc-get-prop -s com.lab126.appmgrd activeApp)" = com.lab126.KPPMainApp ] || return 1
    case "$(status statusbar)" in *start/running*) ;; *) return 1;; esac
    for reader in aidoku weread bilicomics; do
        absent "$locks/$reader-launcher.lock" || return 1
        if pidof "$reader" >/dev/null; then return 1; else [ "$?" = 1 ] || return 1; fi
    done
    managers=$(pidof awesome) && [ -n "$managers" ] || return 1
    for manager in $managers; do
        number "$manager" || return 1
        case "$(sed -n 's/^State:[[:space:]]*//p' "$proc/$manager/status")" in R*|S*) ;; *) return 1;; esac
    done
    panel >/dev/null && no_peer_stage
}
admit() { directory "$media" && directory "$private" && directory "$installed" && native_idle && space "$media" 131072 && space "$private" 32768; }
guard() {
    plain "$stage/guard.sh" || return 1
    answer=$(sh "$stage/guard.sh" "$1" "$label") && [ "$answer" = "$2" ]
}
validate_leaves() {
    find "$stage" ! -type d ! -type f -print >"$state/other" && [ ! -s "$state/other" ] || return 1
    find "$stage" -type f -print >"$state/files" && [ "$(wc -l <"$state/files")" -eq 12 ] || return 1
    for leaf in owner launch.sh guard.sh app/aidoku app/aidoku-startup-portrait seed/library.sqlite3; do plain "$stage/$leaf" || return 1; done
    [ -x "$stage/app/aidoku" ] && [ -x "$stage/app/aidoku-startup-portrait" ] || return 1
    count=0
    while IFS= read -r path; do
        case "${path#"$stage"/}" in
            owner|launch.sh|guard.sh|app/aidoku|app/aidoku-startup-portrait|seed/library.sqlite3) ;;
            seed/local/content/*/*/000001.jpg)
                rest=${path#"$stage/seed/local/content/"}; first=${rest%%/*}; rest=${rest#*/}; second=${rest%%/*}
                [ "${rest#*/}" = 000001.jpg ] && [ "${#first}" = 64 ] && [ "${#second}" = 64 ] || return 1
                case "$first$second" in *[!0-9a-f]*) return 1;; esac
                count=$((count + 1));;
            *) return 1;;
        esac
    done <"$state/files"
    [ "$count" = 6 ]
}
proc_fields() {
    { IFS= read -r record <"$proc/$1/stat"; } 2>/dev/null || return 1
    case "$record" in "$1 ("*') '*) ;; *) return 1;; esac
    set -- ${record##*) }; [ "$#" -ge 20 ] || return 1; task_state=$1; shift 19; birth=$1; number "$birth"
}
can_stop_launcher() {
    launcher_lock=$locks/aidoku-launcher.lock
    directory "$launcher_lock" && plain "$launcher_lock/pid" || return 1
    printf '%s\n' "$pid" | cmp -s - "$launcher_lock/pid" || return 1
    [ -p "$launcher_lock/power-events" ] && [ ! -L "$launcher_lock/power-events" ] &&
        [ "$(readlink "$proc/$pid/fd/3")" = "$launcher_lock/power-events" ] || return 1
    printf '%s\000' sh "$stage/launch.sh" --foreground | cmp -s - "$proc/$pid/cmdline" &&
        proc_fields "$pid" && [ "$birth" = "$born" ]
}
stop_owned() {
    if absent "$state/visit.pid"; then absent "$state/launch.go" || fail visit_process; return 0; fi
    plain "$state/visit.pid" && read -r pid born path <"$state/visit.pid" && number "$pid" && number "$born" &&
        [ "$pid" -gt 1 ] && [ "$path" = "$stage/launch.sh" ] || fail visit_process
    signal_sent=0; tries=90
    while [ -e "$proc/$pid" ]; do
        proc_fields "$pid" || { [ ! -e "$proc/$pid" ] && break; fail visit_process; }
        [ "$birth" = "$born" ] || break
        case "$task_state" in Z|X|x) break;; esac
        if [ "$signal_sent" = 0 ] && can_stop_launcher; then
            kill -TERM "$pid" 2>/dev/null || :; signal_sent=1
        fi
        tries=$((tries - 1)); [ "$tries" -gt 0 ] || fail live_operation; usleep 200000
    done
    rm -f "$state/launch.go" "$state/visit.pid"
}
restore_checks() {
    if ! absent "$state/visit.intent" || ! absent "$state/ready"; then
        marker "$state/ready" && marker "$state/guard/ready" && plain "$state/stock.before" &&
            plain "$state/installed.before" || fail recovery_required
        if [ "$1" = restore ]; then guard restore guard_verified || fail guard_restore; fi
        guard verify guard_verified || fail guard_verify
        stock_equal || fail stock_changed
        installed_state >"$state/installed.after" && cmp -s "$state/installed.before" "$state/installed.after" || fail installed_changed
    else
        absent "$state/visit.done" && absent "$state/launch.go" && absent "$state/visit.pid" || fail recovery_required
    fi
    native_idle || fail native_recovery
}

case "$operation" in
    admit) both_absent && admit || fail native_admission; printf 'admitted\n';;
    prepare)
        both_absent || fail stage_present
        if ! admit; then both_absent || fail stage_present; printf 'not_started\n'; exit 0; fi
        mkdir "$stage" && put "$stage/owner" || fail stage_create
        lock_stage
        mkdir "$state" && chmod 700 "$state" && put "$state/owner" && mkdir "$out" "$state/data" || fail state_create
        touch_stock_idle || fail prepare_idle_guard
        tar -xzf - -C "$stage" && validate_leaves || fail payload
        for helper in aidoku-native-query kindle-jpeg-worker; do
            plain "$installed/app/$helper" && cp -p "$installed/app/$helper" "$stage/app/$helper" || fail helper_copy
        done
        cp "$stage/seed/library.sqlite3" "$state/data/library.sqlite3" && printf 'scribe\n' >"$state/data/device-profile" || fail data
        native_idle && guard capture guard_captured && installed_state >"$state/installed.before" && body "$state/stock.before" || fail baseline
        put "$state/ready" || fail ready; printf 'prepared\n';;
    visit)
        lock_both
        marker "$state/ready" && absent "$state/restored" && absent "$state/cancelled" || fail stage_owner
        put "$state/visit.intent" || fail visit_intent
        native_idle && guard verify guard_verified || fail guard_verify
        touch_stock_idle || fail visit_idle_guard
        (
            wait_go=200
            until marker "$state/launch.go"; do
                absent "$state/cancelled" || exit 1
                wait_go=$((wait_go - 1)); [ "$wait_go" -gt 0 ] || exit 1; usleep 20000
            done
            absent "$state/cancelled" || exit 1
            exec env -i PATH="$PATH" HOME="${HOME:-/}" LC_ALL=C AIDOKU_HOME="$stage" \
                AIDOKU_RESEARCH_DATA="$state/data" AIDOKU_RESEARCH_OUT="$out" AIDOKU_MANAGED_LIBRARY=1 \
                AIDOKU_LAUNCHER_DIAGNOSTICS=launcher-v1 AIDOKU_RESEARCH_DWELL=8000 sh "$stage/launch.sh" --foreground
        ) 9>&- </dev/null >"$out/launcher.log" 2>&1 &
        worker=$!
        proc_fields "$worker" && printf '%s %s %s\n' "$worker" "$birth" "$stage/launch.sh" >"$state/visit.pid" && put "$state/launch.go" || fail visit_process
        exec 9>&-
        wait "$worker" || fail launcher_exit
        lock_both
        absent "$state/cancelled" || fail cancelled
        rm -f "$state/launch.go" "$state/visit.pid"
        for name in launcher.log state.json application.json; do plain "$out/$name" && [ -s "$out/$name" ] || fail visit_output; done
        grep -Fx 'aidoku: launcher-visit ordinary_exit' "$out/launcher.log" >/dev/null || fail ordinary_exit
        put "$state/visit.done" || fail visit_done; printf 'visit_completed\n';;
    collect)
        lock_both; marker "$state/visit.done" || fail evidence
        for name in launcher.log state.json application.json; do plain "$out/$name" && [ -s "$out/$name" ] || fail evidence; done
        tar -czf - -C "$out" launcher.log application.json state.json;;
    restore)
        lock_stage
        if absent "$state"; then
            [ "$(find "$stage" -print)" = "$(printf '%s\n%s' "$stage" "$stage/owner")" ] || fail partial_stage
            mkdir "$state" && chmod 700 "$state" && put "$state/owner" || fail state_owner
        fi
        owned "$state" || fail stage_owner
        ensure_mark "$state/cancelled" || fail cancelled
        stop_owned; restore_checks restore; ensure_mark "$state/restored" || fail restored
        printf 'restored\n';;
    close)
        absent "$stage" && fail close_checkpoint
        owned "$state" && marker "$state/restored" || fail close_requires_restore
        lock_both; restore_checks verify; ensure_mark "$state/close.intent" || fail close_checkpoint
        owned "$stage" && rm -rf "$stage" && absent "$stage" || fail cleanup
        owned "$state" && rm -rf "$state" && both_absent || fail cleanup
        printf 'stage_absent\n';;
    absent) directory "$media" && directory "$private" && media_mounted && both_absent || fail stage_present; printf 'stage_absent\n';;
    *) fail operation;;
esac
