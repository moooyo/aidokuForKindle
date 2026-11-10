# Reading and application follow-up

Updated: 2026-11-10, Asia/Shanghai.

This document tracks the development follow-up to the frozen Candidate18
delivery. The existing Candidate18 archive, manifests, measurements and device
acceptance retain their original scope. Verification of this working tree is
recorded separately below.

## Review of the proposed changes

The original reader already fetched two upcoming pages, retained processed PNGs
and decoded grayscale images, and woke the UI when a foreground image finished.
Those mechanisms were connected to production reading. The remaining concrete
costs were chapter boundaries, vertical source-image boundaries, UI-thread RGB
conversion, and background decoder contention.

The follow-up prepares the next chapter's first frame near the current chapter
end, and the next vertical source image near the current image's bottom. Missing
chapter manifests are fetched on a worker and published only after an atomic
chapter-identity and absent-manifest check. Publication never replaces a
manifest that a foreground request or downloader has already installed.

Foreground image work has priority in a single-decoder scheduler. Background
work can yield while waiting and at cooperative cancellation boundaries. An
already executing library decoder or shared HTTP response is not forcibly
interrupted. Background covers and downloads use the same priority context.

RGB buffers are prepared on workers. UI publication wraps an already prepared
shared buffer instead of expanding grayscale pixels on the event-loop thread.
The prepared-pixel cache retains at most 12 MiB and sixteen entries. Source
identity uses a weak reference and pointer equality to prevent address reuse
from selecting pixels from another image. These cache limits exclude live
Reader images, the separate grayscale cache and whole-process RSS.

## Additional application features

- Categories can be created and renamed through the existing keyboard. Empty
  categories persist. The semantic IDs of built-in categories remain stable;
  custom-category references and serialized book documents change atomically.
- Metadata backups use consistent SQLite snapshots. Restore validates the
  private staging copy, waits for all application jobs and the downloader to
  stop, saves an automatic safety snapshot, and imports into the existing
  database transactionally. A failed runtime restart keeps the application
  fenced. Missing offline originals are reported and their jobs become paused.
- Manual metadata snapshots have a separate limit of 64. Automatic safety
  snapshots retain the most recent two in their own directory; a new snapshot
  is published and synced before an older automatic snapshot is retired.
- Backups include book metadata, progress, settings, and source/tracking account
  credentials. They exclude comic images, disposable caches and installed
  extension files. They are private local files rather than encrypted exports.
- Optional bounded logs accept fixed event identifiers and typed outcomes.
  They can be exported and cleared locally without writing credentials or raw
  provider error bodies.
- Optional download preparation creates grayscale or screen-sized acceleration
  copies while retaining canonical originals and their manifest identities.
  Preparation is asynchronous. Reading falls back to originals when a copy is
  missing, invalid, or unsuitable for the current crop, zoom or layout.
- Daily/weekly library updates support Wi-Fi/charging restrictions and skipping
  completed books. Charging events, retry delays and successful-update times
  persist. Opt-in advance downloads select the actual next one to three
  chapters and preserve existing user pause/cancel decisions.
- Wide images can be read as two halves in source-page order. Bookmarks and
  progress continue to identify the original page. Reading order, reverse
  navigation, retry, retained wake and cold restoration retain the half-page
  position. Early turns wait in a bounded signed queue until the current frame
  establishes its geometry; opening menus, explicit navigation and lifecycle
  changes cancel stale turns. Vertical strips and two-page layouts keep their
  existing behavior.
- AniList and MyAnimeList support real account validation, explicit book
  association, completed-chapter synchronization, and a persistent retry queue.
  Companion accepts legitimate tokens or user-configured OAuth clients; no
  client identity or successful authorization is invented. Automatic sync is
  disabled by default. Already issued HTTP requests cannot be recalled, and the
  upstream APIs do not provide compare-and-swap updates across other clients.
- Simplified Chinese, Traditional Chinese, English and Japanese labels can be
  switched at runtime. Rust-generated content and Slint labels share the same
  selected language.

## Hardware boundaries

The optional reading Wi-Fi policy discovers native service capabilities and
keeps background observations out of the input/render loop. It restores only
the switch values that it owns. Native charging/network behavior still requires
device qualification. See [device policies](DEVICE_POLICIES.md).

