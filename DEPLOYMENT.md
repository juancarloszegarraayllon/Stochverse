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

## Phase 2A.7 — sp.fl_events.sport_id (recover sport context per row)

FL ingestion polls per-sport but pre-2A.7 didn't persist sport
context anywhere — neither column nor `raw_payload` top-level. The
resolver runner had no way to pass `sport=...` to
`FLResolverModule.extract_signal`, so every FL signal hit the
matcher's gate 2 (`sport_not_classified`) and got rejected. First
production FL pass produced **0/19,753 auto-applies**.

Phase 2A.7 fixes the architectural gap with a new column + ingestion
update + thin backfill wrapper. No design doc changes — this is a
data-shape fix discovered post-2B.

### Migration

```bash
DATABASE_URL=<prod-Neon> alembic upgrade head
```

Applies revision `7c3f9b1a2e58`:
- Adds `sp.fl_events.sport_id INTEGER REFERENCES sp.sports(id)` (nullable; backfilled by ingestion).
- Creates partial index `ix_fl_events_sport_unresolved` on `(sport_id, last_seen_at DESC) WHERE fixture_id IS NULL` — supports the resolver runner's hot query.

### Backfill

```bash
DATABASE_URL=<prod-Neon> python scripts/backfill_sp_fl_events_sport_id.py
```

Or:

```bash
make backfill-sp-fl-events-sport-id
make backfill-sp-fl-events-sport-id ARGS="--skip-backfill"   # residual report only
```

Mechanics: re-runs the standard FL backfill (`scripts/backfill_fl.py`)
for indent_days `-7..+7`. Phase 2A.7's ingestion change populates
`sport_id` on every UPSERT, so existing rows in the FL ±7 day window
get backfilled in one pass. The script reports pre/post NULL counts
and per-sport coverage, plus a residual count for rows that stay
NULL.

Rows that legitimately stay `sport_id IS NULL`:
- Events outside the FL ±7 day window (legacy historical rows; no
  FL endpoint serves them today, so they drain naturally as old
  fixtures roll off).
- Events for FL sport_ids not in `FL_SPORT_ID_TO_SP_NAME`. The
  ingestion logs `ingestion.fl.sport_id_unmapped` warnings naming
  the unrecognized FL ids — add them to the map (or seed the
  matching sp.sports row) and re-run.

### Verification

```sql
-- Per-sport coverage on the new column.
SELECT s.name AS sport, COUNT(fle.fl_event_id) AS rows
FROM sp.fl_events fle
INNER JOIN sp.sports s ON s.id = fle.sport_id
GROUP BY 1 ORDER BY 2 DESC;

-- Residual NULLs (expected: rows outside FL's ±7 day window).
SELECT COUNT(*) FROM sp.fl_events WHERE sport_id IS NULL;
```

### What 2A.7 does NOT do

- Does not change `raw_payload` content. Older rows still have
  `SPORT_ID = null` inside their payloads — that field was never
  populated by FL, and the resolver doesn't read it now anyway.
- Does not unlock historical FL fixtures beyond the ±7 day window.
  Out of scope until a per-tournament historical fetch is added.

## Phase 2C.2.5 — alias-tier dry-run calibration

Read-only calibration script. Runs Phase 2C.2's `structurally_normalize`
+ fixture-level scorer pipeline against unresolved provider records
and reports the predicted bucket distribution before the matcher
in Phase 2C.3 commits to the threshold choice.

**No DB writes.** Reads `sp.kalshi_markets` / `sp.fl_events`,
`sp.teams`, and (optionally) `sp.fixtures` for the cross-provider
corroboration pass. Resolver crons continue to run at strict@2a.6.

### How to run

```bash
DATABASE_URL=<prod-Neon> python scripts/dry_run_alias_tier.py \
    --provider kalshi --sport-code tennis --limit 600

# Show top 5 examples per bucket
DATABASE_URL=<prod-Neon> python scripts/dry_run_alias_tier.py \
    --provider kalshi --sport-code tennis --limit 600 \
    --show-examples 5

# Faster — skip the with-corroboration pass (no sp.fixtures lookups)
DATABASE_URL=<prod-Neon> python scripts/dry_run_alias_tier.py \
    --provider kalshi --sport-code tennis --skip-corroboration
```

Or via Makefile:

```bash
make dry-run-alias-tier ARGS="--provider kalshi --sport-code tennis --limit 600"
```

### What the report tells you

