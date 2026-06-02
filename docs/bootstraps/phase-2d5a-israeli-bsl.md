# Phase 2D.5-A Workstream #4 — Israeli Basketball Premier League

**Workstream #4** of Phase 2D.5-A data-driven league bootstrap series.
Selected per Day-31 re-sequencing in
[`phase-2d5a-sequencing-decision.md`](phase-2d5a-sequencing-decision.md)
(amendment #15 second worked example: domestic-league discovery volume
~300/7d dwarfs EuroLeague-proper residual ~80/7d).

**Methodology lineage**: mirrors Italian LBA workstream #3 (PR #211)
single-PR delivery. Cross-sport collision discipline expanded to
**highest level so far**: 11 of 14 BSL teams have Israeli football
counterparts, plus within-BSL bare-prefix discipline (Maccabi/Hapoel/
Ironi/Bnei/Elitzur all excluded).

---

## 1. Context

Workstreams #1-3 complete (LMB Day-28, Liga ACB Day-29, Italian LBA
Day-31). Day-31 afternoon Pattern A.2 pre-scope discovery query
revealed the next-highest unresolved Basketball population is Israeli
BSL (~300+/7d), not EuroLeague-proper.

Israeli BSL is the fourth empirical iteration:

- **LMB** — single-country Baseball, no cross-sport collision
- **Liga ACB** — multi-country light (ESP+AND) Basketball, cross-sport
  collision (Real Madrid CF, FC Barcelona)
- **Italian LBA** — single-country Basketball, 4-city cross-sport
  collision (Milano, Bologna, Napoli, Venezia)
- **Israeli BSL (this workstream)** — single-country Basketball,
  **11-city cross-sport collision** plus 5-prefix within-league
  discipline

## 2. Discovery query (Pattern A.2 per amendment #21)

Production `sp.resolution_log` 7-day window, Basketball routing,
Israeli + EuroLeague provider patterns. Ran Day-31 afternoon ~25 min
post-LBA-apply.

In-scope BSL provider forms (map to manifest teams):

| Provider string | Manifest team |
|---|---|
| Maccabi Tel Aviv / Maccabi Tel-Aviv | Maccabi Tel Aviv |
| Hapoel Tel Aviv / Hapoel Tel-Aviv | Hapoel Tel Aviv |
| Hapoel Jerusalem | Hapoel Jerusalem |
| Bnei Herzliya / Bnei Herzliya Basket | Bnei Herzliya |
| Hapoel HaEmek / Hapoel Haemek | Hapoel HaEmek |
| Maccabi Rishon LeZion / Maccabi Rishon | Maccabi Rishon LeZion |
| Hapoel Beer Sheva (no apostrophe) | Hapoel Be'er Sheva/Dimona |
| Ironi Kiryat Ata | Ironi Kiryat Ata |
| Hapoel Galil Elyon / Galil Elyon | Hapoel Galil Elyon |
| Elitzur Maccabi Netanya (4-token sponsored) | Elitzur Netanya |

Out-of-scope Liga Leumit (Israeli National League / second division)
provider forms — ~80-150/7d FL leakage noise, not in 2025-26 BSL
Premier roster:

- Maccabi Haifa
- Maccabi Petah Tikva
- Maccabi Kiryat Gat
- Maccabi Maale Adumim
- Migdal Haemek
- Elitzur Yavne

## 3. Roster source

Operator-verified Day-31 paste from Wikipedia "2025-26 Israeli
Basketball Premier League" / Winner League standings table. 14 teams
listed.

Operator paste, verbatim:

| Pos | Team | 2024-25 finish |
|---|---|---|
| 1 | Maccabi Tel Aviv | 1st (50 pts) |
| 2 | Hapoel Tel Aviv | 2nd (48 pts) |
| 3 | Hapoel Jerusalem | 3rd (44 pts) |
| 4 | Bnei Herzliya | 4th (44 pts) |
| 5 | Hapoel Holon | 5th (41 pts) |
| 6 | Hapoel HaEmek | 6th (41 pts) |
| 7 | Maccabi Rishon LeZion | 7th (38 pts) |
| 8 | Hapoel Be'er Sheva/Dimona | 8th (36 pts) |
| 9 | Maccabi Ironi Ramat Gan | 9th (36 pts) |
| 10 | Ironi Kiryat Ata | 10th (35 pts) |
| 11 | Ironi Ness Ziona | 11th (35 pts) |
| 12 | Hapoel Galil Elyon | 12th (33 pts) |
| 13 | Elitzur Netanya | 13th (33 pts) |
| 14 | Maccabi Ironi Ra'anana | 14th (32 pts) |

Israeli BSL is structurally a 14-team league (vs LBA's 16 or ACB's 18).

## 4. Framing-question decisions (F1–F8)

### F1 — Canonical_name policy

**Decision**: heritage / Wikipedia-canonical form. Examples:

- "Maccabi Tel Aviv" canonical ← "Maccabi Playtika Tel Aviv" alias
- "Hapoel Be'er Sheva/Dimona" canonical (retain apostrophe + slash
  per Wikipedia) ← "Hapoel Beer Sheva" alias (Day-31 discovery)

