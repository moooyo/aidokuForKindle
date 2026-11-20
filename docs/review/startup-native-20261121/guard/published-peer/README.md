# Offline enrollment for a retained BiliComics release

Do not enroll an install attempt, comparison, a `keep-intended` file alone,
or a successful smoke followed by rollback. The current uninstalled guard
is untouched by this tool. Enrollment never uses SSH, the Kindle or a lease.

The designated operator must first receive an explicit **final retention**
statement from the BiliComics owner for a specific `bili-prod-…` rollout:
normal Library click and exit passed, the installed tree and single lowercase
entry are retained, the scoped native/peer restoration passed, and the same
owner released the shared lease. A generic message about passing a build,
preparing a deployment or finishing a failed batch is not that statement.
Then provide the corresponding original private receipt directory, not
reconstructed JSON. `prepare.py` additionally requires its:

- admission for the measured package, Scribe/5.19.5 and unchanged production
  document; formal card-click and full normal runtime/exit evidence;
- the final owner public rollout record, keep intent, verified cleanup and
  matching project/run/owner acquisition and release with all restoration
  flags. A recorded failure otherwise refuses enrollment. The reviewed
  `bili-prod-f3690b72` exception retains the original observer failure: a
  separate owner receipt rechecked the same, still-running first launch on
  the original lease after correcting its `/tmp` parent alias, with **no
  second click**. The public normal-smoke projection and final release time
  must agree with the original private receipts;
- `document-view.json` from that owner operation, bound to the exact run,
  Kindle boot and document volume. It must identify one lowercase entry and
  classify its alternative lookup as `absent`, `same-inode` or
  `casefold-proven`. The owner independently proved the last case using its
  same-volume test directory; matching current file contents or names alone
  cannot establish it.

Run on the private `test-env` host, using a new output directory each time:

```sh
python3 -B published-peer/prepare.py /absolute/path/to/unchanged/guard.sh \
    "$owner_rollout" "$existing_private_parent" --owner-confirmed-retained "$owner_run"
```

It emits a new private `frozen-bili-guard-*/guard.sh`; it does not install or
select it. The generator verifies the original private boot, volume and
lookup identities but never puts them in the script. Only after a final owner
decision should a new plan select that copy. All existing reference, recovery
and failed plans keep their original guard bytes. If the owner rolls back, the
original uninstalled guard applies unchanged. Do not edit a previously frozen
plan or substitute a fresh device observation for the owner's witness.

The new guard lists only BiliComics-name candidates under `documents`. It
requires exactly one actual basename `bilicomics.sh`, no selected sidecars and
one lower-case catalog row with the public title/MIME. It checks both lookup
relations and public document bytes when an alias is expected. Both lookups
must be on the current document-directory volume, with a stable single-link
regular leaf across each individual read. For the independently proven
folded volume, a virtual alternate inode need not equal the lowercase or
earlier boot's inode. The private batch projection still binds its native
boot, real lowercase entry, peer file stamps and selected rows before/after.
It protects file types, sizes, declared program/launcher leaves,
the public configuration and selected notices; it neither opens user data nor
writes peer files or registration. The synthetic test keeps the upper lookup
physically separate from the enumerated directory to model different inodes
without inventing a second directory entry.