Two passes per record:
1. **Without corroboration** — pure name match. Most pessimistic case.
2. **With corroboration** — `find_fixture` lookup against
   `sp.fixtures` adds +0.20 when the candidate (home_id, away_id)
   pair has an existing fixture at the kickoff window.

The delta between the two passes answers: how much of alias-tier
auto-apply gain depends on cross-provider corroboration?

Bucket distribution: `auto_apply` (≥ 0.85) / `review_queue`
(0.70–0.84) / `no_match` (< 0.70) / `anchor_failed` (no surname
match found) / `extraction_skipped` (extract_signal returned None).

### Calibration decision input

If `auto_apply` is much smaller than the design-doc prediction,
options before 2C.3:
- (a) Accept large day-0 review queue (drains via reviewer write-back which compounds).
- (b) Lower personal-path auto-apply threshold from 0.85 (with stricter top-2 margin).
- (c) Bump corroboration weight from +0.20 to +0.25.

The dry-run output is the data that picks among (a)/(b)/(c).

## Phase 2C.3 — Alias tier (TieredMatcher: strict → alias → review)

The 2B parallel-run cron (`resolver-cron-kalshi`, `resolver-cron-fl`)
keeps the same 02:00 / 02:15 UTC schedule. After 2C.3 it runs
`TieredMatcher` instead of bare `StrictMatcher` — same entry point,
same DATABASE_URL, no Railway-side changes needed.

### What changes per pass

- **Strict tier** runs first (unchanged from 2B). On STRICT hit:
  same auto-apply path as before (UPDATE provider.fixture_id +
  INSERT resolution_log row stamped `strict@2a.6`).
- **Alias tier** runs only when strict returns `NO_MATCH`. Tries
  fuzzy team-name matching with cross-team-collision detection
  and exact-match-wins. On ALIAS hit: UPDATE provider.fixture_id
  + INSERT resolution_log row stamped `alias@2c.0` + INSERT new
  `sp.team_aliases` row (`source='alias_tier'`, `confidence=<match score>`).
- **Review queue**: when the alias tier detects a cross-team
  collision (multiple candidates above 0.78 with no single 1.0
  winner) OR confidence lands in 0.70–0.84, an `sp.review_queue`
  row is inserted with the candidate team_ids. Phase 2F admin UI
  is the consumer.
- **Tennis (and individual sports generally)** early-exit with
  `reason_code='no_match'`, `fail_reason='deferred_to_2d'`. ~180
  Kalshi tennis records / day until Phase 2D ships.

### Dual-tier logging (Phase 2C design D.4)

When alias rescues a record strict missed, BOTH `resolution_log`
rows are written in the same atomic transaction:

- Row 1: strict's `no_match` (`resolver_version='strict@2a.6'`,
  fail_reason='alias_resolution_incomplete'`)
- Row 2: alias's hit (`resolver_version='alias@2c.0'`,
  reason_code='alias'`)

Strict's "I tried and failed" is forensic data — the day-7 review
query joins the two via `(provider, provider_record_id)` to see
the full per-record decision history.

### sp.resolver_runs.extra additions

The single `auto_applies` aggregate now decomposes into
per-tier counters in `extra`:

```json
{
  "limit": null,
  "chunk_size": 200,
  "signal_extraction_skipped": 286,
  "strict_auto_applies":       312,
  "alias_auto_applies":         68,
  "alias_review_queue":        248,
  "alias_tennis_deferred":     180
}
```

`auto_applies` (top-level column) = `strict_auto_applies + alias_auto_applies`.

### Day-7 query additions

The full report is in `scripts/parallel_run_day7_report.sql`. The
2C.3-relevant additions:

```sql
-- Per-tier auto-apply breakdown
SELECT date_trunc('day', started_at)::date         AS day,
       provider,
       SUM((extra->>'strict_auto_applies')::int)   AS strict,
       SUM((extra->>'alias_auto_applies')::int)    AS alias,
       SUM((extra->>'alias_review_queue')::int)    AS review,
       SUM((extra->>'alias_tennis_deferred')::int) AS tennis_deferred
FROM sp.resolver_runs
WHERE started_at > NOW() - INTERVAL '7 days'
  AND run_mode IN ('standalone', 'cron')
GROUP BY 1, 2
ORDER BY 1, 2;

-- Senior-vs-reserve collision patterns (drives Phase 2C.4 sizing)
SELECT reason_detail->>'home_canonical' AS canonical,
       COUNT(*) AS collisions
FROM sp.resolution_log
WHERE provider IN ('kalshi', 'fl')
  AND reason_code = 'review_queue'
  AND (reason_detail->>'home_collision')::boolean = true
  AND decided_at > NOW() - INTERVAL '7 days'
GROUP BY 1
ORDER BY 2 DESC
LIMIT 50;
```

