# Same-thread accounting for the first Library draw

## Why another diagnostic is warranted

The [closed Scribe transition study](../../../startup-library-transition-20261122/README.md) measured **754.553, 597.548 and 637.197 ms** around the native backend's software draw. These are elapsed times on accepted Library frames, not thread CPU times. That cohort used a verified ARMv7 release build. The device receipts confirm a large populated Library but do not give the retained pixel format, actual damage, or per-frame CPU and fault counters. The application configures native windows at scale 2 using framebuffer geometry; that code path is not a measurement of the three frames' exact damage or backing format.

The separate [release x86 comparison](../../../startup-library-transition-20261122/first-draw-profile/release-gray/README.md) measured a **3.853 ms** median for stock Slint on the Scribe design. It asserted 1860 × 2480 pixels, scale 2, Gray8 and 4,612,800 damaged pixels. The static ARM/native and x86/headless builds, process workloads, scheduling conditions, and observation boundaries are not identical. These medians do not form a speed ratio or identify a rasterization bottleneck.

The [November 17 scheduler study](../../../startup-cost-attribution-20261117/README.md) did sample process CPU ticks and faults, main-thread `schedstat` and CPU0 frequency, every 100 ms. Main-thread runnable wait **before the first Library frame** was 65–87 ms on its temporary route and 556–656 ms on its indexed route. Frequencies averaged over the longer settled-covers window were 1.73–1.76 and 1.96–1.97 GHz. These were different visits, routes and windows. Neither their ticks nor the November 22 process-*birth* ticks yield November 22 CPU time inside a specific draw. That quantity remains **unknown** in the old evidence. No later diagnosis is backfilled into it.

## New observation

`startup-draw-diagnostics` is a separate, default-off Cargo feature that enables the existing startup-stage instrumentation and a matching vendor-backend feature. Ordinary builds compile out the new fields, calls and sampler. This changes neither UI content nor the ordinary launcher. The operator stages one diagnostic ARM application; no extra device process or helper is required by the new probe.

On the renderer thread the exact order is: `getrusage(RUSAGE_THREAD)` → `clock_gettime(CLOCK_THREAD_CPUTIME_ID)` → **original draw wall start and end** → thread CPU clock → thread rusage. There is short fixed sampling overhead at the boundaries: CPU encloses the original wall interval, and rusage encloses CPU. The wall is the old Rust `Instant` interval, **not** a new BOOTTIME subtraction; `submitted_us` remains the common device BOOTTIME submission timestamp. Thread time may slightly exceed the wall measurement; retain signed differences and the raw values rather than clipping or treating every excess as impossible. A large wall-minus-CPU interval could reflect blocking, preemption or other off-CPU time, not proven scheduler deprivation. Page faults and context switches are counts, not durations. High thread CPU alone cannot identify a rendering primitive, CPU frequency, or memory-bandwidth limit.

The sample is taken only after a normal managed Library startup has requested its real scene. It is synchronous, bounded to at most eight attempted draws even if no qualifying submission arrives, and closed on the Library callback or a sleep/ownership pause. Initial/sleep frames, input-only callbacks, no-draw attempts, canonical fallback rewrites with changed output identities and unaccepted output cannot supply a valid fourth line. Nothing is polled and no process, book identity, path or pixel data is logged.

After the unchanged primary, stage and transition lines, the opt-in emitter adds:

```text
aidoku: startup_draw_cpu sequence=Q marker=K submitted_us=S wall_us=W thread_us=C minflt=m majflt=M nvcsw=v nivcsw=i width=X height=Y format=gray8|rgb8
```

`Q/K/S` bind the same accepted frame. `W` must equal that exact frame's `startup_transition render_us`. The two clock snapshots and the two resource snapshots fail independently. Unavailable or backwards clock data produces `thread_us=none`; any unavailable/backwards resource count invalidates all four counter fields as a group. Zero is valid. Width and height describe the unrotated retained panel surface, not the dirty rectangle. The format is the actual retained representation after the draw: it does not by itself prove whether that frame fell back from Gray8, that every pixel was drawn, or the scanout pixel format.

## Off-device checks

The [small Linux test runner](check_host.sh) compiles the actual diagnostic module directly with the locked libc source. Its tests compare a real sleeping thread and busy thread, reject a counter rollback as a group, and exercise arming, the eight-attempt limit and closing. The [QEMU verifier](verify_draw.py) links a [controlled callback fixture](draw_probe.rs) against the new ARM release application and backend libraries. Supplied CPU and counter values test the output contract; they are not timing measurements. The [feature-off verifier](verify_off.py) links the [prior-contract callback](off_probe.rs) against the **same source**, rebuilt as a library with only `kindle,startup-stage-diagnostics`; it does not create a second measured application.

Results: **3/3** Linux sampler tests and [8/8 feature-on QEMU cases](qemu-on.json) passed. The executable is a 53,738,400-byte static ARMv7 hard-float EABI5 release program. `--version` passed under both regular and no-NEON QEMU; the Scribe and PW6 CLI paths were silent apart from their expected options. The [feature-off result](qemu-off.json) confirms the off library has no draw diagnostic prefix and both layouts produced exactly the previous three lines. Neither QEMU scope is a native-card visit. Cargo was locked/offline, used six jobs, `CARGO_INCREMENTAL=0`, `CARGO_PROFILE_RELEASE_LTO=false`, `CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16` and target `armv7-unknown-linux-musleabihf`; the only explicit final application features were `kindle,startup-draw-diagnostics`.
