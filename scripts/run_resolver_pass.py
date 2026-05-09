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
import json
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


def _normalize_for_alias(raw: str) -> str:
    """Same normalization the strict tier uses for alias lookups
    (resolver._normalize.normalize_name). Imported lazily so the
    runner stays import-light at module load."""
    from resolver._normalize import normalize_name
    return normalize_name(raw)


# Halt-criteria thresholds from PHASE_2B_DESIGN.md §2. Wired into the
# stdout summary at the end of every pass — operators (and the cron
# review path) read these without having to query sp.resolver_runs.
#
# - CRASH_HARD_LIMIT comes from the design doc's "> 5/day" ceiling
#   applied per-pass (a single pass producing >5 crashes is already
#   hitting the daily threshold inside one window).
# - COVERAGE_FLOOR is the design doc's < 60% sustained-coverage
#   warning. Per-pass detection is best-effort: a single low pass
#   isn't conclusive, so we WARN rather than error.
# - LATENCY_P95_CEILING_MS is 5 minutes per the design doc's
#   "switch to LISTEN/NOTIFY (2E.fix)" trigger.
#
# Kalshi false-positive rate (>1%/24h) is computed at day-7 review
# time — needs the legacy_kalshi_join comparator + a 24h aggregation
# window, neither of which lives in a single pass.
CRASH_HARD_LIMIT = 5
COVERAGE_FLOOR = 0.60
LATENCY_P95_CEILING_MS = 5 * 60 * 1000


