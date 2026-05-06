# /sports v2 — Implementation Plan

> Designed against `KALSHI_AUDIT.md` §7 (merge contract) + §9 (source
> selection). Reviewable before any code. Adjust this doc, then ship.

## 1. Goal

Rebuild `/sports` so the merge between Kalshi and FL/ESPN/SportsDB/SofaScore
is **deterministic** (identity-tuple equality on parsed tickers) instead of
the current title-parsing + fuzzy-team-matching chain.

**Success criteria (must hold across multiple sport + date combos):**

1. Same fixture appears at most once on a /sports page.
2. Same competition appears at most once in COL 1 (no "Champions League" vs "Champions League - Play Offs" duplicates).
3. Outcomes render correctly per the §4 outcome-shape table — no missing-away-team rows, no shape-guessing failures.
4. Live prices update via WebSocket without polling.
5. Settlement / determination state updates live (using `market_lifecycle_v2`).
6. Net code size: **smaller** than today (delete more than we add).
7. Feature flag rollout: v2 ships next to v1; flip back instantly if anything regresses.

## 2. Architecture overview

Three new modules introduce one clean abstraction each. Two existing files
shrink. Dataflow stays the same; the changes are all internal.

```
                     ┌──────────────────────┐
                     │  kalshi_identity.py  │  parse ticker → Identity tuple
                     │  (NEW)               │  match(k_id, fl_id) → bool
                     └──────────┬───────────┘
                                │ used by
            ┌───────────────────┴───────────────────┐
            │                                       │
┌───────────▼───────────┐                  ┌───────▼────────────────┐
│ kalshi_join.py        │                  │ outcome_shapes.py      │
│ (NEW)                 │                  │ (NEW)                  │
│ - build_index()       │                  │ - render_outcomes()    │
│ - join_with_fl()      │                  │ - shape_for(sport,     │
│ - find_unpaired()     │                  │             suffix,    │
│                       │                  │             market_type)│
└───────────┬───────────┘                  └────────┬───────────────┘
            │                                       │
            └────────────────┬──────────────────────┘
                             │ called by
                    ┌────────▼─────────┐
                    │ main.py          │  /api/sports/{id}/feed (rewritten)
                    │ sports_feed_v2() │  ~80 lines (was ~600)
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────────────┐
                    │ live_source_selector.py  │  select source per §9 rules
                    │ (NEW)                    │  used for clock + score + agg
                    └──────────────────────────┘
```

## 3. New modules — proposed APIs

### `kalshi_identity.py` (~200 lines)

```python
@dataclass(frozen=True)
class Identity:
    """Deterministic fixture / market identity. Hashable; usable as dict key."""
    kind: str          # "per_fixture" | "per_leg" | "series" | "per_team" | "tournament" | "outright"
    sport: str         # "Soccer" | "Basketball" | ... (matches Kalshi _sport)
    series_base: str   # "KXEPL" / "KXNBA" / "KXMLB" / etc. — series after suffix-strip
    date: Optional[date] = None       # YYMMDD for per-fixture/per-leg
    time: Optional[str] = None        # HHMM for G7 sports (MLB, esports, etc.)
    team_set: Optional[frozenset] = None  # for per-fixture / per-leg / series
    leg_n: Optional[int] = None       # for per-leg (set/map/round)
    handle: Optional[str] = None      # for tournament (Golf, NASCAR) / outright handles
    year: Optional[int] = None        # 2-digit or 4-digit year
    round_n: Optional[int] = None     # for series (NBA/NHL playoff round)


def parse_ticker(event_ticker: str, series_ticker: str, sport: str) -> Identity:
    """Parse a Kalshi event_ticker into deterministic Identity.

    Implements every grammar pattern documented in KALSHI_AUDIT.md §5:
    - G1, G7, G_LEG (date / time / abbrs / leg)
    - G_SERIES (year + abbrs + R{N})
    - G_TOURNAMENT_HANDLE (Golf / NASCAR)
    - G3 / G4 (year codes for outrights)
    - G5 (alphabetic handle)
    - Per-team season-wins (KXMLBWINS-{TEAM}-{YY})
    - Sport-specific edge cases (Mainz 05, CHOCHO2, etc.)

    Returns an Identity with kind set; raises only on truly unparseable input.
    """


def compute_fl_identity(fl_event: dict, sport: str, kalshi_sport: str) -> Identity:
    """Build the FL-side Identity from an FL event dict.

    Uses START_TIME → date (UTC), SHORTNAME_HOME / SHORTNAME_AWAY → team_set,
    and (for MLB/intl) START_TIME → time HHMM.
    """


def match(k: Identity, fl: Identity, fuzz_days: int = 1) -> bool:
    """Pairing rule from §7: same kind + same sport + (date ±fuzz)
    + same team_set [+ time within 30 min for G7]."""
```

### `kalshi_join.py` (~150 lines)

```python
def build_kalshi_index(records: list[dict], sport: str) -> dict[Identity, list[dict]]:
    """Walk Kalshi cache records, parse each ticker into Identity,
    group by Identity. Replaces _build_kalshi_index_for_sport()
    AND its sanity-check + date-rejection chain."""


def join_with_fl(fl_events: list[dict], kalshi_idx: dict[Identity, list[dict]],
                 sport: str) -> tuple[list[Pairing], list[dict]]:
    """For each FL event, look up its Identity in the Kalshi index.
    Returns (paired_events, unpaired_kalshi_records). One-call replacement for
    the current main loop + second-pass attach + series routing."""


def find_unpaired_buckets(unpaired: list[dict],
                          observed_routings: dict) -> list[Tournament]:
    """Group truly Kalshi-only h2h records by series_base → tournament.
    Uses the persistent _SERIES_TOURNAMENT_HINTS routing table.
    Replaces _collect_unpaired_h2h_for_sport()."""
```

