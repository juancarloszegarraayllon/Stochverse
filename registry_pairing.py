"""Registry-based fixture↔Kalshi pairing — Phase C2c-c foundation.

This module is the bridge between the canonical IdentityRegistry
infrastructure (Phases A-C2b) and request-time Kalshi pairing in
production. It produces the same logical join as
`kalshi_join.join_with_fl` but routed through the registry: FL +
Kalshi data flows in, the registry gets seeded, then we read back
the pairings via `registry.find_aliases_to`.

Phase C2c-c part 1 (this module + diff endpoint):
    Build the registry-based pairing path. Don't route any user
    traffic to it yet. Instead, the diff endpoint surfaces a
    side-by-side comparison of (v2-existing) vs (registry-based)
    pairings on real production data. We promote the registry
    path only once parity holds.

Phase C2c-c part 2 (separate PR):
    Add a user-visible flag (e.g. `?v=3`) on `sports_feed_v2` that
    routes through the registry path. v2 stays accessible behind
    `?v=2` for the verification window, same pattern as v1→v2.

Public API:
    pair_via_registry(sport, fl_response, kalshi_records)
        → {fl_event_id: [kalshi_ticker, ...]}

The function is read-only against its inputs — it builds a fresh
IdentityRegistry per call so concurrent callers don't share state.
The shared `global_registry()` would be the right place if/when we
move to long-lived registry state (e.g. for live ticks); for now,
ephemeral is safer and easier to reason about.
"""
from __future__ import annotations
from typing import Optional

from identity_registry import IdentityRegistry
from fl_registry_seed import seed_from_fl_response
from kalshi_registry_seed import seed_kalshi_records


def pair_via_registry(sport: str,
                       fl_response: dict,
                       kalshi_records: list) -> dict:
    """Compute fixture→ticker pairings via a fresh registry.

    Steps:
      1. Build an empty IdentityRegistry.
      2. Seed FL teams / competitions / fixtures from `fl_response`
         (the FlashLive events-list shape — `{'DATA': [...]}`).
      3. Seed Kalshi pairings via the three-tier matcher (strict →
         alias_table → guarded_fuzzy on still-unpaired records).
         Per_leg records resolve to their parent fixture; outright
         records are skipped.
      4. For every FL event in the response, recover the canonical
         fixture, then look up every alias that points at it under
         source='kalshi'. Build the result dict mapping FL event_id
         → list of Kalshi event_tickers.

    Returns:
        {
          fl_event_id: [kalshi_ticker, ...],   # ordered insertion-stably
          ...
        }

    FL events that don't seed (missing teams or start_time) get
    empty lists. Kalshi records that don't pair to any fixture
    contribute nothing to the result; they're recoverable via
    `seed_kalshi_records`'s stats dict if a caller wants them.
    """
    registry = IdentityRegistry()
    seed_from_fl_response(registry, fl_response, sport)
    seed_kalshi_records(registry, kalshi_records, sport)

    pairings: dict = {}
    data = fl_response.get("DATA") or []
    if not isinstance(data, list):
        return pairings

    for tournament in data:
        if not isinstance(tournament, dict):
            continue
        events = tournament.get("EVENTS") or []
        if not isinstance(events, list):
            continue
        for ev in events:
            if not isinstance(ev, dict):
                continue
            fl_event_id = (ev.get("EVENT_ID") or "").strip()
            if not fl_event_id:
                continue
            # Resolve FL event_id to canonical fixture via the alias
            # written by fl_registry_seed.
            fixture = registry.resolve_through_alias("fl", fl_event_id)
            if fixture is None or not fixture.id.startswith("fixture:"):
                pairings[fl_event_id] = []
                continue
            kalshi_aliases = registry.find_aliases_to(
                fixture.id, source="kalshi",
            )
            pairings[fl_event_id] = [a.external_id
                                       for a in kalshi_aliases]
    return pairings


def diff_pairings(v2_pairings: dict,
                   registry_pairings: dict) -> dict:
    """Compute a diff between two pairing dicts of the same shape.

    Both sides are `{fl_event_id: [kalshi_ticker, ...]}`. Returns:

        {
          'identical_count':    int,  # FL events where both sides
                                      # mapped to the same set of
                                      # tickers (ignoring order)
          'v2_only_pairings':   [
              {'fl_event_id': str,
               'v2_only_tickers': [...]},   # in v2 but not registry
              ...
          ],
          'registry_only_pairings': [
              {'fl_event_id': str,
               'registry_only_tickers': [...]}, # in registry but not v2
              ...
          ],
          'mixed_pairings': [
              {'fl_event_id': str,
               'shared': [...],
               'v2_only': [...],
               'registry_only': [...]},  # both sides have entries
                                          # but they differ
              ...
          ],
        }

    Used by the `/api/_debug/registry_diff` endpoint to validate
    the registry approach against v2 on production data before
    request-time wiring promotes it.
    """
    out = {
        "identical_count":         0,
        "v2_only_pairings":        [],
        "registry_only_pairings":  [],
        "mixed_pairings":          [],
    }
    all_keys = set(v2_pairings) | set(registry_pairings)
    for fl_id in sorted(all_keys):
        v2_set = set(v2_pairings.get(fl_id) or [])
        rg_set = set(registry_pairings.get(fl_id) or [])
        if v2_set == rg_set:
            out["identical_count"] += 1
            continue
        v2_only = v2_set - rg_set
        rg_only = rg_set - v2_set
        if v2_set and not rg_set:
            out["v2_only_pairings"].append({
                "fl_event_id":     fl_id,
                "v2_only_tickers": sorted(v2_only),
            })
        elif rg_set and not v2_set:
            out["registry_only_pairings"].append({
                "fl_event_id":           fl_id,
                "registry_only_tickers": sorted(rg_only),
            })
        else:
            out["mixed_pairings"].append({
                "fl_event_id":   fl_id,
                "shared":        sorted(v2_set & rg_set),
                "v2_only":       sorted(v2_only),
                "registry_only": sorted(rg_only),
            })
    return out
