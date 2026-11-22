import argparse
from pathlib import Path


CLOCK_FUNCTIONS = r'''
valid_rate() {
    case "$1" in ''|0*|*[!0-9]*) return 1;; esac
    [ "${#1}" -le 7 ] && [ "$1" -le 1000000 ]
}
clock_rate() (
    rate=$(getconf CLK_TCK 2>/dev/null) || rate=
    if valid_rate "$rate"; then printf '%s\n' "$rate"; exit 0; fi
    case "$(uname -m)" in arm|armv[4-8]*) ;; *) exit 1;; esac
    od -An -N20 -t u1 /proc/self/exe | awk '
        {for (i=1; i<=NF; i++) bytes[++n]=$i}
        END {if (n!=20 || bytes[1]!=127 || bytes[2]!=69 || bytes[3]!=76 ||
                 bytes[4]!=70 || bytes[5]!=1 ||
                 !((bytes[6]==1 && bytes[19]==40 && bytes[20]==0) ||
                   (bytes[6]==2 && bytes[19]==0 && bytes[20]==40))) exit 1}' || exit 1
    if rate=$(od -An -v -t u4 /proc/self/auxv | awk '
        {for (i=1; i<=NF; i++) {
            if ($i !~ /^[0-9]+$/) bad=1
            if (++n%2) tag=$i
            else {
                if (ended && (tag!=0 || $i!=0)) bad=1
                if (tag==17) {found++; value=$i}
                if (tag==0) {if ($i!=0) bad=1; ended=1}
            }
        }}
        END {if (n==0) exit 1
             if (bad || n%2 || !ended || found>1) exit 2
             if (found!=1 || value+0<1 || value+0>1000000) exit 1
             printf "%.0f\n", value}'); then :
    else status=$?; exit "$status"; fi
    valid_rate "$rate" && printf '%s\n' "$rate"
)
tick_floor() {
    tick=$1
    printf '%s\n' "$((tick / rate * 1000000 + (tick % rate) * 1000000 / rate))"
}
tick_next_ceil() {
    tick=$(($1+1))
    printf '%s\n' "$((tick / rate * 1000000 + ((tick % rate) * 1000000 + rate - 1) / rate))"
}
'''

