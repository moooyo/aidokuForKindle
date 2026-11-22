# Can the first Library screen respond to a tap?

**Historical only — do not run.** The interactive endpoint has been retired. This pilot stopped before the card press and produced no sample. Use the [automatic render-only protocol](../rendered-library/README.md); it requires neither manual input nor recording. The instructions below document the frozen experiment, not an action for an operator.

This pilot measures one normal open of the installed R3 on a Scribe. It does not install a candidate, replace a program, clear a cache or open a book. It leaves the earlier visual-only studies and their original timing anchors unchanged.

## Run the frozen visit

With the device awake, Wi-Fi connected, the ordinary stock entry available and the operator physically ready, run the prepared worker entry in a private terminal:

```powershell
ssh -t test-env /var/tmp/kindle-build-coordination/aidoku-stock-pilot
```

The runner checks the administrative cleanup and the live shared lease. It accepts the Kindle password before acquiring a new lease; it does not reserve a place in line. It navigates to the stock Library, checks the unique Aidoku artwork, and presses and releases that card through the stock X input path. **Do not tap the stock card yourself.** When the Aidoku Library first shows its layout and book entries, tap the sliders icon in its top right once. Leave the sort/filter panel open; do not choose a sort or tap again. The runner exits the app, restores its own navigation and lighting, closes the connection, and releases before interpreting the evidence.

`--status` and `--analyze` on the same entry are host-only. A missing tap, unrecognized screen or missed sampling window is **not measured**, not zero. Do not retry a consumed plan. Only the original operator uses `--recover` after an interrupted close. Never run the superseded cleanup as part of this pilot.

## Interpret the result

- Start: the measured release bracket of the guarded stock card click, not button-down, a shell command, or process start. This is a real stock UI dispatch, not a sensor measurement of a finger lifting from the stock tile.
- Visible shelf: the same visit's sampled toolbar, navigation and entries match the reference; covers are not part of this test. This particular pilot uses the existing six-book shelf. It does not certify an empty-state profile.
- Responsive shelf: a read-only touch witness observes one physical contact and release after the shelf is visible, and the continuous framebuffer trace shows the expected sort panel appearing in that visit. No application intent, screen route or input-ready log substitutes for this evidence.
- `ready_upper_ms` is the first confirmed panel observation minus the earliest possible stock-card release. It includes the operator's reaction and sampling delay. It is an upper bound, not an exact readiness time or optical e-ink timing. A single visit is neither a median nor a candidate speedup.

## Prepare a later visit

Use `host.py --freeze` on `test-env` with a **new private workspace**, fresh operator and the checked source inputs. Supply the published guard covering all three installed entries, including the lowercase BiliComics entry, rather than the older default guard. Bind an already closed reference and navigation batch as templates only. Keep the unmodified stock dispatcher, frame watcher, program references and the tested ARM touch witness in the frozen package. Use `host.py --preflight` before running. The [device protocol](PROTOCOL.md) describes the evidence. Keep private frames, credentials and device identifiers off the public record.