### `outcome_shapes.py` (~250 lines)

```python
@dataclass
class OutcomeShape:
    """Per-(sport, suffix, market_type) rule from KALSHI_AUDIT.md §4."""
    expected_count: int | tuple[int, int]  # exact or range (min, max)
    label_kind: str       # "team" | "tie" | "yes_no" | "spread" | "total" |
                          # "advance" | "winner_phrase" | "threshold" | etc.
    has_tie: bool         # 3-outcome winner shape vs 2-outcome


# Static rule table built from §4. Keys: (sport, suffix, market_type).
_SHAPE_RULES: dict[tuple[str, str, str], OutcomeShape] = {
    ("Soccer", "GAME", ""): OutcomeShape(3, "team_or_tie", has_tie=True),
    ("Soccer", "TOTAL", "Totals"): OutcomeShape(4, "total", has_tie=False),
    ("Basketball", "GAME", ""): OutcomeShape(2, "team", has_tie=False),
    # ... ~80 entries covering all 14 sports
}


def shape_for(sport: str, suffix: str, market_type: str) -> OutcomeShape | None:
    """Look up the rule for a (sport, suffix, market_type). None = unknown shape;
    caller should fall back to generic outcomes-array rendering."""


def render_outcomes(kalshi_record: dict, sport: str) -> list[dict]:
    """Return the canonical per-outcome list {label, prob, yes, no, ticker}.
    Uses shape_for() to apply the right rule. Reads outcome labels directly
    from outcomes[] (which always carry the right team / player / threshold
    label per audit findings) — no title parsing."""
```

### `live_source_selector.py` (~150 lines)

```python
def select_live_source(sport: str, fl_event: dict | None,
                       ticker_date: date) -> dict | None:
    """Return the canonical live-state dict from the right source per §9.

    Priority (per sport):
        Basketball/Football/Hockey: ESPN → FL → SportsDB → SofaScore
        Soccer:                     FL → ESPN → SportsDB → SofaScore
                                    (+ SofaScore aggregate overlay if soccer cup)
        Tennis/Cricket/Boxing/MMA/etc: FL only

    Replaces _enrich_record_live_state()'s ~150-line nested fallback with
    one deterministic dispatch.
    """


def overlay_aggregate(g: dict, sport: str, series_base: str) -> dict:
    """For soccer cup ties (UCL knockouts, Libertadores, etc.) overlay
    aggregate fields from SofaScore lookup_aggregate_sync — only when g
    is missing aggregate_home/aggregate_away. Bounded by AGG_LOOKUP_CAP."""
```

## 4. Existing files — modifications

### `main.py` — large net deletion

| Function | Action | Lines |
|---|---|---|
| `_build_kalshi_index_for_sport()` | **Delete** — replaced by `kalshi_join.build_kalshi_index()` | ~80 |
| `_kalshi_title_corroborates_fl_game()` | **Delete** — corroboration becomes Identity equality | ~75 |
| `_collect_unpaired_h2h_for_sport()` | **Delete** — replaced by `kalshi_join.find_unpaired_buckets()` | ~200 |
| `_matchup_key()` | **Delete** — Identity replaces it | ~30 |
| `_market_type_from_title()` | **Keep but simplify** — only used as fallback when ticker grammar can't classify | ~40 → ~15 |
| `_bare_matchup_from_title()` | **Delete** — outcome labels carry team names directly | ~50 |
| `_enrich_record_live_state()` | **Replace body** with one call to `live_source_selector.select_live_source()` | ~150 → ~10 |
| `sports_feed()` (the `/api/sports/{id}/feed` handler) | **Rewrite** as `sports_feed_v2()` parallel function | ~600 → ~80 |
| Second-pass attach pass (added in `a985a95`) | **Delete** — Identity-equal join handles this case natively | ~150 |
| Series-routing pass (added in `8232b90`) | **Keep but simplify** — still need persistent hints, but routing is one-line lookup | ~130 → ~50 |

**Net main.py change**: `−1505 lines deleted, +200 lines added = net −1305 lines.`

### `static/sports.html` — frontend renderer simplification

| Function | Action | Lines |
|---|---|---|
| `collectOutcomesForRender()` | **Replace** with new `outcomesFromRecord()` that reads outcomes directly + uses shape table | ~80 → ~30 |
| `isWinnerShapedOutcomes()` | **Delete** — shape lookup replaces shape inference | ~25 |
| `renderEventRow()` outcome-classification logic | Simplify — shape is known, no inference needed | ~50 → ~20 |
| `renderAggOrSeries()` | Keep — already works against pre-computed fields | unchanged |

**Net sports.html change**: `−155 lines deleted, +50 lines added = net −105 lines.`

### `kalshi_ws.py` — add lifecycle channel (~30 lines)

```python
# In addition to ticker / orderbook_delta / trade, subscribe to
# market_lifecycle_v2 to receive 'settled' / 'determined' / 'paused'
# events. Forward these to /api/events subscribers via the existing
# _broadcast_to_browsers() pipe.
```

### Net summary

| File | Delete | Add | Net |
|---|---|---|---|
| `main.py` | -1505 | +200 | −1305 |
| `static/sports.html` | -155 | +50 | −105 |
| `kalshi_ws.py` | 0 | +30 | +30 |
| `kalshi_identity.py` (NEW) | 0 | +200 | +200 |
| `kalshi_join.py` (NEW) | 0 | +150 | +150 |
| `outcome_shapes.py` (NEW) | 0 | +250 | +250 |
| `live_source_selector.py` (NEW) | 0 | +150 | +150 |
| **Total** | **-1660** | **+1030** | **-630 lines** |

Smaller, more readable, less surface area for bugs.

## 5. Phased implementation

Strict ordering; each phase ships independently and is verified before the next.

