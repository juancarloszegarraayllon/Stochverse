# Phase 2D.5 Design — FL Alias Coverage Expansion

Status: design doc rev1, awaiting review. **Draft — design discussion before implementation.** Day-7 review (2D.4) gates 2D.5 prioritization, but the design lands in parallel so we move fast when day-7 data arrives.

Reference: SP Architecture v1.4 §7 (Resolution Layer). Builds on Phase 2D's structural finding (`PHASE_2D_DESIGN.md` §E.8 outcome): **2D's corroboration ceiling is gated by upstream alias coverage.** `sp.fixtures` is populated by strict-tier resolution; if FL records didn't get strict-tier-resolved (FL-side alias gap), there's no fixture row to corroborate against. No 2D matcher tuning lifts past this. **2D.5 attacks the ceiling by expanding `sp.team_aliases` coverage on the FL side.**

---

## What 2D.5 attacks

Per the 2D.2.8 dry-run (PR #104) and the rev3 day-0 prediction:

| Bucket                            | Per Kalshi tennis cron |
|-----------------------------------|------------------------|
| auto_apply (corroboration-driven) | ~2-3                   |
| review_queue                      | ~150                   |
| no_match (below threshold)        | ~34                    |
| **anchor_failed (long-tail names)** | **~171**             |

The ~171 anchor_failed/cron records are the 2D.5 target. They reach the matcher but no candidate scores above any tier's anchor floor — typically because the provider name doesn't appear in `sp.team_aliases` at all. Two contributing causes:

1. **FL doesn't ingest the matches Kalshi prices.** Q1 (§E.8) showed top-20 tournament overlap is 100%, but that's a head-of-distribution measurement; the long tail covers Challenger/ITF tournaments outside `DEFAULT_FL_SPORT_IDS=[2]` (ATP/WTA only).
2. **FL ingests the matches but provider-side and FL-side player surnames don't normalize identically.** Compound surnames ("Auger-Aliassime" / "Auger Aliassime" / "Auger"), accents, abbreviations, country-code suffixes — alias-tier 2C handles general cases, but tennis-specific long-tail names slip through.

**Cause 1 is an ingestion-config concern (out of 2D.5 scope per §"Negative space").** **Cause 2 is what 2D.5 attacks** — specifically by adding new `sp.team_aliases` rows so the strict tier picks up names it currently misses.

---

## Day-0 baseline (the inputs to design 2D.5 against)

**The exact fraction of the ~171 anchor_failed/cron that's recoverable is UNKNOWN.** Without classification, the 171 mixes three sub-populations:

- **Cause-2 alias gap (recoverable by 2D.5):** FL has the player but our `sp.team_aliases` doesn't have the provider's spelling.
- **Cause-1 FL coverage gap (NOT recoverable by 2D.5):** FL doesn't ingest the player at all (typically Challenger/ITF). Fix is `DEFAULT_FL_SPORT_IDS` expansion or new tournament codes — separate ingestion concern.
- **Genuinely unmatchable:** typos, retired players, ambiguous abbreviations that no alias addition would resolve.

Without measuring the breakdown, recovery prediction is a coin-flip across ~0–80% of 171.

**This is why 2D.5.0 (classification step) is the mandatory first move.** No alias-add automation ships before we know what fraction of the anchor_failed bucket is even addressable.

### What we DO know from the dry-run

- `anchor_failed` rate (~28.5%) is comparable to non-tennis sports' baseline alias-tier coverage gaps observed in 2C.3 day-7 data. Suggests a meaningful (not trivial) Cause-2 fraction.
- Q1 100% top-20 tournament overlap suggests Cause-1 sits in the long tail, not the head. So Cause-2 likely dominates for ATP/WTA matches; Cause-1 likely dominates for Challenger/ITF.
- The user's named example ("Saleshando" — Otshepo Saleshando, ATP player) is plausibly a Cause-2 case (FL has him, our aliases don't).

---

## Algorithm choices

Three options for adding aliases. Each describes WHO produces the alias and HOW it gets persisted.

### Option A — Manual operator additions only

Operator surfaces an `anchor_failed` record (provider 'Saleshando', tennis, KXATPMATCH-...), looks up the player in `sp.teams`, inserts an `sp.team_aliases` row pointing at the right `team_id`. Repeat per record.

