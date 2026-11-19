# R3 launcher admission

The candidate changes only `packaging/kindle/launch.sh` relative to the frozen R2 program sources. It keeps both the R2 application and the portrait helper byte for byte. It does not change the Library document, catalog registration, native ownership, screen capture, wake handling or the notification protocol. The previous R2 warm-device result remains a failed gate. These host measurements qualify a new candidate for a separate Kindle comparison; they do not establish device performance or installation eligibility.

When both data directories already exist, the Library waiter and worker no longer invoke `mkdir -p` for them. A regular, ordinary device-profile line is read with the shell builtin. Noncanonical endings and read errors retain the first-line `sed` compatibility path, including CR/NUL ordering. The standard absolute launcher path is already usable by its parent; only a nonstandard parent invocation must resolve it, and the worker never needs it. A missing or empty options file with no override skips the portrait-selector subshell. A populated file still goes through byte validation; explicit overrides and the original permission checks are unchanged.

| x86 Linux observation | R2 | R3 |
| --- | ---: | ---: |
| Standard Library entry, process creations through lock attempt | 12 | 2 |
| Standard Library entry, successful `exec` calls through lock attempt | 12 | 4 |
| Nonstandard relative entry, successful `exec` calls through lock attempt | 12 | 6 |
| Library entry with fresh logs/imports, successful `exec` calls through lock attempt | 12 | 5 |
| Isolated portrait dispatch, process creations when options are absent | 3 | 2 |
| Isolated portrait dispatch, process creations with an empty options file | 6 | 2 |
| Isolated portrait dispatch, process creations with saved `portrait=shell` | 6 | 6 |

`strace` followed real waiter and worker processes. Each admission comparison stopped at an intentionally preowned private lock, after checking the exact rejection and the preserved lock sentinel. Counts include the initial shell `exec`. For 30 alternating untraced pairs after two warmup pairs, the final replay recorded R2 and R3 medians of 6.843 and 1.867 ms, with a paired median improvement of 4.843 ms (30/30). Timing ends when the Library waiter returns; it excludes catalog dispatch, display takeover, GUI startup and e-ink rendering. Host times must not be added to or used to predict Kindle times.

The report also covers nine admission scenarios, thirteen portrait selections, twenty-two profile and directory cases (including an actual unprivileged read), eight complete foreground/Library/KUAL/retired-waiter lifecycles in shell and native modes, and seven catalog notification scenarios on each launcher. The full lifecycle checks use mock app and stock-service endpoints with real processes, groups, signals and ownership checks. They assert native state restoration and fixture child cleanup. A first discarded draft mapped a profile directory's `sed` read error from exit 4 to exit 1; R3 restores the original error and diagnostic. Successful write destinations were identical in the admission traces. The report hashes twelve unchanged ownership, notification and restoration functions as well as both launchers, the application and the helper.

`host-receipt.json` is the portable summary from one complete fresh replay. `worktree-receipt.json` separately compares the worktree before and after the integration with the frozen source, the tested R3 script, and the replayed two-hunk patch. Raw fixture output stays on the test host; the reviewed summary contains no device addresses, credentials or user library data.

Replay on `test-env` with a fresh output filename, the frozen R2 launcher, and the candidate launcher:

```sh
python3 -B -I check_host.py --baseline /path/to/r2/launch.sh \
  --candidate /path/to/r3/launch.sh --output /path/to/fresh-report.json
```

The verifier pins its four fixture drivers, the patch and the frozen test inputs. The optional `--application`, `--helper`, and `--frozen-fixture` arguments allow another source location with the same hashes. `--keep-raw` takes a new private directory on the test host. The fixture requires an existing `nobody` account, Python, `strace`, `patch`, and standard Linux tools. The checker refuses changed artifacts, a partial run, or an existing output file.
