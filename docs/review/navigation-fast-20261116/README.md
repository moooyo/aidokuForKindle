# Navigation, Fast mode and native deployment evidence

Date: 2026-11-16, Asia/Shanghai.

The final candidate is installed on the physical Scribe. It restores the
original icon/label navigation and keeps Fast frames after one DU page update,
without delayed or periodic automatic full clearing. The behavior and its
exceptions are documented in [the implementation report](../../DISPLAY_AND_NAVIGATION_20261116.md).

## Final evidence

- [Exact source manifest](source-manifest.json).
- [Development bundle identity](delivery-summary.json).
- [162 package checks](package-verification.json).
- [Native accepted-request traces and assertions](native-results.json).
- [Deployment, metadata preservation and restoration](deployment.json).
- [Four final desktop captures/checks](final-ui.json).
- [Frozen final UI captures](../../uiux-baselines/2026-11-16-a53b9716-navigation-fast/README.md).

With a saved clearing interval of one, the physical hold case advanced from
page 1 to page 8. All seven page updates were DU/PARTIAL, and no later accepted
update occurred after the final page. A separate case performed one manual
GC16 clear and then advanced to page 3 with DU. Startup/Reader-entry clears
and explicitly closing controls are identified separately in the trace.

Both library suites passed on the final behavior: 785 Kindle-feature cases
and 770 default-feature cases, with five existing ignored cases in each.
No new critical kernel messages or reboot occurred in the final native batch.
All 32 installed files matched the archive. User metadata was unchanged;
native services and lighting were restored, temporary staging removed, and
the fresh lease released with owner/token checks.

## Earlier checks and correction

- [Initial navigation UI matrix](initial-navigation-ui.json): 14 passed.
- [Initial display-mode UI matrix](initial-display-ui.json): 24 passed.
- [Localized caption repair](caption-ui.json): four passed.
- [Initial native AUTO result](initial-native-auto.json).

The first native version removed the delayed full refresh but allowed generic
AUTO UI requests to outrank DU in the same queued frame. The final platform
maps those UI notifications to DU only for a fullscreen Fast scene. Menus and
overlays keep AUTO. The initial manual replay also lacked the explicit action
to close Reader controls before its last page turn; the final replay corrects
that measurement setup. Earlier records retain their own binary identities.

The [kindle-eink256 investigation](../../KINDLE_EINK256_COMPARISON_20261116.md)
and [inspected bitmap call sites](eink256-call-sites.json) are source research.
No external APK or native library was executed or incorporated into Aidoku.