STAGE_FUNCTIONS = r'''
parse_stages() {
    awk -v expected="$1" '
      function positive(value) { return value ~ /^[1-9][0-9]*$/ && length(value)<=15 }
      /^aidoku: startup_library_submitted/ {send_line=NR}
      /^aidoku: Launcher worker exited/ {exit_line=NR}
      /^aidoku: startup_stage_diag/ {
          count++; diag_line=NR
          if (NF!=5 || $1!="aidoku:" || $2!="startup_stage_diag" ||
              $3!~/^main_us=[1-9][0-9]*$/ ||
              $4!~/^initial_us=(none|[1-9][0-9]*)$/ ||
              $5!~/^submitted_us=[1-9][0-9]*$/) {bad=1; next}
          main=substr($3,9); initial=substr($4,12); submitted=substr($5,14)
          if (!positive(main) || !positive(submitted) || ("x" submitted)!=("x" expected) ||
              main+0>submitted+0) bad=1
          if (initial!="none" && (!positive(initial) || main+0>initial+0 || initial+0>submitted+0)) bad=1
      }
      END {if (bad || count>1) exit 2
           if (count==0) exit 3
           if (send_line>=diag_line || diag_line>=exit_line) exit 2
           printf "%s %s\n", main, initial}
    ' "$evidence/delta.pending"
}
stage_output() (
    regular "$evidence/clock-rate" || exit 1
    prepared_rate=$(cat "$evidence/clock-rate") || exit 1
    line "$evidence/clock-rate" "$prepared_rate" || exit 1
    if [ "$prepared_rate" != unavailable ]; then valid_rate "$prepared_rate" || exit 1; fi
    if rate=$(clock_rate); then :
    else status=$?; [ "$status" = 1 ] || exit 1; rate=unavailable; fi
    if [ "$prepared_rate" != unavailable ] && [ "$rate" != unavailable ]; then
        [ "$prepared_rate" = "$rate" ] || exit 1
    fi
    IFS=' ' read -r gui gui_birth runner runner_birth worker worker_birth nonce extra <"$evidence/bound" || exit 1
    [ -z "${extra:-}" ] || exit 1
    for ticks in "$worker_birth" "$gui_birth"; do
        case "$ticks" in ''|0*|*[!0-9]*) exit 1;; esac
        [ "${#ticks}" -le 12 ] || exit 1
    done
    [ "$worker_birth" -le "$gui_birth" ] || exit 1
    set -- $event; stamp=$1
    times=$(brackets) || exit 1
    set -- $times; up_before=$1; up_after=$2
    if values=$(parse_stages "$stamp"); then
        set -- $values; main=$1; initial=$2
        [ "$main" -ge "$up_before" ] || exit 1
    else
        status=$?; [ "$status" = 3 ] || exit 1
        values=unavailable
    fi
    if [ "$values" = unavailable ] || [ "$prepared_rate" = unavailable ] || [ "$rate" = unavailable ]; then
        printf 'STAGES unavailable\n'; exit 0
    fi
    for ticks in "$worker_birth" "$gui_birth"; do
        [ "$((ticks / rate))" -le 999999999 ] || exit 1
    done
    worker_min=$(tick_floor "$worker_birth") && worker_max=$(tick_next_ceil "$worker_birth") &&
        gui_min=$(tick_floor "$gui_birth") && gui_max=$(tick_next_ceil "$gui_birth") || exit 1
    [ "$worker_min" -le "$gui_max" ] && [ "$gui_min" -le "$main" ] &&
        [ "$worker_max" -ge "$up_before" ] && [ "$stamp" -ge "$up_after" ] || exit 1
    if [ "$initial" = none ]; then initial_min=none; initial_max=none
    else initial_min=$((initial-up_after)); initial_max=$((initial-up_before)); fi
    printf 'STAGES %s %s %s %s %s %s %s %s %s %s %s\n' "$rate" \
        "$((worker_min-up_after))" "$((worker_max-up_before))" \
        "$((gui_min-up_after))" "$((gui_max-up_before))" \
        "$((main-up_after))" "$((main-up_before))" "$initial_min" "$initial_max" \
        "$((stamp-up_after))" "$((stamp-up_before))"
)
'''


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f"Expected exactly one collector anchor: {old!r}")
    return source.replace(old, new, 1)


def generate(source):
    source = replace_once(source, 'pid() { number "$1" && [ "$1" -gt 1 ]; }\n',
                          'pid() { number "$1" && [ "$1" -gt 1 ]; }\n' + CLOCK_FUNCTIONS)
    source = replace_once(source, '\ncontext || fail owner\n', '\n' + STAGE_FUNCTIONS + '\ncontext || fail owner\n')
    source = replace_once(source, "        printf 'submission_prepared\\n';;",
                          '        if rate=$(clock_rate); then :; else status=$?; [ "$status" = 1 ] || fail clock; rate=unavailable; fi\n'
                          '        put "$evidence/clock-rate" "$rate" || fail clock\n'
                          "        printf 'submission_prepared\\n';;")
    source = replace_once(source,
                          '        delta_stable && program_same && put "$evidence/collected" done || fail collect\n'
                          "        printf 'SUBMISSION %s %s %s %s large nonempty nonempty\\n' \"$lower\" \"$upper\" \"$sequence\" \"$marker\";;",
                          '        stages=$(stage_output) || fail stages\n'
                          '        delta_stable && program_same && put "$evidence/collected" done || fail collect\n'
                          "        printf 'SUBMISSION %s %s %s %s large nonempty nonempty\\n' \"$lower\" \"$upper\" \"$sequence\" \"$marker\"\n"
                          '        printf \'%s\\n\' "$stages";;')
    return source


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.source.resolve() == args.output.resolve():
        parser.error("The frozen source and generated output must be different files")
    result = generate(args.source.read_text(encoding="utf-8"))
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(result)
    args.output.chmod(0o755)


if __name__ == "__main__":
    main()
