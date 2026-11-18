# Last-cover grace experiment: analysis qualification

Use the unmodified measurement runner from `../../tools/run_bench.py`. Compare
the installed application from `78c5b2b` against only the candidate application
ELF, with the original launcher and native query helper, their ordinary default
arguments, the same indexed Library entry, and the same warm offline workload.
Only the designated device operator runs a prepared plan. Preparing and
analyzing plans does not acquire a lease or connect to a Kindle.

On test-env, replace `CANDIDATE_ARM` with the frozen successful build (six
screening pairs are predetermined; each half of a later confirmation uses
`--mode confirm --rounds 5` and its predeclared `--round-start 1` or `6`):

```sh
python3 -I /root/aidoku-next-bench-20261118/tools/run_bench.py prepare \
  --mode screen --primary covers --name frame-screen --candidate-app CANDIDATE_ARM
```

The separate analysis overlay is `../../frame-bench/analyze_frame.py`. To audit
results from one completed batch (use repeated `--batch` for a split confirmation):

```sh
python3 -I /root/aidoku-next-framebench-20261118/tools/analyze_frame.py \
  --batch PRIVATE_BATCH --phase screen --primary covers --output PUBLIC_RESULT.json
```

The primary endpoint is the complete visible cover submission, measured from
the native file-URI request. The input guard is the later of first Library SEND
and software input readiness. Report all paired values, their order, each
partial/complete frame and the last-cover grace outcome. A zero-trigger launch
is an observation even when the candidate would never have changed that case.
Do not treat a grace-completion event alone as evidence that an extra driver
submission was avoided, or absence of that frame as a causal result in all
cases. Preserve separate latency and exercised-mechanism decisions. The screen
requires at least 5/6 positive paired results and a paired median of at least
100 ms; independent confirmation requires at least 9/10. In either phase, the
other endpoint's median paired regression must be at most 50 ms.

`test_analyze_frame.py` exercises synthetic positive/negative paths through
the independent analyzer on test-env, outside a lease. Its invented latency,
catalog rows and pixels are never physical performance evidence.
