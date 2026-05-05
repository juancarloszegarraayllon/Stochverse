# /sports browse — multi-day session notes

This document is a take-home checkpoint for the `/sports` browse page
work that began with the FL OpenAPI audit and ended with the Sofascore-
style 3-column page now live at `https://stochverse.com/sports`.

It exists so anyone (including future-me) can pick the project back up
without re-reading 100k tokens of conversation.

## Where we landed

Page lives at `static/sports.html`, served by `main.py` at `/sports`
and `/sports/{sport_id}`. Frontend bundle version is **0.7.3**.

Layout (desktop):
```
┌─────────────────────── top sport-nav strip ───────────────────────┐
│  Soccer · Tennis · Basketball · Hockey · Football · …             │
│  (text labels, green underline on active, drag-scroll arrows)     │
├──────────────┬─────────────────────────┬──────────────────────────┤
│ COL 1        │ COL 2                   │ COL 3                    │
│ leagues      │ events feed             │ detail panel             │
│ sidebar      │ (game / market view)    │ (placeholder for now)    │
│ grouped by   │ market-type sub-tabs    │                          │
│ country      │ Kalshi PROB/YES/NO chips│                          │
│              │ flash on price moves    │                          │
└──────────────┴─────────────────────────┴──────────────────────────┘
```

Splitters between columns drag-resize, with widths persisted to
`localStorage[sv:sports:cols]`. Game/Market view toggle persisted at
`sv:sports:view`.

## Work completed (chronological)

### Phase 1 — FL OpenAPI audit
- Fetched `fl-openapi.json` and built `fl_probe/coverage.py`, which
  generates `FL_API_COVERAGE.md` listing every FL endpoint and whether
  Stochverse uses it. **53/69 endpoints covered.**
- Probe v4 fixed a misclassification: `/v1/events/player-stats` was
  marked dead based on 0/40 random events, but actually returns 335KB
  on canonical IDs (e.g. `Sbld5SC5`). §3 of
  `DETAILED_EVENT_STATS_SCHEMA.md` rewritten to reflect this.

