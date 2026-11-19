# Independent physical qualification, never included in the latency driver.
wake_same_gui() {
    read -r wp wb ws <"$OUT/gui-owner" || return 1
    is_app "$wp" "$variant" && reopen_proc "$wp" && [ "$r_birth:$r_session" = "$wb:$ws" ]
}
wait_wake_phase() {
    wanted=$1;left=$2
    while [ "$left" -gt 0 ]; do
        wake_same_gui || return 1
        [ "$(cat "$LOCK/phase" 2>/dev/null || :)" != "$wanted" ] || return 0
        usleep 100000;left=$((left-1))
    done
    return 1
}
wake_event() { read -r t unused </proc/uptime; printf '%s %s\n' "$t" "$1" >>"$OUT/wake-markers.tsv"; }
record_wake_state() {
    label=$1;wake_same_gui
    inputs=0
    for desc in /proc/$wp/fd/*; do
        target=$(readlink "$desc" 2>/dev/null || :)
        case "$target" in /dev/input/event[0-9]*) inputs=$((inputs+1));; esac
    done
    printf '%s %s\n' "$label" "$inputs" >>"$OUT/wake-input.tsv"
    if [ "$label" != active ]; then
        epoch=$(cat "$LOCK/epoch");case "$epoch" in ''|*[!0-9]*) return 1;;esac; [ "$epoch" -gt 0 ]
        record=sleep-ready; [ "$label" = parked ] || record=resume-ready
        [ "$(cat "$LOCK/$record")" = "$wp $(cat "$LOCK/nonce") $epoch" ]
        awk -v mode="$label" '
          /^  "platform": \{/ {inpf=1;next}
          /^  }/ {inpf=0;next}
          inpf && /^    "(input_released|render_paused|parked)": / {
            k=$0;sub(/^    "/,"",k);sub(/".*/,"",k);v=$0;sub(/^[^"]*"[a-z_]+": /,"",v);sub(/,$/,"",v);a[k]=v;n++
          }
          END { want=mode=="parked"?"true":"false";if(n!=3 || a["input_released"]!=want || a["render_paused"]!=want || a["parked"]!=want) exit 1; print mode,want,want,want }' "$LOCK/lifecycle-state.json" >>"$OUT/wake-platform.tsv"
    fi
    wake_same_gui
}
reopened_sleep() {
    owner;wake_same_gui;wait_wake_phase active 60
    [ "$(lipc-get-prop -s com.lab126.powerd state)" = active ]
    # This qualification adds sufficient dwell to finish all local covers.
    # The independent analyzer checks the accepted complete frame predates this grab.
    sleep 5;record_wake_state active
    wake_same_gui;printf '%s %s %s\n' "$wp" "$wb" "$ws" >"$OUT/wake-owner"
    wake_event ready_before_sleep;"$STAGE/capture" >"$OUT/pre-wake.ppm"
    [ ! -e "$STAGE/reopen-wake-pending" ];[ ! -L "$STAGE/reopen-wake-pending" ];printf '%s\n' "$C" >"$STAGE/reopen-wake-pending"
    wake_same_gui;lipc-set-prop -i com.lab126.powerd powerButton 1
    wait_wake_phase parked 70;record_wake_state parked;wake_event parked
    wake_same_gui;lipc-set-prop -i com.lab126.powerd wakeUp 1;wait_wake_phase active 120;record_wake_state wake
    [ "$(lipc-get-prop -s com.lab126.powerd state)" = active ];rm "$STAGE/reopen-wake-pending"
    sleep 1;wake_same_gui;printf '%s %s %s\n' "$wp" "$wb" "$ws" >>"$OUT/wake-owner"
    wake_event active_after_wake;"$STAGE/capture" >"$OUT/post-wake.ppm";wake_same_gui
    printf 'same-gui-returned\n' >"$OUT/wake-ok"
}
recover_reopened_sleep() {
    owner
    if [ ! -e "$STAGE/reopen-wake-pending" ] && [ ! -L "$STAGE/reopen-wake-pending" ]; then return 0;fi
    nonlink "$STAGE/reopen-wake-pending"; C=$(cat "$STAGE/reopen-wake-pending")
    case "$C" in baseline-qualify-second|candidate-qualify-second) ;;*) return 1;;esac
    [ "$(cat "$STAGE/case")" = "$C" ];pair=${C%-*};variant=${C%%-*};OUT=$STAGE/out/$pair/second
    if [ ! -e "$LOCK" ]; then native_wait;[ "$(lipc-get-prop -s com.lab126.powerd state)" = active ];rm "$STAGE/reopen-wake-pending";return 0;fi
    wake_same_gui; tries=90
    while [ "$tries" -gt 0 ]; do
        wake_same_gui;ph=$(cat "$LOCK/phase" 2>/dev/null || :);power=$(lipc-get-prop -s com.lab126.powerd state)
        if [ "$ph:$power" = active:active ]; then rm "$STAGE/reopen-wake-pending";return 0;fi
        if [ "$ph" = parked ] && [ "$power" != active ]; then lipc-set-prop -i com.lab126.powerd wakeUp 1;wait_wake_phase active 120;[ "$(lipc-get-prop -s com.lab126.powerd state)" = active ];rm "$STAGE/reopen-wake-pending";return 0;fi
        usleep 100000;tries=$((tries-1))
    done
    return 1
}
