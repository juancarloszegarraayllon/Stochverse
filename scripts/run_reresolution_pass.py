"""Phase 2E re-resolution loop — first scheduled work on the active
critical path to v4 cutover.

Mirrors scripts/run_resolver_pass.py shape, but with a TARGETED
two-tier candidate-selection layer that distinguishes this loop from
the daily cron's brute-force re-resolve of every fixture_id IS NULL
record.

Per docs/reresolution/scope-2026-06-17.md (all 8 framing questions
DECIDED):

  Tier 1 (LATERAL-driven; supports
  ix_resolution_log_provider_record_decided_at from migration
  b3d5e7f9a2c4 — drives from the unresolved provider table, looks up
  the latest decision per row via an Index Scan + LIMIT 1):
    - provider_table.fixture_id IS NULL
    - latest sp.resolution_log row has reason_code = 'no_match'
    - reason_detail->>'fail_reason' IN allowlist of 5 categories
    - reason_detail->>'asymmetric_excluded' IS NULL

  Tier 2 (containment, only on Tier-1 survivors — supports the GIN
  index for the alias-add signal):
    LOOSE (F1a-DECIDED): a sp.team_aliases row with
    created_at > last decided_at whose team_id is referenced anywhere
    in reason_detail (any team in prior candidate set qualifies);
    OR a new sp.fixtures row since last decision overlapping the
    candidate pair.

Day-41 sizing pass established the addressable working set ceiling:
~16,588 records. The Tier-1 filter alone narrows the gross 35,831
no_match population by ~54% (allowlist + prop-exclusion remove
~19,243 structurally-non-addressable records). Tier-2 runs over the
~16,588 survivors only.

## Usage

    # Dry-run (DEFAULT — no writes):
    DATABASE_URL=<url> python scripts/run_reresolution_pass.py --provider fl
    DATABASE_URL=<url> python scripts/run_reresolution_pass.py --provider kalshi

    # Per-sport (optional; default = all sports):
    DATABASE_URL=<url> python scripts/run_reresolution_pass.py --provider fl --sport 3

    # LISTEN/NOTIFY seam (F8) — operator/handler-supplied record set,
    # bypasses the candidate-selection query:
    DATABASE_URL=<url> python scripts/run_reresolution_pass.py \\
        --provider fl --candidate-set fl:ABC123,fl:DEF456

    # Wet apply (--apply required, sp.resolver_runs row written
    # run_mode='live'):
    DATABASE_URL=<url> python scripts/run_reresolution_pass.py \\
        --provider fl --apply

## Exit codes

  0 — success (writes happened OR dry-run report produced)
  1 — DATABASE_URL not set or engine unavailable
  2 — bad CLI args
  3 — Pattern D pre-flight failed (Amendment #17)
  4 — hard failure during pass (matcher unavailable, etc.)
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ──────────────────────────────────────────────────────────────────
# F1 — Allowlist of fail_reason values that are loop-addressable.
# Day-41 sizing: these 5 categories are the only ones an alias-add
# can flip. Everything else (structural_normalize_failed,
# sport_not_classified, deferred_to_2d, kickoff_confidence_*) is
# excluded by the candidate-selection query.
# ──────────────────────────────────────────────────────────────────
LOOP_ELIGIBLE_FAIL_REASONS: tuple[str, ...] = (
    "fuzzy_no_team_resemblance",
    "fuzzy_collision_no_anchor",
    "alias_no_team_resemblance",
    "below_review_threshold",
    "alias_resolution_incomplete",
)


# ──────────────────────────────────────────────────────────────────
# F6 halt criteria (DECIDED Day-42 — mirrors run_resolver_pass.py
# pattern, thresholds tuned to the loop's 5-min cadence).
# ──────────────────────────────────────────────────────────────────
CANDIDATE_SET_MULTIPLIER_CEILING = 5  # >5× trailing-7d mean triggers warn
CANDIDATE_SELECT_LATENCY_CEILING_MS = 5_000  # >5s triggers warn
HARD_LIMIT_CANDIDATE_SET = 50_000  # > addressable population × 3 → hard fail


def _evaluate_halt_criteria(
    *,
    candidate_set_size: int,
    latency_candidate_select_ms: int,
    trailing_7d_mean_candidate_set: float | None,
) -> list[str]:
    """Pure function — return human-readable WARNING strings for each
    F6 halt-criteria threshold this pass exceeded.

    Empty list = pass is healthy. Mirrors
    run_resolver_pass._evaluate_halt_criteria shape so the same
    cron-log scrapers work on both.
    """
    warnings: list[str] = []
    if (
        trailing_7d_mean_candidate_set is not None
        and trailing_7d_mean_candidate_set > 0
        and candidate_set_size
        > CANDIDATE_SET_MULTIPLIER_CEILING * trailing_7d_mean_candidate_set
    ):
        warnings.append(
            f"candidate_set_size={candidate_set_size} exceeds "
            f"{CANDIDATE_SET_MULTIPLIER_CEILING}× trailing 7-day mean "
            f"({trailing_7d_mean_candidate_set:.1f}) — investigate "
            "upstream change before next pass"
        )
    if latency_candidate_select_ms > CANDIDATE_SELECT_LATENCY_CEILING_MS:
        warnings.append(
            f"candidate-selection latency {latency_candidate_select_ms}ms "
            f"exceeds {CANDIDATE_SELECT_LATENCY_CEILING_MS}ms ceiling — "
            "verify GIN + partial-btree indexes are being used "
            "(EXPLAIN ANALYZE the candidate query)"
        )
    return warnings


# ──────────────────────────────────────────────────────────────────
# Candidate-selection — pure SQL Tier 1 + Python Tier 2
# ──────────────────────────────────────────────────────────────────
#
# Tier-1 SQL drives from the MATERIALIZED set of unresolved provider
# rows, then per-row LATERALs the latest resolution_log decision.
# Three perf iterations got us here:
#
#   Day-43 attempt 1 — CTE+DISTINCT-ON+JOIN. 6.3s warm. Planner
#     chose Parallel Seq Scan + Sort over the whole resolution_log:
#     reason_detail JSONB pulled across (effectively) the entire
#     table made heap-fetch-per-row cheaper than index-scan-plus-
#     heap-fetch.
#
#   Day-43 attempt 2 — FROM fl_events JOIN LATERAL ... WHERE
#     fixture_id IS NULL. 2.7s warm. The LATERAL inner was correct
#     (1.1s Index Scan on ix_resolution_log_provider_record_decided_at
#     + LIMIT 1), but the OUTER applied fixture_id IS NULL as a
#     filter on a Seq Scan of fl_events (1.5s), so the LATERAL ran
#     once per fl_events row, not just unresolved ones.
#
#   Day-43 attempt 3 (current) — WITH unresolved AS MATERIALIZED
#     (SELECT … WHERE fixture_id IS NULL). MATERIALIZED is the
#     non-negotiable bit: PG 12+ inlines single-reference CTEs by
#     default, which would put us back at attempt 2's seq-scan
#     choice. MATERIALIZED forces the planner to compute the CTE
#     separately, which means scanning sp.fl_events through
#     ix_fl_events_unresolved (the partial index `WHERE fixture_id
#     IS NULL` already in the initial schema), then iterating that
#     small materialized set into the LATERAL.
#
# Index utilization (post-attempt-3):
#   - Outer driver: ix_fl_events_unresolved /
#     ix_kalshi_markets_unresolved (partial; pre-existing, from
#     20260507_1504_8f404e0dc89a initial schema).
#   - Inner LATERAL: ix_resolution_log_provider_record_decided_at
#     (b3d5e7f9a2c4) — Index Scan + LIMIT 1 per outer row, no sort.
#
# Forward-pointer (if attempt 3 still measures > 5s F6 ceiling):
# bound the candidate set with a time window (e.g., only consider
# records whose latest decision is within the last N days, or only
# unresolved provider rows last_seen_at since some watermark). The
# unresolved-provider set has no upper-bound semantics in the schema
# — old records that never got resolved accrete indefinitely. If
# the set is genuinely tens-of-thousands large, even N indexed
# LATERAL lookups may exceed the ceiling. Defer this fix until
# attempt 3 measures, per the survey-first → scope-second →
# build-third → measure-fourth discipline.

TIER1_SQL_FL = """
WITH unresolved_fl_events AS MATERIALIZED (
    SELECT fl_event_id
    FROM sp.fl_events
    WHERE fixture_id IS NULL
)
SELECT u.fl_event_id AS provider_record_id,
       latest.reason_detail,
       latest.decided_at
