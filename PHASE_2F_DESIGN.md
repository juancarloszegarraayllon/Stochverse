# Phase 2F Design — Operator Review-Queue UI

Status: design doc rev1.1, awaiting sign-off. **Draft — design discussion before implementation.** Pivot from 2D.5 (paused per the 2026-05-10 production spot-check finding): the review queue is structurally accumulating into an unactionable state because operators have no UI to triage it.

**Rev1.1 changes** (5 review pushbacks applied in one revision pass):
- **Q4 revised** — re-queueable rejection now paired with a `rejection_count` guardrail (column added to 2F.0 migration; surfaced in 2F.1 UI; runner-side `>= 3` skip logic deferred to 2F.X).
- **Q6 revised** — anchor_failed surface upgraded from "defer to 2F.X" to "include separate UI tab in 2F.1, fallback to 2F.2 with hard sequence commitment if scope tightens." Operators get visibility into ALL unresolvable records, not just review_queue-routed ones.
- **Q5 expanded** — schema-migration path forward note added: `reviewed_by` Text, no audit table, single shared password are all forward-compatible with multi-operator (additive migrations only, no destructive changes).
- **Q2 expanded** — password rotation requires Railway redeploy noted as known limitation; multi-operator (Q5) gets database-stored hashed passwords.
- **2F.0 migration code expanded** to include `rejection_count` column alongside `reason_detail` and `provider_title`.

Reference: SP Architecture v1.4 §7.5 (Review queue + admin surface). Builds on Phase 2C/2D resolver work — `sp.review_queue` is populated by alias-tier (`alias@2c.0`) and fuzzy-tier (`fuzzy@2d.0`) when their REVIEW_QUEUE branches fire. The matcher comment at `resolver/alias_tier/matcher.py:243-244` already references "the reviewer in 2F" — Phase 2F has been the planned dependency from the start; this design crystallizes it.

---

## Why now (the pivot from 2D.5)

Production spot-check on 2026-05-10 found **2,263 pending records in `sp.review_queue`** with no operator-readable surface to triage them. Every record requires manual SQL JOINs across `review_queue` + `resolution_log` + `fixtures` + `teams` to understand who the candidates are. Estimated triage cost without UI: ~30 min per record × 2,263 = ~1,100 operator-hours.

The Option C1 framing locked in PHASE_2D_DESIGN.md rev3 ("review queue is the headline output, not auto_apply") **only makes sense if there IS an operator UI.** Without 2F:

- 2D.3 ships work into a void. ~1,000 records/day inflow, zero outflow.
- 2D.5 (FL alias coverage expansion) would produce more records that can't be reviewed.
- 2C.3's review queue (already ~250-300/day baseline pre-2D) accumulates uncontested.

**2F is the gating dependency for actual operator value from all the resolver work shipped through 2D.3.**

