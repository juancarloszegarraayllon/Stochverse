# Phase 2D Design — Fuzzy Tier (initial expansion + no-anchor fallback)

Status: design doc, awaiting review. Implementation begins only after sign-off.

Reference: SP Architecture v1.4 §7 (Resolution Layer) and §13.2 (locked decisions). Builds on Phase 2C's alias tier (`PHASE_2C_DESIGN.md`) and the production day-0 data from PR #95 (2C.3 first cron pass).

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

## Day-0 prediction (with stated uncertainty)

Pushback 4 (rev1): original draft's math didn't reconcile. `555 × 0.70 × 0.50 = 194`; `555 × 0.85 × 0.50 = 236`. The 280 figure implied a corroboration rate higher than 50%. Rebuilt below with clean arithmetic.

Pushback 5 (rev1): the 50% corroboration assumption was a guess. **The 2C dry-run measured corroboration at 6 / 247 ≈ 2.4%** — a 20× gap from what 2D was assuming. This is the most material correction in rev1.

### What's the actual corroboration rate?

The 2D corroboration LOOKUP is structurally identical to 2C's: `find_fixture(home_id, away_id, kickoff_at, drift_sec=30*60)` against `sp.fixtures`, then the swapped (away, home) orientation. Same drift window. **Same code path.** The rate should be the same as 2C's measurement — there's no reason 2D would find more corroboration than 2C did with the same lookup.

But the 2C measurement was specifically on tennis (the `deferred_to_2d` bucket). 2D's tennis recovery WILL hit a higher corroboration rate post-cron-swap, because:
- Pre-cron-swap (current 02:00 Kalshi → 02:15 FL): FL hasn't run yet; Kalshi tennis lookups against `sp.fixtures` find no fixtures because no recent FL pass has created them. **Effective corroboration rate: ~2-5%.**
- Post-cron-swap (FL 02:00 → Kalshi 02:15): FL has finished its pass 15 minutes earlier and strict-tier-resolved its tennis events. Kalshi looks at fresh `sp.fixtures` → corroboration rate jumps. **Estimated rate: 20-40%** (still bounded by how much of the FL tennis corpus actually has paired Kalshi records at the same kickoff).

The 20-40% range is itself a guess. **The 2D.2.5 dry-run is the calibration gate** — if the dry-run shows a different rate, day-0 prediction adjusts accordingly. Phase 2D.3 doesn't ship until the 2D.2.5 numbers come in.

### Recomputed numbers

**Tennis recovery (Gap 1):**

|                       | Records resolving (anchor passes) | Of those, with corroboration | Auto-apply | Review-queue |
|-----------------------|------------------------------------|-------------------------------|------------|--------------|
| Conservative          | 555 × 70% = 388                    | 20%                           | 78         | 311          |
| Mid                   | 555 × 78% = 432                    | 30%                           | 130        | 302          |
| Optimistic            | 555 × 85% = 471                    | 40%                           | 188        | 283          |

**Team-sport residuals (Gap 2):** uncertain how many even anchor; volumes are small. Conservative range with corroboration-required-for-auto-apply:
- Conservative: ~30 anchor, 25% corroboration → 8 auto-apply, 22 review.
- Optimistic: ~150 anchor, 40% corroboration → 60 auto-apply, 90 review.

**Total post-2D projection** (per Kalshi cron pass, delta from 2C.3 baseline):

| Scenario      | Baseline (2C.3) | Tennis auto | Team auto | Tennis review | Team review | Post-2D auto |
|---------------|-----------------|-------------|-----------|---------------|-------------|---------------|
| Conservative  | ~388            | +78         | +8        | +311          | +22         | ~474          |
| Mid           | ~388            | +130        | +30       | +302          | +50         | ~548          |
| Optimistic    | ~388            | +188        | +60       | +283          | +90         | ~636          |

Review-queue volume rises by ~330-370/day across providers. The 2C.1 alert-threshold relaxation (1,500) absorbs this with headroom.

**Caveats:**