FROM unresolved_fl_events u
JOIN LATERAL (
    SELECT rl.reason_code, rl.reason_detail, rl.decided_at
    FROM sp.resolution_log rl
    WHERE rl.provider = 'fl'
      AND rl.provider_record_id = u.fl_event_id
    ORDER BY rl.decided_at DESC
    LIMIT 1
) latest ON TRUE
WHERE latest.reason_code = 'no_match'
  AND (latest.reason_detail->>'fail_reason') = ANY(:allowlist)
  AND (latest.reason_detail->>'asymmetric_excluded') IS NULL
"""

TIER1_SQL_KALSHI = """
WITH unresolved_kalshi_markets AS MATERIALIZED (
    SELECT ticker
    FROM sp.kalshi_markets
    WHERE fixture_id IS NULL
)
SELECT u.ticker AS provider_record_id,
       latest.reason_detail,
       latest.decided_at
FROM unresolved_kalshi_markets u
JOIN LATERAL (
    SELECT rl.reason_code, rl.reason_detail, rl.decided_at
    FROM sp.resolution_log rl
    WHERE rl.provider = 'kalshi'
      AND rl.provider_record_id = u.ticker
    ORDER BY rl.decided_at DESC
    LIMIT 1
) latest ON TRUE
WHERE latest.reason_code = 'no_match'
  AND (latest.reason_detail->>'fail_reason') = ANY(:allowlist)
  AND (latest.reason_detail->>'asymmetric_excluded') IS NULL