### Review-queue alert threshold (per design C.1)

Architecture §7.5's `> 100` review-queue alert threshold is raised
to **1,500 for the 14-day post-2C.3 window** to absorb the launch
spike (predicted 400–1,200 review rows on day 1, draining over
~2 weeks). Reverts to 100 on day 15 automatically unless extended
via documented decision.

### Operator action after merge

```bash
git checkout main && git pull
# Railway redeploys the resolver-cron-* services automatically.

# Verify the next 02:00 UTC pass produces alias-tier rows.
psql "$DATABASE_URL" <<'SQL'
SELECT provider,
       SUM((extra->>'strict_auto_applies')::int) AS strict_auto,
       SUM((extra->>'alias_auto_applies')::int)  AS alias_auto,
       SUM((extra->>'alias_review_queue')::int)  AS review_q
FROM sp.resolver_runs
WHERE started_at > NOW() - INTERVAL '24 hours'
  AND run_mode = 'cron'
GROUP BY 1;
SQL
```

Day-7 review (after the parallel-run window): run the full
`scripts/parallel_run_day7_report.sql` and read off the cross-
provider summary (section 9) plus the per-tier breakdown.
**Phase 2C.4 (senior-team disambiguation)** ships only after
day-7 confirms which collision patterns are actually appearing in
production — see PHASE_2C_DESIGN.md.

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

The Kalshi runner SQL filters to sport-shaped rows
(`(raw_payload->>'_is_sport')::boolean = true OR raw_payload->>'category' = 'Sports'`).
`sp.kalshi_markets` stores every Kalshi category we ingest —
Elections, Politics, Crypto, Entertainment, Economics, etc. — and
those non-sport rows dominate `ORDER BY last_seen_at DESC` because
they trade more actively. Without the filter, `--limit 100` produced
~99% non-sport records and zero matcher data. The FL runner needs no
equivalent filter: `ingestion/fl.py` only fetches the sport_ids in
`DEFAULT_FL_SPORT_IDS`, so `sp.fl_events` is sport-shaped by
construction.

### Parallel-run cron schedule

Daily at 02:00 UTC, two scheduled passes via Railway cron services
(`railway.toml`). Staggered 15 minutes apart so they don't compete
for Neon connections during the bulk-load phase.

| Service              | Schedule (UTC) | Command |
|----------------------|----------------|---------|
| `resolver-cron-kalshi` | `0 2 * * *`  | `python scripts/run_resolver_pass.py --provider kalshi --run-mode cron` |
| `resolver-cron-fl`     | `15 2 * * *` | `python scripts/run_resolver_pass.py --provider fl --run-mode cron`     |

#### One-time Railway setup (services don't auto-create)

**Important:** Railway does **not** auto-provision services from
`railway.toml`. The TOML only configures services that already exist
in the project; missing services are silently ignored. The first
deploy after this PR landed produced no cron runs because of this —
the operator has to create each service in the dashboard once, then
the TOML's `cronSchedule` and `startCommand` take over.

Setup steps (do this once per Railway project):

1. Open the Railway project dashboard.
2. Click **+ New** → **Empty Service**. Name it exactly
   `resolver-cron-kalshi` (the name must match the `[[services]]`
   block in `railway.toml`).
3. Connect it to the same GitHub repo + branch as the web service
   (so it picks up the same code).
4. Set the same environment variables the web service uses —
   minimum: `DATABASE_URL`. Use the Railway **Shared Variables**
   feature so all three services (web + both crons) read from one
   source.
5. Repeat for `resolver-cron-fl`.
6. Trigger a redeploy on each. After the first deploy, Railway
   reads `railway.toml`, sees the matching `[[services]]` block,
   and applies `cronSchedule` + `startCommand`. Subsequent code
   pushes update both crons without further dashboard work.

Verify:
- Each service's **Settings → Cron Schedule** field shows
  `0 2 * * *` (kalshi) or `15 2 * * *` (fl).
- Each service's **Settings → Start Command** matches the table
  above.
- The Railway deploy logs for each cron service show
  `Resolver pass complete in Xs:` and the per-counter summary on
  the next scheduled run.

