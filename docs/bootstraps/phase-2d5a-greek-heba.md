# Phase 2D.5-A Workstream #6 — Greek HEBA A1 (Basket League)

**Workstream #6** of Phase 2D.5-A data-driven league bootstrap series.
Selected per Day-31 re-sequencing in
[`phase-2d5a-sequencing-decision.md`](phase-2d5a-sequencing-decision.md).

**Methodology lineage**: mirrors Turkish BSL workstream #5 (PR #217)
single-PR delivery + F2 NEW empirical-coverage discipline. New
methodology dimensions: Greek-to-Latin transliteration handling
(Olympiakos/Olympiacos, Kolossos Rhodes/Kolossos Rodou) and
4-INSERT / 9-BACKFILL composition with documented Phase 2A.5 UUIDs.

---

## 1. Context

Workstreams #1-5 complete (LMB, Liga ACB, Italian LBA, Israeli BSL,
Turkish BSL — all applied + F7-validated by Day-32). Day-32
afternoon Pattern A.2 pre-scope discovery query confirmed Greek
HEBA A1 (~50-70 records/7d playoffs-window) as workstream #6.

Greek HEBA A1 is the sixth empirical iteration:

- LMB / ACB / LBA / Israeli BSL / Turkish BSL — prior workstreams
- **Greek HEBA A1 (this workstream)** — 13-team Greek Basket League;
  5 football-overlap teams with top-5 Super League recognition
  (Olympiakos, Panathinaikos, AEK Athens, PAOK, Aris)

## 2. Discovery query (Pattern A.2 per amendment #21)

Day-32 afternoon production discovery (playoff-window, 7-day,
Basketball routing, Greek provider patterns):

| Provider string | Volume/7d | Maps to |
|---|---:|---|
| AEK Athens (+ BC AEK Athens + AEK Athens *) | ~75 (highest) | AEK Athens |
| Olympiacos / BC Olympiakos Piraeus / Olympiacos * | ~41 | Olympiakos BC |
| Aris / BC Aris Thessaloniki | ~35 | Aris Thessaloniki |
| Kolossos Rhodes / BC Kolossos Rhodes | ~28 | Kolossos Rhodes |
| Panathinaikos / Panathinaikos BC | ~14 | Panathinaikos BC |

**Inactive in 7-day window** (eliminated from playoffs or low FL
coverage): Iraklis, Karditsa, Maroussi, Mykonos, Panionios, PAOK,
Peristeri, Promitheas. Full-season coverage expected post-apply.

**EuroCup crossovers confirmed**:
- Fenerbahce Istanbul vs BC Olympiakos Piraeus
- BC Rytas Vilnius vs BC AEK Athens
- Unicaja vs AEK Athens *
- BC AEK Athens vs CB Malaga

Same pattern as Liga ACB Day-30 (Panathinaikos + Rytas crossovers)
and Turkish BSL Day-32 (Zalgiris crossover).

## 3. Roster source

Operator-verified Day-33 paste from Wikipedia "2025-26 Greek Basket
League season" roster. **13 teams** (unusual count; HEBA A1 typically
12 or 14).

Roster composition:

| # | Team | Type |
|---|---|---|
| 1 | AEK Athens | INSERT |
| 2 | Aris Thessaloniki | INSERT |
| 3 | Olympiakos BC | INSERT |
| 4 | GS Karditsa | INSERT |
| 5 | Iraklis BC | BACKFILL (c17fa0b9) |
| 6 | Kolossos Rhodes | BACKFILL (ca5f6d4a) |
| 7 | Maroussi BC | BACKFILL (d8e37aa5) |
| 8 | Mykonos | BACKFILL (2f32272a) |
| 9 | PAOK BC | BACKFILL (59eb93a6) |
| 10 | Panathinaikos BC | BACKFILL (6e1268f8) |
| 11 | Panionios | BACKFILL (380f47bc) |
| 12 | Peristeri BC | BACKFILL (6a00a818) |
| 13 | Promitheas Patras BC Vikos Cola | BACKFILL (eb0e7a18) |

## 4. NEW METHODOLOGY DIMENSIONS

### 4.1 Greek-to-Latin transliteration handling

Greek-to-Latin transliteration produces multiple valid forms that
do NOT collapse under NFD (cross-script, not Latin-diacritic):

  - "Olympiakos" (Modern Greek transliteration) ↔ "Olympiacos"
    (FL spelling without k) — DIFFERENT normalized keys
  - "Kolossos Rhodes" (English locative) ↔ "Kolossos Rodou"
    (Greek genitive) — DIFFERENT normalized keys

