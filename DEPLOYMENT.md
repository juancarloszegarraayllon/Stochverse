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
