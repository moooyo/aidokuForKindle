#!/bin/sh
set -efu
export LC_ALL=C
umask 077
exec 4>&2 2>/dev/null

fail() { printf 'refused submission_%s\n' "$1" >&4; exit 1; }
[ "$#" = 3 ] || fail arguments
operation=$1; label=$2; name=$3
case "$operation" in prepare|bind|collect) ;; *) fail operation;; esac
case "$label" in ''|*[!0-9a-f]*) fail label;; esac
[ "${#label}" = 16 ] || fail label
case "$name" in
    control-warmup|control-0[1-6]) arm=control;;
    candidate-warmup|candidate-0[1-6]) arm=candidate;;
    *) fail case;;
esac

media=/mnt/us
private=/var/tmp
proc=/proc
stage=$media/.aidoku-native-$label
state=$private/aidoku-native-$label
visit=$state/visit-$name
out=$state/out/$name
evidence=$state/submission-$name
journal=$state/selector-journal
home=$media/aidoku
app=$home/app/aidoku
selected=$stage/programs/selected-app
log=$home/data/logs/launcher.log
boot=$proc/sys/kernel/random/boot_id

absent() { [ ! -e "$1" ] && [ ! -L "$1" ]; }
regular() { [ -f "$1" ] && [ ! -L "$1" ]; }
directory() { [ -d "$1" ] && [ ! -L "$1" ] && [ "$(readlink -f "$1")" = "$1" ]; }
line() { regular "$1" && printf '%s\n' "$2" | cmp -s - "$1"; }
put() { (set -C; printf '%s\n' "$2" >"$1"); }
number() { case "$1" in ''|*[!0-9]*) return 1;; esac; }
pid() { number "$1" && [ "$1" -gt 1 ]; }
file_id() { regular "$1" && stat -c '%d:%i:%u:%g:%a:%s:%y:%z' "$1"; }
log_id() { regular "$1" && stat -c '%d:%i' "$1"; }
fd_id() { stat -Lc '%d:%i' "$1"; }
same_boot() { regular "$visit/boot" && regular "$boot" && cmp -s "$visit/boot" "$boot"; }
context() {
    directory "$stage" && directory "$state" && directory "$out" && directory "$visit" &&
        [ "$(stat -c '%u:%a' "$state")" = 0:700 ] &&
        line "$stage/owner" "$label" && line "$state/owner" "$label" &&
        line "$visit/owner" "$label $name" && same_boot
}
current() {
    directory "$state/visit-live" && line "$state/visit-live/case" "$label $name" &&
        line "$state/current" "$name" && line "$state/ready" "$label" &&
        line "$state/guard/ready" "$label" && line "$visit/request" stock-tap &&
        line "$out/origin" stock-tap
}
proof() {
    directory "$evidence" && [ "$(stat -c '%u:%a' "$evidence")" = 0:700 ] &&
        line "$evidence/owner" "$label $name $arm" &&
        regular "$evidence/boot" && cmp -s "$evidence/boot" "$boot" &&
        regular "$evidence/log-mark" && cmp -s "$evidence/log-mark" "$visit/log-mark" &&
        regular "$evidence/program" && regular "$evidence/log-tail"
}
mount_id() {
    awk -v target="$app" -v src="$selected" '
      $5==target {tr++; line_id=$1; parent=$2; device=$3; root=$4; next}
      $5==src {source_is_mount=1; next}
      { if ($5=="/" || index(target,$5 "/")==1) {
            n=length($5); if(n>tl){ti=$1;tl=n;tc=1}else if(n==tl)tc++ }
        if ($5=="/" || index(src,$5 "/")==1) {
            n=length($5); if(n>sl){sr=$4;sm=$5;sd=$3;sl=n;sc=1}else if(n==sl)sc++ } }
      END {if(!tl || !sl || tc!=1 || sc!=1 || source_is_mount || tr!=1) exit 1
           if(sm=="/")suffix=src;else suffix=substr(src,length(sm)+1)
           if(sr=="/")sr=""; expected=sr suffix
           if(line_id!~/^[1-9][0-9]*$/ || parent!=ti || device!=sd || root!=expected) exit 1
           print line_id}' "$1"
}
selector_same() {
    directory "$journal" && line "$journal/owner" "$label" && line "$journal/sealed" "$label" &&
        line "$journal/case" "$name" && line "$journal/aidoku.mount-intent" "$label" &&
        regular "$journal/aidoku.mount-id" && regular "$journal/aidoku.source" &&
        cmp -s "$journal/aidoku.source" "$evidence/program" &&
        mount=$(cat "$journal/aidoku.mount-id") && number "$mount" && [ "$mount" -gt 0 ] &&
        line "$journal/aidoku.mount-id" "$mount" && [ "$(mount_id "$proc/self/mountinfo")" = "$mount" ]
}
program_same() {
    directory "$stage/programs" && regular "$selected" && [ -x "$selected" ] &&
        regular "$app" && [ -x "$app" ] &&
        [ "$(file_id "$selected")" = "$(cat "$evidence/program")" ] &&
        [ "$(file_id "$app")" = "$(cat "$evidence/program")" ] && selector_same
}
proc_row() (
    who=$1; pid "$who" && IFS= read -r raw <"$proc/$who/stat" || exit 1
    case "$raw" in "$who ("*') '*) ;; *) exit 1;; esac
    set -- ${raw##*) }
    [ "$#" -ge 20 ] || exit 1
    status=$1; parent=$2; shift 19
    case "$status" in Z|X|x) exit 1;; esac
    number "$parent" && number "$1" || exit 1
    printf '%s %s\n' "$parent" "$1"
)
argv() { subject=$1; shift; printf '%s\0' "$@" | cmp -s - "$proc/$subject/cmdline"; }
active_identity() (
    proof && program_same && current && line "$visit/ready" observed &&
        line "$out/dispatched" stock-tap && regular "$visit/bound" || exit 1
    IFS=' ' read -r gui gui_birth runner runner_birth worker worker_birth nonce extra <"$visit/bound" || exit 1
    [ -z "${extra:-}" ] && pid "$gui" && pid "$runner" && pid "$worker" &&
        number "$gui_birth" && number "$runner_birth" && number "$worker_birth" || exit 1
    case "$nonce" in ''|*[!0-9a-f]*) exit 1;; esac
    [ "${#nonce}" = 32 ] || exit 1
    [ "$(proc_row "$gui")" = "$runner $gui_birth" ] &&
        [ "$(proc_row "$runner")" = "$worker $runner_birth" ] || exit 1
    worker_row=$(proc_row "$worker") || exit 1
    set -- $worker_row; [ "$#" = 2 ] && [ "$2" = "$worker_birth" ] || exit 1
    [ "$(readlink "$proc/$gui/exe")" = "$app" ] &&
        [ "$(fd_id "$proc/$gui/exe")" = "$(log_id "$app")" ] &&
        [ "$(mount_id "$proc/$gui/mountinfo")" = "$(cat "$journal/aidoku.mount-id")" ] &&
        [ "$(readlink "$proc/$worker/exe")" = "$(readlink -f /bin/sh)" ] || exit 1
    argv "$worker" sh "$home/launch.sh" --foreground || exit 1
    argv "$gui" "$app" --kindle --device scribe --data-dir "$home/data" || exit 1
    IFS=' ' read -r inode offset <"$evidence/log-mark" || exit 1
    number "$offset" && [ "$(fd_id "$proc/$gui/fd/2")" = "$inode" ] &&
        [ "$(fd_id "$proc/$runner/fd/2")" = "$inode" ] &&
        [ "$(fd_id "$proc/$worker/fd/2")" = "$inode" ] || exit 1
    printf '%s %s %s %s %s %s %s\n' "$gui" "$gui_birth" "$runner" "$runner_birth" "$worker" "$worker_birth" "$nonce"
)
brackets() (
    line "$out/dispatched" stock-tap && regular "$out/anchor" && regular "$out/pointer-brackets" &&
        cmp -s "$out/anchor" "$out/pointer-brackets" || exit 1
    IFS=' ' read -r down_before down_after up_before up_after extra <"$out/anchor" || exit 1
    [ -z "${extra:-}" ] || exit 1
    for value in "$down_before" "$down_after" "$up_before" "$up_after"; do
        number "$value" && [ "${#value}" -le 15 ] && [ "$value" -gt 0 ] || exit 1
    done
    [ "$down_before" -le "$down_after" ] && [ "$down_after" -lt "$up_before" ] &&
        [ "$up_before" -le "$up_after" ] || exit 1
    line "$out/anchor" "$down_before $down_after $up_before $up_after" || exit 1
    printf '%s %s\n' "$up_before" "$up_after"
)
snapshot_delta() (
    proof && same_boot && regular "$log" || exit 1
    IFS=' ' read -r inode offset extra <"$evidence/log-mark" || exit 1
    [ -z "${extra:-}" ] && number "$offset" && [ "$offset" -le 2000000000 ] || exit 1
    [ "$(log_id "$log")" = "$inode" ] || exit 1
    exec 3<"$log"
    [ "$(fd_id /proc/self/fd/3)" = "$inode" ] || exit 1
    before=$(stat -Lc '%d:%i:%s:%y:%z' /proc/self/fd/3) || exit 1
    size=$(stat -Lc '%s' /proc/self/fd/3) || exit 1
    number "$size" && [ "$size" -ge "$offset" ] && [ "$((size-offset))" -le 1048576 ] || exit 1
    tail_size=$(stat -c '%s' "$evidence/log-tail") || exit 1
    number "$tail_size" && [ "$tail_size" -le 64 ] && [ "$tail_size" -le "$offset" ] || exit 1
    if [ "$tail_size" -gt 0 ]; then
        dd bs=1 skip="$((offset-tail_size))" count="$tail_size" <&3 2>/dev/null | cmp -s - "$evidence/log-tail" || exit 1
    fi
    exec 3<"$log"
    [ "$(fd_id /proc/self/fd/3)" = "$inode" ] || exit 1
    tail -c "+$((offset+1))" <&3 >"$evidence/delta.pending" || exit 1
    [ "$(stat -c '%s' "$evidence/delta.pending")" = "$((size-offset))" ] &&
        [ "$(stat -Lc '%d:%i:%s:%y:%z' /proc/self/fd/3)" = "$before" ] &&
        [ "$(stat -c '%d:%i:%s:%y:%z' "$log")" = "$before" ] && same_boot &&
        printf '%s\n' "$before" >"$evidence/delta-stat.pending"
)
delta_stable() {
    regular "$evidence/delta-stat.pending" && regular "$log" &&
        [ "$(stat -c '%d:%i:%s:%y:%z' "$log")" = "$(cat "$evidence/delta-stat.pending")" ] && same_boot
}
parse_delta() {
    awk -v mode="$1" -v worker="$2" '
      function positive(value) { return value ~ /^[1-9][0-9]*$/ && length(value) <= 15 }
      /^aidoku: Launcher worker started/ {
          starts++; if ($0 != "aidoku: Launcher worker started (pid=" worker ").") bad=1
          start_line=NR
      }
      /^aidoku: Launcher worker exited/ {
          exits++; if ($0 != "aidoku: Launcher worker exited with status 0.") bad=1
          exit_line=NR
      }
      /^aidoku: startup_library_submitted/ {
          frames++; frame_line=NR
          if (NF!=8 || $1!="aidoku:" || $2!="startup_library_submitted" ||
              $3!~/^boottime_us=[1-9][0-9]*$/ || $4!~/^sequence=[1-9][0-9]*$/ ||
              $5!~/^marker=[1-9][0-9]*$/ || $6!="layout=large" ||
              $7!="library=nonempty" || $8!="grid=nonempty") {bad=1; next}
          stamp=substr($3,13); sequence=substr($4,10); marker=substr($5,8)
          if (!positive(stamp) || !positive(sequence) || !positive(marker) || marker+0>4294967295) bad=1
      }
      END {
          if (bad || starts!=1 || frames>1 || (mode=="bind" && exits!=0) ||
              (mode=="collect" && exits!=1)) exit 2
          if (frames==0 && mode=="bind") exit 3
          if (frames!=1 || start_line>=frame_line || (mode=="collect" && frame_line>=exit_line)) exit 2
          printf "%s %s %s large nonempty nonempty\n", stamp, sequence, marker
      }
    ' "$evidence/delta.pending"
}

