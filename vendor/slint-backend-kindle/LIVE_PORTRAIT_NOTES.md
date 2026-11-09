# Live portrait orientation candidate

The platform observes a separate, read-only nonblocking evdev descriptor for a
unique recognized pressure-only portrait sensor. The source eligibility checks,
native-word input event ABI, reviewed pressure decoding, and bounded event reader
are adapted from the BiliComics native portrait implementation. The reviewed
startup and wake presentation contract is based on its frozen 0.1.15 reference;
its later live observer is reference code, not validation evidence for Aidoku.

A protected same-run launcher transaction supplies the native WM scanout pose,
raw GETVAR rotation, and complete GETVAR/GETFIX layout identity. Holding pose is
sampled independently from the recognized sensor. Raw rotation values never
identify Up or Down without that baseline, and only relative zero or half turns
are admitted. Rendering uses the residual holding-minus-scanout offset. Raw touch
uses holding plus the unchanged application rotation, then one scale conversion.
Recognized flat or otherwise non-pose sensor samples retain automatic capability
and use the same-run fresh scanout fallback with holding-from-sensor false; a
later valid sensor frame takes over without treating cached posture as fresh.

An orientation boundary notifies the application pointer observer and always
dispatches PointerExited to Slint. The application advances its existing reader
input epoch, cancelling retained tap, hold, seek, and slider work. The touch grab,
framebuffer mapping, native WM ownership, reader identity, page, anchor, and app
orientation preferences are retained. The scanout rotation scope preserves Slint's
logical snapshot coordinate system.

Each admitted presentation rewrites the complete retained RGB frame and requires
an acknowledged full GC16 update before touch can resume. The touch loss-aware
drain tracks every SYN_DROPPED until the next SYN_REPORT, including records read
before and after fresh slot queries. A temporary WouldBlock after GC16 waits for
touch or control readiness without releasing the grab or repeating the same
frame. A new orientation can replace that pending input boundary.

Stock suspend retires the sensor descriptor. Resume revalidates the private
presentation transaction and opens a fresh observer before rendering or touch.
Optional native metadata failures and unsupported layout changes retire the live
observer, retain the applied mapping and reading session, and gate native I/O
until the next resume calibration. Sensor observation adds no idle polling timer.

At implementation preparation, the candidate contained source and unit-test
changes only and no validation had been performed. Subsequent build, test and
runtime verification is recorded separately. Minimum follow-up checks are the remote Rust
unit tests and build, followed by an isolated native run covering Up/Down flips in
portrait and both manual landscape directions, a held touch during a flip, loss
and repeated-drop resynchronization, duplicate readiness, and sleep/resume.

The current UI contains no Flickable consumers. Slint 1.17.1's generic delayed
Flickable child-press cancellation is outside this candidate's reachable UI and
has not been validated. No Slint core patch is included.

## Changed source files

- `packaging/kindle/launch.sh`
- `src/lib.rs`
- `src/main.rs`
- `src/platform.rs`
- `src/bootstrap_lifecycle.rs`
- `src/native_presentation.rs` (new)
- `src/native_presentation/portrait_writer.rs` (new)
- `vendor/slint-backend-kindle/src/lib.rs`
- `vendor/slint-backend-kindle/src/lifecycle.rs`
- `vendor/slint-backend-kindle/src/platform.rs`
- `vendor/slint-backend-kindle/src/touch.rs`
- `vendor/slint-backend-kindle/src/framebuffer/mod.rs`
- `vendor/slint-backend-kindle/src/portrait.rs` (new)
- `vendor/slint-backend-kindle/src/portrait_events.rs` (new, adapted reference)
- `vendor/slint-backend-kindle/src/portrait_pose.rs` (new, adapted reference)
- `vendor/slint-backend-kindle/src/portrait_runtime.rs` (new)
- `vendor/slint-backend-kindle/LIVE_PORTRAIT_NOTES.md` (new)
