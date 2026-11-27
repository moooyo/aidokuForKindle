# Candidate guard for a future stock-device operator. Source only after its
# standard state_owned, same_boot, inode and absent helpers are defined.
# The caller must already hold flock on the owned directory FD 9.
stock_lock_is_current() {
    state_owned && same_boot &&
        locked_inode=$(inode "/proc/$$/fd/9" 2>/dev/null) &&
        named_inode=$(inode "$state" 2>/dev/null) &&
        [ "$locked_inode" = "$named_inode" ]
}

stock_run_may_stage() {
    stock_lock_is_current && absent "$state/closed"
}
