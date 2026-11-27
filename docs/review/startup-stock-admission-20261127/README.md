# Scribe ordinary-card admission and generation timing

This diagnostic archive accompanies the [current handoff](../../../HANDOFF.md).
The last verified Scribe daily development delivery is [`f3e5070`](evidence/f3e5070-delivery.json).
`stock-admission-v2` is test tooling only; it is not part of the installed
launcher, helper or GUI. No Scribe `f3e5070` ordinary-card timing sample has
been accepted. No schedule, authorization or shared lease is transferred by
this document.

## What the evidence says

- The [historical replay](evidence/historical-stage-replay.json) is from
  `83cac53`. One ordinary card with the earlier marks showed **150–170 ms**
  from before the observer fork to entering the wait and **0–10 ms** of
  remaining checked wait. Eight separate managed foreground visits gave a
  **10–60 ms** envelope for the preparation. These are not paired visits;
  the longer ordinary interval is a lead, not a removable cost. Two older
  ordinary visits put card release to worker birth at about **0.51–0.58 s**;
  neither retained the intervening Library waiter birth.
- The first-generation probe retained the ten original markers and added
  nine parent and three child marks. The scoped shell builtins record
  fork return, nonce/epoch/generation/phase records and cleanup, plus the
  child record write and rename. They do not spawn another process for
  marking. The probe was exercised through the managed Library entry on
  isolated [Scribe and PW6 firmware](evidence/firmware-qualified.json), not
  qualified by a successful newer physical-card sample.
- The first [locator](evidence/physical-locator-evidence.json) and a later
  [locator](evidence/native-restoration-physical-locator.json) each found one
  complete official card on a fresh native Library frame after handback.
  The [first measurement](evidence/physical-measurement-rejected.json) opened
  it once and failed native closeout. A bounded repair reply timed out, but
  exact readback and independent cleanup eventually verified return. The
  [next attempt](evidence/native-restoration-measure-admission-rejected.json)
  was refused at read-only admission before upload, overlay or a card
  opening. Its release was verified; the failure predicate remains unknown.
  Neither result becomes a sample after verified handback.
- The [latest operator qualification](evidence/stock-admission-qualified.json)
  and [fixture results](evidence/stock-admission-fixtures.log) record 63
  off-device checks: six admission, 42 recovery/custody, four transport,
  four typed phase, two sealing, three ARM native-navigation and two firmware
  BusyBox timeout checks. The protocol is the same; the archived host code
  resolves lab inputs from private configuration instead of embedding paths.
  The [initialized Library feasibility result](evidence/initialized-library-readiness.json)
  is closed and does not justify replacing the loading frame.
- The archived revision passed [82 checks on `test-env`](evidence/archive-verification.json):
  64 integrated (including a regression for preserving a typed refusal without
  publishing a sample), 13 launcher generation and five directory-lock tests.
  The regenerated device scripts, launcher and decompressed locator archive
  match the earlier inputs byte for byte; the transport differs only in the
  gzip timestamp. See the [archived test results](evidence/archive-fixtures.log).

## Resume off-device first

The operator is paused. The previously announced ready entry was withdrawn.
On `test-env`, the owner's private state directory
`$HOME/.local/state/aidoku-stock-admission-20261127` holds `resume.json` (the
verified private workspace pointer and pause status) and `inputs.json` (lab
bindings). They contain neither a credential nor an authorization. Do not
copy them into Git or print their contents in chat. The previously staged
locator is unused; there is no measurement payload or live guard for that
workspace. A missing workspace is a reason to rebuild and preflight a fresh
plan, not to dispatch a historical frame or reuse an earlier attempt.

After syncing `main`, use the configured SSH agent and start with a host-only
check on `test-env`. The code is meant to run in the private work directory,
not in the checkout. Read these values locally and do not echo private paths:

```sh
state="$HOME/.local/state/aidoku-stock-admission-20261127"
export AIDOKU_STOCK_INPUTS="$state/inputs.json"
work=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["workspace"])' "$state/resume.json")
python3 -I "$work/run_stock_generation.py" preflight locator
```

This preflight expands the pinned SSH configuration locally and checks the
packet; it does not connect to the Kindle or acquire the lease. Verify that
there is no send intent or attempt, and that the expected installation still
applies. If the software version or official entry has changed, do not edit
an old receipt or relax the comparisons; the retained plan no longer applies.

