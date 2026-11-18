#!/bin/sh
# Qualify the exact uninstrumented launcher and ELF in a guarded fixture.
# The driver freezes smoke-lib.sh (definitions preceding its entry point), the
# benchmark utility library, this script and all staged program payloads.
set -eu
set -f
umask 077
side=${1:-}; id=${2:-}
case "$side" in baseline|candidate|recover) ;; *) exit 64;; esac
case "$id" in ''|*[!0-9a-z-]*|-*) exit 65;; esac
[ "$#" -eq 2 ] && [ "${#id}" -le 48 ] && [ "${RUN:-}" = "$id" ] &&
    [ "${STAGE:-}" = "/mnt/us/.$id" ] && [ "$(readlink -f "$STAGE")" = "$STAGE" ] || exit 66
cd "$STAGE"
for lib in bench-lib.sh smoke-lib.sh; do [ -f "$lib" ] && [ ! -L "$lib" ] || exit 67; done
. "$STAGE/smoke-lib.sh"
protected() (
    . "$STAGE/bench-lib.sh"
    owner && stable_entry && same_hash "$INSTALLED" "$BRIDGE_SHA"
)
entry_catalog() (
    . "$STAGE/bench-lib.sh"
    catalog "$1"
)
unchanged_runtime() (
    (cd "$STAGE" && sha256sum -c inputs.sha256 >/dev/null) || exit 1
    protected
)
logmark() { IFS=' ' read -r stamp unused < /proc/uptime && printf '%s\t%s\n' "$stamp" "$1" >> "$OUT/evidence.tsv"; }
private_context() {
    variant=$1; OP=$variant-default; OUT=$STAGE/out/$OP
    expected_home=$STAGE/$variant; APP=$expected_home/app/aidoku
    LOG=$expected_home/data/logs/launcher.log; PIXTMP=/var/tmp/aidoku-next-smoke-$RUN
    [ -d "$OUT" ] && [ ! -L "$OUT" ] && [ "$(readlink -f "$OUT")" = "$OUT" ] || return 1
    cd "$STAGE"; same_line "$OP" case
}
private_cleanup() {
    for leaf in smoke.request smoke.log.bound owned-app owned-app.pending smoke.sleep; do
        if ! absent "$leaf"; then regular "$leaf" && rm "$leaf" || return 1; fi
    done
}
if [ "$side" = recover ]; then
    same_line "$RUN" owner || exit 68
    if regular smoke.request; then
        prior=$(cat smoke.request)
        case "$prior" in baseline-default|candidate-default) ;; *) exit 69;; esac
        private_context "${prior%-default}" || exit 70
        if absent owned-app; then
            m=180
            while [ "$m" -gt 0 ] && absent "$LOCK"; do usleep 100000; m=$((m-1)); done
            m=80
            while [ "$m" -gt 0 ] && ! absent "$LOCK" && absent owned-app; do
                bind_app && break
                usleep 100000; m=$((m-1))
            done
            if absent owned-app && absent "$LOCK"; then
                wait_native 80 && remove_pixels && private_cleanup || exit 70
                printf '%s\n' LIFECYCLE_RECOVERED; exit 0
            fi
            regular owned-app || exit 70
        fi
        recover && private_cleanup || exit 70
    else
        # A failed preflight owned no application. Do not recover a foreign lock.
        wait_native 80 || exit 70
        PIXTMP=/var/tmp/aidoku-next-smoke-$RUN; remove_pixels || exit 70
    fi
    printf '%s\n' LIFECYCLE_RECOVERED; exit 0
fi
private_context "$side" && unchanged_runtime || fail 'The lifecycle fixture or registration changed.'

