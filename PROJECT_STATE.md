# PROJECT_STATE.md

Living state of the SP Architecture rebuild. Each session updates this
file with what landed, what was investigated, and what's open for the
next session. Treat it as the project's running journal.

---

## Session — 2026-05-08 (afternoon onwards)

### Phase 2A.5 — Bootstrap (✅ complete in production)

Bootstrap ran 2026-05-08T15:33:43Z, completed in 41.9s.
**24,400 teams + 30,442 aliases inserted, 0 chunks failed,
no leaked transactions.**

Per-sport baseline (the strict-tier coverage benchmark for Phase 2B):

| Sport             | Teams  | Aliases |
|-------------------|--------|---------|
| Soccer            | 17,305 | 22,040  |
| Tennis (singles)  |  3,452 |  3,843  |
| Basketball        |  1,973 |  2,419  |
| Rugby Union       |    320 |    359  |
| American Football |    309 |    501  |
| Baseball          |    272 |    397  |
| Hockey            |    252 |    310  |
| Darts             |    174 |    190  |
| Cricket           |    107 |    139  |
| MMA               |     95 |     98  |
| Boxing            |     75 |     79  |
| Aussie Rules      |     66 |     67  |
| Golf              |      0 |      0  |
| Handball          |      0 |      0  |
| Rugby League      |      0 |      0  |
| Snooker           |      0 |      0  |
| Volleyball        |      0 |      0  |

**5 sports have zero teams** (Golf, Handball, Rugby League, Snooker,
Volleyball). Resolver's alias tier (Phase 2C) or human review
queue (Phase 2F) will populate them organically when Kalshi/FL
coverage starts.

Skipped (intentional): 1,745 tennis doubles partnerships, 452
entities in unmapped sports (Esports / Motorsport / Table Tennis).

### Phase 2B — Strict matcher (this session)

