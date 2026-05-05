"""Kalshi → IdentityRegistry seeder — Phase C + C2.

Resolves Kalshi cache records through the canonical registry and
writes their fixture-level mappings into the alias index.

Three-tier match strategy at seed time:
    1. Strict abbr-equality — the deterministic path. Kalshi's
       parsed abbr_block must equal one of the FL fixture's
       team-pair concatenations (built from the team aliases in
       the registry).
    2. Alias-table — if the strict pass misses, expand each FL
       team's aliases through `normalize_fl_abbr` (Basketball:
       LAK↔LAL, OKL↔OKC, etc. — same map that Phase 5 punch-list
       seeded for the LAL@OKC pairing fix). Retry the equality
       check against the expanded form.
    3. Guarded fuzzy (Phase C2) — final fallback when (1) and (2)
       both miss. Fires ONLY when:
           a. the FL fixture and the Kalshi record are for the
              same sport and exact same date (no time-fuzz),
           b. the FL fixture has no other Kalshi alias yet,
           c. the bucket — all unpaired FL fixtures for (sport,
              date) intersected with all unpaired Kalshi records
              for (sport, date) — contains exactly ONE FL
              fixture and ONE Kalshi record.

       The 1+1-on-each-side guard is what prevents v1's
       wrong-fixture pairings: if there are two unpaired Atletico
       games and two unpaired Atletico-shaped Kalshi records on
       the same day, we don't gamble — leave them unpaired and
       let an alias-map entry resolve them next deploy.
       Confidence: 0.7 (substantially below strict/alias_table).

On a successful match, two aliases get written:
    source='kalshi', external_id=event_ticker  → fixture canonical id
    method='strict' or 'alias_table' depending on which tier hit.
    confidence=1.0 (strict) or 0.95 (alias_table — high but
    flags that the match required an alias rewrite).

Subsequent request-time pairing collapses to a single
`registry.resolve_through_alias('kalshi', ticker)` — O(1) dict
lookup, no fuzzy logic.

Phase C scope (this module):
    1. seed_kalshi_record  — single record → Fixture or None
    2. seed_kalshi_records — batch walk with stats

Phase C explicitly does NOT:
    * Migrate v2's request-time path. v2 still uses the
      compute_fl_identity / kalshi_join chain. That migration is
      Phase C+1 once we can prove the registry-based seeder hits
      the same pairings.
    * Seed market or outcome layers. The seeder writes a fixture-
      level alias only. Per-market and per-outcome alias seeding
      is Phase C2.
    * Implement guarded fuzzy. Phase C2.
"""
from __future__ import annotations
from typing import Optional

from identity_registry import IdentityRegistry, Fixture, Market
from kalshi_identity import (
    parse_ticker,
    normalize_fl_abbr,
    strip_known_suffix,
)


# ── Tie-word detection ───────────────────────────────────────────

_TIE_WORDS = frozenset({"tie", "draw", "no winner", "no result"})


# ── Market-type detection ────────────────────────────────────────

def _is_winner_market(series_ticker: str) -> bool:
    """True if `series_ticker` ends in a known headline-fixture
    suffix (GAME, MATCH) — i.e., this Kalshi record IS the 2-way/3-way
    Winner market for the fixture, not a sub-market like Spread or
    Total.

    Same heuristic used by main._v2_pick_primary (Phase 5 punch-list
    fix for the empty WINNER-tab bug). Centralizing it here lets the
    market-layer seeder identify which records to canonicalize.
    """
    series = (series_ticker or "").upper()
    _, suffix = strip_known_suffix(series)
    return suffix in ("GAME", "MATCH")


# ── Outcome side classification ──────────────────────────────────