Both forms required as aliases per F3. Same shape as Turkish BSL's
dotless `ı` exception (Day-31): the normalizer collapses Latin
combining-mark diacritics but does NOT handle cross-script
transliterations.

### 4.2 4-INSERT / 9-BACKFILL composition

Highest BACKFILL ratio of Phase 2D.5-A so far (~69% BACKFILL):

| Workstream | INSERT | BACKFILL | BACKFILL ratio |
|---|---:|---:|---:|
| LMB | 17 | 3 | 15% |
| Liga ACB | 16 | 2 | 11% |
| Italian LBA | 13 | 3 | 19% |
| Israeli BSL | 9 | 5 | 36% |
| Turkish BSL | 11 | 5 | 31% |
| **Greek HEBA A1** | **4** | **9** | **69%** |

Reflects Phase 2A.5 legacy `public.entities` coverage of Greek
basketball clubs (Olympiakos / Panathinaikos / AEK / PAOK / etc.
all had legacy stubs — high-profile EuroLeague/EuroCup teams that
appeared in early provider snapshots).

BACKFILL UUIDs documented in canonical notes per amendment #22
pre-apply audit preparation. Tests verify UUID presence in notes
field (`TestHEBABackfillUUIDs.test_backfill_uuids_documented_in_notes`).

### 4.3 5 dormant phantom collision risks

