# Fresh All-settings pixel qualification

This plan subsequently completed and passed the unchanged strict checks.
The [accepted public result](../../evidence/all-options-requalification.json)
and [continuity record](../../evidence/strict-requalification.json) retain its
separate execution outcome; the preparation receipt below describes the earlier
unconsumed state and must not be used to run the plan again.

After retaining the first All-settings batch's right-toolbar failure, the
existing R3 preparation tool froze a fresh independent qualification on
`test-env`:

```sh
python3 -B -I /root/aidoku-all-bench-20261119/tools/analyze_v3.py prepare-all-options \
  --release-plan /root/aidoku-all-bench-20261119/base-warm/private/aidoku-all-v3-default-w-4ccd1128/plan.json
```

The new plan is
`/root/aidoku-all-bench-20261119/all-options-v3/private/aidoku-all-all-options-55a09c24/plan.json`.
Its SHA-256 is
`171045fb408ed6e190cc39651550b6b8450e3368dbec7a764ab9095aac497f76`.
The [preparation receipt](prepared-55a09c24.json) binds it to the same frozen
R3 default plan, identical programs on both sides, and the reviewed six-setting
difference. It also records the unchanged runner and device-script identities,
payload, release binding, and four planned warmup/qualification cases.

The preparation replay called the existing analyzer's `verify_release`,
`all_options_modules`, and `checked_plan` functions. No device runner was
executed and no lease was acquired. The new plan had no execution or lease
record at the preparation observation. Its execution must use a fresh lease
owner and token; neither the previous batch nor its lease may be reused.

The original strict comparison remains intact: full body below the top
72 pixels, plus the observable right region at x=930, y=0, width=930, height=72.
No image normalization, relaxed tolerance, or alternate mask was added.

After the designated operator completes this new batch and releases its lease,
run the unchanged analyzer on `test-env`, using a fresh output:

```sh
python3 -B -I /root/aidoku-all-bench-20261119/tools/analyze_v3.py analyze-all-options \
  --batch /root/aidoku-all-bench-20261119/all-options-v3/private/aidoku-all-all-options-55a09c24 \
  --release-plan /root/aidoku-all-bench-20261119/base-warm/private/aidoku-all-v3-default-w-4ccd1128/plan.json \
  --output /root/aidoku-r3-handoff-20261119/reports/all-options-requalification.json
```

The original `all-options.json` remains a separate failed result. The new
All-settings report is an independent qualification requirement and cannot
be inferred from `assemble_r3.py` or the existing package gate.
