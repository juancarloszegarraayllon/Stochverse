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

### Phase 5+ — canonical entity registry (parallel track)

In response to the realization that adding Polymarket and OddsAPI on top of Kalshi+FL (+ ESPN/SportsDB/SofaScore for live state) would push us into N×N pairwise integration territory, we're building a canonical entity registry as the source-agnostic identity layer underneath the v2 join.

**Sub-phases:**

- **A — `identity_registry.py` foundation**: in-memory registry, canonical IDs (`team:<sport>:<slug>`, `fixture:<sport>:<date>:<home>-vs-<away>`, parameterized markets, outcome layer, alias index with method-precedence). Idempotent registration + version bumping. **No source mappers, no production wiring.** Status: _shipped_.
- **B — FL seed**: walk FL events list, populate teams/fixtures/competitions in the registry. FL is canonical for fixture metadata per the precedence policy. Read-only against production data; doesn't touch v2 yet.
- **C — Kalshi migration**: `kalshi_join` resolves through the registry. The 3-tier matching (strict → alias → guarded fuzzy) becomes the seeding logic for `kalshi_team_alias` / `kalshi_fixture_alias`, run at first sighting. Request-time pairing collapses to O(1) lookup.
- **D — generalize**: ESPN / SofaScore / SportsDB live-state dispatchers also resolve via the registry. Polymarket and OddsAPI plug in as new source mappers when they land, no changes to existing ones.

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
