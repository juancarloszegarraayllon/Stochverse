"""Shared ingestion primitives — protocols, helpers, supervisor.

Per architecture v1.3 §6.1: each provider module shares a common
interface. Per §6.3: ingestion is idempotent (UPSERT keyed on the
provider's primary identifier; raw_payload updated only when content
actually changed via hash comparison). Per §10.1: portability via
SQLAlchemy abstractions and Postgres advisory locks.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from observability import get_logger


_log = get_logger("ingestion")


# ── Types ────────────────────────────────────────────────────────

@dataclass
class IngestionScope:
    """What to fetch on a single pass.

    Generic shape so the same dispatcher can drive different
    providers. Per-provider ingestion modules narrow to what they
    care about.
    """
    sport_ids: list[int] = field(default_factory=list)
    indent_days_range: tuple[int, int] = (0, 0)
    timezone_offset: int = 0
    endpoints: list[str] = field(default_factory=list)


@dataclass
class IngestionResult:
    """Counters returned by a single ingestion pass.

    Logged at the end of each pass; informs metrics on insert /
    update / unchanged rates per provider.
    """
    fetched: int = 0          # API calls made successfully
    failed: int = 0           # API calls that errored
    inserted: int = 0         # new rows written
    updated: int = 0          # existing rows whose payload changed
    unchanged: int = 0        # existing rows whose payload was identical
    schema_drift: int = 0     # validation failures
    duration_ms: int = 0


@dataclass
class ProviderHealth:
    """Snapshot of a provider's recent health.

    Used by the serving layer to set freshness flags and by
    /healthz-style endpoints to surface ingestion liveness.
    """
    name: str
    healthy: bool
    last_success_at: Optional[float] = None
    last_error: Optional[str] = None
    consecutive_failures: int = 0


# ── Payload hashing & UPSERT ─────────────────────────────────────

def payload_hash(raw: Any) -> str:
    """Stable sha256 over canonical-JSON of the payload.

    Used to detect when a provider's response for a given record has
    actually changed vs. just been refreshed. last_changed_at is
    only bumped when the hash differs.

    Canonical: sort keys, no extra whitespace, ensure_ascii=False so
    non-Latin team names hash to the same bytes regardless of how
    the API serialized them.
    """
    canonical = json.dumps(
        raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def upsert_provider_record(
    session: AsyncSession,
    table,
    *,
    primary_key: dict[str, Any],
    fields: dict[str, Any],
    raw: Any,
) -> str:
    """Single-record UPSERT — wrapper around upsert_provider_records_batch.

    Returns one of: 'inserted' | 'updated' | 'unchanged'.

    Prefer the batch variant for ingestion paths; this exists for
    callers that genuinely have one record at a time.
    """
    inserted, updated, unchanged = await upsert_provider_records_batch(
        session, table,
        [{"pk": primary_key, "fields": fields, "raw": raw}],
    )
    if inserted:
        return "inserted"
    if updated:
        return "updated"
    return "unchanged"


async def upsert_provider_records_batch(
    session: AsyncSession,
    table,
    records: list[dict[str, Any]],
) -> tuple[int, int, int]:
    """Multi-row UPSERT with hash-based change detection.

    Each record is shaped: ``{"pk": {col: val}, "fields": {col: val, ...}, "raw": {...}}``.
    Returns ``(inserted_count, updated_count, unchanged_count)``.

    Strategy (architecture §6.3 idempotency, optimized for batches):
      1. Single SELECT to fetch existing payload_hash for every PK in
         the batch. Round trip #1.
      2. Per-record classification in Python: insert (no existing) /
         update (hash differs) / unchanged (hash matches). Counter
         accuracy comes from this comparison, not from RETURNING tricks.
      3. Single multi-row INSERT ... ON CONFLICT DO UPDATE for the
         entire batch. Postgres handles per-row conflict resolution.
         Round trip #2. raw_payload + last_changed_at gated on hash
         change via SQL CASE clauses; last_seen_at always bumps.

    Round-trip count is constant (2) regardless of batch size — the
    big win vs. per-record UPSERT which is N round trips. For Phase 1B
    FL passes this drops 150-275s wall time to a few seconds.

    Constraint: assumes a single-column primary key. All current
    provider tables (fl_events.fl_event_id, kalshi_markets.ticker,
    polymarket_markets.condition_id, oddsapi_events.oddsapi_id)
    satisfy this.
    """
    if not records:
        return (0, 0, 0)

    # Single-column PK assumption — derived from first record's pk dict.
    pk_keys = list(records[0]["pk"].keys())
    if len(pk_keys) != 1:
        raise ValueError(
            "upsert_provider_records_batch supports single-column PKs only; "
            f"got {pk_keys}"
        )
    pk_col = pk_keys[0]
    pk_attr = getattr(table, pk_col)

    # Step 1: fetch existing payload hashes for the batch's PKs.
    pk_values = [r["pk"][pk_col] for r in records]
    existing_rows = await session.execute(
        select(pk_attr, table.payload_hash).where(pk_attr.in_(pk_values))
    )
    existing_hashes = {row[0]: row[1] for row in existing_rows.all()}

    # Step 2: classify + build the multi-row VALUES list.
    inserted_count = 0
    updated_count = 0
    unchanged_count = 0
    rows: list[dict[str, Any]] = []

    for r in records:
        h = payload_hash(r["raw"])
        pk_val = r["pk"][pk_col]
        old_hash = existing_hashes.get(pk_val)

        if old_hash is None:
            inserted_count += 1
        elif old_hash == h:
            unchanged_count += 1
        else:
            updated_count += 1

        row = {
            **r["pk"],
            **r["fields"],
            "raw_payload": r["raw"],
            "payload_hash": h,
        }
        rows.append(row)

    if not rows:
        return (0, 0, 0)

    # Step 3: multi-row UPSERT.
    stmt = pg_insert(table.__table__).values(rows)

    # Build update_cols once (column references resolve via stmt.excluded).
    update_cols: dict[str, Any] = {
        col: stmt.excluded[col]
        for col in records[0]["fields"].keys()
    }
    update_cols["last_seen_at"] = text("NOW()")
    update_cols["raw_payload"] = text(
        f"CASE WHEN {table.__tablename__}.payload_hash = excluded.payload_hash "
        f"THEN {table.__tablename__}.raw_payload ELSE excluded.raw_payload END"
    )
    update_cols["payload_hash"] = stmt.excluded.payload_hash
    update_cols["last_changed_at"] = text(
        f"CASE WHEN {table.__tablename__}.payload_hash = excluded.payload_hash "
        f"THEN {table.__tablename__}.last_changed_at ELSE NOW() END"
    )

    stmt = stmt.on_conflict_do_update(
        index_elements=[pk_col],
        set_=update_cols,
    )
    await session.execute(stmt)

    return (inserted_count, updated_count, unchanged_count)


# ── Postgres advisory lock for singleton enforcement ─────────────
#
# Architecture v1.3 §10.1: portability-friendly singleton via
# pg_try_advisory_lock. Each ingestion module gets a fixed integer
# key; the lock is held for the connection's lifetime. With
# WEB_CONCURRENCY=2, both workers race to acquire — only one wins,
# the other's poller exits cleanly without doing duplicate work.

# Stable integer keys per ingestion module. Picked from a private
# range so they don't collide with anything else using advisory
# locks. Treat as opaque — the values themselves don't matter, only
# uniqueness.
ADVISORY_LOCK_FL = 0x5350_F100   # 'SP' \xF1 \x00 — FL ingestion
ADVISORY_LOCK_KALSHI = 0x5350_F101
ADVISORY_LOCK_POLYMARKET = 0x5350_F102
ADVISORY_LOCK_ODDSAPI = 0x5350_F103


async def try_acquire_advisory_lock(session: AsyncSession, key: int) -> bool:
    """Acquire a Postgres session-level advisory lock.

    Returns True if acquired, False if another connection holds it.
    The lock is automatically released when the session's underlying
    connection closes — no manual unlock needed in the happy path.

    Intended use: at the top of each ingestion task, on a long-lived
    session that the task owns. If False, the task exits immediately;
    the worker that holds the lock keeps polling.
    """
    result = await session.execute(
        text("SELECT pg_try_advisory_lock(:key)"), {"key": key},
    )
    return bool(result.scalar())


# ── Supervisor: restart-on-crash with exponential backoff ────────
#
# Architecture v1.3 §6.1: long-lived asyncio coroutines must be
# supervised. Bare asyncio.create_task() leaves a crashed task dead
# silently. The supervisor catches exceptions, logs with traceback,
# and restarts with exponential backoff (capped at 60s).

async def supervise(
    name: str,
    coro_factory: Callable[[], Awaitable[None]],
    *,
    max_backoff_sec: float = 60.0,
    crash_alert_threshold: int = 10,
    crash_alert_window_sec: float = 300.0,
) -> None:
    """Run `coro_factory()` forever; restart on crash with backoff.

    `coro_factory` is a zero-arg callable that returns a fresh
    coroutine each time — needed because a coroutine can only be
    awaited once. The factory pattern lets the supervisor make a new
    one per attempt.

    On clean return (e.g., explicit cancellation), exits without
    restart. On exception, logs full traceback, sleeps backoff,
    tries again. Repeated crashes within a window emit a louder
    structured log event so monitoring can alert.
    """
    backoff = 1.0
    crash_times: list[float] = []
    attempt = 0

    while True:
        attempt += 1
        try:
            _log.info(
                "ingestion.task.start",
                task=name,
                attempt=attempt,
            )
            await coro_factory()
            _log.info("ingestion.task.complete", task=name)
            return
        except asyncio.CancelledError:
            _log.info("ingestion.task.cancelled", task=name)
            raise
        except Exception as exc:
            now = time.monotonic()
            crash_times.append(now)
            crash_times[:] = [
                t for t in crash_times
                if now - t < crash_alert_window_sec
            ]
            recent_crashes = len(crash_times)
            level = "error" if recent_crashes >= crash_alert_threshold else "warning"
            log_fn = _log.error if level == "error" else _log.warning
            log_fn(
                "ingestion.task.crash",
                task=name,
                attempt=attempt,
                error_class=type(exc).__name__,
                error_msg=str(exc)[:500],
                recent_crashes=recent_crashes,
                next_backoff_sec=backoff,
                exc_info=True,
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff_sec)


# ── Run-id helper ────────────────────────────────────────────────

def new_run_id() -> uuid.UUID:
    """Stable UUID per ingestion run. Logged on every event so a full
    pass can be reconstructed by greping a single ID.
    """
    return uuid.uuid4()
