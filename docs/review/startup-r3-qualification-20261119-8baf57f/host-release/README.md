# R3 release-tool regression and default lifecycle replay

Verified on `test-env` on 2026-11-19. The original tool snapshot is bound to
handoff commit `23a364015e30b3a6e65611dbfc0bc4c0a5657f00`; the production
candidate remains `8baf57f`. No product sources, frozen plans, authority
records, device outputs, or original release tools were changed.

| Check | Result | Evidence |
| --- | --- | --- |
| Original tools against the committed snapshot | All 16 files identical | [Input manifest](input-manifest.json) |
| Original complete suite | 48 passed, 0 failed, 0 skipped | [Log](original-suite.log), [result](original-suite.json) |
| Original complete R3 lineage replay | Failed at the final source comparison | [Log](original-lineage.log), [result](original-lineage.json) |
| Corrected complete suite | 50 passed, 0 failed, 0 skipped | [Log](corrected-suite.log), [result](corrected-suite.json) |
| Complete suite after private reference parameterization | 50 passed, 0 failed, 0 skipped | [Log](private-input-suite.log), [result](private-input-suite.json) |
| Corrected complete R3 lineage replay | Passed; output identical to the original accepted report | [Result](corrected-lineage.json), [report](corrected-lineage-report.json) |
| Corrected default lifecycle replay | All 11 checks passed; 4 physical frames verified | [Log](default-lifecycle.log), [report](default-lifecycle.json) |
| Original tool preservation after verification | All 16 files unchanged | [Record](original-preservation.json) |

The late source-integrity check exposed a variable-shadowing error:
`verify_inputs()` replaced its `original` source-tree mapping with host-report
bytes, then compared those bytes with another source-tree mapping. The
corrected copy uses `host_report_bytes`, retaining the complete before/after
source comparison. The trace-integrity check remains intact.

The two added regression cases exercise the real `verify_inputs()` entry point
and reject a changed second source snapshot, then reject a corrupted trace
in an independent temporary copy. Neither test changes frozen source files
or original trace data. The previous 48 tests did not invoke this complete
lineage path, which explains why their passing result did not catch the bug.

## Corrected tool bundle

Use the [complete tool copy](tools/) at
`/root/aidoku-r3-handoff-20261119/host-release/tools` on `test-env`.
The [repair patch](repair.patch) contains the three lineage repair files:
`r3_lineage.py`, `prepare_all.py`, and `test_r3_lineage.py`.
The [current manifest](current-manifest.json) binds every current tool and test;
the [earlier corrected manifest](corrected-manifest.json) preserves the version
tested immediately after the lineage repair.

The public `test_release_tools.py` now requires
`AIDOKU_RELEASE_REFERENCE_FRAME` instead of embedding the private reference
frame's location. The variable is mandatory, so a missing private input fails
the test rather than skipping it. The existing real reference frame was used
for all 50 cases. Its location and pixel digest are not exported. The separate
environment file exists only on `test-env`; it is not part of this directory's
public artifacts. This privacy edit is documented here instead of including
the removed private location in the patch.

- `r3_lineage.py`: `83a79a7f2ede1cb38aa68c2dcd05f820e0a5a55044ab66f3325c6e09a21c8e8b`.
- `prepare_all.py`: `6da089702085279889c3a17772c2958c32e26d67e104772873abaec0b6394904`.
- `defaults.py` is unchanged:
  `fe1d1243c9a289ec3ded4150730fe43027f39dc008f22335a642e4594f44a341`.

The package gate now selects `HERE/defaults.py` and requires the exact new
lineage-tool hash in `DEFAULT_DEPS`. Its other dependency pins are unchanged.
`assemble_r3.py` already obtains this auditor through `bound_auditor()`;
`run_all.py` already loads `prepare_all.py` and `r3_lineage.py` from its own
directory. Their bytes did not need to change. All four entry points resolve
the same corrected tool bundle, as recorded in
[dependency-binding.json](dependency-binding.json).

The corrected lineage output retains SHA-256
`b2697257ed4cc2030589af32168c4443eeaa0bbfc288b1591251f64947378268`.
The original inputs, accepted lineage report, lifecycle plan, authority,
source manifest, and program identities remain valid without rebinding.
Keep `/root/aidoku-all-release-20261119/tools` frozen. Run later default
qualification, release assembly, packaging, and installation checks through
the corrected bundle; direct calls to the original tool still reproduce the
recorded failure.

## Reproduction

Run the complete host suite through PowerShell:

```powershell
ssh test-env '. /root/aidoku-r3-handoff-20261119/host-release/private-reference.env && python3 -B -I -m unittest discover -s /root/aidoku-r3-handoff-20261119/host-release/tools -p "test_*.py" -v'
```

The existing R3 lifecycle batch `aidoku-all-life-b98ac9d9`, with plan digest
`b23e0e85ca825bacc3e13d0fc72ac8eafd946d88bbb103bd1170d1d0cece39ea`, was
qualified using the corrected `defaults.py`. The resulting report digest is
`30c357115ecf7f6ec90383504d3240cf2bb9ddac4602db5ec2df436eb03d4123`.
It confirms the recommended policy: optimized layout enabled; native portrait,
cover prewarm, grace, import thumbnails, and resident return disabled.

This work used host-side fixtures and replayed already collected device
evidence. It acquired no Kindle lease and issued no device commands. The
remaining physical campaign and final release gate retain their independent
requirements; this record does not establish a package or installation.
