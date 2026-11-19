# R3 specialist trials

Run all preparation and verification on `test-env`. The specialist batches
stage the identical R3 application, launcher, and portrait helper on both
sides. The production 9d92dc4c application and 10432125 launcher remain the
protected installed state. Every batch uses a fresh case and lease identity.
The operator alone runs the sealed plan from its directory with
`python3 -I -u runner.py run plan.json`; never rerun a consumed plan.

The [handoff inventory](../../../../HANDOFF.md) on `test-env` maps these
keys to the frozen plan and its SHA-256. A prepared plan is not a result.

| Priority | Trial | Inventory key |
| --- | --- | --- |
| 1 | All settings, native visuals and ordinary exit | `all-options` |
| 1 | Six warm portrait pairs, `shell` vs `native` | `portrait.screen` |
| 2 | Immediate two-visit retention | `reopen.immediate` |
| 2 | Configured retention with ordinary exit | `reopen.ordinary` |
| 2 | Natural retention expiry | `reopen.ttl` |
| 2 | Short sleep and wake on the second GUI | `reopen.wake` |
| 2 | Six warm reopen pairs | `reopen.screen` |
| 3 | Import visual qualification | `import.pixels` |

The first entry is independently prepared against the frozen R3 warm default
plan (`timing.v3-default-w`). In the examples below, substitute the actual
plan pathname for `PLAN`, the specialist directory for `BATCH`, and a fresh
output filename for `REPORT`. The existing frozen all-options plan does not
need to be prepared again.

```sh
python3 -I tools/analyze_v3.py prepare-all-options --release-plan PLAN
python3 -I tools/analyze_v3.py analyze-all-options --batch BATCH \
  --release-plan PLAN --output REPORT
python3 -I tools/analyze_v3.py analyze --batch BATCH --trial supervisor \
  --workload warm --phase screen --output REPORT
```

Enabling all settings on entry and exiting ordinarily does not exercise
importing or keeper handoff.

The other plans are prepared by `tools/prepare_r3_specialists.py` with scope
`portrait`, `reopen`, or `import`; preparation creates new plan identities.
Replay any completed reopen or import batch independently with:

```sh
python3 -I tools/analyze_r3_specialists.py SCOPE --batch BATCH \
  --plan-sha256 EXPECTED_PLAN_SHA256 --output NEW_REPORT
```

Valid scopes are `immediate`, `ordinary`, `ttl`, `wake`, `screen`, and `import`.
The replay checks exact staged executable bytes and instrumentation as well as
the frozen plan, tool identities, protected snapshots, and release evidence.
Each replay is independent. A valid screen does not imply a passing latency
gate; inspect `qualified`. The reopen screen counts all six preselected pairs,
including fallback visits. It requires five faster second-cover pairs, a
median saving of at least 100 ms, and a second-usable median regression of at
most 50 ms.

Reopen uses two native indexed document requests through the actual appmgrd
entry, and scheduled real app actions for quit and quick return. It does not
measure physical taps. A fresh application GUI owns each visit. The short
sleep uses the already admitted second GUI, with an independent identity,
input ownership and pre/post framebuffer comparison. Only the data keeper
can survive native Home. The 2 MiB limit is the decoded cover payload bound
in the shared production ELF; observed keeper RSS and PSS include other
process memory and are not estimates of idle battery use.

The import trial covers the same synthetic archive, shelf order, and persisted
`off`/`idle` comparison as the guarded historical import workload. It adds
the exact R3 launcher on both sides and carries no latency claim. Its original
stock launcher/ELF result remains evidence for that specific composition.
Do not treat it as an R3 native admission, visual, or timing result.
