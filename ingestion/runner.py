"""Ingestion entry point — boots all provider modules under supervision.

Wired into main.py's startup_event. Each provider module is launched
under the supervisor (architecture v1.3 §6.1) so a crash in one
provider doesn't take down ingestion globally.

Phase 1B: only FL. Phase 1C adds Kalshi REST. Phase 1D adds Kalshi
WS. New providers slot in here as additional create_task calls.
"""
from __future__ import annotations

import asyncio

from observability import get_logger

from .base import supervise


_log = get_logger("ingestion.runner")


async def start_all_ingestion() -> None:
    """Launch all configured ingestion modules under supervision.

    Returns when cancelled. Each provider runs in its own task; a
    crash in one is caught by the supervisor and restarted with
    backoff, leaving the others unaffected.

    No-op if DATABASE_URL is missing — ingestion needs Postgres.
    """
    try:
        from db import async_session, DATABASE_URL
    except Exception as exc:
        _log.warning(
            "ingestion.runner.no_db",
            reason="db module import failed",
            error_msg=str(exc)[:200],
        )
        return

    if not DATABASE_URL or async_session is None:
        _log.info(
            "ingestion.runner.skipping",
            reason="DATABASE_URL not set; ingestion requires Postgres",
        )
        return

    # Defer the FL import so a syntax error in fl.py during dev
    # doesn't prevent the rest of the app from starting up.
    from . import fl

    tasks = [
        asyncio.create_task(
            supervise("fl", lambda: fl.run(async_session)),
            name="ingestion.fl",
        ),
        # Phase 1C: Kalshi REST ingestion
        # asyncio.create_task(
        #     supervise("kalshi", lambda: kalshi.run(async_session)),
        #     name="ingestion.kalshi",
        # ),
    ]

    _log.info("ingestion.runner.started", task_count=len(tasks))

    # Wait for any task to complete — under normal operation they
    # never do (the supervisor keeps restarting them). If we get
    # here it's because of cancellation propagating from the parent.
    await asyncio.gather(*tasks, return_exceptions=True)
