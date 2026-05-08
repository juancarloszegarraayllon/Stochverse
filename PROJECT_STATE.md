# PROJECT_STATE.md

Living state of the SP Architecture rebuild. Each session updates this
file with what landed, what was investigated, and what's open for the
next session. Treat it as the project's running journal.

---

## Session — 2026-05-08 (afternoon onwards)

### What landed

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
