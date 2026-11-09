# Source runtime

`SourceManager` is a blocking service. Call it from a background worker, then
send owned domain models back to the UI thread. Constructing a manager does not
make a network request. Local comics are managed by `LocalLibrary` and the
persistent application store.

`search_page` and `browse_page` return `SourcePage { books, has_next }`; callers
must keep requesting the next page while it is available. The original
`search` and `browse` methods remain compatibility wrappers that return only
the books. Current WASM packages retain their actual `has_next_page` value;
built-in providers use server pagination metadata where available.

## Built-in providers

| Kind | Configuration |
| --- | --- |
| `mangadex` | Public MangaDex API; optional `options.chapter_languages` array and `options.uploads_url`. |
| `komga` | Server URL, username/password or `token` as `X-API-Key`; optional `options.library_id`. |
| `kavita` | Server URL, username/password, bearer `token`, or `options.api_key`. |
| `suwayomi` | Server URL and `options.source_id` for an installed extension; token/login or `options.auth_mode = "basic_auth"`. |

Reverse proxy path prefixes are preserved. Search, catalog, chapter, page, and
image requests use actual provider APIs. HTTP errors and browser challenges are
reported to the application. No built-in provider supplies placeholder results.

Use `cover_page` to resolve cover authentication, then `fetch_page_for_source`
with the book's source ID to obtain a validated canonical image path. Image redirects remove all source headers except public image
negotiation headers when crossing an origin. Authenticated API requests reject
cross-origin redirects. Anonymous repository downloads use a separate client.

## Shared transfers and cache ownership

`fetch_page(page, priority, pin_owner)` is the generic transfer API.
`fetch_page_for_source(source_id, page, priority, pin_owner)` additionally keeps
the source's concurrency and interval policy across CDN hosts and redirects.
The compatibility `download_page` method returns the same canonical path; its
old destination argument no longer creates a second copy.

`FetchPriority::Interactive` is for the visible reader, `Background` for queued
downloads, and `Prefetch` for speculative pages and covers. The default admits
two background/prefetch transfers and reserves one additional foreground slot.
Origins and known source IDs have the same reservation policy. An explicitly
single-request source cannot be preempted; it admits the next interactive
request before lower-priority work. `new_with_download_limits` supports bounded
benchmark and deployment configurations.

Managers share HTTP connection pools and a scheduler for the same canonical
data directory. URL plus canonical header identity controls single-flight:
reader, download, and prefetch callers reuse an active response and its final
path. A reader can promote a queued prefetch, including an archive transfer
that the requested ZIP page depends on. Failed requests can be retried.

Bodies stream through a 64 KiB buffer into a reserved `.part` file. Compressed
artifacts cannot exceed 32 MiB. Every chunk checks the shared disk reservation;
image validation and checksum publication finish before the atomic commit.
No full-response image buffer is retained. The shared cache owns the default
256 MiB unpinned LRU budget and 64 MiB free-space floor. Download owners keep
offline pages protected; callers release reader owners when their chapter
closes. `cache()` returns the same manager for rendered artifacts and leases.

WASM ZIP pages are lazy descriptors. Their validated archive and individual
images both use the shared cache, so a chapter archive is requested once rather
than once per page. Short-lived archive pins protect extraction and are released
with RAII. Existing external local paths, including user imports and old stored
references, return their original canonical file without copying or deletion.

Source options `maximum_parallel_requests` (also `maximumParallelRequests`)
and `request_interval_ms` may tighten admission. Current WASM manifests also
contribute their declared `config.maximumParallelRequests`. Rendered artifact
identities include the actual source fingerprint and rendering options; local
file identities include their path and metadata, including when URL is empty.

## Sleep and network epochs

Keep a `NetworkControl::open(data_dir)` handle alive for the application
lifetime. `suspend()` advances the epoch, cancels queued transfers and shared
flight waiters, and fences final cache publication. It does not pretend that
an active socket has stopped or release its admission permit early. The
platform may shut down the application's own outbound TCP sockets after this
barrier; actual response cleanup then releases the old permit. `resume()`
reopens the new epoch and is idempotent.

Managers and their clones retain their creation epoch. Rebuild a manager when
`is_network_current()` becomes false before starting new metadata or network
work. Cache and external local-file hits remain available through an old
manager. Native source sends and WASM initialization/host calls check the
operation epoch, so a constructor that waited on the WASM mutex cannot later
start an obsolete request. `is_network_interrupted` recognizes this result
without treating a sleep cancellation as a failed download.

Image HTTP transfers have an explicit 120-second request budget, independent
of their 30-second idle-body limit. `image_request_timeout_ms` may set a shorter
or longer bounded budget for a source. Waiting for admission remains subject
to lifecycle cancellation. This does not assume that Linux `Instant` advances
while the device is physically suspended.