2D.5 stays tracked as future work (PR #111 stays open as draft) but doesn't ship until 2F is operational. After 2F.1 lands and operators can actually drain the queue, 2D.5 becomes the lever for reducing inflow.

---

## Day-0 baseline

Per the spot-check + last 24h cron data:

| Counter                                | Value                       |
|----------------------------------------|-----------------------------|
| Pending review_queue rows              | 2,263                       |
| Daily inflow (combined cron)           | ~1,000 (FL + Kalshi)        |
| Inflow by tier (estimated)             | ~750 alias + ~250 fuzzy     |
| Confidence distribution                | mixed; many `confidence=0`* |
| Operator capacity allocated            | 0 (no UI)                   |

\* `confidence=0` is the alias-tier collision-induced REVIEW_QUEUE case (`resolver/alias_tier/matcher.py:239`). NOT a bug per se — the matcher deliberately emits 0 when there's no single best candidate score. But from an operator's perspective, the confidence column tells them nothing; the actual signal is in `reason_detail`. Tracked as a separate investigation (issue forthcoming).

### Throughput math

At 30 sec/record (read provider context, scan candidates, decide), one operator working 2 hours/day = **240 records/day reviewed**. Inflow is ~1,000/day. **Net accumulation: ~760/day even with operator running at capacity.**

This means:

- 2F alone is necessary but **insufficient** for steady-state. Inflow exceeds plausible single-operator throughput.
- 2D.5 (alias expansion) and 2D.6 (Asian-name handling) become co-dependent — they reduce inflow to a level the UI can keep up with.
- **2F.1 ships first to bound the existing 2,263 backlog and surface the actual operator throughput**, then 2D.5 reduces inflow, then we measure whether the queue stabilizes.

The 30 sec/record number is a guess. Real throughput could be 10 sec (fast cases) or 2 min (ambiguous cases). 2F.1's day-0 measurement will calibrate.

---

## Stack choice

### Options

- **(a) FastAPI + Jinja2 + HTMX.** Server-rendered HTML with progressive enhancement for approve/reject buttons. Reuses existing FastAPI server (`main.py:95`). No build step. ~300-500 lines of Python + ~100-200 lines of templates.
- **(b) FastAPI + Jinja2, no HTMX.** Pure server-rendered. Every action is a form POST + redirect. Simpler than (a), slightly worse UX (full-page reload on each decision).
- **(c) FastAPI + React/Vue SPA.** Modern client-side stack. Build pipeline, npm dependencies, more JS surface area. Overkill for a single-operator triage tool.
- **(d) Pure CLI.** Same primitives as 2D.5.1's planned CLI. Loses visual context (provider title, candidate team names side-by-side); operator throughput drops vs a screen-based tool. Useful as a fallback for scripted bulk operations, not as the primary surface.

### Recommendation: (a) FastAPI + Jinja2 + HTMX

Reasoning:

- Existing FastAPI app already handles the API surface. Mounting the admin UI on the same app keeps deploy / auth / observability surfaces unified.
- Jinja2 server-rendering is well-understood; no SPA build pipeline.
- HTMX adds the approve/reject interactivity (single button click → row updates in place) without writing custom JS. ~10-20 lines of attribute-based markup vs hundreds of JS lines for the equivalent SPA behavior.
- Total surface area: small enough to ship in 2F.1 and keep maintenance cost near zero.

Pure (b) is acceptable if the team wants to defer adopting HTMX. Defer the choice — see Open Q1.

---

## Authentication

### Options

- **(a) Hardcoded password env var + FastAPI SessionMiddleware (signed-cookie sessions).** One operator, one password, login form, session cookie. ~50 lines of code. `OPERATOR_PASSWORD_HASH` env var (bcrypt-hashed; never plaintext in env).
- **(b) HTTP Basic auth.** Browser pops the auth dialog. Simpler than (a), but credentials transmit on every request and can't be "logged out" cleanly. Acceptable for internal tools behind a VPN.
- **(c) Third-party (Auth0, Clerk).** Per-user accounts, password resets, MFA. Overkill for one operator + adds a vendor dependency.
- **(d) IP allowlist.** Operator's IP only. Brittle (mobile networks, VPN changes) and zero defense if the IP is shared.

### Recommendation: (a) signed-cookie sessions

Single `OPERATOR_PASSWORD_HASH` env var, login route, session cookie. Adequate for one operator. If multi-operator becomes a real need, (a) → (c) is a swap of the auth layer, not a rewrite of the app.

The login form and session machinery are scoped to ~50 lines. Static guard: every UI route that mutates state checks `request.session.get("operator")`; tests assert this on every mutating route.

---

## Operator workflow (the review screen)

### List view (`/admin/review-queue`)

Default sort: `confidence DESC` (highest-confidence cases first — easiest decisions). Pagination: 50/page.

| Provider | Ticker | Title (provider) | Sport | Kickoff (UTC) | Confidence | Tier | Candidates | Created |
|---|---|---|---|---|---|---|---|---|
| kalshi | KXATPMATCH-26MAY10-DJOMUR | "Djokovic vs Murray" | tennis | 2026-05-10 14:00 | 0.78 | fuzzy@2d.0 | 3 | 2h ago |
| fl | 8a3f2b7c... | (no title — synthesize from teams) | soccer | ... | 0.0 (collision) | alias@2c.0 | 5 | 4h ago |
| ... | ... | ... | ... | ... | ... | ... | ... | ... |

Filters (URL params, persisted in session):

- `?sport=tennis`
- `?provider=kalshi`
- `?tier=fuzzy@2d.0`
- `?confidence_min=0.7` (default unchecked; show low-confidence too)
- `?status=pending` (default; allow `approved` / `rejected` for audit views)

### Detail view (`/admin/review-queue/<id>`)

Single record. Three panels:

**Provider record panel:**
```
Provider:    kalshi
Ticker:      KXATPMATCH-26MAY10-DJOMUR
Title:       Djokovic vs Murray
Sport:       tennis
Kickoff:     2026-05-10 14:00 UTC
Tournament:  Internazionali BNL (KXATP-...)
Created in queue: 2 hours ago by fuzzy@2d.0
```

**Matcher reasoning panel** (denormalized from `reason_detail`):
```
Confidence:  0.78
Anchor:      0.40 (surname match: "Djokovic" → "Djokovic N.")
Quality:     0.30 (initial expansion satisfied)
Corroboration: 0.08 (no FL fixture at this kickoff)

Why review-queue: confidence below 0.85 auto-apply threshold.
```

**Candidates panel:**
```
[Approve]  Novak Djokovic (SRB) vs Andy Murray (GBR)
           Internazionali BNL · 2026-05-10 14:00
           team_ids: a1f3b... / c4d8e...

[Approve]  Novak Djokovic Jr. (SRB) vs Andy Murray (GBR)
           Internazionali BNL · 2026-05-10 14:00
           team_ids: f7a2c... / c4d8e...

[Reject]   No correct candidate
[Skip]     Leave for later (no decision recorded)
```

### Actions

- **Approve(candidate_id):** `UPDATE provider.fixture_id = <fixture_id>; UPDATE sp.review_queue SET status='approved', reviewed_by=<operator>, reviewed_at=NOW(); INSERT sp.team_aliases (...) ON CONFLICT DO NOTHING`. Same write-back as the runner's auto-apply path; `source='operator_review'` to distinguish from `'alias_tier'` / `'fuzzy_tier'`.
- **Reject:** `UPDATE sp.review_queue SET status='rejected', reviewed_by=..., reviewed_at=NOW()`. Provider record stays `fixture_id IS NULL`. See Open Q4 for whether 'rejected' = "permanently no_match" or "queued for re-resolve next cron."
- **Skip:** No DB write. Next request returns the next record.

### Keyboard shortcuts (HTMX progressive enhancement)

- `j` / `k`: next / previous record in list view.
- `1` / `2` / `3`: approve candidate 1 / 2 / 3 in detail view.
- `r`: reject.
- `s`: skip.
- `?`: shortcut help overlay.

Optional in 2F.1 — defer to 2F.2 if it complicates initial ship.

---

## Data model

### Current `sp.review_queue` schema (from `sp_models.py:409`)

```python
id                = uuid.uuid4
provider          = Text         # 'kalshi' | 'fl'
provider_record_id = Text
candidate_fixtures = JSONB       # [team_id_or_fixture_id, ...]  ← opaque UUIDs
confidence        = Float        # often 0 (alias collision case)
status            = Text         # 'pending' | 'approved' | 'rejected'
reviewed_by       = Text         # nullable, populated on action
reviewed_at       = DateTime     # nullable
created_at        = DateTime
UNIQUE(provider, provider_record_id)
```

### What the UI needs that's NOT in the schema today

- **Provider record context:** title, kickoff, sport, tournament. Currently lives in `sp.kalshi_markets.raw_payload` / `sp.fl_events.raw_payload`. UI must JOIN.
- **Candidate team display info:** canonical names, country codes, sport. Lives in `sp.teams`. UI must JOIN.
- **Candidate fixture context:** kickoff time, competition. Lives in `sp.fixtures`. UI must JOIN (when `candidate_fixtures` are fixture_ids; sometimes they're team_id pairs from collision-detection cases).
- **Matcher reasoning:** breakdown of why this score. Lives in `sp.resolution_log.reason_detail` (the LATEST row for this `(provider, provider_record_id)`). UI must JOIN.

### Two approaches to surfacing this info

**Approach 1 — JOINs at read time.** UI handlers run multi-table SELECTs per page. Pros: schema-zero, data always fresh. Cons: query complexity, latency at high page counts.

**Approach 2 — Denormalize at write time.** Add columns to `sp.review_queue` that snapshot the relevant context when the runner inserts. Pros: single-table reads, fast pagination. Cons: snapshot can go stale (e.g., team name changed after the row was inserted), migration cost.

### Recommendation: hybrid — denormalize the high-cost stuff, JOIN the rest

Add ONE migration adding TWO columns to `sp.review_queue`:

- **`reason_detail` JSONB** — snapshot of `MatchResult.reason_detail` at insertion. Includes canonical_home/canonical_away, ratios, fail reasons. The matcher already produces this; the runner just needs to write it.
- **`provider_title` Text** — snapshot of the human-readable provider title (e.g., Kalshi's `raw_payload->>'title'`, FL's synthesized `home vs away`). Saves the per-record JSONB parsing on every page load.

Everything else (team canonical names from `sp.teams`, kickoff times from `sp.fixtures`) stays as a UI-time JOIN. Those tables don't churn often; the JOIN stays cheap with the existing indexes.

**Migration is small and back-compatible:** new columns nullable; existing 2,263 rows have NULL for the new fields and the UI shows "(not snapshotted — review_queue row predates 2F.0)". The runner backfills going forward.

---

## Decision logging

### Options

- **(a) Use existing `sp.review_queue.status` + `reviewed_by` + `reviewed_at`.** No new table. Operator decisions overwrite the row in place. History only via the audit trail of UPDATE statements (which we don't currently log).
- **(b) Add `sp.operator_decisions` table.** Append-only audit log: `id, review_queue_id, action, decided_by, decided_at, before_json, after_json`. Full history; clean compliance/debugging story.
- **(c) Hybrid: existing fields for current state, separate audit table for full history.**

### Recommendation: (a) for 2F.1, escalate to (b) if needed

For 2F.1 (single operator, MVP), the existing fields capture the current state (who, when, what decision). That's sufficient for normal operation.

If audit becomes a real need (multiple operators, dispute resolution, suspected operator error), **2F.X can ship the `sp.operator_decisions` table later.** Append-only audit tables are easy to add retroactively because they don't change existing data shape.

---

## Performance

### Pagination

- 50 records per page, default. URL `?page=N`.
- `ORDER BY confidence DESC, created_at DESC` (highest-confidence + most recent first). Index on `(status, confidence DESC, created_at DESC)` to avoid sort on the partial result.
- 2,263 records / 50 = 46 pages. Manageable; explore if needed.

### Filtering

- Filter combinations should be O(1) lookups via indexes:
  - `(status, sport, confidence DESC)` — composite index on the filter combo.
  - URL params: `?status=pending&sport=tennis&confidence_min=0.7`.
- Avoid full-table scans even at 10x current volume (~22k pending rows in worst-case hypothetical).

### Latency budget

- List view (50 records + filters + pagination + JOIN to teams): target <500 ms p95.
- Detail view (1 record + full JOIN to fixtures + teams + raw_payload): target <300 ms p95.
- Approve action (UPDATE provider table + UPDATE review_queue + INSERT team_aliases): target <500 ms p95.

If any of these exceed budget, add denormalized columns or materialized views in 2F.X. Defer until measured.

### N+1 risk

- The list view JOINs to teams per candidate fixture. For 50 records × 5 candidates × 2 sides = 500 team lookups per page. Solve via a single `SELECT FROM sp.teams WHERE id IN (...)` after fetching the page; assemble in Python. Standard pattern; one query per page, not per row.

---

## Negative space — what 2F explicitly does NOT do

- **No batch approve / batch reject.** Each decision is per-record. Bulk operations are too risky (one wrong click → 50 wrong fixtures linked).
- **No fixture editing.** Operator picks from the candidates the matcher surfaced. If no candidate is right, the action is "Reject" — the record stays unmatched. Adding a "manually select different fixture" path opens too many failure modes for 2F.1.
- **No team editing.** `sp.teams` mutations belong to a separate workflow (some of which lands in 2D.5.1 if scoped that way).
- **No ingestion control.** Operators don't trigger crons or pause ingestion via the UI.
- **No multi-operator collaboration.** Single operator MVP. No "claim" / "release" record locking. If a future world has 2+ operators, 2F.X adds it.
- **No mobile UI.** Desktop-first. Mobile is a 2F.X concern only if operator workflow demands it.
- **No machine-learning suggestions.** The UI surfaces the matcher's existing scores; it doesn't add a separate "what would 2D.7's A.rev2 say?" predictive layer.
- **No real-time updates.** Operator refresh shows new rows; no WebSocket / SSE push. Cron writes; UI reads. Polling refresh is fine.
- **No public exposure.** Behind auth, intended for internal use only. No SEO, no public marketing pages.
- **No revisiting approved/rejected records IN 2F.1.** Once decided, the row stays decided in the UI. The runner's `WHERE status='pending'` guard from PR #108 keeps rejected rows out of the list view automatically. **2F.X adds an "unreject" button** gated by Q4's `rejection_count` guardrail (the column ships in 2F.0; the button + runner-side `rejection_count >= 3` skip logic ship in 2F.2 or 2F.3). For 2F.1, wrong decisions are corrected via separate SQL — same as today.

---

## Day-0 prediction

### Throughput estimates (with stated uncertainty)

| Scenario       | Per-record time | 2-hour session | 4-hour session |
|----------------|-----------------|----------------|----------------|
| Optimistic     | 10 sec          | 720            | 1,440          |
| **Median**     | **30 sec**      | **240**        | **480**        |
| Pessimistic    | 90 sec          | 80             | 160            |

Backlog drain time (median scenario, 2 hours/day):

- Current 2,263 backlog: **~9-10 working days.**
- Daily inflow (~1,000): operator runs at 4x deficit. **Steady-state requires either operator throughput improvement OR inflow reduction (2D.5 / 2D.6).**

### What the 2F.1 day-7 measurement tells us

Day-7 of 2F.1 in production answers:

1. **Actual per-record time** — replaces the 30 sec guess with measurement.
2. **Decision distribution** — what % approve, reject, skip? Skews tell us about matcher quality.
3. **Operator capacity** — sustainable hours/day at this work? Calibrates the 2D.5 inflow-reduction priority.
4. **Confidence-band quality** — do high-confidence records actually get approved? Validates (or invalidates) the auto-apply threshold of 0.85.

If 2F.1 day-7 shows operators clearing >800/day sustainably, the C1 framing works AS-IS and 2D.5 becomes nice-to-have. If they clear <300/day, 2D.5 becomes urgent.

---

## Implementation order

### 2F.0 — Schema migration (small, fast, back-compatible)

`alembic revision -m "add review_queue.reason_detail, provider_title, rejection_count"`

```python
def upgrade():
    op.add_column(
        "review_queue", sa.Column("reason_detail", JSONB(), nullable=True),
        schema="sp",
    )
    op.add_column(
        "review_queue", sa.Column("provider_title", sa.Text(), nullable=True),
        schema="sp",
    )
    # Phase 2F Q4: rejection_count tracks cumulative reject clicks per
    # record. 2F.1 surfaces it in the UI; 2F.X uses it as a runner-side
    # threshold to skip burnout-cycle re-evaluation.
    op.add_column(
        "review_queue",
        sa.Column("rejection_count", sa.Integer(), nullable=False, server_default="0"),
        schema="sp",
    )
    # Composite index for the filter combo: status + confidence + created_at.
    op.create_index(
        "ix_review_queue_pending_confidence",
        "review_queue", ["status", sa.text("confidence DESC"), "created_at"],
        schema="sp",
        postgresql_where=sa.text("status = 'pending'"),
    )
```

Existing rows: NULL on the JSONB / text columns; `rejection_count = 0` from the server default. Runner (Phase 2C/2D) updates: write the new fields on the next REVIEW_QUEUE insert path; the existing PR #108 ON CONFLICT DO UPDATE clause stays as-is for `candidate_fixtures` and `confidence`. Schema-additive, no data migration needed.

### 2F.0.5 — Runner write-side update

Update `scripts/run_resolver_pass.py` to populate `reason_detail` and `provider_title` on the existing `INSERT INTO sp.review_queue ... ON CONFLICT DO UPDATE` SQL. Tiny diff. Tested by an integration test that asserts new columns are non-NULL on next insert.

### 2F.1 — Minimal review UI

- FastAPI router mounted at `/admin/`.
- Routes (review_queue surface): `GET /admin/login`, `POST /admin/login`, `GET /admin/logout`, `GET /admin/review-queue`, `GET /admin/review-queue/<id>`, `POST /admin/review-queue/<id>/approve`, `POST /admin/review-queue/<id>/reject`.
- Routes (anchor_failed surface, per Q6 revised): `GET /admin/anchor-failed`, `GET /admin/anchor-failed/<provider>/<provider_record_id>` (compound key — no UUID; the data lives in `sp.resolution_log`, not a queue table). Read-only — no approve/reject. Detail view links to a pre-filled `make alias-add` command for the technical operator.
- Jinja2 templates: `base.html`, `login.html`, `list.html`, `detail.html`, `anchor_failed_list.html`, `anchor_failed_detail.html`.
- Auth: `OPERATOR_PASSWORD_HASH` env var, FastAPI `SessionMiddleware`.
- HTMX: approve/reject buttons swap the row in place; full page reload only on filter changes / pagination.
- Tests: integration tests against test DB (review_queue surface AND anchor_failed surface); static guards on auth requirement, no batch operations exposed, anchor_failed routes have no mutating action handlers.

Estimated size: ~500-700 lines Python + ~250-350 lines HTML (revised up from ~400-600 / ~200-300 to include anchor_failed surface per Q6).

If 2F.1 timeline tightens (e.g., auth or HTMX ergonomics consume more time), the anchor_failed surface shifts to **2F.2 with a hard sequence commitment** (within 2-3 weeks of 2F.1) — NOT an indefinite defer. Sign-off checklist Q6 captures this fallback path explicitly.

### 2F.1.5 — Production day-7 measurement

Operator drives the queue for 5-7 days. Measure: per-record time, decision distribution, capacity, confidence-band quality. Feeds back into 2D.5 prioritization and 2F.2 scope.

### 2F.2 — Quality-of-life improvements (optional, gated on day-7)

- Keyboard shortcuts (j/k/1/2/3/r/s).
- Audit view (filter `status=approved` / `status=rejected` for past decisions).
- Saved filter presets.
- Per-tier breakdown dashboard ("approved 50 fuzzy / 30 alias today").

### 2F.3 — Hardening (post-2F.1.5 if needed)

- Auth upgrade if multi-operator becomes real.
- `sp.operator_decisions` audit table if compliance/debugging requires it.
- Rate limiting on auth endpoints.
- Real-time push (SSE) if polling proves insufficient.

---

## Test plan

### Unit tests

- Auth dependency: rejects unauthed requests, accepts session-bearing requests.
- Filter parser: URL `?sport=tennis&confidence_min=0.7` → SQL WHERE clause.
- Pagination math: `?page=3` → OFFSET 100 LIMIT 50.

### Integration tests (against test DB)

- Login flow: invalid creds → 401, valid creds → cookie + redirect.
- List view: seeded review_queue rows render correctly; filters narrow results; pagination works.
- Detail view: single row joins to fixtures + teams + raw_payload; renders provider_title from snapshot OR from raw_payload fallback.
- Approve action: UPDATE provider table, UPDATE review_queue.status='approved', INSERT sp.team_aliases (source='operator_review'). Idempotent on second run.
- Reject action: UPDATE review_queue.status='rejected'. Provider table untouched.
- Re-running runner after approve: previously-pending row stays approved (the WHERE status='pending' guard from PR #108 protects it).

### Static guards

- All mutating routes (POST) require auth dependency.
- No `DELETE FROM sp.fixtures` path in any handler.
- No raw operator-input → SQL string concatenation (parameterized queries only).
- 2F write-back to `sp.team_aliases` uses `source='operator_review'`, distinct from `'alias_tier'` and `'fuzzy_tier'`.

### Day-0 smoke test

After deploy: log in, list view loads, click into a record, approve it, verify provider record's `fixture_id` populated. Take ~30 sec end-to-end.

---

## Open questions awaiting sign-off

Each tagged with the PR that's blocked on its resolution.

### Q1 — HTMX or pure server-rendered Jinja2 **[2F.1]**

HTMX adds nice in-place updates but introduces a (tiny) client dependency.

**Options:**

- **(a)** HTMX (~5 KB, vendored in templates). Approve button → row updates in place. Cleaner UX.
- **(b)** Pure Jinja2 + form POST + 303 redirect. Every action = full page reload. Simpler, no JS at all.

**Recommendation: (a) HTMX.** UX win is real; cost is minimal. Vendor htmx.min.js as a static file; no npm / build step needed. If team prefers (b) for simplicity, the structural code is unchanged — only the templates differ.

### Q2 — Authentication shape **[2F.1]**

**Options:**

- **(a)** `OPERATOR_PASSWORD_HASH` env var + FastAPI SessionMiddleware. Single operator, password hashed with bcrypt.
- **(b)** HTTP Basic auth. Browser handles credential storage. No login form.
- **(c)** Third-party (Auth0 etc.). Multi-operator ready, vendor lock-in.

**Recommendation: (a).** Adequate for one operator. Swap to (c) when multi-operator becomes real.

**Known limitation (review pushback):** password rotation requires a Railway redeploy because the hash lives in an env var rather than the database. Acceptable for the single-operator MVP — rotation is rare and a redeploy is ~1 minute. **Multi-operator (Q5) requires database-stored hashed passwords** (per the Q5 schema-migration path note); rotation then becomes a self-service UI action with no redeploy needed. The 2F.1 → multi-operator transition swaps the auth lookup but keeps SessionMiddleware unchanged.

### Q3 — `reason_detail` denormalization vs JOIN **[2F.0]**

**Options:**

- **(a)** Add `reason_detail` JSONB to `sp.review_queue`; runner snapshots at insert. Single-table reads.
- **(b)** JOIN to `sp.resolution_log` at read time to fetch the latest reason_detail per record. Schema-zero.
- **(c)** Skip surfacing reason_detail in the UI (just show confidence + candidates).

**Recommendation: (a).** UI needs the breakdown to be useful; JOIN-at-read complicates queries; skip-altogether (c) loses critical operator context. The denormalization cost is trivial; staleness risk is acceptable because the matcher decision was correct AT INSERT and that's what the operator is reviewing.

### Q4 — Reject semantics: permanent or re-queue **[2F.1]**

When operator clicks "Reject":

**Options:**

- **(a)** Permanent. `status='rejected'`; the runner's `WHERE fixture_id IS NULL` query also adds `AND NOT EXISTS (rejected review_queue row)` so rejected records don't reappear.
- **(b)** Re-queueable. `status='rejected'` but next cron's matcher might pick it up again (e.g., after 2D.5 alias additions). Operator may have rejected based on the candidates available at the time; new aliases could surface a better candidate.
- **(c)** Time-boxed re-queue. `status='rejected'` for 30 days, then automatically returns to `pending` on the off chance the matcher's view of the world has improved.

**Recommendation: (b) re-queueable, with `rejection_count` guardrail to prevent operator burnout cycles.**

How re-queueable actually works in practice (a clarifying note — this caused a review pushback):

1. Operator clicks Reject. `status='rejected'`, `rejection_count` incremented (1 on first reject).
2. Next cron runs. The runner's `WHERE fixture_id IS NULL` query DOES pick the record up; the matcher runs and produces a fresh REVIEW_QUEUE result.
3. The runner's `INSERT INTO sp.review_queue ... ON CONFLICT (provider, provider_record_id) DO UPDATE ... WHERE status='pending'` (PR #108 hotfix) **fails the WHERE clause** because the existing row is `status='rejected'`. No update happens. The row stays in 'rejected' state and the operator does NOT see it again on the next list view.
4. Operator-sticky by default: rejection STAYS rejected. No "review the same kalshi ticker 7 days in a row" pathology.

Where `rejection_count` matters: when 2F.X adds an "Unreject" / "Reopen" button (operator looks at audit history, decides to give the record another shot, or 2D.5.X automation surfaces new candidates and sweeps stale rejections back to pending). Each re-rejection increments the count. **2F.X also adds the runner-side guard: after `rejection_count >= 3` AND `candidate_fixtures` unchanged since last rejection, the row stays rejected even if `status='pending'` — protects against burnout cycles when the candidate set is genuinely unmatchable.**

**2F.1 scope (what this PR ships):**

- Add `rejection_count` column to `sp.review_queue` in 2F.0 migration (default 0). Reject action increments it.
- Surface `rejection_count` in the list view ("Rejected 2 times" badge on history tab; default list filter still hides rejected).
- DO NOT ship the unreject button or the runner-side threshold guard. Those are 2F.X.

**2F.X scope (deferred but committed):**

- Unreject button (gated by an explicit confirm: "this record was rejected; reopening will surface it in the queue again").
- Runner-side threshold guard (skip re-evaluation when `rejection_count >= 3 AND candidate_fixtures unchanged`).
- 2D.5.X automation that compares `candidate_fixtures` snapshots: if new aliases produced a new candidate, auto-flip to `pending` regardless of count.

The 30-day time-boxed re-queue from option (c) is rejected — calendar-based decisions don't track the actual signal (whether the candidate set changed). Count + diff-based escalation is the right shape.

### Q5 — Multi-operator future **[scope]**

**Currently:** one operator. **2F.1 ships single-operator.** No record locking, no "claim/release" semantics.

**If multi-operator becomes real,** 2F.X needs:

- Per-record optimistic locking (operator A approves while operator B is viewing → B's submit fails with "stale view").
- Audit table (Open Q in §"Decision logging").
- User-level auth (vs single shared password).

**No question to answer now; document for future scoping.** Confirmation: single-operator is the right MVP. Approved by default unless counter-proposed.

#### Schema-migration path forward (review pushback)

The single-operator schema choices made now don't block multi-operator future. Specifically:

- **`reviewed_by` as Text field** (vs FK to a future `sp.operators` table). Multi-operator migration: add `sp.operators` table; `reviewed_by` text values become eligible to be `operator_id` UUIDs going forward. Existing text-valued rows stay as-is (historical). No destructive migration.
- **No audit table now.** Multi-operator migration: add `sp.operator_decisions` (append-only). Existing `sp.review_queue.reviewed_by` + `reviewed_at` remain accurate for the current-state slice; the new audit table captures all future mutations.
- **Single shared password** (Q2). Multi-operator migration: add `sp.operators(id, email, password_hash, ...)`; the FastAPI auth layer swaps from "compare against `OPERATOR_PASSWORD_HASH` env" to "look up by email, verify hash." Same SessionMiddleware, different lookup.

**All three migrations are additive** — new tables + new optional columns. No destructive changes (no column drops, no type changes, no required-field migrations on existing rows). Single-operator schema choices are forward-compatible by design.

### Q6 — Surface anchor_failed records too **[2F.1 — committed]**

`sp.review_queue` only contains REVIEW_QUEUE-routed records (confidence 0.70-0.84 OR collision). The ~170/cron `anchor_failed` records (no candidate above any anchor floor) are NOT in review_queue — they're forensic data in `sp.resolution_log`.

**Initial recommendation was (a) defer to 2F.X. Pushback during review:** structurally that's correct, but operationally wrong. Operators need visibility into ALL unresolvable records, not just the ones that anchored. Without surfacing anchor_failed, alias-coverage gaps stay hidden behind a SQL query and the operator wonders why review_queue keeps growing while resolution rates stagnate.

**Options:**

- **(a)** 2F.1 surfaces only `sp.review_queue`. Anchor_failed stays invisible to the operator UI; 2D.5.1's `anchor-failed-report` CLI is the only surface.
- **(b)** **2F.1 includes a separate anchor_failed tab** (different data source, same UI shell). Operator can browse anchor_failed records with full provider context; the detail view links out to the 2D.5.1 CLI (or, if the technical operator is the same person, they have the context to add the alias directly).
- **(c)** 2F.1 surfaces only review_queue but **2F.2 commits to adding anchor_failed** with a hard sequence ("ships within 2-3 weeks of 2F.1"). Better than indefinite defer.

**Recommendation: (b) — separate UI tab in 2F.1, with (c) as the fallback if 2F.1 scope tightens.**

Implementation shape:

- New routes: `GET /admin/anchor-failed`, `GET /admin/anchor-failed/<id>`. No approve/reject — anchor_failed records have no candidates to approve. Detail view shows the provider record, the matcher's `fail_reason` (alias_no_team_resemblance, anchor_score_below_floor, deferred_to_2d, etc.), and any near-miss candidates the matcher considered.
- Data source: `sp.resolution_log` filtered by the latest row per `(provider, provider_record_id)` where `reason_code='no_match'` AND `reason_detail->>'fail_reason'` matches the anchor-failed family. Same JOIN-to-provider-tables pattern as the review_queue list view.
- Detail-view action: a "Suggest alias" link that copies the provider record's title + sport + closest-matching `sp.teams` row to the clipboard, formatted as a `make alias-add ARGS="..."` command. **No inline alias creation in 2F.1** — that's 2D.5.1 CLI territory; the UI just hands the operator a pre-filled command. (The 2D.5.X path of moving alias creation into the UI is tracked in PR #111's §"Operator audience".)
- Filters: same shape as review_queue (sport, provider, recency).
- Estimated additional code: ~80-120 lines Python (routes, query, presenter) + ~80-100 lines HTML (list, detail templates). Fits within the 2F.1 size budget (~500-700 + ~250-350 lines total revised).

If 2F.1's scope ends up tighter than expected (e.g., auth or HTMX ergonomics consume more time than estimated), **fallback is (c): explicitly commit anchor_failed to 2F.2 with a hard sequence**, NOT a vague "future work" defer. Anchor_failed visibility is a planned UI surface, not optional.

### Q7 — Where does the UI run **[2F.1]**

**Options:**

- **(a)** Same Railway service as the existing FastAPI app (`main.py`). Mounted under `/admin/`. Single deploy artifact, single auth surface.
- **(b)** Separate Railway service. Independent deploy cadence. More infra cost.

**Recommendation: (a).** No reason to introduce service-boundary overhead. The admin router can be conditionally mounted via an env var if there's ever a need to disable it (e.g., on public-facing replicas).

### Q8 — Confidence column meaning **[2F.0 / separate investigation]**

The `sp.review_queue.confidence` column shows `0.0` for alias-tier collision-induced rows (per `resolver/alias_tier/matcher.py:239`). This is by-design (no single best score when there's a collision), but it's misleading to an operator scanning the list view.

**Tracked as a separate investigation issue** (see §"Cross-references" below). The 2F.1 list view should display "n/a" or "(collision)" instead of "0.0" when this case is detected; the actual signal lives in `reason_detail.colliding_*_team_ids`.

**Recommendation:** ship 2F.1 with the cosmetic display fix (treat 0.0-confidence as "(collision)" if the row's reason_detail has collision flags). Investigate the deeper question — should the column store something more meaningful? — separately. Schema change, if any, lands as 2F.X or as part of the resolver's MatchResult shape.

---

## Sign-off checklist (rev1)

**Framework:**
- [ ] **2F.1 ships before 2D.5.** Approved or counter-proposed.
- [ ] **Single-operator MVP.** Multi-operator scope deferred. Approved.
- [ ] **2F is necessary but insufficient at current inflow.** 2D.5 / 2D.6 reduce inflow downstream. Acknowledged.

**Stack:**
- [ ] **Q1** — HTMX vs pure Jinja2: recommend (a) HTMX. Approved or counter-proposed.
- [ ] **Q2** — Auth shape: recommend (a) signed-cookie sessions with bcrypt env var. Approved or counter-proposed.
- [ ] **Q7** — Same Railway service: recommend (a). Approved.

**Schema:**
- [ ] **Q3** — Denormalize `reason_detail` and `provider_title`: recommend (a). Approved or counter-proposed.
- [ ] **Q8** — Confidence display: cosmetic fix in UI (show "(collision)") + separate investigation issue for deeper question. Acknowledged.

**Workflow:**
- [ ] **Q4** — Reject semantics: recommend (b) re-queueable + `rejection_count` guardrail (column added in 2F.0; surfaced in UI in 2F.1; runner-side threshold guard + unreject button deferred to 2F.X). Approved or counter-proposed.
- [ ] **Q5** — Multi-operator deferred. Acknowledged.
- [ ] **Q6** — Anchor_failed surfacing: recommend (b) include separate UI tab in 2F.1; fallback (c) commit to 2F.2 with hard 2-3 week sequence if scope tightens. NOT indefinite defer. Approved or counter-proposed.

**Negative space:**
- [ ] No batch operations. Approved.
- [ ] No fixture/team editing. Approved.
- [ ] No ingestion control. Approved.
- [ ] No mobile / public exposure / ML suggestions. Approved.
- [ ] No revisiting decided records IN 2F.1 (the unreject button is 2F.X per Q4 revised). Approved.

**Sequencing:**
- [ ] **2D.5 (PR #111) parked as future work; ships only after 2F is operational.** Acknowledged.
- [ ] **2F.1 day-7 measurement informs 2D.5 prioritization.** Approved.

After rev1 sign-off, 2F ships in this order:

1. **2F.0** — Schema migration (`reason_detail`, `provider_title`, `rejection_count`, partial index on `(status='pending', confidence DESC, created_at)`).
2. **2F.0.5** — Runner write-side update (populate the new columns on REVIEW_QUEUE insert; reject action increments `rejection_count`).
3. **2F.1** — Minimal review UI (FastAPI + Jinja2 + HTMX, signed-cookie auth, list/detail/approve/reject for `sp.review_queue` + read-only anchor_failed surface fed from `sp.resolution_log`).
4. **2F.1.5** — Production day-7 measurement.
5. **2F.2** — Quality-of-life + Q4 follow-ups (keyboard shortcuts, audit views, anchor_failed surface IF deferred from 2F.1, unreject button + runner-side `rejection_count >= 3` guard).
6. **2F.3** — Hardening (auth upgrade, `sp.operator_decisions` audit table, etc.; gated on actual need).

---

## Cross-references

- **PHASE_2D_DESIGN.md rev3** — locks Option C1 framing (review queue is primary 2D output). 2F is the operator surface that makes C1 actionable.
- **PHASE_2D_5_DESIGN.md (PR #111)** — paused. 2D.5 reduces inflow; 2F drains the queue. Both needed for steady-state, but 2F ships first because the queue is currently undrainable.
- **`resolver/alias_tier/matcher.py:243-244`** — already references "the reviewer in 2F" in code comments.
- **Issue forthcoming** — the `confidence=0` semantics on alias-tier collision-induced REVIEW_QUEUE rows. Tracked separately so it doesn't block 2F design.
- **PR #108 (2D.3.1 hotfix)** — `ON CONFLICT (provider, provider_record_id) DO UPDATE WHERE status='pending'` — 2F's approve/reject mutations interact with this guard. Verified in §"Test plan" integration tests.

---

## What this PR is NOT

- Not code. No Python, no SQL, no migration, no templates. Implementation gated on rev1 sign-off.
- Not a final UX lock. Push back on any of Q1-Q8 and the doc gets revised before 2F.0 ships.
- Not closing PR #111 (2D.5 design). 2D.5 stays open as parked future work; 2F.1 in production unblocks it.
- Not the 2C.4 / 2D.6 / 2D.7 design conversations. Those continue independently.
- Not a hosting / infra change. 2F runs on the existing Railway FastAPI service.