def _classify_outcome_side(label: str,
                            registry: IdentityRegistry,
                            fixture: Fixture) -> Optional[str]:
    """Map a Kalshi outcome label to a canonical side.

    Returns 'home' / 'away' / 'tie', or None if the label can't be
    confidently classified.

    Strategy: token-overlap against the home and away teams' canonical
    names AND aliases. The team with the most token overlap wins the
    label. Tie words ('tie', 'draw', etc.) short-circuit to 'tie'.
    A label that overlaps zero tokens with both teams returns None.
    """
    if not label:
        return None
    label_lc = label.strip().lower()
    if label_lc in _TIE_WORDS:
        return "tie"

    home = registry.resolve_team(fixture.home_team_id)
    away = registry.resolve_team(fixture.away_team_id)
    if home is None or away is None:
        return None

    def _tokens(s: str) -> set:
        # Same min-length=3 token rule as main._extract_winner_prices —
        # so 'NY' / 'LA' (which collide with common English words)
        # don't count as evidence by themselves.
        return {t for t in
                "".join(c if c.isalnum() else " "
                        for c in s.lower()).split()
                if len(t) >= 3}

    home_tokens = _tokens(home.canonical_name)
    away_tokens = _tokens(away.canonical_name)
    for a in home.aliases:
        home_tokens |= _tokens(a)
    for a in away.aliases:
        away_tokens |= _tokens(a)

    label_tokens = _tokens(label_lc)
    home_overlap = len(home_tokens & label_tokens)
    away_overlap = len(away_tokens & label_tokens)
    if home_overlap == 0 and away_overlap == 0:
        return None
    if home_overlap > away_overlap:
        return "home"
    if away_overlap > home_overlap:
        return "away"
    # Tie in overlap — refuse to guess.
    return None


# ── Market-layer seeding (Phase C2b — Winner markets only) ───────

def _seed_winner_market_layer(registry: IdentityRegistry,
                               fixture: Fixture,
                               kalshi_record: dict,
                               sport: str) -> Optional[Market]:
    """Register MarketType / Market / Outcomes for a Kalshi Winner
    record and write Kalshi aliases on each.

    Phase C2b scope: ONLY Winner markets (series_ticker ends in
    GAME or MATCH). Parameterized sub-markets (Spread, Total,
    Over/Under, etc.) are deferred to Phase C2c — they need title
    parsing to extract thresholds, which is parser-heavy work that
    deserves its own pass.

    Aliases written under namespaced sources so the fixture-level
    alias (source='kalshi') doesn't collide with the market-level
    alias for the same event_ticker:
        source='kalshi_market',  external_id=event_ticker
                              → market.id  (strict, 1.0)
        source='kalshi_outcome', external_id=<per-outcome ticker>
                              → outcome.id (strict, 1.0)

    Returns the Market on success, None if the record isn't a Winner
    or any required field is missing.
    """
    series = (kalshi_record.get("series_ticker") or "").upper().strip()
    ticker = (kalshi_record.get("event_ticker") or "").upper().strip()
    if not series or not ticker:
        return None
    if not _is_winner_market(series):
        return None

    # Idempotent registrations.
    mt = registry.register_market_type(
        sport=sport, canonical_name="Winner", slug="winner",
        parameterized=False,
    )
    market = registry.register_market(
        fixture_id=fixture.id, market_type_id=mt.id,
    )
    registry.register_alias(
        source="kalshi_market", external_id=ticker,
        canonical_id=market.id, method="strict", confidence=1.0,
    )

    # Outcome layer
    outcomes = (kalshi_record.get("outcomes")
                or kalshi_record.get("_outcomes")
                or [])
    for o in outcomes:
        if not isinstance(o, dict):
            continue
        label = (o.get("label") or "").strip()
        if not label:
            continue
        side = _classify_outcome_side(label, registry, fixture)
        if side is None:
            # Unrecognized outcome — skip silently. This catches
            # malformed Kalshi data without blowing up the whole
            # batch seed.
            continue
        outcome = registry.register_outcome(
            market_id=market.id, side=side,
            canonical_label=label,
        )
        # Kalshi ships a per-outcome ticker (`ticker` field) that
        # the frontend WebSocket subscribes to. Register it as a
        # `kalshi_outcome` alias so post-Phase-D downstream consumers
        # can resolve outcome.id → live tick stream.
        outcome_ticker = (o.get("ticker") or "").strip()
        if outcome_ticker:
            registry.register_alias(
                source="kalshi_outcome",
                external_id=outcome_ticker,
                canonical_id=outcome.id, method="strict",
                confidence=1.0,
            )
    return market


# ── Orientation builders ─────────────────────────────────────────

def _team_alias_set(registry: IdentityRegistry,
                     team_id: str) -> frozenset:
    """All known short-form aliases for the team, as a frozenset.
    Returns empty if team isn't registered or has no aliases.
    """
    team = registry.resolve_team(team_id)
    if team is None:
        return frozenset()
    return team.aliases


