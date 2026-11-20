# Native Library tile to the installed Aidoku Library

**2026-11-19, Kindle Scribe, installed R3 (`8baf57f`).** This study measures
an ordinary visit to the existing book tile on the stock **Library** tab,
not the similarly named recent item on Home and not a shell request to open
the document. No production code, installed launcher, settings or cache was
replaced. Every timed visit started a new application process.

**Answer for a repeated open with the current collection:** about **2.15 s**
from pressing the tile to the book text and navigation, **2.52 s** to the
complete Library including covers. Use these as typical observations, not
deadlines. The six low-overhead visits spanned **2.37-3.28 s** to complete.

## The measurement

The operator first identified the Home and Library views on the device, then
froze the Library page and unique Aidoku artwork verification before timing.
A stock X11 test-pointer click on that tile generated a verified button down,
50 ms hold and button up. Both timestamps and framebuffer samples use the
device's own `CLOCK_BOOTTIME`; SSH, navigation to the Library tab and host
processing are not in the interval. This is native UI input, not a physical
finger. An external camera or optical sensor is still needed for actual
finger-to-settled-ink time. A framebuffer observation is neither an EPDC
completion fence nor a direct input-readiness test.

The first Library endpoint requires the category bar, bottom navigation,
continuing title and populated book caption rows to match. The complete
endpoint additionally requires the continuing-book artwork and six grid
covers (seven slots, six distinct images). The whole sampled body below the
72-pixel system bar is checked separately. For each transition we report
the interval between the previous mismatching scan and the first matching
scan, allowing for the scan and input timestamp widths. A match must persist
for at least 200 ms; that confirmation time is not added to the endpoint.
Each later independent full-resolution capture also matched the entire
installed reference body. User pixels and per-book records remain private.

### Native timing

| Native Library tile, median bounds | 75 ms sampler, 6 visits | 25 ms sampler, 6 visits |
| --- | ---: | ---: |
| Down to book text and navigation | 2.109-2.187 s | 2.210-2.240 s |
| Down to book text and all visible artwork | 2.484-2.563 s | 2.895-2.926 s |
| Up to book text and all visible artwork | 2.429-2.510 s | 2.840-2.873 s |
| Observer CPU, one core (median over 12 s) | 1.85% | 19.54% |

The [75 ms cohort](gui-low-samples.json) is the preferred estimate. It read
4,466 points per scan, including 815 in the artwork. Sampling geometry was
chosen on the earlier release reference before these six visits. Both book
text and artwork were observable with the original match rules. In every
visit the last artwork and whole sampled body stabilized on the same scan.
The two sequential cohorts were **not** randomized pairs; their difference
is not an independent estimate of sampler slowdown and should not be
subtracted from either result. The later [cache-index snapshots](cache-activity.json)
show exactly six existing valid cover derivatives advancing their access
records per low-overhead visit. No processed record was added or removed,
and no recorded size, checksum, file fingerprint, validity or cover-alias flag
changed. This supports reuse; it is not a trace of a specific cache branch.

| Consecutive stages on the normal entry, low-overhead cohort | Median bounds |
| --- | ---: |
| Button down to launcher worker process creation | 0.473-0.484 s |
| Launcher worker to application process creation | 0.710-0.730 s |
| Application process creation to Library layout | 0.917-1.003 s |
| Library layout to complete visible artwork and text | 0.299-0.452 s |

The application process was created about 1.19 s after pressing the tile
(direct median bounds 1.180-1.192 s). These are kernel process birth times,
not the first Rust instruction. Medians of consecutive stages do not add;
the corresponding midpoint **means** are 0.491 + 0.747 + 0.973 + 0.513 s,
or about 2.72 s across the six complete visits.

In the [25 ms observation cohort](gui-samples.json), all six visits passed.
The sample-median brackets from **button down** were **2.210-2.240 s** to the
Library with text and navigation, **2.660-2.690 s** to the first finished
cover, and **2.895-2.926 s** to the complete Library. From **button up**, the
corresponding brackets were 2.155-2.188 s, 2.605-2.638 s and 2.840-2.873 s.
Individual complete-Library intervals spanned 2.508-3.432 s overall. The
complete-art and whole-body edges coincided on every visit. These brackets
describe sampling resolution and the observed cohort, not confidence
intervals or tail percentiles. This dense observer cost a median 19.54% of
one core over its 12 s window; no overhead was subtracted.

The [first, excluded setup visit](gui-pilot.json) is important: text appeared
at 2.574-2.607 s but the last artwork only at **8.899-8.928 s** from down.
The late change was in a book cover, not the clock or a notification. Its
picture matched the independent reference, but the original harness wrongly
required a positive sleep epoch on an ordinary never-suspended visit. That
visit was recovered and released, not counted or substituted into the six.
The corrected exit guard requires the true initial epoch **zero** and the
matching ordinary-exit acknowledgement. The first visit had no per-book
cache instrumentation, so its long delay cannot be attributed to a cache
miss by elapsed time alone.

