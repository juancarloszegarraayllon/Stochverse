# Phase 2D.5-A Workstream #7 — Russian VTB United League

**Workstream #7** of Phase 2D.5-A data-driven league bootstrap series.
Selected per Day-31 re-sequencing in
[`phase-2d5a-sequencing-decision.md`](phase-2d5a-sequencing-decision.md).

**Methodology lineage**: mirrors Greek HEBA #6 (PR #221) single-PR
delivery + F2 NEW empirical-coverage discipline. New dimensions:
Russian-to-Latin transliteration spelling variants (Yekaterinburg/
Ekaterinburg, UNICS/Uniks), out-of-roster manifest inclusion
(Khimki M.), and explicit BC Samara Wikipedia-roster exclusion.

---

## 1. Context

Workstreams #1-6 complete (LMB, ACB, LBA, Israeli BSL, Turkish BSL,
HEBA — all applied + F7-validated by Day-33). Day-34 morning Pattern
A.2 pre-scope discovery confirmed Russian VTB (~230+ records/7d) as
workstream #7.

VTB is the seventh empirical iteration:
- LMB / ACB / LBA / Israeli BSL / Turkish BSL / HEBA — prior workstreams
- **VTB (this workstream)** — 11-team Russian league; 1 football-overlap
  team with bare-form EXCLUSION (Zenit Petersburg), 6 football/hockey-
  overlap teams with bare-form INCLUSION per F2 NEW

## 2. Discovery query (Pattern A.2 per amendment #21)

Day-34 production discovery (7-day, Basketball, Russian provider patterns):

| Provider string | Volume/7d | Maps to |
|---|---:|---|
| BC Lokomotiv Kuban / Lokomotiv Kuban | ~80+ | Lokomotiv Kuban |
| CSKA Moscow / CSKA Moscow * | ~75+ | CSKA Moscow |
| BC Uniks Kazan / Unics Kazan | ~50+ | UNICS Kazan |
| Khimki M. / Khimki | ~42 | Khimki M. (out-of-roster) |
| Enisey / BC Enisey | ~30+ | Enisey |
| Chelyabinsk | ~42 | OUT OF SCOPE (not on VTB roster — §6.3) |

EuroLeague/EuroCup crossover potential: CSKA Moscow + UNICS Kazan
historically active pre-2022; current 2025-26 European participation
depends on geopolitical status. Crossover signal validated post-apply
via F7.

## 3. Roster source + manifest composition

Operator-verified Day-34 paste from Wikipedia "2025-26 VTB United
League season" roster. **11-team manifest** = 10 Wikipedia teams
(minus BC Samara) + 1 out-of-roster team (Khimki M.).

| # | Team | Type | Phase 2A.5 UUID |
|---|---|---|---|
| 1 | CSKA Moscow | INSERT | — |
| 2 | BC Uralmash Yekaterinburg | INSERT | — (separate stubs exist) |
| 3 | BC Nizhny Novgorod | INSERT | — |
| 4 | BC Avtodor | INSERT | — (Avtodor Saratov separate) |
| 5 | MBA Moscow | INSERT/BACKFILL dynamic | (1f5f991a if exists) |
| 6 | Lokomotiv Kuban | BACKFILL | 1dae39ae |
| 7 | UNICS Kazan | BACKFILL | b1d198b0 |
| 8 | Enisey | BACKFILL | eef30d44 |
| 9 | Zenit Petersburg | BACKFILL | d639c09a |
| 10 | Parma Perm | BACKFILL | a1973c38 |
| 11 | Khimki M. | BACKFILL (out-of-roster) | b2fbeb14 |

**BC Samara** from Wikipedia roster INTENTIONALLY EXCLUDED per
operator's Day-34 spec (no discovery volume; §6.5 follow-up).

## 4. NEW METHODOLOGY DIMENSIONS

### 4.1 Russian-to-Latin transliteration handling

Same shape as Greek HEBA's Olympiakos/Olympiacos pattern (Day-33):
spelling variants produce DIFFERENT normalized keys; both required
as aliases.

| Pair | Normalized keys |
|---|---|
| Yekaterinburg / Ekaterinburg | `yekaterinburg` vs `ekaterinburg` |
| UNICS / Uniks | `unics` vs `uniks` |
| Saint Petersburg / St Petersburg / Petersburg | multiple keys |
| Khimki / Khimki M. | `khimki` vs `khimki m` (period stripped) |

### 4.2 Out-of-roster manifest inclusion

**Khimki M.** is NOT on 2025-26 VTB Wikipedia roster (likely
relegated or withdrew) BUT Day-34 discovery shows FL actively
sending ~42 records/7d. Inclusion in manifest resolves active FL
volume without compromising roster fidelity (§6.1 monitoring).

