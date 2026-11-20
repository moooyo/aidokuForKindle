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
silence() { "$@" >/dev/null; }
lock() {
    exec 7<"$stage"
    remaining=90
    until flock -n 7; do remaining=$((remaining - 1)); [ "$remaining" -gt 0 ] || fail live_operation; usleep 200000; done
}
case_name() {
    case "$1" in
        navigation|reference|control-warmup|candidate-warmup|control-picture|candidate-picture|control-0[1-9]|candidate-0[1-9]|control-10|candidate-10) return 0;;
        *) return 1;;
    esac
}
assert_stage() { owned "$stage" && owned "$state" && directory "$output"; }
visit() { sh "$stage/visit/visit.sh" "$1" "$label" "$name" ${2:+"$2"} >/dev/null; }
guard() { sh "$stage/guard/guard.sh" "$1" "$label" >/dev/null; }
programs() { sh "$stage/programs/programs.sh" "$1" "$label" >/dev/null; }
installed_control() (
    manifest=$stage/control/program-modes
    plain "$manifest" || exit 1
    modes=''
    for relative in app/aidoku launch.sh app/aidoku-startup-portrait; do
        live=$media/aidoku/$relative; expected=$stage/control/$relative
        plain "$live" && plain "$expected" && [ -x "$live" ] && cmp -s "$live" "$expected" || exit 1
        mode=$(stat -c %a "$live") || exit 1
        case "$mode" in [0-7][0-7][0-7]) ;; *) exit 1;; esac
        modes="${modes}${mode}
