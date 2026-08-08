# v4-rendering PR A — parity fixtures

Prod-snapshot fixtures for the PR A byte-parity contract. Fixture
files land here during the operator's pre-deploy capture step and
are compared against post-deploy captures to verify the pass
criterion documented on the PR:

> Detail endpoint diff shows exactly `score_display` +
> `title_home` + `title_away` (+ `_kickoff_dt` conditionally) and
> nothing else; `/api/events` diff shows nothing.

## Ticker slots

Three fixtures, one per sport family. Slots reserved with the
tickers the operator picked from today's live slate (2026-08-08):

| Slot | Ticker                                    | Sport    | Provenance                                                                             |
| :--- | :---------------------------------------- | :------- | :------------------------------------------------------------------------------------- |
| SOC  | `KXEFLCUPGAME-26AUG08BROREA`              | Soccer   | EFL Cup, mid-match (60'). Cohort-adjacent series family; ticker appears in daily_diff's `agree_partial_coverage` bucket — exercises the linkage path end-to-end. |
| TEN  | `KXATPCHALLENGERMATCH-26AUG08GENPIR`      | Tennis   | Set 2 with `set_history` + `games` populated. Exercises the full tennis sub-object (`row1/row2`, `server`, `set_history`, `flip` gate). |
| MLB  | _TBD — MLB slate goes live ~17:15Z_       | Baseball | Placeholder; operator fills in when the afternoon slate opens.                          |

## File layout

Each fixture is a JSON envelope with the served responses from
both endpoints under study:

```
{
  "ticker": "<Kalshi event_ticker>",
  "sport":  "<Baseball|Soccer|Tennis>",
  "captured_at_utc": "<ISO-8601, e.g. 2026-08-08T17:22:00Z>",
  "prod_host":       "<host used for capture>",
  "phase":  "pre" | "post",
  "detail_record":  { ... raw /api/event/{ticker} response ... },
  "events_record":  { ... this ticker's record from /api/events ... }
}
```

Naming convention: `<SLOT>_<ticker>_<phase>.json`, e.g.
`SOC_KXEFLCUPGAME-26AUG08BROREA_pre.json`. Pre + post pair up on
the shared `<SLOT>_<ticker>_` prefix.

## Consumption

**Manual (this PR — the actual merge gate):** operator diffs
`pre` vs `post` per the PowerShell capture protocol in the PR
body and applies the pass criterion.

**Automated (deferred to a follow-up):** the skipped test
`tests/test_v4_rendering_pr_a.py::test_byte_parity_against_prod_snapshot`
will replay `build_fl_live_state` against a captured `g` dict
and assert the produced `_live_state` byte-matches the fixture's
`detail_record._live_state`. Blocked on landing the
`LOG_LIVE_STATE_G_DUMP` env-gated log-intercept path that
captures `g` alongside the served response — until that lands,
the served response alone doesn't carry enough information to
replay in isolation.

## Do NOT commit `pre`/`post` capture files here directly

The placeholder files in this directory hold the ticker slots +
expected-key set only — no captured response data. Operator's
capture files (`pre_SOC.json`, `post_SOC.json`, etc.) live
locally in the operator's working directory during the merge
window and are the source-of-truth for the diff step; they are
NOT committed to the repo (they carry live-market data with
timestamped state that has no long-term value once the merge is
verified).