If a cron service runs but exits with `ERROR: DATABASE_URL not
set`, the env var didn't propagate from Shared Variables — set
`DATABASE_URL` directly on that service's **Variables** tab.

#### Per-run signals

Both passes write one row to `sp.resolver_runs` with
`run_mode='cron'` so day-7 reports filter cleanly (excluding ad-hoc
`standalone` runs and post-2E `live` activity).

Each cron run emits two halt-criteria signals:
1. **Stdout WARNING block** at the end of the summary if any
   threshold was exceeded — Railway's cron-log scrape catches it.
2. **Structured log event** `resolver.run_pass.halt_criteria_exceeded`
   for observability tooling.

The runner deliberately exits 0 even when warnings fire — transient
threshold spikes shouldn't surface as Railway deploy failures. The
operator reviews via the day-7 query and the cron logs, not exit
codes.

### Halt criteria (per design doc §2)

Wired into the per-pass evaluation in `_evaluate_halt_criteria()`:

| Trigger                                       | Threshold       | Remediation |
|-----------------------------------------------|-----------------|-------------|
| Crashes in a single pass                      | `> 5`           | Halt parallel-run; investigate before re-enabling. Most likely cause: Neon connection issue or a matcher exception loop. |
| Coverage (auto_applies / records_scanned)     | `< 60%` sustained | Review extraction; possibly relax competition-match or bootstrap more aliases. (Smoke runs `< 100` records skip this check.) |
| Latency p95                                   | `> 5 min`       | Switch from polling to LISTEN/NOTIFY for Phase 2E.fix. |

Two halt criteria from the design doc are evaluated at day-7 review
time (not per-pass — they need cross-pass aggregation):

| Trigger                                | Computed how | Remediation |
|----------------------------------------|--------------|-------------|
| Kalshi false-positive rate `> 1%/24h`  | Diff resolver auto-applies vs `legacy_kalshi_join.pair_via_registry`. Comparator wiring still pending — column `sp.resolver_runs.legacy_diff_count` exists but is NULL until the diff lands. | Tighten kickoff drift to 15 min. |
| FL spot-check error rate `≥ 1 of 5`    | Operator picks 5 random FL strict auto-applies/day from `sp.resolution_log` and verifies against FL's web UI. ~5 min/day manual step. | Halt FL parallel-run, investigate. |

### Day-7 parallel-run report

Single SQL file with all the queries the day-7 review needs:

```bash
psql "$DATABASE_URL" -f scripts/parallel_run_day7_report.sql
```

Runs nine sections (auto-apply rate per day, day-over-day trend,
latency p95, crash count, fail_reason distribution last 24h + full
7-day window, FL transitional sub-paths, Phase 2C backfill candidate
count, cross-provider summary). All queries scope to
`run_mode IN ('standalone', 'cron')` so they exclude post-2E live
activity.

Individual queries can be copy-pasted from the file for ad-hoc
audits. The cross-provider summary (section 9) is the right place
to start the day-7 review.

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

(This is also section 5 of `parallel_run_day7_report.sql`.)

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

### Manual / ad-hoc parallel-run pass

Operators can trigger a pass between the daily 02:00 UTC slots
without disturbing the cron series — `--run-mode standalone` is the
default and the day-7 query includes both modes:

```bash
# Smoke first (skips the coverage halt-check at < 100 records).
DATABASE_URL=<prod-Neon> python scripts/run_resolver_pass.py \
    --provider kalshi --limit 100

# Full ad-hoc passes.
DATABASE_URL=<prod-Neon> python scripts/run_resolver_pass.py --provider kalshi
DATABASE_URL=<prod-Neon> python scripts/run_resolver_pass.py --provider fl
```

Or via Makefile against the local docker-compose Postgres:

```bash
make resolver-pass-kalshi
make resolver-pass-kalshi ARGS="--limit 100"
make resolver-pass-fl
```

### FL spot-check (operator runbook, daily)

The "FL spot-check error rate" halt criterion above is a manual
step. Pick 5 random FL strict-tier auto-applies per day:

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

Cross-reference each row's fixture against FL's web UI (or another
source). ≥1 of 5 wrong on any day → halt FL parallel-run, investigate.
0 of 5 wrong for 5 consecutive days → FL precision is acceptable;
resume normal cadence. ~5 minutes/day. Replaced by automated
cross-provider corroboration in Phase 2D.

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
