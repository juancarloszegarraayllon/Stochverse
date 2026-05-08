"""Phase 2B parallel-run runner.

Standalone script. Operator-invoked or cron-scheduled (recommended:
daily 02:00 UTC during the 7-day parallel-run period). NOT yet wired
into the live web service — that's Phase 2E's job.

Per-pass behavior:

  1. Bulk-load AliasResolver (~30k aliases → in-memory dict).
  2. Bulk-load sp.sports name/code → id table.
  3. Bulk-fetch unresolved provider records (fixture_id IS NULL)
     for the chosen provider.
  4. For each record:
       - extract_signal. If returns None (e.g. Kalshi outright /
         series / tournament), increment signal_extraction_skipped
         and skip — no FixtureSignal means no reason_detail to log.
       - match.
       - On STRICT (auto-apply): UPDATE fixture_id on the provider
         record AND INSERT resolution_log row in the same
         transaction (atomic per design doc §1).
       - On NO_MATCH: INSERT resolution_log row capturing
         reason_detail (which gate rejected, alias resolution
         status, competition gate decision, etc.). Provider
         record's fixture_id stays NULL. No UPDATE.
  5. Commit per chunk (default 200 records / chunk) per the leak-fix
     discipline in db.py.
  6. At end: write one row to sp.resolver_runs with metrics, including
     signal_extraction_skipped in `extra` JSONB so the
     records_scanned breakdown reconciles.

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
        AliasResolver, CompetitionResolver, FLResolverModule,
        KalshiResolverModule, ReasonCode, STRICT_MATCHER_VERSION,
        StrictMatcher,
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
    signal_extraction_skipped = 0     # Phase 2A.6: extract_signal returned None
                                      # (e.g., Kalshi outright/series — not per-fixture)
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

        # ── Step 2.5: bulk-load sp.competitions ────────────────
        competitions = await CompetitionResolver.load_all(bootstrap_session)
        log.info("resolver.run_pass.competitions_loaded", **competitions.stats())

        # ── Step 3: build matcher ──────────────────────────────
        matcher = StrictMatcher(
            aliases=aliases,
            sport_id_by_code_or_name=sport_id_by_code_or_name,
            competitions=competitions,
        )

        # ── Step 4: fetch unresolved provider records ──────────
        if provider == "kalshi":
            extractor = KalshiResolverModule()
            # sp.kalshi_markets holds every Kalshi market we've ever
            # ingested, including non-sports categories (Elections,
            # Politics, Crypto, Entertainment, Economics, ...). Those
            # markets carry no FixtureSignal — the resolver will
            # increment signal_extraction_skipped and never produce
            # useful output for them. They also dominate
            # ORDER BY last_seen_at DESC because they're traded more
            # frequently. Without this filter, --limit 100 returns
            # ~99% non-sports records and produces zero matcher
            # data for review.
            #
            # The filter is intentionally permissive:
            #  - (raw_payload->>'_is_sport')::boolean = true catches
            #    the canonical sport classification set by
            #    main.py's get_data() pass.
            #  - OR raw_payload->>'category' = 'Sports' is the
            #    second-pass catch for rows where _is_sport wasn't
            #    set (data-quality variance: ~1.5% of sport rows in
            #    the prod corpus as of 2026-05-08).
            #
            # DO NOT remove this filter without an alternative gate
            # — the runner would otherwise burn its --limit budget
            # on records it can't possibly match.
            sql = (
                "SELECT ticker AS pk, raw_payload "
                "FROM sp.kalshi_markets "
                "WHERE fixture_id IS NULL "
                "  AND ( "
                "    (raw_payload->>'_is_sport')::boolean = true "
                "    OR raw_payload->>'category' = 'Sports' "
                "  ) "
                "ORDER BY last_seen_at DESC"
            )
        else:  # provider == 'fl'
            extractor = FLResolverModule()
            # FL ingestion (ingestion/fl.py) fetches events only for
            # the sport_ids in DEFAULT_FL_SPORT_IDS, so every row in
            # sp.fl_events is sport-shaped by construction — no
            # category filter needed here. If ingestion ever broadens
            # to non-sport endpoints, mirror the Kalshi filter shape.
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
        chunk_skipped = 0
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
                            # Provider record can't be resolved by the
                            # strict tier (e.g., Kalshi outright,
                            # tournament, or series — not per-fixture).
                            # No FixtureSignal means no reason_detail
                            # to log; track in the run-level counter so
                            # day-7 reports can attribute the gap
                            # between records_scanned and (auto + miss
                            # + crashes).
                            chunk_skipped += 1
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

                        # Auto-apply path: link provider record to
                        # fixture in this transaction. Atomic with the
                        # ResolutionLog row written below — per design
                        # doc §1, link UPDATE and log INSERT must
                        # commit or roll back together.
                        if result.reason_code == ReasonCode.STRICT:
                            await session.execute(text(
                                f"UPDATE sp.{ 'kalshi_markets' if provider == 'kalshi' else 'fl_events' } "
                                f"SET fixture_id = :fixture_id "
                                f"WHERE { 'ticker' if provider == 'kalshi' else 'fl_event_id' } = :pk"
                            ).bindparams(
                                fixture_id=result.fixture_id,
                                pk=row.pk,
                            ))
                            chunk_auto += 1
                        else:
                            # no_match — record stays fixture_id IS
                            # NULL for the next pass / next tier. We
                            # still log the decision below so day-7
                            # review can query reason_detail->>'fail_reason'.
                            chunk_miss += 1

                        # Log every match decision (auto-apply AND
                        # no_match). reason_detail captures which gate
                        # rejected the signal, the resolved sport_id /
                        # team_ids, the competition gate decision,
                        # etc. — the substrate the day-7 review queries
                        # operate on.
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

                        latencies_ms.append(int((time.monotonic() - per_record_start) * 1000))

            # Chunk committed via session.begin() __aexit__.
            auto_applies += chunk_auto
            no_match += chunk_miss
            signal_extraction_skipped += chunk_skipped
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
                        # Counter for records the extractor skipped
                        # (returned None — e.g., Kalshi outright /
                        # series / tournament). Lives in extra rather
                        # than a top-level column to avoid a migration
                        # for what is effectively an audit metric;
                        # day-7 query pulls it via
                        # extra->>'signal_extraction_skipped'.
                        "signal_extraction_skipped": signal_extraction_skipped,
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
        signal_extraction_skipped=signal_extraction_skipped,
        crashes=crashes,
        latency_p95_ms=latency_p95,
    )

    print(f"\nResolver pass complete in {elapsed_sec:.1f}s:")
    print(f"  provider:                   {provider}")
    print(f"  run_mode:                   {run_mode}")
    print(f"  records_scanned:            {records_scanned:>6}")
    print(f"  auto_applies:               {auto_applies:>6}  ({100*auto_applies/(records_scanned or 1):.1f}%)")
    print(f"  no_match:                   {no_match:>6}  ({100*no_match/(records_scanned or 1):.1f}%)")
    print(f"  signal_extraction_skipped:  {signal_extraction_skipped:>6}  ({100*signal_extraction_skipped/(records_scanned or 1):.1f}%)")
    print(f"  crashes:                    {crashes:>6}")
    accounted = auto_applies + no_match + signal_extraction_skipped + crashes
    if accounted != records_scanned:
        print(f"  WARNING unaccounted gap:    {records_scanned - accounted:>6}")
    if latency_p95 is not None:
        print(f"  latency p95:                {latency_p95}ms")
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
