#!/bin/sh
set -efu
export LC_ALL=C
umask 077

fail() { printf 'refused %s\n' "$1" >&2; exit 1; }
[ "$#" -ge 2 ] || fail arguments
operation=$1; label=$2; shift 2
case "$label" in ''|*[!0-9a-f]*) fail label;; esac
[ "${#label}" = 16 ] || fail label

media=/mnt/us
private=/var/tmp
stage=$media/.aidoku-native-$label
state=$private/aidoku-native-$label
output=$state/out
case_name=control-warmup

absent() { [ ! -e "$1" ] && [ ! -L "$1" ]; }
plain() { [ -f "$1" ] && [ ! -L "$1" ] && [ "$(readlink -f "$1")" = "$1" ]; }
directory() { [ -d "$1" ] && [ ! -L "$1" ] && [ "$(readlink -f "$1")" = "$1" ]; }
same() { plain "$1" && printf '%s\n' "$2" | cmp -s - "$1"; }
put() { (set -C; printf '%s\n' "$2" >"$1"); }
owned() { directory "$1" && same "$1/owner" "$label"; }
space() { df -Pk "$1" | awk -v need="$2" 'END {exit !(NF>=6 && $4+0>=need)}'; }
number() { case "$1" in ''|*[!0-9]*) return 1;; esac; }

lock() {
    exec 7<"$stage"
    remaining=90
    until flock -n 7; do remaining=$((remaining - 1)); [ "$remaining" -gt 0 ] || fail live_operation; usleep 200000; done
}

assert_stage() { owned "$stage" && owned "$state" && directory "$output"; }
visit() { sh "$stage/visit/visit.sh" "$1" "$label" "$case_name" ${2:+"$2"} >/dev/null; }
guard() { sh "$stage/guard/guard.sh" "$1" "$label" >/dev/null; }
observer=$stage/observer/framewatch

installed_control() (
    manifest=$stage/control/program-modes
    plain "$manifest" || exit 1
    modes=''
    for relative in app/aidoku launch.sh app/aidoku-startup-portrait; do
        live=$media/aidoku/$relative
        expected=$stage/control/$relative
        plain "$live" && plain "$expected" && [ -x "$live" ] && cmp -s "$live" "$expected" || exit 1
        mode=$(stat -c %a "$live") || exit 1
        case "$mode" in [0-7][0-7][0-7]) ;; *) exit 1;; esac
        modes="${modes}${mode}
"
    done
    printf '%s' "$modes" | cmp -s - "$manifest"
)

touch_stock_idle() {
    [ "$(lipc-get-prop -s com.lab126.appmgrd activeApp 2>/dev/null)" = com.lab126.KPPMainApp ] &&
        [ "$(lipc-get-prop -s com.lab126.powerd state 2>/dev/null)" = active ] &&
        lipc-set-prop -i com.lab126.powerd touchScreenSaverTimeout 1 </dev/null >/dev/null 2>&1
}

input_begin() {
    plain "$stage/input/navigation.sh" || fail missing_navigation
    if absent "$state/pointer-intent"; then
        touch_stock_idle || fail navigation_idle_guard
        put "$state/pointer-intent" "$label" || fail pointer_intent
    fi
    same "$state/pointer-intent" "$label" || fail pointer_intent
    sh "$stage/input/navigation.sh" library "$label" >/dev/null || fail native_library
}

input_restore() {
    if absent "$state/pointer-intent"; then return 0; fi
    same "$state/pointer-intent" "$label" && plain "$stage/input/navigation.sh" &&
        sh "$stage/input/navigation.sh" restore "$label" >/dev/null &&
        rm "$state/pointer-intent" || fail pointer_recovery
}

