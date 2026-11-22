#!/bin/sh
set -efu
export LC_ALL=C
umask 077

fail() { printf 'refused %s\n' "$1" >&2; exit 1; }
reason() { printf 'refused %s\n' "$1" >&2; return 1; }

case_name() {
    case "$1" in
        control-warmup|candidate-warmup|control-0[1-6]|candidate-0[1-6]) return 0;;
        *) return 1;;
    esac
}

[ "$#" -ge 2 ] || fail arguments
operation=$1
label=$2
case "$operation" in prepare|select|check|restore|absent) ;; *) fail operation;; esac
case "$label" in ''|*[!0-9a-f]*) fail label;; esac
[ "${#label}" -eq 16 ] || fail label

if [ "$operation" = prepare ] || [ "$operation" = select ] || [ "$operation" = check ]; then
    [ "$#" -eq 3 ] || fail arguments
    visit_case=$3
    case_name "$visit_case" || fail case
else
    [ "$#" -eq 2 ] || fail arguments
    visit_case=
fi

media=/mnt/us
private=/var/tmp
home=$media/aidoku
stage=$media/.aidoku-native-$label
state=$private/aidoku-native-$label
journal=$state/selector-journal
lock=/tmp/aidoku-launcher.lock
lockseed=/tmp/.aidoku-selector-lock-$label
live=$home/app/aidoku
original=$stage/control/app/aidoku
launcher=$home/launch.sh
launcher_original=$stage/control/launch.sh
helper=$home/app/aidoku-startup-portrait
helper_original=$stage/control/app/aidoku-startup-portrait
selected=$stage/programs/selected-app
manifest=$stage/control/program-modes
intent=$journal/aidoku.mount-intent
attached=$journal/aidoku.mount-id

absent() { [ ! -e "$1" ] && [ ! -L "$1" ]; }
regular() { [ -f "$1" ] && [ ! -L "$1" ] && [ "$(readlink -f "$1")" = "$1" ]; }
directory() { [ -d "$1" ] && [ ! -L "$1" ] && [ "$(readlink -f "$1")" = "$1" ]; }
marker() { regular "$1" && printf '%s\n' "$label" | cmp -s - "$1"; }
write_new() { (set -C; printf '%s\n' "$2" >"$1"); }
single_dir() { [ "$(ls -A "$1")" = "$2" ]; }
move_new() { absent "$2" && "$stage/rename-new" "$1" "$2" && absent "$1"; }
matches() { regular "$1" && regular "$2" && cmp -s "$1" "$2"; }
identity() { regular "$1" && stat -c '%d:%i:%u:%g:%a:%s:%y:%z' "$1"; }

live_unmounted() {
    awk -v target="$live" '
      $5 == target { seen = 1; exit 1 }
      END { exit seen ? 1 : 0 }' /proc/self/mountinfo
}

