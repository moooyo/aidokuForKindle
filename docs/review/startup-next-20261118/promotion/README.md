# Conditional startup promotion

These operator tools are never included in the Kindle payload. They do not
approve a release or establish that a candidate improves performance.
Run only through `ssh test-env`. Do not run any device mode while building,
analyzing or waiting for the designated operator. The historical installer
that changes the shared catalog registration is not used.

## Qualify the exact defaults before packaging

After screening, independent confirmation and separate image qualification,
create the selection described below, initially without a `lifecycle` field.
Also bind the original build evidence: `build-attestation.json` contains the
canonical `source_manifest_sha256`, the selected package members and their
SHA-256 values under `artifact_sha256`, and an `evidence` array of hashed file
URI references to the original build logs, artifact hashes and source records.
Actual producer evidence is required; a newly written boolean is insufficient.

```sh
python3 -I default_lifecycle.py --selection "$selection" --selection-sha256 "$selection_sha" \
  --build-attestation "$build_record" --build-attestation-sha256 "$build_sha" --source-root "$source"
```

This command prepares an independent private batch and prints its plan path;
it never connects to a device. In a coordinated window, the designated operator
uses the `runner.py` in that private batch with `run /absolute/plan.json`. An
interrupted operation with an unreleased lease uses this same runner's `recover`
action. Each side uses the actual indexed document, the production launcher
bytes, exact app executable and a separate identical fixture directory.
The temporary bridge selects only the application home. No optimization CLI,
environment override or automatic exit deadline is supplied. The warm and
empty-cache regimes have independent comparisons and cannot be substituted.

The independent `analyze_lifecycle.py` reads these raw records on test-env.
It cross-checks the actual process arguments, executable/launcher identities,
startup, sleep and resume ownership, software input availability, P6 pixels,
cooperative exit, native catalog row, notification and restoration. The default
cases use an owned TERM request and a matching ordinary-exit acknowledgement;
they do not simulate a human tap. Only an independently checked passing result
is eligible for the selection's final `lifecycle` evidence reference.

In `aidoku-next-life-89a1be78`, the first host analysis rejected the recorded
empty output from `repair-launcher`. Inspection of the frozen runner and device
script showed that `restore_launcher()` consumes the child's recovery marker
and verifies it internally. The outer command is silent on success. Its return
code was zero, its error output was empty, the subsequent protected snapshot
matched the original byte for byte, and the token-checked release succeeded.
The host analyzer was corrected to validate the exact script/runner identities,
the actual outer command contract and those separate restoration observations.
An executable fixture verifies that a wrong marker or nonzero child status
prevents the original launcher from being restored. No physical input or output
from this batch was rewritten. Replaying all evidence passed, including both
independent physical toolbar comparisons.

For the combined local image/status candidate, `status_delivery.py attest`
binds its producer's compiler output, before/after source snapshots, full eight
file delta, suite results and exact program SHA-256. `freeze-default` accepts
only this candidate's new six-pair warm non-regression check, separate cold
six/ten-pair comparisons, and independent physical Library and right-toolbar
pixel qualification. Cases from the earlier cold-only candidate are ineligible.
The associated status qualification compares the observable Wi-Fi and battery
icons in the right 930 by 72 pixels of the Scribe status bar. It checks both
the physical crop boundaries and exact pixels; a uniform region has no usable
information. The left-side clock is excluded. Both normal defaults and the
post-installation smoke repeat this comparison before and after sleep/wake.

Once the independent defaults and native restoration are proved, use
`status_delivery.py package --default-plan /absolute/plan.json
--default-plan-sha256 DIGEST`. It replays the measured evidence, freezes both
packages and the app-only installation delta. The previous cold-only `d9be7683`
release path remains blocked by its own warm regression and is never substituted.

## Freeze accepted inputs

Use `prepare.py --selection /absolute/selection.json --source-root /absolute/source
--output /absolute/fresh-release`. Each reference in the selection document
is a `file:` URI and its SHA-256. Only the candidate(s) whose measurements and
qualification match the release are selected. The selection fields are:

- `schema`: `1`; `source_manifest_sha256`: canonical qualified source digest.
- `artifacts`: release package member names mapped to their qualified files.
- `candidates`: one entry per candidate, with `screen` and `confirm` references
  to the analyzer's six-pair and ten-pair reports, respectively.
- `qualification`: an independent image qualification result with the same
  configuration. `lifecycle`: a physical qualification record binding the
  selected artifact hashes, source manifest and the `pixels`, `input`, `exit`,
  `sleep_wake`, `notification`, `catalog_identity`, `restoration` and
  `production_defaults` checks. A flag copied from an unrelated build is not
  a physical qualification.
- A warm combination needs an additional `combination` record binding the
  resulting production composition to its own screen and confirm results.
  An empty-cache candidate cannot reuse warm evidence; mixed cold releases
  are rejected.

`prepare.py` reads both previously verified packages as the fixed starting
point, verifies the measured binaries and recomputes paired improvements.
All archive members outside the selected program changes must match the old
ZIP and KPM, including file permissions. Successful preparation creates the
two archives, source manifest, ZIP verification, a `delta/` of changed files
only, and `plan.json`. No physical lease is acquired by this step.

## Update and verify the installed application

On test-env, set `release` to the freshly prepared release directory and
`plan_sha` to its frozen `plan.json` SHA-256. Run these commands separately:

```sh
python3 -I run.py --plan "$release/plan.json" --plan-sha256 "$plan_sha" --operation install-smoke --check-only
python3 -I run.py --plan "$release/plan.json" --plan-sha256 "$plan_sha" --operation install-smoke
```

Only the designated physical operator runs the second command. It takes one
fresh lease immediately before the native preflight. Existing nonmatching files,
an active application, or inconsistent native state stop the operation. The
driver never writes the Library document, shared runtime, registration, or user
database. The application and native Library update their ordinary state through
the normal installed entry.

The baseline smoke opens the real Library item with normal user data before
any update and compares the subsequent installed application's pixels below
the clock, including after a same-process short powerd sleep/wake. It checks
native ownership, input roster, boot, brightness, existing catalog identity,
advancing last access and exactly one successful notification and exit. It
never exports real screenshots. This is a functional qualification; the
release timings come from the separate fixed, instrumented experiment.

The transaction acquires the normal app lock, backs up only selected original
bytes/modes, and switches each file atomically. A failed update or smoke first
closes the owned application and rolls the selected program files back. It
will not overwrite a third-party replacement, delete a foreign lock, or signal
an unidentified PID. Raw transport and device evidence remains on test-env in
the private run directory, and the original transaction journal remains on the
device. A report that says `recovery_needed` requires the designated operator
to finish bounded restoration with that existing lease before declaring release.

If an accepted install must later be undone, use the same frozen plan and the
original transaction ID with a **new lease**:

```sh
python3 -I run.py --plan "$release/plan.json" --plan-sha256 "$plan_sha" --operation rollback --transaction-run "$original_run"
```

This changes only journaled files that still have their selected bytes. It
does not modify the catalog registration or replay a backup of user data.

The synthetic checks are `test_prepare.py`, `test_transaction.py`,
`test_smoke.py`, and `test_run.py`. Each is independent of the physical Kindle.
