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

```bash
DATABASE_URL="postgresql://...neon.tech/...?sslmode=require" \
  python scripts/backfill_kalshi.py
```

Or via Makefile (uses local docker-compose Postgres):

```bash
make backfill-fl
make backfill-kalshi
make backfill-all   # both, sequential
```

### What gets backfilled

| Provider | Range covered | Notes |
|---|---|---|
| FL | ±7 days from today | FL `/v1/events/list` only serves ±7 days. Beyond that requires per-tournament historical queries (Phase 2 PR). |
| Kalshi | Open + recently-closed events from `/events` endpoint | Kalshi ages closed events out after a retention window (varies by series). The ±30-day target in §11.2 is best-effort against whatever Kalshi still serves. |

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
- Kalshi backfill: ~2-3 minutes (the legacy paginate is the slow step at 20-60s; ingestion pass after that is ~10-20s).

Both scripts are network-bound on the provider API, not the database.

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
