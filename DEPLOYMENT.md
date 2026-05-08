# Deployment notes

This file documents deployment-time configuration that is **not** in the repo. The codebase ships with sensible defaults; the items below are operator-set on Railway (or whichever host).

## Phase 0 — required env vars

### `WEB_CONCURRENCY`

Set to **`2`** (minimum) in Railway's service environment.

**What it does:** spawns multiple uvicorn workers so a single slow request (typically a third-party API call) can't block every concurrent user. The `/healthz` outage on May 5 2026 was caused, in part, by the deployment running a single worker that hung when an outbound call exhausted the httpx connection pool.

**How to set it on Railway:**

1. Open the service in Railway dashboard.
2. **Variables** tab → add `WEB_CONCURRENCY=2`.
3. The service redeploys automatically.

**Verification:** after deploy, confirm two workers are running by checking deploy logs — uvicorn prints `Started server process [pid]` once per worker.

If your start command is something like `uvicorn main:app --host 0.0.0.0 --port $PORT`, append `--workers ${WEB_CONCURRENCY:-1}` so the env var is consumed. Or set the start command directly:

```
uvicorn main:app --host 0.0.0.0 --port $PORT --workers ${WEB_CONCURRENCY:-2}
```

**When to revisit:** the architecture (v1.2 §10.1) projects Railway-with-2-workers is sufficient through Phase 3. Increase to 4 if request latency p95 climbs above 200ms under typical load, or if you start serving multi-region traffic.

### `STOCHVERSE_LOG_FORMAT` (optional)

Default: JSON output to stdout (Railway-friendly).

Set to `console` for human-readable colored output during local development. Railway should leave it unset.

```
STOCHVERSE_LOG_FORMAT=console
```

## Phase 0 — structured logging

After deploy, every outbound provider call emits a `provider_api_call` JSON event with the schema:

```json
{
  "event": "provider_api_call",
  "provider": "fl",
  "endpoint": "/v1/events/list",
  "status": 200,
  "latency_ms": 142,
  "response_bytes": 8412,
  "timestamp": "2026-05-07T19:00:00.000000Z"
}
```

Cache hits emit the same event with `status: 0` and `extra: {"cache_hit": true}`. Use the difference between request rate and `cache_hit=false` count to compute cache effectiveness.

These events match the Phase 1 `provider_api_calls` table schema (architecture doc §6.3), so the Phase 1 migration can backfill historical call volume from these logs.

## Phase 1E — Backfill scripts

Phase 1E ships standalone scripts under `scripts/` that pump historical data through the same ingestion pipeline as live ingestion. Idempotent — safe to re-run.

### When to run

- After provisioning a fresh Postgres database (Phase 1A) — gives the resolver a corpus to tune against on day 1.
- After a long ingestion outage — catches up missed days from FL.
- During development against a fresh local docker-compose Postgres.

### How to run

Locally against a target Neon database:

```bash
DATABASE_URL="postgresql://...neon.tech/...?sslmode=require" \
  python scripts/backfill_fl.py --days 7
```

Or via Makefile (uses local docker-compose Postgres):

```bash
make backfill-fl
```

### What gets backfilled

| Provider | Range covered | Notes |
|---|---|---|
| FL | ±7 days from today | FL `/v1/events/list` only serves ±7 days. Beyond that requires per-tournament historical queries (Phase 2 PR). |
| Kalshi | n/a — see below | No standalone Kalshi backfill script. |

#### Why no Kalshi backfill script

Kalshi REST exposes only currently-active and recently-closed events via `/events`, and the live ingestion (`ingestion/kalshi.py`) already calls `paginate()` every 30s to pull both open and closed status. A standalone backfill would duplicate the live poller without adding range — Kalshi ages closed events out after a series-specific retention window, and there's no broad-spectrum historical endpoint we can hit through the existing SDK.

When deeper historical Kalshi data is genuinely needed (Phase 2 resolver tuning, settlement audits), the right path is a per-ticker retrieval against Kalshi's `/markets/{ticker}` endpoint with a starting list of historical tickers (settlement CSVs or similar). That's separate engineering, deferred until the requirement is concrete.

