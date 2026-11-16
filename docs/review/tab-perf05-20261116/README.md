# Tab feedback and Perf05 evidence

The final application is installed on the physical Scribe. See the
[findings and implementation](../../TAB_FEEDBACK_AND_PERF05_20261116.md) for
the measured causes, changes, and timing boundaries.

- [Paired timings, stages, control trial and pixel assertions](comparison.json).
- [Final application, helper and package identities](delivery-summary.json).
- [Complete final source manifest](source-manifest.json).
- [Working-tree source match checked on test-env](source-roundtrip.json).
- [797 Kindle, 782 default-library and 20 binary test results](delivery-summary.json).
- [162 package checks](package-verification.json).
- [14 final desktop navigation cases](ui-verification.json).
- [Physical run outcomes, data preservation and lease release](runs.json).
- [Original trace and replay files](traces/).
- [Frozen desktop, native and comparison captures](../../uiux-baselines/2026-11-16-a53b9716-tab-perf05/README.md).

Five physical batches retain distinct identities: the instrumented baseline,
the initial CPU/Tab changes, the same-executable sleep-cover-disabled control,
the final cover-enabled implementation, and installation verification. The
control changed only isolated fixture preferences. It did not change the
user's actual sleep-cover setting.

The final eight-image Perf05 test uses four two-page windows. Three forward
turns change the image, while later endpoint taps are no-ops. First-visit forward
latency changed from a 1301.524 ms median to 139.685 ms. Repeated-run prepared
turns changed from 241.542 ms to 137.699 ms. These are software action-to-SEND
observations on the physical device, not optical completion measurements or
large-sample tail-latency estimates. The full input burst, cancelled request,
unprepared initial images and seeks remain in the traces.

All three paired final Reader captures have zero differing pixels. The cover
feature remains enabled and its full-resolution image is ready after idle.
Final native navigation has no separate pressed-Tab update. User metadata,
native services and lighting were preserved, temporary device staging was
removed, and every fresh lease was released. No new critical kernel messages
or reboot occurred in the recorded batches. Private transport settings, lease
tokens and user database contents are not included.
