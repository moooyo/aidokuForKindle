# Ordinary installed-entry qualification

This is a **functional** release check, not a way to estimate tap-to-screen
latency. There is no device command in `check_programs.py`. Do not unpack a
staged trial onto a Kindle. The trial launchers force offline mode, recording
and timed exit; its fixture is not the user's Library.

## Prepare before taking a lease

1. Select the actual measured ARM build and the production `packaging/kindle/launch.sh`
   from its source. On `test-env`, compare them directly against the original
   payloads that are still retained. For example, from a copy
   of this operator tree with its sibling `bench/`:

   ```sh
   python3 -B normal-entry/check_programs.py "$programs" "$r3_git_launcher" \
     "$selected_source/packaging/kindle/launch.sh" "$warm_payload" "$cold_payload"
   ```

   The checker compares the two ELFs byte-for-byte, reconstructs the exact
   instrumented launchers in memory and checks the untouched control launcher.
   It neither verifies the trial outcome nor establishes that any file is
   currently installed. Reject a different build, even one from the same
   commit. Review the cohort results, pixel/input checks and known failures
   separately. Do not mix startup revisions to qualify one release.

   On November 21 the checker passed two host tests (including changed bytes
   at unchanged size, duplicate/missing members and a raw rather than
   instrumented launcher). The post-SEND programs matched the new frozen
   cold-confirmation upload. The older warm/cold screen uploads had already
   been cleaned up: no archive replay is claimed for them. Their retained
   programs and both per-regime instrumented launchers compared equal to
   the confirmation inputs. This is custody of the retained inputs, not an
   outcome for the new confirmation or a qualification for a later revision.
2. The proposed write set relative to R3 is `/mnt/us/aidoku/app/aidoku` and
   `/mnt/us/aidoku/launch.sh` only. The existing portrait helper **must match
   the one in the selected trial** for this two-file transaction. Preserve
   the indexed document, covers, other helpers, user data and all peer files.
   Supply trusted current byte copies of the old programs, including the
   helper, or explicitly qualify the *observed installed control* as a new
   baseline in its own short, read-only batch. A historical receipt, file size
   or recompiled binary does not supply missing original bytes. Retain the
   actual original programs and modes for rollback, not a copy of user data.
3. Use current owner-approved peer expectations. The shared worker directory is
   `/var/tmp/kindle-build-coordination`. Its `weread-20261120.txt` rollout
   statement supersedes the earlier missing-supervisor assumption: preserve
   both the production document and supervisor and both packaged helpers.
   `bilicomics-inventory-1611.json` is time-scoped, not perpetual proof of
   absence. `bilicomics-registration-note.txt` explains why the old digest is
   not a SQL baseline. Use the existing owner-reviewed `registration()` projection
   (`weread/tools/kindle-font-trial/device.sh`): the exact selected command and
   one `MT:text/x-shellscript` association, plus ordered handler properties and
   associations before/after. Query the one exact Aidoku catalog path separately.
   No registration setter, scanner request, peer restore or new item is part of
   deployment. If a peer has changed independently, do not overwrite it.

## One later, guarded batch

- Start only when the inputs, the positive rehearsal and the interrupted
  transaction rollback are ready. Use the **host** owner/token lease. Verify
  native foreground, active power, statusbar, running window manager, unchanged
  input roster, panel geometry and lights; no app or peer lock may be active.
  Bind the two old program bytes and the installed helper before doing anything.
- Run the current installed **control** once through the existing canonical
  document URI using the R3 [smoke protocol](../../../startup-next-20261118/promotion/smoke.sh).
  Bind the new worker, runner and app by parent, birth and nonce to this
  request and launcher log interval. Use normal Scribe arguments and its real
  data directory, with no research flags. Require the active route to be the
  Library and retain an independently identified upright physical reference
  to its body and nonuniform right toolbar. Do not confuse an open evdev FD
  with a measured input-readiness timestamp.
