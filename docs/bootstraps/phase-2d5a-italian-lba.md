# Phase 2D.5-A Workstream #3 — Italian LBA Serie A

**Workstream #3** of Phase 2D.5-A data-driven league bootstrap series.
Selected over EuroLeague (scope-doc §5 default) per sequencing
decision committed Day-28 in
[`phase-2d5a-sequencing-decision.md`](phase-2d5a-sequencing-decision.md).

**Methodology lineage**: mirrors Liga ACB workstream #2 (PR #204)
closely. Cross-sport collision discipline (now empirically validated
Day-30 morning for Real Madrid Baloncesto / FC Barcelona Bàsquet)
extended to Italian Serie A football overlap (AC Milan, Bologna FC,
SSC Napoli, Venezia FC). Single-country (ITA) tighter scope than ACB's
ESP+AND.

---

## 1. Context

Day-28 (LMB+ACB apply complete) and Day-30 morning (Liga ACB F7
empirically validated) closed the methodology iteration for
single-country basketball bootstraps. Italian LBA is the third
empirical iteration:

- **LMB** — single-country Baseball, no cross-sport collision (Day-28
  apply, Day-29 morning F7: 18 strict resolutions / 6 teams)
- **Liga ACB** — multi-country light (ESP+AND) Basketball with
  cross-sport collision (Real Madrid CF, FC Barcelona); apply
  Day-29 afternoon, F7 Day-30 morning: 41 strict resolutions /
  11 manifest teams + 2 EuroLeague crossovers
- **Italian LBA (this workstream)** — single-country (ITA) Basketball
  with cross-sport collision (4 Italian Serie A football overlaps),
  empirically-driven manifest from Day-28/Day-30 discovery query

## 2. Discovery query (Pattern A.2)

Production `sp.resolution_log` 7-day window, Basketball routing,
Italian-city patterns:

| Provider string | Occurrences/7d | Mapped manifest team |
|---|---:|---|
| Fortitudo Bologna | 28 | **Out-of-scope** — Serie A2 |
| Trieste vs Brescia | 28 | Pallacanestro Trieste 2004 / Pallacanestro Brescia |
| Brescia vs Trieste | 28 | (same pair, reverse order) |
| Olimpia Milano vs Reggiana | 28 | Olimpia Milano / Pallacanestro Reggiana |
| Brescia vs Olimpia Milano | 24 | Pallacanestro Brescia / Olimpia Milano |
| Reggiana vs Olimpia Milano | 14 | Pallacanestro Reggiana / Olimpia Milano |
| Tortona vs Brescia | 14 | Derthona Basket / Pallacanestro Brescia |
| Treviso vs Reggiana | 14 | Universo Treviso Basket / Pallacanestro Reggiana |
| Reggiana vs Basket Napoli | 14 | Pallacanestro Reggiana / Napoli Basket |
| Brescia vs Sassari | 14 | Pallacanestro Brescia / Dinamo Sassari |
| Brescia * vs Trieste | 14 | Pallacanestro Brescia (asterisk-suffix) / Trieste |
| Cantu vs Treviso | 14 | Pallacanestro Cantù / Universo Treviso Basket |
| Olimpia Milano vs Brescia | 14 | (same pair, reverse order) |
| Verona * vs Fortitudo Bologna | 14 | **Out-of-scope** — Serie A2 (both teams) |
| Virtus Gvm Roma 1960 vs Rucker San Vendemiano | 10 | **Out-of-scope** — Serie A2/B |
| Rucker San Vendemiano vs Virtus Gvm Roma 1960 | 6 | **Out-of-scope** — same pair |

### Discovery findings

1. **Provider strings use heritage/short forms**, not current sponsored
   names. Manifest canonical_name policy (F1) uses heritage forms;
   sponsor forms become aliases.
2. **Asterisk-suffix pattern**: "Brescia *" and "Verona *" appear
   alongside non-asterisk forms. Source of the asterisk is not yet
   characterized (filed as follow-up). Manifest includes "Brescia *"
   as an alias on Pallacanestro Brescia to route to strict tier.
3. **Out-of-scope Serie A2/B leakage** via FL provider channel:
   - Fortitudo Bologna (28/7d) — Serie A2
   - Verona / Verona * (28+14/7d) — Tezenis Verona plays Serie A2
   - Virtus Gvm Roma 1960 (10/7d) — Serie A2/B
   - Rucker San Vendemiano (6/7d) — Serie A2
   - These represent ~80/7d of FL traffic with apparent sport-tier
     misclassification. Out-of-scope for this workstream; investigate
     as follow-up.