### Phase 2 — Kalshi vs FL diff
- `kalshi_probe/sports_diff.py` confirmed FL is dramatically more
  comprehensive (40+ sports vs Kalshi's 19).
- Identified 5 Kalshi-only sports we must NOT drop from /sports:
  **Lacrosse, Chess, Squash, WSOP, SailGP**.

### Phase 3 — `/sports` page build
- 3-column Sofascore-style layout in `static/sports.html`.
- Top sport-nav strip with text labels (no emojis — user feedback),
  scroll arrow buttons + edge fades for overflow.
- COL 1 leagues sidebar grouped by country, collapsible.
- COL 2 events feed with game/market view toggle, market-type
  sub-tabs (Winner / Spreads / Totals / etc.), All-events vs On-Kalshi
  filter pills.
- 12 thin `/api/fl/*` pass-through wrappers for FL endpoints.
- `/api/sports/{sport_id}/feed` aggregates FL events by tournament
  with Kalshi prediction-market overlay.

### Phase 4 — Kalshi matching
- `_market_type_from_title(title)`: extract market type from text
  after final colon in Kalshi title.
- `_kalshi_title_corroborates_fl_game()`: defensive whole-word token
  check requiring **all** ≥3-char tokens of FL home_name AND away_name
  to appear as whole words (`\b…\b` regex) in the Kalshi title. Fixes
  the "65 markets falsely attached to one event" bug from the substring
  approach.
- `_build_kalshi_index_for_sport()`: walks Kalshi cache, builds
  `{fl_event_id: [kalshi_records]}` via `match_game()` + corroboration.
- `_extract_winner_prices()`: home/away/tie prob/yes_ask/no_ask/ticker
  via fuzzy label-token matching to team names.
- `_extract_all_outcomes()` (newest): generic outcome extractor
  returning `[{label, prob, yes, no, ticker}, …]` for non-Winner
  sub-markets (Spreads, Totals, player props).

### Phase 5 — Live prices via WebSocket
- Wired `/ws/prices` (the existing homepage stream) into /sports.
- `applyPriceUpdate(ticker, data)` updates chip text and applies
  green/red flash on price moves. Same pattern as homepage cards.
- Subscription happens after every render — gathers all
  `[data-ticker]` rows and calls `PriceWS.subscribe(tickers)`.

### Phase 6 — Polish (this session)
- **Decimal-precision fix:** added `_to_cents(v)` helper in `main.py`
  that does `int(round(float(v)))`. Applied to every direct read of
  `_yb` / `_ya` / `_na` in both `_extract_all_outcomes` and
  `_extract_winner_prices`. Frontend `applyPriceUpdate` now also
  coerces incoming WS payloads with `Math.round(Number(v))` before
  rendering. Eliminates the `56.000000000000001¢` artefacts.
- **Removed sticky top PROB/YES/NO header** (`.sp-c2-header`),
  replaced with a per-event `.sp-c2-evhead` mini-header inside each
  event's `.sp-c2-marketcell`. Matches the homepage prediction-card
  style where every card carries its own column labels.
- **Multi-outcome rendering** scaffold in place: `renderEventRow`
  branches between Winner-compact (home/away/tie aligned to score
  rows) and sub-market-expanded (one row per outcome with label
  inline, via `.sp-c2-mkt-row` wrapper carrying its own data-ticker).
- WS subscription now finds tickers on either `.sp-c2-price-row` OR
  `.sp-c2-mkt-row`, so sub-market outcomes also receive live updates.

### Phase 7 — Day 2 (versions 0.7.10 → 0.7.40)

Big themes today: getting layout parity with the homepage card,
broadening the data sources surfaced, and fixing a long chain of
bugs in the aggregate / series enrichment that culminated in
discovering the cache record never actually carries `_live_state`.

**Layout parity (per-event cards):**
- Outcome labels moved out of the marketcell into the teams cell.
  Marketcell now carries chip rows only, aligned 1:1 with the
  outcome labels on the left.
- Per-tournament column header strip ("Score / Kalshi (PROB / YES /
  NO)") replaces the global sticky header. Sits below the league
  name, hidden when no event in the tournament has a Kalshi pair.
- Matchup header line (`Bayern 0 - 2 PSG`) appears in the teams
  cell when outcomes don't already include team names (Spreads /
  Totals / BTTS / etc.). Score column stays empty for those rows
  to avoid duplication. Detection is shape-based:
  `isWinnerShapedOutcomes` checks if every outcome label EQUALS
  home/away/tie — covers Winner, First Half Winner, Set Winner,
  etc., without a per-market-type whitelist.
- Per-event tab strip (Winner / Spreads / Totals / etc.) replaces
  the global market-type tab. State per event ID in
  `_eventActiveMarketType`. Each tab strip is filtered to the
  homepage's `_SIBLING_SUFFIXES` whitelist (card-class markets
  only; player props go to the Markets-view rows). Cap of 5 visible
  tabs with a "+ N More" / "Less" toggle when an event carries
  more than that.
- K + +N indicator badges removed (redundant with the Kalshi column
  header and per-event tabs). Grid trimmed from 5 cols to 4.

**Tennis scoring:**
- Tennis sport gets a wider score column (`--sp-score-w: 120px`).
- Per-row breakdown: sets-won, server-dot (●), one box per
  completed set + the in-progress set, current point. Matches the
  homepage tennis-row format exactly. Set history grows naturally
  via flex layout — 1 box during set 1, up to 5 in a 5-setter.

**Live clocks:**
- Soccer: `1H 32:15` / `2H 45+3:27` / `ET 95:08` / `HT`. Computed
  from `STAGE_START_TIME` + period offsets. Counts UP.
- Hockey: `P{period} M:SS`. Counts down.
- Basketball / American Football: `Q{period} M:SS`. Counts down.
- 500ms tick (matches homepage), 15-second drift cap so a stale
  capture can't run the clock to zero.
- Period offsets ported verbatim from homepage's
  `computeSoccerMinute` (`SOCCER_PERIOD_OFFSETS`).
- ESPN `display_clock` + `captured_at_ms` enriched into ev._live_state
  on the backend for non-soccer (in-memory match_game, no HTTP).

**Day picker / calendar:**
- Sofascore-style horizontal trio: `‹ Today ›`. Center label opens
  a calendar modal (month grid, navigation arrows, TODAY button).
  Esc / × / backdrop click close.
- Range: ±60 days. FL's events-list only accepts ±7, so beyond
  that we skip the FL call entirely and serve Kalshi-only data
  (Outrights + unpaired h2h). Lets users find World Cup / UCL Final
  / NBA Finals etc. months ahead — Kalshi opens markets long
  before FL ships fixtures.
