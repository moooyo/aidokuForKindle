# Prepare imported Library thumbnails while idle

`import_thumbnail=idle` schedules optional work after the existing local importer
has committed a book, its chapters and its pages. `off` disables this queue; it
does not disable regular visible-cover loading. The setting is read with the
other per-launch startup options. Directory, pairing and `--import` entry points
all use the existing `App::import_local` path. The file importer supports CBZ,
ZIP, image directories and directories of these books. It does not import PDFs.

Import already opens the Library, so the existing visible-cover worker normally
prepares the books displayed on the first page even with the option off. The new
worker handles the other recently imported books. It will only consider a
currently stored, still-in-library local book whose `000001` source belongs to
its immutable import revision. Only that first page is prepared. Reimport of the
same ID replaces queued work for an older revision. There are at most 32 pending
requests and one active worker. Pending requests expire after 90 seconds; they
are neither persisted nor retried indefinitely.

User interaction defers admission for at least 500 ms and cancels an active
preparation. Visible work, the Reader, modals, foreground tasks and active
downloads take precedence; publication of the visible view starts a new quiet
interval. Sleep, exit, source invalidation and metadata restoration cancel the
queue. Cache clearing waits on the worker's publication guard outside the UI
thread before clearing data. This prevents a cancelled import from recreating a
thumbnail after a user's clear operation. Sleep and shutdown cannot acknowledge
a drained import operation until its worker has finished. Cancellation uses the
existing decoder checkpoints; a library codec call already in progress is not
forcibly interrupted.

Thumbnail generation uses the same mutable-source proof, 376 x 514 render key,
accelerator options, bounded codec, quantization, PNG validation, alias and atomic
commit as the visible Library. It makes no network requests and retains no new
decoded images in the process-wide LRU. The worker checks capacity at admission
and at publication, leaving a 1 MiB margin beyond current reservations and the
configured minimum free space. Optional failures leave the original imported
book and normal visible loading intact. The durable derivative is disposable;
normal cache eviction can require later regeneration.

The [synthetic physical workload](../bench/import_probe/README.md) uses hidden
first-appearance titles and two independent application processes. Report user-
visible import and cover timings separately from added idle worker elapsed time
and persistent bytes. A Library open that hits a file produced earlier is a shift
in when work happens, not evidence that import consumes less CPU or power.

| Trace event | Boundary |
| --- | --- |
| `import_started` | UI request handed to the normal importer |
| `import_library_published` | Successful store writes completed; the Library is selected |
| `import_visible_covers_settled` | First driver-accepted frame with all actual slots of the same visible selection filled; tracing must be enabled |
| `import_thumbnail_started` | The single background worker starts this request |
| `import_thumbnail_persisted` / `import_thumbnail_cache_hit` | New or existing valid indexed derivative, with the expected alias |
| `import_thumbnail_cancelled` / `import_thumbnail_skipped` | The optional worker did not report a retained success |
| `import_thumbnail_queue_drained` | A previously nonempty batch has no pending or active work |

`build-host.sh` runs the focused tests inside an isolated Cargo overlay on
`test-env`, with six Cargo jobs and the shared build lock. Three behavior tests
exercise queue bounds and deduplication, cancellation/clear ordering, and a
real import followed by a persisted-cache hit, known pixels and alias. The latter
also covers low capacity, cancellation, and a book removed from the Library.
The same remote binary passed 49 render-cache checks (two ignored) and 23
visible-cover job checks. [Host evidence](host-verification.json).

A separate [two-process headless PW5 comparison](native-host-bench/README.md)
imported nine synthetic books and first displayed one previously hidden cover
after a full process restart. All eight `idle` opens reused a disk thumbnail;
all eight `off` opens created it on demand, with identical 343 x 514 RGB pixels.
Import's first-page model was ready at about 309-310 ms on both sides. Finishing
all optional work moved from about 309-310 to 820-821 ms, including the 500 ms
quiet interval. With a settled first page and a later page turn, the target's
model publication improved from 11.6 to 3.2 ms. With an immediate turn against
the existing publication interval, both models took about 286 ms despite faster
worker completion. These are Linux software measurements, not Kindle timings.