def _orientations_strict(registry: IdentityRegistry,
                          fixture: Fixture) -> set:
    """Cross-product (home_alias × away_alias) in BOTH orientations.

    Returns the set of concatenated strings to compare against a
    Kalshi abbr_block. No alias-table expansion at this tier.
    """
    home_aliases = _team_alias_set(registry, fixture.home_team_id)
    away_aliases = _team_alias_set(registry, fixture.away_team_id)
    out = set()
    for h in home_aliases:
        for a in away_aliases:
            out.add(h + a)
            out.add(a + h)
    return out


def _orientations_with_alias_table(registry: IdentityRegistry,
                                    fixture: Fixture,
                                    sport: str) -> set:
    """Like _orientations_strict, but each home/away alias is
    expanded through normalize_fl_abbr first. Picks up FL/Kalshi
    abbreviation divergence (LAK↔LAL, etc.).
    """
    home_aliases = _team_alias_set(registry, fixture.home_team_id)
    away_aliases = _team_alias_set(registry, fixture.away_team_id)
    expanded_home: set = set()
    for h in home_aliases:
        expanded_home |= normalize_fl_abbr(sport, h)
    expanded_away: set = set()
    for a in away_aliases:
        expanded_away |= normalize_fl_abbr(sport, a)
    out = set()
    for h in expanded_home:
        for a in expanded_away:
            out.add(h + a)
            out.add(a + h)
    return out


# ── Per-record seeder ────────────────────────────────────────────

def seed_kalshi_record(registry: IdentityRegistry,
                        kalshi_record: dict,
                        sport: str) -> Optional[Fixture]:
    """Resolve a Kalshi cache record to a canonical Fixture.

    Walks the three-tier ladder (strict → alias_table; guarded
    fuzzy is Phase C2). On a match, registers a 'kalshi' alias
    against the fixture canonical id and returns the Fixture.
    Returns None for outright records, unparseable tickers, and
    records that miss every tier.

    `kalshi_record` shape (subset, what we actually read):
        {
            'event_ticker':  'KXUCLGAME-26MAY05ARSATM',
            'series_ticker': 'KXUCLGAME',
            ...
        }
    """
    ticker = (kalshi_record.get("event_ticker") or "").upper().strip()
    series = (kalshi_record.get("series_ticker") or "").upper().strip()
    if not ticker or not series:
        return None

    identity = parse_ticker(ticker, series, sport)
    if identity is None:
        return None
    if identity.kind != "per_fixture":
        # Outright / per_leg / per_series — don't pair to fixtures.
        # Per_leg pairing will be handled in Phase C2 alongside the
        # market-layer seeding.
        return None

    fixture_date = identity.date
    abbr_block = identity.abbr_block
    if not fixture_date or not abbr_block:
        return None

    candidates = registry.lookup_fixtures_by_date(sport, fixture_date)
    if not candidates:
        return None

    # Tier 1: strict abbr-equality on team aliases as-stored.
    for fx in candidates:
        if abbr_block in _orientations_strict(registry, fx):
            registry.register_alias(
                source="kalshi", external_id=ticker,
                canonical_id=fx.id, method="strict",
                confidence=1.0,
            )
            _seed_winner_market_layer(registry, fx, kalshi_record, sport)
            return fx

    # Tier 2: alias-table expansion.
    for fx in candidates:
        if abbr_block in _orientations_with_alias_table(registry, fx, sport):
            registry.register_alias(
                source="kalshi", external_id=ticker,
                canonical_id=fx.id, method="alias_table",
                confidence=0.95,
            )
            _seed_winner_market_layer(registry, fx, kalshi_record, sport)
            return fx

    # Tier 3 (guarded fuzzy) — only available via the batch seeder
    # since it requires bucket-level visibility (count of unpaired
    # FL fixtures + unpaired Kalshi records on the same date).
    return None


# ── Batch seeder with stats ──────────────────────────────────────

