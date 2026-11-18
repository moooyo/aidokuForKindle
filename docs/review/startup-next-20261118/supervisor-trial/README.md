# Startup portrait supervisor trial

**Decision: keep the existing launcher.** The instrumented indexed-entry screen
(`aidoku-next-native-screen-ae51d7bc`) measured the old shell implementation
against a native supervisor for the same portrait/capability transaction. All
six control runs used the shell and all six candidates used the native helper.
The paired median improvement in the first usable Library frame was 52.778 ms
(4/6 faster); complete visible covers improved by 17.061 ms (4/6 faster).
The [predetermined screen](../PLAN.md) required at least 100 ms and five primary
wins. No independent confirmation or production deployment followed.

[candidate.patch](candidate.patch) contains only this candidate's nine files,
including its build, packaging and failure tests. [verification.json](verification.json)
records Linux host test counts, fixture timings, per-file and binary hashes, and
the physical screen report hash. The host mock is not a Kindle speed estimate.

For host-only reproduction on `test-env`, start with an isolated checkout of
`78c5b2bc09a469133c886eb0d73aaf9ed63195b2`, apply `candidate.patch` from its
root, then run `sh scripts/build-startup-portrait.sh /tmp/aidoku-startup-portrait`
with Zig 0.15.2 on `PATH`. The replay rebuilt the measured 10,108-byte ARM ELF
byte-for-byte. The archived change is not part of the production package.
