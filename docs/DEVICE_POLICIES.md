# Device policy boundaries

The optional reading Wi-Fi policy and the automatic-update prerequisites use
background observations. `Platform::is_charging()` and `wifi_connected()` return
`Option<bool>` from RAM, without running LIPC commands on the rendering/input
thread. Unknown service responses, query failures, and observations older than
45 seconds remain unknown. The native worker normally samples at ten-second
intervals; it stops periodic observation while suspended. Desktop profiles do
not invent native control capabilities from their simulated status values.

## Reading Wi-Fi ownership

The feature is available only after the actual native services expose valid
`com.lab126.wifid.enable` and `com.lab126.cmd.wirelessEnable` switches. It applies
to an already displayed local, offline, or downloaded chapter, with no active
downloads or Companion session. An online chapter that still needs image
transfers remains connected. The policy does not alter a radio that was already
disabled.

The native worker remembers the original pair of switches and the exact pair
that it changed. Disabling writes `wifid.enable` before `cmd.wirelessEnable`;
restoration reverses that order. Before restoration it re-reads both switches
and restores only when the observed state still matches its own last attempted
write. A changed state retires its ownership and yields to the user/system for
the remainder of the current reading lease. Failed partial writes preserve
enough state to retry restoration rather than forgetting the original value.

Leaving the eligible Reader, disabling the preference, starting downloads, or
opening Companion releases the policy. Suspend requests release it and wait
for the worker's ownership barrier before parking the backend. Normal shutdown
requests restoration, and worker teardown waits for its bounded device commands
to finish. A device-service failure remains an error; physical radio behavior
and recovery after an uncatchable process or power loss require separate device
qualification. Compare-before-restore cannot distinguish two independent
writers that happen to write the same values.

An explicit Companion request first calls `request_network_for_user()`, which
keeps the reading policy released while the UI waits asynchronously. If the
application had disabled Wi-Fi, `user_network_ready()` requires both the restore
barrier and a connected observation collected after that request. It cannot
mistake an old pre-disable connected snapshot for reconnection. Completion,
cancellation, and timeout call `finish_user_network_request()`. A radio already
disabled independently is not automatically enabled. Background update,
tracking, and speculative network work defer while `network_policy_blocked()`
is true, preserving their pending work instead of failing against the
application's own radio policy.

The control ordering follows the upstream KOReader Kindle implementation's
`kindleEnableWifi` routine in
[`frontend/device/kindle/device.lua`](https://github.com/koreader/koreader/blob/01ccbd790d6269301ad523e75c45f62d022a6d7b/frontend/device/kindle/device.lua).
This establishes a source reference for the protocol; it is not acceptance on
every supported Kindle firmware.

## Deliberately unavailable controls

Custom cover/progress sleep screens remain unavailable in this backend. The
current lifecycle explicitly returns display and input ownership to stock
`powerd` during sleep. Drawing an application cover before relinquishing the
framebuffer does not establish a persistent custom screensaver. Supporting it
requires a separate ownership protocol and model/firmware-specific sleep/wake
qualification; this change does not install a system screensaver replacement.

Stylus navigation and proximity-based palm rejection also remain unavailable.
The native `TouchInput` contract currently exposes a single finger pointer and
has no tool identity, stylus-button, or pen-proximity events. A Scribe design
profile alone is not evidence that these events have been discovered, mapped,
and fenced through suspend/resume. Enabling the switches without that input
contract would be a nonfunctional claim. These capabilities are projected as
false by the platform and stay disabled in Settings.

Automatic brightness remains outside the user-approved requirements. The new
policy work does not change manual frontlight/warmth behavior.

## Verification

The policy includes deterministic service doubles covering original-state
restoration order, already-disabled radios, manual changes, setter failures
after a partial write, unavailable cleanup with a later retry, and unknown
connection states. Execute these only on the designated remote worker. They
are correctness checks for ownership and failure handling, not physical Kindle
radio, battery, or sleep-screen acceptance.
