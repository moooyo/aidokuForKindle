# Where does an ordinary Aidoku card launch spend its time?

**Follow-up completed:** a separate [four-visit normal-card diagnostic](../startup-library-transition-20261122/README.md) split the initial-full-to-Library interval. The Library draw, not the input fence or framebuffer write, dominated its final stage on all three formal Scribe launches. The follow-up's visits are not added to this cohort.

## Result: investigate the transition from the initial frame to the Library

On this Scribe, **Rust `main` to the first complete Library submission was both the longest of the three main stages and the largest contributor to the difference between the slowest and fastest launches**. This locates time, not a proven optimization. The four formal launches took **2.542–3.613 s** from ordinary card release to Library submission; their median was **3.203 s**. One earlier warmup was excluded. All five visits produced complete stage records at 100 clock ticks/s and independently restored and released the device. The final return was **05:10:25 Asia/Shanghai, November 22, 2026**. The stock installation remains R3.

| Formal visit | Release → worker birth | Worker birth → Rust `main` | Rust `main` → Library | Release → Library |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 523.323 ms | 964.947 ms | 2124.936 ms | 3613.206 ms |
| 2 | 485.920 ms | 937.949 ms | 1639.353 ms | 3063.222 ms |
| 3 | 452.358 ms | 1160.949 ms | 1729.730 ms | 3343.037 ms |
| 4 | 534.062 ms | 645.908 ms | 1362.409 ms | 2542.379 ms |
| Median | **504.621 ms** | **951.448 ms** | **1684.542 ms** | **3203.129 ms** |

Values are interval midpoints. Each visit's three stages add to its total before rounding; the medians need not. The median total release bracket is **3202.522–3203.736 ms**. Within the application, `main` to the initial full submission had a median of **477.768 ms**, and that submission to the complete Library had a median of **1206.774 ms**. The latter is elapsed time between two callbacks, **not** 1.207 s of rasterization or panel refresh. It can include waiting for preparation and the first-screen lifecycle checks.

Formal visit 1 was **1070.827 ms** slower than visit 4 (conservative bounds **1069.905–1071.748 ms**). The three same-visit contributions to that difference were:

- Release → worker: **−10.740 ms**, bounds **−21.661 to +0.182 ms**; its direction is not resolved.
- Worker → `main`: **+319.039 ms**, bounds **+309.039 to +329.039 ms**. Almost all of the midpoint difference preceded GUI process birth (worker → GUI **+320.000 ms**; GUI → `main` was indistinguishable within the birth bins).
- `main` → Library: **+762.527 ms**, about **71%** of the total difference. Within that stage, **+283.949 ms** came before the initial full submission and **+478.578 ms** after it. These application events share one clock and do not inherit the card-release or process-tick uncertainty in their differences.

**Decision:** keep JPEG on demand, retain the existing power, orientation and first-frame safeguards, and do not deploy the diagnostic build. The next useful optimization experiment is inside the **initial-full → Library** transition. On the same ordinary launch, distinguish the initial display fence, font/metadata readiness, view attachment, and Library render/settle/write/submit time before proposing a scheduling change. None of these four visits alone identifies which one is blocking or how many milliseconds can be removed. Historical staged data, complete-cover latency and the earlier JPEG A/B remain separate.

The [sanitized receipts and recomputed analysis](result.json) are sufficient for offline replay with `python3 -B audit.py public result.json`. The private audit also verified each original collection, ordinary exit, independent absence and exact lease return. The [ARM build and no-device checks](build/README.md) establish the instrumentation separately from these five actual launches.

After independent replay, the consumed 94,765,580-byte upload and temporary build/test outputs were removed. The closed records remain, including the small shared `aidoku-card-stages-return-20261122-051025.json` receipt on `test-env`. A prior unused plan was superseded before any device access; none of its slots or artifacts contributed a sample.

## Predeclared diagnostic

This is a separate Scribe diagnostic, not another JPEG A/B or a release candidate. Run the **same** on-demand JPEG build five times: one excluded warmup, then four measured visits in order. Stop if the ordinary card, log ownership, state or return cannot be verified. Do not replace an attempted measurement. Use fresh processes and an independent device lease for every visit, not a lease covering the waiting or analysis time. Leave the installed stock card, launcher, JPEG worker, portrait helper and Library untouched; select only the temporary Aidoku program and restore the original after each visit. No user touch or video is required.

The primary endpoint is the qualifying complete Library submission, not splash rendering, process creation, input readiness, all covers or optical settling. Preserve the same log and actual stock release interval used in the [closed ordinary-card A/B](../startup-jpeg-native-20261122/README.md). The diagnostic adds a separate record in an opt-in build; it is not added retrospectively to that experiment or to its timings.

## Boundaries and interpretation

| Boundary | What it measures |
| --- | --- |
| Actual native card button release | Same-device `CLOCK_BOOTTIME` brackets from the normal stock input path. |
| Foreground launcher worker birth | Start tick of the verified shell worker; not launcher function entry, a script mark or a power-service response. |
| GUI process birth / Rust `main` entry | Start tick of the verified GUI process, followed by its separate userspace clock reading. Process creation is not entry to `main`. |
| Initial full frame, when recorded | Active initial full submission callback, distinct from Library content. Absence is not a zero-length stage. |
| First complete Library | Same qualifying submission timestamp as the normal application log, with populated large layout/grid. No second reading is substituted. |

Compute every interval from a single boot and a single qualified launch. Convert kernel start ticks using that runtime's clock-tick rate; retain the tick-bin uncertainty. Exact application stamps share one release bracket, so its uncertainty cancels when subtracting those stamps. Do not infer absolute time from wall clocks, SSH latencies, observer polling or a different visit. Only sanitized relative durations and closeout facts leave the device workflow.

Rank three nonoverlapping primary stages: **release to foreground worker birth**, **worker birth to Rust entry**, and **Rust entry to Library submission**. Split the second at GUI process birth and the third at the initial full submission for interpretation; those subdivisions are not additional time. This uses Linux [`proc_pid_stat(5)`, field 22](https://man7.org/linux/man-pages/man5/proc_pid_stat.5.html) rather than a shell log or the kernel scheduler's configuration rate. The [separate historical measurements](HISTORY.md) are context only and are not samples in this cohort.

Report each of the four visits, boundaries and ranges. Separately report (1) the largest usual time segment and (2) where the difference **between this cohort's fastest and slowest total launches** accrued. Use corresponding visits for that difference, allow negative contributions, and distinguish close or overlapping bounds from a uniquely largest contribution. Do not add independently computed segment medians and label the sum a launch. These five visits can localize delay; they cannot establish a cause, a speedup, or a result for another Kindle model. The ordinary launcher is intentionally unmodified, so power-observer readiness, orientation and display takeover are not individually measured by its process-birth interval.