4. **Within-Italy "Virtus" collision risk**: Virtus Bologna (LBA),
   Virtus Roma 1960 (A2), Virtus Cassino (lower). Manifest excludes
   bare "Virtus" alias; always qualified with city.
5. **Cross-sport collision targets**: Milano, Bologna, Napoli,
   Venezia. All bare-city aliases for these four EXCLUDED.

## 3. Roster source

Operator-verified Day-30 paste from Wikipedia "2025-26 LBA season"
roster table. 16 teams listed at the time of bootstrap. Single PR
(per amendment #14) carries scope-doc + manifest + bootstrap script
+ tests.

Operator paste, verbatim:

| Team | Home city | 2024-25 result |
|---|---|---|
| Dinamo Sassari | Sassari | 10th |
| Derthona Basket | Tortona | 9th |
| Aquila Basket Trento | Trento | 5th |
| Olimpia Milano | Milan | 4th |
| APU Udine | Udine | promoted to LBA |
| Pallacanestro Brescia | Brescia | 2nd |
| Pallacanestro Cantù | Cantù | promoted to LBA |
| Napoli Basket | Naples | 14th |
| Universo Treviso Basket | Treviso | 11th |
| Pallacanestro Varese | Varese | 12th |
| Pallacanestro Trieste 2004 | Trieste | 6th |
| Trapani Shark | Trapani | 3rd |
| Reyer Venezia | Venice | 8th |
| Pallacanestro Reggiana | Reggio Emilia | 7th |
| Vanoli Cremona | Cremona | 13th |
| Virtus Bologna | Bologna | 1st (defending champion) |

## 4. Framing-question decisions (F1–F8)

Same framing matrix shape as Liga ACB (PR #204).

### F1 — Canonical_name policy

**Decision**: heritage / sport-historical form, not current sponsor
form. Examples:

- "Olimpia Milano" canonical ← "EA7 Emporio Armani Milano" alias
- "Pallacanestro Brescia" canonical ← "Germani Brescia" alias
- "Pallacanestro Reggiana" canonical ← "UnaHotels Reggio Emilia" alias
- "Aquila Basket Trento" canonical ← "Dolomiti Energia Trentino" alias
- "Derthona Basket" canonical ← "Bertram Yachts Tortona" alias

Mirrors LMB (Bravos de León) and Liga ACB (Real Madrid Baloncesto,
FC Barcelona Bàsquet) F1 precedent.

### F2 — Alias distinctiveness + cross-sport collision discipline

**Decision**: bare city aliases for collision-risk cities EXCLUDED.

EXCLUDED bare-city aliases (Italian Serie A football overlap):
- "Milano" — AC Milan, Inter Milan
- "Bologna" — Bologna FC
- "Napoli" — SSC Napoli
- "Venezia" — Venezia FC

EXCLUDED within-LBA bare alias:
- "Virtus" — multiple Italian basketball clubs (Bologna LBA, Roma
  1960 A2, Cassino) share this name

SAFE bare-city aliases (per operator paste):
- Trieste, Brescia, Trento, Sassari, Tortona, Treviso, Cantu,
  Reggiana, Varese, Cremona, Trapani, Udine

### F3 — Diacritic handling

**Decision**: ASCII + accented variants for Pallacanestro Cantù
("Cantù" + "Cantu", "Pallacanestro Cantù" + "Pallacanestro Cantu").
Normalizer NFD-strips accents (`resolver/alias_tier/normalize.py:104-108`)
so both resolve to the same normalized key; including both is
belt-and-suspenders.

### F4 — Source value

**Decision**: `bootstrap_league_coverage` (same as KBL, LMB, Liga ACB
per Q3 convention).

### F5 — Country_code

**Decision**: all 16 teams `country_code='ITA'` (single-country
league; no Andorra-style exception).

### F6 — Bootstrap script structure

**Decision**: triplet — `scripts/lba_seed.py` (manifest) +
`scripts/bootstrap_lba.py` (apply) + `tests/test_bootstrap_lba.py`.
Direct mirror of `bootstrap_acb.py` structure. Shares
`_check_pattern_d_endpoint` from `scripts/daily_diff.py` per
amendment #17.

### F7 — Verification

**Decision**: F7 query uses team_id JOIN to `sp.fixtures` +
`sp.teams` with `country_code='ITA'` filter, NOT the
`reason_detail->>'home_provider_normalized'` JSON path. Day-29
morning finding (amendment #18) showed FL strict-tier resolutions
leave the JSON name fields NULL; team_id JOIN bypasses this.

```sql
SELECT count(*)
FROM sp.resolution_log rl
JOIN sp.fixtures f ON f.id = rl.fixture_id
JOIN sp.teams t_home ON t_home.id = f.home_team_id
JOIN sp.teams t_away ON t_away.id = f.away_team_id
WHERE rl.reason_detail->>'sport' = 'Basketball'
  AND rl.reason_code = 'strict'
  AND rl.decided_at >= :apply_timestamp
  AND (t_home.country_code = 'ITA' OR t_away.country_code = 'ITA');
```

Expected: ~25-40 strict resolutions in first 14-17 hours post-apply
(scaled from Liga ACB's 41/17h with LBA's ~110 records/7d vs ACB's
~70 records/7d ratio — LBA discovery volume modestly higher, so
similar or somewhat higher F7 yield expected).

### F8 — Success criterion

**Decision**: per amendment #20 (Day-30 morning), aggregate
`matcher_capability_rate` is denominator-sensitive to record-mix;
F8 is F7 league-specific JOIN query showing ≥50% reduction in
asymmetric_anchor_failure for LBA-attributable records over a
7-day window post-apply. Aggregate Basketball capability rate is
NOT the F8 metric (per Liga ACB pattern; Day-29 afternoon
hypothesis 1 confirmed for LMB if Baseball stabilizes at 76-78%).

## 5. Implementation

Triplet delivered in a single PR per amendment #14:

- `scripts/lba_seed.py` — 16-team manifest, ~95 aliases
- `scripts/bootstrap_lba.py` — apply script (mirrors `bootstrap_acb.py`)
- `tests/test_bootstrap_lba.py` — manifest-shape + diacritic +
  cross-sport collision + discovery-target + roster-membership tests
- `docs/bootstraps/phase-2d5a-italian-lba.md` — this scope-doc

## 6. Open questions / follow-ups

### 6.1 Trapani Shark exclusion status

Day-30 WebSearch snippet referenced "Trapani Shark excluded on
2026-01-12." Wikipedia roster for 2025-26 LBA still lists Trapani
Shark. Decision: include in manifest per Wikipedia. If F7
post-apply shows zero Trapani strict resolutions over 7 days,
follow-up REMOVE-from-manifest is safe (idempotent script).

### 6.2 Asterisk-suffix source

"Brescia *" and "Verona *" production strings have unknown source
for the trailing asterisk. Manifest treats them as aliases for the
non-asterisk team. Investigate via FL provider parser or operator
spot-check on raw provider payloads as follow-up.

### 6.3 Serie A2/B leakage via FL

~80/7d of asymmetric_anchor_failure records reference Serie A2/B
teams (Fortitudo Bologna, Tezenis Verona, Virtus Roma 1960, Rucker
San Vendemiano). These are out-of-scope for LBA Serie A bootstrap.
Investigate whether FL's sport tier classifier misroutes Serie A2
records to Basketball matcher (which has no Serie A2 canonicals)
when they should route to a separate sport_id or be filtered
upstream.

## 7. Cross-references

- Parent scope-doc:
  [`phase-2d5a-data-driven-bootstrap.md`](phase-2d5a-data-driven-bootstrap.md)
- Sequencing decision:
  [`phase-2d5a-sequencing-decision.md`](phase-2d5a-sequencing-decision.md)
- Liga ACB precedent: PR #204 (single-PR delivery convention)
- LMB precedent: PRs #202 + #203 (split-PR delivery, pre-amendment #14)
- Day-22 sport_id partition finding: `resolver/aliases.py:51,111`,
  `resolver/alias_tier/candidates.py:106`
- F7 JOIN template: amendment #18 (Day-29 morning finding)
- v1.5 amendment pile: PROJECT_STATE.md (20 amendments as of Day-30 morning)

## 8. Apply runbook (post-merge)

1. `git pull` after PR merge
2. Pattern D pre-flight env verification:
   ```
   $env:DATABASE_URL = '<production-Neon-URL>'
   $env:EXPECTED_PRODUCTION_DB_NAME = 'neondb'
   $env:EXPECTED_PRODUCTION_DB_HOST = 'ep-fragrant-frog-ak3esp11'
   ```
3. `python scripts/bootstrap_lba.py --dry-run` — review INSERT /
   BACKFILL / SKIP counts
4. `python scripts/bootstrap_lba.py` — wet apply; `pattern_d.ok` log
   line confirms production endpoint
5. F7 verification via team_id JOIN template (§4 F7) at
   apply_timestamp + 14-17 hours
6. `sp.baseline_shifts` annotation (per amendment #19: pre-flight
   SELECT to confirm no duplicate row before INSERT;
   event_type='phase_2d5a_lba_bootstrap')
7. Day-N+1 daily-diff measurement (Basketball capability rate +
   per-sport rolling window per amendment #20)
