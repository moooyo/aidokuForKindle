# Distinguish transport failure from stock admission refusal

Start with the private `operations.json` from a **returned** visit. Inspect only the first `guard` operation's `outcome`, `exit_code` and allowlisted `refusal` / `transport_error`. Do not publish the report, its identities, or SSH stderr.

| Observed result | Safe interpretation |
| --- | --- |
| SSH exit 255, missing status, or transport timeout | No verified device result. It does not establish sleep, a foreground app, or peer activity. A recorded transport subreason may identify an authentication or network error, not a stock predicate. |
| Exit 1, `guard_admission` | The device refused its combined projection. It does not identify power, foreground, stock services, protected files or registration individually. |
| Exit 1, `guard_lights` | The stock projection passed and the subsequent light read/validation failed. No conclusion about an individual light or value. |
| `guard` passed, then device `admit` failed | A different check refused: it also checks stage absence, mounts and free space. Do not assign that refusal to the guard. |

An optional **separate, read-only** visit can stream [the probe](stock_admission_probe.sh) as its shell command, with the exact frozen `plan/guard.sh` bytes on stdin. The shell body must **not** consume stdin to load itself: pass it with `sh -c`, `sh` as `$0`, and a fresh 16-hex visit label as `$1`. The only guard action selected is `admit`. Use the already approved pinned SSH transport, private authentication, a finite deadline, exclusive lease and checked release; do not run a study slot, modify the frozen plan or install either script. Never use the historical base guard in place of the current installed-peer guard.

The probe runs the frozen predicate once. Only on its exact generic refusal does it read power, foreground, then statusbar, stopping at the first failing or unavailable read. It emits a single `STOCK_DIAG1` line. These reads follow the guard; they cannot retroactively identify a cause. `other_native_or_protection_or_state_changed` means the three follow-up predicates currently match, **not** that an installed peer changed. A pass is a diagnostic observation, not authorization to run the study.

Accept an output only for SSH exit 0 and exactly one line with a literal suffix from the probe. Exit 255 takes precedence over any stdout; malformed output, interrupted transport and `guard_protocol_unknown` leave the guard result unknown. Never pass arbitrary stdout/stderr or a raw property through to public output. This probe is optional and must not trigger a retry, wake, idle-timer change, polling or a request for manual input.
