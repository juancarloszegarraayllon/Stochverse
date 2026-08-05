"""Phase 3 v4-cutover feature-flag machinery.

Sole source of truth for cohort membership. Every consumer —
main.py:/api/events branching, /api/cutover_status endpoint, the
future daily-diff cohort-aware filter, ad-hoc operator queries —
imports `is_v4_cohort()` from THIS module. Never reimplement the
hash elsewhere.

Design (per Phase 3 flag-wiring S+DS):
  * Config is background-refreshed by a 5s supervise-wrapped loop
    (`_cutover_config_refresh_loop` in main.py, registered in
    ingestion.health as "cutover_config_refresh"). Serving path
    reads memory only — ZERO Neon round-trips on request.
  * Env-var fallback applies ONLY if no DB config has ever loaded
    (initial startup window before the bg loop populates).
  * Cohort membership = "N lowest-hash series in enabled sports,"
    where N = max(pct-derived count, min_cohort_series). Deterministic
    given identical universe → both WEB_CONCURRENCY=2 workers agree.
    Universe divergence (a series loaded on one worker but not
    the other) can shift membership at cohort boundaries; documented
    in the runbook and bounded to warm-load transients.
  * pct=0 default: is_v4_cohort() short-circuits to False; branching
    at call sites is a strict no-op with zero behavior change vs the
    pre-Phase-3 baseline.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from dataclasses import dataclass, field


# Env-var fallback values — used ONLY if no DB config has ever
# loaded (pre-first-refresh startup window). Both default to
# safe-serve-v3 values.
V4_TRAFFIC_PCT_FALLBACK: int = int(os.environ.get("V4_TRAFFIC_PCT_FALLBACK", "0"))
V4_SPORTS_FALLBACK: tuple[str, ...] = tuple(
    s.strip() for s in os.environ.get("V4_SPORTS_FALLBACK", "").split(",") if s.strip()
)
V4_MIN_COHORT_FALLBACK: int = int(os.environ.get("V4_MIN_COHORT_FALLBACK", "2"))


@dataclass(frozen=True)
class CutoverConfig:
    """Immutable snapshot of the current cutover state. Refreshed by
    the bg loop; served from memory to /api/events and /api/cutover_status.

    Fields:
      traffic_pct       — operator-controlled raw target (0-100).
      enabled_sports    — sport-name whitelist. Empty = v4 disabled.
      min_cohort_series — floor on cohort size; prevents lumpy
                          small-N stages (Day-64 spec).
      source            — "db" | "env_fallback" | "default". Tells
                          the operator whether the bg loop has ever
                          successfully loaded from Neon.
      loaded_at_ts      — unix seconds of most recent successful
                          config load.
    """
    traffic_pct:       int             = 0
    enabled_sports:    tuple[str, ...] = ()
    min_cohort_series: int             = 2
    source:            str             = "default"
    loaded_at_ts:      float           = 0.0


# ── Module-level state (RAM-served, bg-refreshed) ────────────────

# Current config. Read by is_v4_cohort() and /api/cutover_status;
# written ONLY by main._cutover_config_refresh_loop and
# _init_from_env_fallback (once at startup). No lock needed — CPython
# dataclass assignment is atomic and both readers/writers run in
# asyncio's single-threaded event loop.
_CURRENT_CONFIG: CutoverConfig = CutoverConfig(source="default")

# Per-worker fallback counter (incremented every time /api/events
# routes to v4 and the v4 pathway raises → served v3 instead).
# Not persisted — cross-worker aggregate = sum across sample
# responses. `threading.Lock` used because /api/events runs in a
# threadpool (FastAPI sync-endpoint semantics), not just the event
# loop.
_FALLBACK_COUNTER_LOCK = threading.Lock()
_FALLBACK_COUNTER = 0


def increment_fallback_counter() -> int:
    """Called from /api/events when v4 raises and we serve v3.
    Returns the new value for the caller's log line."""
    global _FALLBACK_COUNTER
    with _FALLBACK_COUNTER_LOCK:
        _FALLBACK_COUNTER += 1
        return _FALLBACK_COUNTER


