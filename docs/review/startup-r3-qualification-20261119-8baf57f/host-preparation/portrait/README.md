# Independent R3 portrait pixel preparation

The first prepared pixel batch later completed with a retained strict
right-toolbar failure. See the [first-attempt evidence](../../evidence/portrait-first-attempt.md)
and [read-only source/timeline diagnosis](../../status-query/README.md).
The fresh qualification below is a separate plan; the original batch must
not be executed again.

The later fresh `a9497684` qualification also completed and passed the original
strict body and right-status-bar comparison. Its
[accepted paired report](../../evidence/portrait-screen-requalified.json) uses
exactly the original six timing pairs. Both plans are consumed. The preparation
receipts describe their earlier states and do not authorize another execution.

On November 19, 2026, the continuation prepared the missing independent
shell/native portrait pixel qualification on `test-env`. No device runner was
executed, no Kindle connection was made, and no lease was acquired during this
preparation. The original six-pair portrait screen remains unchanged.

The existing tool was used without modification:

```sh
python3 -B -I /root/aidoku-all-bench-20261119/portrait-v3/prepare_portrait.py qualify
```

The new frozen plan is
`/root/aidoku-all-bench-20261119/portrait-v3/private/aidoku-all-v3-portrait-7f0ded74/plan.json`,
with SHA-256
`876931cbff76c0757f485bb19931cd0f580d454e3f51dd2c427fd89447441329`.
It contains baseline and candidate warmups followed by one qualification for
each arm. Both arms use R3 application `1b1c508e...`, launcher `412163f1...`, and
portrait helper `bde28cd5...`. Their only setting difference is `portrait=shell`
versus `portrait=native`.

The host preparation replay invoked the existing `analyze_v3.py`
`verify_release`, `base_measure`, and `checked_plan` functions, selecting the
pinned portrait runner and warm device harness. It checked the release receipt,
all three programs, exact launcher instrumentation, complete payload members,
case order, and the single-option difference. The plan had no execution,
transport, or lease evidence at that observation. This proves prepared inputs,
not device behavior. The [preparation receipt](prepared-7f0ded74.json) records
the exact hashes and scope.

Only the designated device operator may execute the new plan, from its private
directory, with `python3 -B -I -u runner.py run plan.json`. Check its unused state
immediately before starting; never rerun a consumed plan. Device recovery and
lease ownership follow the existing runner and project instructions.

After both the original portrait screen and this independent pixel batch have
completed and released their leases, replay them together on `test-env`:

```sh
python3 -B -I /root/aidoku-all-bench-20261119/tools/analyze_v3.py analyze \
  --batch /root/aidoku-all-bench-20261119/portrait-v3/private/aidoku-all-v3-portrait-0d7ea40e \
  --qualification-batch /root/aidoku-all-bench-20261119/portrait-v3/private/aidoku-all-v3-portrait-7f0ded74 \
  --trial supervisor --workload warm --phase screen \
  --output /root/aidoku-r3-handoff-20261119/host-preparation/portrait/supervisor-screen-with-pixels.json
```

The output must be fresh. Inspect both functional qualification and the
predeclared latency gate. A successful six-pair screen requires ten independent
confirmation pairs before a confirmed speed benefit can be claimed. Pass the
new pixel batch directory to the later `assemble_r3.py --portrait-pixels`
argument; preparation alone cannot satisfy that release input.

## Fresh strict qualification after source review

The original `prepare_portrait.py qualify` tool prepared another independent
pixel batch without changing any programs, settings, runners, or comparison
rules. The new plan is
`/root/aidoku-all-bench-20261119/portrait-v3/private/aidoku-all-v3-portrait-a9497684/plan.json`,
with SHA-256
`710f5baac750d18f275ffe524bd862c2f2f317b5e06dd07d332d5cb5ec4963c8`.
The [fresh preparation receipt](prepared-a9497684.json) verifies the complete
payload and exact program/settings/runner/case-order equality with the first
pixel plan, while requiring a different run and owner identity. It had no
execution or lease evidence at preparation. Device execution must obtain its
own fresh lease token.

The source review found the same integer `powerd.battLevel` query path for
both supervisors. It did not establish a definite product defect or preserve
raw firmware values. The portrait candidate's rejected early snapshot and
later ordinary query remain separately documented; no 80 ms query budget or
pixel mask was changed.

After the designated operator executes and releases this fresh batch, bind
it to the original six timing pairs, using a new report:

```sh
python3 -B -I /root/aidoku-all-bench-20261119/tools/analyze_v3.py analyze \
  --batch /root/aidoku-all-bench-20261119/portrait-v3/private/aidoku-all-v3-portrait-0d7ea40e \
  --qualification-batch /root/aidoku-all-bench-20261119/portrait-v3/private/aidoku-all-v3-portrait-a9497684 \
  --trial supervisor --workload warm --phase screen \
  --output /root/aidoku-r3-handoff-20261119/reports/portrait-screen-requalified.json
```

The first failed report stays unchanged. This fresh qualification does not
repeat the six timing pairs or convert their three favorable primary pairs
into the required five. No ten-pair confirmation plan was prepared.
