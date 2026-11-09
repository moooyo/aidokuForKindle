# Phase 3 Native results

Candidate18's bounded final Native recheck passed two actual cases on Source
`42b11de8d1b07e7af30665f98bb0dbf6291c0d6a6ec3355005c1402f42b21f24`,
ARM `35dd10da4d6101733ff468d3267d5b98594357465a034323df8ecf625753749d`
and the 31,642-byte launcher
`e4a4bc84a1735889079f3b3f25dcc090c8b55f1875e4bf0a9f393cede6d16254`.
Single90 and the final manual90/270 touch, 270 Down/Up and retained flat wake
each recorded one pass and zero failures. PID6613 retained epoch5/page15/manual270,
pressure0 and exact wake pixels without restarting image jobs. Normal exit
restored the exact stock body, active policy/USB1 and no App/sensor/guard/lock.

The [actual targeted stage](../artifacts/phase3/candidate18/native-targeted-returned/tree/native-targeted-01/native-targeted-stage-01.json)
is 3,303 bytes, SHA-256
`49893d49ee347d18e14135ea2ab0829bb3ced2eb083c349abe022bad217d7eb1`.
This is targeted C18 execution, not imported C16 twelve-case acceptance.

The same Native controller checked-paused its dedicated VM for the formal
window and restored it on the same connection. The
[controller state](../artifacts/phase3/candidate18/native-targeted-returned/tree/performance-control/window-01/state.json)
records restored=true, paused=false, no invalidation, one CONT/one RESUME,
paused 01:48:18 UTC to restored 01:55:50 UTC, enclosing formal 01:53:47 to
01:54:08. Fresh boot/owner/stock/policy/USB/no-App-lock checks passed and VM
resources RELEASED. This was not a host-wide lease or a physical-device test.

All 5,485 Native raw files / 495,008,486 bytes underwent ordinary local tree
extraction and exact worker-return comparison of names/bytes/SHA, with no
missing, extra, duplicate, mismatch or error. See the
[return verification](../artifacts/phase3/candidate18/native-targeted-transport-return-01/actual-local-return-verification-01.json)
and [owner release](../artifacts/phase3/candidate18/native-targeted-transport-return-01/owner-release-01.json).
The release digest is
`2638f715ce60497b7baccd815a88bb84420c3d72310f1ac47d37c9352aead919`.

## Explicit conditions and retained failures

The dedicated firmware VM used synthetic sensor input and RAM/devshm fixtures
after its persistent guest journal/storage became read-only. This does not
qualify physical Kindle sensors, persistent storage, memory limits, frontlight,
waveform, RF or input/display latency. Recognized portrait-sensor Up/Down active
presentation preserves chapter/page and manual landscape preference; manual
four-way settings are not automatic four-way rotation.

Original EROFS, sensor-helper return-value, duplicate-argument, guard and RAM05
timeout/unassessed/finally failures remain unchanged. The earlier failed frames
cannot be reinterpreted from a later fresh success. Active-WM-helper timeout is
a test-tool failure. Software readiness numbers are not physical panel timing.
Automatic brightness is not implemented or qualified; frontlight/warmth are the
exposed capability scope, without a claim that Scribe hardware lacks auto.

## Historical source scopes

C16's full selected twelve cases, separate 90/270 stages, eight namespaces /
sixteen read-only databases and complete returned tree remain its own evidence.
See [C16 returned Native](../artifacts/phase3/candidate16/native-runtime-01-returned/tree/native-runtime-01/native-acceptance-manifest.json)
and the [original Native document checkpoint](../artifacts/phase3/candidate15/documentation-staging/current-documents-before-final-01/docs/PHASE3_NATIVE_RESULTS.md)
for earlier C5/C11 identities, failures and limitations. No old PID, boot,
launcher, timing or acceptance is relabeled onto C18.
