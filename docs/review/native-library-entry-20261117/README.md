# Formal Library entry lifetime

**The retained change improves first Library submission from 3.916 to 3.316
seconds and complete visible-cover submission from 4.831 to 4.088 seconds in
the final six-round, stabilized comparison. All six pairs improve both endpoints.**

There is one Aidoku Library item. Its existing title, official-logo cover,
document path, MIME association, scanner request and recent-access behavior
are retained. The final implementation installs no native entry daemon,
interposition library, alternate application registration, or shared-handler
replacement. Earlier prototypes are preserved under [prototype/](prototype/README.md)
and are not part of the application bundle.

## Implementation

Both ZIP and KPM Library documents now invoke `launch.sh --library`. That
entry creates the same independently supervised worker/session as before, but
waits for its actual exit. The shared Scriptlet handler consequently remains
active while Aidoku runs and returns to Library after Aidoku's worker restores
native display/input state. The old entry returned immediately after spawning
the worker, so shared-handler completion and Library reactivation could compete
with the application's startup.

The waiting entry does not own the framebuffer, input, sleep protocol or
launcher lock. Those remain with the independent worker. A worker exit status
is preserved. Catchable cancellation of the entry requests worker cleanup;
retirement of only the small waiting process does not kill the independent
native recovery process. KUAL and direct package launch retain detached
admission. The application executable, helper, GUI and caches are unchanged.

## Measurements

Authoritative stabilized batch: `library-settled-ab-9bd22240`. Both variants
open the real `/mnt/us/documents/Aidoku.sh` through the system's native file-URI
dispatch. The shared handler and its registration are unchanged. The same ELF,
native helper, launcher, Scribe boot and isolated warm offline library are used.
The baseline selects detached admission; the candidate selects `--library`.
One warmup each precedes six predetermined AB/BA rounds. Each request follows
eight seconds of native-screen stabilization, excluded from its measured
interval. Separate qualification runs capture pixels and read native values.

| Endpoint from the open request | Detached entry | Library lifetime entry | Saving |
| --- | ---: | ---: | ---: |
| Software ready | 3.398 s | 2.870 s | 528 ms |
| First Library accepted frame | 3.916 s | 3.316 s | 600 ms / 15.3% |
| Library software input readiness | 3.916 s | 3.316 s | 600 ms |
| Visible-cover settled accepted frame | 4.831 s | 4.088 s | 744 ms / 15.4% |

All six first-frame and complete-cover pairs improve. Candidate ranges are
3.162-3.794 s for first Library and 3.891-4.533 s for complete covers. All
measured runs have seven visible image slots for six unique books, zero cover
failures, and no pending cover publication at the settled endpoint.
See [every observation](settled-comparison.json).

The preceding rapid-repeat comparison also improves the medians by 532 ms
and 510 ms, but only three of six direct pairs improve amid large alternating
system-work variation. It is retained in [rapid-comparison.json](rapid-comparison.json).
The two-pair pilot is retained in [lifetime-pilot.json](lifetime-pilot.json).
No outliers or failed experiments were removed from the evidence.

These are warm, offline driver-submission and software-input boundaries, not
optical panel completion or physical finger-to-photon timing. Unlike the prior
startup-series temporary script outside `documents`, this comparison exercises
the actual indexed Library document and its metadata updates. The earlier
2.25/2.68 s figures are therefore not a directly comparable absolute baseline.
The current same-batch before/after comparison establishes this change's gain.

## Correctness

- 115 remote packaging/lifecycle tests pass, including waiting for the real
  worker status, same-process sleep/wake, and retirement of the waiting entry
  while the independent worker retains recovery responsibility.
- Actual framebuffer pixels match the control and the Slint snapshot below
  the live status bar. Every measured case keeps a single catalog item, the
  original MIME and thumbnail, and updated modification/recent-access times.
  See [entry qualification](entry-qualification.json).
- The final physical screen-saver qualification is
  `library-lifecycle-4b403d14`. Both variants park and resume the same application
  PID, acknowledge their retained frame, reproduce the pre-sleep framebuffer
  below the status bar and shut down normally. The candidate's active framework
  application remains the shared Scriptlet during the cycle; the control has
  already returned to the native home application. See
  [lifecycle qualification](lifecycle-qualification.json).
- A local six-second scheduled wake bounds the screen-saver test. Extended
  deep suspend is outside this qualification. Initial lifecycle attempts are
  retained: one automatic-exit window expired during post-resume inspection;
  another showed ordinary cover-loading differences because the initial
  fixture was not fully warmed. The final run verifies that covers were settled
  before suspend. No application or recovery regression was inferred from the
  invalid comparisons.
- All tests, builds and probes run on `ssh test-env`. Device batches use fresh
  shared leases, restore the original entry and native state, and release
  before analysis. The failed timing-window batch needed explicit owned
  recovery; its recovery and release proofs remain private.

The unchanged Rust application retains the previously documented baseline
night-theme sleep-screen assertion failure. This entry-only change does not
modify that test or the interface to hide it.

## Rejected approaches

1. A separate native application-manager bridge passed 11 host tests and its
   explicit native handshake, but filename-level application associations did
   not replace the default shared MIME handler. Waiting for native unload before
   releasing the application also introduced delay. Temporary registration was
   removed and the original document restored.
2. A version-qualified shared-executable wrapper passed 14 host tests and a real
   routing smoke check. It retained scanning and privilege transition while
   waking the shared wait early and skipping a redundant xrefresh for opted-in
   Aidoku. The first pilot was inconclusive; the fixed-registration six-round
   comparison did not improve startup. It is not retained. See
   [pilot](rejected-hooks-pilot.json) and
   [fixed-registration comparison](rejected-hooks-comparison.json).

The final change is confined to Aidoku's document and launcher lifetime.
The shared executable remains SHA-256
`2b3db59ed7856b3f5026e3837f7ef07fc9bb6bc1c042274b48b3cd3fc2e04abc`.
Application: `570887a0e5603ce2f6d820cf87b2f4e9b385514f71e9a729e85c8ead2f6729fc`.
Native helper: `84c2f27d8233d8695001aa86d880e29454fe188deb9884aca1c6694c82a5887d`.
Measured launcher: `1ae50f6bd7a8262cce185dea08c69f5bc317ed9b7bd7945c55c1f5239dcdd385`.

Build/package identities and deployment evidence accompany the final delivery.
