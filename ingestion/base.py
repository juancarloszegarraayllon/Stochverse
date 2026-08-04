"""Shared ingestion primitives — protocols, helpers, supervisor.

Per architecture v1.3 §6.1: each provider module shares a common
interface. Per §6.3: ingestion is idempotent (UPSERT keyed on the
provider's primary identifier; raw_payload updated only when content
actually changed via hash comparison). Per §10.1: portability via
SQLAlchemy abstractions and Postgres advisory locks.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
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
    # Step 3b rowcount capture (Layer C, 2026-08-03): pre-fix, the
    # actual freshness-bump work was invisible — the returned
    # `unchanged_count` reflected CLIENT-SIDE hash classification (all
    # rows whose payload was byte-identical), not Postgres's write
    # count (which is gated at ~1h staleness server-side). A pass with
    # "unchanged=9500 upd=0 ins=0" was ambiguous between "everything
    # fresh, zero writes" and "everything stale-bumped, ~9500 writes"
    # — exactly the observability gap that let the Aug 1-3 Kalshi
    # persistence outage hide behind an in-window-appearing counter.
    # Now: capture and return the actual rowcount for Step 3b so
    # pass_complete log lines can surface it.
    freshness_bumped = 0
    if unchanged_pks:
        tbl = table.__table__
        pk_column = tbl.c[pk_col]
        last_seen_col = tbl.c["last_seen_at"]
        result = await session.execute(
            update(tbl)
            .where(pk_column.in_(unchanged_pks))
            .where(last_seen_col < text("NOW() - INTERVAL '1 hour'"))
            .values(last_seen_at=text("NOW()"))
        )
        # asyncpg / SQLAlchemy: result.rowcount reports the number of
        # rows the server touched. Guarded with `or 0` because some
        # dialects can return -1 when rowcount is unavailable.
        freshness_bumped = result.rowcount or 0

    return (inserted_count, updated_count, unchanged_count, freshness_bumped)


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


# ── NotLockHolder sentinel (Layer A, 2026-08-03) ───────────────
#
# The 2026-08-01 Kalshi incident-class fix. Pre-Layer-A, every
# advisory-lock-guarded task's non-holder path returned cleanly:
#
#     if not got_lock:
#         _log.info("skipping — another worker holds the lock")
#         return
#
# `supervise()` treated the clean return as `ingestion.task.complete`
# and EXITED without restart — the non-holder worker's task was DEAD
# from boot. Any subsequent holder-crash (via supervise-restart on
# a pooled connection reuse landing on a different backend, or via
# any other transient failure) would then non-deterministically leave
# BOTH workers with dead tasks — silent forever.
#
# Post-Layer-A, non-holder paths raise NotLockHolder. supervise()
# catches this SPECIFICALLY (before the generic Exception handler),
# updates health state to STATE_NOT_HOLDER_POLLING, sleeps
# NOT_HOLDER_POLL_S, and re-invokes coro_factory to re-race for the
# lock. Preserves a LIVE non-holder task that periodically re-races.

class NotLockHolder(Exception):
    """Signal from an advisory-lock-guarded task's run() that it lost
    the acquire race. supervise() catches this specifically and enters
    a slow-poll re-race schedule instead of exiting or exponential-
    backoff. Preserves a live non-holder task that periodically re-
    races for the lock, so a holder-death (crash-path or pool-leaked-
    lock) doesn't leave the workload permanently dead on both workers.
    Root cause the 2026-08-01 Kalshi incident exposed."""


# ── LockGripLost sentinel (Layer E, 2026-08-05) ────────────────
#
# Day-65 mutual-exclusion failure post-Layer-B: BOTH workers reported
# holder_running for all AL surfaces with independently-advancing
# stamps + fl_last_error=InterfaceError("connection is closed") on
# the AL session. Mechanism (Layer A+B+D interaction class):
#
#   1. NullPool AL session left BEGIN-open after try_acquire (no
#      commit). Server-side idle_in_transaction_session_timeout=60s
#      terminates the connection. Session-level advisory lock releases.
#      → Fixed by Layer E.1 (commit-after-acquire).
#
#   2. Even with the commit, Neon proxy silently reaps TCP-idle
#      connections after ~5-10min. Same outcome: connection dies,
#      lock releases, dead-holder loop keeps stamping.
#      → Fixed by Layer E.2 (heartbeat + LockGripLost + supervise re-race).
#
# A holder that can't prove its grip must stop being a holder.
# Heartbeat runs SELECT 1 on the AL session every AL_HEARTBEAT_INTERVAL_S
# seconds. On failure (InterfaceError / OperationalError / any exception
# from execute), heartbeat cancels the parent task, which unwinds and
# raises LockGripLost. supervise() catches LockGripLost as a peer of
# NotLockHolder (before generic Exception), logs distinctly, and re-races.

AL_HEARTBEAT_INTERVAL_S: float = float(
    os.environ.get("AL_HEARTBEAT_INTERVAL_S", "20.0")
)


class LockGripLost(Exception):
    """Signal from a holder's heartbeat that the advisory-lock session
    lost its grip on the underlying Postgres connection (heartbeat
    SELECT 1 raised InterfaceError / OperationalError / connection-
    closed → session-level advisory lock has released silently on the
    server side). supervise() catches this specifically (peer of
    NotLockHolder, before generic Exception) and re-races for the
    lock. Root cause the 2026-08-05 dual-holder incident exposed."""


async def _al_heartbeat_body(
    session,
    lock_key: int,
    log_name: str,
    parent_task: asyncio.Task,
    grip_ref: dict,
) -> None:
    """Heartbeat body — every AL_HEARTBEAT_INTERVAL_S, SELECT 1 on the
    AL session. On failure, stamp grip_ref and cancel parent_task.

    SELECT 1 serves two purposes: (a) proves the TCP connection +
    backend are alive; (b) keeps the session non-idle so Neon proxy /
    intermediate load balancers don't reap the connection during a
    long quiet period between real queries. E.1's commit-after-acquire
    closes the server-side idle-in-txn fuse (60s); this keepalive
    closes the proxy-side TCP-idle fuse (~5-10min).

    Exits cleanly on CancelledError (parent teardown). Exits after
    stamping grip_ref on any other exception (grip lost — parent will
    unwind and raise LockGripLost via the holder_heartbeat CM)."""
    _log = get_logger(f"al_heartbeat.{log_name}")
    while True:
        try:
            await asyncio.sleep(AL_HEARTBEAT_INTERVAL_S)
            await session.execute(text("SELECT 1"))
        except asyncio.CancelledError:
            return
        except Exception as exc:
            _log.warning(
                "al_heartbeat.grip_lost",
                task=log_name,
                lock_key=hex(lock_key),
                error_class=type(exc).__name__,
                error_msg=str(exc)[:200],
            )
            grip_ref["lost"] = True
            grip_ref["error"] = exc
            if not parent_task.done():
                parent_task.cancel()
            return


@contextlib.asynccontextmanager
async def holder_heartbeat(session, lock_key: int, log_name: str):
    """Async context manager: spawn the AL-session heartbeat while the
    body runs, cancel it cleanly on exit, and translate heartbeat-
    driven parent cancellation into a `LockGripLost` raise.

    Usage:
        async with holder_heartbeat(lock_session, ADVISORY_LOCK_X, "x"):
            while True:
                ...  # per-pass work (bounded by wait_for)

    Semantics:
      - Normal completion of the body → heartbeat cancelled cleanly.
      - Exception inside the body → heartbeat cancelled; exception
        propagates unchanged.
      - Heartbeat detects grip loss → heartbeat cancels the parent
        task → the body's next await sees CancelledError → this CM
        checks grip_ref and re-raises as LockGripLost (which supervise
        catches distinctly).
      - External cancellation (supervise teardown) → CancelledError
        propagates unchanged (grip_ref not stamped → no translation).
    """
    parent_task = asyncio.current_task()
    if parent_task is None:
        raise RuntimeError(
            "holder_heartbeat must be entered from within an asyncio task",
        )
    grip_ref: dict = {"lost": False, "error": None}
    hb_task = asyncio.create_task(
        _al_heartbeat_body(session, lock_key, log_name, parent_task, grip_ref),
        name=f"al_heartbeat.{log_name}",
    )
    try:
        yield
    except asyncio.CancelledError:
        if grip_ref["lost"]:
            err = grip_ref["error"]
            raise LockGripLost(
                f"al_heartbeat detected grip loss task={log_name} "
                f"error_class={type(err).__name__} "
                f"error={str(err)[:200]}"
            ) from err
        raise
    finally:
        hb_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await hb_task


# ── Layer D (2026-08-03): pass-timeout tiers + boot-path bound ─
#
# Actual fix for the 2026-08-01 Kalshi incident class: task alive
# but blocked on an unbounded await for ~58h. Audit at PR-Layer-D
# quoted six unbounded awaits in the pass path (Step 1 payload_hash
# SELECT, Step 3a UPSERT, Step 3b freshness UPDATE, session.commit,
# session.rollback, session-context acquire) — any of which can
# hang forever on a dead TCP socket. Postgres server-side
# statement_timeout=60s only protects against RUNAWAY QUERIES, not
# against a socket that stops delivering bytes (Neon compute
# autoscale-to-zero + TCP-idle-timeout by an intermediate proxy is
# a documented scenario).
#
# TWO-TIER PASS CEILING (per operator amendment from PR #283
# deploy tuning): first pass after task (re)start gets a longer
# boot ceiling (default 300s) because catch-up work after a
# restart can legitimately take longer (measured 77.6s post-#283
# on a slow-boot kalshi pass = 65% of the pre-tuning 120s ceiling;
# a healthy slow-boot must not trip the watchdog into a kill/
# restart/slow-boot loop). Subsequent passes get the steady
# ceiling (120s = ~4× normal 30s pass duration).
#
# D.1b — BOOT PATH: run()'s lock-session acquire + try_acquire
# is ALSO wrapped in wait_for. The Aug 1 death confirmed the hang
# window as [00:36, ~01:29] which is EITHER boot-path or first-
# pass — the boot-path bound closes the boot flavor regardless.
INGESTION_PASS_TIMEOUT_STEADY_S: float = float(
    os.environ.get("INGESTION_PASS_TIMEOUT_STEADY_S", "120.0")
)
INGESTION_PASS_TIMEOUT_BOOT_S: float = float(
    os.environ.get("INGESTION_PASS_TIMEOUT_BOOT_S", "300.0")
)
INGESTION_BOOT_TIMEOUT_S: float = float(
    os.environ.get("INGESTION_BOOT_TIMEOUT_S", "60.0")
)


def pass_timeout_for(is_first_pass: bool) -> float:
    """Return the wait_for ceiling for a pass. Callers track their
    own `first_pass: bool` local (True until the first pass completes,
    then False for the rest of the loop's lifetime — supervise-restart
    creates a fresh call and resets first_pass to True)."""
    return INGESTION_PASS_TIMEOUT_BOOT_S if is_first_pass else INGESTION_PASS_TIMEOUT_STEADY_S


async def acquire_lock_session_bounded(
    session_factory,
    lock_key: int,
    *,
    log_name: str,
    logger=None,
    timeout_s: float | None = None,
    use_null_pool: bool = True,
):
    """Bounded session-acquire + advisory-lock-acquire (Layer D.1b).

    Wraps the whole boot-path in asyncio.wait_for(INGESTION_BOOT_TIMEOUT_S).
    Timeout raises TimeoutError → propagates to supervise → crash-restart.

    Layer B (2026-08-04): `use_null_pool=True` (default) redirects the
    lock-holding session to `db.advisory_lock_session` — a NullPool-
    backed engine that closes the connection on session __aexit__ AND
    on garbage-collection of an orphaned session_cm. Advisory locks
    release IMMEDIATELY when the session drops, closing the "pool-
    leaked-lock" flavor of the 2026-08-01 death class as defense-in-
    depth. Caller passes its usual `session_factory` (pooled) — the
    helper swaps to `advisory_lock_session` internally when
    use_null_pool is True and the NullPool engine is available.
    Set `use_null_pool=False` to force the caller-provided factory
    (test seams).

    Returns `(session_cm, session, got_lock)`. Caller MUST call
    `await session_cm.__aexit__(None, None, None)` in a finally block
    to release the session's connection when done (long-lived lock
    sessions live for the loop's lifetime; on exception or
    NotLockHolder, exit unwinds cleanly).

    Shape mirrors kalshi.run / fl.run explicit context-manager usage
    rather than a plain `async with` because the wait_for wrapping
    needs to bound the entire acquire operation, not just the
    try_acquire call."""
    if logger is None:
        logger = _log
    if timeout_s is None:
        timeout_s = INGESTION_BOOT_TIMEOUT_S

    # Resolve the lock-holding factory. Import inside the function
    # so tests can stub `db.advisory_lock_session` without needing
    # module-level import gymnastics.
    lock_factory = session_factory
    if use_null_pool:
        try:
            from db import advisory_lock_session as _al
            if _al is not None:
                lock_factory = _al
        except Exception:
            # No DB available or import failed — fall back to
            # caller's factory (memory-only / test paths).
            pass

    async def _do_acquire():
        session_cm = lock_factory()
        session = await session_cm.__aenter__()
        try:
            got_lock = await try_acquire_advisory_lock(session, lock_key)
            if got_lock:
                # Layer E.1 (2026-08-05): commit-after-acquire.
                # SQLAlchemy async_session runs `session.execute()` inside
                # an implicit BEGIN that stays open until the caller
                # commits/rollbacks. Post-Layer-B on NullPool the AL
                # session was left BEGIN-open forever — the server-side
                # `idle_in_transaction_session_timeout=60000` (db.py:75)
                # then TERMINATED the connection after 60s, silently
                # releasing the session-level advisory lock. The other
                # worker's Layer-A supervise re-race then acquired the
                # now-free lock → dual holders (Day-64 evidence).
                #
                # Postgres session-level advisory locks are transaction-
                # independent (only pg_advisory_xact_lock is txn-scoped).
                # Committing here ends the implicit txn; the lock persists
                # until the connection closes. Defense against server-
                # side idle-in-txn kill. E.2 heartbeat additionally
                # defends against Neon-proxy TCP-idle reap (~5-10min).
                await session.commit()
        except Exception:
            await session_cm.__aexit__(None, None, None)
            raise
        return session_cm, session, got_lock

    try:
        return await asyncio.wait_for(_do_acquire(), timeout=timeout_s)
    except asyncio.TimeoutError:
        # printf-style format works with BOTH stdlib logging and
        # structlog — the helper is called from mixed logger contexts
        # (main.py's F104-F107 use stdlib loggers; ingestion.* uses
        # structlog via observability.get_logger).
        logger.warning(
            "ingestion.task.boot_timeout task=%s timeout_sec=%s note=%s",
            log_name, timeout_s,
            "lock-session acquire hung; raising to supervise for restart",
        )
        raise


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
    # Layer C (2026-08-03): register task in the health registry
    # BEFORE the first attempt so /api/ingestion_status surfaces it
    # from the moment the task starts, not only after the first
    # successful pass. The registry is single-writer-per-key by
    # convention; supervise owns the state transitions, per-pass
    # code owns last_pass_complete_ts.
    from . import health as _health
    _health.register(name)

    backoff = 1.0
    crash_times: list[float] = []
    attempt = 0

    # Slow-poll interval between non-holder re-race attempts (Layer A).
    # 60s default: fast enough that a holder-death recovery lands
    # within the same operator-visible window (staleness thresholds
    # for the two supervised tasks are 300s and 120s), slow enough
    # that the re-race doesn't spam Postgres advisory-lock traffic.
    not_holder_poll_s = float(
        os.environ.get("INGESTION_NOT_HOLDER_POLL_S", "60.0")
    )

    while True:
        attempt += 1
        try:
            _log.info(
                "ingestion.task.start",
                task=name,
                attempt=attempt,
            )
            _health.set_state(
                name, _health.STATE_STARTING,
                attempt=attempt,
            )
            await coro_factory()
            # A clean return post-Layer-A is either (a) a task designed
            # to complete (none currently exist) or (b) a bug in a
            # non-holder path that still uses `return` instead of
            # `raise NotLockHolder`. Either way surface as `dead`.
            _log.info("ingestion.task.complete", task=name)
            _health.set_state(name, _health.STATE_DEAD)
            return
        except asyncio.CancelledError:
            _log.info("ingestion.task.cancelled", task=name)
            _health.set_state(name, _health.STATE_CANCELLED)
            raise
        except NotLockHolder:
            # Layer A: non-holder yields for `not_holder_poll_s` then
            # re-races. Distinct log line so monitoring can distinguish
            # legitimate non-holder-yield (benign, expected under
            # WEB_CONCURRENCY=2) from crash-restart (indicative).
            _log.info(
                "ingestion.task.not_holder_yield",
                task=name,
                attempt=attempt,
                retry_in_sec=not_holder_poll_s,
            )
            _health.set_state(
                name, _health.STATE_NOT_HOLDER_POLLING,
                attempt=attempt,
            )
            await asyncio.sleep(not_holder_poll_s)
            # Reset backoff — non-holder yield is NOT a crash and
            # shouldn't compound exponential backoff on subsequent
            # transient crashes.
            backoff = 1.0
            # fall through to next iteration of while True — re-race.
            continue
        except LockGripLost as exc:
            # Layer E: heartbeat detected the AL session lost its grip
            # (Neon proxy TCP-idle reap, DBA-side pg_terminate_backend,
            # or connection drop). Session-level advisory lock has
            # released silently server-side. Yield same as NotLockHolder
            # so the sibling worker (or this one on re-race) can pick
            # up cleanly — grip loss is an infrastructural signal, NOT
            # counted against crash_alert_threshold. Distinct log line
            # + STATE_NOT_HOLDER_POLLING with last_error_class=LockGripLost
            # so operators can tell grip-loss re-race apart from clean
            # not-holder yield.
            _log.warning(
                "ingestion.task.grip_lost",
                task=name,
                attempt=attempt,
                error_msg=str(exc)[:200],
                retry_in_sec=not_holder_poll_s,
            )
            _health.set_state(
                name, _health.STATE_NOT_HOLDER_POLLING,
                attempt=attempt,
                last_error_class="LockGripLost",
                last_error_msg=str(exc)[:500],
            )
            await asyncio.sleep(not_holder_poll_s)
            backoff = 1.0
            continue
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
            _health.set_state(
                name, _health.STATE_CRASHED_RESTARTING,
                recent_crashes_window=recent_crashes,
                last_error_class=type(exc).__name__,
                last_error_msg=str(exc)[:500],
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff_sec)


# ── Run-id helper ────────────────────────────────────────────────

def new_run_id() -> uuid.UUID:
    """Stable UUID per ingestion run. Logged on every event so a full
    pass can be reconstructed by greping a single ID.
    """
    return uuid.uuid4()