context || fail owner
case "$operation" in
    prepare)
        current && absent "$evidence" && absent "$out/dispatched" && absent "$out/qualified" || fail preparation
        directory "$stage/programs" && regular "$selected" && [ -x "$selected" ] &&
            regular "$app" && [ -x "$app" ] && [ "$(file_id "$selected")" = "$(file_id "$app")" ] || fail program
        regular "$visit/log-mark" && regular "$log" || fail log
        expected=$(cat "$visit/log-mark") && [ "$expected" = "$(stat -c '%d:%i %s' "$log")" ] || fail log
        IFS=' ' read -r inode offset extra <"$visit/log-mark" || fail log
        [ -z "${extra:-}" ] && number "$offset" && [ "$offset" -le 2000000000 ] || fail log
        mkdir -m 700 "$evidence" && put "$evidence/owner" "$label $name $arm" &&
            cp "$visit/boot" "$evidence/boot" && cp "$visit/log-mark" "$evidence/log-mark" &&
            file_id "$selected" >"$evidence/program" && tail -c 64 "$log" >"$evidence/log-tail" || fail preparation
        [ "$expected" = "$(stat -c '%d:%i %s' "$log")" ] && same_boot && program_same || fail preparation
        printf 'submission_prepared\n';;
    bind)
        proof && current && absent "$evidence/bound" && absent "$visit/complete" || fail bind
        trap 'if directory "$evidence"; then rm -f "$evidence/delta.pending" "$evidence/delta-stat.pending"; fi' 0
        trap 'exit 1' HUP INT TERM
        bound=$(active_identity) && brackets >/dev/null || fail identity
        set -- $bound; gui=$1; gui_birth=$2; runner=$3; worker=$5
        tries=60
        last_log_stat=
        while :; do
            [ "$(proc_row "$gui")" = "$runner $gui_birth" ] || fail identity
            log_stat=$(stat -c '%d:%i:%s:%y:%z' "$log") || fail log
            if [ "$log_stat" != "$last_log_stat" ] && snapshot_delta; then
                if event=$(parse_delta bind "$worker"); then
                    if delta_stable; then break; fi
                    result=3
                else result=$?; fi
                [ "$result" = 3 ] || fail log
                if delta_stable; then last_log_stat=$(cat "$evidence/delta-stat.pending"); fi
            fi
            tries=$((tries-1)); [ "$tries" -gt 0 ] || fail timeout
            usleep 200000 || fail timeout
        done
        [ "$(active_identity)" = "$bound" ] || fail identity
        set -- $event; stamp=$1
        times=$(brackets) || fail brackets
        set -- $times; [ "$stamp" -ge "$2" ] || fail clock
        put "$evidence/event" "$event" && put "$evidence/bound" "$bound" || fail bind
        printf 'submission_bound\n';;
    collect)
        proof && program_same && regular "$evidence/bound" && regular "$evidence/event" &&
            absent "$evidence/collected" &&
            line "$visit/complete" qualified && line "$visit/ack" observed &&
            cmp -s "$evidence/bound" "$visit/bound" && line "$out/dispatched" stock-tap || fail collect
        trap 'if directory "$evidence"; then rm -f "$evidence/delta.pending" "$evidence/delta-stat.pending"; fi' 0
        trap 'exit 1' HUP INT TERM
        IFS=' ' read -r gui gui_birth runner runner_birth worker worker_birth nonce extra <"$evidence/bound" || fail identity
        [ -z "${extra:-}" ] && pid "$worker" || fail identity
        snapshot_delta || fail log
        event=$(parse_delta collect "$worker") || fail log
        line "$evidence/event" "$event" || fail log
        set -- $event; stamp=$1; sequence=$2; marker=$3
        times=$(brackets) || fail brackets
        set -- $times; up_before=$1; up_after=$2
        [ "$stamp" -ge "$up_after" ] && [ "$up_before" -le "$up_after" ] || fail clock
        lower=$((stamp-up_after)); upper=$((stamp-up_before))
        delta_stable && program_same && put "$evidence/collected" done || fail collect
        printf 'SUBMISSION %s %s %s %s large nonempty nonempty\n' "$lower" "$upper" "$sequence" "$marker";;
esac
