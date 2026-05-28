# Phase 2D.5-A: Data-Driven League Bootstrap

Bootstrap unrepresented leagues into `sp.teams` + `sp.team_aliases` using
resolver failure signal as the discovery source. Each league's unresolved
population is identified via `asymmetric_anchor_failure` routing in
`sp.resolution_log`, then bootstrapped following the KBL methodology
(`docs/bootstraps/kbl-2025-26.md`).

---

## 1. Context and motivation

Day-27 (2026-05-27) diagnostic chain discovered that ~13,229 `review_queue`
records per week route via `asymmetric_anchor_failure` — the matcher resolves
one side of a fixture but finds zero candidates for the other. The unresolved
side's provider string has no matching entry in `sp.teams` under the correct
`sport_id`.

Root cause: `sp.teams` was populated only by explicit bootstrap scripts
(KBL, national teams) and operator-approved review_queue entries. Any team
that wasn't in a bootstrap manifest AND hasn't been operator-approved is
missing — regardless of how professional or active the team is.

The unresolved strings cluster by league:

| League | Sport | Records/7d | Teams to seed |
|---|---|---:|---:|
| LMB (Mexican Baseball League) | Baseball | ~600 | 20 |
| Liga ACB (Spanish Basketball) | Basketball | ~400 | 18 |
| EuroLeague (Continental Basketball) | Basketball | ~250 | 18 |
| European Baseball (IT/DE/CZ/FR) | Baseball | ~250 | ~15 |
| Polish PLK + Czech NBL + Israeli BSL | Basketball | ~200 | ~20 |
| Tennis surnames | Tennis | ~200 | alias-only |

Total: ~105 canonical rows + aliases across 5-6 bootstrap scripts.
Expected resolution: 1,700-2,500 records/week.

### Why "data-driven bootstrap" instead of league-driven

The KBL pilot bootstrapped by league selection (operator chose KBL as the
methodology pilot). Phase 2D.5-A inverts the discovery: the resolver's
failure signal identifies which leagues are missing, and we bootstrap those
leagues in priority order. Same methodology, different discovery source.

Pattern A.2 discipline: verify the assumed shape exists before committing
scope. Day-27's diagnostic chain (asymmetric_anchor_failure → unresolved
string clustering → league identification → canonical-vs-alias
determination → opponent verification) is Pattern A.2 applied at the
bootstrap-target-selection granularity.

## 2. Scope boundaries

### In scope

- **Canonical creation** in `sp.teams` for teams missing under the correct `sport_id`. Each team gets `canonical_name`, `normalized_name`, `sport_id`, `country_code`.
- **Alias seeding** in `sp.team_aliases` for provider-variant forms (short names, city-only forms, accented/unaccented variants). Source value: `bootstrap_league_coverage` (same as KBL per Q3 decision).
- **One bootstrap script per league** mirroring `scripts/bootstrap_kbl.py` structure: hardcoded manifest, three-branch classifier (INSERT / BACKFILL / SKIP), alias classifier, idempotent via ON CONFLICT.
- **Verification queries** post-apply: check `sp.resolution_log` for reduced asymmetric_anchor_failure rate on the bootstrapped sport.
- **Track A baseline_shifts annotation** per league apply — one `sp.baseline_shifts` row per league bootstrap.

### Out of scope

- **Tennis surname aliases** (Pattern B from Day-27 discovery). Alias-only workstream, different from canonical creation. Separate sub-PR after the league bootstraps.
- **Review_queue re-processing** of existing pending records. The daily cron's re-resolution loop (§7.7) handles this automatically — bootstrapped aliases are picked up on the next cron pass.
- **Admin UI (Phase 2F.1)** for operator review_queue drainage. Separate workstream; this bootstrap reduces inflow but doesn't drain the existing 10,506 pending stock.
- **Cross-sport collision handling for ACB/EuroLeague**. Real Madrid Basketball + Real Madrid Soccer share a name; the `sport_id` partition handles matcher-level disambiguation (confirmed Day-22). The bootstrap manifest uses the sport-specific canonical ("Real Madrid Baloncesto" or equivalent) and seeds aliases including the bare "Real Madrid" form — safe under `sport_id` partition per `resolver/aliases.py:51,111`.

## 3. Framing-question decisions

