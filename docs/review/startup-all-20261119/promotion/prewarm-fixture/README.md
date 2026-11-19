# Controlled primary-connection prewarm qualification

The [source adapter](qualification.rs) runs in a test-only companion of the
exact production sources. Each of the eight cases starts a separate process,
private data directory and synthetic local indexed PNG. It seeds the real
derivative through the normal cover worker, empties the process pixel cache,
and obtains the initial hints from the primary application metadata connection.
Another SQLite connection then holds an explicit exclusive lock: independent
fresh reads of the Library database must report busy/locked while an independent
reader of the **separate derivative index** must still find its one valid row.
No synthetic user book, source credential or user database is used.

The positive case requires the actual prepare worker to finish a validated
prewarm and the ordinary foreground job to consume that exact memory result.
It also runs the disabled path at the same canonical source/derivative path
and independently decodes the current original; the complete RGB output must
match. The prewarm itself must not create or change any disk derivative.

Negative cases alter the original before and after preparation with the same
file size and restored modification time, remove the derivative before and
after preparation, cancel an already selected plan, or occupy the decode
permit. The unchanged normal worker must reject stale or absent derivatives
and produce the expected current output when necessary. The disabled case
must emit no prewarm plan, attempt or consumption marker.

[All eight cases for the final v2 application](final-v2-prewarm.json) passed,
including an independent replay with the exact source-bound test executable.
The earlier `3ef03a41...` performance test's zero usable prewarm runs remain
recorded as zero. These Linux-host cases prove the real component can work
under the controlled contended database condition; they do not prove how often
this happens on a Kindle, any physical latency gain or a recommended default.
That requires the separate registered device protocol. The v2 configuration
keeps this option off by default.

Both feature proofs are built together by the [controlled runner](../grace-fixture/run_fixture.py).
Use the fresh v2 producer receipt, full source tree and ARM as described in the
[grace fixture record](../grace-fixture/README.md).
