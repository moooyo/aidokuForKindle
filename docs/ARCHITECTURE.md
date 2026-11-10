# Architecture

## Application and rendering

The application reuses the sibling WeRead project's technical direction: Rust
2024, Slint 1.17.1, the Slint software renderer, bundled fonts, a separate native
Kindle backend, SQLite, remote Linux builds, and deterministic captures. It has
its own package, application state, data directory, launcher, and verification
evidence. The WeRead executable or service is not a dependency.

| Area | Main implementation |
| --- | --- |
| CLI, font registration, action replay, capture | `src/main.rs` |
| UI routes and shared presentation models | `ui/app.slint`, `ui/state.slint` |
| Design tokens and reusable controls | `ui/theme.slint`, `ui/widgets.slint` |
| Intent dispatch and view synchronization | `src/app.rs`, `src/app/actions.rs`, `src/app/view.rs` |
| Persistent domain objects and SQLite | `src/domain.rs`, `src/store.rs` |
| Local comic import | `src/local.rs` |
| Reader state machine and image processing | `src/reader.rs`, `src/imaging.rs` |
| Shared raw/processed storage and rendering cache | `src/cache.rs`, `src/render_cache.rs` |
| Network providers and interpreted sources | `src/sources/` |
| Shared transfer admission and request coalescing | `src/sources/transfer.rs`, `src/sources/fetch.rs` |
| Persistent download worker | `src/downloads.rs` |
| Offline keyboard and Pinyin candidates | `src/input.rs`, `assets/pinyin/` |
| Local-network configuration/upload service | `src/pairing.rs` |
| Device geometry, refresh, lighting | `src/platform.rs`, `vendor/slint-backend-kindle/` |
| Kindle lifecycle and distribution | `packaging/`, `scripts/build-kindle.sh` |

Slint callbacks emit `(section, action, value)` intents. Rust owns navigation,
library mutations, reader state, and long-running work. Provider calls and image
operations return owned results through channels; UI callbacks do not perform
provider requests. A weak Slint window handle prevents work from keeping an
already closed window alive. Presentation models are derived from persisted
records rather than treated as the source of truth.

Image-bearing models are projected only for the visible route. A reader state
update does not decode hidden library, catalog, search, or history covers. The
UI texture cache uses `(book ID, resolved path)` keys, keeps distinct context
editions, and is capped at 64 textures. Background cover/download changes are
merged on a 300 ms UI cadence; direct interaction state remains responsive.

The desktop feature selects winit and the software renderer. The Kindle feature
selects the vendored framebuffer/evdev backend without the desktop feature.
The ARM target is `armv7-unknown-linux-musleabihf`, cross-compiled with
`cargo zigbuild`. Font outlines are embedded as gzip data and registered at
startup: Noto Sans SC 400/500/700, Noto Serif SC 900, and Literata 400/600/700.

## Identity and persistence

Book and chapter identities include their source. `compound_id` uses a
length-prefixed source identifier so that punctuation in a remote key cannot
create an ambiguous concatenation. Reading the same title from two providers
therefore creates distinct records and progress.

SQLite stores books, chapters, page requests, progress, bookmarks, source
configuration, preferences, and download jobs. Foreign keys preserve ownership;
transactions protect compound changes. The application and cache databases use
rollback `journal_mode=DELETE`, `synchronous=FULL`, `mmap_size=0`, and a busy
timeout. This avoids a WAL shared-memory/mapped-I/O dependency on Kindle userstore
filesystems. These settings are a durability policy, not proof against every
storage fault or forced process termination.
Imported content, source state, cached images, and download payloads live beneath
the selected application data directory. Unix database and newly created data
directory permissions restrict access to the application user.

Progress is one-based and records the actual chapter, page, total, completion,
and timestamp. The reader state machine controls direction, tap regions, menu
gating, chapter transitions, previewed page numbers inside the menu, and refresh
requests. A progress-slider preview changes the displayed number; decoding waits
for release. Vertical movement advances 85% of the viewport, retaining 15%
context. Download completion requires the saved pages to validate; a queued or
interrupted job is not presented as an offline chapter.