### Phase 1 — `kalshi_identity.py` + tests + pytest setup (estimated half day)

- Add `pytest` to requirements (one line; no CI dep yet — runs locally and in any future CI)
- Build Identity dataclass + parse_ticker() + compute_fl_identity() + match()
- pytest tests against every snapshot in `kalshi_probe/snapshots/` — every observed ticker must parse to a non-`None` Identity
- Spot-check: take 10 known FL fixtures from prod cache, verify `match(parse_ticker(K), compute_fl_identity(FL))` returns True
- **No changes to /sports yet** — module sits alongside, unused

**Done criterion**: 100% of snapshot tickers parse without errors. The 10 known FL pairings match.

### Phase 2 — `outcome_shapes.py` + tests (estimated half day)

- Build the shape rule table from KALSHI_AUDIT.md §4
- `render_outcomes()` for the most common cases (GAME / 1H / TOTAL / SPREAD / BTTS / OVERTIME / map-N / set-N)
- Unit-test: render outcomes for every `outcome_shapes_*.json` snapshot, assert outcome counts match expected_count
- **No changes to /sports yet**

**Done criterion**: Every snapshot bucket renders to the expected outcome count.

### Phase 3 — `kalshi_join.py` + diff endpoint (estimated half day)

- `build_kalshi_index()` + `join_with_fl()` + `find_unpaired_buckets()`
- Add `/api/_debug/sports_feed_diff?sport_id=N&indent_days=M` that returns
  `{old: ..., new: ..., diff: {tournaments_added/removed/changed, fixtures_added/removed/changed}}`
- **/sports still uses old code path**

**Done criterion**: For Soccer + Basketball + Hockey on today + tomorrow, `diff.tournaments_changed == 0` and `diff.fixtures_changed.pairing_only == True` (any differences are pairing improvements, not regressions).

### Phase 4 — `live_source_selector.py` (estimated half day)

- Implement the source priority from §9
- Replace `_enrich_record_live_state()` body with one call
- Run the diff endpoint again — should be identical or better (more aggregates resolved, fewer null clocks)

### Phase 5 — `sports_feed_v2()` behind feature flag (estimated 1 day + flagged window)

- Implement the new handler at `/api/sports/{id}/feed?v=2`
- v1 keeps working at the default URL
- Frontend reads `?v2=1` query param to opt in
- Comprehensive manual QA: walk through 10 sports × 3 dates, compare side-by-side

**Promotion exit criteria — ALL must hold before phase 6:**

1. **Time window**: minimum 3 days flagged in production. Extend to **7 days for any sport with a weekly rhythm** (NFL — Sunday-heavy; NCAA Football — Saturday-heavy; UFC — typically Saturday card) so we don't miss the busy day.
2. **Zero P1 bugs**: nothing duplicate-fixture, nothing missing-outcome, nothing wrong-source-attached, nothing 500-on-load.
3. **Parity metrics**: for the same `(sport_id, indent_days)` pair, v2 vs v1 must have:
   - Same or higher pairing rate (paired fixtures / total fixtures) — never regress
   - Same or fewer null-clock events (live games with no clock displayed)
   - Same or fewer null-aggregate events (cup ties with no aggregate displayed)
   - Same or fewer null-outcome events (markets with empty outcomes[])
   - At most 0.5% Identity-parse failure rate across all production tickers (logged with the unparseable ticker for follow-up)
4. **No quiet stretch**: if any criterion is missed at the window expiry, **explicitly extend by 24 hours and document why** rather than letting it drift. After two extensions, escalate to "redesign needed" rather than ship-with-known-issues.

**Done criterion**: ALL exit criteria above met for the full window length.

#### Phase 5 punch list — gaps surfaced during the verification window

Single running list of every issue surfaced from live `/sports?v2=1` testing. One line per issue, fixed in small batches. Promotion to Phase 6 blocks until the list goes a full 7 days without new entries on weekly-rhythm sports (NFL/NCAAF/UFC).

