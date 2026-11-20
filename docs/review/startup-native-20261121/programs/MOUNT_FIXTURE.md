# Isolated selector integration fixture

The integration test owns `test_bind_mounts.py` only; the selector owner
owns the implementation and its unit/negative fixture. The external
selector interface under test is `sh SELECTOR prepare|candidate|control|restore LABEL`.
The fixture provides separate control and candidate tuples, a known past
timestamp, and real Linux `mount`/`umount` in a fresh private mount namespace.
It does not operate a physical reader or the shared host lease.

The test exercises the actual syscall, interruption after the first real
file mount, interruption after the first unmount, a swapped candidate source,
and a foreign same-bytes overmount. The helper is never selected. It checks
mount IDs as well as file identity, and opens an original descriptor before
switching to prove that the underlying file was not modified. A failure
does not authorize detaching an unknown mount; fixture teardown removes
only mounts created within its own namespace.

On `test-env`, `python3 -B test_bind_mounts.py -v` passed all five integration
tests in a new private mount namespace. An independently compiled native
fixture launched via the selected live pathname reported that same target
pathname through `/proc/PID/exe`, not the staging pathname. The live file,
staged candidate and `/proc/PID/exe` had the same inode while the held
original inode was different; `/proc/PID/cmdline` carried exactly the live
path. Restoring after the child exited uncovered the original file. These
are Linux-worker observations, not Kindle measurements.
