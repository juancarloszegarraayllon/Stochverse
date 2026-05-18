# PROJECT_STATE.md

Living state of the SP Architecture rebuild. Each session updates this
file with what landed, what was investigated, and what's open for the
next session. Treat it as the project's running journal.

---

## Session — 2026-05-18

### Queue depth at 66x §7.5 alert threshold — α finding (headline)

Production query 2026-05-18 measured **6,654 pending records in `sp.review_queue`**:

| Reference | Value | Multiple |
|---|---|---|
| Architecture doc §7.5 steady-state target | <20 | — |
| Architecture doc §7.5 alert threshold | >100 | — |
| **Current pending depth** | **6,654** | **333x target, 66x alert** |

Add rate: ~660-740/day per recent operator-validation cycle (Sunday's day-7 retrospective + today's reframing).

Per architecture doc §7.5: a pending-queue depth above the alert threshold indicates **"a problem with the resolver, not the reviewer's pace."** Throughput-side interventions (faster operator decisions, UI improvements, multi-operator workflow) do not address the root cause when the resolver's inflow exceeds plausible operator throughput by orders of magnitude.

**Implication:** this is a Phase 2 architectural sub-track, not a throughput problem. Tracked separately as **#163** (filed today). Not a fix-design conversation for today-scope — needs separate planning. Investigation order would start with decomposing the 6,654 by routing tier (alias-tier collisions vs fuzzy-tier review_queue vs others) and by failure pattern.

**How it surfaced:** during investigation of Issue #162 (admin detail-view NULL-kickoff conflation, originally framed as "34 zombie records"). A verification query intended to size #162's affected scope returned `6,654 pending` — the total queue depth, not a NULL-kickoff subset. The misframing-evolution is preserved in #162's body; #162 has been rescoped strictly to the NULL-kickoff approval hard-block (β), with the queue-depth finding (α) moved to #163.

**Not in scope of this finding:**

- Any specific fix proposal — investigation needed first.
- Speculation about which Phase 2 sub-PR addresses this. Coverage work (Handball/Snooker bootstrap) may or may not be related; needs investigation, not assumption.
- Connection to operator-throughput findings. The §7.5 quote explicitly disentangles them.

### Phase 5 decision — pair decommission with explicit legacy preservation