- **Pros:** zero automation risk. Each addition is human-vetted. Schema-zero. Ships fast — basically a CLI tool over existing tables.
- **Cons:** throughput-limited by operator time. Doesn't scale to ~170/cron of anchor_failed records (would saturate the operator capacity already allocated to 2C+2D review queue).
- **Recovery estimate:** depends entirely on operator throughput. At ~30 sec/alias including lookup, an operator clearing 50 aliases/day = ~350/week. Most of the 170/cron compounds across days because the same names recur — so 350/week of unique aliases probably moves the needle within 2-3 weeks.

### Option B — Automated FL roster bulk-seed

Pull FL's player roster (via `/v1/players/data` or by iterating `/v1/teams/squad` per tournament) and bulk-INSERT `sp.team_aliases` rows for every name FL knows about, mapping them to existing or newly-created `sp.teams` rows.

- **Pros:** high throughput. One bulk seed could add thousands of aliases. Covers names operators wouldn't have known to add.
- **Cons:** requires FL roster API research (no obvious "list all players in tournament X" endpoint at first scan). Bulk-seeding without observability into "did we map names to the right `team_id`?" risks polluting `sp.team_aliases` with wrong mappings. Same provider name across multiple sports/players is a real failure mode (e.g., "Smith"). Hard to dry-run — the population of the bulk seed IS the fix.
- **Recovery estimate:** could be ~50–60% of anchor_failed if FL roster covers ATP/WTA cleanly; ~10–20% if Challenger/ITF dominates the bucket (Cause-1, not addressable).

### Option C — Both, in sequence

C1: ops first, then automation. C2: automation first, then ops.

**Recommend C1.** Reasoning:

- The operator workflow (Option A) is a primitive needed regardless — automated extraction will always have edge cases, retired players, name variants the roster doesn't cover.
- Manual additions provide ground truth for evaluating automated extraction quality. Run 2D.5.1 (ops) for 1-2 weeks, then look at what the operators added. If the additions cluster around names FL roster also has, automation has high payoff. If they cluster around names FL doesn't have, automation has lower payoff and 2D.5.2 deprioritizes.
- Bulk seeding without that observability risks polluting `sp.team_aliases` with wrong mappings that an audit-trail-poor schema can't distinguish from operator additions.
- Same calibration discipline as 2D.2.5 → 2D.2.7 → 2D.2.8 → 2D.3 — measure first, automate later.

Selected: **C1 (ops-first, then automation).** Implementation plan reflects this in §"Implementation order" below.

---

## Schema impact

### Existing `sp.team_aliases` (no migration required for 2D.5.1)

```python
class TeamAlias:
    id                = uuid.uuid4
    team_id           = FK -> sp.teams.id  (CASCADE)
    alias             = Text
    alias_normalized  = Text
    source            = Text                # free-form
    confidence        = Float
    created_at        = DateTime
    UNIQUE(alias_normalized, source)
```

Existing `source` values seen in production: `'kalshi'`, `'fl'`, `'polymarket'`, `'oddsapi'`, `'manual_review'`, `'human_curated'`, `'alias_tier'`, `'fuzzy_tier'`. The free-form text column easily accepts a new value without migration.

**Recommendation: 2D.5.1 uses `source='operator_2d5'`.** Distinguishes from the legacy `'manual_review'` (which has no consistent provenance) and from `'fuzzy_tier'` / `'alias_tier'` (matcher-driven write-back). Day-7 queries can split per-source attribution cleanly.

### Audit columns — defer to 2D.5.2 if needed

Candidate additions: `created_by` (operator email), `source_record_id` (the anchor_failed `provider_record_id` that triggered the addition), `notes` (operator free-text).

**Recommendation: defer.** 2D.5.1 is a low-volume operator workflow; git-tracked CSV import history + the existing `created_at` timestamp give enough provenance for the first iteration. If 2D.5.2 (automation) ships, audit columns become more valuable because the volume is higher and bugs are more impactful. Land them in 2D.5.2's migration if needed.

### `sp.teams` — operator team-creation as an open question

The user's "Saleshando" example branches on whether `sp.teams` already has a row for the player:

- **Existing team:** insert one `sp.team_aliases` row. Schema-zero. Trivial.
- **New team:** create an `sp.teams` row, then the alias. Crosses the "no team creation in the resolver" rule (rev3 §"Negative space"), but **operators making explicit decisions is different from a matcher inferring teams.** 2D.5.1 needs to decide whether team-creation is in scope.

