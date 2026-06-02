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

## Re-sequencing decision (Day-31, post-LBA-apply)

**Date:** 2026-06-02 (Day-31 afternoon, ~25 min after Italian LBA workstream #3 applied at 13:39 UTC)
**Decision:** Israeli BSL selected as workstream #4 over EuroLeague-proper. EuroLeague demoted to workstream #8 as a small fill-in-the-gaps manifest after domestic-league bootstraps cover its constituent teams.
**Context:** Workstreams #1-3 complete (LMB Day-28, Liga ACB Day-29, Italian LBA Day-31). Day-31 afternoon Pattern A.2 pre-scope discovery query for the next workstream revealed the unresolved Basketball population is dominated by domestic basketball leagues (Israeli BSL, Turkish BSL, Greek HEBA A1), NOT EuroLeague-proper.

### Discovery query result (2026-06-02 ~14:00 UTC, ~25 min after LBA apply)

Pattern A.2 discovery query ran against `sp.resolution_log` 7-day window for unresolved Basketball records with EuroLeague-team-pattern home/away forms. Returned 50 distinct provider-string pairs at occurrences ≥14.

Provider-string attribution (by league):

| League | Sample provider strings | Estimated occurrences/7d |
|---|---|---:|
| Israeli BSL / Winner League | Maccabi Rishon LeZion, Hapoel Tel-Aviv, Maccabi Haifa, Hapoel HaEmek, Bnei Herzliya, Elitzur Yavne, Maccabi Petah Tikva, Hapoel Beer Sheva, Maccabi Kiryat Gat, Maccabi Maale Adumim, Migdal Haemek, Ironi Kiryat Ata, Galil Elyon, Hapoel Galil Elyon, Elitzur Maccabi Netanya | ~300+ |
| Turkish BSL (Basketbol Süper Ligi) | Galatasaray SK, Besiktas JK, Esenler Erokspor, Manisa, Fenerbahce Istanbul (domestic) | ~100+ |
| Greek HEBA A1 | BC Olympiakos Piraeus, BC Kolossos Rhodes, BC AEK Athens | ~50+ |
| Russian VTB United | BC Lokomotiv Kuban, CSKA Moscow, Unics Kazan, Enisey | ~150 |
| Serbian KLS / ABA League (multi-country) | KK Crvena zvezda Belgrade, KK Partizan Belgrade, KK Bosna Royal Sarajevo, KK Buducnost Voli (Montenegrin) | ~40 |
| Italian LBA (just-applied today, in-window pre-propagation) | Olimpia Milano, Reggiana, Brescia | ~74 (will drop post-LBA-apply propagation) |
| EuroLeague-proper (cross-country aggregator residual) | Monaco vs Olympiakos *, AEK Athens vs Rytas *, BC Rytas Vilnius vs BC AEK Athens, Fenerbahce Istanbul vs BC Olympiakos Piraeus, FC Universitatea Cluj vs KK Buducnost Voli | ~50-80 (after subtracting domestic-league overlap) |

### Findings from Day-31 discovery query

**Finding 1**: EuroLeague-proper is NOT the highest-volume unresolved Basketball population. Israeli BSL alone (~300/7d) is 3× the EuroLeague-only estimate (~80/7d).

**Finding 2**: Asterisk-suffix FL provider pattern generalizes — appears in `Olympiacos *`, `Rytas *`, `CSKA Moscow *`. Same pattern as Italian LBA's `Brescia *` and `Verona *` from Day-30. Confirms it's a general FL provider-side artifact, not LBA-specific. Manifests for ALL future leagues must handle asterisk-suffix variants.

**Finding 3**: Two-form variants persist across leagues — "Maccabi Tel Aviv" vs "Maccabi Tel-Aviv" (hyphen), "Olympiacos" vs "Olympiakos" (transliteration), "Fenerbahce" vs "Fenerbahçe" (diacritic), "Fenerbahce" vs "Fenerbahce Istanbul" (city suffix). Aliases must cover ALL forms.

**Finding 4**: Cross-sport collision discipline expands per-country for Turkish (Galatasaray, Besiktas, Fenerbahce all top-5 Turkish football clubs) and Greek (Olympiakos, Panathinaikos top-5 Greek football clubs). Israeli BSL has cross-sport overlap with Israeli Premier League soccer (Maccabi Tel Aviv FC, Hapoel Tel Aviv FC) — Maccabi/Hapoel naming pattern shared across many sports.

**Finding 5**: Italian LBA records still appearing in the 7-day window pre-propagation. "Olimpia Milano vs Reggiana" 28/7d, "Brescia vs Olimpia Milano" 28/7d — these were unresolved BEFORE today's LBA apply at 13:39 UTC. Post-apply propagation will resolve them to strict-tier; expect them to drop out of subsequent discovery queries. Day-32 daily-diff will be the first measurement post-LBA-apply-propagation.

**Finding 6**: ABA League multi-country complexity confirmed. KK Bosna Royal Sarajevo (Bosnia-Herzegovina) appears as a Partizan (Serbia) opponent. Serbian KLS / ABA League workstream would actually be multi-country (SRB+BIH+SVN+CRO+MNE) per ABA structure, not single-country.

### Re-sequenced workstreams #4-9

Per amendment #15 (bootstrap leverage ≠ total-daily-volume — production-data discovery overrides scope-doc defaults), the Day-28 sequencing decision is overridden by Day-31 empirical evidence.

**Original Day-28 sequencing** (deprecated):
- #4: EuroLeague (~250 records/7d per scope-doc estimate)
- #5: Polish PLK
- #6: German BBL
- #7: VTB+others

**Day-31 re-sequenced workstreams** (active):

| # | League | Country | Est volume/7d | Methodology risk | Rationale |
|---|---|---|---:|---|---|
| 4 | Israeli BSL (Winner League) | ISR | ~300 | Low | Single country, highest unresolved volume, mirrors LBA methodology |
| 5 | Turkish BSL (Basketbol Süper Ligi) | TUR | ~100+ | Medium | Single country, top-5-football-club cross-sport (Galatasaray, Besiktas, Fenerbahce) discipline required |
| 6 | Greek HEBA A1 | GRC | ~50+ | Medium | Single country, top-5-football-club cross-sport (Olympiakos, Panathinaikos) discipline required |
| 7 | Russian VTB United | RUS | ~150 | Low | Single country |
| 8 | EuroLeague (cross-country aggregator) | Multi (10) | ~50-80 residual | High | Multi-country, but smaller residual after #4-7 cover domestic teams; many EuroLeague teams already in sp.teams from earlier workstreams |
| 9 | Serbian KLS / ABA League | SRB+BIH+SVN+CRO+MNE | ~40 | Medium-high | Multi-country ABA structure; defer to last in series |

Other previously-considered leagues (Polish PLK, German BBL, Lithuanian LKL, Spanish-side ACB-overlap) re-evaluated:

| League | Status | Reason |
|---|---|---|
| Polish PLK | Deferred | Not surfaced in Day-31 discovery; original ~150/7d estimate may be stale or covered by Phase 2A.5 legacy |
| German BBL | Deferred | Not surfaced in Day-31 discovery; same reason |
| Lithuanian LKL | Out-of-scope for Phase 2D.5-A | EuroLeague crossover (Rytas) already in legacy per Day-30 F7 |

### Implication of re-sequencing for EuroLeague workstream

By the time EuroLeague-proper workstream lands (now #8), domestic-league bootstraps will have covered:
- Greek teams (Olympiakos, Panathinaikos, AEK Athens) via Greek HEBA workstream #6
- Turkish teams (Fenerbahce, Galatasaray, Anadolu Efes, Besiktas) via Turkish BSL workstream #5
- Israeli teams (Maccabi Tel Aviv, Hapoel Tel Aviv, Hapoel Jerusalem) via Israeli BSL workstream #4
- Russian teams (CSKA Moscow, Unics Kazan) via Russian VTB workstream #7

EuroLeague workstream #8 then becomes a small "fill-in-the-gaps" manifest of EuroLeague-ONLY teams (Real Madrid Baloncesto and FC Barcelona already from Liga ACB, Olimpia Milano and Virtus Bologna already from Italian LBA, Greek/Turkish/Israeli/Russian from #4-7). Estimated EuroLeague-only residual: 4-6 teams (Žalgiris, Monaco, ASVEL, Paris Basketball, ALBA Berlin, FC Bayern Munich Basketball). Methodology risk substantially reduced from multi-country greenfield to multi-country gap-fill.

This is the second empirical validation of amendment #15 (bootstrap leverage ≠ total-daily-volume; data overrides scope-doc defaults). Day-28 surfaced it on Liga ACB volume estimates; Day-31 surfaces it on EuroLeague composition.

### Workstream #4 = Israeli BSL kickoff

Proceeding immediately with Israeli BSL pre-scope work. Discovery query already done (this section's evidence). Next steps:

1. Authoritative-source roster (Wikipedia 2025-26 Israeli Basketball Premier League / Winner League)
2. Cross-reference Wikipedia roster against Day-31 discovery provider forms
3. Cross-sport collision discipline for Israeli football overlaps (Maccabi Tel Aviv FC, Hapoel Tel Aviv FC, Hapoel Jerusalem FC, Beitar Jerusalem, etc.)
4. Manifest, bootstrap script, tests, scope-doc per amendment #14 single-PR convention

## Document status

- **Drafted**: 2026-05-28 (Day-28 evening)
- **Day-31 re-sequencing addendum**: 2026-06-02 afternoon (post-LBA-apply, post-EuroLeague-discovery-query)
- **Status**: Decision recorded; workstreams #1-3 complete; workstream #4 (Israeli BSL) kicked off Day-31 afternoon
- **Supersedes**: Phase 2D.5-A scope-doc §5 sequencing default (now overridden for workstreams #3 AND #4-9; Day-31 re-sequencing supersedes prior Day-28 re-sequencing for league #4 onward)
- **Cross-references**:
  - `docs/bootstraps/phase-2d5a-data-driven-bootstrap.md` (parent scope-doc)
  - `docs/bootstraps/phase-2d5a-italian-lba.md` (workstream #3 design + apply context)
  - PROJECT_STATE.md v1.5 amendment #15 (Pattern G extension; second empirical validation Day-31)
  - PROJECT_STATE.md v1.5 amendment #21 (Pattern A.2 sequencing — pre-scope discovery before authoritative-source roster sourcing; methodology applied to this re-sequencing)
  - PR #204 (Liga ACB single-PR delivery, methodology maturity demonstration)
  - PR #211 (Italian LBA single-PR delivery, amendment #21 first application)
