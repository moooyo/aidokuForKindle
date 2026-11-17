# Sequential startup optimization results

**The retained changes reduce the first Library submission from 2.822 to
2.254 seconds and the settled visible-cover submission from 6.153 to 2.677
seconds in the final six-round comparison. All six pairs improve both endpoints.**

The five application/launcher items are implemented. The sixth, shared dispatch,
was experimentally assessed without replacing the production shared handler.
The [item-by-item record](PROGRESS.md) includes unsuccessful experiments.

## Final comparison

Batch: `final-integration-fde4063e`. Both variants use the normal native
Scriptlet service, the same Scribe boot, and the same isolated offline copy of
the real library and existing cover artifacts. The control preserves the
starting version's behavior with common frame instrumentation and serial
preparation. The candidate is the exact executable in the verified final ZIP.
One warmup each precedes six predetermined AB/BA rounds. Four later qualification
runs are excluded from performance statistics.

| Endpoint from native launch request | Starting behavior | Retained implementation | Saving |
| --- | ---: | ---: | ---: |
| Software ready | 2.603 s | 2.035 s | 568 ms / 21.8% |
| First Library accepted frame | 2.822 s | 2.254 s | 568 ms / 20.1% |
| Library software input readiness | 2.822 s | 2.254 s | 568 ms / 20.1% |
| Visible-cover settled accepted frame | 6.153 s | 2.677 s | 3.476 s / 56.5% |

Candidate first-frame range: 2.228-2.664 s. Complete-cover range:
2.640-3.113 s. Every measured run contains all seven visible image slots for
six unique books, with no pending cover publication or cover failures.
These are warm, offline software/driver endpoints. Physical taps, network
downloads, and optical e-ink completion are not measured. Capture encoding,
observation dwell, and shutdown are outside the endpoints.

The [complete dataset](final-integration.json) retains all 18 launches.
Stage medians below are independent experiments and must not be added to
predict the overall saving.

## Retained decisions

| Order | Change and measured effect | Decision |
| --- | --- | --- |
| 1 | Start byte/metadata preparation after the first full SEND, overlapping the unchanged checked completion barrier; 109 ms earlier first Library submission in its A/B batch | Retained |
| 2 | Admit cover workers after the first business frame; first render 322 to 202 ms, first Library 134 ms earlier | Retained |
| 3 | Reuse derivatives after fresh source checksum validation; complete covers 2.466 s earlier. Then use coalesced completion wakes and the existing 300 ms initial publication interval; a further independently measured 589 ms | Both retained |
| 4 | Fold sleep-module observation into negotiated AQ2 startup calibration; first Library 125 ms earlier. A standalone-helper trial did not improve its target interval | Combined version retained; standalone trial rejected |
| 5 | Apply changed brightness/warmth through one bounded helper command; attachment median 26 ms lower, with matching native value readback | Retained as a small improvement |
| 6 | Direct diagnostic retains timestamp/scan requests but omits framework/privilege integration; first Library 465 ms earlier, scanner IPC about 6 ms | Assessment complete; diagnostic is not an equivalent production Library entry |

The native refresh completion, portrait consistency, touch synchronization,
sleep/wake ownership, cache identity, malformed-response rejection, and
unsupported-helper fallbacks remain enforced. The logo, document entry,
scanner policy, recent-item behavior, and shared handler are unchanged.

## Where the remaining time goes

The [final cost map](final-cost-map.json) uses disjoint intervals within each
candidate run. Medians of separate intervals do not necessarily sum to the
median total. Metadata work overlaps the initial display barrier.

| Interval | Median |
| --- | ---: |
| Native dispatch to document entry | 345 ms |
| Document entry through launcher supervision, calibration, display takeover, and Rust entry | 797 ms |
| Platform initialization | 65 ms |
| Backend setup | 91 ms |
| Window/bootstrap setup | 89 ms |
| Initial native frame setup and submission | 105 ms |
| Checked initial full-frame/input barrier | 364 ms |
| Remaining font registration and application attachment | 111 ms |
| Initial view synchronization and readiness | 78 ms |
| First Library rendering, buffer settling, writing, and submission | 223 ms |
| Visible covers after first Library submission | 423 ms |

The clearest remaining measured opportunity is the platform entry/supervision
layer. The 465 ms direct-launch difference demonstrates a useful experimental
boundary, not a deployable shared-handler optimization. It includes a different
framework and scheduling context. Removing the scanner request alone cannot
claim that saving. Presenting prepared covers in the first business frame is
another possible direction, but no additional saving is established here.

## Verification and limitations

- 130 successful native launches across the item batches and final integration.
  The first serial warmup in item 1 censored its cover endpoint; it remains in
  the evidence and was never counted as a complete-cover performance sample.
- Scribe and PW5 software captures and real pinyin replays were pixel-identical
  in the four scheduling/cache comparisons. Slow metadata retained feedback;
  cancellation did not become ready.
- The final baseline, candidate, older-helper and missing-helper qualification
  runs all completed. Their actual framebuffer pixels below the live status
  bar exactly match each other and their Slint snapshots, without rotation or
  polarity normalization. Brightness and warmth match unchanged preferences.
  See [raw framebuffer and compatibility evidence](final-native-qualification.json).
- Exact committed-source tests passed 814 library tests, 22 startup tests and
  95 example tests, with five pre-existing ignored tests. Eleven helper tests
  passed. The retained launcher passed 109 packaging/lifecycle tests; the
  unchanged backend passed 190 unit tests. The final package passed 187 checks.
- One existing sleep-screen UI integration test fails on the night-theme cover
  bounds assertion. It reproduces identically on the starting `f56dd36` version,
  with identical UI and test hashes. Earlier experiment source snapshots omitted
  `tests/`; the final complete checkout exposed this existing failure. It is not
  hidden by changing the assertion or the UI. See
  [baseline reproduction](preexisting-ui-test.json).
- The standalone vendored documentation example also lacks its illustrative
  fonts/generated UI and cannot compile independently. Its passing unit tests
  are reported separately from that documentation example.

All verification ran on `ssh test-env`, with six Cargo jobs. Each device batch
used a fresh shared lease and restored native state and lighting before release.
No credentials, lease tokens, user book identities, or private device identifiers
are included in the public evidence.

## Delivery identity

Compiled production source: `fc51eac0625565052fb3fd0ef7c1f117705ecba5`.
Subsequent report-only commits do not change production inputs.

| Artifact | SHA-256 |
| --- | --- |
| Source manifest | `99f7c65e7c683c2109ed86b3082cb6caacf0949cb1c613233b4e6144ce787f37` |
| Application | `570887a0e5603ce2f6d820cf87b2f4e9b385514f71e9a729e85c8ead2f6729fc` |
| Native helper | `84c2f27d8233d8695001aa86d880e29454fe188deb9884aca1c6694c82a5887d` |
| Launcher | `130413e24459e6ab55cad21c3e7a4b92c5f24be477ab51485417f83119baec79` |
| Development ZIP | `13c15d555997a2b8733570e50982778967c68f58c559f97e6f923f5a3d12f6f5` |

The application and vendored backend were force-rebuilt from the frozen Git
archive. Build settings match the controls (`LTO=false`, 16 codegen units,
six jobs). Diagnostic-only backend logging is absent. The exact final binary
was sampled; no earlier executable is substituted based only on source hashes.
See [build identity](final-build.json), [source manifest](final-source-manifest.json),
and [package verification](final-package-verification.json).

Daily installation deployment evidence is recorded after the verified package
is installed. The original program files are retained in an owned rollback backup.
