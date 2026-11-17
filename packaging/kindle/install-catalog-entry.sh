#!/bin/sh
# Install immutable runtime versions; retain the original shared executable.
set -eu
umask 077
CATALOG_HOME=$(CDPATH= cd "$(dirname "$0")" && pwd)
CATALOG_PAYLOAD=$CATALOG_HOME/native-catalog
CATALOG_ROOT=/var/local/kindle-catalog-entry
CATALOG_REGISTRY=/var/local/appreg.db
CATALOG_SERVICE=tech.hackerdude.shell_integration.launcher
CATALOG_LEGACY=/var/local/kmc/bin/sh_integration_launcher
CATALOG_LEGACY_SHA=2b3db59ed7856b3f5026e3837f7ef07fc9bb6bc1c042274b48b3cd3fc2e04abc
CATALOG_SOURCE=/usr/lib/ccat/change.lua
CATALOG_SOURCE_SHA=971541da47ab5c3cafbde9ab80662d1b0055183c57aed9baf942a3360dbff5b4
CATALOG_PROTOCOL=aidoku-catalog-entry-v1
skip() { printf 'aidoku-catalog: %s\n' "$*"; exit 0; }
fail() { printf 'aidoku-catalog: %s\n' "$*" >&2; exit 1; }
regular() { [ -f "$1" ] && [ ! -L "$1" ]; }
digest() { regular "$1" || return 1; catalog_line=$(sha256sum "$1") || return 1; printf '%s\n' "${catalog_line%% *}"; }
hex() { [ "${#1}" -eq 64 ] && case "$1" in *[!0-9a-f]*) false ;; *) true ;; esac; }
managed() {
    catalog_parent=${1%/*}; catalog_component=${catalog_parent##*/}
    [ "${catalog_parent%/*}" = "$CATALOG_ROOT" ] && [ "${1##*/}" = aidoku-library-dispatch ] || return 1
    case "$catalog_component" in v1-*) ;; *) return 1 ;; esac
    hex "${catalog_component#v1-}" || return 1
    [ ! -L "$CATALOG_ROOT" ] && [ ! -L "$catalog_parent" ] && regular "$catalog_parent/protocol" || return 1
    [ "$(cat "$catalog_parent/protocol")" = "$CATALOG_PROTOCOL" ]
}
[ "$(id -u)" = 0 ] || fail 'Root is required to register the shared runtime.'
for tool in sqlite3 sha256sum cp mv chmod mkdir stat; do command -v "$tool" >/dev/null 2>&1 || skip 'A required registration tool is unavailable; original startup is retained.'; done
regular "$CATALOG_REGISTRY" || skip 'The native registry is unavailable; original startup is retained.'
catalog_current=$(sqlite3 -readonly -cmd '.timeout 1500' "$CATALOG_REGISTRY" "SELECT value FROM properties WHERE handlerId='$CATALOG_SERVICE' AND name='command';") || skip 'The registry is busy; original startup is retained.'
if [ "${1:-}" = --disable ]; then
    managed "$catalog_current" || skip 'No owned catalog registration is active.'
    sqlite3 -cmd '.timeout 1500' "$CATALOG_REGISTRY" "UPDATE properties SET value='$CATALOG_LEGACY' WHERE handlerId='$CATALOG_SERVICE' AND name='command' AND value='$catalog_current';"
    skip 'Original Library dispatch restored. Immutable runtime files were retained for active sessions.'
fi
[ "$#" -eq 0 ] || fail 'Unsupported installation argument.'
if [ "$catalog_current" != "$CATALOG_LEGACY" ]; then
    managed "$catalog_current" || skip 'An unrelated handler registration is active; it was preserved.'
fi
[ "$(digest "$CATALOG_LEGACY" || :)" = "$CATALOG_LEGACY_SHA" ] &&
    [ "$(digest "$CATALOG_SOURCE" || :)" = "$CATALOG_SOURCE_SHA" ] ||
    skip 'This handler or catalog version is not qualified; original behavior is retained.'
