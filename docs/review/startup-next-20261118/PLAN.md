# Indexed Library startup trials after 78c5b2b

Date: 2026-11-18. The reference is the installed application from
`78c5b2bc09a469133c886eb0d73aaf9ed63195b2`. The prior [six-pair
report](../startup-catalog-optimization-20261117/README.md) found 2.278 s
to a usable Library frame and 2.829 s to all visible covers. These historical
medians are context, not controls for a new trial.

| Trial | Intervention | Primary endpoint | Secondary guard |
| --- | --- | --- | --- |
| Startup supervisor | Use a native supervisor for the existing startup portrait/capability read. Keep the portrait writer, checked display transition and lifecycle. | File-URI request to first submitted Library frame with software input available | Complete visible covers |
| Cached covers | Prepare at most the first two eligible local, already-processed visible covers. Keep the same scene, input barrier, source verification and normal post-frame admission. | File-URI request to driver acceptance of the complete visible cover set | First usable Library frame |
| First-time local thumbnails | On eligible local originals without a processed cover, avoid a duplicate full source decode while preserving file identity, integrity, resource limits and final pixels. Compare independently with the installed application. | File-URI request to driver acceptance of the complete visible cover set, starting with no processed thumbnails | First usable Library frame |

Isolate one change per test. Use both alternatives on the same binary and
launcher when possible and identify when the control uses the installed
artifact. Confirm the candidate mechanism executed; a silent fallback does not
measure the intervention. Report lack of eligible cache hits, without dropping
those launches from end-to-end results.

## Decision rule fixed before sampling

1. Use the real indexed item, identical boot/fixture/warm-cache policy and a
   predetermined paired crossover. Keep warmups, pixel capture, CPU diagnostics
   and lifecycle qualification outside primary timing samples. Positive paired
   savings mean `control - candidate` for the same pair.
2. Screen each intervention in six pairs (three in each order). Proceed to
   independent confirmation only if at least five pairs are faster on its
   primary endpoint, the median paired saving is at least 100 ms, and the
   secondary endpoint's median paired regression is at most 50 ms. This is an
   engineering screen, not a claim of statistical significance.
3. Independently confirm an eligible candidate in ten new pairs, five in each
   order. Require at least nine primary improvements, median paired saving of
   at least 100 ms, and the same secondary guard. Inspect signed differences
   by order, individual values and exit behavior. Do not promote an ordering,
   lifecycle or correctness regression because an aggregate metric passed.
4. Report attempts that stop early, fail, lose a required event or cannot use
   the device. Do not selectively replace samples based on observed speed. A
   failed harness can be repaired and restarted as a separately identified
   batch. Do not silently combine a restart with the original batch.
5. Only promote qualified product code. Preserve the catalog update/notice,
   the installed Library item, recent access/identity, cover pixels, original
   native state, input and lighting. Test the final entry again without research
   redirection after any accepted installation. If successful interventions are
   combined, qualify the exact combined defaults on the physical device; do not
   add isolated gains. Optical response, downloads and post-reboot cold startup
   are outside this measurement.

## Device and source boundaries

Build and validate through `ssh test-env`, with Cargo concurrency six. Prepare
host tools, fixture, artifacts and restoration plan before acquiring a fresh
lease; one operator handles device commands. Use the actual Library document
and existing qualified registration. Any test routing must be symmetric and
removed with owner/content checks. Restore the installed program and native
state and close all device connections before releasing each batch. Do not
publish user metadata, screenshots, identifiers or private lease material.

The [prior opportunity data](../startup-catalog-optimization-20261117/evidence/remaining-opportunity-evidence.json)
also showed one partial cover frame in six runs. Inspect recurrence before
testing a grace period. Six pairs cannot establish a P95 improvement. The first
Library render cost is a budget; without a measured cause it does not justify
changing fonts, removing feedback or skipping a display/input check. Parked
repeat-open is a different lifecycle and cannot be inferred from these trials.

## First-time thumbnail trial

This additional hypothesis was registered before its physical measurements.
Three historical warmups with no local derivatives took approximately 10.5,
10.8 and 11.3 seconds from first frame to complete covers. They motivated the
experiment; they are not paired measurements against today's installed version.
A previous independent diagnostic attributed about 2.7 seconds to six full
source image validations. Check for duplicate validation plus decoding only on
the miss path. Do not remove admission, truncation/integrity validation or the
fresh proof of the source bytes used for publication.