See **Open Q1** below for the recommendation.

---

## Operator workflow (the "Saleshando" walkthrough)

**Scenario:** operator is reviewing day-7 anchor_failed records, sees provider record `KXATPMATCH-26MAY08SALMUR` (Kalshi tennis ticker, raw_payload title "Otshepo Saleshando vs Andy Murray"). Wants to add 'Saleshando' as an alias so the next cron resolves the record.

### Proposed flow (2D.5.1 CLI tool)

```bash
# Step 1: surface anchor_failed records (CLI report)
make anchor-failed-report ARGS="--provider kalshi --sport-code tennis --since '24 hours' --limit 50"

# Step 2: search sp.teams for the player
make team-search ARGS="--query 'Saleshando' --sport tennis"

# Output, one of:
#   FOUND: c4a8b... | tennis | Otshepo Saleshando | BWA
#   NO MATCHES — consider creating a team

# Step 3a: alias against existing team
make alias-add ARGS="--team-id c4a8b... --alias 'Saleshando' --source operator_2d5 --confidence 1.0"

# Step 3b: create team THEN alias (open Q1: in or out of 2D.5.1?)
make team-create ARGS="--sport tennis --canonical-name 'Otshepo Saleshando' --country BWA"
# Returns new team_id; operator runs alias-add against it.
```

### Safety guardrails (all three commands)

- **Dry-run mode** (`--dry-run` on every write command). Default OFF; operator opts in by passing `--apply`. Avoids accidental writes.
- **JSON output** for audit log. Every successful write emits a structured row to stdout that operators can pipe to a file for post-hoc review.
- **ON CONFLICT (alias_normalized, source) DO NOTHING** mirroring the 2C.3 alias-tier write-back pattern. Operator can re-run the same `alias-add` command idempotently.
- **Sport-id verification.** `alias-add` rejects if the team's `sport_id` doesn't match the sport context (catches operator pasting a soccer team_id for a tennis alias).
- **Normalization preview.** `alias-add` prints what `alias_normalized` will be BEFORE inserting, so the operator can confirm the alias normalizer doesn't strip something important.

### Why CLI not admin UI for 2D.5.1

Phase 2F (admin review-queue UI) is the long-term operator surface. 2D.5.1 ships ahead of that — a CLI tool reuses existing `sp.team_aliases` write paths and avoids gating 2D.5 on Phase 2F. When 2F ships, the same operations move into the UI; 2D.5.1's CLI stays as a fallback / scripted-bulk path.

---

## Day-0 prediction (with stated uncertainty)

Honest framing: **prediction depends on the 2D.5.0 classification step.** Without it, the bands are too wide to be actionable.

### Scenario range (recovery of the ~171 anchor_failed/cron after 2D.5.1 + ops time)

| Scenario       | Cause-2 fraction | Operator throughput | Recovery after 2 weeks |
|----------------|------------------|---------------------|------------------------|
| **Pessimistic** | 20% Cause-2 (Cause-1 dominates the long tail) | 30 unique aliases/day | ~30-50 of 171/cron recovered |
| **Median**      | 50% Cause-2     | 50 unique aliases/day | ~80-100 of 171/cron recovered |
| **Optimistic**  | 70% Cause-2     | 80 unique aliases/day | ~120-140 of 171/cron recovered |

Notes on the bands:

- **2-week window** because alias additions are cumulative — once 'Saleshando' is in, every subsequent cron benefits. Recovery should compound.
- **Operator throughput** is the gating constraint past Cause-2 % — even if 100% of anchor_failed is Cause-2, throughput sets the per-day pace.
- **"Recovered" means the next cron's bucket distribution shows a measurable shift FROM anchor_failed TO strict_auto_applies (or downstream tiers).** Not just "alias inserted"; the cron pass actually picks the alias up. Day-7 query measures this directly.

### What changes the prediction