proc_projections() (
    apr=$1; wpr=$2
    cat "/proc/$apr/cmdline" > "$OUT/app.argv.nul" && cat "/proc/$wpr/cmdline" > "$OUT/worker.argv.nul" || exit 1
    printf '%s\0' "$APP" --kindle --device scribe --data-dir "$expected_home/data" | cmp -s - "$OUT/app.argv.nul" || exit 1
    printf '%s\0' sh "$expected_home/launch.sh" --foreground | cmp -s - "$OUT/worker.argv.nul" || exit 1
    for role in app worker; do
        case "$role" in app) id=$apr;; worker) id=$wpr;; esac
        # Never copy raw environment or child credentials. Reject experimental
        # switches by name; disclose only the one permitted fixture binding.
        tr '\000' '\n' < "/proc/$id/environ" | awk -v home="$expected_home" '
          /^AIDOKU_HOME=/ { if ($0 != "AIDOKU_HOME=" home) bad=1; else print; n++ }
          /^AIDOKU_(STARTUP|RESEARCH|READER_TIMING|NATIVE_REFRESH_TRACE|COLD)/ {bad=1}
          END {if(n!=1 || bad) exit 1}' > "$OUT/$role.env-relevant" || exit 1
    done
    hash=$(file_sha "/proc/$apr/exe") || exit 1
    [ "$hash" = "$(file_sha "$APP")" ] && new_line "$hash" "$OUT/app-exe.sha" || exit 1
    same_app
)
record_artifacts() (
    for rel in app/aidoku launch.sh app/aidoku-native-query app/kindle-jpeg-worker app/aidoku-startup-portrait; do
        path=$expected_home/$rel
        if [ "$rel" = app/aidoku-startup-portrait ] && absent "$path"; then continue; fi
        regular "$path" && [ -x "$path" ] && hash=$(file_sha "$path") || exit 1
        printf '%s\t%s\n' "$rel" "$hash" || exit 1
    done
)
input_fds() (
    id=$1; [ -d "/proc/$id/fd" ] || exit 1
    set +f
    for f in /proc/"$id"/fd/*; do
        dest=$(readlink "$f" 2>/dev/null) || continue
        case "$dest" in /dev/input/event*) idx=${dest#/dev/input/event}; number "$idx" || exit 1; printf 'event%s\n' "$idx";; esac
    done
)
observe_proc() (
    phase=$1; same_app || exit 1; row=$(identity) || exit 1; set -- $row
    printf '%s %s %s %s %s %s\n' "$1" "$2" "$3" "$4" "$5" "$6" > "$OUT/proc-$phase.txt" || exit 1
    roster=$(input_fds "$1") || exit 1
    printf '%s\n' "$roster" | sed '/^$/d' | sort -u > "$OUT/input-$phase.txt" || exit 1
    [ "$phase" = parked ] || [ -s "$OUT/input-$phase.txt" ] || exit 1
    same_app
)
observe_lifecycle() (
    phase=$1; same_app || exit 1
    row=$(identity) || exit 1; set -- $row
    epoch=$(cat "$LOCK/epoch") && number "$epoch" && [ "$epoch" -gt 0 ] || exit 1
    case "$phase" in parked) ack=sleep-ready;; wake) ack=resume-ready;; *) exit 1;; esac
    same_line "$1 $7 $epoch" "$LOCK/$ack" || exit 1
    printf '%s\t%s\n' "$1" "$epoch" > "$OUT/$ack.tsv" || exit 1
    regular "$LOCK/lifecycle-state.json" && [ "$(wc -c < "$LOCK/lifecycle-state.json")" -le 1048576 ] || exit 1
    # This file is written atomically with serde_json::to_vec_pretty. Read
    # only known, exactly indented scalars. Never export the nonce or book data.
    awk -v app="$1" -v epoch="$epoch" -v nonce="$7" -v phase="$phase" '
      /^  "platform": \{$/ { platform=1; next }
      /^  }/ { platform=0; next }
      { k=$0; sub(/^ +"/,"",k); sub(/".*/,"",k); v=$0; sub(/^[ ]*"[a-z_]+": /,"",v); sub(/,$/,"",v); gsub(/^"|"$/,"",v) }
      /^  "(nonce|pid|epoch|phase|same_process_resumes)": / { a[k]=v; seen[k]++ }
      platform && /^    "(input_released|render_paused|parked|pause_count|resume_count|frame_send_count|resume_full_frame_count|input_generation)": / { a[k]=v; seen[k]++ }
      END { split("pid epoch phase same_process_resumes input_released render_paused parked pause_count resume_count frame_send_count resume_full_frame_count input_generation", keys," ");
        if (seen["nonce"]!=1 || a["nonce"]!=nonce || a["pid"]!=app || a["epoch"]!=epoch) exit 1;
        parked=(phase=="parked"); if(a["phase"]!=(parked?"suspended":"active")) exit 1;
        for(i=1;i<=12;i++){ n=keys[i]; if(seen[n]!=1) exit 1;
          if(n=="input_released" || n=="render_paused" || n=="parked") { if (a[n]!=(parked?"true":"false")) exit 1 }
          else if(n!="phase" && a[n]!~/^[0-9]+$/) exit 1;
          printf "%s\t%s\n", n,a[n] }
      }' "$LOCK/lifecycle-state.json" > "$OUT/lifecycle-$phase.tsv" || exit 1
    same_app
)

for leaf in smoke.request smoke.log.bound owned-app owned-app.pending smoke.sleep "$OUT/evidence.tsv"; do absent "$leaf" || fail 'An earlier lifecycle case is unfinished.'; done
regular "$STAGE/capture" && [ -x "$STAGE/capture" ] && [ "$(file_sha "$STAGE/capture")" = "$CAPTURE_SHA" ] || fail 'Unqualified capture tool.'
record_artifacts > "$OUT/installed-hashes.tsv" || fail 'Invalid staged executable.'
wait_native 80 || fail 'Required native ownership is unavailable.'
if ! regular smoke.native; then active=$(lipc-get-prop com.lab126.appmgrd activeApp); new_line "$active" smoke.native; fi
new_line 'width=1860 height=2480 bpp=8 p6_header_bytes=36 p6_bytes=13838436 crop_top_px=72' "$OUT/frame-format.tsv"
entry_catalog "$OUT/catalog-before"; cmp "$STAGE/original-catalog.identity" "$OUT/catalog-before.identity"
# An empty, fixture-local log may not exist until the first worker opens it.
mkdir -p "$expected_home/data/logs"; if absent "$LOG"; then (set -C; : > "$LOG"); fi
bind_log || fail 'Cannot bind the staged log.'
absent "$PIXTMP" && mkdir -m 700 "$PIXTMP" && new_line "$RUN" "$PIXTMP/owner" || fail 'Cannot stage the private capture.'
lipc-set-prop -i com.lab126.powerd touchScreenSaverTimeout 1
sleep 2; native_now || fail 'Native state changed before launch.'
new_line "$OP" smoke.request; logmark request
lipc-set-prop com.lab126.appmgrd start file:///mnt/us/documents/Aidoku.sh
tries=180; ready=0
while [ "$tries" -gt 0 ]; do
    if bind_app; then
        delta=$(log_delta) || fail 'Launcher log was replaced.'
        case "$delta" in *'aidoku: native window ready; profile=scribe, logical=930x1240, scale=2'*)
            if same_line active "$LOCK/phase"; then ready=1; break; fi;; esac
    fi
    usleep 100000; tries=$((tries-1))
