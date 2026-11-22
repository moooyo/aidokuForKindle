#!/bin/sh
set -eu
umask 077

here=$(dirname "$(readlink -f "$0")")
owner=$(cat "$here/owner")
case "${1:---run}:$#" in
    --run:0|--run:1) action=--run ;;
    --preflight:1) action=--preflight ;;
    --status:1) action=--status ;;
    --analyze:1) action=--analyze ;;
    --recover:1) action=--recover ;;
    *) printf 'Invalid operation.\n' >&2; exit 2 ;;
esac
exec python3 -B "$here/host.py" "$action" "$here" --owner "$owner"
