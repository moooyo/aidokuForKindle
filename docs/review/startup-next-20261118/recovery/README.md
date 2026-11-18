# Retiring an invalid batch after native catalog reindexing

In `aidoku-next-cold-confirm-576fdfaf`, the first baseline warmup launched and
exited, but the native catalog replaced the fixed document's identity. Both
observed catalog records contained one item, with the same MIME, thumbnail and
size. The document's on-disk modification time advanced and agreed with the new
catalog row; recent access also advanced. The launcher reported neither deferred
notification completion nor notification fallback. The outcome is consistent
with the stock timestamp/scan path. There is no evidence for the specific reason
the initial quiet catalog operation was unavailable. The qualification failed;
no timing from this batch belongs in an A/B or confirmation calculation.

The normal frozen runner restored the installed launcher and rejected the changed
catalog identity as designed. Exact bytes, permissions and modification time of
the installed launcher were verified. Of 38 protected snapshot rows, only the
document modification-time field changed. The operator verified native control,
closed the owned session, retained the replacement catalog identity and time,
removed the temporary stage and released the lease. Public evidence excludes
identities, timestamps from user files, or private logs. No catalog change was
reversed. The original failed evidence and release receipt are retained.

The independent `retire_invalid.py` tool accepts only known frozen warm or cold
runners and existing plans. It never replaces their source or their pass/fail
criteria. `audit --batch <dir>` is host-only and works with the released incident.
`release --batch <dir>` is only for a newly failed batch while that exact owner
still holds its original lease, after its ordinary cleanup has **already**
restored the original launcher. It cannot acquire a lease or use a released
token. Only the operator runs that mode on test-env.

The release mode checks a completed owned application and its native restoration,
stable frontmost state, fixed installed bytes, the original launcher mode/time,
an uninterrupted history of accepted earlier cases, one eligible stock-style
replacement, advancing access, and no notification messages for the failed case.
Every other protected observation must match. It saves private evidence and
removes only the identified owned stage. It never rewrites registration,
catalog rows, launcher files or the document timestamp. An unrelated change,
incomplete lifecycle, a different selected row, unusual clock, lost lease, or
unrestored display/lighting rejects the exception. A successful retirement
explicitly keeps `success=false` and `physical_catalog_identity_preserved=false`.

`prepare_preflight.py` provides a separate, optional one-shot read-only probe. It
qualifies the fixed installed helper and registry, checks the native frontend and
catalog/file state before and after the helper's `--probe`, and uses a fresh lease.
It does not retimestamp, repair or reindex a file. Immutable SQLite reads refuse
nonempty WAL or recovery journals rather than reading a stale database snapshot.
This is a root-context health observation, not proof of framework launch success.
Its raw private configuration and any device or catalog identity stay outside
public evidence. All host qualification runs through `ssh test-env`.