Mirrors LMB, Liga ACB, Italian LBA F1 precedent.

### F2 — Alias distinctiveness + cross-sport collision discipline

**Decision**: HIGHEST cross-sport collision discipline of Phase 2D.5-A.

EXCLUDED bare-city aliases (11 cities; Israeli football overlap):

| BSL team | Football counterpart | Bare alias EXCLUDED |
|---|---|---|
| Maccabi Tel Aviv | Maccabi Tel Aviv FC | "Tel Aviv" |
| Hapoel Tel Aviv | Hapoel Tel Aviv FC | "Tel Aviv" (shared) |
| Hapoel Jerusalem | Beitar Jerusalem FC + Hapoel Jerusalem FC | "Jerusalem" |
| Hapoel Be'er Sheva/Dimona | Hapoel Be'er Sheva FC | "Be'er Sheva" / "Beer Sheva" |
| Hapoel Holon | Hapoel Holon FC | "Holon" |
| Maccabi Ironi Ra'anana | Hapoel Ra'anana FC | "Ra'anana" / "Raanana" |
| Ironi Ness Ziona | Hapoel Ness Ziona FC | "Ness Ziona" |
| Maccabi Ironi Ramat Gan | Hapoel Ramat Gan FC | "Ramat Gan" |
| Bnei Herzliya | Hapoel Herzliya FC | "Herzliya" |
| Maccabi Rishon LeZion | Hapoel Rishon LeZion FC | "Rishon LeZion" / "Rishon" |
| Elitzur Netanya | Maccabi Netanya FC | "Netanya" |

EXCLUDED within-BSL bare prefixes (5 prefixes):

