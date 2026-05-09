-- Phase 2D.2.7 — corroboration-gap investigation runbook.
-- ============================================================
--
-- The 2D.2.5 dry-run measured 1.5% cross-provider corroboration
-- on tennis. Design rev1 predicted 20-40%. The 2D.2.7 queries
-- below diagnose WHY the rate is so low BEFORE 2D.3 locks
-- threshold values that depend on it.
--
-- Per PHASE_2D_DESIGN.md rev2 §E.8, three queries gate 2D.3:
--
--   Q1 — Tournament overlap.    Are FL fixtures present in the
--                               same kickoff window as Kalshi
--                               tennis records?
--   Q2 — Kickoff alignment.     When same-match pairs exist
--                               across providers, how far apart
--                               are their kickoff timestamps?
--   Q3 — Drift window check.    Does widening find_fixture's
--                               30-min window meaningfully lift
--                               corroboration?
--
-- Output of these queries determines which of three paths 2D.3
-- ships against:
--
--   Path A (tournament gap):  Q1 shows < 30% Kalshi-with-FL-overlap.
--                             FL doesn't ingest the tournaments
--                             Kalshi covers. 2D.3 ships as Option C1
--                             (review-queue tool); Phase 2D.5 expands
--                             DEFAULT_FL_SPORT_IDS or adds Challenger
--                             /ITF tournaments via per-tournament fetch.
--
--   Path B (kickoff gap):     Q1 shows > 70% Kalshi-with-FL-overlap
--                             AND Q2 shows median offset > 30 min on
--                             same-match pairs. 2D.3 ships with
--                             widened drift_sec OR a kickoff-inference
--                             fix (e.g., FL "scheduled" vs Kalshi
--                             "match start" semantics differ).
--
--   Path C (genuinely 1.5%):  Q1 > 70%, Q2 median offset < 30 min.
--                             Data is aligned, drift is appropriate,
--                             rate is just genuinely low. 2D.3 ships
--                             as Option C1 (review-queue tool); accept
--                             the outcome.
--
-- Usage:
--   psql "$DATABASE_URL" -f scripts/investigate_corroboration_gap.sql
--
-- Or run individual sections by copy/paste in psql / pgAdmin.
-- ============================================================


-- ── Q1 — Tournament / timeframe overlap ─────────────────────
--
-- For a sample of 50 recently-unresolved Kalshi tennis records,
-- count FL tennis fixtures within ±2 hours of each Kalshi kickoff.
-- ±2hr is broad enough to absorb any Q2 misalignment so this
-- query measures pure tournament/timeframe overlap, NOT alignment.
--
-- Reading the output:
--   * pct_with_fl_overlap < 30%  → Path A (tournament gap)
--   * pct_with_fl_overlap > 70%  → likely Path B or C (data is
--                                  there; Q2 reveals which)
--   * 30-70%                    → ambiguous; investigate which
--                                 sub-segment is missing FL
--
-- Use ±2 hour window deliberately — wider than 30 min so we
-- separate "no FL fixture exists at all" from "FL fixture exists
-- but timestamps don't align."

\echo '=== Q1. Tournament / timeframe overlap (±2 hour window) ==='

WITH kalshi_sample AS (
  SELECT ticker,
         (raw_payload->>'_kickoff_dt')::timestamptz AS kalshi_kickoff
  FROM sp.kalshi_markets
  WHERE fixture_id IS NULL
    AND raw_payload->>'_sport' = 'Tennis'
    AND raw_payload->>'_kickoff_dt' IS NOT NULL
  ORDER BY last_seen_at DESC
  LIMIT 50
),
overlap AS (
  SELECT s.ticker,
         s.kalshi_kickoff,
         COUNT(fle.fl_event_id) AS fl_fixtures_in_2hr_window
  FROM kalshi_sample s
  LEFT JOIN sp.fl_events fle
    ON fle.sport_id = (SELECT id FROM sp.sports WHERE code = 'tennis')
   AND ABS(EXTRACT(EPOCH FROM (
         to_timestamp((fle.raw_payload->>'START_TIME')::int)
         - s.kalshi_kickoff
       ))) < 7200    -- 2 hours
  GROUP BY s.ticker, s.kalshi_kickoff
)
SELECT
  COUNT(*)                                                   AS total_sampled,
  COUNT(*) FILTER (WHERE fl_fixtures_in_2hr_window > 0)      AS records_with_fl_overlap,
  COUNT(*) FILTER (WHERE fl_fixtures_in_2hr_window = 0)      AS records_with_no_fl_overlap,
  ROUND(100.0 * COUNT(*) FILTER (WHERE fl_fixtures_in_2hr_window > 0)
                / NULLIF(COUNT(*), 0), 1)                    AS pct_with_fl_overlap,
  PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY fl_fixtures_in_2hr_window)
                                                             AS median_fl_fixtures_in_window,
  AVG(fl_fixtures_in_2hr_window)::int                        AS mean_fl_fixtures_in_window
