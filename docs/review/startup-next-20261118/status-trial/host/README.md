# Startup toolbar host qualification

The host exerciser compiles the candidate's `platform_startup_toolbar.rs` and
the deployed `native_query.rs` byte-for-byte, with a stub four-field
`DeviceStatus` and an in-memory timing sink. It uses `rustc` and existing host
dependency files in read-only mode; it does not share a writable Cargo target
with the application build. A private mount namespace provides a synthetic
52-byte ELF header at the expected library name. No real firmware library is
loaded or modified. Production AQ1 compatibility discovery then opens the
fixture's sibling executable through the original code path.

For a successful open, the helper records a single session with opcodes 4 and
5. The first view receives the reported Wi-Fi and battery and the local fields
sampled at view construction. Subsequent cover publication reuses all four
fields. The fake helper's PID must have disappeared and must give `ECHILD`
when passed to `waitpid`; a protocol success alone cannot pass the test.

Seventeen separate processes cover connected/disconnected status, malformed
handshakes, bad framing, short replies, invalid values, unsuccessful helper
exit, trailing output, timeout, cancellation, stale samples, sleep at three
ownership boundaries, and preference invalidation. The fixture injects the
frame boundary and models sleep by calling the same state transitions; these
are neither framebuffer timings nor physical sleep/wake tests.

On `test-env`, with the frozen source and existing toolchain in place:

```sh
python3 -I /root/aidoku-next-status-20261118/host-qualification/run.py \
  --output /root/aidoku-next-status-20261118/host-qualification/verification.json
```

[Verification](verification.json) identifies the product inputs, the runner,
the fixture, and the observed event sequence of every case. The standalone
exerciser and its fake helper are not part of the application package.
