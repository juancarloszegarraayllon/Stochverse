# Phase 2D Design — Fuzzy Tier (initial expansion + no-anchor fallback)

Status: design doc rev3, awaiting review. 2D.2.8 dry-run is in (drift widening lifted corroboration 1.5% → 2.7%); rev3 locks the Option C1 framing as primary and refines the day-0 prediction down to ~10-11% combined Kalshi auto-apply.

Reference: SP Architecture v1.4 §7 (Resolution Layer) and §13.2 (locked decisions). Builds on Phase 2C's alias tier (`PHASE_2C_DESIGN.md`) and the production day-0 data from PR #95 (2C.3 first cron pass) AND the Phase 2D.2.5 dry-run output (PR #101 first run) AND the Phase 2D.2.8 dry-run re-run after Path B drift widening (PR #104 follow-up).

---

## Rev3 calibration update — measured lift, Option C1 locked

**The Phase 2D.2.8 dry-run measured 2.7% cross-provider corroboration on tennis (was 1.5% pre-drift-widening).** Path B from §E.8 produced a +1.2pp lift — meaningful but much smaller than Q3's availability data predicted (85% → 100% fixture availability at ±60min).

Dry-run output (600 unresolved Kalshi tennis records, post-2D.2.8):

```
Bucket distribution (matcher actual):
  auto_apply:          2  (0.3%)  [unchanged from 30-min drift]
  review_queue:      151  (25.2%) [unchanged]
  no_match:           34  (5.7%)  [-9 from 30-min baseline]
  anchor_failed:     171  (28.5%) [+6 due to suffix filter, separate from drift]

Corroboration:    2.7% (was 1.5%)
```

### Why the lift was smaller than Q3 predicted

Q3 measured **FL fixture availability** within the time window (any tennis match within ±60min). Corroboration also requires **team-name matching against the existing fixture's home/away team_ids**. Most fixtures in the wider drift window aren't the same match — they're other tennis matches at adjacent times. They don't help corroborate.

### The deeper structural finding

