# Phase 2D.5-A Workstreams #8 + #9 — EuroLeague gap-fill + ABA League (combined)

**Workstreams #8 + #9** of Phase 2D.5-A combined into a single PR.
Selected per Day-31 re-sequencing in
[`phase-2d5a-sequencing-decision.md`](phase-2d5a-sequencing-decision.md)
+ Day-35 combined-workstream judgment (both predominantly BACKFILL,
moderate-to-low volume).

**Methodology lineage**: mirrors Greek HEBA #6 (PR #221) and VTB #7
(PR #223) single-PR delivery. New dimensions: combined-workstream
delivery, 12-country multi-country composition, cross-league team
dual presence (Partizan + Dubai), highest BACKFILL count of
Phase 2D.5-A (20 of 24 = 83%).

---

## 1. Context

Workstreams #1-7 complete (LMB, ACB, LBA, Israeli BSL, Turkish BSL,
HEBA, VTB — all applied + F7-validated through Day-34).

EuroLeague + ABA combined is the eighth empirical iteration:
- LMB / ACB / LBA / Israeli BSL / Turkish BSL / HEBA / VTB — prior
- **EuroLeague + ABA (this workstream)** — 24-team combined manifest;
  highest BACKFILL ratio (83%); first multi-country aggregator
  workstream (12 countries)

## 2. Discovery query (Pattern A.2 per amendment #21)

Day-35 production discovery confirmed in-scope provider forms:

| Provider string | Volume/7d | Maps to |
|---|---:|---|
| KK Partizan Belgrade | 24 | Partizan Mozzart Bet |
| KK Crvena zvezda Belgrade | 14 | Crvena Zvezda Meridianbet |
| KK Buducnost Voli | 14+14 | Buducnost |
| KK Bosna Royal Sarajevo | 14 | KK Bosna |
| Monaco vs Olympiacos * | 3 | Monaco |
| BC Rytas Vilnius vs AEK Athens | 6 | Rytas (EuroCup crossover) |
| FC Universitatea Cluj | 14+14 | U-BT Cluj-Napoca |