parent_identity() (
    base=${lock%/*}
    [ "$(stat -c %u "$base")" = 0 ] || exit 1
    resolved=$(readlink -f "$base") && directory "$resolved" || exit 1
    entry=$(stat -c '%d:%i:%u:%g:%a:%z' "$base") &&
        identity=$(stat -c '%d:%i:%u:%g:%a' "$resolved") || exit 1
    [ "$identity" = "$(stat -L -c '%d:%i:%u:%g:%a' "$base")" ] && [ "$(stat -c %u "$resolved")" = 0 ] || exit 1
    if [ ! -L "$base" ]; then entry=$identity; fi
    printf '%s\n%s\n%s\n' "$resolved" "$entry" "$identity"
)
same_parent() { [ -n "$parent_bound" ] && [ "$(parent_identity)" = "$parent_bound" ]; }
lock_dir() (
    case "$1" in "$lock"|"$lockseed") ;; *) exit 1;; esac
    same_parent && [ -d "$1" ] && [ ! -L "$1" ] || exit 1
    real_leaf=$parent_real/${1##*/}
    [ "$(readlink -f "$1")" = "$real_leaf" ] && [ ! -L "$real_leaf" ] || exit 1
    ident=$(stat -c '%d:%i:%u:%g:%a' "$1") && [ "$ident" = "$(stat -c '%d:%i:%u:%g:%a' "$real_leaf")" ] || exit 1
    [ "$(stat -c %u "$1")" = 0 ] && [ "$(stat -c %d "$1")" = "$(stat -c %d "$parent_real")" ] || exit 1
    case "$ident" in *:700|*:2700) ;; *) exit 1;; esac
    printf '%s\n' "$ident"
)
lock_snapshot() (
    ident=$(lock_dir "$1") && [ -n "$ident" ] && single_dir "$1" owner || exit 1
    file=$parent_real/${1##*/}/owner
    regular "$file" && [ "$(stat -c '%u:%a:%h' "$file")" = 0:600:1 ] && marker "$file" || exit 1
    owner_info=$(stat -c '%d:%i:%u:%g:%a:%s:%y:%z' "$file") || exit 1
    [ "$ident" = "$(lock_dir "$1")" ] && [ "$owner_info" = "$(stat -c '%d:%i:%u:%g:%a:%s:%y:%z' "$1/owner")" ] || exit 1
    printf '%s\n%s\n' "$ident" "$owner_info"
)
empty_programs() {
    for reader in aidoku weread bilicomics; do
        if pidof "$reader" >/dev/null; then return 1; else [ "$?" = 1 ] || return 1; fi
        if [ "$reader" != aidoku ]; then absent "/tmp/$reader-launcher.lock" || return 1; fi
    done
}
native() {
    empty_programs &&
        [ "$(lipc-get-prop -s com.lab126.appmgrd activeApp)" = com.lab126.KPPMainApp ] &&
        [ "$(lipc-get-prop -s com.lab126.powerd state)" = active ] || return 1
    case "$(status statusbar)" in *start/running*) ;; *) return 1;; esac
    managers=$(pidof awesome) && [ -n "$managers" ] || return 1
    for manager in $managers; do
        case "$manager" in ''|*[!0-9]*) return 1;; esac
        case "$(sed -n 's/^State:[[:space:]]*//p' "/proc/$manager/status")" in R*|S*) ;; *) return 1;; esac
    done
}
owned_lock() { [ -n "$lock_bound" ] && [ "$(lock_snapshot "$lock")" = "$lock_bound" ]; }
drop_lock() {
    owned_lock && move_new "$lock" "$lockseed" &&
        [ "$(lock_snapshot "$lockseed")" = "$lock_bound" ] && rm "$lockseed/owner" &&
        [ "$(lock_dir "$lockseed")" = "$(printf '%s\n' "$lock_bound" | head -n 1)" ] && rmdir "$lockseed"
}
take_lock() {
    same_parent || return 1
    if ! absent "$lockseed"; then
        seed_id=$(lock_dir "$lockseed") && [ -n "$seed_id" ] || return 1
        if ! single_dir "$lockseed" ''; then
            seed_owner=$(lock_snapshot "$lockseed") && [ -n "$seed_owner" ] &&
                [ "$(lock_snapshot "$lockseed")" = "$seed_owner" ] && rm "$lockseed/owner" || return 1
        fi
        [ "$(lock_dir "$lockseed")" = "$seed_id" ] && rmdir "$lockseed" || return 1
    fi
    if ! absent "$lock"; then
        lock_bound=$(lock_snapshot "$lock") && [ -n "$lock_bound" ] && owned_lock
        return
    fi
    mkdir "$lockseed" && lock_dir "$lockseed" >/dev/null && write_new "$lockseed/owner" "$label" &&
        lock_bound=$(lock_snapshot "$lockseed") && [ -n "$lock_bound" ] && move_new "$lockseed" "$lock" && owned_lock
}

