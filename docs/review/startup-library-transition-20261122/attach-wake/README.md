# Post-attachment wake: real software Library A/B

**Keep the current launcher and application.** On `test-env` a direct asynchronous wake removed about **1.08 ms** between controller attachment and first view entry on both host layouts. The first complete Library software draw was consistently earlier on Scribe, but not in both execution orders on Paperwhite 6. This is an isolated mechanism result, not a stock-card or Kindle SEND speedup. The observed 22–25 ms post-attachment gap on the [earlier staged Scribe candidate](../../startup-card-stages-20261122/HISTORY.md) was an elapsed interval, not proof that the timer caused all of it.

| Interval (control minus candidate, paired median) | Scribe 930 × 1240 | Paperwhite 6 632 × 840 |
| --- | ---: | ---: |
| Attachment exit → route start | +1.083 ms (8/8 earlier) | +1.079 ms (8/8) |
| Attachment exit → view entry | +1.087 ms (8/8) | +1.083 ms (8/8) |
| First full loading draw → first complete Library draw | +1.387 ms (8/8) | +0.269 ms (5/8) |
| First full → Library, AB / BA median | +1.175 / +2.098 ms | +2.014 / −0.273 ms |
| Rust entry → first complete Library draw | +1.373 ms (6/8) | +0.529 ms (6/8) |

All [sixteen pairs and four raw interval values per arm](result.json) are retained in microseconds. One warmup per arm was excluded before each profile's eight alternating AB/BA pairs. Each launch ran in a fresh process on one pinned x86 CPU, with a private six-book, six-chapter offline shelf. All 16 timed pairs compared the entire RGB Library buffer byte-for-byte; all timed arms had seven slots, zero completed covers, nontrivial rendered ink, and a successful Slint pointer press/release on the reversible shelf filter after the endpoint. Eight additional isolated checks covered both arms and both profiles: an exit requested at attachment prevented route entry, and a suspended coordinator rejected the next dispatch before a controlled resume. No profiles were pooled.

## Single timing difference

The [materializer](materialize.py) works on a disposable copy of the November 22 transition source. Both arms used a single x86 executable and the same common instrumentation. A retained the existing `schedule()` at the single handoff after `App::attach_prepared` published the application. B changed only that call to:

```rust
aidoku_kindle::reader_timing::mark("application_attached");
self.wake.request();
return Ok(false);
```

The call enqueues via the existing deduplicating `WakeGate`; it does **not** invoke `advance_startup` inline. First-scene yielding, subsequent stage timers, readiness and exit behavior are unchanged. The trial asserts that advance resumes in a later callback with `application_work_allowed`, never inside the attachment callback. The existing Rust tests for the first scene's required SEND/yield and wake-burst deduplication also passed on `test-env`.

## What actually ran

The executable runs the production [`main.rs` job-ready handler](../../../../src/main.rs), including its stop check, lifecycle drive, application-work gate and startup continuation; real metadata/font workers, application window, font registration, database queries, attachment and Library models also run. The test harness [substitutes the window platform](fixture.rs): `MinimalSoftwareWindow`, a channel-backed event-loop proxy, real Slint timers, and `SoftwareRenderer::render` to memory. It is **not Winit/Xvfb, KindlePlatform, hardware input or a framebuffer SEND**. Before the first full scene is drawn no Library projection is accepted. The strong on-device display/input fence is not recreated. A stop check uses `STOP_REQUESTED`; the suspend check injects `Coordinator`'s suspended phase, not a real powerd signal. No physics or timing for sleep/wake is inferred.

This design tests the actual controller and dispatch decision without adding a mock 20 ms delay or assigning any portion of the physical [ordinary-card decomposition](../README.md) to the host timer. The native backend's pending queue, shell, power, brightness and panel are not modeled. The `dev` build and software timings are not production latency estimates. No candidate was installed or copied to a Kindle.

## Reproduce on `test-env`

Use the worker's Ubuntu runner and shared build lock. Place a clean copy of the source used by the [transition build](../build/README.md) at `/work/source` and these three scripts at `/work/study` in a private workspace. Materialize once. No network, device credentials or lease are required. Use a fresh output path for each comparison. The private fixtures, RGB buffers and isolated build can be deleted after obtaining the result.

```sh
cd /work/source
python3 -B /work/study/materialize.py /work/source
cargo build --offline --locked --no-default-features --features kindle --bin aidoku -j6
taskset -c 2 python3 -B /work/study/trial.py run /work/target/debug/aidoku /work/study/new-result.json
python3 -B /work/study/trial.py check /work/study/new-result.json
```