EuroLeague volume LOW (~15/7d residual) because most EuroLeague
teams already covered by prior domestic workstreams (#2 ACB, #4
Israeli BSL, #5 Turkish BSL, #6 HEBA, #7 VTB, #3 LBA). Gap-fill
scope.

ABA volume MODERATE (~90-100/7d total) across 16 teams + 2
EuroLeague-dual teams.

## 3. Roster source + composition

Operator-verified Day-35 paste from Wikipedia "2025-26 EuroLeague
season" + "2024-25 ABA League season" rosters.

**24-team combined manifest = 8 EuroLeague gap-fill + 16 ABA**.
Two teams in both leagues (Partizan Mozzart Bet, Dubai Basketball)
covered by single team_id each.

**Composition: 4 INSERT + 20 BACKFILL** (highest BACKFILL ratio of
Phase 2D.5-A at 83%).

### EuroLeague gap-fill (8)

| Team | Type | Phase 2A.5 UUID | Country |
|---|---|---|---|
| Monaco | BACKFILL | 092518ec | MCO |
| Bayern München | BACKFILL | bdb22a1c | DEU |
| Lyon-Villeurbanne | BACKFILL | 5481c8e7 | FRA |
| Paris Basketball | BACKFILL | e4e0e605 | FRA |
| Partizan Mozzart Bet | BACKFILL (dual-league) | 575ec0fc | SRB |
| Zalgiris Kaunas | BACKFILL | a845d73b | LTU |
| Rytas | BACKFILL | 834075ed | LTU |
| Dubai Basketball | INSERT (dual-league) | — | UAE |

### ABA League (16)

| Team | Type | Phase 2A.5 UUID | Country |
|---|---|---|---|
| Crvena Zvezda Meridianbet | BACKFILL | a3d095e9 | SRB |
| Buducnost | BACKFILL | 063a1204 | MNE |
| KK Bosna | BACKFILL | 99368c5b | BIH |
| Cedevita Olimpija | BACKFILL | e7cce709 | SVN |
| Mega Basket | BACKFILL | 5ef0b126 | SRB |
| Igokea | BACKFILL | ea0cd454 | BIH |
| KK Zadar | BACKFILL | bb0da184 | CRO |
| FMP Beograd | BACKFILL | 1337e0d0 | SRB |
| Borac Mozzart | BACKFILL | 949c6254 | SRB |
| BC Vienna | BACKFILL | 3c7275fc | AUT |
| KK Split | BACKFILL | d7a6e58e | CRO |
| KK Krka Novo Mesto | BACKFILL | 0674ed89 | SVN |
| Spartak Subotica | BACKFILL | 3c6aa492 | SRB |
| SC Derby | INSERT | — | MNE (Podgorica) |
| Ilirija | INSERT | — | SVN |
| U-BT Cluj-Napoca | INSERT | — | ROU |

## 4. NEW METHODOLOGY DIMENSIONS

### 4.1 Combined-workstream delivery

First combined-workstream PR in Phase 2D.5-A. Rationale: both
EuroLeague gap-fill and ABA League are predominantly BACKFILL
with low-to-moderate volume. Combined scope:
- single PR reduces review overhead
- shared Pattern A.2 discovery query (Day-35 single sweep)
- shared apply timestamp / baseline_shifts annotation
- shared post-apply collision audit

Methodology precedent: amendment #14 single-PR convention extends
to combined-workstream delivery when scope + volume justify.

### 4.2 Multi-country composition (12 countries)

First workstream with significant multi-country diversity (prior
Liga ACB was light multi-country at 2 codes; this workstream has
12 distinct codes).

| Country | Teams | Notes |
|---|---:|---|
| SRB | 6 | Partizan, Crvena Zvezda, Mega, FMP, Borac, Spartak |
| SVN | 3 | Cedevita Olimpija, KK Krka, Ilirija |
| MNE | 2 | Buducnost, SC Derby (Podgorica) |
| BIH | 2 | KK Bosna, Igokea |
| CRO | 2 | KK Zadar, KK Split |
| FRA | 2 | Lyon-Villeurbanne, Paris Basketball |
| LTU | 2 | Zalgiris Kaunas, Rytas |
| AUT | 1 | BC Vienna |
| ROU | 1 | U-BT Cluj-Napoca |
| UAE | 1 | Dubai Basketball |
| MCO | 1 | Monaco |
| DEU | 1 | Bayern München |

F7 query filter spans all 12 codes.

### 4.3 Cross-league dual presence

Partizan Mozzart Bet (575ec0fc) appears in both EuroLeague 2025-26
and ABA 2024-25 — single team_id covers both via cross-league
fixture strict resolution.

Dubai Basketball (INSERT) similarly appears in both — single
team_id covers both as fresh INSERT.

This is a manifest-level dimension: when a team is in multiple
target leagues, single team_id + multi-context notes documents the
dual presence.

### 4.4 8 dormant phantom collision risks (Amendment #22 pre-flagged)

**EuroLeague phantoms:**

| Dormant phantom | UUID | Collides with |
|---|---|---|
| Monaco Basket | 51a337b9 | Monaco BACKFILL |
| Bayern | b4318e7f | Bayern München (bare 'Bayern' alias) |
| LDLC ASVEL Lyon-Villeurbanne Espoirs U21 | 4541053d | Lyon-Villeurbanne |

**ABA phantoms:**

| Dormant phantom | UUID | Collides with |
|---|---|---|
| KK Crvena zvezda | 1ebacd0f | Crvena Zvezda Meridianbet |
| KK Borac | 26b9f2eb | Borac Mozzart (`kk borac` alias) |
| KK Student Igokea | 707c2064 | Igokea |
| Zadar | 8d626c4b | KK Zadar (bare `zadar` alias) |
| Split | fd5eb539 | KK Split (bare `split` alias) |

Plus separate-entity stubs NOT to be aliased (see seed file
docstring for full list).

Per Day-33 HEBA + Day-34 VTB precedent: post-apply collision audit
MANDATORY regardless of clean amendment #22 pre-apply audit. Expect
5-10 collision DELETEs based on phantom count + potential surprises.

## 5. Framing-question decisions (F1–F8)

### F1 — Canonical_name

BACKFILLs preserve legacy Phase 2A.5 canonical (production-anchor).
INSERTs use Wikipedia-confirmed forms.

Notable sponsor-prefixed legacy canonicals retained:
- "Partizan Mozzart Bet" (Mozzart Bet current sponsor)
- "Crvena Zvezda Meridianbet" (Meridianbet current sponsor)
- "Borac Mozzart" (Mozzart current sponsor)

### F2 — Alias distinctiveness (F2 NEW empirical-coverage)

Bare/distinctive aliases INCLUDED:
- "Monaco", "Bayern", "ASVEL", "Olimpija", "Krka", "Spartak", "FMP",
  "Bosna", "Zadar", "Split", "Vienna", "Krka"
- "Partizan" bare INCLUDED (Day-35 discovery)

Cross-sport collision considerations:
- Bayern: bare INCLUDED per F2 NEW + post-apply audit catches
  dormant phantom collision

### F3 — Diacritics

NFD-collapsing pairs INCLUDED as belt-and-suspenders documentation:
- "Budućnost" ↔ "Buducnost" (ć → c)
- "Žalgiris" ↔ "Zalgiris" (ž → z)

Spelling variants (different normalized keys):
- "Bayern München" ↔ "Bayern Munich" (München → munchen, Munich
  spelling differs)

Cyrillic-script aliases OUT OF SCOPE per KBL Issue #165 precedent.

### F4 — Source value

`bootstrap_league_coverage` (Q3 convention).

### F5 — Country code

Per-team (12 distinct codes). See §4.2.

### F6 — Bootstrap script structure

Triplet — `scripts/euroleague_aba_seed.py` +
`scripts/bootstrap_euroleague_aba.py` +
`tests/test_bootstrap_euroleague_aba.py`. Direct mirror of
`bootstrap_heba.py` structure with combined-manifest import.

### F7 — Verification

```sql
SELECT count(*)
FROM sp.resolution_log rl
JOIN sp.fixtures f ON f.id = rl.fixture_id
JOIN sp.teams t_home ON t_home.id = f.home_team_id
JOIN sp.teams t_away ON t_away.id = f.away_team_id
WHERE rl.reason_detail->>'sport' = 'Basketball'
  AND rl.reason_code = 'strict'
  AND rl.decided_at >= :apply_timestamp
  AND (t_home.country_code IN
         ('SRB','MNE','BIH','SVN','CRO','AUT','ROU',
          'UAE','MCO','DEU','FRA','LTU')
       OR t_away.country_code IN
         ('SRB','MNE','BIH','SVN','CRO','AUT','ROU',
          'UAE','MCO','DEU','FRA','LTU'));
```

Expected: ~30-50 strict resolutions in first 14-17h post-apply.

NOTE: F7 country filter overlaps with prior workstreams (FRA, DEU,
LTU teams not in this manifest may also resolve through prior
coverage). Consider narrowing to specific team_id list for tighter
attribution.

### F8 — Success criterion

Per amendment #20 — F8 is the F7 league-specific JOIN query showing
≥50% reduction in attributable asymmetric_anchor_failure records
over 7 days.

## 6. Open questions / follow-ups

### §6.1 EuroLeague residual volume low (~15/7d)

Most EuroLeague coverage already provided by prior domestic
workstreams. Future EuroLeague lift will come from those manifests
maturing (sponsor updates, roster churn). This gap-fill workstream
closes the residual.

### §6.2 ABA League roster churns annually

Standard re-curation runbook applies. Annual sponsor changes
(Mega Basket → Mega Superbet → ...) frequent.

### §6.3 Dubai Basketball dual-league presence

Single team_id covers both EuroLeague + ABA. If FL sends different
provider forms per league, alias expansion may be needed.

### §6.4 Hapoel IBI Tel Aviv (EuroLeague rebrand)

EuroLeague 2025-26 includes Hapoel IBI Tel Aviv (rebrand of Hapoel
Tel Aviv from Israeli BSL workstream #4). Existing aliases in Israeli
BSL manifest resolve this team — no action needed.

### §6.5 INSERT teams post-apply monitoring

SC Derby (MNE), Ilirija (SVN), U-BT Cluj-Napoca (ROU), Dubai
Basketball (UAE) are 4 fresh INSERTs. If FL sends different provider
forms post-apply, alias expansion needed via re-curation.

### §6.6 Bayern bare-alias collision risk

Bare `Bayern` alias on Bayern München collides with Bayern
(b4318e7f) Phase 2A.5 stub. Post-apply audit MAY require DELETE of
bare `Bayern` alias depending on how operator decides to route
strict-tier reach (legacy phantom vs new BACKFILL).

### §6.7 Multi-country F7 attribution noise

F7 query filters on 12 country codes that overlap with prior
workstreams (FRA, DEU, LTU not exclusive to this workstream). For
tighter attribution, consider team_id IN (:manifest_team_ids)
filter instead of country_code filter.

## 7. Cross-references

- Parent scope-doc:
  [`phase-2d5a-data-driven-bootstrap.md`](phase-2d5a-data-driven-bootstrap.md)
- Sequencing decision + Day-31 re-sequencing:
  [`phase-2d5a-sequencing-decision.md`](phase-2d5a-sequencing-decision.md)
- VTB precedent (dormant phantom remediation):
  [`phase-2d5a-russian-vtb.md`](phase-2d5a-russian-vtb.md)
- HEBA precedent (BACKFILL-heavy composition):
  [`phase-2d5a-greek-heba.md`](phase-2d5a-greek-heba.md)
- Day-22 sport_id partition: `resolver/aliases.py:51,111`
- F7 JOIN template: amendment #18
- Amendment #22 pre-apply audit: PROJECT_STATE.md Day-32 morning
- Day-33 HEBA + Day-34 VTB post-apply collision precedent:
  PROJECT_STATE.md Day-33/34 morning
- v1.5 amendment pile: PROJECT_STATE.md

## 8. Apply runbook (post-merge)

1. `git pull` after PR merge
2. Pattern D pre-flight env verification
3. **Amendment #22 pre-apply alias-claim audit** (MANDATORY)
4. `python scripts/bootstrap_euroleague_aba.py --dry-run` —
   expect 4 INSERTs + 20 BACKFILLs
5. `python scripts/bootstrap_euroleague_aba.py` — wet apply
6. F7 verification via multi-country JOIN template at
   apply_timestamp + 14-17h
7. **Post-apply collision audit** (MANDATORY per Day-33 HEBA +
   Day-34 VTB) — expected 5-10 collisions; remediate via DELETE on
   `bootstrap_league_coverage`
8. `sp.baseline_shifts` annotation. Consider single combined event
   row (event_type=`phase_2d5a_euroleague_aba_bootstrap`) or
   separate rows per workstream — operator discretion.
9. Day-N+1 daily-diff (amendment #20)