| Date | Sport | Ticker / surface | Root cause | Fix commit |
|---|---|---|---|---|
| 2026-05-05 | All | KXJOINCLUB-26OCT02RODRYGO and ~265 other player/manager futures | G1-shape ticker mis-classified as per_fixture | `833e95d` |
| 2026-05-05 | Basketball | KXWNBADELAY ("Will at least 1 game be played in the WNBA season?") | Outright series prefix missing from `_OUTRIGHT_SERIES_PREFIXES` | `6ebbc92` |
| 2026-05-05 | All | Sub-market views duplicating team crests on matchup line + outcome rows | UI: redundant icon placement | `46a43e1`, `6ebbc92`, `f84514a` |
| 2026-05-05 | All | Sport-nav side links dropped `?v2=1` query param on click | UI: query-string forwarding | `6ebbc92` |
| 2026-05-05 | Basketball | KXNBAGAME-26MAY05LALOKC (LAL@OKC) FL row had no Kalshi block attached | FL/Kalshi NBA shortname divergence — added per-sport `_FL_ABBR_ALIASES` map in `kalshi_identity.py` (LAK↔LAL, OKL↔OKC, etc.) | `bdfe68b` |
| 2026-05-05 | All | When FL has a fixture but Kalshi pairing fails, the Kalshi market vanished into a sibling 'Other: <ticker>' tournament instead of surfacing inside the FL tournament | Added `_V2_SAFETY_NET_LEAGUE_PATTERNS` map + `_v2_safety_net_target()` fallback in `_v2_route_unpaired` — fuzzy-matches series_base to FL tournament NAME substring as a tertiary routing fallback (after deterministic in-request match and persistent hint) | _next commit_ |
| 2026-05-05 | UX | Outright/season-future tournaments were rendered at the TOP of side nav AND cards column, blocking the user from seeing live games | Reordered: outrights now `push` to bottom of side nav, cards column stable-sorts so outrights render last | `fed6818` |
| 2026-05-05 | Basketball | DET-CLE WINNER tab rendered empty even though KXNBAGAME-26MAY05CLEDET was paired and the data was sitting in the markets array | `_v2_pick_primary` chose `KXNBASERIES` (no market_type, came first in records) over `KXNBAGAME` (the actual 2-way Winner). `_extract_winner_prices` then ran on series-level outcomes with no home/away prices. Primary now prefers GAME/MATCH-suffixed series_ticker before falling through to the any-empty-market_type heuristic | `bd6255d` |
| 2026-05-06 | All | "Tomorrow" date pill showed late-evening current-day games (DET-CLE, OKC-LAL); some west-coast tonight games appeared under tomorrow | Backend buckets by UTC date; user thinks in browser-local. Added client-side `passesDateFilter` that compares event START_TIME vs `today + indent_days` in browser TZ | PR #15 |
| 2026-05-06 | Basketball | Orphan "What will the announcers say…" cards (KXNBAMENTION) showed instead of the headline Winner | Frontend `pickMarketForGameRow` returned first market with empty `market_type`; KXNBAMENTION came before KXNBAGAME. Now prefers the market whose `event_ticker` equals backend's chosen `event_ticker` | PR #15 |
| 2026-05-06 | All | GAME N context missing from playoff series cards | Added `renderGameNumberChip(ev)` parsing `^Game N:` from kalshi.title; renders next to SERIES chip | PR #15 |
| 2026-05-06 | Basketball | SAS-MIN/DET-CLE cards rendered without icons / time / full names while NYK-PHI rendered correctly | `isWinnerShapedOutcomes` used strict equality between Kalshi labels ("San Antonio") and FL HOME_NAME ("San Antonio Spurs"). Relaxed to bidirectional substring (≥3 char) with different-side guard | PR #16 |
| 2026-05-06 | Soccer | Universidad Central (Ven) vs Ind. del Valle showed only 2 of 3 Winner outcomes (home dropped) | `_extract_winner_prices` token-overlap couldn't classify "Caracas FC"-style Kalshi labels against FL home/away. Wired sports_feed_v3 to consult registry's `kalshi_outcome` aliases (Phase C2b) for authoritative side mapping; falls back to token-overlap when registry has no info | PR #18 |
| 2026-05-06 | All | Winner-shape cards (Champions League etc.) showed no kickoff time anywhere — matchup header was suppressed | Added `renderKickoffTimeChip(ev)` that renders START_TIME as `HH:MM` chip in aggrow strip; hidden for live/finished/outright | PR #19 |
| 2026-05-06 | All | Inline icon match in winner-shape cards required substring overlap (failed for Kalshi="PSG" vs FL="Paris Saint-Germain") | `collectOutcomesForRender` now stamps `side: 'home'/'away'/'tie'` on Winner outcomes; icon loop reads `o.side` directly instead of substring guessing. Substring kept as fallback for non-Winner outcomes | PR #19 |
| 2026-05-06 | Soccer | Boca/Barcelona SC and other Conmebol fixtures had no Kalshi block — `_FL_ABBR_ALIASES["Soccer"]` was empty | Backfilled ~30 Argentina/Brazil/Ecuador/Colombia/Peru/Bolivia/Paraguay/Uruguay aliases plus Atletico Madrid + PSG cross-league entries | PR #20 |
| 2026-05-06 | All | No "tool to let us know" surface — alias gaps and duplicate canonical entities were invisible | Built `registry_duplicates.py` with `find_duplicate_team_candidates` (token-Jaccard) and `find_duplicate_fixture_candidates` (orientation-blind same-day pair). Added `/api/_debug/registry_duplicates` (per-sport) and `/api/_debug/registry_duplicates_all` (cross-sport). `_notify_webhook` helper POSTs JSON to `STOCHVERSE_NOTIFY_WEBHOOK_URL` (Discord/Slack-compatible) | PR #22 |
| 2026-05-06 | All | Alias backfill was guesswork — needed actual abbrs production was shipping | Built `/api/_debug/unpaired_pairs?sport_id=N&indent_days=M` — surfaces unpaired FL events (with their SHORTNAME_HOME/AWAY) alongside unpaired Kalshi tickers (with parsed abbr_block) per (sport, date) bucket. Replaces guessing loop with data-driven backfill | PR #23 |
| 2026-05-06 | Soccer | Single-string `_FL_ABBR_ALIASES` couldn't represent ALW → {ALR, ARE, ARB} all at once | `normalize_fl_abbr` now accepts list/tuple/set values; Always Ready entries upgraded to list form covering all four equivalence-class members. Verified ARELAN appears in fl_orientations for the real FL fixture | PR #24 |
| 2026-05-06 | Soccer | Bayern Munich vs PSG (UCL Play Offs) rendered as bare synthetic event with no icons / time / matchup header | Initial diagnosis: synthetic Kalshi-only event needing enrichment. Found v3 was missing v1's two synthetic-event helpers (FL_TEAM_HINTS population + cosmetic enrichment pass). Ported both into sports_feed_v3 | PR #26 |
| 2026-05-06 | All | **Hand-coded alias_table approach hit unsustainability wall** — 379 unpaired FL events / 686 unpaired Kalshi tickers in single /unpaired_pairs response. User correctly called out that infrastructure was universal but data layer was manual | Added `title_match` tier between `alias_table` (2) and `guarded_fuzzy` (3). Parses Kalshi `title` ("Bayern Munich vs PSG", "Will X beat Y", etc.), token-Jaccard against FL HOME_NAME/AWAY_NAME canonical names with both-sides-must-overlap guard. Pairs every team Kalshi names without alias maintenance. Confidence 0.85. Resolved Bayern/PSG, Academia Puerto Cabello/Cienciano, and an open-ended set of future pairs | PR #27 |
| 2026-05-06 | All | After Bayern/PSG re-diagnosis: turned out NOT to be synthetic. FL had the fixture; PR #26's enrichment was barking up the wrong tree for this case. PR #27's title-match closed it as a regular paired event | (corrected by PR #27) | — |