admit() {
    directory "$media" && directory "$private" && absent "$stage" && absent "$state" &&
        space "$media" 163840 && space "$private" 28000 &&
        [ "$(lipc-get-prop -s com.lab126.powerd state 2>/dev/null)" = active ] &&
        [ "$(lipc-get-prop -s com.lab126.appmgrd activeApp 2>/dev/null)" = com.lab126.KPPMainApp ] || return 1
    case "$(status statusbar 2>/dev/null)" in *start/running*) ;; *) return 1;; esac
    for reader in aidoku weread bilicomics; do
        [ ! -e "/tmp/$reader-launcher.lock" ] && [ ! -L "/tmp/$reader-launcher.lock" ] || return 1
        if pidof "$reader" >/dev/null; then return 1; else [ "$?" = 1 ] || return 1; fi
    done
}

native_idle() {
    [ "$(lipc-get-prop -s com.lab126.powerd state 2>/dev/null)" = active ] &&
        [ "$(lipc-get-prop -s com.lab126.appmgrd activeApp 2>/dev/null)" = com.lab126.KPPMainApp ] || return 1
    case "$(status statusbar 2>/dev/null)" in *start/running*) ;; *) return 1;; esac
    for reader in aidoku weread bilicomics; do
        [ ! -e "/tmp/$reader-launcher.lock" ] && [ ! -L "/tmp/$reader-launcher.lock" ] || return 1
        if pidof "$reader" >/dev/null; then return 1; else [ "$?" = 1 ] || return 1; fi
    done
}

capture_lights() {
    for light in flIntensity currentAmberLevel; do
        value=$(lipc-get-prop -i com.lab126.powerd "$light" 2>/dev/null) || return 1
        number "$value" && [ "$value" -le 24 ] || return 1
        printf '%s %s\n' "$light" "$value"
    done
}

restore_lights() {
    plain "$state/lights.before" || return 1
    count=0
    while IFS=' ' read -r light value extra; do
        [ -z "${extra-}" ] || return 1
        case "$light" in flIntensity|currentAmberLevel) ;; *) return 1;; esac
        number "$value" && [ "$value" -le 24 ] || return 1
        tries=4
        while :; do
            now=$(lipc-get-prop -i com.lab126.powerd "$light" 2>/dev/null) || return 1
            number "$now" && [ "$now" -le 24 ] || return 1
            [ "$now" = "$value" ] && break
            lipc-set-prop -i com.lab126.powerd "$light" "$value" </dev/null >/dev/null 2>&1 || return 1
            tries=$((tries - 1)); [ "$tries" -gt 0 ] || return 1
            usleep 20000 || return 1
        done
        count=$((count + 1))
    done <"$state/lights.before"
    [ "$count" = 2 ]
}

collect_members() {
    directory "$output/$case_name" || return 1
    same "$output/$case_name/qualified" "$case_name stock-tap" || return 1
    leaves='stock.frame trace origin anchor pointer-brackets qualified touch control.before.frame control.after.frame control.anchor control.method'
    for leaf in $leaves; do
        plain "$output/$case_name/$leaf" && [ -s "$output/$case_name/$leaf" ] || return 1
        printf 'out/%s/%s\n' "$case_name" "$leaf"
    done
}

