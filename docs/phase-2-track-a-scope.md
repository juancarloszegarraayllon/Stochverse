# Phase 2 Track A — Resolver Measurement Infrastructure Scope

Scope doc for Phase 2 Track A: build the measurement substrate that enables every subsequent Phase 2 tuning decision. Throw-away infrastructure, ~6-10 week useful life, deprecated post-Phase 3 cutover.

This document specifies what gets built, what doesn't, and the empirical questions Track A's first week of output should answer.

---

## 1. Goal

Stand up daily-cadence measurement infrastructure that surfaces resolver behavior at the granularity Phase 2 tuning decisions require: **per-sport, scope-filtered, with baseline-shift annotations**.

Per yesterday's (2026-05-19) Phase 2 retrospective, the headline metric — gross auto-apply rate of 0.37% on 14,119 records — was unfit for decision-making because the denominator includes structurally out-of-scope populations (NON_SPORT records, Tennis prop markets, Esports prop variants). Scope-filtered rate by sport surfaces actionable signal; gross rate hides it.

Track A delivers that substrate. Without it, the Tennis dedup workstream (~457-720 merges), the threshold-calibration question, and any future resolver tuning ship into a measurement void — no way to verify the change improved or regressed the resolver.

## 2. Scope (three priorities from PROJECT_STATE 2026-05-18)

| Priority | Item | Status in this scope doc |
|---|---|---|
| 1 | Daily diff infrastructure (architecture doc §11.3) | Primary deliverable. Two-deliverable shape per §5. |
| 2 | Test corpus extraction (architecture doc §12.1) | Subsequent deliverable; design covered §13. Ships after daily-diff substrate is stable. |
| 3 | Re-resolution loop scope verification (architecture doc §7.7) | **Run as standalone diagnostic NOW**, before Track A build cycle starts. Result folds into Track A's design + tomorrow's KBL empirical interpretation. See §6. |

## 3. Architecture overview

```
                                ┌────────────────────────────┐
                                │  Pattern D pre-flight      │
                                │  (verify endpoint at start)│
                                └─────────────┬──────────────┘
                                              │
              ┌─────────────────┐  fresh reads │
              │ sp.kalshi_markets│◀────────────┤
              └─────────────────┘              │
                                               │
              ┌─────────────────┐  fresh reads │  ┌─────────────────────┐
              │ sp.fl_events    │◀────────────┴─▶│ scripts/daily_diff.py│
              └─────────────────┘                │  (cron @ 02:30 UTC) │
                                                 └──────────┬──────────┘
              ┌──────────────────┐                          │ writes
              │ sp.resolution_log│  optional join           │
              │ (legacy compare) │◀─────────────────────────┤
              └──────────────────┘                          │
                                                            ▼
                          ┌────────────────────────────────────┐
                          │ sp.daily_diff_reports              │
                          │ (structured rows, machine-readable)│
                          └────────────────────────────────────┘
                                            │
                          ┌─────────────────┴────────────────┐
                          │ sp.baseline_shifts               │
                          │ (annotations: dedup events,      │
                          │  scope-filter changes, bootstraps)│
                          └──────────────────────────────────┘
                                            │
                                            ▼
                          ┌────────────────────────────────────┐
                          │ scripts/render_daily_diff_report.py│
                          │ (on-demand markdown, human-readable)│
                          └────────────────────────────────────┘
```

Two data planes:

- **Machine-readable**: `sp.daily_diff_reports` rows + `sp.baseline_shifts` rows. Queried for trend analysis, regression-detection, Phase 3 cutover gating.
- **Human-readable**: markdown report rendered on-demand from the structured data (per Q1 — NOT committed to git, NOT generated daily as a side effect). Operator runs `python scripts/render_daily_diff_report.py [--days 7]` to materialize the report for that morning's review.

## 4. Data sources (§4 flag addressed)

**Fresh reads from `sp.kalshi_markets` and `sp.fl_events`**, not joins against existing `sp.resolution_log`. Reasoning:

- Catches the `signal_extraction_skipped` population (records that never produced a resolution_log row because they failed parser-stage extraction). Yesterday's 34% signal_extraction_skipped finding is invisible to a resolution_log-only design.
- Decouples Track A measurement from resolver's own audit trail. If the resolver's log-writing has gaps (e.g., crashes that don't write, transactions that roll back), Track A's measurement isn't gap-correlated.
- Aligns with the architecture doc §11.3 framing: daily-diff measures the resolver against the source-of-truth ingestion population, not against the resolver's own output.

Trade-off accepted: re-runs all matcher logic against last-24h ingestion population. ~14k records/day at current volume; expected ~30-90s wall time per pass. Acceptable.

`sp.resolution_log` IS used in Deliverable 1 (legacy comparison) as the read source for what the legacy Tier 1-4 pairing produced. Decoupled from the fresh-read path.

## 5. Deliverables

Two deliverables, **sequenced per Q6: Deliverable 2 ships first**.

### Deliverable 2 — Daily-diff substrate (lower-risk, all-new code)

**Estimated effort: 1-2 days.**

Components:

- **Migration**: `sp.daily_diff_reports` table (`id`, `report_date`, `window_start`, `window_end`, scope-filtered metrics columns, raw counts columns, `report_json` for sample disagreements + histogram, `created_at`). Plus `sp.baseline_shifts` table (`id`, `event_type`, `event_date`, `affected_population`, `expected_metric_delta`, `notes`).
- **Script** (`scripts/daily_diff.py`): pulls 24h of records from `sp.kalshi_markets` + `sp.fl_events`, runs the new resolver against each via the existing `TieredMatcher`, classifies outcomes per the §7 measurement targets, writes one row to `sp.daily_diff_reports`.
- **Pattern D pre-flight check at script start** (§10): verifies endpoint matches production, exits cleanly with error message if not.
- **Railway cron**: `30 2 * * *` (02:30 UTC, 15-min buffer after Kalshi 02:15 per Q5).
- **Render script** (`scripts/render_daily_diff_report.py`): on-demand markdown rendering from `sp.daily_diff_reports` + `sp.baseline_shifts`. Default `--days 7` window. Output to stdout or `--out <path>` for ad-hoc save. NOT committed to git per Q1.
- **Tests**: classification logic, histogram generation, endpoint-verification pre-flight, idempotency, baseline-shift annotation read-through to report.

Output operator sees within Deliverable 2's scope:

- Per-sport scope-filtered auto-apply rate
- Per-tier breakdown (strict / alias / fuzzy / no_match / review_queue / crash)
- Personal-path vs team-path aggregated rates
- Queue depth + queue-velocity trends
- Baseline-shift event log

**No AGREE/disagree comparison with legacy** in Deliverable 2 — that's Deliverable 1's additive enhancement.

### Deliverable 1 — Legacy Tier 1-4 extraction (HIGHER-RISK, touches production code per §5 flag)

**Estimated effort: 1-2 days, but risk-weighted higher than Deliverable 2 due to production-serving-code surface.**

Components:

- **Survey** main.py's Tier 1-4 pairing code paths. Document inputs/outputs/side effects at `docs/legacy/tier_1_4_surface.md`.
- **Extract** to `legacy/tier_1_4_resolver.py` as pure-function module: no module-state mutation, no FL API calls, deterministic. Mirrors the architecture doc §11.6 Phase 5 decommission preparation framing.
- **Compatibility shim** in main.py: `/api/v3` responses byte-identical before/after extraction. Production-serving traffic unaffected.
- **Tests**: `tests/test_legacy_tier_1_4.py` — deterministic output, no state mutation, known Tier 1-4 cases.
- **Integrate** into `daily_diff.py`: run legacy resolver against same 24h slice, classify each record as AGREE / new-better / old-better / ambiguous.

**Risk per §5 flag**: legacy extraction is the only Track A work that touches production-serving code. Before/after byte-comparison of `/api/v3` sample responses is the verification gate before Deliverable 1 merges.

**Dual-purpose framing**: Deliverable 1 simultaneously serves Track A's measurement substrate AND the architecture doc §11.6 Phase 5 decommission preparation (per PROJECT_STATE 2026-05-18 v1.5 amendment). Deletion becomes mechanical at Phase 5 instead of a refactor.

### Sequencing rationale (Q6 — Ship Deliverable 2 first)

**Deliverable 2 first, Deliverable 1 follows 2-3 days later.** Reasoning:

- Per-sport scope-filtered rates from Deliverable 2 are actionable signal even without legacy comparison. Operator can prioritize Tennis dedup, threshold-calibration, NON_SPORT scope-filter decisions from Deliverable 2 alone.
- Decouples measurement substrate from legacy-extraction risk. If Deliverable 1 hits timeline slip (production-code touching is inherently riskier), Deliverable 2 still ships.
- Legacy comparison adds the "is the new resolver better than the old?" dimension as an additive confidence-check, not as primary signal.

Risk of sequential approach (Deliverable 2 alone for 2-3 days): we can't classify individual records as "new-better" vs "old-better" until Deliverable 1 lands. Acceptable — population-level metrics in Deliverable 2 are enough to drive Phase 2 priorities.

### Standalone diagnostic — Re-resolution loop scope (§7.7 verification — RESOLVED)

**Finding X RESOLVED: H1 confirmed.** Operator ran the discriminator query on 2026-05-20. Result: all 30 sampled pending review_queue records showed ~36 `sp.resolution_log` entries each post-queue-creation — exactly the 3-tiers × ~12-days pattern. Records from May 9-10 (~11 days old) have 33-36 retry attempts. **The cron re-processes pending review_queue records daily across all three tiers (strict → alias → fuzzy).**

Implications for Track A design:

- **Backlog drainage is automatic.** Tennis dedup post-merge: the 1,866 Tennis review_queue records get re-evaluation chances daily without a separate drainage script. Baseline-shift annotation models "dedup event causes discontinuous metric jump" rather than "gradual shift as new records arrive."
- **KBL morning test is meaningful.** The 2 pending KBL records WILL be re-evaluated tonight against the now-populated bootstrap aliases. Tomorrow's queue-depth measurement reflects real resolver behavior, not an "already-queued records skipped" artifact.
- **Retry traffic dominates `sp.resolution_log` writes.** Pending review_queue depth × 3 tiers × daily cron = retry-traffic write rate. With current ~6,654 pending depth × 3 × 365 days ≈ **~7.3M rows/year just from retries**, before counting newly-arrived records. §6.5 archival (Issue #164) becomes a Phase 2 dependency, not deferred maintenance. See §12 risk row.

Discriminator query (for future reference / re-verification cycles):

```sql
-- Does the cron re-process pending review_queue records?
-- Verified 2026-05-20: yes, all 3 tiers, daily.
SELECT
  COUNT(DISTINCT rq.provider_record_id) AS reprocessed,
  COUNT(DISTINCT rq2.provider_record_id) AS pending_not_reprocessed
FROM sp.review_queue rq
LEFT JOIN sp.resolution_log rl ON (
  rl.provider_record_id = rq.provider_record_id
  AND rl.decided_at >= (
    SELECT MAX(started_at) FROM sp.resolver_runs
    WHERE provider = 'kalshi' AND finished_at IS NOT NULL
  )
)
LEFT JOIN sp.review_queue rq2 ON (
  rq2.id = rq.id
  AND rl.provider_record_id IS NULL
)
WHERE rq.status = 'pending';
```

Affects §7 measurement-target wording (`sp.resolution_log` row-volume tracking added) and Deliverable 2's baseline-shift annotation semantics (events cause discontinuous metric jumps, not gradual shifts).

## 6. Output format (Q1 resolution)

Hybrid: on-demand render from data; do NOT commit daily reports to git.

- **Structured** (`sp.daily_diff_reports`): one row per cron pass. Indexable, queryable for trends. Standard SQL output for ad-hoc analysis.
- **Human-readable** (markdown): `scripts/render_daily_diff_report.py` renders from `sp.daily_diff_reports` + `sp.baseline_shifts` on-demand. Default 7-day window; configurable.

No `docs/daily-diff/` directory of committed markdown reports. Reasoning:
- Daily reports drift the repo unnecessarily.
- Trend analysis needs SQL queries, not git log archeology.
- On-demand rendering keeps the operator-facing format flexible (markdown today; could shift to HTML, JSON, or other formats later without migration).

## 7. Measurement targets (§7 flag addressed)

Five metrics from yesterday's table, plus three operator-throughput additions per the §7 flag:

| Metric | Definition |
|---|---|
| Scope-filtered auto-apply rate (overall) | `auto_applies / (records_scanned - signal_extraction_skipped - non_sport_filtered - prop_market_filtered)` |
| Per-sport auto-apply rate | same denominator, partitioned by `reason_detail->>'sport'` |
| Per-tier resolution rate | strict / alias / fuzzy / no_match / review_queue / crash breakdown, per sport |
| Personal-path vs team-path rate | aggregated by INDIVIDUAL_SPORT_CODES membership |
| Baseline-shift event log | annotation table tracking dedup events, scope-filter changes, alias bootstraps |
| **Per-sport queue depth** | rows in `sp.review_queue` where `status='pending'`, grouped by `reason_detail->>'sport'` |
| **Average time-in-queue** | per-sport median + p95 of `(NOW() - created_at)` for pending review_queue records |
| **Abandonment rate** | per-sport fraction of review_queue records that age beyond N days without operator action (N TBD, default 14) |
| **`sp.resolution_log` row volume per cron run** | partitioned by `reason_code` (strict / alias / fuzzy / no_match / review_queue / crash). Tells us the §6.5 archival sizing requirement (per Finding X retry-traffic finding — ~7.3M rows/year extrapolated from current pending depth). |

The bottom three feed Phase 2E operator-throughput design. Capturing them in Track A's first pass saves a separate workstream when Phase 2E begins; cost-incremental given the data source overlap.

The `sp.resolution_log` row-volume metric (added post-Finding X) addresses the retry-traffic finding: every pending review_queue record produces ~3 resolution_log rows per daily cron (one per tier consulted). Track A measures the rate to inform archival cron frequency for Issue #164 (§6.5).

Gross (unfiltered) rate is reported alongside scope-filtered, but framed explicitly as "raw" with operator attention directed at scope-filtered.

## 8. Annotation mechanism (Q2 resolution)

**Separate `sp.baseline_shifts` table.** One row per event.

Schema:

```sql
CREATE TABLE sp.baseline_shifts (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  event_type            TEXT NOT NULL,  -- 'dedup' | 'scope_filter' | 'alias_bootstrap' | 'threshold_change' | 'other'
  event_date            DATE NOT NULL,
  affected_population   TEXT NOT NULL,  -- e.g., 'Tennis players (cross-format dupes)', 'NON_SPORT records'
  expected_metric_delta TEXT,           -- e.g., 'Tennis auto-apply rate +5-10% post-dedup'
  notes                 TEXT,           -- free-form context
  created_at            TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  created_by            TEXT            -- operator or 'script' or '<PR-number>'
);

CREATE INDEX ix_baseline_shifts_event_date ON sp.baseline_shifts(event_date);
CREATE INDEX ix_baseline_shifts_event_type ON sp.baseline_shifts(event_type);
```

Cleaner query surface than nested JSONB. Daily-diff render reads from this table + correlates with `sp.daily_diff_reports` rows on date to attribute observed metric shifts to known events.

Population-level events the table tracks (initial set):
- Tennis dedup ship (post-Track A, expected ~5-10% Tennis auto-apply rate bump)
- NON_SPORT scope-filter ship (expected to clean denominator; raw rate ≠ scope-filtered changes minimally)
- Kalshi prop-market vocabulary additions (per Issue #160 quarterly cadence)
- Future bootstrap applies (Handball, Snooker, etc.)
- Threshold-calibration changes (when those land per Track B)

## 9. Cron timing (Q5 resolution)

**02:30 UTC.** 15-minute buffer after the existing Kalshi cron at 02:15 UTC. Sufficient — Kalshi cron typical wall time ~30s per `railway.toml:65-70`, so 02:15→02:16 finish, 02:30 daily-diff start has ~14 min margin.

Cron-collision considerations:
- FL cron at 02:00 UTC: 30-min gap, no conflict.
- Kalshi cron at 02:15 UTC: 15-min gap, no conflict.
- No other crons scheduled at 02:30 per `railway.toml`.

## 10. Pattern D application

Track A's data-read path needs the same endpoint-verification discipline that Pattern D applies to write paths.

The verify-endpoint-before-write principle (Pattern D on PR #167) extends to verify-endpoint-before-read for measurement scripts. Reasoning:

- A measurement script connected to the wrong DB produces wrong baselines.
- Wrong baselines drift undetected because no one cross-checks them daily.
- A week of measurement against bootstrap-test (per the 2026-05-19/20 incident) would create false trends that drive wrong tuning decisions.

Implementation: `daily_diff.py` runs `SELECT current_database(), current_schema(), inet_server_addr();` at script start. Compares `inet_server_addr()` against a hard-coded production endpoint identifier (or against an env var like `EXPECTED_PRODUCTION_ENDPOINT`). Exits cleanly with an error message if mismatch.

Per Q3 — this is a **sub-bullet under Pattern D on PR #167**, not Pattern E. Same pattern, two directions (read and write). Documented inline in PR #167's Pattern D section.

## 11. Operator role

| Phase | Activity | Time |
|---|---|---|
| Scope-doc review | Comment on this doc, lock open questions | ~30 min one-off |
| Deliverable 2 PR review | Same shape as KBL PR review | ~30 min |
| Deliverable 1 PR review | Higher-risk per §5; verify before/after `/api/v3` byte-identical | ~45 min |
| Day-1 verification | Read first daily-diff report; check counts match expectation | ~15 min |
| Daily report scan | Morning operator scan during 6-10 week measurement window | ~5-10 min/day |
| Baseline-shift annotation | Operator inserts row into `sp.baseline_shifts` when population events occur (post-dedup, post-bootstrap, etc.) | ~2 min per event |

Total operator commitment: ~3-4 hours one-off (scope review + PR reviews + day-1) + 5-10 min/day ongoing for the 6-10 week window.

## 12. Risks + mitigations (§12 flag addressed)

| Risk | Mitigation |
|---|---|
| Legacy extraction (Deliverable 1) regresses production /api/v3 responses | Before/after byte-comparison gate; revert path explicit; ships AFTER Deliverable 2 so measurement substrate isn't blocked by extraction risk |
| Cron timing collision with Kalshi cron 02:15 UTC | 15-min buffer at 02:30; Kalshi wall time ~30s historically; documented escape valve to push to 02:45 if Kalshi runtime grows |
| Daily-diff script run against wrong DB endpoint (Pattern D scenario) | Pre-flight endpoint check at script start; exits cleanly on mismatch |
| **Test corpus extraction surfaces records the legacy classifies differently than expected** (NEW per §12 flag) | Classify-then-review workflow: extracted corpus cases get operator approval before pinning into regression test suite. Records where legacy and new disagree get manual operator classification as "ground truth" before being added to canary set. |
| Cron writes succeed but rendered report shows stale data (cache, race) | Render script reads `sp.daily_diff_reports` ordered by `report_date DESC`; explicit "data through report_date X" header in markdown output |
| Scope-filter rules drift (NON_SPORT decision changes, new prop-market vocabulary entries) | Filter rules versioned via a constant in `daily_diff.py`; baseline-shift event logged when version changes. Reports include the filter-version they ran against. |
| Measurement infrastructure outlives its useful life and accumulates maintenance debt | Hard 6-10 week lifespan committed in §14; deprecation runbook part of scope. |
| **`sp.resolution_log` unbounded growth from retry traffic** (NEW post-Finding X) | Current pending review_queue depth ~6,654 × 3 tiers × daily cron ≈ ~7.3M rows/year just from retries, before counting newly-arrived records. Mitigation: §6.5 archival via Issue #164 — promoted from "deferred maintenance" to "Phase 2 dependency" per Finding X. Track A's `sp.resolution_log` row-volume metric (§7) informs archival cron sizing. Track A is not blocked on §6.5 landing first (storage cost is incremental over 6-10 weeks), but Phase 3 cutover IS blocked on §6.5 because the retry-traffic pattern persists post-Phase-3 absent archival. |

- **NOT a metrics platform.** No Grafana, no Datadog. Operator reads SQL output and rendered markdown.
- **NOT a tuning automation.** Operator looks at trends and makes tuning calls. Track A surfaces signal; humans decide.
- **NOT an admin UI feature.** No `/admin/daily-diff` page. Phase 3 might productize some of these views; Phase 2 Track A doesn't.
- **NOT a backlog drainage tool.** Re-resolution loop verification (§5 diagnostic) tells us whether backlog drains naturally; if not, a separate workstream addresses drainage. Track A measures the situation; doesn't fix it.
- **NOT permanent.** See §14.

## 14. Lifespan + deprecation

**Useful life: 6-10 weeks.** Deprecated after Phase 3 cutover stabilizes (per architecture doc §11.5 throw-away-infrastructure pattern).

Deprecation steps when Phase 3 lands:
1. Cron stopped (Railway dashboard).
2. `sp.daily_diff_reports` + `sp.baseline_shifts` tables archived to object storage per architecture doc §6.5 (or Phase 5 preservation steps per PROJECT_STATE 2026-05-18, whichever lands first).
3. Tables dropped from production.
4. `scripts/daily_diff.py` + `scripts/render_daily_diff_report.py` + `legacy/tier_1_4_resolver.py` deleted from main; preserved via Phase 5 git-tag + sibling-repo archive per the v1.5 amendment Phase 5 preservation steps.

The 6-10 week framing assumes Phase 3 cutover is on the architecture doc's expected timeline. If Phase 3 slips, Track A extends in 4-week increments with explicit review at each extension. Indefinite drift is the failure mode to avoid.

## 15. Open questions (resolved during scope-doc cycle)

| Q | Question | Resolution |
|---|---|---|
| Q1 | Output format: committed daily markdown vs on-demand render? | **On-demand render from data**. No committed daily reports. Per §6. |
| Q2 | Baseline-shift annotation: nested JSONB in `sp.daily_diff_reports` vs separate table? | **Separate `sp.baseline_shifts` table.** Cleaner query surface. Per §8. |
| Q3 | Endpoint-verification pattern: Pattern D extension vs new Pattern E? | **Sub-bullet under Pattern D on PR #167.** Same pattern, two directions. Don't proliferate Patterns when one extends naturally. Per §10. |
| Q4 | Test corpus composition: balanced by sport-class vs by volume? | **Sport-class balance: 30 team-path + 30 personal-path + 30 prop-market + 10 edge cases = 100 cases.** Matches §7.5 sport-class distinction. Per §13. |
| Q5 | Daily-diff cron timing: 02:30 vs 02:45 UTC? | **02:30 UTC.** 15-min buffer after Kalshi 02:15. Per §9. |
| Q6 | Deliverable sequencing: Deliverable 1 first, Deliverable 2 first, or parallel? | **Deliverable 2 first, Deliverable 1 follows 2-3 days later.** Decouples measurement substrate from legacy-extraction risk; Deliverable 2's per-sport scope-filtered rates are actionable signal even without legacy comparison. Per §5. |

## 16. First-week empirical questions

Track A's Deliverable 2 output should answer these by end of week 1:

1. **What's the actual scope-filtered auto-apply rate by sport?** Yesterday's 0.37% gross-rate finding masked per-sport variance (Baseball ~1.10%, Soccer ~1.12%, Tennis 0.00%, etc.). Daily measurement confirms which sports are stable and which drift.

2. **Is queue depth growing or shrinking day-over-day?** Issue #163's 6,654 queue-depth finding (66× the §7.5 alert threshold) — does the trend bend in either direction with daily measurement, or stay flat?

3. **What's the no_match breakdown's day-over-day variance?** If the `fail_reason` distribution changes meaningfully day-to-day, the resolver is drifting (corroboration rate changing, candidate-index shifting). If stable, the resolver has reached a steady state — tuning decisions can be made with confidence.

4. **Are any sports trending toward or away from the architecture doc §2 60% floor?** Per the v1.5 amendment #7 (§7.5 sport-class distinction), the 60% floor applies to team-path sports. Track A surfaces which team-path sports are approaching it and which aren't.

Bonus (folds in after Deliverable 1 ships):
5. **What's the new-resolver-vs-legacy AGREE rate?** Per architecture doc §11.3 framing. Target: AGREE >95%, new-better >3%, old-better <2%, ambiguous <1% (calibrated to reality post-week-2).

## 17. Test corpus composition (Q4 resolution, scope expanded for Deliverable 1)

Per Q4, the corpus extracts 100 cases balanced by sport-class:

- **30 team-path cases**: 6 Baseball + 6 Soccer + 6 Basketball + 6 Hockey + 6 spread across other team-path sports
- **30 personal-path cases**: 10 Tennis + 6 MMA + 6 Boxing + 8 spread across other personal-path sports
- **30 prop-market cases**: 10 Baseball prop + 10 Basketball prop + 10 spread across other prop-bearing sports; per Issue #160 vocabulary
- **10 edge cases**: NON_SPORT records (3), bilateral collision records (3), asymmetric anchor failure records (2), NULL kickoff records (2 — per Issue #162 β scope)

Classification gates per the §12 risk mitigation:
- Each case extracted from `sp.resolution_log` archive.
- Operator approves "ground truth" per case before it lands in regression suite.
- Records where legacy and new resolvers disagree get explicit operator classification.
- Cases versioned under `tests/corpus/<sport-class>/<sport>_<case-id>.json` mirroring architecture doc §12.1.

## Finding X — Re-resolution loop scope (RESOLVED 2026-05-20: H1 confirmed)

Operator ran the §5 discriminator query on 2026-05-20. **All 30 sampled pending review_queue records showed ~36 `sp.resolution_log` entries each post-queue-creation** — the 3-tiers × ~12-days pattern. Records from May 9-10 (~11 days old) have 33-36 retry attempts.

**The cron re-processes pending review_queue records daily across all three tiers (strict → alias → fuzzy).** H1 confirmed.

| Outcome | Confirmed | Implication |
|---|---|---|
| **Cron re-processes all pending records** | ✅ **CONFIRMED** | Tennis dedup auto-drains the existing backlog. Track A's baseline-shift event for "dedup ship" models discontinuous metric jumps. KBL morning test will work as designed — the 2 pending records WILL be re-evaluated against the now-populated bootstrap aliases tonight. |
| ~~Cron processes only newly-arrived records~~ | ❌ ruled out | n/a |
| ~~Partial / conditional re-processing~~ | ❌ ruled out | n/a |

### Three downstream implications (folded into the scope doc above)

1. **Backlog drainage is automatic** for Tennis dedup, KBL re-resolution, and any future bootstrap-or-coverage event. No separate drainage workstream needed.
2. **`sp.resolution_log` retry traffic dominates writes.** Pending review_queue depth × 3 tiers × daily cron ≈ ~7.3M rows/year just from retries. New §7 measurement target (`sp.resolution_log` row volume per cron run by reason_code) added. New §12 risk row added (retry-traffic unbounded growth). §6.5 archival per Issue #164 promoted from "deferred maintenance" to "Phase 3 cutover prerequisite."
3. **v1.5 amendment pile implications** (folded into PR #169's PROJECT_STATE 2026-05-19 entry — operator call on whether to revise PR #169 now or fold into the day-21 entry):
   - Item #3 (§6.5 archival via Issue #164) — more urgent. Phase 2 dependency, not deferred maintenance.
   - Item #5 (audit-stream separation) — refined. Operator approvals don't write resolution_log, BUT cron retry writes DO. Different streams have different growth pressures.
   - Item #6 (fixture-construction routing shape) — refined. The 2 pending KBL records have each been re-attempted 36 times despite the routing-shape blocker. Worth dedicated investigation when Track A surfaces per-record retry-count distribution.

## 18. Status

**Scope-doc state**: Finding X resolved, ready for operator review + merge. All open questions Q1-Q6 locked.

**Next actions**:
1. ✅ Operator runs Finding X diagnostic — RESOLVED 2026-05-20, H1 confirmed.
2. Operator reviews scope doc on PR #175; comments / refinements during PR cycle.
3. Scope-doc PR marked ready-for-review (from DRAFT) and merges to main.
4. Deliverable 2 work starts Wednesday (2026-05-21) after scope doc merges.
5. Deliverable 1 work starts ~2-3 days after Deliverable 2 ships (Friday/Saturday).

## Related

- PROJECT_STATE 2026-05-18 — Phase 2 priority reorder (Track A as #1 priority)
- PROJECT_STATE 2026-05-19 — KBL bootstrap day + Tennis crash incident + Pattern D origin (PR #169)
- PR #167 — KBL methodology doc + Patterns A/B/C/D
- Issue #160 — Kalshi prop-market architectural followup
- Issue #163 — Queue depth 66× §7.5 alert threshold
- Issue #164 — §6.5 archival job not implemented (Track A prerequisite per #164's placement-decision lean)
- v1.5 amendment items 1-7 (pile, ordered by emergence)