This is a new methodology dimension: when production data and
authoritative-source roster diverge, prioritize production-data
empirical coverage to resolve active records, while flagging the
divergence in scope-doc for future re-evaluation.

### 4.3 Wikipedia-roster team exclusion (Samara)

Operator explicitly excluded BC Samara from manifest despite
Wikipedia roster membership. Rationale: no Day-34 discovery volume
suggests low/absent FL coverage. Scope-doc §6.5 follow-up — add if
FL ever sends Samara records.

This complements §4.2 — out-of-roster inclusion (Khimki M.) and
in-roster exclusion (Samara) are both empirical-driven divergences
from Wikipedia.

### 4.4 3 dormant phantom collision risks (Amendment #22 pre-flagged)

| Dormant phantom | UUID | Collides with |
|---|---|---|
| PBC Lokomotiv-Kuban | f4cd06c6 | Lokomotiv Kuban (`pbc lokomotiv kuban` alias normalize match) |
| Parma Permsky Kray | 065f0ed5 | Parma Perm (`parma permsky kray` alias) |
| Avtodor Saratov | c0766622 | BC Avtodor (`avtodor saratov` alias) |

Plus separate-entity Phase 2A.5 stubs that are NOT BACKFILLed:
- Uralmash Ekaterinburg (9684b3a4) — separate from BC Uralmash Yekaterinburg
- Uralmash Yekaterinburg (ce125faf) — separate from BC Uralmash Yekaterinburg

Per Day-33 HEBA precedent (5 predicted + 1 surprise AO Mykonou =
6 post-apply collisions): post-apply collision audit MANDATORY.

## 5. Framing-question decisions (F1–F8)

### F1 — Canonical_name

BACKFILLs preserve legacy Phase 2A.5 canonical_name (production-
anchor discipline). INSERTs use common forms.

