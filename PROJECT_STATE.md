# PROJECT_STATE.md

Living state of the SP Architecture rebuild. Each session updates this
file with what landed, what was investigated, and what's open for the
next session. Treat it as the project's running journal.

---

## Session — 2026-05-28

### Day-28 morning baseline + Tennis dedup +5.90pp validation

Day-28 daily-diff (Tennis dedup lift HELD AND EXTENDED from Day-27):

| Sport | Day-22 | Day-26 (pre-dedup) | Day-27 (post-dedup partial) | Day-28 (full post-dedup) |
|---|---:|---:|---:|---:|
| Tennis | 27.97% | 15.98% | 20.15% | **21.88%** (+5.90pp cumulative) |
| Baseball | 85.17% | — | — | 85.17% (within noise, no leakage) |
| Overall | 51.02% | 47.59% | 46.51% | 46.51% |

Multi-day apples-to-apples via `metrics->'scope_filtered'->>'matcher_capability_rate_overall'`. The +5.90pp Tennis lift from the Day-26 baseline is the empirical close on the Tennis dedup workstream — extends beyond the +4.17pp partial-window measurement on Day-27, confirming the consolidated player population produces durably fewer collision events.

### LMB bootstrap apply (Phase 2D.5-A first deliverable)

Apply at 19:41 UTC. Clean execution:

- **17 new LMB canonicals inserted** (sp.teams, sport_id=6, country_code='MEX')
- **3 BACKFILLs**: Bravos de León, Caliente de Durango, Toros de Tijuana — already existed as stubs with NULL country_code. Three-branch classifier correctly detected and queued UPDATE (not INSERT). KBL precedent's Phase 1.5 backfill discipline working as designed.
- **63 aliases inserted** (sp.team_aliases, source='bootstrap_league_coverage')
- **0 global conflicts, 0 errors**
- Runtime: 10.7s
- Pattern D pre-flight confirmed production endpoint pre-write

Post-apply production state: 289 baseball teams (was 269), 20 MEX (was 0), 269 untouched.

`sp.baseline_shifts` annotation: `f0f99c99-1c1d-4840-beea-6465bfd03e30` (event_type='dedup_bootstrap', event_date=2026-05-28).

Day-29 daily-diff will measure the LMB-attributable Baseball lift (expected ~5-10pp depending on what fraction of the ~600 weekly LMB records reach strict tier via the new aliases).

### Liga ACB scope-doc + manifest + bootstrap script (PR #204, merged)

Production-data discovery revealed Liga ACB is NOT the highest-volume next workstream:

| League | Records/7d |
|---|---:|
| Polish PLK | ~150 |
| VTB United (Russian) | ~120 |
| German BBL | ~110 |
| Italian LBA | ~110 |
| Liga ACB | ~70 |

