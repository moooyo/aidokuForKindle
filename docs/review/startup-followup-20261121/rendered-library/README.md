# Startup to the rendered Library

**Primary:** ordinary native Library card release to the first rendered application Library layout, entries (or the correct empty state) and navigation. Cover placeholders are valid. Complete visible covers are secondary. Input readiness, sorting and optical ink settling are not part of this metric. The operator runs the scripts: no recording, manual tapping or stopwatch is needed.

## Measurement sources

| Source | Start and endpoint | Interpretation |
| --- | --- | --- |
| Normal installed card | Actual guarded stock input release, bracketed on the device's `CLOCK_BOOTTIME`; first confirmed match of non-cover Library regions, timestamped on that same clock | Observed upper bound for this installation, shelf and visit. No images are saved or transferred. Not an exact application submission or a panel completion. |
| New native application log (not in installed R3) | One `aidoku: startup_library_submitted` line on launcher stderr for a qualifying submitted frame on a managed native Library start, without enabling full tracing | Pair its `boottime_us` with a separately verified same-visit stock release. The line alone does not establish how the process started. |
| Instrumented application | `startup_library_frame_submitted` in the opt-in `--performance-trace` log, with Library semantics and the expected shelf slots, after successful submission | Software render endpoint. Pair with the same visit's trigger on the same clock. A staged foreground request is not a normal card release. |

The installed R3 normal launcher does **not** enable `--performance-trace`. Starting the binary over SSH or enabling a trace only in an SSH shell does not measure unmodified card entry. When a trace is available, use device `boottime_us` for an external device-clock trigger; `at_us` alone is relative to Rust entry. Do not substitute `startup_initial_frame_submitted`, an input fence, the opening of a window or `startup_visible_covers_settled`.

For builds containing the new one-line log, [the interval parser](submitted_interval.py) accepts a qualified case directory (`origin`, `qualified`, `anchor`, `pointer-brackets`) and **only that same visit's launcher log delta** on stdin. The collector must bind the log inode and offset before dispatch, verify the same boot, bound process and ordinary exit, and run the parser privately; do not export an entire launcher log. The parser requires one successful worker, one framed submission with nonzero sequence/marker, and matching actual release brackets. It reports `event − up_after` to `event − up_before` in microseconds. Absence, ambiguous ownership, missing markers or a resident activation without a new startup log are not measurements. The R3 framebuffer observer does not pretend to generate this product log.

## Installed R3: three automatic stock-card opens

All three scheduled visits completed on November 21 on the Scribe with the existing six-book shelf. The [sanitized results and return receipt](stock-r3.json) use the ordinary card, not a deep link or a staged worker. The scripts opened the card, observed Library text/navigation and exited normally; no one tapped or recorded the screen. The observer did not store or export an image.

| Visit | Last non-match lower bound | First match upper bound |
| --- | ---: | ---: |
| 1 | 3,477.346 ms | 3,599.788 ms |
| 2 | 2,183.364 ms | 2,305.229 ms |
| 3 | 2,277.125 ms | 2,398.630 ms |

**Median of the three upper bounds: 2,398.630 ms.** The observer checks every 120 ms and confirms stability later; its first matching scan is the endpoint. The artwork masks still differed from the complete-cover reference in all three first matching scans. That neither disqualifies a frame nor identifies which art was a placeholder. No cache was cleared, so do not relabel the slow first visit as a known cold-cache measurement. Installed program metadata was checked during the visits; full program byte comparison was deferred until after timing to avoid warming the application ELF for the study. All three visits and collections, native return, lights and tab/pointer restoration, program comparison, stage removal and owner/token-checked release succeeded at 11:59:01 Asia/Shanghai. This is an installed **baseline**, not an A/B or a KPW6 measurement.

The operator verified the public aggregate against all three private text records and the release receipt. The consumed 63,865,029-byte upload was removed; the small closed plan, logs and measurement remain available for offline `--analyze`. The shared worker receipt is `aidoku-rendered-library-return-20261121-115901.json` in the coordination directory. It records release at that time, not current availability or a reservation.

## Optimization decision

The [historical staged replay](replay.py) changes the analysis endpoint, not the original cohorts, their entry paths or their verdicts. The figures below are from closed Scribe logs, not new stock card visits. Positive paired savings mean the candidate was faster. Differences of per-arm medians are not the median of within-pair differences.

| Candidate vs R3 / six thumbnails | R3 median | Candidate median | Paired saving | Faster pairs |
| --- | ---: | ---: | ---: | ---: |
| [Post-SEND, empty cache](staged-cold.json) | 1,384.565 ms | 1,361.004 ms | +2.139 ms | 5/10 |
| [Post-SEND, warm](staged-warm-post-send.json) | 1,399.821 ms | 1,395.058 ms | +3.431 ms | 3/6 |
| [Rejected progress-frame revision, warm](staged-warm-rejected.json) | 1,368.659 ms | 1,410.450 ms | −37.566 ms | 2/6 |

**Keep R3 installed. No demonstrated first-Library gain justifies promotion.** The staged start clock has 10 ms resolution. Post-SEND's established **680.849 ms** cold improvement was to complete covers only; eliminating the progress frame on the separate warm revision did not help. On the six warm post-SEND pairs, the first launcher worker mark to first full-frame submission took a median **728 ms (R3) / 680 ms (candidate)**. Full-frame to first Library submission took **661 / 689 ms**. These are elapsed intervals, not amounts removable and not daily card timings. Instrument the launcher power observer, portrait setup and display takeover before choosing a first-screen change. One fewer procfs read is not a launch-time result.