### Phase 5+ — canonical entity registry (parallel track)

In response to the realization that adding Polymarket and OddsAPI on top of Kalshi+FL (+ ESPN/SportsDB/SofaScore for live state) would push us into N×N pairwise integration territory, we're building a canonical entity registry as the source-agnostic identity layer underneath the v2 join.

**Sub-phases:**

- **A — `identity_registry.py` foundation**: in-memory registry, canonical IDs (`team:<sport>:<slug>`, `fixture:<sport>:<date>:<home>-vs-<away>`, parameterized markets, outcome layer, alias index with method-precedence). Idempotent registration + version bumping. **No source mappers, no production wiring.** Status: _shipped_.
- **B — FL seed (`fl_registry_seed.py`)**: walk FL events list → populate teams/competitions/fixtures via the registry. FL is canonical for fixture metadata per the precedence policy. Team slug derived from `HOME_NAME` (long form), `SHORTNAME_HOME` stored as an alias on the team — so canonical IDs survive FL abbr-convention changes (`LAK→LAL` etc.) without renaming. FL `EVENT_ID` and `TOURNAMENT_STAGE_ID` registered as `source='fl'` aliases for `resolve_through_alias`-style lookups. Idempotent (re-runs leave registry unchanged); reschedules bump fixture version. **Read-only against prod data; doesn't touch v2 yet.** Status: _shipped_.
- **C — Kalshi seeder (`kalshi_registry_seed.py`)**: walks Kalshi cache records and resolves them through the canonical registry. Two-tier match at seed time — strict abbr-equality (tier 1), then alias-table expansion via `normalize_fl_abbr` (tier 2). On match, writes `(source='kalshi', external_id=ticker)` → `fixture.id` into the alias index with `method='strict'`/`'alias_table'` and `confidence=1.0`/`0.95`. After seeding, request-time Kalshi→fixture pairing collapses to a single `resolve_through_alias` O(1) lookup. **Doesn't migrate v2's request-time path yet — that's Phase C+1 once we prove the registry-based seeder hits the same pairings as the existing `kalshi_join`.** Status: _shipped_.
- **C2a — guarded fuzzy tier (Phase C2 part 1)**: third matching tier in `kalshi_registry_seed.py` with strict 1+1-on-each-side guard. Operates at the batch level — pass 1 runs strict + alias_table; records that miss both go into a buffer keyed by (sport, date); pass 2 walks each bucket and fires guarded fuzzy only if exactly one unpaired FL fixture (zero existing Kalshi aliases) AND exactly one buffered Kalshi record exist for that bucket. Otherwise leaves them all unpaired. Confidence 0.7 to flag downstream that the pairing was inferred. Added `IdentityRegistry.count_aliases_for(canonical_id, source)` to support the unpaired-fixture detection. Status: _shipped_.
- **C2b — market-layer seeding (Winner only)**: when a Kalshi record pairs to a fixture AND its `series_ticker` ends in `GAME` or `MATCH` (canonical headline-Winner suffixes per `KNOWN_SUFFIXES`), the seeder also registers the canonical `MarketType` (`market_type:<sport>:winner`, parameterized=False), the `Market` (`market:<fixture_id>:winner`), and one `Outcome` per Kalshi outcome with side classified via token-overlap against home/away team aliases (or matched against tie-word vocabulary). Aliases written under namespaced sources to avoid colliding with the existing fixture-level alias for the same event_ticker:`source='kalshi'` → fixture, `source='kalshi_market'` → market, `source='kalshi_outcome'` (keyed by per-outcome ticker) → outcome. This is what unlocks cross-source price aggregation for Winner markets — Polymarket/OddsAPI can register their own `market_id`/`outcome_id` aliases against the SAME canonical Market and Outcomes that Kalshi already filled in. Status: _shipped_.
- **C2c-a — per_leg market-layer (`kalshi_registry_seed.py`)**: per_leg Kalshi tickers (tennis sets via `KXATPSETWINNER`/`KXWTASETWINNER`, esports maps via `KXLOLMAP`/`KXCS2MAP`/`KXDOTAMAP`) resolve to their parent match fixture via `parent_fixture_identity` plus a parameterized sub-market for the specific leg. Per-sport taxonomy lives in `_PER_LEG_MARKET_TYPES` (`Tennis → Set Winner`, `Esports → Map Winner`); unmapped sports leave the parent-fixture alias written but skip the market layer. Each leg gets its own canonical `Market` with `params=(("leg_n", N),)` so set 1 and set 2 of the same match resolve to distinct Markets — exactly what cross-source aggregation needs. Outcomes are home/away (no tie). Status: _shipped_.
- **C2c-b — parameterized sub-markets (Spread / Total / Over-Under) — DEFERRED**: needs a sport-vocabulary title parser to extract thresholds (`Over 2.5 goals`, `Lakers wins by over 1.5`, etc.) so Kalshi's grouped-threshold outcomes split into one canonical `Market` per threshold. Per user direction: design and implement once Polymarket and OddsAPI APIs are audited, so the canonical model can be shaped against real cross-source data instead of guessed at. Status: _parked_.
- **C2c-c1 — registry-based pairing module + diff endpoint (`registry_pairing.py`, `/api/_debug/registry_diff`)**: ships the registry-routed pairing path WITHOUT routing user traffic to it. `pair_via_registry(sport, fl_response, kalshi_records)` builds an ephemeral registry, seeds FL+Kalshi via the Phase B/C/C2a/C2b/C2c-a seeders, then reads back the pairings via `registry.find_aliases_to`. `diff_pairings` compares v2 pairings vs registry pairings into a JSON-shaped diff. The new `/api/_debug/registry_diff?sport_id=N&indent_days=M` endpoint surfaces the diff against real production data so we can verify parity before promoting. Status: _shipped_.
- **C2c-c2 — request-time promotion (`?v=3`)**: ships `sports_feed_v3` in `main.py` which mirrors `sports_feed_v2`'s structure but replaces the join step with `pair_via_registry` (registry-routed, strict-date matching with timezone-aware `local_date` from Phase C2d — no fuzz). FL outright collection, persistent series-routing hints, unpaired-Kalshi synthetic surfacing, and tournament shape are all reused from v2 unchanged. The route `/api/sports/{sport_id}/feed?v=3` dispatches into v3; v2 stays at `?v=2`, v1 at default. Frontend `static/sports.html` extended with `?v3=1` URL flag (mirrors the existing `?v2=1` pattern); v3 wins if both flags somehow appear. Sport-nav links carry whichever flag the user landed with. Status: _shipped_.