Decision: ship Liga ACB next anyway per scope-doc §5 (methodology validation #2 for cross-sport collision pattern). Re-evaluate sequencing after Liga ACB applies cleanly. This is **v1.5 amendment**: bootstrap workstream sequencing should re-evaluate after each apply based on production-data discovery (Pattern G extension — scope-doc priority order can be overridden by empirical evidence).

**Wikipedia verification**: 7 of 10 questionable canonicals confirmed correct as drafted (Bàsquet Manresa, Bilbao Basket, CB Canarias, CB Gran Canaria, CB San Pablo Burgos, Força Lleida CE, UCAM Murcia CB). 3 accepted on operator judgment (Río Breogán, Basket Zaragoza, Saski Baskonia).

**Liga ACB manifest**: 18 teams, 98 aliases, 2 country codes (ESP + AND for BC Andorra), cross-sport collision discipline applied:

- **Real Madrid Baloncesto** canonical (not "Real Madrid") — distinguishes from Real Madrid CF soccer canonical
- **FC Barcelona Bàsquet** canonical (not "FC Barcelona") — same discipline
- Bare "Real Madrid" and "Barcelona" aliases safe under sport_id partition (Day-22 finding empirically validated)
- "Madrid" bare alias INTENTIONALLY EXCLUDED — too generic; risks future Madrid-area basketball clubs (Estudiantes)

**bootstrap_acb.py improvement over bootstrap_lmb.py**: reuses `_check_pattern_d_endpoint` from `daily_diff.py` as shared function. bootstrap_lmb.py only logs `current_database`. Backport noted as follow-up (worth normalizing once we have 3+ bootstrap scripts in the cohort).

PR shipped as single PR (scope-doc + manifest + script + tests) rather than two — methodology proven via LMB, so the LMB precedent of two PRs is unnecessary scaffolding for ACB. **v1.5 amendment**: calibrate PR granularity by methodology maturity.

### v1.5 amendment pile (Days 27-28 additions)

The pile, updated for findings across both days:

1-11. (Unchanged from end-of-day-22 entry)

12. **NEW — Multi-agent verification handoffs require artifact paste, not summary or line references.** Today (Day-28): 3 rounds for LMB manifest verification, 1 round for ACB. Improvement curve visible. The pattern: when claiming "verified" / "correct" / "confirmed", the underlying artifact (Python tuple list, SQL query result, file content) must be pasted as a code block. Line references ("see lines 109-112") and summaries ("all 20 teams verified") are insufficient — same epistemic risk as PR #198's first-commit-only merge incident (claimed correct, actually missing the second commit).

13. **NEW — Pattern A.2 applies to data sources, not just code.** Wikipedia verification caught 6 missing teams + 2 phantoms in initial LMB draft (Claude Code's general-knowledge manifest was the wrong-roster shape for the 2026 season). Authoritative source verification cannot be skipped because the deliverable "looks reasonable." Same discipline as Pattern A's "production-data verification" but applied to upstream reference sources.

14. **NEW — Sequential PRs > bundled PRs when methodology is being validated; bundle once proven.** LMB shipped as 2 PRs (scope-doc + implementation) because the data-driven-bootstrap methodology was new. Liga ACB shipped as single PR because the methodology proved out on LMB. Calibrate PR granularity by methodology maturity, not by deliverable size.

15. **NEW — Bootstrap leverage ≠ total-daily-volume (Pattern G extension).** Workstream sequencing should re-evaluate after each apply based on production-data discovery. Liga ACB was scope-doc §5 priority #2 but production data shows 4 other leagues with higher volume (PLK, VTB, BBL, LBA). Scope-doc priority order is a default starting point, not a commitment — empirical evidence overrides.

16. **NEW — 3-letter ISO country codes are the established `sp.teams` convention.** Verified Day-27 via `SELECT country_code, count(*) FROM sp.teams GROUP BY country_code`: KOR has 11 teams, plus 9 other countries with 1 team each. All Phase 2D.5-A bootstraps use MEX, ESP, AND, etc. Day-27 LMB seed prompt incorrectly initially considered 2-letter codes.

17. **NEW — Pattern D pre-flight as shared function** is better than per-script implementation. `bootstrap_acb.py` imports `_check_pattern_d_endpoint` from `daily_diff.py`. `bootstrap_lmb.py` has an inline implementation that should be backported. Future bootstraps should import the shared function from day one.

Pile expanded from 11 to 17 items.

### PR state at end-of-day-28

- **PR #202** — Phase 2D.5-A scope-doc + LMB seed manifest. **Merged Day-27.**
- **PR #203** — bootstrap_lmb.py + 12 tests. **Merged Day-27.**
- **PR #204** — Liga ACB scope-doc + manifest + bootstrap_acb.py + 17 tests (single PR per methodology maturity). **Merged Day-28.**

### Pending — next, operator review

1. **Day-29 daily-diff** — measure LMB-attributable Baseball lift (F7 verification query per scope-doc).
2. **Liga ACB apply** — if Day-29 confirms LMB healthy: Pattern D pre-flight → dry-run → wet apply → F7 → baseline_shifts annotation.
3. **Next-league sequencing decision** — original scope-doc says EuroLeague (~250 records/7d); production data suggests Polish PLK / VTB / German BBL / Italian LBA all have higher volume. Decide based on Day-29 discovery query, not scope-doc default.
4. **Backport Pattern D pre-flight import** to `bootstrap_lmb.py` — small follow-up PR for consistency.

---

## Session — 2026-05-27

### Day-27 morning: Tennis dedup post-apply validation

Day-27 daily-diff confirmed Tennis dedup workstream produced measurable lift (Day-26 → Day-27, partial window):

| Date | Tennis | Overall | Schema |
|---|---:|---:|---|
| Day-22 | 27.97% | 51.02% | v0.2.0 |
| Day-26 (pre-dedup baseline) | 15.98% | 47.59% | v0.3.0 |
| Day-27 (post-dedup partial) | 20.15% | 46.51% | v0.3.0 |

Apples-to-apples comparison via `metrics->'scope_filtered'->>'matcher_capability_rate_overall'`. Tennis +4.17pp partial-window lift validated the dedup mechanism; magnitude consistent with theoretical ~5pp ceiling.

### Day-27 morning: 9-layer Pattern A.2 investigation chain

Originally scoped as "Phase 2D.5 FL alias expansion" investigation. The diagnostic chain reframed the workstream entirely:

**Layer 1** — FL Tennis failure population query:
```sql
SELECT count(*) FROM sp.resolution_log
WHERE provider = 'fl' AND reason_detail->>'sport' = 'Tennis'
  AND reason_code = 'no_match'
  AND reason_detail->>'home_provider_normalized' IS NULL;
-- 45,196 records (94% of FL Tennis failures in 7-day window)
```

**Layer 2** — Sample `reason_detail` for the NULL-form population: explicit `fail_reason='deferred_to_2d'` + `alias_resolution_incomplete` flags. Resolver is correctly tagging records for fuzzy-tier handoff at strict + alias tier.

**Layer 3** — Provider comparison: Kalshi 66.7% NULL form, FL 67.0% NULL form. Not FL-specific — shared matcher path behavior across providers.

**Layer 4** — Single `provider_record_id` trace (GQfsoI5F) across 7 days: same 3-entry pattern repeating daily (`no_match` + `alias_resolution_incomplete`, `no_match` + `deferred_to_2d`, `review_queue` terminal). **No fuzzy-tier follow-up entries.** Suggests fuzzy tier isn't picking up the handoff.

**Layer 5** — 30-day reason_code distribution:

| reason_code | count | % |
|---|---:|---:|
| no_match | 978,144 | 89.0% |
| review_queue | 117,228 | 10.7% |
| strict | 26,403 | 2.4% |
| alias | 238 | 0.02% |
| fuzzy | **80** | 0.007% |

Only 80 fuzzy auto-applies in 30 days against 155K fuzzy invocations.

**Layer 6** — Sport breakdown of 80 fuzzy successes: 100% Tennis, 100% Kalshi provider. Fuzzy works for Tennis when provider sends populated short forms; no successes against FL.

**Layer 7** — `resolver_version='fuzzy@2d.0'` 7-day stats:

| reason_code | count |
|---|---:|
| no_match | 114,442 |
| review_queue | 40,586 |
| fuzzy (auto) | 63 |

155,091 invocations, 0.04% auto-apply rate. Fuzzy IS running, just rarely auto-applying.

**Layer 8** — 39,481 of 40,586 review_queue entries (97.3%) have NULL `fuzzy_score_breakdown.total`. Fuzzy didn't compute confidence for them — they reached review_queue via a different routing path.

**Layer 9** — `sp.review_queue` status: 10,506 pending, 9 lifetime processed (7 approved + 2 rejected). The review_queue is a sink, not a queue. Architecture §7.5 specified <20 target depth and >100 alert threshold; production is at 10,506.

Sample reason_detail for the NULL-fuzzy-score population reveals the actual shape: `home_collision: true OR away_collision: true`, `colliding_home_team_ids` / `colliding_away_team_ids` arrays populated, `home_team_id` or `away_team_id` NULL on the colliding side. **These are collision-bound records, not "fuzzy found low-confidence candidate" cases.**

### Day-27 morning: Phase 2D.5 reframe to data-driven league bootstrap

Sample of the 39K collision-bound records shows the unresolved provider strings cluster by missing league coverage:

- **LMB (Mexican Baseball)**: Monterrey, Puebla, Queretaro, Tabasco
- **Liga ACB (Spanish Basketball)**: Real Madrid, Baskonia, Joventut, Breogan
- **European Baseball**: Parma, Bologna, Hamburg, Mainz, Ostrava, Rouen
- **EuroLeague**: Olympiacos, Panathinaikos, Fenerbahce, CSKA, Maccabi Tel Aviv
- **Polish PLK + Czech NBL + Israeli BSL**: Legia, Slask Wroclaw, Karvina, Hapoel Jerusalem

Architectural finding via code survey: `sp.teams` is populated ONLY by explicit bootstrap scripts (KBL, national teams) and operator-approved review_queue entries. Anything not in a bootstrap manifest AND not operator-approved is missing — regardless of how professional or active the team is. FL ingestion writes only to `sp.fl_events`; it does NOT auto-create `sp.teams` rows.

Phase 2D.5-A reframed: **data-driven league bootstrap using resolver failure signal as discovery source**. Inverts KBL methodology's discovery direction — instead of "which teams exist in this league?" the question becomes "which teams does the resolver fail on most?"

### Day-27 afternoon: Phase 2D.5-A scope-doc (PR #202)

F1-F8 framing decisions locked per KBL precedent:

- **F1 — Canonical_name policy**: Use official team name; provider-variant forms as aliases. KBL F1 precedent.
- **F2 — Alias distinctiveness**: Bare city-name aliases safe within `sport_id` partition. Day-22 finding (`resolver/aliases.py:51,111` + `resolver/alias_tier/candidates.py:106`). Within-sport collision is the only risk; cross-sport is architecturally prevented.
- **F3 — Diacritics**: Both accented and ASCII-stripped variants included. Normalizer (NFD decomposition) handles either, but belt-and-suspenders.
- **F4 — Source value**: `bootstrap_league_coverage` per KBL Q3 convention.
- **F5 — Country code**: 3-letter ISO (MEX, ESP, AND). Per-team; multi-country supported for continental competitions.
- **F6 — One bootstrap script per league** (mirrors KBL).
- **F7 — Verification**: Post-apply query with `decided_at >= :apply_timestamp` filter — avoids double-counting re-resolution loop entries against pre-apply review_queue entries.
- **F8 — Success criterion**: Asymmetric_anchor_failure inflow rate for sport drops ≥50% over 7-day post-apply window (not 48h — league game-day cadence is non-continuous).

### Day-27 afternoon: LMB seed manifest verification (3 rounds before paste)

Multi-agent verification handoff produced 3 rounds of pushback before the actual Python tuples were pasted (rather than summarized as "16 teams confirmed correct"). Wikipedia verification against Posta Deportes April 2026 caught:

- **6 missing teams** in initial draft (Caliente de Durango, Charros de Jalisco, Dorados de Chihuahua, Rieleros de Aguascalientes, Tecolotes de los Dos Laredos, Conspiradores de Querétaro)
- **2 phantom teams** in initial draft (Águilas de Mexicali, Mariachis de Guadalajara — not in 2026 roster)
- Critical target: Conspiradores de Querétaro at 161 records/week was missing from the initial draft

Final LMB manifest: 20 teams (10 Norte + 10 Sur), 76 aliases, all `MEX`, zero within-league collisions. `Tigres` bare excluded due to within-LMB collision with Tigres de Quintana Roo.

PR #202 merged.

### Day-27 afternoon: bootstrap_lmb.py + 12 tests (PR #203)

Mirrors `bootstrap_kbl.py` structure with critical PR #200 alias-safety fix: `INSERT...WHERE NOT EXISTS` instead of `ON CONFLICT (alias_normalized, source) DO NOTHING` on the global UNIQUE constraint. The ON CONFLICT pattern silently drops aliases when the same `(alias_normalized, source)` exists on a different team — empirically caught during Tennis dedup Day-26 (76 merges, ~58% alias loss before recovery).

- Three-branch classifier (INSERT / BACKFILL / SKIP)
- Global conflict pre-check at classify time with warning logs
- `--dry-run` mode
- `confidence=1.0` on aliases (KBL precedent)
- Pattern D pre-flight check (inline implementation; to be standardized in future bootstraps)

12 tests:
- TestLMBManifestShape (9): imports, size=20, 4-tuple arity, country=MEX, normalization, no duplicate canonicals, no cross-team collisions, source value
- TestLMBDiacriticCoverage (1): accented teams have ASCII variants
- TestLMBDay27Targets (2): Day-27 target strings (Monterrey, Puebla, Queretaro, Tabasco) are aliases; Tigres bare excluded

PR #203 merged.

### Day-27 PR state

- **PR #202** — Phase 2D.5-A scope-doc + LMB seed manifest. Merged.
- **PR #203** — bootstrap_lmb.py + 12 tests. Merged.

### Day-27 pending — Day-28 morning

1. LMB bootstrap apply (Pattern D pre-flight → dry-run → wet apply → F7 verification → baseline_shifts annotation).
2. Day-28 daily-diff measurement to validate Tennis +4.17pp lift holds.
3. Liga ACB scope-doc + manifest + bootstrap as next workstream (or pivot to higher-volume league if production data shows different priorities).

---

## Session — 2026-05-25

### Day-25 morning baseline

Reality check (operator-side, pre-work):

| Metric | Value | Note |
|---|---|---|
| `review_queue` depth | 8,724 (was 8,661 day-22) | +63 over 3 days; slow growth |
| `daily_diff_reports` rows | 2 | No cron wired yet; Sat+Sun measurements missed |
| `baseline_shifts` count | 2 | Golf scope-filter (day-22) + ingestion incident (day-25, written 13:51:50 UTC, id `7c11e66b`) |
| Last `resolution_log` write | 2026-05-25 02:47 UTC | This morning's cron ran cleanly |
| `alembic_version` | `c4d9e2a1b3f7` | Track A migration head |

**No daily-diff measurements Sat/Sun.** Railway cron not yet wired; manual runs only. Today's manual run produced stale data (see ingestion incident below) — **Day-26 is the first clean post-incident measurement**, and the first valid test of the Day-22 Golf scope-filter baseline-shift prediction (48.4%/51.0% → predicted ~53-54% post-Golf-filter).

### Day-22 deferred: Handball + Rugby Union cohort pivots (completed Friday, captured here)

Day-22 supplement (PR #182, merged) captured the Golf pivot and three findings (Pattern F, Pattern A.2, reason_code alignment). The Handball + Rugby Union pre-scope discovery that followed was completed Friday afternoon but not documented — captured here.

**Handball pre-scope discovery result:**

| Step | Result |
|---|---|
| Pattern A Q1 (provider attribution) | FL-only: 1,019 distinct/24h, zero Kalshi |
| Pattern A Q2 (variant capture, 7d) | ~50 distinct teams; dominated by Eastern European amateur/women's lower-division |
| Q3 (existing sp.teams) | Zero pre-existing — true zero-start |
| Pattern G diagnostic — top-tier vs long-tail | Top 11 pairs at records_in_7d=2 (cron-duplicate of same match); only 2 of ~30 visible pairs were top-tier |
| Q6 (manifest reachability) | **0/224 records** reachable against 47-team top-tier manifest |

Verdict: Handball's daily volume (~253/day) is structurally long-tail-dominated. Top-tier matches (Bundesliga, ASOBAL, Lidl Starligue) appear at weekly cadence (~2 records/7d per pair) and are buried under daily-cadence amateur/semi-pro. Bootstrap value is near-zero — curating 200+ long-tail teams for diminishing per-team leverage is not tractable.

**Rugby Union pre-scope discovery result:**

| Step | Result |
|---|---|
| Pattern A Q1 (provider attribution) | FL-only: 474 distinct/24h, zero Kalshi |
| Pattern G diagnostic | Same long-tail dominance pattern as Handball |
| Team-name signal | Predominantly Kazakhstan/Belarus/Russia/Indonesia/Greece — NOT top-tier (URC, Top 14, English Premiership, Super Rugby) |
| Sport-classification flag | Several team names look like futsal/indoor (Asahan, Cosmo JNE, Bintang Timur Surabaya) — possible FL sport-mapping misclassification (Issue #183 filed) |

Verdict: same as Handball. Long-tail dominance structural; possible sport-misclassification inflating the no_match count further.

**Architectural finding confirmed: sport_id partition makes bare-token aliases safe across sports.** `resolver/aliases.py:51,111` (AliasIndex) and `resolver/alias_tier/candidates.py:106` (CandidateIndex) both key lookups by `(normalized, sport_id)`. Cross-sport name collisions (e.g., "Barcelona" in Football vs Handball) are architecturally impossible at the matcher layer. The KBL F2 alias-distinctiveness rule was about within-sport collision (Egis Körmend vs KCC Egis, both Basketball sport_id=3), not cross-sport. Handball manifest v2 adopted bare-token aliases matching FL's actual provider-forms accordingly.

**Corroboration ceiling for FL-only sports (v1.5 amendment #10):** alias-tier and fuzzy-tier cap at 0.70 confidence without cross-provider corroboration (strict-tier = 0.98, alias-tier max = 0.50+0.20+0.30 corroboration = 1.00, but without corroboration max = 0.70 = review_queue). For FL-only sports, only strict-tier alias hits auto-apply. Bootstrap value is gated on strict-tier coverage — alias completeness matters MORE for FL-only sports.

### Day-22 deferred: Pattern G validated + v1.5 amendment #11 locked

**Pattern G — bootstrap-cohort 3-step diagnostic** (draft captured in conversation, text below for historical record):

> Apply a 3-step diagnostic before committing to bootstrap-cohort prioritization:
> 1. **Daily volume** — sport's records/day in the no_match + signal_extraction_skipped population.
> 2. **Top-tier vs long-tail split** — multi-day variant capture. Count distinct team-pairs over 7-day window. If top-N pairs are all records_in_7d ≤ 2 (cron duplicates of same match, not recurring matches), long-tail dominates.
> 3. **Reachability of top-tier manifest** — draft starter manifest from Q2-surfaced provider-forms (not general-knowledge canonicals — Pattern A.1 discipline), run Q6-shape reachability check against last-24h records.

**Empirical case studies:**

| Sport | Step 1 | Step 2 | Step 3 | Verdict |
|---|---|---|---|---|
| Golf | 1,371/day | N/A (tournament-prop, not H2H) | N/A | Scope-filter extension, not bootstrap (PR #181) |
| Handball | 1,019 distinct/day (FL-only) | Long-tail dominant; top-tier at weekly cadence, buried | 0/224 reachable against 47-team manifest | Dropped from bootstrap priorities |
| Rugby Union | 474 distinct/day (FL-only) | Long-tail dominant + possible FL sport-misclassification | Diagnostic halted at Step 2 | Dropped |

**v1.5 amendment #11 (locked):** "Bootstrap leverage ≠ total-daily-volume. Daily-diff measures combined top-tier + long-tail population; bootstrap value is gated on top-tier reachability. Pre-scope discovery (Pattern A.2 / Pattern G) must run a 3-step diagnostic before bootstrap commit. Long-tail-dominated sports may not be addressable via bootstrap at all — per-team curation cost rises while per-team leverage drops."

**5-sport cohort framing dropped.** The Day-17 5-sport zero-coverage cohort (Handball, Snooker, Volleyball, Rugby League, Golf) was based on combined-volume signal. Three of five have now been empirically rejected (Golf: tournament-prop scope-filter, Handball: long-tail-dominated, Rugby Union: same). Snooker (80/day) and Rugby League (60/day) are almost certainly the same pattern — diagnostic halted per operator decision to stop pre-checking and pivot away entirely.

**Phase 2 next-workstream pivot:** Tennis dedup locked as next primary workstream. Three reasons:
1. Known scope (~457-720 cross-format pairs, bounded engineer-time)
2. Empirical foundation already laid (Day-20 investigation, Phase 2D.5 prerequisite)
3. Largest single-sport review_queue (4,613 records); dedup lifts collision-routed records into auto-apply

### Day-25 ingestion incident — diagnosis arc

**Timeline:**

| Event | Timestamp (UTC) |
|---|---|
| Kalshi REST ingestion dies | 2026-05-22 ~16:54 (Thursday) |
| FL Phase 1B ingestion dies | 2026-05-23 ~14:07 (Friday) |
| Weekend: both providers stale | Sat-Sun |
| Day-25 morning: detected via pre-work baseline check | 2026-05-25 ~13:00 |
| First Railway redeploy: FL restored, Kalshi still dead | 2026-05-25 ~13:18 |
| Second Railway redeploy: Kalshi restored | 2026-05-25 ~13:48 |
| Both providers writing fresh records | 2026-05-25 ~13:48 |

**Key diagnosis finding: supervision infrastructure already existed but didn't prevent the outage.** `ingestion/base.py:supervise()` (lines 269-319) catches exceptions, logs with traceback, restarts with exponential backoff. `ingestion/runner.py:start_all_ingestion()` wires both FL and Kalshi under supervision. The infrastructure was correct per architecture v1.3 §6.1.

**Root cause chain:**

1. Ingestion task crashes (original cause unknown — lost in Railway log rotation)
2. `supervise()` catches exception, restarts task with backoff ✓ (working as designed)
3. Restarted task calls `pg_try_advisory_lock` (session-scoped, `ingestion/base.py:245-259`)
4. Lock held by dead session's connection in Neon's PgBouncer-based connection pool (ghost lock)
5. `got_lock = False` → task returns cleanly (`ingestion/kalshi.py:303` / `ingestion/fl.py:352`)
6. **`supervise()` interprets clean return as intentional shutdown** → exits restart loop
7. Task permanently dead; service stays "online" because web API endpoints unaffected

**The bug is the interaction between three correct-in-isolation components:**
- Session-scoped advisory locks (correct for singleton enforcement)
- Connection pooler keeping dead sessions alive (correct for pool efficiency)
- Supervisor exiting on clean return (correct for intentional-shutdown semantic)

None of the three is wrong alone; the failure emerges from their composition. The lock-contention exit path using `return` instead of a retriable signal is the precise coupling point.

**Three-layer fix scoped and Issues filed:**

| Layer | Issue | Fix | Priority |
|---|---|---|---|
| **2 — Lock-contention retry** | #184 | Change `return` → sleep-and-retry loop in `kalshi.py:run()` + `fl.py:run()`. 10 LOC. | Medium |
| **3 — Transaction-scoped locks** | #185 | Switch `pg_try_advisory_lock` → `pg_try_advisory_xact_lock`. Eliminates ghost-lock-leak class. 20 LOC. | Low |
| **Defense-in-depth — Staleness monitor** | #186 | Daily-diff pre-flight `last_seen_at` age check. Exit code 6 for ingestion-stale. 20 LOC. | Medium |

**Methodology observation:** The diagnosis chain itself (supervisor-exists-but-exits-on-clean-return → advisory-lock-leak-with-pooler → ghost-session-identification → multi-redeploy-resolution) is worth capturing as a worked example of composition-failure diagnosis. The bug is NOT in any single component — it's in the handshake between three components' assumptions. Same epistemic shape as the "Goyang Skygunners" Pattern A finding: the system passed all its unit tests but failed at the integration boundary.

### Issues filed today

| Issue | Title | Priority |
|---|---|---|
| #183 | FL ingestion: possible sport-misclassification for Rugby Union | Low |
| #184 | Ingestion lock-contention exit path uses return instead of raise | Medium |
| #185 | Switch ingestion advisory locks from session-scoped to transaction-scoped | Low |
| #186 | Ingestion staleness monitor — daily-diff pre-flight last_seen_at age check | Medium |

**Ingestion incident baseline_shifts annotation written** at 13:51:50 UTC (`id 7c11e66b-b5fb-43cc-adb1-18cd76ec479e`, `event_type='ingestion_incident'`, `event_date=2026-05-25`). Notes field captures root cause (advisory-lock-leak + supervisor clean-return interaction), recovery timeline, and Issues #184/#185/#186 references. Day-26 daily-diff measurement will render this annotation in the baseline-shift-events section of the report output — second real test of the Track A annotation mechanism after the Golf scope-filter event.

### Tennis dedup scope-doc — substrate ready

F1-F8 framing matrix locked from Friday's prep (Day-22). Schema survey confirmed one-row-per-player in `sp.teams` (no separate `sp.players` table). FK cascade enumeration complete:

| Table | Column | Type | Merge action |
|---|---|---|---|
| `sp.team_aliases` | `team_id` | Direct FK, CASCADE DELETE | Copy aliases to canonical team (INSERT ON CONFLICT DO NOTHING), then cascade handles delete |
| `sp.fixtures` | `home_team_id` | Direct FK, NO ACTION | UPDATE to canonical team_id before team delete |
| `sp.fixtures` | `away_team_id` | Direct FK, NO ACTION | Same |
| `sp.review_queue` | `candidate_fixtures` | JSONB (no FK) | Search-and-replace team_id in array |
| `sp.resolution_log` | `reason_detail` | JSONB (no FK) | Immutable audit; do NOT rewrite |

F4 cascade SQL, F5 JSONB rewrite shape, F6 two-phase batching strategy (457 high-confidence + 263 candidate-verification), F7 rollback via `sp.dedup_audit` table — all concrete from Friday's session.

**Scope-doc draft is next workstream** once ingestion stabilizes and Day-26 produces the first clean post-incident measurement.

### v1.5 amendment pile (end-of-day-25)

1. **Neon migration** — unchanged
2. **§7.4 corroboration model** — unchanged
3. **§6.5 archival job status (#164)** — unchanged (urgency confirmed Day-21; 58K no_match writes/day)
4. **§7.7 cadence** — unchanged
5. **audit-stream separation for operator approvals** — unchanged
6. **alias-tier and fuzzy-tier don't consult `sp.team_aliases`** — unchanged
7. **§7.5 sport-class distinction** — unchanged (empirically validated Day-21)
8. **matcher-capability vs incremental-apply distinction** — unchanged
9. **daily-diff vs production-cron reason_code semantic gap** — unchanged (Finding 3, Day-22)
10. **FL-only sports have structural review_queue floor at 0.70** — adopted Day-22; corroboration-ceiling confirmed empirically during Handball pre-scope discovery
11. **Bootstrap leverage ≠ total-daily-volume** — adopted Day-22; Pattern G diagnostic validated by Handball + Rugby Union case studies

Pile at 11 items.

### PR state at end-of-day-25

- **PR #167** — KBL methodology + Patterns A/B/C/D/E. Open. Pending Pattern F + Pattern G + Pattern A.2 additions. Operator review pending.
- **PR #179** — Track A Deliverable 2. **Merged Day-22.**
- **PR #180** — Day-21 supplement. **Merged Day-22.**
- **PR #181** — Golf scope-filter extension (v0.3.0). **Merged Day-22.**
- **PR #182** — Day-22 supplement. **Merged Day-22.**
- **PR #187** — this Day-25 entry.

### Pending — next, operator review

1. **Ingestion stability verification** — monitor `last_seen_at` over next 24h to confirm both providers stay alive post-redeploy.
2. **Day-26 daily-diff manual run** — first clean post-incident measurement. Validates Golf scope-filter baseline-shift prediction.
3. **Tennis dedup scope-doc kickoff** — F1-F8 matrix ready; operator-led drafting per KBL precedent.
4. **PR #167 review** — Pattern D/E + add Patterns F, G, A.2 per Day-22/25 findings.
5. **Issue #184 fix PR** — lock-contention return→retry. Small standalone PR, 10 LOC.

---

## Session — 2026-05-22

### Day-22 morning baseline + daily-diff day-over-day

Reality check (operator-side, pre-work):

| Metric | Value | Note |
|---|---|---|
| `review_queue` depth | 8,661 (was 8,452) | +209 overnight; normal new arrivals |
| `daily_diff_reports` rows | 1 | Yesterday's manual run; Railway cron still paused |
| Last `resolution_log` write | 2026-05-22 02:45 UTC | Last night's Kalshi cron ran cleanly |
| `alembic_version` | `c4d9e2a1b3f7` | Track A migration head |

Daily-diff manual run today (env vars caught missing by Pattern D pre-flight — exactly the catch class it's designed for):

| Metric | Day-21 | Day-22 | Δ |
|---|---:|---:|---:|
| Records scanned | 17,996 | 15,661 | -2,335 (likely weekday/weekend population shift) |
| Matcher-capability rate (scope-filtered) | 48.4% | **51.0%** | +2.6pp |

n=2 is too small to call this a stable band — two data points that happen to be close. Real stability assessment needs a week of measurements. The 2.6pp rate lift is plausibly explained by the smaller denominator (some sports' records didn't arrive overnight), not a real capability improvement.

### Golf pivot — bootstrap framing was wrong; scope-filter extension is the right tool

Day-21 evening framed Golf as the biggest single-sport bootstrap candidate in the 5-sport zero-coverage cohort (1,371 no_match/day, largest single-sport opportunity). Day-22 morning survey + Pattern A discriminator queries empirically reframed this:

**Survey verdict** (codebase read, `outcome_shapes.py:200-208` + `kalshi_identity.py` + `KALSHI_AUDIT.md §4`): Golf records on Kalshi are structurally tournament-prop-only. Every series base (KXPGATOUR, KXPGAR1LEAD, KXPGAMAKECUT, KXPGA3BALL, etc.) attaches sub-markets to a `(tournament_handle, year)` identity. The Kalshi resolver returns `None` at signal extraction for non-per_fixture records (`resolver/kalshi.py:150-152`); these records never reach the matcher.

**Pattern A empirical queries** (operator-side, production):

| Query | Result | Implication |
|---|---|---|
| Q1 (series-ticker breakdown, 24h) | KXPGA3BALL = 49, KXPGAH2H = 1, others 1-4 each | Tournament-prop dominance confirmed; H2H is edge-case rare |
| Q2 (resolution_log Golf-rows) | **Zero rows** | Production cron writes nothing for Golf — never enters resolver accounting |
| Q3 (suffix vocabulary harvest) | 17 colon-suffix prop categories | Vocabulary locks deterministically |

Q2 result is significant standalone: Day-21's "1,371 no_match" framing was wrong on the source. The number came from daily-diff's synthetic `signal_extraction_skipped` classification, not from production `sp.resolution_log`. Golf records never enter the production cron's accounting at all.

**Outcome**: PR #181 ships the scope-filter extension. 5-sport bootstrap cohort priority reshuffles — Handball (253/day, team-coverage) becomes the new biggest bootstrap candidate. Volleyball dropped from the cohort (2/day, too small).

### Three substantive findings (day-22)

**Finding 1 — Pattern F (sibling to Pattern A): bootstrap-methodology forks at INDIVIDUAL_SPORT_CODES.**

Today's Golf pivot proves the fork exists. For each future cohort sport, the operator now has a forced gating question: "are records H2H matchups or tournament-prop markets?" The Pattern A query discipline needs to extend to answer this question *before* scope-doc commit, not after seed-content discovery.

- **Team-path sports** (sports NOT in `INDIVIDUAL_SPORT_CODES`): KBL/Handball/Snooker/Rugby Union/Rugby League follow the KBL methodology (team-coverage bootstrap).
- **Personal-path sports** (`INDIVIDUAL_SPORT_CODES` = {tennis, mma, boxing, golf, snooker, darts}): require the H2H-vs-prop discrimination gate *before* committing to a methodology shape. Outcomes branch:
  - H2H-dominant → player-coverage bootstrap (different methodology, untested).
  - Tournament-prop-dominant → scope-filter extension (Issue #160 pattern).
  - Mixed → both, scoped per series-ticker subset.

**Pattern F is a sibling to Pattern A, not a sub-pattern.** Pattern A is content-stage discipline (production-data verification before assuming alias completeness). Pattern F is meta-discipline (methodology-shape discrimination before scope-doc commit). Different epistemic layers; naming as siblings keeps the conceptual map clean. To land in PR #167 as Pattern F.

**Finding 2 — Pattern A.2: discovery-before-commit at multiple granularities.**

Same epistemic shape as Pattern A's pre-merge production-data verification, applied at the scope-doc-shape granularity rather than the seed-content granularity. Today's Golf finding is the worked example: ~15 minutes of code survey caught what would have been days of wasted player-bootstrap scoping. The methodology framing is "discovery before commit, at every stage of granularity" — pre-scope-doc for methodology shape, pre-merge for seed content. Same Pattern A discipline, layered.

To land in PR #167 as a Pattern A.2 sub-bullet under the existing Pattern A.

**Finding 3 — daily-diff vs production-cron reason_code semantic alignment.**

Q2's zero-row result confirmed a real gap. Track A's day-over-day comparisons require explicit understanding:

- Production cron writes NOTHING to `sp.resolution_log` for records where the resolver returns `None` at extract_signal (Kalshi non-per_fixture, FL records without sport_id).
- Daily-diff classifies the same records as `signal_extraction_skipped` via its synthetic counter (`scripts/daily_diff.py:_resolve_record`).
- Comparing daily-diff's classification against production `resolution_log` requires this gap to be explicit, or future operators / dashboards will read "Golf no_match = 1,371/day" out of daily-diff and try to reconcile it against `resolution_log` where the rows don't exist.

**Ownership**: small follow-up PR (Claude Code drafts) extends `scripts/daily_diff.py` module docstring to document the synthetic-reason_code semantics + the production-cron asymmetry. Track A scope doc (PR #175) is already merged — annotation as inline comment-in-code rather than a second scope-doc supplement keeps the doc-touch surface minimal.

### Scope-filter extension PR — PR #181

Diff shape: 3 files, ~150 lines.

- `resolver/fuzzy_tier/matcher.py` — 17 Golf entries appended to `KALSHI_PROP_MARKET_SEGMENTS`.
- `scripts/daily_diff.py` — `SCOPE_FILTER_VERSION` v0.2.0 → v0.3.0.
- `tests/test_daily_diff.py` — 2 new tests, both green.

**Intentional out-of-scope** (test-pinned):
- Multi-player matchup props (KXPGA3BALL "Smith/Jones/Brown", 2-ball "Smith vs Jones") — no colon-suffix, needs series-ticker-base filter; KXPGA3BALL is 49/24h of unfiltered records. Separate workstream if/when operator wants to filter.
- Tournament-outright shapes ("PGA Tour Championship Winner") — no colon; same disposition.
- KXPGAH2H per-fixture records (1/24h) — intentionally left open for a future personal-path Golf bootstrap if it ever makes sense; filtering now would prematurely close the door.

**Expected baseline-shift on merge**: ~1,371 records/day shift from `raw.signal_extraction_skipped` → `raw.prop_market_filtered_out`. Scope-filtered denominator tightens; headline matcher-capability rate rises ~2-3pp (51.0% → ~53-54%). First real test of the Track A `sp.baseline_shifts` annotation mechanism — operator inserts an annotation row at ship time so the day-23 rate jump is operator-attributable.

### v1.5 amendment pile (end-of-day-22 refinements)

The pile, updated for today's findings:

1. **Neon migration** — unchanged
2. **§7.4 corroboration model** — unchanged
3. **§6.5 archival job status (#164)** — unchanged (urgency confirmed yesterday)
4. **§7.7 cadence** — unchanged
5. **audit-stream separation for operator approvals** — unchanged
6. **alias-tier and fuzzy-tier don't consult `sp.team_aliases`** — unchanged
7. **§7.5 sport-class distinction** — unchanged (empirically validated yesterday)
8. **matcher-capability vs incremental-apply distinction** — unchanged (Track A measurement substrate semantic)
9. **NEW — daily-diff vs production-cron reason_code semantic gap**: production cron writes nothing for resolver-returns-None records; daily-diff synthesizes `signal_extraction_skipped`. Documented this session per Finding 3; doc-only fix scheduled.

Pile expanded from 8 to 9 items.

### PR state at end-of-day-22

- **PR #167** — KBL methodology + Patterns A/B/C/D/E. Open. Pending Pattern F (sibling) + Pattern A.2 (sub-bullet) additions per Findings 1+2.
- **PR #179** — Track A Deliverable 2 implementation arc. **Merged this morning.**
- **PR #180** — Day-21 supplement append. **Merged this morning.**
- **PR #181** — Golf scope-filter extension (v0.3.0). Open; awaiting operator review + merge + baseline_shifts annotation.
- **PR #182** — this day-22 entry (Option α: separate small PR off main, mirroring Day-21 supplement pattern).

### Pending — next, operator review

1. **PR #181 review + merge + baseline_shifts annotation** — Golf scope-filter extension. Operator inserts the suggested `sp.baseline_shifts` row at merge time.
2. **Next daily-diff measurement** — verify baseline-shift prediction: Golf records land in `prop_market_filtered_out`, headline rate rises to ~53-54%.
3. **PR #167 review** — Pattern D/E methodology doc + Pattern F (new sibling) + Pattern A.2 (sub-bullet) additions per Findings 1+2. Operator's call whether to fold Findings 1+2 into the existing PR or open a follow-up.
4. **Handball bootstrap pre-scope discovery** — new biggest single-sport bootstrap leverage (253/day) per Day-22 cohort reshuffle. Apply Pattern A.2 (pre-scope discovery) AND Pattern F (H2H-vs-prop discrimination) before scope-doc commit. Handball is team-path (not in INDIVIDUAL_SPORT_CODES) so the H2H gate likely passes, but the discipline applies regardless.
5. **Doc-only fix for Finding 3** — Claude Code drafts small follow-up PR extending `scripts/daily_diff.py` module docstring with daily-diff vs production-cron reason_code semantic gap.

---

## Session — 2026-05-21

### KBL bootstrap pilot — EMPIRICALLY VALIDATED end-to-end

Both pending KBL records resolved overnight via tonight's 02:15 UTC Kalshi cron pass. The bootstrap methodology pilot landed correctly on production (2026-05-20 after Pattern D wrong-endpoint recovery), and the resolver auto-applied the records using the new aliases:

| Record | Resolution | Notes |
|---|---|---|
| `KXKBLGAME-26MAY100330GOYEGI` | `reason_code='strict'`, `fixture_id=e5f11624-5ffb-43d7-ac29-b848424fff00` | "Goyang Skygunners" alias → Goyang Sono team_id via strict-tier AliasIndex |
| `KXKBLGAME-26MAY130600EGIGOY` | `reason_code='strict'`, `fixture_id=c06ee663-52ee-437d-8c31-96e677f592b6` | Same pattern, sides swapped |

End-to-end flow confirmed:

1. ✅ Bootstrap aliases applied to production (post-Pattern-D recovery, 2026-05-20 13:53 UTC)
2. ✅ Strict-tier `AliasIndex` (`resolver/aliases.py`) contains `"goyang skygunners"` → Goyang Sono team_id mapping
3. ✅ `ensure_fixture()` created `sp.fixtures` rows tonight via strict-tier auto-apply path
4. ✅ `fixture_id` propagated to `sp.kalshi_markets`
5. ✅ Records resolved with `reason_code='strict'`, `fail_reason=null`

**The KBL methodology pilot is empirically complete and successful.** The 5-sport zero-coverage cohort (Handball, Snooker, Volleyball, Rugby League, Golf) can proceed with the validated methodology — Patterns A + B + C + D all confirmed as institutional knowledge.

### Methodology cohort — 4 patterns validated

The KBL pilot produced four named methodological patterns now captured in `docs/bootstraps/kbl-2025-26.md` (PR #167):

- **Pattern A** — Production-data verification via `sp.resolution_log` is mandatory before bootstrap merge. Caught the "Goyang Skygunners" Kalshi-form-vs-official-form alias gap that authoritative-source curation missed.
- **Pattern B** — Observe-then-react over pre-seed-speculate for unproven aliases. Kept the seed manifest focused on empirically-verified provider forms; deferred speculation for the 8 KBL teams without Kalshi history.
- **Pattern C** — Clear bytecode before invoking diagnostic scripts against production from local Python. Surfaced during the Tennis ValidationError verification cycle; saved hours of misdiagnosis after stale `__pycache__` produced false-positive crashes.
- **Pattern D** — Verify connection endpoint matches the expected branch before any database write operation. Surfaced via the 2026-05-19 wrong-Neon-branch apply; added as Step 4 of the standard apply runbook. Sub-pattern extends to read paths (verify-endpoint-before-read for measurement scripts; PR #167 commit `aa95a36`).

Four patterns × five future bootstraps = ~20 known-risk-classes pre-emptively closed. ROI on the pattern documentation is materially positive even before the cohort bootstraps start.

### Architectural finding (v1.5 amendment #6 REPLACED, 2026-05-21)

Yesterday's v1.5 amendment item #6 (review_queue routing on fixture-construction failure) was misdiagnosed. `admin/queries.py:769-871` `approve_record()` confirms `ensure_fixture()` runs at OPERATOR APPROVAL time, not at routing time. `review_queue.candidate_fixtures` carrying team UUIDs (not fixture UUIDs) is designed behavior — the operator approval flow creates the fixture row at approve time. **Item #6 removed from v1.5 pile.**

**New v1.5 amendment #6 replacement, surfaced during today's KBL diagnostic:**

> **Alias-tier and fuzzy-tier matchers use a `CandidateIndex` built from `sp.teams.canonical_name` only; they do NOT consult `sp.team_aliases`.** Only the strict tier has access to aliases via the separate `AliasIndex` (`resolver/aliases.py:64-84`). Bootstrap aliases are therefore narrow-purpose: they only help records that successfully reach strict-tier's alias lookup step.
>
> Records that fail strict-tier due to upstream gates (`kickoff_confidence` < 0.85, `sport_not_classified`, etc.) get zero benefit from bootstrap aliases regardless of coverage completeness.
>
> KBL pilot validated this path end-to-end: bootstrap-applied "Goyang Skygunners" alias → strict-tier AliasIndex hit → `ensure_fixture()` → fixture_id propagated. The pilot's success specifically routes through strict-tier; alias-tier and fuzzy-tier never see the alias.
>
> **Implications for future bootstraps** (Handball/Snooker/Volleyball/Rugby League/Golf): aliases will only help records that pass strict-tier's prerequisite gates. If a sport has consistent kickoff-confidence issues OR `sport_not_classified` issues, bootstrap aliases won't recover those records. Per-sport empirical verification (Pattern A) of strict-tier reach is mandatory before assuming aliases will land.

v1.5 amendment pile still at 7 items (item #6 replaced, not added).

### Orphan review_queue rows — new pattern discovered (separate Issue forthcoming)

The 2-pending KBL queue-depth this morning turned out to be a separate concern from the resolution status:

- Both KBL records have `fixture_id` populated (strict-tier resolved them)
- Both still have `sp.review_queue` rows with `status='pending'`
- `review_queue.created_at` shows May 9-12 (pre-bootstrap timestamps; rows from earlier failed resolution attempts)
- The post-PR-#108 runner uses `ON CONFLICT DO NOTHING WHERE status='pending'` for idempotency on INSERT, but there's NO inverse — no mechanism that UPDATE-clears a pending review_queue row when strict-tier later auto-applies the record.

**Structural property of the architecture:** every record that EVER routed to review_queue AND later auto-applied carries an orphan row indefinitely. Affects every sport in principle.

**Measured scope (operator-run discriminator query, 2026-05-21):**

| Provider | Orphan count | Pending total | Orphan rate |
|---|---|---|---|
| Kalshi | 36 | 4,456 | 0.8% |
| FL | 0 | 3,996 | 0% |
| **Total** | **36** | **8,452** | **0.4%** |

**The orphan pattern is Kalshi-only and very narrow.** Initial hypothesis ("Issue #163's 6,654 queue depth likely contains meaningful orphan inflation") was wrong — measurement shows orphans contribute only 0.4% of pending depth. #163's 6,654-pending figure is NOT meaningfully inflated by orphans; the pending population represents genuine pending records.

**Probable provenance of the 36 Kalshi orphans** (informal hypothesis, low-priority verification deferred):

- 2 are the KBL records that resolved tonight via strict-tier bootstrap alias.
- ~34 others likely from PR #171's Tennis ValidationError fix + PR #161's asymmetric routing fix — records that previously crashed or mis-routed, then resolved on subsequent cron passes after the fixes shipped. Per-record provenance verification is a small follow-up later in the week, not urgent.

**Kalshi-vs-FL asymmetry** (36 vs 0) suggests FL's resolver path has different review_queue routing semantics. Maybe FL's strict-tier runs differently, or FL's review_queue insertion gate is stricter. **Worth a small follow-up investigation** — doesn't block Track A or any current work.

**Fix shape (deferred until Track A measurement substrate ships):** runner-side cleanup option (cleanest) — when strict tier auto-applies, runner UPDATE-clears matching review_queue row to `status='auto_resolved'`. ~10 LOC in `scripts/run_resolver_pass.py` + one-shot backfill for the existing 36 orphans.

**Reframed implications:**

- Track A measurement substrate doesn't need elaborate orphan handling. Simple pending-vs-resolved distinction suffices. Orphan classification could be a Track A follow-up metric if useful.
- Issue framing shifts from "structural inflation of #163" to "small data-integrity cleanup with operational hygiene value." Still worth filing for completeness.
- 5 remaining sport bootstraps proceed with confidence — methodology validated, orphan accumulation is bounded at ~0.4% of pending depth.

Issue filing this session with the measured numbers prominently in the body.

### Phase 2 work-in-progress state

> **[Updated by end-of-day append: PR #180 opened with empirical-validation findings; PR #179 opened (DRAFT) carrying the full Track A Deliverable 2 implementation arc.]** See the appended sections below for the implementation timeline and first-measurement findings.

- **PR #175 merged** — Phase 2 Track A scope doc on main as of `10d4b65`. Measurement substrate's design committed; Deliverable 2 build starts today.
- **Track A Deliverable 2 scaffolding** — landed this session on branch `claude/track-a-deliverable-2-daily-diff` (commit `9b2323c`). Migration `c4d9e2a1b3f7` for `sp.daily_diff_reports` + `sp.baseline_shifts`, `scripts/daily_diff.py` skeleton with Pattern D pre-flight check, `scripts/render_daily_diff_report.py`, 26 test stubs, Makefile targets. DRAFT PR forthcoming. Can develop locally despite Railway hobby builds paused; cron-deploy gated until Railway resumes.
- **Track A Deliverable 1 (legacy extraction)** — ~2-3 days after D2 ships. Higher-risk per scope doc §5 (touches main.py production code). Dual-purpose: serves Track A measurement substrate AND architecture doc §11.6 Phase 5 decommission preparation per v1.5 amendment.
- **Tennis dedup workstream** — pending Track A measurement substrate per yesterday's priority sequence. ~457 high-confidence + ~263 candidate-verification merges; 3-5 days design + script + tests + verification.

### v1.5 amendment pile (refined per today's findings)

The pile, ordered by emergence:

1. **Neon migration (§10.1 + §11.2 + §14)** — unchanged
2. **§7.4 corroboration model (binary vs accumulating per-provider)** — unchanged
3. **§6.5 archival job status (#164)** — **PROMOTED** per yesterday's Finding X to Phase 2 dependency, not deferred maintenance. ~7.3M rows/year retry traffic confirms urgency.
4. **§7.7 cadence** — unchanged (continuous-loop vs daily-cron doc-vs-code reconciliation)
5. **audit-stream separation for operator approvals** — **REFINED** per yesterday's Finding X. Operator approvals don't write `resolution_log`, but cron retry writes DO. Different streams have different growth pressures.
6. **REPLACED 2026-05-21**: alias-tier and fuzzy-tier matchers don't consult `sp.team_aliases` — only strict tier does, via separate `AliasIndex`. Bootstrap aliases narrow-purpose. (Previous #6 fixture-construction routing shape was misdiagnosed; REMOVED.)
7. **§7.5 sport-class distinction (refined re-refined 2026-05-20)** — three populations: (a) true cross-format duplicates, (b) common-surname distinct-player collisions, (c) corroboration-ceiling residual. Unchanged from yesterday.

### Issues filed today

- **#178** — Orphan `sp.review_queue` rows when subsequent cron pass auto-applies. Body carries measured numbers (36 Kalshi orphans / 0.8% of Kalshi pending; 0 FL orphans; 0.4% total orphan rate). Framed as data-integrity hygiene, not measurement-blocking. Track D parked; fix-shape decision committed (Option 1 — runner-side cleanup ~10 LOC). Implementation scheduled after Track A Deliverable 2 ships.

### PR state at end-of-session-so-far

- **PR #167** — KBL methodology doc + Patterns A/B/C/D + sub-pattern. Open. Patterns validated by KBL pilot success.
- **PR #169 (DRAFT)** — 2026-05-19 PROJECT_STATE entry. Open. v1.5 refinements deferred to today's entry per the day-19 decision; v1.5 amendment pile updated above to reflect those refinements.
- **PR #175 (MERGED)** — Track A scope doc.
- **2026-05-18 PROJECT_STATE entries** — branch `claude/project-state-2026-05-18-phase5-decision`, no PR opened.
- **This entry (2026-05-21)** — DRAFT PR forthcoming for operator review.

### Pending — operator-side, day-22 morning

1. **Orphan review_queue Issue follow-up** — once Issue is filed today, operator runs the runner-side cleanup design discussion for the eventual fix PR. Not blocking; Track A Deliverable 2 takes priority.
2. **Track A Deliverable 2 PR review** — DRAFT PR forthcoming from today's scaffolding work. Migration + script + tests, no Railway dependency.
3. **Optional**: cross-sport duplication audit (yesterday's deferred item) — provides Tennis dedup workstream's actual scope numbers if/when Tennis dedup work begins.

### Track A Deliverable 2 — IMPLEMENTATION COMPLETE + EMPIRICALLY VALIDATED

Six-step implementation arc landed on branch `claude/track-a-deliverable-2-daily-diff` (PR #179 DRAFT). Each step shipped as a single reviewable commit:

| Step | Commit | Scope |
|---|---|---|
| 1 (scaffold + migration `c4d9e2a1b3f7`) | `9b2323c` (merged via #177) | Migration + script skeletons + 26 test stubs |
| 2 | `b6ca38d` | Scope-filter classification (NON_SPORT, prop-market, head-to-head) |
| 3 | `4ba72f5` | Per-sport metric aggregation (3 pure functions) |
| 4 | `e7fd82c` | Pattern D pre-flight (redesigned for Neon) |
| 5 | `b8739b8` | `_measure` loop + `_write_report` + main wiring |
| 6 | `775a55a` | Confidence histogram + render script + integration test bodies |
| Refactor | `6bec829` | `auto_apply_rate` → `matcher_capability_rate` rename + path-rate headline-promote |

**Migration `c4d9e2a1b3f7` applied to production** via Neon web console chunked-apply (4 single-statement chunks; multi-statement BEGIN/COMMIT block misexecuted as COMMIT-only — Pattern E origin). `sp.daily_diff_reports` + `sp.baseline_shifts` live, alembic head at `c4d9e2a1b3f7`.

**Pattern D redesign for Neon**: scope-doc proposal used `inet_server_addr()` as the endpoint discriminator. Operator pre-flight confirmed Neon returns `169.254.254.254` (link-local proxy) — identical across branches, useless as a discriminator. Real signal: `current_database()` + `DATABASE_URL` hostname substring match against `EXPECTED_PRODUCTION_DB_HOST`. Implemented in `_check_pattern_d_endpoint` (pure function, no live-DB dependency for tests).

**First measurement run** (production, 2026-05-21 evening):
- 17,996 records scanned across 13.4 minutes
- Exit 0, one row written to `sp.daily_diff_reports` (`report_date = 2026-05-21`)
- Pattern D pre-flight passed against production endpoint
- Render script produces clean markdown output

### First measurement findings reshape Phase 2 framings

The empirical data invalidates several day-21-morning framings and re-prioritizes the Phase 2 sequence.

**Headline metrics** (scope_filter_version v0.1.0 — schema renamed to v0.2.0 post-validation):

| Metric | Value | Note |
|---|---:|---|
| Matcher-capability rate (scope-filtered) | **48.4%** | Headline metric |
| Matcher-capability rate (unfiltered) | 27.9% | Includes NON_SPORT / prop-market / signal-extraction-skipped |
| Team-path rate | **70.2%** | Scope-filtered, sports not in INDIVIDUAL_SPORT_CODES |
| Personal-path rate | **12.3%** | Tennis/MMA/Boxing/Golf/Snooker/Darts |

**Reframings forced by the data:**

1. **Tennis matcher-capability is 24.1%, not 0%.** The day-20 framing ("Tennis at 0%") was based on the production cron's *incremental* apply rate — which only counts newly-resolved records per pass. Daily-diff measures *capability* — re-runs the matcher against all records including already-resolved ones. Tennis dedup's expected impact is therefore "lift collision-routed records from review_queue to strict," not "create capability from zero." The lever exists but is smaller-magnitude than the previous framing implied.

2. **Soccer (85.2%) and Baseball (78.0%) are the highest-leverage threshold-tuning targets.** Already performing well; the remaining 15-22% gap is the most concentrated source of auto-apply rate lift available.

3. **5-sport bootstrap cohort priority — Golf first.** Golf produces 1,371 `no_match` rows/day, far the largest single-sport opportunity. Cohort sequencing: Golf (1,371) > Handball (253) > Rugby Union (108) > Snooker (80) > Rugby League (60) > Volleyball (2, too small). The Volleyball bootstrap may not be worth a dedicated cycle.

4. **NON_SPORT scope-filter doing real work**: 7,629 / 17,996 (42%) of records filtered as out-of-scope. Issue #174's framing empirically validated — NON_SPORT is a denominator-hygiene issue, not a noise-floor issue.

5. **Confidence histogram is bimodal**: 5,016 records at 0.95-1.00, 211 at 0.70-0.85, **zero** at 0.85-0.95. Records either clearly match or fall to the review_queue band — there is no soft middle. Empirically validates the §7.5 v1.5 amendment three-population framing: clear matches, collision-routed records, and the corroboration-ceiling residual are distinct populations, not a continuous distribution.

6. **`sp.resolution_log` volume**: 87% `no_match` writes (58,225 of 67,109 per cron pass). §6.5 archival urgency confirmed — Issue #164 promotion to Phase 2 dependency is correct, and the magnitude is now empirically grounded.

### Pattern D + Pattern E methodology refinements (PR #167 `bfb1044`)

Two operational findings from today's apply landed on PR #167 as same-PR refinements rather than spawning follow-up PRs:

- **Pattern D refinement** — platform-aware endpoint check (URL hostname over `inet_server_addr()`). The scope-doc proposal's SQL signal was Neon-naive; the empirically-grounded check is in `scripts/daily_diff.py:_check_pattern_d_endpoint`. For non-Neon platforms, `inet_server_addr()` may still work — the discriminative signal is platform-specific. Future operators applying Pattern D to a new platform must empirically validate which signal varies between branches before relying on it.
- **Pattern E (new)** — verify DDL apply via structural EXISTS / COUNT check, not return-status alone. Neon web console SQL editor reported "Statement executed successfully" when pasting a multi-statement BEGIN/COMMIT block but executed only the final COMMIT statement. Recovery: chunked into 4 single-statement applies, each verified via EXISTS check. Two lessons captured — tool-specific (Neon console quirk) and pattern-general (never trust return-status alone for DDL).

Patterns D + E complement each other: D ensures the apply targets the right database; E ensures the apply actually landed. Both mechanical, both ~5-second checks, both with order-of-magnitude cost asymmetry vs. silent-failure recovery.

### Metric naming refinement (PR #179 `6bec829`)

The first measurement's 48.4% rate immediately collided semantically with the production cron's 0.37% incremental rate from day-20 morning — same name (`auto_apply_rate`), different populations. The conflation produced a real interpretation bug during result review.

**Rename + version bump:**

- `scope_filtered.auto_apply_rate_overall` → `scope_filtered.matcher_capability_rate_overall`
- `scope_filtered.auto_apply_rate_per_sport` → `scope_filtered.matcher_capability_rate_per_sport`
- `raw.auto_apply_rate_overall_unfiltered` → `raw.matcher_capability_rate_overall_unfiltered`
- `SCOPE_FILTER_VERSION` v0.1.0 → v0.2.0

The metric measures what the matcher *could* auto-apply given today's records (**capability**), distinct from the production cron's apply rate which measures newly-resolved records per pass (**incremental**). The two are conceptually different and must not be conflated in dashboards or operator discussions.

**v0.1.0 row preservation**: the row written today retains its v0.1.0 schema (key names `auto_apply_rate_*`). Not backfilled. Render script falls back to v0.1.0 keys when reading rows stamped v0.1.0 — historical readers consult the version stamp. Smaller diff, cleaner provenance.

**Headline-promote**: Team-path and Personal-path rates moved into the window-summary table (was: latest-only section). Today's 5.7× gap between the two populations (70.2% / 12.3%) is operationally significant and day-over-day comparison should track its evolution as Tennis dedup + Golf bootstrap land.

**Column-name caveat**: `sp.daily_diff_reports.scope_filter_version` is slightly imprecise — it now stamps metrics-schema changes too, not just scope-filter-rule changes. Column rename deferred until there's another reason to touch the table; would balloon the rename diff.

### Phase 2 sequencing locked at end-of-day-21

Empirical data forces re-prioritization. End-of-day-21 sequencing:

| Priority | Workstream | Driver |
|---|---|---|
| **Tomorrow (day-22)** | Tennis dedup OR Golf bootstrap scoping | Golf is biggest leverage per today's data (1,371 no_match/day) |
| **This week** | NON_SPORT scope-filter design | Issue #174 — 42% of records confirmed out-of-scope |
| **Next week** | 5-sport bootstrap cohort kickoff | Golf first per data; Handball/Snooker/Rugby Union/Rugby League follow |
| **Concurrent** | §6.5 archival design | 58,225 no_match retries/day; Issue #164 |

**Tennis dedup is no longer the obvious next move.** The day-20 framing positioned it as the highest-impact Phase 2 work because the 0% Tennis framing implied capability creation. Today's 24.1% measurement reframes it as a marginal-improvement lever — still worth doing, but Golf's 1,371-per-day no_match volume is materially larger leverage per engineer-day.

### v1.5 amendment pile (end-of-day-21 refinements)

The pile, updated for today's empirical findings:

1. **Neon migration (§10.1 + §11.2 + §14)** — unchanged
2. **§7.4 corroboration model** — unchanged
3. **§6.5 archival job status (#164)** — **URGENCY CONFIRMED EMPIRICALLY**. 58,225 no_match writes/day measured; the §6.5 archival is now a Phase 2 dependency with empirical sizing, not deferred maintenance.
4. **§7.7 cadence** — unchanged
5. **audit-stream separation for operator approvals** — unchanged
6. **alias-tier and fuzzy-tier don't consult `sp.team_aliases`** — unchanged (replacement landed this morning)
7. **§7.5 sport-class distinction** — **EMPIRICALLY VALIDATED** by today's bimodal histogram (zero records in the 0.85-0.95 band; clear separation between auto-apply and review_queue populations).
8. **NEW — matcher-capability vs incremental-apply distinction**: production cron measures incremental-apply rate (newly-resolved records per pass); Track A daily-diff measures matcher-capability rate (all records re-run against fresh matcher). These are conceptually distinct measurements of different things; the v1.5 amendment captures the distinction so future dashboards / metrics discussions don't conflate them. Empirical surface area: today's 0.37% (incremental) vs 48.4% (capability) — same word "auto_apply" naming two populations, must be disambiguated in schema + docs + discussions.

Pile expanded from 7 to 8 items.

### PR state at end-of-day-21

- **PR #167** — KBL methodology + Patterns A/B/C/D/E (`bfb1044` adds D refinement + E). Open, awaiting review.
- **PR #169** — 2026-05-19 entry. Merged this morning.
- **PR #175** — Track A scope doc. Merged.
- **PR #176** — Day-21 entry (morning). Merged this morning.
- **PR #177** — Track A Deliverable 2 scaffold. Merged.
- **PR #178** — Orphan `review_queue` Issue filed.
- **PR #179 (DRAFT)** — Track A Deliverable 2 implementation arc. Open, awaiting operator review.
- **PR #180** — this day-21 supplement append (Option α per end-of-day decision).

### Pending — operator-side, day-22 morning (revised end-of-day)

1. **PR #179 review + merge** — Track A Deliverable 2 implementation arc. Operator merge approves the implementation; Railway cron config follows after hobby-build resume.
2. **Phase 2 next-workstream decision** — Tennis dedup vs Golf bootstrap scoping. Today's data points to Golf; operator picks based on broader prioritization context.
3. **PR #167 review** — Pattern D/E methodology doc. Ready for operator merge consideration.
4. **§6.5 archival design** — Issue #164 promoted to active design work given today's 58K no_match/day measurement.

---

## Session — 2026-05-19

> **Forward reference (added during 2026-05-21 day-21 cycle):** This entry's "v1.5 amendment pile" section below proposes item #6 (review_queue routing on fixture-construction failure) which was subsequently identified as misdiagnosed during the 2026-05-21 KBL empirical-validation cycle. The misdiagnosed item is REMOVED from the pile and REPLACED with the alias-tier-and-fuzzy-tier-don't-consult-sp.team_aliases finding per the 2026-05-21 entry. Read this entry as a point-in-time record of day-19 framing; the day-21 entry holds the corrections.

### Track C — KBL bootstrap shipped + applied to production (validation deferred)

Phase 2C methodology pilot for the 5-sport zero-coverage cohort (Handball, Snooker, Volleyball, Rugby League, Golf — surfaced 2026-05-17). Korean Basketball League as the league-level pilot at 10 teams. Extended PR #156's national-teams bootstrap pattern with an aliases-write dimension.

**Scope shipped (PR #166, merged at 23dc495 / 15:38 UTC):**

- `scripts/kbl_seed.py` — 10-team manifest, 4-tuple `(canonical, country, aliases, notes)` format. F1/F2/F3 decisions encoded.
- `scripts/bootstrap_kbl.py` — three-branch team classifier (INSERT new / BACKFILL country_code / SKIP) + parallel alias classifier (INSERT new alias / SKIP existing). Mirrors PR #156's idempotency discipline.
- `tests/test_bootstrap_kbl.py` — 13 manifest-shape unit tests (always run) + 5 integration tests (SP_INTEGRATION_DB-gated).
- `docs/bootstraps/kbl-2025-26.md` — methodology template for cohort reference.
- `Makefile` — `bootstrap-kbl` target mirroring `bootstrap-national-teams`.

**Three F-decisions resolved during scope cycle:**

- **F1 — Canonical_name policy: mirror PR #156 precedent.** UPDATE-branch teams (Goyang Sono, KCC Egis) keep legacy canonicals; current 2025-26 official forms live as aliases. Avoids drift with FL's §9.3 canonical_name authority.
- **F2 — Anyang rebrand alias coverage: 6 distinct normalized aliases.** Coverage spans JeongKwanJang / JungKwanJang / Cheongkwanjang / 6-token Wikipedia form / KGC legacy / Hangul full. Wikipedia 6-token form was added during PR #166 review.
- **F3 — Hangul coverage: partial v1 (3 of 10 teams).** Production query returned 0 Hangul-containing rows for current KBL records; partial coverage is operationally sufficient. Remaining 7 teams tracked at #165.

**Post-review fix during PR #166 review (commit 23dc495):**

Operator's pre-merge production-data verification via DISTINCT `team_form` query against `sp.resolution_log` for `KXKBL%` records found Kalshi uses the 2-token "City Nickname" form (`Goyang Skygunners`), not the 3-token official (`Goyang Sono Skygunners`). Seed manifest was missing the empirical form. Surgical addition (1 alias, total grew 20 → 21).

This finding produced two Phase 2C cohort patterns now documented at `docs/bootstraps/kbl-2025-26.md` (PR #167, follow-up to #166):

- **Pattern A — pre-merge production-data verification is mandatory.** "The provider's actual generated form trumps the team's official name for alias coverage purposes." Authoritative-source curation (NamuWiki, Wikipedia) does not always capture provider-specific ticker-generation patterns.
- **Pattern B — observe-then-react over pre-seed-speculate for unproven aliases.** Teams with no historical provider records ship with authoritative-source aliases only; speculative short-forms risk cross-league collisions. §7.7 daily cron's re-resolution behavior provides retroactive coverage.

**Production apply attempt (2026-05-19, 15:50 UTC) — LANDED ON WRONG BRANCH:**

```
KBL bootstrap complete in 4.0s:
  Teams Inserted:      8
  Teams Backfilled:    2  (Goyang Sono + KCC Egis got country_code='KOR')
  Aliases Inserted:   21
```

Bootstrap script reported clean success. But the apply landed on the `bootstrap-test` Neon branch (endpoint `ep-square-wave-akhp46h0`) instead of production (endpoint `ep-fragrant-frog-ak3esp11`). The `DATABASE_URL` env var pointed at the wrong endpoint at apply time. **Production state remained pre-apply for ~16 hours.** Discovery + recovery covered in the 2026-05-20 session entry.

**Re-apply against production (2026-05-20, after wrong-endpoint discovery):**

```
KBL bootstrap complete in 3.8s:
  Teams Inserted:      8 (8 actually committed)
  Teams Backfilled:    2 (2 actually committed)
  Aliases Inserted:   21 (21 actually committed)
```

Endpoint sanity check pre-apply confirmed `ep-fragrant-frog-ak3esp11` (production). All 10 KBL teams now in production `sp.teams` with `country_code='KOR'`. 21 aliases in `sp.team_aliases` with `source='bootstrap_league_coverage'`.

**Validation deferred to the 02:15 UTC Kalshi cron pass following the re-apply (i.e., the night of 2026-05-20 → morning of 2026-05-21).** The 2026-05-19 → 2026-05-20 cron (run_id `a7836f51-49e0-443f-9ce9-1338e27b6b49`) ran against pre-apply production state — no KBL aliases were visible. Empirical KBL test moves to the day-21 morning.

**Pattern D (added to PR #167 at commit `c6f5b93`):** captures the methodological learning. Wrong-endpoint apply is a class of silent-success-against-wrong-DB error that costs hours-to-days of misdirection. Pre-flight `SELECT current_database(), current_schema(), inet_server_addr();` is now step 4 of the apply runbook in `docs/bootstraps/kbl-2025-26.md`.

**Three pending-verification scenarios for tomorrow morning's queue-depth query:**

```sql
SELECT COUNT(*) FROM sp.review_queue
WHERE provider_record_id LIKE 'KXKBL%' AND status = 'pending';
```

| Result | Interpretation | Phase 2 implication |
|---|---|---|
| 0 records | Bootstrap empirically validated; alias-tier alone resolved the 3 pending KBL records | Continue Track A measurement infrastructure as planned |
| 1-2 records | Partial validation; some records' fixture-construction worked | Investigate which records auto-resolved + why others didn't |
| 2 records (unchanged) | Finding 2 below (candidate_fixtures = team UUIDs) explains the persistence: fixture-construction is the blocker, not team-matching | Phase 2 priority shifts toward fixture-construction debugging alongside Track A |

All three outcomes are informative. Result drives where Phase 2 priority lands next.

### Three architectural findings surfaced during KBL apply cycle

These are orthogonal to KBL itself — surfaced during the diagnostic queries that traced KBL state. Worth pinning separately since each has Phase 2 implications larger than any single bootstrap.

**Plus a fourth finding (Tennis resolver crash, Issue #170 → PR #171) that emerged in the late-afternoon 10K-record pass — discovered + fixed + verified same day. Captured below the three architectural findings alongside the methodological learning the verification cycle produced (Pattern C).**

#### Finding 1 — 0% auto-apply rate on 100-record manual pass (HALT CRITERIA EXCEEDED)

The manual resolver pass at 15:37 UTC scanned 100 non-KBL records and **auto-resolved zero**. Consistent with the day's aggregate 87.5% no_match rate, but starker at sample level. Halt-criteria warning fired (coverage <60% threshold triggers extraction review per the parallel-run discipline).

**Initially hypothesized as partially explained by Finding 4** (Tennis crashes dragging the rate to zero by exception). **Falsified by post-fix measurement**: after PR #171 deployed + verified, a fresh `--limit 500` sample returned `crashes=0` AND `auto_applies=0`. The 0% auto-apply rate is **structurally independent** of the Tennis crash bug.

**Implication: this is now confirmed as an independent Phase 2 puzzle.** Hypothesis ranking from yesterday's PROJECT_STATE entry remains valid (threshold drift, corroboration degradation, alias-thinness, all-three) and a fourth hypothesis emerged: **34% signal_extraction_skipped** in the same 500-record sample is upstream of everything else. If half the records can't produce a FixtureSignal, the auto-apply denominator is roughly half what the morning's aggregate assumed.

**Implication: this is still bigger than any single bootstrap, even after accounting for Tennis crashes.** Coverage expansion via league/sport bootstraps addresses the long tail; the strict + alias + fuzzy tier auto-apply rate at the head of the distribution is independently degraded. Track A daily-diff infrastructure becomes more urgent — it's the only mechanism that surfaces auto-apply regression / improvement per resolver change.

Worth a dedicated investigation cycle alongside (not after) Track A. Hypotheses to discriminate (Tennis crash exclusion factor applied):
- Threshold calibration drift (auto-apply threshold too high for current production score distribution)
- Corroboration-rate degradation (cross-provider corroboration finding fewer matches than the +0.30 boost assumes)
- Alias-tier coverage thinness compounding (Sunday's "91 clean alias matches over 7 days" finding generalized to broader records)
- All three combined

The §6.5 archival gap (#164) and broken-loop scoping (Track A priority 3) sit upstream of this finding; clean measurement requires both. Putting them in Track A priorities was correct.

#### Finding 2 — `review_queue.candidate_fixtures` contains team UUIDs, not fixture UUIDs

Diagnostic check on the existing pending KBL records found `candidate_fixtures` populated with team UUIDs (`8beb6b11-...` = Goyang Sono, `00907265-...` = KCC Egis), not fixture UUIDs. This suggests the resolver identified teams correctly but couldn't construct a canonical `sp.fixtures` row from team-pair + kickoff.

Architectural implication for review_queue write semantics: the routing decision to `review_queue` fires when teams match but no fixture exists yet, NOT only when teams don't match. This is a distinct routing shape from collision (multiple team candidates) and asymmetric (one side anchored, one didn't). Whether this is the intended §7.5 design or an emergent behavior worth distinguishing in the admin UI is a Phase 2 question.

**Operational consequence:** if the 3 pending KBL records' actual blocker is fixture-construction (not team-matching), the bootstrap's aliases won't help them auto-promote even with tonight's cron. New KBL records arriving with kickoff data present should auto-resolve cleanly via the new aliases; the existing 3 records may stay pending until fixture-construction is fixed for them.

This finding interacts with Issue #162 β (NULL-kickoff approval hard-block) but is distinct: #162 fires at approval time; this finding affects routing time. Worth surfacing the relationship at the next §7.5 admin-UI work cycle.

#### Finding 3 — Operator approvals don't write to `sp.resolution_log`

The "approved" KBL record (May 9 game) reached `status='approved'` via the admin UI's review_queue.status change path, NOT via resolver auto-promotion. The `review_queue` table and `resolution_log` are separate audit streams. Operator approvals update `review_queue.status` + write `sp.team_aliases` (per `approve_record()`) but **do not** write a corresponding `sp.resolution_log` row.

Implication for §7.5 admin UI architecture and downstream analytics:
- "How many records reached resolved state in the last 7 days?" requires a UNION across `resolution_log` (resolver-side decisions) + `review_queue` (operator-side decisions). The current dry-run-*-tier and corroboration-gap diagnostics query only `resolution_log` — they under-count by the operator-approval rate.
- The day-7 retrospective on 2026-05-17 noted "2 approved records total ever (test records)" — the count came from `review_queue.status = 'approved'`. That's the right count, but verifying it required knowing about the audit-stream separation.
- When Track A daily-diff infrastructure builds, the "did the new resolver agree with the old on this record?" comparison needs both audit streams as input — otherwise diffs miss operator-decision outcomes entirely.

Not a bug per se — both streams are intentional. But the audit-stream separation deserves explicit documentation. A v1.5 architecture-doc amendment item.

#### Finding 4 — Tennis ValidationError discovered systemic, fix shipped + verified same day (#170 closed, #171 merged)

Late-afternoon manual 10K-record pass (run_id `14a94404-9183-4da6-8669-2f0ac84d631b`) revealed that the morning's ValidationError filed as #168 (initially framed as a single record's edge case) was systemic across all Tennis Kalshi record patterns:

- `KXITFWMATCH-*` (ITF Women's Tennis)
- `KXITFMATCH-*` (ITF Tennis)
- `KXATPMATCH-*` (ATP Tennis)
- `KXATPCHALLENGERMATCH-*` (ATP Challenger)

All four crashed with identical error shape: `candidate_fixtures.0 UUID input should be a string, bytes or UUID object [input_value=None]`.

**Root cause:** PR #161's asymmetric-routing branch constructed `candidate_fixtures = [anchored_team_id] + failed_side_candidates` without guarding against `anchored_team_id=None`. The personal-path matcher's collision case returns `_SideMatch(anchor_failed=False, team_id=None, collision=True)`. When this state co-occurred with anchor-failure on the OTHER side (asymmetric anchor failure + collision on the anchored side), the asymmetric branch picked the collision-side's None team_id as `anchored_team_id`, then pydantic validation rejected the list.

**Production scope** (operator scope-data queries during evening cycle):
- 3,279 records currently affected: Tennis 3,003 + UFC 238 + Boxing 38
- 46,505 crashes over 7 days; ~6,643/day; ~13% of all daily resolver decisions
- Each crashing record retried 41-44 times across daily crons over ~6 weeks

**Fix shipped + verified same day:** PR #171 (~7 LOC defensive guard + 3 unit tests) merged at e08bccf, Railway auto-deployed, verified locally via `--limit 500` sample showing crashes=0. Issue #170 closed by PR #171; #168 closed as duplicate of #170 preserving the specific reproduction record provenance.

#### Methodological learning — stale-bytecode false-positive scare (Pattern C)

PR #171 verification cycle produced a ~2-3h diagnostic detour. The `--limit 500` sample initially returned 30 crashes even after Railway confirmed PR #171 was deployed. The crashes had identical shape to pre-fix — same field path (`candidate_fixtures.0`), same error class (pydantic ValidationError), same affected ticker patterns (ITFW, ITFM, ATPChallenger).

Initial hypothesis was a second unguarded MatchResult construction site. An exhaustive code-site audit of all 6 `candidate_fixtures=` constructions across `resolver/` showed none should produce None per the matcher's `_SideMatch` invariants and `CandidateTeam.team_id` non-Optional typing. The audit-says-safe-but-observation-says-crash mismatch was the signal to test environmental causes.

**Resolution:** `find resolver -name "__pycache__" -exec rm -rf {} +` followed by re-running `--limit 500` against the same production data — crash count dropped from 30 to 0 with no other change. The local Python had been loading stale compiled bytecode from a previous run.

**Pattern C** captured in `docs/bootstraps/kbl-2025-26.md` (commit `1dfcbd4` on PR #167's branch). Cost-asymmetry pinned: environmental diagnosis ~30 seconds; code-site diagnosis multiple message exchanges + potential wrong-site patches. **Always test environmental causes (pyc, venv, deploy) before code causes when the bug shape is "audit says one thing, observation says another."**

Issue #172 filed for the complementary script-side process improvement: stderr warning + docstring guidance in `scripts/run_resolver_pass.py` for `--run-mode standalone` invocations.

### Phase 2 priority order — Track A regains #1 priority

Yesterday's PROJECT_STATE 2026-05-18 entry proposed inserting Track Z (Tennis crash containment) above Track A measurement infrastructure if the Tennis crash were still active. **Track Z's work is done.** PR #171 verified, production deploy clean, tonight's 02:15 UTC scheduled Kalshi cron will run cleanly.

**Priority order back to yesterday's framing:**

- **Track A (top priority):** Measurement infrastructure (daily diff, test corpus, re-resolution loop scope). Tomorrow morning's Phase 2 planning starts here.
- **Track B (informed by Track A):** Resolver tuning.
- **Track C (alongside Track B):** Coverage expansion (KBL pilot done; Handball next).
- **Track D (parked):** Issue #162 β NULL-kickoff (34 records, low urgency).

Track A is more urgent than yesterday's framing suggested because of two findings from today that are independent of the Tennis crash:
- **0% auto-apply rate** observed in two separate 500-record post-fix samples. Cannot be explained by Tennis crashes (which are resolved). Independent Phase 2 puzzle.
- **34% signal_extraction_skipped** observed in the same 500-record sample. Upstream of matching entirely — half the records can't even produce a FixtureSignal. Track A diff infrastructure measuring matching quality on un-extracted records would be measuring noise.

### v1.5 architecture-doc amendment pile (now 4 items)

The pile, ordered by emergence:

1. **Neon migration (§10.1 + §11.2 + §14)** — production runs on Neon Launch plan; architecture doc still says "Postgres provider — Railway-managed (Phase 0–1) → Neon evaluation at end of Phase 1." Doc-drift from Phase 1 evaluation outcome.
2. **§7.4 corroboration model (binary vs accumulating per-provider)** — code implements binary boolean has_corroboration with fixed +0.30/+0.20 bonus; doc describes "+0.05 per additional provider agreeing." Reconcile (either implement the doc behavior or update the doc to match code).
3. **§6.5 archival job status (#164)** — job never shipped; storage growth unbounded. Either implement (Track A prerequisite per Issue #164's lean) or document as deferred-to-Phase-X with explicit deferral rationale.
4. **§7.7 cadence (daily cron vs continuous 5-10 min loop)** — code implements daily cron only (FL 02:00, Kalshi 02:15 per railway.toml); doc describes continuous 5-10 min loop. Reserved `live` mode in run_resolver_pass.py:151-152 marks where it would live; Phase 2E.
5. **NEW (today): audit-stream separation for operator approvals (Finding 3 above)** — document that `sp.resolution_log` captures resolver-side decisions only; `sp.review_queue.status` captures operator-side decisions; both are intentional but the asymmetry is non-obvious for analytics work.
6. **NEW (today): review_queue routing on fixture-construction failure (Finding 2 above)** — document the third routing shape distinct from collision + asymmetric: "teams identified but fixture not yet constructible." Whether this is intentional or emergent behavior is itself a v1.5 question.

Plus the two from yesterday's 2026-05-18 entry (Phase 5 preservation steps + Neon migration § cited there).

### Deferred cleanups

- **Test constants `_BASEBALL_SPORT_ID = 3` / `_BASKETBALL_SPORT_ID = 7` in `tests/test_phase_2d5_asymmetric_routing.py`** (PR #161 era). Production has Basketball=3, not Baseball=3; the constant names mislead future readers. Lean: replace with obviously-synthetic values (`_TEAM_PATH_SPORT_ID = 901`, `_PERSONAL_PATH_SPORT_ID = 902`). Fix during whatever test file gets touched next adjacent to these constants. Not blocking, not an Issue.

### Issues filed today

- **#164** — §6.5 archival job not implemented; storage growth unbounded
- **#165** — KBL Hangul follow-up for remaining 7 teams (filed alongside PR #166)
- **#168** — Resolver pydantic ValidationError on KXITFWMATCH-26MAY19FERBAR. Initial isolated-record framing. **Closed as duplicate of #170** after the 10K-pass evening session revealed the systemic shape.
- **#170 (P1)** — Systemic Tennis resolver ValidationError, `candidate_fixtures[0] is None`, crashed every pass. **Closed by PR #171** (defensive guard at the asymmetric branch).
- **#172** — `run_resolver_pass.py --run-mode standalone` should add stderr warning + docstring guidance about clearing `__pycache__` before invocation. Captures the script-side complement to Pattern C's operator-side discipline.

### PR state at end of session

- **PR #166** — KBL bootstrap (Phase 2C). Merged at 23dc495 / 15:38 UTC. Apply attempt at 15:50 UTC landed on wrong Neon branch (`bootstrap-test`); production-re-applied on 2026-05-20 after wrong-endpoint discovery. See Pattern D + day-20 entry.
- **PR #167** — Docs follow-up for KBL methodology. Patterns A + B initially; Pattern C added at commit `1dfcbd4` (stale-bytecode diagnostic discipline) during today's late-evening cycle. Open.
- **PR #169 (DRAFT)** — This 2026-05-19 PROJECT_STATE entry. Reframed to scenario-A after `--limit 500` post-`__pycache__`-clear confirmed PR #171's fix is correct as-is.
- **PR #171** — Tennis ValidationError guard. Merged + Railway-auto-deployed + verified via `--limit 500` (crashes=0). Closes #170.
- **PR for 2026-05-18 PROJECT_STATE entries** — branch `claude/project-state-2026-05-18-phase5-decision` carries 4 commits (α queue-depth finding, priority reorder, Phase 5 preservation, Phase 5 tag pin). No PR opened yet.

### Pending — operator-side, tomorrow morning

1. **KBL queue-depth verification (this morning's deferred test).** Run `SELECT COUNT(*) FROM sp.review_queue WHERE provider_record_id LIKE 'KXKBL%' AND status='pending';`. Three scenarios documented in the KBL bootstrap section drive Phase 2 next-priority interpretation. Result is informative regardless of which scenario lands.
2. **Tonight's 02:15 UTC Kalshi cron health check.** Pull `sp.resolver_runs` row. Verify `crashes` is at or near zero — confirms PR #171's fix landed in production cleanly (the locally-verified `crashes=0` was against production DB, but tonight's cron is the empirical real-cron-pass confirmation). Optionally count new `fail_reason="fuzzy_collision_no_anchor"` rows in `sp.resolution_log` for the previously-crashing population now routing cleanly.
3. **Track A start.** Daily-diff infrastructure scope doc cycle. The 0% auto-apply rate finding + 34% signal_extraction_skipped finding both need Track A measurement infrastructure to characterize cleanly.
4. **Review PR #167** (docs follow-up; now includes Pattern C) and merge if approved.
5. **Review PR #169** (this PROJECT_STATE entry) and merge if approved.
6. **Investigate `resolver-cron-fl` deploy failure at b1a46867** (Railway). Surfaced during today's late-evening Railway dashboard exploration. Doesn't affect tonight's Kalshi cron — separate service. Tomorrow when fresh.
6. **Decide ordering** of 2026-05-18 PROJECT_STATE PR open vs #169 merge — file header conflict possible if both merge separately.

---

## Session — 2026-05-17

### Phase 2F.1.5 day-7 retrospective + 2D.4 three-tier resolver review

First day-7 retrospective in the Phase 2F program. The 2F.1.5 (operator-throughput) and 2D.4 (three-tier resolver) retrospectives fold into the same session — same `sp.resolution_log` queries surface both retrospectives' findings.

### Session shape and limitations

Operator (jcz) did not drive the review queue between PR #156 production apply (2026-05-14 19:30 UTC) and today's session — Friday/Saturday used for non-engineering work per standing instructions.

This means 2F.1.5 retrospective measures **resolver-side data only**. Operator-throughput data (UI friction, per-record decision time, approve/reject distribution) is structurally absent. Two pending approvals exist in `sp.review_queue`; both are pre-launch test records, not real operator work. Operator-driven validation deferred to follow-up session and begins concurrent with Priority A development.

### Volume reality

- **22,619 unique records** over 7 days (2026-05-11 to 2026-05-17)
- **~3,231 records/day raw** — approximately 3.2× the design doc's ~1,000/day estimate
- Provider split: FlashLive 67.4% (15,250), Kalshi 32.6% (7,369)

After filtering Golf single-player records (out-of-scope by design — see Finding 2): 19,313 records/week, ~2,759/day, about 2.8× design estimate. Still meaningfully above estimate but in a believable range.

### Resolution rate reality

Latest decision per unique `provider_record_id`:

| Outcome | Records | % | Notes |
|---|---|---|---|
| `no_match` | 13,059 | 57.7% | Largest bucket; further decomposed below |
| strict tier clean | 5,149 | 22.8% | All clean matches come from strict |
| `review_queue` routed | 4,312 | 19.1% | Reaching operators correctly |
| alias tier clean | 91 | 0.4% | Almost zero |
| fuzzy tier clean | 8 | 0.04% | Essentially zero |

**Key observation**: alias and fuzzy tiers produce 99 total clean matches over 7 days. The system runs almost entirely on strict tier. Alias coverage is genuinely thin; fuzzy threshold is conservative enough that most fuzzy decisions go to `review_queue` rather than auto-resolve.

### sp.review_queue state

- **4,822 pending records** waiting for operator decisions
- **2 approved records** total ever (test records from pre-launch operator development)
- **0 rejected records**

At design-assumption 240 records/day operator capacity, current backlog alone takes ~20 days to clear. Daily inflow of new review_queue records is ~616/day (4,312 over 7 days). Daily deficit at sustainable capacity: ~376/day, growing.

### The no_match bucket decomposed

| Fail reason | Records | % of no_match |
|---|---|---|
| `fuzzy_no_team_resemblance` | 6,696 | 51.3% |
| `structural_normalize_failed` | 3,306 | 19.8% (100% Golf single-player) |
| `deferred_to_2d` | 1,789 | 13.7% (NOT a bug — see Finding 3) |
| `sport_not_classified` | 958 | 7.3% |
| `alias_no_team_resemblance` | 793 | 6.1% |
| `below_review_threshold` | 228 | 1.7% |

### Findings inside the no_match bucket

#### Finding 1 — `fuzzy_no_team_resemblance` has three distinct subgroups (most important finding)

The 6,696 records in this bucket aren't homogeneous:

**Both sides anchor-failed: 5,024 (74.7%)** — genuine coverage gaps. Sport breakdown: Baseball 19.6%, Tennis 19.1%, Handball 11.8%, MMA 7.9%, Basketball 6.8%, Darts 5.6%, Rugby Union 5.1%, Cricket 4.3%, Boxing 3.9%, Snooker 3.7%, Soccer 3.6%, Aussie Rules 3.2%, others. Need actual data work (player rosters, alias expansion, threshold tuning).

**Asymmetric records: 1,705 by initial slice, 2,051 by separate breakdown** — one side anchored cleanly, other didn't. Further decomposed via heuristic on `provider_normalized LIKE '%:%'`:

- **~89.6% (1,837) are real asymmetric records** that should route to operators
- **~10.4% (214) are Kalshi prop-bet markets** ("Colorado: First Inning Run", "Shakhtar: First Half Winner") — structural artifacts where the "away" field contains a market-segment label, not a team/player name

Sport patterns within real asymmetric:

- **Tennis surname-only failures**: Kalshi sends surname-only tickers (e.g., "Rogers" vs "Kalieva"); `sp.teams` has the full-name entry for one player ("Elvina Kalieva") but not the other. The trigram threshold can't bridge "Rogers" to "Sloane Rogers" at 0.30.
- **Baseball prop markets and minor-league failures**: NY Mets + "Colorado: Hits" / "Colorado: First Inning Run" pattern dominates the sampled Baseball asymmetric records. Real minor-league coverage gaps exist (e.g., "Club 360" / "Glitch FC" Soccer minor league).
- **Soccer minor-league failures**: Real two-team games where one team has a coverage gap (e.g., "Club 360" matched / "Glitch FC" not matched).

#### Finding 2 — `structural_normalize_failed` is fully characterized

100% of 3,306 records are Golf with `is_personal=true`. Golf data flowing through head-to-head matcher; normalizer correctly identifies single-competitor records and reports `away_normalize_succeeded: false` because there is no away side. This is a **product gap, not a bug**. Decision needed: filter out at ingestion, or build single-competitor resolver path. Not immediate priority.

#### Finding 3 — `deferred_to_2d` is NOT a bug (correction from initial framing)

Initial framing during session was wrong. Investigation revealed: the three tiers (strict, alias, fuzzy) all run on every record in the same orchestrated pass with the same `run_id`. Three rows get written, ~6 microseconds apart. The `DISTINCT ON (provider_record_id) ORDER BY decided_at DESC` query was picking the alias-tier `deferred_to_2d` row as the "latest" decision because alias's timestamp is the latest of the three (the cron's alias-then-fuzzy execution order produces alias-tier rows with later microsecond timestamps than the fuzzy-tier row written immediately before, even though fuzzy's decision is the operationally meaningful one).

In reality, the fuzzy tier IS processing these records — they're getting routed to `review_queue` or another no_match category. The 1,789 records appearing as "ending at deferred_to_2d" are actually getting their final decision from the fuzzy tier; the alias row is just the chronologically-last log entry.

**Verification**: sampled 49 of these records' fuzzy-tier decisions — all 49 with fuzzy `review_queue` decisions are present in `sp.review_queue`. System routing is working correctly.

No `deferred_to_2d` bug exists. Worth noting in the logging layer's display semantics — the `deferred_to_2d` label is confusing if you're querying "latest decision per record" — but the system itself is functioning correctly. The Priority A item that the session initially identified ("fix `deferred_to_2d` routing") was disqualified by this investigation; the actual Priority A is the asymmetric routing intervention.

### Country-name collision pattern (pre-registered from PR #156)

Confirmed in production data. The Senegal/France record (`KXWCGAME-26JUN16FRASEN`) now routes to alias-tier `review_queue` with 5 candidate France-named teams. Same pattern likely affects other country bootstraps when matched against legacy club data. **Not a regression** — exactly the operational consequence pre-registered in PR #156's Phase 2 verification.

### Operational impact framing

System processes 22,619 records/week with these outcomes:

- ~26.7% cleanly resolved (against non-Golf denominator of 19,313)
- ~22.3% routed to operators for review (4,312 / 19,313)
- ~40% genuinely unhandled with current coverage and matcher design

The matcher is doing more than half its job. The bottleneck isn't matcher quality — it's a combination of:

1. Coverage gaps in `sp.teams` and `sp.team_aliases` for individual-athlete sports
2. Asymmetric records being correctly identified as failures but not surfaced to operators
3. Operator throughput unverified (no real operator work yet)

### Concrete next-step priorities

#### Priority A — Route real asymmetric records to review_queue

**Records affected**: 1,837 currently dropped at `no_match`, should be operator-actionable.

**Why first**: highest leverage of the immediate options. Records exist, one side resolved cleanly (so the operator has anchoring context), the other side has a parsed name available. The collision review surface shipped this week handles exactly this shape — operator clicks the unmatched player/team's name, gets fuzzy candidates, picks correct match or adds alias.

**Scope**: resolver decision logic change. When fuzzy tier completes with `home_anchor_failed XOR away_anchor_failed = true` and at least one canonical resolved, route to `review_queue` instead of `no_match`. Pre-filter out Kalshi prop-market shape (`provider_normalized` contains `:` — heuristic, refine in scope doc).

**Estimated**: 1-2 days including tests + verification.

**Direct effect**: ~1,837 records per week (~262/day) become operator-actionable. Reduces "real unhandled" volume by ~24%.

#### Priority B — Coverage work for individual-athlete sports

**Records affected**: ~5,024 both-failed records, dominated by Baseball (1,318), Tennis (1,281), Handball (790), MMA (531), Basketball (454), Boxing (265).

**Why not first**: larger scope, requires data curation per sport. Specifically:

- **Tennis**: surname-only Kalshi tickers vs full-name canonicals — needs surname-aware matching OR per-player alias generation
- **Baseball**: minor-league teams likely the gap (similar shape to legacy Soccer roster)
- **MMA/Boxing**: individual fighter coverage similar to PR #156 national-teams shape
- **Handball**: lower-priority coverage gap

**Estimated**: Multi-PR effort spread over weeks. Tennis is probably the natural first sub-target given the volume and shape.

#### Priority C — Operator-driven validation

The honest gap: 4,822 records sit in `review_queue` waiting for any operator. Until someone actually drives the queue, we don't know:

- Whether the UI is fast enough for sustained operator throughput
- Where friction points exist (which the design doc anticipated would surface during 2F.1.5)
- Whether the country-name collision pattern is fast to resolve in practice or slow

This isn't engineering work — it's product validation work. But it has to happen, and the longer we ship matcher improvements without testing the operator surface against real records, the more we're building features without feedback.

**Concrete plan**: start driving the queue concurrently with Priority A development. ~15-20 minutes/day, beginning Monday 2026-05-18.

### Out of scope this week — but worth pinning

- **Golf coverage path** (3,306 records). Either filter at ingestion or build single-competitor resolver. Product decision needed. Not urgent.
- **Kalshi prop-bet handling** (214 records, plus likely more not in the asymmetric bucket). Ingestion-layer filtering or primary-market-attachment strategy.
- **Threshold tuning for Tennis surname-only matching** (Issue #142 territory). Once Tennis coverage work has data, threshold-tuning becomes a real conversation.
- **`sport_not_classified` diagnosis** (958 records). Likely small slice but unexplored.
- **`alias_no_team_resemblance` vs `fuzzy_no_team_resemblance`** (793 records). Investigation deferred — likely same shape as fuzzy bucket.

### Carried items still active

The three day-7 prep observations from PR #156's Phase 2 verification:

1. **Dry-run gating as structural safety pattern** — confirmed valuable, name explicitly in next PROJECT_STATE entry. Phase 1.5 backfill catch on PR #156 was a real-world instance.
2. **2A.5 ↔ 2F latent-state class of bug** — instance found and fixed via Phase 1.5 backfill in PR #156.
3. **Country-name collision pattern** — materialized in production, behaving as pre-registered. Not a regression.

### Engineering observations from today's session

- The `deferred_to_2d` wrong-turn was caught only because of the "do both" instinct earlier in the session pushing through to investigate rather than declaring victory at a non-bug. Worth pinning as a class-of-observation: **latest-decision-per-record queries against multi-tier resolver logs can mask the operationally meaningful decision when tier writes are microseconds apart.** Future analysis queries should either filter by `resolver_version` or use a different aggregation strategy.
- The **Kalshi prop-bet pattern** wasn't anticipated by the design doc and didn't surface in PR #156's smoke tests because national-team records don't have prop markets. Worth filing as tech-debt issue: "Kalshi prop-bet records flow through head-to-head matcher producing structural false-failures."
- The **Golf product gap** (3,306 records / 14.6% of weekly volume) is the largest single category of records the matcher wasn't designed for. Worth a product decision before next planning cycle.

### Decision artifact

Immediate work begins on **Priority A — asymmetric record routing**. Scope doc cycle with Claude Code starts today, implementation begins as soon as scope is approved. No artificial pacing — operator has bandwidth and prioritizes this.

Operator-driven validation (Priority C) layered in concurrently — ~15-20 min/day starting Monday 2026-05-18.

---

## Session — 2026-05-12

### Phase 2F.0.1 + 2F.1 closeout — pg_trgm migration, anchor_failed surface hardening, sub-PR #4 production smoke completed (✅)

Phase 2F.1's anchor_failed surface (sub-PR #4, shipped 2026-05-11) reached
production smoke validation today. Smoke testing surfaced **four
distinct issues** in cascading sequence; all four were addressed via
focused PRs landing in correct dependency order per Issue #129's
PR-ordering convention. The day closes with anchor_failed routing
operators to actionable suggest-alias widgets for the dominant record
shape, and with all major roadmap items through 2F.1 marked complete.

This was the highest-yield smoke-test day of the 2F phase — three of
the four findings were operationally invisible until production data
ran against the surface. Worth naming the pattern explicitly:
integration tests prove the code is right; production click proves
the deploy + data + extension state is right; **both matter**.

### What landed (PRs merged, in order)

- **PR #140** — Phase 2F.0.1 `pg_trgm` extension migration. Single-line
  alembic revision `b8e1f4c2a7d3` (`CREATE EXTENSION IF NOT EXISTS
  pg_trgm`). Surfaced during PR #133 smoke test against
  France/Senegal — `pg_trgm` was **available** on the Neon server
  (visible via `pg_available_extensions`) but **never activated** in
  `sports_prod` (`installed_version IS NULL`). Two existing call sites
  depended on `similarity()`: `admin/queries.py:_build_suggested_aliases`
  (the suggest-alias widget) and `scripts/alias_add.py` (the
  "team not found in sport" error path). Both 500'd in production
  pre-#140. Verification via PR comment showing
  `pg_extension` row (`extname='pg_trgm', extversion='1.6'`) AFTER
  `alembic upgrade head` against production. Forward+downgrade+
  re-upgrade roundtrip verified locally; verification artifact comment
  is the downstream-gate per Issue #129.

- **PR #137** — Phase 2F.1 sub-PR #4.1. Suggest-alias widget's `{% else %}`
  branch in `admin/templates/anchor_failed_detail.html` split into four
  distinct state branches: `ok` (existing candidate-button list),
  `no_good_candidates` (Path B — stub `make alias-add` command with
  `--team-canonical ''` left blank), `no_parsed_names` (Path C —
  surface raw payload + reference PR #138), `unclassified` (Path A —
  sub-PR #4 original message kept as-is). State assignment lives in
  `_build_suggested_aliases`; template branches on
  `detail.suggested_aliases_state` string equality. Introduces
  `SUGGESTED_TEAMS_MIN_SIMILARITY = 0.30` and the B-aware
  parsed-name-source fallback (`reason_detail._provider_normalized`
  → `_canonical` → FL `raw_payload.HOME_NAME` / `AWAY_NAME` — Kalshi
  title NOT auto-split because format varies). Smoke-validated against
  France/Senegal (`KXWCGAME-26JUN16FRASEN`) — rendered Path C
  correctly with the "(Soccer)" sport-name disambiguation parenthetical
  added during template review.

- **PR #138** — Phase 2F.1 sub-PR #5. Resolver-side fix: lift the
  four parsed-name preservation assignments
  (`home_provider_normalized`, `away_provider_normalized`,
  `home_canonical`, `away_canonical`) above the anchor-failure
  early-return in `resolver/fuzzy_tier/matcher.py:217-221`. Mirrors
  alias-tier's already-correct pattern at
  `alias_tier/matcher.py:208-211`. Pre-#138 records in
  `sp.resolution_log` are append-only audit; they stay at Path C
  indefinitely. Post-#138 records route to Path B or `ok` per the
  PR #137 state machine. Two production smoke records cited in the
  PR body: France/Senegal (Soccer, Kalshi) AND UFC Fight Night
  (`KXUFCFIGHT-26MAY16TGTERS`, MMA — "George Tuco Tokkos" /
  "Ivan Erslan"). Two sports, two providers, same bug pattern.
  Verified post-merge via SQL probe — `has_home_pn=true` on rows
  created post-cron. Drive-by: also fixed
  `test_phase_2f0_migration::test_upgrade_then_downgrade_roundtrip`
  which silently broke when PR #140 extended the migration chain
  past 2F.0 (test assumed `downgrade -1 from head` returns to
  pre-2F.0; now returns to 2F.0 only).

### Production issues discovered AND closed during today's smoke loop

Four distinct issues, each surfaced by a different test record. None
catastrophic. All four addressed within the day.

- **Issue 1: `pg_trgm` not activated in production** — surfaced by the
  first attempted detail-view click. Closed by PR #140.

- **Issue 2: Suggest-alias widget conflated three "no candidates" causes
  into one wrong message** — France/Senegal showed
  "Matcher didn't classify a sport" when sport WAS classified
  ("Soccer"). Root cause: empty-dict truthiness fell through to a
  single `{% else %}` branch. Closed by PR #137 (four-state machine
  + Path B / Path C explicit branches).

- **Issue 3: fuzzy tier dropped parsed names on anchor-failure
  early-return** — France/Senegal and UFC Fight Night records had
  `reason_detail` without `home_provider_normalized` /
  `home_canonical` etc. Alias tier preserved them; fuzzy tier didn't.
  Closed by PR #138 (lift assignments above early-return).

- **Issue 4: asymmetric anchor failures silently omit the failed side**
  — three FL Basketball records (`fJ2dHHQj`, `bsAhY9ld`, `dSItAYDD`)
  rendered only the anchored side's candidate buttons. State machine
  routes mixed-per-side records to `ok` because at least one side has
  candidates; template skips sides with empty `candidates` lists. Filed
  as **Issue #143**; fix tracked as sub-PR #6 (sequenced after this
  journal entry).

**Key structural finding (Issue 4)**: pure Path B
(`no_good_candidates`) is structurally unreachable for asymmetric
records — any side with at least one above-threshold candidate routes
the record to `ok` state. Pure Path B requires BOTH sides to have
zero candidates, which the fuzzy tier's permissive per-side anchoring
rarely produces in current data. Issue 4's fix in sub-PR #6 makes
Path B-shape rendering visible **per-side** inside the `ok` state
without changing the four-state record-level model. This was framed
in the original PR #133 conversation as "Path B is rare in
production" — the sharper framing is "structurally unreachable for
the dominant record shape."

### Day-0 production shape (post-#138, post-cron)

```
sp.resolution_log fuzzy_no_team_resemblance rows / cron:    ~50-80
  with parsed names preserved (post-#138, last 30 min):     100%
  pre-#138 (older rows, audit log immutable):                stay
                                                             Path C
  asymmetric anchor failure (one side, not both):           ~dominant
                                                             shape
                                                             in casual
                                                             browsing
sp.team_aliases.source values in production:
  legacy_bootstrap                                           (Phase 2A.5)
  alias_tier / fuzzy_tier                                    (runner write-back)
  operator_review                                            (PR #123 approve)
  manual_anchor_failed                                       (NEW: PR #133;
                                                             sub-PR #4
                                                             primitive,
                                                             ~0 rows
                                                             today —
                                                             will grow
                                                             as operators
                                                             use the
                                                             clipboard
                                                             widget)
```

Day-7 measurement window for 2F.1 opens ~2026-05-18.

### Engineering observation: implicit boundary contracts (continued from 2D.3 entry)

Yesterday's entry named the pattern: **"implicit data contracts at
module boundaries"** — three production bugs in three days during
the 2F.1 mutation work, all surfacing in `_validate_candidate_team_id` /
session autobegin / positional indexing. Today extended the same
pattern with four more cases.

What's worth carrying forward: **today's four issues were caught
earlier than yesterday's three.** Specifically:

- **PR #140 (pg_trgm extension)**: caught in seconds, by the first
  attempted detail-view click. Production data + production extension
  state combined to expose it. No automated test could have predicted
  this; the contract was between `admin/queries.py` and Neon's
  installed-vs-available extension state.
- **PR #137 (template conditional)**: caught in the same session as
  the pg_trgm bug — France/Senegal records exercised the wrong-message
  path immediately. Contract was between empty-dict truthiness and
  template's `{% else %}` semantics.
- **PR #138 (resolver-side preservation)**: caught by the diagnosis
  trail of PR #137 — looking at WHY France/Senegal had no parsed
  names traced back to the fuzzy tier's early-return. Contract was
  between fuzzy-tier emission and alias-tier-equivalent expectations.
- **Issue #143 (asymmetric per-side rendering)**: caught during PR #137
  smoke verification rounds — three out of three browsed records hit
  the bug. Contract was between record-level state machine and
  per-side template rendering.

The "implicit boundary contracts" pattern is now formally named **and
operationally validated**: each of the four cases would have stayed
hidden behind a "looks fine in tests" state until a real production
record exercised the contract. The bias was that pre-production
integration tests **only seeded data they knew about** — France/Senegal
was a real-world record with shapes the tests didn't anticipate.

**Mitigation worth committing to from today forward**:

1. Sub-PR-#4-style smoke testing isn't optional. Every operator-facing
   surface needs at least one round of production-data browsing
   before "ready to declare shipped." Integration tests prove the
   code; production click proves the deploy + data + state.
2. Schema-gotcha-style discoveries (sp.resolver_runs.id BIGINT vs
   run_id UUID, caught early during PR #133 work) suggest a
   prospective audit: every JOIN crossing the BIGINT/UUID schema
   line should have a typed comment at the SQL site explaining
   which column is which. Filed informally — would be a 2F.X /
   2G concern if it becomes a pattern.
3. Issue #129's downstream-gating convention worked cleanly today:
   PR #140 → #137 → #138, each gated on production verification of
   the upstream. Branch protection blocked one inadvertent direct-
   push attempt, demonstrating "value-of-branch-protection working
   as intended."

### Tracked deviations from PHASE_2F_DESIGN rev1.2

- Sub-PR #4.1 (PR #137) was not in the original 2F.1 design — emerged
  from production smoke test as a four-state refinement of the original
  two-state widget. Design doc could be bumped to rev1.3 to capture the
  state machine, but the cost-benefit (more doc churn vs. PR #137's
  inline state-machine documentation) doesn't justify it for a
  shipped-and-validated state. Leave rev1.2 as the lock; PR #137's
  inline comments are the source of truth for the state model.
- Sub-PR #5 (PR #138) is on the rev1.2 fallback path under §Q6
  ("anchor_failed surface in 2F.1 OR hard-sequenced 2F.2"). It
  shipped in 2F.1 timeline, not 2F.2 — better than the design's
  fallback. No deviation in spirit.
- Sub-PR #6 (Issue #143 fix, in flight) was unanticipated by rev1.2.
  Not on the design doc; emerged from production smoke. Same
  rev1.3-vs-leave-alone tradeoff as #137 — leaving rev1.2 as-is.

### Open follow-ups (issues filed today, ordered by intended action)

**Actionable now (small admin polish, no day-7 dependency):**
- **#131** — Approve route empty body on `ApprovalError` (HX-Request branch + `_error.html` partial). ~30-40 LOC. Real UX bug.
- **#132** — `hx-disabled-elt` selector typo (one-line fix). ~5 LOC.
- **#134** — Per-record recurrence count column on anchor_failed list view. ~20 LOC. Operationally useful for day-7 triage.
- **#143** — Asymmetric anchor failure silent omission (sub-PR #6 in flight). ~40-60 LOC. Closes the dominant-shape gap before day-7 measurement window opens.

**Tech-debt + convention (low priority, ship-when-convenient):**
- **#141** — Pin `SUGGESTED_ALIASES_STATE_*` constant values (template literal-string drift guard). ~15 LOC.
- **#144** — Add `test_phase_2f0_1_pg_trgm_migration::test_upgrade_then_downgrade_roundtrip`. ~80 LOC.
- **#145** — Migration test scoping convention (PR template checkbox + optional static guard). Establishes the prospective convention #144 instantiates.

**Deferred (gated on day-7 or external triggers):**
- **#135** — UI affordance to create canonical `sp.teams` from anchor_failed surface. Deferred indefinitely pending day-7 data.
- **#136** — Bootstrap national-team rows into `sp.teams`. Defer to 2D.5.X OR sooner if World Cup / Euros / AFCON within 8 weeks.
- **#142** — `SUGGESTED_TEAMS_MIN_SIMILARITY` length-aware tuning. Passive marker — DO NOT ACT until day-7 data shows real operator-reported missing matches.

**Passive marker (3-year-out concern):**
- **#139** — Investigate Python-side `rapidfuzz` alternative if Neon ever drops `pg_trgm`.

### Notes for the next session's first 5 minutes

- **Sub-PR #6 is the next scheduled work** (Issue #143). Branch
  `claude/phase-2f1-sub-PR-6-asymmetric-anchor-failure` is already
  checked out at the start of this entry's commit; implementation
  follows immediately.
- **Day-7 measurement window opens ~2026-05-18.** Same shape as
  2B/2C/2D.4 day-7 reviews: query `sp.resolver_runs` for the week,
  measure operator throughput, decide on 2F.X prioritization. The
  asymmetric-anchor-failure fix (sub-PR #6) lands before the window
  opens so day-7 data isn't biased by the silent-omission bug.
- **No actively-scoped 2F.2 / 2F.3 work** (per `PHASE_2F_DESIGN.md:364`
  — "Quality-of-life improvements, optional, gated on day-7"). The
  next time-bound item after sub-PR #6 is the day-7 measurement
  itself.
- **Phase 3 doesn't have a design doc yet.** 2D.5 has a draft design
  in PR #111 but is paused per the 2026-05-10 spot-check. If day-7
  data triggers 2D.5 resumption, that's the natural next-phase
  decision point.
- `PROJECT_STATE.md` is now current through 2F.1 closeout. The next
  entry should pick up at sub-PR #6 ship + day-7 review, whichever
  lands first.

---

## Session — 2026-05-10 / 2026-05-11

### Phase 2F.0 + 2F.0.5 + 2F.1 — operator review-queue UI shipped (✅ minus anchor_failed)

The operator review-queue UI is live in production. Two operators can log
in, page through pending review_queue rows, inspect the matcher's
reasoning, and approve or reject — with `sp.team_aliases` getting a real
`source='operator_review'` write-back on approve. Every item under §"2F.1
— Minimal review UI" of `PHASE_2F_DESIGN.md` rev1.1 is shipped except the
anchor_failed surface (sub-PR #4), which is the next planned work item
and is genuinely greenfield (no draft, no partial implementation).

This was a ~36-hour stretch broken into three logical phases that landed
back-to-back: the schema migration (2F.0), the runner write-side update
to populate the new columns going forward (2F.0.5), and the UI itself
(2F.1) shipped as four sub-PRs of which three are merged. Phase 2F.1
also generated three unplanned production incidents — none catastrophic,
all the same class of bug — which warrant their own subsection below.

### What landed (PRs merged, in order)

- **PR #112** — `PHASE_2F_DESIGN.md` rev1.1 (doc-only). Locked the
  design with Q1–Q8 resolved: server-side rendering with HTMX progressive
  enhancement; cookie-signed bcrypt auth via env vars (two seats, no
  user table); list+detail+approve+reject in 2F.1; anchor_failed
  surface in 2F.1 OR hard-sequenced 2F.2 (Q6 revised); "(collision)"
  cosmetic for `confidence=0` (Q8). The design predates implementation
  to keep scope-creep audits cheap.

- **PR #114** — Phase 2F.0 schema migration
  (`20260510_1800_a1c4f9e8b2d7_phase_2f0_review_queue_columns.py`).
  Three columns added to `sp.review_queue`: `reason_detail` JSONB
  (snapshot of `MatchResult.reason_detail` at insertion — denormalized
  so the UI reads a single table per page; staleness is acceptable
  because the matcher decision was correct at insert and that's what
  the operator is reviewing), `provider_title` TEXT (snapshot of
  Kalshi's `raw_payload->>'title'` or FL's synthesized `"home vs
  away"`; saves per-record JSONB parsing on every page load), and
  `rejection_count` INTEGER NOT NULL DEFAULT 0 (guardrail against
  operator burnout cycles — 2F.1 surfaces it in the list view; 2F.X
  adds the unreject button + runner-side skip logic). Plus a partial
  index `ix_review_queue_pending_confidence` on `(status, confidence
  DESC, created_at) WHERE status='pending'` to cover the list view's
  default query without sort-at-query-time. Latency budget: <500 ms
  p95. Existing 2,263 pending rows backfilled with NULL for
  `reason_detail`/`provider_title` (UI falls back to provider-table
  JOIN) and 0 for `rejection_count` (server-side default).

- **PR #115** — Phase 2F.0.5 runner write-side.
  `scripts/run_resolver_pass.py` now populates `reason_detail` and
  `provider_title` on every `INSERT INTO sp.review_queue ... ON
  CONFLICT DO UPDATE`. Kalshi branch reads `raw_payload['title']`;
  FL branch synthesizes from `HOME_NAME` + `AWAY_NAME`. Without
  this, new 2F.0-shape rows would have NULL on the new columns and
  force the UI's fallback path on 100% of records — the migration
  is online but useless until the runner backfills it forward.
  Shipped same day as 2F.0 to keep that gap to a single overnight
  cron cycle.

- **PR #117** — Migration-bearing PR checklist + PR template. Added
  `DEPLOYMENT.md → Migration-bearing PR checklist` (Railway does NOT
  auto-run alembic on deploy; migrations are manual) and
  `.github/PULL_REQUEST_TEMPLATE.md` with explicit checkboxes for
  forward+downgrade roundtrip, `alembic current` verification, and
  operator action-after-merge. Process change, not feature work —
  but motivated by 2F.0 being the first migration since 2C and a
  clean template existed only in DEPLOYMENT.md, not at PR-creation
  time.

- **PR #118** — Phase 2F.1 sub-PR #1: admin auth scaffolding.
  bcrypt password hashing, itsdangerous signed cookies, two
  `ADMIN_USER_*` / `ADMIN_PASS_HASH_*` env-var seats,
  `require_operator` FastAPI dependency. `GET /admin/` returns 503
  (not 500) when env vars unset — explicit "not configured" instead
  of leaking a ConfigurationError stacktrace.

- **PR #119** — Phase 2F.1 sub-PR #2: read-only list + detail views.
  10-column list view (Provider, Ticker, Title, Sport, Kickoff,
  Confidence, Tier, Candidates, Status, Created) with
  provider/tier/confidence_min filters and offset pagination.
  Detail view with the design's three panels (raw payload, parsed
  fields, matcher decision). `_format_confidence` renders the
  "(collision)" cosmetic for `confidence=0` per Q8. Vendored
  htmx-1.9.10 from raw.githubusercontent.com (47,755 bytes) to
  avoid CDN dependency. The list view shipped with 10 columns
  rather than the design's 9 — Status was added at operator
  request during PR review; documented deviation.

- **PR #122** — CSS extraction (closes issue #120). Consolidated
  inline `<style>` blocks from templates into
  `admin/static/admin.css`. Pure refactor, no behavior change.
  Filed pre-mutations to keep the approve/reject diff focused.

- **PR #123** — Phase 2F.1 sub-PR #3: approve / reject mutations.
  `_validate_candidate_team_id` enforces server-side that the
  operator-selected team_id is in the candidate set (Python
  pre-flight + SQL `WHERE status='pending'` + rowcount==0 raise —
  three-layer idempotency). Approve writes to `sp.team_aliases`
  with `source='operator_review'` and resolves the fixture; reject
  increments `rejection_count`. Two commits piggy-backed on the
  main diff:
    - **Commit A** — `confidence_min` empty-string filter returns
      422. FastAPI's `float | None` binder treats `""` as a parse
      error; fixed by binding as `str | None` and parsing
      defensively. Filter-UX bug surfaced during local smoke; not
      on the design.
    - **Commit B** — `next_record_id` referenced in the decision
      template but never computed. Added
      `find_next_pending_record_id()` helper and threaded it
      through approve/reject/detail handlers so the "Next record"
      link actually works. Filter-context limitation (next pending
      ignores current filter set) deferred to 2F.X.

### Production issues encountered during 2F.1 rollout

Three production bugs in three days, all surfacing in PR #123's
approve path. None catastrophic — each was operator-visible (500 on
click, recoverable by reload) and patched within the hour. But they
all sit in the same class: **implicit data contracts at module
boundaries**. Worth naming the pattern so the next phase catches it
earlier.

- **PR #125 — `approve_record` session autobegin conflict.** First
  approve-click in production returned 500. SQLAlchemy 2.0
  autobegin: the pre-flight reads in `approve_record` opened an
  implicit transaction; the explicit `async with session.begin():`
  block then raised `InvalidRequestError: A transaction is already
  begun on this Session`. The runner side (PR #108) had set the
  right pattern — reads inside the same `begin()` block — but
  `approve_record` was written without referencing that convention.
  Fix: move all reads inside `session.begin()`. Local repro via
  `apt install postgresql` + `pg_ctlcluster 16 main start` for
  integration tests; that local harness is the new standard for
  any DB-transaction PR going forward. Added
  `test_approve_does_not_hit_session_autobegin_conflict` as a
  regression guard. While fixing, also caught the HTMX fragment
  template referencing `detail.*` after the mutation but the
  handler not re-loading `detail` post-commit — fixed in the same
  PR.

- **PR #126 / #127 / #128 — Minnesota vs Cleveland partial-collision
  500.** Bradley vs Campbell approved cleanly (alias-tier,
  non-collision shape). Minnesota vs Cleveland returned 500 on
  every approval attempt. `candidate_fixtures` JSONB column stores
  team_ids in a layout that depends on the matcher branch that
  wrote it: non-collision → `[home_id, away_id]`; full-collision →
  `[home_cands..., away_cands...]`; **partial-collision (one side
  colliding, one not) → `[colliding_side_cands..., empty]`** — so
  positional `[1]` picked the second HOME candidate instead of the
  away default. `_validate_candidate_team_id` correctly rejected
  it as out-of-set; the 500 was the validator working as designed
  against an upstream bug.

  Diagnostic cycle ran across three PRs over ~90 minutes:
    - **PR #126** — instrumented `approve_record` raises with the
      offending team_ids and the validator's expected set. Shipped
      with explicit "REVERT AFTER USE" in the commit message and
      title. Instrumentation-first because the bug was data-shape
      dependent and not reproducible from the operator report
      alone; needed the actual team_id values from the failing
      row to confirm the hypothesis.
    - **PR #127** — DIAG output identified the positional-indexing
      mismatch within minutes. Fix sourced defaults from
      `reason_detail.{home,away}_team_id` (name-keyed, correct for
      all collision shapes) instead of positional `raw_cf[0]/[1]`.
      Removed the now-unused `_load_candidate_team_ids` helper.
      Added `test_approve_partial_collision_validates_away_against_correct_default`
      as a regression guard.
    - **PR #128** — revert PR #126's instrumentation.

  The positional-indexing bug was wrong from inception: PR #123
  read `candidate_fixtures` under the non-collision contract
  assumption, but the column ships *three* contracts depending on
  the writer branch, with no schema-level distinction. The
  Bradley row tested cleanly because it was non-collision shape;
  partial-collision rows are a non-trivial fraction of pending
  volume but didn't get exercised until Minnesota hit production.
  Filed as issue #121 (expanded scope: naming AND typing).

  **PR ordering incident (subset of the above).** The revert PR
  (#128) merged at 20:43 while the fix PR (#127) was still in
  review; production stayed broken between 20:43 and 20:54.
  Postmortem: revert PRs (and ANY sequencing-dependent PR pair)
  need (a) blocking language in the title, (b) a concrete
  production-deliverable verification step in the PR body (DB row,
  response payload — not just HTTP status), and (c) branch
  protection enforcement so the rule doesn't depend on humans
  being available. Filed as issue #129. This matters as a class of
  bug, not a single mistake: the same failure mode applies to
  migration+migration-dependent PR pairs, refactor PRs that split
  a function before the call-site update lands, and any case
  where merging in the wrong order leaves production in an
  inconsistent state for the gap between merges.

### Day-0 production shape

Phase 2F is a UI shipment, not a runner change, so there's no clean
"day-0 cron run" the way 2D.3 had. The closest comparable
measurement is the steady-state shape of the queue the UI now
reads. From the most recent `sp.resolver_runs` row at time of
writing:

```
sp.review_queue depth:       ~2,270 pending (within 2C.1's
                             1,500-row alert headroom because
                             2C.1's threshold applies to
                             alias-tier specifically; combined
                             2C+2D+legacy depth is the relevant
                             ceiling)
new rows / cron:             ~310 (combined alias + fuzzy
                             review_queue from the last
                             2D.3-shape resolver_runs row)
reason_detail populated:     100% of post-PR-#115 inserts;
                             ~0% of pre-2F.0 inserts (fallback
                             path active for the existing 2,263
                             rows)
provider_title populated:    same as reason_detail
operator approves / cron:    N/A — operator-driven, not
                             cron-driven; ramp will be measured
                             at day-7
```

Two production approve cycles validated against the four-table
atomic-transaction shape (write to `sp.review_queue` setting
`status='decided'` + `decision_team_id`; write to `sp.team_aliases`
with `source='operator_review'`; write to `sp.fixtures` resolving
`provider_record_id` → `fixture_id`; write to `sp.resolver_runs`
extra-counter increment):

- **Bradley vs Campbell** — alias-tier, non-collision shape.
  Approved cleanly post-PR #125. Verified all four table writes
  committed atomically; idempotent on retry; HTMX fragment swap
  rendered the decided-state panel without a full page reload.

- **Minnesota vs Cleveland** — collision-tier, partial-collision
  shape (home colliding, away non-colliding). First attempt
  500'd pre-PR #127; approved cleanly post-PR #127 + post-PR
  #128. Same four-table verification.

Sub-PR #4 (anchor_failed read-only surface) is the next planned
work item. Anchor_failed records account for ~170/cron of FL
long-tail volume and currently have no operator surface — they
sit in `sp.resolution_log` rows with `reason_code='no_match'`
AND `reason_detail->>'fail_reason'` in the anchor-failed family
(`alias_no_team_resemblance`, `fuzzy_no_team_resemblance`,
`alias_no_existing_fixture`, `fuzzy_no_existing_fixture`). They
never reach `sp.review_queue`. The 2F.1 design (Q6 revised) put
the read-only listing in 2F.1 or hard-sequenced 2F.2 depending
on scope; with 2F.1 shipping clean on review_queue,
anchor_failed is the natural next sub-PR.

> **Erratum (2026-05-12 / sub-PR #4 scoping pass):** Earlier
> drafts of this paragraph said the records "sit in
> `sp.resolver_runs.extra.anchor_failed_records` JSON." That was
> wrong — `sp.resolver_runs.extra` carries per-run counters, not
> per-record forensic data. Per-record `no_match` decisions land
> in `sp.resolution_log` (one row per tier consulted, per
> `scripts/run_resolver_pass.py:430-440`). The mistake came from
> conflating the run-level counter `extra.fuzzy_review_queue` /
> `extra.fuzzy_auto_applies` shape with per-record audit
> storage. Sub-PR #4's query targets `sp.resolution_log`.

### Tracked deviations from PHASE_2F_DESIGN.md rev1.1

- **List view ships 10 columns, not 9.** Added Status column at
  operator request during PR #119 review; lets operators see
  "decided" rows when filters are off without clicking through.
  Documented deviation, not a bug.
- **CSS extraction (PR #122) wasn't on the design doc.** Closed
  issue #120 (inline styles). Pure refactor; consistent with the
  design's spirit.
- **Commit A on PR #123** (`confidence_min` empty-string fix) —
  filter-UX bug, not a design item.
- **Commit B on PR #123** (`next_record_id` thread-through) — the
  design referenced the "Next record" link but the implementation
  was template-only; needed handler-side wiring.
- **Three production hotfixes** (#125 / #127, with #126/#128 as
  the diagnostic cycle) — see the "Production issues" subsection.

### Open tech-debt issues (`tech-debt` label, see GitHub)

- **Issue #105** — `test_unpaired_kalshi_only_fixture_appears`
  date-dependent; still deselected from CI. Fix: `freezegun` /
  `time-machine`. Carried from 2D.3.
- **Issue #109** — per-record `session.begin()` adds ~83ms/record.
  Still comfortably below cron-stagger budget; carried from 2D.3.
- **Issue #120** — closed by PR #122 (CSS extraction).
- **Issue #121** — `sp.review_queue.candidate_fixtures` misleading
  name AND implicit shape contract. Scope expanded post-#127 to
  cover both the naming and the per-side layout problems (same
  root cause). Recommended fix: rename + reshape to
  `candidate_team_ids_by_side` JSONB object with explicit
  `{home: [...], away: [...]}` shape, scheduled after sub-PR #4
  lands.
- **Issue #124** — `test_approve_double_click_is_idempotent`
  skipped due to TestClient+asyncpg event-loop conflict ("Future
  attached to a different loop" on sequential POSTs in one test).
  Idempotency still covered by
  `test_approve_concurrent_decision_returns_already_decided`.
  Migration to `httpx.AsyncClient` tracked.
- **Issue #129** — branch-protection-enforced verification gate
  for sequencing-dependent PR pairs. Generalized from the
  #127/#128 ordering incident: applies to ANY PR pair where
  merge order matters (revert+fix, migration+migration-dependent,
  refactor that splits a function before the call-site update
  lands, etc.). Tooling task: GitHub branch-protection required
  status check that parses PR body for a "blocks: #N" header and
  refuses merge if the blocker isn't both merged AND has a
  verification artifact (DB query result, response payload
  screenshot) posted as a comment.

### Engineering observation: implicit data contracts at module boundaries

Three production bugs in three days — #125, #127, and the Commit
A/B fixes on #123 — share a class. Each was a contract between two
modules that wasn't enforced by types, schemas, or tests, only by
convention documented elsewhere (or not at all):

- **#125** (session autobegin) — the contract "reads must be
  inside `session.begin()`" exists in PR #108's commit message and
  in SQLAlchemy 2.0 docs. Not enforced by SQLAlchemy at write
  time; the error surfaces at the second `begin()` call far from
  the cause. The runner had the right pattern; `approve_record`
  reinvented the wrong one because the convention wasn't in code.

- **#127** (positional indexing on partial-collision) — the
  contract "`candidate_fixtures[0]` is home, `[1]` is away" holds
  for non-collision rows but not for partial-collision. Two
  different layouts in the same column. No schema annotation; no
  test that exercised partial-collision shape pre-production. The
  bug was wrong from inception — PR #123's reviewer (Claude) and
  the human reviewer both signed off because the documented
  contract in the inline comment matched the non-collision case,
  which was the only case the reviewer's mental model held.

- **Commit A on #123** (`confidence_min` empty-string) —
  FastAPI's query-binder contract for `float | None` doesn't say
  how `""` is treated; reasonable people would expect either
  "treat as None" or "422". The actual answer is 422, surfaced
  only when the form submitted with an empty filter.

Pattern name: **implicit boundary contracts**. Mitigation worth
testing in 2F.X / 2G:

1. When module A's output feeds module B's positional/keyed read,
   require either (a) a typed dataclass at the boundary, or (b) an
   integration test that exercises every emission shape A
   produces.
2. Code review checklist item: "If this PR reads a JSONB/dict
   from the DB, what shapes does the writer emit, and does at
   least one test exercise each shape?"
3. `_load_candidate_team_ids` removal in PR #127 is the right
   instinct — when a helper exists only to paper over an implicit
   contract, the contract itself is the bug.

The 2F.1 incidents were all <1-hour fixes because the validators
caught the bad state — `_validate_candidate_team_id` rejecting the
wrong team_id is exactly the safety net you want. But the *cost*
of finding each one in production is real: PR turnaround, operator
trust, debugger time. Catching them at boundaries before they ship
is the leverage.

### Notes for the next session's first 5 minutes

- Sub-PR #4 (anchor_failed read-only surface) is the next planned
  work. Greenfield; no draft branch; references in
  `admin/__init__.py` and `admin/router.py` are forward-looking
  comments only.
- Sub-PR #4 reads from `sp.resolution_log` (see erratum above
  if reading the body of this entry). No schema change needed
  — the `ix_resolution_log_provider_record` and
  `ix_resolution_log_run` indexes cover the query shape. Plan
  is `DISTINCT ON (provider, provider_record_id)` over the
  `LIMIT 7` most recent `resolver_runs` rows, filtered to the
  four-element anchor-failed fail_reason family. PHASE_2F_DESIGN
  rev1.1's fail_reason enumeration is wrong (lists
  `anchor_score_below_floor` which doesn't exist in the code,
  and `deferred_to_2d` which is non-terminal) — sub-PR #4 bumps
  the design doc to rev1.2 with the corrected list.
- Day-7 measurement window for 2F.1 lands ~2026-05-18. Same shape
  as 2B/2C/2D.4: query `sp.resolver_runs` for the week, compare
  review-queue depth to operator throughput, decide on 2F.X
  prioritization (unreject button, rejection_count skip logic,
  anchor_failed if not already shipped).
- Issue #129 (branch-protection enforcement) is tooling, not
  feature — schedule alongside any other GitHub-Actions work.
- `PROJECT_STATE.md` is now current through 2F.1; the next entry
  should pick up at sub-PR #4 or the day-7 review, whichever
  lands first.

---

## Session — 2026-05-09

### Phase 2D.3 shipped + 2D.3.1 hotfix verified in production (✅)

The 14-day post-2D.3 parallel-run window is now LIVE with all three
tiers consulting per record (strict → alias → fuzzy → review/no_match).
Orchestrator version stamp: `tiered@2d.0`. Per-tier resolver versions:
`strict@2a.6`, `alias@2c.0`, `fuzzy@2d.0`.

### What landed (PRs merged, in order)

- **PR #102** — Phase 2D.2.6 tennis-specific prop suffix extension
  (`Total Games`, `Set Winner`, `Match Winner`, `Tiebreak`). Cleaned
  up the `anchor_failed` bucket before measuring 2D.3's behavior.
- **PR #103** — Phase 2D.2.7 corroboration-gap investigation runbook
  (`scripts/investigate_corroboration_gap.sql`). Operator runs Q1+Q2+Q3
  to attribute the 1.5% measured corroboration to one of three paths
  (tournament gap / kickoff misalignment / genuinely 1.5%).
- **Investigation outcome** — Path B (kickoff misalignment) confirmed:
  Q1 100% tournament overlap, Q2 median/max 30 (pile-up at filter
  edge), Q3 85%→100% lift at ±60min (+15pp).
- **PR #104** — Phase 2D.2.8 per-tier drift widening
  (`KICKOFF_DRIFT_SEC = 60 * 60` for fuzzy tier; strict + alias keep
  30 min). Static guard asserts fuzzy_tier drift > strict tier drift.
  Dry-run re-run measured corroboration 1.5% → 2.7% (+1.2pp).
- **PR #106** — PHASE_2D_DESIGN.md rev3 (doc-only). Locked Option C1
  as primary 2D framing (review queue is the headline, ~150/cron;
  auto_apply ~2-3/cron is bonus). Day-0 prediction final: ~10-11%
  combined Kalshi auto-apply. Deferred A.rev2 to 2D.7 follow-up.
- **PR #107** — Phase 2D.3 TieredMatcher 3-tier extension. Pure
  infrastructure wiring of the already-shipped 2D.2 matcher into the
  runner. Bumped `TIERED_RESOLVER_VERSION` to `tiered@2d.0`. Added
  `fuzzy_auto_applies` and `fuzzy_review_queue` counters in
  `sp.resolver_runs.extra`. `sp.team_aliases` write-back uses
  `source='fuzzy_tier'`. Triple-tier resolution_log per design D.4.
- **PR #108** — Phase 2D.3.1 hotfix. Two changes scoped tightly to the
  619-crash regression that surfaced after 2D.3 went live:
  1. **`ON CONFLICT (provider, provider_record_id) DO UPDATE WHERE
     status='pending'`** on the `sp.review_queue` insert. The same
     unresolved record (fixture_id IS NULL because review_queue is
     pending operator approval) comes back to the resolver on every
     cron pass; without ON CONFLICT, the second pass crashes on the
     uniqueness constraint. WHERE clause protects operator-decided
     rows from being overwritten.
  2. **Per-record `async with session.begin():`** instead of
     chunk-level. Prior chunk-level transaction caused IntegrityError
     on one record to cascade `PendingRollbackError` to every
     subsequent record in the chunk. Each record now commits or rolls
     back independently.

### Day-0 production numbers (run 545a0379-a9e9-4742-8304-ce741a9444fc)

```
records_scanned:      4,754
auto_applies (total):    24
  strict tier:           19
  alias  tier:            2
  fuzzy  tier:            3
review_queue (total): 1,011
  alias  tier:          741
  fuzzy  tier:          270
no_match:             1,623
crashes:                  0
runtime:           6m 36s
```

Phase 2D.3 fully operational. Three-tier matcher producing expected
output shape. Tonight's scheduled crons (FL 02:00 UTC / Kalshi 02:15
UTC) execute cleanly per the verified hotfix.

### Tracked follow-ups (post-2D.4)

- **2D.4 day-7 review** — same cadence as 2B and 2C. Decision point
  for 2D.5 / 2D.6 / 2D.7 prioritization. Includes re-running the §E.8
  corroboration investigation against persisted 2D fuzzy-tier data
  (replaces the team-sport proxy with tennis-specific numbers).
- **2D.5 / §E.9** — FL alias coverage expansion for the ~170
  anchor_failed records/cron long-tail. Sample 200 anchor_failed
  records, classify by failure mode (FL missing vs alias gap), expand
  `DEFAULT_FL_SPORT_IDS` and/or seed `sp.team_aliases` for top-N
  tennis players.
- **2D.6 / §E.10** — Asian-name single/two-character surname handling
  ("Hu", "Ng", "Li", "Choo"). Country-of-origin disambiguation layer
  for surnames ≤ 2 chars where ≥ 3 candidates collide. Conservative
  scope.
- **2D.7 / §E.11** — A.rev2 per-candidate initial-expansion filter in
  `_find_personal_match`. Discriminates "Junfeng Hu" from "Zhizhen Hu"
  for multi-token providers. Deferred from 2D.3 because dry-run
  showed the fuzzy auto_apply path is small leverage; current
  cross-team collision detection routes these to review_queue
  (operators handle the discrimination).

### Open tech-debt issues (`tech-debt` label, see GitHub)

- **Issue #105** — `test_unpaired_kalshi_only_fixture_appears` is
  date-dependent; deselected from CI runs as a workaround. Fix:
  freeze the test clock with `freezegun` or `time-machine`.
- **Issue #109** — Resolver runtime: per-record `session.begin()`
  adds ~83ms/record (~6m 36s for 4.7k records). Not urgent — sits
  comfortably below the 15-min cron stagger window. Investigate if
  any cron run exceeds 10 min, `records_scanned` exceeds 8k for 2+
  consecutive cycles, or 2D.5 ships and adds more records.

### Notes for the next session's first 5 minutes

- Wait for 5-10 cron cycles to land before any 2D.4 day-7 review
  work. Steady-state numbers matter, not a single-sample read.
- The `tech-debt` label needs creating in the GitHub UI; once
  created, apply to issues #105 and #109.
- 2C.1 alert threshold of 1,500 review-queue rows still has headroom
  with combined 2C+2D volume (~400-500/day). If review queue depth
  grows past 1,500 sustained for >7 days, escalate per 2C.1 mechanism.
- Operator capacity: ~67-83 min/day of review work at ~10 sec/record
  for the combined 2C+2D queue. If the actual review pace lags,
  revisit 2D.7 (A.rev2) prioritization to shave the auto-apply path.

---

## Session — 2026-05-08 (afternoon onwards)

### Phase 2A.5 — Bootstrap (✅ complete in production)

Bootstrap ran 2026-05-08T15:33:43Z, completed in 41.9s.
**24,400 teams + 30,442 aliases inserted, 0 chunks failed,
no leaked transactions.**

Per-sport baseline (the strict-tier coverage benchmark for Phase 2B):

| Sport             | Teams  | Aliases |
|-------------------|--------|---------|
| Soccer            | 17,305 | 22,040  |
| Tennis (singles)  |  3,452 |  3,843  |
| Basketball        |  1,973 |  2,419  |
| Rugby Union       |    320 |    359  |
| American Football |    309 |    501  |
| Baseball          |    272 |    397  |
| Hockey            |    252 |    310  |
| Darts             |    174 |    190  |
| Cricket           |    107 |    139  |
| MMA               |     95 |     98  |
| Boxing            |     75 |     79  |
| Aussie Rules      |     66 |     67  |
| Golf              |      0 |      0  |
| Handball          |      0 |      0  |
| Rugby League      |      0 |      0  |
| Snooker           |      0 |      0  |
| Volleyball        |      0 |      0  |

**5 sports have zero teams** (Golf, Handball, Rugby League, Snooker,
Volleyball). Resolver's alias tier (Phase 2C) or human review
queue (Phase 2F) will populate them organically when Kalshi/FL
coverage starts.

Skipped (intentional): 1,745 tennis doubles partnerships, 452
entities in unmapped sports (Esports / Motorsport / Table Tennis).

### Phase 2B — Strict matcher (this session)

Per locked design in `PHASE_2B_DESIGN.md` (PR #77).

**Migration `bdf12a30e49b`:**
- Created `sp.resolver_runs` table with `provider` + `run_mode` columns.
- Altered `sp.fixtures.competition_id` from NOT NULL to NULL —
  enables sport-only fallback when `sp.competitions` is empty.

**New modules:**
- `resolver/aliases.py` — `AliasResolver` bulk-load + in-memory
  `(alias_normalized, sport_id) → set of team_ids` index. Strict
  tier punts on ambiguous aliases (>1 team_id per key).
- `resolver/fixtures.py` — `find_fixture` (drift-windowed search) +
  `ensure_fixture` (DO-NOTHING + re-fetch per design §1).
  `ensure_fixture` returns `(fixture_id, created_new)` so insert-vs-
  conflict path is recorded in `resolution_log.reason_detail`.
- `resolver/matcher.py` — `StrictMatcher` with 4-condition gate
  (kickoff_confidence ≥ 0.85, both teams alias-resolved unambiguously,
  sport classified, kickoff drift ≤ 30min). Tries swapped
  orientation when find_fixture misses (handles direction-blind
  Kalshi abbr_block). 0.98 confidence on hit.
- `scripts/run_resolver_pass.py` — standalone runner. Bulk-loads
  aliases, walks unresolved provider records in 200-row chunks,
  each chunk one transaction. Atomic per design §1: UPDATE
  provider table's fixture_id + INSERT resolution_log in the same
  `session.begin()` block. Writes one `sp.resolver_runs` row at
  end with parallel-run metrics.
- 22 unit tests + 1 integration stub.

**Operator action sequence (parallel-run kickoff):**

```bash
git checkout main && git pull

# Apply migration (creates sp.resolver_runs, alters sp.fixtures)
DATABASE_URL=<prod-Neon> alembic upgrade head

# Smoke-run on a small slice
DATABASE_URL=<prod-Neon> python scripts/run_resolver_pass.py \
  --provider kalshi --limit 100

# Full passes
DATABASE_URL=<prod-Neon> python scripts/run_resolver_pass.py --provider kalshi
DATABASE_URL=<prod-Neon> python scripts/run_resolver_pass.py --provider fl

# Day-7 metrics query (filters out post-2E live activity):
psql "$DATABASE_URL" <<'SQL'
SELECT provider,
       SUM(records_scanned)  AS scanned,
       SUM(auto_applies)     AS auto_applies,
       ROUND(100.0 * SUM(auto_applies) / NULLIF(SUM(records_scanned), 0), 2) AS coverage_pct
FROM sp.resolver_runs
WHERE started_at > NOW() - INTERVAL '7 days'
  AND run_mode IN ('standalone', 'cron')
GROUP BY 1;
SQL
```

### Phase 2A.6 — Competition seeding + matcher gate (this session, follow-up to 2B)

Caught a defect during 2B post-merge review: the strict-tier matcher
read `signal.competition_hint` only into `reason_detail` for logging;
it never resolved or filtered by competition. Combined with
`sp.fixtures.competition_id` becoming nullable in migration
`bdf12a30e49b`, the strict tier had silently degraded into
"competition-blind" rather than implementing the design's intended
sport-only fallback. **Smoke-run was paused before the first pass.**

Path B chosen (defer FL competitions to 2C, ship Kalshi competition
gate now):

**New modules:**
- `scripts/bootstrap_sp_competitions.py` — Kalshi-only seed.
  Bulk-loads existing `sp.competitions.kalshi_series_bases` into a
  Python set, fetches DISTINCT `(_sport, series_ticker)` from
  `sp.kalshi_markets`, applies `kalshi_identity.strip_known_suffix`
  to derive `series_base`, queues new rows with
  `kalshi_series_bases=[base]` and inserts in 1000-row chunks. Same
  idempotency pattern as `bootstrap_sp_teams`.
- `resolver/competitions.py` — `CompetitionResolver` with bulk-load +
  in-memory `(provider, hint) → (competition_id, kind)` lookup. Kinds:
  `'explicit'`, `'no_hint'`, `'unresolvable'`. For Kalshi tries hint
  as-is then `strip_known_suffix(hint)` so callers can pass either
  the full series_ticker or a stripped base.

**Matcher changes (`resolver/matcher.py`):**
- `RESOLVER_VERSION` bumped from `strict@2b.0` → `strict@2a.6`.
- Constructor accepts optional `competitions: CompetitionResolver`.
- New `_competition_gate` enforces per-provider policy:
  * Kalshi `'explicit'` → use competition_id, filter `find_fixture`,
    write on `ensure_fixture`.
  * Kalshi `'no_hint'` → sport-only fallback, log
    `kalshi_no_hint_sport_only: true`.
  * Kalshi `'unresolvable'` → strict tier FAILS (`fail_reason=
    'kalshi_competition_unresolvable'`).
  * FL → transitional sport-only, every successful match logs
    `fl_transitional_sport_only: true`.
- `find_fixture` accepts optional `competition_id` filter; matches
  on `(competition_id = filter OR competition_id IS NULL)` to avoid
  forking one logical fixture into two when FL (no comp) created it
  before Kalshi (with comp) arrives.

**Other:**
- `resolver/kalshi.py`: `competition_hint` now uses `series_ticker`
  (the canonical Kalshi-side identifier). `_soccer_comp` ("Champions
  League") is preserved on `raw_signals['soccer_comp']` for
  diagnostics. Mirrors the bootstrap's seed key.
- `resolver/__init__.py` exports `CompetitionResolver`.
- `scripts/run_resolver_pass.py` loads a CompetitionResolver after
  AliasResolver and passes it to `StrictMatcher`.
- 13 new unit tests (CompetitionResolver coverage + matcher gate
  per-provider behavior + degrade-without-index).
- DEPLOYMENT.md adds a Phase 2A.6 runbook before the 2B section.

**Operator action sequence updated:**

```bash
git checkout main && git pull
# Migration was already applied for 2B (bdf12a30e49b at head).

# 2A.6 step — seed competitions before the first parallel-run pass.
DATABASE_URL=<prod-Neon> python scripts/bootstrap_sp_competitions.py --dry-run
DATABASE_URL=<prod-Neon> python scripts/bootstrap_sp_competitions.py

# Then proceed with 2B parallel-run as documented above.
DATABASE_URL=<prod-Neon> python scripts/run_resolver_pass.py \
  --provider kalshi --limit 100  # smoke

# Audit gate decisions during the run:
psql "$DATABASE_URL" -c "
  SELECT reason_detail->>'competition_resolution' AS resolution, COUNT(*)
  FROM sp.resolution_log
  WHERE provider='kalshi' AND decided_at > NOW() - INTERVAL '24 hours'
  GROUP BY 1 ORDER BY 2 DESC"
```

**FL transitional path is queryable** via
`reason_detail ? 'fl_transitional_sport_only'`. Phase 2C work will
seed `fl_tournament_stage_ids` and re-run the matcher against rows
carrying that flag to backfill explicit competition_ids.

**Two audit refinements added pre-PR (per review feedback):**

1. **Kalshi linked-to-NULL backfill flag.** When `find_fixture`'s
   equal-or-NULL filter links a Kalshi explicit-comp signal to a
   NULL-comp fixture, `reason_detail` now records
   `linked_to_null_comp_fixture: true` AND
   `null_comp_fixture_pending_backfill: <fixture-uuid>`. Phase 2C's
   backfill query is now a one-liner against `resolution_log`
   instead of a manual SQL audit.
2. **FL transitional sub-paths.** Every successful FL match also
   stamps `fl_transitional_path` with one of three values:
   `matched_null_comp_fixture` (typical), `matched_existing_comp_fixture`
   (Kalshi created it earlier with explicit comp — Phase 2C must
   verify FL's resolved comp aligns), or `created_null_comp_fixture`
   (FL was first; new row, awaits 2C to set the column). Required
   `find_fixture` return shape change: now returns
   `(fixture_id, fixture_competition_id)` so the matcher can audit
   the equal-or-NULL filter outcome.

### Phase 2A.7 — sp.fl_events.sport_id (this session, follow-up to 2B/2A.6)

First production FL pass produced **0/19,753 auto-applies**. Diagnosed:
`sp.fl_events` had no sport context preserved (no column, and
`raw_payload.SPORT_ID` was always null), so the runner couldn't pass
`sport=...` into `FLResolverModule.extract_signal`. Every FL signal
hit the matcher's gate 2 (`sport_not_classified`).

Earlier (PR #85) review claimed FL was "sport-shaped by construction"
because `ingestion/fl.py` polls per-sport — that conflated "polled
per-sport" with "preserved sport in storage." The poller had the
sport_id in scope at write time but never wrote it.

**Migration `7c3f9b1a2e58`:**
- Adds `sp.fl_events.sport_id INTEGER REFERENCES sp.sports(id)` (nullable).
- Partial index `ix_fl_events_sport_unresolved` on
  `(sport_id, last_seen_at DESC) WHERE fixture_id IS NULL` —
  supports the runner's hot query without bloating the full table.

**Ingestion (`ingestion/fl.py`):**
- New `FL_SPORT_ID_TO_SP_NAME` map (single source of truth, 17
  entries matching `DEFAULT_FL_SPORT_IDS`). Decoupled from
  `main.py._KALSHI_SPORT_BY_FL_ID` which is older + has several
  conflicting IDs.
- One bulk SELECT at pass start resolves `FL sport_id → sp.sports.id`.
- Per-sport batch now passes `{"sport_id": sp_sport_id}` in fields,
  so UPSERT populates the column on insert AND backfills it on
  conflict.
- Unmapped FL sport_ids are skipped with `ingestion.fl.sport_id_unmapped`
  warning rather than NULL-out the column on existing rows during
  conflict resolution.

**Runner (`scripts/run_resolver_pass.py`):**
- FL SQL now `INNER JOIN sp.sports` and filters `sport_id IS NOT NULL`.
- `extract_signal` is called with `sport=row.sport_name` for FL.
- Kalshi path unchanged — `_sport` lives on `raw_payload` already.

**Backfill:**
- `scripts/backfill_sp_fl_events_sport_id.py` wraps the existing
  `backfill_fl.py` and reports pre/post NULL counts + per-sport
  coverage. Re-fetching the FL ±7 day window backfills sport_id on
  every currently-fetchable row.
- Rows outside the ±7 day window stay NULL until a future
  per-tournament historical fetch lands. Documented; not in scope.

**Tests (+7 new):**
- `TestFLSportIdMap` (2): every DEFAULT_FL_SPORT_ID is mapped; every
  mapped name aligns with the canonical sp.sports seed.
- `TestIngestionWritesSportId` (3): static guards that batch fields
  include `sport_id`, pre-pass bulk-loads sp.sports, and unmapped
  ids are skipped with warning.
- `test_fl_query_joins_sports_and_filters_sport_id` (1): static
  guard on the runner SQL JOIN + filter + extract_signal call shape.
- 1 existing FL-test class kept (TestFLEventValidator etc.).

**Operator action sequence (2A.7 → re-run smoke):**

```bash
git checkout main && git pull
DATABASE_URL=<prod-Neon> alembic upgrade head            # apply 7c3f9b1a2e58

# Backfill existing rows (re-fetch FL ±7 days; populates sport_id).
DATABASE_URL=<prod-Neon> python scripts/backfill_sp_fl_events_sport_id.py

# Re-run smoke. Expected: real auto-applies + meaningful no_match
# fail_reason distribution. Compare against the 0/19,753 baseline.
DATABASE_URL=<prod-Neon> python scripts/run_resolver_pass.py \
    --provider fl --limit 100
```

### Production incident — Phase 2A.7 NameError in `_ingest_pass`

**Root cause:** The 2A.7 PR (#86) built `sp_sport_id_by_fl_id` inside
`run()` and referenced it from `_ingest_pass` as a free variable.
Python doesn't propagate locals across function boundaries, so every
call from `_today_pre_game_loop` / `_week_loop` raised NameError.
Bonus bug: `run()` itself called `session.execute(...)` before the
`async with session_factory() as lock_session:` block opened —
NameError on `session` would have crashed `run()` first.

**Production effect:** From PR #86 deploy until the hotfix lands,
**every FL ingestion poll silently failed.** Production
`sp.fl_events.sport_id` stayed 100% NULL across all 19,759 rows
despite `last_seen_at` updates (a separate code path keeps writing
that). The supervisor caught the NameError and restarted `run()` in a
tight loop — Railway logs should show one warning per supervisor
restart cycle.

**Why static guards missed it:** The 2A.7 PR's tests asserted that
specific strings appeared in the source ("`sport_id`" in batch fields,
"`SELECT id, name FROM sp.sports`" in the file). Those substrings did
appear — just in the wrong function. A real call-path test would have
NameError'd within seconds.

**Fix shipped (this hotfix):**
- Move the `sp_sport_id_by_fl_id` build into `_ingest_pass` so it has
  the function's own session in scope. Cost: one extra 17-row SELECT
  per pass (sp.sports). Self-correcting if sp.sports gains rows
  mid-process. Drops the broken pre-pass code from `run()`.
- 4 new integration tests (`TestIngestPassIntegration`):
  * `test_ingest_pass_runs_without_name_error` — the smoking gun.
    Mocks `flashlive_feed._fl_get` + `upsert_provider_records_batch`,
    calls `_ingest_pass` end-to-end, asserts it completes.
  * `test_ingest_pass_writes_sport_id_in_batch` — asserts each
    record's `fields["sport_id"]` is the canonical sp.sports.id, not
    None or the FL numeric id.
  * `test_ingest_pass_skips_unmapped_fl_sport_id` — asserts unmapped
    FL ids are filtered out of the iteration (don't get polled, don't
    NULL-out existing rows).
  * `test_run_does_not_name_error_at_startup` — exercises `run()`
    against a mocked session_factory + advisory_lock returning False.
    Pre-hotfix, this would NameError at the `session.execute` line.
- Plus a static guard `test_lookup_is_built_inside_ingest_pass_not_run`
  that asserts the construction lives inside `_ingest_pass`'s body,
  not `run()`'s. Defends against re-introduction.

**Deploy sequence:**
```bash
git checkout main && git pull           # after hotfix merge
# Railway redeploys automatically.
# Watch Railway logs: ingestion.fl.pass_complete (no errors expected).

# Verify production writes start landing:
psql "$DATABASE_URL" -c "
  SELECT COUNT(*) FILTER (WHERE sport_id IS NOT NULL) AS with_sport,
         COUNT(*) FILTER (WHERE sport_id IS NULL)     AS without_sport
  FROM sp.fl_events
  WHERE last_seen_at > NOW() - INTERVAL '15 minutes';"
# Expected after one poll cycle (~60s for today loop, ~10min for
# week loop): with_sport > 0 and rising.

# Then backfill the existing rows:
DATABASE_URL=<prod-Neon> python scripts/backfill_sp_fl_events_sport_id.py
# Then re-run the FL smoke (--limit 100) to validate matcher behavior.
```

### Production incident — transaction leak in db.py

**Reported:** four connections leaked over ~35 minutes (pids 645, 647,
649, 32493). `idle in transaction` state. Manually killed by operator;
the same pattern reappeared within 5 minutes.

**Root cause:** two anti-patterns in `db.py`:
1. `db.upsert_entities` and `db.sync_events_to_db` wrap a slow loop
   over thousands of records in a single `async with session.begin()`
   block. With Neon's ~75ms round-trip, transactions stay open for
   5-30+ minutes. Cancellation or asyncpg state issues mid-loop leave
   the connection idle-in-transaction.
2. `db.sync_events_to_db` creates its own engine per call but only
   disposes it on the success path (the except branch's dispose call
   itself can fail silently). Cancelled calls leak 2 pool connections
   per occurrence until process exit.

**Fix shipped (this session):**
- `idle_in_transaction_session_timeout=60000` and `statement_timeout=60000`
  added to engine `connect_args.server_settings` — Postgres self-kills
  stuck transactions / queries even if app code is buggy.
- `lock_timeout=30000` added to defend against deadlocks.
- `application_name='stochverse-web'` added so leaks surface clearly
  in `pg_stat_activity` triage queries.
- `db.upsert_entities` refactored: chunk teams into batches of 100,
  one transaction per chunk. Per-chunk failures isolated; subsequent
  chunks proceed.
- `db.sync_events_to_db` refactored: chunk records into batches of 50,
  one transaction per chunk. `try/finally` around `_engine.dispose()`
  so cancellation paths can't leak the engine.
- 10 unit tests verify chunking math + failure isolation + static-
  inspection guards against regressing to the mega-transaction pattern.

**Bootstrap (Phase 2A.5) was paused while this incident was active.**
Resume after the fix is deployed and the leak pattern stops
reproducing.

### What landed

- **Transaction leak fix in `db.py`** (this commit). 10 new tests; all
  60 existing tests still pass.

- **Phase 2A.5 — Bootstrap.** New `scripts/bootstrap_sp_teams.py` +
  Alembic migration `d8e717ed79dd` (seeds `sp.sports` with 17-sport
  finite list, each with per-sport drift threshold from §5.4). One-
  time legacy → SP migration: pulls `public.entities` (team-typed)
  into `sp.teams`, `public.entity_aliases` into `sp.team_aliases`
  with `source='legacy_bootstrap'`, `confidence=0.95`. Idempotent
  via `(alias_normalized, source)` unique constraint. Has `--dry-run`
  flag for safe operator preview. Makefile target `bootstrap-sp-teams`,
  DEPLOYMENT.md runbook.
  - **Operator action required after merge:** apply migration,
    `--dry-run` first, then run for real, **document per-sport
    coverage counts in this file** (replaces "open question:
    Phase 2B baseline").

- **Phase 2B design doc** locked at `PHASE_2B_DESIGN.md` (PR #77).
  Three pushbacks addressed: ensure_fixture pinned to DO-NOTHING +
  re-fetch; parallel-run criteria split per-provider (Kalshi auto
  diff, FL operator spot-check); bootstrap as separate 2A.5 PR.
  `sp.resolver_runs` schema includes `provider` + `run_mode` for
  filtering parallel-run vs live-runner data.

- **Phase 2A — Resolver scaffolding.** New `resolver/` package with the
  contract types and per-provider extraction logic. No DB writes,
  no matching, no resolution_log — pure foundation for 2B+.
  - `resolver/types.py` — `FixtureSignal`, `TeamCandidate`,
    `MatchResult`, `ReasonCode` (Pydantic v2 + Enum).
  - `resolver/protocol.py` — `ResolverModule` runtime-checkable
    Protocol.
  - `resolver/_normalize.py` — strict NFD-only name normalization
    per architecture §9.2.
  - `resolver/fl.py` — `FLResolverModule.extract_signal` reads FL's
    raw_payload + tournament context, produces FixtureSignal with
    fl_team_id / name / shortname candidates.
  - `resolver/kalshi.py` — `KalshiResolverModule.extract_signal`
    reads Kalshi cache record, runs `parse_ticker`, produces
    FixtureSignal with title-parsed name candidates + abbr_block
    direction-blind candidate.
  - `tests/test_resolver_2a.py` — 31 unit tests covering
    normalization, type validation, Protocol conformance, FL
    extraction edge cases, Kalshi extraction edge cases (per_fixture
    vs outright, kickoff fallbacks, sport_override, title parsing
    `vs` / `at` / `@` separators).

### Phase 2 sub-roadmap (decomposition)

| Sub-phase | Scope | Status |
|---|---|---|
| **2A** | Resolver scaffolding: types, Protocol, extract_signal stubs | ✅ this session |
| 2B | Strict tier — match against sp.fixtures via exact alias on both teams + kickoff ±30min + competition. Write to sp.resolution_log + sp.review_queue. UPSERT sp.fixtures. | next |
| 2C | Confidence scoring + alias tier (per-sport drift threshold) | after 2B |
| 2D | Time-anchored fuzzy + cross-provider corroboration | after 2C |
| 2E | Three-loop resolver runner (hot via LISTEN/NOTIFY + 30s batch + 5–10min re-resolution) | after 2D |
| 2F | Admin review-queue UI minimum: auth + list + approve/reject + audit | separate PR |
| 2G | Diff tooling — compare new resolver decisions to legacy `kalshi_join` pairings via `/api/_debug/resolver_diff` | after 2E |

### Deferred design questions — resolve in Phase 2C+ scoping

- **Individual sports don't fit the team model.** Tennis singles, golf,
  MMA, boxing entities currently land in `sp.teams` as "team-of-one"
  rows. Works mechanically (resolver matches against alias rows
  regardless), but is awkward — a player isn't a team. Surfaced
  during the 2A.5 cross-sport audit. May warrant a separate
  `sp.players` table with similar alias machinery in Phase 2C+
  scoping. Don't attempt during Phase 2B — strict-tier matching
  works as-is on the team-of-one shape.

### Open questions still — decide before 2B

- **Hot-loop trigger.** Implement `LISTEN/NOTIFY` immediately in 2E,
  or start with a 1s polling loop and add NOTIFY in a follow-up?
  Recommendation: polling first (simpler, easier to debug);
  `LISTEN/NOTIFY` in a 2E.fix PR once polling-based correctness is proven.
- **Confidence threshold for auto-apply vs review-queue routing.**
  Architecture default 0.85 — verify against real production data
  during 2B's parallel-run period.
- **Admin UI tech.** Architecture §7.5 says "auth, list view, detail
  view, approve/reject, audit log." Phase 2F decision: render via
  vanilla JS in `static/admin/` (consistent with current frontend
  stack) or use FastAPI + Jinja2 server templates. Lean toward Jinja2
  — server-rendered admin pages are easier to keep secure and
  versionable than a SPA.

---

## Session — 2026-05-07/08

### What landed (PRs merged, in order)

- **Phase 0** — `WEB_CONCURRENCY≥2` documented + structlog JSON output +
  FL request-path 30s in-memory cache + `provider_api_call` event
  emitter (PR #66, sha `df59f56`). Operator set `WEB_CONCURRENCY=2` on
  Railway; two worker processes confirmed on next boot.

- **Phase 1A** — SP schema (`sp.*` Postgres schema, 12 tables), Alembic
  init with async template + Neon URL handling, `docker-compose.yml`,
  Makefile dev targets, `.env.example`, `.gitignore` updates
  (PRs #67, #68). Initial migration `8f404e0dc89a` written by hand.

- **Phase 1A migration commit fix** — debugged silent failure where
  `alembic upgrade head` reported success but no DDL persisted. Root
  cause: async SQLAlchemy + Alembic gotcha — `connect()` begin-once
  mode doesn't reliably propagate the COMMIT across the sync/async
  boundary inside `run_sync`. Fix: `transaction_per_migration=True`
  in `context.configure()` + explicit `await connection.commit()`
  after `run_sync`. (PR #69, sha `47aa9ee`)

- **Phase 1A migration applied to production** — Neon DB has the
  `sp` schema with 12 tables and `sp.alembic_version` at revision
  `8f404e0dc89a`. Verified by operator after the env.py fix.

- **Neon password rotation** — operator-driven. Old `neondb_owner`
  password (which had been pasted in chat earlier) was rotated in
  the Neon dashboard. Railway `DATABASE_URL` env var updated to the
  new pooled URL (`-pooler` hostname). Verified by `/healthz` after
  redeploy.

- **Phase 1B — FL ingestion** (PR #70, sha `a8dd0e7`). New
  `ingestion/` package with `base.py` (Protocols, supervisor,
  advisory locks, payload-hash UPSERT), `schema_validation.py`
  (Pydantic boundary validators), `fl.py` (today + week cadence
  loops), `runner.py` (entry point under supervision). 17 unit tests.
  Production confirmed: `ingestion.fl.pass_complete` events flowing,
  ~19k rows in `sp.fl_events` after backfill.

- **Phase 1C — Kalshi REST ingestion** (PR #71, sha `22bbb72`).
  `ingestion/kalshi.py` reads from legacy `_cache["data_all"]`,
  parses each ticker via `kalshi_identity.parse_ticker`, UPSERTs
  to `sp.kalshi_markets`. Same coupling pattern as Phase 1B uses
  for FL. 9 new unit tests.

- **Phase 1 fixes — batch UPSERT, accurate counters, kalshi
  cold-cache priming** (PR #72, sha `2ac0ab8`). Three issues
  surfaced by the first day of production:
  - Cold-cache priming fires `get_data()` in an executor when the
    legacy cache is empty (90s timeout).
  - Counter accuracy: SELECT-first classification before UPSERT;
    counters are correct by construction.
  - FL pass duration 150–275s → 18–30s via multi-row
    `INSERT ... ON CONFLICT DO UPDATE`. 5–15× faster.

- **Phase 1D — investigated, de-scoped.** A Kalshi WS consumer
  module was shipped (PR #73, sha `89868af`) but found to be a
  silent no-op: `kalshi_ws.LIVE_PRICES` is keyed by **market_ticker**
  (sub-market level) while `sp.kalshi_markets.ticker` PK is
  **event_ticker**. The `WHERE km.ticker = v.ticker` clause never
  matched. Investigation also surfaced that prices already live in
  the legacy `public.prices` table at sub-market granularity,
  populated by `kalshi_ws._price_flush_loop` for both REST and WS
  sources. The right architectural answer: `sp.*` is the entity
  layer, `public.prices` is the price-history layer. Phase 1D was
  removed from scope. The dead code is deleted in this session's
  doc-update PR.

- **Phase 1E — FL backfill script** (PR #74). `scripts/backfill_fl.py`
  pumps indent_days from -7 to +7 through the same ingestion pipeline.
  Operator ran against production Neon and confirmed `sp.fl_events`
  grew to ~19k rows. **`scripts/backfill_kalshi.py` removed** in a
  follow-up commit (`443933e`) — Kalshi REST exposes only currently-
  active and recently-closed events, which the live ingestion already
  pulls every 30s. No useful backfill range. A real Kalshi historical
  backfill would require per-ticker queries against `/markets/{ticker}`
  with a starting list of historical tickers — Phase 2+ if/when needed.

- **Architecture doc v1.4** — boundary statement that
  `public.prices` is a load-bearing dependency of Phase 3 serving;
  system-of-record table covering 14 data classes (canonical
  fixtures, teams + aliases, provider records, sub-market identity
  with the documented redundancy between `sp.kalshi_markets.raw_payload->'outcomes'`
  and `public.markets`, current price snapshots, per-tick price
  history, resolver audit trail, review queue, provider telemetry,
  live scores, legacy entities). v1.3 was discussed but never
  produced as a file artifact; v1.4 supersedes both v1.2 and the
  conceptual v1.3.

### What was investigated and rejected (no code shipped beyond the
deletion of Phase 1D)

- A "field-aware merge" or "separate WS-only columns" approach
  to making `sp.kalshi_markets` price-aware. Rejected because
  `sp.*` was never intended to store prices and `public.prices`
  already does so at sub-market granularity. The doc was updated
  to make this boundary explicit rather than rebuild infrastructure
  that already works.

### Production verification at end of session

- `sp.fl_events`: ~19k rows after FL backfill; passes complete in
  18–30s (down from 150–275s).
- `sp.kalshi_markets`: ~7,325 rows; pass durations 12–17s; counter
  fields (`inserted` / `updated` / `unchanged`) are correct.
- Phase 1D's WS module is deleted; `ingestion.runner.started` will
  emit `task_count=2` going forward.
- `sp.alembic_version` at `8f404e0dc89a`; legacy `public.*` tables
  untouched.

### Open questions for next session

- **Phase 2 resolver scoping.** Architecture doc §7 has the design
  (three-loop pattern, central matcher with confidence scoring,
  `resolution_log`, `review_queue`). Concrete decisions still
  open:
  - Resolver version 0 — what minimum strict-tier match coverage
    is acceptable to start? (Today's pairing is in `kalshi_join.py`;
    porting it as the resolver's strict tier is the obvious starting
    point.)
  - Hot-loop trigger: implement `LISTEN/NOTIFY` immediately, or
    start with a 1s polling loop and add NOTIFY in a follow-up?
  - Admin review-queue UI: what's the minimum viable shape? The
    spec in §7.5 lists auth + list view + side-by-side detail +
    approve/reject + audit log. Phase 2 sub-phases probably want
    auth + list + approve before detail/audit.
  - Confidence threshold for auto-apply vs review-queue routing:
    default 0.85 per §7.4 — verify against real production data
    before locking in.

- **Phase 1F — archival.** Architecture §6.5 says raw provider
  payloads >30 days old archive to object storage. Decision deferred
  this session. The legacy `public.prices` is already pruned via
  `PRICE_RETENTION_HOURS` (default 6), which is acceptable
  steady-state. The `sp.*` provider tables (`fl_events`,
  `kalshi_markets`) don't yet have retention; doing nothing means
  they grow without bound. Phase 1F decides.

- **Phase 4 prep — Polymarket / OddsAPI integration.** Not started.
  The architecture's claim that adding a provider is "a new
  ingestion poller + a new resolver module + configuration entries"
  gets tested for real here.

### Notes for the next session's first 5 minutes

- Run `git pull origin main` to get this session's commits.
- Verify `make test` is green (39 ingestion tests pass).
- Verify production: `sp.kalshi_markets` row count steady, FL passes
  still in the 18–30s range, no `kalshi_ws` mentions in recent logs.
- Re-read this PROJECT_STATE.md before starting Phase 2 design work.