### Verification

After backfill completes, sanity-check what landed:

```sql
-- FL: count + date range
SELECT COUNT(*),
       MIN((raw_payload->>'START_TIME')::int) AS earliest_unix,
       MAX((raw_payload->>'START_TIME')::int) AS latest_unix
FROM sp.fl_events;

-- Kalshi: count by market_type, what's covered
SELECT market_type, COUNT(*)
FROM sp.kalshi_markets
GROUP BY market_type
ORDER BY 2 DESC;

-- Both: how recent is the data
SELECT 'fl_events' AS table, MIN(last_seen_at), MAX(last_seen_at) FROM sp.fl_events
UNION ALL
SELECT 'kalshi_markets', MIN(last_seen_at), MAX(last_seen_at) FROM sp.kalshi_markets;
```

### Cost / time

- FL backfill (±7 days × ~17 sports): ~3-5 minutes against Neon US-West.

The script is network-bound on the FL API, not the database.

## Phase 2A.5 — Bootstrap sp.teams + sp.team_aliases from legacy

One-time migration that seeds the SP entity layer's team data from
`public.entities` (entity_type='team') and `public.entity_aliases`.
Pre-seeds the alias table so Phase 2B's strict-tier resolver has
data to match against on day 1, instead of cold-starting from zero
coverage.

### When to run

Once, after the seed_sp_sports migration is applied. Before Phase 2B
ships its matcher. The bootstrap is idempotent — re-running is safe
but produces no new rows after the first successful run.

### How to run

Locally against production Neon:

```bash
# Verify migrations are at head — the seed_sp_sports migration must
# have applied. Required revision: d8e717ed79dd or later.
DATABASE_URL="<prod-Neon>" alembic current

# Dry-run first — reads everything, writes nothing, logs counts.
DATABASE_URL="<prod-Neon>" python scripts/bootstrap_sp_teams.py --dry-run

# If the dry-run counts look reasonable (per-sport teams >= legacy
# entity counts), run for real:
DATABASE_URL="<prod-Neon>" python scripts/bootstrap_sp_teams.py
```

Or via Makefile (uses local docker-compose Postgres):

```bash
make bootstrap-sp-teams
make bootstrap-sp-teams ARGS="--dry-run"
```

### Verification

After running, check the per-sport coverage:

```sql
SELECT
  s.name,
  COUNT(DISTINCT t.id)            AS teams,
  COUNT(a.id)                     AS aliases,
  COUNT(DISTINCT a.team_id)       AS teams_with_at_least_one_alias
FROM sp.sports s
LEFT JOIN sp.teams t        ON t.sport_id = s.id
LEFT JOIN sp.team_aliases a ON a.team_id = t.id AND a.source = 'legacy_bootstrap'
GROUP BY 1
ORDER BY 1;
```

Expected: most active sports (Soccer, Basketball, Hockey, Baseball,
Tennis, Football) should show non-zero teams + aliases. Sports with
no legacy data (Snooker, Darts, etc. — depends on your historical
ingestion coverage) may show zero; that's not a bootstrap failure,
it's a fact about the legacy data.

### Document the baseline

After bootstrap completes, copy the per-sport counts table into
`PROJECT_STATE.md`. Phase 2B's parallel-run will reference this
baseline when assessing whether strict-tier coverage is healthy
(architecture v1.4 §13 / Phase 2B design doc §2 — the **<60%
coverage** threshold).

### Limitations / known scoping

- Bootstrap migrates `public.entities` (team-typed) and
  `public.entity_aliases`. Player and league entities are not
  migrated — out of scope for the resolver's matching surface.
- `public.markets` (sub-market identity) is **not** bootstrapped;
  that's deferred to Phase 2C alias-tier work.
- `country_code` on `sp.teams` is left NULL — legacy schema doesn't
  carry it. Population is a future concern (Phase 4 if/when needed
  for OddsAPI integration).