- **C2d — timezone-aware Fixture.local_date (`competition_timezones.py`)**: rewrites Fixture canonical IDs to use the LOCAL game date (in the venue's timezone) rather than the FL UTC start_time date. Resolves the multi-game-series false-positive class (NBA Game 1 vs Game 2 on consecutive nights, KBO 3-game series, MLB 3-4 game series) AND the Kalshi-local vs FL-UTC date-offset class (Soccer CONMEBOL evening games crossing midnight UTC) in one stroke. Mechanism: `competition_tz(name, sport)` resolves an IANA timezone via substring match on the FL competition NAME (with per-sport fallback); `compute_local_date(utc_ts, tz)` converts to the local calendar date using `zoneinfo` (DST-aware). The FL seeder calls these at fixture-registration time; the canonical ID becomes `fixture:<sport>:<LOCAL_DATE>:<home>-vs-<away>`. Kalshi tickers (which already use local game dates) match strict-date directly — no fuzz needed. Per-team timezone is a known follow-up for US leagues with cross-timezone clubs (NBA West Coast late-evening edge cases). Status: _shipped_.

- **C2e — time-aware tiebreaker + canonical-ID time component**: completes the "use every signal both APIs already give us" matching story. Two coupled changes:
  1. **Canonical Fixture ID gains a UTC HHMM component**: `fixture:<sport>:<local_date>:<HHMM>:<home>-vs-<away>`. This means MLB doubleheaders (PHI-ATH 17:00 + PHI-ATH 23:00 same day) get distinct canonical Fixtures rather than the second one collapsing into the first — same for any same-day-multi-fixture case (intl basketball multi-game days, esports back-to-back maps, etc.). Reschedules now also produce a new canonical Fixture rather than version-bumping (more accurate semantically — a different start time IS a different game-state).
  2. **Time-aware tiebreaker in `kalshi_registry_seed`**: `_pick_best_by_time(matching, identity)` — when multiple Fixtures share `(sport, local_date, abbr_block)`, picks the one whose `start_time_utc` is closest to the Kalshi ticker's encoded time within ±30 min (matches `kalshi_join.match()`'s existing `fuzz_min`). Falls through to first-match when the Kalshi identity has no time component (G1 tickers like NBA `KXNBAGAME-26MAY05CLEDET`). Wired into single-record + batch + per_leg paths. Status: _shipped_.

### Phase C2c-c2 stage-2 — registry promoted to default