def _evaluate_halt_criteria(
    *,
    records_scanned: int,
    auto_applies: int,
    crashes: int,
    latency_p95_ms: int | None,
) -> list[str]:
    """Pure function — return human-readable WARNING strings for each
    halt-criteria threshold this pass exceeded.

    Empty list = pass is healthy. Logic split out from main() so it
    can be exercised by real call-path tests (lesson from PR #87
    NameError — static-source guards aren't enough).
    """
    warnings: list[str] = []

    if crashes > CRASH_HARD_LIMIT:
        warnings.append(
            f"crashes={crashes} exceeds halt threshold {CRASH_HARD_LIMIT} per pass "
            "(design doc §2: > 5/day → halt parallel-run; investigate before re-enabling)"
        )

    # Coverage check: only meaningful when the pass scanned a real
    # corpus. Tiny smoke runs (--limit 5) are excluded — a single
    # pass below the floor doesn't prove a sustained problem.
    if records_scanned >= 100:
        coverage = auto_applies / records_scanned
        if coverage < COVERAGE_FLOOR:
            warnings.append(
                f"coverage={coverage:.1%} (auto_applies/records_scanned) is below "
                f"the {COVERAGE_FLOOR:.0%} floor (design doc §2: < 60% sustained → "
                "review extraction; possibly relax competition-match or bootstrap "
                "more aliases)"
            )

    if latency_p95_ms is not None and latency_p95_ms > LATENCY_P95_CEILING_MS:
        warnings.append(
            f"latency p95={latency_p95_ms}ms exceeds {LATENCY_P95_CEILING_MS}ms ceiling "
            "(design doc §2: > 5min → switch to LISTEN/NOTIFY for 2E.fix)"
        )

    return warnings


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
        AliasResolver, AliasTierMatcher, CandidateIndex,
        CompetitionResolver, FLResolverModule, FuzzyTierMatcher,
        KalshiResolverModule, ReasonCode, STRICT_MATCHER_VERSION,
        StrictMatcher, TIERED_RESOLVER_VERSION, TieredMatcher,
    )
    from sp_models import (
        FLEvent, KalshiMarket, ResolutionLog, ResolverRun, TeamAlias,
    )

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
    auto_applies = 0                  # strict-tier OR alias-tier auto-apply
    no_match = 0                      # both tiers returned NO_MATCH
    crashes = 0
    signal_extraction_skipped = 0     # Phase 2A.6: extract_signal returned None
                                      # (e.g., Kalshi outright/series — not per-fixture)
    # Phase 2C.3 / 2D.3 per-tier breakdown — surfaced in
    # sp.resolver_runs.extra so day-7 reports can split the
    # auto_applies aggregate cleanly.
    strict_auto_applies = 0
    alias_auto_applies = 0
    alias_review_queue = 0
    alias_tennis_deferred = 0         # alias-tier early-exit on individual sports
    # Phase 2D.3: fuzzy-tier counters. Per rev3 design (Option C1),
    # review_queue is the headline output (~150/cron tennis), with
    # auto_apply ~2-3/cron a small bonus. Tracked separately from the
    # alias-tier counters so day-7 reports can attribute volume to
    # the right tier.
    fuzzy_auto_applies = 0
    fuzzy_review_queue = 0
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

        # ── Step 2.6: bulk-load sp.teams for alias-tier (Phase 2C.3) ──
        candidate_index = await CandidateIndex.load_all(bootstrap_session)
        log.info("resolver.run_pass.candidates_loaded", **candidate_index.stats())

        # ── Step 3: build TieredMatcher (strict → alias → fuzzy) ──
        strict_matcher = StrictMatcher(
            aliases=aliases,
            sport_id_by_code_or_name=sport_id_by_code_or_name,
            competitions=competitions,
        )
        alias_matcher = AliasTierMatcher(
            candidates=candidate_index,
            sport_id_by_code_or_name=sport_id_by_code_or_name,
        )
        # Phase 2D.3: fuzzy tier reuses the same CandidateIndex and
        # sport-id table — no extra bootstrap step needed. Per design
        # rev3, fuzzy runs only when alias tier returns NO_MATCH.
        fuzzy_matcher = FuzzyTierMatcher(
            candidates=candidate_index,
            sport_id_by_code_or_name=sport_id_by_code_or_name,
        )
        matcher = TieredMatcher(
            strict=strict_matcher,
            alias=alias_matcher,
            fuzzy=fuzzy_matcher,
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
            # Phase 2A.7: FL ingestion polls per-sport but didn't
            # persist sport context until 2A.7 added sp.fl_events.sport_id.
            # The runner JOINs sp.sports so the matcher can read the
            # sport name and pass it to extract_signal — without this,
            # every FL signal hit gate 2 (sport_not_classified) and
            # got rejected (production smoke produced 0/19,753
            # auto-applies before the column existed).
            #
            # `sport_id IS NOT NULL` filters out pre-2A.7 rows that
            # haven't been re-touched by ingestion yet. They drain as
            # `make backfill-fl` runs (covers ±7 day FL window) and
            # the live ingestion poll re-UPSERTs them.
            sql = (
                "SELECT fle.fl_event_id AS pk, "
                "       fle.raw_payload, "
                "       s.name AS sport_name "
                "FROM sp.fl_events fle "
                "INNER JOIN sp.sports s ON s.id = fle.sport_id "
                "WHERE fle.fixture_id IS NULL "
                "  AND fle.sport_id IS NOT NULL "
                "ORDER BY fle.last_seen_at DESC"
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
        chunk_strict_auto = 0
        chunk_alias_auto = 0
        chunk_alias_review = 0
        chunk_alias_tennis_deferred = 0
        # Phase 2D.3: per-chunk fuzzy tallies. Distinguished from
        # alias counters via the FINAL tier_result.resolver_version
        # (alias@2c.0 vs fuzzy@2d.0), not just the reason_code, since
        # both tiers can emit ALIAS/REVIEW_QUEUE/NO_MATCH-shaped results.
        chunk_fuzzy_auto = 0
        chunk_fuzzy_review = 0

        try:
            async with async_session() as session:
                # Phase 2D.3.1 hotfix: per-record transaction boundary
                # (was chunk-level pre-2D.3.1). The chunk-level
                # transaction caused IntegrityError on one record to
                # cascade as PendingRollbackError to every subsequent
                # record in the chunk — the symptom that triggered
                # the 619-crash incident with the review_queue
                # uniqueness constraint. Each record now commits or
                # rolls back independently inside the chunk's
                # session lifetime.
                for row in chunk:
                    records_scanned += 1
                    per_record_start = time.monotonic()

                    try:
                        # Phase 2A.7: FL extractor needs sport
                        # passed in (raw_payload doesn't carry
                        # SPORT_ID — see PROJECT_STATE.md). The
                        # runner SQL JOINs sp.sports so row.sport_name
                        # is the canonical name. Kalshi's
                        # extract_signal reads _sport off raw_payload
                        # itself; passing sport=None keeps that path
                        # working.
                        if provider == "fl":
                            signal = extractor.extract_signal(
                                row.raw_payload,
                                sport=row.sport_name,
                            )
                        else:
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

                    # Phase 2D.3.1: per-record transaction wraps the
                    # matcher's reads (find_fixture lookups) AND the
                    # runner's writes (resolution_log + UPDATE +
                    # INSERTs). Failures roll back ONLY this record's
                    # work, not the whole chunk.
                    match_failed = False
                    try:
                        async with session.begin():
                            try:
                                # Phase 2C.3 / 2D.3: TieredMatcher
                                # returns list[MatchResult] — strict,
                                # then alias on miss, then fuzzy on
                                # alias miss. Runner writes one
                                # resolution_log row per tier
                                # consulted (design D.4: "I tried and
                                # failed" is forensic data).
                                tier_results = await matcher.match(session, signal)
                            except Exception as e:
                                log.warning(
                                    "resolver.run_pass.match_failed",
                                    run_id=str(run_id),
                                    provider=provider,
                                    pk=row.pk,
                                    error_class=type(e).__name__,
                                    error_msg=str(e)[:300],
                                )
                                match_failed = True
                                raise

                            # Log every tier decision in order. Per design
                            # D.4: strict's no_match + alias's hit BOTH
                            # land in resolution_log when alias rescues a
                            # record strict missed.
                            for tier_result in tier_results:
                                session.add(ResolutionLog(
                                    run_id=run_id,
                                    provider=provider,
                                    provider_record_id=row.pk,
                                    fixture_id=tier_result.fixture_id,
                                    confidence=tier_result.confidence,
                                    reason_code=tier_result.reason_code.value,
                                    reason_detail=tier_result.reason_detail,
                                    resolver_version=tier_result.resolver_version,
                                ))

                            # Final tier decision drives the routing.
                            final = tier_results[-1]

                            if final.reason_code == ReasonCode.STRICT:
                                # 2B strict-tier auto-apply path.
                                await session.execute(text(
                                    f"UPDATE sp.{ 'kalshi_markets' if provider == 'kalshi' else 'fl_events' } "
                                    f"SET fixture_id = :fixture_id "
                                    f"WHERE { 'ticker' if provider == 'kalshi' else 'fl_event_id' } = :pk"
                                ).bindparams(
                                    fixture_id=final.fixture_id,
                                    pk=row.pk,
                                ))
                                chunk_auto += 1
                                chunk_strict_auto += 1
                            elif final.reason_code == ReasonCode.ALIAS:
                                # 2C alias-tier auto-apply path. Atomic
                                # with the resolution_log writes above:
                                # UPDATE provider table + INSERT
                                # sp.team_aliases (write-back, per design
                                # §3 self-improving property).
                                await session.execute(text(
                                    f"UPDATE sp.{ 'kalshi_markets' if provider == 'kalshi' else 'fl_events' } "
                                    f"SET fixture_id = :fixture_id "
                                    f"WHERE { 'ticker' if provider == 'kalshi' else 'fl_event_id' } = :pk"
                                ).bindparams(
                                    fixture_id=final.fixture_id,
                                    pk=row.pk,
                                ))
                                # Write back BOTH sides to sp.team_aliases
                                # so the next strict-tier pass picks them
                                # up at 0.98 confidence. Use ON CONFLICT
                                # DO NOTHING per D.5 — confidence is
                                # provenance, not a per-match score.
                                for side_label in ("home", "away"):
                                    team_id_str = final.reason_detail.get(
                                        f"{side_label}_team_id"
                                    )
                                    provider_norm = final.reason_detail.get(
                                        f"{side_label}_provider_normalized"
                                    )
                                    # The matcher records canonical/ratio
                                    # but not the raw provider string the
                                    # alias should preserve. Pull from
                                    # the original signal candidates.
                                    provider_raw = (
                                        signal.home_team_candidates[0].raw
                                        if side_label == "home" and signal.home_team_candidates
                                        else (
                                            signal.away_team_candidates[0].raw
                                            if side_label == "away" and signal.away_team_candidates
                                            else None
                                        )
                                    )
                                    if team_id_str and provider_raw:
                                        await session.execute(text(
                                            """
                                            INSERT INTO sp.team_aliases
                                              (id, team_id, alias, alias_normalized,
                                               source, confidence, created_at)
                                            VALUES
                                              (gen_random_uuid(), :tid, :alias,
                                               :alias_norm, 'alias_tier', :conf, NOW())
                                            ON CONFLICT (alias_normalized, source)
                                              DO NOTHING
                                            """
                                        ).bindparams(
                                            tid=uuid.UUID(team_id_str),
                                            alias=provider_raw,
                                            alias_norm=_normalize_for_alias(provider_raw),
                                            conf=final.confidence,
                                        ))
                                chunk_auto += 1
                                chunk_alias_auto += 1
                            elif final.reason_code == ReasonCode.FUZZY:
                                # Phase 2D.3 fuzzy-tier auto-apply path.
                                # Same atomic shape as the alias path: UPDATE
                                # provider table + INSERT sp.team_aliases
                                # write-back as source='fuzzy_tier' so the
                                # next strict-tier pass picks the alias up
                                # at 0.98 confidence. Per design rev3 §"What
                                # rev3 changes": no matcher behavioral
                                # changes; this is pure infrastructure
                                # wiring of the already-shipped 2D.2 matcher.
                                await session.execute(text(
                                    f"UPDATE sp.{ 'kalshi_markets' if provider == 'kalshi' else 'fl_events' } "
                                    f"SET fixture_id = :fixture_id "
                                    f"WHERE { 'ticker' if provider == 'kalshi' else 'fl_event_id' } = :pk"
                                ).bindparams(
                                    fixture_id=final.fixture_id,
                                    pk=row.pk,
                                ))
                                for side_label in ("home", "away"):
                                    team_id_str = final.reason_detail.get(
                                        f"{side_label}_team_id"
                                    )
                                    provider_raw = (
                                        signal.home_team_candidates[0].raw
                                        if side_label == "home" and signal.home_team_candidates
                                        else (
                                            signal.away_team_candidates[0].raw
                                            if side_label == "away" and signal.away_team_candidates
                                            else None
                                        )
                                    )
                                    if team_id_str and provider_raw:
                                        await session.execute(text(
                                            """
                                            INSERT INTO sp.team_aliases
                                              (id, team_id, alias, alias_normalized,
                                               source, confidence, created_at)
                                            VALUES
                                              (gen_random_uuid(), :tid, :alias,
                                               :alias_norm, 'fuzzy_tier', :conf, NOW())
                                            ON CONFLICT (alias_normalized, source)
                                              DO NOTHING
                                            """
                                        ).bindparams(
                                            tid=uuid.UUID(team_id_str),
                                            alias=provider_raw,
                                            alias_norm=_normalize_for_alias(provider_raw),
                                            conf=final.confidence,
                                        ))
                                chunk_auto += 1
                                chunk_fuzzy_auto += 1
                            elif final.reason_code == ReasonCode.REVIEW_QUEUE:
                                # 2C / 2D review-queue path. Insert one
                                # sp.review_queue row with the candidate
                                # team_ids. Provider.fixture_id stays NULL.
                                #
                                # Per-tier attribution: the alias tier and
                                # fuzzy tier both emit REVIEW_QUEUE, but
                                # they're tracked separately so day-7
                                # reports can split the volume. Use the
                                # resolver_version stamp on the final
                                # MatchResult (alias@2c.0 vs fuzzy@2d.0)
                                # since reason_code alone doesn't carry
                                # the source tier.
                                #
                                # Phase 2D.3.1 hotfix: ON CONFLICT DO
                                # UPDATE on the (provider, provider_record_id)
                                # uniqueness constraint. The same
                                # unresolved record (fixture_id IS NULL)
                                # comes back to the resolver on every cron
                                # pass until an operator approves the
                                # review_queue row, so the second pass
                                # would otherwise IntegrityError on the
                                # duplicate key. WHERE status='pending'
                                # protects already-decided rows: if an
                                # operator has approved or rejected the
                                # record, leave the review_queue row
                                # alone (the operator's decision is the
                                # source of truth, even if a re-resolve
                                # would now produce a different score).
                                await session.execute(text(
                                    """
                                    INSERT INTO sp.review_queue
                                      (id, provider, provider_record_id,
                                       candidate_fixtures, confidence,
                                       status, created_at)
                                    VALUES
                                      (gen_random_uuid(), :provider, :pk,
                                       CAST(:cands AS jsonb), :conf,
                                       'pending', NOW())
                                    ON CONFLICT (provider, provider_record_id)
                                      DO UPDATE SET
                                        candidate_fixtures = EXCLUDED.candidate_fixtures,
                                        confidence         = EXCLUDED.confidence
                                      WHERE sp.review_queue.status = 'pending'
                                    """
                                ).bindparams(
                                    provider=provider,
                                    pk=row.pk,
                                    cands=json.dumps(
                                        [str(t) for t in final.candidate_fixtures]
                                    ),
                                    conf=final.confidence,
                                ))
                                if final.resolver_version.startswith("fuzzy"):
                                    chunk_fuzzy_review += 1
                                else:
                                    chunk_alias_review += 1
                                # Don't count as miss — these are pending
                                # human approval.
                            else:
                                # All consulted tiers said NO_MATCH. Record
                                # stays fixture_id IS NULL — re-resolve
                                # picks it up on the next cron pass per
                                # Phase 2E mechanism (or stays unresolved
                                # if it's genuinely unmatched).
                                chunk_miss += 1
                                # Track tennis-deferred rows separately so
                                # the day-7 query can attribute the
                                # ~180/day Kalshi tennis no_match volume.
                                # The deferred_to_2d marker is set by the
                                # alias tier's tennis early-exit; in the
                                # 3-tier orchestrator that record then
                                # passes through fuzzy (which may also
                                # emit NO_MATCH if the surname doesn't
                                # match any candidate). The marker is
                                # inspected on the FINAL result; if fuzzy
                                # was consulted, its reason_detail
                                # supersedes the alias-tier marker.
                                if final.reason_detail.get("fail_reason") == "deferred_to_2d":
                                    chunk_alias_tennis_deferred += 1
                    except Exception as e:
                        # Phase 2D.3.1: per-record failure isolation.
                        # Anything that escapes the per-record
                        # session.begin() (matcher crash, write-side
                        # IntegrityError, FK violation, etc.) lands
                        # here. The transaction was rolled back by
                        # session.begin() __aexit__; the session
                        # remains usable for the next record. Match-
                        # side failures already emitted a specific
                        # log line above (match_failed flag); avoid
                        # double-warn by suppressing the generic
                        # record_failed log in that case.
                        chunk_crashes += 1
                        if not match_failed:
                            log.warning(
                                "resolver.run_pass.record_failed",
                                run_id=str(run_id),
                                provider=provider,
                                pk=row.pk,
                                error_class=type(e).__name__,
                                error_msg=str(e)[:300],
                            )
                        continue

                    latencies_ms.append(int((time.monotonic() - per_record_start) * 1000))

            # Per-record commits already happened inside the loop
            # (Phase 2D.3.1 hotfix). Roll up chunk counters to the
            # global counters now that the chunk has been processed.
            auto_applies += chunk_auto
            no_match += chunk_miss
            signal_extraction_skipped += chunk_skipped
            crashes += chunk_crashes
            strict_auto_applies += chunk_strict_auto
            alias_auto_applies += chunk_alias_auto
            alias_review_queue += chunk_alias_review
            alias_tennis_deferred += chunk_alias_tennis_deferred
            fuzzy_auto_applies += chunk_fuzzy_auto
            fuzzy_review_queue += chunk_fuzzy_review
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
                    # Phase 2C.3: stamp the orchestrator version on
                    # the run row, not the strict-tier version. Per-
                    # tier ResolutionLog rows continue to carry their
                    # own per-tier version (strict@2a.6 / alias@2c.0).
                    resolver_version=TIERED_RESOLVER_VERSION,
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
                        "signal_extraction_skipped": signal_extraction_skipped,
                        # Phase 2C.3 / 2D.3 per-tier breakdown.
                        "strict_auto_applies":    strict_auto_applies,
                        "alias_auto_applies":     alias_auto_applies,
                        "alias_review_queue":     alias_review_queue,
                        "alias_tennis_deferred":  alias_tennis_deferred,
                        # Phase 2D.3 (Option C1): fuzzy review queue
                        # is the headline output (~150/cron tennis);
                        # auto_apply ~2-3/cron is bonus. See
                        # PHASE_2D_DESIGN.md rev3 for prediction.
                        "fuzzy_auto_applies":     fuzzy_auto_applies,
                        "fuzzy_review_queue":     fuzzy_review_queue,
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
        strict_auto_applies=strict_auto_applies,
        alias_auto_applies=alias_auto_applies,
        alias_review_queue=alias_review_queue,
        alias_tennis_deferred=alias_tennis_deferred,
        fuzzy_auto_applies=fuzzy_auto_applies,
        fuzzy_review_queue=fuzzy_review_queue,
    )

    total_review_queue = alias_review_queue + fuzzy_review_queue
    print(f"\nResolver pass complete in {elapsed_sec:.1f}s:")
    print(f"  provider:                   {provider}")
    print(f"  run_mode:                   {run_mode}")
    print(f"  records_scanned:            {records_scanned:>6}")
    print(f"  auto_applies (total):       {auto_applies:>6}  ({100*auto_applies/(records_scanned or 1):.1f}%)")
    print(f"    strict tier:              {strict_auto_applies:>6}")
    print(f"    alias  tier:              {alias_auto_applies:>6}")
    print(f"    fuzzy  tier:              {fuzzy_auto_applies:>6}")
    print(f"  review_queue (total):       {total_review_queue:>6}  ({100*total_review_queue/(records_scanned or 1):.1f}%)")
    print(f"    alias  tier:              {alias_review_queue:>6}")
    print(f"    fuzzy  tier:              {fuzzy_review_queue:>6}")
    print(f"  no_match:                   {no_match:>6}  ({100*no_match/(records_scanned or 1):.1f}%)")
    print(f"    of which tennis deferred: {alias_tennis_deferred:>6}")
    print(f"  signal_extraction_skipped:  {signal_extraction_skipped:>6}  ({100*signal_extraction_skipped/(records_scanned or 1):.1f}%)")
    print(f"  crashes:                    {crashes:>6}")
    accounted = auto_applies + no_match + total_review_queue + signal_extraction_skipped + crashes
    if accounted != records_scanned:
        print(f"  WARNING unaccounted gap:    {records_scanned - accounted:>6}")
    if latency_p95 is not None:
        print(f"  latency p95:                {latency_p95}ms")
    print(f"\n  metrics written to sp.resolver_runs (run_id={run_id})")

    # ── Halt-criteria evaluation (PHASE_2B_DESIGN.md §2) ──────────
    halt_warnings = _evaluate_halt_criteria(
        records_scanned=records_scanned,
        auto_applies=auto_applies,
        crashes=crashes,
        latency_p95_ms=latency_p95,
    )
    if halt_warnings:
        # Surface to stdout for cron-log scrapers AND the structured
        # log so observability tooling can alert. Use 'warning' level
        # — operator decides whether to halt; the runner doesn't
        # self-disable.
        log.warning(
            "resolver.run_pass.halt_criteria_exceeded",
            run_id=str(run_id),
            provider=provider,
            run_mode=run_mode,
            warnings=halt_warnings,
        )
        print()
        print("  HALT CRITERIA EXCEEDED — review before next pass:")
        for w in halt_warnings:
            print(f"    - {w}")
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