- Date picks auto-flip the state filter: past → 'finished',
  today → 'live', future → 'upcoming'. Skipped if user explicitly
  picked 'all'.
- Pill counts (`X events / Y on Kalshi`) are state-filter-aware
  but ignore the `onlyMatched` toggle (since that toggle IS the
  Kalshi pill — its number shouldn't change when clicked).

**Continent buckets in COL 1:**
- New `bucketCountry()` helper. Falls through:
  `COUNTRY_NAME` → `NAME_PART_1` → NAME prefix before `:` →
  keyword regex on full NAME (`UEFA`/`UCL`/`Europa` → Europe;
  `CONMEBOL`/`Libertadores` → South America; `CONCACAF` → North &
  Central America; `AFC`/`AFCON` → Asia; `CAF` → Africa;
  `FIFA`/`World Cup` → World) → `International`. Title-cased.
- Outright tournaments are special-cased to the top of the country
  list (instead of mid-alphabet at "O").

**Outrights / unpaired Kalshi pipelines:**
- `_collect_outrights_for_sport()`: surfaces tournament/season
  futures (Champions League Winner, World Cup Winner, MVP, Heisman
  Trophy, etc.) as a synthetic "Outrights" tournament group.
  Filters by:
  - title doesn't match the universal head-to-head regex
    (`_HEAD_TO_HEAD_TITLE_RE` — covers `vs / v. / v / @ / at`
    across all sports).
  - ≥`_OUTRIGHT_MIN_OUTCOMES` outcomes (3) — real outrights have
    many candidates.
  - Expiration filter: hidden once `_exp_dt` is in the past
    relative to the picked date.
- `_collect_unpaired_h2h_for_sport()`: surfaces Kalshi h2h markets
  Kalshi has open but FL doesn't have today (e.g. tomorrow's UCL
  match). Two-pass:
  - Pass 1: bucket records by (`_series_base`, sorted matchup
    name) so all sub-markets for the same fixture group together
    regardless of team-order ordering.
  - Pass 2: filter date (ticker date must equal target date),
    drop FL-paired matchups, build event row carrying ALL the
    matchup's markets in `markets[]`.
  - `_cup_priority` sort runs first so cup matches (UCL, UEL,
    Libertadores, FA Cup, etc.) survive the `MAX_UNPAIRED=500`
    cap before regular leagues.
- League-name → region map (`_LEAGUE_TO_REGION`) routes synthetic
  tournaments under the correct continent bucket in COL 1.

**Aggregate / series enrichment — the chase:**

This was the longest debug of the day. The arc:
1. Soccer aggregate (`AGG 4-5 LEG 2/2`) wasn't showing on /sports
   while the homepage card showed it for the same game.
2. Tried reading `primary.aggregate_home` — always None.
3. "Fixed" by reading `primary._live_state.aggregate_home` (where
   the homepage's `renderSeriesPill` reads from). Still None.
4. Added the `/api/_debug/event_record?q=<substring>` endpoint to
   inspect the cache directly. Output revealed: `_live_state_keys: []`
   on EVERY cache record. `_live_state` isn't even a top-level key.

**Real cause:** the homepage's `/api/events` handler enriches
`_live_state` PER REQUEST via `match_game` + `_enrich_soccer_aggregate`
(main.py:2407-2429). The cache itself never stores it. /sports was
reading a field that doesn't exist.

**Real fix:** new `_enrich_record_live_state(title, sport)` helper
that mirrors the /api/events enrichment chain. Called per matched
event in /sports. Returns a synthetic `_live_state` with
`aggregate_home/away`, `series_home_wins/away_wins`, `display_clock`,
`period`, etc. In-memory match_game, no HTTP cost; SofaScore is
5-min cached for soccer.

Also extended for the unpaired h2h pipeline. For both paths, the
title passed to enrichment is the **headline** record's title
(`primary.title`, e.g. `'Bayern Munich vs PSG'` from KXUCLGAME),
not the bundle's `bare_title` (which picks up the first cache
record's title, often a sub-market like `'PSG at Bayern Munich:
Totals'` that SofaScore doesn't index by).