"""


def _extract_team_ids_from_reason_detail(reason_detail: Any) -> set[str]:
    """Pure function — walk reason_detail JSONB and collect every
    team_id string mentioned (loose F1a semantics).

    Keys we know carry team_ids: colliding_home_team_ids,
    colliding_away_team_ids, asymmetric_failed_side_candidate_team_ids,
    candidate_home_team_id, candidate_away_team_id, home_team_id,
    away_team_id, plus any *_team_id / *_team_ids variation.

    Defensive walk: handles nested dicts, list values, and missing
    keys without raising. Returns lowercase string UUIDs.
    """
    if not reason_detail or not isinstance(reason_detail, dict):
        return set()
    out: set[str] = set()

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if not isinstance(k, str):
                    continue
                if "team_id" in k.lower():
                    if isinstance(v, str):
                        out.add(v.lower())
                    elif isinstance(v, list):
                        for item in v:
                            if isinstance(item, str):
                                out.add(item.lower())
                else:
                    _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(reason_detail)
    return out


def _filter_tier2(
    tier1_rows: list[dict],
    aliases_by_team: dict[str, list],
    fixtures_since_by_team: dict[str, list],
) -> list[dict]:
    """Pure function — apply F1's Tier-2 LOOSE alias-add OR
    fixture-state filter to the Tier-1 survivors.

    `tier1_rows` items: {provider_record_id, reason_detail, decided_at}.
    `aliases_by_team`: {team_id_lower: [(team_alias_created_at, ...), ...]}.
    `fixtures_since_by_team`: same shape.

    A row passes Tier 2 iff at least one team_id in the row's
    reason_detail has either:
      - an alias_created_at > row.decided_at (alias-add signal), OR
      - a fixture_created_at > row.decided_at (fixture-state signal).

    Returns the surviving rows in input order.
    """
    survivors: list[dict] = []
    for row in tier1_rows:
        decided_at = row["decided_at"]
        team_ids = _extract_team_ids_from_reason_detail(row["reason_detail"])
        if not team_ids:
            continue
        hit = False
        for tid in team_ids:
            for ts in aliases_by_team.get(tid, []):
                if ts > decided_at:
                    hit = True
                    break
            if hit:
                break
            for ts in fixtures_since_by_team.get(tid, []):
                if ts > decided_at:
                    hit = True
                    break
            if hit:
                break
        if hit:
            survivors.append(row)
    return survivors


async def _select_candidates(
    *,
    session,
    provider: str,
    sport_id: int | None,
    log,
) -> list[dict]:
    """Run the two-tier candidate-selection query against production.

    Returns list of {provider, provider_record_id, reason_detail,
    decided_at}.
    """
    from sqlalchemy import text

    # ── Tier 1 ───────────────────────────────────────────────
    sql = TIER1_SQL_FL if provider == "fl" else TIER1_SQL_KALSHI
    rows = (await session.execute(
        text(sql), {"allowlist": list(LOOP_ELIGIBLE_FAIL_REASONS)},
    )).all()
    tier1 = [
        {
            "provider": provider,
            "provider_record_id": r.provider_record_id,
            "reason_detail": r.reason_detail,
            "decided_at": r.decided_at,
        }
        for r in rows
    ]
    log.info(
        "reresolution.candidates.tier1",
        provider=provider,
        tier1_size=len(tier1),
    )
    if not tier1:
        return []

    # ── Tier 2 ───────────────────────────────────────────────
    all_team_ids: set[str] = set()
    min_decided_at = None
    for r in tier1:
        ids = _extract_team_ids_from_reason_detail(r["reason_detail"])
        all_team_ids.update(ids)
        if min_decided_at is None or r["decided_at"] < min_decided_at:
            min_decided_at = r["decided_at"]
    if not all_team_ids:
        return []

    # sp.team_aliases — alias-add signal.
    alias_rows = (await session.execute(
        text(
            "SELECT team_id::text AS team_id, created_at "
            "FROM sp.team_aliases "
            "WHERE created_at > :min_decided_at "
            "  AND team_id::text = ANY(:team_ids)"
        ),
        {
            "min_decided_at": min_decided_at,
            "team_ids": list(all_team_ids),
        },
    )).all()
    aliases_by_team: dict[str, list] = {}
    for ar in alias_rows:
        aliases_by_team.setdefault(ar.team_id.lower(), []).append(
            ar.created_at,
        )

    # sp.fixtures — fixture-state signal.
    # F1 condition (4) second branch: a new sp.fixtures row whose
    # home/away team_pair overlaps the prior candidate set.
    fixture_rows = (await session.execute(
        text(
            "SELECT home_team_id::text AS home_id, "
            "       away_team_id::text AS away_id, created_at "
            "FROM sp.fixtures "
            "WHERE created_at > :min_decided_at "
            "  AND (home_team_id::text = ANY(:team_ids) "
            "       OR away_team_id::text = ANY(:team_ids))"
        ),
        {
            "min_decided_at": min_decided_at,
            "team_ids": list(all_team_ids),
        },
    )).all()
    fixtures_since_by_team: dict[str, list] = {}
    for fr in fixture_rows:
        for tid in (fr.home_id.lower(), fr.away_id.lower()):
            fixtures_since_by_team.setdefault(tid, []).append(fr.created_at)

    survivors = _filter_tier2(
        tier1, aliases_by_team, fixtures_since_by_team,
    )
    log.info(
        "reresolution.candidates.tier2",
        provider=provider,
        tier1_size=len(tier1),
        survivors=len(survivors),
        alias_rows_considered=len(alias_rows),
        fixture_rows_considered=len(fixture_rows),
    )
    return survivors


# ──────────────────────────────────────────────────────────────────
# Trailing-7d mean (for F6 halt-criteria comparison)
# ──────────────────────────────────────────────────────────────────


async def _trailing_7d_mean_candidate_set(
    session, provider: str,
) -> float | None:
    """Return the trailing 7-day mean of candidate_set_size from
    sp.resolver_runs.extra JSONB. Returns None if there's no prior
    data (e.g., first pass on fresh deploy)."""
    from sqlalchemy import text

    row = (await session.execute(
        text(
            "SELECT avg((extra->>'candidate_set_size')::int) AS mean "
            "FROM sp.resolver_runs "
            "WHERE provider = :provider "
            "  AND run_mode = 'live' "
            "  AND started_at > NOW() - INTERVAL '7 days' "
            "  AND (extra->>'candidate_set_size') IS NOT NULL"
        ),
        {"provider": provider},
    )).first()
    if row is None or row.mean is None:
        return None
    return float(row.mean)


# ──────────────────────────────────────────────────────────────────
# Main pass
# ──────────────────────────────────────────────────────────────────


async def main(
    *,
    provider: str,
    sport_id: int | None,
    candidate_set_override: list[tuple[str, str]] | None,
    apply: bool,
) -> int:
    from sqlalchemy import text
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from db import async_session, DATABASE_URL

    if not DATABASE_URL or async_session is None:
        print("ERROR: DATABASE_URL not set; loop requires Postgres.",
              file=sys.stderr)
        return 1

    if provider not in ("fl", "kalshi"):
        print(f"ERROR: --provider must be 'fl' or 'kalshi', got "
              f"{provider!r}", file=sys.stderr)
        return 2

    # ── Pattern D pre-flight (Amendment #17) ──────────────────
    if apply:
        from scripts.daily_diff import _check_pattern_d_endpoint
        allow_non_prod = (
            os.environ.get("DAILY_DIFF_ALLOW_NON_PRODUCTION", "").strip()
            == "1"
        )
        if not allow_non_prod:
            expected_db_name = (
                os.environ.get("EXPECTED_PRODUCTION_DB_NAME", "").strip()
                or "neondb"
            )
            expected_db_host = (
                os.environ.get("EXPECTED_PRODUCTION_DB_HOST", "").strip()
                or None
            )
            async with async_session() as preflight_session:
                result = await preflight_session.execute(
                    text("SELECT current_database();")
                )
                current_db = result.scalar_one()
            rc, msg = _check_pattern_d_endpoint(
                os.environ.get("DATABASE_URL"),
                current_db,
                expected_db_name=expected_db_name,
                expected_db_host=expected_db_host,
                allow_non_production=False,
            )
            if rc != 0:
                print(f"ERROR: {msg}", file=sys.stderr)
                return 3

    from observability import get_logger
    log = get_logger("resolver.reresolution_pass")

    started_at = time.monotonic()
    run_id = uuid.uuid4()
    log.info(
        "reresolution.start",
        run_id=str(run_id),
        provider=provider,
        sport_id=sport_id,
        run_mode="live",
        apply=apply,
        candidate_set_override=bool(candidate_set_override),
    )

    candidate_select_started = time.monotonic()
    async with async_session() as session:
        if candidate_set_override is not None:
            # F8 seam: caller supplied (provider, record_id) pairs
            # directly. Bypass candidate-selection query entirely.
            # Used by future LISTEN/NOTIFY handler; in this script
            # it's an operator override for targeted re-resolution.
            candidates = await _hydrate_candidate_override(
                session=session,
                provider=provider,
                override=candidate_set_override,
                log=log,
            )
        else:
            candidates = await _select_candidates(
                session=session,
                provider=provider,
                sport_id=sport_id,
                log=log,
            )
    latency_candidate_select_ms = int(
        (time.monotonic() - candidate_select_started) * 1000
    )

    candidate_set_size = len(candidates)
    log.info(
        "reresolution.candidates.complete",
        run_id=str(run_id),
        provider=provider,
        candidate_set_size=candidate_set_size,
        latency_candidate_select_ms=latency_candidate_select_ms,
    )

    if candidate_set_size > HARD_LIMIT_CANDIDATE_SET:
        log.error(
            "reresolution.hard_limit_exceeded",
            run_id=str(run_id),
            candidate_set_size=candidate_set_size,
            hard_limit=HARD_LIMIT_CANDIDATE_SET,
        )
        print(
            f"ERROR: candidate_set_size={candidate_set_size} exceeds "
            f"hard limit {HARD_LIMIT_CANDIDATE_SET} — refusing to run. "
            "Day-41 sizing: addressable population is ~16,588; "
            "candidate set should not exceed ~50k under normal "
            "conditions. Investigate upstream before next pass.",
            file=sys.stderr,
        )
        return 4

    auto_applies = 0
    no_match = 0
    review_queue_writes = 0
    crashes = 0

    if apply and candidates:
        try:
            (
                auto_applies, no_match, review_queue_writes, crashes,
            ) = await _run_matcher_over_candidates(
                provider=provider,
                candidates=candidates,
                run_id=run_id,
                log=log,
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "reresolution.matcher_failure",
                run_id=str(run_id),
                error_class=type(exc).__name__,
                error_msg=str(exc)[:300],
            )
            return 4

    elapsed_sec = time.monotonic() - started_at
    elapsed_ms = int(elapsed_sec * 1000)

    # ── F6 halt criteria ────────────────────────────────────
    async with async_session() as halt_session:
        trailing_mean = await _trailing_7d_mean_candidate_set(
            halt_session, provider,
        )
    halt_warnings = _evaluate_halt_criteria(
        candidate_set_size=candidate_set_size,
        latency_candidate_select_ms=latency_candidate_select_ms,
        trailing_7d_mean_candidate_set=trailing_mean,
    )

    # ── sp.resolver_runs row (only on --apply) ──────────────
    if apply:
        try:
            async with async_session() as metrics_session:
                from sp_models import ResolverRun
                await metrics_session.execute(
                    pg_insert(ResolverRun.__table__).values(
                        run_id=run_id,
                        resolver_version="reresolution@2e.0",
                        provider=provider,
                        run_mode="live",
                        started_at=__import__("datetime").datetime.now(
                            __import__("datetime").timezone.utc,
                        ),
                        finished_at=__import__("datetime").datetime.now(
                            __import__("datetime").timezone.utc,
                        ),
                        records_scanned=candidate_set_size,
                        auto_applies=auto_applies,
                        no_match=no_match,
                        crashes=crashes,
                        latency_p95_ms=None,
                        extra={
                            "candidate_set_size": candidate_set_size,
                            "review_queue_writes": review_queue_writes,
                            "latency_candidate_select_ms":
                                latency_candidate_select_ms,
                            "latency_total_ms": elapsed_ms,
                            "halt_warnings": halt_warnings,
                            "sport_id": sport_id,
                            "candidate_set_override":
                                bool(candidate_set_override),
                        },
                    )
                )
                await metrics_session.commit()
        except Exception as e:  # noqa: BLE001
            log.error(
                "reresolution.metrics_write_failed",
                run_id=str(run_id),
                error_class=type(e).__name__,
                error_msg=str(e)[:300],
            )

    # ── Structured log per F6 ───────────────────────────────
    log.info(
        "reresolution.complete",
        run_id=str(run_id),
        provider=provider,
        run_mode="live",
        candidate_set_size=candidate_set_size,
        auto_applies=auto_applies,
        no_match=no_match,
        review_queue_writes=review_queue_writes,
        crashes=crashes,
        latency_total_ms=elapsed_ms,
        latency_candidate_select_ms=latency_candidate_select_ms,
        dry_run=not apply,
    )

    # ── Stdout summary ──────────────────────────────────────
    verb = "Would" if not apply else ""
    print(f"\nRe-resolution pass {'dry-run' if not apply else 'apply'} "
          f"complete in {elapsed_sec:.2f}s:")
    print(f"  provider:                  {provider}")
    print(f"  run_mode:                  live")
    print(f"  sport_id:                  "
          f"{sport_id if sport_id is not None else '(all)'}")
    print(f"  candidate_set_size:        {candidate_set_size:>6}")
    if trailing_mean is not None:
        print(f"  trailing-7d-mean:          {trailing_mean:>6.1f}")
    print(f"  candidate-select latency:  {latency_candidate_select_ms:>6} ms")
    if apply:
        print(f"  auto_applies:              {auto_applies:>6}")
        print(f"  no_match:                  {no_match:>6}")
        print(f"  review_queue_writes:       {review_queue_writes:>6}")
        print(f"  crashes:                   {crashes:>6}")
        print(f"\n  metrics written to sp.resolver_runs "
              f"(run_id={run_id}, run_mode=live)")
    else:
        print(f"  {verb} run matcher over {candidate_set_size} records.")
        print(f"\n  Dry-run: no writes to sp.resolution_log, "
              "sp.review_queue, or sp.resolver_runs.")
        print(f"  Operator sanity check — Day-41 addressable ceiling is "
              f"~16,588.")

    if halt_warnings:
        log.warning(
            "reresolution.halt_criteria_exceeded",
            run_id=str(run_id),
            provider=provider,
            warnings=halt_warnings,
        )
        print()
        print("  HALT CRITERIA EXCEEDED — review before next pass:")
        for w in halt_warnings:
            print(f"    - {w}")

    return 0


async def _hydrate_candidate_override(
    *,
    session,
    provider: str,
    override: list[tuple[str, str]],
    log,
) -> list[dict]:
    """Hydrate a caller-supplied (provider, record_id) list into the
    same shape _select_candidates returns. F8 LISTEN/NOTIFY seam.

    Filters the override to records that actually match this script
    invocation's `provider` and exist with `fixture_id IS NULL` —
    a record an external caller asked us to re-resolve that has
    since been resolved by another pass is silently dropped (not an
    error).
    """
    from sqlalchemy import text
    record_ids = [rid for (p, rid) in override if p == provider]
    if not record_ids:
        return []
    sql = (
        "WITH latest AS ("
        "  SELECT DISTINCT ON (provider, provider_record_id) "
        "    provider, provider_record_id, reason_detail, decided_at, "
        "    reason_code "
        "  FROM sp.resolution_log "
        "  WHERE provider = :provider "
        "    AND provider_record_id = ANY(:rids) "
        "  ORDER BY provider, provider_record_id, decided_at DESC"
        ") "
        "SELECT latest.provider_record_id, latest.reason_detail, "
        "       latest.decided_at FROM latest "
    )
    if provider == "fl":
        sql += (
            "JOIN sp.fl_events fle "
            "  ON fle.fl_event_id = latest.provider_record_id "
            " AND fle.fixture_id IS NULL"
        )
    else:
        sql += (
            "JOIN sp.kalshi_markets km "
            "  ON km.ticker = latest.provider_record_id "
            " AND km.fixture_id IS NULL"
        )
    rows = (await session.execute(
        text(sql), {"provider": provider, "rids": record_ids},
    )).all()
    out = [
        {
            "provider": provider,
            "provider_record_id": r.provider_record_id,
            "reason_detail": r.reason_detail,
            "decided_at": r.decided_at,
        }
        for r in rows
    ]
    log.info(
        "reresolution.candidate_override.hydrated",
        provider=provider,
        requested=len(record_ids),
        hydrated=len(out),
    )
    return out


async def _run_matcher_over_candidates(
    *,
    provider: str,
    candidates: list[dict],
    run_id: uuid.UUID,
    log,
) -> tuple[int, int, int, int]:
    """Run the TieredMatcher over the candidate set and write fresh
    sp.resolution_log rows. Reuses the same write pattern as
    run_resolver_pass.py (ON CONFLICT WHERE status='pending' covers
    sp.review_queue idempotency per F4).

    Returns (auto_applies, no_match, review_queue_writes, crashes).
    """
    # Lazy import — keeps module-load light and matches the resolver
    # bootstrap shape.
    from sqlalchemy import text
    from db import async_session
    from resolver import (
        AliasResolver, AliasTierMatcher, CandidateIndex,
        CompetitionResolver, FLResolverModule, FuzzyTierMatcher,
        KalshiResolverModule, StrictMatcher, TieredMatcher,
    )
    from sp_models import ResolutionLog

    auto = 0
    miss = 0
    rq_writes = 0
    crashes = 0

    async with async_session() as session:
        # ── Bootstrap matcher state (single-shot) ────────────
        aliases = await AliasResolver.load_all(session)
        sport_rows = (await session.execute(
            text("SELECT id, code, name FROM sp.sports")
        )).all()
        sport_id_by_code_or_name: dict[str, int] = {}
        for r in sport_rows:
            sport_id_by_code_or_name[r.code.lower()] = r.id
            sport_id_by_code_or_name[r.name.lower()] = r.id
        competitions = await CompetitionResolver.load_all(session)
        candidate_index = await CandidateIndex.load_all(session)

        strict_matcher = StrictMatcher(
            aliases=aliases,
            sport_id_by_code_or_name=sport_id_by_code_or_name,
            competitions=competitions,
        )
        alias_matcher = AliasTierMatcher(
            candidates=candidate_index,
            sport_id_by_code_or_name=sport_id_by_code_or_name,
        )
        fuzzy_matcher = FuzzyTierMatcher(
            candidates=candidate_index,
            sport_id_by_code_or_name=sport_id_by_code_or_name,
        )
        matcher = TieredMatcher(
            strict=strict_matcher,
            alias=alias_matcher,
            fuzzy=fuzzy_matcher,
        )
        extractor = (
            FLResolverModule() if provider == "fl"
            else KalshiResolverModule()
        )

        # ── Fetch raw payloads for the candidate set ─────────
        record_ids = [c["provider_record_id"] for c in candidates]
        if provider == "fl":
            payload_rows = (await session.execute(
                text(
                    "SELECT fle.fl_event_id AS pk, fle.raw_payload, "
                    "       s.name AS sport_name "
                    "FROM sp.fl_events fle "
                    "INNER JOIN sp.sports s ON s.id = fle.sport_id "
                    "WHERE fle.fl_event_id = ANY(:ids) "
                    "  AND fle.fixture_id IS NULL "
                    "  AND fle.sport_id IS NOT NULL"
                ),
                {"ids": record_ids},
            )).all()
        else:
            payload_rows = (await session.execute(
                text(
                    "SELECT km.ticker AS pk, km.raw_payload "
                    "FROM sp.kalshi_markets km "
                    "WHERE km.ticker = ANY(:ids) "
                    "  AND km.fixture_id IS NULL"
                ),
                {"ids": record_ids},
            )).all()

        # ── Walk records and write sp.resolution_log per pass ─
        for row in payload_rows:
            try:
                signal = extractor.extract_signal(
                    row.raw_payload
                    if provider == "kalshi"
                    else {"raw_payload": row.raw_payload,
                          "sport_name": row.sport_name},
                )
                if signal is None:
                    continue
                result = matcher.match(signal)
                rc = result.reason_code.value if hasattr(
                    result.reason_code, "value"
                ) else str(result.reason_code)
                await session.execute(
                    pg_insert_resolution_log(
                        run_id=run_id,
                        provider=provider,
                        provider_record_id=row.pk,
                        fixture_id=result.fixture_id,
                        confidence=result.confidence,
                        reason_code=rc,
                        reason_detail=result.reason_detail,
                        resolver_version=result.resolver_version,
                    )
                )
                if rc in ("strict", "alias", "fuzzy"):
                    auto += 1
                elif rc == "review_queue":
                    rq_writes += 1
                elif rc == "no_match":
                    miss += 1
            except Exception as exc:  # noqa: BLE001
                crashes += 1
                log.warning(
                    "reresolution.record_crash",
                    run_id=str(run_id),
                    provider_record_id=row.pk,
                    error_class=type(exc).__name__,
                    error_msg=str(exc)[:200],
                )
        await session.commit()
    return auto, miss, rq_writes, crashes


def pg_insert_resolution_log(
    *,
    run_id: uuid.UUID,
    provider: str,
    provider_record_id: str,
    fixture_id: uuid.UUID | None,
    confidence: float,
    reason_code: str,
    reason_detail: dict | None,
    resolver_version: str,
):
    """Pure SQL constructor for the sp.resolution_log INSERT —
    append-only, indexed on (provider, provider_record_id) +
    decided_at. No ON CONFLICT clause needed (BIGSERIAL PK).
    """
    from sqlalchemy import text
    return text(
        "INSERT INTO sp.resolution_log "
        "(run_id, provider, provider_record_id, fixture_id, "
        " confidence, reason_code, reason_detail, resolver_version) "
        "VALUES (:run_id, :provider, :pk, :fid, :conf, :rc, "
        "        CAST(:rd AS JSONB), :rv)"
    ).bindparams(
        run_id=run_id,
        provider=provider,
        pk=provider_record_id,
        fid=fixture_id,
        conf=float(confidence),
        rc=reason_code,
        rd=json.dumps(reason_detail or {}),
        rv=resolver_version,
    )


# ──────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────


def parse_candidate_set(raw: str) -> list[tuple[str, str]]:
    """Parse `--candidate-set provider:record_id,provider:record_id`
    into a list of tuples. Raises ValueError on bad shape."""
    out: list[tuple[str, str]] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            raise ValueError(
                f"candidate-set entry {entry!r} missing 'provider:' "
                "prefix"
            )
        p, rid = entry.split(":", 1)
        p = p.strip().lower()
        rid = rid.strip()
        if p not in ("fl", "kalshi"):
            raise ValueError(
                f"candidate-set provider {p!r} must be 'fl' or 'kalshi'"
            )
        if not rid:
            raise ValueError(
                f"candidate-set entry {entry!r} has empty record_id"
            )
        out.append((p, rid))
    return out


def cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Phase 2E re-resolution loop — targeted re-resolve of the "
            "addressable no_match back-catalog. Mirrors "
            "run_resolver_pass.py shape with a two-tier candidate-"
            "selection layer (DECIDED Day-42)."
        ),
    )
    parser.add_argument(
        "--provider", required=True, choices=["fl", "kalshi"],
        help="Provider whose unresolved no_match records should be "
             "re-resolved.",
    )
    parser.add_argument(
        "--sport", type=int, default=None, metavar="SPORT_ID",
        help="Restrict to a single sport_id. Defaults to all sports "
             "(F8 per-sport-tuning seam from day one).",
    )
    parser.add_argument(
        "--candidate-set", default=None, metavar="LIST",
        help="Operator/handler override — comma-separated "
             "provider:record_id pairs that bypass candidate selection. "
             "F8 LISTEN/NOTIFY seam.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true",
        help="Default. Run candidate selection + report counts WITHOUT "
             "calling the matcher or writing sp.resolution_log / "
             "sp.review_queue / sp.resolver_runs.",
    )
    mode.add_argument(
        "--apply", action="store_true",
        help="Run wet — invoke the matcher, write the resolution_log "
             "rows, write the sp.resolver_runs metrics row "
             "(run_mode='live'). Pattern D pre-flight enforced.",
    )
    args = parser.parse_args(argv)

    override: list[tuple[str, str]] | None = None
    if args.candidate_set:
        try:
            override = parse_candidate_set(args.candidate_set)
        except ValueError as exc:
            print(f"ERROR: bad --candidate-set: {exc}", file=sys.stderr)
            return 2

    if not args.dry_run and not args.apply:
        # Default to dry-run if neither flag is passed (safety).
        args.dry_run = True

    return asyncio.run(main(
        provider=args.provider,
        sport_id=args.sport,
        candidate_set_override=override,
        apply=args.apply,
    ))


if __name__ == "__main__":
    sys.exit(cli_main())
