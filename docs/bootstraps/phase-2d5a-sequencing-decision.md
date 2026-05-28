# Phase 2D.5-A Workstream Sequencing — Decision Rationale for League #3

**Date:** 2026-05-28 (Day-28)
**Decision:** Italian LBA selected as workstream #3, deferring scope-doc default (EuroLeague) to workstream #4.
**Context:** LMB (workstream #1) applied to production Day-28. Liga ACB (workstream #2) ready for apply Day-29. Decision needed: which league is workstream #3?

---

## Original scope-doc §5 sequence (pre-empirical)

The Phase 2D.5-A scope-doc proposed: LMB → Liga ACB → EuroLeague → European Baseball → PLK+Czech+Israeli cohort → Tennis surnames.

This was a reasonable initial ordering based on a-priori reasoning about cross-sport collision risk and methodology validation:
- LMB: clean single-country baseline (no cross-sport)
- ACB: cross-sport collision pattern (Real Madrid CF vs Real Madrid Baloncesto)
- EuroLeague: multi-country expansion

## Empirical data forcing reconsideration (Day-28 discovery query)

Production `sp.resolution_log` analysis of `asymmetric_anchor_failure` records over the 14-day window revealed that Liga ACB is NOT the highest-volume basketball league with coverage gaps:

| League | Records/7d | Country scope | Notes |
|---|---:|---|---|
| Polish PLK | ~150 | Single (POL) | Dabrowa, Legia, Slask, Gdynia, LKS Lodz, Bydgoszcz, Spojnia |
| VTB United | ~120 | Multi (RUS+) | CSKA Moscow, Lokomotiv Kuban, Chelyabinsk, Khimki |
| German BBL | ~110 | Single (DEU) | Bonn, Wurzburg, Crailsheim, Giessen, Kirchheim, Bayreuth |
| Italian LBA | ~110 | Single (ITA) | Fortitudo Bologna, Brescia, Olimpia Milano, Verona, Trieste, Reggiana |
| Liga ACB | ~70 | Multi (ESP+AND) | Already in flight |
| EuroLeague | ~250 (gross) | Multi (10 countries) | 4-team overlap with Liga ACB; net incremental volume reduces |

**Key insight (v1.5 amendment #15 applied):** Scope-doc priority order is the default starting point. Production-data discovery overrides when evidence diverges. Bootstrap workstream sequencing should re-evaluate after each apply.

## Candidate analysis for league #3

### Option A: EuroLeague (original scope-doc default)
- **Volume**: ~250 records/7d gross
- **Overlap**: 4 of 20 teams already in Liga ACB (Barcelona, Baskonia, Real Madrid, Valencia Basket) → net 16 new canonicals
- **Per-team density**: 250 / 20 = 12.5 records/team/week (high)
- **Cross-sport collision risk**: HIGH (Real Madrid, Barcelona, Olimpia Milano = Inter Milan)
- **Country scope**: 10 countries (TUR, ESP, DEU, GRC, ISR, FRA, SRB, LTU, ITA, UAE/MCO)
- **Source availability**: Wikipedia excellent
- **Methodology delta from ACB**: Tests multi-country bootstrap (new dimension); cross-sport collision pattern already proven via ACB

### Option B: Polish PLK (highest single-league pure volume)
- **Volume**: ~150 records/7d
- **Overlap**: None with existing bootstraps
- **Per-team density**: 150 / 16 = 9.4 records/team/week (medium)
- **Cross-sport collision risk**: LOW (basketball-only club names)
- **Country scope**: Single (POL)
- **Source availability**: Wikipedia good; **diacritic-heavy** (Śląsk Wrocław, Łódź, Dąbrowa) means more alias variants per team
- **Methodology delta from ACB**: Tests diacritic-heavy single-country bootstrap (new dimension); no cross-sport pattern needed

### Option C: Italian LBA
- **Volume**: ~110 records/7d
- **Overlap**: Mild (Olimpia Milano is in EuroLeague but not Liga ACB)
- **Per-team density**: 110 / 16 = 6.9 records/team/week (medium)
- **Cross-sport collision risk**: HIGH (Bologna, Brescia, Milano all have major soccer clubs)
- **Country scope**: Single (ITA)
- **Source availability**: Wikipedia excellent
- **Methodology delta from ACB**: Tests cross-sport pattern in a new country with tighter scope (single-country); methodology mirrors ACB closely

### Option D: German BBL
- **Volume**: ~110 records/7d
- **Overlap**: None (Bayern Munich is in EuroLeague but not Liga ACB)
- **Per-team density**: 110 / 18 = 6.1 records/team/week (medium-low)
- **Cross-sport collision risk**: MEDIUM (Bayern, Hamburg have soccer dominance)
- **Country scope**: Single (DEU)
- **Source availability**: Wikipedia excellent
- **Methodology delta from ACB**: Tests cross-sport pattern in new country; mostly mid-tier teams (less collision pressure)

### Option E: VTB United
- **Volume**: ~120 records/7d
- **Overlap**: None
- **Per-team density**: 120 / 12 = 10 records/team/week (high)
- **Cross-sport collision risk**: MEDIUM (CSKA Moscow has soccer + hockey variants)
- **Country scope**: Multi (RUS dominant; KAZ, BLR, EST)
- **Source availability**: Wikipedia coverage in Russian + English; **Cyrillic transliteration** introduces alias complexity
- **Methodology delta from ACB**: Tests Cyrillic/transliteration bootstrap (new dimension); high methodology risk

## Decision framework

Three competing criteria:

1. **Maximum leverage per engineer-day** → prefer Polish PLK (150/7d, no overlap)
2. **Methodology validation breadth** → prefer EuroLeague (multi-country first time) or VTB (Cyrillic first time)
3. **Lowest risk, highest confidence** → prefer Italian LBA (mirrors ACB pattern closely, no new methodology dimensions)

## Recommendation: Italian LBA as workstream #3

### Reasoning

**Why LBA over PLK (the highest-volume candidate):**
- PLK introduces a new methodology dimension (diacritic-heavy single-country)
- LBA mirrors ACB's exact pattern (Mediterranean Latin-script basketball league, cross-sport collision)
- Methodology proven on ACB applies directly; risk of failure is minimal
- Volume gap (110 vs 150) is meaningful but not decisive — both are well above worth-bootstrapping threshold

**Why LBA over EuroLeague (scope-doc default):**
- EuroLeague multi-country complexity is a real new dimension untested by ACB
- EuroLeague has 4-team overlap with Liga ACB → effective net volume is ~200/7d (250 - some fraction routing through already-aliased teams)
- LBA is a cleaner methodology iteration; EuroLeague is better as workstream #4 once LBA proves out cross-sport pattern in a second country

**Why LBA over BBL/VTB:**
- BBL: comparable volume but less concentrated cross-sport collision pressure to validate against
- VTB: Cyrillic transliteration is a real new dimension; defer to workstream #5+ when methodology proven on 3+ leagues

### Strategic sequence (revised end-of-Day-28)

| # | League | Workstream goal |
|---|---|---|
| 1 | LMB | Methodology validation, single-country, no cross-sport (DONE) |
| 2 | Liga ACB | Cross-sport collision pattern, multi-country light (in flight Day-29) |
| 3 | **Italian LBA** | Cross-sport collision pattern, new country, tighter scope |
| 4 | EuroLeague | Multi-country bootstrap (new methodology dimension); leverages 4-team overlap with ACB |
| 5 | Polish PLK | Highest pure-volume single-league; diacritic-heavy aliases |
| 6 | German BBL | Single-country, medium cross-sport |
| 7+ | VTB United / European Baseball / others | Cyrillic transliteration, niche leagues |

This sequencing optimizes for:
- **Methodology de-risking** in early workstreams (LMB → ACB → LBA all incremental dimensions)
- **Maximum leverage** in middle workstreams (EuroLeague → PLK)
- **High-risk methodology dimensions deferred** (Cyrillic, niche leagues) until late, after methodology proven on 3+ leagues

## v1.5 amendment #15 in action

This decision document is the worked example of amendment #15 ("bootstrap leverage ≠ total-daily-volume; sequencing re-evaluates after each apply"). The scope-doc default (EuroLeague next) is overridden by empirical evidence that:

- LBA validates a useful new dimension (cross-sport in new country) at lower risk
- EuroLeague's 4-team ACB overlap reduces its effective marginal value
- Pure-volume candidates (PLK) require methodology dimensions not yet validated

Each apply teaches us something. Day-29 LBA apply teaches: does the ACB cross-sport methodology generalize cleanly across countries?

- **If yes** → EuroLeague becomes safer (cross-sport proven in 3 leagues across 3 countries)
- **If no** → revise approach before EuroLeague's broader scope amplifies any methodology flaws

## Pre-conditions before LBA workstream begins

1. Liga ACB apply confirmed clean (Day-29 morning)
2. Day-29 F7 verification confirms LMB-attributable Baseball lift materialized
3. Day-30 daily-diff confirms no Tennis or Basketball regression

If any pre-condition fails, sequencing decision re-evaluates before LBA scope-doc begins.

## Document status

- **Drafted**: 2026-05-28 (Day-28 evening)
- **Status**: Decision recorded; execution begins Day-30+ pending Liga ACB Day-29 apply
- **Supersedes**: Phase 2D.5-A scope-doc §5 sequencing default (for league #3 only; later sequence may revise further)
- **Cross-references**:
  - `docs/bootstraps/phase-2d5a-data-driven-bootstrap.md` (parent scope-doc)
  - PROJECT_STATE.md v1.5 amendment #15 (Pattern G extension)
  - PR #204 (Liga ACB single-PR delivery, methodology maturity demonstration)