def fallback_counter_value() -> int:
    with _FALLBACK_COUNTER_LOCK:
        return _FALLBACK_COUNTER


# ── Public API ───────────────────────────────────────────────────

def get_current_config() -> CutoverConfig:
    """RAM read of the current config. Used by is_v4_cohort(),
    /api/cutover_status, and (future) the daily-diff cohort filter."""
    return _CURRENT_CONFIG


def set_current_config(cfg: CutoverConfig) -> None:
    """Bg-loop writer. Called ONLY from
    main._cutover_config_refresh_loop AFTER a successful DB read
    OR from _init_from_env_fallback at startup."""
    global _CURRENT_CONFIG
    _CURRENT_CONFIG = cfg


def _init_from_env_fallback() -> None:
    """Called once at module import (via _init_default_config()) so
    the very first request lands on env-fallback values rather than
    the default (all zeros). Bg loop overrides with DB values within
    5s."""
    fallback = CutoverConfig(
        traffic_pct=V4_TRAFFIC_PCT_FALLBACK,
        enabled_sports=V4_SPORTS_FALLBACK,
        min_cohort_series=V4_MIN_COHORT_FALLBACK,
        source="env_fallback",
        loaded_at_ts=time.time(),
    )
    set_current_config(fallback)


_init_from_env_fallback()


# ── Cohort membership — sole source of truth ─────────────────────

def compute_cohort_size(universe_size: int, cfg: CutoverConfig) -> int:
    """Deterministic cohort size given the universe size and current
    config. Follows the "MIN-COHORT FLOOR" spec (Day-64) with a
    single-series-precision widener:

        pct_derived_count = round(universe_size * traffic_pct / 100)
        cohort_size       = max(pct_derived_count, min_cohort_series)
        cohort_size       = min(cohort_size, universe_size)      # cap

    Special cases:
    - traffic_pct == 0 → cohort_size 0 regardless of min_cohort_series
      (operator explicitly set "no v4 traffic"; the floor is a
       precision aid for small pcts, not a minimum for pct=0).
    - universe_size == 0 → cohort_size 0 (nothing to select).
    - universe_size < min_cohort_series → cohort_size = universe_size
      (capped; can't select more than what exists).

    Deterministic across workers given identical universe. Universe
    divergence (a series loaded on one worker but not the other) can
    shift membership at cohort boundaries — bounded to warm-load
    transients."""
    if universe_size <= 0 or cfg.traffic_pct <= 0:
        return 0
    pct_derived = round(universe_size * cfg.traffic_pct / 100)
    size = max(pct_derived, cfg.min_cohort_series)
    return min(size, universe_size)


def _hash_for(series_ticker: str) -> int:
    """md5-based deterministic hash. Same input → same integer
    across processes, workers, days. Never change this function
    without a companion migration for existing cohort assignments."""
    return int(hashlib.md5(series_ticker.encode()).hexdigest(), 16)


def _series_universe(cfg: CutoverConfig) -> list[str]:
    """Sport-scoped universe (all Kalshi series whose classified
    sport is in enabled_sports). Read from RAM only.

    Note: this is the RAW sport-classified universe — includes series
    that have no v4 linkage yet (fixture_id NULL in sp.kalshi_markets).
    For cohort-membership computation, use _linked_universe() below
    which filters to LINKED-only. Exposed here for /api/cutover_status
    observability (operator sees delta between total-classified and
    exercisable-via-v4)."""
    if not cfg.enabled_sports:
        return []
    try:
        from main import _SERIES_SPORT_DYNAMIC
    except Exception:
        return []
    enabled_set = set(cfg.enabled_sports)
    return [
        series for series, sport in _SERIES_SPORT_DYNAMIC.items()
        if sport in enabled_set
    ]


