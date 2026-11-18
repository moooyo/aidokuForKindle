#!/bin/sh
# Change only the selected program files while the application is stopped.
set -eu
set -f
export LC_ALL=C
umask 077
MEDIA=/mnt/us
LOCK=/tmp/aidoku-launcher.lock

die() { printf 'Program transaction: %s\n' "$1" >&2; exit 1; }
regular() { [ -f "$1" ] && [ ! -L "$1" ]; }
directory() { [ -d "$1" ] && [ ! -L "$1" ]; }
absent() { [ ! -e "$1" ] && [ ! -L "$1" ]; }
digest() (
    regular "$1" || exit 1
    line=$(sha256sum "$1") || exit 1
    printf '%s\n' "${line%% *}"
)
has_digest() { regular "$1" && [ "$(digest "$1")" = "$2" ]; }
marker() { regular "$2" && printf '%s\n' "$1" | cmp -s - "$2"; }
new_marker() { (set -C; printf '%s\n' "$1" >"$2"); }
absent_app() (
    command -v pidof >/dev/null || exit 1
    if pids=$(pidof aidoku 2>/dev/null); then exit 1; else code=$?; fi
    [ "$code" -eq 1 ] && [ -z "$pids" ]
)

[ "$#" = 2 ] || die 'Expected install or rollback and the original transaction ID.'
OP=$1; RUN=$2
case "$OP" in install|rollback) ;; *) die 'Unknown operation.' ;; esac
case "$RUN" in ''|-*|*[!a-zA-Z0-9_-]*) die 'Invalid transaction ID.' ;; esac
[ "${#RUN}" -le 80 ] || die 'Transaction ID is too long.'
STAGE=$(pwd -P) || die 'Cannot resolve the guarded stage.'
BACKUPS=$MEDIA/.aidoku-performance-backups
BACKUP=$BACKUPS/$RUN
MANIFEST=$STAGE/changes.tsv
marker "$RUN" "$STAGE/owner" || die 'The stage belongs to another transaction.'
regular "$MANIFEST" || die 'Missing stage manifest.'
[ "$(wc -c <"$MANIFEST")" -le 600 ] && [ "$(tail -c 1 "$MANIFEST" | od -An -tu1 | tr -d ' \n')" = 10 ] || die 'Invalid manifest size or terminator.'
awk -F'|' '
  NF != 3 || seen[$3]++ || NR > 3 { bad=1 }
  $3 != "aidoku/launch.sh" && $3 != "aidoku/app/aidoku" && $3 != "aidoku/app/aidoku-startup-portrait" { bad=1 }
  $1 == "absent" && $3 != "aidoku/app/aidoku-startup-portrait" { bad=1 }
  $1 != "absent" && ($1 !~ /^[0-9a-f]+$/ || length($1) != 64) { bad=1 }
  $2 !~ /^[0-9a-f]+$/ || length($2) != 64 || $1 == $2 { bad=1 }
  END { exit bad || NR == 0 }
' "$MANIFEST" || die 'The program allowlist is invalid.'
directory "$MEDIA" && directory "$MEDIA/aidoku" && directory "$MEDIA/aidoku/app" || die 'An installed program directory is unavailable or linked.'
LOCKED=0; ARMED=0

