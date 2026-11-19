# Controlled qualification of the last-cover grace interval

This is functional evidence for an optional setting, not a latency benchmark.
It supplies a deliberately narrow timing boundary that a fixed run of six
normal Library opens may never encounter. All zero-trigger physical runs remain
in the performance estimate. The independent physical comparison of Library
and status-bar pixels is still required; none of those pixels come from this
host fixture.

The runner copies the exact measured application source to an isolated build
directory on `test-env`. It checks the complete production source manifest,
the producer's receipt and actual ARM hash. The only change is the appended
`#[cfg(test)]` adapter in `src/app/cover_jobs.rs`; every production byte and the
original executable stay unchanged. A private overlay receives Cargo output,
with at most six build jobs. Separate processes run the cases using a real
Slint UI, single-shot timer, event dispatch and unchanged production eligibility,
completion, cancellation and expiry methods. The adapter supplies a two-item
visible model, result readiness and an explicitly controlled platform clock.
It does not decode synthetic work, inject input into a Kindle or measure
ordinary scheduling frequency, optical completion or a user-visible speedup.

The six conditions are:

| Condition | Required behavior |
| --- | --- |
| Last local result completes before expiry | Arm once, publish once, retire before any later wake |
| Last result is still pending at normal expiry | Fire one callback; permit immediate publication and never rearm |
| Another UI change arrives during the wait | Cancel immediately, retain dirty pixels, issue no stale callback |
| Feature disabled | Do not arm a timer |
| Different job owns the last result | Do not arm a timer |
| Millisecond timer callback arrives after 39.1 ms | A spent timer must not postpone publication until a later periodic poll |

The sixth input models Slint's clock conversion. A platform start time of
1,000.9 ms is truncated to 1,000 ms; a 40 ms single-shot callback is eligible
at 1,040.0 ms, after only 39.1 ms on the application's finer clock. Expiry is
a maximum permitted wait. Correct handling can end it fractionally early,
but it cannot defer again after the only scheduled wake has been consumed.

The original `3ef03a41...` build passed the first five cases and failed the last:
the callback had fired, but the application returned `defer=true` and would
rely on another event or the normal 50 ms poll. This is a reproduced possibility,
not an estimate that every startup loses 50 ms. The initial real-clock pilot
also observed an early wake; the controlled replay isolates that behavior
from host timing noise. [Original observations](grace-before-fix.json).

The [narrow preview fix](spent-timer.patch) checks the real timer's running
state in addition to the original absolute deadline. An isolated preview of
that change passed all six cases. [Preview observations](grace-preview.json).
The preview was not a release build and does not qualify `3ef03a41...` or any
later ARM artifact. One earlier preview attempt did not apply its patch because
an enclosing host Git repository ignored the relative path. The current tool
uses the local patch utility, zero context fuzz and an explicit byte-difference
check before permitting a preview build.

For the independently built **v2 `1b1c508e...`**, the producer receipt and
369-file source manifest `28c1bb31...` identify a new source and ARM pair. All
six [final grace observations](final-v2-grace.json) passed and were reproduced
from the retained test executable, including immediate retirement on the spent
timer. The same test-only build also exercised eight separate [cached prewarm
cases](../prewarm-fixture/final-v2-prewarm.json). This supports an optional
feature's correctness on the host. The controlled wins are not physical
startup gains or additional A/B samples.

For a **new actual release candidate**, run on test-env, replacing the four
inputs with its frozen source, producer receipt, receipt hash and exact ARM:

```sh
python3 -B -I run_fixture.py run --source SOURCE --receipt RECEIPT \
  --receipt-sha256 RECEIPT_SHA256 --application FINAL_ARM
python3 -B -I run_fixture.py verify --batch PRIVATE_BATCH \
  --completed-sha256 COMPLETED_SHA256 --output FRESH_PUBLIC_JSON
```

`verify` rechecks the input/compiled test identities and original event order,
then reruns the six isolated cases. It refuses draft patches, missing positive
or negative observations, partial executions and foreign hashes. `preview-fix`
is for the historical controlled source comparison only: it writes no
production-eligible completion record. No device lease or physical access is
used by any of these commands.

For the exact v2 combined qualification, use `combined` in place of `run`
and run `verify` once for `--feature grace` and once for `--feature prewarm`.
The host `tools/` directory contains `controlled_prewarm.py` and the separate
reviewed `prewarm-fixture/qualification.rs` as `prewarm-adapter.rs`. Static
inputs, both frozen source adapters, the actual compiler-emitted test file
and each execution's event and observation sidecars remain bound to the
private completion receipt. No host duration is exported as a real user wait.
