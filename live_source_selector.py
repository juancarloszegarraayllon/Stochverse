"""Deterministic live-state source dispatch per KALSHI_AUDIT.md §9.

Phase 4 of /sports v2 (see SPORTS_V2_PLAN.md). Replaces the
~150-line nested fallback chain in `_enrich_record_live_state()`
with a clean per-sport priority list + composable overlays.

Public API:
  select_live_source(title, sport, *, sources=None)
      → dict (canonical game data) or None

  enrich_for_record(title, sport, record, *, sources=None)
      → dict matching `_live_state` schema (or {}); equivalent to
        the existing `_enrich_record_live_state()` output, ready
        for drop-in replacement in phase 5.

Source priority (per sport — see §9):
  Basketball / Football / Hockey: ESPN → FL → SportsDB → SofaScore
  Soccer:                         FL → ESPN → SportsDB → SofaScore
  Baseball:                       ESPN → FL → SportsDB → SofaScore
  Cricket:                        FL → SofaScore
  Tennis:                         FL
  Rugby:                          FL → SofaScore
  Boxing / MMA / Golf / Motorsport / Esports / Aussie Rules /
  Darts / Table Tennis:           FL
  Chess / Lacrosse / Other:       (none — Kalshi-only)

Soccer-specific overlay: SofaScore aggregate lookup for cup ties
when the primary source is missing aggregate_home/away.

Tests inject `sources` (a dict of fake match_game callables) to
avoid hitting real feeds.
"""
from __future__ import annotations

from typing import Callable, Optional


# ── Source priority per sport (KALSHI_AUDIT.md §9) ───────────────

_SPORT_PRIORITY: dict[str, list[str]] = {
    # ESPN canonical for live clock (per _ESPN_CLOCK_SPORTS)
    "Basketball":   ["espn", "fl", "sportsdb", "sofascore"],
    "Football":     ["espn", "fl", "sportsdb", "sofascore"],
    "Hockey":       ["espn", "fl", "sportsdb", "sofascore"],
    "Baseball":     ["espn", "fl", "sportsdb", "sofascore"],
    # FL canonical for soccer (richer stoppage-time + STAGE_START_TIME)
    "Soccer":       ["fl", "espn", "sportsdb", "sofascore"],
    # Sports with multi-source coverage where FL is primary
    "Cricket":      ["fl", "sofascore"],
    "Rugby":        ["fl", "sofascore"],
    # Sports covered exclusively by FL
    "Tennis":       ["fl"],
    "Boxing":       ["fl"],
    "MMA":          ["fl"],
    "Golf":         ["fl"],
    "Motorsport":   ["fl"],
    "Esports":      ["fl"],
    "Aussie Rules": ["fl"],
    "Darts":        ["fl"],
    "Table Tennis": ["fl"],
    # Sports we have no live source for — Kalshi-only
    "Chess":        [],
    "Lacrosse":     [],
    "Other Sports": [],
}


# ── Default source callers (wrap the four feed modules) ──────────
# Each is a wrapper that's safe to call: catches import / runtime
# errors and returns None instead of propagating.

def _try_espn(title: str, sport: str) -> Optional[dict]:
    try:
        from espn_feed import match_game
        return match_game(title, sport)
    except Exception:
        return None


def _try_fl(title: str, sport: str) -> Optional[dict]:
    try:
        from flashlive_feed import match_game
        return match_game(title, sport)
    except Exception:
        return None


def _try_sportsdb(title: str, sport: str) -> Optional[dict]:
    try:
        from sportsdb_feed import match_game
        return match_game(title, sport)
    except Exception:
        return None


def _try_sofascore(title: str, sport: str) -> Optional[dict]:
    try:
        from sofascore_feed import match_game
        return match_game(title, sport)
    except Exception:
        return None


_DEFAULT_SOURCES: dict[str, Callable[[str, str], Optional[dict]]] = {
    "espn":      _try_espn,
    "fl":        _try_fl,
    "sportsdb":  _try_sportsdb,
    "sofascore": _try_sofascore,
}


# ── Public: select_live_source ───────────────────────────────────

def select_live_source(
    title: str,
    sport: str,
    *,
    sources: Optional[dict[str, Callable[[str, str], Optional[dict]]]] = None,
) -> Optional[dict]:
    """Walk the per-sport priority chain. Return the first non-None
    source result, augmented with `_source` (the source name).

    `sources` parameter exists for testing — pass a dict of fake
    match_game callables to avoid hitting real feeds. Defaults to
    the real `_DEFAULT_SOURCES`.

    Returns None when no source has data.
    """
    if not title or not sport:
        return None
    src_map = sources if sources is not None else _DEFAULT_SOURCES
    for name in _SPORT_PRIORITY.get(sport, []):
        caller = src_map.get(name)
        if caller is None:
            continue
        try:
            g = caller(title, sport)
        except Exception:
            g = None
        if g is not None:
            # Tag the source for diagnostics. Non-destructive: only
            # set if not already present.
            if "_source" not in g:
                g["_source"] = name
            return g
    return None


# ── Soccer aggregate overlay ─────────────────────────────────────

# Series-base prefixes that indicate a cup-tie competition where
# aggregate scoring matters. Source: KALSHI_AUDIT.md §2 + the
# existing _cup_prefixes_for_agg in main.py.
_CUP_PREFIXES = (
    "KXUCL", "KXUEL", "KXUECL",
    "KXCONMEBOLLIB", "KXCONMEBOLSUD", "KXCONMEBOL",
    "KXFACUP", "KXDFBPOKAL", "KXCOPADELREY",
    "KXCOPPAITALIA", "KXKNVBCUP", "KXMLSCUP",
    "KXCONCACAFCCUP",
)


