# Performance and immediate-wake review

This is the historical Candidate5 audit that motivated Phase 3. References to
current behavior and remaining costs describe that audit snapshot. See
[the Phase 3 verification report](PHASE3_VERIFICATION.md) for implementation and
measurement status on the current Candidate7.

Reviewed on 2026-11-06, Asia/Shanghai. This is a read-only audit of the delivered
Candidate5 sources and existing evidence. No application, test, build or runtime
probe was executed for this review. The proposed gains below are not new measured
results, and the delivered package is unchanged.

## Current wake behavior

Normal powerd sleep retains the actual process, registered fonts, Slint images
and canonical RGB buffer. The supervisor resumes that process automatically;
the user does not need to launch KUAL or reopen the application. Existing native
evidence records the same PID, one font initialization, unchanged image-job
counts and the restored Reader page. A lost sleeping process uses automatic
`--resume` recovery with a new PID. A complete operating-system reboot has no
automatic startup service in this package.

That result is different from proving that the first illuminated physical frame
is the comic, without a transient stock Home or screensaver image. The existing
captures are taken after readiness acknowledgement. They establish the restored
state, not continuous first-frame timing. The current sleep path returns display
ownership to stock powerd; it does not install the comic as the stock sleep image.

See [native evidence and boundaries](SUSPEND_RESUME.md).

## Highest-priority wake improvements

1. In `packaging/kindle/launch.sh:237-246`, the power-state query starts and then
   waits one unconditional second before examining the result. A fast successful
   query should return immediately, with a bounded timeout for a slow or hung
   command. Preserve active-state, USB, PID, birth and epoch checks.
2. `src/app/lifecycle.rs:76` still calls `sync_view` before the backend resumes.
   Reader projection performs three or four indexed SQL reads and additional
   allocations/counts. Restore only retained scene state from RAM before the
   first frame; defer database-derived metadata until after that frame or an
   explicit menu request. Keep correct menu/modal/busy/layout projection rather
   than simply deleting the synchronization call.
3. The native backend forces a complete Slint scene render before rewriting RGB
   (`vendor/slint-backend-kindle/src/platform.rs:477-530`). A stable pure Reader
   frame could use an explicitly eligible retained-RGB fast path. Geometry,
   output color mode and pending repairs must match. A previous frame containing
   menus cannot be copied as if it were the pure comic.
4. Audit stock repaint and synchronous helper/statusbar operations around display
   acquisition. Quick-cancel paths can request `xrefresh`; its visible effect has
   not been measured. Normal sleep and explicit exit still need correct stock
   ownership and restoration.

The software `resume-ready` acknowledgement currently follows full RGB writing
and GC16 submission. It does not await physical waveform completion, and selected
ioctl errors are not propagated into that counter. A future latency measurement
must distinguish power input, backlight illumination, software submission and
the first physically visible comic frame. Do not promise zero delay from the
current counters or isolated screenshots.

## Remaining reading, cache and download costs

| Priority | Observed code path | Candidate improvement |
| --- | --- | --- |
| P1 | Processed PNG hits still read/decode PNG; prefetched `GrayImage` values are discarded; UI publication allocates RGB. | Retain a small byte-budgeted decoded-frame cache, reuse prefetched pixels, and reduce copies. Keep memory limits appropriate to the target device. |
| P1 | Foreground results only send an MPSC message and await the next 50 ms application poll. | Wake the UI immediately for a coalesced foreground Reader completion; keep background work throttled. Do not increase global polling to trade latency for idle power. |
| P1 | Display, prefetch and callbacks repeatedly deserialize complete chapter manifests. A changed page path can delete/reinsert all chapter pages. | Cache immutable manifests and use identity-checked reads/updates by chapter and position. Preserve source/epoch/generation validation. |
| P1 | Download progress commits count mapped paths with JSON extraction over all chapter pages, normally after two pages or 100 ms. | Maintain mapped counts in the same transaction, with complete validation at completion. Repeated whole-chapter scans can approach quadratic cumulative work for long chapters. |
| P1 | Non-Reader routes still load all books/history and visit chapters for every library book. Normal Scribe library download-size projection also visits completed jobs, all pages and file metadata. | Route-specific projections, SQL pagination/aggregation and incrementally invalidated download-byte totals. Demo fixtures bypass part of this real offline-library path. |
| P1 | Cover misses still perform synchronous decode/resize/quantization on the UI thread, including a processed file prepared by a worker. | Return ready pixels from background work and keep bounded reusable textures. |
| P2 | Dirty LRU hints can cause a separate FULL transaction before artifact/pin publication; directory sync occurs under the manager lock. | Merge noncritical hint updates into the publication transaction where safe; measure actual flushes and lock waits. Retain raw-file and pin durability boundaries. |
| P2 | New vertical windows recalculate geometry and render from the original; large PNG crop paths can scan all rows repeatedly. | Reuse content-fingerprint-based crop/geometry information and combine passes without unbounded bitmap retention. |
| P2 | Repeated batch selections can attempt unchanged chapter/job writes, recount whole-book unread state and emit one event per job. | Skip equal values, recount only relevant changes and coalesce batch notification. DML attempts do not necessarily imply physical fsync. |

An additional concrete prefetch mismatch exists for a final unpaired page in
landscape spread mode: interactive rendering uses single-page width, while
prefetch always uses the spread width. Use one render-configuration function for
both paths so the last-page processed key matches.

Relevant entry points are `src/app.rs:709,1294,1389,1477,1727`,
`src/app/view.rs:59,1120,1205`, `src/render_cache.rs:127,185,407`,
`src/store.rs:338,902,956,1024`, and `src/cache.rs:1188`.

## Measurement and implementation order

First remove fixed wake waiting and pre-frame database work, and measure the
separate wake milestones. Then measure cached/uncached page transitions at
32/128/512-page chapter sizes, decoded-byte allocations, PNG decode counts,
SQLite VM steps, foreground callback p95 and RSS. Include final unpaired spread
pages and several new vertical windows. Follow with a large real offline-library
projection workload; the existing demo screenshots do not cover its file walk.

Preserve the current two-background-plus-reserved-foreground admission policy
unless measurements support a change. More decoder threads can multiply memory
and lock contention. Preserve `DELETE/FULL`, final-file validation, raw/pin
publication, and forced sleep/exit persistence barriers. Optimize redundant work
before weakening durability or increasing concurrency.

The earlier [cache scaling](CACHE_PERFORMANCE.md),
[Reader metadata](READER_PERFORMANCE.md) and
[download measurements](DOWNLOAD_PERFORMANCE.md) retain their original scope.
In particular, approximately 0.167 ms describes a metadata collector, not total
page-turn, menu, wake or physical Kindle latency.
