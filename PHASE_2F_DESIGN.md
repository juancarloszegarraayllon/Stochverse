# Phase 2F Design — Operator Review-Queue UI

Status: design doc rev1, awaiting review. **Draft — design discussion before implementation.** Pivot from 2D.5 (paused per the 2026-05-10 production spot-check finding): the review queue is structurally accumulating into an unactionable state because operators have no UI to triage it.

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
- **No revisiting approved/rejected records.** Once decided, the row is closed. If operators discover a wrong decision, the fix is via separate SQL (or an "unreview" feature added in 2F.X if the need emerges).

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

`alembic revision -m "add review_queue.reason_detail and provider_title"`

```python
def upgrade():
    op.add_column("review_queue", sa.Column("reason_detail", JSONB(), nullable=True), schema="sp")
    op.add_column("review_queue", sa.Column("provider_title", sa.Text(), nullable=True), schema="sp")
    # Composite index for the filter combo: status + sport + confidence
    # (sport will live in reason_detail for new rows; older filtering by
    # provider_record_id JOIN to provider tables stays unchanged).
    op.create_index(
        "ix_review_queue_pending_confidence",
        "review_queue", ["status", sa.text("confidence DESC"), "created_at"],
        schema="sp",
        postgresql_where=sa.text("status = 'pending'"),
    )
```

Existing rows: NULL on new columns. Runner (Phase 2C/2D) updates: write the new fields on the next REVIEW_QUEUE insert path. Schema-additive, no data migration needed.

### 2F.0.5 — Runner write-side update

Update `scripts/run_resolver_pass.py` to populate `reason_detail` and `provider_title` on the existing `INSERT INTO sp.review_queue ... ON CONFLICT DO UPDATE` SQL. Tiny diff. Tested by an integration test that asserts new columns are non-NULL on next insert.

### 2F.1 — Minimal review UI

- FastAPI router mounted at `/admin/`.
- Routes: `GET /admin/login`, `POST /admin/login`, `GET /admin/logout`, `GET /admin/review-queue`, `GET /admin/review-queue/<id>`, `POST /admin/review-queue/<id>/approve`, `POST /admin/review-queue/<id>/reject`.
- Jinja2 templates: `base.html`, `login.html`, `list.html`, `detail.html`.
- Auth: `OPERATOR_PASSWORD_HASH` env var, FastAPI `SessionMiddleware`.
- HTMX: approve/reject buttons swap the row in place; full page reload only on filter changes / pagination.
- Tests: integration tests against test DB; static guards on auth requirement, no batch operations exposed.

Estimated size: ~400-600 lines Python + ~200-300 lines HTML.

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

**Recommendation: (b) re-queueable.** The 2D.5 alias-expansion plan implies the candidate set changes over time. A record rejected today because all 5 candidates were wrong might have a 6th candidate (the right one) tomorrow if 2D.5 adds the alias. The runner's existing query (`WHERE fixture_id IS NULL`) already picks these up; no extra logic needed.

If (b) causes operator frustration ("why is this record back?!"), 2F.X adds a `rejected_until` timestamp.

### Q5 — Multi-operator future **[scope]**

**Currently:** one operator. **2F.1 ships single-operator.** No record locking, no "claim/release" semantics.

**If multi-operator becomes real,** 2F.X needs:

- Per-record optimistic locking (operator A approves while operator B is viewing → B's submit fails with "stale view").
- Audit table (Open Q in §"Decision logging").
- User-level auth (vs single shared password).

**No question to answer now; document for future scoping.** Confirmation: single-operator is the right MVP. Approved by default unless counter-proposed.

### Q6 — Surface anchor_failed records too **[scope]**

`sp.review_queue` only contains REVIEW_QUEUE-routed records (confidence 0.70-0.84 OR collision). The ~171/cron `anchor_failed` records (no candidate above any anchor floor) are NOT in review_queue — they're forensic data in `sp.resolution_log`.

**Options:**

- **(a)** 2F.1 surfaces only `sp.review_queue`. Anchor_failed stays a separate operator surface (eventually 2D.5.1's `anchor-failed-report` CLI).
- **(b)** 2F.1 surfaces both. Operator sees a unified "things to review" inbox; anchor_failed records get an "add alias" action that wires into 2D.5.1's `alias-add` workflow.

**Recommendation: (a) for 2F.1.** Different decision shape ("approve/reject candidates" vs "add a missing alias"); blending them in the same UI risks confusing both flows. 2F.X can add anchor_failed as a second surface once 2D.5.1's CLI ships and the workflow is well-understood.

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
- [ ] **Q4** — Reject semantics: recommend (b) re-queueable. Approved or counter-proposed.
- [ ] **Q5** — Multi-operator deferred. Acknowledged.
- [ ] **Q6** — Anchor_failed surfacing: recommend (a) defer to 2F.X. Approved or counter-proposed.

**Negative space:**
- [ ] No batch operations. Approved.
- [ ] No fixture/team editing. Approved.
- [ ] No ingestion control. Approved.
- [ ] No mobile / public exposure / ML suggestions. Approved.

**Sequencing:**
- [ ] **2D.5 (PR #111) parked as future work; ships only after 2F is operational.** Acknowledged.
- [ ] **2F.1 day-7 measurement informs 2D.5 prioritization.** Approved.

After rev1 sign-off, 2F ships in this order:

1. **2F.0** — Schema migration (`reason_detail`, `provider_title`, partial index).
2. **2F.0.5** — Runner write-side update (populate the new columns on REVIEW_QUEUE insert).
3. **2F.1** — Minimal review UI (FastAPI + Jinja2 + optional HTMX, signed-cookie auth, list/detail/approve/reject).
4. **2F.1.5** — Production day-7 measurement.
5. **2F.2** — Quality-of-life (optional; gated on 2F.1.5 data).
6. **2F.3** — Hardening (auth, audit table, etc.; gated on actual need).

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