The closed reader contains only the comic image across the full runtime canvas.
Contain fitting preserves the artwork's aspect ratio; no toolbar, page footer,
bookmark corner, spread gutter, or background notification reserves space.
Menus appear only after an explicit center tap, top swipe, or hold. Legacy
footer preferences remain readable for data compatibility but add no overlay or
hidden footer touch region. Reader projection returns before library/history
enumeration and uses three indexed metadata queries, or four on a progress miss.
Ordinary page progress changes update one row; unread counts are recomputed only
when completion/read status changes.

Reader menus and page actions project the accepted image session while another
page is pending. A first image shows static loading feedback. Current image or
chapter failures preserve the accepted frame and open a recovery panel with an
exact retry destination and a return action. Cross-chapter labels identify both
chapters, including equal-number special chapters; safe reason categories avoid
putting raw URLs or credentials into the UI or session checkpoint. Stale failures
cannot replace that state. Successful retry returns to the image-only canvas.

`--demo` selects a separate data directory and seeds it once. Later demo actions
persist, allowing restart tests without repeatedly resetting preferences or
history. Its artwork and catalog are fixtures from the supplied design, not
provider responses. The normal application starts with a real, empty library.
Bundled demo page artwork is refreshed atomically when its bytes change, without
resetting the demo database, bookmarks, preferences, or reading progress.

## Local imports and images

`LocalLibrary` imports image directories and CBZ/ZIP archives, sorts page names
naturally, reads bounded ComicInfo metadata, and commits a managed content cache.
Archive paths, symlinks, entry sizes, and aggregate limits are checked before
publication. The importer does not use arbitrary archive paths as output paths.
Direct archive import permits at most 2 GiB and 20,000 entries; the separate LAN
upload endpoint has a smaller 32 MiB request limit.

Image processing composites alpha onto white, crops white margins when enabled,
fits the viewport, adjusts contrast, and quantizes to sixteen gray levels with
optional dithering. The reader normally preloads two following image frames,
including bounded next-chapter and vertical source-image transitions. Wide-page
mode prepares only the closest half-page to preserve its cache budget. It does not
decode an unrestricted full-size bitmap simply because a provider supplies one.
The reusable cover-texture cache is capped at 64 entries; history loads cover
textures only for its visible page. Cover identity includes the resolved file
path so catalog/search editions and user-selected replacement covers cannot
reuse stale artwork solely because their manga IDs match.

`render_cache` persists eight-bit grayscale PNG derivatives. Its key combines the
page request identity and original content fingerprint with viewport width and
height, crop, dithering, contrast, vertical mode/offset, and normalized zoom.
In-band PNG metadata stores the full scaled strip height and viewport height, so
a warm vertical-page hit restores geometry without rescanning the original.
Identical cold renders are coalesced. A missing or corrupt derivative is rebuilt
from the original; if publishing the derivative fails, the already rendered
pixels are still returned for reading.

| Boundary | Current limit |
| --- | --- |
| Compressed page | 32 MiB |
| Standard decoder source image | 16 x 1024 x 1024 pixels |
| Standard decoder allocation budget | 96 MiB |
| Streaming noninterlaced PNG source | 256 x 1024 x 1024 pixels |
| Streaming PNG dimensions | At most 16,384 wide and 1,000,000 high |
| Requested output side | At most 8,192 pixels |

Large noninterlaced PNG pages use a row decoder and retain only the data needed
for the target output/vertical window. Validation reads through the final PNG
chunks. Oversized interlaced PNGs and oversized JPEG/GIF/WebP pages are rejected
with an instruction to split or convert them; they are not silently fully
decoded. The pixel constants are binary limits, approximately 16.8 and 268.4
million pixels respectively.

## Shared cache and download scheduling

`cache/shared-v1` contains the canonical raw files, disposable processed PNGs,
temporary writes, and a persistent SQLite index. Reader, prefetch, cover, ZIP
entry, and download paths consume the returned canonical artifact path. The
compatibility `download_page` API returns this path rather than populating a
second caller-specific destination. Request identity includes URL and headers;
different authentication/header contexts do not share one response accidentally.