| Prefix | BSL teams sharing | Reason |
|---|---:|---|
| Maccabi | 4 (Tel Aviv, Rishon LeZion, Ironi Ramat Gan, Ironi Ra'anana) | Within-BSL collision |
| Hapoel | 6 (Tel Aviv, Jerusalem, Holon, HaEmek, Be'er Sheva/Dimona, Galil Elyon) | Within-BSL collision |
| Ironi | 4 (Kiryat Ata, Ness Ziona, Maccabi Ironi Ramat Gan, Maccabi Ironi Ra'anana) | Within-BSL collision |
| Bnei | 1 (Herzliya) — current; common Israeli sports-club prefix | Future-promotion collision |
| Elitzur | 1 (Netanya) — current; Elitzur Yavne in Liga Leumit | Future-promotion collision |

SAFE bare aliases (per operator paste; no football collision):

- "HaEmek" / "Haemek" — regional name (Jezreel Valley)
- "Galil Elyon" — regional name (Upper Galilee)
- "Kiryat Ata" — no Israeli football top tier presence

### F3 — Apostrophe + special-character handling

**Decision**: The normalizer (`resolver/_normalize.py`) strips
apostrophe + slash as punctuation. Apostrophe and no-apostrophe forms
produce DIFFERENT normalized keys:

- "Hapoel Be'er Sheva" → `hapoel be er sheva` (apostrophe → space)
- "Hapoel Beer Sheva" → `hapoel beer sheva`

Both must be present as aliases. Same pattern for Ra'anana
(`ra anana` vs `raanana`).

Hyphenated "Tel-Aviv" normalizes identically to "Tel Aviv" (hyphen →
space, whitespace collapses); including both is belt-and-suspenders
documentation.

### F4 — Source value

**Decision**: `bootstrap_league_coverage` (Q3 convention; same as KBL,
LMB, Liga ACB, Italian LBA).

### F5 — Country_code

**Decision**: all 14 teams `country_code='ISR'` (single-country
league).

### F6 — Bootstrap script structure

**Decision**: triplet — `scripts/israeli_bsl_seed.py` (manifest) +
`scripts/bootstrap_israeli_bsl.py` (apply) +
`tests/test_bootstrap_israeli_bsl.py`. Direct mirror of
`bootstrap_lba.py`. Shares `_check_pattern_d_endpoint` from
`scripts/daily_diff.py` per amendment #17.

### F7 — Verification

**Decision**: F7 query uses team_id JOIN to `sp.fixtures` +
`sp.teams` with `country_code='ISR'` filter (per amendment #18; FL
strict reason_detail is sparse).

```sql
SELECT count(*)
FROM sp.resolution_log rl
JOIN sp.fixtures f ON f.id = rl.fixture_id
JOIN sp.teams t_home ON t_home.id = f.home_team_id
JOIN sp.teams t_away ON t_away.id = f.away_team_id
WHERE rl.reason_detail->>'sport' = 'Basketball'
  AND rl.reason_code = 'strict'
  AND rl.decided_at >= :apply_timestamp
  AND (t_home.country_code = 'ISR' OR t_away.country_code = 'ISR');
```

Expected: ~50-80 strict resolutions in first 14-17 hours post-apply,
scaled from Liga ACB's 41/17h with BSL's ~3× higher unresolved volume.

### F8 — Success criterion

**Decision**: per amendment #20 (Day-30) — F8 is the F7 league-specific
JOIN query showing ≥50% reduction in BSL-attributable
asymmetric_anchor_failure records over 7 days. Aggregate Basketball
capability rate is NOT the F8 metric.

## 5. Implementation

Single PR per amendment #14:

- `scripts/israeli_bsl_seed.py` — 14-team manifest, 54 raw aliases /
  43 unique-normalized (same-team apostrophe/hyphen/capitalization
  pairs absorb into 11 in-batch dedupes)
- `scripts/bootstrap_israeli_bsl.py` — apply script (mirrors
  `bootstrap_lba.py`)
- `tests/test_bootstrap_israeli_bsl.py` — manifest-shape, apostrophe
  coverage, hyphen coverage, cross-sport collision discipline (11
  cities + 5 prefixes), Day-31 discovery target coverage, Liga Leumit
  exclusion, roster-membership tests
- `docs/bootstraps/phase-2d5a-israeli-bsl.md` — this scope-doc

## 6. Open questions / follow-ups

### 6.1 Liga Leumit FL leakage (~80-150/7d noise)

Day-31 discovery surfaced 6 Liga Leumit (Israeli National League /
second division) team names at material occurrence rates:

- Maccabi Haifa
- Maccabi Petah Tikva
- Maccabi Kiryat Gat
- Maccabi Maale Adumim
- Migdal Haemek
- Elitzur Yavne

Estimated ~80-150/7d total. Out-of-scope for BSL Premier workstream.

This mirrors the Italian LBA finding (Serie A2/B leakage ~80/7d at
LBA workstream). Same investigation thread: does FL's sport-tier
classifier misroute second-division Basketball records to the
top-tier `sp.teams` matcher? Filed as cross-workstream follow-up.

### 6.2 Hebrew script alias coverage

OUT OF SCOPE for v1 per KBL Issue #165 precedent (ASCII Latin-script
only). Hebrew-script provider strings ("מכבי תל אביב" for Maccabi
Tel Aviv etc.) would require Hebrew-aware normalizer support
(currently the normalizer assumes Latin-script NFD decomposition).

If production data shows material Hebrew-script provider strings
post-apply, escalate to a normalizer extension workstream. Initial
expectation: FL + Kalshi providers send Latin-transliterated forms
exclusively.

### 6.3 Maccabi Tel Aviv EuroLeague crossover handling

Maccabi Tel Aviv is the 6× EuroLeague champion (1977, 1981, 2001,
2004, 2005, 2014). Cross-league fixtures with Spanish (Real Madrid
Baloncesto, FC Barcelona Bàsquet), Greek (Olympiakos, Panathinaikos),
Turkish (Fenerbahce, Anadolu Efes), Italian (Olimpia Milano, Virtus
Bologna), Russian (CSKA Moscow), Lithuanian (Žalgiris, Rytas), and
Israeli (this workstream) teams expected.

Per Liga ACB Day-30 F7 precedent (Panathinaikos + Rytas crossovers
surfaced), expect 1-3 EuroLeague crossovers in Israeli BSL F7. These
resolve cleanly on the BSL side; full strict-tier coverage on both
sides will require the eventual EuroLeague workstream #8 (now a
gap-fill of 4-6 EuroLeague-only teams after #4-7 cover the domestic
leagues).

## 7. Cross-references

- Parent scope-doc:
  [`phase-2d5a-data-driven-bootstrap.md`](phase-2d5a-data-driven-bootstrap.md)
- Sequencing decision + Day-31 re-sequencing addendum:
  [`phase-2d5a-sequencing-decision.md`](phase-2d5a-sequencing-decision.md)
- Italian LBA precedent (single-PR delivery, amendment #21 first
  application): [`phase-2d5a-italian-lba.md`](phase-2d5a-italian-lba.md)
- Day-22 sport_id partition finding: `resolver/aliases.py:51,111`
- F7 JOIN template: amendment #18 (Day-29 morning)
- Apostrophe handling: this workstream first surfaces it; future
  Israeli + Arabic-named workstreams inherit the discipline
- KBL Hangul partial coverage precedent (Issue #165): basis for v1
  Latin-script-only scope

## 8. Apply runbook (post-merge)

1. `git pull` after PR merge
2. Pattern D pre-flight env verification:
   ```
   $env:DATABASE_URL = '<production-Neon-URL>'
   $env:EXPECTED_PRODUCTION_DB_NAME = 'neondb'
   $env:EXPECTED_PRODUCTION_DB_HOST = 'ep-fragrant-frog-ak3esp11'
   ```
3. `python scripts/bootstrap_israeli_bsl.py --dry-run` — review
   INSERT / BACKFILL / SKIP counts
4. `python scripts/bootstrap_israeli_bsl.py` — wet apply;
   `bootstrap.israeli_bsl.pattern_d.ok` log confirms production
   endpoint
5. F7 verification via team_id JOIN template (§4 F7) at
   apply_timestamp + 14-17 hours
6. `sp.baseline_shifts` annotation (per amendment #19 pre-flight
   existence check; `event_type='phase_2d5a_israeli_bsl_bootstrap'`)
7. Day-N+1 daily-diff with per-sport rolling-window measurement
   (per amendment #20)