run_pilot() {
    [ "$#" -ge 1 ] && [ "$#" -le 2 ] || fail arguments
    number "$1" && [ "$1" -ge 500 ] && [ "$1" -le 12000 ] || fail manual_window_ms
    window_ms=$1
    touch_mode=${2:-require-touch}
    case "$touch_mode" in require-touch|allow-missing-touch) ;; *) fail touch_policy;; esac
    assert_stage && same "$state/ready" "$label" && absent "$state/current" && absent "$output/$case_name" || fail pilot_intent
    installed_control || fail changed_control
    guard admit || fail previsit_protection
    mkdir "$output/$case_name" && put "$state/current" "$case_name" && put "$output/$case_name/origin" stock-tap || fail visit_intent
    out=$output/$case_name
    visit prepare || fail visit_prepare
    input_begin
    sh "$stage/input/dispatch.sh" target "$label" "$case_name" >/dev/null || fail native_tile
    "$observer" capture "$out/stock.frame" || fail initial_capture
    visit arm stock-tap || fail visit_arm

    if [ ! -x "$stage/input/touch-witness" ] && [ "$touch_mode" = require-touch ]; then
        fail missing_touch_witness
    fi
    watcher=
    touch_pid=
    cleanup_watchers() {
        for pid in "$watcher" "$touch_pid"; do
            [ -n "$pid" ] || continue
            wait "$pid" 2>/dev/null || :
        done
        watcher=
        touch_pid=
    }
    trap 'cleanup_watchers' EXIT
    trap 'exit 1' HUP INT TERM
    "$observer" watch "$out/trace" 2>"$out/watch-status" 7>&- &
    watcher=$!
    if [ -x "$stage/input/touch-witness" ]; then
        "$stage/input/touch-witness" watch auto 12000 > "$out/touch" 2>"$out/touch-status" 7>&- &
        touch_pid=$!
    fi
    tries=75
    until grep -Fxq READY "$out/watch-status" 2>/dev/null; do
        kill -0 "$watcher" 2>/dev/null || fail watcher_started
        tries=$((tries - 1)); [ "$tries" -gt 0 ] || fail watcher_started; usleep 20000
    done
    if [ -n "$touch_pid" ]; then
        tries=75
        until grep -Fxq READY "$out/touch-status" 2>/dev/null; do
            kill -0 "$touch_pid" 2>/dev/null || fail touch_witness_started
            tries=$((tries - 1)); [ "$tries" -gt 0 ] || fail touch_witness_started; usleep 20000
        done
    fi
    usleep 200000 || fail pretrigger_dwell

    sh "$stage/input/dispatch.sh" tap "$label" "$case_name" >"$out/request.anchor" || fail tile_dispatch
    pointer=$(cat "$out/pointer-brackets" 2>/dev/null) || fail pointer_brackets
    set -- $pointer
    [ "$#" = 4 ] || fail pointer_brackets
    for value in "$@"; do number "$value" && [ "$value" -gt 0 ] || fail pointer_brackets; done
    [ "$1" -le "$2" ] && [ "$2" -le "$3" ] && [ "$3" -le "$4" ] || fail pointer_brackets
    printf '%s %s %s %s\n' "$1" "$2" "$3" "$4" >"$out/anchor" || fail pointer_brackets

    visit bind || fail normal_session
    "$observer" capture "$out/control.before.frame" || fail control_before
    start=$("$observer" clock) || fail control_anchor
    usleep $((window_ms * 1000)) || fail control_window
    "$observer" capture "$out/control.after.frame" || fail control_after
    end=$("$observer" clock) || fail control_anchor
    printf '%s %s\n' "$start" "$end" >"$out/control.anchor" || fail control_anchor
    put "$out/control.method" manual-window || fail control_method

    wait "$watcher" || fail observer_trace
    watcher=
    if [ -n "$touch_pid" ]; then
        wait "$touch_pid" || fail touch_witness
        touch_pid=
        plain "$out/touch" && [ -s "$out/touch" ] || fail touch_witness
        read -r atw status window_start window_end down_before down_after up_before up_after samples _extra <"$out/touch" ||
            fail touch_witness
        [ -z "${_extra-}" ] && [ "$atw" = ATW1 ] || fail touch_witness
        case "$status" in
            SINGLE|TIMEOUT) ;;
            *) fail touch_witness;;
        esac
        for value in "$window_start" "$window_end" "$samples"; do
            number "$value" && [ "$value" -gt 0 ] || fail touch_witness
        done
        [ "$window_start" -lt "$window_end" ] || fail touch_witness
        case "$status" in
            TIMEOUT)
                [ "$down_before" = 0 ] && [ "$down_after" = 0 ] && [ "$up_before" = 0 ] && [ "$up_after" = 0 ] ||
                    fail touch_witness
                [ "$touch_mode" = allow-missing-touch ] || fail missing_touch
                ;;
            SINGLE)
                for value in "$down_before" "$down_after" "$up_before" "$up_after"; do
                    number "$value" && [ "$value" -gt 0 ] || fail touch_witness
                done
                [ "$down_before" -le "$down_after" ] && [ "$up_before" -le "$up_after" ] &&
                    [ "$down_before" -le "$up_before" ] && [ "$down_after" -le "$up_after" ] || fail touch_witness
                ;;
        esac
    fi
    visit check && visit exit || fail normal_exit
    input_restore
    restore_lights || fail guard_lights
    guard admit || fail postvisit_protection
    installed_control || fail changed_control
    same "$state/current" "$case_name" && put "$out/qualified" "$case_name stock-tap" && rm "$state/current" || fail visit_close
    trap - EXIT HUP INT TERM
}