Notable: legacy canonicals retained even where Wikipedia 2025-26
uses different form (UNICS Kazan vs Wikipedia "BC UNICS"; Zenit
Petersburg vs "BC Zenit Saint Petersburg"; Parma Perm vs "BC
Parma"; Khimki M. with trailing period).

### F2 — Alias distinctiveness (F2 NEW empirical-coverage)

Bare club aliases INCLUDED where FL sends bare forms (per Turkish
BSL #5 + HEBA #6 precedent):

| VTB canonical | Bare alias | Cross-sport context |
|---|---|---|
| CSKA Moscow | "CSKA Moscow" + "CSKA" | CSKA Moscow FC + HC (Day-22 partition) |
| Lokomotiv Kuban | "Lokomotiv Kuban" | distinctive city-qualified |
| BC Avtodor | "Avtodor" | distinctive single-team form |
| BC Uralmash Yekaterinburg | "Uralmash" | distinctive |
| Enisey | "Enisey" | distinctive |
| UNICS Kazan | "UNICS" | distinctive |
| Parma Perm | "Parma" | distinctive |

Bare generic/collision forms EXCLUDED:

| Excluded | Reason |
|---|---|
| Zenit | Zenit Saint Petersburg FC (top-5 Russian football recognition) |
| Moscow | within-VTB collision (CSKA + MBA both Moscow) |
| Kazan, Perm | too generic |
| Lokomotiv | shared (Lokomotiv Moscow FC, Lokomotiv Yaroslavl HC, etc.) |

### F3 — Transliteration (cross-script)

Russian-to-Latin Latin-only manifest (Cyrillic deferred per KBL
Issue #165 precedent). Both forms of each transliteration variant
INCLUDED per §4.1.

### F4 — Source value

`bootstrap_league_coverage` (Q3 convention).

### F5 — Country code

All 11 teams `country_code='RUS'` (single-country league).

### F6 — Bootstrap script structure

Triplet — `scripts/vtb_seed.py` + `scripts/bootstrap_vtb.py` +
`tests/test_bootstrap_vtb.py`. Direct mirror of `bootstrap_heba.py`
structure.

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
  AND (t_home.country_code = 'RUS' OR t_away.country_code = 'RUS');
```

Expected: ~50-100 strict resolutions in first 14-17h post-apply
(scaled from ~230+/7d discovery volume).

### F8 — Success criterion

Per amendment #20 — F8 is the F7 league-specific JOIN query showing
≥50% reduction in RUS-attributable asymmetric_anchor_failure records
over 7 days. Aggregate Basketball capability rate NOT the F8 metric.

## 6. Open questions / follow-ups

### §6.1 Khimki M. out-of-roster monitoring

Khimki M. not on 2025-26 VTB Wikipedia roster (likely relegated /
withdrew) but FL sending ~42/7d Day-34. Monitor whether records
cease at season end. Re-evaluate manifest inclusion on annual
re-curation.

### §6.2 Cyrillic alias coverage

Deferred per KBL Issue #165 (Hebrew script precedent). Latin
transliterations only in v1. Escalate to normalizer extension
workstream if production shows material Cyrillic-script provider
strings.

### §6.3 Chelyabinsk out-of-roster discovery noise

Day-34 discovery surfaced "Chelyabinsk" at ~42 records/7d but
Chelyabinsk is NOT on 2025-26 VTB roster. Likely a different
Russian basketball league (VTB.B regional second-division or
similar). Same out-of-scope noise pattern as LBA Serie A2/B,
Israeli BSL Liga Leumit, Turkish BSL TBL. Cross-workstream FL
sport-tier classifier investigation.

### §6.4 Dormant phantom collision remediation

3 pre-identified dormant phantoms (PBC Lokomotiv-Kuban f4cd06c6,
Parma Permsky Kray 065f0ed5, Avtodor Saratov c0766622) plus 2
separate-entity Uralmash stubs (9684b3a4, ce125faf). Per Day-33
HEBA AO Mykonou finding: post-apply collision audit MANDATORY
regardless of clean amendment #22 pre-apply audit. Post-apply
remediation via DELETE under `bootstrap_league_coverage` source.

### §6.5 BC Samara monitoring

BC Samara on 2025-26 Wikipedia roster but NOT in manifest (no
Day-34 discovery volume). Add to manifest if FL begins sending
Samara records (re-curation runbook).

### §6.6 MBA Moscow INSERT/BACKFILL dynamic resolution

Operator flagged MBA Moscow (1f5f991a) as possibly-existing Phase
2A.5 legacy stub. Three-branch classifier handles dynamically at
apply time. No pre-commit decision needed.

## 7. Cross-references

- Parent scope-doc:
  [`phase-2d5a-data-driven-bootstrap.md`](phase-2d5a-data-driven-bootstrap.md)
- Sequencing decision + Day-31 re-sequencing addendum:
  [`phase-2d5a-sequencing-decision.md`](phase-2d5a-sequencing-decision.md)
- Greek HEBA precedent (transliteration + dormant phantom 5+1):
  [`phase-2d5a-greek-heba.md`](phase-2d5a-greek-heba.md)
- Turkish BSL precedent (F2 NEW empirical-coverage):
  [`phase-2d5a-turkish-bsl.md`](phase-2d5a-turkish-bsl.md)
- Day-22 sport_id partition finding: `resolver/aliases.py:51,111`
- F7 JOIN template: amendment #18 (Day-29 morning)
- Amendment #22 pre-apply alias-claim audit: PROJECT_STATE.md
  Day-32 morning
- Day-33 HEBA AO Mykonou finding (post-apply audit mandate):
  PROJECT_STATE.md Day-33 morning
- v1.5 amendment pile: PROJECT_STATE.md (22 amendments as of
  Day-33 morning)

## 8. Apply runbook (post-merge)

1. `git pull` after PR merge
2. Pattern D pre-flight env verification
3. **Amendment #22 pre-apply alias-claim audit** (MANDATORY):
   ```sql
   SELECT ta.alias_normalized, COUNT(DISTINCT ta.team_id) AS team_count,
          ARRAY_AGG(DISTINCT t.canonical_name) AS canonicals,
          ARRAY_AGG(DISTINCT ta.source) AS sources
   FROM sp.team_aliases ta
   JOIN sp.teams t ON t.id = ta.team_id
   WHERE t.sport_id = 3
     AND ta.alias_normalized IN (:vtb_manifest_alias_normalized_list)
   GROUP BY ta.alias_normalized
   HAVING COUNT(DISTINCT ta.team_id) > 0;
   ```
4. `python scripts/bootstrap_vtb.py --dry-run` — expect 5 INSERTs +
   6 BACKFILLs (MBA Moscow dynamic resolution)
5. `python scripts/bootstrap_vtb.py` — wet apply;
   `bootstrap.vtb.pattern_d.ok` log confirms production endpoint
6. F7 verification via JOIN template at apply_timestamp + 14-17h,
   `country_code='RUS'` filter
7. **Post-apply collision audit** (MANDATORY per Day-33 HEBA finding —
   pre-apply audit cannot predict post-apply collisions from
   INSERT into existing single-team aliases). Expected 3-5
   collisions based on §4.4 pre-flagged phantoms + potential
   surprises. Remediate via DELETE on `bootstrap_league_coverage`
   source.
8. `sp.baseline_shifts` annotation (event_type=
   `phase_2d5a_vtb_bootstrap`; amendment #19 pre-flight existence
   check)
9. Day-N+1 daily-diff with per-sport rolling-window measurement
   (amendment #20)
