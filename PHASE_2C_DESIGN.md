# Phase 2C Design — Alias Tier (fuzzy + cross-provider corroboration)

Status: design doc, awaiting review. Implementation begins only after sign-off.

Reference: SP Architecture v1.4 §7 (Resolution Layer) and §13.2 (locked decisions). Builds on Phase 2B's strict tier (`PHASE_2B_DESIGN.md`).

---

## Scope

Phase 2C adds a second resolution tier — **alias tier** — that runs after Phase 2B's strict tier rejects a record. Auto-applies at `confidence ≥ 0.85` (architecture default). Records with `0.70 ≤ confidence < 0.85` route to `sp.review_queue` for the Phase 2F admin UI. Records below 0.70 stay `no_match` and reach Phase 2D's fuzzy / cross-provider tier later.

**Out of scope for 2C:**
- Phase 2D — pure name-similarity fuzzy + multi-provider corroboration when neither side can be alias-resolved at all
- Phase 2E — three-loop runner with `LISTEN/NOTIFY` (current cron stays in place)
- Phase 2F — admin review-queue UI
- Phase 2G — resolver diff tooling

The alias tier is **deliberately structural, not statistical**. It exploits one strong observation: whenever the strict tier fails on `alias_resolution_incomplete`, the surname (or canonical-name root) is almost always the same on both sides — what differs is structural shape (initial vs full first name, parenthetical country codes, abbreviation conventions). A tier that anchors on surname + scores around it should recover most of the gap.

---

## Day-0 baseline (the inputs to design 2C against)

Production data after PR #88 cron rolled (cumulative across 2A.5/2A.6/2A.7/2B):

| Metric                       | Kalshi    | FL         |
|------------------------------|-----------|------------|
| records_scanned              | 4,384     | ~18,484    |
| auto_applies (strict@2a.6)   | 350       | 14,408     |
| coverage                     | **8.0%**  | **~78%**   |
| no_match (`alias_resolution_incomplete`) | ~2,500 | ~6,266 |
| signal_extraction_skipped    | (varies)  | (small)    |

**The gap 2C targets: ~8,766 records with valid fixtures behind them but where strict-tier alias resolution fails.** Three known patterns from the day-0 audit:

1. **Tennis name format mismatch** (~600+ records, mostly Kalshi). Kalshi uses `"Miomir Kecmanovic"`; FL alias is `"Kecmanovic M. (Srb)"`. Strict-tier `normalize_name` lowercases both but the token order, parenthetical, and initial-vs-full-name differ — exact alias hit fails.
2. **Long-tail teams in regional/lower-tier leagues** (most of the rest). Same team referenced consistently across both providers but with no entry in `sp.team_aliases` for either provider's spelling. Recovery requires either fuzzy matching against existing teams or cross-provider corroboration.
3. **Player prop markets misclassified as per-fixture** (small but pernicious). Tickers like `KXMLBTB`, `KXMLBHR`, `KXMLBHRR`, `KXNBASTL` aren't in `_OUTRIGHT_SERIES_PREFIXES` so `kalshi_identity.parse_ticker` returns `kind="per_fixture"` and the abbr_block contains a player handle. These reach the matcher and fail on `alias_resolution_incomplete`. **2C must reject these explicitly, not match them.**

---

## Three sharpenings — confirmations

### 1. Confidence thresholds — architecture-locked, no change in 2C

| Source                   | Confidence | Used by                                                  |
|--------------------------|------------|----------------------------------------------------------|
| Strict tier auto-apply   | 0.98       | Phase 2B (strict@2a.6)                                   |
| **Alias tier auto-apply**| **0.85**   | **Phase 2C (this doc)** — architecture default §7.4       |
| Alias tier review        | 0.70–0.84  | Routes to `sp.review_queue`; reviewer accepts or rejects |
| Alias tier no_match      | < 0.70     | No DB write to fixtures; `resolution_log.reason_code='no_match'` |
| Human-verified           | 1.00       | Reserved for review-queue approvals (Phase 2F)           |

The 0.05 gap between strict (0.98) and human-verified (1.00) preserves the distinction. The 0.15 gap between alias auto-apply (0.85) and strict auto-apply (0.98) signals that alias-tier matches are real but materially less certain — useful for downstream consumers (admin UI, replay) that want to filter.

### 2. Tier evaluation order — strict, then alias, then no_match

Per architecture v1.4 §7. Phase 2C inserts into the chain between strict-tier and the (currently terminal) no_match path:

```
extract_signal(raw_payload)
        │
        ▼
    [None?] ── yes ──► signal_extraction_skipped (no resolution_log row)
        │
        no
        ▼
   strict tier (Phase 2B)
        │
        ├── STRICT (auto-apply) ──► resolution_log + provider.fixture_id ←─ Phase 2B
        │
        └── NO_MATCH
                │
                ▼
          alias tier (Phase 2C)        ←─ this doc
                │
                ├── ALIAS auto-apply (≥0.85) ──► resolution_log + provider.fixture_id
                ├── ALIAS review     (0.70–0.84) ──► resolution_log + sp.review_queue
                └── NO_MATCH (<0.70 or rejected) ──► resolution_log only
                       │
                       ▼
                  Phase 2D fuzzy (future)
```

The matcher orchestration lives in `resolver/matcher.py`. Strict tier already returns `MatchResult` with `reason_code` — when it returns `NO_MATCH`, the orchestrator hands the same `FixtureSignal` to the alias tier rather than terminating.

