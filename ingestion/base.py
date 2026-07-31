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

from sqlalchemy import select, text, update
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
    """Multi-row UPSERT with hash-based change detection, client-side
    partitioned to keep unchanged-row raw_payload off the wire.

    Each record is shaped: ``{"pk": {col: val}, "fields": {col: val, ...}, "raw": {...}}``.
    Returns ``(inserted_count, updated_count, unchanged_count)``.

    Strategy (architecture §6.3 idempotency, optimized for batches; wire
    cost minimized per cost-investigation 2026-07-26 PR #2):
      1. Single SELECT to fetch existing payload_hash for every PK in
         the batch. Round trip #1.
      2. Per-record classification in Python: insert (no existing) /
         update (hash differs) / unchanged (hash matches). Partition
         the batch into `changed_rows` (need full UPSERT) and
         `unchanged_pks` (need only a last_seen_at bump).
      3a. Multi-row INSERT ... ON CONFLICT DO UPDATE for `changed_rows`
          only — skipped if none. Round trip #2. Because we only send
          rows whose payload_hash actually differs (or that don't yet
          exist), the previous CASE-gates on raw_payload / last_changed_at
          are unreachable and have been removed; every conflict now
          writes the new raw_payload and bumps last_changed_at = NOW().
      3b. Lightweight UPDATE table SET last_seen_at = NOW() WHERE pk IN
          (unchanged_pks) AND last_seen_at < NOW() - INTERVAL '1 hour'
          — skipped if none. Round trip #3. Preserves the re-resolution
          loop's freshness watermark (its candidate scan uses
          `WHERE last_seen_at > NOW() - INTERVAL '3d'`); a ticker whose
          content is byte-identical still gets its last_seen_at bumped
          hourly, well inside the 3-day watermark (71× margin). Sends
          only the PK list (~few bytes per row) instead of the full
          raw_payload (~12 KB per row for Kalshi). Task #22 added the
          hourly staleness gate — WAL cut ~99% vs the unconditional
          per-pass bump baseline.

    Round-trip count is 2 or 3 depending on the partition (always ≥2
    for a non-empty batch, at most 3). The per-row wire cost for the
    unchanged fraction drops from ~O(raw_payload) to ~O(PK) — for
    Kalshi's 30s cadence with ~5-7k tickers and typical unchanged
    fractions (70-90% during normal trading), this cuts the batch
    wire cost by 70-90% pass-over-pass.

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

    # Step 2: classify + partition. `changed_rows` carries full raw_payload
    # to the UPSERT; `unchanged_pks` gets only a last_seen_at bump.
    inserted_count = 0
    updated_count = 0
    unchanged_count = 0
    changed_rows: list[dict[str, Any]] = []
    unchanged_pks: list[Any] = []

    for r in records:
        h = payload_hash(r["raw"])
        pk_val = r["pk"][pk_col]
        old_hash = existing_hashes.get(pk_val)

        if old_hash is None:
            inserted_count += 1
            changed_rows.append({
                **r["pk"],
                **r["fields"],
                "raw_payload": r["raw"],
                "payload_hash": h,
            })
        elif old_hash == h:
            unchanged_count += 1
            unchanged_pks.append(pk_val)
        else:
            updated_count += 1
            changed_rows.append({
                **r["pk"],
                **r["fields"],
                "raw_payload": r["raw"],
                "payload_hash": h,
            })

    # Step 3a: multi-row UPSERT for changed rows only. CASE-gates from
    # the previous implementation are removed because every row in this
    # branch has a genuinely-different (or absent) payload_hash — the
    # "hash matched" arm is unreachable.
    if changed_rows:
        stmt = pg_insert(table.__table__).values(changed_rows)
        update_cols: dict[str, Any] = {
            col: stmt.excluded[col]
            for col in records[0]["fields"].keys()
        }
        update_cols["raw_payload"] = stmt.excluded.raw_payload
        update_cols["payload_hash"] = stmt.excluded.payload_hash
        update_cols["last_seen_at"] = text("NOW()")
        update_cols["last_changed_at"] = text("NOW()")
        stmt = stmt.on_conflict_do_update(
            index_elements=[pk_col],
            set_=update_cols,
        )
        await session.execute(stmt)

    # Step 3b: freshness-only UPDATE for unchanged rows. Wire cost is
    # the PK list, not raw_payload. Preserves the re-resolution
    # candidate-scan watermark (`last_seen_at > NOW() - INTERVAL 'Nd'`).
    #
    # STALENESS GATE (task #22, 2026-07-29): the additional
    # `last_seen_at < NOW() - INTERVAL '1 hour'` predicate skips rows
    # bumped within the last hour. Cuts Neon WAL churn ~99% (measured
    # baseline ~19M row versions/day / ~23 GB WAL/day for this
    # statement; post-gate steady state ~183k row versions/day) while
    # preserving the reresolution watermark's 3-day (72-hour) window
    # with 71× margin. Wire cost of the PK IN list (~150KB per pass,
    # ~432 MB/day) is unchanged — the gate runs SERVER-side, Postgres
    # evaluates per-row and writes only stale tuples. Client-side
    # gating (in-memory bump timestamps) would save wire too but adds
    # state that doesn't survive worker restart; SQL-side is simpler
    # and correct-by-construction.
    if unchanged_pks:
        tbl = table.__table__
        pk_column = tbl.c[pk_col]
        last_seen_col = tbl.c["last_seen_at"]
        await session.execute(
            update(tbl)
            .where(pk_column.in_(unchanged_pks))
            .where(last_seen_col < text("NOW() - INTERVAL '1 hour'"))
            .values(last_seen_at=text("NOW()"))
        )

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
# Task #21 Surface C (2026-07-30): singleton-enforce the two
# main.py-hosted background loops that were previously duplicated
# under WEB_CONCURRENCY=2.
ADVISORY_LOCK_SCORE_FLUSH = 0x5350_F104   # main._score_flush_loop
ADVISORY_LOCK_PRICE_PRUNE = 0x5350_F105   # main._price_prune_loop
# Day-62 standings-walk singletons — both loops previously ran on
# both WEB_CONCURRENCY=2 workers, doubling ~215k/day of standings
# calls. Aligned to Surface C's pattern.
ADVISORY_LOCK_BRACKET_WALK      = 0x5350_F106   # main._tournament_bracket_warm_loop
ADVISORY_LOCK_MULTI_STAGE_DISC  = 0x5350_F107   # main._multi_stage_discovery_loop


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
