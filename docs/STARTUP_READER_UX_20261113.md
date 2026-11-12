# Startup, exit, sleep-cover settings, and Reader controls

Date: 2026-11-13 (Asia/Shanghai). Target: Aidoku 0.1.3.

## Changes

Startup follows committed BiliComics `9f6b6884f9cc9a080318cff9b3d5e5f2a7f1d632`:
one Slint window displays the tiny-font loading scene before background font and
metadata preparation. The independent raw framebuffer splash and its duplicate
startup observation were removed. The window begins with `booting = true`, so
constructing it does not first instantiate the full Library and navigation UI.
The synchronous `App::attach` path explicitly leaves boot state when ready; the
normal asynchronous path keeps its first successful SEND and yielded-turn gate.
The four activity segments advance every 400 ms.

The launcher now waits for its owned observer/source relationship within a
bounded interval instead of always sleeping for one second. Native portrait
permission and the awake capability observation still precede display takeover
and drawing. No additional launcher splash was introduced.

Ordinary exit requests no loading or exit animation. It announces the existing
ordinary-exit protocol and quits the event loop, then performs bounded download,
device-policy, and startup-worker cleanup. Native control restoration remains
the launcher's responsibility. Exit validation uses `exit_requested`, shutdown,
and process completion rather than requiring an exit-feedback frame.

Reader controls use an opaque light surface with dark text and icons. Inactive
labels use a heavier weight and `#111`; disabled labels use `#666`, and control
lines use `#888`. These neutral values lie on the existing sixteen-level palette.
The control palette compensates for the accepted native full-scene software
night inversion, leaving comic pixels and tap-zone guidance on their existing
paths. The platform records the accepted transform, and attachment projects it
before the next render even when a later lighting operation fails. The startup
scene also compensates for that transition so it does not change visible colors
halfway through loading.

Native scanout polarity is normalized once at the framebuffer write boundary.
For an observed eight-bit inverted format (`grayscale = 2`), line writes and
fills encode canonical luma as `255 - value`; normal Gray8 and 32-bit writes keep
their existing encoding. Software night mode and the retained scene remain
independent. The same-run layout/anchor check still rejects an incompatible
format; no hardware mode or waveform is changed. This preserves all byte values
and fixes the native inversion that a software-only UI screenshot cannot model.

The reported stock sleep screen is consistent with the previously preserved
production `screensaver_mode = "system"` setting. The cover implementation is
unchanged. This task authorizes enabling the current user's cover setting through
the ordinary `settings/set` action, which saves both `sleep_cover = true` and
`screensaver_mode = "cover"`. Production verification must preserve the reading
position and other preferences. A running Reader supplies the cover; ordinary
application exit returns screen ownership to Kindle.

## Remote verification

All verification runs through `ssh test-env`, with Cargo concurrency limited to
six. No local application build, test suite, or runtime probe was run.

| Check | Result |
| --- | --- |
| Default library | 735 passed, 5 ignored |
| Kindle library | 748 passed, 5 ignored |
| Default executable | 20 passed |
| Kindle executable | 20 passed |
| Native polarity regressions | 6 passed |
| Complete native backend | 146 passed |
| Sleep UI | 40 cases |
| Reader control rendering | 8 cases across two profiles, two layouts, and both software-inversion states |
| Startup visible-color continuity | 8 cases |
| Render evidence | 64 PNGs |
| Launcher focused regressions | 6 passed |
| Launcher complete idle rerun | 75 passed |
| KPM package fixtures | 4 passed |

The first combined Rust run exhausted temporary build space while compiling the
last executable configuration. Owned rebuildable intermediates were removed;
the separate Kindle executable run passed. The first launcher run had one
initialization timeout while the host was under resource pressure; the fixture
logs did not establish its exact cause. The unchanged case passed alone, followed
by the complete 75-test rerun. No assertion or timeout was weakened, and original
failed logs remain available.

