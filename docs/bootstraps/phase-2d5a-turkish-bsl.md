# Phase 2D.5-A Workstream #5 — Turkish Basketbol Süper Ligi (BSL)

**Workstream #5** of Phase 2D.5-A data-driven league bootstrap series.
Selected per Day-31 re-sequencing in
[`phase-2d5a-sequencing-decision.md`](phase-2d5a-sequencing-decision.md).

**Methodology lineage**: mirrors Israeli BSL workstream #4 (PR #215)
single-PR delivery. Three new methodology dimensions surfaced:
empirical-coverage discipline (bare-name aliases INCLUDED for
football-overlap teams, contrast with Israeli BSL exclusion);
diacritic empirical verification + Turkish dotless `ı` exception;
canonical-name fragmentation pattern (dormant phantom acceptance).

---

## 1. Context

Workstreams #1-4 complete (LMB Day-28, Liga ACB Day-29, Italian LBA
Day-31 morning, Israeli BSL Day-31 afternoon). Day-31 afternoon
Pattern A.2 pre-scope discovery query (run post-Israeli-BSL-apply)
revealed Turkish BSL as workstream #5 (~234 records/7d).

Turkish BSL is the fifth empirical iteration:

- LMB — single-country Baseball, no cross-sport
- Liga ACB — multi-country light Basketball, 2-city cross-sport
- Italian LBA — single-country Basketball, 4-city cross-sport
- Israeli BSL — single-country Basketball, 11-city cross-sport
  (operator-clarity exclusion); 5-prefix within-league exclusion
- **Turkish BSL (this workstream)** — single-country Basketball,
  5-club cross-sport (empirical-coverage INCLUSION); 3 new
  methodology dimensions

## 2. Discovery query (Pattern A.2 per amendment #21)

Production `sp.resolution_log` 7-day window, Basketball routing,
Turkish provider patterns. Ran Day-31 afternoon ~post-Israeli-BSL-
apply. 21 distinct provider-form pairs at occurrences ≥7.

In-scope BSL provider forms (map to manifest teams):

| Provider string | Manifest team |
|---|---|
| Galatasaray / Galatasaray SK | Galatasaray |
| Besiktas / Besiktas JK / Besiktas * | Beşiktaş |
| Fenerbahce / Fenerbahce Istanbul / Fenerbahce * | Fenerbahçe |
| Esenler Erokspor | Esenler Erokspor |
| Bursaspor (basketball context) | Bursaspor Basketbol |
| Merkezefendi / Merkezefendi Belediyesi Denizli Basket | Merkezefendi Basket |
| Trabzonspor (basketball context) | Trabzonspor (Basketbol) |
| Manisa | Manisa Basket |
| Mersin SK | Mersin MSK |
| Bahcesehir Kol. / Bahcesehir Kol. * | Bahçeşehir Koleji |

Routes to dormant phantom by design:

| Provider string | Resolution target | Reason |
|---|---|---|
| Turk Telekom | legacy phantom (id d436ec55) | Manifest canonical is `Türk Telekom Ankara`; bare form stays with dormant phantom |
| (Karsiyaka not in discovery) | (would route to legacy ff68785a) | Manifest canonical is `Karşıyaka Basket`; legacy stub stays dormant |

EuroLeague crossover confirmed: `Fenerbahce Istanbul vs BC Olympiakos
Piraeus` 14/7d + `Olympiacos vs Fenerbahce` 7/7d. Cross-workstream
signal preserved for Greek HEBA #6 design (Olympiakos provider form
is `BC Olympiakos Piraeus`).

Out-of-scope: TBL (Türkiye Basketbol Ligi second-division) FL
leakage. Estimated ~80-150/7d per LBA Serie A2/B + Israeli BSL Liga
Leumit pattern; not in v1 manifest. Filed as §6.1 follow-up.

## 3. Roster source

Operator-verified Day-31 paste from Wikipedia "2025-26 Türkiye
Basketbol Süper Ligi" roster. 16 teams.

Operator paste, verbatim:

| # | Team | Location |
|---|---|---|
| 1 | Anadolu Efes | Istanbul |
| 2 | Bahçeşehir Koleji | Istanbul |
| 3 | Beşiktaş Gain | Istanbul |
| 4 | Bursaspor Basketbol | Bursa |
| 5 | Esenler Erokspor | Istanbul |
| 6 | Fenerbahçe Beko | Istanbul |
| 7 | Galatasaray MCT Technic | Istanbul |
| 8 | Glint Manisa Basket | Manisa |
| 9 | Karşıyaka Basket | İzmir |
| 10 | Mersin MSK | Mersin |
| 11 | ONVO Büyükçekmece | Istanbul |
| 12 | Petkim Spor | İzmir |
| 13 | Tofaş | Bursa |
| 14 | Trabzonspor | Trabzon |
| 15 | Türk Telekom | Ankara |
| 16 | Yukatel Merkezefendi Basket | Denizli |

## 4. NEW METHODOLOGY DIMENSIONS

### 4.1 Empirical-coverage discipline (F2 NEW)

Israeli BSL workstream #4 (Day-31 afternoon) EXCLUDED 11 bare-city
aliases for football-overlap teams as **operator-clarity discipline**.

Turkish BSL workstream #5 (this) INCLUDES bare-name aliases for 5
football-overlap teams as **empirical-coverage discipline**:

| BSL canonical | Süper Lig football counterpart | Bare alias INCLUDED |
|---|---|---|
| Galatasaray | Galatasaray SK | "Galatasaray" (30+/7d discovery) |
| Fenerbahçe | Fenerbahçe SK | "Fenerbahçe" + "Fenerbahce" (28+/7d) |
| Beşiktaş | Beşiktaş JK | "Beşiktaş" + "Besiktas" (14+/7d, asterisk) |
| Trabzonspor (Basketbol) | Trabzonspor | "Trabzonspor" (14+/7d) |
| Bursaspor Basketbol | Bursaspor | "Bursaspor" (14/7d) |

**Refinement of F2 framing**: when empirical production data and
operator-clarity discipline conflict, **empirical data wins**. Day-22
sport_id partition is the safety guarantee at the matcher layer; the
operator-clarity layer is optional documentation discipline that
applies when production strings DON'T send bare forms at material
rates.

**Production verification (Day-31 afternoon)**: sp.teams Soccer
rows (sport_id=1) already exist for `Besiktas`, `Bursaspor`,
`Fenerbahce`, `Galatasaray`, `Trabzonspor`. Day-22 sport_id partition
**5th empirical validation** — basketball-side canonicals coexist
safely.

### 4.2 Diacritic empirical verification + dotless `ı` exception

Production sp.team_aliases inspection (Day-31 afternoon):

  - Legacy `Fenerbahçe Gelişim` stored as `fenerbahce gelisim` (ç, ş
    both collapse via NFD + combining-mark strip)
  - Legacy `Tofas` (ASCII-stripped at source) matches manifest `Tofaş`
    via NFD-normalize: `tofas` exact match → BACKFILL predicted

**NFD-decomposing characters** (functional collapse to ASCII):
ş, ç, ü, ğ.

**EXCEPTION — Turkish dotless `ı` (U+0131) does NOT decompose under
NFD.** It's a precomposed base letter, distinct from `i`. Verified
locally:

  - `Karşıyaka` → NFD → `Karşıyaka` (ş decomposes, ı does NOT) →
    `karsıyaka` (still has ı after strip)
  - `Karsiyaka` → `karsiyaka` (regular i)
  - **Different normalized keys.**

For ı-containing teams, both forms FUNCTIONALLY REQUIRED in manifest:

| Canonical | ı-form alias | i-form alias |
|---|---|---|
| Karşıyaka Basket | Karşıyaka Basket | Karsiyaka Basket |
|  | Karşıyaka Basketbol | Karsiyaka Basketbol |
|  | Pınar Karşıyaka | Pinar Karsiyaka |