### F1 — Canonical_name policy

**Decision:** Use the team's official full name as canonical. Short forms and provider-variant forms go into aliases. Mirrors KBL F1 precedent. Example: canonical = "Sultanes de Monterrey", aliases = ["Monterrey", "Sultanes"].

### F2 — Alias distinctiveness

**Decision:** Bare city-name aliases (e.g., "Monterrey") are safe within their `sport_id` because the `AliasIndex` and `CandidateIndex` both partition by `sport_id` (`resolver/aliases.py:51,111`, `resolver/alias_tier/candidates.py:106`). No cross-sport collision risk. Within-sport collision risk is low for single-city leagues (LMB has one "Monterrey" team, Liga ACB has one "Real Madrid" team).

Exception: if two teams in the same sport + league share a city (rare), bare city-name aliases are excluded for those teams. The bootstrap manifest documents any such exclusions.

### F3 — Non-Latin script coverage

**Decision:** Spanish diacritics (Querétaro, León) included as both accented and ASCII-stripped aliases. The normalizer (`resolver/alias_tier/normalize.py:104-108`) strips accents via NFD decomposition, so both forms resolve to the same normalized key. Including both is belt-and-suspenders.

### F4 — Source value

**Decision:** `bootstrap_league_coverage` (same as KBL, per Q3 convention). Single value across all Phase 2D.5-A bootstraps.

### F5 — Country_code

**Decision:** Per-team country_code. LMB: all "MEX". Liga ACB: all "ESP". EuroLeague: per-team (ESP, GRC, TUR, ISR, DEU, etc.). Mirrors KBL (uniform "KOR") for single-country leagues; extends to multi-country for continental competitions.

### F6 — Bootstrap script structure

**Decision:** One `scripts/bootstrap_<league>.py` per league + one `scripts/<league>_seed.py` manifest. Same triplet structure as KBL (seed + bootstrap + tests). Makefile target optional (deferred — LMB is small enough to invoke directly).

### F7 — Verification

**Decision:** Post-apply verification query per league. Counts only NEW resolution_log entries written after the apply timestamp — same `provider_record_id` may have older review_queue entries from pre-apply cron passes (re-resolution retry traffic per Finding X), which must be excluded.

```sql
-- Did the bootstrapped teams resolve previously-failing records?
-- Only count entries decided AFTER apply to avoid double-counting
-- old review_queue entries + new strict resolutions for the same record.
SELECT
  reason_code,
  count(*) AS records
FROM sp.resolution_log
WHERE reason_detail->>'sport' = '<sport>'
  AND decided_at >= :apply_timestamp
  AND decided_at < :apply_timestamp + INTERVAL '7 days'
  AND provider_record_id IN (
    SELECT DISTINCT provider_record_id
    FROM sp.resolution_log
    WHERE reason_code = 'review_queue'
      AND reason_detail->>'routing_shape' = 'asymmetric_anchor_failure'
      AND decided_at >= :apply_timestamp - INTERVAL '7 days'
      AND decided_at < :apply_timestamp
  )
GROUP BY reason_code;
```

Expected: `strict` count rises (newly aliased teams resolve via strict tier); `review_queue` + `no_match` for the bootstrapped population drops.

### F8 — Success criterion

**Decision:** Per-league success = asymmetric_anchor_failure inflow rate for the bootstrapped sport drops by ≥50% measured over a 7-day post-apply window (not 48 hours — league game schedules are non-continuous; LMB games may not occur every day, so a 48-hour window may miss the effect). The 50% threshold accounts for teams not in the manifest (smaller leagues, amateur tiers) that continue to fail.

## 4. Implementation plan — LMB first

### 4.1 LMB team manifest (`scripts/lmb_seed.py`)

20 teams across two zones of 10 (Norte + Sur). Format mirrors `kbl_seed.py`:

```python
LMB_TEAMS_SEED = [
    ("Sultanes de Monterrey", "MEX", ("Monterrey", "Sultanes", "Sultanes de Monterrey"), "LMB Norte"),
    # ... 15 more
]
LMB_ALIAS_SOURCE = "bootstrap_league_coverage"
```

### 4.2 Bootstrap script (`scripts/bootstrap_lmb.py`)