5 Phase 2A.5 legacy stubs have bare-form canonical_names that may
produce alias collisions with our BACKFILL manifest entries
(amendment #22 pre-apply audit MANDATORY):

| Dormant phantom | UUID | Collides with manifest |
|---|---|---|
| Iraklis | b0602d2c | Iraklis BC BACKFILL ('iraklis' alias) |
| Kolossos Rodou | 7260b8e5 | Kolossos Rhodes BACKFILL ('kolossos rodou' alias) |
| Maroussi | 11fb2774 | Maroussi BC BACKFILL ('maroussi' alias) |
| Peristeri | 0c6092b5 | Peristeri BC BACKFILL ('peristeri' alias) |
| Promitheas | fca05a4b | Promitheas Patras BC Vikos Cola BACKFILL ('promitheas' alias) |

Post-apply remediation pattern (same as Day-31/32 Turkish + Israeli
BSL collision arc):
1. Run amendment #22 audit query
2. Detect alias_tier write-back collisions
3. DELETE collision aliases under alias_tier source
4. Re-verify zero-collision state

Two SEPARATE entities NOT to be aliased:
- EA Promitheas 2014 (4180be23) — youth/reserve, distinct identity
- AS Karditsas (c7da3b82) + Karditsa Iaponiki (77ed94bd) — separate
  Karditsa entities; GS Karditsa is INSERTed fresh

## 5. Framing-question decisions (F1–F8)

### F1 — Canonical_name

BACKFILLs preserve legacy Phase 2A.5 canonical (production-anchor
discipline). INSERTs use city-qualified Wikipedia forms.

Notable: `Promitheas Patras BC Vikos Cola` retains sponsor-prefixed
form per F1 amendment #12 authoritative-source primacy — the legacy
stub IS the production-anchor; changing the canonical breaks legacy
fixture associations.

### F2 — Alias distinctiveness (F2 NEW empirical-coverage discipline)

Bare club aliases INCLUDED for 5 football-overlap teams (per Turkish
BSL workstream #5 precedent):

| HEBA team | Greek Super League FC counterpart | Bare alias INCLUDED |
|---|---|---|
| Olympiakos BC | Olympiakos FC (top-5 recognition) | "Olympiakos" + "Olympiacos" |
| Panathinaikos BC | Panathinaikos FC (top-5) | "Panathinaikos" |
| AEK Athens | AEK Athens FC | "AEK" + "AEK Athens" |
| PAOK BC | PAOK FC (top-5) | "PAOK" |
| Aris Thessaloniki | Aris FC | "Aris" |

Bare city aliases EXCLUDED (too generic + cross-sport collision):
- Athens, Thessaloniki, Piraeus, Patras, Marousi, Rhodes, BC

### F3 — Diacritics + cross-script transliterations

- Latin diacritics: NFD collapse (no current HEBA team requires)
- Greek-to-Latin transliterations: BOTH forms required as separate
  aliases (Olympiakos/Olympiacos, Kolossos Rhodes/Kolossos Rodou)
  per §4.1

### F4 — Source value

`bootstrap_league_coverage` (Q3 convention).

### F5 — Country code

All 13 teams `country_code='GRC'` (single-country league).

### F6 — Bootstrap script structure

Triplet — `scripts/heba_seed.py` + `scripts/bootstrap_heba.py` +
`tests/test_bootstrap_heba.py`. Direct mirror of
`bootstrap_turkish_bsl.py`. Shares `_check_pattern_d_endpoint` per
amendment #17.

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
  AND (t_home.country_code = 'GRC' OR t_away.country_code = 'GRC');
```

Expected: ~25-50 strict resolutions in first 14-17h post-apply
(playoffs-only window; scaled from prior workstreams).

### F8 — Success criterion

Per amendment #20 — F8 is the F7 league-specific JOIN query showing
≥50% reduction in GRC-attributable asymmetric_anchor_failure records
over 7 days. Aggregate Basketball capability rate NOT the F8 metric.

## 6. Open questions / follow-ups

### §6.1 Non-playoff HEBA teams (8 of 13 inactive in 7-day window)

Iraklis, Karditsa, Maroussi, Mykonos, Panionios, PAOK, Peristeri,
Promitheas had zero Day-32 discovery volume (eliminated from
playoffs). F7 will only show playoff-active teams initially; full
13-team coverage validates over full season. Re-measure F7 at
Day-N+7 and Day-N+14 to confirm full-roster strict-tier reach.

### §6.2 EuroCup crossover handling

Olympiakos and AEK Athens active in EuroCup; BC-prefixed forms
already in manifest. Crossover fixtures (Fenerbahce ↔ BC Olympiakos
Piraeus, BC Rytas ↔ BC AEK Athens, Unicaja ↔ AEK Athens *, BC AEK
Athens ↔ CB Malaga) will produce strict resolutions on HEBA side
immediately; full both-sides coverage requires the eventual
EuroLeague workstream #8 gap-fill.

### §6.3 GS Karditsa post-apply monitoring

Two legacy Phase 2A.5 stubs (AS Karditsas c7da3b82, Karditsa
Iaponiki 77ed94bd) exist but are SEPARATE entities; INSERT fresh.
If FL sends a different Karditsa form than "GS Karditsa" / "Karditsa"
/ "Karditsa BC", alias expansion may be needed post-F7.

### §6.4 Dormant phantom collision remediation (Amendment #22)

5 dormant phantoms identified pre-apply (Iraklis b0602d2c, Kolossos
Rodou 7260b8e5, Maroussi 11fb2774, Peristeri 0c6092b5, Promitheas
fca05a4b). Post-apply, alias_tier write-back may create collisions
requiring Day-N remediation (same pattern as Day-31/32 Turkish +
Israeli BSL collision arc).

Pre-apply audit is MANDATORY per amendment #22.

## 7. Cross-references

- Parent scope-doc:
  [`phase-2d5a-data-driven-bootstrap.md`](phase-2d5a-data-driven-bootstrap.md)
- Sequencing decision + Day-31 re-sequencing addendum:
  [`phase-2d5a-sequencing-decision.md`](phase-2d5a-sequencing-decision.md)
- Turkish BSL precedent (F2 NEW empirical-coverage discipline,
  dormant phantom acceptance): [`phase-2d5a-turkish-bsl.md`](phase-2d5a-turkish-bsl.md)
- Israeli BSL precedent (5-prefix + 11-city exclusion):
  [`phase-2d5a-israeli-bsl.md`](phase-2d5a-israeli-bsl.md)
- Italian LBA precedent (asterisk-suffix pattern):
  [`phase-2d5a-italian-lba.md`](phase-2d5a-italian-lba.md)
- Day-22 sport_id partition finding: `resolver/aliases.py:51,111`
- F7 JOIN template: amendment #18 (Day-29 morning)
- Amendment #22 pre-apply alias-claim audit: PROJECT_STATE.md
  Day-32 morning
- v1.5 amendment pile: PROJECT_STATE.md (22 amendments as of
  Day-32 morning)

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
     AND ta.alias_normalized IN (:heba_manifest_alias_normalized_list)
   GROUP BY ta.alias_normalized
   HAVING COUNT(DISTINCT ta.team_id) > 0;
   ```
   Any rows returned must be resolved before apply (DELETE legacy
   alias OR omit manifest alias).
4. `python scripts/bootstrap_heba.py --dry-run` — expect 4 INSERTs + 9 BACKFILLs
5. `python scripts/bootstrap_heba.py` — wet apply;
   `bootstrap.heba.pattern_d.ok` log confirms production endpoint
6. F7 verification via JOIN template at apply_timestamp + 14-17h,
   `country_code='GRC'` filter
7. **Post-apply collision audit** (alias_tier write-back may have
   created new collisions on dormant phantoms — remediate per
   Day-31/32 pattern)
8. `sp.baseline_shifts` annotation (event_type=
   `phase_2d5a_heba_bootstrap`; amendment #19 pre-flight existence
   check)
9. Day-N+1 daily-diff with per-sport rolling-window measurement
   (amendment #20)