leaf() {
    target=$MEDIA/$relative
    key=${relative##*/}
    scratch=${target%/*}/.aidoku-tx-$RUN-$key
    saved=$BACKUP/$key.original
}

check_journal() (
    directory "$BACKUPS" && directory "$BACKUP" && marker "$RUN" "$BACKUP/owner" &&
        marker "$RUN" "$BACKUP/sealed" && regular "$BACKUP/changes.tsv" &&
        cmp -s "$MANIFEST" "$BACKUP/changes.tsv" || exit 1
    while IFS='|' read -r old new relative; do
        leaf
        if [ "$old" != absent ]; then
            has_digest "$saved" "$old" && regular "$BACKUP/$key.mode" || exit 1
            mode=$(cat "$BACKUP/$key.mode") || exit 1
            case "$mode" in [0-7][0-7][0-7]) ;; *) exit 1 ;; esac
        else
            absent "$saved" || exit 1
        fi
    done <"$MANIFEST"
)

before_state() {
    if [ "$old" = absent ]; then absent "$target"; else has_digest "$target" "$old"; fi
}

restore() (
    check_journal || exit 1
    # Check every leaf before restoring any of them. A concurrent program
    # replacement or a corrupted backup must not trigger a partial rollback.
    while IFS='|' read -r old new relative; do
        leaf
        if ! before_state; then
            marker "$RUN" "$BACKUP/$key.intent" && has_digest "$target" "$new" || exit 1
        fi
        if ! absent "$scratch"; then
            directory "$scratch" && marker "$RUN" "$scratch/owner" || exit 1
            for work in "$scratch/new" "$scratch/restore"; do
                absent "$work" || regular "$work" || exit 1
            done
        fi
    done <"$MANIFEST"
    while IFS='|' read -r old new relative; do
        leaf
        if ! before_state; then
            if absent "$scratch"; then
                mkdir "$scratch" && new_marker "$RUN" "$scratch/owner" || exit 1
            fi
            if [ "$old" = absent ]; then
                marker "$RUN" "$BACKUP/$key.intent" && has_digest "$target" "$new" && rm "$target" || exit 1
            else
                cp -p "$saved" "$scratch/restore" && chmod "$(cat "$BACKUP/$key.mode")" "$scratch/restore" &&
                    has_digest "$scratch/restore" "$old" && has_digest "$target" "$new" &&
                    mv -f "$scratch/restore" "$target" || exit 1
            fi
        fi
        before_state || exit 1
        if [ "$old" != absent ]; then
            [ "$(stat -c %a "$target")" = "$(cat "$BACKUP/$key.mode")" ] || exit 1
        fi
        if ! absent "$scratch"; then
            marker "$RUN" "$scratch/owner" || exit 1
            for work in "$scratch/new" "$scratch/restore"; do
                if ! absent "$work"; then regular "$work" && rm "$work" || exit 1; fi
            done
            rm "$scratch/owner" && rmdir "$scratch" || exit 1
        fi
    done <"$MANIFEST"
    marker "$RUN" "$BACKUP/rolled-back" || new_marker "$RUN" "$BACKUP/rolled-back" || exit 1
)

finish() {
    status=$?
    trap - 0 HUP INT TERM
    if [ "$ARMED" = 1 ]; then
        if ! restore; then
            printf '%s\n' 'Automatic rollback did not complete; the private journal is retained.' >&2
            status=1
        fi
    fi
    if [ "$LOCKED" = 1 ]; then
        if ! { marker "$RUN" "$LOCK/install-owner" && rm "$LOCK/install-owner" && rmdir "$LOCK"; }; then status=1; fi
    fi
    exit "$status"
}
trap finish 0
trap 'exit 129' HUP; trap 'exit 130' INT; trap 'exit 143' TERM
absent_app || die 'Aidoku must be fully stopped.'
mkdir "$LOCK" || die 'Another process owns the launch lock.'
new_marker "$RUN" "$LOCK/install-owner" || { rmdir "$LOCK" || :; die 'Could not bind the installation lock.'; }
LOCKED=1
absent_app || die 'An application started before the lock was established.'

if [ "$OP" = rollback ]; then
    restore || die 'Rollback refused: journal, target or saved bytes do not match.'
    printf '%s\n' ROLLED_BACK
    exit 0
fi

if absent "$BACKUPS"; then mkdir "$BACKUPS" || die 'Cannot create backup root.'; fi
directory "$BACKUPS" && absent "$BACKUP" || die 'Backup root is unsafe or this transaction ID was already used.'
directory "$STAGE/payload" && directory "$STAGE/payload/aidoku" || die 'The guarded payload is missing or linked.'
while IFS='|' read -r old new relative; do
    leaf
    directory "${STAGE}/payload/${relative%/*}" && has_digest "$STAGE/payload/$relative" "$new" &&
        before_state && absent "$scratch" || die 'Program preflight does not match the frozen plan.'
done <"$MANIFEST"
mkdir "$BACKUP" && new_marker "$RUN" "$BACKUP/owner" && cp "$MANIFEST" "$BACKUP/changes.tsv" || die 'Cannot create the backup journal.'
while IFS='|' read -r old new relative; do
    leaf
    if [ "$old" != absent ]; then
        cp -p "$target" "$saved" && has_digest "$saved" "$old" && before_state || die 'Original program backup differs.'
        stat -c %a "$target" >"$BACKUP/$key.mode" || die 'Cannot record original mode.'
    fi
done <"$MANIFEST"
new_marker "$RUN" "$BACKUP/sealed" && check_journal || die 'Backup journal could not be sealed.'
ARMED=1
while IFS='|' read -r old new relative; do
    leaf
    mkdir "$scratch" && new_marker "$RUN" "$scratch/owner" &&
        cp "$STAGE/payload/$relative" "$scratch/new" && chmod 755 "$scratch/new" &&
        has_digest "$scratch/new" "$new" && [ -x "$scratch/new" ] || die 'Could not stage the candidate in the target filesystem.'
    before_state && new_marker "$RUN" "$BACKUP/$key.intent" &&
        mv -f "$scratch/new" "$target" && has_digest "$target" "$new" && [ -x "$target" ] || die 'Atomic program replacement failed.'
    rm "$scratch/owner" && rmdir "$scratch" || die 'Owned staging cleanup failed.'
done <"$MANIFEST"
new_marker "$RUN" "$BACKUP/installed" || die 'The transaction did not complete.'
ARMED=0
printf '%s\n' INSTALLED
