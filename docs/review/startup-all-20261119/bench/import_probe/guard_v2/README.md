# Read-only cache evidence and independent short trials

The original six-pair batch `aidoku-all-import-screen-362b98e8` is **invalid**.
All 28 planned application stages completed. The launcher, native Home, lighting,
boot and input state were restored, the owned staging area was removed, and the
lease was released. The installed/user-state integrity gate failed. No timing
from this batch may support qualification, be combined with a later batch, or
be exported as a release result. The original tools and private records remain
unchanged.

The before/after guard contained 38 lines. Only line 12 differed on both
restoration observations; the two after records were byte-identical. The old
guard computes that line by sorting the relative name, size and whole-second
mtime of regular files under the installed cache's `raw` and `processed`
directories and hashing those records. The separate filename-set digest, cache
index bytes/WAL presence, program and library fingerprints, and all other
snapshot lines matched. The three guard calls had empty stderr. There are no
old per-file records or per-file content digests for the installed cache.
Consequently, neither the affected file nor what changed (size or timestamp)
nor a writer can be identified. Stable filenames/indexes are insufficient
evidence for unchanged file contents; an aggregate mismatch is not proof of
application corruption either.

In the frozen payload, both application invocations (normal and resume) use
`--data-dir` beneath their run and arm; the bridge binds it to the private case.
All 28 actual `state.json` database paths agree exactly with their assigned
case. The generator does not copy the installed database or image cache into
this workload. Both the synthetic sort change and all imported content and
derivative operations target that case directory. `CacheManager` derives its
root from the selected data directory. The old guard reads the installed cache
index and uses `find`/`stat` on the artifact trees; no explicit write to the
installed cache was found in the frozen scripts. The normal indexed document
still writes its normal entry log, outside the protected cache. This is a source
and path audit, not a system-wide writer trace. The cause remains unknown.

Version two is a separate host installation at
`/root/aidoku-all-bench-20261119/import_probe_r2`. It reuses the pinned V1 guard,
including the original 38-line comparison, and additionally captures private
inventories after owned staging but before activation of the bridge, and after
restoration of the launcher. Each inventory records relative file name, size,
mtime to nanoseconds, and full SHA-256. A reconstruction of the original
whole-second aggregate from each inventory must match its respective original
guard. Exactly matching inventory records (including contents) are an additional
acceptance condition. Raw rows and any private path-specific changes remain in
the private run directory. The new observer runs outside every timed case.

The only ARM diagnostic is a static read-only executable targeting the two
installed artifact subtrees. It has no path or command arguments. It opens
verified directories and regular files by descriptor with `O_NOFOLLOW` and
`O_NOATIME`, fails closed when those flags are unavailable, rejects symlinks
and special files, and checks file identity, size and times both before and
after reading. It never writes the observed files or resets timestamps. It
will refuse an unstable or oversized inventory (8,192 files, 512 MiB total,
16,384 entries, four directory levels and a 60-second deadline). A refused
initial inventory stops the test before the first case. As with all endpoint
snapshots, a matched pair does not prove that no transient writes occurred
between the observations or identify a writer.

Only two predetermined three-pair screen batches can be frozen. The first
covers 1-3 (`AB/BA/AB`); the second covers 4-6 (`BA/AB/BA`), with independent
fresh lease/run identities and all restoration checks each time. Independent
qualification uses both arms, both phases, native pixels and the same new
inventory. A failure only in the read-only cache gate invalidates the batch;
when the bridge and all other owned native state are demonstrably restored,
the device can still be cleaned up and released without modifying user files.
Unrelated native, installation or registry drift retains the existing recovery
guard; no third-party changes are ever overwritten. The old and new batch
families cannot be mixed in the new analyzer.

On test-env, set `T=/root/aidoku-all-bench-20261119/import_probe_r2/tools` and
`ELF` to the verified new candidate. Both sides use exactly these same program
bytes. The private control file selects `off` or `idle`, independently of the
new product default.

```sh
python3 -I "$T/run_bench.py" prepare --trial import --mode screen --round-start 1 --rounds 3 --name imp2a --primary covers --baseline-app "$ELF" --candidate-app "$ELF" --baseline-option import_thumbnail=off --candidate-option import_thumbnail=idle
python3 -I "$T/run_bench.py" prepare --trial import --mode screen --round-start 4 --rounds 3 --name imp2b --primary covers --baseline-app "$ELF" --candidate-app "$ELF" --baseline-option import_thumbnail=off --candidate-option import_thumbnail=idle
python3 -I "$T/run_bench.py" prepare --trial import --mode qualify --name imp2q --primary covers --baseline-app "$ELF" --candidate-app "$ELF" --baseline-option import_thumbnail=off --candidate-option import_thumbnail=idle
```

Only the designated device operator executes the frozen `runner.py run
.../plan.json`. After both fresh screen halves and separate qualification:

```sh
python3 -I "$T/analyze_import.py" --phase screen --batch "$FIRST_THREE" --batch "$LAST_THREE" --qualification-batch "$QUALIFICATION" --output "$REPORT"
```

Qualified tool SHA-256 values:

| File | SHA-256 |
| --- | --- |
| `run_bench.py` | `c0d69c5ac8702d4b483f06665a10e8646fc28053d7015c808ed771676fc66ee4` |
| `bench_device.sh` | `3c9bdfde3fc87385283d1070a37340e950c9d0a037ffd421037e67f0e3c7a7ad` |
| `analyze_import.py` | `fe4039045cbb1cde54a3d18313e0eef31a6cfbddfa45158a4d62435b405311a6` |
| `cache-inventory` | `7d9a37d00eb41e0132d5b47931e2f41ef8aac345f31449e195b23e513f559dc4` |

The five test-env host fixtures cover original failure replay, exact inventory
and no access-time updates, content changes at unchanged size and timestamp,
metadata-only changes, truncation and symlink rejection, and the permitted
screen/qualification orders. These are preparation and diagnostic checks, not
new device performance results.
