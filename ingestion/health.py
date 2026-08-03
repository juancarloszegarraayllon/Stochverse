"""Ingestion task health registry — module-level state for
`/api/ingestion_status` visibility.

Load-bearing observability layer surfaced by the 2026-08-01 → 08-03
Kalshi persistence outage: task was alive but blocked for ~58h on an
unbounded await; the only externally-visible signal was the absence
of `last_seen_at` fossils, spotted by manual DB query. This registry
is the aggregate liveness endpoint that turns the NEXT silent death
into a 5-minute detection instead of a 2-day investigation.

Design shape:
  - Module-level `TASK_HEALTH: dict[str, dict]` — plain dict, mutated
    in place from every task's own asyncio context. No cross-thread
    writes (all writers are async tasks on the FastAPI event loop),
    so no lock needed. `async_session` is the discipline for DB
    concurrency; this dict is single-writer-per-key by convention.
  - Per-task entry shape documented in the module docstring below;
    schema evolution allowed additively via new keys.
  - State strings (not enum) for schema-drift tolerance across
    ingestion/monitoring version skew.

Update sites (Layer C):
  - `supervise()` in ingestion/base.py writes state on each
    transition (start, complete, crash, cancelled).
  - Each ingestion pass's success point (e.g. inside
    kalshi._ingest_pass post-commit) writes `last_pass_complete_ts`.
  - The four main.py bare-create_task loops (F104-F107) write their
    own health from within each iteration's success path.

Layer A will add `NotLockHolder` transitions (`not_holder_polling`
state) once the sentinel + supervise re-race branch land.
"""
from __future__ import annotations

import os
import time
from typing import Any


# Per-task entry shape — the ONE contract every writer must respect.
# Missing keys default to `None` at read time; add new keys freely.
#     {
#         "name":                     str,       # matches supervise's `name` arg
#         "state":                    str,       # see STATE_* constants below
#         "last_pass_complete_ts":    float,     # unix seconds; 0.0 if never
#         "staleness_threshold_sec":  int,       # per-task alert threshold
#         "recent_crashes_window":    int,       # from supervise counter
#         "attempt":                  int,       # supervise iteration count
#         "last_error_class":         str | None,
#         "last_error_msg":           str | None,
#     }
TASK_HEALTH: dict[str, dict] = {}


# State strings — enumerate here for grep discoverability. Not an
# enum type because monitoring (which may be on a different deploy
# cadence) shouldn't fail on an unknown state string.
STATE_UNKNOWN                 = "unknown"
STATE_HOLDER_RUNNING          = "holder_running"
STATE_NOT_HOLDER_POLLING      = "not_holder_polling"  # Layer A
STATE_CRASHED_RESTARTING      = "crashed_restarting"
STATE_DEAD                    = "dead"                # supervise clean-returned
STATE_CANCELLED               = "cancelled"


# Per-task staleness thresholds (seconds since last successful pass).
# Monitoring alerts on `seconds_since_last_pass > threshold`.
# Values sized to task cadence × 3-4 (buffer for transient hiccups
# without false alarms). All env-tunable via
# `INGESTION_STALENESS_<task>_S`.
def _threshold(name: str, default_sec: int) -> int:
    """Env-tunable staleness threshold per task name."""
    env_key = f"INGESTION_STALENESS_{name.upper()}_S"
    try:
        return int(os.environ.get(env_key, default_sec))
    except (TypeError, ValueError):
        return default_sec


DEFAULT_STALENESS_THRESHOLDS_S: dict[str, int] = {
    "fl":                 300,   # FL polls every 5s live / 60s idle → 5min ceiling
    "kalshi":             120,   # 30s pass cadence × 4 = 120s
    "score_flush":         90,   # 30s cadence × 3 = 90s
    "price_prune":       7200,   # hourly × 2 = 7200s
    "bracket_walk":      3600,   # 30-min steady-state × 2 = 3600s
    "multi_stage_disc":  5400,   # 30-min cycle × 3 = 5400s
}


def register(name: str) -> None:
    """Initialize a task's health entry. Idempotent — a second call
    with the same name preserves the existing state.

    Called on task startup (typically the first line of the run()
    wrapper or supervise's first iteration) so `/api/ingestion_status`
    surfaces the task before its first pass completes."""
    if name in TASK_HEALTH:
        return
    TASK_HEALTH[name] = {
        "name":                     name,
        "state":                    STATE_UNKNOWN,
        "last_pass_complete_ts":    0.0,
        "staleness_threshold_sec":  _threshold(
            name, DEFAULT_STALENESS_THRESHOLDS_S.get(name, 300),
        ),
        "recent_crashes_window":    0,
        "attempt":                  0,
        "last_error_class":         None,
        "last_error_msg":           None,
    }


def stamp_pass_complete(name: str) -> None:
    """Update `last_pass_complete_ts` to now. Called from each task's
    per-iteration success point (post-commit in ingestion, per-cycle
    in main.py loops). Auto-registers the task if not already present
    so the registry stays populated even if a future task forgets to
    call `register()` at startup."""
    if name not in TASK_HEALTH:
        register(name)
    TASK_HEALTH[name]["last_pass_complete_ts"] = time.time()
    # A successful pass unambiguously means the task is running as
    # the holder (bare-loop tasks don't have a non-holder mode; for
    # supervised tasks, non-holder state is only entered via Layer A's
    # NotLockHolder path which never calls stamp_pass_complete).
    TASK_HEALTH[name]["state"] = STATE_HOLDER_RUNNING


def set_state(name: str, state: str, **extras: Any) -> None:
    """Update the state field + any extra fields. Auto-registers.
    Called by supervise() on transitions and by future Layer-A
    NotLockHolder handling."""
    if name not in TASK_HEALTH:
        register(name)
    TASK_HEALTH[name]["state"] = state
    for k, v in extras.items():
        TASK_HEALTH[name][k] = v


def snapshot() -> list[dict]:
    """Return a list-of-dicts snapshot for the /api/ingestion_status
    endpoint. Computes `seconds_since_last_pass` and `stale` flag
    at read time — stored `last_pass_complete_ts` is the durable
    stamp, everything else derived."""
    now = time.time()
    out: list[dict] = []
    for name, entry in TASK_HEALTH.items():
        last_ts = entry.get("last_pass_complete_ts") or 0.0
        secs = (now - last_ts) if last_ts > 0 else None
        threshold = entry.get("staleness_threshold_sec") or 0
        stale = (
            secs is not None
            and threshold > 0
            and secs > threshold
        )
        item = dict(entry)
        item["seconds_since_last_pass"] = secs
        item["stale"] = stale
        out.append(item)
    return out


def get_entry(name: str) -> dict | None:
    """Get a single task's raw entry (for /api/kalshi_status nicety
    mirror). Returns None if never registered."""
    return TASK_HEALTH.get(name)
