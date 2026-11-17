# Qualified Library catalog access

This runtime is opt-in for the canonical Aidoku Library document. The shared
handler, catalog implementation and helper payloads must match their qualified
identities. Unrecognized combinations execute the original handler unchanged.
Firmware sources and libraries are not redistributed.

The wrapper loads a small hook into the original handler. Only its first
`utimes(path, NULL)` call for the marked Aidoku document is eligible. A bounded,
birth/session-checked child queries the native catalog service, commits recent
access without the immediate notification, and verifies the unchanged identity
and file. Failure retains the original timestamp and scan behavior.

On success, the wrapper passes the immutable helper path and hash to the
document. The independently owned launcher worker consumes the handoff, clears
it before starting application children, restores native display/input at exit,
and then publishes the normal catalog notification. The startup timestamp is
preserved. Existing 129-byte output validation, deadlines, ownership checks and
fallbacks remain enforced. The client also has its own whole-process alarm.

The installer registers one versioned runtime only after verifying all files.
It preserves unrelated registrations and never overwrites an existing runtime
with different bytes. Older versions remain available to resident workers.
The protocol is Aidoku-only today; other projects must coordinate any extension
of this shared integration instead of independently replacing its registration.
