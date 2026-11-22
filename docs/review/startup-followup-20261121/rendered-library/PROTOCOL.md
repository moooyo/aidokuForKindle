# Render-only stock card pilot protocol (log-only)

This protocol defines one stock entry visit measured from card release to the first
in-visit Library render completion signal. It is log-only evidence.

## Scope

- One visit consumes exactly one case id.
- Host runs exactly three fixed visits: `control-01`, `control-02`, `control-03`.
- Primary endpoint is from stock tile release bracket to `ARL1 first_match_us`.
- Covers may still be placeholders.
- No framebuffer images, no trace files, no video, and no operator touch probe.

## Input and endpoint contract

- Stock-card dispatch must use the existing guarded stock path (`tile-input` +
  `dispatch.sh` target/tap).
- The logger helper must run as `input/render-watch input/reference.rule` and:
  - emit exactly one `READY` line on stderr before stock tap,
  - emit one `ARL1` line on stdout:
    `ARL1 LAST_NEGATIVE_US FIRST_MATCH_US CONFIRMED_US COVER_FIRST COVER_CONFIRMED`.
    `COVER_FIRST` and `COVER_CONFIRMED` must be `MATCH` or `DIFFER`.
- All endpoint clocks are device `CLOCK_BOOTTIME` microseconds.

## Device operation order

1. `admit LABEL`
2. `prepare LABEL tapcheck`
3. `visit LABEL <case>`
4. `collect LABEL <case>`
5. `close LABEL`
6. `absent LABEL`

## Archive members per visit

- `out/<case>/origin`
- `out/<case>/anchor` (four-value stock tap bracket)
- `out/<case>/pointer-brackets` (raw tap bracket)
- `out/<case>/qualified`
- `out/<case>/rendered-library` (`ARL1 ...`)

## Device replies

- `admit` -> `render_admitted`
- `prepare` -> `render_prepared`
- `visit` -> `render_completed`
- `close` -> `render_closed`
- `absent` -> `render_absent`

`collect` returns a gzip tar stream only.

## Close semantics

- `close` is fail-closed when both stage/state are absent.
- Interrupted visits must run bounded recovery before close success.
- If a visit reached dispatch, `input/dispatch.sh tap` must persist
  `out/<case>/dispatched` with `stock-tap`; parent flow only verifies it.
- `guard capture` must persist `state/guard/ready` with the lease label;
  parent flow only verifies it.
- `prepare` must record a control stat snapshot (`d:i:s:mode:mtime:ctime`)
  plus live mode checks from `control/program-modes`; `visit` only verifies the
  snapshot is unchanged.
- `close` performs the only byte-level control comparison (`cmp`) when the
  snapshot exists; if prepare failed before snapshot creation, close skips this
  compare path and only runs bounded native recovery checks.
- `close` must run `guard restore` + `guard verify` whenever
  `state/guard/ready` exists, even if `state/ready` is absent.
- `close` must restore lights whenever `state/lights.before` exists.
- Close always uses `visit recover <case>` under the held stage lock.
- Missing verified close is blocking and not reinterpreted as success.
