#!/usr/bin/env python3
"""Derive draw-accounting scripts from the closed Library-transition scripts."""

import argparse
from pathlib import Path


DEVICE_MEMBERS = 'for leaf in origin anchor pointer-brackets qualified submission stages transition; do'
DEVICE_MEMBERS_NEW = 'for leaf in origin anchor pointer-brackets qualified submission stages transition drawaccount; do'

DEVICE_EXTRACT = r'''    [ "$(printf '%s' "$result" | wc -l)" -eq 3 ] || return 1
    primary=$(printf '%s' "$result" | sed -n '1p')
    stages=$(printf '%s' "$result" | sed -n '2p')
    transition=$(printf '%s' "$result" | sed -n '3p')'''
DEVICE_EXTRACT_NEW = r'''    [ "$(printf '%s' "$result" | wc -l)" -eq 4 ] || return 1
    primary=$(printf '%s' "$result" | sed -n '1p')
    stages=$(printf '%s' "$result" | sed -n '2p')
    transition=$(printf '%s' "$result" | sed -n '3p')
    drawaccount=$(printf '%s' "$result" | sed -n '4p')'''

DEVICE_SAVE = r'''    put "$output/$case_id/stages" "$stages" || return 1
    put "$output/$case_id/transition" "$transition" || return 1
    printf '%s\n' "$primary"'''
DEVICE_SAVE_NEW = r'''    if [ "$drawaccount" != 'DRAWACCOUNT unavailable' ]; then
        [ "$transition" != 'TRANSITION unavailable' ] || return 1
        set -- $transition
        [ "$#" = 17 ] && [ "$1" = TRANSITION ] || return 1
        render_wall=${14}
        [ "$render_wall" != none ] || return 1
        set -- $drawaccount
        [ "$#" = 10 ] && [ "$1" = DRAWACCOUNT ] || return 1
        shift
        canonical=DRAWACCOUNT
        for value in "$@"; do canonical="$canonical $value"; done
        [ "$drawaccount" = "$canonical" ] || return 1
        draw_unsigned() {
            case "$1" in 0) return 0;; [1-9]*) digits=$1;; *) return 1;; esac
            number "$digits" && [ "${#digits}" -le 12 ]
        }
        draw_unsigned "$1" && [ "$1" = "$render_wall" ] || return 1
        [ "$2" = none ] || draw_unsigned "$2" || return 1
        if [ "$3" = none ] || [ "$4" = none ] || [ "$5" = none ] || [ "$6" = none ]; then
            [ "$3" = none ] && [ "$4" = none ] && [ "$5" = none ] && [ "$6" = none ] || return 1
        else
            for value in "$3" "$4" "$5" "$6"; do draw_unsigned "$value" || return 1; done
        fi
        for value in "$7" "$8"; do
            draw_unsigned "$value" && [ "${#value}" -le 4 ] && [ "$value" -gt 0 ] && [ "$value" -le 8192 ] || return 1
        done
        case "$9" in gray8|rgb8) ;; *) return 1;; esac
    fi
    put "$output/$case_id/stages" "$stages" || return 1
    put "$output/$case_id/transition" "$transition" || return 1
    put "$output/$case_id/drawaccount" "$drawaccount" || return 1
    printf '%s\n' "$primary"'''