- Bootstrapped aliases get `source='legacy_bootstrap'` and
  `confidence=0.95`. The 0.05 gap from 1.0 distinguishes them from
  human-curated aliases (added later via the review queue), so a
  bootstrapped alias that produces a false-positive can be
  identified and downweighted/removed without touching curated data.

## Phase 2A.6 — Bootstrap sp.competitions (Kalshi only)

Phase 2A.6 seeds `sp.competitions` from distinct (sport, series_base)
tuples observed in `sp.kalshi_markets` so the strict-tier matcher's
competition gate has data to resolve against. FL competitions are
deferred to Phase 2C — `sp.fl_events.raw_payload` doesn't currently
carry a tournament-level sport_id, so a clean FL seed needs an
ingestion change first. Until then, FL signals take an explicit
`fl_transitional_sport_only` path through the matcher (logged on
every successful match).

### When to run

Once, after `bootstrap_sp_teams` has been applied and the Phase 2B
migration is at head. Before running the first Phase 2B parallel-run
pass — without this step the Kalshi side of the matcher would
sport-only-fall-back on every record (silently degraded gate).

### How to run

```bash
DATABASE_URL=<prod-Neon> python scripts/bootstrap_sp_competitions.py --dry-run
DATABASE_URL=<prod-Neon> python scripts/bootstrap_sp_competitions.py
```

Or via Makefile:

```bash
make bootstrap-sp-competitions
make bootstrap-sp-competitions ARGS="--dry-run"
```

Idempotent — a re-run is a no-op for any series_base already covered.

### Verification

```sql
SELECT s.name              AS sport,
       COUNT(c.id)         AS competitions,
       SUM(jsonb_array_length(c.kalshi_series_bases)) AS kalshi_bases_indexed
FROM sp.sports s
LEFT JOIN sp.competitions c ON c.sport_id = s.id
GROUP BY 1
ORDER BY 1;
```

After Phase 2B parallel-run starts, audit the per-resolution
competition decisions:

```sql
-- Kalshi distribution of competition gate outcomes
SELECT reason_detail->>'competition_resolution' AS resolution,
       COUNT(*) AS count
FROM sp.resolution_log
WHERE provider = 'kalshi'
  AND decided_at > NOW() - INTERVAL '24 hours'
GROUP BY 1
ORDER BY 2 DESC;

-- FL transitional-path coverage (will be 100% of FL strict matches
-- until Phase 2C lands)
SELECT COUNT(*) FILTER (WHERE reason_detail ? 'fl_transitional_sport_only') AS transitional,
       COUNT(*)                                                              AS fl_strict_total
FROM sp.resolution_log
WHERE provider = 'fl'
  AND reason_code = 'strict'
  AND decided_at > NOW() - INTERVAL '24 hours';
```

### Limitations

- canonical_name = series_base (e.g., `KXEPL`). Display polish
  (mapping `KXEPL` → "Premier League") is a Phase 2C concern via
  manual_review or a name-mapping pass.
- FL `fl_tournament_stage_ids` array stays empty; CompetitionResolver
  returns `unresolvable` for FL hints. The matcher routes around this
  via the FL transitional path — no FL strict match is gated on
  competition match in 2A.6.
- A Kalshi explicit-comp signal arriving on a fixture FL created
  earlier with NULL competition_id will still LINK — `find_fixture`
  uses an equal-or-NULL filter precisely to avoid forking one
  logical fixture into two during the 2A.6 → 2C transition. When
  this happens the matcher stamps two flags on `resolution_log.reason_detail`:
  ```
  linked_to_null_comp_fixture: true
  null_comp_fixture_pending_backfill: <fixture-uuid>
  ```
  Phase 2C's reconciliation query becomes a one-liner:
  ```sql
  SELECT (reason_detail->>'null_comp_fixture_pending_backfill')::uuid AS fixture_id,
         (reason_detail->>'competition_id')::uuid                     AS expected_competition_id
  FROM sp.resolution_log
  WHERE reason_detail ? 'linked_to_null_comp_fixture'
    AND decided_at > '<2A.6 deploy timestamp>';
  ```

