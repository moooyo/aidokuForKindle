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

absent() { [ ! -e "$1" ] && [ ! -L "$1" ]; }
plain() { [ -f "$1" ] && [ ! -L "$1" ] && [ "$(readlink -f "$1")" = "$1" ]; }
directory() { [ -d "$1" ] && [ ! -L "$1" ] && [ "$(readlink -f "$1")" = "$1" ]; }
same() { plain "$1" && printf '%s\n' "$2" | cmp -s - "$1"; }
put() { (set -C; printf '%s\n' "$2" >"$1"); }
owned() { directory "$1" && same "$1/owner" "$label"; }
space() { df -Pk "$1" | awk -v need="$2" 'END {exit !(NF>=6 && $4+0>=need)}'; }
number() { case "$1" in ''|*[!0-9]*) return 1;; esac; }
case_name() { case "$1" in ''|*[!a-z0-9-]*) return 1;; esac; [ "${#1}" -le 32 ]; }
known_case() { case "$1" in control-01|control-02|control-03) return 0;; esac; return 1; }
media_mounted() {
    directory "$media" || return 1
    if [ "$media" != /mnt/us ]; then return 0; fi
    awk '$5=="/mnt/us"{found=1} END{exit !found}' /proc/self/mountinfo 2>/dev/null
}
boot_id_value() {
    value=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null) || return 1
    printf '%s\n' "$value" | grep -Eq '^[0-9a-f-]{36}$' || return 1
    printf '%s' "$value"
}

lock() {
    exec 7<"$stage"
    remaining=90
    until flock -n 7; do remaining=$((remaining - 1)); [ "$remaining" -gt 0 ] || fail live_operation; usleep 200000; done
}

assert_stage() { owned "$stage" && owned "$state" && directory "$output"; }
visit() { sh "$stage/visit/visit.sh" "$1" "$label" "$2" ${3:+"$3"} >/dev/null; }
guard() {
    reply=$(sh "$stage/guard/guard.sh" "$1" "$label") || return 1
    [ "$#" = 1 ] || [ "$reply" = "$2" ]
}

control_snapshot_prepare() (
    manifest=$stage/control/program-modes
    snapshot=$state/control.snapshot
    plain "$manifest" || exit 1
    absent "$snapshot" || exit 1
    modes=''
    for relative in app/aidoku launch.sh app/aidoku-startup-portrait; do
        live=$media/aidoku/$relative
        expected=$stage/control/$relative
        plain "$live" && plain "$expected" && [ -x "$live" ] || exit 1
        mode=$(stat -c %a "$live") || exit 1
        case "$mode" in [0-7][0-7][0-7]) ;; *) exit 1;; esac
        live_size=$(stat -c %s "$live") || exit 1
        expected_size=$(stat -c %s "$expected") || exit 1
        number "$live_size" && number "$expected_size" && [ "$live_size" = "$expected_size" ] || exit 1
        fingerprint=$(stat -c '%d:%i:%s:%a:%Y:%Z' "$live") || exit 1
        printf '%s %s\n' "$relative" "$fingerprint" >>"$snapshot" || exit 1
        modes="${modes}${mode}
"
    done
    printf '%s' "$modes" | cmp -s - "$manifest"
)

control_snapshot_same() (
    snapshot=$state/control.snapshot
    plain "$snapshot" || exit 1
    count=0
    while IFS=' ' read -r relative fingerprint extra; do
        [ -n "$relative" ] && [ -n "$fingerprint" ] && [ -z "${extra-}" ] || exit 1
        case "$relative" in app/aidoku|launch.sh|app/aidoku-startup-portrait) ;; *) exit 1;; esac
        live=$media/aidoku/$relative
        plain "$live" && [ -x "$live" ] || exit 1
        now=$(stat -c '%d:%i:%s:%a:%Y:%Z' "$live") || exit 1
        [ "$now" = "$fingerprint" ] || exit 1
        count=$((count + 1))
    done <"$snapshot"
    [ "$count" = 3 ]
)