Per locked design in `PHASE_2B_DESIGN.md` (PR #77).

**Migration `bdf12a30e49b`:**
- Created `sp.resolver_runs` table with `provider` + `run_mode` columns.
- Altered `sp.fixtures.competition_id` from NOT NULL to NULL —
  enables sport-only fallback when `sp.competitions` is empty.

**New modules:**
- `resolver/aliases.py` — `AliasResolver` bulk-load + in-memory
  `(alias_normalized, sport_id) → set of team_ids` index. Strict
  tier punts on ambiguous aliases (>1 team_id per key).
- `resolver/fixtures.py` — `find_fixture` (drift-windowed search) +
  `ensure_fixture` (DO-NOTHING + re-fetch per design §1).
  `ensure_fixture` returns `(fixture_id, created_new)` so insert-vs-
  conflict path is recorded in `resolution_log.reason_detail`.
- `resolver/matcher.py` — `StrictMatcher` with 4-condition gate
  (kickoff_confidence ≥ 0.85, both teams alias-resolved unambiguously,
  sport classified, kickoff drift ≤ 30min). Tries swapped
  orientation when find_fixture misses (handles direction-blind
  Kalshi abbr_block). 0.98 confidence on hit.
- `scripts/run_resolver_pass.py` — standalone runner. Bulk-loads
  aliases, walks unresolved provider records in 200-row chunks,
  each chunk one transaction. Atomic per design §1: UPDATE
  provider table's fixture_id + INSERT resolution_log in the same
  `session.begin()` block. Writes one `sp.resolver_runs` row at
  end with parallel-run metrics.
- 22 unit tests + 1 integration stub.

**Operator action sequence (parallel-run kickoff):**

```bash
git checkout main && git pull

# Apply migration (creates sp.resolver_runs, alters sp.fixtures)
DATABASE_URL=<prod-Neon> alembic upgrade head

# Smoke-run on a small slice
DATABASE_URL=<prod-Neon> python scripts/run_resolver_pass.py \
  --provider kalshi --limit 100

# Full passes
DATABASE_URL=<prod-Neon> python scripts/run_resolver_pass.py --provider kalshi
DATABASE_URL=<prod-Neon> python scripts/run_resolver_pass.py --provider fl

# Day-7 metrics query (filters out post-2E live activity):
psql "$DATABASE_URL" <<'SQL'
SELECT provider,
       SUM(records_scanned)  AS scanned,
       SUM(auto_applies)     AS auto_applies,
       ROUND(100.0 * SUM(auto_applies) / NULLIF(SUM(records_scanned), 0), 2) AS coverage_pct
FROM sp.resolver_runs
WHERE started_at > NOW() - INTERVAL '7 days'
  AND run_mode IN ('standalone', 'cron')
GROUP BY 1;
SQL
```

### Phase 2A.6 — Competition seeding + matcher gate (this session, follow-up to 2B)

Caught a defect during 2B post-merge review: the strict-tier matcher
read `signal.competition_hint` only into `reason_detail` for logging;
it never resolved or filtered by competition. Combined with
`sp.fixtures.competition_id` becoming nullable in migration
`bdf12a30e49b`, the strict tier had silently degraded into
"competition-blind" rather than implementing the design's intended
sport-only fallback. **Smoke-run was paused before the first pass.**

Path B chosen (defer FL competitions to 2C, ship Kalshi competition
gate now):

**New modules:**
- `scripts/bootstrap_sp_competitions.py` — Kalshi-only seed.
  Bulk-loads existing `sp.competitions.kalshi_series_bases` into a
  Python set, fetches DISTINCT `(_sport, series_ticker)` from
  `sp.kalshi_markets`, applies `kalshi_identity.strip_known_suffix`
  to derive `series_base`, queues new rows with
  `kalshi_series_bases=[base]` and inserts in 1000-row chunks. Same
  idempotency pattern as `bootstrap_sp_teams`.
- `resolver/competitions.py` — `CompetitionResolver` with bulk-load +
  in-memory `(provider, hint) → (competition_id, kind)` lookup. Kinds:
  `'explicit'`, `'no_hint'`, `'unresolvable'`. For Kalshi tries hint
  as-is then `strip_known_suffix(hint)` so callers can pass either
  the full series_ticker or a stripped base.

**Matcher changes (`resolver/matcher.py`):**
- `RESOLVER_VERSION` bumped from `strict@2b.0` → `strict@2a.6`.
- Constructor accepts optional `competitions: CompetitionResolver`.
- New `_competition_gate` enforces per-provider policy:
  * Kalshi `'explicit'` → use competition_id, filter `find_fixture`,
    write on `ensure_fixture`.
  * Kalshi `'no_hint'` → sport-only fallback, log
    `kalshi_no_hint_sport_only: true`.
  * Kalshi `'unresolvable'` → strict tier FAILS (`fail_reason=
    'kalshi_competition_unresolvable'`).
  * FL → transitional sport-only, every successful match logs
    `fl_transitional_sport_only: true`.
- `find_fixture` accepts optional `competition_id` filter; matches
  on `(competition_id = filter OR competition_id IS NULL)` to avoid
  forking one logical fixture into two when FL (no comp) created it
  before Kalshi (with comp) arrives.

**Other:**
- `resolver/kalshi.py`: `competition_hint` now uses `series_ticker`
  (the canonical Kalshi-side identifier). `_soccer_comp` ("Champions
  League") is preserved on `raw_signals['soccer_comp']` for
  diagnostics. Mirrors the bootstrap's seed key.
- `resolver/__init__.py` exports `CompetitionResolver`.
- `scripts/run_resolver_pass.py` loads a CompetitionResolver after
  AliasResolver and passes it to `StrictMatcher`.
- 13 new unit tests (CompetitionResolver coverage + matcher gate
  per-provider behavior + degrade-without-index).
- DEPLOYMENT.md adds a Phase 2A.6 runbook before the 2B section.

**Operator action sequence updated:**

```bash
git checkout main && git pull
# Migration was already applied for 2B (bdf12a30e49b at head).

# 2A.6 step — seed competitions before the first parallel-run pass.
DATABASE_URL=<prod-Neon> python scripts/bootstrap_sp_competitions.py --dry-run
DATABASE_URL=<prod-Neon> python scripts/bootstrap_sp_competitions.py

# Then proceed with 2B parallel-run as documented above.
DATABASE_URL=<prod-Neon> python scripts/run_resolver_pass.py \
  --provider kalshi --limit 100  # smoke

# Audit gate decisions during the run:
psql "$DATABASE_URL" -c "
  SELECT reason_detail->>'competition_resolution' AS resolution, COUNT(*)
  FROM sp.resolution_log
  WHERE provider='kalshi' AND decided_at > NOW() - INTERVAL '24 hours'
  GROUP BY 1 ORDER BY 2 DESC"
```

**FL transitional path is queryable** via
`reason_detail ? 'fl_transitional_sport_only'`. Phase 2C work will
seed `fl_tournament_stage_ids` and re-run the matcher against rows
carrying that flag to backfill explicit competition_ids.

### Production incident — transaction leak in db.py

**Reported:** four connections leaked over ~35 minutes (pids 645, 647,
649, 32493). `idle in transaction` state. Manually killed by operator;
the same pattern reappeared within 5 minutes.

**Root cause:** two anti-patterns in `db.py`:
1. `db.upsert_entities` and `db.sync_events_to_db` wrap a slow loop
   over thousands of records in a single `async with session.begin()`
   block. With Neon's ~75ms round-trip, transactions stay open for
   5-30+ minutes. Cancellation or asyncpg state issues mid-loop leave
   the connection idle-in-transaction.
2. `db.sync_events_to_db` creates its own engine per call but only
   disposes it on the success path (the except branch's dispose call
   itself can fail silently). Cancelled calls leak 2 pool connections
   per occurrence until process exit.

**Fix shipped (this session):**
- `idle_in_transaction_session_timeout=60000` and `statement_timeout=60000`
  added to engine `connect_args.server_settings` — Postgres self-kills
  stuck transactions / queries even if app code is buggy.
- `lock_timeout=30000` added to defend against deadlocks.
- `application_name='stochverse-web'` added so leaks surface clearly
  in `pg_stat_activity` triage queries.
- `db.upsert_entities` refactored: chunk teams into batches of 100,
  one transaction per chunk. Per-chunk failures isolated; subsequent
  chunks proceed.
- `db.sync_events_to_db` refactored: chunk records into batches of 50,
  one transaction per chunk. `try/finally` around `_engine.dispose()`
  so cancellation paths can't leak the engine.
- 10 unit tests verify chunking math + failure isolation + static-
  inspection guards against regressing to the mega-transaction pattern.

**Bootstrap (Phase 2A.5) was paused while this incident was active.**
Resume after the fix is deployed and the leak pattern stops
reproducing.

### What landed

- **Transaction leak fix in `db.py`** (this commit). 10 new tests; all
  60 existing tests still pass.

- **Phase 2A.5 — Bootstrap.** New `scripts/bootstrap_sp_teams.py` +
  Alembic migration `d8e717ed79dd` (seeds `sp.sports` with 17-sport
  finite list, each with per-sport drift threshold from §5.4). One-
  time legacy → SP migration: pulls `public.entities` (team-typed)
  into `sp.teams`, `public.entity_aliases` into `sp.team_aliases`
  with `source='legacy_bootstrap'`, `confidence=0.95`. Idempotent
  via `(alias_normalized, source)` unique constraint. Has `--dry-run`
  flag for safe operator preview. Makefile target `bootstrap-sp-teams`,
  DEPLOYMENT.md runbook.
  - **Operator action required after merge:** apply migration,
    `--dry-run` first, then run for real, **document per-sport
    coverage counts in this file** (replaces "open question:
    Phase 2B baseline").

- **Phase 2B design doc** locked at `PHASE_2B_DESIGN.md` (PR #77).
  Three pushbacks addressed: ensure_fixture pinned to DO-NOTHING +
  re-fetch; parallel-run criteria split per-provider (Kalshi auto
  diff, FL operator spot-check); bootstrap as separate 2A.5 PR.
  `sp.resolver_runs` schema includes `provider` + `run_mode` for
  filtering parallel-run vs live-runner data.

- **Phase 2A — Resolver scaffolding.** New `resolver/` package with the
  contract types and per-provider extraction logic. No DB writes,
  no matching, no resolution_log — pure foundation for 2B+.
  - `resolver/types.py` — `FixtureSignal`, `TeamCandidate`,
    `MatchResult`, `ReasonCode` (Pydantic v2 + Enum).
  - `resolver/protocol.py` — `ResolverModule` runtime-checkable
    Protocol.
  - `resolver/_normalize.py` — strict NFD-only name normalization
    per architecture §9.2.
  - `resolver/fl.py` — `FLResolverModule.extract_signal` reads FL's
    raw_payload + tournament context, produces FixtureSignal with
    fl_team_id / name / shortname candidates.
  - `resolver/kalshi.py` — `KalshiResolverModule.extract_signal`
    reads Kalshi cache record, runs `parse_ticker`, produces
    FixtureSignal with title-parsed name candidates + abbr_block
    direction-blind candidate.
  - `tests/test_resolver_2a.py` — 31 unit tests covering
    normalization, type validation, Protocol conformance, FL
    extraction edge cases, Kalshi extraction edge cases (per_fixture
    vs outright, kickoff fallbacks, sport_override, title parsing
    `vs` / `at` / `@` separators).

### Phase 2 sub-roadmap (decomposition)

| Sub-phase | Scope | Status |
|---|---|---|
| **2A** | Resolver scaffolding: types, Protocol, extract_signal stubs | ✅ this session |
| 2B | Strict tier — match against sp.fixtures via exact alias on both teams + kickoff ±30min + competition. Write to sp.resolution_log + sp.review_queue. UPSERT sp.fixtures. | next |
| 2C | Confidence scoring + alias tier (per-sport drift threshold) | after 2B |
| 2D | Time-anchored fuzzy + cross-provider corroboration | after 2C |
| 2E | Three-loop resolver runner (hot via LISTEN/NOTIFY + 30s batch + 5–10min re-resolution) | after 2D |
| 2F | Admin review-queue UI minimum: auth + list + approve/reject + audit | separate PR |
| 2G | Diff tooling — compare new resolver decisions to legacy `kalshi_join` pairings via `/api/_debug/resolver_diff` | after 2E |

### Deferred design questions — resolve in Phase 2C+ scoping

- **Individual sports don't fit the team model.** Tennis singles, golf,
  MMA, boxing entities currently land in `sp.teams` as "team-of-one"
  rows. Works mechanically (resolver matches against alias rows
  regardless), but is awkward — a player isn't a team. Surfaced
  during the 2A.5 cross-sport audit. May warrant a separate
  `sp.players` table with similar alias machinery in Phase 2C+
  scoping. Don't attempt during Phase 2B — strict-tier matching
  works as-is on the team-of-one shape.

### Open questions still — decide before 2B

- **Hot-loop trigger.** Implement `LISTEN/NOTIFY` immediately in 2E,
  or start with a 1s polling loop and add NOTIFY in a follow-up?
  Recommendation: polling first (simpler, easier to debug);
  `LISTEN/NOTIFY` in a 2E.fix PR once polling-based correctness is proven.
- **Confidence threshold for auto-apply vs review-queue routing.**
  Architecture default 0.85 — verify against real production data
  during 2B's parallel-run period.
- **Admin UI tech.** Architecture §7.5 says "auth, list view, detail
  view, approve/reject, audit log." Phase 2F decision: render via
  vanilla JS in `static/admin/` (consistent with current frontend
  stack) or use FastAPI + Jinja2 server templates. Lean toward Jinja2
  — server-rendered admin pages are easier to keep secure and
  versionable than a SPA.

---

## Session — 2026-05-07/08

### What landed (PRs merged, in order)

- **Phase 0** — `WEB_CONCURRENCY≥2` documented + structlog JSON output +
  FL request-path 30s in-memory cache + `provider_api_call` event
  emitter (PR #66, sha `df59f56`). Operator set `WEB_CONCURRENCY=2` on
  Railway; two worker processes confirmed on next boot.

- **Phase 1A** — SP schema (`sp.*` Postgres schema, 12 tables), Alembic
  init with async template + Neon URL handling, `docker-compose.yml`,
  Makefile dev targets, `.env.example`, `.gitignore` updates
  (PRs #67, #68). Initial migration `8f404e0dc89a` written by hand.

- **Phase 1A migration commit fix** — debugged silent failure where
  `alembic upgrade head` reported success but no DDL persisted. Root
  cause: async SQLAlchemy + Alembic gotcha — `connect()` begin-once
  mode doesn't reliably propagate the COMMIT across the sync/async
  boundary inside `run_sync`. Fix: `transaction_per_migration=True`
  in `context.configure()` + explicit `await connection.commit()`
  after `run_sync`. (PR #69, sha `47aa9ee`)

- **Phase 1A migration applied to production** — Neon DB has the
  `sp` schema with 12 tables and `sp.alembic_version` at revision
  `8f404e0dc89a`. Verified by operator after the env.py fix.

- **Neon password rotation** — operator-driven. Old `neondb_owner`
  password (which had been pasted in chat earlier) was rotated in
  the Neon dashboard. Railway `DATABASE_URL` env var updated to the
  new pooled URL (`-pooler` hostname). Verified by `/healthz` after
  redeploy.

- **Phase 1B — FL ingestion** (PR #70, sha `a8dd0e7`). New
  `ingestion/` package with `base.py` (Protocols, supervisor,
  advisory locks, payload-hash UPSERT), `schema_validation.py`
  (Pydantic boundary validators), `fl.py` (today + week cadence
  loops), `runner.py` (entry point under supervision). 17 unit tests.
  Production confirmed: `ingestion.fl.pass_complete` events flowing,
  ~19k rows in `sp.fl_events` after backfill.

- **Phase 1C — Kalshi REST ingestion** (PR #71, sha `22bbb72`).
  `ingestion/kalshi.py` reads from legacy `_cache["data_all"]`,
  parses each ticker via `kalshi_identity.parse_ticker`, UPSERTs
  to `sp.kalshi_markets`. Same coupling pattern as Phase 1B uses
  for FL. 9 new unit tests.

- **Phase 1 fixes — batch UPSERT, accurate counters, kalshi
  cold-cache priming** (PR #72, sha `2ac0ab8`). Three issues
  surfaced by the first day of production:
  - Cold-cache priming fires `get_data()` in an executor when the
    legacy cache is empty (90s timeout).
  - Counter accuracy: SELECT-first classification before UPSERT;
    counters are correct by construction.
  - FL pass duration 150–275s → 18–30s via multi-row
    `INSERT ... ON CONFLICT DO UPDATE`. 5–15× faster.

- **Phase 1D — investigated, de-scoped.** A Kalshi WS consumer
  module was shipped (PR #73, sha `89868af`) but found to be a
  silent no-op: `kalshi_ws.LIVE_PRICES` is keyed by **market_ticker**
  (sub-market level) while `sp.kalshi_markets.ticker` PK is
  **event_ticker**. The `WHERE km.ticker = v.ticker` clause never
  matched. Investigation also surfaced that prices already live in
  the legacy `public.prices` table at sub-market granularity,
  populated by `kalshi_ws._price_flush_loop` for both REST and WS
  sources. The right architectural answer: `sp.*` is the entity
  layer, `public.prices` is the price-history layer. Phase 1D was
  removed from scope. The dead code is deleted in this session's
  doc-update PR.

- **Phase 1E — FL backfill script** (PR #74). `scripts/backfill_fl.py`
  pumps indent_days from -7 to +7 through the same ingestion pipeline.
  Operator ran against production Neon and confirmed `sp.fl_events`
  grew to ~19k rows. **`scripts/backfill_kalshi.py` removed** in a
  follow-up commit (`443933e`) — Kalshi REST exposes only currently-
  active and recently-closed events, which the live ingestion already
  pulls every 30s. No useful backfill range. A real Kalshi historical
  backfill would require per-ticker queries against `/markets/{ticker}`
  with a starting list of historical tickers — Phase 2+ if/when needed.

- **Architecture doc v1.4** — boundary statement that
  `public.prices` is a load-bearing dependency of Phase 3 serving;
  system-of-record table covering 14 data classes (canonical
  fixtures, teams + aliases, provider records, sub-market identity
  with the documented redundancy between `sp.kalshi_markets.raw_payload->'outcomes'`
  and `public.markets`, current price snapshots, per-tick price
  history, resolver audit trail, review queue, provider telemetry,
  live scores, legacy entities). v1.3 was discussed but never
  produced as a file artifact; v1.4 supersedes both v1.2 and the
  conceptual v1.3.

### What was investigated and rejected (no code shipped beyond the
deletion of Phase 1D)

- A "field-aware merge" or "separate WS-only columns" approach
  to making `sp.kalshi_markets` price-aware. Rejected because
  `sp.*` was never intended to store prices and `public.prices`
  already does so at sub-market granularity. The doc was updated
  to make this boundary explicit rather than rebuild infrastructure
  that already works.

### Production verification at end of session

- `sp.fl_events`: ~19k rows after FL backfill; passes complete in
  18–30s (down from 150–275s).
- `sp.kalshi_markets`: ~7,325 rows; pass durations 12–17s; counter
  fields (`inserted` / `updated` / `unchanged`) are correct.
- Phase 1D's WS module is deleted; `ingestion.runner.started` will
  emit `task_count=2` going forward.
- `sp.alembic_version` at `8f404e0dc89a`; legacy `public.*` tables
  untouched.

### Open questions for next session

- **Phase 2 resolver scoping.** Architecture doc §7 has the design
  (three-loop pattern, central matcher with confidence scoring,
  `resolution_log`, `review_queue`). Concrete decisions still
  open:
  - Resolver version 0 — what minimum strict-tier match coverage
    is acceptable to start? (Today's pairing is in `kalshi_join.py`;
    porting it as the resolver's strict tier is the obvious starting
    point.)
  - Hot-loop trigger: implement `LISTEN/NOTIFY` immediately, or
    start with a 1s polling loop and add NOTIFY in a follow-up?
  - Admin review-queue UI: what's the minimum viable shape? The
    spec in §7.5 lists auth + list view + side-by-side detail +
    approve/reject + audit log. Phase 2 sub-phases probably want
    auth + list + approve before detail/audit.
  - Confidence threshold for auto-apply vs review-queue routing:
    default 0.85 per §7.4 — verify against real production data
    before locking in.

- **Phase 1F — archival.** Architecture §6.5 says raw provider
  payloads >30 days old archive to object storage. Decision deferred
  this session. The legacy `public.prices` is already pruned via
  `PRICE_RETENTION_HOURS` (default 6), which is acceptable
  steady-state. The `sp.*` provider tables (`fl_events`,
  `kalshi_markets`) don't yet have retention; doing nothing means
  they grow without bound. Phase 1F decides.

- **Phase 4 prep — Polymarket / OddsAPI integration.** Not started.
  The architecture's claim that adding a provider is "a new
  ingestion poller + a new resolver module + configuration entries"
  gets tested for real here.

### Notes for the next session's first 5 minutes

- Run `git pull origin main` to get this session's commits.
- Verify `make test` is green (39 ingestion tests pass).
- Verify production: `sp.kalshi_markets` row count steady, FL passes
  still in the 18–30s range, no `kalshi_ws` mentions in recent logs.
- Re-read this PROJECT_STATE.md before starting Phase 2 design work.
