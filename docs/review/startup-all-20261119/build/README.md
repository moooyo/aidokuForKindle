# Integrated build qualification

The operator uploads one source archive, including untracked production inputs,
to `test-env`. This harness never connects to a Kindle and does not sign a release.
The control is production revision `c6867d1` with source digest `1c7909c4...`;
`8539f3a` adds only the previous evidence. All Cargo commands take the existing
shared build lock and use six jobs. Linux mount namespaces give the integrated
build its own writable target over immutable dependency and compiler caches.

Copy `integration.py` and `run.sh` to `/root/aidoku-all-build-20261119/tools/`.
From the Windows checkout, the operator can construct the source input without
running local checks:

```powershell
$destination = Join-Path $env:TEMP ('aidoku-all-' + [guid]::NewGuid().ToString('N') + '.tar')
$items = @('Cargo.toml','Cargo.lock','build.rs','LICENSE','THIRD_PARTY_NOTICES.md','README.md','AGENTS.md','src','ui','assets','vendor','examples','tests','packaging','scripts')
foreach ($optional in @('.cargo','rust-toolchain.toml')) { if (Test-Path -LiteralPath $optional) { $items += $optional } }
tar --exclude=__pycache__ --exclude='*.pyc' --exclude=target --exclude=.git -cf $destination @items
if ($LASTEXITCODE -ne 0) { throw 'Could not assemble the source archive' }
scp $destination test-env:/root/aidoku-all-build-20261119/incoming/final.tar
```

On test-env, set `r=/root/aidoku-all-build-20261119` and use the explicit label
`final` only after all contributors have frozen their files. A separate label
preserves each preliminary, corrected or failed experiment.

```sh
python3 -B -I "$r/tools/integration.py" freeze final "$r/incoming/final.tar"
sh "$r/tools/run.sh" final arm
python3 -B -I "$r/tools/integration.py" receipt final
sh "$r/tools/run.sh" final host
sh "$r/tools/run.sh" final desktop
python3 -B -I "$r/tools/integration.py" settings-ui final
```

`freeze` validates archive paths, extracts a new read-only tree, and records
the product manifest, all build/packaging/test input hashes and differences
from the installed source. It never replaces an existing label. Each compile
checks this snapshot before and after the invocation, binds the actual fresh
Cargo event to its exact executable and retains compiler and test logs. Test
executables are also retained so that feature-specific experiments do not need
another full build. Host integration tests use a fixed, headless scene; their
screenshots are not physical output. Check individual suite results even if a
later independent stage is attempted after a failure.

- `sources/final/`: exact complete source used to compile and package.
- `artifacts/final/`: `candidate-arm`, `launch.sh`, `aidoku-startup-portrait`,
  `candidate-desktop`, and `host/` with reusable Linux test executables.
- `reports/final/`: `freeze.json`, `source-manifest.json`, `build-inputs.json`,
  `arm/`, `host/`, `desktop/`, `settings-ui/`, and `build-receipt.json`.

The build receipt binds the three actual release artifacts and the six packaging
input hashes consumed by `../promotion/prepare_all.py`. The production ZIP and
KPM are assembled only by that release workflow after qualification, from the
same frozen source and verified installed package members. Building an ELF or
passing host tests does not establish a Kindle timing, lifecycle or install.

For a subsequent launcher-only experiment, freeze a new complete source tree
with the same inputs and modes as `final-v2`, apart from the launcher bytes.
Run `python3 -B -I tools/reuse_programs.py final-v3 --from final-v2 --check-only`
on test-env, then omit `--check-only` to stage the two original native programs
and the new script and issue a new build receipt. Override the incoming archive
path with `--archive` only if its filename differs from the frozen label.
`reports/final-v3/reuse/` records the full input manifests, exact script diff
and permission checks. The original source count, fresh Rust compilation event,
compiler-produced executable and C helper are independently rechecked. R2's
post-build CPython sidecar, if present, is disclosed and matched by its exact
hash; it was not in the original source archive. No native program is rebuilt,
and the source digest alone is insufficient to identify a new launcher.
New physical measurements and the release gate are separate from this receipt.

The final integrated artifact used the distinct label `final-v2`. The
[verified build results](final-v2-verification.json) bind its three program
identities and all input hashes. The library passed 852 tests (six ignored)
and the application passed 22. Across 15 PW5 and 14 Scribe settings cases,
all 793 checks passed. The earlier label `final` is not a substitute for these
results. The host test run overall was not fully green, as explained below.

On November 19, the [paired sleep-screen test](sleep-baseline-control.json)
reproduced the exact PW5 dark-preference failure on both the installed
`c6867d1` source and `final-v2`. All five PNGs available before the stop matched
byte for byte. The dark paper was counted as artwork; the fitted 550 × 824
cover and all four gray markers matched the passing light image. The test
remains failed and its later scenes were not reached. The [November 17
baseline record](../../startup-sequence-20261117/preexisting-ui-test.json)
contains the same assertion and dimensions on an earlier revision.