The [follow-up on JPEG capability admission](../jpeg-probe-admission/README.md) instead removes a specific unused pre-window operation from a normal Library open. The paired software-render study tests the change on `test-env`; it does not replace any ordinary-card samples above. The next native comparison needs identical submission instrumentation in both arms.

## Sampled Library rendering observer

`render-watch RULE` reads the existing private, independently reviewed Scribe
`AFR3RULE` for exactly six grid books. It opens `/dev/fb0` read-only and keeps
only sparse samples in memory; it never records, exports, or prints pixels.
The rule itself contains private sampled pixels and is an input, not public
evidence. The outer operator owns the stock-screen and tile identity checks,
the actual input events and their device-clock brackets. This observer never
injects input, operates a Kindle lease, or validates a release action.

On the first scan, all five text/navigation regions must individually differ
from the target. Otherwise the command fails. A passing first scan writes the
single line `READY` to stderr; dispatch input only after receiving this line.
This detects an absence of each complete target region, not the identity of
the underlying stock page. The outer stock and tile guards remain necessary.

Thereafter it compares the same five masks as the existing `observer.py`
six-book rule. Three consecutive matching scans, spanning at least 200 ms
from the first scan's end to the third scan's start, produce one stdout line:

```text
ARL1 LAST_NEGATIVE_US FIRST_MATCH_US CONFIRMED_US COVER_FIRST COVER_CONFIRMED
```

All three times use `CLOCK_BOOTTIME` microseconds. The first is the start of
the last negative scan before this matching sequence, the second is the end
of its first positive scan, and the third is the end of its confirming scan.
The two cover fields are independently `MATCH` or `DIFFER` against all seven
reference artwork masks at the first positive and confirming scans. They are
diagnostic only: `DIFFER` does not identify a placeholder, and cover matching
does not determine the text/navigation endpoint.

The outer operator must retain the actual input down and up clock brackets,
check that the initial `READY` preceded input and that the first positive scan's
end is not earlier than the end of the release bracket. Only if the last
negative scan started at or after `up_after` is a post-release lower observation
bound available. Otherwise report only the upper bound. Reject missing or
inconsistent events. Under a single stable transition the observation interval
spans the negative scan's start to the positive scan's end. Framebuffer scans are
not atomic; the comparison does not prove that the entire screen had the
target pixels at one instant or that an e-ink panel had optically settled.
Report the conservative upper bound `FIRST_MATCH_US - up_before` as the primary
release-to-sampled-render value. The later confirmation time is not added.
Do not treat a failed or missing visit as zero. The current predicate is for
the known six-book Scribe shelf, not an empty-state or Paperwhite fixture.

Scans are scheduled at 120 ms for at most 12 seconds. Each scan verifies the
framebuffer layout and page pan before and after the read. A scan exceeding
50 ms, a gap exceeding 150 ms, an insufficient three-scan dwell, a layout or
pan change within a scan, or observer process CPU exceeding 10% of one core
invalidates the visit. Unsupported layouts and malformed, flat or wrong-count
rules also fail closed. Failures exit 2 with a short `ARL1_ERR CODE` on stderr
and no successful result. A page flip between complete scans is permitted.

## Off-device checks

On `test-env`, the final host, device-shell, staged-replay and submitted-interval fixtures passed **62 tests together**. The C observer separately passed native and ARM/QEMU checks, four Python replay tests and a closed historical Scribe text-mask replay. The actual three-visit stock batch then exercised the ARM observer and the full host/device path. The ordinary application log is source-only on this device: five Linux Rust tests with the `kindle` feature passed (including the headless transition from a booting application to the correct empty Library). The R3 hardware values above do not come from that new log.

`test_render_watch.c` tests layout admission, both pan pages, both rotations,
per-region differences and the state machine. `test_render_watch.py` compares
the complete mask map against the original Python observer for both logical
orientations and exercises synthetic traces and malformed rules. `replay.c`
is an off-device adapter: it reads an old private `AFW2` trace from stdin and
prints only a result or a short failure code. It takes the first scan at least
120 ms after the preceding selected scan; an old 75 ms trace therefore yields
150 ms spacing. This replay checks classification and transitions, not the
cost or timing of a new live observer.

Run all builds and tests on `ssh test-env`, inside its Ubuntu runner. With this
directory and the original observer in their normal tree, supply an unused
output path to the checked build script:

```sh
sh /work/docs/review/startup-followup-20261121/rendered-library/check_render_watch.sh /work/render-watch
```

## Automating another stock-card batch

The [host runner](host.py) owns preparation, admission, the three [device visits](device.sh), recovery, release and log analysis. The operator, not the user, runs it on `test-env`. Create a new private workspace and use `host.py --freeze WORK --owner OWNER` with the retained control workspace (`--legacy`), closed `--reference` and `--navigation` templates, current `--device`, reviewed `--guard` / `--approved-guard`, normal `--visit` / `--dispatch`, and checked ARM `--watcher`. The frozen plan contains its own host runner and tools. Put the owner's thread ID in the workspace `owner` file and the [entrypoint](entrypoint.sh) beside it. Old plans are single-use; do not copy their token or repeat their closure.

With all fixtures and binaries ready, use `entrypoint.sh --preflight` (no device access) and then `entrypoint.sh --run` on the worker. Authentication is obtained before atomic acquisition via a private pipe or an operator-controlled no-echo terminal; do not place a password in arguments, source files, notifications or public results. A busy lease is not a reservation. The script drives the actual native card for each visit and closes the device before analysis. Hand the device back before modifying tools or interpreting results.
