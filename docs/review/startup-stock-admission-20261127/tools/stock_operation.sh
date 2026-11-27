# The original native, catalog, input, mount, and restoration guards above
# this block are used unchanged except where the trace-only plan requires it.

admission_reason() { printf '%s\n' "$1"; exit 1; }

stock_admit() (
    [ "$(id -u)" = 0 ] || admission_reason privilege_unverified
    directory "$media" && directory "$home" && directory "$home/app" && directory "$data" &&
        directory /var/tmp || admission_reason paths_unverified
    clean_absent || admission_reason attempt_paths_not_empty
    no_overlay || admission_reason mount_state_unverified
    native admission 8>&1 >/dev/null || exit 1
    line "$data/device-profile" scribe || admission_reason profile_unverified
    for tool in @TOOLS@; do command -v "$tool" >/dev/null || admission_reason "missing_tool_$tool"; done
    @CHECK_TIMEOUT@
    filesystem=$(df -Pk "$media") || admission_reason space_read_failed
    available=$(printf '%s\n' "$filesystem" | awk 'END {print $4}') && number "$available" &&
        [ "$available" -ge 0 ] || admission_reason space_unverified
    [ "$available" -ge "$space" ] || admission_reason space_requirement_not_met
)

refuse_admission() {
    printf 'AB_ADMISSION_REFUSED %s %s %s %s %s\n' "$action" "$kind" "$label" "$expected_boot" "$1"
    exit 1
}

check_admission() {
    # Only fixed reason tokens go on the wire. Suppress stderr from the
    # system utilities; their text does not determine permission or policy.
    if result=$(stock_admit 2>/dev/null); then
        [ -z "$result" ] || fail admission_internal_error
    else
        status=$?
        [ "$status" = 1 ] || fail admission_internal_error
        case "$result" in ''|*[!a-z0-9_]*) fail admission_internal_error;; esac
        refuse_admission "$result"
    fi
}

owned_receipt() { printf 'AB_RESTORED %s %s\n' "$kind" "$label"; }

current_boot_is() { line "$boot" "$expected_boot"; }

close_scope() {
    current_boot_is && stock_lock_is_current && line "$state/reserved" "$label"
}

close_original() (
    no_overlay && stock_original && lights compare && entry_preserved && reference &&
        stamp=$(original_stamp) && line "$state/original" "$stamp"
)

close_owned() (
    policy=$1
    close_scope || exit 1
    # A delayed repair request may observe a spent intent, but it must not
    # repeat the repair steps. Only the call that claimed it can send input.
    if [ "$policy" = repair ] && ! absent "$state/input-restore-intent"; then policy=confirm; fi
    already_closed=no
    if line "$state/closed" "$label"; then
        already_closed=yes
    else
        absent "$state/closed" || exit 1
    fi
    if absent "$state/armed"; then
        # This journal could have received files, but it could not navigate or click.
        no_overlay && native >/dev/null && absent "$state/tap-intent" && absent "$state/active-slot" &&
            absent "$state/input-restore-intent" || exit 1
        absent "$stage" || stage_owned || exit 1
    else
        line "$state/armed" "$label" && stage_owned || exit 1
        if [ "$policy" = repair ] && [ "$already_closed" = no ]; then
            @CLOSE_TAP@
            wait_stock && @RESTORE_LAUNCHER@@RESTORE_LIGHTS@no_overlay || exit 1
        else
            @INSPECT_TAP@
        fi
        close_original || exit 1
        if [ "$policy" = repair ] && [ "$already_closed" = no ]; then
            @RESTORE_NAVIGATION@
            close_original || exit 1
        else
            @INSPECT_NAVIGATION@
            stock_original && no_overlay && lights compare || exit 1
        fi
    fi
    close_scope || exit 1
    @READBACK_RETURN@
    { [ "$already_closed" = yes ] || put "$state/closed" "$label"; } && owned_receipt
)

@READBACK_FUNCTIONS@

finalize_owned() (
    current_boot_is && stock_lock_is_current && line "$state/reserved" "$label" && line "$state/closed" "$label" &&
        no_overlay && native >/dev/null || exit 1
    if ! absent "$stage"; then stage_owned && rm -rf "$stage" || exit 1; fi
    absent "$stage" && stock_lock_is_current && rm -rf "$state" && clean_absent &&
        printf 'AB_FINALIZED %s %s\n' "$kind" "$label"
)

case "$action" in
    admit)
        [ "$expected_boot" = - ] || fail arguments
        check_admission
        { IFS= read -r current_boot <"$boot"; } 2>/dev/null || refuse_admission boot_unreadable
        printf '%s\n' "$current_boot" | grep -Eq '^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$' &&
            line "$boot" "$current_boot" || refuse_admission boot_invalid
        printf 'AB_ADMITTED %s %s %s\n' "$kind" "$label" "$current_boot"; exit 0;;
esac
if ! current_boot_is; then
    [ "$action" != reserve ] || refuse_admission reservation_boot_unverified
    fail different_boot
fi
case "$action" in
    absent)
        clean_absent && no_overlay && native >/dev/null || fail residue
        printf 'AB_ABSENT %s %s\n' "$kind" "$label"; exit 0;;
    reserve)
        check_admission
        # Only reserve may create the journal. Run never makes a directory
        # after a delayed dispatch or a successful close and finalize.
        mkdir -m 700 "$state" || fail state_creation
        exec 9<"$state"; flock -n 9 || fail operation_running
        locked=$(inode "/proc/$$/fd/9") && named=$(inode "$state") && [ "$locked" = "$named" ] &&
            put "$state/owner" "$label" && put "$state/mode" "$mode" && put "$state/kind" "$kind" &&
            put "$state/boot" "$expected_boot" && current_boot_is && stock_lock_is_current &&
            put "$state/reserved" "$label" || fail state_creation
        printf 'AB_RESERVED %s %s\n' "$kind" "$label"; exit 0;;
    close|finalize@RESTORE_ACTIONS@)
        state_owned || fail no_owned_journal
        exec 9<"$state"; flock -n 9 || fail operation_running
        stock_lock_is_current || fail no_owned_journal
        case "$action" in
            close) close_owned @CLOSE_POLICY@ || fail closeout;;
            finalize) finalize_owned || fail finalize;;
            @RESTORE_CASES@
        esac
        exit 0;;
esac
state_owned || fail no_owned_journal
exec 9<"$state"; flock -n 9 || fail operation_running
current_boot_is && stock_run_may_stage && line "$state/reserved" "$label" && absent "$state/entered" &&
    absent "$stage" && no_overlay && native >/dev/null || fail run_not_admitted
put "$state/entered" "$label" || fail run_already_entered
finish() {
    result=$?
    trap - 0
    trap '' HUP INT TERM
    @STOP_WATCHER@
    close_owned @RUN_CLOSE_POLICY@ || { printf 'AB_REFUSED closeout\n' >&2; exit 1; }
    exit "$result"
}
trap finish 0
trap 'exit 1' HUP INT TERM
