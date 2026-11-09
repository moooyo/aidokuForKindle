# Phase 3 performance results

Candidate18 formal acceptance passed on the frozen 236-file Source42b: one
actual exact ignored library test with twenty measured JSONL records, 21 primary
timing processes, six independent SQL collections, one observer and one marked
publication/sync audit. Source, both ELFs, all four tools and wrapper identities
were unchanged before/after. The Native owner checked-paused/restored its
dedicated VM, with actual resource/load conditions recorded; no whole-test-env
idle or control of other chats is claimed.

The [formal manifest](../artifacts/phase3/candidate18/performance-formal-01/documents/formal-01/formal-run-manifest.json)
is 9,627 bytes, SHA-256
`e1dcb9c492e54aae23e853009dd99ed934243f49a73d436e0ef1016bd518fb72`;
the [usage manifest](../artifacts/phase3/candidate18/performance-formal-01/documents/formal-01/usage/usage-run-manifest.json)
is 6,207 bytes,
`3e24d573268cd51253b9889a5993aac356dea6c8fc0c37b13ea90457bc09d98f`.
The actual example is 4,964,616 bytes /
`321e09e18783054988af408d11c16050935ec705c753af7548531377502599b7`;
the library-test ELF is 21,267,112 bytes /
`840cb015697cd6a6aa23eec7ec28f012241a47a0b3607b4cabe7989618239038`.
The original locked/release/no-default-features, features[] and actual --lib
artifact provenance are preserved. BUILD/pure qualification is separate from
these actual executed samples; original OOM/preparation failures remain raw.

## Actual selected measurements

Numbers below are median trial p95 unless labeled otherwise; traced SQL timing
is excluded. See [compact statistics](../artifacts/phase3/candidate18/performance-formal-01/documents/formal-01/actual-compact-statistics.json)
and [focused summary](../artifacts/phase3/candidate18/performance-formal-01/documents/formal-01/focused/summary.json).

| Workload | Actual result | Interpretation |
| --- | --- | --- |
| 1k / 5k usage rebuild, five samples each | scan medians 6.537436 / 29.999183 ms | Rebuild work scales with fixture size. |
| Append eight completed items | scan medians 0.734161 / 0.370580 ms | Eight chapter/window/check operations; both snapshot identities retained. |
| 512-page window vs simulated full-manifest strategy | 9.859 vs 106.558 microseconds; SQL VM 41 vs 2,570 | Algorithm comparison in the new Store, not a C5 binary speedup. |
| 32-page small fixture | worst 31.740 vs 14.250 microseconds; median p95 8.430 vs 9.689 | The window has a retained worst-sample counterexample; not universally faster. |
| Prefetched JPEG / PNG memory hit | 51.258 / 56.249 microseconds | Processed decode and grayscale output allocation counters were zero, not all allocator/RSS work. |
| 5k visible-eight / visible-eighteen projection | 5.996829 / 5.474102 ms | Metadata eight/eighteen but materialized book documents sixteen/thirty-six; aggregate/sort SQL still scales with N. |
| Dirty-LRU publication | 4.919217 ms | Independent SQL scope one transaction/three DML; marked audit 21 commits,126 successful fsync,zero fdatasync. |

The render measurement process maxRSS was 52,944 KiB, not the whole App memory
budget. Samples exclude Slint, covers, physical flash/input latency and the
selected-source catalog's O(S) refs/O(S log S) sort path. The resident render
cache's 12 MiB/sixteen entries excludes live Reader arcs/RSS; ordinary channels,
deferred queues and origin history have no claimed count bound. Typed busy retry
is limited to implemented dequeue/checkpoint/ownership/wake paths, not all writes.

## Actual archive return and history

The original formal raw has 24,801 files / 347,884,256 bytes. Its complete bundle
adds post-run stats/control for 24,811 members. The 70,352,377-byte archive /
`ef25d4087aeb9c61890f68fa1cf08f15d77f21bd77329f1d2c6993fc7c524c07`
was ordinarily copied locally and the exact archive returned to the worker,
where every member name/bytes/SHA matched. Only eighteen exact report documents
were locally extracted; no expanded 25k-fixture tree comparison is claimed.
The return receipt is 1,397 bytes /
`da1ab160b38ade1bddb5eac8cfdf7f001f00b8240f31c6c0317a89a994a49fed`;
final RELEASE is 2,637 bytes /
`a3669b9d6a7b3220e00416fe47d6b4165c2be34c2dbfcea93c3c73fa415e8068`.

The [original performance checkpoint](../artifacts/phase3/candidate15/documentation-staging/current-documents-before-final-01/docs/PHASE3_PERFORMANCE_RESULTS.md)
preserves C11/C14/C15 source identities, measurements and pending statements.
C11's original counterexample remains its own result, alongside the fresh C18
counterexample above. No previous candidate's success or failure is overwritten.
