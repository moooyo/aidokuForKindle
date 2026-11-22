# Library startup follow-up, 2026-11-21

Current acceptance measures [normal card release to the rendered Library](rendered-library/README.md), without a click or complete covers. The cohort decisions below retain their original endpoints. Their cover result is not the new headline startup result.

## Optional JPEG capability at startup

The [completed November 22 Scribe ordinary-card test](../startup-jpeg-native-20261122/README.md) found **no consistent improvement**: B was earlier in two of six pairs, with a paired median A−B of −31.223 ms. R3 was restored and left installed. The earlier host result below is a different endpoint.

The [JPEG admission comparison](jpeg-probe-admission/README.md) targets an operation that six earlier Scribe traces located before the first Library submission: a successful **25.456 ms median** check for a decoder that none of those Library frames used. The new product path moves qualification to the first actual consumer, not to a background thread. In the [two-profile host experiment](jpeg-probe-admission/HOST-TRIAL.md) the complete software frame arrived **13.83 ms earlier on the Scribe layout and 14.63 ms earlier on PW6**, at the paired median (8/8 each with identical pixels). Keep this source change; it is not a measurement of the ordinary installed card. The control for a later native A/B must have the same submission log as the candidate. Keep installed R3 unchanged.

## Confirmed: complete covers with an empty thumbnail cache

The post-SEND candidate submitted all covers in **3.980 s**, versus **4.657 s** for the installed control (separate medians). The median of within-pair savings was **680.849 ms**, about **14.6%** of the control median. All ten new pairs were faster. The other endpoint, first usable Library, improved by only **2.114 ms at the paired median** (5/10). The confirmation passed; the 1 s practical target did not. This is a result for the staged entry with six missing thumbnails, not a 0.68 s reduction in daily icon latency.

| Pair | Order | Control usable / all covers (ms) | Candidate usable / all covers (ms) | Cover saving (ms) |
| ---: | :---: | ---: | ---: | ---: |
| 1 | AB | 1395.522 / 4996.002 | 1434.139 / 4037.022 | 958.980 |
| 2 | BA | 1378.219 / 4643.727 | 1354.193 / 3938.247 | 705.480 |
| 3 | AB | 1392.569 / 4709.053 | 1467.608 / 4093.270 | 615.783 |
| 4 | BA | 1397.435 / 4695.866 | 1426.626 / 4236.337 | 459.529 |
| 5 | AB | 1377.007 / 4657.637 | 1355.955 / 3985.864 | 671.773 |
| 6 | BA | 1346.494 / 4632.853 | 1336.998 / 3938.151 | 694.702 |
| 7 | AB | 1339.270 / 4596.277 | 1344.538 / 3921.831 | 674.446 |
| 8 | BA | 1405.767 / 4652.795 | 1357.507 / 3965.544 | 687.251 |
| 9 | AB | 1383.152 / 4657.108 | 1434.554 / 4029.168 | 627.940 |
| 10 | BA | 1386.122 / 4695.098 | 1364.599 / 3973.470 | 721.628 |

Both independent pictures matched in the body and observable right toolbar, in software and on the live framebuffer. The checked input fence, six misses per visit, seven slots, protected state, stock body and native return passed. The stage was removed and the matching lease released at 03:10:34 Asia/Shanghai on November 21. No production program or user data was replaced. See the private `test-env` handoff for the closed empty-cache confirmation record.

## Rejected: skipping a progress frame on warm opens

The separate warm build eliminated the intervening progress submission in all six timed candidate visits: the first Library was frame 2, versus frame 3 on every control. That did **not** make the Library usable sooner. From the same staged trigger, control and candidate medians were **1.369 s and 1.410 s** to first usable and **2.084 s and 2.108 s** to all covers. Paired medians were **37.559 ms slower** and **52.912 ms slower** respectively, with two candidate wins on either endpoint. The 100 ms / five-win screen failed, as did the 50 ms cover regression guard. Do not promote this build or pool it with the unchanged cold-confirmation binary. The trial changes were removed from the product working tree.

