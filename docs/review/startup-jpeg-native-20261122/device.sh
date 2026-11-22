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
known_case() { case "$1" in control-warmup|candidate-warmup|control-0[1-6]|candidate-0[1-6]) return 0;; esac; return 1; }
media_mounted() {
    directory "$media" || return 1
    if [ "$media" != /mnt/us ]; then return 0; fi
    awk '$5=="/mnt/us"{found=1} END{exit !found}' /proc/self/mountinfo 2>/dev/null
}
no_app_mount() {
    awk -v target="$media/aidoku/app/aidoku" '$5==target{found=1} END{exit found}' /proc/self/mountinfo 2>/dev/null
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
    [ "$reply" = "$2" ]
}
selector_case() {
    reply=$(sh "$stage/selector/selector.sh" "$1" "$label" "$2") || return 1
    [ "$reply" = "$3" ]
}
selector_restore() {
    reply=$(sh "$stage/selector/selector.sh" restore "$label") || return 1
    [ "$reply" = selector_restore ]
}
selector_lock_absent() { absent "/tmp/.aidoku-selector-lock-$label"; }
submission() {
    reply=$(sh "$stage/submission/submission.sh" "$1" "$label" "$2") || return 1
    [ "$reply" = "$3" ]
}
touch_stock_idle() {
    [ "$(lipc-get-prop -s com.lab126.appmgrd activeApp 2>/dev/null)" = com.lab126.KPPMainApp ] &&
        [ "$(lipc-get-prop -s com.lab126.powerd state 2>/dev/null)" = active ] &&
        lipc-set-prop -i com.lab126.powerd touchScreenSaverTimeout 1 </dev/null >/dev/null 2>&1
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
admit() {
    media_mounted && directory "$private" && absent "$stage" && absent "$state" && no_app_mount &&
        selector_lock_absent &&
        space "$media" 262144 && space "$private" 32768 && native_idle
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
original_control() (
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
collect_members() {
    case_id=$1
    directory "$output/$case_id" && same "$output/$case_id/origin" stock-tap &&
        same "$output/$case_id/qualified" "$case_id stock-tap" || return 1
    for leaf in origin anchor pointer-brackets qualified submission; do
        plain "$output/$case_id/$leaf" && [ -s "$output/$case_id/$leaf" ] || return 1
        printf 'out/%s/%s\n' "$case_id" "$leaf"
    done
}
capture_submission() {
    case_id=$1
    result=$(sh "$stage/submission/submission.sh" collect "$label" "$case_id") || return 1
    set -- $result
    [ "$#" = 8 ] && [ "$1" = SUBMISSION ] && [ "$6" = large ] &&
        [ "$7" = nonempty ] && [ "$8" = nonempty ] || return 1
    for value in "$2" "$3" "$4" "$5"; do number "$value" && [ "${#value}" -le 20 ] || return 1; done
    [ "$4" != 0 ] && [ "$5" != 0 ] || return 1
    printf 'SUBMISSION %s %s %s %s large nonempty nonempty\n' "$2" "$3" "$4" "$5"
}
run_visit() {
    [ "$#" = 1 ] && known_case "$1" || fail case_binding
    case_id=$1
    assert_stage && same "$state/ready" "$label" && same "$state/case" "$case_id" &&
        absent "$state/current" && absent "$output/$case_id" || fail visit_intent
    selector_case check "$case_id" selector_check || fail selected_arm
    guard verify guard_verified || fail previsit_protection
    mkdir "$output/$case_id" && put "$state/current" "$case_id" &&
        put "$output/$case_id/origin" stock-tap || fail visit_intent

    out=$output/$case_id
    visit prepare "$case_id" || fail visit_prepare
    input_begin
    sh "$stage/input/dispatch.sh" target "$label" "$case_id" >/dev/null || fail native_tile
    visit arm "$case_id" stock-tap || fail visit_arm
    submission prepare "$case_id" submission_prepared || fail submission_prepare

    sh "$stage/input/dispatch.sh" tap "$label" "$case_id" >"$out/request.anchor" || fail tile_dispatch
    same "$out/dispatched" stock-tap || fail tile_dispatch
    pointer=$(cat "$out/pointer-brackets" 2>/dev/null) || fail pointer_brackets
    set -- $pointer
    [ "$#" = 4 ] || fail pointer_brackets
    for value in "$@"; do number "$value" && [ "$value" -gt 0 ] || fail pointer_brackets; done
    [ "$1" -le "$2" ] && [ "$2" -lt "$3" ] && [ "$3" -le "$4" ] || fail pointer_brackets
    printf '%s %s %s %s\n' "$1" "$2" "$3" "$4" >"$out/anchor" || fail pointer_brackets

    visit bind "$case_id" || fail normal_session
    submission bind "$case_id" submission_bound || fail submission_bind
    visit check "$case_id" && visit exit "$case_id" || fail normal_exit
    capture_submission "$case_id" >"$out/submission" || fail submission_collect
    input_restore
    restore_lights || fail guard_lights
    guard verify guard_verified || fail postvisit_protection
    selector_case check "$case_id" selector_check || fail selected_arm
    same "$state/current" "$case_id" && put "$out/qualified" "$case_id stock-tap" &&
        rm "$state/current" || fail visit_close
}
remove_owned() { owned "$1" && rm -rf "$1" && absent "$1"; }

case "$operation" in
    admit)
        [ "$#" = 0 ] || fail arguments
        admit || fail native_admission
        printf 'jpeg_admitted\n';;
    prepare)
        [ "$#" = 1 ] && known_case "$1" || fail case_binding
        case_id=$1
        admit || fail stage_admission
        boot=$(boot_id_value) || fail stage_admission
        mkdir "$stage" && put "$stage/owner" "$label" || fail new_media
        lock
        mkdir "$state" && put "$state/owner" "$label" && chmod 700 "$state" &&
            [ "$(stat -c '%u:%a' "$state")" = 0:700 ] && mkdir "$output" || fail new_state
        put "$state/boot-id" "$boot" && put "$state/case" "$case_id" || fail new_state
        touch_stock_idle || fail prepare_idle_guard
        tar -xzf - -C "$stage" || fail payload
        assert_stage && plain "$stage/visit/visit.sh" && plain "$stage/guard/guard.sh" &&
            plain "$stage/input/navigation.sh" && plain "$stage/input/dispatch.sh" &&
            plain "$stage/input/artwork.guard" && plain "$stage/input/tile-input" &&
            plain "$stage/selector/selector.sh" && plain "$stage/submission/submission.sh" &&
            plain "$stage/programs/selected-app" && [ -x "$stage/programs/selected-app" ] &&
            plain "$stage/rename-new" && [ -x "$stage/rename-new" ] || fail missing_modules
        guard capture guard_captured || fail guard_capture
        same "$state/guard/ready" "$label" || fail guard_capture
        lights=$(capture_lights) && put "$state/lights.before" "$lights" || fail guard_lights
        put "$state/selector-intent" "$label $case_id" || fail arm_selection
        selector_case prepare "$case_id" selector_prepare || fail arm_selection
        selector_case select "$case_id" selector_select || fail arm_selection
        selector_case check "$case_id" selector_check || fail selected_arm
        put "$state/ready" "$label" || fail readiness
        printf 'jpeg_prepared\n';;
    visit)
        lock
        run_visit "$@"
        printf 'jpeg_completed\n';;
    collect)
        assert_stage && [ "$#" = 1 ] && known_case "$1" && same "$state/case" "$1" || fail case_binding
        lock
        files=$(collect_members "$1") || fail evidence
        tar -czf - -C "$state" $files || fail transfer;;
    close)
        [ "$#" = 0 ] || fail arguments
        if absent "$stage" && absent "$state"; then fail close_checkpoint; fi
        if absent "$stage"; then
            owned "$state" && same "$state/closed-pending" "$label" &&
                media_mounted && same "$state/boot-id" "$(boot_id_value)" && native_idle && no_app_mount &&
                selector_lock_absent &&
                remove_owned "$state" || fail partial_state
            printf 'jpeg_closed\n'; exit 0
        fi
        owned "$stage" || fail stage_owner
        lock
        absent "$state" && fail partial_state
        owned "$state" || fail state_owner
        media_mounted && same "$state/boot-id" "$(boot_id_value)" || fail changed_boot
        if ! absent "$state/current"; then
            read -r current <"$state/current" || fail visit_owner
            known_case "$current" && same "$state/case" "$current" || fail visit_owner
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
        if ! absent "$state/lights.before"; then restore_lights || fail guard_lights; fi
        if ! absent "$state/selector-intent"; then
            read -r case_id <"$state/case" || fail case_binding
            known_case "$case_id" && same "$state/selector-intent" "$label $case_id" &&
                plain "$stage/selector/selector.sh" && selector_restore &&
                no_app_mount && selector_lock_absent && original_control || fail program_restore
        elif ! absent "$state/ready"; then
            fail program_restore
        fi
        if ! absent "$state/guard/ready"; then
            same "$state/guard/ready" "$label" || fail guard_admission
            guard restore guard_verified || fail guard_restore
            guard verify guard_verified || fail guard_admission
        fi
        media_mounted && same "$state/boot-id" "$(boot_id_value)" && native_idle &&
            no_app_mount && selector_lock_absent || fail native_recovery
        if absent "$state/closed-pending"; then put "$state/closed-pending" "$label" || fail close_intent; fi
        same "$state/closed-pending" "$label" && remove_owned "$stage" && remove_owned "$state" || fail cleanup
        printf 'jpeg_closed\n';;
    absent)
        [ "$#" = 0 ] || fail arguments
        media_mounted && directory "$private" && absent "$stage" && absent "$state" &&
            native_idle && no_app_mount && selector_lock_absent || fail stage_present
        printf 'jpeg_absent\n';;
    *) fail operation;;
esac