### 3. Alias-tier matches write back to `sp.team_aliases`

A successful alias-tier auto-apply adds a row to `sp.team_aliases`:
- `alias` = the original (raw) provider string
- `alias_normalized` = its normalized form
- `team_id` = the resolved team
- `source = 'alias_tier'`
- `confidence` = the per-match confidence (e.g., 0.91)

Effect: the next strict-tier pass over the same provider record will find an exact alias hit and resolve at 0.98 confidence. The alias tier's recovery work compounds over time — a 7-day parallel-run will produce a much smaller alias-tier workload by day 7 than day 1, because strict tier picked up the slack. This is the architecture's "self-improving" property §7.6.

Alias tier in **review** (0.70–0.84) does NOT write back. Only an explicit reviewer approval in 2F writes the alias with `source='human_curated'` and `confidence=1.0`.

---

## Question A — Fuzzy matching algorithm

### Answer (definitive): Two structural paths — personal-name surname-anchored, team-name whole-string token-set ratio.

The original draft only specified the personal-name path. Pushback 1 surfaced that this leaves non-personal cases unspecified — `"São Paulo FC"` vs `"Sao Paulo"` and `"Bayern München"` vs `"Bayern Munich"` have no surname-vs-given-name structure. This revision splits the problem cleanly.

**Path 1 — Personal names** (sports where the "team" is one human: tennis, MMA, boxing, golf, snooker, darts).

```
1. Pre-normalize each candidate string into a canonical form:
     a. Strip parentheticals: "(Srb)" / "(Q)" / "(JR)" / "(W)" → ""
     b. Strip standalone abbreviated suffixes: "Jr.", "Sr.", "II", "III"
     c. Drop accents (existing _normalize.normalize_name)
     d. Lowercase
     e. Tokenize on whitespace
     f. Detect surname structure:
          - If exactly 2 tokens AND second token is 1-2 chars + optional dot:
              "kecmanovic m" → surname="kecmanovic", initials=["m"]
          - Else if exactly 2 tokens, both > 2 chars:
              "miomir kecmanovic" → surname="kecmanovic" (last token), given_names=["miomir"]
          - Else if 3+ tokens with no parenthetical:
              last token is surname; rest are given names / qualifiers
          - Else: structural detection failed; fall through to whole-string match

2. Match against sp.teams via:
     a. Exact (surname, other-tokens) match against any seeded canonical_name —
        index lookup, microseconds.
     b. If miss, exact surname match + token-set-ratio on other tokens
        between Provider's tokens and the candidate team's normalized_name
        tokens. Threshold: ratio ≥ 0.85.
     c. If multiple candidates pass (b), the alias tier punts to review queue
        with all candidates ranked by score.

3. Surname must match exactly (after accent strip + lowercase).
   No fuzzy-on-surname in 2C — that's Phase 2D fuzzy-tier territory.
```

**Path 2 — Team names** (soccer, basketball, hockey, baseball, football, cricket, volleyball, handball, rugby, aussie rules).

```
1. Pre-normalize:
     a. Drop accents (existing _normalize.normalize_name handles this)
     b. Lowercase, strip punctuation, collapse whitespace
        ("Atlético Tucumán" → "atletico tucuman")
        ("São Paulo FC"     → "sao paulo fc")
        ("Bayern München"   → "bayern munchen")
     c. Tokenize on whitespace

2. Match against sp.teams via:
     a. Exact normalized-name match — strict tier already covers this.
        If we reach Path 2 we know it missed.
     b. Token-set ratio between Provider's token set and each
        candidate team's normalized_name token set (within sport_id).
        Threshold: ratio ≥ 0.92.
     c. If multiple candidates pass, punt to review queue.

3. Threshold (0.92) is HIGHER than the personal-name path (0.85)
   because Path 2 has no anchor token — every match relies on
   whole-string overlap, so we tolerate fewer differences before
   crossing into false-positive territory.

   Token-set ratio at 0.92 catches:
     - Diacritic differences ("munchen" vs "munich") — actually
       collapses to identity after step 1.a.
     - Qualifier suffix dropped ("sao paulo fc" vs "sao paulo")
     - Localized variant ("bayern munchen" vs "bayern munich")
     - City qualifier added/removed ("atletico" vs "atletico tucuman")
   But rejects:
     - "real madrid" vs "atletico madrid" (~0.50)
     - "manchester united" vs "manchester city" (~0.67)
```

**Path discrimination — sport-driven.**

The two paths are selected by the signal's sport. The list of "personal" sport codes is hardcoded in `resolver/alias_tier/normalize.py`:

```python
INDIVIDUAL_SPORT_CODES: frozenset[str] = frozenset({
    "tennis", "mma", "boxing", "golf", "snooker", "darts",
})
```

Hardcoded rather than a `sp.sports.is_individual` column to keep schema-zero (per the Schema Changes section). The list rarely changes — adding a new sport is a 1-line edit + matching `sp.sports` seed entry.

**Why structural detection is sport-driven, not autodetected:**

A string like `"Real Madrid"` is two tokens, both > 2 chars. The personal-name path would (incorrectly) treat `"madrid"` as a surname. Sport context is the cleanest discriminator — soccer is in `_TEAM_SPORTS` (the complement of `INDIVIDUAL_SPORT_CODES`), so we route to Path 2 unambiguously.

