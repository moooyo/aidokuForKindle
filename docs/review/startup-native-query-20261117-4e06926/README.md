# Native-query optimization and remaining startup investigation

Date: 2026-11-17, Asia/Shanghai.

**The retained native-query change improves first Library submission by 700 ms
in the final clean comparison.** Software readiness improves by 550 ms. All
six balanced rounds favor the candidate. A separate follow-up that delays the
short data-stage caption does not establish a useful first-frame improvement
and is not adopted.

Implementation commit: `4e069260fa612e465b8fde8730596a0c60e0261f`, on
`codex/startup-native-query`. It includes the earlier input/label change
`6b7765f`; main remains a separate integration decision. The daily installation
was not replaced during these experiments.

## Authoritative comparison and build correction

The final comparison uses three production variants with no diagnostic backend
logging. Each has one warmup, followed by six rounds covering every ordering
once. They use the same Scribe boot, isolated offline library snapshot,
production launcher, compiler and release settings. Three additional timing-only
launches run after the primary measurements and are excluded from them.

| Variant | Software-ready median | First Library SEND median |
| --- | ---: | ---: |
| Input-discovery main, `4ce1fd1` | 2.860 s | 3.250 s |
| Clean input/label candidate, `6b7765f` | 2.750 s | 3.145 s |
| Native-query candidate, `4e06926` | **2.200 s** | **2.445 s** |

Relative to the clean `6b7765f` baseline, native queries improve readiness by
550 ms / 20.0% and first Library SEND by 700 ms / 22.3%. Relative to `4ce1fd1`,
the combined improvement is 660 ms and 805 ms respectively. The corrected
small input/label change alone saves 110 ms and 105 ms in this comparison.
Each comparison favors the newer variant in all six rounds.

| Round | Input-discovery SEND | Clean small-change SEND | Native-query SEND |
| --- | ---: | ---: | ---: |
| 1 | 3.160 s | 2.840 s | 2.380 s |
| 2 | 3.790 s | 3.430 s | 2.450 s |
| 3 | 3.180 s | 3.150 s | 2.450 s |
| 4 | 3.260 s | 3.200 s | 2.440 s |
| 5 | 3.320 s | 3.140 s | 2.500 s |
| 6 | 3.240 s | 2.950 s | 2.330 s |

The native-query candidate's measured first-SEND range is 2.330-2.500 s.
Its mean is 2.425 s, versus 3.118 s for the clean small-change baseline.
These are warm software/driver observations, not optical panel completion or
completion of every background cover image.

During final instrumentation work, an audit found that preserved source mtimes
and a shared Cargo target directory had allowed an older diagnostic vendored
backend to remain in the earlier small-change executables. Production source
manifests alone did not catch that stale dependency. The old measurements are
retained, but the final comparison above supersedes their performance claims.
Both project-owned packages were force-cleaned before each new ARM build;
diagnostic literals are explicitly checked in every binary. The native-query
candidate rebuilt to the exact same hash as before. See
[the build audit](BUILD_AUDIT.md) and [clean build identities](clean-build-identities.json).

[clean-comparison.json](clean-comparison.json) is the authoritative dataset:
18 measured production launches, three production warmups, and three separate
diagnostic launches. It includes all observations, query-session counts,
frequency snapshots, native refresh parameters and input-node checks.

## What changed

The old portrait transaction launched six separate `lipc-get-prop` processes.
The first UI status snapshot launched five more property queries. Each process
loaded firmware libraries, established its client state and then closed it.

A small `aidoku-native-query` process now opens one firmware LIPC connection
per bounded transaction. Its requests are eight fixed, read-only opcodes;
neither arbitrary property names nor setters are accepted. Rust preserves the
original ordering of the portrait queries, including the framebuffer/sensor
observations between the before/after checks. The first UI snapshot uses a
separate short-lived session.

The Rust parent checks framing, output limits, UTF-8, cancellation and deadlines.
It requires clean EOF and successful termination before publishing a portrait
record or trusting a full UI snapshot. A malformed connected portrait session
does not retry or publish cached orientation. Missing helpers, incompatible
firmware ABIs and unavailable initial connections use the legacy command path
before any query has been sent. A failed UI transport discards its partial
snapshot before using the legacy observation path.

The helper is 5,680 bytes and uses the firmware ARM hard-float glibc ABI. The
main application and JPEG worker remain static. No firmware libraries are
redistributed. Real-device ABI inspection and eight successful read-only calls
qualified the bridge on this Scribe; other ABIs retain the fallback.

Implementation: [transport](../../../src/native_query.rs),
[firmware bridge](../../../src/native_query/worker.c),
[portrait transaction](../../../src/native_presentation/portrait_writer.rs),
[status snapshot](../../../src/platform.rs), and
[helper build](../../../scripts/build-native-query.sh).

## Where the improvement occurs

| Interval | Clean small-change baseline | Native-query candidate |
| --- | ---: | ---: |
| Native request to application exec request | 1.555 s | 1.035 s |
| Launcher portrait-calibration interval | 740 ms | 270 ms |
| First view synchronization | 187 ms | 68 ms |

Every candidate calibration trace records one session, six property replies
and a successful session finish. Every first-view trace records one session,
five replies and a successful finish. An additional real-device launch with
the helper deliberately absent succeeds through the legacy path. The candidate
binary in that compatibility trial is byte-identical to the forced rebuild.

The original launcher, ownership checks, input barriers, fresh pose checks,
brightness setters, and sleep/wake authorization flow remain in place. The
optimization changes how repeated native reads are transported.

## Further experiments and remaining opportunities

