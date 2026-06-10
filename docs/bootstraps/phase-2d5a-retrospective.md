# Phase 2D.5-A — Retrospective

**Status:** COMPLETE. All 9 league workstreams applied (Day-28 → Day-35) — 8 Basketball + 1 Baseball (LMB, the sport-bridge workstream that launched the data-driven methodology).
**Window:** 2026-05-28 → 2026-06-08 (applies); retrospective written Day-37 (2026-06-10).
**Headline:** `sp.teams` Basketball 1,981 → **2,042** (+61 teams); 122 Basketball teams now carry `country_code`. Basketball matcher capability 53.3% → 58.6% (trough 44.6% Day-31, recovered +14.0pp as bootstrapped denominator lifted strict-tier resolutions).

---

## 1. What Phase 2D.5-A was

A data-driven league-bootstrap campaign. Instead of guessing which leagues to cover, the unresolved-record signal from production (`sp.resolution_log` no_match) ranked priority: the leagues the resolver failed on most got bootstrapped first. Nine basketball leagues, applied across eight sessions, each pre-populating `sp.teams` + `sp.team_aliases` so the resolver auto-matches provider records at strict tier.

| # | Workstream | Apply date | Annotation |
|---|---|---|---|
| 1 | LMB (Mexican Baseball — sport bridge) | 2026-05-28 | phase_2d5a_lmb_bootstrap |
| 2 | Liga ACB (Spain) | 2026-05-29 | phase_2d5a_acb_bootstrap |
| 3 | Italian LBA | 2026-06-02 | phase_2d5a_lba_bootstrap |
| 4 | Israeli BSL | 2026-06-02 | phase_2d5a_israeli_bsl_bootstrap |
| 5 | Turkish BSL | 2026-06-02 | phase_2d5a_turkish_bsl_bootstrap |
| 6 | Greek HEBA A1 | 2026-06-04 | phase_2d5a_heba_bootstrap |
| 7 | Russian VTB | 2026-06-05 | phase_2d5a_vtb_bootstrap |
| 8+9 | EuroLeague gap-fill + ABA League | 2026-06-08 | phase_2d5a_euroleague_aba_bootstrap (combined) |

