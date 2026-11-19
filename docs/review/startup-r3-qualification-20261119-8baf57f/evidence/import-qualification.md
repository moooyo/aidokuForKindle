# R3 idle-import correctness and pixels

The original specialist analyzer passed the independent R3 import qualification:
`valid=true`, `qualified=true`, `source_images_verified=true`, and
`paired_native_pixels_verified=true`. The [public report](import-pixels.json)
covers eight completed stages, with 22 recorded operations and zero failures.
Its `performance_claim` remains false. The earlier R2 economic timing results
remain separate; this qualification supplies no R3 timing pairs or speed claim.

Both modes imported the same generated sources. The target four books were
outside the visible shelf during import and visible after reopening. With
`import_thumbnail=idle`, each import performed four idle jobs and persisted
four verified target derivatives. Those four target pixels match the later
reopening. The `off` control performed no idle thumbnail work.

| Mode | Target derivatives after import | New derivatives on reopen | Reused distinct covers on reopen |
| --- | ---: | ---: | ---: |
| Off | 0 | 4 | 11 |
| Idle | 4 | 0 | 15 |

The same pattern holds in warmup and independent qualification. The common
source-bound pixels match between modes, and the final derivative sets after
reopening are identical. No generated source title, cache key, image hash, or
user image is present in the public export.

[Retained process evidence](import-process-evidence.json) identifies eight
different GUI process IDs across the eight complete application traces.
Every ingest/reopen pair has different IDs. Each prior GUI completed its
ordered shutdown and returned to native Home before the subsequent native
open request. The identities come from `application.json`; an independent
kernel process-birth witness was not collected, and raw IDs are not exported.

The native ingest pictures match between modes, and the native reopen pictures
also match between modes. Each comparison covers the complete 1860-by-2480
body below the top 72 pixels and the observable right status-bar region at
x=930, y=0, width=930, height=72. All four region comparisons have zero
differing pixels and satisfy the original contrast requirement.

The installed user-cache inventory contains 55 files before and after the
batch. Added, removed, size, nanosecond-mtime, and content changes are all zero.
The fixed inventory parser reconstructed each original whole-second guard;
both reconstructions match, and the full protected before/after snapshots
match byte for byte. Native, entry, and input state were restored, the owned
stage was removed, and the owner/token-checked lease release closed its
connections and removed its lease directory.

The complete specialist report stays on `test-env` at
`/root/aidoku-r3-handoff-20261119/reports/import-pixels.json`. All analysis and
public export work ran on `test-env`; reporting agents did not access a Kindle.