mount_record() {
    awk -v target="$live" -v src="$selected" '
      $5 == target {tr++; line_id=$1; p=$2; d=$3; root=$4; next}
      $5 == src {source_is_mount=1; next}
      {
        if ($5 == "/" || index(target, $5 "/") == 1) {
            n = length($5); if (n > tl) {ti=$1; tl=n; tc=1} else if (n == tl) tc++
        }
        if ($5 == "/" || index(src, $5 "/") == 1) {
            n = length($5); if (n > sl) {sr=$4; sm=$5; sd=$3; sl=n; sc=1} else if (n == sl) sc++
        }
      }
      END {
        if (!tl || !sl || tc != 1 || sc != 1 || source_is_mount || tr > 1) exit 1
        if (!tr) { print "none"; exit 0 }
        if (sm == "/") suffix = src; else suffix = substr(src, length(sm) + 1)
        if (sr == "/") sr = ""
        expected = sr suffix
        if (line_id !~ /^[1-9][0-9]*$/ || p != ti || d != sd || root != expected) exit 1
        print line_id
      }' /proc/self/mountinfo
}
unchanged() {
    observed_info=$(identity "$1") && regular "$2" && [ -n "$observed_info" ] && [ "$observed_info" = "$(cat "$2")" ]
}
classify() {
    unchanged "$selected" "$journal/aidoku.source" || return 1
    mounted=$(mount_record) || return 1
    if [ "$mounted" = none ]; then
        unchanged "$live" "$journal/aidoku.old" || return 1
        if absent "$intent"; then absent "$attached" || return 1
        else marker "$intent" || return 1; fi
        printf 'old\n'
        return
    fi
    marker "$intent" && unchanged "$live" "$journal/aidoku.source" || return 1
    if ! absent "$attached"; then regular "$attached" && printf '%s\n' "$mounted" | cmp -s - "$attached" || return 1; fi
    printf '%s\n' "$mounted"
}
inspect() {
    expected_case=$1
    marker "$journal/sealed" && marker "$journal/owner" || return 1
    regular "$journal/case" && case_name "$(cat "$journal/case")" || return 1
    if [ -n "$expected_case" ]; then printf '%s\n' "$expected_case" | cmp -s - "$journal/case" || return 1; fi
    classify >/dev/null || return 1
}

owned_stages() {
    directory "$stage" && directory "$state" && marker "$stage/owner" && marker "$state/owner" &&
        directory "$home" && directory "$home/app" && directory "$stage/control" &&
        directory "$stage/control/app" && directory "$stage/programs" || return 1
    for file in "$original" "$launcher_original" "$helper_original" "$selected" "$manifest" "$stage/rename-new"; do
        regular "$file" || return 1
    done
    [ -x "$live" ] && [ -x "$selected" ] && [ -x "$stage/rename-new" ]
}

verify_originals() {
    [ "$(mount_record)" = none ] || return 1
    matches "$live" "$original" || return 1
    matches "$launcher" "$launcher_original" || return 1
    matches "$helper" "$helper_original" || return 1
    if cmp -s "$live" "$selected"; then return 1; else [ "$?" -eq 1 ] || return 1; fi
    modes=''
    for file in "$live" "$launcher" "$helper"; do
        value=$(stat -c %a "$file") || return 1
        case "$value" in [0-7][0-7][0-7]) ;; *) return 1;; esac
        modes="${modes}${value}
"
    done
    printf '%s' "$modes" | cmp -s - "$manifest"
}

clear_intent() {
    if ! absent "$attached"; then regular "$attached" && rm "$attached" || return 1; fi
    if ! absent "$intent"; then marker "$intent" && rm "$intent" || return 1; fi
    absent "$attached" && absent "$intent"
}
publish_record() {
    pending=$1.pending
    if ! absent "$pending"; then regular "$pending" && rm "$pending" || return 1; fi
    write_new "$pending" "$2" && absent "$1" && mv "$pending" "$1"
}
publish_id() {
    publish_record "$attached" "$1" && [ "$(classify)" = "$1" ]
}

prepare() {
    requested=$1
    verify_originals || { reason control_or_modes; return 1; }
    absent "$journal" && mkdir "$journal" && write_new "$journal/owner" "$label" || { reason journal_creation; return 1; }
    identity "$live" >"$journal/aidoku.old" && identity "$selected" >"$journal/aidoku.source" ||
        { reason program_metadata; return 1; }
    write_new "$journal/case" "$requested" && write_new "$journal/sealed" "$label" &&
        inspect "$requested" || { reason sealed_tuple; return 1; }
}