(Eight annotation rows cover nine workstreams — #8 and #9 were combined into one apply and one annotation.)

---

## 2. The methodology, institutionalized

By the end of the phase the per-workstream sequence had stabilized into a repeatable runbook. Cost per workstream declined across the phase (LMB took 3 rounds of correction; later workstreams took 0–1).

1. **Pattern A.2 pre-scope discovery** — query production `sp.resolution_log` no_match (Basketball, 7d) BEFORE sourcing a roster. Production failure volume ranks which teams matter and catches out-of-scope leakage.
2. **Authoritative roster paste** — operator-verified Wikipedia season roster → manifest of `(canonical, country, aliases, notes)` tuples.
3. **Amendment #22 pre-apply alias-claim audit** — scan every manifest alias against all existing `sp.team_aliases` sources for the sport; resolve multi-team_id collisions before apply.
4. **Pattern D pre-flight → dry-run → wet apply** — verify connection endpoint (`current_database` + DATABASE_URL host) matches production before any write.
5. **Post-apply collision audit** — re-scan; DELETE bootstrap rows colliding with legacy stubs / dormant phantoms.
6. **baseline_shifts annotation** — idempotent INSERT (pre-flight existence check per amendment #19).
7. **F7 verification (~14h post-apply)** — team_id JOIN against `sp.fixtures` counts strict resolutions attributable to the league. This is the canonical validation, not aggregate capability rate.

---

## 3. Amendments produced (#12–#22)

The phase expanded the v1.5 amendment pile from 11 to 22. One line each:

- **#12** — Multi-agent verification handoffs require artifact paste, not summary or line references.
- **#13** — Pattern A.2 applies to data sources (authoritative-source verification), not just code.
- **#14** — Sequential PRs while methodology is being validated; bundle once proven (calibrate PR granularity by maturity).
- **#15** — Bootstrap leverage ≠ total-daily-volume; production discovery overrides scope-doc default sequencing.
- **#16** — 3-letter ISO country codes are the established `sp.teams` convention.
- **#17** — Pattern D pre-flight as a shared function, not per-script reimplementation.
- **#18** — Journal claims about code/schema state require artifact verification at write time, not memory.
- **#19** — Production-state write ops against observability tables require idempotency discipline (pre-flight existence check).
- **#20** — Aggregate matcher_capability_rate is denominator-sensitive to daily record-mix; use weekly/per-sport windows. F7 JOIN is canonical validation.
- **#21** — Pattern A.2 pre-scope discovery is more efficient run BEFORE authoritative-source sourcing, not after.
- **#22** — Pre-apply alias-claim audit is mandatory; manifest aliases under bootstrap_league_coverage do not block on legacy/alias_tier aliases (within-source NOT-EXISTS only), so cross-source collisions must be audited explicitly.

---

## 4. What surprised us

- **BACKFILL is not prominence-correlated** (Day-31, Israeli BSL). The Phase 2A.5 legacy `public.entities` accumulator was a provider-snapshot of teams that appeared in live feeds during the discovery window — NOT an authoritative roster. Maccabi Tel Aviv (6× EuroLeague champion) was missing while mid-tier teams were present. Empirical dry-run prediction is the only reliable BACKFILL signal.
- **Collision discipline is post-apply-mandatory** (Days 32–35). Clean pre-apply audits still produced new collisions on insert (AO Mykonou Day-33; Uralmash spelling variants Day-34; 5 EuroLeague cross-source collisions Day-35). The post-apply audit caught what pre-apply could not predict.
- **Off-season F7 volume is low but valid** (Day-36, EuroLeague+ABA). 9 strict resolutions vs 30–50 projection — explained by seasons ending May/June, not a methodology failure. Volume grows when leagues resume.

---

## 5. The automation pivot (Days 36–37)

After 9 manual workstreams, the question became: can we build the universe faster than one-league-at-a-time? A Claude Code survey established that **FL already exposes a full team-master-data API** (`/v1/tournaments/standings` for rosters, `/v1/teams/data` for canonical + country) — no external encyclopedia (Wikidata/TheSportsDB) needed. The pilot (German BBL) and engine build validated against production established what FL can and can't do:

**FL automates (validated):** roster completeness (18/18 BBL), BACKFILL detection (15/15 correct, 0 collisions), country codes (Germany → DEU), in ~13 seconds vs ~40 min manual.

**FL cannot provide:** real canonical names (FL "canonical" is a bare-city provider short-form — "Bonn", not "Telekom Baskets Bonn") or alias variants. Real canonicals come from the operator for INSERTs only; aliases come from production discovery.

**The engine (Components 1+2, validated; Component 3 in progress):**
- **Collision audit** (`resolver/collision_audit.py`) — amendment #22 as a tested function; catches all three historical real collisions; 14/14 tests.
- **Alias harvester** (`scripts/harvest_aliases.py`) — mines real provider strings from `sp.resolution_log` no_match (better than hand-guessed aliases), pipes through the collision audit. After precision tuning (distinctive-token matching, 0.85 threshold, country filter), the false-positive class is structurally eliminated — 44,766 of 45,256 strings rejected pre-audit. 24/24 text-match tests.
- **Fragmentation detection** (`resolver/fragmentation.py`) — encodes the Day-37 locked rule (below); 16/16 tests.
- **Batch orchestrator** (`scripts/fl_universe_batch.py`) — in progress.

Two findings to pin as methodology notes:
- **FL standings exist only for league-table stages, not knockout** (Play Offs 404 → Main success). Any playoff-structured league hits this; the stage-rank heuristic prefers regular-season stages.
- **FL canonical = provider short-form.** FL automates structure (roster / BACKFILL / country); humans + authoritative sources own identity.

---

## 6. The fragmentation resolution rule (Day-37, LOCKED) — methodology amendment

Production analysis of 7 BBL fragmented pairs revealed the legacy accumulator often holds the **same real club as two `sp.teams` rows** — a city-stub ("Oldenburg") and a full-name stub ("EWE Baskets Oldenburg"). The rule:

> For each city-stub / full-name pair, compare fixture counts:
> - **One side has zero fixtures → ALIAS-LINK** (automatable). Canonical winner = the side WITH fixture history (Option A, fixture-history wins, per F1 discipline). Full-name form becomes an alias on the live stub; the dormant duplicate is a phantom (leave per dormant-phantom discipline, or DELETE).
> - **Both sides have fixtures → MERGE REQUIRED** (operator-driven, never auto-applied). Reuses Tennis-dedup FK-cascade machinery.

BBL distribution: 5 alias-link (Oldenburg, Ludwigsburg, Braunschweig, Würzburg, Syntainics) + 2 merge-required (Rostock 5+3, Hamburg 2+1). Component 3 auto-proposes alias-links and flags merges for the operator; it never merges automatically.

This generalizes: every league whose legacy data captured both short and full forms has this shape. The fixture-count fork is the safe automation boundary.

---

## 7. Next-phase decision

1. **Finish Component 3** (batch orchestrator) — last automation piece; fragmentation primitive already built + tested.
2. **Batch-crawl all basketball leagues** via FL — re-seed from authoritative rosters rather than the provider-snapshot-bounded legacy accumulator.
3. **Operator merge tasks** (deferred) — Rostock + Hamburg BBL merges; reuse Tennis-dedup machinery. Blocks nothing.
4. **Generalize to other sports** — top-tier Soccer (Serie A / Bundesliga / Ligue 1), once basketball batch proves out. Note: Pattern G long-tail risk applies below top tiers.
5. **Durable maintenance (Path B)** — schedule the harvester + collision audit as the annual-refresh engine, so roster churn is caught automatically rather than re-bootstrapped by hand.

**Open methodology question carried forward:** the canonical-fragmentation rule resolves detection + alias-link; the operator-merge sub-case (both sides have fixtures) still needs the Tennis-dedup machinery wired into a repeatable runbook before it scales across all leagues.
