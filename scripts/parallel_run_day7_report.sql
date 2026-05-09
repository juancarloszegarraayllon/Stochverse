-- Phase 2B parallel-run day-7 review queries.
-- ===========================================================
--
-- Run after the 7-day observation window (PHASE_2B_DESIGN.md §2)
-- closes — at that point we either lock the strict-tier configuration
-- or adjust per the threshold tables in the design doc.
--
-- All queries scope to the parallel-run window:
--   * started_at > NOW() - INTERVAL '7 days'
--   * run_mode IN ('standalone', 'cron')   -- excludes 'live' (post-2E)
--
-- Usage:
--   psql "$DATABASE_URL" -f scripts/parallel_run_day7_report.sql
--
-- Or run individual sections by copy/paste in psql / pgAdmin.
-- ===========================================================


-- ── 1. Daily auto-apply rate per provider ────────────────────
-- Coverage trend: rising = strict tier picking up ground; flat =
-- saturated; falling = upstream changes (new alias gaps,
-- ingestion shifts). < 60% sustained → review extraction (design §2).

\echo '=== 1. Daily auto-apply rate per provider ==='

SELECT date_trunc('day', started_at)::date         AS day,
       provider,
       SUM(records_scanned)                        AS scanned,
       SUM(auto_applies)                           AS auto_applies,
       SUM(no_match)                               AS no_match,
       SUM((extra->>'signal_extraction_skipped')::int) AS extract_skipped,
       SUM(crashes)                                AS crashes,
       ROUND(100.0 * SUM(auto_applies) /
             NULLIF(SUM(records_scanned), 0), 2)   AS coverage_pct
FROM sp.resolver_runs
WHERE started_at > NOW() - INTERVAL '7 days'
  AND run_mode IN ('standalone', 'cron')
GROUP BY 1, 2
ORDER BY 1, 2;


-- ── 2. Day-over-day trend (auto-apply rate) ──────────────────
-- One row per provider per day with the prior-day delta.
-- Watching for: stable trend = baseline locked; rising = good;
-- falling = upstream regression worth investigating.

\echo ''
\echo '=== 2. Day-over-day auto-apply rate trend ==='

WITH daily AS (
  SELECT date_trunc('day', started_at)::date AS day,
         provider,
         100.0 * SUM(auto_applies) /
           NULLIF(SUM(records_scanned), 0)  AS coverage_pct
  FROM sp.resolver_runs
  WHERE started_at > NOW() - INTERVAL '7 days'
    AND run_mode IN ('standalone', 'cron')
  GROUP BY 1, 2
)
SELECT day,
       provider,
       ROUND(coverage_pct, 2)                              AS coverage_pct,
       ROUND(coverage_pct - LAG(coverage_pct) OVER (
                              PARTITION BY provider ORDER BY day),
             2)                                            AS day_over_day_pct
FROM daily
ORDER BY provider, day;


-- ── 3. Daily latency p95 ─────────────────────────────────────
-- > 5 min sustained → switch to LISTEN/NOTIFY (design §2; 2E.fix).
-- The runner emits a per-pass WARNING when this threshold is
-- exceeded; this query is the cross-day audit.

\echo ''
\echo '=== 3. Daily latency p95 ==='

SELECT date_trunc('day', started_at)::date AS day,
       provider,
       MAX(latency_p95_ms)                 AS worst_p95_ms,
       AVG(latency_p95_ms)::int            AS mean_p95_ms,
       COUNT(*)                            AS passes
FROM sp.resolver_runs
WHERE started_at > NOW() - INTERVAL '7 days'
  AND run_mode IN ('standalone', 'cron')
  AND latency_p95_ms IS NOT NULL
GROUP BY 1, 2
ORDER BY 1, 2;


-- ── 4. Daily crash count ─────────────────────────────────────
-- > 5/day → halt parallel-run; investigate before re-enabling
-- (design §2). The runner halt-criteria check fires per-pass on
-- > 5 in a single pass; this query catches the cumulative case.

\echo ''
\echo '=== 4. Daily crash count ==='

SELECT date_trunc('day', started_at)::date AS day,
       provider,
       SUM(crashes)                        AS crashes,
       SUM(records_scanned)                AS scanned,
       ROUND(100.0 * SUM(crashes) /
             NULLIF(SUM(records_scanned), 0), 2) AS crash_pct
