# Scribe non-palette PNG transparency incident

## Current status

Recovery completed in fresh batch `display-png-recovery-7d4b21c2`. Boot identity
changed, native window-manager/statusbar operation was verified, the owned
probe was absent, and the staging path was removed or confirmed absent.
The recovery lease was released before analysis. The codec batch remains a
failed experiment, not a successful decode.

During physical batch `display-followup-20261115-22823e73`, the corrected
header-first codec probe submitted a valid 64 x 64, grayscale8 PNG containing
a non-palette tRNS color key. SSH disconnected during the codec command.
Subsequent recovery connections first timed out, then the device's SSH port
refused connections. The following RGB color-key trial did not run. Further
physical trials stopped immediately.

The batch did not change native window-manager, statusbar, lighting, display
or application-installation state. All controller transports ended. Its exact
owned lease was released for the required manual-reconnection wait, with
device health, probe termination and temporary-file cleanup explicitly left
unverified. Recovery must use a fresh lease and fresh evidence identity.

## Source-level defect and review gap

The matching Amazon 5.19.5 build 479432 kernel contains this call chain:

1. `drivers/media/platform/mtk-png/mtk_png_core.c`, in the M2M callback
   `mtk_png_device_run`, passes `src_vb->planes[0].m.userptr` to
   `mtk_png_set_trns` at lines 475-477.
2. `mtk_png_hal.c:253-258` returns early when tRNS is absent, or uses the
   DMA-address-based palette path when PLTE is present.
3. The remaining non-palette path casts the userspace pointer to `u8 *` at
   line 260 and directly dereferences it at lines 264 and 269-271. This kernel
   callback does not use a protected userspace read or the vb2 kernel mapping.
   It is also missing `trns.offset`.
4. The RGB branch additionally writes both color-key words to KEY0 at lines
   272-274.

The pretrial review noticed the missing offset but failed to identify the
more serious unprotected userspace-pointer dereference. That review gap incorrectly
treated this as a likely pixel mismatch that could be safely tested. A valid
input file and a userspace deadline do not contain a kernel access fault.
The recovered log confirms `page domain fault (0x01b)` at the user address
`0xb6f74000`, with `codec-probe` as the current task. PC `0xc064dd64` contains
ARM instruction `e5d37000` (`ldrb r7, [r3]`), and r3 equals the fault address.
The surrounding byte-load/shift instructions match the grayscale tRNS branch.
DAC `0x51` disables user domain 1, consistent with the domain fault. This is
strong source-and-log evidence of the direct userspace read; the missing
offset is an additional correctness defect.

An initial description incorrectly called this a dedicated PNG worker. The
actual source registers `mtk_png_device_run` directly with M2M; the M2M core
can invoke it synchronously from scheduling. The recovered task context and
page-domain fault support that corrected description. This was not evidence
for a missing process address space in an independently named PNG worker.

Palette transparency and intrinsic LA/RGBA alpha take different branches.
Their earlier successful physical results remain scoped to those inputs and
do not qualify non-palette tRNS. JPEG's working USERPTR results also remain
separate from this PNG callback defect.

## Immediate containment

- The as-run probe was retired. Its source hash was
  `d339353c4e8d3de384b66f392648857c891c1b562ec5d8a6b4bca3b3c3ed3324`,
  and its ARM executable hash was
  `cee1cf448734a9af4b0e1ce48eb05c6f7ff179ca71fbaa5f6b79b7e2addce3ca`.
- The current [probe](../scripts/native-codec-probe.c) walks PNG chunk
  boundaries and rejects every non-palette tRNS before opening a video
  device, with `unsupported_png_color_key_transparency` and ENOTSUP.
- This also covers a color key that never matches an actual image pixel;
  its mere presence selects the unsafe kernel branch.
- Palette tRNS is accepted only with a preceding valid-sized PLTE and a
  nonempty transparency table within the palette entry count. Embedded
  `tRNS` text in another chunk is not mistaken for a transparency chunk.
- The [admission suite](../scripts/verify-codec-admission.py) passed 23 checks
  on `test-env`, intercepting every video open and every ioctl. The guarded
  source and ARM executable hashes are recorded with the build evidence.
- The RGB color-key device trial will not be attempted. It shares the
  established unsafe pointer path and now has a pre-device rejection test.

Do not repeat the faulting input against this unfixed driver or try pointer
address tricks. A production PNG hardware route must reject this branch.
The measured PNG pipeline was also slower than CPU decoding and resizing,
so there is no performance reason to expose this defective branch in the
Reader.

## Recovery procedure

The completed recovery compared boot identity with the saved pretrial value,
collected persistent kernel crash records, and verified native service/process
state. Installed applications and user data were preserved. The owned staging
path was removed or confirmed absent, all transports closed, and the fresh
lease released before analysis. The probe's own output file was not present after reboot; no result from
that file is claimed. Original failure and unsuccessful reconnect records are
preserved separately.

See the [extended qualification record](SCRIBE_MEDIA_QUALIFICATION_20261115.md)
and the [earlier JPEG allocation incident](SCRIBE_CODEC_INCIDENT_20261114.md).