When the user authorizes continuation, follow the [ready queue](READY_QUEUE.md).
A single explicit approval can include the locator and one conditional
opening. Report ready only with approval and complete material, take a fresh
lease just before commands, and verify release immediately. First run the
no-input locator from an already active native Library: no wake, pointer,
page/tab action or launcher mount. The later offline `seal` step requires its
closed fresh frame, checks unambiguous Library and one whole official card,
and tests the match independently with the ARM selector. Only then preflight
`measure` and announce ready again; there is no reservation while sealing.

The physical runner takes the approved expiry and per-visit budget through
`--approval-expires` and `--max-visit-seconds`. The respective minimums are
299 and 641 seconds, including bounded recovery, not booked slots. It also
requires an explicit `--confirmed-one-physical-visit` and the private stdin
credential transport; do not put credentials in command arguments or source.
A busy lease opens no connection. On a refusal, stop: do not wake, navigate,
retry or open a card manually. If mutation may have occurred, only the original
owner may do the bounded recovery and independently verify absence/release.
A refused or truncated reply cannot be passed to the timing decoder.

## Maintainer verification on `test-env`

The source in [`tools/`](tools/) uses a private, root-owned mode-0600 JSON file
selected by `AIDOKU_STOCK_INPUTS`. `schema` is `1`; `paths` maps the keys below
to canonical approved local paths. `device_hostname` is the separately bound
expected IPv4 host for the pinned SSH configuration; it is used only by the
host preflight/physical transport. There is no default device address.

| Private resource | Required path keys |
| --- | --- |
| Existing Linux operator, stock templates and offline readers | `base_runner`, `stock_templates`, `stock_reference`, `staging_tools` |
| Reference files and original delivery proof | `generation_lab`, `development_release`, `physical_delivery` |
| Offline official-card selector and replay fixture | `classifier`, `historical_fixture` |
| Pinned physical credentials adapter and optional firmware lab | `askpass`, `emulator` |

Use a new private `aidoku-stock-generation-20261127.*` workspace on `test-env`
for qualification; copy only reviewed sources there. Never overwrite the
paused or an active workspace. `prepare_stock_generation.py prepare` generates
both scripts, proves the trace-only launcher matches the qualified derivative,
and stages only a locator. The test scripts use Linux subprocesses, disposable
locks and a simulated device filesystem; no physical SSH invocation or real
shared lease is needed. The sealing fixture uses an archived frame privately
and cannot produce a live measurement guard.

Run `test_stock_admission.py`, `test_stock_generation_recovery.py`,
`test_stock_generation_transport.py`, and `test_stock_generation_seal.py`.
Pass the retained `phase_records.py` to `test_generation_phase_records.py`.
`test_generation_probe.py` takes the repository root at `f3e5070` for its
13 targeted launcher checks; `test_stock_lock_fence.py` exercises a real
Linux flock with a removed or replaced journal directory. The ARM input simulation needs the existing
OCR/library kit on an isolated Xvfb display; the timeout tests take the two
firmware applets from the local images and run them under QEMU. These are not
new full firmware or physical visits. The final `qualify_stock_operator.py`
requires the seven integrated suites' real logs and regenerated templates.
The archived qualification is historical documentation, not the preflight or
approval for a different payload. The archived reports contain aggregates
and refusal/release states, not raw frames, traces, device identities or lease
credentials.

## Choose the next experiment from a valid sample

The post-release analyzer first validates the ordinary visit, matching worker,
GUI and waiter, input bracket, scoped markers, native exit and restoration,
framebuffer envelope and the unique loading observation. Report the preparation
(`observer_start` to `observer_wait_begin`), remaining wait and total directly
from matched time buckets. A same-bucket interval is **0–10 ms**, not zero.
Do not add the concurrent child stages to the parent total. A child's atomic
record publication is bounded by the pre-rename marker, rename return and
parent acceptance; their log order is not a cross-process clock. Publication
is not proof of the service's subscription.

Only consider a local write or cleanup optimization if it reproducibly explains
a long preparation and the same visit shows useful slack before the parent's
wait (a positive lower bound for wait-entry time minus record publication).
If the bounds overlap, the order is unresolved. If the record is late,
shortening preparation can simply turn it into additional wait. If the long
preparation does not recur, instrument the ordinary-card release-to-worker
interval rather than bypassing native dispatch. Keep the existing safety and
input fences. The sensor descriptor release had no verified equivalent
application-side shortcut; do not move orientation reads across its close.

These are digital intervals only. Neither loading, framebuffer match, driver
SEND nor WAIT proves the actual visible Library endpoint. Keep the loading
display duration out of the user's first-screen metric; do not compare
unpaired runs or infer a production speedup from one diagnostic visit.