FROM sp.resolver_runs
WHERE started_at > NOW() - INTERVAL '7 days'
  AND run_mode IN ('standalone', 'cron')
GROUP BY 1, 2
HAVING SUM(crashes) > 0
ORDER BY 1, 2;


-- ── 5. fail_reason distribution per provider per day ─────────
-- The most useful query for understanding WHY records aren't
-- matching. Phase 2C alias tier targets the bulk of
-- alias_resolution_incomplete; kalshi_competition_unresolvable
-- means re-running bootstrap_sp_competitions; etc.

\echo ''
\echo '=== 5. fail_reason distribution (last 24h) ==='

SELECT provider,
       reason_detail->>'fail_reason'              AS fail_reason,
       COUNT(*)                                   AS n,
       ROUND(100.0 * COUNT(*) /
             SUM(COUNT(*)) OVER (PARTITION BY provider), 1) AS pct_of_provider
FROM sp.resolution_log
WHERE reason_code = 'no_match'
  AND decided_at > NOW() - INTERVAL '24 hours'
GROUP BY 1, 2
ORDER BY 1, 3 DESC;


\echo ''
\echo '=== 6. fail_reason distribution (full 7-day window) ==='

SELECT provider,
       reason_detail->>'fail_reason'              AS fail_reason,
       COUNT(*)                                   AS n,
       ROUND(100.0 * COUNT(*) /
             SUM(COUNT(*)) OVER (PARTITION BY provider), 1) AS pct_of_provider
FROM sp.resolution_log
WHERE reason_code = 'no_match'
  AND decided_at > NOW() - INTERVAL '7 days'
GROUP BY 1, 2
ORDER BY 1, 3 DESC;


-- ── 7. FL transitional sub-paths (Phase 2A.6 audit) ──────────
-- All three values should drain to matched_existing_comp_fixture
-- after Phase 2C lands and FL events get explicit competition_ids.
-- Pre-2C this report is pure transitional-state visibility.

\echo ''
\echo '=== 7. FL transitional sub-paths (last 7 days) ==='

SELECT reason_detail->>'fl_transitional_path' AS path,
       COUNT(*)                               AS n,
       ROUND(100.0 * COUNT(*) /
             SUM(COUNT(*)) OVER (), 1)        AS pct
FROM sp.resolution_log
WHERE provider = 'fl'
  AND reason_code = 'strict'
  AND decided_at > NOW() - INTERVAL '7 days'
  AND reason_detail ? 'fl_transitional_path'
GROUP BY 1
ORDER BY 2 DESC;


-- ── 8. Phase 2C backfill candidates ──────────────────────────
-- Kalshi explicit-comp signals that linked to NULL-comp fixtures.
-- Phase 2C reconciliation pass replays the matcher with seeded
-- FL competitions and backfills competition_id from this list.

\echo ''
\echo '=== 8. Phase 2C backfill candidates ==='

SELECT COUNT(*)                                                       AS n,
       MIN(decided_at)                                                AS earliest,
       MAX(decided_at)                                                AS latest
FROM sp.resolution_log
WHERE reason_detail ? 'linked_to_null_comp_fixture';


-- ── 9. Cross-provider summary ────────────────────────────────
-- Single-row health check: 7-day totals + worst p95 + halt-flag
-- count. Read at the top of the day-7 review meeting.

\echo ''
\echo '=== 9. Cross-provider 7-day summary ==='

SELECT provider,
       SUM(records_scanned)                              AS scanned_7d,
       SUM(auto_applies)                                 AS auto_applies_7d,
       SUM(no_match)                                     AS no_match_7d,
       SUM(crashes)                                      AS crashes_7d,
       ROUND(100.0 * SUM(auto_applies) /
             NULLIF(SUM(records_scanned), 0), 2)         AS coverage_pct_7d,
       MAX(latency_p95_ms)                               AS worst_p95_ms,
       COUNT(*)                                          AS passes_7d
FROM sp.resolver_runs
WHERE started_at > NOW() - INTERVAL '7 days'
  AND run_mode IN ('standalone', 'cron')
GROUP BY 1
ORDER BY 1;
