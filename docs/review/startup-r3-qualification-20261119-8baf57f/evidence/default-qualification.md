# Default startup timing and independent pixels

The continuation replayed the original R3 warm-cache and empty-thumbnail-cache
timing batches, each containing six predetermined pairs, with new independent
native pixel batches on `test-env`. The original timing samples were not rerun
or modified. The immutable
`analyze_v3.py` accepted all frozen program, launcher instrumentation, payload,
case, restoration, and release evidence.

| Regime | Complete pairs | Usable Library paired saving | Complete covers paired saving | Default guard | Independent pixels |
| --- | ---: | ---: | ---: | --- | --- |
| [Warm cache](default-warm-with-pixels.json) | 6 | 101.088 ms (4/6 faster) | 161.602 ms (5/6 faster) | Passed | Passed |
| [Empty thumbnail cache](default-cold-with-pixels.json) | 6 | -9.358 ms (3/6 faster) | 48.212 ms (4/6 faster) | Passed | Passed |

The default guard permits at most 50 ms median paired regression on each
startup endpoint. The separately reported exit-to-native-Home paired saving
was 440.661 ms for warm cache, with four of six favorable pairs; it was
-115.261 ms for empty thumbnail cache, with three of six favorable pairs.
Negative savings mean the candidate took longer. All pairs, including the
slow fifth cold pair, remain in the public summaries. These balanced pairs
provide a default protection result, not a confirmed isolated or aggregate speedup.

The independent images are 1860 by 2480. Their complete body below the top
72 pixels matched. The right status-bar region at x=930, y=0, width=930,
height=72 was observable, nonuniform, and identical. These are digital
framebuffer comparisons; they do not measure optical e-ink completion.

Each timing batch's 14 stages and each independent pixel batch's four stages
passed all retained guards: success, released lease, same boot, restored
native ownership, restored entry, unchanged protected installation/user state,
and unchanged input roster. A [separate host replay](pixel-restoration.json)
of both pixel batches' receipts found 16 recorded operations with zero failures
per batch. Both owned staging directories were removed; both owner/token-checked
releases and connection shutdowns were complete. The replay compared the exact
`before.stdout` and `after.stdout` bytes and exported the boolean fields from
`report.json` and `lease-released.json`. No device access was performed by the
reporting agents.

The complete paired reports remain on `test-env` under
`/root/aidoku-r3-handoff-20261119/reports/`, named
`default-warm-with-pixels.json` and `default-cold-with-pixels.json`.
Each checked public export records its report SHA-256, fixed numeric timings, public
program hashes, geometry, and boolean outcomes. It excludes private fixture
identities, per-book information, user pixels, transport records, and tokens.
[The export script](summarize_default.py) ran on `test-env` and does not access
the device or private raw captures. The original historical exporter and
analyzers were not modified.