FROM overlap;


-- ── Q1.1 — Per-record breakdown (for triage) ───────────────
--
-- If Q1's pct is somewhere in the 30-70% middle, this query
-- shows which Kalshi records had ZERO FL fixtures in their
-- window. Spot-checking these against FL's web UI tells you
-- whether the issue is "tournament not on FL" or "tournament
-- on FL but ingestion missed it."

\echo ''
\echo '=== Q1.1. Per-record breakdown (Kalshi records with zero FL overlap) ==='

WITH kalshi_sample AS (
  SELECT ticker,
         raw_payload->>'title' AS title,
         raw_payload->>'series_ticker' AS series_ticker,
         (raw_payload->>'_kickoff_dt')::timestamptz AS kalshi_kickoff
  FROM sp.kalshi_markets
  WHERE fixture_id IS NULL
    AND raw_payload->>'_sport' = 'Tennis'
    AND raw_payload->>'_kickoff_dt' IS NOT NULL
  ORDER BY last_seen_at DESC
  LIMIT 50
),
overlap AS (
  SELECT s.ticker, s.title, s.series_ticker, s.kalshi_kickoff,
         COUNT(fle.fl_event_id) AS fl_fixtures_in_2hr_window
  FROM kalshi_sample s
  LEFT JOIN sp.fl_events fle
    ON fle.sport_id = (SELECT id FROM sp.sports WHERE code = 'tennis')
   AND ABS(EXTRACT(EPOCH FROM (
         to_timestamp((fle.raw_payload->>'START_TIME')::int)
         - s.kalshi_kickoff
       ))) < 7200
  GROUP BY s.ticker, s.title, s.series_ticker, s.kalshi_kickoff
)
SELECT ticker, title, series_ticker, kalshi_kickoff
FROM overlap
WHERE fl_fixtures_in_2hr_window = 0
ORDER BY kalshi_kickoff DESC;


-- ── Q2 — Kickoff alignment (proxy from 2C alias-tier data) ──
--
-- Tennis itself doesn't have stored corroboration data
-- (the 1.5% was measured in the read-only 2D.2.5 dry-run, not
-- persisted to resolution_log). To measure kickoff alignment
-- between providers we use 2C alias-tier corroboration data
-- which IS stored — it's for TEAM sports, not tennis, but it
-- tells us whether Kalshi vs FL kickoff timestamps are
-- generally well-aligned across providers.
--
-- If team-sport median offset is < 5 min, providers ARE aligned
-- and tennis's 1.5% rate isn't a kickoff-alignment problem.
-- If team-sport median offset is > 30 min, alignment is a
-- broader provider issue and tennis's rate reflects the same
-- gap.
--
-- Reading the output:
--   * median_offset_min < 5     → providers well-aligned;
--                                 tennis's low rate is NOT
--                                 a kickoff issue (Path A or C)
--   * median_offset_min 5-30    → mild misalignment; broader
--                                 corroboration is suppressed
--                                 by the 30-min drift cutoff
--                                 in some cases
--   * median_offset_min > 30    → severe misalignment; Path B
--                                 candidate (fix drift_sec
--                                 or kickoff inference)

\echo ''
\echo '=== Q2. Kickoff alignment (proxy: 2C alias-tier corroborated team-sport pairs) ==='

