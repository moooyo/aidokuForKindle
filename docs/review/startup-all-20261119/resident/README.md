# Library cover retention for 45 seconds

`resident=45` opts in to a distinct **Quick return to Kindle** action. The window
and its GUI process exit, the usual independent launcher restores native control
and finishes its catalog notification, and a fresh Library open must complete
the normal portrait observation, framebuffer and input gates. The GUI PID is
not reused. The state reused is the last displayed, validated local cover art.
Ordinary **Exit** is still a full exit and never asks to retain new pixels.

The opt-in is consumed from the normal `data/startup-options` choices. The
launcher admits the action only for a managed Library session. Selecting Quick
return requires an active, initialized application and is unavailable while
sleeping, resuming or restoring metadata. The persisted choice is checked at
selection and again after normal shutdown has drained its workers. Turning the
choice off explicitly stops the keeper and revokes quick return for that run.
This feature installs no new executable, service, handler or system registration.

The same Aidoku ELF can start in a data-only role. It owns a private abstract
Unix socket and up to 2 MiB of memory with at most six recent, locally rendered
cover images. There are no persisted images or socket files, no database/network
activity and no framebuffer, input or brightness ownership. It accepts a single
authenticated same-ELF, same-effective-user consumer in the same data directory.
The nonrenewable 45 s deadline uses `CLOCK_BOOTTIME`. A kernel timer bounds even
a stopped helper. Unavailable or low available memory (below 48 MiB), wrong
version or directory, timeouts, an invalid transfer or a missing peer leave the
normal startup path available. A late peer can delay its opt-in consumer by
at most the 120 ms exchange deadline; it cannot start the GUI or own a lock.

No cache hit is granted by receiving a blob. Each normal cover job still proves
the current mutable source contents and the validated, currently indexed disk
derivative, namespace and complete rendering options. A valid resident image
can replace the usual PNG inflation for that job. Misses, changed sources or
rendering options and stale/replaced artifacts keep the normal disk path.
The speculative two-cover preload yields only to a matching resident item.
The tracing events `resident_library_keeper_published`,
`resident_library_keeper_reclaimed`, and `cover_resident_pixels_hit` distinguish
a published handoff from an actual saved decode; an admitted keeper alone is not
a user-visible speedup. Two opens must use the same executable inode and private
data directory for a controlled comparison.

[Host qualification](host-verification.json) records process isolation, bounds
and source/derivative eligibility. Host milliseconds and process RSS refer to
the standalone fixture rather than a complete Kindle program. See the
[transport probe](transport-probe/README.md) for reproduction. Measure the
formal indexed return/reopen path on the Kindle before claiming latency gains.
Use the [same-window research prototype](../resident-owner-prototype/README.md)
only for the separate original-GUI-PID question; its host rendering and mock
native resources do not demonstrate a physical Kindle admission transaction.
