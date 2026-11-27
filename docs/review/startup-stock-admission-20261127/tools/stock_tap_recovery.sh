# Only the one planned card stimulus may establish this tap intent. Never
# clear the intent until the positive-restoration journal is finalized.
record_visit_log() (
    bound_out=$1; log_identity=$2
    [ "$(inode "$log")" = "$log_identity" ] || exit 1
    if absent "$bound_out/bound-log-inode"; then put "$bound_out/bound-log-inode" "$log_identity" || exit 1; fi
    line "$bound_out/bound-log-inode" "$log_identity"
)

verify_visit_log() (
    regular "$log" && live_log_inode=$(inode "$log") && line "$1/bound-log-inode" "$live_log_inode"
)

remember_visit() (
    bound_out=$1; bound_tuple=$2
    if absent "$bound_out/bound-tuple"; then put "$bound_out/bound-tuple" "$bound_tuple" || exit 1; fi
    line "$bound_out/bound-tuple" "$bound_tuple"
)

visit_process_finished() (
    who=$1; birth=$2
    if ! { IFS= read -r stat_row <"/proc/$who/stat"; } 2>/dev/null; then
        absent "/proc/$who"; exit $?
    fi
    case "$stat_row" in "$who ("*') '*) ;; *) exit 1;; esac
    set -- ${stat_row##*) }
    [ "$#" -ge 20 ] || exit 1
    process_state=$1; shift 19
    number "$1" || exit 1
    [ "$1" = "$birth" ] || exit 0
    case "$process_state" in Z|X|x) exit 0;; *) exit 1;; esac
)

bound_worker_exited() (
    bound_out=$1
    verify_visit_log "$bound_out" && regular "$bound_out/bound-tuple" && IFS= read -r bound_tuple <"$bound_out/bound-tuple" &&
        line "$bound_out/bound-tuple" "$bound_tuple" || exit 1
    set -- $bound_tuple
    [ "$#" = 7 ] && pid "$1" && number "$2" && pid "$3" && number "$4" && pid "$6" && number "$7" || exit 1
    # A final log marker precedes process exit. Do not remove a mount while
    # either same-birth process still runs. Unreadable identities fail closed.
    visit_process_finished "$1" "$2" && visit_process_finished "$3" "$4" &&
        visit_process_finished "$6" "$7" || exit 1
    captured_log=$(worker_log "$bound_out") || exit 1
    printf '%s\n' "$captured_log" | awk -v worker="$3" '
        /^aidoku: Launcher worker started/ {
            if ($0!="aidoku: Launcher worker started (pid=" worker ").") wrong=1;
            starts++; first=NR;
        }
        /^aidoku: Launcher worker exited/ {
            if ($0 !~ /^aidoku: Launcher worker exited with status (0|[1-9][0-9]?[0-9]?)\.$/ || $7+0>255) wrong=1;
            ends++; last=NR;
        }
        END {exit wrong || starts!=1 || ends!=1 || first>=last}'
)

unissued_owned_tap() (
    absent "$state/tap-intent" || exit 1
    # A sampler may have been interrupted before the input command.
    if ! absent "$state/active-slot"; then
        item=$(case_path) && absent "$item/dispatched" || exit 1
    fi
)

issued_owned_tap() (
    item=$state/out/control-01
    line "$state/tap-intent" control-01 && directory "$item" &&
        line "$item/origin" stock-tap && line "$item/dispatched" stock-tap
)

inspect_owned_tap() {
    unissued_owned_tap || { issued_owned_tap && bound_worker_exited "$state/out/control-01"; }
}

finish_one_owned_tap() (
    unissued_owned_tap && exit 0
    issued_owned_tap || fail click_completion_unverified
    item=$state/out/control-01
    if ! bound_worker_exited "$item"; then
        # A click can still be queued, or a worker can have exited before we
        # could bind it. Neither an empty lock nor a quiet log proves either.
        if tuple=$(await_own 60); then
            remember_visit "$item" "$tuple" || exit 1
            if absent "$item/term"; then stop_owned "$tuple" || :; fi
        fi
    fi
    wait_stock || exit 1
    remaining=25
    until bound_worker_exited "$item"; do
        remaining=$((remaining-1)); [ "$remaining" -gt 0 ] && usleep 50000 || fail click_completion_unverified
    done
)