done
[ "$ready" -eq 1 ] || fail 'Normal application did not become ready.'
row=$(identity); set -- $row; apid=$1; worker=$5; session_nonce=$7
proc_projections "$apid" "$worker" || fail 'Observed argv, environment or executable differs from the production contract.'
observe_proc active || fail 'Cannot observe actual initial input descriptors.'
logmark ready
sleep 12
frame=$(snapshot "$OUT/library.right.tsv") || fail 'Initial Library frame or right toolbar unavailable.'
if [ "$side" = candidate ]; then
    cmp -s "$STAGE/out/baseline-default/library.right.tsv" "$OUT/library.right.tsv" || fail 'Library right toolbar differs from the baseline.'
fi
new_line "$frame" "$OUT/library.sha"; logmark library
new_line "$RUN" smoke.sleep; same_app || fail 'Sleep owner changed.'
logmark request_sleep; lipc-set-prop -i com.lab126.powerd powerButton 1
wait_phase parked 45 && observe_proc parked && observe_lifecycle parked || fail 'Failed to prove an owned input release and park.'
logmark parked; same_app || fail 'Wake owner changed.'
logmark request_wake; lipc-set-prop -i com.lab126.powerd wakeUp 1
wait_phase active 100 && observe_proc wake && observe_lifecycle wake || fail 'Failed to prove retained input reacquisition and wake.'
logmark wake; rm smoke.sleep
wake_frame=$(snapshot "$OUT/wake.right.tsv") || fail 'Restored Library frame or right toolbar unavailable.'
cmp -s "$OUT/library.right.tsv" "$OUT/wake.right.tsv" || fail 'Restored right toolbar differs.'
new_line "$wake_frame" "$OUT/wake.sha"; [ "$frame" = "$wake_frame" ] || fail 'Restored Library pixels differ.'
logmark restored_frame
epoch=$(cat "$LOCK/epoch"); number "$epoch" && [ "$epoch" -gt 0 ] && absent "$LOCK/ordinary-exit" || fail 'Stale or invalid exit epoch.'
logmark request_term; signal_owned TERM || fail 'Cannot terminate the owned application.'
tries=500; ack=0
while [ "$tries" -gt 0 ]; do
    if same_line "$apid $session_nonce $epoch" "$LOCK/ordinary-exit"; then ack=1; break; fi
    [ -d "$LOCK" ] || break; usleep 10000; tries=$((tries-1))
done
[ "$ack" -eq 1 ] || fail 'No matching cooperative exit acknowledgment was observed.'
printf '%s\t%s\n' "$apid" "$epoch" > "$OUT/ordinary-exit.tsv"; logmark ordinary_exit
wait_native 200 || fail 'Native interface was not restored.'
logmark native_restored
delta=$(log_delta); printf '%s\n' "$delta" > "$OUT/launcher.log"
[ "$(grep -c '^aidoku: Launcher worker started (pid=[0-9][0-9]*)\.$' "$OUT/launcher.log" || :)" -eq 1 ] &&
    [ "$(grep -c '^aidoku: native window ready;' "$OUT/launcher.log" || :)" -eq 1 ] &&
    [ "$(grep -c '^aidoku: Launcher worker exited with status' "$OUT/launcher.log" || :)" -eq 1 ] &&
    grep -Fxq 'aidoku: Launcher worker exited with status 0.' "$OUT/launcher.log" &&
    [ "$(grep -Fxc 'aidoku: Catalog notification completed.' "$OUT/launcher.log" || :)" -eq 1 ] || fail 'Ambiguous lifecycle completion.'
case "$delta" in *'Catalog notification unavailable'*|*'standard reindex recovery'*) fail 'Catalog recovery replaced the normal completion.';; esac
entry_catalog "$OUT/catalog-after"; cmp "$OUT/catalog-before.identity" "$OUT/catalog-after.identity"
[ "$(cat "$OUT/catalog-after.access")" -gt "$(cat "$OUT/catalog-before.access")" ] || fail 'Recent access did not advance.'
remove_pixels && private_cleanup && protected || fail 'Qualification cleanup failed.'
new_line ok "$OUT/case-ok"; printf '%s\n' LIFECYCLE_OK
