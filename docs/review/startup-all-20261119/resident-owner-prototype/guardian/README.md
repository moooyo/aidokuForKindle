# Stable Library owner and visit waiters

This is an isolated Linux prototype. No packaged launcher, stock Scriptlet,
catalog item, or app-manager registration is replaced. The research copy of
the native route observer has no qualified entry profile. Host transport tests
do not establish that the real Kindle document route can return to this GUI.

`aidoku-launch-guardian -- /absolute/worker [args...]` creates a guardian in a
separate session. Only this process waits for the original direct worker.
The initial foreground caller receives the worker's exit status as usual.
After the original worker has completed stock restoration, it may instead
send a single `S` on its inherited `AIDOKU_INITIAL_WAITER_FD` (8). The guardian
checks the kernel credentials of that packet against its unreaped direct child,
then returns success to the initial waiter. It releases both sockets and waits
on the child with only its internal exit notification pipe. It opens no device.
Catchable cancellation goes through the original private waiter channel and
waits for the worker's cleanup. Losing only the waiter does not reparent the
worker. Losing the guardian sends the worker its parent-death cleanup signal.

Later visits use `PendingLibraryWaiter`, which retains the exact Unix stream
checked by `native_library_visit::observe`. The supervisor reserves the
unused `su` process identity and confirms the route again before its final
policy checks. `A` acknowledges admission and `S` acknowledges that this
visit's display and input ownership have been restored. Neither message is
read from a visitor. A lost admitted connection cannot trigger a fallback
launch. The waiter must connect to an independently authenticated app-private
supervisor; this module exposes no command that opens or wakes the GUI.

Begin `NativeLibraryIdle` after the active worker has quiesced its backend,
closed the owned device descriptors, and confirmed Home. Keep the same gate
through all failed attempts in this idle period. It watches a BOOTTIME absolute
deadline, normally 45 seconds, a one-second memory sample, and the same
app-issued cancellation endpoint. `MemAvailable` below 48 MiB, unknown memory,
normal exit, USB, a new sleep event, or owner disconnect make the gate terminal.
The supervisor checks `before_commit` after native revalidation and immediately
before attachment. Readiness of the visit listener is never authorization.
On a terminal result the supervisor must close its listener, discard retained
GUI state, and exit its worker. The guardian waits for that cleanup; this module
does not touch system services, devices, or processes beyond its own worker.
A kernel lacking timerfd support for BOOTTIME refuses the idle path.

To reproduce, copy this directory and the adjacent `native-library-visit.rs`
research source to `test-env`, then run from the Linux working copy:

```sh
sh guardian/run-host.sh
AIDOKU_TEST_FULL_IDLE_TTL=1 sh guardian/run-host.sh
```

An alternate path to the same unqualified observer source can be passed as
the first argument. The full-deadline run takes 45 seconds and needs no device
lease. The ARM C helper can be built with the adjacent `build-launch-guardian.sh`;
the Rust harness also checks the project's ARM target. See `host-results.json`
for observed verification and scope.