- With the app stopped and its launch lock exclusively held, stage each of the
  two byte-matched new files on the same filesystem and rename atomically.
  Keep an owner-bound intent journal, original modes and both old byte copies.
  If either target or backup stops matching, do not continue replacing files.
  The R3 [transaction](../../../startup-next-20261118/promotion/transaction.sh)
  documents the stopped-app lock and rollback order; its historical hashes
  and absolute plans do **not** make it runnable on this new selection.
- Open the same URI normally on the candidate. Verify the app path, approved
  argv and physical input descriptors. Check equal observable Library body and
  right toolbar against the control, then short sleep and same-process wake,
  released/reacquired input and equal restored pixels. Bind the device's active
  framebuffer page and independently established upright orientation; the old
  P6 helper expected rotation 3, while the rebuilt trial observed rotation 1
  and a double-height framebuffer. Raw unnormalized bytes or two equal blank
  screens do not pass. Use the [R3 lifecycle checks](../../../startup-next-20261118/promotion/lifecycle_device.sh)
  for app generations, acknowledgements and bounded owned shutdown. Keep any
  book pixels private; do not export user content in a public receipt.
- Signal only the bound app for ordinary exit. Require its PID/nonce/current
  epoch acknowledgement (zero on an ordinary never-suspended visit), one good
  worker exit and one completed catalog notification with no reindex fallback.
  The same single catalog identity must remain and its access time must advance.
  Restore and verify native foreground, input/display ownership, original
  lights, and unchanged peers/registration. If qualification fails after a
  swap, close only the owned session, roll back **both** program files only if
  still the known old or installed candidate, and verify exact old bytes and
  metadata. Preserve an unresolved journal rather than overwrite third-party
  changes. Remove owned staging and release promptly; analyze off-device.

**Rebuilt-worker boundary:** the November 20 staged runs restored their
temporary control copies on exit; their payload archives do not contain the
installed R3 application. Neither the old normal-entry raster helper nor its
private upright reference is an input to those runs. A double-height staged
capture is not a reference for the daily Library. Until original program
custody and a tested normal-entry capture/recovery path have been established,
the checker is valid preparation, **not deployment admission**. No coordinates,
tap durations or optical timing are inferred here.

On November 21 the read-only collection below succeeded: the host now has
the observed originals and modes, and the launcher and helper matched their
references. The [follow-up report](../../../startup-followup-20261121/README.md)
records the closeout. This removes the missing-programs prerequisite, **not**
the normal-entry panel, lifecycle and recovery qualification.

## Read-only collection of the current control

`retain_control.py` is a standalone host operator for the shorter prerequisite,
not the deployment. Set `work` to the prepared owner-private worker directory.
Use its existing trusted transport and private authentication environment.

```sh
python3 -B "$work/normal-entry/retain_control.py" --work "$work/runs" \
  --owner "$owner_thread" --lease-tool "$work/device_lease.py" \
  --launcher "$work/references/r3-launch.sh" --portrait "$work/references/portrait" --check-only
```

Only the designated device operator removes `--check-only`, immediately before
the actual collection. This takes one fresh host owner/token lease and makes
one SSH connection. The remote shell requires stock foreground and power, no
peer lock or active reader, and stable canonical executable files. It opens
exactly the three program files, checks their descriptor identities against
their names, streams their bytes and modes, and rechecks native and file
identities. It starts no app, creates no device stage and reads no book
or application data. SSH closes and the lease is released **before** host
comparison or publication, including refusal, timeout and handled interruption.
An existing foreign lease is never acquired or released by this operator.

On success the private attempt has `control/{app/aidoku,launch.sh,app/aidoku-startup-portrait,modes.json}`.
The host retains these bytes privately; use `modes.json`, not workstation
copy modes, when restoring. The launcher must match the Git R3 launcher;
the helper must match the supplied measured-build helper. If otherwise
complete, plausible program bytes fail either comparison they are kept under
`not-admitted/` for diagnosis, the result is unsuccessful and there is no
`control/`. A refused, changed or truncated capture is never accepted. This
can identify a newly observed installed control, not prove that the main ELF
is a particular historical release. The receipt contains no user records.
