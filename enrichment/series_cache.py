"""Throttled persistence of _SERIES_TO_STAGE_CACHE to cache_blobs.

The save throttle uses a module-level sentinel that's rebound via
`global` inside _maybe_save_series_cache. `global` is module-
scoped, so the sentinel + the function must live in the same module
or the rebind would silently shadow the import.
"""
import asyncio
import time
from caches.state import _SERIES_TO_STAGE_CACHE


_series_cache_last_save_ts: float = 0.0
_SERIES_CACHE_SAVE_INTERVAL_S: float = 60.0  # at most once per minute


def _maybe_save_series_cache():
    """Throttled save of _SERIES_TO_STAGE_CACHE. Called from each
    write site. Cheap when not due to save (timestamp compare only).
    Schedules an async DB write when due, doesn't block the caller."""
    global _series_cache_last_save_ts
    now = time.time()
    if now - _series_cache_last_save_ts < _SERIES_CACHE_SAVE_INTERVAL_S:
        return
    _series_cache_last_save_ts = now
    try:
        from db import save_cache_blob
        snapshot = dict(_SERIES_TO_STAGE_CACHE)
        asyncio.create_task(save_cache_blob("series_to_stage", snapshot))
    except Exception:
        pass