Stylus navigation and proximity-based palm rejection remain unavailable because
the current backend does not expose tool identity or pen proximity. Custom
cover/progress sleep screens require a separate ownership agreement with stock
powerd. Their settings remain truthfully unavailable. Automatic brightness
continues to be excluded from requirements.

[The KOReader source review](KOREADER_HARDWARE_RESEARCH.md) found CPU image
scaling and conditional MediaTek display-driver operations. Its MDP completion
fence is not a source-to-destination image-scaling command. No undocumented MDP
scaler ioctl has been enabled in this application.

## Verification

All execution ran through `ssh test-env`, in the isolated source and output root
`/root/aidoku-followup-20261110-01`. The final build concurrency was
`CARGO_BUILD_JOBS=6`, with incremental compilation disabled. Checks ran in
separate stages. Local operations were editing, ordinary source/artifact
transfer, extraction and reading captured images.

| Check | Result | Scope |
| --- | --- | --- |
| `cargo fmt --all -- --check` | Passed | Rust source formatting. |
| `cargo test --locked --all-targets -- --test-threads=2` | Library: 636 passed, 2 ignored; binary: 3 passed; benchmark example: 95 passed | Host correctness checks; the example reuses some Store tests. |
| `cargo test --locked --no-default-features --features kindle --lib -- --test-threads=2` | 649 passed, 2 ignored | Kindle feature compilation, native helper contracts and deterministic service doubles. |
| `scripts/verify-reading-followup.py` | 13 passed, 0 failed | Runtime language switching, download settings, category keyboard input, metadata backup, wide-page pixel order, early turns and exact half-page reopen. |
| Reader-panel pointer checks | 2 passed, 0 failed | Actual PW6 switch and Scribe radio-button clicks persist the wide-page preference. |
| Launcher fixture suite | 29 passed, 0 failed | Mock native helper CLI and shell lifecycle; the USB boundary also passed three repeated checks. |
| ARM release cross-build | Passed | `armv7-unknown-linux-musleabihf`, statically linked and stripped ARM EABI5 executable. |

The two ignored tests are opt-in performance measurements. They were not counted
as successful executions. Raw evidence is retained in
[the UI report](../artifacts/reading-followup-20261110-01/ui-final-03/report.json),
[all-target log](../artifacts/reading-followup-20261110-01/tests-final-03.log) and
[Kindle-feature log](../artifacts/reading-followup-20261110-01/tests-kindle-03.log).
Compile warnings were retained; this record does not claim a warning-free build.

The [verification summary](../artifacts/reading-followup-20261110-01/verification-summary.json)
binds the tested 321-file source selection and executable identities. The
[ARM executable](../artifacts/reading-followup-20261110-01/aidoku-armv7-followup-20261110)
is 49,089,448 bytes, SHA-256
`b13b0aaac26cf5d5b268bd8b28ccee5b1936a193a2bcd218ccdc8df5ab5086e8`.
It is a development ELF, separate from the frozen Candidate18 archive.

Initial failures remain preserved: six tests exposed invalid fault-injection or
image-reading fixtures; the first wide-page UI attempt also exposed an actual
early-turn navigation bug and an unsuitable blank-image heuristic. The corrected
run adds an explicit early-turn case and does not replace those failed records.
A later verification attempt was interrupted by an unavailable worker. The user
authorized a normal reboot of the verification VM, after which all final checks
were rerun. No local verification fallback was used.

Supplemental checks also exposed outdated test assumptions. Reader panels do
not use the Settings page's section state; the final panel checks exercise
their real pointer targets. The Linux launcher fixture lacked `usleep` and the
current portrait helper CLI. Those were repaired inside the private fixture,
with invalid-argument/stale-epoch cases and a deterministic sleep-repaint
barrier. Production `launch.sh` remains unchanged, including its safety gates.
The first launcher failures and the intermediate USB timing failure remain in
the retained logs. These are mock protocol tests, not sensor calibration.

Physical Kindle, real-account authorization, MDP acceleration and end-to-end
physical page-turn latency have not been qualified by this task. The host checks
do not establish a universal speedup or a whole-process memory bound.