### FL transitional sub-paths

Every successful FL strict-tier match in 2A.6 stamps both
`fl_transitional_sport_only=true` AND a `fl_transitional_path`
sub-flag describing which of three reachable paths the match took:

| `fl_transitional_path`            | Meaning                                                                                    |
|-----------------------------------|--------------------------------------------------------------------------------------------|
| `matched_null_comp_fixture`       | Typical 2A.6 case. Existing fixture had NULL competition_id; FL joined it sport-only.      |
| `matched_existing_comp_fixture`   | Uncommon: fixture was previously created by Kalshi with explicit competition_id. FL is now joining sport-only. Phase 2C must verify FL's resolved comp aligns with what Kalshi wrote. |
| `created_null_comp_fixture`       | FL was first to see this fixture; new row created with NULL competition_id. Awaits Phase 2C to set the column. |

Day-7 audit:

```sql
SELECT reason_detail->>'fl_transitional_path' AS path, COUNT(*)
FROM sp.resolution_log
WHERE provider = 'fl'
  AND reason_code = 'strict'
  AND decided_at > NOW() - INTERVAL '24 hours'
GROUP BY 1 ORDER BY 2 DESC;
```

Most should be `matched_null_comp_fixture` or `created_null_comp_fixture`.
A material `matched_existing_comp_fixture` count means Kalshi-Kalshi-FL
order is common in your data — fine, but flags real comp-asymmetry
work for 2C.

## Phase 2B — Strict-tier resolver parallel-run

Phase 2A.5 baseline is in place. Phase 2B's standalone runner
(`scripts/run_resolver_pass.py`) drives the parallel-run period
defined in `PHASE_2B_DESIGN.md` §2.

### Migration

```bash
DATABASE_URL=<prod-Neon> alembic upgrade head
```

Applies revision `bdf12a30e49b`:
- Creates `sp.resolver_runs` (one row per pass; queryable metrics)
- Alters `sp.fixtures.competition_id` to nullable

### How to run

```bash
# Smoke-run on a small slice first
DATABASE_URL=<prod-Neon> python scripts/run_resolver_pass.py \
    --provider kalshi --limit 100

# Full passes
DATABASE_URL=<prod-Neon> python scripts/run_resolver_pass.py --provider kalshi
DATABASE_URL=<prod-Neon> python scripts/run_resolver_pass.py --provider fl

# Cron-tagged pass (for the 02:00 UTC daily during parallel-run)
DATABASE_URL=<prod-Neon> python scripts/run_resolver_pass.py \
    --provider kalshi --run-mode cron
```

Or via Makefile:

```bash
make resolver-pass-kalshi
make resolver-pass-kalshi ARGS="--limit 100"
make resolver-pass-fl
```

### What each run produces

- For each provider record matched by the strict tier: an UPDATE
  setting `fixture_id` on the provider table + an INSERT into
  `sp.resolution_log` with confidence 0.98, `reason_code='strict'`,
  and full `reason_detail` JSONB. Both writes happen in one
  transaction per the leak-fix discipline.
- For each match-attempt that misses any of the 4 gates: an INSERT
  into `sp.resolution_log` with `fixture_id IS NULL`, `confidence=0`,
  `reason_code='no_match'`, and the full `reason_detail` JSONB
  capturing which gate rejected the signal. Provider record's
  `fixture_id` stays NULL. Day-7 review queries
  `reason_detail->>'fail_reason'` against this log.
- For provider records the extractor can't produce a FixtureSignal
  from (Kalshi outright/series/tournament shapes — no per-fixture
  semantics): no `resolution_log` row (no signal → no reason_detail
  to log). Tracked in the run-level
  `extra->>'signal_extraction_skipped'` counter so the
  records_scanned breakdown reconciles.
- One row per pass in `sp.resolver_runs` with `provider`,
  `run_mode`, counters, latency p95, and
  `extra.signal_extraction_skipped`.

### Day-7 parallel-run report