- **2D.5.0 classification result:** if Cause-2 < 30%, 2D.5.1's ceiling is low and 2D.5.2 (automation) must address Cause-1 indirectly via ingestion expansion.
- **Operator capacity** for 2D.5 work specifically. Already allocated ~67-83 min/day to combined 2C+2D review queue. 2D.5.1 needs additional ~25-40 min/day for ~50 aliases at 30 sec each. **If operator capacity isn't there, 2D.5.1 doesn't ship.**
- **Re-resolve mechanism.** 2D.5.1 only helps if the same provider record gets re-evaluated by the resolver after the alias is added. Today the resolver picks up records WHERE `fixture_id IS NULL`, so this happens automatically. Stays free.

### Cross-provider lift (the structural finding's payoff)

Per rev3 §"The deeper structural finding": every NEW FL strict-tier resolution = one new `sp.fixtures` row = one more candidate for 2D's corroboration check. So 2D.5 has TWO recovery paths:

1. **Direct:** anchor_failed → strict_auto_apply (the alias adds make the matcher succeed).
2. **Indirect:** the new `sp.fixtures` rows lift 2D's corroboration rate, moving more 2D records from review_queue to fuzzy auto_apply.

The indirect lift is bounded by what fraction of 2D's review_queue rows are corroboration-bottlenecked vs threshold-bottlenecked. **Need 2D.4 day-7 data to estimate this.** Pessimistic estimate: +0.5-1pp corroboration. Optimistic: +3-5pp. Either way smaller than the direct effect.

---

## Negative space — what 2D.5 explicitly does NOT do

- **Doesn't change the matcher.** Strict / alias / fuzzy tier code unchanged. 2D.5 only adds rows to `sp.team_aliases`.
- **Doesn't add a new resolver tier.** Aliases compound through the existing strict tier on the next cron pass.
- **Doesn't expand `DEFAULT_FL_SPORT_IDS`.** That's an ingestion-config decision with its own risk surface (more API quota, more per-cron records, possible non-tennis sport coverage). Tracked as a separate Phase 2D.5-adjacent item; design lives elsewhere if/when that work happens.
- **Doesn't auto-create teams in the matcher.** Same DO-NOT-CREATE rule from 2C/2D carries forward for runtime resolution. Operator team creation in 2D.5.1 is gated by Open Q1.
- **Doesn't address the Asian-name short-surname problem.** That's 2D.6 (per rev3 §E.10).
- **Doesn't address single-token provider ambiguity.** A provider sending just "Park" is fundamentally ambiguous; alias additions don't resolve that (which Park?).
- **Doesn't cover non-tennis sports.** FL coverage gap is tennis-specific per Q1 100% top-20 overlap on tennis. Soccer / basketball / etc. have different gap profiles; if 2D.4 review surfaces non-tennis anchor_failed pressure, that's a separate scoping conversation.
- **Doesn't backfill historical anchor_failed records.** New aliases benefit FUTURE cron passes only — the historical no_match rows in `sp.resolution_log` stay as-is (they're forensic data, not actionable).
- **Doesn't introduce an automated alias-quality score.** Operator additions are trusted at face value; if quality issues emerge, 2D.5.2 or a separate audit phase addresses it.

---

## Implementation order

Each step ships as its own PR. Each waits on the prior unless explicitly parallel.

### 2D.5.0 — Anchor-failed classification script **[gates the prediction]**

