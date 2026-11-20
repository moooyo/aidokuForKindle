# Production indexed-entry visit

The outer lease holder calls `sh visit.sh STEP LABEL CASE [ORIGIN]` on the
device. `LABEL` is 16 lowercase hex digits; `CASE` is a short lowercase
alphanumeric/hyphen identifier starting with a letter. Private receipts are
under `/var/tmp/aidoku-native-LABEL/visit-CASE`. Both that state parent and
`/mnt/us/.aidoku-native-LABEL` must already have an `owner` containing `LABEL`.

The one exception is `inspect LABEL CASE`, which checks exactly the admission
readbacks, produces only `visit_inputs_admitted no_stage|staged` or a safe
`refused visit_REASON`, and creates no stage, marker, lock, open, signal or
setting. With no stage it skips only the stage ownership checks; if either
stage already exists, both must have the proper owner. The outer runner must
preserve the first nested `refused visit_...` tag rather than substitute its
generic `visit_prepare` when diagnosing a real refusal.

1. `prepare LABEL CASE` snapshots stock state, light, the single indexed
   production document, access time and existing log offset. It opens no app.
2. `arm LABEL CASE stock-tap` or `arm LABEL CASE indexed-uri` checks the
   prepared state again and records dispatch intent **before** the outer
   timed action. For `stock-tap` the outer sampler must positively identify
   the stock Library tab and the unique tile, perform its single click and
   restore its pointer/tab. For `indexed-uri` the outer timed action can send
   the fixed production URI itself, or use `request LABEL CASE indexed-uri`
   to send it once. `arm` never sends a request. Do not retry an uncertain
   dispatch. Origins are distinct and never pooled.
3. `bind LABEL CASE` binds the normal process tree and verifies argv, launch
   environment, active phase, readiness and actual display/input descriptors.
   Call after the independently observed timing window, not on its critical
   path. `check LABEL CASE` repeats those checks without changing the owner.
4. `exit LABEL CASE` requests ordinary shutdown only from the bound GUI,
   verifies its exit acknowledgement and waits for stock ownership to return.
   It restores the original lights and checks the catalog/log interval.
   `recover LABEL CASE` is bounded cleanup after interruption; it cannot
   issue another open or promote a failed measurement. A refusal is not
   permission to kill or replace an unrelated session.

If an older, already-frozen wrapper cannot recognize a production launcher
directory that inherited setgid, the **original, still-verified host lease
holder only** can stream this corrected script as
`sh -s -- recover-dispatched LABEL CASE`. It locks the media stage against the
outer wrapper and checks both owner markers, current case, captured guard,
actual dispatch intent, request origin and unchanged boot before any signal.
This recovery-only entry accepts only a root-owned real production directory
with mode `0700` or `2700`; it never chmods it. The launcher creates that
directory using `mkdir` with umask `077`; the native helper checks
`(st_mode & 0777) == 0700` (`src/native_query/startup_portrait.c`). The real
`/tmp` can be a system alias. Only this fixed parent is resolved: the lock
leaf must be a non-link directory at the exact resolved-parent/leaf path and
both names must identify the same private inode throughout the observation.
Other private paths keep their stricter canonical-directory guards. The real
program/argv, nonce, process ancestry/birth and matching log checks still
apply. No visit qualifies on recovery. Continue with the outer `close` only
after `restored_only` or independent owner-bound recovery evidence; a new
session must not use this command to displace an existing owner.

The module does not lease, install, upload, read user books, take snapshots
of images, or remove another operator's stage. Reuse an existing private
case only for recovery. No phone, optical settling, or touch-readiness time
is inferred from these guards. Only short pass/fail flags belong in public
evidence; do not export the private files containing launch identities.

The worker-only mock uses an isolated private filesystem, actual Linux shell
utilities, a real fixture SQLite database and simulated processes/firmware. On
`test-env`, `dash -n visit.sh` and `python3 -B test_visit.py -v` exercise
cases, including ordinary restoration, duplicate indexes, stale process
identities and nonce, wrong startup environment, missing physical input,
reindex fallback and ambiguous log ownership. No Kindle was used by this test.
