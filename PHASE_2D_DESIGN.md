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

### Answer (definitive): Path-aware composable signals, higher anchor floor than 2C.

```
Personal-name path (tennis recovery, post-A + A.1):
  surname_anchor (binary, after compound-fallback retries):  +0.40
  remainder_token_set_quality (linear, ≥0.85 threshold):     up to +0.20
    OR
  initial_expansion (binary, all short tokens prefix-match):  +0.30
  cross_provider_corroboration (existing fixture):            +0.20
  kickoff_drift_tightness (≤5 min):                           +0.10

Team-name path (no-anchor fallback, post-B):
  fuzz_ratio_anchor (binary, ratio ≥0.85):                    +0.40
  fuzz_ratio_quality (linear, 0.85→+0.10, 1.0→+0.30):         up to +0.30
  cross_provider_corroboration (REQUIRED for auto-apply):     +0.30
                                                               (without: max ~0.70)
```

**Maximum personal-path with corroboration**: 0.40 + 0.30 (initial expansion) + 0.20 + 0.10 = **1.00**.

**Maximum personal-path without corroboration**: 0.40 + 0.30 + 0.10 = **0.80** → review_queue.

**Maximum team-path with corroboration**: 0.40 + 0.30 + 0.30 = **1.00**.

**Maximum team-path without corroboration**: 0.40 + 0.30 = **0.70** → review_queue lower bound (exclusive of auto-apply).

The asymmetry between personal and team paths reflects the different signal strengths:
- Personal path's surname anchor + initial expansion is structurally precise (low FP risk).
- Team path's fuzzy character-level ratio is statistically noisier — corroboration carries more of the safety margin.

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

**Tennis recovery (Gap 1):** structural pattern is well-understood; algorithm is deterministic.
- Conservative: 70% of 555/run = ~390 records resolved per Kalshi cron, of which ~50% have corroboration (other side already resolved via FL strict) → ~195 auto_apply, ~195 review_queue.
- Optimistic: 85% of 555/run = ~470 resolved, ~280 auto_apply, ~190 review_queue.

**Team-sport residuals (Gap 2):** much smaller volume; uncertain how many will fuzzy-anchor at all.
- Conservative: ~20 / Kalshi cron, ~30 / FL cron → maybe 50 auto_apply per day across both providers, plus 50-100 review_queue.
- Optimistic: ~50 / Kalshi cron, ~80 / FL cron → 200 auto_apply per day, plus 100-200 review_queue.

**Total post-2D projection** (delta from 2C.3 baseline):

| Provider | 2C.3 baseline (cron) | Post-2D low      | Post-2D high      |
|----------|----------------------|------------------|-------------------|
| Kalshi   | ~388 auto-apply      | ~580             | ~660              |
| FL       | (similar deltas)     | (+20-80)         | (+80-200)         |
| Total /day | ~16,500            | ~17,000          | ~17,500           |

Tennis review-queue volume rises by ~190-280/day for the 14-day post-2D window. The C.1 alert-threshold relaxation (1500) absorbs this.

