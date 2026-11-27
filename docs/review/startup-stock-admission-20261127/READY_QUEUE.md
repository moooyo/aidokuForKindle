# Ready queue for the shared Kindle

The coordinator keeps one FIFO list of **ready** batches. A ready notice is a
handoff signal, not a reservation, a lease, or authorization. The actual
operator process on `test-env` must hold the shared OS file lock through
all device operations, cleanup, connection closure and verified handback.
Keep the existing owner/token lease as the durable recovery record. There
is no need to replace either mechanism with a scheduler.

1. **Publish ready only when dispatchable.** The designated operator has a
   current user's approval, a complete preflighted package, scripts, fixtures
   and tools, and can execute immediately. A batch which is still building,
   awaiting review/approval, or missing an operator is not ready. Include a
   nonsecret batch name, profile, permitted actions and visit count, approval
   expiry, worst-case execution allowance including restoration, and a reply
   route. Never include a lease token, credentials,
   host addresses, private paths, frames, or inventory data in a notice.
2. **Invite the next ready operator on verified release.** Queue order is the
   order of ready notices, not a time-slot calendar. Withdraw or skip a stale,
   expired, or blocked entry; do not make the device wait for its operator to
   return or its artifacts to finish. A new ready notice goes at the tail.
   The outgoing operator verifies original state, closes connections and
   releases with owner/token checks, then promptly notifies the next entry.
   An old return receipt is not proof that the lock is free now.
3. **Dispatch through the shared operation lock.** A current handoff means
   the selected operator may *try* to acquire, not that it succeeded. The
   operation lock must enclose the existing atomic owner/token acquire or the
   original owner's recovery check. If either is busy, open no device
   connection and do not alter or steal the record. Let the current holder
   finish and notify; keep any wait on the host outside both locks. No
   automatically repeated device requests. Start the local time budget at
   dispatch rather than pre-booking a wall-clock window. Limit the duration
   and scope to the actual approval, and keep the existing restoration budget.
4. **Return as soon as possible.** A read-only refusal never justifies a
   wake/tap/setting change or a repeated attempt. A clean refusal releases
   immediately. For a potentially mutating request with unknown outcome,
   complete only the original operator's bounded restoration/verification.
   If that cannot finish, record the unresolved state and exit the OS lock;
   keep the original owner/token record to block new visits until verified
   recovery. Never announce an unverified handback. Do offline analysis and
   wait for follow-up work outside the operation lock. The next new batch
   uses a fresh attempt and token even if fewer visits were run.

For Aidoku, `preflight` validates the package without acquiring a lease. A
later approved dispatch uses `run` with `--confirmed-one-physical-visit`,
`--approval-expires` and `--max-visit-seconds`. The latter two limit when the
operation may start and its execution budget; they do not reserve time or
keep the device idle. `equipment_busy: true` means this attempt never entered
and did not start a lease; it is not a measurement result. The shared queue
can use coordination messages and existing verified return receipts. The
runner does not publish or grant queue positions, and separate clients must
not infer FIFO from atomic lease acquisition alone.

For coordination metrics, record when a batch became ready, the previous
verified release, when the next lease actually started, and the reason for
a skip. Only time with both an idle device and an eligible ready batch is a
queue delay. Builds and approval waits do not count; running extra tests to
raise utilization does not help.
