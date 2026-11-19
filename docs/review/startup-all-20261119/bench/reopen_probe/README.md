# Reopening the Library with retained cover pixels

This compares two cooperative exits and two real opens of the existing indexed
Library item. The steady-state system is the deployed 9d92dc4c application;
both private measurement arms use the **same R2 program** and the same launcher
and portrait helper. The only configured difference is `resident=off` versus
`resident=45`. No document, registry, icon or user data is replaced for this
feature. The same temporary, guarded launcher bridge that the main benchmark
uses is retired after the batch.

For each arm the first opening gets a new private copy of the sealed warm library
and cache. The second opening reuses precisely that arm's data directory and ELF
inode. The baseline uses the normal `app/exit` action; the candidate uses the
explicit `app/quick-return` action. A timed action runs six seconds after software
readiness in both arms (18 seconds for fixture warmup) after the complete Library
frame. The ordinary 30-second execution deadline is a failure backstop. Both
second openings request an ordinary exit. A deadline alone is insufficient:
the analyzer requires the captured `exit_requested` event in the right order.

Each open must produce a newly admitted GUI PID and birth stamp, the usual checked
display/input boundary, all seven visible slots, stable catalog identity,
advancing last access and exactly one successful owned notification. Both visits
must return display control to native Home. The keeper is a different same-ELF
process and independent session. A successful handoff needs the same observed
keeper before the second request, explicit reclaim by the fresh GUI, and at least
one `cover_resident_pixels_hit`. A miss remains in the speed calculation. Ordinary
exit must leave no keeper. These are software boundaries, not optical tap times.

The operator can prepare and check the tools on `ssh test-env` without a lease:

```sh
python3 -I /root/aidoku-all-bench-20261119/reopen_probe/adapter/materialize_reopen.py
python3 -I /root/aidoku-all-bench-20261119/reopen_probe/adapter/test_probe.py -v
python3 -I /root/aidoku-all-bench-20261119/reopen_probe/test_analyze_reopen.py -v
```

The exact arguments for the comparable arms are `--baseline-app` and
`--candidate-app` pointing to the same R2 ELF, the same `--*-launcher` and
`--*-extra aidoku-startup-portrait=...`, `--baseline-option resident=off` and
`--candidate-option resident=45`. Prepare with `run_bench.py prepare --trial
reopen --primary covers --scenario immediate --mode pilot|screen|confirm --name
<label>` plus the preceding inputs. The command freezes a new private plan. Only
the physical operator runs `python3 -I -u runner.py run plan.json` from its
directory; never rerun a consumed plan. Compare all six predetermined screen
pairs and, only if the predeclared rule passes, ten independent confirmation
pairs (five in each order). `analyze_reopen.py` accepts one `--batch <directory>`
per batch, `--phase`, `--candidate-sha256`, and `--output <public.json>`.

Reports show the second request to complete covers, the first and second usable
frames, the two entire visits including their exits, and the interval in Home.
They also report the duration of the three resource observations. CPU tick
deltas are raw process counters and PSS/RSS are observed memory, not battery
consumption. UID-checked transport and content proof checks are part of the
application. The observer separately checks keeper executable/cmdline, birth,
session and the absence of framebuffer, input or database descriptors. Only the
three `/dev/null` streams and one listening Unix socket may remain, apart from
a transient `/proc/meminfo` read for the pressure policy. Public data omit process
identifiers, source paths, raw snapshots, private timestamps and user captures.

Independent `--mode qualify --scenario ttl|ordinary` batches verify expiration
and normal full exit. The TTL batch observes natural retirement, then a fresh
ordinary open with no reclaimed data. The ordinary case asks the candidate to
exit normally even though retention is enabled; it must not start a helper. A
separate second-visit powerd sleep/wake qualification records the **new** GUI's
identity, released/reacquired input, matching restored Library content and visible
toolbar. It is not a claim that the *original* GUI survived return to Home.

`loss_qualification.py` is an optional additional qualification for unexpected
keeper disappearance. Its staged test helper can signal only a proven private
candidate keeper through a kernel `pidfd`, with case, run owner, executable,
command line, birth and session checks. The fixed helper is not packaged with
the product. An unsupported `pidfd` API stops before the launcher bridge is
activated; do not substitute a numeric-PID signal or count this as device proof.
`reopened_wake.py` similarly derives only the independent two-visit sleep/wake
qualification and never modifies a frozen performance batch.

The physical benchmark never creates system-wide memory pressure. It privately
records available KiB at the three observations. The separate production
transport's [host qualification](../../resident/host-verification.json) covers
pressure refusal and loss of headroom during service with controlled low/unknown
input. Do not equate this with a low-memory device trial or an idle battery test.