DRAW_FUNCTIONS = r'''
parse_drawaccount() {
    awk -v expected_stamp="$1" -v expected_sequence="$2" -v expected_marker="$3" -v expected_wall="$4" '
      function value(field, name, optional, positive, limit, parts, count, text) {
          count=split(field, parts, "="); text=parts[2]
          if (count!=2 || parts[1]!=name) {bad=1; return ""}
          if (text=="none" && optional) return text
          if (text!~/^(0|[1-9][0-9]*)$/ || length(text)>limit || (positive && text=="0")) bad=1
          return text
      }
      /^aidoku: startup_library_submitted/ {send_line=NR}
      /^aidoku: startup_transition/ {transition_line=NR}
      /^aidoku: Launcher worker exited/ {exit_line=NR}
      /^aidoku: startup_draw_cpu/ {
          count++; draw_line=NR
          if (NF!=14 || $1!="aidoku:" || $2!="startup_draw_cpu") {bad=1; next}
          sequence=value($3,"sequence",0,1,15); marker=value($4,"marker",0,1,15)
          submitted=value($5,"submitted_us",0,1,15); wall=value($6,"wall_us",0,0,12)
          cpu=value($7,"thread_us",1,0,12); minor=value($8,"minflt",1,0,12)
          major=value($9,"majflt",1,0,12); voluntary=value($10,"nvcsw",1,0,12)
          involuntary=value($11,"nivcsw",1,0,12)
          width=value($12,"width",0,1,4); height=value($13,"height",0,1,4)
          if ($14=="format=gray8") format="gray8"
          else if ($14=="format=rgb8") format="rgb8"
          else bad=1
          if (("x" sequence)!=("x" expected_sequence) || ("x" marker)!=("x" expected_marker) ||
              ("x" submitted)!=("x" expected_stamp) || width+0>8192 || height+0>8192) bad=1
          missing=(minor=="none")+(major=="none")+(voluntary=="none")+(involuntary=="none")
          if (missing!=0 && missing!=4) bad=1
          if ($0!="aidoku: startup_draw_cpu sequence=" sequence " marker=" marker " submitted_us=" submitted \
                  " wall_us=" wall " thread_us=" cpu " minflt=" minor " majflt=" major \
                  " nvcsw=" voluntary " nivcsw=" involuntary " width=" width " height=" height " format=" format) bad=1
      }
      END {
          if (bad || count>1) exit 2
          if (count==0) exit 3
          if (!send_line || !exit_line || send_line>=draw_line || draw_line>=exit_line ||
              (transition_line && transition_line>=draw_line) || expected_wall=="none" ||
              (expected_wall!="absent" && ("x" wall)!=("x" expected_wall))) exit 2
          printf "DRAWACCOUNT %s %s %s %s %s %s %s %s %s\n", \
              wall, cpu, minor, major, voluntary, involuntary, width, height, format
      }
    ' "$evidence/delta.pending"
}
drawaccount_output() (
    set -- $event; stamp=$1; sequence=$2; marker=$3
    if [ "$transition" = 'TRANSITION unavailable' ]; then
        if stage_values=$(parse_stages "$stamp"); then
            set -- $stage_values; main=$1; initial=$2
        else status=$?; [ "$status" = 3 ] || exit 1; main=unknown; initial=unknown; fi
        if original=$(parse_transition "$stamp" "$sequence" "$marker" "$initial" "$main"); then
            set -- $original; [ "$#" = 17 ] && [ "$1" = TRANSITION ] || exit 1; render=${14}
        else status=$?; [ "$status" = 3 ] || exit 1; render=absent; fi
    else
        set -- $transition; [ "$#" = 17 ] && [ "$1" = TRANSITION ] || exit 1; render=${14}
    fi
    if result=$(parse_drawaccount "$stamp" "$sequence" "$marker" "$render"); then
        if [ "$transition" = 'TRANSITION unavailable' ] || [ "$render" = absent ]; then
            printf 'DRAWACCOUNT unavailable\n'
        else printf '%s\n' "$result"; fi
    else status=$?; [ "$status" = 3 ] || exit 1; printf 'DRAWACCOUNT unavailable\n'; fi
)
'''

COLLECTOR_COMPUTE = r'''        transition=$(transition_output) || fail transition
        delta_stable && program_same && put "$evidence/collected" done || fail collect'''
COLLECTOR_COMPUTE_NEW = r'''        transition=$(transition_output) || fail transition
        drawaccount=$(drawaccount_output) || fail drawaccount
        delta_stable && program_same && put "$evidence/collected" done || fail collect'''
COLLECTOR_OUTPUT = r'''        printf '%s\n' "$transition";;'''
COLLECTOR_OUTPUT_NEW = r'''        printf '%s\n' "$transition"
        printf '%s\n' "$drawaccount";;'''

DEVICE_REPLACEMENTS = ((DEVICE_MEMBERS, DEVICE_MEMBERS_NEW), (DEVICE_EXTRACT, DEVICE_EXTRACT_NEW),
                       (DEVICE_SAVE, DEVICE_SAVE_NEW))
COLLECTOR_REPLACEMENTS = (('\ncontext || fail owner\n', '\n' + DRAW_FUNCTIONS + '\ncontext || fail owner\n'),
                          (COLLECTOR_COMPUTE, COLLECTOR_COMPUTE_NEW),
                          (COLLECTOR_OUTPUT, COLLECTOR_OUTPUT_NEW))


def apply_replacements(source, replacements):
    for old, new in replacements:
        if source.count(old) != 1:
            raise ValueError('unexpected_frozen_source')
        source = source.replace(old, new, 1)
    return source


def generate_device(source):
    return apply_replacements(source, DEVICE_REPLACEMENTS)


def generate_submission(source):
    return apply_replacements(source, COLLECTOR_REPLACEMENTS)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('old_device', type=Path)
    parser.add_argument('old_submission', type=Path)
    parser.add_argument('new_device', type=Path)
    parser.add_argument('new_submission', type=Path)
    args = parser.parse_args()
    paths = (args.old_device, args.old_submission, args.new_device, args.new_submission)
    if len({path.resolve() for path in paths}) != len(paths):
        parser.error('Frozen inputs and generated outputs must all differ')
    device = generate_device(args.old_device.read_text(encoding='ascii'))
    submission = generate_submission(args.old_submission.read_text(encoding='ascii'))
    if args.new_device.exists() or args.new_submission.exists():
        parser.error('Generated outputs must not already exist')
    created = []
    try:
        with args.new_device.open('x', encoding='ascii', newline='\n') as stream:
            created.append(args.new_device)
            stream.write(device)
        with args.new_submission.open('x', encoding='ascii', newline='\n') as stream:
            created.append(args.new_submission)
            stream.write(submission)
        args.new_device.chmod(0o700)
        args.new_submission.chmod(0o755)
    except BaseException:
        for path in reversed(created):
            path.unlink()
        raise


if __name__ == '__main__':
    main()