After C2d/C2e shipped and verified clean across in-season sports via `/api/_debug/registry_diff` (Soccer clean parity, Basketball/Hockey/Baseball stricter than v2 — registry correctly rejecting v2's playoff-cross-day and KBO-cross-day fuzz-bridge false positives), the `sports_feed` route's default flipped from `v: int = 1` to `v: int = 3`. Unflagged `/api/sports/{sport_id}/feed` now routes through `sports_feed_v3` (registry-based pairing). `?v=2` remains accessible as the legacy-kalshi_join-handler safety-window fallback for ~1 week. `?v=1` remains accessible as the original v1 fuzzy-match handler for emergency rollback / debugging only. Status: _shipped_.

- **D — generalize**: ESPN / SofaScore / SportsDB live-state dispatchers also resolve via the registry. Polymarket and OddsAPI plug in as new source mappers when they land, no changes to existing ones. Status: _planned_.

**Source precedence policy (per field):** FL authoritative for fixture metadata (start time, scores, teams). Each market source authoritative for its own market metadata + prices. No source overrides another source's prices — every source's prices attach to the same canonical Outcome under that source's namespace.

**Versioning:** Fixture has `version` + `updated_at_utc`; mappers bump version when a real change is observed (rescheduling, postponement, cancellation). Downstream caches key off `(fixture_id, version)`.

**Auditability:** every alias row stores `(source, external_id, canonical_id, method, confidence, observed_at_utc)`. "Why did we pair these?" answers via a registry lookup, not by reading code.

### Phase 6 — promote v2 to default (1 hour)

- Swap the default handler
- v1 stays accessible as `/api/sports/{id}/feed?v=1` for ~1 week
- Watch error rates / Sentry

### Phase 7 — delete v1 code (estimated 1 hour)

- After v2 is default for ≥1 week with no rollbacks, delete the old code paths per §4 above
- This is the big net-deletion commit

**Total estimate**: 4-5 working days end to end. Phases 1-4 can be parallelized if multiple sessions run.

## 6. Rollout / migration

**Feature flag mechanism**: simple `?v=2` query param on the API, mirrored to `?v2=1` on the frontend. No env vars; no DB; cheapest possible flag.

**Rollback plan**:
- During phases 5-6: change frontend default back to v1 (one-line revert).
- After phase 7: revert the deletion commit (git revert is clean since old code lived in main.py until the very last step).

**Production observation**:
- Prometheus / Sentry counters on `sports_feed_v2()` for: pairing rate, missing-outcome events, null-clock events.
- Compare to v1 baselines on the same metrics for the 3-day flagged window.

## 7. Test strategy

### Unit tests
- `tests/test_kalshi_identity.py` — every snapshot ticker parses to expected Identity.
- `tests/test_outcome_shapes.py` — every snapshot bucket renders to expected outcome count.
- `tests/test_kalshi_join.py` — known FL/Kalshi pairs match; known Kalshi-only records emit unpaired.

### Integration tests
- `/api/_debug/sports_feed_diff?sport_id=1&indent_days=0` — prod diff endpoint.
- Run nightly across 14 sport_ids × 7 indent_days = 98 combos.

### Manual QA matrix
Before promoting v2 to default, walk through:
- Soccer / today + tomorrow + Wed/Thu (UCL fixtures, MLS, Brasileirao)
- Basketball / today (NBA playoffs)
- Hockey / today (NHL playoffs)
- Baseball / today (MLB doubleheader if any)
- Tennis / today (any active tour)
- MMA / nearest UFC card
- Esports / today (LoL / CS2 / Valorant active days)
- Golf / nearest tournament
- One Tier-3 sport (Cricket / Lacrosse) — sanity check

## 8. Risk mitigation

| Risk | Mitigation |
|---|---|
| Identity parsing misses an edge-case ticker | Snapshot-driven tests catch it before deploy. Fallback: any ticker that fails to parse stays in v1's path until grammar is updated. |
| Outcome shape table missing a (sport, suffix, market_type) combo | `shape_for()` returns None → fall back to generic outcomes-array rendering. Logs the unknown combo for follow-up. |
| Source-selector's canonical priority doesn't match historical behavior | Diff endpoint catches this in phase 3. Tunable per-sport without changing the architecture. |
| WS lifecycle channel adds load | Channel is unfiltered; messages are infrequent (settle / determine / pause). Forward only the ones we care about. |
| Persistent `_SERIES_TOURNAMENT_HINTS` learns wrong mapping | Hint store is timestamp-overwrite — most recent observation wins. Worst case one bad request seeds bad data; next request corrects it. |

## 9. Open questions for review

1. **Module location**: keep new files at repo root (alongside `flashlive_feed.py`) or create a `kalshi/` package? I lean root for parity with existing layout but happy to package.
2. **Feature flag granularity**: `?v=2` URL param OR an env var like `SPORTS_V2_ENABLED=1` that switches everyone? URL param lets us test in prod without a deploy; env var lets us flip the whole site at once. Lean URL.
3. **Phase 5 length**: 3-day flagged window — too short, too long, just right?
4. **Test framework**: Stochverse doesn't have a test suite today as far as I can see. Add `pytest` as part of phase 1, or do snapshot-style asserts with a stdlib script?
5. **Delete order in phase 7**: one big commit or 4 smaller ones (one per deleted function family)? Bigger commit is easier to revert; smaller is easier to bisect.
6. **WS lifecycle channel**: phase 6 or defer to a v2.1? Adds value (live FT badges) but isn't required for the structural fix.

## 10. Out of scope for v2

These stay in v1 OR don't change at all (per `KALSHI_AUDIT.md` deferred work):

- Unclassified-records (politics, weather) — not a /sports concern
- Multivariate event collections rendering
- Cache builder classifier audit (the OWGRRANK family of bugs) — separate cleanup
- Player headshots / news / lineups for Kalshi-only sports — additive features post-v2
- Settlement UI — needs lifecycle channel; covered in phase 6 if we add it then, otherwise v2.1
- **Settled-Kalshi-data lookup for Finished tab** (deferred, not a priority — traders may want to refer back to closing data)
  - **Problem**: when a Kalshi market settles, the websocket drops it from the active cache. FL rows for FINISHED games then render with `kalshi: null`, so the Finished tab on /sports has no closing line / final YES-NO / last trade, even on the same day the game was played. Same applies if the user navigates back to a prior day via the date pill.
  - **Scope**: backend lookup only. Does NOT change Finished-tab visibility window (still scoped to whatever date the user has selected via the navigator). Does NOT add live updates or trading capability — settled markets are immutable.
  - **What it would surface**: final YES/NO outcome (where the market settled), closing price / last-trade price, optionally open/high/low for that market's last day. Available on Kalshi's REST `/trade-api/v2/markets/?status=settled` indefinitely.
  - **Implementation sketch** (~50 lines + short-TTL cache):
    - New `kalshi_settled_lookup.py` with an async `fetch_settled_for(date, abbr_block)` that hits Kalshi REST and returns a `{ticker: {final, close, last_trade}}` dict
    - Wire into `sports_feed_v3` as a fallback when an FL event is FINISHED and `kalshi` would otherwise be null
    - Cache by `(date, abbr_block)` for ~5 min — settled data doesn't change, but we want to bound REST volume
  - **Why deferred**: not required for live trading or pre-game surfaces. Pure historical reference. Build it once user feedback confirms traders are actually navigating back to look at closing lines.

## 11. Session retrospective — 2026-05-06

### What worked

- **Title-based matching tier (PR #27) is the structural answer** to the alias-maintenance problem. Closes a class of bugs (Bayern/PSG, Conmebol unpaired, J-League, EPL when FL/Kalshi abbrs diverge) without per-team manual coding.
- **`/api/_debug/unpaired_pairs` (PR #23)** turned alias backfill from guesswork into data-driven work. The right move would have been to ship this *first*, before any guessed alias rounds.
- **Registry-aware outcome side classification (PR #18)** correctly fixed Universidad Central's missing outcome — registry's existing alias data, just not consulted by the legacy `_extract_winner_prices` path.
- **Synthetic event enrichment in v3 (PR #26)** ports v1's `_FL_TEAM_HINTS` + cosmetic enrichment block into v3 — necessary for genuinely Kalshi-only events. Note: was *not* the right diagnosis for Bayern/PSG (which is a real FL fixture) — that needed PR #27 instead.

### What didn't work / was wasted

- **Two rounds of guessed Conmebol aliases (PRs #20, #21)** before building the diagnostic. Should have built `/unpaired_pairs` first, used it once, and shipped precise aliases. The order cost iterations and user patience.
- **Multiple chip-styling iterations (PRs #21 → #23 → #25)** for the WINNER chip. Should have asked for a mockup before shipping a shape and iterating. Net result: feature removed, awaiting mockup.
- **Wrong root-cause diagnosis on Bayern/PSG icons.** Burned several PRs (#16 substring relax, #19 explicit side, #26 synthetic enrichment) before realizing the actual bug was fixture-pairing failure, not synthetic-event handling. PR #27 closed it for real.

### Operating-mode adjustments going forward

- **For pairing/data bugs**: always run `/api/_debug/unpaired_pairs` or `registry_diff` *before* touching aliases. No more guesses.
- **For visual changes**: mockup or precise reference *first*, then implement once. No "let me try this and see."
- **For rendering bugs where the symptom is missing data**: 30 seconds of DevTools verification *before* changing code. Saves rounds.
- **Bias toward universal/structural solutions** (title-match) over per-team fixes (alias_table entries) when the problem class is broad.

### Today's PR ledger

| PR | Title | Status |
|---|---|---|
| #15 | Local-date filter + headline market picker + GAME N chip | merged |
| #16 | Substring-relaxed `isWinnerShapedOutcomes` (SAS-MIN icons) | merged |
| #17 | Plan doc — settled-Kalshi entry | merged |
| #18 | Registry-aware outcome side classification (Universidad Central) | merged |
| #19 | Kickoff time chip + explicit `side` on Winner outcomes | merged |
| #20 | Conmebol alias backfill — Argentina/Brazil/Ecuador/Colombia | merged |
| #21 | WINNER market chip (later removed) + Venezuela/Peru aliases | merged |
| #22 | Duplicate-detection endpoints + Discord/Slack webhook helper | merged |
| #23 | Thinner WINNER chip (later removed) + `/unpaired_pairs` diagnostic | merged |
| #24 | List-valued `_FL_ABBR_ALIASES` + Always Ready ARE | merged |
| #25 | Removed WINNER chip — pending mockup | merged |
| #26 | sports_feed_v3 — synthetic event enrichment (FL_TEAM_HINTS port) | merged |
| #27 | **Title-match tier** — universal pairing without alias maintenance | merged |

### Open issues / things left to do

See § 12 below.

## 12. Open punch list — picked up next session

### High-priority — verify today's deploy

- [ ] **Verify PR #27 (title-match) post-deploy**: refresh `/sports`, confirm Bayern Munich vs PSG renders as a regular fixture card (icons + kickoff time + matchup header). Check `/api/_debug/unpaired_pairs` to confirm bucket counts dropped.
- [ ] **Verify PR #26 (synthetic enrichment) post-deploy**: any genuinely Kalshi-only fixture with no FL counterpart should now show team icons + estimated kickoff time.
- [ ] **Confirm Conmebol pairs**: Boca/Barcelona SC, Always Ready/Lanus, Puerto Cabello/Cienciano all show Kalshi prices.

### WINNER chip — pending

- [ ] **Awaiting mockup from user.** Previous iterations (PR #21 pill, PR #23 underline-strip) were rejected. Removed in PR #25. Implement once mockup is shared.

### Active follow-ups (already on the deferred list, ready to pick up)

- [ ] **NBA per-team timezone for cross-tz clubs** — Phase C2d follow-up. Lakers/Warriors/Suns evening games tipping off late ET could still drift dates if league-level TZ differs from team-local. Small extension to `competition_timezones.py`.
- [ ] **Backend ±1 day expansion in `sports_feed_v3`** — flagged in PR #15 description. PT users navigating to "tomorrow" can't see late-evening events whose UTC date is day-after-tomorrow.
- [ ] **Series-chip parser bug** — "SERIES SPU 1-0" displaying even when Spurs lost. FL `INFO_NOTICE` / `WINNER` parsing in chip generator.
- [ ] **"Upcoming" + date navigation UX nit** — when user has "All" state pill active alongside "Tomorrow," FINISHED games still appear. Either auto-toggle to Upcoming when navigating dates forward, or change "All" semantics.
- [ ] **Discord webhook setup** (user action, not me) — create webhook URL, set `STOCHVERSE_NOTIFY_WEBHOOK_URL` env var on Render, optionally add Render Cron Service hitting `/registry_duplicates_all?notify=true` daily.

### Deferred (in plan doc, not blocking)

- **Phase C2c-b parameterized sub-markets** (Spread / Total / Over-Under) — wait for Polymarket + OddsAPI audit
- **Settled-Kalshi-data lookup for Finished tab** — pure historical reference; build when user demand confirms traders navigate back
- **Settlement UI** (live FT badges) — needs WS lifecycle channel
- **Player headshots / news / lineups** for Kalshi-only sports — additive feature post-v2
- **Phase 7 — delete v1 code** — once v3 proves itself over a verification window. Not urgent; v1 is the safety rollback.

### Polymarket / OddsAPI integration (future phase)

- Polymarket source mapper (separate seeder module)
- OddsAPI source mapper
- Cross-source price aggregation surfaces (uses canonical Outcome IDs from Phase C2b)
- Phase C2c-b parameterized sub-markets (gated on this audit)
