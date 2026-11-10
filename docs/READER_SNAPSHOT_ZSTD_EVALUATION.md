# Host zstd supplement for reader snapshots

This supplements the preserved [zlib and packed-data evaluation](READER_SNAPSHOT_EVALUATION.md).
That earlier experiment did not include KOReader's actual codec and must not
be treated as a complete evaluation of its compression technique.

## Upstream policy and write timing

KOReader's [TileCacheItem.dump](https://github.com/koreader/koreader/blob/master/frontend/document/tilecacheitem.lua)
selects the zstd codec. Its [DocCache.serialize](https://github.com/koreader/koreader/blob/master/frontend/document/doccache.lua)
selects a persistent item for the current document, skips hinted items when
choosing the displayed page, and can avoid rewriting an existing cache file.
[Persist](https://github.com/koreader/koreader/blob/master/frontend/persist.lua)
performs serialization and file synchronization.

A displayed-frame snapshot can therefore be written at closing or another
serialization boundary. It does not have to add a foreground write to each page
turn. The earlier zlib encode/write measurements describe the cost of one
snapshot; multiplying that cost by every page turn would assume a different
policy. This supplement measures encoding and independent synchronized writing
separately and makes no such assumption.

## Host implementation and qualification

The optional `host-zstd` feature exists only in the standalone scripts harness.
It loads the preexisting system `libzstd.so.1` with `dlopen`; there is no package
installation or new production dependency. The loaded implementation is zstd
1.5.7, version number 10507, at
`/usr/lib/x86_64-linux-gnu/libzstd.so.1.5.7`. Its SHA-256 is
`27f07c9a49c2c956bcfb64cd4712976586a66facbf15fc7f09bc37413b5f2b21`.

The raw level-1 and level-3 frames enable content size and frame checksum.
The decoder requires the complete source/render identity, exact bounded output
length, a single standard frame, its content checksum, and no trailing data.
Output is capped at 12 MiB and the compressed envelope at the existing 32 MiB
artifact limit. This models a guarded raw-pixel representation, rather than
reproducing KOReader's LuaJIT table serialization byte for byte.

All 15 codec/case oracles passed, including 10 zstd cases. Negative checks
reject truncation, corruption, missing or incorrect frame checksum, short or
excess advertised output, concatenated frames, trailing bytes, and changed
source or render identity. The five source and pixel hashes and all five PNG
byte sizes exactly match the preserved reference report. Production source
copies, dependency versions, runtime flate2 features, binary, harness, header,
and system-library hashes are recorded in the supplementary build metadata.

The release run used `ssh test-env`, cold processes, warm OS page caches,
interleaved serial codec order, 21 decode samples, 11 encode-only samples, and
11 independent sync-write samples per case/codec. There were no concurrent
builds, host UI runs, or bulk uploads during timing. This is an x86_64 host
experiment, not a physical Kindle or full-application startup measurement.

Library initialization has an explicit boundary: first `dlopen(RTLD_NOW)`,
symbol resolution, `dladdr`, and version inspection happen before the codec
timer and are recorded separately for each fresh process. Context allocation
and release, buffer allocation, frame checks, checksum verification, and codec
work remain inside the timer. Median library initialization was 0.071–0.076 ms.
The cold figures below add each sample's initialization to its codec time;
they do not merely add two independently calculated medians.

## Decode results

Times are median / p95 in milliseconds. The PNG column and zstd codec timers
include file opening/reading, metadata identity and bounds checks, output
allocation, decompression, and terminal integrity checks. Shared-cache index
and source/artifact SHA-256 checks are excluded for every codec. The raw report
also retains zstd timings before library initialization is added.

| Case | Existing PNG | Zstd level 1, including initialization | Zstd level 3, including initialization |
| --- | ---: | ---: | ---: |
| PW comic front | 1.814 / 2.362 | 1.041 / 1.550 | 1.038 / 1.207 |
| Scribe comic back | 5.591 / 5.899 | 2.574 / 3.109 | 2.564 / 3.031 |
| PW color panels | 1.683 / 1.752 | 1.685 / 2.029 | 1.585 / 2.301 |
| Scribe gradient | 3.231 / 3.787 | 5.149 / 6.460 | 4.509 / 6.141 |
| PW vertical window | 2.527 / 3.106 | 1.475 / 1.805 | 1.440 / 1.701 |

Zstd has a clear median and p95 benefit on the comic front, comic back, and
vertical window. The color-panel result crosses over: level 3 saves 0.098 ms
at the median but has a worse p95. The gradient is slower with both levels;
level 3 adds 1.278 ms at the median. The largest initialization-inclusive median
gain is 3.027 ms on the Scribe comic back.

## Per-snapshot encoding and independent synchronization

Encoding includes context creation/release and the complete framed envelope;
its pixel-exact roundtrip oracle runs after the encoder timer. Independent
sync-write reads and verifies the already encoded file before timing only file
writing, file sync, atomic rename, and directory sync. This uses the project's
atomic-publication boundary, rather than duplicating KOReader's exact file
writer. These are separate measurements, not observed end-to-end close latency.
The columns below contain medians in milliseconds; p95 remains in the raw JSON.

| Case | Zstd 1 encode | Zstd 3 encode | PNG sync-write | Zstd 1 sync-write | Zstd 3 sync-write |
| --- | ---: | ---: | ---: | ---: | ---: |
| PW comic front | 1.290 | 1.642 | 4.865 | 5.060 | 5.183 |
| Scribe comic back | 4.526 | 5.099 | 5.482 | 5.411 | 5.821 |
| PW color panels | 2.114 | 2.089 | 5.076 | 5.122 | 5.086 |
| Scribe gradient | 7.421 | 7.139 | 5.556 | 5.816 | 5.660 |
| PW vertical window | 2.265 | 2.632 | 5.206 | 4.984 | 5.236 |

| Case | Existing PNG bytes | Zstd 1 bytes | Zstd 3 bytes |
| --- | ---: | ---: | ---: |
| PW comic front | 130,149 | 103,957 | 104,100 |
| Scribe comic back | 524,281 | 398,489 | 397,300 |
| PW color panels | 172,920 | 175,986 | 170,579 |
| Scribe gradient | 768,181 | 690,764 | 683,146 |
| PW vertical window | 257,985 | 193,026 | 195,137 |

The measured close-time candidate is inexpensive to encode: 1.290–7.421 ms
across the two levels. Independent synchronization costs 4.984–5.821 ms on this
worker. Those costs do not justify rejecting a close-time policy as though it
were necessarily foreground work on every turn. A new snapshot would still
be an additional representation while the existing processed PNG remains.

## Decision and limits

Zstd is a viable candidate, not an ineffective codec. This source revision
keeps the existing PNG path and does not add a default current-frame codec:
three cases show a clear read benefit, one is mixed, and one regresses. The
largest observed benefit is a few milliseconds in an isolated codec path.
There is no strict process-start-to-image-ready measurement establishing that
PNG decompression controls the application's first displayed frame. Capture
delay and `exit-after-ms` are not substitutes for that measurement.

The decision is therefore to defer a new default representation pending an
actual startup profile, rather than to reject zstd because encoding or closing
is too expensive. Same-process wake already reuses the retained RGB scene.
Any later adoption still needs to preserve the displayed-session anchor,
source/configuration and render-version proof, two-page byte budget, and cache
clear/download-owner cleanup. This experiment qualifies neither that lifecycle
nor the availability and behavior of a zstd implementation on physical Kindle
firmware.

## Reproduction and preserved evidence

The harness is `scripts/reader-snapshot-benchmark.rs` with the optional module
`scripts/reader-snapshot-benchmark-zstd.rs`. Build the isolated evaluation with
`--offline --release -j6 --features host-zstd`. Its archived manifest names the
binary `reader-snapshot-zstd-evaluation` so the earlier binary remains intact.
The generic repository template retains its original package/bin name while
declaring the same optional feature and source paths.

Use `scripts/benchmark-reader-snapshot.py --zstd-supplement` with
`--reference-report` pointing to the preserved zlib JSON and a different
`--output` path. `--qualify-only` performs setup, audit, and reference matching
without collecting timing samples. The runner records 315 decode, 165
encode-only, and 165 independent sync-write samples.

The supplementary remote report is
`/root/aidoku-koreader-eval-20261110-01/snapshot/zstd-supplement/results.json`.
Its local evidence copy is
`artifacts/koreader-evaluation-20261110-01/snapshot/zstd-supplement/results.json`,
SHA-256 `18ff4a35b783eb79e39d19c9092e9ca4a1ac02c9a9895efc8d985b2900af2391`.
Neighboring metadata, feature tree, lock, qualification and build logs bind the
result to the exact inputs. The earlier zlib report remains unchanged at
SHA-256 `106bfe6ff2371a3b12e0d7cd1b8f0835b8866a6982c87483e57977c59c5f342b`;
its aligned Rust harness is also archived separately.
