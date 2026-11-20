#!/bin/sh
set -efu
export LC_ALL=C
umask 077
fail() { printf 'refused %s\n' "$1" >&2; exit 1; }
[ "$#" -eq 2 ] || fail arguments
operation=$1; label=$2
case "$operation" in prepare|control|candidate|restore) ;; *) fail operation;; esac
case "$label" in *[!0-9a-f]*|'') fail label;; esac
[ "${#label}" -eq 16 ] || fail label
media=/mnt/us
home=$media/aidoku
stage=$media/.aidoku-native-$label
state=/var/tmp/aidoku-native-$label
journal=$state/program-journal
lock=/tmp/aidoku-launcher.lock
lockseed=/tmp/.aidoku-native-lock-$label
absent() { [ ! -e "$1" ] && [ ! -L "$1" ]; }
regular() { [ -f "$1" ] && [ ! -L "$1" ] && [ "$(readlink -f "$1")" = "$1" ]; }
directory() { [ -d "$1" ] && [ ! -L "$1" ] && [ "$(readlink -f "$1")" = "$1" ]; }
marker() { regular "$1" && printf '%s\n' "$label" | cmp -s - "$1"; }
write_new() { (set -C; printf '%s\n' "$2" >"$1"); }
single_dir() { [ "$(ls -A "$1")" = "$2" ]; }
move_new() { absent "$2" && "$stage/rename-new" "$1" "$2" && absent "$1"; }
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
matches() { regular "$1" && regular "$2" && cmp -s "$1" "$2"; }
identity() { regular "$1" && stat -c '%d:%i:%u:%g:%a:%s:%y:%z' "$1"; }
leaf() {
    relative=$1; key=${relative##*/}
    original=$stage/control/$relative; selected=$stage/candidate/$relative
    live=$home/$relative; intent=$journal/$key.mount-intent; attached=$journal/$key.mount-id
}
mount_record() {
    awk -v target="$live" -v src="$selected" '
      $5==target {tr++; line_id=$1; p=$2; d=$3; root=$4; next}
      $5==src {source_is_mount=1; next}
      { if ($5=="/" || index(target,$5 "/")==1) {
            n=length($5); if(n>tl){ti=$1;tl=n;tc=1}else if(n==tl)tc++ }
        if ($5=="/" || index(src,$5 "/")==1) {
            n=length($5); if(n>sl){sr=$4;sm=$5;sd=$3;sl=n;sc=1}else if(n==sl)sc++ } }
      END {if(!tl || !sl || tc!=1 || sc!=1 || source_is_mount || tr>1) exit 1
           if(!tr){print "none";exit 0}
           if(sm=="/")suffix=src;else suffix=substr(src,length(sm)+1)
           if(sr=="/")sr=""; expected=sr suffix
           if(line_id!~/^[1-9][0-9]*$/ || p!=ti || d!=sd || root!=expected) exit 1
           print line_id}' /proc/self/mountinfo
}
unchanged() {
    observed_info=$(identity "$1") && regular "$2" && [ -n "$observed_info" ] && [ "$observed_info" = "$(cat "$2")" ]
}
classify() {
    unchanged "$selected" "$journal/$key.source" || return 1
    mount_id=$(mount_record) || return 1
    if [ "$mount_id" = none ]; then
        unchanged "$live" "$journal/$key.old" || return 1
        if absent "$intent"; then absent "$attached" || return 1
        else marker "$intent" || return 1; fi
        printf 'old\n'
    else
        [ "$relative" != app/aidoku-startup-portrait ] && marker "$intent" && unchanged "$live" "$journal/$key.source" || return 1
        if ! absent "$attached"; then regular "$attached" && printf '%s\n' "$mount_id" | cmp -s - "$attached" || return 1; fi
        printf '%s\n' "$mount_id"
    fi
}
inspect() {
    marker "$journal/sealed" || return 1
    for relative in app/aidoku launch.sh app/aidoku-startup-portrait; do
        leaf "$relative"
        classify >/dev/null || return 1
    done
}
owned_stages() {
    directory "$stage" && directory "$state" && marker "$stage/owner" && marker "$state/owner" &&
        directory "$home" && directory "$home/app" || return 1
    for arm in control candidate; do
        directory "$stage/$arm" && directory "$stage/$arm/app" || return 1
        for relative in app/aidoku launch.sh app/aidoku-startup-portrait; do regular "$stage/$arm/$relative" || return 1; done
    done
    regular "$stage/control/program-modes" && regular "$stage/rename-new" && [ -x "$stage/rename-new" ]
}
verify_originals() {
    modes=''
    for relative in app/aidoku launch.sh app/aidoku-startup-portrait; do
        leaf "$relative"
        [ "$(mount_record)" = none ] && matches "$live" "$original" && [ -x "$live" ] || return 1
        [ "$(stat -c '%u:%g:%a' "$live")" = "$(stat -c '%u:%g:%a' "$selected")" ] || return 1
        if [ "$relative" = app/aidoku-startup-portrait ]; then matches "$live" "$selected" || return 1
        else
            if cmp -s "$original" "$selected"; then return 1; else [ "$?" -eq 1 ] || return 1; fi
        fi
        value=$(stat -c %a "$live") || return 1
        case "$value" in [0-7][0-7][0-7]) ;; *) return 1;; esac
        modes="${modes}${value}
