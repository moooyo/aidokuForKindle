# Stock Library input

The November 21 initial stock snapshot is upright as packed Gray8 (1860
visible columns, 1872-byte stride). On just its bottom strip the independent
public navigator recognized **one Home** (89% underline) and **one Library**
(0%). Reversing the picture recognized neither. The original tab is Home.
No other titles or pixels are needed for this observation. This is an offline
read of the Aidoku operator's own snapshot, not another device operation.

The entry declares `assets/launcher/aidoku-cover-v2.png`, the 600×800
official mark, title and subtitle; neither the legacy cover nor a Home recent
item is an acceptable substitute. `tile_mode.py`'s old private screenshots
and compiled click program are not recoverable from the shared installation.

The separately qualified stock navigation module reuses the already tested
ARM OCR/XTEST toolchain, not another project's pictures or target. The shell
allowlist calls only `navigation`, `nav-home`, `nav-library` and
`restore-pointer`; never invoke its other project's `open-card` operation.
Supply `navigation-kit.tar.gz` to the operator's fresh owned stage. It contains
only `input/navigation.sh`, `input/native-target`, `input/card-input`,
`input/lib/*` and `input/models/*`. Never transfer the host-only public fixture.
No screenshots, books or case tokens are in this archive.

The interface is `sh "$stage/input/navigation.sh" inspect|library|restore
"$label"` with the established media/state roots both containing their matching
`owner` file. `inspect` prints `home X Y` or `library X Y` or fails without
sending input. `library` exclusively saves the initial tab and pointer, checks
the current stock owner/foreground, positively locates both labels/underline
and rechecks actual live pixels immediately before selecting the Library tab.
`restore` selects the original tab if necessary, restores the stored pointer
and checks all three fields. Capture the stock Library to worker-private
storage between `library` and `restore`; this is **not** an Aidoku launch.
Place navigation behind the designated operator's host lease, capture-before-
and-after and bounded recovery; verify enough staging space for 66 MB.

On the shared worker's 1860×2480 synthetic Xvfb, the real ARM programs ran
under QEMU. The actual previously captured footer was recognized solely in
its upright orientation; an unknown footer refused navigation before saving
any original state. A painted Home→Library→Home round trip observed button
releases on the X server and verified the original pointer. This confirms
the navigation machinery, not the current Library artwork or a physical click.

After a real `navigation` batch has closed, run `python3 -B
operator/input/freeze_target.py "$work" "$work/batches/navigation-$label"`
on the `test-env` host. Its QEMU substep runs only in the shared Ubuntu
userspace. It independently recognizes the selected Library footer before
checking the distinctive bar spacing, whole logo, `Aidoku` lettering and
small subtitle of the packaged artwork at the observed scale. Zero or multiple
complete covers refuse. It never uses the old stock grid position or any
external label/other book as a launchable target. Success saves a private
cover-and-footer guard and `tap-files.json` **only in that closed batch**.
Pass the latter to the study freezer as `--tap-files`. A failed visual guard
does not authorize title-only matching, scrolling to a guess or URI timing
labelled as a tile tap.

The November 21 navigation-only operation returned to the original stock tab
and pointer and closed its lease. The resulting private Library frame passed
the selected-tab OCR, one complete official mark/title/subtitle match and a
second uniqueness check using the actual ARM selector in read-only fixture
mode. A provisional straight-bar scan alone had refused the real view;
checking both dark and light regions of the independently generated official
logo distinguishes unrelated stripes while still rejecting an additional logo
at a different scale. No app launch or startup measurement is claimed by this
preparation. Keep the guard and original frame out of the public record.

The additional stage files are `input/dispatch.sh`, `input/tile-input` and
the private `input/artwork.guard`. With the study controller's `current`,
`pointer-intent`, per-case and owner journals in place:

- `target LABEL CASE` rechecks the stock owner and selected Library with
  read-only OCR and the visible framebuffer, and persists the unique result.
- `tap LABEL CASE` consumes the case once. The ARM input helper requires a
  whole, exact artwork match, the bound Library footer and no extra candidate
  logo elsewhere in the visible area. It repeats these checks after moving
  the X pointer, then acknowledges the actual button states. It holds for
  50 ms, retains down/up brackets in the private case, and prints just
  `down_before_us down_after_us` from device `CLOCK_BOOTTIME` to the observer.
- The restoration operation remains `navigation.sh restore LABEL` in the
  controller's bounded cleanup. No staged input helper may send an app
  request by URI or set a production file.

`ubuntu-run --build <input-workdir> sh build.sh` builds the ARMv7 code with
the shared Zig and the previously available public X libraries; the large
OCR runtime remains the same one used for public navigation. The synthetic
`test_artwork.py` QEMU/Xvfb suite exercises changed footers, absent/duplicate
tiles (including one at a different scale), a tile moved within the actual
view, the acknowledged clock brackets and rejection of a fixture by the
production binary. Host fixture and source files do not belong on the device.

Never send an unguarded screen coordinate. Exit to stock before restoring the
original tab, then restore and inspect the saved idle pointer. An ambiguous,
changed, unsupported or unrecognized view must not dispatch the document.