The handful of edge cases (e.g., women's tennis where some FL aliases are formatted as `"Williams S. (USA)"` while Kalshi uses `"Serena Williams"`) are handled by Path 1 because tennis is in the individual set.

**Library choice: `rapidfuzz` (proposed dependency add).**
- MIT license, ~2MB compiled C extension, well-maintained (active monthly releases).
- Provides `rapidfuzz.fuzz.token_set_ratio` directly. We don't need the full library; importing `from rapidfuzz import fuzz` gives us the single function call used by both paths.
- 100k-string scans complete in milliseconds; fits the in-memory pattern AliasResolver established.

**Open question A.1:** Is adding `rapidfuzz` to `requirements.txt` acceptable? Alternative: hand-roll token_set_ratio in pure Python (~30 lines, slower but zero new deps). I lean toward `rapidfuzz` — name matching is operationally critical and the library is battle-tested, but flag it for sign-off.

---

## Question B — Cross-provider corroboration

### Answer (definitive): Kickoff-window fixture lookup as tiebreaker, NOT as primary signal.

The user's framing: when Kalshi says `"Rublev vs Kecmanovic"` and FL has a fixture with `"Rublev A."` vs `"Kecmanovic M."` at the same kickoff, that's strong evidence even without exact alias matches.

**Operational definition of "strong evidence":**
- Both sides surname-resolve to candidate sets (each side may have multiple plausible team_ids if there's ambiguity, e.g., two players named Smith).
- For each `(home_id, away_id)` permutation across the candidate sets:
  - Query `find_fixture(home_id, away_id, kickoff_at, drift_sec=30*60)` (the same Phase 2B helper).
  - If exactly ONE permutation produces an existing fixture, **that's the corroboration signal**. The fixture exists because some prior strict-tier or alias-tier auto-apply established it; this signal is "another provider's record points at the same canonical fixture."
  - If zero permutations produce a fixture: no corroboration available — fall back to surname-anchor + token-set scoring alone.
  - If two or more permutations produce existing fixtures: ambiguity worsens, not improves. Punt to review queue with all candidates.

**Why this matches the user's intuition:**
The Rublev/Kecmanovic case: FL ran first → strict tier resolved Rublev A. and Kecmanovic M. via existing aliases → created `sp.fixtures` row at kickoff T. Later, Kalshi's record `"Rublev vs Kecmanovic"` at kickoff T arrives. Surname anchor finds (rublev_id, kecmanovic_id) for both Kalshi tokens. `find_fixture(rublev_id, kecmanovic_id, T, drift=30min)` returns the FL-created fixture. Alias tier auto-applies with high confidence.

**Score contribution:**

| Signal                                         | Score  | Notes |
|------------------------------------------------|--------|-------|
| Both sides anchor (surname for personal, exact-token-set match for team-name path) unambiguous        | +0.50  | Required floor — without this, alias tier returns no_match |
| Token-set ratio on non-anchor tokens (avg of both sides) | up to +0.30 | Linear scaling: 1.0 ratio → +0.30; 0.85 ratio → +0.20; below 0.85 → 0 |
| Cross-provider corroboration (existing fixture at this kickoff in exactly one orientation) | +0.20 | Strongest non-name signal we have in 2C |
| Sport context matches (signal.sport == team.sport_id's sport) | required floor | Already enforced by AliasResolver.resolve(sport_id) |

Maximum: 0.50 + 0.30 + 0.20 = **1.00 exactly**. No clamp needed.

Why no kickoff-drift contribution: drift is already a hard filter at strict-tier gate 1 (`KICKOFF_DRIFT_SEC = 30 * 60`). A continuous-confidence boost for drifts inside that filter would be double-counting — every record reaching the alias tier has already passed the drift gate. The original draft's `+0.05` for drifts ≤ 5 min was a magic number without supporting data; dropped per Pushback 3.

A "perfect" alias-tier match (anchor + perfect non-anchor tokens + cross-provider corroboration) hits 1.00 on the dot — equal to human-verified. That is intentional: when all three signals agree, we have stronger evidence than the strict tier does (strict needs only the anchor + alias hit), so capping at 0.98 would understate confidence. The final routing still distinguishes via reason_code: an alias-tier 1.00 lands as `reason_code='alias'`, a human-curated 1.00 as `reason_code='strict'` after the alias is written and the next strict-tier pass picks it up.

**Important:** cross-provider corroboration in 2C is **only used as a tiebreaker for already-surname-anchored candidates**. It cannot promote a no-surname-match into a hit. The user's case still requires surname tokens to align across providers.

The pure "no name resemblance, kickoff alone" case (e.g. Kalshi "X vs Y" + FL fixture "A vs B" at same time) is Phase 2D corroboration territory, not 2C. Locking this scope avoids 2C silently linking unrelated fixtures via accidental kickoff coincidence.

---

## Question C — Confidence scoring + thresholds

### Answer (definitive): Composable signal sum, transparent score breakdown.

```python
@dataclass(frozen=True)
class AliasTierScore:
    confidence: float                       # final, clamped 0..1
    breakdown: dict[str, float]             # {signal_name: contribution}
    candidate_team_ids: tuple[uuid.UUID, ...]   # ranked
    surname_anchor_passed: bool             # required-floor gate
```

The matcher writes `breakdown` verbatim into `resolution_log.reason_detail.alias_score_breakdown` — an auditable record of which signals contributed how much to a given decision. Reviewers in 2F see the breakdown alongside the candidate fixtures.

**Routing:**

| Final confidence | Reason code            | DB writes |
|------------------|------------------------|-----------|
| ≥ 0.85           | `alias` (auto-apply)   | provider.fixture_id UPDATE + resolution_log INSERT + sp.team_aliases INSERT (write-back) |
| 0.70 – 0.84      | `review_queue`         | resolution_log INSERT + sp.review_queue INSERT (top-K candidates ranked) |
| 0.70 – 0.84 with top-2 within 0.05 of each other | `review_queue` (forced) | Same as above. Forces ambiguous cases into review even if top score is high. |
| < 0.70           | `no_match`             | resolution_log INSERT only |

The "top-2 within 0.05" rule prevents auto-applying when two candidates are nearly tied — those are exactly the cases where the wrong choice causes downstream confusion that's expensive to undo.

---

## Schema changes

### Answer (definitive): None required. Existing tables suffice.

| Existing table        | What 2C uses it for |
|-----------------------|---------------------|
| `sp.team_aliases`     | Read existing aliases (strict tier already does this); WRITE new aliases on auto-apply with `source='alias_tier'`, `confidence < 1.0` |
| `sp.resolution_log`   | Reason_detail JSONB carries `alias_score_breakdown`, `surname_anchor`, `corroboration_fixture_id` |
| `sp.review_queue`     | Candidate_fixtures JSONB carries the ranked top-K with score breakdown each |
| `sp.resolver_runs`    | New `extra` keys: `alias_tier_auto`, `alias_tier_review`, `alias_tier_no_match` |

**No new tables. No new columns.** This is deliberate — the 2A.6 → 2A.7 cycle showed schema changes carry their own risk surface, and 2C's data model fits the existing audit-log shape cleanly.

The one borderline case: should there be a `sp.team_aliases.score_when_created` column to record what the alias-tier confidence was at write-time? Counter-argument: `resolution_log` already carries that data, joinable on `(provider_record_id, decided_at)`. A column would be redundant.

---

## Negative space — what 2C explicitly does NOT do

The user's framing was sharp here. Documenting verbatim:

### 1. Player prop markets stay rejected.

`KXMLBTB`, `KXMLBHR`, `KXMLBHRR`, `KXNBASTL` (and any future per-player-stat market) must NOT alias-tier-match. These have abbr_blocks containing player handles, not team abbreviations.

**Two-layer defense:**
- **Upstream (preferred):** extend `_OUTRIGHT_SERIES_PREFIXES` in `kalshi_identity.py` to include the player-prop prefixes. `parse_ticker` then returns `kind='outright'`, `extract_signal` returns `None`, runner increments `signal_extraction_skipped`. Cleanest.
- **Backstop:** alias tier detects "abbr_block doesn't surname-resolve in either orientation against this sport" and returns `no_match` with `reason_code='alias_no_team_resemblance'`. Catches future prop prefixes that haven't been added to the outright list.

The audit dashboard distinguishes the two — `signal_extraction_skipped` in `extra` (counted) vs `alias_no_team_resemblance` in `resolution_log.reason_detail.fail_reason` (logged). Operators add new prop-market prefixes to the outright list when the latter starts climbing.

### 2. Mention markets, outright winners, financial-misclassified-as-Sports.

Same upstream-list pattern. The `_OUTRIGHT_SERIES_PREFIXES` list is the surface for "this Kalshi ticker is not a per-fixture market." 2C doesn't try to alias-tier-resolve any of these — they should be filtered before extract_signal returns a `FixtureSignal`.

The Kalshi runner SQL already filters `category = 'Sports'` (PR #85). Anything that reaches the matcher and fails alias-tier on `alias_no_team_resemblance` is signal that the upstream filtering missed something.

### 3. Cross-provider corroboration WITHOUT surname anchor.

Documented above. Locked out of 2C; reserved for Phase 2D where we'll have the analytical machinery to score arbitrary similarity claims.

### 4. Auto-creating new teams.

Architecture §7.4 forbids auto team creation in any tier except review-queue approvals. 2C honors this — when no team_id resolves on either side after surname anchor + token-set scoring, the alias tier returns `no_match`. Phase 2F's reviewer creates the team manually if appropriate.

### 5. Updating fixture metadata on conflict.

Same DO-NOTHING discipline as Phase 2B's `ensure_fixture`. Alias tier auto-apply only sets `fixture_id` on the provider record + writes the alias back. It does NOT touch fixture scores, state, venue, etc.

### 6. Cross-sport disambiguation.

`AliasResolver.resolve(sport_id)` already enforces sport scoping. 2C inherits this — a tennis surname won't match a soccer team even if normalized strings collide. No change to the existing behavior.

---

## Implementation sketch (for review, not yet code)

### File layout

```
resolver/
  alias_tier.py         (new — AliasTierMatcher, AliasTierScorer)
  alias_tier/
    normalize.py        (new — structural normalization for tennis-style names)
    scorer.py           (new — composable signal scoring)
  matcher.py            (modified — orchestrate strict → alias → no_match)
  fixtures.py           (unchanged from 2A.6)
  ...

scripts/
  run_resolver_pass.py  (modified — surface alias_tier_* counters in summary + sp.resolver_runs.extra)

migrations/
  (none — no schema changes per Schema Changes section above)

tests/
  test_resolver_2c.py   (new)
```

### `resolver/alias_tier/normalize.py` shape

```python
@dataclass(frozen=True)
class StructuredName:
    surname: str            # required anchor
    other_tokens: tuple[str, ...]   # initials, given names, qualifiers
    raw: str                # original encoding for audit
    detection_path: str     # 'tennis_initial' | 'two_token' | 'multi_token' | 'fallback_whole'

def structurally_normalize(s: str) -> StructuredName | None:
    """Decompose a name into surname-anchored structure.
    Returns None if no surname can be confidently identified."""
    ...
```

### `resolver/alias_tier/scorer.py` shape

```python
class AliasTierScorer:
    """Pure function over (FixtureSignal, candidate_team_ids per side,
    optional existing-fixture lookup). Returns AliasTierScore."""

    def score(
        self,
        signal: FixtureSignal,
        home_candidates: list[CandidateTeam],
        away_candidates: list[CandidateTeam],
        existing_fixture_lookup: Callable[[uuid.UUID, uuid.UUID, datetime], uuid.UUID | None],
    ) -> AliasTierScore: ...
```

### `resolver/alias_tier.py` shape

```python
class AliasTierMatcher:
    """Tier 2 matcher. Stateless apart from injected resolvers."""

    def __init__(
        self,
        aliases: AliasResolver,
        sport_id_by_code_or_name: dict[str, int],
        competitions: CompetitionResolver | None,
        scorer: AliasTierScorer,
    ) -> None: ...

    async def match(
        self,
        session: AsyncSession,
        signal: FixtureSignal,
    ) -> MatchResult: ...
```

### Matcher orchestration (`resolver/matcher.py` change)

```python
class StrictMatcher:
    # Existing 2B implementation unchanged.

class TieredMatcher:
    """Phase 2C: orchestrate strict → alias → no_match."""

    def __init__(self, strict: StrictMatcher, alias: AliasTierMatcher) -> None: ...

    async def match(self, session, signal) -> MatchResult:
        result = await self.strict.match(session, signal)
        if result.reason_code == ReasonCode.STRICT:
            return result    # auto-apply
        return await self.alias.match(session, signal)
```

The runner constructs `TieredMatcher(strict, alias)` instead of bare `StrictMatcher`. Existing strict-tier metrics in `sp.resolver_runs` keep their meaning; new keys in `extra` track alias-tier rates.

### Alias write-back

Inside `AliasTierMatcher.match` at auto-apply path:

```python
session.add(TeamAlias(
    team_id=resolved_team_id,
    alias=raw_provider_name,
    alias_normalized=normalize_name(raw_provider_name),
    source='alias_tier',
    confidence=score.confidence,
))
```

Same atomic transaction as the provider-table UPDATE + resolution_log INSERT (per design §1). On `(alias_normalized, source)` UNIQUE constraint conflict (idempotent re-resolution of the same record), `ON CONFLICT DO NOTHING`.

---

## Test plan

### Unit tests (`tests/test_resolver_2c.py`)

#### TestStructuralNormalize (10+ cases)
- Tennis initial pattern: `"Kecmanovic M (Srb)"` → surname="kecmanovic", others=("m",), country stripped
- Two-token full name: `"Miomir Kecmanovic"` → surname="kecmanovic", others=("miomir",)
- Single-token name: `"Kecmanovic"` → surname="kecmanovic", others=()
- Three-token name: `"Carlos Alcaraz Garfia"` → surname="garfia"? Or "alcaraz garfia"? Edge case; test the actual behavior we ship
- Empty / None / whitespace → None
- Punctuation: `"O'Brien"`, `"Saint-Étienne"` → handled by accent-strip; surname tokenization preserved
- Accent stripping: `"Atlético"` → `"atletico"`
- Suffix stripping: `"Smith Jr."` → surname="smith", others=("jr",)
- Country in parentheses: `"Sinner J. (Ita)"` → surname="sinner", others=("j",) (country stripped)

#### TestAliasTierScorer (15+ cases)
- Healthy auto-apply: surname-anchored both sides, perfect token set, kickoff drift 0 → confidence ≥ 0.85
- Healthy with corroboration: above + cross-provider fixture exists → near 1.0 (clamped)
- Surname only, weak tokens: confidence in 0.70–0.84 → review queue
- Surname mismatch: confidence < 0.70 → no_match
- Top-2 within 0.05: routed to review even if top is ≥ 0.85
- Sport mismatch: AliasResolver returns no candidates → no_match before scoring
- Wrong-orientation existing fixture: corroboration fires for `(away, home)` → reason_detail flags `orientation_flipped`
- Multiple fixtures in drift window: ambiguity → review queue, not auto-apply
- Player prop case (KXMLBTB-shape signal): surname anchor fails on both sides → reason_code='alias_no_team_resemblance'

#### TestTieredMatcher (5+ cases)
- Strict tier hits → alias tier never invoked
- Strict tier misses, alias tier hits → MatchResult.reason_code='alias', confidence ≥ 0.85
- Strict tier misses, alias tier review → reason_code='review_queue'
- Strict tier misses, alias tier misses → reason_code='no_match', no DB writes apart from log
- AliasResolver refresh between calls — previously alias-tier-resolved record now strict-tier-resolves on next pass (compounding behavior)

### Integration tests (`tests/test_resolver_2c_integration.py`)

Real call-path tests with mocked DB session — same shape as PR #87's `TestIngestPassIntegration`. **Lesson from yesterday's PR-cycle: every non-trivial tier change ships with at least one integration test that exercises the actual call path, not just static-source guards.**

- Spot-check 1: Kecmanovic. `extract_signal` produces a FixtureSignal with `home_team_candidates=["Miomir Kecmanovic"]`. Sp.team_aliases has `("kecmanovic m srb", source='legacy_bootstrap')` mapped to team_id T. AliasTierMatcher resolves. Confidence ≥ 0.85. Provider.fixture_id UPDATEd. New alias row inserted with `source='alias_tier'`.
- Spot-check 2: KXMLBTB. extract_signal returns a per_fixture FixtureSignal (current behavior — bug we're documenting). AliasTierMatcher's structural-normalize returns no surname anchor for the player handle. Result: `reason_code='no_match'`, `fail_reason='alias_no_team_resemblance'`. Provider.fixture_id stays NULL.
- Spot-check 3: long-tail team. Sp.teams has `"FK Aktobe"` with no aliases. Provider record references `"Aktobe"`. Surname anchor pass (single-token), token-set ratio 1.0 against canonical_name token set. Auto-apply.
- Spot-check 4: ambiguous surname (two Smith soccer teams). AliasResolver returns multiple candidates. Top-2 within 0.05 → review queue.
- Spot-check 5: cross-provider corroboration. Kalshi signal arrives after FL strict-tier created the fixture. No exact alias hit. Cross-provider lookup finds the existing fixture in one orientation. +0.20 corroboration boost. Auto-apply.

### Static guards (mirroring 2B's pattern, treated as a backstop, not the primary test surface)

- `TieredMatcher.match` calls `strict.match` first
- `AliasTierMatcher.match` writes to `sp.team_aliases` on auto-apply path
- `AliasTierMatcher.match` does NOT write to `sp.team_aliases` on review path
- Confidence threshold constants (`0.85`, `0.70`) appear at module scope, not buried as magic numbers

---

## Day-0 numbers prediction

Given the day-0 audit:

**Conservative recovery estimate (lower bound):**
- Tennis surname-mismatch: 70% of ~600 = ~420 records (some still won't resolve — qualifiers, doubles, withdrawals)
- Long-tail teams resolvable via surname-anchor + token-set: 40% of remaining ~7,766 = ~3,100
- Cross-provider corroboration adds incremental coverage on previously-ambiguous: ~500
- **Total auto-apply recovery: ~4,000 records**

**Optimistic recovery estimate (upper bound):**
- Tennis: 85% of ~600 = ~510
- Long-tail: 60% of ~7,766 = ~4,660
- Corroboration uplift: ~700
- **Total auto-apply recovery: ~5,800 records**

**Review queue expected to populate at ~10–20% of alias-tier auto-apply rate** — most ambiguous-surname cases, sub-threshold token-set scores, top-2-tied cases. So expect ~400–1,200 review-queue rows over the parallel-run window. Architecture §7.5 calls for steady-state depth < 20 with a 24h triage SLA — initial backlog at 2C launch will exceed that and drain over the first ~2 weeks of human review.

**Projected post-2C cumulative coverage:**

| Provider | Day-0 (today) | Day-7 prediction (low) | Day-7 prediction (high) |
|----------|---------------|------------------------|-------------------------|
| Kalshi   | 350 / 4,384 (8.0%)    | ~700 / 4,384 (16%)   | ~1,000 / 4,384 (23%)   |
| FL       | 14,408 / 18,484 (78%) | ~17,500 / 18,484 (95%) | ~18,200 / 18,484 (98%) |
| Total    | 14,758                | ~18,200              | ~19,200                |

**Caveats on the prediction:**
- Player-prop rejection (KXMLBTB etc.) reduces the Kalshi denominator, not numerator. So Kalshi coverage % may rise faster than the absolute auto-apply count suggests, depending on how many prop tickers are in the current 2,500 no_match bucket.
- FL is closer to its ceiling already (78%); incremental gains diminish.
- Cross-provider corroboration only fires when the OTHER provider has already resolved a fixture at the same kickoff. Day 1 of 2C runs against Day-0's strict-tier fixtures; the corroboration signal grows as 2C's own auto-applies create more fixtures for Day 2+ to corroborate against. This is mild compounding within the 7-day window.

---

## Open questions awaiting sign-off

### A.1 — `rapidfuzz` dependency add?

Adds ~2MB to the deploy. MIT license, well-maintained. Alternative: hand-roll token_set_ratio in pure Python (~30 lines, ~5x slower per call but irrelevant against the DB I/O budget).

**Recommendation:** add the dep. The function we want is single-purpose, well-tested, and the hand-roll's failure modes (Unicode edge cases, performance regression in long names) outweigh the dep cost.

### A.2 — Surname-anchor strictness on multi-token names

`"Carlos Alcaraz Garfia"` — should surname be `"garfia"` (last token) or `"alcaraz garfia"` (compound)? FL might list him as `"Alcaraz C. (Esp)"` (compound dropped) or `"Alcaraz Garfia C."` (compound kept).

**Recommendation:** start with last-token-as-surname, fall back to compound-suffix-pair if last-token alone produces no candidates. Document the case in test fixtures explicitly.

### B.1 — Should alias-tier auto-apply also touch competition_id like strict tier does?

Strict tier writes competition_id when creating a fixture (Phase 2A.6). Alias tier matches against an EXISTING fixture (the surname-anchor + corroboration model assumes the fixture is already there). So alias tier should NOT create new fixtures — only link to existing ones.

**Recommendation:** Lock `AliasTierMatcher` to NEVER call `ensure_fixture`. Either it links to an existing one or returns no_match. Forces 2C to operate purely as a "second look" tier and leaves fixture creation to strict tier. This is also a meaningful safety property — alias-tier confidence isn't high enough to justify minting new fixtures.

### C.1 — Review queue capacity at launch

Day-0 prediction: 400–1,200 review rows on day 1, draining over ~2 weeks of human review. Architecture §7.5 alert threshold is `> 100`. The 2C launch will cross that threshold.

**Resolution (per Pushback 2):**

> Architecture §7.5 alert threshold raised from 100 to 1,500 for the
> 14-day post-2C window. Reverts to 100 on day 15 automatically
> unless extended via documented decision.

Reversible by default rather than requiring active reversion. Mechanism: a single `REVIEW_QUEUE_ALERT_THRESHOLD_OVERRIDE_UNTIL` constant (or env var) carrying the date. The alert path checks `NOW() < OVERRIDE_UNTIL` and uses 1,500; otherwise 100.

Concretely, this gets implemented in 2C.4 as part of the runner / DEPLOYMENT.md update (the alert pathway itself isn't in code yet; the threshold is operator-tracked today). Adding code support gives us a calendar-driven auto-revert and a single line to extend if 14 days isn't enough.

**Other options considered + rejected:**
- (b) Lower the auto-apply threshold below 0.85 during parallel-run to drain into auto-applies. Rejected — increases FP rate during the highest-risk window.
- (c) Accept the alert and triage manually. Rejected — expensive operator time, alert fatigue defeats the purpose.

### C.2 — Phase 2D scope cleanup

Phase 2D was originally defined as "fuzzy + cross-provider corroboration" — but 2C now claims the corroboration-as-tiebreaker path. 2D should narrow to:
- Pure name-similarity fuzzy when neither side anchors (e.g. `"X Co Ltd"` vs `"X Football Club"` — anchor token unclear)
- Cross-provider corroboration WITHOUT anchor (the Rublev-but-no-surname-token-overlap case)

**Recommendation:** Lock the narrowed 2D scope in this design doc's sign-off, update SPORTS_BROWSE_SESSION_NOTES (or wherever 2D was last described) on merge.

### D.1 — Personal-vs-team path discrimination mechanism

Pushback 1 surfaced this. Two implementations possible:
- (a) Hardcoded `INDIVIDUAL_SPORT_CODES = frozenset({"tennis", "mma", "boxing", "golf", "snooker", "darts"})` in `resolver/alias_tier/normalize.py`.
- (b) New `sp.sports.is_individual` boolean column + migration.

**Recommendation:** (a). The list rarely changes — adding a new sport is a 1-line edit + matching `sp.sports` seed entry. Schema-zero stays intact. Tests assert every entry in the constant exists in `sp.sports.code` (cheap correctness guard against typos).

### D.2 — Token-set-ratio threshold for the team-name path (Path 2)

The 0.85 threshold is right for the personal-name path (anchor signal carries the disambiguation). The team-name path has no anchor — every match relies on whole-string overlap, so we tolerate fewer differences before crossing into false-positive territory.

**Recommendation:** 0.92 for Path 2. Empirically catches diacritic differences ("munchen" vs "munich"), qualifier-suffix drops ("sao paulo fc" vs "sao paulo"), and city-qualifier additions, while rejecting genuine cross-team near-misses ("manchester united" vs "manchester city" ≈ 0.67). Tunable post-launch if day-7 spot-checks show false positives.

### D.3 — Equal-or-NULL competition_id filter for the alias-tier corroboration lookup

When alias tier looks up an existing fixture for cross-provider corroboration, should it filter by `competition_id` like strict tier does (Phase 2A.6)?

**Recommendation:** YES — carry forward the same equal-or-NULL filter (`competition_id = filter OR competition_id IS NULL`) that strict tier uses. Same audit flags too: when alias-tier corroboration links a Kalshi explicit-comp signal to a NULL-comp fixture, write `linked_to_null_comp_fixture: true` + `null_comp_fixture_pending_backfill: <uuid>` to `reason_detail`. Phase 2C's reconciliation pass (which the audit flag was originally designed for) can then process alias-tier rows alongside strict-tier rows uniformly.

### D.4 — RESOLVER_VERSION semantics after orchestration

Today `RESOLVER_VERSION = "strict@2a.6"` is stamped on every `resolution_log` row by the strict-tier matcher. After 2C, a `TieredMatcher` wraps both the strict and alias tiers and the version stamp can mean different things at different decision points.

**Recommendation:** Per-tier versioning. Strict-tier rows continue to stamp `strict@2a.6` (no semantic change to the strict tier in 2C). Alias-tier rows stamp `alias@2c.0`. A new top-level `tiered@2c.0` constant exists only for `sp.resolver_runs.resolver_version` (the run-level metadata; needed for "which orchestrator produced this run"). Replay queries can filter by tier-specific version cleanly.

### D.5 — Alias write-back idempotency on re-runs

When alias tier auto-applies, it writes a new `sp.team_aliases` row. Re-running the same provider record (e.g., after an interrupted pass) shouldn't double-write — the existing `(alias_normalized, source)` UNIQUE constraint with `ON CONFLICT DO NOTHING` handles this. But what if the same alias produces a *different* confidence on the later run (because more candidates exist now, e.g., after a sp.teams seed update)?

**Recommendation:** `ON CONFLICT DO NOTHING`, no confidence update on re-write. Reasoning: the sp.team_aliases.confidence column is the alias's *provenance confidence* (how sure are we this string maps to this team) — once written it represents a frozen decision. If the same alias produces a different score later, that's a signal for the reviewer to look at, not for the resolver to silently overwrite. Audit visibility: the `resolution_log` row written for the re-run carries the new score in `reason_detail.alias_score_breakdown`, so the divergence is queryable without a column-update.

### D.6 — Alias_no_team_resemblance always logs to resolution_log

When alias tier rejects a record because no anchor was found at all (the player-prop case where both sides surname-resolve to nothing), should a `resolution_log` row be written?

**Recommendation:** YES, log every match decision. Per Phase 2A.6 discipline (PR #84) — `reason_code='no_match'`, `fail_reason='alias_no_team_resemblance'`, full `reason_detail` capturing what we tried. Operators query for this fail_reason to identify upstream filter gaps (KXMLBTB-shaped tickers slipping past the outright-prefix list). Without the log row, we'd have no way to count these and act on them.

The alternative (silently skip) was considered and rejected — silent skips are exactly the failure mode that produced PR #82 / #84 / #86 / #87. Every match attempt produces an auditable record.

---

## Sign-off checklist

Before implementation begins:

**Algorithm + scope:**
- [ ] **A** — Two-path algorithm (personal-name surname-anchor + team-name whole-string token-set). Threshold 0.85 personal / 0.92 team. Approved or counter-proposed.
- [ ] **A.1** — `rapidfuzz` dependency add. Approved or rejected (with hand-roll mandate).
- [ ] **A.2** — Multi-token surname strictness. Last-token-as-surname with compound-suffix fallback. Approved.
- [ ] **B** — Cross-provider corroboration scoped to "tiebreaker for anchored candidates only." Approved or expanded.
- [ ] **B.1** — Alias tier never calls `ensure_fixture` (links to existing or returns no_match). Approved.
- [ ] **C** — Confidence model 0.50 + 0.30 + 0.20 = 1.00 exactly (no kickoff-drift term per Pushback 3). Thresholds 0.85 auto / 0.70-0.84 review / top-2-within-0.05 forced review. Approved or counter-proposed.
- [ ] **C.1** — Review-queue alert threshold raised to 1,500 for 14-day post-2C window with auto-revert (per Pushback 2). Approved.
- [ ] **C.2** — Phase 2D scope narrowed to "no anchor" cases. Approved.

**Implementation details:**
- [ ] **D.1** — Hardcoded `INDIVIDUAL_SPORT_CODES` constant; no schema column. Approved.
- [ ] **D.2** — Path-2 (team-name) threshold = 0.92. Approved or counter-proposed.
- [ ] **D.3** — Equal-or-NULL competition_id filter carried forward to alias-tier corroboration lookup. Audit flags reused. Approved.
- [ ] **D.4** — Per-tier `RESOLVER_VERSION` (`strict@2a.6` + `alias@2c.0` + run-level `tiered@2c.0`). Approved.
- [ ] **D.5** — `ON CONFLICT DO NOTHING` on alias write-back; no confidence update on re-run. Approved.
- [ ] **D.6** — `alias_no_team_resemblance` always writes to `resolution_log`. Approved.

**Process:**
- [ ] Schema-zero approach. Approved.
- [ ] Negative-space list (player props, mention markets, outright winners, no auto-create teams). Approved.
- [ ] Test plan: real call-path integration tests as the primary surface, static guards as backstop. Approved.

After sign-off, 2C ships in this order — each step is its own PR:

1. **2C.1 — Player-prop prefix list extension.** Single-file diff in `kalshi_identity.py` adding the player-prop prefixes (`KXMLBTB`, `KXMLBHR`, `KXMLBHRR`, `KXNBASTL`, ...) to `_OUTRIGHT_SERIES_PREFIXES`. Cleans up the upstream filter before the alias tier sees these tickers. **Smallest possible PR. Same shape as the runner instrumentation PRs from yesterday — fix the upstream filter first, alias-tier work follows.** Test plan: extend `tests/test_kalshi_identity.py` with cases asserting each new prefix returns `kind='outright'`.
2. **2C.2 — Structural normalizer + scorer.** Pure-Python modules with full unit tests. No DB, no matcher integration yet. Two paths (personal + team) tested independently. ~15-20 unit tests covering every detection branch + threshold boundary.
3. **2C.3 — `AliasTierMatcher` + `TieredMatcher` orchestration + alias write-back.** Integration tests with mocked DB session — exercises the actual call path per the lesson from PR #87. Spot-check fixtures from the doc's test plan (Kecmanovic auto-applies, KXMLBTB rejects, long-tail team auto-applies, ambiguous surname routes to review, cross-provider corroboration uplifts).
4. **2C.4 — Runner integration + `sp.resolver_runs.extra` counters + DEPLOYMENT.md + alert threshold override.** Smoke against prod with `--limit 100`, then full pass.
5. **2C.5 — Day-7 review.** Same cadence as 2B. Adjust thresholds if FP rate exceeds halt criteria.
