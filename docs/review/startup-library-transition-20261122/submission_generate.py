import argparse
from pathlib import Path


TRANSITION_FUNCTIONS = r'''
parse_transition() {
    awk -v expected_stamp="$1" -v expected_sequence="$2" -v expected_marker="$3" -v initial="$4" -v main="$5" '
      function value(field, name, zero, parts, count, text) {
          count=split(field, parts, "="); text=parts[2]
          if (count!=2 || parts[1]!=name) {bad=1; return "none"}
          if (text=="none") return text
          if (length(text)>15 || (zero && text!~/^(0|[1-9][0-9]*)$/) ||
              (!zero && text!~/^[1-9][0-9]*$/)) {bad=1; return "none"}
          return text
      }
      function chain(list, names, count, position, previous, current) {
          count=split(list, names, " "); previous="none"
          for (position=1; position<=count; position++) {
              current=values[names[position]]
              if (current!="none") {
                  if (previous!="none" && previous+0>current+0) bad=1
                  previous=current
              }
          }
      }
      /^aidoku: startup_library_submitted/ {send_line=NR}
      /^aidoku: startup_stage_diag/ {stage_line=NR}
      /^aidoku: Launcher worker exited/ {exit_line=NR}
      /^aidoku: startup_transition/ {
          count++; transition_line=NR
          if (NF!=20 || $1!="aidoku:" || $2!="startup_transition") {bad=1; next}
          sequence=value($3,"sequence",0); marker=value($4,"marker",0)
          submitted=value($5,"submitted_us",0)
          if (("x" sequence)!=("x" expected_sequence) ||
              ("x" marker)!=("x" expected_marker) ||
              ("x" submitted)!=("x" expected_stamp)) bad=1
          values["F"]=value($6,"fence_us",0)
          values["MP"]=value($7,"metadata_ready_us",0)
          values["MR"]=value($8,"metadata_received_us",0)
          values["FP"]=value($9,"fonts_ready_us",0)
          values["FR"]=value($10,"fonts_received_us",0)
          values["FG"]=value($11,"fonts_registered_us",0)
          values["AB"]=value($12,"attach_enter_us",0)
          values["AE"]=value($13,"attach_exit_us",0)
          values["VB"]=value($14,"view_enter_us",0)
          values["VE"]=value($15,"view_exit_us",0)
          values["R"]=value($16,"scene_requested_us",0)
          values["RC"]=value($17,"render_us",1)
          values["SC"]=value($18,"settle_us",1)
          values["WC"]=value($19,"write_us",1)
          values["DC"]=value($20,"send_us",1)
      }
      END {
          if (bad || count>1) exit 2
          if (count==0) exit 3
          if (send_line>=transition_line || transition_line>=exit_line ||
              (stage_line && stage_line>=transition_line)) exit 2
          values["I"]=(initial=="unknown" ? "none" : initial)
          values["M"]=(main=="unknown" ? "none" : main)
          values["S"]=expected_stamp
          if (initial=="none" && values["F"]!="none") bad=1
          chain("M I F S")
          chain("M MP MR AB AE VB VE R S")
          chain("M FP FR FG AB AE VB VE R S")
          count=split("I F MP MR FP FR FG AB AE VB VE R", names, " ")
          for (position=1; position<=count; position++) {
              current=values[names[position]]
              if (current!="none" && (current+0>expected_stamp+0 ||
                  expected_stamp+0-current+0>999999999999)) bad=1
          }
          count=split("RC SC WC DC", costs, " "); missing=0
          for (position=1; position<=count; position++) {
              current=values[costs[position]]
              if (current=="none") missing++
              else if (current+0>999999999999) bad=1
          }
          if (missing!=0 && missing!=4) bad=1
          if (bad) exit 2
          printf "TRANSITION"
          count=split("I F MP MR FP FR FG AB AE VB VE R", names, " ")
          for (position=1; position<=count; position++) {
              current=values[names[position]]
              if (current=="none") printf " none"
              else printf " %.0f", current+0-expected_stamp+0
          }
          for (position=1; position<=4; position++) printf " %s", values[costs[position]]
          printf "\n"
      }
    ' "$evidence/delta.pending"
}
transition_output() (
    set -- $event; stamp=$1; sequence=$2; marker=$3
    if stage_values=$(parse_stages "$stamp"); then
        set -- $stage_values; main=$1; initial=$2
    else status=$?; [ "$status" = 3 ] || exit 1; main=unknown; initial=unknown; fi
    if transition=$(parse_transition "$stamp" "$sequence" "$marker" "$initial" "$main"); then :
    else status=$?; [ "$status" = 3 ] || exit 1; printf 'TRANSITION unavailable\n'; exit 0; fi
    if [ "$initial" = unknown ]; then printf 'TRANSITION unavailable\n'
    else printf '%s\n' "$transition"; fi
)
'''


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f"Expected exactly one collector anchor: {old!r}")
    return source.replace(old, new, 1)


def generate(source):
    source = replace_once(source, "\ncontext || fail owner\n",
                          "\n" + TRANSITION_FUNCTIONS + "\ncontext || fail owner\n")
    source = replace_once(source,
                          '        stages=$(stage_output) || fail stages\n'
                          '        delta_stable && program_same && put "$evidence/collected" done || fail collect\n',
                          '        stages=$(stage_output) || fail stages\n'
                          '        transition=$(transition_output) || fail transition\n'
                          '        delta_stable && program_same && put "$evidence/collected" done || fail collect\n')
    source = replace_once(source, '        printf \'%s\\n\' "$stages";;',
                          '        printf \'%s\\n\' "$stages"\n'
                          '        printf \'%s\\n\' "$transition";;')
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
