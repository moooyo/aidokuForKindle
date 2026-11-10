# Reader snapshot evaluation

The measured change reuses the existing processed PNG after checking the
canonical source fingerprint. It does not add another current-frame codec,
persistent alias, or pin owner. Same-process wake already repaints the retained
RGB scene without decoding a page.

## Measurement scope

The reference was the `ffb3417` source snapshot. The standalone release harness
compiled exact copies of `cache.rs`, `domain.rs`, `imaging.rs`,
`imaging/decode_scheduler.rs`, and `render_cache.rs` on `ssh test-env`.
Measurements used cold processes, warm operating-system page caches, serial
execution, interleaved codec order, 21 reopen samples, and 11 write samples per
configuration. They do not measure physical Kindle flash, application first
paint, battery consumption, total RSS, or a suspended process's retained frame.

The final run matches all dependency versions in the project lock and its
runtime flate2 features: flate2 1.1.10, crc32fast 1.5.4, and the zlib-rs 0.6.8
backend. The worker used rustc 1.99.1 on x86_64 Linux. The build metadata archives
the production source hashes, both lock hashes, dependency feature trees, binary
hash, and harness hash. The initial miniz-only exploratory run is retained
separately and is not the basis for the tables below.

The five cases use the bundled illustrative comic pages and the JPEG
evaluation's deterministic color-panel and gradient fixtures. Source and pixel
hashes are recorded in the raw report. The output buffers range from 1,931,456
to 4,612,800 bytes. All five codecs produced byte-identical grayscale pixels;
25 codec/case audits rejected truncation and corruption, and the raw formats
also rejected trailing compressed bytes and a different source identity.

## Additional encoding tradeoff

Median times are in milliseconds. Codec-only reopening includes file opening,
bounded metadata and identity checks, decompression, and terminal integrity
checks. It excludes shared-cache index and fingerprint checks for every codec.
The additional persistence measurement includes encoding, a full roundtrip
oracle, file synchronization, atomic rename, and directory synchronization. It
does not include a new shared-cache index transaction.

| Case | Existing PNG decode | Raw zlib level 6 decode | Raw zlib level 6 encode | Additional validated persistence | PNG bytes | Raw zlib level 6 bytes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| PW comic front | 1.784 | 0.771 | 7.610 | 15.663 | 130,149 | 99,598 |
| Scribe comic back | 5.588 | 1.777 | 24.893 | 36.256 | 524,281 | 375,391 |
| PW color panels | 1.653 | 0.871 | 15.102 | 24.780 | 172,920 | 129,380 |
| Scribe gradient | 3.193 | 2.168 | 67.790 | 80.230 | 768,181 | 524,395 |
| PW vertical window | 2.513 | 1.017 | 12.021 | 20.725 | 257,985 | 179,690 |

Raw zlib level 6 saves 0.782–3.811 ms on these reopen decodes. Encoding alone
costs 7.610–67.790 ms, and the persistent file duplicates a PNG that has already
been created. Raw level 1 also increases compressed size by approximately
51–75 percent. Four-bit packing adds a validation and unpacking pass; its
level-1 output remains larger than the existing PNG in every case. The raw
JSON contains all level-1, level-6, and packed measurements, including p95.

These results do not establish a need for a second current-frame encoding.
KOReader's serialization is close/serialization-time work and need not happen
on each foreground turn. An exit-only writer would defer the measured write
cost; this table is a per-frame cost comparison, not a claim that such a design
blocks every page turn. The actual upstream zstd codec is evaluated in a
separate supplement; the zlib results alone do not characterize zstd.
Maintaining another format would also require its own displayed-frame
publication, source and render-version proof, two-page byte budget, cleanup,
and download-owner lifecycle. The implementation therefore keeps the existing
processed artifacts and the displayed-reader checkpoint and leases.

## Existing PNG reuse

