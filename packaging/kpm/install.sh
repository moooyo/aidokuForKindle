#!/bin/sh
# Install only the frozen Aidoku program allowlist; keep all existing user data.
set -eu
umask 077
PACKAGE_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
PAYLOAD_DIR=$PACKAGE_DIR/payload
MANIFEST=$PACKAGE_DIR/payload.tsv
HOME_DIR=/mnt/us/aidoku
LOCK=/tmp/aidoku-launcher.lock
RUN=$(tr -d '\n-' </proc/sys/kernel/random/uuid)
case "$RUN" in ''|*[!0-9a-fA-F]*) printf '%s\n' 'aidoku-kpm: Invalid backup identity.' >&2; exit 64 ;; esac
[ "${#RUN}" -eq 32 ] || exit 64
ROOT=/mnt/us/.aidoku-install-backups/kpm-$RUN
INSTALLED=0; COMPLETE=0
fail() { printf '%s\n' "aidoku-kpm: $*" >&2; exit 64; }
regular() { [ -f "$1" ] && [ ! -L "$1" ]; }
directory() { [ -d "$1" ] && [ ! -L "$1" ]; }
number() { case "$1" in ''|*[!0-9]*) return 1 ;; *) return 0 ;; esac; }
hash() { [ "${#1}" -eq 64 ] && case "$1" in *[!0-9a-f]*) false ;; *) true ;; esac; }
sha() (
    regular "$1" || exit 1
    line=$(sha256sum "$1") || exit 1
    digest=${line%% *}; hash "$digest" || exit 1
    printf '%s\n' "$digest"
)
manifest_digest() {
    awk -F '|' -v path="$1" '
        NF == 4 && $4 == path { count++; digest = $1 }
        END { if (count != 1) exit 1; print digest }
    ' "$MANIFEST"
}
record() { printf '%s\n' "$2" >"$ROOT/$1.pending" && mv "$ROOT/$1.pending" "$ROOT/$1"; }
no_app() (
    p=''; if p=$(pidof aidoku 2>/dev/null); then exit 1; else c=$?; [ "$c" -eq 1 ] && [ -z "$p" ]; fi
)
no_lock() { [ ! -e "$LOCK" ] && [ ! -L "$LOCK" ]; }
safe_relative() {
    case "$1" in ''|/*|*/|*//*|*[!A-Za-z0-9_./-]*) return 1 ;; esac
    case "/$1/" in */../*|*/./*|*/data/*) return 1 ;; esac
    case "$1" in aidoku/*|documents/Aidoku.sh) ;; *) return 1 ;; esac
}

safe_destination() (
    safe_relative "$1" || exit 1
    path=/mnt/us; saved=$IFS; IFS=/; set -f; set -- $1; IFS=$saved
    for item do path=$path/$item; [ ! -L "$path" ] || exit 1; done
    [ ! -e "$path" ] || regular "$path"
)

backup_metadata() {
    no_app && no_lock || fail 'Metadata backup requires a closed application.'
    [ ! -e "$ROOT/backup-metadata" ] && [ ! -L "$ROOT/backup-metadata" ] || fail 'Metadata backup already exists.'
    mkdir "$ROOT/backup-metadata"; : >"$ROOT/metadata-presence.tsv"
    for relative in library.sqlite3 library.sqlite3-wal library.sqlite3-shm library.sqlite3-journal device-profile; do
        source=$HOME_DIR/data/$relative
        if [ -e "$source" ] || [ -L "$source" ]; then
            regular "$source" || fail 'An existing metadata input is unsafe.'
            cp "$source" "$ROOT/backup-metadata/$relative"
            [ "$(sha "$source")" = "$(sha "$ROOT/backup-metadata/$relative")" ] || fail 'A private metadata backup differs.'
            printf 'present|%s\n' "$relative" >>"$ROOT/metadata-presence.tsv"
        else
            printf 'absent|%s\n' "$relative" >>"$ROOT/metadata-presence.tsv"
        fi
    done
    for source in "$HOME_DIR"/data/sources/state/*/defaults.json; do
        if [ ! -e "$source" ] && [ ! -L "$source" ]; then continue; fi
        state_dir=${source%/defaults.json}; key=${state_dir##*/}
        hash "$key" && regular "$source" && directory "$state_dir" && directory "$HOME_DIR/data/sources/state" && directory "$HOME_DIR/data/sources" || fail 'An existing source preference path is unsafe.'
        relative=sources/state/$key/defaults.json
        mkdir -p "$ROOT/backup-metadata/sources/state/$key"
        cp "$source" "$ROOT/backup-metadata/$relative"
        [ "$(sha "$source")" = "$(sha "$ROOT/backup-metadata/$relative")" ] || fail 'A private source preference backup differs.'
        printf 'present|%s\n' "$relative" >>"$ROOT/metadata-presence.tsv"
    done
    record metadata-backup-complete true
}

install_payload() {
    [ ! -e "$ROOT/backup" ] && [ ! -L "$ROOT/backup" ] || fail 'Program backups already exist.'
    mkdir "$ROOT/backup"; : >"$ROOT/original.tsv"; : >"$ROOT/installed.tsv"
    while IFS='|' read -r expected size mode relative; do
        target=/mnt/us/$relative
        if [ -e "$target" ]; then
            regular "$target" || fail 'A destination changed before backup.'
            mkdir -p "$ROOT/backup/$(dirname "$relative")"
            cp "$target" "$ROOT/backup/$relative"
            original=$(sha "$target"); original_mode=$(stat -c %a "$target")
            [ "$(sha "$ROOT/backup/$relative")" = "$original" ] || fail 'A program backup differs.'
            printf 'present|%s|%s|%s\n' "$original" "$original_mode" "$relative" >>"$ROOT/original.tsv"
        else
            printf 'absent|-|-|%s\n' "$relative" >>"$ROOT/original.tsv"
        fi
    done <"$MANIFEST"
    record backups-complete true
    while IFS='|' read -r expected size mode relative; do
        target=/mnt/us/$relative
        safe_destination "$relative" || fail 'A destination changed before installation.'
        mkdir -p "$(dirname "$target")"
        pending=$target.$RUN.pending
        [ ! -e "$pending" ] && [ ! -L "$pending" ] || fail 'A pending program path already exists.'
        cp "$PAYLOAD_DIR/$relative" "$pending"; chmod "$mode" "$pending"
        [ "$(sha "$pending")" = "$expected" ] || fail 'A staged program copy differs.'
        mv "$pending" "$target"
        printf '%s|%s\n' "$expected" "$relative" >>"$ROOT/installed.tsv"
    done <"$MANIFEST"
    while IFS='|' read -r expected size mode relative; do
        [ "$(sha "/mnt/us/$relative")" = "$expected" ] || fail 'An installed program differs.'
    done <"$MANIFEST"
    [ -x "$HOME_DIR/app/aidoku" ] && [ -r "$HOME_DIR/launch.sh" ] || fail 'The installed runtime cannot be launched.'
    record payload-installed true
    INSTALLED=1
}

prepare_manual_data() {
    directory "$HOME_DIR" || fail 'The installed application root is unsafe.'
    if [ -e "$HOME_DIR/data" ] || [ -L "$HOME_DIR/data" ]; then directory "$HOME_DIR/data" || fail 'The data directory is unsafe.'; else mkdir "$HOME_DIR/data"; fi
    profile=$HOME_DIR/data/device-profile
    if [ -e "$profile" ] || [ -L "$profile" ]; then
        regular "$profile" || fail 'The existing device profile is unsafe.'
        value=$(sed -n '1{s/\r$//;p;}' "$profile")
        case "$value" in pw5|pw6|scribe|scribe2|scribe3|scribe3-nofrontlight) ;; *) fail 'The saved device profile is invalid and was preserved.' ;; esac
    else
        [ ! -e "$profile.$RUN.pending" ] && [ ! -L "$profile.$RUN.pending" ] || fail 'A pending profile path already exists.'
        printf '%s\n' scribe >"$profile.$RUN.pending"
        regular "$profile.$RUN.pending" && [ "$(cat "$profile.$RUN.pending")" = scribe ] || fail 'The new profile differs.'
        mv "$profile.$RUN.pending" "$profile"
        record profile-created true
    fi
    imports=$HOME_DIR/data/imports
    if [ -e "$imports" ] || [ -L "$imports" ]; then directory "$imports" || fail 'The imports directory is unsafe.'; else mkdir "$imports"; fi
    sample_sha=$(manifest_digest aidoku/samples/Aidoku-Sample.cbz || :)
    sample=$imports/Aidoku-Sample.cbz
    if [ -n "$sample_sha" ] && [ ! -e "$sample" ] && [ ! -L "$sample" ]; then
        [ "$(sha "$HOME_DIR/samples/Aidoku-Sample.cbz")" = "$sample_sha" ] || fail 'The public sample differs.'
        [ ! -e "$sample.$RUN.pending" ] && [ ! -L "$sample.$RUN.pending" ] || fail 'A pending sample path already exists.'
        cp "$HOME_DIR/samples/Aidoku-Sample.cbz" "$sample.$RUN.pending"
        [ "$(sha "$sample.$RUN.pending")" = "$sample_sha" ] || fail 'The new public sample differs.'
        mv "$sample.$RUN.pending" "$sample"
        record sample-import-created true
    fi
}