**Universal across sports:**
- Soccer (cup ties): SofaScore via `_enrich_soccer_aggregate` →
  `aggregate_home/away`, `leg_number`, `round_name`. AGG pill.
- NBA / NHL / MLB / NFL playoffs: ESPN match_game →
  `series_home_wins/away_wins`, `series_summary`. SERIES pill.
- Renders adapt: `renderAggOrSeries(ev)` picks aggregate (if
  populated) → SERIES (if populated) → nothing.
- Pill is a green-on-green pill matching homepage's `.agg-pill`,
  not muted grey text.

**Team crests / national flags:**
- FL's events-list response already ships `HOME_IMAGES` /
  `AWAY_IMAGES` arrays of CDN URLs — same source the homepage's
  H2H pane uses. National-team fixtures get country flags, club
  fixtures get crests, FL handles the distinction.
- Renders inline next to team names: in matchup headers, in
  Winner-shape outcome labels (matched against HOME_NAME /
  AWAY_NAME), and in the unmatched fallback team rows.
- 14×14 lazy-loaded `<img>`. No backend change — passthrough only.
- Game events only — outright tournaments and unpaired Kalshi h2h
  don't carry `HOME_IMAGES` (no FL pair), so they render text-only.

**Title parsing for NHL/NBA-shape Kalshi titles:**
- Kalshi has two title shapes:
  - Soccer: `Bayern vs PSG: Spreads` (matchup BEFORE colon)
  - NHL/NBA: `NHL Game: Tampa Bay vs Montreal Canadiens` (matchup
    AFTER colon, league prefix BEFORE)
- `_market_type_from_title` now disambiguates: if the suffix after
  the last colon matches the head-to-head regex, it's the matchup,
  not a sub-market. Returns `''` for headline.
- New `_bare_matchup_from_title` walks colon-split parts and
  returns the first one that matches h2h shape.
- Smoke-tested 6 cases across both shapes.

**Playoff-series duplicate fix (last fix of the day):**
- `_build_kalshi_index_for_sport` was over-attaching: a single
  Cleveland-Toronto FL fixture got paired with `KXNBAGAME-
  26MAY01CLETOR`, `26MAY03CLETOR`, `26MAY05CLETOR`, etc. (every
  game in the playoff series — `match_game` only checks team
  names). Markets view rendered the same matchup 3-5×.
- Added a date sanity check: drop records whose ticker date
  doesn't match the FL event's start time (UTC, ±1 day fuzz for
  timezone drift between Kalshi/ET and FL/UTC).

**Outright "expiration" filter:**
- Outrights stay visible across the calendar window UNTIL their
  Kalshi market expires. Source field is `_exp_dt` on the cache
  record (resolved `expected_expiration_time`). So 'World Cup
  Winner 2026' shows from now through the tournament's settlement
  day, then drops. Avoids hiding World Cup Winner just because
  user picked a date outside today.

**Sentry / observability:**
- Wrapped initial WebSocket hello send to suppress
  WebSocketDisconnect noise from clients that disconnect during
  handshake. Was flooding Sentry; now silently cleans up and
  returns.

## Important conventions / gotchas

### Cache field names — the `_yb / _ya / _nb / _na` rule
The Kalshi cache stores prices in **integer cents** under
**underscore-prefixed keys**:
- `_yb` = yes_bid (probability — 1¢ = 1%)
- `_ya` = yes_ask (what to PAY to buy YES — green chip)
- `_nb` = no_bid
- `_na` = no_ask  (what to PAY to buy NO — red chip)

Earlier code that read `o.get("yes_bid")` returned None on every
record. The fallback path through `_coerce_cents()` handles the rare
raw-passthrough case where keys are `yes_bid` / `yes_bid_dollars`.

### Bundle cache-busting
`build.mjs` reads the version from `src/main.ts` (`version: '0.7.3'`)
and rewrites `static/index.html`'s `?v=…` query in-place. **Never
hardcode the version in `index.html` manually** — it'll get clobbered
on the next build.

### Per-sport clock routing
`_ESPN_CLOCK_SPORTS = {"Basketball", "Hockey", "Football"}` use ESPN
for live clocks; everything else uses FL. Keep this in mind when
adding live-clock work to /sports — currently the page just shows
the half/period state, not a per-second running clock.