**Caveats on the prediction:**
- Order-of-operations: Kalshi tennis records arriving BEFORE the corresponding FL pass will get `no_match(fuzzy_no_existing_fixture)` and pick up on the next pass. So per-pass auto-apply will be lower than steady-state.
- Initial expansion compatibility for asian-name conventions has not been audited yet. If "Naomi Osaka" / "Osaka N." style records are common in production (likely for women's tennis), the algorithm handles them — but volumes are unknown.
- The 555/run figure is from one Kalshi pass. FL tennis volumes are different (probably higher absolute count, but FL tennis already auto-applies via strict tier per the 2A.5 baseline).

---

## Open questions awaiting sign-off

### A.1 — Compound surname fallback retry order

Three retries proposed: (last token), (last two tokens), (second-to-last token). Should we exhaust ALL n suffix combinations? For 4-token names ("Lopez Garcia Sanz Mendez") that's 3 retries. Recommendation: stop at 3 retries (last 1, last 2, last 3 tokens). Beyond that = diminishing returns + cost of false positives.

### A.2 — Initial expansion case-sensitivity

The structural normalize lowercases; both sides hit the rule with same case. **No additional handling needed.** Documenting for clarity.

### A.3 — Multi-initial cases ("J.J. Watt")

`"j.j. watt"` after normalize = ["j", "j", "watt"]. P_short=["j","j"]. If candidate has ["jj", "watt"] (single bigram), C_short=["watt"] which doesn't apply, P_short=["j","j"] each prefix-checks "watt"? "watt".startswith("j") → ✗. Recommendation: this case stays in review queue. Real-world impact: rare. Document as a known limitation.

### B.1 — Team-path fuzz.ratio threshold

0.85 chosen by analogy to alias-tier auto-apply. This is character-level Levenshtein-derived; the empirical FP rate at this threshold isn't known. **Recommendation:** ship at 0.85, watch the day-7 review for false positives, tighten to 0.90 if needed. Same calibration discipline as 2C threshold tuning.

### C.1 — Initial-expansion contribution magnitude

`+0.30` chosen so personal-path-with-corroboration hits 1.00 exactly. Could be `+0.25` (more conservative — auto-apply requires both initial expansion AND corroboration AND tight kickoff). Could be `+0.35` (more permissive — auto-apply on initial expansion + corroboration alone, without kickoff tightness bonus).

**Recommendation:** ship at +0.30 (the natural 0.40+0.30+0.20+0.10=1.00 decomposition). The day-7 review surfaces whether that's too aggressive. Adjusting one constant in subsequent PR is cheap.

### C.2 — Personal-path token-set as alternative to initial expansion

The model proposes initial expansion OR remainder token-set, not both. A perfect remainder token-set match (rare for tennis) gives +0.20 max; initial expansion gives +0.30. So initial expansion is preferred when both apply.

But what if both signals fire AND agree? E.g., "M Kecmanovic" / "M Kecmanovic" — initial AND remainder both match exactly. Recommendation: take the max of the two. No double-counting.

### D.1 — Resolver version stamp

`fuzzy@2d.0` for the matcher, `tiered@2d.0` for the orchestrator (since adding a tier is a semantic change to TieredMatcher). Per-decision rows on `resolution_log` keep tier-specific stamps; `sp.resolver_runs.resolver_version` becomes `tiered@2d.0`.

### D.2 — Day-0 dry-run before 2D.3 ships

Mirror the 2C.2.5 calibration discipline. **Recommendation:** ship `scripts/dry_run_fuzzy_tier.py` as Phase 2D.2.5 before 2D.3. Output is the threshold-tuning input; if the bucket distribution looks bad, we revisit 0.85 / 0.30 / 0.30 numbers before committing the matcher.

---

## Sign-off checklist

Before implementation begins:

**Algorithm:**
- [ ] **A** — Initial expansion algorithm (structural prefix-match for short tokens). Approved.
- [ ] **A.1** — Compound surname fallback (3 retry levels). Approved.
- [ ] **A.2** — Case-sensitivity is post-normalization (no extra handling). Approved.
- [ ] **A.3** — Multi-initial cases stay in review queue. Approved as known limitation.
- [ ] **B** — Team-path character-level fuzzy with corroboration-required-for-auto-apply. Approved.
- [ ] **B.1** — fuzz.ratio threshold = 0.85. Approved or counter-proposed.

**Confidence model:**
- [ ] **C** — Personal: 0.40 anchor + 0.30 initial-expansion (or 0.20 remainder ratio) + 0.20 corroboration + 0.10 kickoff-tight = 1.00 max.
- [ ] **C** — Team: 0.40 anchor + 0.30 fuzz-quality + 0.30 corroboration = 1.00 max (no kickoff-tight bonus).
- [ ] **C.1** — Initial-expansion contribution = +0.30. Approved or counter-proposed.
- [ ] **C.2** — Initial expansion vs remainder: take max, no double-count. Approved.

**Schema:**
- [ ] Schema-zero approach (no new tables, no new columns). Approved.

**Negative space:**
- [ ] Pure kickoff-coincidence matching stays out of bounds. Approved.
- [ ] No team / fixture creation in fuzzy tier. Approved.
- [ ] No recovery of `signal_extraction_skipped` records. Approved.
- [ ] No ratio-tuning for individual sports beyond initial expansion. Approved.

**Process:**
- [ ] **D.1** — Per-tier `RESOLVER_VERSION` (`fuzzy@2d.0`) + run-level `tiered@2d.0`. Approved.
- [ ] **D.2** — Phase 2D.2.5 dry-run script before 2D.3 matcher. Approved.
- [ ] Test plan: real call-path integration tests as primary surface, static guards as backstop. Approved.

After sign-off, 2D ships in this order — each step is its own PR:

1. **2D.1 — `initials_compatible` + compound-surname fallback (pure-Python).** Single-file additions to `resolver/fuzzy_tier/initial_expansion.py`. ~10 unit tests. No DB integration.
2. **2D.2 — `FuzzyTierMatcher`.** Composes 2D.1 + 2C `CandidateIndex` + cross-provider corroboration. ~15 real call-path tests with mocked DB session.
3. **2D.2.5 — Dry-run script.** Calibration data from production (pattern matches 2C.2.5).
4. **2D.3 — `TieredMatcher` extends to 3 tiers + runner integration.** Per-tier counters in `sp.resolver_runs.extra`. DEPLOYMENT.md updates.
5. **2D.4 — Day-7 review.** Same cadence as 2B and 2C. Adjust thresholds if FP rate exceeds halt criteria.

---

## What this PR is NOT

- Not code. No `resolver/fuzzy_tier/`, no migration, no test changes. Implementation gated on sign-off.
- Not a final lock. Push back on any of the recommendations and the doc gets revised before 2D.1 ships.
- Not in conflict with Phase 2C.4. Senior-team disambiguation (2C.4) ships independently per the day-7 review data — its scope (review-queue reduction in team sports via "II"/"U19"/"B" suffix detection) doesn't overlap with 2D's tennis recovery + no-anchor fallback.