rollback_partial() {
    [ "$INSTALLED" -eq 0 ] && regular "$ROOT/backups-complete" && regular "$ROOT/original.tsv" || return 0
    no_app && no_lock || return 1
    while IFS='|' read -r presence original mode relative; do
        target=/mnt/us/$relative
        safe_destination "$relative" || return 1
        if [ "$presence" = present ]; then
            [ "$(sha "$ROOT/backup/$relative")" = "$original" ] || return 1
            cp "$ROOT/backup/$relative" "$target"; chmod "$mode" "$target"
            [ "$(sha "$target")" = "$original" ] || return 1
        elif [ -e "$target" ]; then
            match=0
            while IFS='|' read -r expected installed; do
                if [ "$installed" = "$relative" ] && [ "$(sha "$target")" = "$expected" ]; then match=1; fi
            done <<EOF
$(while IFS='|' read -r digest bytes permissions path; do printf '%s|%s\n' "$digest" "$path"; done <"$MANIFEST")
EOF
            [ "$match" -eq 1 ] || return 1
            rm "$target"
        fi
        pending=$target.$RUN.pending
        if regular "$pending"; then rm "$pending"; elif [ -e "$pending" ] || [ -L "$pending" ]; then return 1; fi
    done <"$ROOT/original.tsv"
    record partial-program-installation-rolled-back true
}
verify_payload() {
    directory "$PAYLOAD_DIR" && regular "$MANIFEST" || fail 'The package payload or allowlist is missing.'
    awk -F '|' 'NF != 4 || seen[$4]++ { exit 1 } END { if (NR == 0) exit 1 }' "$MANIFEST" || fail 'The program allowlist contains duplicate or malformed records.'
    total=0
    while IFS='|' read -r expected size mode relative; do
        hash "$expected" && number "$size" && safe_relative "$relative" || fail 'The program allowlist is invalid.'
        case "$mode" in 755|644) ;; *) fail 'A program mode is invalid.' ;; esac
        path=$PAYLOAD_DIR/$relative
        regular "$path" && [ "$(sha "$path")" = "$expected" ] && [ "$(stat -c %s "$path")" = "$size" ] || fail 'A package program file differs.'
        safe_destination "$relative" || fail 'An installation destination is unsafe.'
        total=$((total+1))
    done <"$MANIFEST"
    [ "$total" -gt 0 ] && [ "$(find "$PAYLOAD_DIR" -type f | wc -l | tr -d '[:space:]')" = "$total" ] || fail 'The package inventory differs.'
    [ -z "$(find "$PAYLOAD_DIR" -type l -print)" ] || fail 'The package payload contains a link.'
    bin_sha=$(manifest_digest aidoku/app/aidoku) && launch_sha=$(manifest_digest aidoku/launch.sh) || fail 'The package must name one production executable and launcher.'
    [ "$(sha "$PAYLOAD_DIR/aidoku/app/aidoku")" = "$bin_sha" ] && [ "$(sha "$PAYLOAD_DIR/aidoku/launch.sh")" = "$launch_sha" ] || fail 'The frozen production identity differs.'
}
finish() {
    code=$1; trap - 0; trap '' HUP INT TERM; set +e
    if [ "$code" -ne 0 ] || [ "$COMPLETE" -ne 1 ]; then
        rm -f "$PACKAGE_DIR/.aidoku-installation-complete" "$PACKAGE_DIR/.aidoku-installation-complete.pending"
        INSTALLED=0
        rollback_partial || code=72
        rm -f "$ROOT/payload-installed"
    fi
    printf 'aidoku-kpm-backup=%s\n' "$ROOT"
    exit "$code"
}
for tool in sha256sum stat cp mv find wc tr pidof chmod dirname sed awk; do command -v "$tool" >/dev/null 2>&1 || fail 'A required installation command is unavailable.'; done
no_app && no_lock || fail 'Exit Aidoku before installing this package.'
if [ -e "$HOME_DIR" ] || [ -L "$HOME_DIR" ]; then directory "$HOME_DIR" || fail 'The application root is unsafe.'; fi
if [ -e "$HOME_DIR/data" ] || [ -L "$HOME_DIR/data" ]; then directory "$HOME_DIR/data" || fail 'The application data directory is unsafe.'; fi
if [ -e "$HOME_DIR/data/device-profile" ] || [ -L "$HOME_DIR/data/device-profile" ]; then
    regular "$HOME_DIR/data/device-profile" || fail 'The existing profile is unsafe.'
    profile=$(sed -n '1{s/\r$//;p;}' "$HOME_DIR/data/device-profile")
    case "$profile" in pw5|pw6|scribe|scribe2|scribe3|scribe3-nofrontlight) ;; *) fail 'The existing profile is invalid and was preserved.' ;; esac
