"""Throttled persistence of team-id and team-pair caches to cache_blobs.

Mirrors the shape of enrichment/series_cache.py — module-level
sentinel rebound via `global` inside _maybe_save_team_caches. The
sentinel + the function must live in the same module or the rebind
silently shadows the import.

Two blobs because the caches are independent:
  - team_ids        → _TEAM_ID_CACHE         (name → team_id)
  - team_pair_events → _TEAM_PAIR_EVENT_CACHE (pair → past event_id)

Saved together on the same throttle to keep the call sites simple
(both writes happen inside the H2H resolution chain).
"""
import asyncio
import time
from caches.state import _TEAM_ID_CACHE, _TEAM_PAIR_EVENT_CACHE


_team_caches_last_save_ts: float = 0.0
_TEAM_CACHES_SAVE_INTERVAL_S: float = 60.0  # at most once per minute


def _maybe_save_team_caches():
    """Throttled save of both team caches. Called from each write
    site. Cheap when not due to save (timestamp compare only).
    Schedules an async DB write when due, doesn't block the caller."""
    global _team_caches_last_save_ts
    now = time.time()
    if now - _team_caches_last_save_ts < _TEAM_CACHES_SAVE_INTERVAL_S:
        return
    _team_caches_last_save_ts = now
    try:
        from db import save_cache_blob
        ids_snapshot = dict(_TEAM_ID_CACHE)
        pair_snapshot = dict(_TEAM_PAIR_EVENT_CACHE)
        asyncio.create_task(save_cache_blob("team_ids", ids_snapshot))
        asyncio.create_task(
            save_cache_blob("team_pair_events", pair_snapshot)
        )
    except Exception:
        pass
