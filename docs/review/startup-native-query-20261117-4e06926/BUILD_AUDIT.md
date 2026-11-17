# Build-cache correction

Date: 2026-11-17, Asia/Shanghai.

A final diagnostic build unexpectedly had the same executable hash as the
uninstrumented native-query candidate. Inspection showed that the copied
vendored backend source had an older preserved mtime than the shared Cargo
fingerprint. Cargo had reused the prior project dependency instead of building
the intended diagnostic backend.

The audit also found diagnostic-only backend logging in the earlier
input/feedback experiment's three candidate executables. Their production
source manifests did not list those timing statements. Matching a source
manifest and a build configuration was therefore insufficient to establish
the contents of a reused dependency artifact.

| Artifact | Diagnostic backend logging |
| --- | --- |
| Input-discovery baseline, `625ca3c1...` | Absent |
| Earlier input/feedback attempt 1, `7b369333...` | Present |
| Earlier input/feedback attempt 2, `302f4ee7...` | Present |
| Earlier selected input/feedback package, `534ef996...` | Present |
| Native-query candidate, `3833d6ec...` | Absent |
| Subsequent caption-only experiment, `43deb8e9...` | Absent |

The extra statements measured durations and wrote diagnostic stderr lines;
they did not change the business logic. They nevertheless invalidate a strict
matched-instrumentation comparison and the old package's exact production-source
claim. Original binaries, measurements, source records and failed setup
evidence are retained; they are not silently edited or dropped.

## Correction

For each authoritative ARM build, `aidoku-kindle` and `slint-backend-kindle`
were explicitly cleaned in this task's own target cache before compilation.
Immutable registry dependencies were retained. Rebuild logs confirm compilation
of the vendored backend from the selected source directory. Binary checks
reject diagnostic literals in production variants and require them in the
diagnostic variant.

The clean input/feedback baseline is
`b25c0ecd052526b2f3cc975a1a345100d6c9a41375c233fe41c06212c2706290`.
Its source remains implementation commit `6b7765f` and source fingerprint
`a0dfc29f3072dbf9d1a462fe5f4959fee650b9e14cc83f539e24bbbd3c9dfeda`.
The native-query candidate rebuilt byte-identically as `3833d6ec...`.
The correctly instrumented diagnostic variant is `480fa32a...`.

Six balanced rounds then remeasured the input-discovery baseline, the clean
input/feedback baseline, and the native-query candidate in the same batch.
The corrected small-change first-SEND median is 3.250 to 3.145 seconds;
the native-query change further improves it to 2.445 seconds. These results
supersede the old input/feedback and provisional native-query comparison.
The caption-only follow-up used two binaries without diagnostic logging and
remains a separate valid experiment; it was not adopted.

Use the native-query package identified in [delivery.json](delivery.json).
The earlier input/feedback ZIP is retained as historical evidence, not the
recommended distribution. No daily-use installation was replaced by either
the earlier candidate or this experiment.

Evidence: [artifact audit](build-cache-audit.json),
[clean identities](clean-build-identities.json),
[final comparison](clean-comparison.json),
[baseline clean](baseline-clean-clean.log),
[baseline rebuild](baseline-clean-rebuild.log),
[candidate clean](candidate-clean-clean.log), and
[candidate rebuild](candidate-clean-rebuild.log).