| Pair | Order | Control usable / all covers (ms) | Candidate usable / all covers (ms) | Usable saving (ms) |
| ---: | :---: | ---: | ---: | ---: |
| 1 | AB | 1366.341 / 2093.202 | 1345.319 / 2045.033 | +21.022 |
| 2 | BA | 1335.873 / 2026.644 | 1409.933 / 2121.363 | −74.060 |
| 3 | AB | 1445.589 / 2147.545 | 1411.017 / 2094.656 | +34.572 |
| 4 | BA | 1384.793 / 2094.961 | 1503.195 / 2197.586 | −118.402 |
| 5 | AB | 1362.822 / 2074.118 | 1686.086 / 2395.980 | −323.264 |
| 6 | BA | 1371.023 / 2065.480 | 1372.082 / 2076.585 | −1.059 |

For diagnosis, the first Library render itself took a median **202 ms** on the candidate and **138 ms** on control. The time from the initial input fence to the Library submission was 90 ms longer at the paired median. The timed JPEG capability check took 18–34 ms; it cannot by itself explain a 300 ms opportunity. Neither the observation of a slower render nor the removal of one submission isolates the cause of the entire launch difference.

All six measured visits per arm reused six derivatives and submitted seven slots. Both independent software and live-framebuffer pictures matched in the body and right toolbar. Both arms exited, the stock body and protected state matched, and the temporary stage and owner/token lease were released at 03:17:13 Asia/Shanghai. See the warm-start entry in the private `test-env` handoff for the source delta, results and trace archive.

The rejected warm build passed its ARM build and QEMU CLI check, 23 targeted startup tests (including both ready and waiting resource cases) and seven native-media tests in the shared Linux userspace. Fresh L01 captures of the same offline demo matched the previous desktop build pixel-for-pixel on both PW5 (eight visible books) and Scribe (15); the body and right toolbar were checked independently. Passing these checks did not override the measured regression. The disposable build and trial files were cleaned up (3.58 GiB removed from the private worker directory); the patch, captures and closed run records remain.

## Installed version and rollback

No installed program was changed. A subsequent [read-only operation](../startup-fast-20261119/operator/normal-entry/README.md) retained the three installed Aidoku programs and their modes for rollback. The launcher matched the R3 Git bytes and the portrait helper matched the tested helper. The 53,710,336-byte main program is an observed control, not a reconstruction from a public receipt. There was no read of user books or application data. This separate lease closed at 03:19:11 Asia/Shanghai. Within the control-retention workspace, the original programs are under `runs/observed-<run-id>/`; the private `test-env` handoff identifies the exact run.

## Two independent comparisons

- **Confirm the empty-cache result.** The November 20 [screen](../startup-fast-20261119/PHYSICAL.md) qualified the unchanged post-SEND candidate only for empty-cache confirmation. Reuse its exact program, not a rebuild. Give both arms separate warmups, ten new alternating AB/BA pairs and independent pictures. The [frozen rule](../startup-fast-20261119/operator/clean-worker/staged-pilot/cohort.py) requires a median paired complete-cover saving of at least 100 ms, nine wins and no more than 50 ms median usable-screen regression. Report the 1 s practical target separately.
- **Try to improve the warm first screen.** All six warm traces of the post-SEND candidate recorded an activity pulse and a submission between the initial full frame and the Library. The Library frame spent 26–40 ms waiting for preceding framebuffer consumption despite fonts and metadata both being ready before the initial input fence. Do not refresh the progress animation just for crossing that fence; continue animating actual resource waits. Test the revised build against R3 in a new six-pair warm screen. Its primary endpoint is the first submitted Library frame with software input ready; report all covers as a guard. Use the existing 100 ms / five-win screen and 50 ms guard, and list the 300 ms practical target separately.

The new warm build also times its existing JPEG-capability probe. It does not move that probe onto a worker: the helper can outlive a timed-out caller, and the startup sleep path does not currently join it. Timing a bounded query is not evidence that moving it would be safe or faster.

Both comparisons start at a staged managed foreground launcher on the Scribe, using an isolated six-book synthetic shelf. Timing visits do not take pictures. Validate six cache hits or misses, seven visible slots, the checked initial display/input fence, final body and right-toolbar pixels on both software and live framebuffer, ordinary exit, restoration and lease release. Do not pool visits across builds, regimes or prior cohorts. Neither the staged clock nor the framebuffer measures a finger tap to settled ink. The [installed R3 tile measurement](../startup-entry-timing-20261119/README.md) remains separate.