This is a functional requirement, not documentation belt-and-
suspenders. If normalizer is enhanced to map ı → i in the future,
the belt-and-suspenders pairs become redundant and can be removed
on re-curation.

### 4.3 Canonical-name fragmentation pattern (dormant phantom acceptance)

2 manifest canonicals deliberately diverge from Phase 2A.5 legacy
stubs:

| Manifest canonical | Legacy canonical | Legacy UUID | Decision |
|---|---|---|---|
| Karşıyaka Basket | Karşıyaka | ff68785a-0698-4934-b594-c68ccfdb1711 | Dormant phantom |
| Türk Telekom Ankara | Turk Telekom | d436ec55-a303-49a5-84af-0e3f0e90156b | Dormant phantom |

**Dormant phantom discipline**:
- Legacy stub stays in sp.teams (do NOT delete; preserves historical
  fixture history if any)
- Legacy stub does NOT get BACKFILLed with country_code
- Manifest does NOT add bare-form aliases ("Karşıyaka", "Karsiyaka",
  "Türk Telekom", "Turk Telekom") to new canonical
- Production strings sending bare legacy forms route to dormant
  phantom via canonical_name lookup (still strict-tier resolution,
  just no country_code)
- Manifest follows Wikipedia 2025-26 canonical per F1 amendment #12
  authoritative-source primacy

**Methodology refinement**: when Wikipedia canonical differs from
legacy canonical, accept dormant phantom over canonical compromise.
The alternative (using legacy canonical "Karşıyaka" as manifest
canonical with "Basket" as alias) would compromise authoritative-
source primacy. The alternative (overwriting legacy canonical with
"Karşıyaka Basket") would discard legacy fixture history.

Dormant phantom is the **third option that preserves both
principles**.

## 5. Framing-question decisions (F1–F8)

### F1 — Canonical_name policy

**Decision**: heritage / Wikipedia-canonical form with location-
disambiguator where legacy bare form would conflict. Examples:

- "Galatasaray" canonical ← "Galatasaray MCT Technic" alias
- "Fenerbahçe" canonical ← "Fenerbahçe Beko" alias
- "Türk Telekom Ankara" canonical (NOT bare "Türk Telekom" — dormant
  phantom occupies that slot)
- "Karşıyaka Basket" canonical (NOT bare "Karşıyaka" — dormant phantom)

### F2 — Alias distinctiveness (NEW empirical-coverage discipline)

**Decision**: bare-name aliases INCLUDED for football-overlap teams
when production discovery shows them at material rates. Day-22
sport_id partition validates safety.

See §4.1 for full rationale.

### F3 — Diacritic + dotless-i handling

**Decision**:
- For ş/ç/ü/ğ: both diacritic and ASCII forms in manifest as
  documentation pairs (normalizer collapses them).
- For ı: both `ı` and `i` forms FUNCTIONALLY REQUIRED (normalizer
  does NOT collapse them).

See §4.2 for full rationale.

### F4 — Source value

`bootstrap_league_coverage` (Q3 convention).

### F5 — Country_code

All 16 teams `country_code='TUR'` (single-country league).

### F6 — Bootstrap script structure

Triplet — `scripts/turkish_bsl_seed.py` +
`scripts/bootstrap_turkish_bsl.py` +
`tests/test_bootstrap_turkish_bsl.py`. Direct mirror of
`bootstrap_israeli_bsl.py` structure. Shares
`_check_pattern_d_endpoint` from `scripts/daily_diff.py` per
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
  AND (t_home.country_code = 'TUR' OR t_away.country_code = 'TUR');