def seed_kalshi_records(registry: IdentityRegistry,
                          records: list, sport: str) -> dict:
    """Walk Kalshi cache records for a sport, attempt to seed each
    through the three-tier ladder (strict → alias_table → guarded
    fuzzy). Returns a stats dict for observability:

        {
            'total':              int,  # records in
            'paired_strict':      int,  # tier-1 hits
            'paired_alias':       int,  # tier-2 hits
            'paired_guarded':     int,  # tier-3 hits (Phase C2)
            'unpaired':           int,  # missed every tier
            'outright':           int,  # parsed as outright
            'unparseable':        int,  # parse_ticker None or wrong kind
        }

    Implementation: two passes.
      Pass 1 — for each record, run tier 1 + tier 2. Records that
               miss both are buffered for pass 2 along with their
               parsed identity.
      Pass 2 — group buffered records by (sport, fixture_date).
               For each bucket, find the unpaired FL fixtures (those
               with zero kalshi aliases) for that (sport, date). If
               the bucket has EXACTLY one unpaired FL fixture and
               EXACTLY one buffered Kalshi record, pair them with
               method='guarded_fuzzy', confidence=0.7. Anything
               else: leave unpaired.

    The 1+1 guard is the safety. If the bucket has two unpaired FL
    fixtures or two unparied Kalshi records on the same date, we
    refuse to guess — the caller should add an alias-map entry to
    disambiguate next deploy.
    """
    stats = {
        "total":          0,
        "paired_strict":  0,
        "paired_alias":   0,
        "paired_guarded": 0,
        "unpaired":       0,
        "outright":       0,
        "unparseable":    0,
    }
    # Pass-2 buffer: (ticker, identity) per still-unpaired record
    buffered: list = []

    # ── Pass 1: strict + alias_table ───────────────────────────
    for rec in records:
        if not isinstance(rec, dict):
            continue
        stats["total"] += 1
        ticker = (rec.get("event_ticker") or "").upper().strip()
        series = (rec.get("series_ticker") or "").upper().strip()
        if not ticker or not series:
            stats["unparseable"] += 1
            continue
        identity = parse_ticker(ticker, series, sport)
        if identity is None:
            stats["unparseable"] += 1
            continue
        if identity.kind == "outright":
            stats["outright"] += 1
            continue
        if identity.kind != "per_fixture":
            stats["unparseable"] += 1
            continue

        fixture_date = identity.date
        abbr_block = identity.abbr_block
        if not fixture_date or not abbr_block:
            stats["unpaired"] += 1
            continue

        candidates = registry.lookup_fixtures_by_date(sport, fixture_date)
        hit = None
        hit_method = None
        for fx in candidates:
            if abbr_block in _orientations_strict(registry, fx):
                hit, hit_method = fx, "strict"
                break
        if hit is None:
            for fx in candidates:
                if abbr_block in _orientations_with_alias_table(
                    registry, fx, sport,
                ):
                    hit, hit_method = fx, "alias_table"
                    break

        if hit is not None:
            registry.register_alias(
                source="kalshi", external_id=ticker,
                canonical_id=hit.id, method=hit_method,
                confidence=1.0 if hit_method == "strict" else 0.95,
            )
            _seed_winner_market_layer(registry, hit, rec, sport)
            if hit_method == "strict":
                stats["paired_strict"] += 1
            else:
                stats["paired_alias"] += 1
            continue

        # Buffer for tier-3 attempt
        buffered.append((ticker, identity, rec))

    # ── Pass 2: guarded fuzzy ──────────────────────────────────
    # Group buffered records by (sport, fixture_date).
    by_date: dict = {}
    for ticker, identity, raw in buffered:
        key = (sport, identity.date)
        by_date.setdefault(key, []).append((ticker, identity, raw))

    for (sp, dt), bucket_records in by_date.items():
        # Find unpaired FL fixtures for this (sport, date).
        all_fixtures = registry.lookup_fixtures_by_date(sp, dt)
        unpaired_fixtures = [
            fx for fx in all_fixtures
            if registry.count_aliases_for(fx.id, source="kalshi") == 0
        ]
        # 1+1 guard
        if len(unpaired_fixtures) == 1 and len(bucket_records) == 1:
            fx = unpaired_fixtures[0]
            ticker, _, raw = bucket_records[0]
            registry.register_alias(
                source="kalshi", external_id=ticker,
                canonical_id=fx.id, method="guarded_fuzzy",
                confidence=0.7,
            )
            _seed_winner_market_layer(registry, fx, raw, sport)
            stats["paired_guarded"] += 1
        else:
            # Bucket too ambiguous — leave every record unpaired.
            stats["unpaired"] += len(bucket_records)

    return stats