### Capability injection order
`_augmentEventCapabilities()` had a bug where match-tab injection ran
AFTER standings early-returns, so events with no new standings to
merge silently lost their Player Stats / Predicted tabs. Fix:
`_injectMatchCapabilityTabs()` is extracted and runs unconditionally
BEFORE standings logic. Don't reintroduce the early-return pattern.

### Whole-word matching for Kalshi corroboration
Substring matching causes "Brussels Basketball" to match anything
with the token "basketball" in it (we lost a whole afternoon to this).
The corroboration check uses `re.search(r'\b' + tok + r'\b', title)`
and requires **all** ≥3-char tokens of BOTH home and away to appear.
If you change this, run `kalshi_probe/sports_diff.py` and eyeball a
few sports for false positives before shipping.

## Pending work / wishlist

Done in Phase 7 (today): live clocks (1), country flags + team
crests via FL HOME_IMAGES (2). Outrights / unpaired pipeline,
calendar picker, day-shift filtering, NBA/NHL series enrichment,
aggregate fix all landed. Pending list trimmed accordingly:

In rough priority order:

1. **Inline COL 3 stats panel** — still a placeholder. Should render
   the same lineups / standings / H2H / form panels the homepage
   event-card uses, but inlined as a third pane. Highest user-visible
   gap left in /sports.
2. **Mobile single-column treatment** — the 3-column grid blows out
   on phone widths. Needs a media query to stack vertically with a
   tab switcher between columns.
3. **Kalshi-only sport rendering** — Lacrosse / Chess / Squash /
   WSOP / SailGP need a Kalshi-only card variant (no FL stats panel)
   served from `/api/sports/kalshi-only-feed?sport=…`. Endpoint
   exists; frontend rendering is stub.
4. **Cross-platform price comparison** — once Polymarket scoping
   lands, render a second chip row per outcome with the Polymarket
   side-by-side. Architecture already supports stacking rows in
   `.sp-c2-marketcell`.
5. **CSS for `.sp-c2-mkt-row` and `.sp-c2-mkt-label`** — the
   sub-market layout works but the label could use ellipsis
   truncation and a slightly larger font. Currently relies on
   inherited styles; should be made explicit.
6. **Kalshi audit** — parallel to the FL audit. Probe what Kalshi
   API surfaces we use vs what's available, similar to
   `fl_probe/coverage.py`. Output: `KALSHI_API_COVERAGE.md`.
7. **Pre-launch Sentry hygiene** — see Phase 7 notes; bump to Team
   plan if usage exceeds free 5k errors/mo, add `beforeSend` hook
   to drop transient I/O exceptions, source maps, release tagging.

## Key files

| File | Purpose |
|------|---------|
| `static/sports.html` | The page itself — ~640 lines, standalone |
| `main.py` | `/api/sports/*`, `_extract_*`, `_to_cents`, matching |
| `src/main.ts` | Bundle version (currently 0.7.3) |
| `static/dist/main.js` | esbuild output (do NOT edit by hand) |
| `static/index.html` | Homepage; `?v=` query auto-synced by build |
| `build.mjs` | Bundle build + cache-buster sync |
| `fl_probe/coverage.py` | FL API audit → FL_API_COVERAGE.md |
| `kalshi_probe/sports_diff.py` | Kalshi vs FL sports universe diff |
| `DETAILED_EVENT_STATS_SCHEMA.md` | Master FL endpoint schema (446 lines) |
| `fl-openapi.json` | Committed FL OpenAPI spec |

## Quick local verification

```bash
# Build bundle (auto-syncs cache-buster):
node build.mjs

# Run server:
uvicorn main:app --reload

# Smoke-test the feed endpoint:
curl -s 'http://localhost:8000/api/sports/1/feed' | jq '.tournaments[0].events[0].kalshi.primary_prices'

# Diff Kalshi vs FL sports (requires server running):
python3 kalshi_probe/sports_diff.py --base http://localhost:8000

# Check WS subscriptions in the browser console on /sports:
document.querySelectorAll('.sp-c2-price-row[data-ticker], .sp-c2-mkt-row[data-ticker]').length
```

## Known cosmetic issues (low priority)

- Browser console shows
  `Error: A listener indicated an asynchronous response by returning
  true, but the message channel closed before a response was received`
  — these are Chrome extension errors, not from our code. Ignore.
- WS flashes are rare on illiquid markets — tested OK on actively-
  traded events; the 330-ticker subscription confirms WS is wired.

