# Demo reference scenarios

Demo initialization creates real Store records from `assets/demo/catalog.json`
and the bundled chapter/page artwork. The fixture date is November 4, 2026.
The canonical library contains 46 books. Reference data is selected when an
isolated demo database is first seeded, rather than imposed by a display-time
sort override.

The library summary retains the handoff's illustrative demo capacity label.
The Storage section instead measures the actual sparse fixture files, managed
cache artifacts, and filesystem free space. Its per-book rows are real measured
associations rather than fictional gigabyte totals. These two labels have
different demo scopes; normal application storage labels use measured data.

## Fresh snapshots

| Entry point | Initial snapshot |
| --- | --- |
| `demo::seed(store, data_dir)` | Legacy history snapshot with 20 actual progress records. |
| `demo::seed_for_screen(store, data_dir, "library")` | Library snapshot with 46 actual history records, one for each canonical library book. |
| `seed_for_screen` with another initial screen | The legacy history snapshot, including its 20-record timeline. |

The library snapshot is applied before the shared `.demo-v1-seeded` completion
marker is written. Its progress timestamps decrease in canonical book order,
starting at `2026-11-04T18:20:00+08:00`. Consequently the ordinary recent-read,
descending SQL order produces the reference library order.

The primary book (`demo-00`) retains chapter 34, page 12, and its actual
40-page manifest, giving 30% progress. Books `demo-02`, `demo-07`, and `demo-21`
use approximate target fractions of 23%, 60%, and 80%: their page is the rounded
product of the actual manifest count and target fraction, clamped to `1..total`.
For 22-page samples these become pages 5, 13, and 18; the displayed fraction is
always the real `page / total`. Every other book uses its existing sample
chapter at page 1. No manifest is expanded to fit a percentage. These progress
records are not marked completed. The shared thumbnail rule omits the page-1
progress bar; continuation text still reports the actual `1 / total`.

Canonical zero-based library indices `0, 3, 4, 8, 11, 14, 17` have completed
download records for their selected sample chapters. Initialization uses
`Store::save_download`, which checks the real local page references before
accepting completion. The primary book's existing completed chapter downloads
remain present. A valid local reading sample alone is not treated as a completed
download for every book.

## Persistence and sorting

Both scenarios use the same completion marker. After initialization completes,
changing the launch screen does not reseed progress, preferences, downloads,
bookmarks, categories, or a user's cover selection. The marker is not a request
to upgrade an existing demo session to another snapshot. Bundled page artwork
may be refreshed independently of those database records.

All eight runtime library sort choices use the normal SQL metrics: title,
recent read, recent open, recent update, added date, latest chapter, unread
count, and chapter count. Changing the sort or direction uses the stored data;
the initial canonical order is not forced back after an interaction.

## Artwork

The 18 dedicated library covers belong only to their corresponding titles.
Additional titles or generated volume editions without dedicated art have an
empty cover URI and use the application's title plate. A recycled cover with
another title printed inside it is not a valid fallback.

Screen-specific catalog, search, history, and Paperwhite cover editions remain
contextual projections of the bundled art. History artwork can still be shown
when a book has no dedicated base cover. A user-selected external cover path
takes precedence over these projections. See the [artwork provenance](../assets/demo/README.md)
for the distinction between fixture art and provider content.

## Reference comparisons

Use a different fresh demo data root for each initial reference scenario. With
the CLI, `--demo` uses a `demo/` child below `--data-dir`; keep both the database
and completion marker within that isolated root. For example, on the designated
remote worker:

```powershell
ssh test-env '/absolute/frozen/aidoku --demo --offline --screen library --device pw5 --data-dir /tmp/aidoku-library-reference'
ssh test-env '/absolute/frozen/aidoku --demo --offline --screen history --device pw5 --data-dir /tmp/aidoku-history-reference'
```

Each directory must be new for its intended initial snapshot. Preserve existing
directories when checking persistence or user edits.

Opening History after seeding the library naturally exposes its 46-record
timeline. It is not the independent 20-record history reference. A single shared,
subsequently edited database cannot be
claimed to reproduce both initial timelines simultaneously. The prototype's
static screen states specify comparison targets, rather than overriding the
application's evolving history and sort results.
