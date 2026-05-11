"""FlashLive ingestion module.

Phase 1B per SP Architecture v1.3 §11.2. Polls FL endpoints on
configurable cadences and writes raw payloads to sp.fl_events.
Runs alongside the existing system; the legacy FlashLive feed in
flashlive_feed.py keeps writing to its own GAMES dict for the v3
serving path. This module is independent — it doesn't read or
write GAMES.

Cadences (architecture §6.2):
  Today's pre-game fixtures: 60s
  This week's fixtures (indent_days 1..7): 5–10 min
  Future fixtures (>1 week): 1–6 hr

Live scores at 5–10s are NOT in this module — that's served by the
legacy feed for the v3 path; the SP architecture will swap to the
new path during phase 3 cutover.

Design (architecture §6.3):
  * Idempotent UPSERT keyed on FL event_id with hash-based change
    detection. raw_payload only updates when content changes.
  * Pydantic validation at the boundary — raw still persists, but
    schema drift is logged and counted per-field for alerting.
  * Singleton enforcement via Postgres advisory lock so multiple
    workers don't duplicate the poll.
  * Crashes are caught by the supervisor (ingestion.base.supervise);
    this module's own loops just need to be safe to restart.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Iterable

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from observability import get_logger
from sp_models import FLEvent

from .base import (
    ADVISORY_LOCK_FL,
    IngestionResult,
    new_run_id,
    try_acquire_advisory_lock,
    upsert_provider_records_batch,
)
from .schema_validation import (
    FLEventValidator,
    FLTournamentValidator,
    validate_or_drift,
)


_log = get_logger("ingestion.fl")


# ── Sport scope ─────────────────────────────────────────────────
#
# FL sport_id mapping (matches main.py's _KALSHI_SPORT_BY_FL_ID).
# Phase 1B targets the sports we already pair Kalshi against;
# adding a new sport is one entry plus optionally a competition
# alias seed.
DEFAULT_FL_SPORT_IDS: list[int] = [
    1,   # Soccer
    2,   # Tennis
    3,   # Basketball
    4,   # Hockey
    5,   # American Football
    6,   # Baseball
    7,   # Handball
    8,   # Cricket
    9,   # Volleyball
    11,  # Rugby Union
    12,  # Aussie Rules
    13,  # Rugby League
    21,  # MMA
    22,  # Boxing
    23,  # Golf
    24,  # Snooker
    25,  # Darts
]


# Phase 2A.7: FL numeric sport_id → sp.sports.name. Used during
# ingestion to translate the per-sport poll's sport_id into the
# canonical sp.sports row, populating sp.fl_events.sport_id on every
# UPSERT. Keep keys aligned with DEFAULT_FL_SPORT_IDS above; values
# must match an existing sp.sports.name (case-sensitive).
#
# WHY this lives here, not in main.py: main.py's _KALSHI_SPORT_BY_FL_ID
# is older and conflicts on several IDs (e.g., 8 → "Rugby" / "Cricket"
# disagreement); it's tied to the legacy Kalshi-merge feed shape and
# carries non-resolver semantics. The map below is the SP architecture's
# single source of truth for FL → sp.sports translation. Resolver
# extraction reads sp.sports.name back through the runner SQL JOIN.
FL_SPORT_ID_TO_SP_NAME: dict[int, str] = {
    1:  "Soccer",
    2:  "Tennis",
    3:  "Basketball",
    4:  "Hockey",
    5:  "American Football",
    6:  "Baseball",
    7:  "Handball",
    8:  "Cricket",
    9:  "Volleyball",
    11: "Rugby Union",
    12: "Aussie Rules",
    13: "Rugby League",
    21: "MMA",
    22: "Boxing",
    23: "Golf",
    24: "Snooker",
    25: "Darts",
}


# ── Cadence loops ────────────────────────────────────────────────

async def _ingest_pass(
    session: AsyncSession,
    *,
    sport_ids: Iterable[int],
    indent_days: int,
    timezone_offset: int = 0,
) -> IngestionResult:
    """One pass: fetch /v1/events/list for each sport, UPSERT events.

    Errors fetching one sport don't abort the others. Each sport's
    fetch already emits a provider_api_call event (Phase 0
    instrumentation); this function adds an ingestion-level summary.
    """
    from flashlive_feed import _fl_get
    run_id = new_run_id()
    result = IngestionResult()
    started = time.monotonic()

    # Phase 2A.7 hotfix: resolve FL sport_id → sp.sports.id via this
    # function's own session. Previously the lookup was built in
    # `run()` and referenced from here as a free variable — a scope
    # bug that NameError'd on every pass and stopped sport_id from
    # ever being persisted (production sp.fl_events.sport_id stayed
    # 100% NULL after PR #86 merged).
    #
    # Cost: one bulk SELECT (17-row sp.sports table) per pass. Cheap;
    # also self-correcting if sp.sports gains rows mid-process.
    sport_ids_in = list(sport_ids)
    name_rows = (await session.execute(
        sa_text("SELECT id, name FROM sp.sports")
    )).all()
    sp_sports_id_by_name = {row.name: row.id for row in name_rows}

    sp_sport_id_by_fl_id: dict[int, int] = {}
    skipped_unmapped: list[int] = []
    for fl_id in sport_ids_in:
        sp_name = FL_SPORT_ID_TO_SP_NAME.get(fl_id)
        sp_id = sp_sports_id_by_name.get(sp_name) if sp_name else None
        if sp_id is None:
            skipped_unmapped.append(fl_id)
            continue
        sp_sport_id_by_fl_id[fl_id] = sp_id
    if skipped_unmapped:
        _log.warning(
            "ingestion.fl.sport_id_unmapped",
            fl_sport_ids=skipped_unmapped,
            hint="Add to FL_SPORT_ID_TO_SP_NAME or seed sp.sports.",
        )
    sport_ids = [s for s in sport_ids_in if s in sp_sport_id_by_fl_id]

    for sport_id in sport_ids:
        try:
            payload = await _fl_get(
                "/v1/events/list",
                {
                    "sport_id":    sport_id,
                    "timezone":    timezone_offset,
                    "indent_days": indent_days,
                },
            )
            if payload is None:
                result.failed += 1
                continue
            result.fetched += 1
        except Exception as exc:
            result.failed += 1
            _log.warning(
                "ingestion.fl.fetch_failed",
                run_id=str(run_id),
                sport_id=sport_id,
                indent_days=indent_days,
                error_class=type(exc).__name__,
                error_msg=str(exc)[:300],
            )
            continue

        # Collect every event in this sport's payload into a batch,
        # then UPSERT in one shot. Multi-row INSERT ... ON CONFLICT
        # DO UPDATE is constant round-trip count regardless of N,
        # so a sport with thousands of events takes the same wire
        # time as a sport with dozens.
        batch: list[dict] = []
        for tournament_raw in (payload.get("DATA") or []):
            # Validate tournament shape (logs drift; doesn't block
            # event processing — child events may still be valid).
            _, t_drift = validate_or_drift(
                provider="fl",
                record_kind="tournament",
                record_id=str(tournament_raw.get("TOURNAMENT_STAGE_ID") or ""),
                raw=tournament_raw,
                validator=FLTournamentValidator,
            )
            if t_drift:
                result.schema_drift += 1

            for event_raw in (tournament_raw.get("EVENTS") or []):
                if not isinstance(event_raw, dict):
                    continue
                event_id = (event_raw.get("EVENT_ID") or "").strip()
                if not event_id:
                    continue

                _, drift = validate_or_drift(
                    provider="fl",
                    record_kind="event",
                    record_id=event_id,
                    raw=event_raw,
                    validator=FLEventValidator,
                )
                if drift:
                    result.schema_drift += 1
                    # Persist raw anyway — P4. The resolver will
                    # skip records that can't be parsed; downstream
                    # alerting catches the rate.

                # Phase 2A.7: stamp sport_id from the per-sport loop
                # context. The unmapped-FL-id pre-filter above means
                # every sport_id reached here has a non-NULL sp_id;
                # stable update_cols across sports and existing rows
                # get backfilled on conflict.
                batch.append({
                    "pk":     {"fl_event_id": event_id},
                    "fields": {"sport_id": sp_sport_id_by_fl_id[sport_id]},
                    "raw":    event_raw,
                })

        if batch:
            try:
                inserted, updated, unchanged = await upsert_provider_records_batch(
                    session, FLEvent, batch,
                )
                result.inserted += inserted
                result.updated += updated
                result.unchanged += unchanged
            except Exception as exc:
                result.failed += len(batch)
                _log.warning(
                    "ingestion.fl.upsert_batch_failed",
                    run_id=str(run_id),
                    sport_id=sport_id,
                    batch_size=len(batch),
                    error_class=type(exc).__name__,
                    error_msg=str(exc)[:300],
                )

        # Commit per sport — bounds the transaction size and makes
        # partial progress durable when a sport fails mid-batch.
        await session.commit()

    result.duration_ms = int((time.monotonic() - started) * 1000)
    _log.info(
        "ingestion.fl.pass_complete",
        run_id=str(run_id),
        indent_days=indent_days,
        sport_ids=list(sport_ids),
        fetched=result.fetched,
        failed=result.failed,
        inserted=result.inserted,
        updated=result.updated,
        unchanged=result.unchanged,
        schema_drift=result.schema_drift,
        duration_ms=result.duration_ms,
    )
    return result


async def _today_pre_game_loop(
    session_factory,
    sport_ids: list[int],
    interval_sec: float = 60.0,
) -> None:
    """Loop: every 60s, refresh today's events for all sports."""
    while True:
        try:
            async with session_factory() as session:
                await _ingest_pass(session, sport_ids=sport_ids, indent_days=0)
        except Exception:
            # Supervisor handles logging + restart; raise so it sees us crash.
            raise
        await asyncio.sleep(interval_sec)


