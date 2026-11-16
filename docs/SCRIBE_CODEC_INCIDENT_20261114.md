# Scribe hardware-codec probe incident

Incident: 2026-11-14. Audit updated: 2026-11-15, Asia/Shanghai.

The later [non-palette PNG transparency incident](SCRIBE_PNG_COLOR_KEY_INCIDENT_20261115.md)
has a separate record. Its unprotected userspace-pointer access is distinct from
the JPEG capture-plane allocation error described here.

## Confirmed outcome

An isolated hardware JPEG decoder experiment was followed by a Scribe reboot.
The retained previous-kernel log reports a NULL-pointer exception in the
`mtk-jdec` workqueue. The fault occurred during the first JPEG case in the
second media batch. This was a kernel failure, not an acceptance result for
the decoder.

The experiment combined a non-streaming MDP allocation context with decoder
USERPTR queues. The matching source advertises only USERPTR for JPEG and PNG,
while the MDP node supports MMAP. A subsequent audit identified a concrete
initialization-order error: the probe allocated a one-plane GREY capture queue
before submitting the first JPEG header. `mtk_jdec_buf_queue` then changed the
capture format to two-plane NV12 through `mtk_jdec_set_queue_data`.

The worker's `mtk_jdec_set_disp_param` loops over the new format's plane count.
For a missing plane, `vb2_plane_cookie` returns NULL, and
`vb2_dma_contig_plane_dma_addr` dereferences that result without checking it.
The recorded faulting ARM instruction is a load through NULL immediately after
a helper call, in a plane-address loop; the register state is consistent with
access to the second plane. This is strong source-and-log evidence for the
capture-layout mismatch, rather than proof that the allocator itself was
incompatible or that hardware JPEG decoding is impossible.

The device reports build `058-juno_1905_barolo_bellatrix3-479432`. The exact
Amazon 5.19.5 build 4794320058 source was subsequently retrieved, and its
`mtk_jdec_core.c` is byte-identical to the source used for this diagnosis.
The correct initialization must parse the first coded header and handle its
source-change notification before final capture negotiation and allocation.
See [the complete API audit](SCRIBE_HARDWARE_API_AUDIT_20261114.md).

## Scope and recovery

- The experiment used a standalone, temporary probe and synthetic inputs.
- The application was not replaced or installed by this batch. The four reader
  presets do not use the hardware JPEG/PNG decoder nodes.
- The preceding MDP copy, scaling, rotation, and RGB-to-gray calls completed.
- The JPEG case lost its SSH connection; later JPEG/PNG cases were not run.
- All host-side transports ended. The first cleanup attempts could not connect
  while device SSH was unavailable.
- The aborted lease was retired with owner/token checks, recording that device
  recovery and staging cleanup were still unverified at that point. The
  user-reported restart terminated the process-scoped decoder contexts; no
  persistent display setting had been changed by the media experiment.
- After the user confirmed recovery and enabled SSH, a fresh recovery lease
  verified the native window manager and statusbar, confirmed the test process
  was absent, retrieved the previous-kernel log, and confirmed the old staging
  directory was absent. It then released the lease with owner/token checks.
  The corrected full-size MDP output from the interrupted batch was missing
  after the restart; no pixel comparison of that output is claimed.

Private evidence identifies media batch `display-media-fc62c663` and recovery
batch `display-recovery-3984bda2`. Raw credentials, tokens, private transport
configuration, and complete device identifiers remain outside the repository.

## Containment

The media probe now rejects JPEG and PNG execution before opening any input
or device descriptor. The application retains its existing software decoders.
Immediately after the incident, further hardware-codec execution was stopped
pending a separately reviewed allocator and driver contract. PNG had not then
been exercised through USERPTR. That initial lack of qualification was not
inferred from the JPEG fault.

The separate MDP grayscale transform results remain scoped to their recorded
inputs and outputs. They do not qualify hardware decoding, optical grayscale
quality, or a complete application speedup.

A later MDP-only full-size batch completed without a reboot and retained its
complete output, but pixel comparison failed. That separate result is recorded
in the [API audit](SCRIBE_HARDWARE_API_AUDIT_20261114.md); it does not rehabilitate
the retired decoder probe. Later Y4 and four-preset display checks used the
existing HWTCON display interface, restored native state, and released their
fresh leases. Those display batches did not execute JPEG or PNG hardware.

## Corrected trials explicitly requested afterward

The user subsequently requested physical testing of the corrected calls and
a same-picture hardware/CPU scaling comparison. A new opt-in probe used
page-aligned USERPTR memory, first-header negotiation and checked plane counts.
It also corrected JPEG packet-tail submission after a non-crashing timeout
trial exposed that additional requirement. GREY JPEG and the tested 8-bit PNG
paths then produced valid pixels; JPEG-linked IMGRZ scaling completed the
paired benchmark. Default NV12 and standalone full-size MDP retained their
failed qualifications. No new reboot occurred, and every batch restored owned
state and released its lease.

See [the corrected calls, results and benchmark](SCRIBE_MEDIA_BENCHMARK_20261115.md).
The original failing probe remains retired. These later results do not erase
the original crash or qualify arbitrary input formats and recovery scenarios.
