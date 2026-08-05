"""Phase 3 v4 pathway — event_ticker → fixture linkage cache.

Sole home for `_V4_LINKAGE_MAP` and `_apply_v4_linkage()`. The
serving path (main.py:/api/events branching) imports THIS module;
never build a parallel lookup elsewhere.

Architectural constraint (operator-mandated): serving path is
DB-free. The linkage cache is RAM-served; the bg loop
(_v4_linkage_refresh_loop in main.py) refreshes from
sp.kalshi_markets ∪ sp.fl_events every V4_LINKAGE_REFRESH_INTERVAL_S
seconds and atomically swaps a new dict into _V4_LINKAGE_MAP.

Cold-cache behavior: warm-load from cache_blobs['v4_linkage_snapshot']
at boot so first-request v4 lookups succeed before the bg loop's
first pass. Missing linkage for a specific event_ticker is NOT an
error (unresolved markets legitimately have fixture_id NULL) —
fall through to v3 silently without incrementing fallback counter.
"""
from __future__ import annotations

import time
from typing import Any


# event_ticker (UPPER) → linkage entry
# Entry shape: {"fl_event_id": str, "fixture_id": int, "updated_at_ts": float}
_V4_LINKAGE_MAP: dict[str, dict] = {}
_V4_LINKAGE_MAP_LAST_REFRESHED_TS: float = 0.0
_V4_LINKAGE_MAP_SOURCE: str = "default"   # "default" | "warm_load" | "bg_refresh"


# SCOPE DECISION (2026-08-05): full field-level parity between
# v4-served and v3-served records requires REFACTORING v3's
# match_game call sites so v4 can substitute the FL game lookup
# without duplicating v3's ~30-line _live_state build (which has
# per-sport special-cases in main.py:3260+). That refactor is a
# substantive scoped workstream and is deferred to a follow-up PR.
#
# This PR ships the LINKAGE INFRASTRUCTURE + cohort ATTRIBUTION
# machinery. What `_apply_v4_linkage` does in this PR:
#   1. Verify a linkage exists for record.event_ticker (proves
#      the bg refresh + linkage map are working end-to-end).
#   2. Verify an FL game dict is reachable via GAMES_BY_EVENT_ID
#      (proves the FL secondary index is populated + swept in sync).
#   3. Stamp _v4_linked + _v4_fl_event_id markers on the record.
#      Enables the daily_diff cohort attribution + operator log
#      grepping BEFORE the actual v3-vs-v4 rendering divergence.
#
# What this PR DOES NOT ship (deferred to v4-rendering PR):
#   * Actual v3 _live_state field OVERWRITES on the record.
#   * v3 formatting-site refactor to prefer linkage'd FL game.
#   * Response-shape divergence between v3-served and v4-served.
#
# Consequence for operator: at pct>0, cohort tickers show
# _v4_linked=True in logs and daily_diff attributes them to
# cohort buckets — but the served response is BYTE-IDENTICAL to
# v3 (same match_game path, same _live_state build). This is
# intentional — proves the linkage infrastructure BEFORE anything
# user-visible changes. Operator can flip pct=5 for soccer, watch
# daily_diff cohort attribution + /api/cutover_status metrics,
# with zero user-visible risk. v4-rendering PR ships once
# infrastructure is verified.
V4_OVERLAY_KEYS: tuple[str, ...] = ()   # empty this PR; populated by v4-rendering follow-up


