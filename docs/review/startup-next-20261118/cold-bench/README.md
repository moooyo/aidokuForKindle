# Cold local cover comparison

This experiment measures the delay when six local cover sources exist but the
application has no rendered derivatives. It does not evict the kernel page cache,
reboot, contact an upstream server, or measure physical touch or optical latency.
The reference is the installed application from `78c5b2b`; the candidate is its
independent cold-validation build with production defaults. Neither arm changes
the installed Library document, catalog registration, or launcher implementation.
The common temporary research bridge is removed after each leased batch.

The host materializer derives a separate runner and device script from exact hash
pins of the preceding warm experiment. The warm files and their frozen batches are
never changed. `prepare` copies the generated runner and device script, input
archive, build identities, and predetermined ordering into a fresh private batch.
Separate `run` and same-owner `recover` operations use those frozen bytes.

One read-only backup of the user library database and one copy of the profile
form the immutable private seed. Every launch, **including both warmups**, uses a
previously nonexistent data directory containing just those two verified copies.
The `cache/` path is absent before the 8-second native stabilization interval
starts. The application initializes its own cache and index under that case's
directory. A warmup can therefore warm OS pages but cannot warm the next launch's
application cache. No user cache or index is deleted or rebuilt.

Before and after each batch, the harness checks the installed program/document
identities and original library, input roster, boot, lights, native control,
catalog identity and registration. It records the original cache artifact tree,
sizes, mtimes and index bytes privately. Each case must have exactly six local
derivative misses, no local derivative hits, six completed jobs, a complete
visible projection, and a single successful recent-access notification. Absolute
paths, fixture hashes, real screenshots, and user metadata stay in private evidence.

The app remains active for 20 seconds in timing samples. A separate qualification
captures from the actual framebuffer with a 24-second dwell and records a timestamp
immediately before capture, so the analyzer can reject images taken before all
covers finished. The primary endpoint runs from the real indexed file-URI request
to the accepted frame containing every visible cover. `max(first frame, software
input readiness)` is the secondary endpoint. Six A/B pairs are screened; an
eligible result then needs ten new pairs and independent pixel/lifecycle checks.

The designated operator prepares on `test-env` before taking any lease. From the
independent host directory:

```sh
python3 -I adapter/materialize.py
python3 -I adapter/test_materialize.py -v
python3 -I tools/run_bench.py prepare --mode screen --primary covers \
  --name cold-screen --candidate-app /path/to/qualified/candidate-arm
```

The last command prints the frozen plan pathname without touching the Kindle.
Only when the device is available does the operator run `python3 -I -u runner.py
run plan.json` from that plan's directory. The private report records successes,
deadline/failure cases, input restoration, and exact lease release. Do not retry
with an already consumed plan. The operator must complete bounded recovery under
the original ownership before interpreting a failed batch or acquiring a new one.
