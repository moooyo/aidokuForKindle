# Project agent instructions

## Verification

Run builds, tests, validation and runtime probes through `ssh test-env`.
Do not run them on the Windows workstation without explicit authorization for
the current task. Keep Cargo build concurrency at six.

## Shared physical Kindle

Acquire the shared Kindle lease only immediately before executing actual device
commands. Prepare code, build artifacts, fixtures and host tools before acquiring
it. One designated operator owns all device access for the current batch.

Hold the lease only for a continuous device-operation batch and its necessary
bounded restoration checks. Finish device operations, restore owned native
state, and release with owner/token checks immediately. Perform host analysis,
report writing, tool debugging and waits for other tasks outside the lease.
Do not reserve the device while waiting for a build, another task or an idle
manual step. End the owned session and release before such a wait.

Use a fresh lease identity and token for each later batch. Preserve original
state, operation outcomes and release evidence. Never take over another task's
lease or reuse a released token. Keep credentials, raw tokens and private device
identifiers out of committed sources and public evidence.