WITH alias_corroborated AS (
  -- 2C alias-tier rows where corroboration fired and a fixture
  -- was linked. The reason_detail captures the resolved
  -- fixture_id and the provider-side kickoff inference.
  SELECT rl.provider,
         rl.provider_record_id,
         rl.fixture_id,
         (rl.reason_detail->>'has_cross_provider_corroboration')::boolean AS corroborated,
         rl.decided_at
  FROM sp.resolution_log rl
  WHERE rl.reason_code = 'alias'
    AND rl.fixture_id IS NOT NULL
    AND rl.decided_at > NOW() - INTERVAL '14 days'
    AND (rl.reason_detail->>'has_cross_provider_corroboration')::boolean = true
),
provider_kickoff AS (
  -- Pull each provider's stored kickoff_at on the matched fixture.
  -- The fixture's kickoff_at is the strict-tier-applied value
  -- (whichever provider linked first). To get BOTH providers'
  -- kickoff intent, we need to look at the provider rows.
  SELECT ac.fixture_id,
         ac.decided_at,
         f.kickoff_at AS fixture_kickoff,
         (km.raw_payload->>'_kickoff_dt')::timestamptz AS kalshi_kickoff,
         to_timestamp((fle.raw_payload->>'START_TIME')::int) AS fl_kickoff
  FROM alias_corroborated ac
  INNER JOIN sp.fixtures f ON f.id = ac.fixture_id
  LEFT JOIN sp.kalshi_markets km ON km.fixture_id = ac.fixture_id
  LEFT JOIN sp.fl_events fle    ON fle.fixture_id = ac.fixture_id
  WHERE km.ticker IS NOT NULL
    AND fle.fl_event_id IS NOT NULL
  -- Limit to fixtures that BOTH providers linked. These are the
  -- pairs where we can compare kickoffs directly.
)
SELECT
  COUNT(*)                                                   AS pairs_compared,
  ROUND(AVG(ABS(EXTRACT(EPOCH FROM
        (kalshi_kickoff - fl_kickoff))) / 60))               AS mean_offset_min,
  PERCENTILE_CONT(0.5) WITHIN GROUP (
    ORDER BY ABS(EXTRACT(EPOCH FROM (kalshi_kickoff - fl_kickoff))) / 60
  )                                                          AS median_offset_min,
  PERCENTILE_CONT(0.95) WITHIN GROUP (
    ORDER BY ABS(EXTRACT(EPOCH FROM (kalshi_kickoff - fl_kickoff))) / 60
  )                                                          AS p95_offset_min,
  MAX(ABS(EXTRACT(EPOCH FROM (kalshi_kickoff - fl_kickoff))) / 60)
                                                             AS max_offset_min
FROM provider_kickoff
WHERE kalshi_kickoff IS NOT NULL
  AND fl_kickoff IS NOT NULL;


-- ── Q2.1 — Tennis-specific kickoff sample (manual runbook) ──
--
-- Q2 above uses team-sport data as a proxy. For tennis-specific
-- alignment, the operator must manually identify same-match
-- Kalshi+FL pairs (since tennis records don't stored
-- corroboration data). Steps:
--
--   1. Re-run scripts/dry_run_fuzzy_tier.py with --show-examples 10
--      to see the tickers of the 3 corroborated tennis records.
--   2. For each ticker, paste it into the query below to compare
--      kickoffs.
--   3. Repeat for 20 NON-corroborated tennis tickers from the
--      anchor_passed bucket (also visible in --show-examples).
--
-- Manual lookup query template:

\echo ''
\echo '=== Q2.1. Tennis kickoff sample template (manual lookup) ==='
\echo 'Replace PASTE_KALSHI_TICKER_HERE / PASTE_FL_EVENT_ID_HERE.'
\echo 'Run separately for each pair after identifying via dry-run --show-examples.'
\echo ''

-- Template (uncomment + parameterize per pair):
--
-- SELECT 'kalshi' AS provider,
--        ticker,
--        raw_payload->>'title' AS title,
--        (raw_payload->>'_kickoff_dt')::timestamptz AS kickoff
-- FROM sp.kalshi_markets WHERE ticker = 'PASTE_KALSHI_TICKER_HERE'
-- UNION ALL
-- SELECT 'fl',
--        fl_event_id,
--        raw_payload->>'HOME_NAME' || ' vs ' || (raw_payload->>'AWAY_NAME'),
--        to_timestamp((raw_payload->>'START_TIME')::int)
-- FROM sp.fl_events WHERE fl_event_id = 'PASTE_FL_EVENT_ID_HERE';


-- ── Q3 — Drift window appropriateness ──────────────────────
--
-- If Q2 shows median offset > 30 min on actual same-match pairs
-- (or Q2.1 manual sample shows the same), the 30-min drift
-- window in find_fixture is too tight for tennis. Q3 measures
-- how many additional FL fixtures fall into a wider window so
-- you can size the trade-off.
--
-- For each unresolved Kalshi tennis record, count FL tennis
-- fixtures at three drift bands:
--   * within ±30 min     (current window — what 2D.2 queries)
--   * within ±60 min     (proposed widened window)
--   * within ±2 hours    (broader sanity check)
--
-- Reading the output:
--   * If "30 min" and "60 min" rates are similar → 30 min is
--     appropriate; widening doesn't help.
--   * If "60 min" rate is much higher → tennis kickoffs commonly
--     drift by 30-60 min (rescheduling, match-overruns); widen
--     drift_sec to 60*60 in 2D.3.
--   * If "2 hours" >> "60 min" → drift is multi-hour; either
--     fix kickoff inference or accept that the corroboration
--     signal is unreliable for tennis.

\echo ''
\echo '=== Q3. Drift window comparison (30 / 60 / 120 min) ==='