### Data-stage caption: tested, not adopted

The follow-up preserves initial feedback and font timing but only publishes a
brief data-stage label when an existing activity pulse is due. Both variants
already contain the native-query helper. Five independent AB/BA pairs give:

| Endpoint | Helper baseline | Caption experiment |
| --- | ---: | ---: |
| Software ready | 2.300 s | 2.110 s |
| First Library SEND | 2.520 s | 2.500 s |

Metadata-ready to attachment falls from about 152 ms to 0.1 ms, and pre-ready
updates fall from three to two. However, the ready-to-content interval grows;
the whole first-SEND median improves by only 20 ms. This does not justify the
additional behavior change. It remains an isolated experiment, not part of
the implementation commit. All slow-preparation, cancellation, paired-profile
pixel checks and 12 native launches passed. See
[the full experiment](caption-experiment/startup-ab.json),
[its exact diff](caption-experiment/source.patch), and
[host verification](caption-experiment/desktop/verification.json).

### Current cost map

These intervals are independent summaries and can overlap. They must not be
added as a forecast of removable latency.

| Remaining component | Current observation | Conclusion |
| --- | ---: | --- |
| Entire pre-exec path | 1.035 s median | Now much smaller; several independent native operations remain |
| Native request to Scriptlet entry | 305 ms median | Outside the application's own startup loop; no isolated saving established |
| Optional sleep-cover capability observation | 135 ms median | Candidate for separate startup scheduling or query consolidation |
| Portrait calibration | 270 ms median | Fresh observation and supervision remain; not all of this is overhead |
| Native display takeover | 100 ms median | Preserve confirmed ownership before framebuffer writes |
| Application/backend/window setup | Roughly 0.30 s | Further detailed profiling is needed before choosing an implementation |
| Initial scene interval | 458 ms median | Includes input setup and the checked full-frame boundary |
| Initial full-frame completion barrier | 337-364 ms in two new diagnostic samples | Do not remove; asynchronous completion/overlapped preparation is a larger backend project |
| Resource preparation window | 246 ms median | Fonts and metadata overlap display work; caption removal alone did not deliver this as a saving |
| Fonts | 63 ms median | Low priority after build-time expansion |
| Application metadata | 132 ms median | Potential overlap work, not an independently additive delay |
| Attachment, including lighting application | 69 ms median | Small bounded target; setters were not changed |
| First view synchronization | 68 ms median | Repeated native-query process cost has largely been removed |
| Ready to first Library SEND | 240 ms median | Predominantly software rendering in the diagnostic samples |
| First Library software rendering | 184-186 ms in two diagnostic samples | A future layout/text/pixel profile could identify a concrete rendering optimization |
| First Library buffer-settle wait | 24-29 ms in those samples | Not a large independent target after the retained changes |
| First Library pixel write / submission | About 16 ms / 8 ms | Lower priority |

The largest remaining architectural opportunity is to let independent resource
work proceed while the first checked display update completes. That requires
an asynchronous completion boundary with preserved input blocking, pause/wake,
rotation and buffer-ownership semantics. No numerical saving is claimed for
that unimplemented design. Likewise, a fast path that presents actual Library
content in the first full frame would need a bounded slow-start fallback and
unchanged readiness definitions; it is not equivalent to deleting a loading
frame or relabeling a timing marker.

### Native entry review

The pinned [Scriptlet handler](https://github.com/KindleModding/sh_integration/blob/adb7ef5c570a25c76fd64e94837ae0f590bd3b9c/launcher/main.c)
requests a full scanner pass before forking the application. Its one-second
polling sleep appears after that fork, so it is not a one-second pre-launch
delay. The 305 ms observed dispatch interval includes native dispatch and
handler work; this study does not isolate scanner cost. Changing the shared
handler would also affect recent-item/scanner behavior and other applications.
No shared handler, scanner policy, CPU governor or display waveform was changed.

CPU frequencies were sampled read-only. They varied at readiness and reached
2 GHz at first-content observations. These snapshots are not a controlled
frequency experiment and do not establish the cause of every timing variation.

## Verification and delivery

- 806 core tests passed, with five pre-existing ignored tests.
- 22 startup tests, six C-helper protocol tests, six KPM tests and four ELF/package
  contract tests passed: 844 executed tests in these suites.
- 187 final package checks passed. The static-main contract remains unchanged;
  only the query helper may use the checked firmware interpreter.
- All 24 launches in the final clean batch completed. The three primary variants
  use the same input nodes, and final Library pixels match below the top 100
  physical status pixels. Full private-library frames remain private.
- Every acquired device lease was restored and released. Original installed
  application, launcher, cover, entry, library and lighting were preserved.
- All builds, tests, validation and device access used test-env; Cargo jobs were six.

See [verification](verification.json), [frame checks](clean-native-frame-checks.json),
[release proof](clean-lease-release.json), [source verification](committed-source-verification.json),
[build identity](candidate-build.json), [package checks](package-verification-clean.json),
and [delivery identity](delivery.json).

Application SHA-256:
`3833d6ec12c2462c29278fdca5a3451e0c62d55358de39c3aed3ce1b6a3a0bb9`.
Query-helper SHA-256:
`b91c2de2d9d1ca1ae7806d0ee6cc78f0f9a3d8a84feae081255d0fcbdb105e16`.
Production source SHA-256:
`4e7194efa8efcba76645052a27e87559d9c127b2e7bab260d737504ffbd82b7b`.

The current candidate is ready for review as a development package. This record
does not newly qualify physical typing, rotation, sleep/wake, USB takeover,
cold boots, network startup, optical refresh quality or other physical models.