"
    done
    printf '%s' "$modes" | cmp -s - "$manifest"
)
observer=$stage/observer/framewatch
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
run_case() {
    case_name "$name" && directory "$output" && absent "$state/current" && absent "$output/$name" &&
        same "$state/ready" "$label" || fail new_visit
    case "$kind:$name" in
        reference:reference) same "$state/mode" reference || fail reference_mode ;;
        case:control-0[1-9]|case:candidate-0[1-9]|case:control-10|case:candidate-10|case:control-warmup|case:candidate-warmup|picture:control-picture|picture:candidate-picture)
            if same "$state/mode" tapcheck; then
                [ "$kind:$name:$origin" = case:control-warmup:stock-tap ] || fail tapcheck_visit
                installed_control || fail changed_control
            else
                same "$state/mode" study || fail study_mode
                programs "${name%%-*}" || fail program_selection
            fi;;
        *) fail case_kind;;
    esac
    case "$origin" in indexed-uri|stock-tap) ;; *) fail source;; esac
    guard verify || fail previsit_protection
    space "$private" 28000 || fail private_space
    mkdir "$output/$name" && put "$state/current" "$name" && put "$output/$name/origin" "$origin" || fail visit_intent
    out=$output/$name
    visit prepare || fail visit_prepare
    if [ "$origin" = stock-tap ]; then
        plain "$stage/input/dispatch.sh" || fail no_qualified_pointer
        input_begin
        sh "$stage/input/dispatch.sh" target "$label" "$name" >/dev/null || fail native_tile
    fi
    "$observer" capture "$out/stock.frame" || fail initial_capture
    visit arm "$origin" || fail visit_arm
    if [ "$kind" = case ]; then
        "$observer" watch "$out/trace" 2>"$out/watch-status" 7>&- &
        watcher=$!
        tries=75
        until grep -Fxq READY "$out/watch-status" 2>/dev/null; do
            kill -0 "$watcher" 2>/dev/null || fail watcher_started
            tries=$((tries - 1)); [ "$tries" -gt 0 ] || fail watcher_started; usleep 20000
        done
        usleep 200000 || fail pretrigger_dwell
    fi
    if [ "$origin" = indexed-uri ]; then
        put "$out/dispatched" "$origin" || fail duplicate_request
        start=$("$observer" clock) || fail trigger_clock
        lipc-set-prop com.lab126.appmgrd start file:///mnt/us/documents/Aidoku.sh || fail native_dispatch
        end=$("$observer" clock) && printf '%s %s\n' "$start" "$end" >"$out/anchor" || fail trigger_clock
    else
        sh "$stage/input/dispatch.sh" tap "$label" "$name" >"$out/anchor" || fail tile_dispatch
    fi
    if [ "$kind" = case ]; then wait "$watcher" || fail observer_trace; else sleep 12; fi
    visit bind || fail normal_session
    case "$kind" in
        reference)
            "$observer" capture "$out/library-a.frame" && usleep 400000 &&
                "$observer" capture "$out/library-b.frame" || fail reference_capture;;
        picture) "$observer" capture "$out/final.frame" || fail picture_capture;;
    esac
    visit check && visit exit || fail normal_exit
    guard verify || fail postvisit_protection
    if same "$state/mode" tapcheck; then installed_control || fail changed_control; fi
    same "$state/current" "$name" && put "$out/qualified" "$name $origin" && rm "$state/current" || fail visit_close
}
files_for() {
    case_name "$1" && directory "$output/$1" || return 1
    if [ "$1" = navigation ]; then
        same "$output/$1/qualified" 'navigation stock_restored' || return 1
        leaves='library.frame qualified'
    else
        recorded=$(cat "$output/$1/origin") || return 1
        case "$recorded" in indexed-uri|stock-tap) ;; *) return 1;; esac
        same "$output/$1/qualified" "$1 $recorded" || return 1
        case "$1" in reference) leaves='stock.frame library-a.frame library-b.frame origin anchor qualified';;
            *-picture) leaves='stock.frame final.frame origin anchor qualified';;
            *) leaves='stock.frame trace origin anchor qualified';;
        esac
    fi
    for leaf in $leaves; do
        plain "$output/$1/$leaf" && [ -s "$output/$1/$leaf" ] || return 1
        printf 'out/%s/%s\n' "$1" "$leaf"
    done
}
remove_owned() {
    owned "$1" && rm -rf "$1" && absent "$1"
}
case "$operation" in
    prepare)
        [ "$#" = 1 ] || fail arguments
        case "$1" in reference) required=20480;; navigation) required=98304;; tapcheck) required=163840;; study) required=286720;; *) fail mode;; esac
        directory "$media" && directory "$private" && absent "$stage" && absent "$state" &&
            space "$media" "$required" && space "$private" 28000 || fail stage_admission
        mkdir "$stage" && put "$stage/owner" "$label" || fail new_media
        lock
        mkdir "$state" && put "$state/owner" "$label" && chmod 700 "$state" &&
            [ "$(stat -c '%u:%a' "$state")" = 0:700 ] && mkdir "$output" && put "$state/mode" "$1" || fail new_state
        touch_stock_idle || fail prepare_idle_guard
        tar -xzf - -C "$stage" || fail payload
        assert_stage && plain "$stage/guard/guard.sh" && plain "$observer" && [ -x "$observer" ] || fail missing_modules
        if [ "$1" = navigation ]; then
            plain "$stage/input/navigation.sh" || fail missing_navigation
        else
            plain "$stage/visit/visit.sh" || fail missing_visit
        fi
        guard capture || fail guard_capture
        if [ "$1" = tapcheck ]; then
            absent "$stage/programs" && absent "$stage/rename-new" && absent "$stage/candidate" &&
                installed_control || fail tapcheck_control
        elif [ "$1" = study ]; then
            plain "$stage/programs/programs.sh" && plain "$stage/rename-new" && [ -x "$stage/rename-new" ] &&
                put "$state/program-intent" "$label" && programs prepare || fail program_prepare
        fi
        put "$state/ready" "$label" || fail readiness
        printf 'device_prepared\n';;
    navigation)
        [ "$#" = 0 ] && assert_stage || fail stage_owner
        lock
        same "$state/mode" navigation && same "$state/ready" "$label" &&
            absent "$state/current" && absent "$output/navigation" && absent "$state/pointer-intent" || fail navigation_intent
        guard verify || fail previsit_protection
        mkdir "$output/navigation" || fail navigation_intent
        input_begin
        "$observer" capture "$output/navigation/library.frame" || fail library_capture
        input_restore
        guard verify || fail postvisit_protection
        put "$output/navigation/qualified" 'navigation stock_restored' || fail navigation_receipt
        printf 'device_navigation\n';;
    reference|case|picture)
        assert_stage || fail stage_owner
        lock
        kind=$operation
        if [ "$kind" = reference ]; then
            [ "$#" = 0 ] || fail arguments
            name=reference; origin=indexed-uri
        else
            [ "$#" = 2 ] || fail arguments
            name=$1; origin=$2
        fi
        run_case
        printf 'device_%s\n' "$kind";;
    collect|collect-case)
        assert_stage || fail stage_owner
        lock
        if [ "$operation" = collect-case ]; then
            [ "$#" = 1 ] && files=$(files_for "$1") || fail evidence
        else
            [ "$#" = 0 ] || fail arguments
            files=''
            set +f
            for item in "$output"/*; do
                directory "$item" || continue
                [ -f "$item/qualified" ] || continue
                if same "$item/exported" "$label"; then continue; fi
                subset=$(files_for "${item##*/}") || fail evidence
                files="$files $subset"
            done
            set -f
        fi
        [ -n "$files" ] || fail no_qualified_evidence
        tar -czf - -C "$state" $files || fail transfer;;
    discard-case)
        assert_stage && [ "$#" = 1 ] || fail stage_owner
        lock
        files=$(files_for "$1") || fail evidence
        for item in $files; do
            case "$item" in */stock.frame|*/library.frame|*/library-a.frame|*/library-b.frame|*/final.frame|*/trace) rm "$state/$item" || fail discard;; esac
        done
        if same "$state/mode" study && plain "$stage/birth_ticks.sh"; then
            ( . "$stage/birth_ticks.sh" && birth_ticks "$state/visit-$1/bound" ) || :
        fi
        put "$output/$1/exported" "$label" && printf 'device_discarded\n';;
    close)
        [ "$#" = 0 ] || fail arguments
        if absent "$stage" && absent "$state"; then printf 'device_closed\n'; exit 0; fi
        if absent "$stage"; then
            owned "$state" && same "$state/closed-pending" "$label" && remove_owned "$state" || fail partial_state
            printf 'device_closed\n'; exit 0
        fi
        owned "$stage" || fail stage_owner
        lock
        if absent "$state"; then remove_owned "$stage" || fail cleanup; printf 'device_closed\n'; exit 0; fi
        owned "$state" || fail state_owner
        if ! absent "$state/current"; then
            plain "$state/current" && name=$(cat "$state/current") && case_name "$name" && same "$state/current" "$name" || fail visit_owner
            if [ -f "$state/visit-$name/owner" ]; then visit recover || fail visit_recovery
            else absent "$state/visit-live" && absent "$state/visit-$name/request" || fail visit_recovery; fi
            rm "$state/current" || fail visit_recovery
        fi
        absent "$state/visit-live" || fail live_visit
        if same "$state/program-intent" "$label"; then programs restore || fail program_recovery
        elif ! absent "$state/program-intent"; then fail program_owner; fi
        input_restore
        if same "$state/guard/ready" "$label"; then guard restore || fail guard_restore
        elif ! absent "$state/ready" || ! absent "$state/program-intent"; then fail guard_owner; fi
        if absent "$state/closed-pending"; then put "$state/closed-pending" "$label" || fail close_intent; fi
        same "$state/closed-pending" "$label" && remove_owned "$stage" && remove_owned "$state" || fail cleanup
        printf 'device_closed\n';;
    *) fail operation;;
esac