A read-only Python script, same shape as `scripts/investigate_corroboration_gap.sql` (PR #103) and `scripts/dry_run_fuzzy_tier.py` (PR #101). Samples N=200 anchor_failed records from `sp.resolution_log`, joins to provider tables, and produces a classification report:

```
Anchor-failed classification (N=200, tennis, last 7 days)
  Cause-1 (FL doesn't ingest):         42 (21%)
  Cause-2 (FL ingests, alias gap):    118 (59%)
  Cause-3 (genuinely unmatchable):     30 (15%)
  Cause-4 (other / unclassified):      10 (5%)
```

The classifier uses heuristics:

- **Cause-1:** check `sp.fl_events` for ANY event with the same kickoff window; if zero, FL doesn't have it.
- **Cause-2:** check `sp.fl_events` for events with the same kickoff window AND a player whose normalized name shares ≥1 token with the provider's name; if found, alias is the gap.
- **Cause-3 / Cause-4:** fallback for everything else.

**Operator runs once. Output drives 2D.5.1 prioritization (and possibly forces a re-scope if Cause-2 is small).** Same calibration discipline as 2D.2.5 dry-run.

### 2D.5.1 — Operator alias-add CLI **[gated on 2D.5.0 + Open Q sign-off]**

Three Make targets:

- `anchor-failed-report` — read-only report, no writes.
- `team-search` — read-only sp.teams lookup.
- `alias-add` — write to `sp.team_aliases` with `source='operator_2d5'`. Idempotent via ON CONFLICT.

Optionally: `team-create` (gated on Open Q1).

Schema-zero. ~150-300 lines of Python. Tests: integration tests against the test DB (mocked sp.teams + sp.team_aliases tables); unit tests for the search query construction and the alias normalizer.

### 2D.5.1.5 — Production measurement after 2D.5.1 ships

Operator runs `alias-add` for ~50-100 records over 5-7 days. Day-7 query measures bucket-distribution shift. Results inform whether 2D.5.2 (automation) is high-payoff or marginal.

### 2D.5.2 — FL roster bulk-seed **[separate design rev needed — TBD]**

Designed AFTER 2D.5.1.5 measurement is in. Likely structure:

- Iterate ATP/WTA tournaments via `/v1/teams/data` or `/v1/players/data` endpoints.
- For each FL player, look up `sp.teams` by canonical name (with the same normalization the matcher uses).
- If found: insert `sp.team_aliases` rows for every spelling variant FL provides.
- If not found: TBD per Open Q1's outcome — either skip, or auto-create via a separate guarded path.
- `source='fl_roster'` for attribution.

Open question pending FL API research: is there a "list all players in tournament X" endpoint, or do we have to iterate per-event?

### 2D.5.3 — Day-7 review of 2D.5 effectiveness

Combined with 2D.4. Measures:

- Anchor-failed bucket size: did it shrink?
- Strict_auto_applies: did it grow?
- 2D corroboration rate: did the indirect lift materialize?
- Operator capacity used: are we over the daily budget?

Decision point for 2D.5.4 (continue / pivot / sunset).

---

## Test plan

### 2D.5.0 — classification script

- **Unit tests** on the classifier function. Fixture set of synthetic anchor_failed records with known Cause-1/2/3 attributions. Assert classifier returns expected label.
- **Integration test** against test DB with 10-20 seeded `sp.fl_events` and `sp.kalshi_markets` rows. Assert end-to-end report renders the right counts.

### 2D.5.1 — operator CLI

- **Unit tests** for the alias normalizer (already covered in `resolver/_normalize.py` tests; just confirm 2D.5.1's CLI uses the same function — static guard).
- **Integration tests** for each Make target:
  - `anchor-failed-report` returns the right rows for a seeded `sp.resolution_log`.
  - `team-search` matches by canonical name + normalized name; respects sport_id filter.
  - `alias-add` inserts the right row; idempotent on second run; respects ON CONFLICT.
- **Static guards:**
  - alias-add CLI defaults to `--dry-run` (operator must explicitly `--apply`).
  - alias-add only ever uses `source='operator_2d5'` (no other source value in the CLI source).
  - alias-add never writes to `sp.teams` directly (gated through team-create command).

### 2D.5.2 — TBD per design rev

---

## Open questions awaiting sign-off

Each tagged with the PR that's blocked on its resolution.

### Q1 — Team creation in 2D.5.1 scope **[2D.5.1]**

When the operator wants to add an alias for a player NOT yet in `sp.teams`, does 2D.5.1's CLI provide a `team-create` command? Or does the operator have to file a ticket / use a separate manual path?

**Options:**

- **(a)** **Include `team-create` in 2D.5.1.** Operator can both create the team AND add the alias in one workflow. Faster turnaround, fewer hand-offs. Requires the CLI to validate sport_id, country_code, normalized_name uniqueness.
- **(b)** **Defer team-create to 2D.5.2 or later.** 2D.5.1 only adds aliases against EXISTING teams. If a player isn't in `sp.teams`, the operator skips and we capture it as "unmatchable" until automation or a separate creation path lands.
- **(c)** **Allow team-create only for players FL has but `sp.teams` doesn't.** Operator runs `team-create-from-fl --fl-event-id X --player-name Y`, which fetches FL's player metadata via `/v1/players/data` and seeds the `sp.teams` row from authoritative data.

**Recommendation: (a)** for 2D.5.1. The scope of "operator working from anchor_failed reports" already implies they have full context; constraining them to existing teams creates friction without proportional safety. Risk mitigation comes from `--dry-run` default + JSON audit output, not from blocking the operation.

(c) is interesting but couples 2D.5.1 to the FL roster work that belongs to 2D.5.2. Defer (c) as a 2D.5.2 enhancement.

### Q2 — Source value for operator-added aliases **[2D.5.1]**

What value goes in `sp.team_aliases.source` for 2D.5.1 entries?

**Options:**

- **(a)** `'operator_2d5'` — distinguishes from prior `'manual_review'` entries. Day-7 queries can split cleanly.
- **(b)** `'manual_review'` — reuses the existing legacy value. Less attribution clarity.
- **(c)** `'operator'` — generic, future-proof if 2D.5 evolves into 2D.6 / 2E operator workflows.

**Recommendation: (a) `'operator_2d5'`.** Most attribution clarity, no migration cost. Future operator-driven alias paths can use `'operator_2dN'` patterns or graduate to `'operator'` if a unified surface emerges.

### Q3 — How does the operator surface anchor_failed records **[2D.5.1]**

The `anchor-failed-report` CLI is the proposed surface. But the operator also needs to KNOW to run it. Options:

- **(a)** Daily cron-output line in the resolver's print summary (already shows "anchor_failed: N"). Operator reads cron logs, runs the CLI when N is high.
- **(b)** Email / Slack alert when anchor_failed exceeds a threshold (~150/cron). Pushes to operator instead of polling.
- **(c)** Phase 2F admin UI shows it as a tab. (Out of 2D.5 scope.)

**Recommendation: (a)** for 2D.5.1. Existing cron-log surface; no new infra. Operator pulls when they have time. (b) becomes valuable if anchor_failed spikes unpredictably; defer until day-7 data shows the actual variance.

### Q4 — Confidence value for operator-added aliases **[2D.5.1]**

The `confidence` column is documented as "provenance, not per-match score" (per the alias-tier write-back comment in the runner). For operator additions:

- **(a)** `1.0` — operator is ground truth.
- **(b)** `0.95` — leave a small margin in case the operator is wrong (typo, paste error).
- **(c)** Match the alias-tier auto-apply confidence (`final.confidence`, ~0.85+) — consistent with how matcher-driven write-backs are scored.

**Recommendation: (a) `1.0`.** Operator additions ARE ground truth by construction; the write-back compounds at the `confidence=1.0` level into the strict tier's 0.98 auto-apply path on the next cron. If operator error becomes a real problem, 2D.5.4 (post-day-7) can add an audit-and-quarantine step rather than encoding skepticism into the confidence value.

### Q5 — FL roster API research for 2D.5.2 **[2D.5.2]**

`/v1/players/data` takes a player ID, not a tournament ID. `/v1/teams/squad` is for team rosters (soccer, basketball). Is there a "list players in tournament X" endpoint for tennis specifically?

**Action: research before 2D.5.2 design.** If no, 2D.5.2's bulk-seed must iterate per-event (every FL tennis event over the last N weeks → extract players → dedupe → seed). Higher API quota cost but tractable.

**No recommendation here; this is a research question, not a design choice.**

### Q6 — Audit table for operator actions **[2D.5.1 vs 2D.5.2]**

Every alias-add operator action is a state mutation. Should we record it in a dedicated `sp.alias_audit` table (created_by, action_type, before/after JSON snapshots, timestamp)?

**Options:**

- **(a)** No audit table for 2D.5.1; operators capture JSON output of CLI commands to git-tracked files for provenance. Cheap and fast.
- **(b)** Add `sp.alias_audit` migration in 2D.5.1. More complete, more upfront work.
- **(c)** Defer to 2D.5.2 (when bulk automation increases the auditing value).

**Recommendation: (a)** for 2D.5.1. The volume is low (50-100 ops/week worst case), git-tracked CSV is enough. Revisit in 2D.5.2 if automation pushes volume higher.

### Q7 — Should 2D.5 cover non-tennis sports **[2D.5 scope]**

The motivating evidence (~170 anchor_failed/cron, Q1 100% tennis tournament overlap, the user's "Saleshando" example) is tennis-specific. Soccer / basketball / etc. likely have different anchor_failed profiles.

**Options:**

- **(a)** **Tennis-only for 2D.5.0 / 2D.5.1.** Other sports out of scope. If 2D.4 day-7 review surfaces non-tennis anchor_failed pressure, scope a separate Phase 2D.5-NB ("non-ball") or 2D.8.
- **(b)** Sport-agnostic from the start. CLI takes `--sport-code`; same workflow works for any sport.

**Recommendation: (b)** for the CLI tooling itself (it's free — same code path), but **(a) for the operator-facing prioritization.** Tennis is where the measured pressure is. Don't mandate operator review of non-tennis anchor_failed until 2D.4 data tells us it's worth the time.

### Q8 — Re-resolve cadence for newly-aliased records **[2D.5.1]**

Today the runner picks up records `WHERE fixture_id IS NULL` on every cron. Newly-added aliases benefit the NEXT cron pass automatically.

**No question here, just confirming the design relies on existing behavior.** No re-resolve trigger needed; the daily cron is the trigger. **Approved by default.**

---

## Sign-off checklist (rev1)

**Framework:**
- [ ] **C1 selected** (ops-first, automation later) — not A (ops-only) or B (automation-only) or C2 (automation-first). Approved or counter-proposed.
- [ ] **2D.5.0 classification step is mandatory before 2D.5.1 scope locks.** Approved.
- [ ] **2D.5.1 is CLI not admin UI.** Phase 2F admin UI work stays separate. Approved.

**Schema:**
- [ ] **Schema-zero for 2D.5.1.** No new columns; new `source` value only. Approved or counter-proposed.
- [ ] **Audit columns deferred to 2D.5.2 if needed.** Approved.

**Open questions:**
- [ ] **Q1** — Team creation in 2D.5.1: recommend (a) include `team-create` command. Approved or counter-proposed.
- [ ] **Q2** — Source value: recommend `'operator_2d5'`. Approved or counter-proposed.
- [ ] **Q3** — Operator surface: recommend (a) cron-log line. Approved or counter-proposed.
- [ ] **Q4** — Confidence value: recommend (a) `1.0`. Approved or counter-proposed.
- [ ] **Q5** — FL roster API research blocks 2D.5.2 design. Acknowledged.
- [ ] **Q6** — Audit table: recommend (a) git-tracked CSV for 2D.5.1. Approved or counter-proposed.
- [ ] **Q7** — Sport scope: recommend (b) sport-agnostic CLI, (a) tennis-first prioritization. Approved or counter-proposed.

**Negative space:**
- [ ] No matcher changes. Approved.
- [ ] No new tier. Approved.
- [ ] No `DEFAULT_FL_SPORT_IDS` expansion. Approved.
- [ ] No matcher team auto-creation. Approved.
- [ ] No Asian-name short-surname work (2D.6 territory). Approved.
- [ ] No historical backfill of anchor_failed records. Approved.

**Sequencing:**
- [ ] **2D.4 day-7 review (cycles 7+, ~5/16-5/17) gates 2D.5.0 → 2D.5.1 sequencing.** 2D.5 design lands in parallel; ship order locks after day-7 data. Approved.

After rev1 sign-off, 2D.5 ships in this order:

0. **Day-7 review (2D.4)** — informs 2D.5.0 prioritization.
1. **2D.5.0** — anchor-failed classification script. Operator runs once, output drives prediction lock-in.
2. **2D.5.1** — operator CLI. Schema-zero. Three Make targets (`anchor-failed-report`, `team-search`, `alias-add`) + optional `team-create` per Q1.
3. **2D.5.1.5** — production measurement window after 2D.5.1 ships. Operator adds 50-100 aliases over 5-7 days; day-7 query measures bucket shift.
4. **2D.5.2** — FL roster bulk-seed (separate design rev).
5. **2D.5.3** — day-7 review of 2D.5 effectiveness.

---

## What this PR is NOT

- Not code. No Python, no SQL, no migration. Implementation gated on rev1 sign-off + 2D.4 day-7 data.
- Not 2D.6 / 2D.7. Asian-name handling and A.rev2 stay deferred per `PHASE_2D_DESIGN.md` rev3.
- Not in conflict with 2D.4. Day-7 review is the gate; this design exists so we move fast when the gate opens.
- Not a final lock on operator workflow ergonomics. Push back on any of Q1-Q7 and the doc gets revised before 2D.5.0 ships.
- Not an FL ingestion-config change. `DEFAULT_FL_SPORT_IDS` expansion is its own scope.