The volatile, unpinned cache has a default 256 MiB LRU budget and a 64 MiB
free-space reserve. This is not a cap on all application data: imported content,
externally supplied files, and pinned downloads can occupy additional space.
Download owners pin their validated raw artifacts; active readers/renderers use
leases while consuming artifacts. Normal eviction and cache clearing skip pins,
leases, and writes in progress. Persisted aliases let managed cover artifacts be
resolved after restart.

Repeated same-owner pins use an indexed existence check and perform no mutation
or transaction. Cache byte totals are maintained incrementally. Clear operations
delete at most 32 entries per transaction, then rejoin the FIFO state-lock queue;
already waiting readers, pins and commits run before the next batch. Each batch
rechecks entry generation, pins, leases and active reservations. The current
displayed image keeps both raw and processed leases, including during sleep.

Reservation checks run before and during writes. Responses and archive entries
are copied in 64 KiB chunks into generated `.part` files, bounded by the source
size limits. Completion validates the artifact, synchronizes its bytes, publishes
the generated leaf atomically, and records the committed artifact. Final image
validation can use additional bounded decode buffers; 64 KiB is the copy-buffer
size, not a claim about total process memory. Partial or invalid bytes do not
become a completed download merely because a file was created.

The shared transfer scheduler admits at most two background page transfers and
reserves a third capacity slot for interactive reading. Source and HTTP-origin
parallelism/interval policies both apply to image/archive admission. Singleflight
coalesces an identical in-progress request across independent source managers.
An interactive waiter can promote a queued background flight, including its
dependent archive transfer; it does not preempt a response already in flight.
API, repository, and image clients remain separated by redirect policy while
their process-wide client pools are reused across source-manager instances.

`DownloadWorker::enqueue_many` validates and deduplicates a chapter batch before
the controller persists it. The controller owns its SQLite connection, reuses
source clients, fetches up to two pages concurrently, and batches progress writes
instead of updating every network chunk. Duplicate enqueue does not implicitly
resume a paused job. Individual and global controls use the same controller and
persisted job states. Pause stops new admission while already active fetches
drain; cancellation releases its download ownership after active work drains.
Resume reuses validated saved pages. Other owners' completed files and pins, and
unrelated external files, are not removed by cancelling another job.

The detail view enters chapter selection through a 500 ms hold; a hold suppresses
the following ordinary open action. Selection controls replace the existing
directory-header area, leaving the ordinary design unchanged. Presets cover the
next five/ten chapters, unread chapters, and all chapters. Download management
uses eight panel rows with SQL `LIMIT`/`OFFSET` for the visible job slice; global
controls occupy rows in that panel instead of materializing the whole queue.

Pins and leases protect against this application's normal eviction policy. They
cannot stop an external process or user from removing a file, guarantee a free
space reserve against unrelated writers, or make every storage/kill sequence
safe. Missing or changed originals must be revalidated and, when possible,
fetched again. A derivative remains optional when the valid original is present.

## Sources

The built-in MangaDex, Komga, Kavita, and Suwayomi adapters implement actual
provider catalog, search, detail, chapter, page, and cover requests. Configuration
keeps server URLs, credentials, and provider-specific options separate from
cached library objects. Reverse-proxy path prefixes are retained. Authenticated
API redirects cannot cross origins; cross-origin image redirects remove private
headers. Public repository requests use a separate HTTP client.

Repository indexes accept current objects and legacy array indexes. `.aix`
installation validates identity, version, paths, lengths, and the WebAssembly
header, then publishes an immutable payload directory. The runtime uses Wasmi's
interpreter, which does not require an ARM JIT. Current Aidoku exports exchange
Postcard buffers following the MIT-licensed `aidoku-rs` contract. The separately
licensed Swift AidokuRunner is not copied or linked.

Supported source services include standard buffers/date functions, HTTP,
persistent typed preferences, read-only HTML/CSS queries, static/dynamic catalog
listings, image-request resolution, and bounded image entries inside ZIP files.
Source-declared settings are mapped to typed application controls. Legacy ABIs,
JavaScript, canvas, browser WebViews/challenges, DOM mutation, and text-only pages
return explicit capability errors. A successfully installed package can still
invoke one of these unsupported paths later.