```

Expected: ~30-60 strict resolutions in first 14-17h post-apply,
scaled from Israeli BSL's ~50-100 projection adjusted for Turkish
BSL's ~234/7d discovery volume (less than Israeli BSL's ~300/7d).

### F8 — Success criterion

Per amendment #20 — F8 is the F7 league-specific JOIN query showing
≥50% reduction in TUR-attributable asymmetric_anchor_failure records
over 7 days. Aggregate Basketball capability rate is NOT the F8
metric.

## 6. BACKFILL predictions

3 manifest canonicals expected to BACKFILL onto Phase 2A.5 legacy
stubs (per Phase 2A.5 sport_id=3 inspection Day-31 afternoon):

| Manifest canonical | Legacy UUID | Normalize match |
|---|---|---|
| Anadolu Efes | ca2f4866-c4ac-4a26-976f-d54401ce8c1d | exact: `anadolu efes` |
| Bursaspor Basketbol | 85c6d6bf-8ffb-4309-b0aa-9ba3d146ad4c | exact: `bursaspor basketbol` |
| Tofaş | 7f3d7ec1-c48f-48cf-8b8f-089faec3fc53 | NFD: `tofas` matches legacy `tofas` |

13 fresh INSERTs expected.

Predictions documented but NOT enforced — Day-31 afternoon Israeli
BSL finding (Phase 2A.5 coverage non-prominence-correlated) implies
that predictions can be surprising; apply-time empirical verification
is authoritative.

## 7. Open questions / follow-ups

### §7.1 TBL (Türkiye Basketbol Ligi) FL leakage

Expected ~80-150/7d Turkish second-division noise per LBA Serie A2/B
+ Israeli BSL Liga Leumit precedent. Out-of-scope for v1; investigate
FL sport-tier classifier in cross-workstream follow-up.

### §7.2 Türkçe script aliases

N/A — Turkish uses Latin script natively. Pınar Karşıyaka historical
sponsorship form included defensively per ı-handling discipline (§4.2).

### §7.3 EuroLeague crossover handling

Day-31 discovery confirms Fenerbahçe ↔ Olympiakos at material rate
(14+7=21/7d). Cross-workstream signal for Greek HEBA #6 design:
Olympiakos provider form is `BC Olympiakos Piraeus` (Greek-side
canonical). When Greek HEBA workstream ships, these cross-league
fixtures gain full strict-tier coverage on both sides.

Per Liga ACB Day-30 F7 precedent (Panathinaikos + Rytas crossovers
materialized), expect 2-4 EuroLeague crossovers in Turkish BSL F7
(Fenerbahçe vs Olympiakos / Panathinaikos / Maccabi Tel Aviv / Real
Madrid Baloncesto / Virtus Bologna).

## 8. Cross-references

- Parent scope-doc:
  [`phase-2d5a-data-driven-bootstrap.md`](phase-2d5a-data-driven-bootstrap.md)
- Sequencing decision + Day-31 re-sequencing addendum:
  [`phase-2d5a-sequencing-decision.md`](phase-2d5a-sequencing-decision.md)
- Israeli BSL precedent (single-PR delivery, 11-city operator-clarity
  exclusion): [`phase-2d5a-israeli-bsl.md`](phase-2d5a-israeli-bsl.md)
- Italian LBA precedent:
  [`phase-2d5a-italian-lba.md`](phase-2d5a-italian-lba.md)
- Day-22 sport_id partition finding: `resolver/aliases.py:51,111`
- F7 JOIN template: amendment #18 (Day-29 morning)
- v1.5 amendment pile: PROJECT_STATE.md (21 amendments as of
  Day-31 afternoon)

## 9. Apply runbook (post-merge)

1. `git pull` after PR merge
2. Pattern D pre-flight env verification
3. `python scripts/bootstrap_turkish_bsl.py --dry-run` — review
   INSERT / BACKFILL / SKIP counts; expect 13 INSERTs + 3 BACKFILLs
4. `python scripts/bootstrap_turkish_bsl.py` — wet apply;
   `bootstrap.turkish_bsl.pattern_d.ok` log confirms production
   endpoint
5. F7 verification via team_id JOIN template (§F7) at
   apply_timestamp + 14-17 hours, `country_code='TUR'` filter
6. `sp.baseline_shifts` annotation (per amendment #19 pre-flight
   existence check; `event_type='phase_2d5a_turkish_bsl_bootstrap'`)
7. Day-N+1 daily-diff with per-sport rolling-window measurement
   (per amendment #20)