def is_cup_series(series_base: str) -> bool:
    """True if the series_base indicates a soccer cup-tie competition.

    Used to gate aggregate-overlay calls so we don't waste SofaScore
    lookups on round-robin league fixtures (which never have aggregates).
    """
    s = (series_base or "").upper()
    return any(s.startswith(p) for p in _CUP_PREFIXES)


def overlay_soccer_aggregate(
    g: dict,
    title: str,
    series_base: str,
    *,
    sofa_lookup: Optional[Callable[[str], Optional[dict]]] = None,
) -> dict:
    """Overlay aggregate_home / aggregate_away / leg_number / round_name
    on a soccer game dict when the primary source is missing them.

    Only runs when:
      - g is non-None
      - series_base is a cup-tie series
      - g is missing aggregate_home or aggregate_away

    Mutates and returns g (for chaining).

    `sofa_lookup` is the SofaScore aggregate lookup callable — exists
    for testing. Defaults to the real `sofascore_feed.lookup_aggregate_sync`.
    """
    if g is None:
        return g
    if g.get("aggregate_home") is not None and g.get("aggregate_away") is not None:
        # Already has aggregate from the primary source.
        return g
    if not is_cup_series(series_base):
        return g

    if sofa_lookup is None:
        try:
            from sofascore_feed import lookup_aggregate_sync as sofa_lookup
        except ImportError:
            return g

    try:
        agg = sofa_lookup(title)
    except Exception:
        agg = None

    if not agg:
        return g

    # Fill any missing fields without overriding what's already there.
    if g.get("aggregate_home") is None and agg.get("aggregate_home") is not None:
        g["aggregate_home"] = agg.get("aggregate_home")
    if g.get("aggregate_away") is None and agg.get("aggregate_away") is not None:
        g["aggregate_away"] = agg.get("aggregate_away")
    if not g.get("is_two_leg") and agg.get("is_two_leg"):
        g["is_two_leg"] = True
    if not g.get("leg_number") and agg.get("leg_number"):
        g["leg_number"] = agg.get("leg_number")
    if not g.get("round_name") and agg.get("round_name"):
        g["round_name"] = agg.get("round_name")
    return g


# ── Public: enrich_for_record ────────────────────────────────────
# This produces the same dict shape `_enrich_record_live_state`
# currently returns, so phase 5 swap is just changing the call site.

def _series_base_from_record(record: dict) -> str:
    """Extract series_base from a cache record. Strips known suffixes."""
    s = (record.get("series_ticker") or "").upper()
    if not s:
        return ""
    # Reuse outcome_shapes.KNOWN_SUFFIXES via local import to avoid
    # cycles. If outcome_shapes isn't available (test isolation),
    # we still return the raw series_ticker — caller falls back to
    # is_cup_series's prefix match.
    try:
        from kalshi_identity import strip_known_suffix
        base, _ = strip_known_suffix(s)
        return base
    except Exception:
        return s


def enrich_for_record(
    title: str,
    sport: str,
    record: Optional[dict] = None,
    *,
    sources: Optional[dict[str, Callable[[str, str], Optional[dict]]]] = None,
    sofa_lookup: Optional[Callable[[str], Optional[dict]]] = None,
) -> dict:
    """Drop-in replacement for `_enrich_record_live_state()`.

    Returns a dict matching the `_live_state` schema documented in
    KALSHI_AUDIT.md §1, or `{}` if no live state is available.

    The dict carries:
      state, display_clock, period, stage_start_ms, captured_at_ms,
      clock_running, is_two_leg, aggregate_home, aggregate_away,
      leg_number, round_name, series_home_wins, series_away_wins,
      series_summary, series_game_number, is_playoff, _source

    Implementation:
      1. select_live_source() — try sources in per-sport priority
      2. For Soccer cup-ties: overlay_soccer_aggregate() via SofaScore
      3. (Bracket-cache and basketball-series fallbacks deferred to
         caller — those need access to module-level cache state in
         main.py and don't fit a pure-function module cleanly.
         Phase 5 will wire those in.)

    The caller in phase 5 / sports_feed_v2() is responsible for the
    bracket-cache / playoff-series fallback enrichments AFTER calling
    this; they require module state in main.py.
    """
    g = select_live_source(title, sport, sources=sources)
    if g is None:
        return {}

    if sport == "Soccer":
        series_base = _series_base_from_record(record or {})
        g = overlay_soccer_aggregate(
            g, title, series_base, sofa_lookup=sofa_lookup,
        )

    # Project to the canonical `_live_state` schema. Use .get so
    # missing fields end up as None / "" / 0 rather than KeyError.
    return {
        "state":              g.get("state", ""),
        "display_clock":      g.get("display_clock", ""),
        "period":             g.get("period", 0),
        "stage_start_ms":     g.get("stage_start_ms", 0),
        "captured_at_ms":     g.get("captured_at_ms", 0),
        "clock_running":      g.get("clock_running", True),
        "is_two_leg":         bool(g.get("is_two_leg")),
        "aggregate_home":     g.get("aggregate_home"),
        "aggregate_away":     g.get("aggregate_away"),
        "leg_number":         g.get("leg_number"),
        "round_name":         g.get("round_name", ""),
        "series_home_wins":   g.get("series_home_wins"),
        "series_away_wins":   g.get("series_away_wins"),
        "series_summary":     g.get("series_summary", "") or "",
        "series_game_number": g.get("series_game_number"),
        "is_playoff":         bool(g.get("is_playoff")),
        "_source":            g.get("_source", ""),
    }