def _linked_universe(cfg: CutoverConfig) -> list[str]:
    """Cohort-eligible universe: sport-scoped AND has ≥1 event_ticker
    in the linkage map.

    Day-64 dry-run finding (folded into this PR): pct=5 against the
    raw sport-scoped Soccer universe (~360 series incl. totals/1H/BTTS/
    futures) produced cohort_size=18 — but only ~30 series have
    linkage today (game-markets with fixture_id). Most cohort members
    fell through to v3 silently, real v4 exercise ≈ 1-2.

    Filtering to linked-only ensures cohort membership always
    exercises the v4 pathway. Consequence: cohort membership shifts
    as linkage refreshes (series moves in/out of linked_universe as
    resolver classifies fixture_id). Bounded to the linkage refresh
    cadence (~30s); documented in the runbook."""
    if not cfg.enabled_sports:
        return []
    try:
        from linkage import linked_series_set
    except Exception:
        return []
    linked = linked_series_set()
    return [s for s in _series_universe(cfg) if s in linked]


def cohort_series_list(cfg: CutoverConfig | None = None) -> list[str]:
    """Return the current cohort as a list of series_tickers.
    Deterministic: sort LINKED universe by hash, take first N series
    where N = compute_cohort_size(len(linked_universe), cfg).

    Day-64 fold: cohort membership is drawn from LINKED universe
    (sport-scoped AND has linkage entry), not raw sport-scoped
    universe. Guarantees every cohort member can actually be served
    v4 — no silent fall-through to v3 for cohort members with no
    linkage.

    Recomputed on every call — the linked universe grows as the
    linkage cache refreshes (~30s cadence). Caching would introduce
    staleness at cohort boundaries."""
    if cfg is None:
        cfg = get_current_config()
    linked = _linked_universe(cfg)
    n = compute_cohort_size(len(linked), cfg)
    if n <= 0:
        return []
    # Sort by hash ascending → take first N. Ties broken by ticker
    # (stable ordering across calls given identical universe).
    ranked = sorted(linked, key=lambda s: (_hash_for(s), s))
    return ranked[:n]


def compute_effective_pct(cohort_size: int, universe_size: int) -> float:
    """Actual cohort as % of the LINKED universe. Delta vs
    traffic_pct signals whether MIN-COHORT FLOOR or rounding
    widened. Zero for empty universe.

    Example (Day-64 dry-run corrected via linked universe):
      traffic_pct=5, linked=30, min=2 → cohort=2 → effective=6.67
      traffic_pct=5, linked=30, min=5 → cohort=5 → effective=16.67
      traffic_pct=25, linked=30, min=2 → cohort=8 → effective=26.67
    Operator sees BOTH — the delta signals when floor triggered."""
    if universe_size <= 0:
        return 0.0
    return round(cohort_size / universe_size * 100, 2)


def is_v4_cohort(series_ticker: str, *, cfg: CutoverConfig | None = None) -> bool:
    """Sole source of truth for cohort membership. Every consumer
    imports this function; NEVER re-implement the hash elsewhere.

    Deterministic given identical linked universe: same series →
    same routing across processes, workers, days (until the linkage
    map refreshes and shifts the lowest-N-by-hash boundary; bounded
    to the ~30s linkage refresh cadence and documented in the
    runbook).

    Fast path: sport-scope check + linkage check short-circuit
    BEFORE building the cohort list, so non-cohort tickers pay ~2
    dict lookups."""
    if cfg is None:
        cfg = get_current_config()
    if not cfg.enabled_sports or cfg.traffic_pct <= 0:
        return False
    if not series_ticker:
        return False
    s_upper = str(series_ticker).upper()
    try:
        from main import _SERIES_SPORT_DYNAMIC
    except Exception:
        return False
    sport = _SERIES_SPORT_DYNAMIC.get(s_upper)
    if sport not in cfg.enabled_sports:
        return False
    # Fast path: if the series has no linkage entry, it can never
    # be in cohort under the Day-64 linked-universe rule. Short-
    # circuit before building the cohort list.
    try:
        from linkage import is_linked
    except Exception:
        return False
    if not is_linked(s_upper):
        return False
    # Slow path (only for sport-scoped, linked series): compute
    # cohort and check membership. Cohort ≤ min(N, linked_universe);
    # membership check is O(cohort_size), trivial at N ≤ 100.
    return series_ticker in cohort_series_list(cfg)