---

## Phase 8 — Kalshi audit + /sports v2 (Day 2)

**TL;DR:** /sports v1 had recurring duplication / missing-team /
wrong-date bugs from fuzzy team-name matching. Paused v1 fixes,
did a complete Kalshi audit, then started building v2 from scratch
on a deterministic identity-based join. Phases 1–4 of 7 done.
~1,200 lines of new code, 195 passing tests, **zero changes to
live /sports yet** — all new code sits alongside until phase 5
swaps the handler.

### Strategic pivot

Started Day 2 trying to fix /sports v1 duplicates (Copa Libertadores
showing twice, Bayern-PSG duplication, "Failed to load events"
errors, Aston Villa missing team). Shipped multiple "universal"
fixes (commits `091616a`, `a985a95`, `8232b90`, `61c4301`) and still
hit issues with new team-name patterns. Called it: **stop patching
v1**. Kalshi data is the source of truth — audit it completely,
then rebuild /sports v2 against a deterministic contract.

### Kalshi audit complete (KALSHI_AUDIT.md)

Single comprehensive doc, ~900 lines. Sections:

1. **Cache record schema** — universal fields, `_live_state` caveat
2. **§1.5 Schema dictionary** — every Kalshi data object from
   `kalshi-openapi.json`: Event, Market, Series, Trade, Settlement,
   MarketCandlestick, Order, Fill, Position, Milestone, etc.
   165 schemas extracted with field types, enums, descriptions.
3. **§1.6 WebSocket protocol** — full subscribe/unsubscribe protocol,
   heartbeat (10s ping/pong), all 22 server error codes, payload
   schemas for all 11 channels (`ticker`, `orderbook_delta`, `trade`,
   `market_lifecycle_v2`, `fill`, `user_orders`, `market_positions`,
   `multivariate_market_lifecycle`, `communications`,
   `order_group_updates`, `multivariate`)
4. **Series tickers per sport** — suffix taxonomy
5. **Title shapes** — vs/at orientation rule
6. **§4 Outcome shapes** — per (sport × suffix × market_type) for
   all 14 sport-tagged buckets
7. **§5 Ticker grammar** — six patterns lock down fixture identity:
   - G1: `{base}-{YYMMDD}{abbrs}` (most sports, no time)
   - G7: `{base}-{YYMMDD}{HHMM}{abbrs}` (MLB, esports, AFL,
     intl basketball/hockey — time disambiguates doubleheaders)
   - G_LEG: G1/G7 + `-{N}` (tennis sets, esports maps)
   - G_SERIES: `{YY}{abbrs}R{N}` (NBA/NHL playoff series)
   - G_TOURNAMENT_HANDLE: `{HANDLE}{YY}` (Golf, NASCAR)
   - G_YEAR / G_YEAR_HANDLE: outright variations
8. **§7 Merge contract** — deterministic identity tuples + pairing
   rule (replaces match_game + corroboration + second-pass + series
   routing with one equality check)
9. **§9 Cross-source field mapping** — per-field provenance for
   FL / ESPN / SportsDB / SofaScore + canonical priority per sport

Reference snapshots in `kalshi_probe/snapshots/`:
- `sports_inventory.json`
- `outcome_shapes_<sport>.json` × 14 sports
- `ticker_grammar_<sport>.json` × 14 sports

These lock the audit against real production data — every parser
and shape rule is verified against them in tests.

**14 of 20 sports inventoried** in cache (the other 6 are
non-sports markets — politics/weather/novelty — out of /sports scope).

### Deferred work (logged in audit doc)

- Unclassified records audit (3,377 politics/weather/novelty records)
- Cache builder classifier audit (OWGRRANK was misclassified as
  Esports; may be more)
- Edge-case ticker patterns (Mainz 05 `M05UNI`, CHOCHO2 rematch)
- Multivariate event collections rendering rules

### /sports v2 implementation plan (SPORTS_V2_PLAN.md)

Reviewable plan doc, ~360 lines. Approved with two tweaks:
- pytest in phase 1 (not deferred)
- Phase 5 promotion exit criteria: time + zero P1 bugs +
  pairing-rate parity + extension protocol (24h then redesign-flag,
  not silent stretch); 7-day window for weekly-rhythm sports (NFL,
  NCAAF, UFC)