def _apply_v4_linkage(record: dict) -> None:
    """v4 pathway (linkage-infrastructure scope for this PR).

    Verifies the linkage cache + FL secondary index end-to-end for
    the given record, then stamps observability markers. Does NOT
    overlay v3 live-state fields — that's the v4-rendering follow-
    up PR's scope (requires refactoring v3's match_game sites).

    Semantic contract:
      - Missing event_ticker / missing linkage / missing FL game =
        NO-OP silent return. LEGITIMATE states (unresolved market,
        cold cache, GC'd game); caller MUST NOT interpret as error,
        MUST NOT increment fallback counter.
      - On success: sets record['_v4_linked'] = True and
        record['_v4_fl_event_id'] = <fl_event_id> for observability.
      - Raises ONLY on genuinely-unexpected error (dict shape drift,
        import failure). Caller catches Exception at /api/events
        branching site and increments fallback counter.

    Response shape at pct>0 (this PR): byte-identical to v3 for
    cohort tickers. _v4_linked is added; nothing else changes.
    Frontend / downstream consumers unaffected. Cohort attribution
    in daily_diff reads the marker to split per-cohort buckets."""
    event_ticker = record.get("event_ticker")
    if not event_ticker:
        return
    linkage = _V4_LINKAGE_MAP.get(str(event_ticker).upper())
    if not linkage:
        return
    fl_event_id = linkage.get("fl_event_id")
    if not fl_event_id:
        return
    from flashlive_feed import GAMES_BY_EVENT_ID
    fl_game = GAMES_BY_EVENT_ID.get(str(fl_event_id))
    if not fl_game:
        return
    # Success — stamp observability markers. V4_OVERLAY_KEYS is
    # empty this PR; v4-rendering PR populates it and overlays.
    for key in V4_OVERLAY_KEYS:
        if key in fl_game:
            record[key] = fl_game[key]
    record["_v4_linked"] = True
    record["_v4_fl_event_id"] = str(fl_event_id)


def snapshot_state() -> dict:
    """RAM-read state summary for /api/cutover_status. Includes the
    map source (default/warm_load/bg_refresh) so operators can
    distinguish 'bg loop never completed' from 'bg loop working
    normally'."""
    return {
        "linkage_map_size":         len(_V4_LINKAGE_MAP),
        "linkage_last_refreshed_ts": _V4_LINKAGE_MAP_LAST_REFRESHED_TS or None,
        "linkage_source":           _V4_LINKAGE_MAP_SOURCE,
    }


def is_linked(series_ticker: str, series_to_tickers: dict | None = None) -> bool:
    """Check whether a SERIES has any linked event_ticker in the map.

    Series-level check because cohort membership is at series
    granularity: cohort filter needs to know "is this series
    represented in the linkage map at all?" not "is this specific
    event_ticker linked?". A series with 10 game markets where 3
    have fixture_id → 3 event_tickers in the map → is_linked=True.

    Two lookup strategies:
      - series_to_tickers explicit: caller passes a pre-built
        {series_ticker: [event_ticker, ...]} map. Preferred for
        loops that check many series (build map once).
      - Scan the linkage map's values for series-prefix match:
        cheap for one-off checks. Used when series_to_tickers not
        provided."""
    if not series_ticker:
        return False
    s_upper = str(series_ticker).upper()
    if series_to_tickers is not None:
        tickers = series_to_tickers.get(s_upper, [])
        return any(t in _V4_LINKAGE_MAP for t in tickers)
    # Fallback scan — inspects each event_ticker in the map and
    # checks if it matches the series prefix. Kalshi event_ticker
    # convention: SERIES-SUFFIX (e.g., KXNBAGAME-26APR11WSHPIT).
    # Series prefix ends at first '-'; matches on that prefix.
    prefix = s_upper + "-"
    for event_ticker in _V4_LINKAGE_MAP:
        if event_ticker.startswith(prefix):
            return True
    return False


def build_series_to_tickers_map() -> dict[str, list[str]]:
    """Group current linkage-map entries by series_ticker (derived
    from event_ticker prefix). Used by cutover.cohort_series_list()
    to compute the linked-universe efficiently: one pass over the
    linkage map, produces {series: [tickers]} for all linked series.

    Series-derivation: event_ticker splits on first '-' into
    (series_ticker, suffix). Matches Kalshi's convention (
    KXNBAGAME-26APR11WSHPIT → KXNBAGAME) and works for all
    observed market families."""
    out: dict[str, list[str]] = {}
    for event_ticker in _V4_LINKAGE_MAP:
        # Series prefix — everything before the first '-'.
        if "-" in event_ticker:
            series = event_ticker.split("-", 1)[0]
        else:
            series = event_ticker
        out.setdefault(series, []).append(event_ticker)
    return out


def linked_series_set() -> set[str]:
    """Set of series_tickers that have ≥1 entry in the linkage map.
    Used by cutover to compute linked_universe efficiently."""
    return set(build_series_to_tickers_map().keys())