control_snapshot_verify_close() (
    manifest=$stage/control/program-modes
    snapshot=$state/control.snapshot
    plain "$manifest" && plain "$snapshot" || exit 1
    modes=''
    count=0
    while IFS=' ' read -r relative fingerprint extra; do
        [ -n "$relative" ] && [ -n "$fingerprint" ] && [ -z "${extra-}" ] || exit 1
        case "$relative" in app/aidoku|launch.sh|app/aidoku-startup-portrait) ;; *) exit 1;; esac
        live=$media/aidoku/$relative
        expected=$stage/control/$relative
        plain "$live" && plain "$expected" && [ -x "$live" ] || exit 1
        mode=$(stat -c %a "$live") || exit 1
        case "$mode" in [0-7][0-7][0-7]) ;; *) exit 1;; esac
        live_size=$(stat -c %s "$live") || exit 1
        expected_size=$(stat -c %s "$expected") || exit 1
        number "$live_size" && number "$expected_size" && [ "$live_size" = "$expected_size" ] || exit 1
        now=$(stat -c '%d:%i:%s:%a:%Y:%Z' "$live") || exit 1
        [ "$now" = "$fingerprint" ] || exit 1
        cmp -s "$live" "$expected" || exit 1
        modes="${modes}${mode}
"
        count=$((count + 1))
    done <"$snapshot"
    [ "$count" = 3 ] || exit 1
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
    media_mounted && directory "$private" && absent "$stage" && absent "$state" &&
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

parse_rendered_line() {
    rendered_file=$1
    plain "$rendered_file" && [ -s "$rendered_file" ] || return 1
    read -r tag last_negative first_match confirmed cover_first cover_confirmed extra <"$rendered_file" || return 1
    [ -z "${extra-}" ] && [ "$tag" = ARL1 ] || return 1
    number "$last_negative" || return 1
    number "$first_match" && [ "$first_match" -gt 0 ] || return 1
    number "$confirmed" && [ "$confirmed" -gt 0 ] || return 1
    [ "$last_negative" -lt "$first_match" ] && [ "$first_match" -le "$confirmed" ] || return 1
    case "$cover_first" in MATCH|DIFFER) ;; *) return 1;; esac
    case "$cover_confirmed" in MATCH|DIFFER) ;; *) return 1;; esac
    printf 'ARL1 %s %s %s %s %s\n' "$last_negative" "$first_match" "$confirmed" "$cover_first" "$cover_confirmed" >"$rendered_file"
}

collect_members() {
    case_name=$1
    directory "$output/$case_name" || return 1
    same "$output/$case_name/qualified" "$case_name stock-tap" || return 1
    leaves='origin anchor pointer-brackets qualified rendered-library'
    for leaf in $leaves; do
        plain "$output/$case_name/$leaf" && [ -s "$output/$case_name/$leaf" ] || return 1
        printf 'out/%s/%s\n' "$case_name" "$leaf"
    done
}

render_watch_refusal() {
    status_file=$1
    reason=
    if plain "$status_file"; then
        reason=$(sed -n 's/^ARL1_ERR //p' "$status_file" | tail -n 1)
    fi
    case "$reason" in
        INITIAL) printf 'render_watch_initial\n';;
        TIMEOUT) printf 'render_watch_timeout\n';;
        SCAN) printf 'render_watch_scan\n';;
        RULE_LAYOUT) printf 'render_watch_rule_layout\n';;
        RULE) printf 'render_watch_rule\n';;
        '') printf 'render_watch_start\n';;
        *) printf 'render_watch_runtime\n';;
    esac
}