Interpreter execution and option updates share a lock. A source receives bounded
guest memory, host resources, buffers, fuel, and operation duration. Current
limits include 64 MiB guest memory, 80 million fuel units per call, 16 MiB
individual buffers/HTTP bodies, and a 45-second host-operation deadline. A fuel
unit is an interpreter accounting unit, not a millisecond or performance claim.

## Platform and lifecycle

Design profiles describe logical geometry and capabilities. Native geometry is
read from the visible framebuffer, and touch coordinates account for the 2x
physical scale. Profile availability does not establish that a physical model
or firmware is supported. Lighting and sensor controls follow declared device
capabilities, including hiding front-light controls for the no-front-light
Scribe profile.

UI/partial requests use the detected driver's safe AUTO waveform on dirty
regions; fast feedback uses DU, and full refresh uses flashing GC16. Page turns
upgrade to full refresh at the configured interval. This is a conservative
backend mapping, not a claim that every panel exposes the exact waveform named
in the design. Night mode currently uses software inversion and a full refresh.
Orientation is manual; sensor-driven automatic rotation is not connected.

The shell launcher acquires an Aidoku-specific lock, pauses supported stock UI
resources, starts the app, and restores resources through exit/signal traps.
Powerd sleep first stops new dispatch, checkpoints the visible navigation/page,
quiesces the idle worker, and releases native input and framebuffer ownership.
The supervisor then retains the process with SIGSTOP while stock sleep owns the
display. Confirmed wake resumes that PID, drains stale touch input, rewrites the
entire retained RGB scene and submits flashing GC16. It does not initialize fonts,
run status subprocesses or decode an unchanged page before that repaint. Pending
mode changes are repaired asynchronously only after the retained frame is drawn.
UI timer epochs discard delayed taps, holds and seek releases from before sleep.

Nonce/PID/sleep epochs reject stale acknowledgements. A lost retained process
during this supervisor's own sleep transition can fall back to `--resume` using
the durable route, filter, pagination, selection and reader checkpoint. Pairing
tokens, keyboard input and source credentials are excluded from that checkpoint;
incognito reading restores only in RAM. Download pause ownership distinguishes
automatic sleep pauses from explicit user pauses and network readiness gates
automatic restart. Source manager epochs reject old HTTP work while cached and
local files remain readable. USB takeover and explicit exit never relaunch.

Input activity is coalesced by one bounded background worker at intervals of at
least 20 seconds. Only actual active downloads with keep-awake enabled request
periodic idle resets. Observing status or painting does not keep the device awake.
The worker's pause acknowledgement follows the exit of any active LIPC child.
On orderly application exit,
the UI event loop ends before waiting for a bounded worker-stop acknowledgement.
The acknowledgement follows fetch-guard teardown and closure of the worker's
SQLite connection. The app allows two seconds for it; the launcher allows up to
five seconds after TERM before enforcing its shutdown bound. A timeout is not a
successful acknowledgement, and a forced SIGKILL cannot run shell cleanup traps.

## Pairing and remaining integrations

The pairing service is started on demand, binds an ephemeral local-network port,
and exposes a 15-minute session. The QR flow uses a high-entropy token; manual
pairing uses the displayed code with attempt limits. Requests enforce their
expected authority/origin, JSON/upload sizes, upload counts, and session expiry.
Source configuration and imports are sent back to the application as events.
The service uses HTTP on the local network rather than a public relay.

Configured AniList/MyAnimeList accounts use explicit book association, completed
chapter updates, and a persistent retry queue. Companion accepts legitimate
tokens or the user's own OAuth client configuration. No account authorization
is inferred from installation. Migration matches chapters and preserves supported
local state, while conservatively resetting source-specific page positions.
It does not copy tracking bindings between provider identities.

Scheduled updates and opt-in advance downloads run on background workers with
network/charging prerequisites, cancellation fences and persistent retry state.
Metadata backups, bounded local logs and optional prepared download images are
described in [the follow-up record](READING_FOLLOWUP.md). Physical-device and
real-account acceptance retain their separate verification requirements.
