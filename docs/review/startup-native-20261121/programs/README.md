# Temporary installed program selector

The outer lease holder calls `sh programs.sh OP LABEL`; `LABEL` is a new
16-character lowercase hex directory label, not a lease token. Use these
operations only during a previously admitted, continuously owned device batch:

- `prepare`: compare the current control bytes against their independently
  retained originals, check modes and the unchanged helper, require no existing
  mount on any selected leaf, and bind metadata to the private journal.
- `candidate`, `control`: select the requested uninstrumented tuple at the
  normal installed paths with native control idle. Both leave the app lock
  absent for an ordinary launch. Repeated selections are permitted.
- `restore`: require no mounts left on the two installed leaves, the original
  file identities and metadata, **and** fresh equality with their control
  bytes. This is also the recovery path for an interrupted switch.

The existing indexed document, registration, data, and installed files are
never replaced. Only the candidate app and launcher are temporarily exposed
using **two single-file bind mounts**. The official document still starts
the normal launcher path, and the launcher still starts the normal application
path. No bind or write targets a peer, directory, helper or user-data file.
Both mounts must be gone before the outer operator may remove the stage.
The physical probe found `fuse.fsp` refused a no-replace rename to an empty
destination on `/mnt/us`; the failed preparation had no timed visits.
There is **no** fallback to rename, ordinary replacement, or hard links.

## Custody and recovery

Create `/mnt/us/.aidoku-native-$label` and `/var/tmp/aidoku-native-$label`,
each with a regular `owner` containing the label and a newline. Stage both
`control/` and `candidate/` with their `launch.sh`, `app/aidoku` and identical
`app/aidoku-startup-portrait`. Include `control/program-modes` from the
accepted read-only collection: one original mode per line, in app, launcher,
helper order. `payload/rename-new` must be executable at `$stage/rename-new`;
it only moves the two `/tmp` **lock directories**, not any program file.

Before each bind, write an atomic owner-bound intent under the private
journal. Check `/proc/self/mountinfo` for **exactly one** mount at the
destination, the expected source path relative to the source filesystem's
mount root, device numbers and parent mount ID. Verify that the visible
inode, size, mode, owner/group, mtime and ctime match the frozen source.
Record and recheck the new mount ID. Recovery after a crash between `mount`
and ID publication uses the existing intent, unique source and target,
source inode and single observed mount. A mount ID is not a permanent
identifier by itself. Any unrelated/stacked layer, changed ID after recording
or source change fails without unmounting it. Only normal `umount` is
permitted; do not force or lazily detach a busy mount. An unresolved
transaction retains its owned launch lock and journal, not a success marker.
No SSH connection, lease or device permission is provided by this module.

For an owned terminal failure call `restore` directly; a generic stock-idle
guard must not mistake the selector's temporary app lock for a foreign app.
Only the two fixed lock leaves may use the system `/tmp` parent alias. Bind
the resolved parent and its root-owned identity, compare both leaf spellings
and recheck the original directory/marker across the rename. Directory mode
must be `0700` or inherited `02700`; require a single regular `0600` owner
marker matching the label. Never follow a symlink leaf or symlinked owner.

## Host rehearsal

Run `python3 -B test_programs.py -v` **inside `ubuntu-run`**, which gives
the fixtures a disposable mount namespace. They perform real file binds and
ordinary unmounts without touching any device. Cases exercise repeated
selection, original inode and metadata restoration, abrupt worker death
immediately after each bind/unmount, catchable termination, rejected foreign
and stacked layers, changed source/ID, peer lock, symlink, helper mismatch,
partial preparation and actual setgid tmpfs with a parent alias. The script
retains full control-byte comparisons at admission and final closeout,
but not on every measured visit.

The small `/tmp` lock helper uses `renameat2(RENAME_NOREPLACE)` only.
Build it with the shared cross-toolchain and build lock:
`zig cc -target arm-linux-musleabihf -Os -static -s -Wall -Werror -o rename-new rename_new.c`.
The 3,096-byte static ARM executable passed `sh test_rename.sh qemu-arm
./rename-new`; the native C build passed the same collision and directory
tests. Neither result by itself qualifies a foreign filesystem or a device.