Decision captured today during the Phase 2 verification cycle. Architecture doc §11.6 (Phase 5 Decommission) currently calls for full deletion of the legacy backend code from the active codebase. Discussion today identified the gap: full deletion eliminates the ability to reference how the old system worked, which has legitimate diagnostic value (debugging regressions in v4 that may have been handled differently in v3; explaining historical schema choices; recovery from edge cases the new system doesn't yet cover). Keeping the legacy code in-tree alongside v4 was rejected — that creates the exact dual-system maintenance burden Phase 5 is meant to resolve.

**Resolution:** Phase 5 deletion proceeds as planned, paired with explicit preservation steps that capture pre-deletion state in clearly-separated, read-only form. Active codebase gets cleaned up; legacy stays browseable indefinitely as diagnostic reference.

**Preservation steps to execute before Phase 5 deletion lands:**

1. **Tag the pre-deletion commit** in the main repo as `v3-legacy-archive`. One git command, zero ongoing cost. Tag is durable in main repo history.
2. **Push current state to a sibling repo** `stochverse-legacy` on GitHub. Use GitHub's archive-repository feature to mark it read-only — one click; signals "preserved, not maintained" without ambiguity.
3. **Final `pg_dump` of legacy entity tables** before they are removed per architecture doc §5.5: `public.entities`, `public.entity_aliases`, `public.game_scores`, and any other legacy tables being consolidated. Store in object storage with clear naming (e.g., `legacy-public-entities-final-YYYY-MM-DD.sql.gz`). Aligns with §9.9 backup practices.
4. **Add a "Legacy backend reference" section to the main repo's README** pointing at the preservation paths:
   - Git tag in main repo
   - Archived sibling repo URL
   - Object storage location of final `pg_dump`

**Explicit non-goals — what this preservation is NOT:**

- Not a permanently-running legacy endpoint. That would defeat the architectural cleanup per §8.1 deprecation policy.
- Not an extension of the v3 deprecation window beyond the 60-day floor in §8.1.
- Not a soft-rollback mechanism. Phase 3's 14-day dual-running window is the rollback path; this preservation is for diagnostic reference only, with no runtime role.

**Document changes when Phase 5 planning begins (weeks out, after Phase 3 cutover stable at 100% traffic for 14+ days):**

- Architecture doc §11.6 amended to list the four preservation steps as preconditions before deletion proceeds.
- Architecture doc changelog v1.5 entry. Proposed language:

  > **v1.5 — Phase 5 preservation steps.** Date: [Phase 5 planning date]. Author: decision pass during Phase 2 verification cycle on 2026-05-18. Phase 5 decommission (§11.6) is paired with explicit preservation steps — git tag, archived sibling repo, final `pg_dump` of legacy data tables, README pointer — to address the legitimate "see how the old system worked" diagnostic need without compromising the architectural cleanup. Preservation is for reference use only, not as a running fallback. §11.6 updated to list the preservation steps as preconditions before deletion proceeds.

**Why log this decision today rather than at Phase 5 planning:**

Per architecture doc §16.3 (Document maintenance): "Update it as decisions are made, open questions are resolved... The worst outcome is a document that no one trusts because it has drifted from reality." This decision emerged from today's session while reasoning is fresh. Capturing it now prevents it from being lost or re-litigated when Phase 5 planning starts. No code changes today — execution waits for the Phase 5 trigger (Phase 3 cutover complete + v4 stable at 100% traffic for 14+ days). Decision ratifies as architecture doc v1.5 amendment when Phase 5 planning begins.

---

## Session — 2026-05-17

### Phase 2F.1.5 day-7 retrospective + 2D.4 three-tier resolver review

First day-7 retrospective in the Phase 2F program. The 2F.1.5 (operator-throughput) and 2D.4 (three-tier resolver) retrospectives fold into the same session — same `sp.resolution_log` queries surface both retrospectives' findings.

### Session shape and limitations

Operator (jcz) did not drive the review queue between PR #156 production apply (2026-05-14 19:30 UTC) and today's session — Friday/Saturday used for non-engineering work per standing instructions.

This means 2F.1.5 retrospective measures **resolver-side data only**. Operator-throughput data (UI friction, per-record decision time, approve/reject distribution) is structurally absent. Two pending approvals exist in `sp.review_queue`; both are pre-launch test records, not real operator work. Operator-driven validation deferred to follow-up session and begins concurrent with Priority A development.

### Volume reality

- **22,619 unique records** over 7 days (2026-05-11 to 2026-05-17)
- **~3,231 records/day raw** — approximately 3.2× the design doc's ~1,000/day estimate
- Provider split: FlashLive 67.4% (15,250), Kalshi 32.6% (7,369)

After filtering Golf single-player records (out-of-scope by design — see Finding 2): 19,313 records/week, ~2,759/day, about 2.8× design estimate. Still meaningfully above estimate but in a believable range.

### Resolution rate reality

Latest decision per unique `provider_record_id`:

| Outcome | Records | % | Notes |
|---|---|---|---|
| `no_match` | 13,059 | 57.7% | Largest bucket; further decomposed below |
| strict tier clean | 5,149 | 22.8% | All clean matches come from strict |
| `review_queue` routed | 4,312 | 19.1% | Reaching operators correctly |
| alias tier clean | 91 | 0.4% | Almost zero |
| fuzzy tier clean | 8 | 0.04% | Essentially zero |

**Key observation**: alias and fuzzy tiers produce 99 total clean matches over 7 days. The system runs almost entirely on strict tier. Alias coverage is genuinely thin; fuzzy threshold is conservative enough that most fuzzy decisions go to `review_queue` rather than auto-resolve.

### sp.review_queue state

- **4,822 pending records** waiting for operator decisions
- **2 approved records** total ever (test records from pre-launch operator development)
- **0 rejected records**

At design-assumption 240 records/day operator capacity, current backlog alone takes ~20 days to clear. Daily inflow of new review_queue records is ~616/day (4,312 over 7 days). Daily deficit at sustainable capacity: ~376/day, growing.

### The no_match bucket decomposed

| Fail reason | Records | % of no_match |
|---|---|---|
| `fuzzy_no_team_resemblance` | 6,696 | 51.3% |
| `structural_normalize_failed` | 3,306 | 19.8% (100% Golf single-player) |
| `deferred_to_2d` | 1,789 | 13.7% (NOT a bug — see Finding 3) |
| `sport_not_classified` | 958 | 7.3% |
| `alias_no_team_resemblance` | 793 | 6.1% |
| `below_review_threshold` | 228 | 1.7% |

### Findings inside the no_match bucket

#### Finding 1 — `fuzzy_no_team_resemblance` has three distinct subgroups (most important finding)

The 6,696 records in this bucket aren't homogeneous:

**Both sides anchor-failed: 5,024 (74.7%)** — genuine coverage gaps. Sport breakdown: Baseball 19.6%, Tennis 19.1%, Handball 11.8%, MMA 7.9%, Basketball 6.8%, Darts 5.6%, Rugby Union 5.1%, Cricket 4.3%, Boxing 3.9%, Snooker 3.7%, Soccer 3.6%, Aussie Rules 3.2%, others. Need actual data work (player rosters, alias expansion, threshold tuning).

**Asymmetric records: 1,705 by initial slice, 2,051 by separate breakdown** — one side anchored cleanly, other didn't. Further decomposed via heuristic on `provider_normalized LIKE '%:%'`:

- **~89.6% (1,837) are real asymmetric records** that should route to operators
- **~10.4% (214) are Kalshi prop-bet markets** ("Colorado: First Inning Run", "Shakhtar: First Half Winner") — structural artifacts where the "away" field contains a market-segment label, not a team/player name

Sport patterns within real asymmetric:

- **Tennis surname-only failures**: Kalshi sends surname-only tickers (e.g., "Rogers" vs "Kalieva"); `sp.teams` has the full-name entry for one player ("Elvina Kalieva") but not the other. The trigram threshold can't bridge "Rogers" to "Sloane Rogers" at 0.30.
- **Baseball prop markets and minor-league failures**: NY Mets + "Colorado: Hits" / "Colorado: First Inning Run" pattern dominates the sampled Baseball asymmetric records. Real minor-league coverage gaps exist (e.g., "Club 360" / "Glitch FC" Soccer minor league).
- **Soccer minor-league failures**: Real two-team games where one team has a coverage gap (e.g., "Club 360" matched / "Glitch FC" not matched).

#### Finding 2 — `structural_normalize_failed` is fully characterized

100% of 3,306 records are Golf with `is_personal=true`. Golf data flowing through head-to-head matcher; normalizer correctly identifies single-competitor records and reports `away_normalize_succeeded: false` because there is no away side. This is a **product gap, not a bug**. Decision needed: filter out at ingestion, or build single-competitor resolver path. Not immediate priority.

#### Finding 3 — `deferred_to_2d` is NOT a bug (correction from initial framing)

Initial framing during session was wrong. Investigation revealed: the three tiers (strict, alias, fuzzy) all run on every record in the same orchestrated pass with the same `run_id`. Three rows get written, ~6 microseconds apart. The `DISTINCT ON (provider_record_id) ORDER BY decided_at DESC` query was picking the alias-tier `deferred_to_2d` row as the "latest" decision because alias's timestamp is the latest of the three (the cron's alias-then-fuzzy execution order produces alias-tier rows with later microsecond timestamps than the fuzzy-tier row written immediately before, even though fuzzy's decision is the operationally meaningful one).

In reality, the fuzzy tier IS processing these records — they're getting routed to `review_queue` or another no_match category. The 1,789 records appearing as "ending at deferred_to_2d" are actually getting their final decision from the fuzzy tier; the alias row is just the chronologically-last log entry.

**Verification**: sampled 49 of these records' fuzzy-tier decisions — all 49 with fuzzy `review_queue` decisions are present in `sp.review_queue`. System routing is working correctly.

No `deferred_to_2d` bug exists. Worth noting in the logging layer's display semantics — the `deferred_to_2d` label is confusing if you're querying "latest decision per record" — but the system itself is functioning correctly. The Priority A item that the session initially identified ("fix `deferred_to_2d` routing") was disqualified by this investigation; the actual Priority A is the asymmetric routing intervention.

### Country-name collision pattern (pre-registered from PR #156)

Confirmed in production data. The Senegal/France record (`KXWCGAME-26JUN16FRASEN`) now routes to alias-tier `review_queue` with 5 candidate France-named teams. Same pattern likely affects other country bootstraps when matched against legacy club data. **Not a regression** — exactly the operational consequence pre-registered in PR #156's Phase 2 verification.

### Operational impact framing

System processes 22,619 records/week with these outcomes:

- ~26.7% cleanly resolved (against non-Golf denominator of 19,313)
- ~22.3% routed to operators for review (4,312 / 19,313)
- ~40% genuinely unhandled with current coverage and matcher design

The matcher is doing more than half its job. The bottleneck isn't matcher quality — it's a combination of:

1. Coverage gaps in `sp.teams` and `sp.team_aliases` for individual-athlete sports
2. Asymmetric records being correctly identified as failures but not surfaced to operators
3. Operator throughput unverified (no real operator work yet)

### Concrete next-step priorities

#### Priority A — Route real asymmetric records to review_queue

**Records affected**: 1,837 currently dropped at `no_match`, should be operator-actionable.

**Why first**: highest leverage of the immediate options. Records exist, one side resolved cleanly (so the operator has anchoring context), the other side has a parsed name available. The collision review surface shipped this week handles exactly this shape — operator clicks the unmatched player/team's name, gets fuzzy candidates, picks correct match or adds alias.

**Scope**: resolver decision logic change. When fuzzy tier completes with `home_anchor_failed XOR away_anchor_failed = true` and at least one canonical resolved, route to `review_queue` instead of `no_match`. Pre-filter out Kalshi prop-market shape (`provider_normalized` contains `:` — heuristic, refine in scope doc).

**Estimated**: 1-2 days including tests + verification.

**Direct effect**: ~1,837 records per week (~262/day) become operator-actionable. Reduces "real unhandled" volume by ~24%.

#### Priority B — Coverage work for individual-athlete sports

**Records affected**: ~5,024 both-failed records, dominated by Baseball (1,318), Tennis (1,281), Handball (790), MMA (531), Basketball (454), Boxing (265).

**Why not first**: larger scope, requires data curation per sport. Specifically:

- **Tennis**: surname-only Kalshi tickers vs full-name canonicals — needs surname-aware matching OR per-player alias generation
- **Baseball**: minor-league teams likely the gap (similar shape to legacy Soccer roster)
- **MMA/Boxing**: individual fighter coverage similar to PR #156 national-teams shape
- **Handball**: lower-priority coverage gap

**Estimated**: Multi-PR effort spread over weeks. Tennis is probably the natural first sub-target given the volume and shape.

#### Priority C — Operator-driven validation

The honest gap: 4,822 records sit in `review_queue` waiting for any operator. Until someone actually drives the queue, we don't know:

- Whether the UI is fast enough for sustained operator throughput
- Where friction points exist (which the design doc anticipated would surface during 2F.1.5)
- Whether the country-name collision pattern is fast to resolve in practice or slow

This isn't engineering work — it's product validation work. But it has to happen, and the longer we ship matcher improvements without testing the operator surface against real records, the more we're building features without feedback.

**Concrete plan**: start driving the queue concurrently with Priority A development. ~15-20 minutes/day, beginning Monday 2026-05-18.

### Out of scope this week — but worth pinning

- **Golf coverage path** (3,306 records). Either filter at ingestion or build single-competitor resolver. Product decision needed. Not urgent.
- **Kalshi prop-bet handling** (214 records, plus likely more not in the asymmetric bucket). Ingestion-layer filtering or primary-market-attachment strategy.
- **Threshold tuning for Tennis surname-only matching** (Issue #142 territory). Once Tennis coverage work has data, threshold-tuning becomes a real conversation.
- **`sport_not_classified` diagnosis** (958 records). Likely small slice but unexplored.
- **`alias_no_team_resemblance` vs `fuzzy_no_team_resemblance`** (793 records). Investigation deferred — likely same shape as fuzzy bucket.

### Carried items still active

The three day-7 prep observations from PR #156's Phase 2 verification:

1. **Dry-run gating as structural safety pattern** — confirmed valuable, name explicitly in next PROJECT_STATE entry. Phase 1.5 backfill catch on PR #156 was a real-world instance.
2. **2A.5 ↔ 2F latent-state class of bug** — instance found and fixed via Phase 1.5 backfill in PR #156.
3. **Country-name collision pattern** — materialized in production, behaving as pre-registered. Not a regression.

### Engineering observations from today's session

- The `deferred_to_2d` wrong-turn was caught only because of the "do both" instinct earlier in the session pushing through to investigate rather than declaring victory at a non-bug. Worth pinning as a class-of-observation: **latest-decision-per-record queries against multi-tier resolver logs can mask the operationally meaningful decision when tier writes are microseconds apart.** Future analysis queries should either filter by `resolver_version` or use a different aggregation strategy.
- The **Kalshi prop-bet pattern** wasn't anticipated by the design doc and didn't surface in PR #156's smoke tests because national-team records don't have prop markets. Worth filing as tech-debt issue: "Kalshi prop-bet records flow through head-to-head matcher producing structural false-failures."
- The **Golf product gap** (3,306 records / 14.6% of weekly volume) is the largest single category of records the matcher wasn't designed for. Worth a product decision before next planning cycle.

### Decision artifact

Immediate work begins on **Priority A — asymmetric record routing**. Scope doc cycle with Claude Code starts today, implementation begins as soon as scope is approved. No artificial pacing — operator has bandwidth and prioritizes this.

Operator-driven validation (Priority C) layered in concurrently — ~15-20 min/day starting Monday 2026-05-18.

---

## Session — 2026-05-12

### Phase 2F.0.1 + 2F.1 closeout — pg_trgm migration, anchor_failed surface hardening, sub-PR #4 production smoke completed (✅)

Phase 2F.1's anchor_failed surface (sub-PR #4, shipped 2026-05-11) reached
production smoke validation today. Smoke testing surfaced **four
distinct issues** in cascading sequence; all four were addressed via
focused PRs landing in correct dependency order per Issue #129's
PR-ordering convention. The day closes with anchor_failed routing
operators to actionable suggest-alias widgets for the dominant record
shape, and with all major roadmap items through 2F.1 marked complete.

This was the highest-yield smoke-test day of the 2F phase — three of
the four findings were operationally invisible until production data
ran against the surface. Worth naming the pattern explicitly:
integration tests prove the code is right; production click proves
the deploy + data + extension state is right; **both matter**.

### What landed (PRs merged, in order)

- **PR #140** — Phase 2F.0.1 `pg_trgm` extension migration. Single-line
  alembic revision `b8e1f4c2a7d3` (`CREATE EXTENSION IF NOT EXISTS
  pg_trgm`). Surfaced during PR #133 smoke test against
  France/Senegal — `pg_trgm` was **available** on the Neon server
  (visible via `pg_available_extensions`) but **never activated** in
  `sports_prod` (`installed_version IS NULL`). Two existing call sites
  depended on `similarity()`: `admin/queries.py:_build_suggested_aliases`
  (the suggest-alias widget) and `scripts/alias_add.py` (the
  "team not found in sport" error path). Both 500'd in production
  pre-#140. Verification via PR comment showing
  `pg_extension` row (`extname='pg_trgm', extversion='1.6'`) AFTER
  `alembic upgrade head` against production. Forward+downgrade+
  re-upgrade roundtrip verified locally; verification artifact comment
  is the downstream-gate per Issue #129.

- **PR #137** — Phase 2F.1 sub-PR #4.1. Suggest-alias widget's `{% else %}`
  branch in `admin/templates/anchor_failed_detail.html` split into four
  distinct state branches: `ok` (existing candidate-button list),
  `no_good_candidates` (Path B — stub `make alias-add` command with
  `--team-canonical ''` left blank), `no_parsed_names` (Path C —
  surface raw payload + reference PR #138), `unclassified` (Path A —
  sub-PR #4 original message kept as-is). State assignment lives in
  `_build_suggested_aliases`; template branches on
  `detail.suggested_aliases_state` string equality. Introduces
  `SUGGESTED_TEAMS_MIN_SIMILARITY = 0.30` and the B-aware
  parsed-name-source fallback (`reason_detail._provider_normalized`
  → `_canonical` → FL `raw_payload.HOME_NAME` / `AWAY_NAME` — Kalshi
  title NOT auto-split because format varies). Smoke-validated against
  France/Senegal (`KXWCGAME-26JUN16FRASEN`) — rendered Path C
  correctly with the "(Soccer)" sport-name disambiguation parenthetical
  added during template review.

- **PR #138** — Phase 2F.1 sub-PR #5. Resolver-side fix: lift the
  four parsed-name preservation assignments
  (`home_provider_normalized`, `away_provider_normalized`,
  `home_canonical`, `away_canonical`) above the anchor-failure
  early-return in `resolver/fuzzy_tier/matcher.py:217-221`. Mirrors
  alias-tier's already-correct pattern at
  `alias_tier/matcher.py:208-211`. Pre-#138 records in
  `sp.resolution_log` are append-only audit; they stay at Path C
  indefinitely. Post-#138 records route to Path B or `ok` per the
  PR #137 state machine. Two production smoke records cited in the
  PR body: France/Senegal (Soccer, Kalshi) AND UFC Fight Night
  (`KXUFCFIGHT-26MAY16TGTERS`, MMA — "George Tuco Tokkos" /
  "Ivan Erslan"). Two sports, two providers, same bug pattern.
  Verified post-merge via SQL probe — `has_home_pn=true` on rows
  created post-cron. Drive-by: also fixed
  `test_phase_2f0_migration::test_upgrade_then_downgrade_roundtrip`
  which silently broke when PR #140 extended the migration chain
  past 2F.0 (test assumed `downgrade -1 from head` returns to
  pre-2F.0; now returns to 2F.0 only).

### Production issues discovered AND closed during today's smoke loop

Four distinct issues, each surfaced by a different test record. None
catastrophic. All four addressed within the day.

- **Issue 1: `pg_trgm` not activated in production** — surfaced by the
  first attempted detail-view click. Closed by PR #140.

- **Issue 2: Suggest-alias widget conflated three "no candidates" causes
  into one wrong message** — France/Senegal showed
  "Matcher didn't classify a sport" when sport WAS classified
  ("Soccer"). Root cause: empty-dict truthiness fell through to a
  single `{% else %}` branch. Closed by PR #137 (four-state machine
  + Path B / Path C explicit branches).

- **Issue 3: fuzzy tier dropped parsed names on anchor-failure
  early-return** — France/Senegal and UFC Fight Night records had
  `reason_detail` without `home_provider_normalized` /
  `home_canonical` etc. Alias tier preserved them; fuzzy tier didn't.
  Closed by PR #138 (lift assignments above early-return).

- **Issue 4: asymmetric anchor failures silently omit the failed side**
  — three FL Basketball records (`fJ2dHHQj`, `bsAhY9ld`, `dSItAYDD`)
  rendered only the anchored side's candidate buttons. State machine
  routes mixed-per-side records to `ok` because at least one side has
  candidates; template skips sides with empty `candidates` lists. Filed
  as **Issue #143**; fix tracked as sub-PR #6 (sequenced after this
  journal entry).

**Key structural finding (Issue 4)**: pure Path B
(`no_good_candidates`) is structurally unreachable for asymmetric
records — any side with at least one above-threshold candidate routes
the record to `ok` state. Pure Path B requires BOTH sides to have
zero candidates, which the fuzzy tier's permissive per-side anchoring
rarely produces in current data. Issue 4's fix in sub-PR #6 makes
Path B-shape rendering visible **per-side** inside the `ok` state
without changing the four-state record-level model. This was framed
in the original PR #133 conversation as "Path B is rare in
production" — the sharper framing is "structurally unreachable for
the dominant record shape."

### Day-0 production shape (post-#138, post-cron)

```
sp.resolution_log fuzzy_no_team_resemblance rows / cron:    ~50-80
  with parsed names preserved (post-#138, last 30 min):     100%
  pre-#138 (older rows, audit log immutable):                stay
                                                             Path C
  asymmetric anchor failure (one side, not both):           ~dominant
                                                             shape
                                                             in casual
                                                             browsing
sp.team_aliases.source values in production:
  legacy_bootstrap                                           (Phase 2A.5)
  alias_tier / fuzzy_tier                                    (runner write-back)
  operator_review                                            (PR #123 approve)
  manual_anchor_failed                                       (NEW: PR #133;
                                                             sub-PR #4
                                                             primitive,
                                                             ~0 rows
                                                             today —
                                                             will grow
                                                             as operators
                                                             use the
                                                             clipboard
                                                             widget)
```

Day-7 measurement window for 2F.1 opens ~2026-05-18.

### Engineering observation: implicit boundary contracts (continued from 2D.3 entry)

Yesterday's entry named the pattern: **"implicit data contracts at
module boundaries"** — three production bugs in three days during
the 2F.1 mutation work, all surfacing in `_validate_candidate_team_id` /
session autobegin / positional indexing. Today extended the same
pattern with four more cases.

What's worth carrying forward: **today's four issues were caught
earlier than yesterday's three.** Specifically:

- **PR #140 (pg_trgm extension)**: caught in seconds, by the first
  attempted detail-view click. Production data + production extension
  state combined to expose it. No automated test could have predicted
  this; the contract was between `admin/queries.py` and Neon's
  installed-vs-available extension state.
- **PR #137 (template conditional)**: caught in the same session as
  the pg_trgm bug — France/Senegal records exercised the wrong-message
  path immediately. Contract was between empty-dict truthiness and
  template's `{% else %}` semantics.
- **PR #138 (resolver-side preservation)**: caught by the diagnosis
  trail of PR #137 — looking at WHY France/Senegal had no parsed
  names traced back to the fuzzy tier's early-return. Contract was
  between fuzzy-tier emission and alias-tier-equivalent expectations.
- **Issue #143 (asymmetric per-side rendering)**: caught during PR #137
  smoke verification rounds — three out of three browsed records hit
  the bug. Contract was between record-level state machine and
  per-side template rendering.

The "implicit boundary contracts" pattern is now formally named **and
operationally validated**: each of the four cases would have stayed
hidden behind a "looks fine in tests" state until a real production
record exercised the contract. The bias was that pre-production
integration tests **only seeded data they knew about** — France/Senegal
was a real-world record with shapes the tests didn't anticipate.

**Mitigation worth committing to from today forward**:

1. Sub-PR-#4-style smoke testing isn't optional. Every operator-facing
   surface needs at least one round of production-data browsing
   before "ready to declare shipped." Integration tests prove the
   code; production click proves the deploy + data + state.
2. Schema-gotcha-style discoveries (sp.resolver_runs.id BIGINT vs
   run_id UUID, caught early during PR #133 work) suggest a
   prospective audit: every JOIN crossing the BIGINT/UUID schema
   line should have a typed comment at the SQL site explaining
   which column is which. Filed informally — would be a 2F.X /
   2G concern if it becomes a pattern.
3. Issue #129's downstream-gating convention worked cleanly today:
   PR #140 → #137 → #138, each gated on production verification of
   the upstream. Branch protection blocked one inadvertent direct-
   push attempt, demonstrating "value-of-branch-protection working
   as intended."

### Tracked deviations from PHASE_2F_DESIGN rev1.2

- Sub-PR #4.1 (PR #137) was not in the original 2F.1 design — emerged
  from production smoke test as a four-state refinement of the original
  two-state widget. Design doc could be bumped to rev1.3 to capture the
  state machine, but the cost-benefit (more doc churn vs. PR #137's
  inline state-machine documentation) doesn't justify it for a
  shipped-and-validated state. Leave rev1.2 as the lock; PR #137's
  inline comments are the source of truth for the state model.
- Sub-PR #5 (PR #138) is on the rev1.2 fallback path under §Q6
  ("anchor_failed surface in 2F.1 OR hard-sequenced 2F.2"). It
  shipped in 2F.1 timeline, not 2F.2 — better than the design's
  fallback. No deviation in spirit.
- Sub-PR #6 (Issue #143 fix, in flight) was unanticipated by rev1.2.
  Not on the design doc; emerged from production smoke. Same
  rev1.3-vs-leave-alone tradeoff as #137 — leaving rev1.2 as-is.

### Open follow-ups (issues filed today, ordered by intended action)

**Actionable now (small admin polish, no day-7 dependency):**
- **#131** — Approve route empty body on `ApprovalError` (HX-Request branch + `_error.html` partial). ~30-40 LOC. Real UX bug.
- **#132** — `hx-disabled-elt` selector typo (one-line fix). ~5 LOC.
- **#134** — Per-record recurrence count column on anchor_failed list view. ~20 LOC. Operationally useful for day-7 triage.
- **#143** — Asymmetric anchor failure silent omission (sub-PR #6 in flight). ~40-60 LOC. Closes the dominant-shape gap before day-7 measurement window opens.

**Tech-debt + convention (low priority, ship-when-convenient):**
- **#141** — Pin `SUGGESTED_ALIASES_STATE_*` constant values (template literal-string drift guard). ~15 LOC.
- **#144** — Add `test_phase_2f0_1_pg_trgm_migration::test_upgrade_then_downgrade_roundtrip`. ~80 LOC.
- **#145** — Migration test scoping convention (PR template checkbox + optional static guard). Establishes the prospective convention #144 instantiates.

**Deferred (gated on day-7 or external triggers):**
- **#135** — UI affordance to create canonical `sp.teams` from anchor_failed surface. Deferred indefinitely pending day-7 data.
- **#136** — Bootstrap national-team rows into `sp.teams`. Defer to 2D.5.X OR sooner if World Cup / Euros / AFCON within 8 weeks.
- **#142** — `SUGGESTED_TEAMS_MIN_SIMILARITY` length-aware tuning. Passive marker — DO NOT ACT until day-7 data shows real operator-reported missing matches.

**Passive marker (3-year-out concern):**
- **#139** — Investigate Python-side `rapidfuzz` alternative if Neon ever drops `pg_trgm`.

### Notes for the next session's first 5 minutes

- **Sub-PR #6 is the next scheduled work** (Issue #143). Branch
  `claude/phase-2f1-sub-PR-6-asymmetric-anchor-failure` is already
  checked out at the start of this entry's commit; implementation
  follows immediately.
- **Day-7 measurement window opens ~2026-05-18.** Same shape as
  2B/2C/2D.4 day-7 reviews: query `sp.resolver_runs` for the week,
  measure operator throughput, decide on 2F.X prioritization. The
  asymmetric-anchor-failure fix (sub-PR #6) lands before the window
  opens so day-7 data isn't biased by the silent-omission bug.
- **No actively-scoped 2F.2 / 2F.3 work** (per `PHASE_2F_DESIGN.md:364`
  — "Quality-of-life improvements, optional, gated on day-7"). The
  next time-bound item after sub-PR #6 is the day-7 measurement
  itself.
- **Phase 3 doesn't have a design doc yet.** 2D.5 has a draft design
  in PR #111 but is paused per the 2026-05-10 spot-check. If day-7
  data triggers 2D.5 resumption, that's the natural next-phase
  decision point.
- `PROJECT_STATE.md` is now current through 2F.1 closeout. The next
  entry should pick up at sub-PR #6 ship + day-7 review, whichever
  lands first.

---

## Session — 2026-05-10 / 2026-05-11

### Phase 2F.0 + 2F.0.5 + 2F.1 — operator review-queue UI shipped (✅ minus anchor_failed)

The operator review-queue UI is live in production. Two operators can log
in, page through pending review_queue rows, inspect the matcher's
reasoning, and approve or reject — with `sp.team_aliases` getting a real
`source='operator_review'` write-back on approve. Every item under §"2F.1
— Minimal review UI" of `PHASE_2F_DESIGN.md` rev1.1 is shipped except the
anchor_failed surface (sub-PR #4), which is the next planned work item
and is genuinely greenfield (no draft, no partial implementation).

This was a ~36-hour stretch broken into three logical phases that landed
back-to-back: the schema migration (2F.0), the runner write-side update
to populate the new columns going forward (2F.0.5), and the UI itself
(2F.1) shipped as four sub-PRs of which three are merged. Phase 2F.1
also generated three unplanned production incidents — none catastrophic,
all the same class of bug — which warrant their own subsection below.

### What landed (PRs merged, in order)

- **PR #112** — `PHASE_2F_DESIGN.md` rev1.1 (doc-only). Locked the
  design with Q1–Q8 resolved: server-side rendering with HTMX progressive
  enhancement; cookie-signed bcrypt auth via env vars (two seats, no
  user table); list+detail+approve+reject in 2F.1; anchor_failed
  surface in 2F.1 OR hard-sequenced 2F.2 (Q6 revised); "(collision)"
  cosmetic for `confidence=0` (Q8). The design predates implementation
  to keep scope-creep audits cheap.

- **PR #114** — Phase 2F.0 schema migration
  (`20260510_1800_a1c4f9e8b2d7_phase_2f0_review_queue_columns.py`).
  Three columns added to `sp.review_queue`: `reason_detail` JSONB
  (snapshot of `MatchResult.reason_detail` at insertion — denormalized
  so the UI reads a single table per page; staleness is acceptable
  because the matcher decision was correct at insert and that's what
  the operator is reviewing), `provider_title` TEXT (snapshot of
  Kalshi's `raw_payload->>'title'` or FL's synthesized `"home vs
  away"`; saves per-record JSONB parsing on every page load), and
  `rejection_count` INTEGER NOT NULL DEFAULT 0 (guardrail against
  operator burnout cycles — 2F.1 surfaces it in the list view; 2F.X
  adds the unreject button + runner-side skip logic). Plus a partial
  index `ix_review_queue_pending_confidence` on `(status, confidence
  DESC, created_at) WHERE status='pending'` to cover the list view's
  default query without sort-at-query-time. Latency budget: <500 ms
  p95. Existing 2,263 pending rows backfilled with NULL for
  `reason_detail`/`provider_title` (UI falls back to provider-table
  JOIN) and 0 for `rejection_count` (server-side default).

- **PR #115** — Phase 2F.0.5 runner write-side.
  `scripts/run_resolver_pass.py` now populates `reason_detail` and
  `provider_title` on every `INSERT INTO sp.review_queue ... ON
  CONFLICT DO UPDATE`. Kalshi branch reads `raw_payload['title']`;
  FL branch synthesizes from `HOME_NAME` + `AWAY_NAME`. Without
  this, new 2F.0-shape rows would have NULL on the new columns and
  force the UI's fallback path on 100% of records — the migration
  is online but useless until the runner backfills it forward.
  Shipped same day as 2F.0 to keep that gap to a single overnight
  cron cycle.

- **PR #117** — Migration-bearing PR checklist + PR template. Added
  `DEPLOYMENT.md → Migration-bearing PR checklist` (Railway does NOT
  auto-run alembic on deploy; migrations are manual) and
  `.github/PULL_REQUEST_TEMPLATE.md` with explicit checkboxes for
  forward+downgrade roundtrip, `alembic current` verification, and
  operator action-after-merge. Process change, not feature work —
  but motivated by 2F.0 being the first migration since 2C and a
  clean template existed only in DEPLOYMENT.md, not at PR-creation
  time.

- **PR #118** — Phase 2F.1 sub-PR #1: admin auth scaffolding.
  bcrypt password hashing, itsdangerous signed cookies, two
  `ADMIN_USER_*` / `ADMIN_PASS_HASH_*` env-var seats,
  `require_operator` FastAPI dependency. `GET /admin/` returns 503
  (not 500) when env vars unset — explicit "not configured" instead
  of leaking a ConfigurationError stacktrace.

- **PR #119** — Phase 2F.1 sub-PR #2: read-only list + detail views.
  10-column list view (Provider, Ticker, Title, Sport, Kickoff,
  Confidence, Tier, Candidates, Status, Created) with
  provider/tier/confidence_min filters and offset pagination.
  Detail view with the design's three panels (raw payload, parsed
  fields, matcher decision). `_format_confidence` renders the
  "(collision)" cosmetic for `confidence=0` per Q8. Vendored
  htmx-1.9.10 from raw.githubusercontent.com (47,755 bytes) to
  avoid CDN dependency. The list view shipped with 10 columns
  rather than the design's 9 — Status was added at operator
  request during PR review; documented deviation.

- **PR #122** — CSS extraction (closes issue #120). Consolidated
  inline `<style>` blocks from templates into
  `admin/static/admin.css`. Pure refactor, no behavior change.
  Filed pre-mutations to keep the approve/reject diff focused.

- **PR #123** — Phase 2F.1 sub-PR #3: approve / reject mutations.
  `_validate_candidate_team_id` enforces server-side that the
  operator-selected team_id is in the candidate set (Python
  pre-flight + SQL `WHERE status='pending'` + rowcount==0 raise —
  three-layer idempotency). Approve writes to `sp.team_aliases`
  with `source='operator_review'` and resolves the fixture; reject
  increments `rejection_count`. Two commits piggy-backed on the
  main diff:
    - **Commit A** — `confidence_min` empty-string filter returns
      422. FastAPI's `float | None` binder treats `""` as a parse
      error; fixed by binding as `str | None` and parsing
      defensively. Filter-UX bug surfaced during local smoke; not
      on the design.
    - **Commit B** — `next_record_id` referenced in the decision
      template but never computed. Added
      `find_next_pending_record_id()` helper and threaded it
      through approve/reject/detail handlers so the "Next record"
      link actually works. Filter-context limitation (next pending
      ignores current filter set) deferred to 2F.X.

### Production issues encountered during 2F.1 rollout

Three production bugs in three days, all surfacing in PR #123's
approve path. None catastrophic — each was operator-visible (500 on
click, recoverable by reload) and patched within the hour. But they
all sit in the same class: **implicit data contracts at module
boundaries**. Worth naming the pattern so the next phase catches it
earlier.

- **PR #125 — `approve_record` session autobegin conflict.** First
  approve-click in production returned 500. SQLAlchemy 2.0
  autobegin: the pre-flight reads in `approve_record` opened an
  implicit transaction; the explicit `async with session.begin():`
  block then raised `InvalidRequestError: A transaction is already
  begun on this Session`. The runner side (PR #108) had set the
  right pattern — reads inside the same `begin()` block — but
  `approve_record` was written without referencing that convention.
  Fix: move all reads inside `session.begin()`. Local repro via
  `apt install postgresql` + `pg_ctlcluster 16 main start` for
  integration tests; that local harness is the new standard for
  any DB-transaction PR going forward. Added
  `test_approve_does_not_hit_session_autobegin_conflict` as a
  regression guard. While fixing, also caught the HTMX fragment
  template referencing `detail.*` after the mutation but the
  handler not re-loading `detail` post-commit — fixed in the same
  PR.

- **PR #126 / #127 / #128 — Minnesota vs Cleveland partial-collision
  500.** Bradley vs Campbell approved cleanly (alias-tier,
  non-collision shape). Minnesota vs Cleveland returned 500 on
  every approval attempt. `candidate_fixtures` JSONB column stores
  team_ids in a layout that depends on the matcher branch that
  wrote it: non-collision → `[home_id, away_id]`; full-collision →
  `[home_cands..., away_cands...]`; **partial-collision (one side
  colliding, one not) → `[colliding_side_cands..., empty]`** — so
  positional `[1]` picked the second HOME candidate instead of the
  away default. `_validate_candidate_team_id` correctly rejected
  it as out-of-set; the 500 was the validator working as designed
  against an upstream bug.

  Diagnostic cycle ran across three PRs over ~90 minutes:
    - **PR #126** — instrumented `approve_record` raises with the
      offending team_ids and the validator's expected set. Shipped
      with explicit "REVERT AFTER USE" in the commit message and
      title. Instrumentation-first because the bug was data-shape
      dependent and not reproducible from the operator report
      alone; needed the actual team_id values from the failing
      row to confirm the hypothesis.
    - **PR #127** — DIAG output identified the positional-indexing
      mismatch within minutes. Fix sourced defaults from
      `reason_detail.{home,away}_team_id` (name-keyed, correct for
      all collision shapes) instead of positional `raw_cf[0]/[1]`.
      Removed the now-unused `_load_candidate_team_ids` helper.
      Added `test_approve_partial_collision_validates_away_against_correct_default`
      as a regression guard.
    - **PR #128** — revert PR #126's instrumentation.

  The positional-indexing bug was wrong from inception: PR #123
  read `candidate_fixtures` under the non-collision contract
  assumption, but the column ships *three* contracts depending on
  the writer branch, with no schema-level distinction. The
  Bradley row tested cleanly because it was non-collision shape;
  partial-collision rows are a non-trivial fraction of pending
  volume but didn't get exercised until Minnesota hit production.
  Filed as issue #121 (expanded scope: naming AND typing).

  **PR ordering incident (subset of the above).** The revert PR
  (#128) merged at 20:43 while the fix PR (#127) was still in
  review; production stayed broken between 20:43 and 20:54.
  Postmortem: revert PRs (and ANY sequencing-dependent PR pair)
  need (a) blocking language in the title, (b) a concrete
  production-deliverable verification step in the PR body (DB row,
  response payload — not just HTTP status), and (c) branch
  protection enforcement so the rule doesn't depend on humans
  being available. Filed as issue #129. This matters as a class of
  bug, not a single mistake: the same failure mode applies to
  migration+migration-dependent PR pairs, refactor PRs that split
  a function before the call-site update lands, and any case
  where merging in the wrong order leaves production in an
  inconsistent state for the gap between merges.

### Day-0 production shape

Phase 2F is a UI shipment, not a runner change, so there's no clean
"day-0 cron run" the way 2D.3 had. The closest comparable
measurement is the steady-state shape of the queue the UI now
reads. From the most recent `sp.resolver_runs` row at time of
writing:

```
sp.review_queue depth:       ~2,270 pending (within 2C.1's
                             1,500-row alert headroom because
                             2C.1's threshold applies to
                             alias-tier specifically; combined
                             2C+2D+legacy depth is the relevant
                             ceiling)
new rows / cron:             ~310 (combined alias + fuzzy
                             review_queue from the last
                             2D.3-shape resolver_runs row)
reason_detail populated:     100% of post-PR-#115 inserts;
                             ~0% of pre-2F.0 inserts (fallback
                             path active for the existing 2,263
                             rows)
provider_title populated:    same as reason_detail
operator approves / cron:    N/A — operator-driven, not
                             cron-driven; ramp will be measured
                             at day-7
```

Two production approve cycles validated against the four-table
atomic-transaction shape (write to `sp.review_queue` setting
`status='decided'` + `decision_team_id`; write to `sp.team_aliases`
with `source='operator_review'`; write to `sp.fixtures` resolving
`provider_record_id` → `fixture_id`; write to `sp.resolver_runs`
extra-counter increment):

- **Bradley vs Campbell** — alias-tier, non-collision shape.
  Approved cleanly post-PR #125. Verified all four table writes
  committed atomically; idempotent on retry; HTMX fragment swap
  rendered the decided-state panel without a full page reload.

- **Minnesota vs Cleveland** — collision-tier, partial-collision
  shape (home colliding, away non-colliding). First attempt
  500'd pre-PR #127; approved cleanly post-PR #127 + post-PR
  #128. Same four-table verification.

Sub-PR #4 (anchor_failed read-only surface) is the next planned
work item. Anchor_failed records account for ~170/cron of FL
long-tail volume and currently have no operator surface — they
sit in `sp.resolution_log` rows with `reason_code='no_match'`
AND `reason_detail->>'fail_reason'` in the anchor-failed family
(`alias_no_team_resemblance`, `fuzzy_no_team_resemblance`,
`alias_no_existing_fixture`, `fuzzy_no_existing_fixture`). They
never reach `sp.review_queue`. The 2F.1 design (Q6 revised) put
the read-only listing in 2F.1 or hard-sequenced 2F.2 depending
on scope; with 2F.1 shipping clean on review_queue,
anchor_failed is the natural next sub-PR.

> **Erratum (2026-05-12 / sub-PR #4 scoping pass):** Earlier
> drafts of this paragraph said the records "sit in
> `sp.resolver_runs.extra.anchor_failed_records` JSON." That was
> wrong — `sp.resolver_runs.extra` carries per-run counters, not
> per-record forensic data. Per-record `no_match` decisions land
> in `sp.resolution_log` (one row per tier consulted, per
> `scripts/run_resolver_pass.py:430-440`). The mistake came from
> conflating the run-level counter `extra.fuzzy_review_queue` /
> `extra.fuzzy_auto_applies` shape with per-record audit
> storage. Sub-PR #4's query targets `sp.resolution_log`.

### Tracked deviations from PHASE_2F_DESIGN.md rev1.1

- **List view ships 10 columns, not 9.** Added Status column at
  operator request during PR #119 review; lets operators see
  "decided" rows when filters are off without clicking through.
  Documented deviation, not a bug.
- **CSS extraction (PR #122) wasn't on the design doc.** Closed
  issue #120 (inline styles). Pure refactor; consistent with the
  design's spirit.
- **Commit A on PR #123** (`confidence_min` empty-string fix) —
  filter-UX bug, not a design item.
- **Commit B on PR #123** (`next_record_id` thread-through) — the
  design referenced the "Next record" link but the implementation
  was template-only; needed handler-side wiring.
- **Three production hotfixes** (#125 / #127, with #126/#128 as
  the diagnostic cycle) — see the "Production issues" subsection.

### Open tech-debt issues (`tech-debt` label, see GitHub)

- **Issue #105** — `test_unpaired_kalshi_only_fixture_appears`
  date-dependent; still deselected from CI. Fix: `freezegun` /
  `time-machine`. Carried from 2D.3.
- **Issue #109** — per-record `session.begin()` adds ~83ms/record.
  Still comfortably below cron-stagger budget; carried from 2D.3.
- **Issue #120** — closed by PR #122 (CSS extraction).
- **Issue #121** — `sp.review_queue.candidate_fixtures` misleading
  name AND implicit shape contract. Scope expanded post-#127 to
  cover both the naming and the per-side layout problems (same
  root cause). Recommended fix: rename + reshape to
  `candidate_team_ids_by_side` JSONB object with explicit
  `{home: [...], away: [...]}` shape, scheduled after sub-PR #4
  lands.
- **Issue #124** — `test_approve_double_click_is_idempotent`
  skipped due to TestClient+asyncpg event-loop conflict ("Future
  attached to a different loop" on sequential POSTs in one test).
  Idempotency still covered by
  `test_approve_concurrent_decision_returns_already_decided`.
  Migration to `httpx.AsyncClient` tracked.
- **Issue #129** — branch-protection-enforced verification gate
  for sequencing-dependent PR pairs. Generalized from the
  #127/#128 ordering incident: applies to ANY PR pair where
  merge order matters (revert+fix, migration+migration-dependent,
  refactor that splits a function before the call-site update
  lands, etc.). Tooling task: GitHub branch-protection required
  status check that parses PR body for a "blocks: #N" header and
  refuses merge if the blocker isn't both merged AND has a
  verification artifact (DB query result, response payload
  screenshot) posted as a comment.

### Engineering observation: implicit data contracts at module boundaries

Three production bugs in three days — #125, #127, and the Commit
A/B fixes on #123 — share a class. Each was a contract between two
modules that wasn't enforced by types, schemas, or tests, only by
convention documented elsewhere (or not at all):

- **#125** (session autobegin) — the contract "reads must be
  inside `session.begin()`" exists in PR #108's commit message and
  in SQLAlchemy 2.0 docs. Not enforced by SQLAlchemy at write
  time; the error surfaces at the second `begin()` call far from
  the cause. The runner had the right pattern; `approve_record`
  reinvented the wrong one because the convention wasn't in code.

- **#127** (positional indexing on partial-collision) — the
  contract "`candidate_fixtures[0]` is home, `[1]` is away" holds
  for non-collision rows but not for partial-collision. Two
  different layouts in the same column. No schema annotation; no
  test that exercised partial-collision shape pre-production. The
  bug was wrong from inception — PR #123's reviewer (Claude) and
  the human reviewer both signed off because the documented
  contract in the inline comment matched the non-collision case,
  which was the only case the reviewer's mental model held.

- **Commit A on #123** (`confidence_min` empty-string) —
  FastAPI's query-binder contract for `float | None` doesn't say
  how `""` is treated; reasonable people would expect either
  "treat as None" or "422". The actual answer is 422, surfaced
  only when the form submitted with an empty filter.

Pattern name: **implicit boundary contracts**. Mitigation worth
testing in 2F.X / 2G:

1. When module A's output feeds module B's positional/keyed read,
   require either (a) a typed dataclass at the boundary, or (b) an
   integration test that exercises every emission shape A
   produces.
2. Code review checklist item: "If this PR reads a JSONB/dict
   from the DB, what shapes does the writer emit, and does at
   least one test exercise each shape?"
3. `_load_candidate_team_ids` removal in PR #127 is the right
   instinct — when a helper exists only to paper over an implicit
   contract, the contract itself is the bug.

The 2F.1 incidents were all <1-hour fixes because the validators
caught the bad state — `_validate_candidate_team_id` rejecting the
wrong team_id is exactly the safety net you want. But the *cost*
of finding each one in production is real: PR turnaround, operator
trust, debugger time. Catching them at boundaries before they ship
is the leverage.

### Notes for the next session's first 5 minutes

- Sub-PR #4 (anchor_failed read-only surface) is the next planned
  work. Greenfield; no draft branch; references in
  `admin/__init__.py` and `admin/router.py` are forward-looking
  comments only.
- Sub-PR #4 reads from `sp.resolution_log` (see erratum above
  if reading the body of this entry). No schema change needed
  — the `ix_resolution_log_provider_record` and
  `ix_resolution_log_run` indexes cover the query shape. Plan
  is `DISTINCT ON (provider, provider_record_id)` over the
  `LIMIT 7` most recent `resolver_runs` rows, filtered to the
  four-element anchor-failed fail_reason family. PHASE_2F_DESIGN
  rev1.1's fail_reason enumeration is wrong (lists
  `anchor_score_below_floor` which doesn't exist in the code,
  and `deferred_to_2d` which is non-terminal) — sub-PR #4 bumps
  the design doc to rev1.2 with the corrected list.
- Day-7 measurement window for 2F.1 lands ~2026-05-18. Same shape
  as 2B/2C/2D.4: query `sp.resolver_runs` for the week, compare
  review-queue depth to operator throughput, decide on 2F.X
  prioritization (unreject button, rejection_count skip logic,
  anchor_failed if not already shipped).
- Issue #129 (branch-protection enforcement) is tooling, not
  feature — schedule alongside any other GitHub-Actions work.
- `PROJECT_STATE.md` is now current through 2F.1; the next entry
  should pick up at sub-PR #4 or the day-7 review, whichever
  lands first.

---

## Session — 2026-05-09

### Phase 2D.3 shipped + 2D.3.1 hotfix verified in production (✅)

The 14-day post-2D.3 parallel-run window is now LIVE with all three
tiers consulting per record (strict → alias → fuzzy → review/no_match).
Orchestrator version stamp: `tiered@2d.0`. Per-tier resolver versions:
`strict@2a.6`, `alias@2c.0`, `fuzzy@2d.0`.

### What landed (PRs merged, in order)

- **PR #102** — Phase 2D.2.6 tennis-specific prop suffix extension
  (`Total Games`, `Set Winner`, `Match Winner`, `Tiebreak`). Cleaned
  up the `anchor_failed` bucket before measuring 2D.3's behavior.
- **PR #103** — Phase 2D.2.7 corroboration-gap investigation runbook
  (`scripts/investigate_corroboration_gap.sql`). Operator runs Q1+Q2+Q3
  to attribute the 1.5% measured corroboration to one of three paths
  (tournament gap / kickoff misalignment / genuinely 1.5%).
- **Investigation outcome** — Path B (kickoff misalignment) confirmed:
  Q1 100% tournament overlap, Q2 median/max 30 (pile-up at filter
  edge), Q3 85%→100% lift at ±60min (+15pp).
- **PR #104** — Phase 2D.2.8 per-tier drift widening
  (`KICKOFF_DRIFT_SEC = 60 * 60` for fuzzy tier; strict + alias keep
  30 min). Static guard asserts fuzzy_tier drift > strict tier drift.
  Dry-run re-run measured corroboration 1.5% → 2.7% (+1.2pp).
- **PR #106** — PHASE_2D_DESIGN.md rev3 (doc-only). Locked Option C1
  as primary 2D framing (review queue is the headline, ~150/cron;
  auto_apply ~2-3/cron is bonus). Day-0 prediction final: ~10-11%
  combined Kalshi auto-apply. Deferred A.rev2 to 2D.7 follow-up.
- **PR #107** — Phase 2D.3 TieredMatcher 3-tier extension. Pure
  infrastructure wiring of the already-shipped 2D.2 matcher into the
  runner. Bumped `TIERED_RESOLVER_VERSION` to `tiered@2d.0`. Added
  `fuzzy_auto_applies` and `fuzzy_review_queue` counters in
  `sp.resolver_runs.extra`. `sp.team_aliases` write-back uses
  `source='fuzzy_tier'`. Triple-tier resolution_log per design D.4.
- **PR #108** — Phase 2D.3.1 hotfix. Two changes scoped tightly to the
  619-crash regression that surfaced after 2D.3 went live:
  1. **`ON CONFLICT (provider, provider_record_id) DO UPDATE WHERE
     status='pending'`** on the `sp.review_queue` insert. The same
     unresolved record (fixture_id IS NULL because review_queue is
     pending operator approval) comes back to the resolver on every
     cron pass; without ON CONFLICT, the second pass crashes on the
     uniqueness constraint. WHERE clause protects operator-decided
     rows from being overwritten.
  2. **Per-record `async with session.begin():`** instead of
     chunk-level. Prior chunk-level transaction caused IntegrityError
     on one record to cascade `PendingRollbackError` to every
     subsequent record in the chunk. Each record now commits or rolls
     back independently.

### Day-0 production numbers (run 545a0379-a9e9-4742-8304-ce741a9444fc)

```
records_scanned:      4,754
auto_applies (total):    24
  strict tier:           19
  alias  tier:            2
  fuzzy  tier:            3
review_queue (total): 1,011
  alias  tier:          741
  fuzzy  tier:          270
no_match:             1,623
crashes:                  0
runtime:           6m 36s
```

Phase 2D.3 fully operational. Three-tier matcher producing expected
output shape. Tonight's scheduled crons (FL 02:00 UTC / Kalshi 02:15
UTC) execute cleanly per the verified hotfix.

### Tracked follow-ups (post-2D.4)

- **2D.4 day-7 review** — same cadence as 2B and 2C. Decision point
  for 2D.5 / 2D.6 / 2D.7 prioritization. Includes re-running the §E.8
  corroboration investigation against persisted 2D fuzzy-tier data
  (replaces the team-sport proxy with tennis-specific numbers).
- **2D.5 / §E.9** — FL alias coverage expansion for the ~170
  anchor_failed records/cron long-tail. Sample 200 anchor_failed
  records, classify by failure mode (FL missing vs alias gap), expand
  `DEFAULT_FL_SPORT_IDS` and/or seed `sp.team_aliases` for top-N
  tennis players.
- **2D.6 / §E.10** — Asian-name single/two-character surname handling
  ("Hu", "Ng", "Li", "Choo"). Country-of-origin disambiguation layer
  for surnames ≤ 2 chars where ≥ 3 candidates collide. Conservative
  scope.
- **2D.7 / §E.11** — A.rev2 per-candidate initial-expansion filter in
  `_find_personal_match`. Discriminates "Junfeng Hu" from "Zhizhen Hu"
  for multi-token providers. Deferred from 2D.3 because dry-run
  showed the fuzzy auto_apply path is small leverage; current
  cross-team collision detection routes these to review_queue
  (operators handle the discrimination).

### Open tech-debt issues (`tech-debt` label, see GitHub)

- **Issue #105** — `test_unpaired_kalshi_only_fixture_appears` is
  date-dependent; deselected from CI runs as a workaround. Fix:
  freeze the test clock with `freezegun` or `time-machine`.
- **Issue #109** — Resolver runtime: per-record `session.begin()`
  adds ~83ms/record (~6m 36s for 4.7k records). Not urgent — sits
  comfortably below the 15-min cron stagger window. Investigate if
  any cron run exceeds 10 min, `records_scanned` exceeds 8k for 2+
  consecutive cycles, or 2D.5 ships and adds more records.

### Notes for the next session's first 5 minutes

- Wait for 5-10 cron cycles to land before any 2D.4 day-7 review
  work. Steady-state numbers matter, not a single-sample read.
- The `tech-debt` label needs creating in the GitHub UI; once
  created, apply to issues #105 and #109.
- 2C.1 alert threshold of 1,500 review-queue rows still has headroom
  with combined 2C+2D volume (~400-500/day). If review queue depth
  grows past 1,500 sustained for >7 days, escalate per 2C.1 mechanism.
- Operator capacity: ~67-83 min/day of review work at ~10 sec/record
  for the combined 2C+2D queue. If the actual review pace lags,
  revisit 2D.7 (A.rev2) prioritization to shave the auto-apply path.

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

**Two audit refinements added pre-PR (per review feedback):**

1. **Kalshi linked-to-NULL backfill flag.** When `find_fixture`'s
   equal-or-NULL filter links a Kalshi explicit-comp signal to a
   NULL-comp fixture, `reason_detail` now records
   `linked_to_null_comp_fixture: true` AND
   `null_comp_fixture_pending_backfill: <fixture-uuid>`. Phase 2C's
   backfill query is now a one-liner against `resolution_log`
   instead of a manual SQL audit.
2. **FL transitional sub-paths.** Every successful FL match also
   stamps `fl_transitional_path` with one of three values:
   `matched_null_comp_fixture` (typical), `matched_existing_comp_fixture`
   (Kalshi created it earlier with explicit comp — Phase 2C must
   verify FL's resolved comp aligns), or `created_null_comp_fixture`
   (FL was first; new row, awaits 2C to set the column). Required
   `find_fixture` return shape change: now returns
   `(fixture_id, fixture_competition_id)` so the matcher can audit
   the equal-or-NULL filter outcome.

### Phase 2A.7 — sp.fl_events.sport_id (this session, follow-up to 2B/2A.6)

First production FL pass produced **0/19,753 auto-applies**. Diagnosed:
`sp.fl_events` had no sport context preserved (no column, and
`raw_payload.SPORT_ID` was always null), so the runner couldn't pass
`sport=...` into `FLResolverModule.extract_signal`. Every FL signal
hit the matcher's gate 2 (`sport_not_classified`).

Earlier (PR #85) review claimed FL was "sport-shaped by construction"
because `ingestion/fl.py` polls per-sport — that conflated "polled
per-sport" with "preserved sport in storage." The poller had the
sport_id in scope at write time but never wrote it.

**Migration `7c3f9b1a2e58`:**
- Adds `sp.fl_events.sport_id INTEGER REFERENCES sp.sports(id)` (nullable).
- Partial index `ix_fl_events_sport_unresolved` on
  `(sport_id, last_seen_at DESC) WHERE fixture_id IS NULL` —
  supports the runner's hot query without bloating the full table.

**Ingestion (`ingestion/fl.py`):**
- New `FL_SPORT_ID_TO_SP_NAME` map (single source of truth, 17
  entries matching `DEFAULT_FL_SPORT_IDS`). Decoupled from
  `main.py._KALSHI_SPORT_BY_FL_ID` which is older + has several
  conflicting IDs.
- One bulk SELECT at pass start resolves `FL sport_id → sp.sports.id`.
- Per-sport batch now passes `{"sport_id": sp_sport_id}` in fields,
  so UPSERT populates the column on insert AND backfills it on
  conflict.
- Unmapped FL sport_ids are skipped with `ingestion.fl.sport_id_unmapped`
  warning rather than NULL-out the column on existing rows during
  conflict resolution.

**Runner (`scripts/run_resolver_pass.py`):**
- FL SQL now `INNER JOIN sp.sports` and filters `sport_id IS NOT NULL`.
- `extract_signal` is called with `sport=row.sport_name` for FL.
- Kalshi path unchanged — `_sport` lives on `raw_payload` already.

**Backfill:**
- `scripts/backfill_sp_fl_events_sport_id.py` wraps the existing
  `backfill_fl.py` and reports pre/post NULL counts + per-sport
  coverage. Re-fetching the FL ±7 day window backfills sport_id on
  every currently-fetchable row.
- Rows outside the ±7 day window stay NULL until a future
  per-tournament historical fetch lands. Documented; not in scope.

**Tests (+7 new):**
- `TestFLSportIdMap` (2): every DEFAULT_FL_SPORT_ID is mapped; every
  mapped name aligns with the canonical sp.sports seed.
- `TestIngestionWritesSportId` (3): static guards that batch fields
  include `sport_id`, pre-pass bulk-loads sp.sports, and unmapped
  ids are skipped with warning.
- `test_fl_query_joins_sports_and_filters_sport_id` (1): static
  guard on the runner SQL JOIN + filter + extract_signal call shape.
- 1 existing FL-test class kept (TestFLEventValidator etc.).

**Operator action sequence (2A.7 → re-run smoke):**

```bash
git checkout main && git pull
DATABASE_URL=<prod-Neon> alembic upgrade head            # apply 7c3f9b1a2e58

# Backfill existing rows (re-fetch FL ±7 days; populates sport_id).
DATABASE_URL=<prod-Neon> python scripts/backfill_sp_fl_events_sport_id.py

# Re-run smoke. Expected: real auto-applies + meaningful no_match
# fail_reason distribution. Compare against the 0/19,753 baseline.
DATABASE_URL=<prod-Neon> python scripts/run_resolver_pass.py \
    --provider fl --limit 100
```

### Production incident — Phase 2A.7 NameError in `_ingest_pass`

**Root cause:** The 2A.7 PR (#86) built `sp_sport_id_by_fl_id` inside
`run()` and referenced it from `_ingest_pass` as a free variable.
Python doesn't propagate locals across function boundaries, so every
call from `_today_pre_game_loop` / `_week_loop` raised NameError.
Bonus bug: `run()` itself called `session.execute(...)` before the
`async with session_factory() as lock_session:` block opened —
NameError on `session` would have crashed `run()` first.

**Production effect:** From PR #86 deploy until the hotfix lands,
**every FL ingestion poll silently failed.** Production
`sp.fl_events.sport_id` stayed 100% NULL across all 19,759 rows
despite `last_seen_at` updates (a separate code path keeps writing
that). The supervisor caught the NameError and restarted `run()` in a
tight loop — Railway logs should show one warning per supervisor
restart cycle.

**Why static guards missed it:** The 2A.7 PR's tests asserted that
specific strings appeared in the source ("`sport_id`" in batch fields,
"`SELECT id, name FROM sp.sports`" in the file). Those substrings did
appear — just in the wrong function. A real call-path test would have
NameError'd within seconds.

**Fix shipped (this hotfix):**
- Move the `sp_sport_id_by_fl_id` build into `_ingest_pass` so it has
  the function's own session in scope. Cost: one extra 17-row SELECT
  per pass (sp.sports). Self-correcting if sp.sports gains rows
  mid-process. Drops the broken pre-pass code from `run()`.
- 4 new integration tests (`TestIngestPassIntegration`):
  * `test_ingest_pass_runs_without_name_error` — the smoking gun.
    Mocks `flashlive_feed._fl_get` + `upsert_provider_records_batch`,
    calls `_ingest_pass` end-to-end, asserts it completes.
  * `test_ingest_pass_writes_sport_id_in_batch` — asserts each
    record's `fields["sport_id"]` is the canonical sp.sports.id, not
    None or the FL numeric id.
  * `test_ingest_pass_skips_unmapped_fl_sport_id` — asserts unmapped
    FL ids are filtered out of the iteration (don't get polled, don't
    NULL-out existing rows).
  * `test_run_does_not_name_error_at_startup` — exercises `run()`
    against a mocked session_factory + advisory_lock returning False.
    Pre-hotfix, this would NameError at the `session.execute` line.
- Plus a static guard `test_lookup_is_built_inside_ingest_pass_not_run`
  that asserts the construction lives inside `_ingest_pass`'s body,
  not `run()`'s. Defends against re-introduction.

**Deploy sequence:**
```bash
git checkout main && git pull           # after hotfix merge
# Railway redeploys automatically.
# Watch Railway logs: ingestion.fl.pass_complete (no errors expected).

# Verify production writes start landing:
psql "$DATABASE_URL" -c "
  SELECT COUNT(*) FILTER (WHERE sport_id IS NOT NULL) AS with_sport,
         COUNT(*) FILTER (WHERE sport_id IS NULL)     AS without_sport
  FROM sp.fl_events
  WHERE last_seen_at > NOW() - INTERVAL '15 minutes';"
# Expected after one poll cycle (~60s for today loop, ~10min for
# week loop): with_sport > 0 and rising.

# Then backfill the existing rows:
DATABASE_URL=<prod-Neon> python scripts/backfill_sp_fl_events_sport_id.py
# Then re-run the FL smoke (--limit 100) to validate matcher behavior.
```

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
