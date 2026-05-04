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