fi
verify_payload
[ ! -e "$PACKAGE_DIR/.aidoku-installation-complete" ] && [ ! -L "$PACKAGE_DIR/.aidoku-installation-complete" ] && [ ! -e "$PACKAGE_DIR/.aidoku-installation-complete.pending" ] && [ ! -L "$PACKAGE_DIR/.aidoku-installation-complete.pending" ] || fail 'An installation marker already exists.'
if [ -e /mnt/us/.aidoku-install-backups ] || [ -L /mnt/us/.aidoku-install-backups ]; then directory /mnt/us/.aidoku-install-backups || fail 'The backup parent is unsafe.'; else mkdir /mnt/us/.aidoku-install-backups; fi
[ ! -e "$ROOT" ] && [ ! -L "$ROOT" ] || fail 'The new backup path already exists.'
mkdir "$ROOT"
record run "$RUN"
trap 'finish "$?"' 0
trap 'exit 129' HUP; trap 'exit 130' INT; trap 'exit 143' TERM
backup_metadata
install_payload
prepare_manual_data
record installation-complete true
printf '%s\n' "$(sha "$MANIFEST")" >"$PACKAGE_DIR/.aidoku-installation-complete.pending"
mv "$PACKAGE_DIR/.aidoku-installation-complete.pending" "$PACKAGE_DIR/.aidoku-installation-complete"
COMPLETE=1
