# Original navigation and persistent Fast mode

Date: 2026-11-16, Asia/Shanghai.

The user restored the original icon-and-label bottom navigation reference and
requested that Fast page turns stop scheduling a later full refresh. Both
changes are implemented and deployed to the physical Scribe.

## Navigation

The main tabs restore the original library, compass, search, history and
settings icons above their labels. The active tab uses stronger ink and a
short marker at the top border. Vertical separators and the lower underline
are removed. The restored bar is 72 design pixels on Paperwhite and 80 on
Scribe. Its height is independent of the existing Reader toolbar height.

The user's screenshot and the original `e936c1c` navigation component are the
visual references. This supersedes the earlier text-only footer. Existing
navigation actions and library selection actions remain functional.

## Fast behavior

- Fast publishes a persistent black-and-white reading frame. There is no
  220 ms restoration timer and no retained temporary-preview slot.
- Ordinary page turns request DU without flashing, then retain that frame
  until another actual operation changes it.
- Automatic periodic clearing is disabled for Fast in both Reader navigation
  and platform refresh counting. Chapter loading preserves its partial request.
- The interval control shows Manual and is disabled in Fast. The saved
  interval is retained for other presets.
- The explicit clear-ghosting control still requests GC16 full clearing.
  Initial entry, lifecycle restoration and other required boundary refreshes
  retain their existing behavior.

The first native follow-up exposed a second integration issue: generic AUTO
UI requests queued with a page update outranked DU in the backend's safe
request merge. Only while the visible scene is a fullscreen Fast reading
frame, the platform now maps those generic UI notifications to DU too. Menus
and overlays retain AUTO; the backend's global waveform priority is unchanged.

## Verification and deployment

All builds and checks ran through `ssh test-env`, with Cargo concurrency six.
The final Kindle library passed 785 tests and the default library passed 770,
with five existing ignored cases in each. The main binary tests passed 20
earlier in this change. Desktop and static ARM application/helper builds passed.

The earlier UI matrices passed 14 navigation cases, 24 display-mode cases,
and four manual-control cases. A localized caption clipping issue was repaired
and four focused cases rerun. Final source-bound captures and physical checks
are retained with their own executable identities.

The final physical Scribe run used the installed bytes with isolated data:

| Case | Result |
| --- | --- |
| Original navigation | Search tab opened and the original icon/label bar was captured |
| Fast hold | Pages 1 through 8; all seven page updates were DU/PARTIAL with flags `0x2` |
| Idle after the last page | Zero additional accepted display updates |
| Saved clearing interval of one | No automatic full refresh during the seven page turns |
| Manual clearing | One explicit GC16 full clear; the following page update returned to DU |

Fast tests use `--state-output`, not screenshot capture, so the measurement
does not introduce a capture-triggered refresh. Startup and Reader-entry
clears are preserved and excluded from page-turn clearing counts. The manual
case also includes explicit panel/menu closing updates before its final page.

All 32 package files were verified on the device. Existing program files and
library/source metadata were backed up; user metadata remained byte-identical.
No new critical kernel messages or reboot occurred. Native services, lighting
and warmth were restored, temporary staging removed, and the owned lease released.

The [evidence index](review/navigation-fast-20261116/README.md) contains the
source identity, exact native requests, deployment receipt, screenshots and
package checks. The [kindle-eink256 comparison](KINDLE_EINK256_COMPARISON_20261116.md)
separately covers software error diffusion and hardware Y4; it does not change
Fast into a grayscale waveform or establish optical grayscale counts.