WITH kalshi_sample AS (
  SELECT ticker,
         (raw_payload->>'_kickoff_dt')::timestamptz AS kalshi_kickoff
  FROM sp.kalshi_markets
  WHERE fixture_id IS NULL
    AND raw_payload->>'_sport' = 'Tennis'
    AND raw_payload->>'_kickoff_dt' IS NOT NULL
  ORDER BY last_seen_at DESC
  LIMIT 200
),
drift_counts AS (
  SELECT s.ticker,
         COUNT(*) FILTER (
           WHERE ABS(EXTRACT(EPOCH FROM (
             to_timestamp((fle.raw_payload->>'START_TIME')::int)
             - s.kalshi_kickoff
           ))) < 1800
         ) AS fl_within_30min,
         COUNT(*) FILTER (
           WHERE ABS(EXTRACT(EPOCH FROM (
             to_timestamp((fle.raw_payload->>'START_TIME')::int)
             - s.kalshi_kickoff
           ))) < 3600
         ) AS fl_within_60min,
         COUNT(*) FILTER (
           WHERE ABS(EXTRACT(EPOCH FROM (
             to_timestamp((fle.raw_payload->>'START_TIME')::int)
             - s.kalshi_kickoff
           ))) < 7200
         ) AS fl_within_120min
  FROM kalshi_sample s
  LEFT JOIN sp.fl_events fle
    ON fle.sport_id = (SELECT id FROM sp.sports WHERE code = 'tennis')
  GROUP BY s.ticker
)
SELECT
  COUNT(*)                                                   AS sampled,
  ROUND(100.0 * COUNT(*) FILTER (WHERE fl_within_30min > 0)
                / NULLIF(COUNT(*), 0), 1)                    AS pct_with_fl_within_30min,
  ROUND(100.0 * COUNT(*) FILTER (WHERE fl_within_60min > 0)
                / NULLIF(COUNT(*), 0), 1)                    AS pct_with_fl_within_60min,
  ROUND(100.0 * COUNT(*) FILTER (WHERE fl_within_120min > 0)
                / NULLIF(COUNT(*), 0), 1)                    AS pct_with_fl_within_120min,
  AVG(fl_within_30min)::numeric(5,2)                         AS mean_fl_at_30min,
  AVG(fl_within_60min)::numeric(5,2)                         AS mean_fl_at_60min,
  AVG(fl_within_120min)::numeric(5,2)                        AS mean_fl_at_120min
FROM drift_counts;


-- ── Interpretation guide → 2D.3 ship path ──────────────────

\echo ''
\echo '=== Interpretation guide ==='
\echo ''
\echo 'Map the Q1+Q2+Q3 outputs to one of three 2D.3 design paths:'
\echo ''
\echo '  PATH A — Tournament gap (FL doesn''t cover Kalshi tennis tournaments)'
\echo '    Trigger:    Q1.pct_with_fl_overlap < 30%'
\echo '    Implication: 2D.3 cannot recover what FL never ingests.'
\echo '    Ship:       2D.3 as Option C1 (review-queue tool, current weights).'
\echo '                Phase 2D.5 expands DEFAULT_FL_SPORT_IDS or adds'
\echo '                Challenger/ITF per-tournament fetches.'
\echo ''
\echo '  PATH B — Kickoff misalignment'
\echo '    Trigger:    Q1.pct_with_fl_overlap > 70%'
\echo '                AND Q2.median_offset_min > 30  (or Q2.1 manual sample)'
\echo '                AND Q3.pct_with_fl_within_60min >> Q3.pct_with_fl_within_30min'
\echo '    Implication: Data IS aligned, but the 30-min drift_sec is too tight.'
\echo '    Ship:       2D.3 with adjusted drift_sec (e.g. 60*60).'
\echo '                Re-run dry_run_fuzzy_tier.py with new drift to estimate'
\echo '                the lift before locking 2D.3.'
\echo ''
\echo '  PATH C — Genuinely 1.5% (data aligned, drift appropriate)'
\echo '    Trigger:    Q1.pct_with_fl_overlap > 70%'
\echo '                AND Q2.median_offset_min < 30'
\echo '                AND Q3 shows minimal lift from widening drift'
\echo '    Implication: The 1.5% rate is the actual provider-overlap signal.'
\echo '    Ship:       2D.3 as Option C1 (review-queue tool, current weights).'
\echo '                Accept the outcome; 2D.4 day-7 review confirms.'
\echo ''
\echo '  AMBIGUOUS — None of the above fits cleanly'
\echo '    Action:     Re-read raw output with operator + designer; might'
\echo '                indicate Q1.1 per-record patterns worth investigating.'
\echo '                Don''t lock 2D.3 without clearer signal.'