The rendering tests exercise real Slint pixels. Their native inversion step is
a software simulation, not physical scanout or optical panel evidence. Visual
review of that capture confirmed the intended light controls and dark labels.
The first native image still showed dark controls under inverted scanout; that
result required the separate framebuffer-encoding correction above rather than
being accepted on the strength of the software simulation.

## Frozen artifacts

| Artifact | Bytes | SHA-256 |
| --- | --- | --- |
| Desktop | 60,617,544 | `efcf3191cd2542ef06865455652af5205a491136bc2b95d1dfbe5e4486d945cf` |
| ARM | 50,015,416 | `198ad4c3dd91e19c2f8c4ac0b2e5d1975d5997be90635e658a79e2115dc61df5` |
| Launcher | 42,066 | `2369a9ffd8c62ac43717f3528be96662055013418aeb834e9ecfea247ace3e69` |
| KPM package | 30,395,969 | `89eb3842157f4a2bdbee13ac09aba50d93f14af539d2ae2454085b94295cb32d` |
| Payload manifest | 3,270 | `9fe94f00bd955127efa99b53cd710a64074517499dd1f5641d21d352c604d0c8` |

The implementation was frozen before the polarity-corrected ARM build. Two
external vendor provenance notices changed afterward; executable inputs did
not. All 397 runtime, test, and packaging inputs match the staged Git archive,
SHA-256 `f39c76b3b048cab05ddb932427295f5a25995b1e36f7623ac10f271f8f846f88`.
The complete current scope has SHA-256
`0047385eb1fb8b3bc63d32e9ba4555c003e25c8b6b5575ec906c6193359c993b`.
The desktop executable does not include this native-only dependency and retains
its original qualified identity.

## Physical delivery

The final ARM and launcher passed the isolated native batch. Manual review of
the bound native-framebuffer projection confirmed light Reader controls with
dark labels and a retained night-mode comic. The previous ARM's dark menu was
explicitly rejected rather than accepted from protocol gates alone. The final
sleep capture showed the selected book's first-page cover; wake restored the
same eighth page and process, followed by pointer navigation and normal exit.
All 58 software evidence gates passed. The one startup capture crossed the
ready boundary, so it is not presented as two consecutive loading frames; the
single-scene timing is verified by the real trace and component tests.

KPM installed 0.1.3 with matching payloads, the expected own registration, zero
dependencies, and unchanged unrelated packages and hooks. The production
preference action used the installed application, an isolated launcher HOME,
`--resume --offline`, and normal settings dispatch; it did not write production
preferences through SQL. The saved route was Settings, so no production Reader
replay or acquired-page screenshot is claimed.

All 12 database-semantic checks passed. `sleep_cover` was already true and
remained true; `screensaver_mode` changed from `system` to `cover`. Every other
preference value, the complete navigation document, both progress rows, and
other application tables remained identical. No progress timestamp changed in
this session. The cache-index bytes and all its rows were also unchanged; there
were no derivative-cache changes requiring an exception.

The 118-file set and modes were preserved. Only the authorized preference
database change and the expected 87/101-byte document/KPM log appends differed;
the log prefixes matched exactly. All 30 installed payloads, package artifacts,
0.1.3 registration and zero dependency edges were verified. The final composite
passed 18 checks, SHA-256
`9e127a5822deb7dda9dbb21b1b92253bd9d26efea46dfcda1b008921ab5bf08f`.
Original reports that required obsolete exit-animation markers remain preserved
alongside the corrected single-startup/no-animation-exit analysis.

Every device batch ended with owned native-state restoration, reaped transports,
and checked lease release before host review. Native scanout images retain the
bound-mode projection limitation; no optical panel timing, physical sensor-motion
test, or universal hardware compatibility claim is made. Book-cover sleep applies
while Aidoku remains open in Reader; exiting normally returns the device to its
system screen.

## Grayscale research

[The grayscale research](KINDLE_GRAYSCALE_RESEARCH_20261113.md) distinguishes
256 Gray8 input values from optical panel levels. The exact public Scribe kernel
source uses Y8-to-Y4 conversion. This task does not change production image
quantization or claim a 256-level optical mode.
