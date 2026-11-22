# Stock-tile pilot device protocol (single visit)

**Superseded; do not execute.** This is the archived interaction protocol. The [current metric](../rendered-library/README.md) ends at the rendered Library, not at a control response.

This protocol defines the minimal device-side contract for one R3 pilot:
from **stock tile release** to a **first observed in-visit response** on an
offline reversible Library control. It is a bounded diagnostic only.

## Scope

- One visit only, no A/B pairing and no rollout decision.
- Primary endpoint is a conservative upper bound from tile release to first
  observed control response in the same visit.
- Cover placeholders are allowed.
- If only first observed success is available, report it as an upper bound.

## Input method rules

- Stock-card dispatch uses the existing guarded stock path (`tile-input` via
  `input/dispatch.sh`). This is not optional.
- Aidoku in-app control response must use a **real app input path**:
  - Preferred for this pilot: operator performs one manual tap on the chosen
    reversible offline control (for example Library sort icon).
  - Do not claim XTEST acknowledgment as in-app response evidence.
  - Do not synthesize an unproven evdev replay path for this pilot.

## Clock and trigger contract

- `stock-tap` anchor must be four `CLOCK_BOOTTIME` values in strict order:
  `down_before down_after up_before up_after`.
- Host timing uses `up_before/up_after` as the release bracket.
- Indexed URI anchor remains two values:
  `request_before request_after`.
- Touch witness command shape is fixed:
  `touch-witness watch auto 12000` (preferred) or
  `touch-witness watch /dev/input/eventX 12000`.
  It writes one compact witness line to stdout and writes one `READY` line to
  stderr after the first successful sample.
- The witness stdout record is:
  `ATW1 STATUS window_start_us window_end_us down_before_us down_after_us up_before_us up_after_us sampled_ticks`
  where every timestamp is from device `CLOCK_BOOTTIME` microseconds.
- `STATUS=SINGLE` means one complete idle→single-touch→idle bracket.
  `STATUS=TIMEOUT` means no touch was observed in-window.
- Refusal paths print `refused <reason>` on stderr and return non-zero.
- Host launchers must redirect stdout to `out/<case>/touch` and stderr
  to a separate status file; passing an output path as argv is invalid.

## Device operation order (single pilot)

1. `prepare` and ownership checks complete.
2. Stock navigation enters Library and target uniqueness is confirmed.
3. Observer capture starts (`stock.frame`, then trace/watch as needed).
4. Start `touch-witness watch auto 12000` and wait for `READY`.
5. Stock tile tap dispatch writes four-value anchor.
6. Visit binds to app and confirms active app/session ownership.
7. One manual reversible control action in app.
8. Require the 12 s witness result to be either one complete contact bracket
   (`SINGLE`) or clean timeout (`TIMEOUT`).
9. Capture control evidence (before/after frame and response timestamp bracket).
10. Normal exit, stock restoration, light restoration, close and release.

## Current command interface

The current pilot device script uses these subcommands:

1. `admit LABEL`
2. `prepare LABEL tapcheck`
3. `pilot LABEL <manual_window_ms> [require-touch|allow-missing-touch]`
4. `collect-case LABEL control-warmup`
5. `discard-case LABEL control-warmup`
6. `close LABEL`
7. `absent LABEL`

## Required archive members (minimum)

- Trigger and stock binding:
  - `out/<case>/origin`
  - `out/<case>/anchor` (4-value for `stock-tap`)
  - `out/<case>/stock.frame`
  - `out/<case>/trace` (or equivalent bounded sampled evidence)
- Control-response evidence:
  - `out/<case>/control.before.frame`
  - `out/<case>/control.after.frame`
  - `out/<case>/control.anchor` (response observation bracket on `CLOCK_BOOTTIME`)
  - `out/<case>/control.method` (`manual-window` for this pilot)
  - `out/<case>/touch` (`ATW1` line only; no coordinates)

`control.state.before.json` / `control.state.after.json` are optional in this
pilot unless a validated in-app state export is available in the same visit.

## Host output fields (minimum)

- `down_before_us`
- `down_after_us`
- `up_before_us`
- `up_after_us`
- `first_response_observed_us`
- `first_response_is_upper_bound` (`true` when only first observed success is available)

## Failure and close semantics

- Reject and stop if anchor is missing, malformed, or clock order is invalid.
- Reject and stop if response evidence is missing or not bound to the same visit.
- Any interruption after possible mutation requires bounded restore and verified
  close before release.
- A missing verified close is blocking; do not reinterpret it as success.
- `close` is fail-closed when stage/state are already absent; host must preserve
  the original successful `close` record and use `absent` as a separate check.
