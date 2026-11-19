# Six Library startup features

Baseline: production commit `c6867d1`, application SHA-256
`9d92dc4cd01005a6ba2f48fe61786e5df5570c43feb5709fe3159bb164807d0f`.
The original qualified catalog registration, Library document, cover and
native query service remain the reference. The request is to implement all
six features. Feature availability and evidence of a speedup are separate.

| Feature | Independent variable | Primary comparison |
| --- | --- | --- |
| Startup portrait supervisor | Shell or fixed native helper | Indexed request to first usable Library frame |
| Local cached cover prewarm | Zero or two eligible covers | Indexed request to complete visible covers |
| Final-cover grace | Off or one bounded grace interval | Same cover endpoint, retaining every zero-trigger case |
| Import thumbnails | Off or bounded idle work | Import acknowledgment/accepted frame, background costs and later presentation of previously off-screen books |
| Library layout | Original row projection or batched projection and short-title measurement shortcut | First usable Library frame; query count, real drawing cost and pixel equality |
| Quick return | Full exit or explicit 45-second quick return | Second indexed open, first and complete frames; memory, idle CPU, native ownership, expiry and recovery |

For fresh-start comparisons use the existing indexed item, offline data,
consistent boot and stabilization, six predetermined pairs with three in each
order. Record raw pairs, including unfavorable and zero-hit/zero-grace cases.
Use the median of paired savings, control minus candidate. A primary benefit
of at least 100 ms in at least five of the six directions (and <=50 ms median
regression on the other endpoint) warrants independent confirmation in ten
new pairs: require a median saving of at least 100 ms and nine faster pairs.
This is a practical acceptance rule, not a population-wide statistical claim.
Warm and cold default-vs-installed checks are separate: require six pairs
per regime with no more than 50 ms median paired regression in either usable
Library frame or complete covers. These protection checks alone do not prove
a speedup. If a configuration misses a performance gate, retain its functional
result and distinguish optional availability from the recommended default.

Use deterministic generated books for import, never user books or a real
cache clear. Exclude fixture setup from latency. Use separate processes for
the import session and later presentation; measure both, as well as added
background work. Keep settings, assets and resolutions identical on both arms.
Do not count baseline-visible covers as additional work moved out of startup.

On quick return qualify native Home while the feature is parked, including
released input and display, ordinary exit, fresh portrait authorization on
reopen, expired/missing/stale state, and independent catalog completion per
visit. Record whether the GUI PID or only an application-owned pixel keeper
survived. Neither a normal powerd wake nor a cache hit proves GUI reuse.
Any GUI reentry prototype without a current native ownership/entry proof must
remain unselected, separately identified from the shippable pixel feature.

Software endpoints are accepted framebuffer submissions, not optical times.
Keep screenshots, process sampling and manual interventions separate from
primary timing trials. Verify library pixels and the right status bar, two
desktop profiles, default and optional paths, cooperative exit, short sleep/
wake and restoration before replacing a normal installation. Preserve
rollback copies. Credentials, identifiers, private paths and actual user
screenshots remain outside the repository. All verification runs on test-env.

## Second integration revision

The first candidate (`3ef03a41...`) remains a separate tested build. Its
prewarm encountered SQLite busy before every measured attempt. A controlled
clock fixture exposed a spent one-shot timer that could miss its release
turn. In an independent import pilot four offscreen derivatives were reused,
but the saved reopen time did not cover the initial user-visible delay. A
subsequent import screen failed a user-cache size/mtime guard and is excluded
in full; the user's cache was not written back to approximate the old hash.

For the next build, select paths on the primary metadata read snapshot before
starting downloads, retire the grace interval on the first spent-timer turn,
and leave idle thumbnail generation opt-in. The All preset still enables it.
Freeze a new ELF; do not transfer the earlier warm/cold default qualification,
performance claims, or individual clock qualification to it. Keep the same
per-feature practicality thresholds. Requalify the program, control values,
both cache regimes and the uninstrumented normal entry before installation.