remove_owned() {
    owned "$1" && rm -rf "$1" && absent "$1"
}

case "$operation" in
    admit)
        [ "$#" = 0 ] || fail arguments
        admit || fail native_admission
        printf 'pilot_admitted\n';;
    prepare)
        [ "$#" = 1 ] && [ "$1" = tapcheck ] || fail arguments
        directory "$media" && directory "$private" && absent "$stage" && absent "$state" && space "$media" 163840 &&
            space "$private" 28000 || fail stage_admission
        mkdir "$stage" && put "$stage/owner" "$label" || fail new_media
        lock
        mkdir "$state" && put "$state/owner" "$label" && chmod 700 "$state" &&
            [ "$(stat -c '%u:%a' "$state")" = 0:700 ] && mkdir "$output" || fail new_state
        touch_stock_idle || fail prepare_idle_guard
        tar -xzf - -C "$stage" || fail payload
        assert_stage && plain "$stage/visit/visit.sh" &&
            plain "$stage/guard/guard.sh" && plain "$observer" && [ -x "$observer" ] && plain "$stage/input/navigation.sh" &&
            plain "$stage/input/dispatch.sh" && plain "$stage/input/artwork.guard" &&
            plain "$stage/input/tile-input" || fail missing_modules
        guard admit || fail guard_admission
        capture_lights >"$state/lights.before" || fail guard_lights
        installed_control || fail tapcheck_control
        put "$state/ready" "$label" || fail readiness
        printf 'pilot_prepared\n';;
    pilot)
        lock
        run_pilot "$@"
        printf 'pilot_completed\n';;
    collect-case)
        assert_stage && [ "$#" = 1 ] && [ "$1" = "$case_name" ] || fail stage_owner
        lock
        files=$(collect_members) || fail evidence
        tar -czf - -C "$state" $files || fail transfer;;
    discard-case)
        assert_stage && [ "$#" = 1 ] && [ "$1" = "$case_name" ] || fail stage_owner
        lock
        files=$(collect_members) || fail evidence
        for item in $files; do
            case "$item" in */stock.frame|*/trace|*/control.before.frame|*/control.after.frame) rm "$state/$item" || fail discard;; esac
        done
        put "$output/$case_name/exported" "$label" && printf 'pilot_discarded\n';;
    close)
        [ "$#" = 0 ] || fail arguments
        if absent "$stage" && absent "$state"; then fail close_checkpoint; fi
        if absent "$stage"; then
            owned "$state" && same "$state/closed-pending" "$label" && remove_owned "$state" || fail partial_state
            printf 'pilot_closed\n'; exit 0
        fi
        owned "$stage" || fail stage_owner
        lock
        absent "$state" && fail partial_state
        owned "$state" || fail state_owner
        if ! absent "$state/current"; then
            same "$state/current" "$case_name" || fail visit_owner
            if [ -f "$state/visit-$case_name/owner" ]; then
                visit recover || fail visit_recovery
            else
                absent "$state/visit-live" && absent "$state/visit-$case_name/request" || fail visit_recovery
            fi
            rm "$state/current" || fail visit_recovery
        fi
        input_restore
        if ! absent "$state/ready"; then
            restore_lights || fail guard_lights
            guard admit || fail guard_admission
        fi
        if absent "$state/closed-pending"; then put "$state/closed-pending" "$label" || fail close_intent; fi
        native_idle || fail native_recovery
        same "$state/closed-pending" "$label" && remove_owned "$stage" && remove_owned "$state" || fail cleanup
        printf 'pilot_closed\n';;
    absent)
        [ "$#" = 0 ] || fail arguments
        directory "$media" && directory "$private" && absent "$stage" && absent "$state" || fail stage_present
        printf 'pilot_absent\n';;
    *) fail operation;;
esac