The blocking facade polls reqwest's read-timeout body on the caller's std
thread. The source layer owns one shared timer driver and enters that context
only while consuming HTTP bodies; callers and download workers do not supply a
Tokio runtime. This retains independent idle and total deadlines without the
lazy body timer panicking outside a reactor. Native metadata, repositories,
WASM host responses, and image streams all use the same body boundary.

## Aidoku packages

Repository indexes accept the current `{ "name": ..., "sources": [...] }`
format and legacy arrays. Relative package/icon URLs resolve against the final
index URL. Installing an `.aix` package verifies its manifest identity, version,
archive paths, extracted sizes, and WebAssembly header, and publishes an
immutable payload directory. The runtime prefers `main.wasm`, with
`source.wasm` as a compatibility filename.

Wasmi interprets current Aidoku sources without a JIT. The wire structs and host
contract follow the MIT-licensed
[Aidoku Rust source API](https://github.com/Aidoku/aidoku-rs). Its license is
included in `aidoku-rs.LICENSE`. No code from the separately licensed Swift
AidokuRunner is included.

The runtime executes the `start`, `get_search_manga_list`, `get_manga_update`,
`get_page_list`, and `free_result` exports and decodes their Postcard buffers.
It also supports static/dynamic catalog listings and `get_image_request`.
Supported host modules are standard buffers/date functions, HTTP requests,
persistent typed preferences, and read-only HTML/CSS queries. URL image pages
and bounded ZIP image entries become ordinary application pages.

The optional image-request URL follows the SDK's Postcard `String` contract;
the search query uses its separate raw-string contract. Wrappers release only
their own argument descriptors and consume returned image requests. Repeated
preferences and unchanged manga metadata avoid synchronous file replacement.
The regression fixture checks 1500 requests with context and 1500 without
context, including request-header propagation and bounded host resources. HTTP
header names are compared without case sensitivity; source-defined context
keys retain their original semantics.

The following requirements produce explicit capability errors when invoked:

- Legacy pre-Postcard source ABIs.
- JavaScript contexts, browser WebViews, and interactive browser verification.
- Canvas image processing and generated canvas images.
- HTML DOM mutation and text-only reader pages.

Unsupported imports use explicit traps: an optional import does not prevent a
source from running paths that do not invoke it. A package that installs
successfully can still require an unsupported capability during a later call.

## State and limits

Each source has a state directory named by the SHA-256 digest of its ID.
Preferences store actual Postcard values, while cached manga retain the original
chapter context needed by subsequent page requests. Sparse catalog entries
preserve previously loaded details. Source execution and UI option changes
share a transaction lock; state files use atomic replacement.

`source_settings` exposes static source controls and their actual types.
`with_source_option` validates a change, synchronizes host preferences, and
returns a `SourceConfig` for the application to persist. Invalid values left by
a source version change fall back to the new schema default. Unsupported action
controls remain unavailable rather than becoming arbitrary text inputs.

Interpreter memory, fuel, operation duration, network response sizes, request
resources, preferences, HTML documents, selected nodes, and HTML output all have
explicit limits suited to a constrained reader. ZIP resolution uses bounded
file streams and shared disk artifacts. Downloaded images use the shared
streaming-aware image validator before publication.

## Verification

Tests include actual WAT guest execution, Postcard request/result buffers,
standard/HTTP/preferences/HTML imports, unsupported capabilities, interpreter
fuel and memory boundaries, source option upgrades, repository installation,
provider HTTP fixtures, credential isolation across redirects, transfer
priorities, streaming failures, shared cache paths, and source limits across
multiple CDNs. Run these
tests only in the verification environment authorized for the project.

### Final SDK verification

The final source snapshot passed 94 source tests on `test-env`. The independent
real Rust fixture uses the unmodified `register_source!` macro from SDK revision
`ae6485e3c14ecd1fd5497d170eae884c68d04a87`, including
`ImageRequestProvider`. It completed 1500 requests with context and 1500 without
context, preserving the URL and HTTP headers and checking a context-free cover.
The native debug host run took 398.56 ms in total and made no HTTP request;
this is an ABI/resource regression result, not a Kindle download benchmark.

The proof compared all 14 Rust files in `src/sources` byte-for-byte against the
formatted `/tmp/aidoku-phase2` snapshot through a sorted SHA-256 manifest. The
manifest digest is
`fb455f763976f92b8939325608d61b0e1e92a8de970df5d365dd0972f37a3744`.
The guest WASM digest is
`743a511213ce2f1dc7480f2df9f4618f6d5d8f575494c7ab542847bcab8a2ac3`.

Proof artifacts are saved under `tmp/sources-verification/`:

- `sdk-image-request-proof-final.json`: counts, timings, SDK revision, and hashes.
- `sdk-image-request-proof-final.log`: the actual host build and proof output.
- `sdk-proof-source-files.sha256`: relative source paths and individual hashes.
- `sdk-image-request-guest-build.log`: the independent guest build log.

The source hash intentionally covers Rust code, including its regressions;
updating this documentation does not change the tested source-code snapshot.
