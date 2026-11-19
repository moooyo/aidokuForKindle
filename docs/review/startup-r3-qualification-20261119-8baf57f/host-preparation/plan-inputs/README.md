# Original R3 prepared-plan inputs

[The host audit](original-plan-inputs-v2.json) checked all 17 plans recorded as
prepared in the frozen handoff inventory. All 17 passed their host input checks,
and all 23 explicitly pinned replay dependencies matched. The two completed
default timing plans were excluded. This is preparation evidence, not a
physical qualification or release approval.

The audit ran exclusively on `test-env`. It did not import or execute a frozen
runner, acquire a lease, or connect to a Kindle. For every plan it checked the
handoff digest, runner, device script, payload, original launcher, archive
manifest, embedded R3 application and helper, and relevant option files.
Python compilation and `sh -n` checked source syntax without running the
application or device script. The import payload contains 20 generated CBZ
archives, all matching the manifest resolved relative to `comics/`.

The shared checks cover the exact producer receipt, staged R3 programs, required
host transport files, Python version, Pillow, SSH, and the named replay modules.
Transport credentials were neither printed nor copied. Device-side warm/cold
fixtures, current native state, and future reachability require the designated
operator's normal guarded preflight and were not inspected here.

At the audit's recorded observation time, `lifecycle.defaults` had execution
records and the other 16 original plans had not started. These are time-bound
observations during concurrent operator work, not permission to run a plan.
Consult the current private report before any execution; never rerun a consumed
plan. The new independent portrait pixel plan is outside these original 17.

The first audit attempt resolved the import archive manifest relative to the
payload root and therefore reported one false mismatch. Inspection of the
generator established that its entries are relative to `comics/`. Version two
rechecked all 20 members using that documented base and records the correction
and prior report digest. No frozen file or workload was modified.

| Inventory key | Host inputs |
| --- | --- |
| `all-options` | Passed |
| `import.pixels` | Passed |
| `lifecycle.defaults` | Passed |
| `pixels.def-c` | Passed |
| `pixels.def-w` | Passed |
| `pixels.grace` | Passed |
| `pixels.layout` | Passed |
| `pixels.prewarm` | Passed |
| `portrait.screen` | Passed |
| `reopen.immediate` | Passed |
| `reopen.ordinary` | Passed |
| `reopen.screen` | Passed |
| `reopen.ttl` | Passed |
| `reopen.wake` | Passed |
| `timing.v3-grace` | Passed |
| `timing.v3-layout` | Passed |
| `timing.v3-prewarm` | Passed |
