#!/usr/bin/env python3
"""Derive a stage-recording operator from the closed stock-card operator."""

import argparse
from pathlib import Path


OLD_MEMBERS = 'for leaf in origin anchor pointer-brackets qualified submission; do'
NEW_MEMBERS = 'for leaf in origin anchor pointer-brackets qualified submission stages; do'

OLD_CAPTURE = r'''capture_submission() {
    case_id=$1
    result=$(sh "$stage/submission/submission.sh" collect "$label" "$case_id") || return 1
    set -- $result
    [ "$#" = 8 ] && [ "$1" = SUBMISSION ] && [ "$6" = large ] &&
        [ "$7" = nonempty ] && [ "$8" = nonempty ] || return 1
    for value in "$2" "$3" "$4" "$5"; do number "$value" && [ "${#value}" -le 20 ] || return 1; done
    [ "$4" != 0 ] && [ "$5" != 0 ] || return 1
    printf 'SUBMISSION %s %s %s %s large nonempty nonempty\n' "$2" "$3" "$4" "$5"
}'''

NEW_CAPTURE = r'''capture_submission() {
    case_id=$1
    result=$(sh "$stage/submission/submission.sh" collect "$label" "$case_id") || return 1
    [ "$(printf '%s\n' "$result" | wc -l)" -eq 2 ] || return 1
    primary=$(printf '%s\n' "$result" | sed -n '1p')
    stages=$(printf '%s\n' "$result" | sed -n '2p')
    set -- $primary
    [ "$#" = 8 ] && [ "$1" = SUBMISSION ] && [ "$6" = large ] &&
        [ "$7" = nonempty ] && [ "$8" = nonempty ] || return 1
    for value in "$2" "$3" "$4" "$5"; do number "$value" && [ "${#value}" -le 20 ] || return 1; done
    [ "$4" != 0 ] && [ "$5" != 0 ] || return 1
    primary=$(printf 'SUBMISSION %s %s %s %s large nonempty nonempty' "$2" "$3" "$4" "$5")
    set -- $stages
    if [ "$#" = 2 ] && [ "$1" = STAGES ] && [ "$2" = unavailable ]; then
        stages='STAGES unavailable'
    else
        [ "$#" = 12 ] && [ "$1" = STAGES ] || return 1
        rate=$2
        number "$rate" && [ "${#rate}" -le 7 ] && [ "$rate" -gt 0 ] && [ "$rate" -le 1000000 ] || return 1
        shift 2
        stage_integer() {
            case "$1" in -*) digits=${1#-};; *) digits=$1;; esac
            number "$digits" && [ "${#digits}" -le 20 ]
        }
        for value in "$1" "$2" "$3" "$4" "$5" "$6" "$9" "${10}"; do
            stage_integer "$value" || return 1
        done
        if [ "$7" = none ] || [ "$8" = none ]; then
            [ "$7" = none ] && [ "$8" = none ] || return 1
        else
            stage_integer "$7" && stage_integer "$8" || return 1
        fi
        stages=$(printf 'STAGES %s %s %s %s %s %s %s %s %s %s %s' "$rate" \
            "$1" "$2" "$3" "$4" "$5" "$6" "$7" "$8" "$9" "${10}")
    fi
    put "$output/$case_id/stages" "$stages" || return 1
    printf '%s\n' "$primary"
}'''


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError('unexpected_device_source')
    return source.replace(old, new, 1)


def generate(source):
    source = replace_once(source, OLD_MEMBERS, NEW_MEMBERS)
    return replace_once(source, OLD_CAPTURE, NEW_CAPTURE)


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