**2D's corroboration looks at `sp.fixtures` — populated by strict-tier resolution.** If FL records didn't get strict-tier-resolved (alias coverage gap on FL's side), there's no fixture in the table to corroborate against. This is structural to 2D's design, not a threshold problem. Tightening drift, raising weights, or lowering thresholds doesn't help: the upstream alias coverage limits the ceiling.

This is the right finding to ship 2D against, not to keep tuning. **The auto-apply ceiling for tennis is ~2-3 records per cron under the current alias coverage. Raising it requires Phase 2D.5 (FL-side alias coverage expansion), not more 2D matcher tuning.**

### Decision: lock Option C1 as primary 2D framing

The 151 review_queue records ARE the 2D value proposition for tennis. Operators see structurally informative pairs ("Provider says 'Khachanov' vs candidate 'Khachanov K. (Wrl)'") and approve in seconds. That converts:

- **Before 2D:** 555 records permanently in `deferred_to_2d` per Kalshi tennis cron.
- **After 2D.3 (rev3):** 150 records actively triaged in review_queue + ~200 records flagged for alias expansion (anchor_failed/no_match) + 2-3 records auto-applied.

### Updated day-0 prediction (final, rev3)

| Bucket | Per Kalshi tennis cron | % of 600-record sample |
|---|---|---|
| anchor_failed | ~171 | 28.5% |
| extraction_skipped (post 2D.2.6 suffix extension) | ~50 | ~8% |
| no_match (below threshold) | ~34 | 5.7% |
| **review_queue** | **~151** | **25.2%** |
| auto_apply (corroboration-driven) | ~2-3 | 0.3-0.5% |

**Combined Kalshi auto-apply rate after 2D.3 ships: ~10-11%.**

Honest progression of the prediction across three calibration rounds:

| Round | Combined Kalshi auto-apply prediction | Source |
|---|---|---|
| Rev1 | 16-23% | Day-0 estimate before any dry-run |
| Rev2 | 12-15% | Post-2D.2.5 dry-run (1.5% measured corroboration) |
| **Rev3** | **~10-11%** | **Post-2D.2.8 dry-run (2.7% measured corroboration)** |

Rev3 is the last calibration round before 2D.3. The number doesn't move further until the matcher is in production and 2D.4 day-7 review data lands.

### What rev3 changes vs rev2

- **Day-0 prediction** revised down (12-15% → 10-11%).
- **A.rev2 (per-candidate initial-expansion filter)** deferred from 2D.3 to 2D.7 follow-up. The dry-run shows 2D's primary value is the review queue, not auto-apply — A.rev2 helps disambiguation on the ~2-3 auto-apply records per cron, which is small leverage. The Junfeng Hu / Zhizhen Hu case currently routes to review_queue via cross-team collision detection. Not broken, just imperfect; operators handle it. A.rev2 ships separately as 2D.7 with its own dry-run measurement.
- **Three new follow-up PRs tracked** post-2D.4: 2D.5 (FL alias coverage expansion for ~170 anchor_failed/cron long-tail), 2D.6 (Asian-name single-character surname handling), 2D.7 (A.rev2 per-candidate filter).
- **2D.3 scope tightened** to pure infrastructure: TieredMatcher 3-tier wiring + runner integration + write-back + triple-tier logging. No matcher behavioral changes; current weights stay.

### What rev3 explicitly DOES NOT do

- Doesn't change the matcher API or scorer constants.
- Doesn't bump corroboration weight (rev2 rejected Option A; still rejected).
- Doesn't lower auto-apply threshold (rev2 rejected Option B; still rejected).
- Doesn't add new tiers or change tier order.
- Doesn't introduce schema changes.
- Doesn't add A.rev2 to 2D.3 (deferred to 2D.7 — see §E.11).

---

## Rev2 calibration update — regime change

**The Phase 2D.2.5 dry-run measured 1.5% cross-provider corroboration on tennis. Design rev1 assumed 20-40%. The 20× gap fundamentally invalidates rev1's framework.**

Dry-run output (600 unresolved Kalshi tennis records, post-cron-swap):

```
Bucket distribution (matcher actual):
  auto_apply:          2  (0.3%)
  review_queue:      157  (26.2%)
  no_match:           43  (7.2%)
  anchor_failed:     165  (27.5%)

Corroboration:    3 of 202 anchored = 1.5%
```

Why is the rate so low? Three hypotheses to investigate before locking rev2 (per **§Open question E.8** below):

1. **Tournament overlap**: does FL ingest the same tennis tournaments Kalshi covers?
2. **Kickoff timestamp alignment**: are start times within `find_fixture`'s 30-min drift window?
3. **Drift window appropriateness**: is 30 min too narrow for tennis specifically?

**The cron swap (E.1) was supposed to lift the rate from 2.4% (pre-swap measurement) to 20-40%. Instead it dropped to 1.5%.** The swap can't have caused the drop; the most likely explanation is that the original 2.4% sample was on a different mix of records (or measurement noise on a small denominator). Either way, the rev1 prediction was wrong.

### Three rev2 options the user evaluated

| Option | Description | Verdict |
|---|---|---|
| A | Bump corroboration to +0.40 | Rejected — corroboration only fires 1.5% of the time, so we'd still get ~10 auto-applies per 600 instead of 2. Marginally better, fundamentally limited. |
| B | Lower `AUTO_APPLY_THRESHOLD` to 0.65 | Rejected — would auto-apply the 157 review_queue records but examples show high FP risk (`Cristian` first-name-not-surname; `Park` common surname; `Li` 1-char common surname). |
| **C1** | **Reframe 2D as a review-queue tool, not auto-apply tool. Ship as-is; the 157 anchored-no-corroboration records become primary 2D output.** | **Selected.** |

### What rev2 changes

- **Confidence model**: unchanged from rev1 (locked at signed-off rev1 values). The 0.70-no-corr / 1.00-with-corr math stays the same; the *expectation* shifts from "tennis auto-apply" to "tennis review-queue with operator approval."
- **Day-0 prediction**: revised down from 190-280 tennis auto-applies/cron to **~2-3 fuzzy auto-applies + ~150 fuzzy review-queue records per cron**.
- **Three additional findings** (per §E.6, E.7, E.8 below):
  - E.6: tennis-specific prop suffixes leaking into anchor_failed
  - E.7: single-character surname problem (Asian naming conventions)
  - E.8: corroboration-rate investigation queries (operator runs before locking 2D.3)
- **Personal-path matching enhancement** (per Question A rev2): per-candidate initial-expansion filtering BEFORE collision detection. Discriminates "Junfeng Hu" from "Zhizhen Hu" via the initial-expansion check; doesn't help single-token providers like "Park" alone (still review_queue).

### What rev2 explicitly DOES NOT do

- Doesn't change the matcher API or scorer constants.
- Doesn't bump corroboration weight (rejected Option A).
- Doesn't lower auto-apply threshold (rejected Option B).
- Doesn't add new tiers or change tier order.
- Doesn't introduce schema changes.

### Operational implications of Option C1

Review-queue volume rises significantly:
- 2C.3 alias-tier review queue: ~250-300 records/day across providers
- 2D fuzzy-tier review queue: ~150-200 records/day (Kalshi tennis dominant)
- **Combined: ~400-500 review-queue rows/day, ~2,800-3,500/week**

The 2C.1 alert threshold was 1,500 for the 14-day post-2C.3 window. With 2D adding another spike, the operator's daily review capacity becomes the constraint. Reviewer UX matters — see §"Operational notes" below.

---

## Scope

Phase 2D adds a third resolution tier — **fuzzy tier** — that runs after Phase 2C's alias tier returns NO_MATCH. The fuzzy tier targets two specific gaps the alias tier deliberately can't handle:

**Gap 1 — Tennis defer (the 555 records/Kalshi-cron-pass).** Alias tier currently early-exits on `INDIVIDUAL_SPORT_CODES` because the structural mismatch between Kalshi's surname-only / "Given Surname" forms and FL's "Last F. (Country)" form produces ~0 token-set ratio on remainders. 2D recovers this with **structural initial expansion**: when a 1-2 char token on one side is the prefix of a multi-char token on the other side, treat them as compatible.

**Gap 2 — Team-sport no-anchor residuals.** 2C.3's lowered 0.78 threshold + collision detection catches most team-sport cases. The residual is records where NO candidate scores above 0.78 (very weak similarity, e.g., misspellings or genuinely novel names) but the kickoff alignment + cross-provider corroboration suggests there IS a match. 2D handles these with **character-level fuzzy + corroboration as primary signal**.

Auto-applies at `confidence ≥ 0.85`. `0.70 ≤ confidence < 0.85` routes to `sp.review_queue`. `< 0.70` stays `no_match`.

**Out of scope for 2D:**
- Phase 2C.4 — senior-team disambiguation (separate roadmap item; ships only after day-7 review confirms collision patterns)
- Phase 2E — three-loop runner with `LISTEN/NOTIFY` (cron stays in place)
- Phase 2F — admin review-queue UI
- Phase 2G — resolver diff tooling
- New team / fixture creation in fuzzy tier — same DO-NOT-CREATE discipline as 2C alias tier (per design B.1 carry-forward)
- Any path that bypasses surname-or-token-similarity entirely. The pure "kickoff coincidence alone" case stays out of bounds — it's an obvious false-positive vector and there's no operational data showing it would help.

---

## Day-0 baseline (the inputs to design 2D against)

Production data after PR #95 (2C.3) merged and the first kalshi cron ran:

| Counter                              | Per Kalshi cron pass    |
|--------------------------------------|-------------------------|
| records_scanned                      | 4,384                   |
| signal_extraction_skipped            | 2,017 (46.0%)           |
| strict_auto_applies                  | ~310                    |
| alias_auto_applies                   | 78                      |
| alias_review_queue                   | 750                     |
| alias_tennis_deferred                | 555                     |
| no_match (other than tennis defer)   | ~670                    |
| crashes                              | 0                       |

**The gap 2D targets:**

1. **555 tennis records/run deferred to 2D** with `fail_reason='deferred_to_2d'`. ~180/day per the user's prediction; `~3,885/week` cumulative.
2. **Residual ~670 no_match/run** beyond tennis: a mix of `alias_no_team_resemblance` (provider name doesn't fuzzy-match any candidate above 0.78), `structural_normalize_failed`, and `alias_no_existing_fixture` (anchor passed + above threshold but no fixture at this kickoff). Phase 2D fuzzy can recover a fraction of these.

---

## Three sharpenings — confirmations

### 1. Confidence thresholds — architecture-locked, no change in 2D

Same as 2C:

| Source                   | Confidence | Used by                                   |
|--------------------------|------------|-------------------------------------------|
| Strict tier auto-apply   | 0.98       | Phase 2B (strict@2a.6)                    |
| Alias tier auto-apply    | 0.85       | Phase 2C.3 (alias@2c.0)                   |
| **Fuzzy tier auto-apply**| **0.85**   | **Phase 2D (this doc)** — same threshold  |
| Fuzzy tier review        | 0.70–0.84  | Routes to `sp.review_queue`               |
| Fuzzy tier no_match      | < 0.70     | No DB write to fixtures                   |
| Human-verified           | 1.00       | Reserved for review-queue approvals       |

The 2C and 2D auto-apply thresholds match deliberately. What distinguishes them is how the confidence is constructed (different signal weights — see §C below) and the audit trail: `resolver_version='alias@2c.0'` vs `'fuzzy@2d.0'`.

### 2. Tier evaluation order — strict, alias, fuzzy, no_match

Per architecture v1.4 §7. 2D inserts BETWEEN alias and the (currently terminal) no_match path:

```
extract_signal → strict tier → STRICT auto-apply
                     │
                     └ NO_MATCH
                            │
                            ▼
                       alias tier (Phase 2C.3)
                            │
                            ├── ALIAS / REVIEW_QUEUE → routed
                            │
                            └ NO_MATCH
                                   │
                                   ▼
                              fuzzy tier (Phase 2D, this doc)
                                   │
                                   ├── FUZZY / REVIEW_QUEUE → routed
                                   │
                                   └ NO_MATCH (terminal — Phase 2E live runner doesn't add tiers)
```

`TieredMatcher.match()` extends to return `list[MatchResult]` of length 1, 2, or 3 — runner already iterates and writes one `resolution_log` row per tier per the 2C.3 D.4 logging discipline. **No runner changes needed for the iteration shape.**

### 3. Tennis gate flips: `INDIVIDUAL_SPORT_CODES` no longer early-exits

After 2D ships, the alias tier's tennis defer changes meaning:
- **Before 2D**: alias tier returns `NO_MATCH(deferred_to_2d)` for individual sports.
- **After 2D**: alias tier still returns `NO_MATCH(deferred_to_2d)` for individual sports. **The fuzzy tier picks them up.** Alias tier is unchanged — the orchestrator routes individual-sport records to 2D via the existing fall-through.

This is a one-line update to `AliasTierMatcher` documentation, not a logic change. The sentinel keeps the day-7 query (`fail_reason='deferred_to_2d'`) working — it now means "deferred from alias to fuzzy" rather than "deferred to a future phase."

---

## Order-of-operations and re-resolve

Pushback 1 from rev1 review surfaced two operational questions that the original draft glossed over. Both need explicit answers in the doc.

### Question E.1 — Cron schedule swap

**Current ordering:** `resolver-cron-kalshi` at 02:00 UTC, `resolver-cron-fl` at 02:15 UTC. Locked in `railway.toml` since PR #88.

**Problem at 2D rollout:** Kalshi tennis records depend on FL having already resolved the same fixture for cross-provider corroboration. Under the current 02:00/02:15 ordering, Kalshi runs FIRST — `find_fixture` against `sp.fixtures` misses because FL hasn't ingested the day's events yet. Result: Kalshi tennis records get `no_match(fuzzy_no_existing_fixture)` on the morning pass, then resolve on the NEXT day's pass after FL has run.

**Recommendation: swap to FL 02:00 / Kalshi 02:15.** FL strict tier resolves ~78% of its corpus on first contact; running it first means Kalshi's 2D fuzzy lookups see freshly-resolved fixtures. Estimated impact: same-pass resolution for ~80% of tennis records that would otherwise lag a cycle.

The swap is **independent of 2D ship timing** and can land as a small `railway.toml` PR ahead of 2D.1. Same shape as the original 2B parallel-run cron PR (#88).

The 02:15 stagger should remain to avoid Neon connection pool contention during the bulk-load phase (15 min is enough for FL's ~165s pass + buffer).

### Question E.2 — Pre-2E re-resolve mechanism

**Currently:** the runner SQL filters by `fixture_id IS NULL` on the provider table — NOT by absence of a `resolution_log` row. A no_match record stays `fixture_id IS NULL`, gets selected on the next cron, runs through the full TieredMatcher again. The "re-resolve pass" already exists implicitly via per-cron retries.

**Implication for 2D:** records that 2D no_match's today (e.g., because corroboration wasn't available) get RE-TRIED tomorrow. If FL has resolved the corresponding fixture in the interim, 2D corroboration fires on retry → auto-apply. **No code change needed for re-resolve.**

**Cost: resolution_log accretion.** A record stuck in no_match for 14 days produces ~42 `resolution_log` rows (3 tiers × 14 days). At ~1KB per row this is small (~42KB per stuck record). Not a problem volume-wise but produces noise in audit queries. Day-7 review query already groups by `(provider, provider_record_id, decided_at::date)` so it's queryable; documented as known noise.

**Phase 2E** addresses this properly with a continuous-loop runner that only re-resolves records when their underlying data changes. Pre-2E, the per-cron retry is sufficient.

**Net effect for 2D's day-0 prediction:** records that don't auto-apply on day 1 due to missing corroboration will mostly resolve on day 2 (after FL has ingested + strict-tier-resolved the corresponding fixture). Steady-state auto-apply rate is higher than first-pass rate by maybe 10-20%. The day-0 numbers in §"Day-0 prediction" reflect first-pass; steady-state is in the upper half of the range.

---

## Question A — Tennis pattern: structural initial expansion

### Answer (definitive): Initial expansion as an additional structural-equivalence rule.

The 2C.2.5 dry-run made the gap concrete. Token-set ratio:
- `"miomir"` vs `"m"` = 29
- `"miomir"` vs `"m srb"` = 36 (parenthetical-stripped) or 36 with country present

Both well below 2C's 0.85 threshold. But to a human, "M" is obviously the initial of "Miomir" — they're compatible.

**Initial expansion rule:**

```
For provider_remainder_tokens P and candidate_remainder_tokens C:

  P_long  = [t in P if len(t) > 2]
  P_short = [t in P if len(t) <= 2]
  C_long  = [t in C if len(t) > 2]
  C_short = [t in C if len(t) <= 2]

  Compatible if EVERY short token on either side is the prefix of
  some long token on the other side:

    every s in P_short: any l in C_long with l.startswith(s)
    every s in C_short: any l in P_long with l.startswith(s)
```

Concretely:

| Provider remainder | Candidate remainder | P_short | C_short | Compatible? |
|---|---|---|---|---|
| `"miomir"` | `"m"` | [] | ["m"] | "miomir".startswith("m") → ✓ |
| `"daniil"` | `"d"` | [] | ["d"] | "daniil".startswith("d") → ✓ |
| `"john"` | `"m"` | [] | ["m"] | "john".startswith("m") → ✗ |
| `""` (single-token) | `"m"` | [] | ["m"] | C_short non-empty but no P_long → ✗ (no compat) |
| `"miomir"` | `""` | [] | [] | trivially ✓ |
| `"miomir andrey"` | `"m a"` | [] | ["m", "a"] | both prefix-checks pass → ✓ |
| `"miomir andrey"` | `"m b"` | [] | ["m", "b"] | "b" doesn't prefix any of {"miomir","andrey"} → ✗ |

Symmetric — works on both Kalshi-with-full-name vs FL-with-initial AND Kalshi-with-initial vs FL-with-full-name (rare, but defensive).

**Score contribution:** when initial expansion is compatible (all short tokens map cleanly), contribute a fixed `+0.30` to confidence. NOT linear-scaled — initial expansion is binary "compatible or not." Linear scaling would suggest "75% compatible" which doesn't have a defensible interpretation.

**Why not lower the personal-path token-set threshold?** Two issues:

- A linear-scaled personal-path threshold of 0.20 or 0.30 would also reward genuinely-low overlap cases like "miomir" vs "andrey" (~0.20) — false positives.
- Initial expansion is a clean structural signal. "M" is the initial of "Miomir" with high confidence. Token-set ratio doesn't model this; treating it as a separate signal is more precise than relaxing the existing one.

### Edge cases

**Compound first names.** "Stefanos Tsitsipas" / "Tsitsipas S.": surname matches, P_long=[stefanos], C_short=[s], "stefanos".startswith("s") → ✓.

**Compound surnames.** "Carlos Alcaraz Garfia" / "Alcaraz C. (Esp)" — surname mismatch ("garfia" vs "alcaraz"). Per design D.A.2, the personal-name normalizer's "fall back to compound-suffix" path should fire here. **Phase 2D includes this fallback** (see §A.1 below) — it was deferred from 2C.

**Hyphenated names** like "Anna-Lena Friedsam" — tokenizer splits to ["anna", "lena", "friedsam"]. Surname="friedsam", P_long=["anna","lena"]. Candidate "Friedsam A." has C_short=["a"]. Compatible: "anna".startswith("a") → ✓. Works.

**Asian-name conventions** like "Naomi Osaka" (given-surname like English) vs "Osaka N." (surname-given like FL) — surname matches "osaka" both sides; remainder works exactly like Western patterns.

**Cross-player initial collisions.** "Marin Cilic" vs "Mensik J. (Cze)" — different surnames, doesn't reach initial-expansion. Anchor failure prevents it.

What about "Marin Cilic" vs "Cilic M. (Cro)" AND "Murray Andy" vs "Murray A. (Gbr)" — both have provider with "M" something. But each side has its own structural-normalize result; the initial expansion is per-side, not cross-side.

### Spot-check handling intent (Pushback 2)

Concrete cases the user named, with explicit per-case behavior so we know what the algorithm does on day 0.

**Case 1 — `"M.K."` → `"Miomir Kecmanovic"` (compound initials).**

Provider tokens after normalize: `["m", "k"]` (period stripped as punct). Both length 1. Personal-name structural detection per 2C: `personal_initial` rule fires (2 tokens, second is 1-2 chars), so surname=`"m"`, others=`("k",)`.

That's wrong — "k" is the surname-initial and "m" is the given-name initial. The 2C structural rule was designed for "Last F." (surname-first), not "F.L." (initial-first).

Candidate "Miomir Kecmanovic" → surname=`"kecmanovic"`. Provider surname=`"m"` doesn't match. **Anchor fails.**

**2D handling:** stays no_match. Bidirectional surname-interpretation expansion is a meaningful enhancement but adds complexity to the personal-name normalizer for a rare case. Documented as a known limitation. Future extension: detect `2-token, both 1-char` pattern and try both `(surname=token[0], initial=token[1])` AND `(initial=token[0], surname=token[1])`. Defer to a 2D follow-up after observing day-7 frequency.

**Case 2 — `"Carlos"` → `"Carlos Alcaraz Garfia"` (single-token provider, multi-token candidate).**

Provider: single-token, surname=`"carlos"`, others=`()`. Candidate (after structural normalize): personal_multi, surname=`"garfia"`, others=`("carlos","alcaraz")`. **Surname mismatch.**

Compound surname fallback (A.1) on the candidate side tries `"alcaraz garfia"`, then `"carlos alcaraz garfia"`. None match provider's "carlos".

**2D handling:** stays no_match. Single-token provider names are structurally ambiguous (could be first name OR last name); auto-applying on first-name-as-surname is a false-positive vector. There would typically be many "Carlos" candidates in the pool — multi-match collision would force review queue even if we did try the inverse interpretation. Documented as a deliberate non-goal.

**Case 3 — `"Bautista"` → `"Roberto Bautista Agut"` (middle name as primary; first omitted).**

Provider: surname=`"bautista"`, others=`()`. Candidate default interpretation: surname=`"agut"`, others=`("roberto","bautista")`. **Surname mismatch on the default.**

Compound fallback retries: `"bautista agut"` (still mismatch), then full tokens (mismatch).

**This is the case that needs E.3 — multi-interpretation candidate surname index.** The candidate `"Roberto Bautista Agut"` should be indexed under MULTIPLE surname interpretations:
- `surname="agut"` (default, last token)
- `surname="bautista agut"` (compound)
- `surname="bautista"` (middle-token-as-surname, common for Spanish/Portuguese-style compound names)

With the multi-interpretation index, provider `"Bautista"` finds `"Roberto Bautista Agut"` via the `surname="bautista"` interpretation. Anchor passes. Initial expansion: empty remainders both sides → no contribution. Confidence = 0.40 (anchor) + 0.30 (corroboration if present) = 0.70 → review_queue boundary.

**2D handling:** auto-apply only with corroboration; review_queue otherwise. This is correct — "Bautista" alone is genuinely ambiguous (could be Pablo Carreño Busta misread, etc.), and review queue is the safe routing.

**Case 4 — `"Wang"` → multiple Wangs (cross-player surname collision).**

Provider: surname=`"wang"`. Candidate pool has "Wang Q.", "Wang X.", "Wang Y." — all with surname=`"wang"`.

**Multiple candidates anchor.** Per 2C-style collision detection (which 2D inherits): multiple candidates above threshold → collision → review_queue regardless of confidence.

**2D handling:** review_queue with `colliding_team_ids` listing all matching Wangs. Reviewer in 2F picks the right one. Same as the 2C "Real Sociedad" case. Phase 2D will surface the volume of these collisions in the day-7 review — if specific surname-collision patterns dominate (e.g., Chinese tennis players consistently routed to review), that's input for a 2D follow-up similar to 2C.4 (senior-team disambiguation) — perhaps gender disambiguation via `country_code` + competition context.

**Summary table of intent:**

| Case | Provider | Candidate | Anchor | Initial expansion | Routing |
|---|---|---|---|---|---|
| 1 | "M.K."        | "Miomir Kecmanovic"      | fails (structural) | n/a       | no_match (limitation) |
| 2 | "Carlos"      | "Carlos Alcaraz Garfia"  | fails (cross-surname) | n/a    | no_match (deliberate) |
| 3 | "Bautista"    | "Roberto Bautista Agut"  | passes via E.3 multi-index | empty | review_queue (or auto with corroboration) |
| 4 | "Wang"        | multiple "Wang X."       | passes for ALL    | n/a       | review_queue (collision) |

### A.1 — Compound surname fallback

**Carry over from 2C design D.A.2 (deferred to 2D).** When the last-token-as-surname interpretation fails, retry with the last-two-tokens as a compound surname.

For "Carlos Alcaraz Garfia":
- First attempt: surname="garfia", remainder=("carlos", "alcaraz")
- If first attempt anchor-fails (no candidate has surname="garfia"): retry
- Second attempt: surname="alcaraz garfia", remainder=("carlos",)
- If THAT fails: retry with surname="alcaraz", remainder=("carlos", "garfia")

Three interpretations tried in sequence; first that produces an anchor hit wins.

This expands the personal-name candidate-search work but with a surname-anchor index by `(sport_id, surname)` (already built by 2C's `CandidateIndex` but unused), each retry is O(1) lookup — total cost negligible.

### A.rev2 — Per-candidate initial-expansion filter BEFORE collision detection

**Added in rev2.** The 2D.2 matcher's `_find_personal_match` currently routes any surname-collision (multiple candidates returned by `candidates_for_surname`) directly to review_queue. This loses information: when the provider's remainder uniquely identifies one of the colliding candidates via initial-expansion, that candidate should win over the others.

**Concrete case (the user's "single-character surname problem", §E.7):**

Candidates indexed under surname `"hu"`:
- `"Hu Z. (Chn)"` — surname=`hu`, remainder=`("z",)`
- `"Hu J. (Chn)"` — surname=`hu`, remainder=`("j",)`
- `"Hu Y. (Chn)"` — surname=`hu`, remainder=`("y",)`

Provider input `"Junfeng Hu"` → surname=`hu`, remainder=`("junfeng",)`. Lookup returns 3 candidates.

Pre-rev2 behavior: 3 distinct team_ids → collision → review_queue.

**Rev2 enhancement: filter candidates by initial-expansion compatibility.**
- `initials_compatible(("junfeng",), ("z",))` → False
- `initials_compatible(("junfeng",), ("j",))` → True (`"junfeng".startswith("j")`)
- `initials_compatible(("junfeng",), ("y",))` → False

After filter: exactly one compatible candidate (`Hu J.`). Anchor passes for that candidate; collision suppressed. Confidence math runs normally.

**When the filter doesn't help (still review_queue):**
- Provider `"Park"` alone (single-token, empty remainder): every candidate `Park K.`, `Park S.`, `Park J.` passes initial-expansion vacuously (P_short=`[]`, P_long=`[]`). Filter doesn't discriminate. → collision → review queue.
- Provider `"Wang"` likewise. Genuinely ambiguous — review queue is correct.

**Implementation in 2D.2 matcher** (re-edits 2D.2 code; small change to `_find_personal_match`):

```python
def _find_personal_match(self, provider_struct, sport_id):
    raw_candidates = self.candidates.candidates_for_surname(...)
    unique_by_team_id = dedupe_by_team_id(raw_candidates)

    if len(unique_by_team_id) > 1:
        # NEW (rev2): filter by initial-expansion compatibility BEFORE
        # declaring collision. The provider's remainder may uniquely
        # discriminate among same-surname candidates.
        compatible = [
            c for c in unique_by_team_id
            if initials_compatible(provider_struct.other_tokens, c.structured.other_tokens)
        ]
        if len(compatible) == 1:
            # Exactly one compatible — winner.
            ... single-candidate path ...
        # Multiple compatible OR zero compatible after filter:
        # collision (same as before).
        ... collision path ...
```

This is a 2D.2 amendment, not a separate PR. Ships in **2D.3** alongside the matcher integration (since 2D.2 is already merged, 2D.3 includes a follow-up patch to `_find_personal_match`).

**Test plan adjustment:** add a new test `test_initial_expansion_filter_disambiguates_among_same_surname` to `test_resolver_2d.py` proving the "Junfeng Hu" case auto-resolves while "Park" stays in review_queue.

---

## Question B — Team-sport no-anchor fallback

### Answer (definitive): Character-level fuzzy with high threshold + cross-provider corroboration as primary signal.

The 2C.3 lowered threshold (0.78) plus collision detection catches most team-sport cases. The residual is provider records where the closest candidate scores < 0.78 — usually one of:

- **Spelling variance**: "Bayern München" / "Bayern Munich" (token-set 89, below 2C.3's 0.78... actually wait, 89 > 78, so 2C.3 catches this. Let me reconsider.)

Actually let me recompute: 2C.3 threshold is 0.78, not 0.92. Bayern München (89) IS above the new threshold. So this case is handled by 2C.

The actual residual after 2C.3:
- Provider names with NO meaningful token overlap: ratios in 0.30-0.60 range. These need character-level similarity (Levenshtein-derived) which can pick up character-shuffle cases.
- Provider names that match ZERO candidates above 0.78. The "alias_no_team_resemblance" bucket from the day-7 query. Production volume estimate: ~50-150/day.

**For these residuals, 2D fuzzy:**

```
1. For each candidate in sport, compute fuzz.ratio() (character-level
   Levenshtein-derived similarity, NOT token-set). Range [0, 1].
2. Threshold ≥ 0.85. Lower than that = no fuzzy candidate.
3. If multiple candidates pass: collision → review_queue.
4. If exactly one candidate passes: anchor passed.
5. Cross-provider corroboration via find_fixture is REQUIRED for
   auto-apply. Without corroboration, even a 0.95 fuzzy match goes
   to review_queue.
```

The corroboration-required rule is stricter than 2C alias-tier. Reasoning: 2C had token-set anchor = surname/qualifier overlap, which is a meaningful structural signal. 2D fuzzy has only character-level similarity, which is more error-prone (e.g., "Real Madrid" / "Real Mallorca" character ratio ~0.85). Requiring corroboration reduces false-positive risk to acceptable levels.

### B.1 — Cross-provider corroboration as primary signal (the user's earlier question)

In 2C, corroboration was a tiebreaker that added +0.20 to surname-anchored candidates. In 2D, corroboration is **required for fuzzy auto-apply** — it's the difference between auto-apply and review_queue.

This is what the original 2C design doc § "Open question A.2" envisioned for 2D's expanded scope:

> **B.1 — Cross-provider corroboration as primary signal.**
> 2C scoped corroboration as a tiebreaker for already-anchored candidates only. The pure "no name resemblance, kickoff alone" case stays locked out of 2C; reserved for Phase 2D.

This doc proposes 2D specifically NOT bypass name resemblance entirely. There's still an anchor (fuzzy ratio ≥ 0.85) — corroboration just gates auto-apply vs review-queue. The pure "kickoff alone" case (zero name resemblance) stays out of bounds.

---

## Question C — Confidence model + thresholds

### Answer (definitive): Path-aware composable signals, three-component max-1.00.

Pushback 3 (rev1): the original draft had a `+0.10 kickoff_drift_tightness` term on the personal path. Same reasoning as the 2C Pushback 3 review applies — kickoff drift is hard-filtered at strict-tier gate 1, and adding sub-window-tightness bonus inside the filter is a magic number without supporting data. **Dropped.**

Personal path now has three signals (matching team path's structure):

```
Personal-name path:
  surname_anchor (binary, after compound-fallback retries):       +0.40
  initial_expansion (binary, all short tokens prefix-match)
    OR remainder_token_set_quality (linear, ≥0.85 threshold)
    [take the max, no double-count]:                              up to +0.30
  cross_provider_corroboration (existing fixture at this kickoff): +0.30

Team-name path:
  fuzz_ratio_anchor (binary, ratio ≥0.85):                        +0.40
  fuzz_ratio_quality (linear, 0.85→+0.10, 1.0→+0.30):             up to +0.30
  cross_provider_corroboration (REQUIRED for auto-apply):         +0.30
```

**Both paths**: max with corroboration = 0.40 + 0.30 + 0.30 = **1.00**.

**Both paths**: max without corroboration = 0.40 + 0.30 = **0.70** → review-queue lower bound (exclusive of auto-apply at 0.85).

The corroboration weight is **higher in 2D than in 2C** (+0.30 vs +0.20 in 2C alias-tier). Reasoning:
- The 2C alias tier's anchor (token-set ratio) is a stronger structural signal than 2D's anchors (initial expansion is binary, character-level ratio is statistically noisier than token-set). The corroboration weight has to carry more of the safety margin.
- Symmetric across paths: same +0.30 for both personal and team. Avoids per-path magic numbers.

The personal-vs-team distinction is now WHICH anchor signal is used (initial-expansion-or-token-set vs character-level-ratio), not the WEIGHTING. Cleaner.

### Routing (same as 2C)

| Final confidence | Reason code     | DB writes |
|------------------|-----------------|-----------|
| ≥ 0.85           | `fuzzy`         | provider.fixture_id UPDATE + resolution_log INSERT + sp.team_aliases INSERT (write-back, source='fuzzy_tier') |
| 0.70 – 0.84      | `review_queue`  | resolution_log + sp.review_queue |
| Top-2 within 0.05 | forced review  | resolution_log + sp.review_queue (even if ≥ 0.85) |
| < 0.70           | `no_match`     | resolution_log only |

`source='fuzzy_tier'` distinguishes 2D-tier alias write-backs from 2C alias-tier write-backs. The next strict-tier pass picks both up at 0.98 confidence; the source distinction is for audit and 2F reviewer confidence calibration.

### Routing (same as 2C)

| Final confidence | Reason code     | DB writes |
|------------------|-----------------|-----------|
| ≥ 0.85           | `fuzzy`         | provider.fixture_id UPDATE + resolution_log INSERT + sp.team_aliases INSERT (write-back, source='fuzzy_tier') |
| 0.70 – 0.84      | `review_queue`  | resolution_log + sp.review_queue |
| Top-2 within 0.05 | forced review  | resolution_log + sp.review_queue (even if ≥ 0.85) |
| < 0.70           | `no_match`     | resolution_log only |

`source='fuzzy_tier'` distinguishes 2D-tier alias write-backs from 2C alias-tier write-backs. The next strict-tier pass picks both up at 0.98 confidence; the source distinction is for audit and 2F reviewer confidence calibration.

---

## Schema changes

### Answer (definitive): None required.

Same as 2C. Existing tables suffice:

| Existing table        | What 2D uses it for |
|-----------------------|---------------------|
| `sp.team_aliases`     | Read (via 2C `CandidateIndex` on startup). Write back on auto-apply with `source='fuzzy_tier'`. The existing `ON CONFLICT (alias_normalized, source)` UNIQUE handles idempotency. |
| `sp.resolution_log`   | One row per tier consulted (3-tier list now). `reason_detail.fuzzy_score_breakdown` carries the per-signal contributions. |
| `sp.review_queue`     | Same as 2C — `candidate_fixtures` JSONB. |
| `sp.resolver_runs`    | New `extra` keys: `fuzzy_auto_applies`, `fuzzy_review_queue`. |

The 2C `CandidateIndex` ALREADY builds the personal-name surname index (`by_sport_surname`). 2D activates it — no schema or index changes needed.

---

## Negative space — what 2D explicitly does NOT do

### 1. Pure kickoff-coincidence matching.

The 2C-locked stance carries forward: corroboration is a CONTRIBUTING signal, not the primary one. 2D requires either a passing fuzzy ratio OR a passing surname anchor as a precondition. "Two unrelated games happen to be at the same kickoff in different sports" is an obvious false-positive vector.

### 2. Cross-sport disambiguation in fuzzy tier.

Already enforced by `CandidateIndex.candidates_for_sport(sport_id)` — same sport scoping as 2B/2C. A tennis surname won't match a soccer team via fuzzy similarity even if the strings are character-close.

### 3. Auto-creating teams or fixtures.

Same DO-NOT-CREATE discipline as 2C alias tier. 2D links to existing fixtures or returns no_match. The fuzzy tier confidence is structurally weaker than strict's; minting new fixtures off fuzzy similarity is too aggressive.

### 4. Tennis fixture creation.

Today's tennis records mostly point at fixtures that DON'T exist in `sp.fixtures` because the strict tier never creates them (alias tier defers, fuzzy will rely on existing fixtures). Net effect: the FIRST FL tennis pass creates fixtures via strict-tier `ensure_fixture`; subsequent Kalshi tennis passes link via 2D fuzzy. **Order-of-operations matters** — Kalshi tennis records arriving before the corresponding FL pass will return `no_match(fuzzy_no_existing_fixture)` and pick up on the next pass.

This is the same equal-or-NULL competition_id transition pattern from 2A.6 — documented; no special-case logic.

### 5. Ratio-tuning for individual sports beyond initial expansion.

Personal-name path is precision-first. If initial expansion + surname anchor + corroboration together can't produce auto-apply confidence, the case routes to review queue. We don't add ad-hoc per-sport tuning (e.g., "tennis-specific qualifier suffixes") — those are 2D.5+ concerns gated on day-N audit data.

### 6. Recovery of `signal_extraction_skipped` records.

The 2,017 records/Kalshi-pass that `extract_signal` skipped (Kalshi outright/series/tournament shapes, prop bets caught by 2C.1/2C.2.6 filters) are CORRECTLY rejected. 2D doesn't try to revive them.

---

## Implementation sketch

### File layout

```
resolver/
  fuzzy_tier/
    __init__.py            (new — exports)
    initial_expansion.py   (new — initial-expansion rule, pure function)
    matcher.py             (new — FuzzyTierMatcher)
  matcher.py               (modified — TieredMatcher wraps strict + alias + fuzzy)
  alias_tier/
    matcher.py             (modified — one-line docstring update;
                            alias tier still returns NO_MATCH for individual
                            sports, fuzzy picks up via the orchestrator)
  ...

scripts/
  run_resolver_pass.py     (modified — surface fuzzy_auto_applies +
                            fuzzy_review_queue counters in summary +
                            sp.resolver_runs.extra; runner already
                            iterates len(tier_results) so the loop is
                            unchanged)

tests/
  test_resolver_2d.py      (new)
```

### `resolver/fuzzy_tier/initial_expansion.py` shape

```python
def initials_compatible(
    provider_tokens: tuple[str, ...],
    candidate_tokens: tuple[str, ...],
) -> bool:
    """True iff every short token (len 1-2) on either side is the
    prefix of some long token (len > 2) on the other side. See
    PHASE_2D_DESIGN.md §A for the rule."""
    ...
```

Pure function. Unit-tested standalone.

### `resolver/fuzzy_tier/matcher.py` shape

```python
class FuzzyTierMatcher:
    def __init__(
        self,
        candidates: CandidateIndex,
        sport_id_by_code_or_name: dict[str, int],
    ) -> None: ...

    async def match(
        self,
        session: AsyncSession,
        signal: FixtureSignal,
    ) -> MatchResult: ...
```

Internal flow:
1. Sport classified gate (same as 2C alias).
2. kickoff_at gate (required for corroboration).
3. Personal vs team path discrimination via INDIVIDUAL_SPORT_CODES (same as 2C).
4. Personal path: surname anchor + initial expansion. Compound surname fallback (A.1).
5. Team path: character-level fuzz.ratio() ≥ 0.85.
6. Fixture-level confidence per §C.
7. Routing per §C.

### Matcher orchestration (`resolver/matcher.py` change)

```python
class TieredMatcher:
    def __init__(self, strict, alias, fuzzy=None) -> None: ...

    async def match(self, session, signal) -> list[MatchResult]:
        strict_result = await self.strict.match(session, signal)
        if strict_result.reason_code == ReasonCode.STRICT:
            return [strict_result]
        alias_result = await self.alias.match(session, signal)
        if alias_result.reason_code in (ReasonCode.ALIAS, ReasonCode.REVIEW_QUEUE):
            return [strict_result, alias_result]
        if self.fuzzy is None:
            return [strict_result, alias_result]
        fuzzy_result = await self.fuzzy.match(session, signal)
        return [strict_result, alias_result, fuzzy_result]
```

The runner iterates `tier_results` and writes one resolution_log per. Atomic transaction shape unchanged from 2C.

---

## Test plan

### Unit tests

#### TestInitialsCompatible (`tests/test_fuzzy_tier_initial_expansion.py`, ~10 tests)
- Single-initial vs full-name compatible
- Multi-initial vs multi-name compatible
- Mismatched initial rejected
- Asymmetric (provider initials vs candidate full, candidate initials vs provider full)
- Empty tokens on one or both sides
- Hyphenated names (tokenized away)

#### TestCompoundSurnameFallback (~5 tests)
- "Carlos Alcaraz Garfia" → "Alcaraz C." (first attempt fails, second succeeds)
- "Lopez Garcia M." with both forms in candidates
- Three-level fallback exhaustion
- Single-token name (no fallback applicable)

#### TestFuzzyTierMatcher (~15 tests, real call-path with mocked DB session)

Per the PR #87 lesson: real call-path tests as the primary surface. Mocked DB session, hand-built CandidateIndex.

- **Tennis recovery**: Kecmanovic case — surname matches, initial expansion compatible, with corroboration → auto_apply (confidence 1.00).
- **Tennis without corroboration**: same case → review_queue (0.80).
- **Tennis with surname mismatch**: → no_match.
- **Tennis with incompatible initials**: surname matches but P_long doesn't start with C_short → no_match.
- **Tennis compound surname**: "Alcaraz Garfia" handled via fallback.
- **Team-sport residual**: "Bayrn Munich" misspell with fuzz.ratio ≥ 0.85 → fuzzy match. Without corroboration → review_queue. With corroboration → auto_apply.
- **Team-sport collision**: multiple candidates with fuzz.ratio ≥ 0.85 → review_queue regardless of corroboration.
- **Cross-team rejection**: fuzz.ratio < 0.85 → no_match.
- **Tennis without kickoff**: kickoff_at_missing gate → no_match.
- **TieredMatcher 3-tier**: strict miss + alias miss + fuzzy hit returns ALL THREE results in order (D.4 logging).

### Integration tests (`tests/test_resolver_2d_integration.py`, against mocked DB)

Spot-check fixtures from production data:
- 5 actual Kalshi tennis records that produced `deferred_to_2d` in the day-0 cron — assert the 2D matcher resolves at least 3 of them.
- 5 team-sport `alias_no_team_resemblance` records — assert at least 1 resolves via fuzzy character-level path.

These integration spot-checks need the live `sp.team_aliases` data; they're gated behind `SP_INTEGRATION_DB` env var (same pattern as the existing 2B integration stub).

### Day-N dry-run (Phase 2D.2.5, mirrors 2C.2.5)

Before 2D.3 (matcher integration), run a script analogous to `scripts/dry_run_alias_tier.py` against production tennis records. Expected output:
- ~70-80% of 555 deferred → either auto_apply (with corroboration) or review_queue (without).
- ~20-30% remain no_match — surname/initial mismatch or genuinely novel names.

---

## Day-0 prediction (rev2 — Option C1 framework)

**Rev2 reframes 2D as a review-queue tool, not an auto-apply tool.** The 1.5% measured corroboration rate means the auto-apply path is essentially unused for tennis (corroboration is required for auto-apply per rev1's math, and corroboration almost never fires).

### Recomputed numbers (per Kalshi cron pass, tennis)

Based on 2D.2.5 actual measurement (600-record sample):

| Bucket | Per cron pass | % of input |
|---|---|---|
| anchor_failed | ~165 | 27.5% |
| extraction_skipped | ~30 | 5% (rises to ~50 after E.6 suffix-list extension) |
| no_match (below threshold) | ~43 | 7.2% |
| **review_queue** | **~157** | **26.2%** |
| auto_apply (corroboration-driven) | ~2 | 0.3% |
| (other ~205 unaccounted in current dry-run sample — TBD) | | |

**Headline: ~150 fuzzy review-queue records per Kalshi cron, ~2-3 fuzzy auto-applies.**

This is a major change from rev1's 78-188 tennis auto-apply prediction. The day-0 narrative for 2D shifts:
- **Before rev2 (rev1 framing):** "2D recovers ~190 tennis auto-applies/day."
- **After rev2 (Option C1):** "2D produces ~150 high-quality review-queue items/day. Operator approves them in seconds because the structural match (provider 'Kecmanovic' → candidate 'Kecmanovic M. (Srb)') is informative. Net effect: ~150 fixtures linked/day with review-queue confirmation, ~2 auto-apply links/day."

### Team-sport residuals (Gap 2)

Unchanged from rev1 expectations — 2D.2.5 dry-run was tennis-only. Estimates:
- Conservative: ~30 anchored, 25% corroboration → 8 auto-apply, 22 review.
- Optimistic: ~150 anchored, 40% corroboration → 60 auto-apply, 90 review.

Recommend a separate 2D.2.5 dry-run on a team sport (per E.8 below) to validate before 2D.3.

### Total post-2D projection (revised)

| Source | Baseline (2C.3) | Post-2D delta (auto) | Post-2D delta (review) |
|---|---|---|---|
| 2C alias-tier | ~388 auto / ~250 review | unchanged | unchanged |
| 2D tennis | n/a (deferred) | +2-3 auto | +150 review |
| 2D team-sport residual | n/a | +8-60 auto | +22-90 review |
| **Total per Kalshi cron** | **~388 auto / ~250 review** | **~398-451 auto** | **~422-490 review** |

Review-queue volume nearly doubles. The 2C.1 alert threshold of 1,500 still has headroom (review_queue typically drains via reviewer approvals; the threshold catches stuck-pile pathology, not steady-state load).

### Operator capacity check

If review queue grows by ~150/day from 2D plus ~250/day from 2C = ~400/day total, operator review at ~10 sec/record = ~67 min/day of review work. That's an actual workload increase. The user should confirm operator availability before 2D.3 ships, or accept that review queue depth grows for the first ~2 weeks while operators work through the backlog.

### Caveats (rev2 — narrowed since the dry-run resolved most uncertainty)

1. **The 1.5% corroboration rate is real but the cause is unknown.** Per E.8 below, three investigation queries should run before locking 2D.3. If the cause is fixable (e.g., wrong drift window for tennis, FL tournament gap), the rate could improve and auto-apply numbers go up.
2. **Team-sport residuals haven't been dry-run'd.** Run `dry_run_fuzzy_tier.py --sport-code soccer --limit 600` before locking 2D.3 thresholds for the team path.
3. **555 records/run figure is from one Kalshi tennis pass.** Steady-state may differ once 2D.3 is shipping. Per design rev1 §"E.2 re-resolve mechanism": records that don't resolve on day 1 retry on day 2.

---

## Open questions awaiting sign-off

Each question is tagged with the PR that's blocked on its resolution:
**[2D.1]** = blocks 2D.1 (initials_compatible + compound-surname fallback)
**[2D.2]** = blocks 2D.2 (FuzzyTierMatcher)
**[2D.3]** = blocks 2D.3 (orchestrator + runner integration)
**[dry-run]** = answered by 2D.2.5 dry-run output
**[doc]** = documentation-only; no implementation effect

### A.1 — Compound surname fallback retry order **[2D.1]**

Three retries proposed: (last token), (last two tokens), (last three tokens). For 4-token names ("Lopez Garcia Sanz Mendez") that's 3 retries. **Recommendation:** stop at 3 retries. Beyond that = diminishing returns + FP risk.

### A.2 — Initial expansion case-sensitivity **[doc]**

The structural normalize lowercases; both sides hit the rule with same case. **No additional handling needed.** Documenting for clarity.

### A.3 — Multi-initial cases ("J.J. Watt") **[2D.1]**

`"j.j. watt"` after normalize = `["j", "j", "watt"]`. P_short=`["j","j"]` each prefix-checks the candidate's long tokens. If candidate is `["jj", "watt"]` (single bigram), only "watt" is in C_long; "watt".startswith("j") → ✗. **Recommendation:** stays in review queue / no_match. Real-world impact: rare. Documented as known limitation; 2D.1 tests assert it.

### B.1 — Team-path fuzz.ratio threshold **[2D.2]** **[dry-run]**

0.85 chosen by analogy to alias-tier auto-apply. Character-level Levenshtein-derived; empirical FP rate at this threshold isn't known. **Recommendation:** ship at 0.85, watch the day-7 review for false positives, tighten to 0.90 if needed. Same calibration discipline as 2C threshold tuning.

### C.1 — Initial-expansion contribution magnitude **[2D.3]**

`+0.30` chosen so personal-path-with-corroboration hits 1.00 exactly. Could be `+0.25` (more conservative — auto-apply requires both initial expansion AND corroboration AND every other signal). Could be `+0.35` (more permissive).

**Recommendation:** ship at +0.30 (the natural 0.40+0.30+0.30=1.00 decomposition post-Pushback-3 rev1). The day-7 review surfaces whether that's too aggressive.

### C.2 — Personal-path token-set as alternative to initial expansion **[2D.3]**

The model proposes initial expansion OR remainder token-set, not both. **Recommendation:** take the max of the two. No double-counting.

### D.1 — Resolver version stamp **[2D.3]**

`fuzzy@2d.0` for the matcher, `tiered@2d.0` for the orchestrator (since adding a tier is a semantic change to TieredMatcher). Per-decision rows on `resolution_log` keep tier-specific stamps; `sp.resolver_runs.resolver_version` becomes `tiered@2d.0`.

### D.2 — Day-0 dry-run before 2D.3 ships **[dry-run]**

Mirror the 2C.2.5 calibration discipline. **Recommendation:** ship `scripts/dry_run_fuzzy_tier.py` as Phase 2D.2.5 before 2D.3. The dry-run output answers Pushback 5 (corroboration rate) AND validates threshold choices BEFORE 2D.3 commits to them.

### E.1 — Cron schedule swap (FL 02:00 / Kalshi 02:15) **[doc + railway.toml — separate PR]**

Per Pushback 1 (rev1). Currently Kalshi runs first; under that ordering Kalshi tennis lookups find no fresh FL fixtures → corroboration rate ~2-5%. **Recommendation: swap.** Lands as a small `railway.toml` PR ahead of 2D.1; benefits 2C.3 too (alias-tier corroboration sees fresher fixtures). Independent of 2D ship timing.

### E.2 — Pre-2E re-resolve mechanism **[doc]**

Per Pushback 1 (rev1). The runner's existing `fixture_id IS NULL` filter naturally re-tries no_match records on every cron pass — no code change needed. Cost: `resolution_log` row accretion (~42 rows per stuck record over 14 days, ~42KB). Documented; deferred to Phase 2E for proper handling.

### E.3 — Multi-interpretation candidate surname index **[2D.1]**

Per Pushback 2 case 3 (the "Bautista" → "Roberto Bautista Agut" case). Candidate names index under MULTIPLE surname interpretations:
- Default: last token (`"agut"`)
- Compound: last-2-tokens (`"bautista agut"`)
- Middle-as-surname: token[-2] alone (`"bautista"`)

For 24,400 candidates × 3 interpretations = ~73,000 keys in `_by_sport_surname`. Memory ~10MB resident; fine.

**Recommendation:** YES, expand the index. This is an addition to `CandidateIndex` in 2C — needs to ship in 2D.1 alongside the compound fallback. The provider-side fallback (A.1) and candidate-side interpretation (E.3) are complementary: E.3 ensures the candidate is reachable under multiple surname interpretations, A.1 lets the provider try multiple surname interpretations of its own input.

### E.4 — Single-token provider name handling ("Carlos") **[2D.2]**

Per Pushback 2 case 2. Single-token provider input is structurally ambiguous (could be first or last name). Trying both interpretations and taking the most-frequent canonical-name token would auto-apply on first-name-as-surname collisions in pools with many "Carlos" candidates.

**Recommendation:** stay no_match for 2D. Document as deliberate non-goal. If day-7 review shows non-trivial single-token-provider volume, follow-up with a "surname-or-first-name index" enhancement.

### E.5 — Corroboration window for 2D **[doc]**

Same 30-min drift as 2C's strict tier and alias tier. Tightening to 5-min would reduce FALSE corroborations (kickoff-coincidence between unrelated games) but day-0 corroboration rate is already low; tightening makes it worse.

**Recommendation:** keep 30-min drift, same as 2C.

### E.6 — Tennis-specific prop suffixes leaking into anchor_failed **[separate PR — 2C.2.6 follow-up]**

**Added in rev2.** The 2D.2.5 dry-run revealed records like `"Alexander Bublik: Total Games"` reaching `anchor_failed` instead of `extraction_skipped`. Tennis sub-market suffixes weren't in the 2C.2.6 suffix list (which targeted soccer/basketball patterns).

**Tennis additions to `_KALSHI_PROP_TITLE_SUFFIXES`:**
```python
"Total Games"
"Set Winner"
"Match Winner"
# Note: "Game Spread" already in the list (added in 2C.2.6 for soccer)
```

**Recommendation:** ship as a tiny follow-up PR (same shape as 2C.1, 2C.2.6) BEFORE 2D.3. Reduces fuzzy-tier load by catching ~10-20 records/cron upstream. Operator extends the list further when post-2D.4 audit shows new suffixes climbing in `anchor_failed` for `_KALSHI_PROP_TITLE_SUFFIXES` candidates.

This is independent of 2D.3; can ship in parallel with the corroboration investigation (E.8).

### E.7 — Single-character / common-surname problem **[superseded by §E.10 / 2D.6 in rev3]**

**Added in rev2; superseded in rev3.** Rev3 splits this question into two follow-ups: §E.10 (2D.6) covers the surname-collision routing pattern broadly; §E.11 (2D.7) covers the multi-token A.rev2 filter that was originally the recommendation here. Both deferred to post-2D.4 per the rev3 calibration update at the top of this doc. Content below is preserved for historical context.

The 2D.2.5 dry-run highlighted Asian-naming-convention surnames that are short and common — `"Ng"`, `"Hu"`, `"Li"`, `"Choo"` (4-char). These produce many same-surname candidates in the index, leading to surname-collision → review_queue routing.

The single-token provider case (e.g., provider sends just `"Park"`) is genuinely ambiguous and must stay in review queue.

The multi-token case (e.g., `"Junfeng Hu"`) IS discriminable via initial-expansion. **Per A.rev2 above**, the per-candidate filter solves this: when the provider has a discriminating remainder, the matcher can pick the right candidate among same-surname colliders.

**Recommendation:** ship the A.rev2 per-candidate initial-expansion filter as part of 2D.3. Re-run the 2D.2.5 dry-run after 2D.3 ships to measure the lift; if multi-token-discriminable cases were a significant slice of the 165 anchor_failed records, the lift will be visible in the next bucket distribution.

### E.8 — Corroboration-rate investigation **[separate runbook — pre-2D.3]**

**Added in rev2. Runbook shipped as `scripts/investigate_corroboration_gap.sql` in PR #103.** The 1.5% rate is unexplained. Three queries the operator runs against production before 2D.3 locks; the results inform whether to ship 2D.3 as-is or revise further.

```bash
psql "$DATABASE_URL" -f scripts/investigate_corroboration_gap.sql
```

The runbook includes the queries below plus an interpretation guide that maps Q1+Q2+Q3 outputs to one of three 2D.3 ship paths (Path A tournament gap / Path B kickoff misalignment / Path C genuinely 1.5%). Operators copy outputs into the 2D.3 PR description as the calibration source-of-record.

**Query 1 — Tournament overlap.** Does FL ingest the same tennis tournaments Kalshi covers?

```sql
-- Top 20 Kalshi tennis series_tickers (Kalshi's competition signal)
SELECT series_ticker, COUNT(*)
FROM sp.kalshi_markets
WHERE raw_payload->>'_sport' = 'Tennis'
  AND fixture_id IS NULL
GROUP BY 1 ORDER BY 2 DESC LIMIT 20;

-- Top 20 FL tennis tournaments (FL's competition signal)
SELECT raw_payload->'tournament_stage'->>'NAME' AS tournament,
       COUNT(*)
FROM sp.fl_events
WHERE sport_id = (SELECT id FROM sp.sports WHERE code = 'tennis')
GROUP BY 1 ORDER BY 2 DESC LIMIT 20;
```

If the two lists are largely disjoint, FL doesn't have the fixtures Kalshi expects. Possible cause: FL's `DEFAULT_FL_SPORT_IDS=[2]` covers ATP/WTA but not Challenger/ITF tournaments that Kalshi may include.

**Query 2 — Kickoff alignment.** For a known shared tennis fixture (find one manually first via title fuzzy-match), compare timestamps:

```sql
-- Sample some recent same-day Kalshi tennis records
SELECT ticker, raw_payload->>'title' AS title,
       raw_payload->>'_kickoff_dt' AS kickoff
FROM sp.kalshi_markets
WHERE raw_payload->>'_sport' = 'Tennis'
  AND fixture_id IS NULL
  AND last_seen_at > NOW() - INTERVAL '24 hours'
ORDER BY last_seen_at DESC LIMIT 10;

-- For one of those (with player surnames matching FL's), compare:
SELECT 'kalshi' AS provider, raw_payload->>'_kickoff_dt' AS kickoff
FROM sp.kalshi_markets WHERE ticker = '<paste_ticker_here>'
UNION ALL
SELECT 'fl', to_timestamp((raw_payload->>'START_TIME')::int)::text
FROM sp.fl_events WHERE fl_event_id = '<paste_fl_id_here>';
```

If timestamps differ by more than 30 min, `find_fixture`'s drift window misses them.

**Query 3 — Drift window appropriateness.** Tennis matches frequently get rescheduled by hours due to weather/preceding-match overruns. Recompute corroboration rate at a wider drift:

```python
# In a one-off Python script (not the dry-run script):
from resolver.fixtures import find_fixture
# For each anchored 2D candidate pair, try drift_sec=60*60 (1 hour)
# instead of the default 30*60. Measure how many additional pairs
# corroborate.
```

If a 60-min window lifts the rate to ≥20%, that's the fix — but it adds FP risk for tournaments that legitimately have multiple matches per hour. Trade-off needs measurement before locking.

**Recommendation:** operator runs Q1 and Q2 before locking 2D.3. Q3 is a follow-up if Q1 and Q2 confirm the data IS aligned and drift is the gap. **Don't lock 2D.3 thresholds until Q1 + Q2 results are in.** If FL has wildly different tennis coverage than Kalshi, no amount of matcher tuning fixes that — Phase 2D.5 would need to expand FL's `DEFAULT_FL_SPORT_IDS` or add Challenger/ITF tournaments.

#### Investigation outcome — Path B selected (PR #103 → 2D.2.8)

Operator ran the runbook against production. Results:

| Query | Result | Interpretation |
|---|---|---|
| Q1 — tournament overlap | **100%** | Tournament gap ruled out (Path A invalidated). |
| Q2 — kickoff alignment | median 30, max 30 — pile-up at the 30-min filter edge | Many same-fixture pairs sit at 31–60 min offsets and are silently rejected by `find_fixture`. |
| Q3 — drift band lift | **85% at ±30min → 100% at ±60min (+15pp)**, mean fixture count 9.37 → 17.90 (~2× candidates) | Widening drift recovers most of the missing corroboration without unbounded FP risk (the 2× candidate growth is bounded). |

**Decision: Path B — widen `KICKOFF_DRIFT_SEC` for the fuzzy tier ONLY (30 → 60 min).** Per-tier configurable; strict tier (`resolver/matcher.py`) and alias tier (`resolver/alias_tier/matcher.py`) keep their 30-min window because their tighter anchor signals (exact alias hits) don't need slack. Fuzzy tier's wider window matches its looser confidence model — corroboration is a 0.30 bonus, not load-bearing — so the extra candidate breadth is safe.

**Shipped as 2D.2.8 (small calibration PR ahead of 2D.3),** mirroring the 2C.2.5 → 2C.2.7 → 2C.3 calibration discipline. After 2D.2.8 merges, operator re-runs `make dry-run-fuzzy-tier ARGS="--provider kalshi --sport-code tennis --limit 600 --show-examples 5"` and reports the new corroboration rate. The actual lift becomes the calibration source-of-record for 2D.3.

**Forward-looking:** after 2D.3 ships, repeat the corroboration investigation against persisted 2D fuzzy-tier data (replaces the current team-sport proxy with tennis-specific numbers). Tracked as a 2D.4 review item.

#### Investigation re-run — measured lift (2D.2.8 dry-run, rev3)

After the per-tier drift widening shipped in PR #104, the operator re-ran `make dry-run-fuzzy-tier ARGS="--provider kalshi --sport-code tennis --limit 600 --show-examples 5"`. Result: **corroboration rose from 1.5% to 2.7% (+1.2pp).** Smaller than Q3's +15pp availability lift predicted.

The gap between Q3's prediction and the dry-run measurement is the **team-name-matching constraint**: Q3 measured fixtures-in-window, but corroboration also requires the fixture's home/away team_ids to match the candidate selection. Most fixtures in the wider drift band are different matches happening at adjacent times.

Structural ceiling identified: **2D's corroboration looks at `sp.fixtures` populated by strict-tier resolution.** Where FL's strict-tier resolution missed (alias coverage gap on FL's side), there's no fixture row to corroborate against. No amount of 2D matcher tuning lifts past this ceiling — Phase 2D.5 (FL alias coverage expansion) is the relevant lever, not threshold/weight tuning. See §E.9.

### E.9 — FL alias coverage expansion **[2D.5 — post-2D.4]**

**Scope:** the ~171 anchor_failed records/cron + the ~205 unaccounted records the dry-run shows go past the matcher entirely (no candidate scores above any tier's anchor floor) reflect long-tail player names not in `sp.team_aliases`. Two sources contribute:

1. **FL doesn't ingest the Challenger/ITF tournament rounds** that Kalshi prices. Q1 (Investigation §E.8) showed top-20 overlap is 100% but that's a head-of-distribution measurement; the long tail covers tournaments outside `DEFAULT_FL_SPORT_IDS=[2]`.
2. **FL ingests the matches but provider-side and FL-side player surnames don't normalize identically.** "Auger-Aliassime" / "Auger Aliassime" / "Auger" — alias-tier handles 2C-style cases, but tennis-specific compound surnames slip through.

**Approach (proposed, refined in 2D.5 PR):**
- Sample 200 anchor_failed records, classify by failure mode (FL missing vs alias gap).
- For FL-missing: expand `DEFAULT_FL_SPORT_IDS` and/or add Challenger/ITF tournament codes to ingestion config.
- For alias-gap: extend `sp.team_aliases` seed for the top-N tennis players via FL's player roster API.

**Why post-2D.4:** the day-7 review data tells us which failure mode dominates. Premature expansion adds operational load without measurable lift.

### E.10 — Asian-name handling **[2D.6 — post-2D.4]**

**Scope:** single-character / two-character surnames common in Asian naming conventions ("Hu", "Ng", "Li", "Choo"). The current 2D matcher's surname-anchor path discriminates poorly when:

1. The provider sends just the surname ("Hu") and 5+ candidates share it ("Junfeng Hu", "Zhizhen Hu", etc.).
2. Cross-team collision detection routes these to review_queue (correct behavior — operator approves).
3. Single-token providers stay in review_queue regardless of A.rev2 (E.11).

**Approach (proposed, refined in 2D.6 PR):**
- Add a country-of-origin disambiguation layer: if Kalshi's `_kickoff_dt` correlates with an FL fixture where one player is from a known origin country (FL provides nationality), bump that candidate.
- Threshold: only fires for surnames ≤ 2 chars where ≥ 3 candidates collide. Conservative scope — don't generalize to all surnames.

**Why post-2D.4:** sample size on Asian-name pathology is small. Day-7 review data tells us if the pattern is operationally relevant or rare-edge-case.

### E.11 — A.rev2 per-candidate initial-expansion filter **[2D.7 — post-2D.4]**

**Scope:** the per-candidate initial-expansion filter in `_find_personal_match` that filters candidates BEFORE collision detection. Discriminates "Junfeng Hu" from "Zhizhen Hu" for multi-token providers; doesn't help single-token "Park" alone (still review_queue).

**Why deferred from 2D.3 (rev3 decision):** the 2D.2.8 dry-run measurement shows 2D's primary value is the review queue (~150/cron), not auto-apply (~2-3/cron). A.rev2 helps disambiguation on the auto-apply path, which is small leverage. The Junfeng Hu / Zhizhen Hu case currently routes to review_queue via cross-team collision detection — not broken, just imperfect.

**Approach (per the rev2 design — unchanged in scope, just unsequenced):**
- Patch `_find_personal_match` so each candidate is initial-expansion-filtered against the provider's tokens before collision detection runs.
- Add 4-6 unit tests for the discrimination cases.
- Run `dry_run_fuzzy_tier.py` post-patch to measure how many additional auto-applies the filter recovers.

**Why post-2D.4:** 2D.4 day-7 review tells us if the auto-apply ceiling is operationally limiting. If reviewers are happily processing the 150 review_queue/cron and auto-apply latency isn't a bottleneck, A.rev2 stays a nice-to-have. If reviewers are bottlenecked on Junfeng/Zhizhen-shaped collisions specifically, A.rev2 becomes a higher-priority follow-up.

---

## Sign-off checklist (rev3)

Status: rev1 + rev2 calibration items (cron swap E.1, 2D.1 primitives, 2D.2 matcher, 2D.2.5 dry-run, 2D.2.6 tennis suffixes, 2D.2.7 investigation runbook, 2D.2.8 per-tier drift widening) are SHIPPED. Remaining items below are rev3 ship items + post-2D.4 follow-ups.

**Rev3 framework lock:**
- [ ] **Option C1 (locked, primary 2D framing)** — 2D ships as a review-queue tool. Per 2D.2.8 dry-run measurement (post-drift-widening), ~151 review_queue / ~2-3 auto_apply per Kalshi tennis cron. Approved or counter-proposed.
- [ ] **Day-0 prediction (final, rev3)** — ~10-11% combined Kalshi auto-apply rate after 2D.3 ships. ~150 fuzzy review-queue records/cron, ~2-3 fuzzy auto-applies/cron. Operator review load ~67 min/day at ~10 sec/record. Approved or revisit.
- [ ] **A.rev2 deferred to 2D.7.** Per-candidate initial-expansion filter is real value but not in 2D.3 scope (review queue is 2D's primary output, not auto-apply; A.rev2's leverage is on the small auto-apply path). Approved or counter-proposed.
- [ ] **Structural finding documented (§E.8 re-run outcome).** 2D's corroboration ceiling is gated by upstream alias coverage (FL strict-tier resolution populates `sp.fixtures`); no 2D matcher tuning lifts past it. 2D.5 is the relevant lever. Acknowledged.

**Shipped pre-2D.3 calibration items (rev2 carry-forward, marked done):**
- [x] **2D.2.6 / E.6** — Tennis-specific prop suffix extension (PR #102). Shipped.
- [x] **2D.2.7 / E.8 runbook** — Corroboration-gap investigation runbook (PR #103). Shipped.
- [x] **2D.2.8 / E.8 outcome** — Per-tier drift widening (PR #104). Shipped. Dry-run re-run measured 2.7% corroboration (was 1.5%); rev3 prediction locked from this number.

**2D.3 scope (rev3, tightened):**
- [ ] **TieredMatcher 3-tier extension** — strict → alias → fuzzy → no_match. Wires already-shipped 2D.2 matcher into orchestration.
- [ ] **Runner integration** — per-tier counters in `sp.resolver_runs.extra`.
- [ ] **`sp.team_aliases` write-back** — fuzzy-tier auto-applies write back as `source='fuzzy_tier'` (compounds same way 2C.3 alias-tier write-back does).
- [ ] **Triple-tier logging** — `resolution_log` extends to up to 3 rows per record (strict no_match + alias no_match/result + fuzzy result).
- [ ] **Calibration anchor test from 2D.2 stays** — Kecmanovic case scores 0.70 without corroboration, 1.00 with. No matcher behavioral change.
- [ ] **DEPLOYMENT.md updates** — document Option C1 framing as primary 2D output (review queue is the headline, ~2-3 auto_apply expected).

**Post-2D.4 follow-up PRs (tracked, not in 2D.3):**
- [ ] **2D.5 / E.9** — FL alias coverage expansion for the ~170 anchor_failed records/cron long-tail. See §E.9 above.
- [ ] **2D.6 / E.10** — Asian-name single-character / two-character surname handling. See §E.10 above.
- [ ] **2D.7 / E.11** — A.rev2 per-candidate initial-expansion filter. See §E.11 above.

**Items already approved in rev1/rev2 (carry forward unchanged):**
- A, A.1, A.3, B, B.1, C, C.1, C.2, D.1, D.2, E.1 (shipped), E.2, E.3 (shipped in 2D.1), E.4, E.5, E.7 (now subsumed by 2D.6 / E.10).

**Schema:**
- [ ] Schema-zero approach (no new tables, no new columns). Approved.

**Negative space (rev1 carry-forward):**
- [ ] Pure kickoff-coincidence matching stays out of bounds. Approved.
- [ ] No team / fixture creation in fuzzy tier. Approved.
- [ ] No recovery of `signal_extraction_skipped` records. Approved.
- [ ] No ratio-tuning for individual sports beyond initial expansion. Approved.
- [ ] Test plan: real call-path integration tests as primary surface, static guards as backstop. Approved.

After rev3 sign-off, 2D ships in this final order:

0. **E.1 cron swap** — already shipped (PR #97).
1. **2D.1 — pure-Python primitives** — already shipped (PR #99).
2. **2D.2 — FuzzyTierMatcher** — already shipped (PR #100).
3. **2D.2.5 — dry-run script** — already shipped (PR #101).
4. **2D.2.6 — Tennis-specific suffix list extension** — already shipped (PR #102).
5. **2D.2.7 — Corroboration investigation runbook** — already shipped (PR #103). Investigation outcome: Path B (kickoff misalignment) — see §E.8 outcome table above.
6. **2D.2.8 — Per-tier drift widening** — already shipped (PR #104). Dry-run re-run lifted corroboration 1.5% → 2.7%; that number locks the rev3 calibration.
7. **PHASE_2D_DESIGN.md rev3** — this PR. Doc-only. Locks Option C1 as primary, defers A.rev2 to 2D.7, tracks 2D.5/2D.6/2D.7 as post-2D.4 follow-ups.
8. **2D.3 — TieredMatcher 3-tier extension + runner integration + write-back + triple-tier logging.** Pure infrastructure; no matcher behavioral changes. Wires 2D.2 (already shipped) into the runner. DEPLOYMENT.md updates document the Option C1 framing.
9. **2D.4 — Day-7 review.** Same cadence as 2B and 2C. Adjust thresholds if FP rate exceeds halt criteria. Includes re-running the §E.8 corroboration investigation against persisted 2D fuzzy-tier data (replaces the team-sport proxy with tennis-specific numbers). Decision point for 2D.5 / 2D.6 / 2D.7 prioritization.
10. **2D.5 — FL alias coverage expansion** — post-2D.4. See §E.9.
11. **2D.6 — Asian-name handling** — post-2D.4. See §E.10.
12. **2D.7 — A.rev2 per-candidate initial-expansion filter** — post-2D.4. See §E.11.

---

## What this PR is NOT

- Not code. No `resolver/fuzzy_tier/`, no migration, no test changes. Implementation gated on sign-off.
- Not a final lock. Push back on any of the recommendations and the doc gets revised before 2D.1 ships.
- Not in conflict with Phase 2C.4. Senior-team disambiguation (2C.4) ships independently per the day-7 review data — its scope (review-queue reduction in team sports via "II"/"U19"/"B" suffix detection) doesn't overlap with 2D's tennis recovery + no-anchor fallback.