select_selected() {
    requested=$1
    inspect "$requested" || { reason mount_or_file_changed; return 1; }
    observed=$(classify) || return 1
    if [ "$observed" = old ]; then
        clear_intent && publish_record "$intent" "$label" && [ "$(classify)" = old ] || return 1
        mount -o bind "$selected" "$live" || { reason bind_refused; return 1; }
        observed=$(classify) && [ "$observed" != old ] || { reason unexpected_bind; return 1; }
    fi
    if absent "$attached"; then publish_id "$observed" || return 1; fi
}

verify_selected() {
    requested=$1
    inspect "$requested" || { reason mount_or_file_changed; return 1; }
    observed=$(classify) || return 1
    [ "$observed" != old ] || { reason not_selected; return 1; }
    regular "$attached" && printf '%s\n' "$observed" | cmp -s - "$attached" || return 1
}

select_original() {
    inspect '' || { reason mount_or_file_changed; return 1; }
    observed=$(classify) || return 1
    if [ "$observed" != old ]; then
        [ "$(classify)" = "$observed" ] && umount "$live" || { reason unmount_refused; return 1; }
    fi
    [ "$(classify)" = old ] && clear_intent || return 1
}

clear_journal() {
    marker "$journal/owner" || return 1
    for entry in aidoku.mount-id aidoku.mount-intent aidoku.old aidoku.source case sealed owner; do
        file=$journal/$entry
        if ! absent "$file"; then regular "$file" && rm "$file" || return 1; fi
    done
    rmdir "$journal"
}

if [ "$operation" = absent ]; then
    directory "$media" && directory "$private" && regular "$live" && absent "$stage" && absent "$state" &&
        absent "$lock" && absent "$lockseed" && live_unmounted || fail stage_present
    printf 'selector_absent\n'
    exit 0
fi

if [ "$operation" = check ]; then
    owned_stages || fail stage
    directory "$journal" && marker "$journal/owner" && marker "$journal/sealed" || fail unprepared
    verify_selected "$visit_case" || fail verify
    printf 'selector_check\n'
    exit 0
fi

owned_stages || fail stage
exec 9<"$state"
flock -n 9 || fail live_operation
parent_bound=$(parent_identity) && [ -n "$parent_bound" ] || fail tmp_parent
parent_real=$(printf '%s\n' "$parent_bound" | head -n 1)
lock_bound=
sealed=0
journal_present=0
if ! absent "$journal"; then
    directory "$journal" || fail journal
    journal_present=1
    marker "$journal/owner" || fail journal
    if ! absent "$journal/sealed"; then
        if marker "$journal/sealed"; then sealed=1
        elif [ "$operation" != restore ]; then fail journal; fi
    fi
fi

native || fail native
locked=0
ready=0
finish() {
    result=$?
    trap - 0 HUP INT TERM
    if [ "$locked" -eq 1 ]; then
        if [ "$result" -ne 0 ] && [ "$ready" -eq 1 ]; then
            if ! { owned_lock && native && select_original; }; then
                printf 'refused retained_selector_lock\n' >&2
                exit 1
            fi
        fi
        drop_lock || { printf 'refused lock_release\n' >&2; exit 1; }
    fi
    exit "$result"
}
trap finish 0
trap 'exit 1' HUP INT TERM

take_lock || fail selector_lock
locked=1
native && owned_lock || fail recheck
case "$operation" in
    prepare)
        [ "$sealed" = 0 ] || fail unprepared
        prepare "$visit_case" || fail prepare
        ;;
    select)
        [ "$sealed" = 1 ] || fail unprepared
        ready=1
        select_selected "$visit_case" || fail selection
        ;;
    restore)
        if [ "$sealed" = 0 ]; then
            verify_originals || fail unsealed_restore
            if [ "$journal_present" = 1 ]; then rm -rf "$journal"; fi
        else
            ready=1
            select_original || fail restoration
            verify_originals || fail restored_control_bytes
            clear_journal || fail journal_clear
        fi
        ;;
esac
drop_lock || fail lock_release
locked=0
printf 'selector_%s\n' "$operation"
