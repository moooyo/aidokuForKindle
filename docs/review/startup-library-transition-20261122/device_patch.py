#!/usr/bin/env python3
"""Derive a transition-recording operator from the closed stage operator."""

import argparse
from pathlib import Path


OLD_MEMBERS = 'for leaf in origin anchor pointer-brackets qualified submission stages; do'
NEW_MEMBERS = 'for leaf in origin anchor pointer-brackets qualified submission stages transition; do'

OLD_EXTRACT = r'''    result=$(sh "$stage/submission/submission.sh" collect "$label" "$case_id") || return 1
    [ "$(printf '%s\n' "$result" | wc -l)" -eq 2 ] || return 1
    primary=$(printf '%s\n' "$result" | sed -n '1p')
    stages=$(printf '%s\n' "$result" | sed -n '2p')'''

NEW_EXTRACT = r'''    result=$(sh "$stage/submission/submission.sh" collect "$label" "$case_id" && printf X) || return 1
    case "$result" in *'
X') result=${result%X};; *) return 1;; esac
    [ "$(printf '%s' "$result" | wc -l)" -eq 3 ] || return 1
    primary=$(printf '%s' "$result" | sed -n '1p')
    stages=$(printf '%s' "$result" | sed -n '2p')
    transition=$(printf '%s' "$result" | sed -n '3p')'''

OLD_SAVE = r'''    put "$output/$case_id/stages" "$stages" || return 1
    printf '%s\n' "$primary"'''

NEW_SAVE = r'''    if [ "$transition" != 'TRANSITION unavailable' ]; then
        set -- $transition
        [ "$#" = 17 ] && [ "$1" = TRANSITION ] || return 1
        shift
        canonical=TRANSITION
        for value in "$@"; do canonical="$canonical $value"; done
        [ "$transition" = "$canonical" ] || return 1
        transition_point() {
            case "$1" in none|0) return 0;; -[1-9]*) digits=${1#-};; *) return 1;; esac
            number "$digits" && [ "${#digits}" -le 12 ]
        }
        transition_cost() {
            case "$1" in 0) return 0;; [1-9]*) digits=$1;; *) return 1;; esac
            number "$digits" && [ "${#digits}" -le 12 ]
        }
        for value in "$1" "$2" "$3" "$4" "$5" "$6" "$7" "$8" "$9" "${10}" "${11}" "${12}"; do
            transition_point "$value" || return 1
        done
        shift 12
        if [ "$1" = none ] || [ "$2" = none ] || [ "$3" = none ] || [ "$4" = none ]; then
            [ "$1" = none ] && [ "$2" = none ] && [ "$3" = none ] && [ "$4" = none ] || return 1
        else
            for value in "$@"; do transition_cost "$value" || return 1; done
        fi
    fi
    put "$output/$case_id/stages" "$stages" || return 1
    put "$output/$case_id/transition" "$transition" || return 1
    printf '%s\n' "$primary"'''

REPLACEMENTS = ((OLD_MEMBERS, NEW_MEMBERS), (OLD_EXTRACT, NEW_EXTRACT), (OLD_SAVE, NEW_SAVE))


def generate(source):
    for old, new in REPLACEMENTS:
        if source.count(old) != 1:
            raise ValueError('unexpected_device_source')
        source = source.replace(old, new, 1)
    return source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.source.resolve() == args.output.resolve():
        parser.error('The source and generated operator must differ')
    result = generate(args.source.read_text(encoding='ascii'))
    with args.output.open('x', encoding='ascii', newline='\n') as stream:
        stream.write(result)
    args.output.chmod(0o700)


if __name__ == '__main__':
    main()
