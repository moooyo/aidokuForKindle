# Status query review after the R3 pixel mismatches

This read-only review covers production commit `8baf57f` and the retained
All-options and portrait qualification logs. No new device command, capture,
test, or product change was performed. The relative event times are preserved
in [diagnostic.json](diagnostic.json). Private device identities, absolute
device clocks, image data, and image digests are excluded.

No definite mode-dependent battery or Wi-Fi implementation defect was found.
The original strict pixel failures remain failures. The available records do
not contain the raw firmware property values, so they cannot independently
establish natural battery decline or completely exclude an implementation
problem. Image recognition of a percentage is not a firmware observation.

## Shared data path

| Source location | Relevant behavior |
| --- | --- |
| `packaging/kindle/launch.sh:652`; `src/native_query/startup_portrait.c:119` | Both supervisors invoke the same application with `--capture-native-portrait`. |
| `src/native_presentation/portrait_writer.rs:194`, `:212`, `:215`, `:225`, `:248`, `:258` | The portrait transaction reads optional sleep modules, power state, display mode, and portrait orientation. It closes its query session before publishing; it neither reads nor transfers battery or Wi-Fi values. |
| `packaging/kindle/launch.sh:662` | Both modes continue through the common application-launch path. |
| `src/bootstrap_metrics.rs:58` | The accepted initial frame initiates the same toolbar observation. |
| `src/platform_startup_toolbar.rs:126`, `:129`, `:137`, `:141` | A single 80 ms total budget covers helper startup, Wi-Fi, battery, and clean session completion. |
| `src/native_query.rs:60`, `:65`, `:197`, `:214` | Both observations locate the sibling `aidoku-native-query`; reply opcodes must match the requested property. A response marker records success, without its property value. |
| `src/native_query/worker.c:44`, `:161`, `:163` | Battery reads call `LipcGetIntProperty` for `com.lab126.powerd.battLevel` and return the integer as decimal text. There is no portrait selector, subtraction, or rounding here. |
| `src/platform_startup_toolbar.rs:137`; `src/platform.rs:1881`, `:1886` | Both paths parse `u8`. The ordinary path clamps above 100 and uses zero on failure; it does not transform one valid percentage into an adjacent percentage. |
| `src/platform_startup_toolbar.rs:6`, `:7`, `:79`; `src/platform.rs:1175` | A startup sample must be less than one second old when first adopted. Its published view, or the ordinary status observation, is then cached in that GUI process for up to 30 seconds. The supervisors do not share this cache. |
| `src/platform.rs:1155`, `:1818`, `:1826`, `:1875`, `:1886` | A rejected startup observation falls back to a fresh ordinary status query using the same helper and property names. Helper unavailability has the existing `lipc-get-prop` path to the same properties. |
| `src/app/view.rs:128`, `:167` | The resulting integer is assigned to the UI battery state. |

The supervisors can change when the later GUI reaches this common query:
the shell permission wait uses 10 ms intervals (`launch.sh:315`), while the
native wait uses 1 ms intervals (`startup_portrait.c:178`). This is a possible
timing difference, not evidence that either path changes a property value.

## Retained timing observations

Times below are relative to each GUI's `startup_toolbar_probe_started` event.
The battery-response positions are inferred from the fixed query order in the
source; the trace does not label or preserve the returned integer.

| Qualification arm | Probe outcome | Probe elapsed | Startup battery response | Selected observation |
| --- | --- | ---: | ---: | --- |
| All-options baseline | Ready | 69.057 ms | 66.132 ms | Startup snapshot |
| All-options candidate | Ready | 47.412 ms | 44.546 ms | Startup snapshot |
| Portrait baseline | Ready | 53.207 ms | 43.425 ms | Startup snapshot |
| Portrait candidate | Unavailable | 83.701 ms | 77.387 ms | Fresh ordinary query at 481.572 ms |

All-options used a successful snapshot on both arms. Their selected battery
responses were 31.160957 seconds apart. Consequently, the fallback observed
in the portrait trial cannot be the common sufficient explanation for both
pixel mismatches.

In the portrait candidate, helper readiness occurred at 53.611 ms, followed
by successful responses at 73.575 ms and 77.387 ms. No
`native_query_session_finished` appeared before `probe_unavailable` at
83.701 ms. Exhausting the 80 ms budget during clean session completion is the
closest explanation of this sequence. It is an inference: the unavailable
event does not retain an error category, so a deadline failure is not proven.

The later ordinary query opened another helper session, received its five
status responses, and finished cleanly. Its battery response was 404.185 ms
after the discarded startup response. The selected battery responses across
the two portrait arms were 30.544251 seconds apart. No recorded property value
establishes whether the two responses within the candidate carried the same
percentage, or why the final percentage differed between visits.

## Layout relationship and next qualification

`ui/widgets.slint:501` sizes the percentage label using `preferred-width` and
anchors its right edge. The Wi-Fi icon's horizontal position follows that
label's left edge (`:500`). A changed percentage can therefore move an
otherwise identical Wi-Fi glyph. The battery fill also depends directly on
the percentage (`:508`), so a text-only mask would not explain every changed
status pixel. A controlled host rendering with only the percentage changed
could independently check the precise pixel displacement; it was not run in
this review.

There is no basis here to change the 80 ms budget or production behavior.
Keep the original All-options and portrait failures. The designated operator
will use a fresh strict portrait picture qualification with the original
auditor and full original comparison region. Existing timing pairs are not
repeated or reclassified by this diagnostic.