run_visit() {
    [ "$#" = 1 ] || fail arguments
    case_name "$1" || fail case
    known_case "$1" || fail case
    case_id=$1

    assert_stage && same "$state/ready" "$label" && absent "$state/current" && absent "$output/$case_id" || fail visit_intent
    control_snapshot_same || fail changed_control
    guard verify guard_verified || fail previsit_protection
    mkdir "$output/$case_id" && put "$state/current" "$case_id" && put "$output/$case_id/origin" stock-tap || fail visit_intent

    out=$output/$case_id
    visit prepare "$case_id" || fail visit_prepare
    input_begin
    sh "$stage/input/dispatch.sh" target "$label" "$case_id" >/dev/null || fail native_tile
    visit arm "$case_id" stock-tap || fail visit_arm

    plain "$stage/input/render-watch" && [ -x "$stage/input/render-watch" ] || fail missing_render_logger
    plain "$stage/input/reference.rule" || fail missing_render_rule
    logger=
    owned_child() {
        number "$1" && [ "$1" -gt 1 ] || return 1
        [ -r "/proc/$1/status" ] || return 1
        ppid=$(sed -n 's/^PPid:[[:space:]]*//p' "/proc/$1/status")
        number "$ppid" && [ "$ppid" = "$$" ]
    }
    cleanup_logger() {
        [ -n "$logger" ] && owned_child "$logger" && kill -0 "$logger" 2>/dev/null && kill "$logger" 2>/dev/null || :
        if [ -n "$logger" ]; then
            tries=25
            while owned_child "$logger" && kill -0 "$logger" 2>/dev/null; do
                tries=$((tries - 1))
                if [ "$tries" -le 0 ]; then
                    owned_child "$logger" && kill -9 "$logger" 2>/dev/null || :
                    break
                fi
                usleep 20000 || break
            done
            owned_child "$logger" && wait "$logger" 2>/dev/null || :
        fi
        logger=
    }

    trap 'cleanup_logger' EXIT
    trap 'exit 1' HUP INT TERM
    "$stage/input/render-watch" "$stage/input/reference.rule" >"$out/rendered-library" 2>"$out/rendered-status" 7>&- &
    logger=$!
    tries=75
    until grep -Fxq READY "$out/rendered-status" 2>/dev/null; do
        if ! kill -0 "$logger" 2>/dev/null; then
            fail "$(render_watch_refusal "$out/rendered-status")"
        fi
        tries=$((tries - 1)); [ "$tries" -gt 0 ] || fail render_watch_start; usleep 20000
    done
    usleep 200000 || fail pretrigger_dwell

    sh "$stage/input/dispatch.sh" tap "$label" "$case_id" >"$out/request.anchor" || fail tile_dispatch
    same "$out/dispatched" stock-tap || fail tile_dispatch
    pointer=$(cat "$out/pointer-brackets" 2>/dev/null) || fail pointer_brackets
    set -- $pointer
    [ "$#" = 4 ] || fail pointer_brackets
    for value in "$@"; do number "$value" && [ "$value" -gt 0 ] || fail pointer_brackets; done
    [ "$1" -le "$2" ] && [ "$2" -le "$3" ] && [ "$3" -le "$4" ] || fail pointer_brackets
    down_before=$1
    down_after=$2
    up_before=$3
    up_after=$4
    printf '%s %s %s %s\n' "$down_before" "$down_after" "$up_before" "$up_after" >"$out/anchor" || fail pointer_brackets
    if ! wait "$logger"; then
        fail "$(render_watch_refusal "$out/rendered-status")"
    fi
    logger=
    parse_rendered_line "$out/rendered-library" || fail rendered_library

    visit bind "$case_id" || fail normal_session
    visit check "$case_id" && visit exit "$case_id" || fail normal_exit
    input_restore
    restore_lights || fail guard_lights
    guard verify guard_verified || fail postvisit_protection
    control_snapshot_same || fail changed_control
    same "$state/current" "$case_id" && put "$out/qualified" "$case_id stock-tap" && rm "$state/current" || fail visit_close
    trap - EXIT HUP INT TERM
}

remove_owned() {
    owned "$1" && rm -rf "$1" && absent "$1"
}