For every control and candidate open, seed the same private copy of library
metadata and profile into a separate data directory with no cache. Require six
local cover jobs, six misses and zero derivative hits. Warmups also get their
own fresh directories. Record how often the new single-decode path was used;
unsupported formats retain their fallback. Keep both programs' input/output
images, network policy and exit dwell equal. Follow the same six-pair screen,
ten independent confirmation pairs, 100 ms / 50 ms guards, and independent
pixel and lifecycle requirements above. Recheck a normal warm open for any
selected cold-only implementation. This measures a cold application thumbnail
cache on a warm operating system, not a reboot or the cost of downloading art.

## Last visible cover trial

The completed two-cover prewarm screen produced intermediate frames in 4/6
control and 2/6 candidate launches; only one candidate launch consumed any
prewarmed pixels. Those data motivate a separate test, not a result for a new
implementation. Before timing the new implementation, select the same warm
fixture and balance six new pairs. Use complete cover submission as primary,
first usable frame as secondary, and the same decision rule above. Keep every
case, including zero triggers. Record whether a bounded grace period was
actually used, hit its deadline, or delivered a single final frame. A change
that only improves occasional tails without passing the full-sample decision
rule is not promoted, and no six-pair P95 improvement is inferred. Never delay
completed results or user-driven work to wait for an optional cover.

## Independent cold-thumbnail and toolbar-status candidate

This plan is recorded before any physical samples of the new composition.
The previously tested standalone cold-thumbnail ELF `d9be7683` improved cold
cover completion, but its six-pair warm protection check failed. It does not
proceed to the independent production-default lifecycle check, installation,
or daily use. Its cold screen, confirmation and pixel evidence belong only to
that ELF. Keep the failed warm check in the record.

The new candidate is a distinct production-default ELF combining the
cold-thumbnail change with a bounded, fresh, read-only toolbar-status probe.
It reads Wi-Fi and battery after the initial framebuffer submission and before
the driver's checked wait, and reaps its helper. There is no background status
worker. Its intended warm-path change is to provide an already-observed status
for the initial Library view instead of issuing synchronous device queries
from that view. Also measure initial submission to input readiness: advancing
a read cannot be credited if it simply delays that boundary.
If the host implementation, identity binding or correctness gates fail,
record the reason and do not claim a physical result. A and B have the same
installed launcher, native-query helper, indexed entry, workload-specific cache policy
and native ownership checks. A is the currently installed `78c5b2b` ELF on
both workloads (`62de2771...`); B must have a new frozen hash, unchanged from
screening through qualification. Never combine observations from `d9be7683`
with B or substitute a new B partway through a stage.

- **Cold performance:** start each launch from its own empty application
  thumbnail cache on the same six-local-cover fixture. Require six misses,
  zero derivative hits, a fully covered Library frame and the established
  native/catalog guards. Predetermine six new pairs (three AB, three BA);
  require five improvements and at least 100 ms median paired improvement to
  complete covers, with at most 50 ms paired median regression at the first
  usable frame. Confirm in ten independent new pairs (five AB, five BA),
  requiring nine improvements and the same 100/50 ms thresholds. Use the
  installed A for both stages. Record all launches including status misses.
- **Warm protection first:** six new pairs (three AB, three BA) with the same
  installed A and the same new B, six verified derivative hits per launch
  and zero misses. Both first usable Library frame and complete covers must
  have median paired savings (A minus B) of at least -50 ms. This is only a
  regression guard. Stop the new composition if it does not pass; do not retry
  until it passes or claim statistical noninferiority. Report exit-to-native
  activity separately for both workloads.
- **Mechanism:** report attempted, hit, fallback and unverified counts on
  both sides. Show that a fresh status result was consumed by the first
  Library projection and that synchronous native status queries were absent
  from that projection on hits. Preserve the existing synchronous path on a
  stale, failed or unavailable result. A faster frame alone cannot establish
  this mechanism; do not omit misses or count time spent in the earlier probe
  as a second claimed end-to-end saving.
- **Independent qualification:** in separately identified cases check pixels,
  usable input, ordinary exit, restoration of native state and lighting,
  recent access and notification, and same-process short sleep/wake using
  the exact new defaults. Screenshot or profiling cases do not enter the
  timing pairs. Only select the exact B after all gates pass, and verify
  the unmodified installed entry separately after any deployment. Record
  failed, not reached and not executed stages explicitly; no active device
  registration or user cache is changed for a failed trial.
