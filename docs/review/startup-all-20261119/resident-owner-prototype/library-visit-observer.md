# Native Library visit observation

The installed entry cannot authenticate a new visit for a retained GUI. On
2026-11-19 the device operator opened the normal indexed Aidoku item twice and
sampled the allowlisted roles on admission and immediately before each ordinary
exit. [All four observations](native-entry-live-results.json) found the expected
worker and waiter, a known manager and an active shared Scriptlet. The waiters
were distinct. None of the four observations authenticated the canonical
`su` command, exact handler or the required direct chain. All four contained an
unclassified process. Its identity was not investigated or added to the allowlist.

This is enough to recognize two ordinary admissions, but not to bind either
new native activation to the exact document for the purpose of returning input
and display to an older GUI. It does not show that the upstream handler never
ran; it shows that this candidate cannot authenticate the native visit when the
application needs the evidence. **Same-GUI reentry stays disabled for this
installed route.** The original admission starts a new GUI as usual. A cover
keeper may return cached pixels to that new process and has no display authority.
The original Library document and shared registration were preserved; stock
state and lighting were restored, and the device lease was released. There was
no on-device reentry attempt or associated performance measurement.

A future product integration needs a short-lived, single-use capability from
the authoritative point that receives the native open and knows the canonical
document. It must arrive through a supported, reviewed release of that entry
interface, bind that exact activation and document to the intended resident
owner/GUI generation, and be invalidated if the waiter cancels or the visit
expires. The receiver must verify its issuer and consume the grant once before
permitting a fresh native calibration and the separate display takeover. New
frame completion and an input snapshot still precede interaction. A process
ID or birth, package flag, static notification value, shared foreground-app
identity or helper-generated claim is not such a capability. This round did
not modify the shared runtime or register a new authority.

The [pinned upstream handler](https://github.com/KindleModding/sh_integration/blob/adb7ef5c570a25c76fd64e94837ae0f590bd3b9c/launcher/main.c)
receives a LIPC `go` request with a request ID and document URI. It resolves the
script path, updates access and requests a scanner refresh, then forks the
privilege-transition child. For the canonical Aidoku document the invoked
command is `su -c 'sh -l "/mnt/us/documents/Aidoku.sh"'`. The request ID is used
in the reply to appmgrd; it is not passed to the document. The shared `activeApp`
identifier describes the handler, not the document or request.

The document then execs the package launcher with `--library`. The existing
optional catalog hook is the only project component that currently checks the
document before interpreter entry. It passes a static helper path and digest
only after a successful quiet update. Those values identify a notification
implementation, not a particular native visit. Each accepted launch keeps its
waiter until the worker has released display ownership, attempted the deferred
catalog notification or recovery, and exited. The original handler then retires
the Scriptlet and requests its native repaint.

The [app-side observer](native-library-visit.rs) has no qualified production
route and remains in this research directory. Its input is the accepted local
socket. A future route must prove the peer's executable, exact
arguments and process birth, follow a fixed chain of live direct parents through
a `su` process that still has the full canonical command, and link that process
to the pinned shared handler and app manager. It verifies the canonical document
and rechecks the full chain after reading the files. A consumed `su` PID and birth
must be rejected by the supervisor. The observer never grants display admission;
the new presentation must still obtain its own native calibration and own the
display before drawing or accepting input.

The existing device evidence for the 2026-11-17 formal-entry batches and the
2026-11-18 catalog delivery and lifecycle batches does not include living `su`
argv, executable identity and direct ancestry. The latter lifecycle captures
store app and worker argv and the app/runner/worker identities only. Source shows
the arguments at privilege-transition exec, but does not show whether that
process remains alive or retains them throughout a visit. If it replaces its
image, disagrees with a qualified profile, or is no longer an ancestor, the
observer cannot admit that visit. The cold launch remains available.

In a future, independently qualified route, keep the initial fully authenticated file
handles and identities for the active visit. Later checks should confirm those
identities and live process links and revoke on changes, without hashing each
executable again at every prepare and commit stage. No startup cost was measured
for the currently disabled observer.

Remote Linux fixture verification ran with a non-root UID and no new privileges.
Four production-launcher fixture cases passed: ordered notification after
restoration, nonzero application status preservation, notification after waiter
retirement, and waiting for the actual worker exit. A separate private Unix
socket test completed two unprivileged client visits with one supervisor PID,
distinct client PID/birth pairs, kernel peer credentials, and restoration then
settlement before each client retired. The observer's two standalone Rust
tests passed, and the same source passed an ARM musl cross-check. It rejected
both an unqualified connected peer and a disconnected socket. These fixtures
did not exercise a native visit, real catalog notification, Kindle calibration
or display takeover.