async def _week_loop(
    session_factory,
    sport_ids: list[int],
    interval_sec: float = 600.0,
) -> None:
    """Loop: every ~10 min, refresh fixtures 1..7 days out."""
    while True:
        try:
            async with session_factory() as session:
                for d in range(1, 8):
                    await _ingest_pass(
                        session, sport_ids=sport_ids, indent_days=d,
                    )
        except Exception:
            raise
        await asyncio.sleep(interval_sec)


# ── Entry point ──────────────────────────────────────────────────

async def run(
    session_factory,
    sport_ids: list[int] | None = None,
) -> None:
    """Top-level FL ingestion entry. Acquires the singleton lock,
    then runs the cadence loops as supervised tasks.

    `session_factory`: callable returning an AsyncSession context
    manager. In production this is `db.async_session`. Passed in
    so tests can provide a fake.

    Returns when cancelled. Crashes inside the loops are caught by
    the surrounding supervisor (ingestion.base.supervise) — this
    function should not catch exceptions itself.
    """
    sport_ids = sport_ids or DEFAULT_FL_SPORT_IDS

    # The advisory lock is held for the lifetime of the session
    # below. Inside that session we don't actually issue writes —
    # the cadence loops open their own per-pass sessions. We just
    # need ONE session held open so the lock stays held.
    async with session_factory() as lock_session:
        got_lock = await try_acquire_advisory_lock(
            lock_session, ADVISORY_LOCK_FL,
        )
        if not got_lock:
            _log.info(
                "ingestion.fl.skipping",
                reason="another worker holds the FL ingestion advisory lock",
            )
            return

        _log.info(
            "ingestion.fl.starting",
            sport_ids=sport_ids,
            cadences={
                "today_pre_game_sec": 60,
                "week_sec":           600,
            },
        )

        # asyncio.gather both loops; if one returns or raises, we
        # let the surrounding supervisor handle restart of the whole
        # run() call — keeps the lock semantics simple.
        await asyncio.gather(
            _today_pre_game_loop(session_factory, sport_ids, interval_sec=60.0),
            _week_loop(session_factory, sport_ids, interval_sec=600.0),
        )