The old saved-local-path branch performed a full source-image validation before
the renderer checked its processed PNG. A valid PNG was consequently preceded
by an unnecessary source codec pass on a cold reopen. The comparison below
retains the source SHA-256, URL and header identity, complete render key, PNG
metadata, and processed-file checksum checks. A processed miss still decodes
the canonical source. The cache manager stays alive beyond the request timer;
its shutdown access flush is excluded.

This isolates the proposed reuse path, rather than measuring the desktop UI or
the entire reader job. The reader pipeline resolves an existing local path as
a size-bounded candidate without hashing or decoding it. The immediately
following renderer performs the source checksum and render-key verification.
Candidate resolution therefore adds no duplicate external-source hash before
a processed hit. Optional download-copy lookup is a separate operation and is
not included in this direct-PNG-path comparison.

The recorded cases use managed raw artifacts. They do not measure the later
mutable-external-source integrity hardening: external files are now read and
SHA-256 checked on every render request, including resident pixel hits. File
size, mtime, ctime, and file identity alone cannot prove unchanged content.
Only cache-owned managed artifacts and private staging retain their trusted
fingerprint memo. Warm OS caches therefore do not imply an I/O-free external
source path, and the timing table makes no such claim.

| Case | Eager source validation median / p95 | Fingerprint and existing PNG median / p95 |
| --- | ---: | ---: |
| PW comic front | 3.804 / 4.262 | 2.459 / 3.398 |
| Scribe comic back | 8.087 / 8.530 | 6.513 / 7.175 |
| PW color panels | 7.465 / 8.345 | 2.498 / 3.095 |
| Scribe gradient | 15.625 / 19.766 | 4.523 / 4.644 |
| PW vertical window | 4.914 / 5.018 | 3.239 / 3.301 |

The source-free cover alias API was excluded from formal timing because it
does not enforce the same reader source and navigation identity contract.
Its reduced validation was not counted as a performance improvement.

The pipeline keeps the compressed-size limit before hashing. A candidate is
not a checksum or image-validity proof: rendering verifies managed raw files
against the existing index SHA-256 and freshly hashes mutable external source
bytes. `CachedPage` remains only a candidate until rendering succeeds.
Foreground and read-ahead jobs publish its original position-sensitive page
identity after a successful render; failed read-ahead renders cannot write
their path back to the manifest. Multiple successful windows from one source
publish one path.

Nine `reader_cache_*` regression tests cover one cold source hash on the direct
PNG path without a duplicate source codec pass, a fresh external hash on
resident hits, source replacement, invalid originals, corrupt processed PNGs,
a managed checksum mismatch, missing sources, changed HTTP headers, empty and oversized files,
and successful-only prefetch publication. Invalid but size-bounded source
candidates are explicitly accepted by the resolver and rejected by rendering;
corrupt PNG derivatives are rejected before fallback to a valid source.

## Reproduction and evidence

Use `scripts/reader-snapshot-benchmark.rs` and
`scripts/benchmark-reader-snapshot.py` on the remote worker. The standalone
manifest template is `scripts/reader-snapshot-benchmark.toml`; copy it as
`Cargo.toml` into the evaluation directory, copy the project lock, and place the
five exact production module copies under `snapshot-production/` (the decoder
scheduler is placed directly in that directory). Copy the Rust harness beside
the manifest, build with `--offline --release -j6`, and supply the source and
JPEG fixture directories to the Python runner. Audit dependency versions and
the runtime feature tree before timing. Compilation and other benchmarks must
finish before collecting the serial samples.

The remote final report is
`/root/aidoku-koreader-eval-20261110-01/snapshot/results.json`. The local evidence
copy is `artifacts/koreader-evaluation-20261110-01/snapshot/results.json`, SHA-256
`106bfe6ff2371a3b12e0d7cd1b8f0835b8866a6982c87483e57977c59c5f342b`.
The neighboring `build-metadata.json`, feature trees, lock and build logs bind
the measurements to their source and dependency configuration. Exploratory
run 1 is preserved with its original harness, lock, raw data, and interpretation
notes and is explicitly superseded by the aligned final run.
