# Project the first Library status from a bounded panel-update window

This is a distinct application candidate. Apply the [cold source patch](../cold-trial/candidate.patch)
to `78c5b2b`, followed by [status.patch](status.patch). The cold-only application previously
passed its cold-screen comparison and independent confirmation but failed the warm-start
guard. Those measurements belong to that earlier binary; they do not qualify this one.

The `78c5b2b` baseline made a complete native status query during the first Library
view projection. In the previous warm six-pair check, the first view took about 72 ms
and the interval from an established native query session to successful close alone
took 29 ms. The total view interval also includes Library database and model work.

This version uses the callback after the native driver accepts the initial full frame.
It starts the existing metadata and font preparation, then uses a single optional AQ1
session to read the current Wi-Fi state and battery level. It shares an 80 ms deadline
across startup, both reads and receipt of clean EOF/exit. The child is retired before
the callback returns. The backend then performs the original checked framebuffer wait
and input synchronization. This does not claim that panel completion or touch scanning
is preemptible, or that hardware wait will shrink by the status probe's elapsed time.

Only the normal fresh Kindle Library route can do this. Acquisition is skipped or
abandoned if a power/exit transition, unavailable helper, malformed status or time limit
intervenes. At first view construction, only a fully completed sample less than one
second old can be used. The existing clock and disk calls still run at view construction.
The four genuine view fields are then available to subsequent Library cover publications
for the ordinary 30-second cache period, including cache-only use by the Reader. Full
device-status requests and all writes, including lighting, remain separate. The first
application of lighting preferences can preserve only the unconsumed radio/battery sample;
sleep and ordinary invalidation retire the applicable sample or freshness deadline.

There are no new environment keys, command-line options, background threads, helper
binaries, catalog changes or extra framebuffer submissions. Trace events distinguish
`startup_toolbar_probe_started`, `startup_toolbar_probe_ready`/`startup_toolbar_probe_unavailable`
and the first view's `startup_toolbar_snapshot_used`/`startup_toolbar_sync_fallback`.
Kernel timestamps and application output are required to establish whether the new
probe falls inside already necessary display work on a physical device.

[Seventeen host mock checks](host/README.md) exercise the production AQ1 transport and toolbar
state machine with a process fixture. Compiler input identity and the final ELF hash
are recorded in [arm-identity.json](arm-identity.json). The measured ARM target is
`9d92dc4cd01005a6ba2f48fe61786e5df5570c43feb5709fe3159bb164807d0f` (53,379,824 bytes).
The final Linux Rust suite passed 825 library tests (six ignored, including the
explicit synthetic fixture benchmark) and 22 startup tests. The final [PW5 and Scribe
desktop Library captures](desktop-pixels.json) match their frozen L01 references pixel
for pixel. Those checks cannot establish Kindle latency or native lifecycle correctness.
Use the [separate physical analysis](../status-bench/) with fresh measurements for
the combined executable, including the first display/input barrier and warm first-frame
guard; never transfer performance/pixel qualification from the cold-only binary.

## Replay the exact product changes

Start at clean product revision `78c5b2bc09a469133c886eb0d73aaf9ed63195b2`,
with these review files available. Apply these two patches in this order; they
together are the complete product change. No launcher, document, catalog runtime,
packaging script, dependency lock or asset is changed.

```sh
git apply --check --whitespace=error-all docs/review/startup-next-20261118/cold-trial/candidate.patch
git apply --whitespace=error-all docs/review/startup-next-20261118/cold-trial/candidate.patch
git apply --check --whitespace=error-all docs/review/startup-next-20261118/status-trial/status.patch
git apply --whitespace=error-all docs/review/startup-next-20261118/status-trial/status.patch
```

An independent replay on `test-env` verified the base Git archive's recorded
commit, both exact patch SHA-256 values, intermediate and final manifests,
all eight changed paths, and byte equality of the complete final source against
the original supplier. The original build's pre/post source records, Cargo
artifact event log and ELF were checked; no rebuild or device access was needed.
See [the patch chain and eight file hashes](verified-source-chain.json) and
[the complete 360-file source manifest](combined-source-manifest.json).

To check a separately uploaded result without changing it, run on `test-env`:

```sh
python3 -I /root/aidoku-next-status-20261118/tools/replay.py --tree /absolute/path/to/product-tree
```

Expected canonical source SHA-256:
`1c7909c4efefd88d80cdacab12f1e832fa0533f3b62ef094cd956f16ca75f768`.