Architecture: 4 new modules + 2 file modifications. Net diff
estimate: **−630 lines** across all files (delete more than add).

```
kalshi_identity.py        phase 1   parse Kalshi tickers → Identity
outcome_shapes.py         phase 2   per-(sport, suffix, mt) shape rules
kalshi_join.py            phase 3   identity-based FL ↔ Kalshi join
live_source_selector.py   phase 4   per-sport canonical source dispatch
```

### Phase 1 — kalshi_identity.py ✅

`d6f7e59` — 325 lines + 58 tests. Parses every Kalshi event_ticker
into a hashable Identity tuple, computes FL-side identity,
deterministic match() function. **100% snapshot-parse rate**
(236/236 tickers). Snapshot tests caught one missing pattern
(G_YEAR_HANDLE) and forced bidirectional suffix-stripping.

### Phase 2 — outcome_shapes.py ✅

`7528c78` — 340 lines + 31 tests. Per-(sport, suffix, market_type)
rule table sourced from KALSHI_AUDIT.md §4. 80+ rules covering
14 sports. `render_outcomes()` normalizes pricing across all three
field formats (`_yb` compact, `yes_bid` int, `yes_bid_dollars`
string). Snapshot sweep verifies every observed bucket has a rule
and outcome counts validate.

### Phase 3 — kalshi_join.py + diff endpoint ✅

`a01d113` — 210 lines + 24 tests + `/api/_debug/sports_join_diff`
endpoint. Identity-based join replaces the current
`_build_kalshi_index_for_sport` + `_kalshi_title_corroborates_fl_game`
+ second-pass attach + series-routing pipeline.

The diff endpoint is the **gate** for phase 5 promotion:

```
https://stochverse.com/api/_debug/sports_join_diff?sport_id=1&indent_days=0
```

Returns `{v1, v2, diff: {pairing_rate_v1_pct, pairing_rate_v2_pct,
v2_only_pairings, v1_only_pairings, identical_pairings_count}}`.
Promotion requires `v1_only_pairings == 0` and `pairing_rate_v2 >=
pairing_rate_v1`.

### Phase 4 — live_source_selector.py ✅

`a3b868d` — 190 lines + 39 tests. Replaces
`_enrich_record_live_state()`'s ~150-line nested fallback with three
composable functions: `select_live_source()`, `is_cup_series()`,
`overlay_soccer_aggregate()`, all wrapped by `enrich_for_record()`.

Per-sport priority chain locked per audit §9. Source callers are
injectable for testing — all 39 tests run with zero real-feed I/O.

Bracket-cache and basketball-series-cache fallbacks deferred to
phase 5's sports_feed_v2() handler — they need access to main.py's
module-level _cache state.

### Test suite

195 tests passing, 0 regressions. Run:

```bash
python3 -m pytest
```

Breakdown: `test_kalshi_identity.py` 58 / `test_outcome_shapes.py` 31
/ `test_kalshi_join.py` 24 / `test_live_source_selector.py` 39
/ existing 43.

### Resume tomorrow at phase 5

**Phase 5 deliverable:** `sports_feed_v2()` handler at
`/api/sports/{id}/feed?v=2`, behind `?v2=1` frontend flag.

This is the integration phase that wires phases 1–4 together:

```python
# Pseudocode for the handler
@app.get("/api/sports/{sport_id}/feed")
async def sports_feed(sport_id: int, ..., v: int = 1):
    if v == 2:
        return await sports_feed_v2(...)
    return await sports_feed_v1(...)  # existing handler

async def sports_feed_v2(sport_id, indent_days, timezone):
    # 1. Resolve sport
    sport = _KALSHI_SPORT_BY_FL_ID[sport_id]
    # 2. Fetch FL events list
    fl_data = await _fl_get("/v1/events/list", ...)
    fl_events = flatten(fl_data)
    # 3. Identity-based join
    cache_records = _cache.get("data_all") or []
    idx = build_kalshi_index(cache_records, sport)
    pairings, unpaired = join_with_fl(fl_events, idx, sport)
    buckets = find_unpaired_buckets(unpaired, sport)
    # 4. Per-pairing live-state enrichment
    for p in pairings:
        live = enrich_for_record(primary.title, sport, primary)
        # + bracket-cache fallback (cache-coupled, lives here)
        # + basketball-series fallback (cache-coupled, lives here)
    # 5. Render outcomes per shape rules
    for r in p.kalshi_records:
        outs = outcomes_with_shape(r, sport, suffix, market_type)
    # 6. Series-routing for unpaired buckets (route to FL bucket if
    #    series_base has been seen paired before)
    return {tournaments: out_tournaments + unpaired_buckets, ...}
```

