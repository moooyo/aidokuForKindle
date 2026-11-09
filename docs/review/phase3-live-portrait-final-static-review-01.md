# Bounded live portrait static review

The review covers the isolated implementation in
`artifacts/phase3/live-inversion-implementation-01/source`. It does not grant a
build, test, runtime, physical-device or final-source acceptance result.

One confirmed startup and resume blocker was found: a recognized sensor's flat
or unreviewed numeric sample propagated as an error, preventing launch or leaving
the retained application suspended. The implementation now distinguishes sensor
presence from a valid holding sample. A recognized source remains automatic while
its pose is absent. The fallback uses the current transaction's calibrated
scanout, sets `holding_from_sensor=false`, and does not reuse a cached holding
pose or infer an absolute pose from a raw kernel rotation code. A subsequent
complete valid sensor frame remains able to update the live presentation.

The four changed blocks were independently reread after that repair. No further
confirmed blocker or statically apparent compile error was found within this
bounded review. Metadata I/O, ambiguous or unreadable sources, ownership changes
and unsupported framebuffer layouts retain their explicit rejection paths.

The remaining static scope includes control-before-sensor-before-touch event
ordering, independent manual rotation, ownership and input generations,
loss-aware touch drain across repeated `SYN_DROPPED`, and the GC16 completion
boundary. A temporary post-frame `WouldBlock` retains input ownership and waits
for readiness rather than repeating the same frame. Reader identity, page,
anchor and application rotation preferences are retained by the reviewed paths.

The reference BiliComics code was used only to compare implementation contracts.
Its builds and physical tests supply no Aidoku validation credit. All required
builds, tests and runtime checks remain the responsibility of the remote final
candidate verification. No local verification was performed.