Mirrors `bootstrap_kbl.py`:
1. Load `sp.sports WHERE code = 'baseball'` → get `sport_id`
2. Three-branch classifier: INSERT new / BACKFILL country_code / SKIP
3. Alias classifier: INSERT new alias / SKIP existing
4. `--dry-run` mode
5. Idempotent via ON CONFLICT

### 4.3 Tests (`tests/test_bootstrap_lmb.py`)

Mirrors `tests/test_bootstrap_kbl.py`:
- Manifest shape tests (canonical format, alias distinctiveness, country_code)
- Source value constant matches convention
- No empty aliases, no bare single-character aliases

### 4.4 Apply sequence

1. PR with seed + script + tests
2. Merge + `git pull`
3. Pattern D pre-flight env verification:
   ```
   $env:DATABASE_URL = '<production-Neon-URL>'
   $env:EXPECTED_PRODUCTION_DB_NAME = 'neondb'
   $env:EXPECTED_PRODUCTION_DB_HOST = 'ep-fragrant-frog-ak3esp11'
   ```
   Verify all three match production before proceeding.
4. `python scripts/bootstrap_lmb.py --dry-run` — review output (INSERT/BACKFILL/SKIP counts)
5. `python scripts/bootstrap_lmb.py` — wet apply. Script's `pattern_d.ok` log line confirms production endpoint.
6. Verification query (F7)
7. `sp.baseline_shifts` annotation
8. Day-N+1 daily-diff measurement (7-day window per F8)

## 5. Sequencing

> **Day-28 update:** See [`phase-2d5a-sequencing-decision.md`](phase-2d5a-sequencing-decision.md) for the sequencing reconsideration that supersedes this section's workstream #3 default. Day-28 production-data discovery showed Italian LBA is a cleaner methodology iteration than EuroLeague for league #3 (worked example of v1.5 amendment #15).

| Priority | League | Est. effort | Est. lift |
|---|---|---|---|
| 1 | LMB (Mexican Baseball) | 2 hours | ~600 records/week |
| 2 | Liga ACB (Spanish Basketball) | 2 hours | ~400 records/week |
| 3 | EuroLeague (Continental Basketball) | 2 hours | ~250 records/week |
| 4 | European Baseball (IT/DE/CZ/FR) | 3 hours (multi-country) | ~250 records/week |
| 5 | PLK + Czech NBL + Israeli BSL | 3 hours (multi-league) | ~200 records/week |
| 6 | Tennis surname aliases | 2 hours (alias-only, no canonical creation) | ~200 records/week |

Total: ~14 hours across 6 deliverables over 5-6 calendar days.

One league per PR. Tennis dedup Day-26 taught that smoke-test discipline catches bugs that code review misses (3 bugs caught in-flight). Bundling multiple leagues bundles multiple bootstraps' worth of potential bugs into one debugging surface. PR overhead is small; debugging multi-league failures is large.

## 6. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Wrong canonical created (e.g., wrong "Monterrey" team) | Manual verification against league website before apply; `--dry-run` review |
| Alias collision within sport_id | Manifest documents all aliases; within-sport distinctiveness check in tests |
| Cross-sport confusion at ACB/EuroLeague phase | `sport_id` partition handles matcher-level disambiguation; bare aliases safe per Day-22 finding |
| Bootstrap doesn't cover all teams in a league | F8 success criterion is ≥50% reduction, not 100%; remaining teams bootstrapped in follow-up |
| Stale manifest (team relocated/renamed) | Re-curation runbook in seed file docstring per KBL precedent |

## 7. v1.5 amendment context

- **Amendment #11** (bootstrap leverage ≠ total-daily-volume): Pattern G diagnostic validated. Phase 2D.5-A discovery uses resolver failure signal instead of daily-volume, avoiding the long-tail trap.
- **Amendment #10** (FL-only corroboration ceiling): LMB is FL-only. Strict-tier auto-apply is the mechanism; alias/fuzzy cap at 0.70 review_queue. Same as Handball finding — bootstrap value is gated on strict-tier coverage.
- **Day-22 sport_id partition finding**: bare aliases safe under distinct sport_ids. Applies to ACB/EuroLeague phase.

---

_LMB seed manifest in `scripts/lmb_seed.py` (Track 2, below)._