Estimated effort: **~1 day**. Then phases 6–7 (promote + delete v1).

### Phase 5 verification plan

Before flipping the frontend default:
1. Hit `/api/_debug/sports_join_diff` for Soccer / Basketball / Hockey
   on today + tomorrow + Wed/Thu. Verify `v1_only_pairings == 0`.
2. Hit `/api/sports/1/feed?v=2` and `/api/sports/1/feed?v=1`
   side-by-side. Same tournaments? Same fixtures per tournament?
   Same outcomes per fixture?
3. Frontend opt-in: `/sports?v2=1` — manual QA across the 10-sport
   matrix (Soccer / NBA / NHL / MLB / Tennis / MMA / Esports / Golf
   / Cricket / Lacrosse).
4. Run prod traffic on `?v=2` for ≥3 days (≥7 for NFL / NCAAF / UFC
   weekly-rhythm sports). All 4 promotion criteria must hold:
   - Zero P1 bugs
   - `pairing_rate_v2 >= pairing_rate_v1`
   - `null-clock`, `null-aggregate`, `null-outcome` events same or fewer
   - `parse_failure_rate <= 0.5%`

### Files to keep handy when resuming

| File | What's in it |
|---|---|
| `KALSHI_AUDIT.md` | The full audit — answers "what does Kalshi ship?" |
| `SPORTS_V2_PLAN.md` | Implementation plan with promotion criteria |
| `kalshi_identity.py` | Phase 1: ticker → Identity |
| `outcome_shapes.py` | Phase 2: outcome shape rules |
| `kalshi_join.py` | Phase 3: identity-based join |
| `live_source_selector.py` | Phase 4: source dispatch |
| `tests/test_*.py` | All passing — `python3 -m pytest` |
| `kalshi-openapi.json` | Kalshi REST spec (committed) |
| `kalshi-websockets.json` | Kalshi WS spec (committed) |
| `kalshi_probe/snapshots/` | Reference data for every sport |

### Recent commit log (Day 2)

```
a3b868d  sports v2 phase 4: live_source_selector.py — canonical source dispatch
a01d113  sports v2 phase 3: kalshi_join.py + /api/_debug/sports_join_diff endpoint
7528c78  sports v2 phase 2: outcome_shapes.py — deterministic shape rules
d6f7e59  sports v2 phase 1: kalshi_identity.py — deterministic ticker parsing
0a28656  sports v2: implementation plan against KALSHI_AUDIT.md
fc6527c  kalshi audit: §1.6 WebSocket protocol from kalshi-websockets.json
9d1ae49  kalshi audit: complete §1.5 schema dictionary from kalshi-openapi.json
e4958c8  kalshi audit: §9 cross-source field mapping (FL/ESPN/SportsDB/SofaScore)
e0f3a74  kalshi audit: Esports section + multi-map structure (G_LEG generalized)
d695515  kalshi audit: MMA section — most granular sub-market structure observed
db7b7a2  kalshi audit: Tennis added — outcome shapes + ticker grammar
1f05e37  kalshi audit: probe_outcomes + probe_tickers + debug endpoints (steps 5/6)
85fc37e  kalshi audit: /api/_debug/sports_inventory for prioritizing per-sport probes
e2281c4  kalshi audit: KALSHI_AUDIT.md — full audit doc + merge contract (step 7)
61c4301  sports: nickname aliases + cosmetic enrichment for synthetic events
8232b90  sports: series-routing pass — eliminates parallel league buckets
a985a95  sports: universal second-pass attach — fixes ALL fl/kalshi pairing gaps
091616a  sports: prefix-match fallback in corroboration to handle FL truncations
```

### One-line resume

> Continue at SPORTS_V2_PLAN.md phase 5: build `sports_feed_v2()`
> handler that wires `kalshi_identity` + `outcome_shapes` +
> `kalshi_join` + `live_source_selector` into a parallel handler at
> `/api/sports/{id}/feed?v=2`, with frontend `?v2=1` flag.
