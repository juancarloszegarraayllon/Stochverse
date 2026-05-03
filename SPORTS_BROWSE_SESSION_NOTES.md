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

In rough priority order:

1. **Live clocks for live games** — currently shows half/period only.
   Per-event ESPN-routed clock for the 3 sports above; FL clock for
   the rest. Refresh on a 5-10s cadence (NOT WS — too chatty).
2. **Country flag emojis** in COL 1 country headers and COL 2
   tournament rows.
3. **Mobile single-column treatment** — currently the 3-column grid
   blows out on phone widths. Needs a media query to stack vertically
   with a tab switcher between columns.
4. **Inline COL 3 stats panel** — currently a placeholder. Should
   render the same lineups / standings / H2H / form panels the
   homepage event-card uses, but inlined as a third pane.
5. **CSS for `.sp-c2-mkt-row` and `.sp-c2-mkt-label`** — the
   sub-market layout works but the label could use ellipsis
   truncation and a slightly larger font. Currently relies on
   inherited styles; should be made explicit.
6. **Kalshi-only sport rendering** — Lacrosse / Chess / Squash /
   WSOP / SailGP need a Kalshi-only card variant (no FL stats panel)
   served from `/api/sports/kalshi-only-feed?sport=…`. Endpoint
   exists; frontend rendering is stub.
7. **Cross-platform price comparison** — once Polymarket scoping
   lands, render a second chip row per outcome with the Polymarket
   side-by-side. Architecture already supports stacking rows in
   `.sp-c2-marketcell`.
8. **Kalshi audit** — parallel to the FL audit. Probe what Kalshi
   API surfaces we use vs what's available, similar to
   `fl_probe/coverage.py`. Output: `KALSHI_API_COVERAGE.md`.

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
