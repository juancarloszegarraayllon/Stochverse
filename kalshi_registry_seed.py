"""Kalshi → IdentityRegistry seeder — Phase C.

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
    3. Guarded fuzzy — DEFERRED to Phase C2. Out of scope here.
       When (1) and (2) miss, we record the Kalshi record as
       unpaired and move on. Phase C2 will add the date+competition
       guarded fuzzy fallback (only fires when exactly one unpaired
       FL fixture and one Kalshi record remain in a (date, comp)
       bucket).

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

from identity_registry import IdentityRegistry, Fixture
from kalshi_identity import (
    parse_ticker,
    normalize_fl_abbr,
)


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
            return fx

    # Tier 2: alias-table expansion.
    for fx in candidates:
        if abbr_block in _orientations_with_alias_table(registry, fx, sport):
            registry.register_alias(
                source="kalshi", external_id=ticker,
                canonical_id=fx.id, method="alias_table",
                confidence=0.95,
            )
            return fx

    # Tier 3 (guarded fuzzy) — Phase C2.
    return None


# ── Batch seeder with stats ──────────────────────────────────────

def seed_kalshi_records(registry: IdentityRegistry,
                          records: list, sport: str) -> dict:
    """Walk Kalshi cache records for a sport, attempt to seed each
    via seed_kalshi_record. Returns a stats dict for observability:

        {
            'total':         int,  # records in
            'paired_strict': int,  # tier-1 hits
            'paired_alias':  int,  # tier-2 hits
            'unpaired':      int,  # missed every tier (excl. outright)
            'outright':      int,  # parsed as outright
            'unparseable':   int,  # parse_ticker returned None or
                                   # kind not in (per_fixture, outright)
        }
    """
    stats = {
        "total":         0,
        "paired_strict": 0,
        "paired_alias":  0,
        "unpaired":      0,
        "outright":      0,
        "unparseable":   0,
    }
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
            # per_leg etc. — counted as unparseable for now, will
            # become a real bucket in Phase C2.
            stats["unparseable"] += 1
            continue

        # Re-do the candidate walk but now we also need to know
        # which tier hit (so we can stat correctly).
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
        if hit is None:
            stats["unpaired"] += 1
            continue
        # Register alias
        registry.register_alias(
            source="kalshi", external_id=ticker,
            canonical_id=hit.id, method=hit_method,
            confidence=1.0 if hit_method == "strict" else 0.95,
        )
        if hit_method == "strict":
            stats["paired_strict"] += 1
        else:
            stats["paired_alias"] += 1

    return stats