case "$operation" in
    admit)
        [ "$#" = 0 ] || fail arguments
        admit || fail native_admission
        printf 'render_admitted\n';;
    prepare)
        [ "$#" = 1 ] && [ "$1" = tapcheck ] || fail arguments
        media_mounted && directory "$private" && absent "$stage" && absent "$state" && space "$media" 163840 &&
            space "$private" 28000 || fail stage_admission
        mkdir "$stage" && put "$stage/owner" "$label" || fail new_media
        lock
        mkdir "$state" && put "$state/owner" "$label" && chmod 700 "$state" &&
            [ "$(stat -c '%u:%a' "$state")" = 0:700 ] && mkdir "$output" || fail new_state
        boot=$(boot_id_value) || fail stage_admission
        put "$state/boot-id" "$boot" || fail new_state
        touch_stock_idle || fail prepare_idle_guard
        tar -xzf - -C "$stage" || fail payload
        assert_stage && plain "$stage/visit/visit.sh" && plain "$stage/guard/guard.sh" && plain "$stage/input/navigation.sh" &&
            plain "$stage/input/dispatch.sh" && plain "$stage/input/artwork.guard" && plain "$stage/input/tile-input" &&
            plain "$stage/input/render-watch" && [ -x "$stage/input/render-watch" ] &&
            plain "$stage/input/reference.rule" || fail missing_modules
        guard capture guard_captured || fail guard_capture
        same "$state/guard/ready" "$label" || fail guard_capture
        capture_lights >"$state/lights.before" || fail guard_lights
        control_snapshot_prepare || fail tapcheck_control
        put "$state/ready" "$label" || fail readiness
        printf 'render_prepared\n';;
    visit)
        lock
        run_visit "$@"
        printf 'render_completed\n';;
    collect)
        assert_stage && [ "$#" = 1 ] && case_name "$1" && known_case "$1" || fail stage_owner
        lock
        files=$(collect_members "$1") || fail evidence
        tar -czf - -C "$state" $files || fail transfer;;
    close)
        [ "$#" = 0 ] || fail arguments
        if absent "$stage" && absent "$state"; then fail close_checkpoint; fi
        if absent "$stage"; then
            owned "$state" && same "$state/closed-pending" "$label" &&
                media_mounted && same "$state/boot-id" "$(boot_id_value)" && native_idle &&
                remove_owned "$state" || fail partial_state
            printf 'render_closed\n'; exit 0
        fi
        owned "$stage" || fail stage_owner
        lock
        absent "$state" && fail partial_state
        owned "$state" || fail state_owner
        if ! absent "$state/current"; then
            read -r current <"$state/current" || fail visit_owner
            case_name "$current" && known_case "$current" || fail visit_owner
            if [ -f "$state/visit-$current/owner" ]; then
                if ! absent "$output/$current/dispatched"; then
                    same "$output/$current/dispatched" stock-tap || fail visit_recovery
                fi
                visit recover "$current" || fail visit_recovery
            else
                absent "$state/visit-live" && absent "$state/visit-$current/request" || fail visit_recovery
            fi
            rm "$state/current" || fail visit_recovery
        fi
        input_restore
        if ! absent "$state/lights.before"; then
            restore_lights || fail guard_lights
        fi
        if ! absent "$state/guard/ready"; then
            same "$state/guard/ready" "$label" || fail guard_admission
            guard restore guard_verified || fail guard_restore
            guard verify guard_verified || fail guard_admission
        fi
        media_mounted || fail native_recovery
        same "$state/boot-id" "$(boot_id_value)" || fail native_recovery
        native_idle || fail native_recovery
        if ! absent "$state/control.snapshot"; then
            control_snapshot_verify_close || fail changed_control
        elif ! absent "$state/ready"; then
            fail changed_control
        fi
        if absent "$state/closed-pending"; then put "$state/closed-pending" "$label" || fail close_intent; fi
        same "$state/closed-pending" "$label" && remove_owned "$stage" && remove_owned "$state" || fail cleanup
        printf 'render_closed\n';;
    absent)
        [ "$#" = 0 ] || fail arguments
        media_mounted && directory "$private" && absent "$stage" && absent "$state" || fail stage_present
        printf 'render_absent\n';;
    *) fail operation;;
esac
