# Library toolbar clock screening

Decision: retain `native_time()` and its bounded stock `date +%H:%M` call.
The subprocess is not mathematically necessary to format a clock, but an
unconditional in-process replacement cannot currently preserve the native
timezone behavior. The savings are small and have not been measured on a
Scribe. No product code or device state was changed in this screening.

The ordinary Library startup snapshot calls `native_local_status()` once to
populate time and free space, and caches the result. The ordinary status cache
also has a 30-second TTL. The `date` child is polled at intervals up to 5 ms,
subject to its 700 ms deadline; a failed child currently displays `--:--`.
Other product code already uses `chrono::Local`, but that usage does not
establish equivalence to the stock clock in the Library toolbar.

The application's target is static ARM musl, while the actual stock `date`,
its libc, inherited `TZ`/`TZDIR`, available timezone data, and localtime
management are not established by this host experiment. `chrono` 0.4.45 uses
its own timezone parser on Unix rather than delegating to musl `localtime_r`.
It does not search `TZDIR`, and its same-thread cache does not reload a zone
file just because the file changes while the `TZ` string stays the same.
When a timezone cannot be parsed, `chrono` may fall back to another discovered
timezone or UTC without reporting that fallback to the caller. A simple
`chrono::Local` fast path with error fallback therefore misses valid but wrong
outputs. Musl's own `localtime_r` would not resolve this: the inspected Zig
musl implementation caches by `TZ` string and uses fixed zoneinfo paths, and
the device's actual static runtime and stock timezone configuration remain
unverified. Do not equate the Zig musl source with a proven Kindle runtime.

## Off-device results

On `ssh test-env`, the standalone probe used the project's `chrono` 0.4.45,
`iana-time-zone` 0.1.65 and `libc` 0.2.191. The host build and the static
ARM hard-float build under QEMU both agreed with Ubuntu GNU `date` for all 63
samples: seven instants covering midnight, US spring and autumn transitions,
the repeated local hour and 2040; and unset, empty, UTC, Shanghai, New York,
London, Lord Howe, Chatham and one nonexistent timezone path. Agreement for a
missing path on this worker does not prove equivalent fallback elsewhere.

Two controlled cases produced different results with both probe builds:

- With `TZ=Asia/Shanghai` and `TZDIR` pointing to an isolated directory where
  that name contains London timezone data, `date` returned `13:00` and
  `chrono` returned `20:00` for the same instant.
- With `TZ` pointing to one timezone file, replacing its New York data with
  Tokyo data and waiting 1.2 seconds changed fresh `date` output from `08:00`
  to `21:00`; the long-lived `chrono` process still returned `08:00`.

These are deliberately controlled failure cases, not a claim that either
configuration occurs on the Kindle. In one 30-pair host run with alternating
order, `date` plus the production-style 5 ms polling had median 5.243 ms and
p95 5.312 ms; `chrono::Local::now().format` had median 0.004 ms and p95
0.014 ms. The latter includes one first-call sample of 0.027 ms. This is a
host microbenchmark, not a Scribe measurement or an estimate of saved launch
time. It uses a small reproduction of the child polling behavior, not an
instrumented call to the production function.

For a future opportunity, an already authorized diagnostic visit could compare
the stock and in-process results at the same instant without changing the
display. Matching once would establish only that observation: it would not
qualify daylight-saving transitions, invalid or changed data, or other future
timezone settings. Any proposed fast path would need explicit, verifiable
admission and refresh semantics plus a reliable fallback; there is no such
qualified implementation here.

## Reproduce

Copy this directory into a private `test-env` workspace and run its script
inside the shared Ubuntu build runner with the build lock. The script creates
and removes its own temporary project and target directory, uses six Cargo
jobs, and runs an offline host comparison plus the ARM/QEMU comparison:

```sh
/opt/kindle-toolchain/ubuntu-run --build "$WORKSPACE" sh /work/check.sh
```

`evaluate.py` supplies identical fixed UTC instants to the probe and GNU
`date -d`. Its benchmark instead uses `chrono::Local::now()` and a fresh
`date +%H:%M`, alternating their order. The host's GNU/glibc results and QEMU's
emulated ARM results are not a substitute for the actual firmware's `date` or
an on-device timing run.