```sql
SELECT provider,
       SUM(records_scanned)                 AS scanned,
       SUM(auto_applies)                    AS auto_applies,
       SUM(no_match)                        AS no_match,
       SUM((extra->>'signal_extraction_skipped')::int) AS extract_skipped,
       SUM(crashes)                         AS crashes,
       SUM(legacy_diff_count)               AS kalshi_legacy_diffs,
       ROUND(100.0 * SUM(auto_applies) / NULLIF(SUM(records_scanned), 0), 2) AS coverage_pct,
       ROUND(100.0 * SUM(legacy_diff_count) / NULLIF(SUM(auto_applies), 0), 2) AS kalshi_fp_pct,
       MAX(latency_p95_ms)                  AS worst_latency_p95_ms
FROM sp.resolver_runs
WHERE started_at > NOW() - INTERVAL '7 days'
  AND run_mode IN ('standalone', 'cron')   -- exclude post-2E live activity
GROUP BY 1
ORDER BY 1;
```

Thresholds from design doc §2:
- Kalshi false-positive rate > 1.0% / 24h → tighten drift to 15 min
- Coverage < 60% sustained → review extraction
- Latency p95 > 5min → switch to LISTEN/NOTIFY (2E.fix)
- Crashes > 5/day → halt parallel-run

### Why isn't strict tier matching? — fail_reason audit

Every no_match decision is logged with the gate that rejected it.
Use this as the first stop when coverage looks low:

```sql
SELECT provider,
       reason_detail->>'fail_reason' AS fail_reason,
       COUNT(*)                      AS n,
       ROUND(100.0 * COUNT(*) /
             SUM(COUNT(*)) OVER (PARTITION BY provider), 1) AS pct_of_provider
FROM sp.resolution_log
WHERE reason_code = 'no_match'
  AND decided_at > NOW() - INTERVAL '24 hours'
GROUP BY 1, 2
ORDER BY 1, 3 DESC;
```

Common values + remediation:
- `alias_resolution_incomplete` — team didn't normalize to a seeded
  alias. Check `reason_detail->>'home_resolved'` /
  `away_resolved` to see which side. Phase 2C alias tier will
  recover most of these.
- `kalshi_competition_unresolvable` — `series_ticker` strips to a
  base not in `sp.competitions.kalshi_series_bases`. Re-run
  `bootstrap_sp_competitions.py` against the latest Kalshi data.
- `sport_not_classified` — `_sport` field empty/unknown on the
  raw payload. Ingestion-side classification problem, not resolver.
- `kickoff_at_missing` / `kickoff_confidence_below_threshold` —
  payload lacked an explicit kickoff timestamp; ticker fallback
  gave 0.6 confidence. Strict tier requires ≥0.85.
- `home_and_away_same_team` — alias data bug; both sides resolved
  to the same `team_id`. Manual triage on the specific alias.

### FL spot-check (manual, not a query)

`legacy_diff_count` is NULL for FL — there's no automatic comparator.
Instead, pick 5 random FL strict-tier auto-applies per day and
manually verify the chosen fixture matches the underlying real-world
game:

```sql
SELECT decided_at, provider_record_id, fixture_id,
       reason_detail->>'home_team_id' AS home,
       reason_detail->>'away_team_id' AS away
FROM sp.resolution_log
WHERE provider = 'fl'
  AND reason_code = 'strict'
  AND decided_at > NOW() - INTERVAL '24 hours'
ORDER BY random()
LIMIT 5;
```

Cross-reference with FL's web UI. ≥1 of 5 wrong on any day halts
FL parallel-run; 0 of 5 wrong for 5 consecutive days = FL precision
acceptable.

## Future phases

When Phase 1 ships, this file will be amended with:
- Postgres provisioning (`DATABASE_URL`)
- Alembic migration commands
- `make migrate` invocation on deploy
- Worker process for ingestion modules

When Phase 2 ships, additionally:
- Resolver loop start command (in same process or separate worker)
- Admin UI password env var (`ADMIN_PASSWORD_HASH`, `SESSION_SECRET`)

Until then, only `WEB_CONCURRENCY` is required.