regular "$CATALOG_PAYLOAD/SHA256SUMS" || fail 'The catalog runtime manifest is missing.'
catalog_seen=''
while read -r expected name extra; do
    hex "$expected" && [ -z "$extra" ] || fail 'The runtime manifest is malformed.'
    case "$name" in aidoku-library-dispatch|aidoku-entry-hooks.so|aidoku-catalog-client) ;; *) fail 'Unexpected runtime file.' ;; esac
    case " $catalog_seen " in *" $name "*) fail 'A runtime file is duplicated.' ;; esac
    catalog_seen="$catalog_seen $name"
    [ "$(digest "$CATALOG_PAYLOAD/$name")" = "$expected" ] || fail 'A runtime checksum differs.'
done <"$CATALOG_PAYLOAD/SHA256SUMS"
set -- $catalog_seen
[ "$#" -eq 3 ] || fail 'The catalog runtime is incomplete.'
[ "$("$CATALOG_PAYLOAD/aidoku-library-dispatch" --version 2>/dev/null || :)" = 'aidoku-catalog-entry 1' ] || fail 'The runtime cannot execute on this firmware.'
catalog_version=$(digest "$CATALOG_PAYLOAD/aidoku-library-dispatch")
hex "$catalog_version" || fail 'Invalid runtime version.'
catalog_destination=$CATALOG_ROOT/v1-$catalog_version
if [ -e "$CATALOG_ROOT" ] || [ -L "$CATALOG_ROOT" ]; then
    [ -d "$CATALOG_ROOT" ] && [ ! -L "$CATALOG_ROOT" ] && [ "$(stat -c '%u:%a' "$CATALOG_ROOT")" = 0:755 ] || fail 'The shared runtime root is unsafe.'
else
    mkdir -m 755 "$CATALOG_ROOT"
fi
if [ -e "$catalog_destination" ] || [ -L "$catalog_destination" ]; then
    [ -d "$catalog_destination" ] && [ ! -L "$catalog_destination" ] && [ "$(stat -c '%u:%a' "$catalog_destination")" = 0:755 ] || fail 'The immutable runtime directory is unsafe.'
else
    mkdir -m 755 "$catalog_destination"
fi
for name in aidoku-library-dispatch aidoku-entry-hooks.so aidoku-catalog-client; do
    target=$catalog_destination/$name
    expected=$(digest "$CATALOG_PAYLOAD/$name")
    if [ -e "$target" ] || [ -L "$target" ]; then
        [ "$(digest "$target" || :)" = "$expected" ] || fail 'An existing immutable runtime differs.'
    else
        pending=$target.pending.$$
        [ ! -e "$pending" ] && [ ! -L "$pending" ] || fail 'A staged runtime file already exists.'
        cp "$CATALOG_PAYLOAD/$name" "$pending"
        if [ "$name" = aidoku-entry-hooks.so ]; then chmod 644 "$pending"; else chmod 755 "$pending"; fi
        [ "$(digest "$pending")" = "$expected" ] || fail 'A staged runtime checksum differs.'
        mv "$pending" "$target"
    fi
done
if [ -e "$catalog_destination/protocol" ] || [ -L "$catalog_destination/protocol" ]; then
    regular "$catalog_destination/protocol" && [ "$(cat "$catalog_destination/protocol")" = "$CATALOG_PROTOCOL" ] || fail 'An existing runtime protocol differs.'
else
    printf '%s\n' "$CATALOG_PROTOCOL" >"$catalog_destination/protocol.pending.$$"
    chmod 644 "$catalog_destination/protocol.pending.$$"
    mv "$catalog_destination/protocol.pending.$$" "$catalog_destination/protocol"
fi
catalog_command=$catalog_destination/aidoku-library-dispatch
if [ "$catalog_current" != "$catalog_command" ]; then
    catalog_changed=$(sqlite3 -cmd '.timeout 1500' "$CATALOG_REGISTRY" "UPDATE properties SET value='$catalog_command' WHERE handlerId='$CATALOG_SERVICE' AND name='command' AND value='$catalog_current'; SELECT changes();") || fail 'The registration transaction failed.'
    [ "$catalog_changed" = 1 ] || fail 'The handler changed concurrently; the new runtime was not selected.'
fi
printf '%s\n' 'aidoku-catalog: Deferred catalog notifications are enabled for the qualified Aidoku entry.'