"
    done
    printf '%s' "$modes" | cmp -s - "$stage/control/program-modes"
}
reason() { printf 'refused %s\n' "$1" >&2; return 1; }
prepare() {
    verify_originals || { reason control_or_modes; return 1; }
    absent "$journal" && mkdir "$journal" && write_new "$journal/owner" "$label" || { reason journal_creation; return 1; }
    for relative in app/aidoku launch.sh app/aidoku-startup-portrait; do
        leaf "$relative"
        identity "$live" >"$journal/$key.old" && [ -x "$selected" ] && identity "$selected" >"$journal/$key.source" ||
            { reason program_metadata; return 1; }
    done
    write_new "$journal/sealed" "$label" && inspect || { reason sealed_tuple; return 1; }
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
select_arm() {
    want=$1
    inspect || { reason mount_or_file_changed; return 1; }
    for relative in app/aidoku launch.sh; do
        leaf "$relative"
        observed=$(classify) || return 1
        if [ "$want" = candidate ]; then
            if [ "$observed" = old ]; then
                clear_intent && publish_record "$intent" "$label" && [ "$(classify)" = old ] || return 1
                mount -o bind "$selected" "$live" || { reason bind_refused; return 1; }
                observed=$(classify) && [ "$observed" != old ] || { reason unexpected_bind; return 1; }
            fi
            if absent "$attached"; then publish_id "$observed" || return 1; fi
        else
            if [ "$observed" != old ]; then
                [ "$(classify)" = "$observed" ] && umount "$live" || { reason unmount_refused; return 1; }
            fi
            [ "$(classify)" = old ] && clear_intent || return 1
        fi
    done
}
owned_stages || fail stage
exec 9<"$state"; flock -n 9 || fail live_operation
parent_bound=$(parent_identity) && [ -n "$parent_bound" ] || fail tmp_parent
parent_real=$(printf '%s\n' "$parent_bound" | head -n 1)
lock_bound=''
sealed=0
if ! absent "$journal"; then
    directory "$journal" || fail journal
    if ! absent "$journal/sealed"; then
        if marker "$journal/owner" && marker "$journal/sealed"; then sealed=1
        elif [ "$operation" != restore ]; then fail journal; fi
    fi
fi
native || fail native
locked=0; ready=0
finish() {
    result=$?
    trap - 0 HUP INT TERM
    if [ "$locked" -eq 1 ]; then
        if [ "$result" -ne 0 ] && [ "$ready" -eq 1 ]; then
            if ! { owned_lock && native && select_arm control; }; then
                printf 'refused retained_program_lock\n' >&2; exit 1
            fi
        fi
        drop_lock || { printf 'refused lock_release\n' >&2; exit 1; }
    fi
    exit "$result"
}
trap finish 0
trap 'exit 1' HUP INT TERM
take_lock || fail program_lock
locked=1
native && owned_lock || fail recheck
if [ "$operation" = restore ] && [ "$sealed" = 0 ]; then
    verify_originals || fail unsealed_restore
elif [ "$operation" = prepare ] && [ "$sealed" = 0 ]; then
    prepare || fail prepare
else
    [ "$sealed" = 1 ] || fail unprepared
    ready=1
    case "$operation" in candidate) select_arm candidate || fail selection;;
        *) select_arm control || fail restoration;; esac
    if [ "$operation" = restore ]; then verify_originals || fail restored_control_bytes; fi
fi
drop_lock || fail lock_release
locked=0
printf 'programs_%s\n' "$operation"
