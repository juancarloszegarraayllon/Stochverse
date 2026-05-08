"""Phase 2B parallel-run runner.

Standalone script. Operator-invoked or cron-scheduled (recommended:
daily 02:00 UTC during the 7-day parallel-run period). NOT yet wired
into the live web service — that's Phase 2E's job.

Per-pass behavior:

  1. Bulk-load AliasResolver (~30k aliases → in-memory dict).
  2. Bulk-load sp.sports name/code → id table.
  3. Bulk-fetch unresolved provider records (fixture_id IS NULL)
     for the chosen provider.
  4. For each record: extract_signal → match → if hit, atomic
     transaction (UPDATE fixture_id, INSERT resolution_log).
  5. Commit per chunk (default 200 records / chunk) per the leak-fix
     discipline in db.py.
  6. At end: write one row to sp.resolver_runs with metrics.

Usage:

    DATABASE_URL=<prod-Neon> python scripts/run_resolver_pass.py --provider kalshi
    DATABASE_URL=<prod-Neon> python scripts/run_resolver_pass.py --provider fl
    DATABASE_URL=<prod-Neon> python scripts/run_resolver_pass.py --provider kalshi --run-mode cron
    DATABASE_URL=<prod-Neon> python scripts/run_resolver_pass.py --provider kalshi --limit 100  # smoke
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import uuid
from typing import Any

# Make project root importable when invoked as `python scripts/...`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# Per-chunk transaction size. Mirrors the leak-fix discipline in db.py
# — bounds each transaction to a small, testable scope. 200 records ×
# ~3 round-trips each = ~45s @ 75ms RT, well under the 60s
# idle_in_transaction_session_timeout.
CHUNK_SIZE = 200


async def main(
    provider: str,
    run_mode: str,
    limit: int | None,
) -> int:
    from sqlalchemy import text
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from db import async_session, DATABASE_URL
    if not DATABASE_URL or async_session is None:
        print("ERROR: DATABASE_URL not set; resolver requires Postgres.", file=sys.stderr)
        return 2

    if provider not in ("fl", "kalshi"):
        print(f"ERROR: --provider must be 'fl' or 'kalshi', got {provider!r}", file=sys.stderr)
        return 2
    if run_mode not in ("standalone", "cron"):
        print(
            f"ERROR: --run-mode must be 'standalone' or 'cron' for parallel-run; "
            f"'live' is reserved for Phase 2E. Got {run_mode!r}",
            file=sys.stderr,
        )
        return 2

    from observability import get_logger
    from resolver import (
        AliasResolver, FLResolverModule, KalshiResolverModule,
        ReasonCode, STRICT_MATCHER_VERSION, StrictMatcher,
    )
    from sp_models import FLEvent, KalshiMarket, ResolutionLog, ResolverRun

    log = get_logger("resolver.run_pass")
    started_at = time.monotonic()
    started_at_dt = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    run_id = uuid.uuid4()

    log.info(
        "resolver.run_pass.start",
        run_id=str(run_id),
        provider=provider,
        run_mode=run_mode,
        resolver_version=STRICT_MATCHER_VERSION,
        limit=limit,
    )

    # Counters for the eventual sp.resolver_runs row.
    records_scanned = 0
    auto_applies = 0
    no_match = 0
    crashes = 0
    latencies_ms: list[int] = []

    async with async_session() as bootstrap_session:
        # ── Step 1: bulk-load aliases ──────────────────────────
        aliases = await AliasResolver.load_all(bootstrap_session)
        log.info("resolver.run_pass.aliases_loaded", **aliases.stats())

        # ── Step 2: bulk-load sport id table ───────────────────
        sport_rows = (await bootstrap_session.execute(
            text("SELECT id, code, name FROM sp.sports")
        )).all()
        sport_id_by_code_or_name: dict[str, int] = {}
        for row in sport_rows:
            sport_id_by_code_or_name[row.code] = row.id
            sport_id_by_code_or_name[row.name] = row.id
        log.info(
            "resolver.run_pass.sports_loaded",
            sport_count=len(sport_rows),
        )

        # ── Step 3: build matcher ──────────────────────────────
        matcher = StrictMatcher(
            aliases=aliases,
            sport_id_by_code_or_name=sport_id_by_code_or_name,
        )

        # ── Step 4: fetch unresolved provider records ──────────
        if provider == "kalshi":
            extractor = KalshiResolverModule()
            sql = (
                "SELECT ticker AS pk, raw_payload "
                "FROM sp.kalshi_markets "
                "WHERE fixture_id IS NULL "
                "ORDER BY last_seen_at DESC"
            )
        else:  # provider == 'fl'
            extractor = FLResolverModule()
            sql = (
                "SELECT fl_event_id AS pk, raw_payload "
                "FROM sp.fl_events "
                "WHERE fixture_id IS NULL "
                "ORDER BY last_seen_at DESC"
            )
        if limit:
            sql += f" LIMIT {int(limit)}"

        unresolved_rows = (await bootstrap_session.execute(text(sql))).all()
        log.info(
            "resolver.run_pass.unresolved_loaded",
            provider=provider,
            count=len(unresolved_rows),
        )

    # ── Step 5: walk + match in chunks, each its own transaction ──
    for chunk_start in range(0, len(unresolved_rows), CHUNK_SIZE):
        chunk = unresolved_rows[chunk_start:chunk_start + CHUNK_SIZE]
        chunk_auto = 0
        chunk_miss = 0
        chunk_crashes = 0

        try:
            async with async_session() as session:
                async with session.begin():
                    for row in chunk:
                        records_scanned += 1
                        per_record_start = time.monotonic()

                        try:
                            signal = extractor.extract_signal(row.raw_payload)
                        except Exception as e:
                            chunk_crashes += 1
                            log.warning(
                                "resolver.run_pass.extract_failed",
                                run_id=str(run_id),
                                provider=provider,
                                pk=row.pk,
                                error_class=type(e).__name__,
                                error_msg=str(e)[:300],
                            )
                            continue

                        if signal is None:
                            # Provider record can't be resolved (e.g.
                            # Kalshi outright). Don't write
                            # resolution_log — nothing to log.
                            continue

                        try:
                            result = await matcher.match(session, signal)
                        except Exception as e:
                            chunk_crashes += 1
                            log.warning(
                                "resolver.run_pass.match_failed",
                                run_id=str(run_id),
                                provider=provider,
                                pk=row.pk,
                                error_class=type(e).__name__,
                                error_msg=str(e)[:300],
                            )
                            continue

                        if result.reason_code == ReasonCode.STRICT:
                            # Auto-apply: link provider record to fixture
                            # AND append resolution_log row in this same
                            # transaction (atomic per design doc §1).
                            await session.execute(text(
                                f"UPDATE sp.{ 'kalshi_markets' if provider == 'kalshi' else 'fl_events' } "
                                f"SET fixture_id = :fixture_id "
                                f"WHERE { 'ticker' if provider == 'kalshi' else 'fl_event_id' } = :pk"
                            ).bindparams(
                                fixture_id=result.fixture_id,
                                pk=row.pk,
                            ))
                            session.add(ResolutionLog(
                                run_id=run_id,
                                provider=provider,
                                provider_record_id=row.pk,
                                fixture_id=result.fixture_id,
                                confidence=result.confidence,
                                reason_code=result.reason_code.value,
                                reason_detail=result.reason_detail,
                                resolver_version=result.resolver_version,
                            ))
                            chunk_auto += 1
                        else:
                            # no_match — no DB write. The record stays
                            # fixture_id IS NULL for the next pass /
                            # next tier.
                            chunk_miss += 1

                        latencies_ms.append(int((time.monotonic() - per_record_start) * 1000))

            # Chunk committed via session.begin() __aexit__.
            auto_applies += chunk_auto
            no_match += chunk_miss
            crashes += chunk_crashes
        except Exception as e:
            crashes += len(chunk)
            log.error(
                "resolver.run_pass.chunk_failed",
                run_id=str(run_id),
                chunk_index=chunk_start // CHUNK_SIZE,
                chunk_size=len(chunk),
                error_class=type(e).__name__,
                error_msg=str(e)[:300],
            )

    # ── Step 6: write sp.resolver_runs metrics row ────────────
    finished_at_dt = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    elapsed_sec = time.monotonic() - started_at

    # latency_p95 if we have samples
    latency_p95 = None
    if latencies_ms:
        latencies_ms.sort()
        idx = max(0, int(len(latencies_ms) * 0.95) - 1)
        latency_p95 = latencies_ms[idx]

    try:
        async with async_session() as session:
            async with session.begin():
                session.add(ResolverRun(
                    run_id=run_id,
                    resolver_version=STRICT_MATCHER_VERSION,
                    provider=provider,
                    run_mode=run_mode,
                    started_at=started_at_dt,
                    finished_at=finished_at_dt,
                    records_scanned=records_scanned,
                    auto_applies=auto_applies,
                    no_match=no_match,
                    crashes=crashes,
                    latency_p95_ms=latency_p95,
                    extra={
                        "limit": limit,
                        "chunk_size": CHUNK_SIZE,
                    },
                ))
    except Exception as e:
        log.error(
            "resolver.run_pass.metrics_write_failed",
            run_id=str(run_id),
            error_class=type(e).__name__,
            error_msg=str(e)[:300],
        )

    log.info(
        "resolver.run_pass.complete",
        run_id=str(run_id),
        provider=provider,
        run_mode=run_mode,
        elapsed_sec=round(elapsed_sec, 1),
        records_scanned=records_scanned,
        auto_applies=auto_applies,
        no_match=no_match,
        crashes=crashes,
        latency_p95_ms=latency_p95,
    )

    print(f"\nResolver pass complete in {elapsed_sec:.1f}s:")
    print(f"  provider:        {provider}")
    print(f"  run_mode:        {run_mode}")
    print(f"  records_scanned: {records_scanned:>6}")
    print(f"  auto_applies:    {auto_applies:>6}  ({100*auto_applies/(records_scanned or 1):.1f}%)")
    print(f"  no_match:        {no_match:>6}  ({100*no_match/(records_scanned or 1):.1f}%)")
    print(f"  crashes:         {crashes:>6}")
    if latency_p95 is not None:
        print(f"  latency p95:     {latency_p95}ms")
    print(f"\n  metrics written to sp.resolver_runs (run_id={run_id})")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--provider", required=True, choices=["fl", "kalshi"],
        help="Provider whose unresolved records should be matched.",
    )
    parser.add_argument(
        "--run-mode", default="standalone", choices=["standalone", "cron"],
        help="standalone (default) for ad-hoc operator runs; cron for "
             "scheduled invocations during parallel-run. 'live' is "
             "reserved for Phase 2E and rejected here.",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process only the first N unresolved records. Use for "
             "smoke-testing against a small slice.",
    )
    args = parser.parse_args()
    rc = asyncio.run(main(
        provider=args.provider,
        run_mode=args.run_mode,
        limit=args.limit,
    ))
    sys.exit(rc)