1. **Pre-2D.2.5 dry-run: the corroboration rate is the largest unknown.** If the dry-run shows ~5% (closer to 2C's measurement), the auto-apply numbers drop to the conservative end of the table. If ~50% (above the mid-range), they could exceed the optimistic. **Don't lock 2D.3 until the dry-run confirms.**
2. **Cron swap (E.1) is on the critical path.** Without the FL→Kalshi swap, day-0 corroboration rate stays at the ~5% pre-swap baseline. Recommend swapping in a small `railway.toml` PR before 2D.1.
3. **Order-of-operations decay across passes (E.2).** Records that don't auto-apply on pass 1 due to missing corroboration mostly resolve on pass 2 (after FL has run). Steady-state auto-apply is higher than first-pass auto-apply by maybe 10-20%.
4. **Initial-expansion compatibility for non-Western names** has not been audited. Asian conventions (Naomi Osaka / Osaka N.) work mechanically per the algorithm; volume of cases unknown.
5. **555 records/run is from one Kalshi pass** measured immediately after PR #95 merged. The FL tennis no_match volume is different (FL tennis mostly auto-applies via strict tier). 2D's FL tennis recovery is incremental on top of FL's already-78% baseline.

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

---

## Sign-off checklist

Before implementation begins. Tags: `[2D.1]` blocks 2D.1 ship; `[2D.2]` blocks 2D.2; `[2D.3]` blocks 2D.3; `[dry-run]` answered by 2D.2.5 dry-run; `[doc]` documentation-only.

**Algorithm — `[2D.1]` blockers:**
- [ ] **A** — Initial expansion (structural prefix-match for short tokens). Approved.
- [ ] **A.1** — Compound surname fallback, 3 retry levels (last 1, 2, 3 tokens). Approved.
- [ ] **A.3** — Multi-initial cases ("J.J. Watt") stay no_match; documented limitation. Approved.
- [ ] **E.3** — Multi-interpretation candidate surname index (default + compound + middle-as-surname). Approved.

**Algorithm — `[2D.2]` blockers:**
- [ ] **B** — Team-path character-level fuzzy with corroboration-REQUIRED for auto-apply. Approved.
- [ ] **B.1** — `fuzz.ratio()` threshold = 0.85. **[dry-run validates]** Approved (initial; tighten to 0.90 if FP rate exceeds threshold at day-7).
- [ ] **E.4** — Single-token provider name ("Carlos") stays no_match in 2D. Approved as deliberate non-goal.

**Algorithm — `[doc]` only:**
- [ ] **A.2** — Case-sensitivity is post-normalization. Documented; no implementation effect. Approved.

**Confidence model — `[2D.3]` blockers:**
- [ ] **C** — Both paths: 0.40 anchor + 0.30 quality (max of initial-expansion-or-remainder for personal; linear for team) + 0.30 corroboration = 1.00 max. (Pushback 3 rev1: dropped +0.10 kickoff-tight bonus.)
- [ ] **C.1** — Initial-expansion contribution = +0.30. **[dry-run validates]** Approved.
- [ ] **C.2** — Initial expansion vs remainder: take max, no double-count. Approved.

**Process — `[2D.3]` blocker:**
- [ ] **D.1** — Per-tier stamp `fuzzy@2d.0`; orchestrator stamp `tiered@2d.0`. Approved.

**Calibration — `[dry-run]`:**
- [ ] **D.2** — Phase 2D.2.5 dry-run before 2D.3 ships. **Required.**
- [ ] Pushback 5 corroboration rate validated by 2D.2.5 dry-run before locking thresholds. **Required.**

**Operational — separate PRs:**
- [ ] **E.1** — Cron schedule swap (FL 02:00 / Kalshi 02:15). Lands as a small `railway.toml` PR ahead of 2D.1; benefits 2C.3 too. Approved.
- [ ] **E.2** — Pre-2E re-resolve uses existing `fixture_id IS NULL` filter; document `resolution_log` accretion as known issue. Approved.
- [ ] **E.5** — Corroboration window stays at 30-min drift (same as 2C). Approved.

**Schema:**
- [ ] Schema-zero approach (no new tables, no new columns). Approved.

**Negative space:**
- [ ] Pure kickoff-coincidence matching stays out of bounds. Approved.
- [ ] No team / fixture creation in fuzzy tier. Approved.
- [ ] No recovery of `signal_extraction_skipped` records. Approved.
- [ ] No ratio-tuning for individual sports beyond initial expansion. Approved.
- [ ] Test plan: real call-path integration tests as primary surface, static guards as backstop. Approved.

After sign-off, 2D ships in this order — each step is its own PR:

0. **(Pre-2D.1) — Cron swap.** Tiny `railway.toml` PR per E.1. Benefits 2C.3 immediately; sets up 2D's corroboration-rate measurement.
1. **2D.1 — `initials_compatible` + compound-surname fallback + multi-interpretation surname index (pure-Python).** Additions to `resolver/fuzzy_tier/initial_expansion.py` and `resolver/alias_tier/candidates.py`. ~15 unit tests. No DB integration.
2. **2D.2 — `FuzzyTierMatcher`.** Composes 2D.1 + 2C `CandidateIndex` + cross-provider corroboration. ~15 real call-path tests with mocked DB session.
3. **2D.2.5 — Dry-run script.** Calibration data from production (pattern matches 2C.2.5). **Threshold-locking gate** for 2D.3.
4. **2D.3 — `TieredMatcher` extends to 3 tiers + runner integration.** Per-tier counters in `sp.resolver_runs.extra`. DEPLOYMENT.md updates.
5. **2D.4 — Day-7 review.** Same cadence as 2B and 2C. Adjust thresholds if FP rate exceeds halt criteria.

---

## What this PR is NOT

- Not code. No `resolver/fuzzy_tier/`, no migration, no test changes. Implementation gated on sign-off.
- Not a final lock. Push back on any of the recommendations and the doc gets revised before 2D.1 ships.
- Not in conflict with Phase 2C.4. Senior-team disambiguation (2C.4) ships independently per the day-7 review data — its scope (review-queue reduction in team sports via "II"/"U19"/"B" suffix detection) doesn't overlap with 2D's tennis recovery + no-anchor fallback.
