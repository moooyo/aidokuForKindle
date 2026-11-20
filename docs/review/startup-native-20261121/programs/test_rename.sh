#!/bin/sh
set -eu
[ "$#" -gt 0 ] || exit 64
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
printf 'original\n' >"$scratch/source"
printf 'keep\n' >"$scratch/occupied"
if "$@" "$scratch/source" "$scratch/occupied"; then exit 1; else occupied=$?; fi
[ "$occupied" -eq 3 ]
printf 'original\n' | cmp -s - "$scratch/source"
printf 'keep\n' | cmp -s - "$scratch/occupied"
inode=$(stat -c '%d:%i' "$scratch/source")
"$@" "$scratch/source" "$scratch/destination"
[ ! -e "$scratch/source" ]
[ "$(stat -c '%d:%i' "$scratch/destination")" = "$inode" ]
mkdir "$scratch/directory" "$scratch/existing"
if "$@" "$scratch/directory" "$scratch/existing"; then exit 1; else occupied=$?; fi
[ "$occupied" -eq 3 ]
[ -d "$scratch/directory" ]
[ -d "$scratch/existing" ]
"$@" "$scratch/directory" "$scratch/moved"
[ ! -e "$scratch/directory" ]
[ -d "$scratch/moved" ]
ln -s "$scratch/missing" "$scratch/link"
if "$@" "$scratch/destination" "$scratch/link"; then exit 1; else occupied=$?; fi
[ "$occupied" -eq 3 ]
[ -L "$scratch/link" ]
[ -f "$scratch/destination" ]