## Optimization budget

The previously qualified [controlled R3 trace replay](r3-replay.json) is a
different experiment. Its start is the indexed file-URI request from stock
firmware, and its end is driver acceptance. The six warm and six empty-cache
visits used isolated data, local source images, and verified six hits and six
misses respectively per visit. Empty means *no derived thumbnails*, not an
OS restart. These results do not replace the real UI measurements above.

| From native open request, medians | All six thumbnails cached | All six thumbnails absent |
| --- | ---: | ---: |
| Usable Library frame | 2.136 s | 2.198 s |
| First frame with artwork | 2.537 s | 3.380 s |
| All visible covers submitted | 2.848 s | 8.196 s |
| First Library frame to all covers (paired within each visit) | 0.719 s | 5.992 s |

- **Missing thumbnails:** the six serial jobs took 5.786 s, including 4.805 s
  between combined validation start and validated decode. That interval mixes
  source identity checks, decoding, resizing and grayscale work; it is not
  4.805 s of decoder CPU. Generating/rechecking the derivatives after that
  marker took another 0.547 s. Preventing the six misses is worth investigating:
  the two controlled complete medians differ by 5.348 s. It is not a free or
  unconditional saving. Saving 20% of the 4.805 s interval would remove about
  0.96 s of serial work; measure the end-to-end effect and any shifted import
  cost before claiming it. Start by counting misses and why they occurred,
  then split hashing, decode and resize on unchanged inputs in the
  [local cover render path](../../../src/render_cache.rs). Preserve source
  identity and integrity checks.
- **Repeated opens:** native admission, launcher setup and getting to the
  first real screen are larger targets for the first-screen metric than the
  Library database query. The controlled warm trace placed Rust entry at
  1.213 s, then spent about 0.366 s in the checked initial display/input
  interval; the first Library view took 0.032 s and rendering it 0.168 s.
  The checked interval is a correctness barrier, not a proposed 366 ms cut.
  For a concrete experiment, record the observer startup, portrait read and
  display takeover separately in the [launcher](../../../packaging/kindle/launch.sh).
  A verified 20% reduction in the worker-to-app interval (~0.72 s) would be
  about 0.14 s, provided it moves the critical path. No such reduction has
  been demonstrated here.
  The six warm thumbnail jobs took 0.458 s; a 20% reduction is about 92 ms of
  work *after* first Library submission. Finishing the last job already leads
  to final publication in about 1 ms; do not budget a generic 300 ms delay
  there. Component medians and overlapping intervals must not be added.
- **Do not count past wins twice.** The installed release already incorporates
  earlier catalog/cover improvements. None of the individual R3 feature
  screens met the predeclared speed rule, including prewarm, layout and
  native portrait; no measured one-second warm-start win is available by
  changing a setting. See the [release qualification](../startup-r3-qualification-20261119-8baf57f/README.md).

## Evidence and scope

The device operator ran only through `ssh test-env`, with a new owner/token
lease per continuous batch. The indexed document, app/launcher/helper and
shared registration were checked against the installed release. A normal open
legitimately advances recent access and can populate caches; this is not a claim that the user's
entire filesystem remained byte-identical. Owned ordinary exit, a return to
the original Home tab, native ownership, lighting, pointer and lease release
were checked. Neither the old timing pairs nor any release install operation
were rerun. No reboot, cache purge or optical measurement was performed.

`replay_gui.py` validates private run receipts, input and process identities,
frame matches and sampler costs on the worker; it accepts released complete
batches for the primary summaries. `frame_analysis.py` contains the digital
bracketing rules. `replay_r3.py` independently recomputes the original R3
fixture results from the hash-bound qualification runs. The JSON files in
this directory contain no private user pixels, their hashes or device IDs.

For host-only reproduction on `test-env`, use the private input locations
from `/root/aidoku-entry-study-20261119/replay.env` and the scripts in this
directory. These commands do not connect to the Kindle:

```sh
. /root/aidoku-entry-study-20261119/replay.env
python3 -B replay_gui.py --low "$LOW_BATCH" "$INSTALLED_REFERENCE"
python3 -B replay_gui.py "$DENSE_BATCH" "$INSTALLED_REFERENCE"
python3 -B replay_gui.py --pilot "$EXCLUDED_PILOT" "$INSTALLED_REFERENCE"
python3 -B replay_cache_counts.py "$LOW_BATCH"
python3 -I replay_r3.py "$R3_REPORTS" "$R3_BENCH"
```

Worker-only `frozen-dense/` and `frozen-low/` under the same study directory
retain the capture, input and operator sources. They are records of consumed
runs, not instructions to reuse an old device lease.

A final worker replay reproduced all five JSON reports byte-for-byte. The
21 analysis/custody tests and two sampler tests passed. One invocation in
the worker's default temporary directory hit `ENOSPC` while creating two
synthetic archives; the unchanged suite passed with `TMPDIR=/dev/shm`.
This occurred after device release and did not affect any captured visit.
