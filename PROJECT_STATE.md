# PROJECT_STATE.md

Living state of the SP Architecture rebuild. Each session updates this
file with what landed, what was investigated, and what's open for the
next session. Treat it as the project's running journal.

---

## Where we are / what's next — phase status header

**Read this first, every session.** Track against the SP Architecture
v1.4 seven-phase arc (§11), not the 2D.5-A coverage-workstream
vocabulary. The product (v4 serving, Phase 3+) is the goal. Resolver
accuracy is a critical INPUT to the product, not a substitute for it.

### Current position

**Phase 2 (Resolution) — IN PROGRESS.** Next boundary: **Phase 3
(v4 cutover) — NOT STARTED.**

### Seven-phase status

- **Phase 0 — Tactical fixes**: DONE. (`WEB_CONCURRENCY`, structlog
  JSON, `sp.provider_api_calls`, FL fetch off request path.)
- **Phase 1 — Foundation**: DONE except §6.5 archival. (Neon
  provisioned; canonical schema via alembic chain; FL + Kalshi
  ingestion modules writing to `sp.*`; 30-day backfill scripts. The
  end-of-phase §6.5 archival job to object storage is still NOT
  BUILT — see exit gates.)
- **Phase 2 — Resolution**: IN PROGRESS. Matcher built
  (`resolver/matcher.py`), Tier 1–4 ported (`resolver/alias_tier/`,
  `resolver/fuzzy_tier/`, FL + Kalshi modules), `sp.resolution_log`
  writing, admin UI mounted (`admin/router.py`, `main.py:386`).
  Missing pieces in the exit gates below.
- **Phase 3 — Cutover** (`/api/v4` feature-flagged): NOT STARTED.
  `grep "/api/v4" main.py` returns zero. No flag scaffold; frontend
  still on legacy endpoints.
- **Phase 4 — New providers** (Polymarket, OddsAPI): NOT STARTED.
  Strings appear in protocol enums; `resolver/matcher.py:309`
  states "Other providers ... not yet wired through."
- **Phase 5 — Decommission legacy**: NOT STARTED. `main.py` still
  carries `_SERIES_TOURNAMENT_HINTS` (L1483), `_FL_TEAM_HINTS`
  (L1493), `sports_feed_v3` paths, synth-event construction.
- **Phase 6 — Frontend rewrite**: NOT STARTED (placeholder per
  §11.7; triggers require backend stable on v4 for one quarter).

### Phase 2 EXIT GATES — these block Phase 3

| Gate | Spec | Status |
|---|---|---|
| Three-loop runner (§7.7) | Hot via LISTEN/NOTIFY + batch 30s + re-resolution 5–10 min | **NOT BUILT.** Only cron-batch twice/day (`run_resolver_pass.py:149` rejects `--run-mode live`, reserved for "Phase 2E"). Hot loop and 5–10 min re-resolution loop missing. Re-resolution is also the §7.6 accuracy multiplier — it retroactively re-resolves the back-catalog whenever aliases are added. |
| Daily-diff measurement loop (§11.3) | "Run a daily diff; tune until acceptable" | **BUILT BUT UNWIRED.** `scripts/daily_diff.py` exists; `railway.toml` has no cron entry for it (only the two resolver crons). Manual / irregular runs; measurement gaps since Day-21. |
| Review queue health (§7.5) | Steady-state <20 pending; alert >100 | **18,303 pending (as of 2026-06-15)** — ~915× the spec's <20 steady-state target, ~183× the >100 alert threshold. Grew from ~16,755 (Day-37) despite coverage work — the exit-gate failure made concrete: nothing drains the queue and the §7.6 re-resolution loop that would re-sweep it isn't built. Throughput gate for Phase 3; also unharvested labeled aliases that would feed re-resolution. |
| Archival job (§6.5) | Nightly mover to object storage; 30d hot / 1y archive / delete; `resolution_log` retained forever | **NOT BUILT.** No S3/object-storage code, no bucket config, no nightly job. `sp.resolution_log` unbounded (~130K rows — not yet biting, but the architectural assumption is fully un-implemented). |

### What unblocks Phase 3

A `/api/v4/sports/{id}/feed` endpoint to flag traffic onto AND a
review queue tolerable enough to cut over behind that flag.
**Neither exists yet.**

### Accuracy note

Coverage breadth is past peak leverage toward the product — basketball
67.5%, baseball / soccer 70%+ are good enough to ship. Aggregate
matcher capability is 35.3% (scope-filtered, 15,250 records,
2026-06-15T21:13Z daily_diff run) — denominator-suppressed by
near-zero-coverage sports (Tennis ceiling, Golf / long-tail); per-league
F7 remains the honest per-sport measure (Amendment #20). Highest-
leverage remaining accuracy work that also serves the critical path is
the re-resolution loop (compounds across all existing coverage) plus
queue harvest — not the next league bootstrap.

**Active workstream (Day-44 continued — LIVE IN PRODUCTION):** the
§7.6 / §7.7 re-resolution loop is **LIVE**. Three Phase 2E Railway
services created in the dashboard Day-44 (`resolver-reresolution-fl`,
`resolver-reresolution-kalshi`, `daily-diff`); both reresolution
services validated on first live pass (`run_mode='live'`,
crashes=0, halt_warnings=[], `candidate_set_size=0` correctly
selective per F7 Part B's alias-velocity-evidence settlement).
**First Phase 2 exit gate moved built → live.** F8 validation
pending — staged targeted before/after on a known
alias-flip-eligible record (passive flips ~0 by design until
coverage resumes; the loop is a multiplier with nothing to
multiply this week). Five crons total now (2 daily resolvers +
3 new Phase 2E). Coverage stays resequenced behind the loop:
worth more compounding through the now-live machinery than
ahead of it.

### Pointer

Full architecture: **SP Architecture v1.4** (Google Drive). This
header is the standing answer to "where are we / what's next" —
update it when a gate closes.

---

## Scope boundaries (durable, cross-session)

**Stochverse Academy** (`academy.stochverse.com`) is a separate
bilingual educational resource being built in its own repository and
deployed independently (likely Astro on Vercel / Cloudflare Pages).
It shares no infrastructure with the main product. Two boundaries
apply in BOTH directions and must persist across sessions:

- **Main product → Academy**: do NOT add blog/content/CMS to the
  FastAPI app; do NOT create routes/models/templates for educational
  content; do NOT touch the TypeScript frontend bundle for Academy
  pages; do NOT add subdomain or reverse-proxy logic for
  `academy.stochverse.com`.
- **Academy → Main product**: do NOT copy Stochverse production
  internals into Academy without explicit operator go-ahead. That
  includes the `sp.*` schema, resolver logic, the v1.5 amendment
  methodology pile, and any production data.

If anything Academy-related surfaces in main-product work, flag it
and leave it for the parallel Academy session.

---

## Session — 2026-07-09

### Day-47: F8 Attempt 2 aborted at §2; FL home/away inversion discovered and sized

Loop remains LIVE and healthy — FL 144 / Kalshi 144 runs, latest ~22:05 / ~22:02 UTC, `total_crashes = 0`. `main` synced to `40caff4`. F8 Attempt 2 did not touch production; it aborted cleanly at §2 (record selection) on two successive candidates after the read-only §2c check surfaced disqualifiers. In the process, the similarity-filtered discovery pattern that replaces the raw-inequality §2c uncovered a distinct FL defect class — 95 fixtures with `home_team_id` / `away_team_id` inverted relative to FL's `HOME_NAME` / `AWAY_NAME`, running at a steady ~0.2% of the FL `created_new_fixture` path since week of 2026-05-04. Six hypotheses eliminated on read-only trace; the surviving hypothesis (FL emits crossed names on some rare event shape) is retroactively unfalsifiable. Instrumentation PR opened to make the next occurrence dispositive; no backfill of the 95 attempted. Displaces F8 as this session's action item — the inversion blocks Phase 3 by sequencing (`/api/v4/sports/{id}/feed` will serve fixtures with home/away).

### Day-47: Loop health (unchanged, clean)

- **FL 144 / Kalshi 144 live runs** in the trailing 12h. Latest fires ~22:05 / ~22:02 UTC.
- `total_crashes = 0` both providers.
- `main` at `40caff4` (post-#243 / #244 merge).

All five crons continue clean.

### Day-47: F8 Attempt 2 — aborted at §2, zero production writes

Two targets selected in turn, both rejected on read-only checks before any break:

- **Monterrey** (`KQdgXlWh`, canonical `"Sultanes de Monterrey"`): passes raw §2c inequality but `similarity('monterrey', 'sultanes de monterrey') = 0.4545`, above the ~0.30 fuzzy anchor. The fuzzy tier would have rescued the record into `review_queue` — Attempt 1's failure through a different door.
- **Finding — §2c is incomplete.** The current criterion (FL provider string ≠ canonical_name after normalization) accepts diacritic and punctuation differences (`Bolivar` / `Bolívar`, `St.Louis` / `St. Louis`) that the normalizer erases to the same key. It also accepts semantic near-misses that the fuzzy tier will rescue. §2c must require `similarity(provider_string, canonical) < 0.30` — a semantic token-set difference, not mere inequality.

Applied the corrected criterion (`sim < 0.30`) to the §1 discovery CTE. Surfaced clean candidates immediately:

- `YVRjxyEk` — FL `"Queretaro"` → canonical `"Conspiradores de Querétaro"`, sim `0.23`.
- `M9rZw8VQ` — FL `"CSA"` → canonical `"Steaua București"`, sim `0`.

### Day-47: Option (a) confirmed viable — F8's forced decision is now cheap

Read-only trace of `scripts/run_reresolution_pass.py`:

- `--candidate-set fl:{ID} --apply` branches at `:579` on `candidate_set_override is not None` and calls `_hydrate_candidate_override` (`:766-829`) INSTEAD of the Tier-1/Tier-2 `_select_candidates` path. Hydrate only requires `provider match + fixture_id IS NULL`.
- `_run_matcher_over_candidates` (`:832-967`) bootstraps the same `TieredMatcher`, calls `matcher.match(signal)` at `:935`, executes `pg_insert_resolution_log(...)` at `:939-950`, commits at `:966`. Real persisted `sp.resolution_log` row.
- Cost: ~5 s, `candidate_set_size = 1`, zero unintended `review_queue` writes. Versus Attempt 1's `--limit 5000` cost — 53 min, 2,202 unintended queue rows.
- **Caveat**: `_run_matcher_over_candidates:909` filters `AND fle.sport_id IS NOT NULL`. A record whose `sport_id` is NULL is silently dropped without a log row. Carry that predicate into the §1 discovery CTE (or into the target-selection filter) or the "cheap forced decision" is a no-op on the wrong target.

### Day-47: The new finding — FL home/away inversion (displaces F8)

Surfaced from the corrected §2c similarity discovery: fixtures whose `home_team_id` holds the team FL calls away, and vice versa. Exact-match test both directions; no fuzzy judgment involved.

**Sizing** (all-time, no `last_seen_at` filter):

- **95 inverted fixtures total.** First appearance week of 2026-05-04 — the week of the `initial_sp_schema` migration (`8f404e0dc89a`, 2026-05-07).
- **Rate is stable at ~0.2%** of the FL `created_new_fixture` denominator. Weekly pct: `0.19 / 0.34 / 0.49 / 0.06 / 0.11 / 0.04 / 0.09 / 0.14 / 0.39 / 0.16`. No trend. Poisson noise around a constant rate.
- **Not a bootstrap artifact; not self-correcting.** The 7-day window in the initial probe was our sampling frame, not the bug's lifespan.
- **Raw-count decay (50 → 1/week) is denominator drop.** FL creations fell from 15,422 → 632/week as the fixture table filled behind live coverage. Rate held.
- **Propagation is real, downstream of the original defect.** 20 of the first 50 lack `created_new_fixture = true` — they linked to an already-inverted fixture via strict-tier `find_fixture`.
- **Signature** on 100% of the 95: `first_decision_provider = 'fl'`, `created_new_fixture = true`, `kalshi_markets_on_fixture = 0`, `fl_transitional_path = 'created_null_comp_fixture'`, `fl_transitional_sport_only = true`, all created 02:03–02:10 UTC (daily FL cron window). Spans Soccer and Basketball.

### Day-47: Six hypotheses eliminated on read-only trace

1. **Kalshi-origin inheritance** — `first_decision_provider = 'fl'` on all, zero Kalshi markets on the fixture. Not Kalshi's doing.
2. **Swap-probe propagation** — no `orientation_flipped = true` on any of the 95. The probe (`resolver/matcher.py:207-223`) is a read-only `find_fixture` lookup and does not carry swapped orientation into `ensure_fixture`.
3. **Writer / `ensure_fixture` transposition** — `reason_detail.home_team_id` and `sp.fixtures.home_team_id` come from the same local (`matcher.py :154-155` → `:179-180` and `:228-234`); they cannot disagree, and both contradict FL.
4. **Extraction branching** — `FLResolverModule.extract_signal` (`fl.py:88-97`) is straight-line kwargs. No payload-shape, sport, or competition branch.
5. **Ingestion transform** — `ingestion/fl.py:243` stores `raw = event_raw` verbatim; `ingestion/base.py:191` copies `raw_payload` verbatim.
6. **Participant-id precedence** — `_team_candidates` weights `fl_team_id 1.0 > name 0.9 > shortname 0.7`, and `AliasResolver.resolve()` (`resolver/aliases.py:86-120`) short-circuits on first unambiguous hit — so a crossed `HOME_PARTICIPANT_TEAM_ID` WOULD shadow a correct `HOME_NAME`. But `HOME_PARTICIPANT_TEAM_ID` / `AWAY_PARTICIPANT_TEAM_ID` are NULL on all 11 sampled — the weight-1.0 candidate never exists, resolve() falls through to name at 0.9. Shortnames also corroborate names (CLI/LIS, RIV/RAC, BAT/VAL). Mechanism cannot fire.

### Day-47: Surviving hypothesis — retroactively unfalsifiable

FL emits crossed `HOME_NAME` / `AWAY_NAME` on some rare event shape, at a steady ~0.2%. Nothing on-disk lets us reconstruct the payload as it was at decision time for the 95:

- `sp.fl_events.payload_hash` is overwritten unconditionally on every UPSERT (`ingestion/base.py:212`) — current-value only, no history.
- No payload-history audit table exists for `sp.fl_events`; migrations don't create one; `sp.resolution_log` audits decisions, not payloads.
- Strict-tier `reason_detail` (the tier the 95 went through) stamps ids only — no names, no normalized inputs. The alias tier (`alias_tier/matcher.py:208-223`) and fuzzy tier (`fuzzy_tier/matcher.py:434-437`) DO capture `*_provider_normalized` and `*_canonical`, but the 95 have strict-tier signatures (`home_team_id`/`away_team_id` set from `matcher.py:179-180`), not alias/fuzzy signatures.
- `payload_changed_after_decision` was TRUE on 11/11 sampled, but the non-inverted control on the same path is 2381 TRUE / 684 FALSE (78% baseline). `P(11/11 | null) ≈ 0.06` — not dispositive.

Retroactively unfalsifiable on existing data. Instrument forward.

### Day-47: Instrumentation PR — freeze extractor's candidates into strict-tier reason_detail

`resolver/matcher.py` after `:180`, additive JSONB, no migration:

```python
reason_detail["extracted_home_candidates"] = [
    {"raw": c.raw, "normalized": c.normalized, "kind": c.kind, "weight": c.weight}
    for c in signal.home_team_candidates
]
reason_detail["extracted_away_candidates"] = [
    {"raw": c.raw, "normalized": c.normalized, "kind": c.kind, "weight": c.weight}
    for c in signal.away_team_candidates
]
```

Effect: every future strict-tier decision — including new FL `created_new_fixture` rows — carries a frozen snapshot of the payload as the extractor saw it, independent of any later `raw_payload` overwrite. Detector queries live in `docs/reresolution/homeaway-inversion.md` (same PR): weekly-rate monitor + candidate-snapshot comparison. The comparison is dispositive on the next live inversion:

- If `kind='name'` at decision time resolves to the AWAY canonical, FL sent us a crossed payload → source-side guard needed at the extractor.
- If it resolves correctly, the inversion originated downstream of extraction → the trace has a gap.

Expected verdict within days of merge: `~1–7` qualifying FL creations per week, so a live occurrence should surface within one or two weekly runs. **Do NOT backfill the 95** — cause unknown, any rewrite is a guess at which side is correct, and rewriting destroys the evidence the detector needs.

`payload_hash_at_decision` and a history table were considered and skipped; noted as options if a second occurrence class appears.

### Day-47: Filed, not chased (different failure mode — mis-resolution, not inversion)

- **`ANGpZ5Z7`** — FL `"Abo"` (Åbo, Finnish) → canonical `"All Boys"` (Argentine), similarity `0.083`. Opponent `"Ilves 2"` is Finnish. Probable mis-resolution to the Argentine team; the alias / fuzzy tier landed the wrong Latinized short form.
- **`M9rZw8VQ`** — FL `"CSA"` → canonical `"Steaua București"`, similarity `0`. Possibly legitimate (`CSA Steaua București` is the same club historically); noted as ambiguous.

Neither is home/away inversion. Filed separately so the inversion class stays clean.

### Day-47: Methodology note

§2c's read-only checks rejected two F8 targets before any write. Attempt 1's 53-minute / 2,202-queue-row mistake was not repeated. The entire day's investigation — six hypotheses eliminated, bug sized from a 7-day sample to full history, instrumentation PR authored — cost **zero production writes**. Discovery under a similarity-filter (`sim < 0.30`) was the entry point; the corrected §2c both prevents the F8 misfire and exposes the inversion class that displaced F8 as the action item.

### Day-47: What blocks Phase 3

Phase 3's `/api/v4/sports/{id}/feed` will serve fixtures with `home_team_id` / `away_team_id`. Nothing consumes those columns yet, so the inversion is not a live regression — but the endpoint would be. Not an emergency; must be understood before the endpoint exists. Sequencing constraint on Phase 3, not a blocker on the loop.

### Day-47: PR state

- **Instrumentation PR**: `claude/explore-repo-pFQ9r` — `resolver/matcher.py` + `docs/reresolution/homeaway-inversion.md`. Additive JSONB; no migration; universal to all providers (not FL-specific).
- **Day-47 journal PR**: this entry, single-file PR (`claude/project-state-2026-07-09-day47`).
- **F8 Attempt 2 status**: aborted at §2. To be re-attempted after the inversion class is understood — or deferred entirely if the instrumentation-driven trace shows the loop's forced-decision mechanism is already validated implicitly by the inversion investigation's read-only trace of the same `_hydrate_candidate_override` / `_run_matcher_over_candidates` path.
- **`f8-procedure.md` §2c amendment (deferred, separate PR)**: switch inequality to `similarity(provider_string, canonical) < 0.30`. Carry `AND fle.sport_id IS NOT NULL` into the §1 discovery CTE per the `:909` caveat, so a NULL-sport_id target doesn't silently no-op the forced pass. Scoped as a follow-up doc-only PR, not bundled into either PR above.
- **Phase-status header active-workstream line unchanged**: loop still LIVE. F8 not surfaced as the active workstream — the inversion investigation is.

### Pending — next session

1. **Read the first live inversion row** — the instrumentation lands on `main`; the next FL cron pass (02:00 UTC) exercises the new keys on the day's unresolved records. Any new inversion arrives with `extracted_home_candidates` / `extracted_away_candidates` populated. Run the candidate-snapshot query in `docs/reresolution/homeaway-inversion.md` when the first row appears (`~1–7` days). Verdict determines the fix path.
2. **`f8-procedure.md` §2c amendment PR** — `sim < 0.30` clause + sport_id predicate. Small, doc-only.
3. **F8 Attempt 3 (deferred)** — if the inversion trace confirms the loop's forced-decision path works as spec, F8's dispositive value drops materially. Reassess necessity after the inversion verdict.
4. **Remaining Phase 2 exit gates before Phase 3** — review-queue drain (passive under the loop), §6.5 archival (unbuilt). Unchanged from Day-46 pending.
5. **Carried-forward**: 9 pre-existing `test_phase_2d5_*` / `test_phase_2f1_*` collection errors; not exercised this session.

---

## Session — 2026-07-08

### Day-46: Loop healthy overnight; F8 Attempt 1 surfaced the canonical_name-shadowing failure mode; record restored clean

Loop remains LIVE and healthy. F8 Attempt 1 did not complete the dispositive flip, but produced a structural finding that sharpens the selection criterion for Attempt 2 — the alias tier and fuzzy tier build `CandidateIndex` from `sp.teams.canonical_name`, not from `sp.team_aliases` (Day-21 architectural finding re-surfaced). Deleting a single alias only breaks the strict tier; if the team's canonical_name matches the FL provider string, the alias tier re-matches it by name and the record never produces the no_match decision the loop's Tier-1 filter requires. New §2c "canonical-name-shadow-prevention" check added to the F8 procedure. Attempt 1 record restored byte-for-byte from snapshot; only residue is two append-only `sp.resolution_log` rows (expected accretion, harmless).

### Day-46: Loop health (unchanged, clean)

- **FL 144 / Kalshi 144 live runs** in the trailing 12h. Latest fires ~21:35 / ~21:37 UTC.
- `total_crashes = 0` both providers.
- daily-diff writing on schedule: fresh rows at 2026-06-24 03:05 (nightly cron) plus manual triggers. Measurement gap (previously stuck at 2026-06-15) stays closed.

All five crons continue to run cleanly at their real cadences.

### Day-46: F8 Attempt 1 — record MRQznWTj (Warwick Senators vs Geraldton Buccaneers, FL, Basketball)

**Selection** (§1 discovery query):
- `fl_event_id = MRQznWTj`, sport = Basketball.
- `break_side = away` (Geraldton Buccaneers, team_id `f3cca7c9-...`), `alias_count = 1`.
- Alias `id 767d508b-...`, `alias/alias_normalized = 'Geraldton Buccaneers' / 'geraldton buccaneers'`, `source = legacy_bootstrap`, `confidence = 0.95`.

**§2b verification passed on the pre-break decision**: `reason_code = 'strict'`, `away_team_id` present in `reason_detail`. Snapshot captured.

**Break executed cleanly** (§4): `DELETE` 1 alias row, `UPDATE` 1 `fl_events` row to `fixture_id = NULL`. Pattern D confirmed `neondb`.

**Runner limitation surfaced**: `run_resolver_pass.py --limit 50` did NOT reach the target record. 43,878 unresolved FL records; runner has no record-targeting flag (only `--provider`, `--run-mode`, `--limit`). Raised progressively; `--limit 5000` finally produced the fresh decision for `MRQznWTj`.

### Day-46: The finding — canonical_name shadowing (why F8 could not complete on this record)

The forced fresh decision (§5) wrote **two** rows at `2026-07-08 22:09:51`, same pass:

| id | tier | reason_code | reason_detail |
|---|---|---|---|
| 7272498 | `strict@2a.6` | `no_match` | `fail_reason = alias_resolution_incomplete`, `away_resolved = false`, `home_resolved = true`. **No team_id anywhere in reason_detail.** |
| 7272499 | `alias@2c.0` | `review_queue` | `away_team_id = f3cca7c9` **RESOLVED via canonical-name match**. `home_collision = true`, `colliding_home_team_ids = [92b83146, 5948f38d]`. |

**Root cause**: `CandidateIndex` (used by the alias tier and fuzzy tier) is built from `sp.teams.canonical_name`, not from `sp.team_aliases`. Geraldton's canonical name (`"Geraldton Buccaneers"`) exactly matched the FL provider string, so the alias tier immediately re-matched it by name (`away_ratio 1.0`, `away_collision false`) — the alias row was never on the resolution path for that side; it was just belt-and-suspenders that duplicated the canonical.

Two independent disqualifiers meant the loop could NOT catch this record post-break:

1. **Latest decision was `review_queue`, not `no_match`** → the loop's Tier-1 filter (`reason_code = 'no_match'`) excludes it entirely.
2. **The `no_match` row's `reason_detail` had NO team_id** → even if the latest had been no_match, Tier-2 LOOSE containment had nothing to match the re-added alias's `team_id` against.

**This was a Day-21 lesson we failed to apply at selection time.** The scope doc's F1a design correctly anticipated Tier-2 containment needs team_ids in `reason_detail`; the F8 procedure's §2b checked that on the pre-break decision but not that the post-break decision would continue to carry them. §2c closes the gap by requiring FL-provider-string ≠ canonical_name (post-normalization) BEFORE the break — that ensures the strict-tier no_match will be the latest decision AND that it will carry the team_ids the alias tier previously landed on.

### Day-46: Sharpened selection criterion — §2c added to F8 procedure

**The rule**: the break-side team's `canonical_name` must NOT match the FL provider string after normalization. The alias must be the ONLY path to the team across ALL tiers — strict tier AND the canonical-name `CandidateIndex`.

**Concretely, prefer records where**:
- FL sends a shorter form ("Bonn") while canonical is fuller ("Telekom Baskets Bonn").
- FL sends an abbreviation while canonical is spelled out.
- Canonical carries sponsor / suffix tokens the FL string doesn't.
- Any material token differences after normalization.

The check is a SQL query comparing `fl_events.raw_payload->>'HOME_NAME'` / `'AWAY_NAME'` against `sp.teams.canonical_name` with `lower + unaccent + whitespace-collapse` normalization. `SHADOW_RISK = FALSE` required. Details in `docs/reresolution/f8-procedure.md` §2c (added this session).

### Day-46: Side finding — Warwick Senators (92b83146) latent collision with 5948f38d

Surfaced only during the failed forced-decision pass: `colliding_home_team_ids = [92b83146, 5948f38d]`. Warwick's aliasing normally resolves without collision because the strict tier lands it first; when the strict tier stopped resolving `MRQznWTj` (because the away side was broken), the alias tier's home-side candidate lookup surfaced the collision. **Masked in normal operation.**

Not blocking F8; noted as the kind of thing a **review-queue drain workstream** (one of the remaining Phase 2 exit gates) would systematically expose. Logged for the eventual queue-drain scope conversation.

### Day-46: Clean restore

**Alias re-inserted** with all original values including original `created_at (2026-05-08T15:33:27.981378+00)` — an emergency restore, not a live F8 §7 (which requires `created_at = NOW()` for the Tier-2 freshness predicate to fire). `fixture_id` restored to `b35f850a-2964-4564-8c31-dc2ab919ecee`.

**Post-restore verification** matches snapshot byte-for-byte: `alias_count = 1`, `alias_row_present = 1`, `fixture_id` correct. Only residue: two append-only `sp.resolution_log` rows from the forced pass (expected accretion, harmless — next daily cron pass re-resolves strict for this record just as before).

### Day-46 addendum: Forced-decision pass cost + halt-warning context (journaled for gate #3 traceability)

The `run_resolver_pass.py --provider fl --run-mode standalone --limit 5000` run that reached MRQznWTj (§5 forced-decision step; needed because the runner has no record-targeting flag and `--limit 50` didn't reach the target) completed at `22:39:57Z`. Journaling the cost here so a future reader checking gate #3 (§7.5 review queue health) doesn't find the ~2,200-row jump mysterious.

- **Runtime**: 3,200.6s (**53 minutes**), 5,000 records scanned.
- **Production writes** — all legitimate resolver decisions on genuinely-unresolved backlog records; the same decisions the 02:00 daily cron would eventually have made:
  - 74 strict `auto_applies` (74 records got `fixture_id` set)
  - 2,202 `review_queue` writes (1,216 alias-tier + 986 fuzzy-tier)
  - 2,724 `no_match` rows
  - Thousands of `sp.resolution_log` accretion rows
  - One `sp.resolver_runs` row (`run_id 664313d4-48f4-4b7d-a129-7742888c4448`)

**Gate #3 (§7.5 review queue health) impact**: review queue grew by ~2,202 as a side effect. Was ~18,303 (Day-38 measurement); now ~20,500 estimated. **Not a regression** — the daily cron would have written the same rows over the next N passes; this run just batched them into 53 minutes. Log the number so gate-#3 tracking on daily-diff post-loop-live doesn't misattribute the jump to loop mis-behavior.

**Halt warning fired — expected, NOT a regression**: `halt_criteria_exceeded: coverage=1.5% (74/5000) below the 60% floor`. The floor (design doc §2) is calibrated for the DAILY CRON scanning fresh records, most of which resolve. This pass deliberately scanned 5,000 records from the UNRESOLVED BACKLOG — records that have already failed repeatedly. **1.5% coverage on that population is the expected shape, not a defect.** Exit 0 as designed. Note this so a future reader of `sp.resolver_runs.extra->>'halt_warnings'` for `run_id 664313d4` doesn't misread it as resolver degradation.

**Interesting datum — 74 of 5,000 backlog records DID resolve** (1.5%). Small but real evidence that the backlog is not fully static — records that previously failed CAN flip if aliases have since been added. Consistent with the alias_tier ~45/7d + fuzzy_tier ~9/7d write-back rates from the F7 Part B velocity query. Also a sanity check: **re-resolution DOES flip records when aliases land** — but for records that predated the alias-add without the alias-add signal being captured in `reason_detail`, only the daily cron's naïve retry catches them, not the loop's Tier-2 targeted path. Tier-2's specificity is by design (targeted work); the daily cron is the fallback for whatever Tier-2 doesn't see.

### Day-46 addendum: F8 procedure amendment — cost-of-forced-decision (options a/b/c)

Added to `docs/reresolution/f8-procedure.md` between §2c and §3, so the operator reads it BEFORE running §5 in Attempt 2:

- **Option (a)** — `run_reresolution_pass.py --candidate-set fl:{RECORD_ID} --apply` — the LISTEN/NOTIFY seam repurposed. Bypasses Tier-1+Tier-2 selection, drives the matcher against one record at zero scan cost. Requires verification against the code (`_hydrate_candidate_override` call site) that `--apply` actually persists a fresh `resolution_log` row. Doc carries the verification callout.
- **Option (b)** — `run_resolver_pass.py --limit N` with a target chosen at §1 selection to sort within the first ~50 rows. Day-45 observation: runner orders by `last_seen_at DESC`, so pick a very recently-seen record.
- **Option (c)** — wait for the 02:00 UTC daily cron. Slowest but zero-effort.

Attempt-1 baseline (53 min / 2,202 queue writes / halt warning) recorded in the doc verbatim so future readers see the cost that should NOT be repeated. Pushed as an addendum commit on PR #243.

### Day-46: F8 procedure doc created — `docs/reresolution/f8-procedure.md`

Prior sessions carried the procedure in chat only. This session codifies it as a doc that survives resets, incorporating Day-45's operator-side review notes and Day-46's §2c criterion. Delivered as a separate PR (branch `claude/f8-procedure-doc`). Section labels are stable so the operator's session references stay working (§1 discovery, §2a/§2b/§2c gates, §3 snapshot, §4–§8 the writes, §9 verify).

### Day-46: PR state

- **PROJECT_STATE Day-46 journal**: this entry, single-file PR (`claude/project-state-2026-07-08-day46`).
- **F8 procedure doc**: separate PR (`claude/f8-procedure-doc`) — new file `docs/reresolution/f8-procedure.md`.
- **Phase-status header active-workstream line unchanged**: loop still LIVE, F8 pending. Attempt 1 was a false start on a well-understood failure mode, not a regression.

### Pending — next session

1. **F8 Attempt 2 — rerun with a properly-selected target**. Same 8-step sequence with the §2c gate added. Selection preference: national-team friendly / off-season exhibition where FL sends an abbreviation and canonical is spelled out. Dispositive moment unchanged: live FL cron pass with `candidate_set_size ≥ 1`, record flips to strict/alias, `fixture_id` repopulates.
2. **Runner record-targeting flag** — if F8 continues to run into the `--limit` reach issue, add `--record-id <provider_record_id>` to `scripts/run_resolver_pass.py` as a small quality-of-life improvement for future forced-decision cases. Deferred until F8 Attempt 2 shows whether the progressive `--limit` bump is livable.
3. **After F8 passes**: remaining Phase 2 exit gates before Phase 3 (`/api/v4`) opens:
   - **Review-queue drain** — passive consequence of the loop running as coverage resumes. Was 18,303 at Day-38; measure post-F8. Warwick's Day-46 latent-collision finding is the kind of thing a systematic drain would expose.
   - **§6.5 archival** — the last unbuilt Phase 2 exit gate. Orthogonal to F8; can scope-doc + build in parallel with Attempt 2.
4. **Carried-forward**: 9 pre-existing `test_phase_2d5_*` / `test_phase_2f1_*` collection errors (track so not blamed on Phase 2E).

---

## Session — 2026-06-23

### Day-44 continued: Re-resolution loop LIVE IN PRODUCTION — first Phase 2 exit gate moved built → live

The loop went live this session, staged FL-first per the operator's path-to-live brief. #238 merged. Three Phase 2E Railway services created in the dashboard. Both reresolution services validated on first live pass — `run_mode='live'`, zero crashes, no halt-criteria warnings, `candidate_set_size=0` matching F7 Part B's design prediction. Five crons total now (2 pre-existing daily resolvers + 3 new Phase 2E). The first §7.7 three-loop runner gate is no longer "built" — it's "live".

### Day-44 continued: #238 merged to main

`claude/reresolution-loop-scope` head `04ae152` → main. The PR bundled everything from Day-40's scope decision through Day-44's enable-step:
- Scope doc with all 8 framing questions DECIDED on production evidence (#229 / #240 chain documented the build)
- Loop build (`scripts/run_reresolution_pass.py`)
- Three CONCURRENTLY migrations: `a2c4f6d8e1b3` (fail_reason partial + reason_detail GIN), `b3d5e7f9a2c4` (provider_record_decided_at), `c5e7f9a3b1d4` (composite partial `ix_*_unresolved_last_seen` for the Day-44 watermark)
- Four perf-iteration attempts (DISTINCT-ON → LATERAL → MATERIALIZED CTE → last_seen_at watermark) — each measured against production, each surfacing the next bottleneck
- Tier-1 SQL: MATERIALIZED CTE with the 3-day `last_seen_at` watermark; LATERAL inner LIMIT 1 against the new covering index
- Tier-2 Python LOOSE F1a alias-add OR fixture-state filter
- Pre-merge cleanup: no-op `upgrade()`/`downgrade()` bodies + glob-based CI guard pinning the no-op + docstring-runbook pattern (Day-44 a8cf26a)
- Enable-step: railway.toml uncommented, CI guard inverted to "live-blocks-present" guard (Day-44 04ae152)
- 248 tests green
- `sp.alembic_version` stamped `c5e7f9a3b1d4` (all three indexes physically present in production; linear-history chain intact)

### Day-44 continued: Three Phase 2E services created in Railway dashboard (services don't auto-create)

Per `DEPLOYMENT.md` "One-time Railway setup (services don't auto-create)" runbook — `railway.toml` declares the schedule + startCommand, but the operator creates each service explicitly. Staged rollout:

| Service | Schedule | Start command |
|---|---|---|
| `resolver-reresolution-fl` | `*/5 * * * *` | `python scripts/run_reresolution_pass.py --provider fl --apply` |
| `resolver-reresolution-kalshi` | `2-59/5 * * * *` (FL+2min stagger) | `python scripts/run_reresolution_pass.py --provider kalshi --apply` |
| `daily-diff` | `0 3 * * *` (nightly) | `python scripts/daily_diff.py` |

### Day-44 continued: First live passes validated

**FL** — `run_id 88da8238`:
- `run_mode='live'`, `crashes=0`, `halt_warnings=[]`
- `latency_candidate_select_ms=2579` (~2.6s) — under the 5s F6 ceiling
- `candidate_set_size=0` (correctly selective; no recent alias-adds to act on)
- `finished_at` populated; transaction committed cleanly

**Kalshi** — `run_id a3ed664a`:
- `run_mode='live'`, `crashes=0`, `halt_warnings=[]`
- `latency_candidate_select_ms=931` (~0.93s) — **even faster than FL**, despite the larger total unresolved population. The 3-day `last_seen_at` watermark bounds the driver set just as designed; Kalshi's higher write velocity means its unresolved-recent set is denser than FL's
- `candidate_set_size=0`

**`daily-diff`** — service created Day-44 but the nightly 03:00 UTC schedule hadn't fired by session close. Manual trigger run scanning. Confirm tomorrow that a fresh row landed in `sp.daily_diff_reports` for 2026-06-23 or 2026-06-24 — the latest existing row was 2026-06-15, so this closes the Day-21 measurement-debt explicitly (the carried-since-Day-21 gate is now wired and presumed-running, awaiting visible evidence).

### Day-44 continued: Env-var catch (Railway services don't inherit env)

**Worth recording for the operator runbook**: new Railway services don't inherit env vars from sibling services or the project. First Phase 2E service got a default `dev:dev@localhost:5432/sports_dev` `DATABASE_URL` — caught before the first run via dashboard review. Replaced with the production Neon URL (`ep-fragrant-frog-ak3esp11/neondb`) copied from `resolver-cron-fl`'s env. Each of the three new services got the full set:

- `DATABASE_URL` (production Neon)
- `EXPECTED_PRODUCTION_DB_HOST`
- `EXPECTED_PRODUCTION_DB_NAME`
- `STOCHVERSE_LOG_FORMAT`
- `FLASHLIVE_API_KEY` (the FL reresolution path; daily-diff queries FL too)

**Pattern D pre-flight (Amendment #17) is what would have caught a real env-misfire** if the localhost URL had stuck — the pre-flight does a `current_database()` lookup and refuses to write if it doesn't match `EXPECTED_PRODUCTION_DB_NAME`. Pattern D passing on both first live passes confirms correct production-DB targeting. The catch came earlier (dashboard review) and Pattern D is the belt-and-suspenders second line.

### Day-44 continued: F7 Part B SETTLED on alias-velocity evidence

The pre-ship lift estimate that the scope doc §7 marked as "owed" is now **settled by direct observation, not estimate**:

- The backlog-helping alias sources (`bootstrap_league_coverage`, `operator_review`) are at **0 alias adds / 7 days**. Coverage work has been paused since the Day-40 strategic pivot. There's nothing fresh in the 3-day `last_seen_at` window for the loop to act on this week.
- The loop's value is a **multiplier**: it lifts records that NEW aliases would now help. With zero new aliases, multiplier × zero = zero flips — **by design, not by failure**.
- The `candidate_set_size=0` on both first live passes is the **direct confirmation** of this prediction. The selection logic is correct (Day-43 validated at 10,160 → 1 when there WAS a recent alias); the activity floor is zero by structural design.

The dispositive F8 validation is **targeted before/after**: stage a known addressable no_match record + add the alias that should fix it + watch the next 5-min pass flip it. This proves end-to-end correctness on a real flip without waiting for coverage to resume. Scoped for next session.

### Day-44 continued: Five crons running

| Cron | Cadence | Start | Status |
|---|---|---|---|
| `resolver-cron-fl` | `0 2 * * *` daily | `run_resolver_pass.py --provider fl --run-mode cron` | Pre-existing |
| `resolver-cron-kalshi` | `15 2 * * *` daily | `run_resolver_pass.py --provider kalshi --run-mode cron` | Pre-existing |
| `resolver-reresolution-fl` | `*/5 * * * *` | `run_reresolution_pass.py --provider fl --apply` | LIVE (validated) |
| `resolver-reresolution-kalshi` | `2-59/5 * * * *` | `run_reresolution_pass.py --provider kalshi --apply` | LIVE (validated) |
| `daily-diff` | `0 3 * * *` nightly | `daily_diff.py` | LIVE (first fire pending) |

### Day-44 continued: Phase-status header refreshed

Active-workstream line updated to "LIVE IN PRODUCTION" framing: the §7.6 / §7.7 re-resolution loop is no longer "built / perf-validated, crons-off"; it's "live, validated on first pass, F8 pending." First Phase 2 exit gate moved built → live. The previous Day-44 framing ("Crons stay off pending F7 Part B + #238 merge") is now stale — both gates cleared this session.

### Day-44 continued: PR state

- **#238 (`claude/reresolution-loop-scope`)** — **MERGED** to main. End of arc that started Day-40.
- This entry: PROJECT_STATE Day-44-continued journal + header refresh.

### Pending — next session

1. **Confirm daily-diff wrote a fresh row.** SELECT MIN/MAX `report_date` from `sp.daily_diff_reports`; a `2026-06-23` or `2026-06-24` row closes the Day-21 measurement-debt explicitly. If absent, check Railway logs for the 03:00 UTC nightly run + env vars on that service (it's the third service created post-env-var-catch, so should be clean, but verify).
2. **F8 VALIDATION — the dispositive test.** Stage a targeted before/after on a known addressable no_match record: identify a record with `reason_code='no_match'` whose `reason_detail` candidate teams include some `team_id` with no current alias; add the alias; watch the next 5-min loop pass flip the record to `reason_code IN ('strict', 'alias', 'fuzzy')` or `review_queue`. Proves the loop works end-to-end on a real flip. Passive flips are ~0 by design (F7 Part B settlement); a staged flip is the only honest end-to-end proof until coverage resumes.
3. **Remaining Phase 2 exit gates** (before Phase 3 `/api/v4` opens):
   - **Review-queue drain** — passive consequence of the loop running. Was 18,303 at Day-38; measure post-loop after F8 validates.
   - **§6.5 archival** — the last unbuilt Phase 2 exit gate. Object-storage move + retention policy for `sp.resolution_log` and the raw JSONB payload tables. Orthogonal to the loop; can scope-doc + build in parallel with F8.
4. **Carried-forward**: 9 pre-existing `test_phase_2d5_*` / `test_phase_2f1_*` collection errors (track so not blamed on Phase 2E).

---

## Session — 2026-06-22

### Day-44: Re-resolution loop — PERF-VALIDATED END-TO-END (attempt 4)

Attempt 3 surfaced the driver-set ceiling; attempt 4 bounded the candidate set by `last_seen_at` watermark; production re-measured 2.88s on both providers' dry-runs, comfortably under the 5s F6 halt ceiling. Loop is BUILT + foundation-deployed + perf-validated. Crons stay off pending F7 Part B and #238 merge.

### Day-44: The three reads opened the session

Read 1 — row counts. Surfaced the problem:
- `sp.fl_events` WHERE `fixture_id IS NULL`: **33,882**
- `sp.kalshi_markets` WHERE `fixture_id IS NULL`: **48,277**

Both large. The unresolved-provider set has no upper-bound semantics in the schema — old records that never resolved accrete indefinitely.

Read 2 — EXPLAIN ANALYZE attempt-3 (MATERIALIZED CTE) on warm cache:
- CTE Scan via `ix_fl_events_unresolved`: **540ms** ✓ (the partial index drives the CTE; attempt 2's seq scan stays gone)
- Inner LATERAL via `ix_resolution_log_provider_record_decided_at`: **13,654ms** ✗
- Total: **15.5s** — query-shape optimization EXHAUSTED.

The LATERAL is O(N_unresolved). N=33,882 × per-row Index Scan + LIMIT 1 = a multi-second budget no further query tweak fixes. The inner index is doing its job; the driver set is the bottleneck.

Read 3 was deferred — moving straight to candidate-bounding (the step-4 fork already designed in the Day-43 journal).

### Day-44: Candidate-bounding decision — last_seen_at watermark, 3-day window

**Choice between the two designs sketched in Day-43**:
- (a) Time-window on the LATERAL's `decided_at` — bounds by recency-of-prior-decision.
- (b) `last_seen_at` watermark on the CTE — bounds by **product relevance**.

**Decided: option (b).** Rationale captured for the journal: option (a) drops records whose latest decision was old but might still be product-relevant (the provider is still sending the record). Option (b) drops records the provider stopped sending — past/dead fixtures, zero product value. The blind spot is exactly the right blind spot.

**Window-size sized against production counts** (with attempt-3 implications):
- FL unresolved 33,882 → 7d 6,646.
- Kalshi unresolved 48,277 → 7d 11,299 → **3d 7,487**.

Kalshi is the binding constraint (larger unresolved set). 3d projects ~3s warm vs the 5s ceiling — comfortable margin. **2d and 3d are identical on Kalshi (both 7,487)**, so 3d costs nothing in latency over 2d but gives +24h correctness margin for alias-add events.

`RERESOLUTION_LASTSEEN_WINDOW_DAYS = 3` chosen as a tunable named module-level constant (single source of truth, baked into both TIER1 SQL strings via f-string at module load).

### Day-44: Correctness verification — `last_seen_at` write-path

Operator-flagged concern: if `last_seen_at` can go stale for a still-relevant record, the watermark would wrongly drop it. **Verified safe.**

`ingestion/base.py:207` sets `update_cols["last_seen_at"] = text("NOW()")` unconditionally on every UPSERT. Comment at line 139: *"last_seen_at always bumps."* Both FL (`ingestion/fl.py`) and Kalshi (`ingestion/kalshi.py`) route through `upsert_provider_records_batch`. **The watermark is reliable** — `last_seen_at` reflects the most recent provider poll regardless of whether the row's `raw_payload` changed.

### Day-44: Attempt 4 BUILT — script change + third CONCURRENTLY migration

Branch `claude/reresolution-loop-scope` head `441cd31`.

**Script change** (`scripts/run_reresolution_pass.py`):
- New named constant `RERESOLUTION_LASTSEEN_WINDOW_DAYS = 3`.
- Both `TIER1_SQL_FL` and `TIER1_SQL_KALSHI` rewritten as f-strings to bake the constant into the watermark predicate inside the MATERIALIZED CTE:
  ```sql
  WITH unresolved_fl_events AS MATERIALIZED (
      SELECT fl_event_id FROM sp.fl_events
      WHERE fixture_id IS NULL
        AND last_seen_at > NOW() - INTERVAL '3 days'
  )
  ```
- Module docstring updated with the semantic-narrowing note: **the loop now re-resolves only the last_seen-within-N-days slice, NOT the full back-catalog. The narrowing IS the design, not a known-incomplete shortcut.** Blind spot = past/dead fixtures with zero product value; F7/F8 value case was always about lifting current coverage, not historical resurrection. No periodic full sweep needed.

**New migration `c5e7f9a3b1d4`** (third Phase 2E CONCURRENTLY migration; third application of the console+stamp pattern):
- `ix_fl_events_unresolved_last_seen ON sp.fl_events (last_seen_at) WHERE fixture_id IS NULL`
- `ix_kalshi_markets_unresolved_last_seen ON sp.kalshi_markets (last_seen_at) WHERE fixture_id IS NULL`

Composite partial indexes that **exactly match the windowed CTE filter** — range scan over `last_seen_at` for unresolved-only rows, no heap fetch needed for `fixture_id`. The two alternative paths (existing `ix_*_unresolved` + heap-fetch each row for `last_seen_at`, OR existing `ix_*_last_seen` + heap-fetch each row for `fixture_id`) both lose; this composite is the deterministic fix.

The original `ix_*_unresolved` from initial schema stays in place — still used by `scripts/run_resolver_pass.py`'s daily cron filter on `fixture_id IS NULL`. Not exclusive to this loop.

**Tests** (+6, all DB-free): `TestTier1SQLShape::test_*_watermark_predicate_inside_materialized_cte` (positional check — CTE body contains nested parens `NOW()`, so regex couldn't span; switched to "predicate must appear before `JOIN LATERAL`"); `TestWindowConstant` (constant exists, value=3, baked into both SQL strings, positive int). 248 tests green.

### Day-44: Re-measure — PERF VALIDATED END-TO-END

EXPLAIN ANALYZE (warm) on attempt-4 FL query:
- CTE Scan via **`ix_fl_events_unresolved_last_seen`** (new) — **19ms**
- Inner LATERAL via `ix_resolution_log_provider_record_decided_at` — 2,100ms (over the bounded ~6.6k FL 3d-window driver set)
- **Total: 2.2s warm**

Dry-runs:
- **FL dry-run: 2.88s** — under the 5s F6 ceiling, **NO halt-criteria warning** (first clean run).
- **Kalshi dry-run: 2.88s** — same shape, same clean exit.
- `candidate_set_size = 0` on both this pass — correctly selective; no recent alias-adds to act on this 5-min window. (Selection logic was already validated Day-43 at 10,160 → 1; this pass just had no alias-add fresh enough to fire.)

End-to-end: loop selects the RIGHT records (Day-43) and selects them FAST enough (Day-44). The methodology pattern from Day-43 — **covering index alone is necessary but not sufficient (step 1) → LATERAL restructure (step 2) → MATERIALIZED CTE (step 3) → bound the driver set (step 4)** — is now complete and validated end-to-end.

### Day-44: Bookkeeping — alembic linear-history gap (pre-merge cleanup)

Surfaced during the production index inventory: `sp.alembic_version` stamped `c5e7f9a3b1d4` (Day-44), having jumped from `a2c4f6d8e1b3` (Day-42) **SKIPPING** `b3d5e7f9a2c4` (Day-43). The Day-43 `provider_record_decided_at` index was built via console but its `alembic stamp b3d5e7f9a2c4` apparently never ran.

**All three indexes physically EXIST in production** (verified via `pg_indexes` lookup). The query side is fully fine — `ix_resolution_log_provider_record_decided_at`, the two `ix_*_unresolved_last_seen` indexes, and the two Day-42 indexes all serve their queries. **Only the alembic linear history has a gap** — it never recorded `b3d5e7f9a2c4` as applied.

Mitigations in place:
- The migration's `IF NOT EXISTS` makes even a future fresh-DB upgrade safe (no duplicate-index error).
- The migration's docstring already says `upgrade()` body is "NOT INVOKED IN PRODUCTION — documentation + replay-against-fresh-DB only."

Flagged for the **pre-#238-merge cleanup** (next session). The fix is either `alembic stamp b3d5e7f9a2c4` retroactively (then re-verify head is `c5e7f9a3b1d4`) or annotate the upgrade() bodies of all three Phase 2E migrations as no-ops so any future main-based `alembic upgrade head` doesn't replay the env.py incompatibilities. Survey first before deciding which.

### Day-44: Phase-status header refreshed (this PR also)

The active-workstream line in the phase-status header updated from the Day-40 "decided to pivot to the loop" framing to the Day-44 "loop is BUILT + foundation-deployed + perf-validated" reality. Crons-off / F7-Part-B-pending / #238-merge-pending all explicit. Coverage-resequenced-behind-the-loop framing preserved (the loop is a multiplier; coverage worth more after it ships).

### Day-44: PR state

- **#238 (`claude/reresolution-loop-scope`)** — head `441cd31`. Scope doc + loop build + three migrations + four perf attempts (3 query-shape + 1 driver-bounding) + tests + railway.toml crons-flagged-off. **Open + unmerged.** Stays unmerged until F7 Part B lands AND the alembic linear-history gap is cleaned up.
- This entry: PROJECT_STATE Day-44 journal.

### Day-44: Path to live (next session — 5 steps)

1. **F7 Part B** — operator's pre-ship lift estimate (how many of ~16,588 expected to flip in the first Day-N+1 → Day-N+7 window). The measuring stick; required before crons.
2. **Pre-#238-merge cleanup**: settle the env-hostile `upgrade()` path. All three Phase 2E index migrations stamped via console; annotate / no-op the `upgrade()` bodies so a future main-based `alembic upgrade head` from a clean checkout doesn't replay the three failures. Same pass also resolves the skipped-`b3d5e7f9a2c4` linear-history gap.
3. **Enable crons** — un-comment the three `railway.toml` blocks; create the three Railway services (`resolver-reresolution-fl`, `resolver-reresolution-kalshi`, `daily-diff`). The LIVE step.
4. **Merge #238** — once 1+2+3 land.
5. **F8-validate over Day-N+1 → Day-N+7**: daily-diff trajectory (the just-wired cron starts flowing automatically) + targeted before/after on a known recent-alias-add population (the F7 Part A check from the scope doc).

### Day-44 carried-forward gates

- §6.5 archival (still separate Phase 2 exit gate; orthogonal)
- §7.5 review queue (18,303 at Day-38; re-measure post-loop expected)
- 9 pre-existing `test_phase_2d5_*` / `test_phase_2f1_*` collection errors (track so not blamed on Phase 2E)

---

## Session — 2026-06-20

### Day-43: Re-resolution loop — selection logic validated; candidate-query perf in 3-attempt fix arc

FL dry-run validated the selection LOGIC. The candidate-selection QUERY was too slow on three successive iterations; fix attempt 3 (MATERIALIZED CTE driver) shipped this session, pending operator re-measure. PR #238 stays unmerged. Crons stay off. Survey-first → scope-second → build-third → **measure-fourth** discipline held — three attempts measured against production, none assumed-correct.

### Day-43: Selection logic validated on first FL dry-run

Operator ran `python scripts/run_reresolution_pass.py --provider fl` against production. Result:

- Tier-1 returned ~10,160 records (vs the ~16,588 addressable ceiling from Day-41 sizing — sensible reduction by the `decided_at`-since-alias-add freshness filter at Tier-2 input).
- Tier-2 LOOSE alias-add OR fixture-state filter narrowed to **0-1 survivors** per pass.
- `candidate_set_size` well under the Day-41 ceiling — the loop selects the RIGHT records.

The Day-41 F1 four-condition rule + F1a LOOSE semantics are **production-validated**. The arithmetic of the loop's value proposition holds: per pass, a small targeted candidate set; the matcher is run on tens, not thousands; the alias-add signal correctly throttles.

### Day-43: Candidate-query perf — three-attempt fix arc

The selection logic is right; the query latency is the problem. Each attempt was measured against production via EXPLAIN ANALYZE warm cache; each surfaced the next bottleneck.

**Attempt 1 — add `ix_resolution_log_provider_record_decided_at`** (migration `b3d5e7f9a2c4`, console+stamp).

Hypothesis: the existing `ix_resolution_log_provider_record` covered the first two columns of `DISTINCT ON (provider, provider_record_id) ORDER BY decided_at DESC`, but `decided_at` was not in the index, forcing heap fetches + Incremental Sort. Adding a covering index `(provider, provider_record_id, decided_at DESC)` should let the planner read latest-decision in index order.

Outcome: **insufficient.** After `ANALYZE sp.resolution_log` the planner STILL chose Parallel Seq Scan + Sort on the whole `sp.resolution_log` (~5.3s scan + 0.9s sort). Index was ignored.

Root cause: the CTE+DISTINCT-ON shape pulled `reason_detail` JSONB across (essentially) every row. The planner correctly reasoned that heap-fetch-per-row was unavoidable, and a seq scan was cheaper than index-scan-plus-heap-fetch. **Not a stats problem; the query structure couldn't be saved by an index.** The new index stayed (it's needed for the next attempt) but was unused.

Migration path detail: `alembic upgrade` failed the same way as `a2c4f6d8e1b3` — both alembic CONCURRENTLY escape hatches incompatible with this repo's async env.py. Used the **second application of the console+stamp pattern** from the Day-42 lesson; built online via Neon console, `alembic stamp b3d5e7f9a2c4`. The docstring carried the verbatim runbook (`psql ...; alembic stamp ...; ANALYZE; EXPLAIN ANALYZE; re-run dry-run`) so the operator copied + ran in one pass. The CI guard test from Day-42 was generalized from a single-file check to a **glob-based scan of every `CREATE INDEX CONCURRENTLY` migration in the chain** — both Phase 2E migrations now pinned by the same test, asserting (1) no `autocommit_block()` call, (2) `COMMIT + execution_options(AUTOCOMMIT)` pattern, (3) CONCURRENTLY preserved, (4) `alembic stamp` runbook present in docstring.

**Attempt 2 — LATERAL Tier-1 rewrite** (commit `901f707`, branch `claude/reresolution-loop-scope`).

Hypothesis: the DISTINCT ON shape forced a full-table scan because Postgres had to materialize latest-decision-per-record across all `sp.resolution_log`. The cure is to drive from the (much smaller) unresolved provider table and per-row LATERAL the latest decision — that lets `ix_resolution_log_provider_record_decided_at` serve the LATERAL's `ORDER BY decided_at DESC LIMIT 1` directly (Index Scan + LIMIT 1 per outer row, no sort).

Outcome: **partial — 2.7s warm, still over the 5s F6 halt ceiling.** EXPLAIN ANALYZE attribution:
- Nested Loop (good — intended shape).
- Inner LATERAL: Index Scan using `ix_resolution_log_provider_record_decided_at`, LIMIT 1 — 1,104ms. **Working as designed.**
- Outer driver: **Seq Scan on `sp.fl_events`** — 1,519ms. **New bottleneck.**

Root cause: the planner was applying `fixture_id IS NULL` as a post-join filter on a Seq Scan of `sp.fl_events`, not as the access path. So the LATERAL ran once per FL event (resolved or not), not just the unresolved set. `ix_fl_events_unresolved` — the partial index `WHERE fixture_id IS NULL` from the initial schema — was ignored.

**Attempt 3 — MATERIALIZED CTE driver** (commit `ed2a44d`).

Hypothesis: wrapping the unresolved set in a CTE makes the planner compute it separately before any LATERAL work. **`MATERIALIZED` is non-negotiable** — PG 12+ inlines single-reference CTEs by default, which would put us back at attempt 2's seq-scan choice. The `MATERIALIZED` keyword forces a separate computation step; Postgres MUST scan `sp.fl_events` to build the temp result first, and `ix_fl_events_unresolved` (partial, `WHERE fixture_id IS NULL`) is the only access path for that scan.

Same shape on the Kalshi side with `unresolved_kalshi_markets` + `ix_kalshi_markets_unresolved`.

Outcome: **awaiting operator re-measure.** Expected plan:
- CTE Scan on `unresolved_*` driven by Index Scan using `ix_*_unresolved` (partial). No Seq Scan on the provider table.
- Per outer row: Index Scan using `ix_resolution_log_provider_record_decided_at` + LIMIT 1. No sort.
- Outer WHERE filters on `reason_code`, fail_reason allowlist, asymmetric_excluded per LATERAL output row.

### Day-43: Reusable lesson — expensive latest-decision queries on hot accreting tables

The arc surfaces a methodology pattern for any future "latest decision per (key1, key2) on a large accreting log table" query. Pin this so the next session doesn't re-derive:

1. **The covering index alone is necessary but not sufficient.** Adding `(key1, key2, ts DESC)` to a table that already has `(key1, key2)` is required for any index-order traversal, but won't help a query that pulls wide payload columns (JSONB) across most of the population — the planner will pick seq scan anyway.
2. **Restructure the query to minimize the outer-scan population.** Drive from the smallest filtered set; LATERAL the latest-decision lookup per row. The covering index then serves the LATERAL's `ORDER BY ts DESC LIMIT 1` directly. This is the move from "scan-most-of-table-and-dedup" to "scan-small-driver-and-point-look-up".
3. **Force the planner's hand on the outer driver.** Even with LATERAL, the planner may not use a partial index for the outer scan when the predicate is in the top-level WHERE. **`WITH unresolved AS MATERIALIZED (SELECT ... WHERE <partial-index-predicate>)`** is the explicit, non-rewriteable form. `MATERIALIZED` is mandatory under PG 12+.
4. **(Possible step 4, deferred) Bound the driver set with a time window.** If the unresolved-population set is itself tens-of-thousands large, N indexed LATERAL lookups may still exceed the latency ceiling. Two natural watermarks: `decided_at > NOW() - INTERVAL '<N> days'` on the LATERAL's resolution_log scan, OR `last_seen_at > NOW() - INTERVAL '<N> days'` on the CTE's provider scan. Don't build until evidence — survey-first → scope-second → build-third → **measure-fourth**.

### Day-43: Three attempt regression guards landed in CI

The three attempts now have static-source pin tests in `tests/test_reresolution_pass.py::TestTier1SQLShape`:

- `test_*_does_not_use_distinct_on` — attempt-1 guard. DISTINCT ON's planner-forced seq scan stays gone.
- `test_*_filters_fixture_id_null_inside_materialized_cte` — attempt-2 guard. Regex-checks the predicate lives **inside the CTE body**, not in the outer WHERE. A maintainer "simplifying" by hoisting it silently regresses to attempt 2's seq-scan.
- `test_fl_sql_uses_materialized_cte_for_unresolved_driver` — attempt-3 guard. `AS MATERIALIZED` is mandatory.
- `test_*_lateral_keys_on_*` — copy-paste guards. FL LATERAL keys on `u.fl_event_id`; Kalshi LATERAL keys on `u.ticker`. The provider tables differ at the PK column.

Plus the CI guard generalization from Day-42: every `CREATE INDEX CONCURRENTLY` migration in the chain is pinned by the same test (no `autocommit_block`, has COMMIT + AUTOCOMMIT pattern, CONCURRENTLY preserved, `alembic stamp` runbook present).

**242 tests green** (engine + dedup + merge + reresolution + the 4 new shape guards across attempts 1-3).

### Day-43: PR state

- **#238 (`claude/reresolution-loop-scope`)** — head `ed2a44d`. Scope doc with all 8 framing questions DECIDED + loop build + two migrations (`a2c4f6d8e1b3`, `b3d5e7f9a2c4`) + Option B → MATERIALIZED CTE attempts + tests + railway.toml crons-flagged-off. **Open + unmerged.** Stays unmerged until operator re-measure confirms attempt 3's warm latency drop.
- This entry: PROJECT_STATE Day-43 journal.

### Day-43: Pending — next session opens with three reads

All quick, all read-only:

1. **Row counts** — `SELECT count(*) FROM sp.fl_events WHERE fixture_id IS NULL;` + same for `sp.kalshi_markets`. This determines whether candidate-bounding (step 4 of the methodology pattern) is needed. The unresolved-provider set has no upper-bound semantics in the schema — old records that never resolved accrete indefinitely.
2. **EXPLAIN ANALYZE the attempt-3 FL query** on warm cache — confirm CTE Scan via `ix_fl_events_unresolved`, NO Seq Scan on `sp.fl_events`. Inner LATERAL via `ix_resolution_log_provider_record_decided_at` (unchanged from attempt 2).
3. **FL dry-run** — measure candidate-select latency vs the 5s F6 ceiling.

Branching from those reads:

- **IF latency clears**: loop validated end-to-end. Proceed to Kalshi dry-run, then F7 Part B pre-ship lift estimate, then un-comment `railway.toml` blocks + create the three Railway services. Crons go live.
- **IF row counts are 50k+ AND latency borderline**: make the candidate-bounding design decision. Two designs already sketched (time-window on the LATERAL's `decided_at` vs `last_seen_at` watermark on the CTE's provider scan); pick one based on which population shape better matches the unresolved-set decay curve, then build attempt 4.

### Day-43 carried-forward gates

- **F7 Part B owing**: operator's pre-ship lift estimate. Still the measuring stick before crons go live.
- **Daily-diff cron wiring**: still folded into the same railway.toml workstream; un-comments when the loop crons un-comment.
- **§6.5 archival**: separate Phase 2 exit gate; orthogonal.
- **§7.5 review queue**: 18,303 at last measurement (Day-38); re-measure post-loop expected.
- **9 pre-existing test collection errors** in `test_phase_2d5_*` / `test_phase_2f1_*`: track so not blamed on Phase 2E work.

---

## Session — 2026-06-19

### Day-41/42: Re-resolution loop — sizing + scope-doc closed + build + production indexes landed

First active work on the §7.6 / §7.7 three-loop runner (Phase 2 exit gate, decided Day-40 post-BBL fork). Survey-first → scope-second → build-third discipline held (same as Component 4). All 8 framing-question decisions closed on production evidence, loop built + tested (236 green), two indexes online in production. Crons NOT enabled — operator runs dry-run review before un-commenting `railway.toml`.

### Day-41: Sizing pass — production decomposition of the no_match backlog

Read-only production query at 2026-06-18 against `sp.resolution_log` (latest decision per `(provider, provider_record_id)`). The decomposition converted three scope-doc framing defaults into evidence-driven decisions.

Total unresolved no_match records: **35,831**. Three structurally-distinct populations:

| Population | Records | Loop-addressable? |
|---|---:|---|
| Loop-addressable (5 fail_reason categories — alias-add could flip): fuzzy_no_team_resemblance (non-prop) 10,856 + fuzzy_collision_no_anchor 3,179 + alias_no_team_resemblance 1,040 + below_review_threshold 886 + alias_resolution_incomplete 627 | **~16,588 (46%)** | YES |
| Prop contamination (`asymmetric_excluded='kalshi_prop_market'`, e.g. "Colorado: First Inning Run") | 4,200 (12%) | NO — not a team |
| Upstream failures (failed before team-matching): structural_normalize_failed 8,521 (Golf single-player) + sport_not_classified 3,941 (Esports/contaminants) + deferred_to_2d 2,528 (non-terminal artifact) + kickoff_confidence_below_threshold 53 | ~15,043 (42%) | NO — no team was a candidate |

**Latent prop-attachment forward-pointer** (logged so it isn't lost): the 4,200 `kalshi_prop_market` records relate to **real games** — "Shakhtar: First Half Winner" → a real Shakhtar fixture. The `home_canonical` side of the prop already identifies the fixture's home team. They're excluded from this loop because alias-resolution can't move them — but a future Phase 3+ **prop-attachment feature** (attach each prop to the fixture its real side already identifies) is a legitimate scope item. Recorded in the scope doc §1 Day-41 subsection.

### Day-41: All 8 scope-doc decisions DECIDED on production evidence (#238 updated)

- **F1 — DECIDED.** Four-condition candidate-selection rule: (1) `fail_reason` allowlist of 5 categories, (2) `asymmetric_excluded IS NULL`, (3) `fixture_id IS NULL`, (4) last decision was no_match AND a relevant alias-add OR fixture-state change since. Working set bounded at ~16,588 records max. The allowlist + prop-exclusion at the top of the filter chain prune the candidate set BEFORE the JSONB containment scan — structurally cheaper AND more correct than relying on alias-match to incidentally fail on the ~19,243 non-addressable records.
- **F1a — DECIDED: LOOSE.** Any new alias on any of the prior decision's candidate teams qualifies. The "loose × 288 passes/day sweeps the world" worry defused by F1's pre-filter bounding the working set. Upgrade path to strict is well-defined if measured cost surprises.
- **F2 — DECIDED: two indexes, one migration.** Partial expression btree on `(reason_detail->>'fail_reason') WHERE reason_code='no_match'` for the structural pre-filter (evidence-driven by the 54% selectivity finding) + GIN with `jsonb_path_ops` for Tier-2 containment. Both `CREATE INDEX CONCURRENTLY`. F2a — accept post-migration measurement (rollback is non-blocking `DROP INDEX CONCURRENTLY`).
- **F3 — DECIDED: confirmed as specced.** 5-min cadence; `resolver-reresolution-fl` at `*/5 * * * *`; `resolver-reresolution-kalshi` offset 2 min; `daily-diff` at `0 3 * * *` (45 min after the daily cron pair, closes Day-21 measurement-debt per F5). F3a — no quiet window.
- **F4 — DECIDED: no change.** Existing `ON CONFLICT (provider, provider_record_id) DO UPDATE WHERE status='pending'` clause at `run_resolver_pass.py:640` covers the re-resolution write path. F4a — same `sp.resolver_runs` view, filter by `run_mode` when desired.
- **F5 — DECIDED: daily-diff cron writes to `sp.daily_diff_reports`** (closes the Day-21 debt; the Day-33/34 measurement gaps in trajectory). F5a — write on every cron pass.
- **F6 — DECIDED: mirrors `run_resolver_pass.py` halt criteria.** Halt warnings on candidate_set_size > 5× trailing-7d mean / latency > 5s / GIN scan ratio < 80%. Exit 0 on warnings, exit non-zero only on hard failures. Hard limit 50k candidates → exit 4. F6a — `sp.resolver_runs` rows retained forever.
- **F7 — SHARPENED.** Three-part validation measures against the ~16,588 addressable denominator, NOT the gross 35,831 — measuring against the wrong denominator would understate the loop's effectiveness by ~54% mechanically. Aggregate `matcher_capability_rate` (35.3%) reported alongside but NOT the gate; it's denominator-suppressed by the ~19,243 structurally-non-addressable records.
- **F8 — DECIDED: deferred-seam set confirmed.** LISTEN/NOTIFY hot loop deferred to Phase 2E.fix (trigger: p95 > 5 min per `DEPLOYMENT.md:1152`); `--candidate-set` CLI mode designed-in; `--sport <id>` arg from day one; review_queue harvest out of scope; Phase 2D fuzzy tuning auto-picked-up via `TIERED_RESOLVER_VERSION` stamp.

### Day-42: Loop BUILT — migration + script + cron entries (flagged off) + tests

Branch `claude/reresolution-loop-scope` head `31dd97f`. 236 tests green (engine + dedup + merge + reresolution; 20 SP_INTEGRATION_DB-gated stubs).

- **`migrations/versions/20260619_1200_a2c4f6d8e1b3_phase_2e_reresolution_indexes.py`** — both indexes `CREATE INDEX CONCURRENTLY`, depends on `f1b3d5e7a9c2` (BBL `dedup_audit` chain). First CONCURRENTLY migration in the chain (see lesson below).
- **`scripts/run_reresolution_pass.py`** — mirrors `run_resolver_pass.py` shape. Two-tier candidate selection (Tier-1 SQL allowlist + prop-exclusion + fixture_id IS NULL + latest no_match; Tier-2 Python LOOSE F1a alias-add OR fixture-state). `run_mode='live'` on `sp.resolver_runs` writes. `--sport` per-sport restriction (F8 day-one seam). `--candidate-set provider:record_id,…` override (F8 LISTEN/NOTIFY seam). DEFAULT `--dry-run`; explicit `--apply` required + Pattern D pre-flight (Amendment #17). Reuses `TieredMatcher` bootstrap — no resolver-tier changes. F6 halt criteria + structured log per scope doc.
- **`railway.toml`** — three new cron service entries appended, **all commented out** ("flagged off pending operator dry-run review"). CI-guarded by `tests/test_reresolution_pass.py::TestStaticInvariants::test_railway_toml_crons_are_commented_off` — a maintainer can't accidentally un-comment without the test failing.
- **`tests/test_reresolution_pass.py`** — 48 pure-function tests + 7 SP_INTEGRATION_DB-gated stubs. Covers allowlist exactness, Tier-1 SQL shape, JSONB walker across all known team_id key shapes, Tier-2 loose semantics, F6 halt branches, F8 seam parsing, CLI safety, static invariants (railway.toml comment guard + the migration pattern guard added Day-42 — see lesson below).

### Day-42: Production indexes landed (via psql, not alembic)

Both indexes built online + stamped:

- `ix_resolution_log_fail_reason_no_match` — partial btree on `(reason_detail->>'fail_reason') WHERE reason_code='no_match'`. Tier-1 pre-filter index.
- `ix_resolution_log_reason_detail_gin` — GIN on `reason_detail` with `jsonb_path_ops`. Tier-2 containment index.
- `sp.alembic_version` stamped to **`a2c4f6d8e1b3`** post-build.

Online build, zero production downtime. `sp.resolution_log` (130k+ rows, hot-write table) carried writes throughout.

### Day-42: MIGRATION LESSON — first CONCURRENTLY migration in the chain, async env.py incompatibilities

Pinning this so the next CONCURRENTLY migration doesn't repeat the detour:

**The async env.py path through alembic is incompatible with both standard `CREATE INDEX CONCURRENTLY` escape hatches.** This repo's `migrations/env.py` runs the asyncpg engine via `connectable.connect()` + `transaction_per_migration=True` + `AsyncConnection.run_sync` sync-bridge. Two failure modes observed Day-42 in sequence:

1. **`op.get_context().autocommit_block()` fails on entry**: `AssertionError (assert self._transaction is not None, alembic/runtime/migration.py:329)`. alembic's per-migration `self._transaction` tracker doesn't reliably reflect the underlying DB transaction state through the sync-bridge — same class as the Phase 1A async-alembic commit gotcha that env.py's lifecycle comments (lines 102-112, 122-131) were written to address. Clean failure before any write; `sp.alembic_version` stayed at `f1b3d5e7a9c2`.
2. **`op.execute("COMMIT")` + `execution_options(isolation_level="AUTOCOMMIT")` fallback also fails**: `InvalidRequestError: transaction already initialized`. SQLAlchemy 2.0's `execution_options(isolation_level=…)` won't switch isolation on a connection whose transaction state is still considered open through the sync-bridge — even after a raw `COMMIT` SQL has gone through the wire.

**Resolution (used Day-42, established for future CONCURRENTLY migrations)**: skip the alembic-upgrade path entirely for CONCURRENTLY DDL. Build the indexes directly via psql / Neon console, then `alembic stamp <rev>` to record the revision without running `upgrade()`.

The migration's docstring carries the operator-runbook fallback verbatim:

```bash
psql "$DATABASE_URL" -c "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_resolution_log_fail_reason_no_match ON sp.resolution_log ((reason_detail->>'fail_reason')) WHERE reason_code = 'no_match';"
psql "$DATABASE_URL" -c "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_resolution_log_reason_detail_gin ON sp.resolution_log USING gin (reason_detail jsonb_path_ops);"
alembic stamp a2c4f6d8e1b3
```

**What made this a 20-min detour, not a block**: the migration was shipped with both the failed-pattern attempt AND the operator-runbook fallback in its docstring, AND a CI guard test (`TestStaticInvariants::test_migration_uses_repo_concurrently_pattern_not_autocommit_block`) that pins the migration's pattern shape. When the alembic path failed, the runbook was on disk in the migration file itself; operator copied + ran + stamped in one pass. The CI guard prevents a future maintainer from "cleaning up" by reintroducing `autocommit_block()` or dropping `CONCURRENTLY`.

**Forward-pointer for the next CONCURRENTLY migration**: don't bother trying `alembic upgrade head` for the DDL itself. Go straight to the psql + stamp path. The migration file is then a stamping target + documentation source, not an execution path. Same pattern as production-truth-test discipline (Amendment #18) — the alembic path is a hypothesis; the psql path is the verified mechanism.

### Day-41/42 PR state

- **PR #238 (`claude/reresolution-loop-scope`)** — UPDATED (head `31dd97f`): scope doc with all 8 framing questions DECIDED + loop build + migration + tests + railway.toml crons-flagged-off. Open + unmerged. Operator runs dry-run review before any merge.
- This entry: PROJECT_STATE Day-41/42 journal — operator-reviewed.

### Pending — next session

1. **Dry-run per provider** — `DATABASE_URL=<neon> python scripts/run_reresolution_pass.py --provider fl` (then `--provider kalshi`). Reads the new indexes, writes nothing. Sanity-check `candidate_set_size` against the ~16,588 ceiling per provider. Reconciles the indexes-are-being-used assumption (EXPLAIN ANALYZE in the SP_INTEGRATION_DB tests is the integration-side check).
2. **If shape's right**: enable crons. Un-comment the three `railway.toml` blocks, create the three services in the Railway dashboard. First `--apply` goes via the cron itself; `sp.resolver_runs` rows with `run_mode='live'` appear within 5 min.
3. **F7 Part B owing**: operator's pre-ship lift estimate for the addressable-set flip rate over Day-N+1 → Day-N+7. This is the measuring stick before the loop's effectiveness can be evaluated. Recorded as "outstanding operator owings" in the scope doc §8.
4. **Carried Phase 2 exit gates** (still open after the loop ships): §11.3 daily-diff cron starts flowing the moment the loop's `railway.toml` block is uncommented (F5 folded-in piece); §6.5 archival; §7.5 review queue (18,303 → measure post-loop).
5. **Carried earlier**: 9 pre-existing test_phase_2d5_* / test_phase_2f1_* collection errors (track so not blamed on Phase 2E work).

---

## Session — 2026-06-17

### Day-40: BBL Component 4 F7-validated — first MERGE confirmed in production behavior

F7 at ~14h post-merge (2026-06-16T21:59Z → 2026-06-17 ~12:00 UTC). country_code='DEU' team_id JOIN per Amendment #20.

**Positive check — merged winners resolving cleanly**: Hamburg vs Wurzburg, Rostock vs Ludwigsburg both appearing under the winner canonicals. Lift visible on the absorbed fixtures.

**Critical negative check — PASSES**: zero loser canonicals (Rasta Vechta, Rostock Seawolves, Hamburg Towers, MLP Academics Heidelberg) appear in production resolutions. No fragmentation reintroduced. Covers ALL 4 pairs, regardless of per-pair volume.

Vechta / Heidelberg windows had no resolutions yet — early-window + BBL season-tail artifact (same low-but-valid pattern as the off-season F7s Day-35/36 EuroLeague + ABA workstreams). Low volume on the positive side is NOT a defect; the negative check is the dispositive guarantee that the MERGE preserved canonical integrity.

**BBL workstream #10 closed end to end** — first FL-universe-engine-driven workstream + first MERGE in program history, now confirmed in production BEHAVIOR (not just sp.dedup_audit row counts).

### Day-40: POST-BBL FORK DECIDED — pivot to Phase 2 exit plumbing

The strategic fork carried since Day-38 is **resolved**: the next active workstream is the **§7.6 / §7.7 re-resolution loop**, not another league bootstrap. Three-point reasoning, recorded here so it is not re-litigated next session:

1. **Coverage breadth is past peak leverage toward the product.** Basketball 67.5%, baseball / soccer 70%+ — good enough to ship. Aggregate matcher capability 35.3% is denominator-suppressed by near-zero-coverage long-tail sports, not by the headline team-path sports (Amendment #20). The next league moves the wrong needle.
2. **The re-resolution loop is the highest remaining lever AND a Phase 2 exit gate.** Per the phase-status header — §7.7 three-loop runner is one of the four gates blocking Phase 3. It also retroactively compounds across all 39 days of prior coverage work (every previously-no_match record that NOW has an alias gets re-swept). The 18,303-row review queue is a labeled-alias backlog the loop unlocks. One workstream, two gates progressed.
3. **The finish line is the product.** Resolver accuracy is a critical INPUT to the product, not a substitute for it. The header's standing rule — track against v1.4 §11, not coverage vocabulary. Phase 3 cutover needs `/api/v4` + tolerable queue + measurement; the loop moves us toward all three.

### Day-40: coverage is RESEQUENCED, not abandoned

Recording this explicitly: **the league-bootstrap workstreams are not closed.** Coverage is sequenced *behind* the re-resolution loop because the loop is a multiplier — built first, it retroactively re-resolves all existing coverage AND multiplies the value of every future bootstrap (a new league lands once; its alias deltas keep re-sweeping previously-stuck records on every subsequent loop tick). Coverage resumes once the loop ships; each future league is then worth more than it would have been today. This is sequencing for leverage, not abandonment.

### Day-40 PR state

- BBL workstream #10: closed (PR #236 merged Day-39, F7 validated Day-40).
- This entry: PROJECT_STATE Day-40 journal — operator-reviewed.

### Pending — next session

1. **Re-resolution loop — scope build** from the Day-40 machinery survey (separately reported; covers what exists vs. what §7.6 / §7.7 specifies). Survey informs build scope.
2. **Carried Phase 2 exit gates**: §11.3 daily-diff cron wiring (open since Day-21), §6.5 archival, §7.5 review queue health (18,303 pending).
3. **Phase 3 cutover scaffolding** — `/api/v4` is not started; flag scaffold not present. Stays gated behind the loop + queue gates per the header.
4. Coverage resumes after the loop. Carried candidates from prior pending lists stay parked, not closed.

---

## Session — 2026-06-16

### Day-39: BBL Component 4 — first MERGE in program history, APPLIED + VERIFIED

BBL workstream #10's four MERGE-required FK-cascades (deferred from Day-38) applied to production and verified. This is the first MERGE the program has ever run — both sides of each pair carried live fixtures (Amendment #25 MERGE fork), partly-irreversible. BBL workstream #10 — the first workstream to carry a MERGE component — is now fully complete (Components 1-3 additive, F7-validated Day-38/39 morning; Component 4 applied Day-39).

### Day-39: BBL Components 1-3 F7 validation (clean)

F7 at ~14h post-apply (2026-06-15T20:18Z → 2026-06-16 ~10:00 UTC). country_code='DEU' team_id JOIN per Amendment #20. Both INSERTs resolving (Telekom Baskets Bonn, Ratiopharm Ulm); Amendment #26 Bamberg flip validated in production (Bamberg Baskets resolving under legacy canonical); BACKFILLs + 7 re-homed phantoms (Wurzburg, Bayern, Syntainics MBC, Frankfurt, Trier, Hamburg) all resolving clean. Confirmed Components 1-3 landed before introducing the irreversible Component 4.

### Day-39: Component 4 machinery survey — tested merge primitive EXISTS (not scoped-and-deferred)

Pre-cascade survey overturned the Day-38 assumption that the Tennis-dedup machinery might be unbuilt. Findings:
- `scripts/tennis_dedup.py:563` `merge_cluster()` — tested (40→44 unit tests), production-battle-tested (drove the +8.62pp Tennis lift Day-29). Single-transaction-per-merge, 5-step FK cascade (alias reparent → fixtures re-point → review_queue JSONB swap → audit INSERT → loser DELETE). The low-level cascade is sport-agnostic; only the cluster-DETECTION layer is Tennis-specific.
- `sp.dedup_audit` rollback table EXISTS in production — alembic head confirmed `f1b3d5e7a9c2` via `sp.alembic_version` (note: alembic_version lives in `sp` schema, not public — bare query errors). pre_state JSONB snapshot + `rolled_back_at` column + `--rollback --audit-id` path. MERGE is reversible.
- Reuse path: construct MergeGroup objects directly from operator-confirmed (winner, loser) tuples, skip Tennis detection, call merge_cluster() per pair.

### Day-39: THREE Tennis-specific assumptions caught in dry-run (zero production impact)

The "reuses Tennis machinery" claim was sound at the cascade level but carried three Tennis-specific assumptions that did NOT transfer to BBL. All three surfaced at safe checkpoints (production inspection + dry-run), none reached a write:

1. **candidate_fixtures winner+loser collision** — 6 review_queue rows carry BOTH winner and loser in the same array (BBL city-stub/full-name pairs the resolver offered as co-candidates; Tennis player-merges don't produce this shape). Naive swap would double the winner_id. Step 3 of merge_cluster() confirmed naive (`tennis_dedup.py:671-681`). Fix: order-preserving dedupe hook (`WITH ORDINALITY + MIN(ord)`; NOT plain DISTINCT — `candidate_fixtures[0]`=anchored side, `[1:]`=trigram-ordered, load-bearing per matcher/admin/template invariant chain).
2. **merge_cluster owns its own transaction** — no external seam to inject the dedupe in-transaction. Fix (operator-chosen Option 1 over replicate-cascade or accept-atomicity-gap): additive `post_review_queue_swap_hook` kwarg on `merge_cluster()`, called same-session after Step 3 swap before Step 4 audit (audit `pre_state` captures original pre-swap state — rollback authoritative). Tennis callers default None → zero behavior change, pinned by parity tests.
3. **`load_team_rows` hardcoded `WHERE s.code = 'tennis'`** (`tennis_dedup.py:349`) — returned empty for BBL teams, dry-run aborted "winner not found" though team demonstrably existed. Fix: parameterized `sport_code` kwarg (default `'tennis'`); merge_bbl passes `'basketball'`.

### Day-39: scripts/merge_bbl.py built + 4-gate verification + apply

Wrapper around `merge_cluster()` with the dedupe hook. Branch `claude/bbl-seed-workstream10` (head `20438ac`). 188 tests green (44 tennis_dedup + 24 merge_bbl + engine). CLI defaults to `--dry-run`; `--apply` requires `--merge-pr` for `sp.dedup_audit` provenance; Pattern D enforced.

Four gates all passed before apply:
- Caveat 1 (rollback table): `sp.dedup_audit` live at head `f1b3d5e7a9c2`.
- Caveat 2 shape: `candidate_fixtures` team-id-shaped (confirmed via production inspection).
- Caveat 2 collision: 6 rows winner+loser co-occur → dedupe hook fires.
- `load_team_rows` scope: fixed to basketball.

Dry-run reconciled exactly with hand-verified production state (fixtures 8/3/1/2, 6 collision rows, correct losers). APPLY at 2026-06-16T21:59Z, 18.0s, all 4 pairs committed:

| Winner | absorbed | loser DELETEd |
|---|---|---|
| Vechta (`87d4c8c9`) 9→17 fix | 8 | Rasta Vechta (`74e4e1e2`) |
| Rostock (`1b81310d`) 5→8 fix | 3 | Rostock Seawolves (`3aa87552`) |
| Hamburg (`09624eed`) 3→4 fix | 1 | Hamburg Towers (`76f717ca`) |
| Heidelberg (`36cf720f`) 3→5 fix | 2 | MLP Academics Heidelberg (`29b00c01`) |

Post-apply verification (read-only): 4 `sp.dedup_audit` rows present, `merge_phase='phase_b'`, `merge_pr='claude/bbl-seed-workstream10'`, `rolled_back_at=NULL` (all active). 4 losers gone from `sp.teams`; 4 winners absorbed fixtures exactly (Vechta 17, Rostock 8, Hamburg 4, Heidelberg 5). 14 fixtures re-pointed, 6 review_queue rows deduped, 4 aliases reparented.

### Day-39: methodology note — "reuses tested machinery" is a claim to verify, not assume

The Component 4 cascade DID reuse the tested Tennis primitive — but three Tennis-specific assumptions rode along with it, each of which would have corrupted production data if trusted blind. All three caught in read-only checks / dry-run. The discipline that worked: survey the primitive's actual code, inspect production data against its assumptions, dry-run, reconcile dry-run against hand-verified state, THEN apply. Highest-blast-radius operation in 38 days, zero production missteps. Pattern: a tested primitive transfers its tested CORE but not its caller's domain assumptions — verify every seam against the new domain.

### Day-39 PR state

- `claude/bbl-seed-workstream10` (head `20438ac`): `bbl_seed.py` + `bootstrap_bbl.py` + `merge_bbl.py` + `tennis_dedup.py` additive hook + tests. READY FOR PR — bundles full workstream #10 per convention.

### Pending — next session

1. **BBL Component 4 F7** — opens ~2026-06-17 ~12:00 UTC (~14h post-merge). Confirm merged canonicals (Vechta/Rostock/Hamburg/Heidelberg) resolve cleanly, no fragmentation reintroduced, losers stay gone.
2. **Open + merge PR** for `claude/bbl-seed-workstream10`.
3. **THE post-BBL strategic fork** — BBL workstream #10 now fully closed (additive + MERGE). The deliberate decision: keep widening coverage (more leagues) vs. pivot to Phase 2 exit plumbing (re-resolution loop → v4 endpoint → queue drain). Per the phase-status header accuracy note + Day-38 numbers conversation, everything points at the pivot — re-resolution loop is the highest lever AND a Phase 2 exit gate. Make this call fresh, deliberately, not by defaulting to another bootstrap.
4. Daily-diff cron wiring (open since Day-21); §6.5 archival; review_queue 18,303.

---

## Session — 2026-06-15

### Day-38: FL-universe engine MERGED (PR #231) + Academy boundary persisted + BBL workstream #10 additive apply (Components 1-3)

Three substantive landings this session: the FL-universe automation engine merged to main, the Stochverse Academy scope boundary persisted as a durable cross-session block, and BBL workstream #10's additive components (manifest + Bamberg flip + ALIAS-LINK phantom-releases) applied to production. The 4 MERGE-REQUIRED FK-cascades (Component 4) were deliberately deferred to a separate session — first MERGE in program history, machinery-survey-first.

### Day-38: FL-universe engine merged — PR #231

`claude/fl-universe-engine` head `8e40658` → main. Title: "FL-universe engine: Components 1-3 + fragmentation + Amendment #26 classifier (additive tooling, no apply path)".

Full diff walked against a 5-point pre-merge checklist, all passed:
1. **Additive-only** — `git diff --name-status` showed 10 files all `A` (added), 0 modified/deleted.
2. **No write paths in engine code** — grep confirmed only emitted DDL (phantom-release DELETE written to markdown as operator-run text); the two real writes (INSERT/UPDATE) live in pre-existing untouched files (resolver/fixtures.py, resolver/alias_tier/matcher.py), not in the merge surface.
3. **`classify_team_pure` two-pass design** — exact-match first, then `_reconcile_distinctive` (Amendment #26); hit → BACKFILL-CANDIDATE not BACKFILL; production caller passes the distinctive index.
4. **Distinct-entity guard** (`resolver/fragmentation.py`) — `_has_distinct_entity_marker` = OR of reserve-marker (U15-U24/Espoir/Reserve/Junior/trailing II/B) + gender-marker (Women/Femenino/Femminile/Féminin/Damen/Kobiet/trailing W), wired as asymmetry test `if fl_has_marker != sp_has_marker: continue` — blocks senior-vs-reserve / men-vs-women over-match.
5. **Test count** — 124 tests / 5 files (19+16+5+60+24).

10 files: resolver/collision_audit.py, resolver/fragmentation.py, resolver/text_match.py, scripts/harvest_aliases.py, scripts/fl_universe_batch.py + 5 test files. Amendment #26 was production-validated this session via the BBL Bamberg flip (see below). 9 pre-existing test collection errors in tests/test_phase_2d5_* / test_phase_2f1_* noted as UNRELATED — predate the branch.

### Day-38: Stochverse Academy scope boundary persisted to main

Operator started a parallel Claude Code session for Stochverse Academy (`academy.stochverse.com`) — bilingual educational resource, separate repo, independent deploy (likely Astro on Vercel/Cloudflare Pages), no shared infrastructure. A `## Scope boundaries (durable, cross-session)` block was prepended ABOVE the 2026-06-12 session header in PROJECT_STATE.md (so it survives context compaction), committed via `claude/project-state-academy-boundary` (`474965c`) and merged to main. Captures both directions: (a) Main→Academy: no blog/CMS in FastAPI, no routes/models/templates for educational content, no frontend bundle changes, no subdomain/reverse-proxy logic; (b) Academy→Main: no copying sp.* schema, resolver logic, v1.5 amendment methodology, or production data into Academy without explicit operator go-ahead. Standing rule: flag-and-log Academy-adjacent items, don't act. (Operator handles the clean-shell discipline — Academy session runs with no production env vars so it can't reach Neon by accident; chat Claude can't enforce this.)

### Day-38: BBL workstream #10 — additive apply (Components 1-3 of 4)

BBL is the first FL-universe-engine-driven workstream and the first workstream to carry a MERGE component. It decomposes into FOUR distinct apply components, NOT one atomic operation. Components 1-3 (additive, well-rehearsed shapes) applied this session; Component 4 (the 4 MERGEs) deferred.

**Pre-apply prep:**
- **Pattern A.2 discovery** against sp.resolution_log (Basketball, 7-day, BBL regex): real BBL forms are bare-city (already in manifest) plus asterisk variants `Bonn *` / `Bayern *`. Two key findings: (a) **3x3 contaminant** — `Bonn 3x3` / `Ub 3x3` etc. are FIBA 3x3, a DIFFERENT SPORT; must NOT be aliased. Verified safe via `distinctive_tokens('bonn 3x3')` → `('bonn','3x3')` (keeps 3x3 as discriminating token; no collision with bare `bonn`). (b) Sponsor/full-name tokens (telekom/ratiopharm/brose/niners/seawolves/towers/academics) did NOT appear in production — FL sends bare-city.
- **Manifest finalized** → `scripts/bbl_seed.py` (branch `claude/bbl-seed-workstream10`). Engine draft (`seed.py.draft`) had 2 `TODO_OPERATOR_FILL` INSERT canonicals (Amendment #24: FL gives structure not identity). Filled: Bonn → "Telekom Baskets Bonn", Ulm → "Ratiopharm Ulm". Initially added `Bonn *` / `Bayern *` asterisk aliases, then **STRIPPED them** after verifying `normalize_name('Bonn *')` → `'bonn'` (normalizer strips the asterisk entirely → asterisk aliases are no-ops, the bare form already resolves starred provider strings via normalized-key match). Reframes the Italian LBA Day-30 asterisk finding: no explicit asterisk alias needed. Final manifest: 17 teams (2 INSERT + 15 BACKFILL), one bare alias each.
- **bootstrap_bbl.py generated** — Claude Code mirrored bootstrap_vtb.py (three-branch classifier, PR #200 alias-safety INSERT...WHERE NOT EXISTS, shared _check_pattern_d_endpoint, --dry-run). Claude Code cannot run the production dry-run (sandbox has no DATABASE_URL — Amendment #18 honesty held); operator ran it locally.
- **Amendment #22 pre-apply audit** — clean (0 collisions). The 7 phantom team_ids passed as excluded_team_ids; all 7 verified fixture_count=0 (ALIAS-LINK safety gate). aliases_audited.md correctly reported all 7 phantom-canonical aliases as "colliding" PRE-release (each phantom owns its own canonical) — a sequencing assertion, not a defect; clean post-Part-1.

**Component 1 — manifest apply** (2026-06-15T20:18Z, bootstrap_bbl.py, 7.1s, 0 errors):
- 2 INSERT: Telekom Baskets Bonn (`af3f14bc`), Ratiopharm Ulm (`be45bfcc`), both country_code=DEU
- 15 BACKFILL country_code=DEU onto Phase 2A.5 stubs
- 17 aliases, skipped_global_conflict=0 (script's within-run check agreed with the SQL audit). existing_teams_loaded 2042 → sp.teams Basketball 2044 post-apply.

**Component 2 — Bamberg candidate→confirmed BACKFILL** (out-of-band, not in manifest):
- Bamberg classified BACKFILL-CANDIDATE by the engine (Amendment #26 fuzzy-reconciliation: bare "Bamberg" under-matches legacy canonical "Bamberg Baskets"). Flipped to confirmed BACKFILL onto `7370e1f3`: country_code=DEU + `bamberg` alias added. **First production validation of Amendment #26.**
- Surfaced the first hand-written-INSERT schema fact: `sp.team_aliases` has a NOT-NULL raw `alias` column (text, no default) alongside `alias_normalized`. Initial INSERT failed (23502); corrected with both columns. (Prior 9 workstreams never hit this — they inserted aliases via the bootstrap script, which carries the full column set internally.)

**Component 3 — 7 ALIAS-LINK phantom-releases** (two-part, order-mandatory):
- Part 1: DELETE 7 zero-fixture dormant phantoms (a13dd3fb Löwen Braunschweig, 3a70071e Fitness First Würzburg, bdb22a1c Bayern München, f9d5b8cc MHP Riesen Ludwigsburg, 0f20ba32 Science City Jena, 65ca6885 EWE Baskets Oldenburg, 060beffc BV Chemnitz 99). All 7 DELETE 1; team_aliases cascaded (Bayern München carried `bayern munchen` under two sources — both cascaded with the one team DELETE). Re-verified zero phantoms remain + phantom aliases cascaded clear.
- Part 2: 7 canonicals re-homed as aliases on winners (full column set per the Bamberg lesson).
- **Post-apply Amendment #22 audit: 0 collisions** across the full BBL alias surface (7 re-homed + Bamberg + 17 manifest aliases all team_count=1).

**baseline_shifts annotation** — event_type=`phase_2d5a_bbl_bootstrap`, event_date=2026-06-15. Amendment #19 idempotency pre-flight (0 rows). Surfaced the second hand-written-INSERT schema fact: `sp.baseline_shifts` has a NOT-NULL `affected_population` column (initial INSERT failed 23502; corrected). Notes field flags Component 4 as PENDING.

### Day-38: hand-written-INSERT schema-surprise pattern (two instances)

Both schema surprises this session (team_aliases `alias`, baseline_shifts `affected_population`) share a shape: the prior 9 workstreams wrote these rows THROUGH scripts that carry the full column set internally, while this session wrote several by hand (Bamberg flip, phantom-release alias adds, the annotation). Every direct INSERT needs its schema verified first (information_schema.columns) rather than written from assumption — the Amendment #12/#18 discipline applied at the column-set granularity. Relevant forward: Component 4's dedup cascade also writes by hand. No new amendment; covered by #12/#18.

### Day-38: Component 4 DEFERRED — the 4 MERGE-REQUIRED FK-cascades

Verified from fragmentation.md (read at apply time, not memory): 4 MERGE-REQUIRED pairs, each with BOTH sides carrying live fixtures (Amendment #25 fork):
| Winner | fixtures | Loser | fixtures |
|---|---:|---|---:|
| Vechta | 9 | Rasta Vechta | 8 |
| Rostock | 5 | Rostock Seawolves | 3 |
| Hamburg | 2 | Hamburg Towers | 1 |
| Heidelberg | 3 | MLP Academics Heidelberg | 2 |

14 loser-side fixtures total must be re-pointed (home + away) loser→winner before each loser DELETE. This is the FIRST MERGE in program history and the partly-irreversible operation. Deferred for two reasons: (1) the plan asserts "reuses Tennis-dedup FK-cascade machinery" but that machinery is UNVERIFIED — the Tennis dedup workstream was scoped (Day-22/25, sp.dedup_audit rollback table + two-phase batching) but it is NOT confirmed whether a reusable merge script was ever built or scoped-and-deferred. The correct first step for Component 4 is a code-survey, not live cascade SQL. (2) A clean F7 checkpoint on the additive work (1-3) before introducing the first production merge isolates "did the bootstrap land" from "did the merge land."

### Phase 2D.5-A / FL-universe status
- Phase 2D.5-A (9 manual basketball bootstraps): COMPLETE, F7-validated. Basketball capability 53.3% → 67.5%.
- FL-universe engine: BUILT, VALIDATED, MERGED (PR #231).
- BBL workstream #10: additive Components 1-3 APPLIED + verified; Component 4 (4 MERGEs) DEFERRED.
- v1.5 amendment pile: 26 items.

### Day-38 PR state
- PR #229 (retrospective + run-ahead docs), PR #230 (Day-36 pilot-eval): MERGED.
- PR #231 (FL-universe engine): MERGED.
- `claude/project-state-academy-boundary` (`474965c`): MERGED.
- `claude/bbl-seed-workstream10` (`bbl_seed.py` + `bootstrap_bbl.py`): pushed, NO PR yet — holding to bundle with Component 4.

### Pending — next session
1. **BBL F7 verification** — opens ~2026-06-16 10:00 UTC (~14h post-apply). country_code='DEU', team_id JOIN per Amendment #20. Expected: strict resolutions for the BBL teams now covered (Bonn/Ulm INSERTs, Bamberg + 15 BACKFILLs, 7 re-homed phantom canonicals).
2. **BBL Component 4 — 4 MERGEs.** Open with the machinery survey: does a tested dedup/merge-cascade script + sp.dedup_audit rollback table exist, or was it scoped-and-deferred? Survey FIRST, then stage cascade SQL. Highest blast radius in the program; first-ever MERGE.
3. **Open PR** for claude/bbl-seed-workstream10 (bundle with Component 4 per workstream convention).
4. **Daily-diff cron wiring** — open since Day-21; worth resolving before more engine runs to avoid measurement blackouts.
5. 9 pre-existing test collection errors (track so not blamed on #231).
6. Review_queue operator work (16,755 pending) + Tennis surname workstream (21.5% ceiling) — both architectural.

---

## Session — 2026-06-12

### Day-36 afternoon: FL-universe BBL pilot RUN + evaluated (operator-driven, read-only)

Operator ran `scripts/fl_universe_seed.py` from `claude/fl-universe-seed-pilot` against production FL API + Neon (read-only; no DB writes). First operator-driven run of the pilot.

Discovery finding: `--league-hint 'Bundesliga'` returned 0 candidates; `'Basketball Bundesliga'` returned 0; `'BBL'` returned 5 candidates. FL labels German top-flight basketball as "BBL", not "Bundesliga". The default `--league-hint` baked into the script (`'Bundesliga'`) is wrong for this league. Same class as Amendment #23 (heuristic assumption ≠ FL response reality).

Stage-rank fix (#23) confirmed working: 5 candidates surfaced — BBL Main [100,100], BBL Play-in [100,5], BBL Play Offs [100,5], DBBL Women Main [80,100], DBBL Women Play Offs [80,5]. Script correctly selected BBL Main (stage_id `rXnrx7Ca`, season_id `O6TXI3cK`); regular-season scored 100, knockout de-prioritized to 5, women's league scored lower on league-name (80) and was not selected. Standings returned 18 teams, no 404.

Roster result: 18/18 teams harvested, 15.8s runtime. Classification: INSERT=3 (Bamberg, Bonn, Ulm), BACKFILL=15, SKIP=0.

Authoritative-source verification (Wikipedia 2025-26 Basketball Bundesliga, amendment #13 discipline): roster count 18 confirmed exact — 0 missing, 0 phantom teams. FL structure layer (roster discovery, country=DEU population, FL crawl) validated as complete and correct.

### Day-36 afternoon: FALSE-INSERT discovered — pilot classification layer NOT production-safe

The 3 INSERTs were checked against production `sp.teams` via ILIKE/substring search (NOT exact normalized_name — the classifier already did exact-match and said "no match"). Result:

| FL INSERT | Production reality | Verdict |
|---|---|---|
| Bamberg | `sp.teams` stub "Bamberg Baskets" EXISTS (id `7370e1f3-faff-40be-9139-25075d40dd62`, Phase 2A.5, country_code NULL) | FALSE-INSERT — should be BACKFILL |
| Bonn | no stub under bonn/telekom (DEU) | genuine INSERT (authoritative: Telekom Baskets Bonn) |
| Ulm | no stub under ulm/ratiopharm | genuine INSERT (authoritative: Ratiopharm Ulm) |

False-INSERT rate: 1 of 3. Applying the pilot draft (`bbl_seed.py.draft`) as-is would have created a SECOND Bamberg team_id, fragmenting resolution (dormant-phantom failure mode).

Mechanism: FL sends bare-city form ("Bamberg" → normalized "bamberg"); legacy stub carries fuller canonical ("Bamberg Baskets" → "bamberg baskets"). Exact normalized_name comparison cannot catch the substring relationship (`bamberg ⊆ bamberg baskets`). FL bare-city names systematically UNDER-MATCH legacy canonicals that carry suffixes ("Baskets", "Towers", sponsor names) — and this hits the most prominent, most internationally-active clubs hardest (the ones legacy bootstrap stored under fuller names).

Epistemic note: `bbl_seed.py.draft` confidently labeled Bamberg "PILOT INSERT" with no UUID while production held a matching stub. The artifact looked authoritative but disagreed with production reality — same amendment #12/#18 shape (artifact verification over apparent-cleanliness). The 14/15 correct BACKFILLs are only as trustworthy as exact normalized_name matching happened to be; Bamberg proves the method can silently miss.

### v1.5 amendment #26 (NEW)

FL-universe automated INSERT/BACKFILL classification requires a fuzzy/substring reconciliation pass against existing `sp.teams` canonicals BEFORE apply. Exact normalized_name matching produces FALSE-INSERTs when FL's bare-city form is a substring of a fuller legacy canonical (Bamberg → Bamberg Baskets, Day-36 BBL pilot). The classifier's "no normalized match → INSERT" branch must be augmented: before emitting INSERT, run an ILIKE/substring/trigram check on the city/distinctive token against `sp.teams` (sport-scoped); any hit becomes a BACKFILL candidate for operator confirmation, not a silent INSERT. Pile expands 25 → 26.

(Numbering note: briefing-draft labeled this #25; the existing v1.5 pile already holds #25 — the Day-37 canonical fragmentation resolution rule. This finding takes the next slot, #26.)

### Day-36 afternoon: Engine-branch merge GATE

`claude/fl-universe-engine` (108 tests, Components 1+2 + orchestrator + cross-league dedup) MUST NOT merge until the fuzzy-reconciliation layer (amendment #26) is built into the classifier AND tested. Rationale: if a single pilot league produced a false-INSERT, generalizing the engine across all leagues propagates the failure mode at scale. The engine's INSERT/BACKFILL output is unsafe until #26 is addressed.

Scoped fix (engine branch, before merge):
1. Augment `classify_against_sp_teams`: add substring/ILIKE/trigram reconciliation pass on distinctive token before emitting INSERT. Hit → BACKFILL candidate (operator-confirm), not silent INSERT.
2. Re-run BBL pilot through the fixed classifier; assert Bamberg now classifies BACKFILL onto `7370e1f3`.
3. Add regression test: bare-city FL name vs suffixed legacy canonical → BACKFILL, not INSERT.

### Day-36 pilot evaluation — VERDICT

FL-universe pilot SUCCEEDS as a structure-harvesting tool, FAILS as an apply-ready autopilot. Structure layer (roster/country/crawl) is excellent and genuinely 40min → 16s. Classification layer (INSERT/BACKFILL) is NOT production-safe without the amendment #26 fuzzy-reconciliation pass. Decision: keep the engine as a front-end scaffold; build #26 fix before engine merge; BBL can proceed as workstream #10 manual-finished from the CORRECTED scaffold (flip Bamberg INSERT → BACKFILL onto `7370e1f3`; keep Bonn/Ulm as genuine INSERTs; run Pattern A.2 alias discovery; amendment #22 audit; apply; F7).

### Pending — Day-37 (updated)
1. Build amendment #26 fuzzy-reconciliation layer into `fl-universe-engine` classifier; re-validate BBL; THEN reconsider engine merge.
2. BBL workstream #10 manual-finish from corrected scaffold (optional — can wait for #26-fixed engine to regenerate clean).
3. Daily-diff cron wiring (Days 33-34 measurement gaps).
4. Review_queue operator work (16,755 pending, 9 lifetime processed).
5. Tennis surname workstream (21.5% ceiling).

---

## Session — 2026-06-10

### Day-37: Phase 2D.5-A retrospective committed

`docs/bootstraps/phase-2d5a-retrospective.md` — comprehensive close-out doc covering the 9 applied workstreams, institutionalized methodology runbook, amendments #12-#22 produced, three surprises (BACKFILL not prominence-correlated, collision discipline post-apply-mandatory, off-season F7 low-but-valid), the Day-36/37 automation pivot, Day-37 LOCKED fragmentation rule, and the next-phase decision.

Headline numbers (verified Day-37 via SELECT against production):
- `sp.teams` Basketball: 1,981 → **2,042** (+61 teams)
- 122 Basketball teams now carry `country_code`
- Basketball matcher capability: 53.3% → 58.6% (trough 44.6% Day-31, recovered +14.0pp as bootstrapped denominator lifted strict-tier resolutions)
- 9 workstreams / 8 baseline_shifts annotations (EuroLeague + ABA combined into one annotation row)

### Day-37: Fragmentation resolution rule LOCKED (Amendment #25)

Day-37 production analysis of 7 BBL fragmented pairs LOCKED the resolution rule:

> For each city-stub / full-name pair, compare fixture counts:
> - **One side has zero fixtures → ALIAS-LINK** (automatable). Canonical winner = the side WITH fixture history (Option A, fixture-history wins, per F1 production-anchor discipline). Full-name form becomes an alias on the live stub; dormant duplicate flagged as phantom (not deleted by automation).
> - **Both sides have fixtures → MERGE REQUIRED** (operator-driven, never auto-applied). Reuses Tennis-dedup FK-cascade machinery.

BBL distribution: 5 alias-link (Oldenburg, Ludwigsburg, Braunschweig, Würzburg, Syntainics MBC) + 2 merge-required (Rostock 5+3, Hamburg 2+1). The fixture-count fork is the safe automation boundary.

This generalizes to every league whose legacy `public.entities` accumulator captured both short and full forms — that's most of them.

### Day-37: Component 3 — fragmentation detection primitive built

`resolver/fragmentation.py` + `tests/test_fragmentation.py` (16/16 tests passing).

- Pure / impure split same pattern as `collision_audit` + `text_match`
- `find_fragmentation_candidates_pure` + `find_all_fragmentation_pairs_pure` + `classify_fragmentation_pair_pure`
- Encodes Day-37 LOCKED rule end-to-end
- Token-subset detection over distinctive-only tokens (reuses `resolver.text_match.distinctive_tokens`)
- Catches the 7 BBL pairs; correctly rejects Real Madrid vs Real Sociedad (no subset) and identical-distinctive duplicates (defer to collision audit)
- All 4 verdict shapes tested (ALIAS-LINK anchor-side / partner-side, MERGE-REQUIRED both-fixtures / both-zero degenerate)

**Engine test total: 54/54** (14 collision_audit + 24 text_match + 16 fragmentation).

**Component 3 batch orchestrator (`scripts/fl_universe_batch.py`) NOT YET BUILT** — fragmentation primitive ready to wire in.

### Day-36: EuroLeague + ABA F7 verification (off-season baseline)

F7 verification at ~14h post-apply (apply 2026-06-08T19:55:53Z → sample ~10:00 UTC Day-36):
- **9 strict resolutions / 7 distinct team-pairs**
- Multi-country JOIN filter spanning all 12 codes (MCO, DEU, FRA, LTU, SRB, MNE, BIH, SVN, CRO, AUT, ROU, UAE)
- Below 30-50 projection but EXPLAINED — EuroLeague and ABA seasons end May/June; off-season baseline. Not a methodology failure.
- Methodology note: off-season F7 volume is low but valid (Surprise #3 in retrospective §4).

### Day-36: Env var drop pattern RESOLVED

6th+ consecutive session was the breaking point. Resolved via PowerShell `$PROFILE` script export — `DATABASE_URL`, `EXPECTED_PRODUCTION_DB_NAME`, `EXPECTED_PRODUCTION_DB_HOST` now auto-loaded on session start. Per-team workstream friction eliminated.

### Day-36: Automation strategy decided — Path B durable engine

After 9 manual workstreams the question became: can we build the universe faster than one-league-at-a-time?

Claude Code conducted a survey of existing sports-data integrations + credentials + FL API capabilities. Result: **FL already exposes a full team-master-data API** (`/v1/tournaments/standings` for rosters, `/v1/teams/data` for canonical + country) — no external encyclopedia (Wikidata / TheSportsDB) needed.

Decision: Path B durable engine. Build automation that re-seeds from authoritative FL rosters rather than the provider-snapshot-bounded legacy accumulator. Three components:
- **Component 1**: collision audit (amendment #22 as tested function)
- **Component 2**: production-failure alias harvester
- **Component 3**: batch multi-league crawl + fragmentation detection

### Day-36: BBL pilot built + validated

`scripts/fl_universe_seed.py` (branch `claude/fl-universe-seed-pilot`) — German BBL proof-of-concept of the FL crawl pipeline.

Validation results:
- FL crawl returned **complete roster (18/18 BBL teams)**
- BACKFILL detection: **15/15 correct**, all verified `name_count=1`, `sport_id=3`, `country_code` NULL, Phase 2A.5 origin
- Clean country codes (Germany → DEU)
- **13s runtime** vs ~40 min manual

**LIMITATION (confirmed): FL canonical = provider short-form ("Bonn"), NOT a usable canonical_name.** It IS useful as an alias. Real canonicals still come from operator for INSERTs only; BACKFILLs keep their existing Phase 2A.5 canonical per F1 discipline. (Captured as Amendment #24.)

Stage-selection bug discovered + fixed: `/v1/tournaments/standings` only exists for league-table stages (Play Offs returns 404). Stage-rank heuristic now prefers regular-season stages; 404 fallback iterates remaining candidates. (Captured as Amendment #23.)

### Day-36: Alias harvester built + precision-tuned + validated against production

`scripts/harvest_aliases.py` + `resolver/collision_audit.py` + `resolver/text_match.py` (branch `claude/fl-universe-engine`).

Initial implementation over-proposed via false-positive fuzzy matches (Paris/Dubai/EBAA→Braunschweig class). Precision-tuning shipped:
- Distinctive-token matching (`resolver/text_match.py`) — strips generic sport tokens before fuzzy comparison
- Threshold default 0.75 → 0.85
- `--country-filter` flag with defensive country-hint extraction across 8 reason_detail key shapes
- Reference-forms quality warning (operator-supplied human-guessed forms surface real production strings that collide with separate Phase 2A.5 stubs — recommend FL-derived `SHORT_NAME` / `NAME` instead)

Production re-run validation (BBL, threshold 0.85, --country-filter Germany):
- 45,256 strings mined → **44,766 rejected below threshold** (pre-audit) → 14 candidates → 0 clean / 7 collision / 7 same-team
- Paris/Dubai/EBAA → Braunschweig false-positive class **structurally eliminated** — not the collision audit catching them, but the distinctive-token matcher upstream
- 7 remaining collisions are the canonical-fragmentation pattern (legitimate full-name BBL clubs) — surfaced the Day-37 LOCKED rule
- `clean=0` is the correct result for a well-covered league — harvester correctly refused to emit junk

**Engine test total at end of Day-36: 38/38** (14 collision + 24 text_match).

### Day-37: v1.5 amendment pile 22 → 25 (NEW: #23, #24, #25)

- **#23** — FL standings exist only for league-table stages, not knockout (Play Offs 404 → Main success); stage-rank heuristic prefers regular-season stages. Discovered Day-36 BBL pilot.
- **#24** — FL canonical = provider short-form, not canonical_name; FL automates structure (roster / BACKFILL / country), humans + authoritative sources own identity. Confirmed Day-36 BBL pilot.
- **#25** — Canonical fragmentation resolution: city-stub / full-name pairs route by fixture-count (alias-link if one side zero / merge-required if both); fixture-history wins canonical per F1 production-anchor discipline; merge never auto-applied. Locked Day-37 from BBL production analysis.

Pile expanded from 22 to 25 items.

### Pending — next-session agenda

1. **Component 3 batch orchestrator** — `scripts/fl_universe_batch.py`. Primitive done; orchestrator remains. Per Day-37 brief: enumerate basketball leagues via `/v1/tournaments/list?sport_id=3`, per league pick league-table stage (404 fallback), harvest roster, classify INSERT/BACKFILL/SKIP, run fragmentation primitive on BACKFILL/SKIP teams, run collision audit, emit per-league bundles. `--max-leagues` cap. NO auto-apply.
2. **Rostock + Hamburg BBL merges** (operator task, deferred). Reuse Tennis-dedup FK-cascade machinery. Blocks nothing.
3. **Daily-diff cron wiring** — still unaddressed since Day-21; Days 33-34 measurements permanently missing from sp.daily_diff_reports trajectory. Worth resolving before Component 3 ships to avoid measurement blackouts.

### Branches open, no PR yet

- `claude/fl-universe-seed-pilot` — FL pilot (BBL proof-of-concept) + stage-selection fix
- `claude/fl-universe-engine` — Components 1+2 + fragmentation primitive (Component 3 partial)

PR consolidation deferred per operator directive. This entry's PR is the only one open against `main`.

---

## Session — 2026-06-08

### Day-35 morning: VTB F7 verification

F7 count at ~14h post-apply (2026-06-05T20:17:21Z apply → 2026-06-08T~10:17Z sample):
- **21 strict resolutions / 6 distinct team-pairs**
- Apply timestamp filter: `decided_at >= '2026-06-05 20:17:21+00'`, `country_code='RUS'`

Per-team-pair breakdown:
- CSKA Moscow vs Lokomotiv Kuban: 6 resolutions
- CSKA Moscow vs UNICS Kazan: 4 resolutions
- Lokomotiv Kuban vs CSKA Moscow: 4 resolutions
- Lokomotiv Kuban vs Zenit Petersburg: 3 resolutions
- UNICS Kazan vs CSKA Moscow: 3 resolutions
- Enisey vs CSKA Moscow: 1 resolution

**5 distinct VTB manifest teams resolving**: CSKA Moscow, Lokomotiv Kuban, UNICS Kazan, Zenit Petersburg, Enisey. All domestic VTB fixtures — no EuroLeague crossovers (CSKA Moscow European participation limited in 2025-26). Both INSERT (CSKA Moscow) and BACKFILL (Lokomotiv Kuban, UNICS Kazan, Zenit Petersburg, Enisey) branches validated.

Lower than 50-100 projection but expected — VTB may be in post-season. F7 will grow as more cron passes run.

### Day-35 morning: Daily-diff — Basketball trajectory point 5

Measurement script run: `python scripts/daily_diff.py`
Window: 2026-06-07 → 2026-06-08, 15,908 records, runtime 567.91s

**Report date 2026-06-08: 35.7% capability (scope-filtered)**

**NOTE**: Days 33-34 cron measurements were missing from sp.daily_diff_reports — `daily_diff.py` is a manual-run script (Railway cron paused per Day-21 session). Days 33-34 measurements permanently absent from trajectory. Day-35 is the first new measurement since Day-31.

**Basketball trajectory point 5 (post-LBA + Israeli BSL + Turkish BSL + HEBA + VTB):**
- Day-29 (pre-LBA/BSL): 53.3%
- Day-30 (1d post-LBA): 51.5%
- Day-31 (post-Israeli BSL + Turkish BSL): 44.6%
- Day-35 (2026-06-08, post-HEBA + VTB fully in window): **58.6% (+14.0pp vs Day-31 trough)**

**+14pp Basketball lift confirms cumulative effect of workstreams #3-7 now fully in 7-day measurement window.** Denominator inflation hypothesis confirmed in reverse — bootstrapped leagues now contributing strict-tier resolutions.

Tennis: 15.5% → 25.6% (+10.1pp) — Tennis dedup lift re-emerging
Baseball: 71.6% → 72.0% (+0.4pp) — stable, denominator inflation stabilizing
Soccer: 68.4% → 70.7% (+2.3pp) — small positive drift

sp.resolution_log volume (latest cron): 116,609 rows (up from 102,603). Strict: 169. No_match: 101,671. Review_queue: 14,769.

### Day-35 morning: EuroLeague workstream #8 pre-scope Pattern A.2 discovery

Production discovery query confirmed EuroLeague residual is very small (~12-15 records/7d) — most EuroLeague teams already resolving via prior domestic workstream crossovers:
- **Already covered**: Anadolu Efes (#5 TUR), Barcelona/Real Madrid/Valencia/Baskonia (#2 ACB), Fenerbahçe (#5 TUR), Olimpia Milano/Virtus Bologna (#3 ITA), Maccabi Tel Aviv (#4 ISR), Olympiakos/Panathinaikos (#6 GRC), CSKA Moscow (#7 RUS)
- **Genuine gaps**: Monaco (~3/7d), BC Rytas Vilnius (~6/7d EuroCup crossover vs AEK), small residual

EuroLeague legacy stub verification: all 7 gap-fill teams exist as Phase 2A.5 stubs (Monaco, Bayern München, Lyon-Villeurbanne, Paris Basketball, Partizan Mozzart Bet, Zalgiris Kaunas, Rytas) — pure BACKFILL workstream.

### Day-35 morning: ABA League workstream #9 pre-scope Pattern A.2 discovery

Production discovery confirmed ~90-100 Basketball unresolved records/7d attributable to ABA:
- KK Partizan Belgrade: ~52/7d (dominant)
- KK Crvena zvezda Belgrade: ~28/7d
- KK Buducnost Voli: ~28/7d
- KK Bosna Royal Sarajevo: ~14/7d
- FC Universitatea Cluj (U-BT Cluj-Napoca): ~28/7d

All confirmed teams have Phase 2A.5 legacy stubs — predominantly BACKFILL workstream.

### Day-35 morning: EuroLeague #8 + ABA League #9 APPLIED (combined workstream)

Apply at **2026-06-08T19:55:53Z**. Runtime 14.1s, 0 errors. Combined into single PR per operator decision (both predominantly BACKFILL, low combined overhead).

**Apply results:**
- **4 INSERTs**: Dubai Basketball (UAE), SC Derby (MNE), Ilirija (SVN), U-BT Cluj-Napoca (ROU)
- **20 BACKFILLs from Phase 2A.5 Basketball stubs**: Monaco (092518ec), Bayern München (bdb22a1c), Lyon-Villeurbanne (5481c8e7), Paris Basketball (e4e0e605), Partizan Mozzart Bet (575ec0fc), Zalgiris Kaunas (a845d73b), Rytas (834075ed), Crvena Zvezda Meridianbet (a3d095e9), Buducnost (063a1204), KK Bosna (99368c5b), Cedevita Olimpija (e7cce709), Mega Basket (5ef0b126), Igokea (ea0cd454), KK Zadar (bb0da184), FMP Beograd (1337e0d0), Borac Mozzart (949c6254), BC Vienna (3c7275fc), KK Split (d7a6e58e), KK Krka Novo Mesto (0674ed89), Spartak Subotica (3c6aa492)
- 81 aliases inserted, 3 deduped within batch, 0 global conflicts
- `existing_teams_loaded`: 2,038 pre-apply → 2,042 post-apply
- **Multi-country**: SRB/MNE/BIH/SVN/CRO/AUT/ROU/UAE/MCO/DEU/FRA/LTU (12 country codes)

baseline_shifts annotation: row inserted (event_type=`phase_2d5a_euroleague_aba_bootstrap`, event_date=2026-06-08). Amendment #19 idempotency discipline applied.

### Day-35 morning: Pre-apply manifest fixes — 5 collision aliases + Dubai Basketball tuple

Amendment #22 pre-apply audit discovered 5 collision-causing aliases in `euroleague_aba_seed.py`:
1. `'Monaco Basket'` from Monaco — Monaco Basket (51a337b9) is separate stub
2. `'KK Student Igokea'` from Igokea — KK Student Igokea (707c2064) is separate stub
3. `'FMP'` bare from FMP Beograd — FMP Beograd U19 (42e58805) holds this alias
4. `'KK Borac'` from Borac Mozzart — KK Borac (26b9f2eb) is separate stub
5. `'Dubai'` bare from Dubai Basketball — Dubai (6b8852e4) holds this alias

Plus: Dubai Basketball alias tuple was malformed — `("Dubai Basketball")` is a Python string not a tuple; iterating over it produced empty space alias. Fixed to `("Dubai Basketball",)`.

Separate patch PR #227 (claude/euroleague-aba-seed-fixes) filed and merged.

### Day-35 morning: Post-apply collision audit — 6 dormant phantoms

Post-apply Pass 1 query revealed **6 collisions**:

| Alias | Manifest team | Dormant phantom |
|---|---|---|
| bayern | Bayern München | b4318e7f (Bayern) |
| bayern munich | Bayern München | b4318e7f (Bayern) via alias_tier |
| cluj napoca | U-BT Cluj-Napoca | 506aa215 (Cluj-Napoca) |
| dubai basketball | Dubai Basketball INSERT | 6b8852e4 (Dubai) via alias_tier |
| split | KK Split | fd5eb539 (Split) |
| zadar | KK Zadar | 8d626c4b (Zadar) |

6 individual DELETEs against `bootstrap_league_coverage` source on manifest team_ids. Zero-collision verification confirmed: 0 rows post-remediation.

**Notable**: `dubai basketball` collision reveals Dubai (6b8852e4) legacy stub already had alias_tier write-back for `dubai basketball` — suggesting FL previously sent Dubai Basketball records that partially resolved. Our INSERT created a separate team_id for Dubai Basketball; the alias_tier row on the legacy stub remains as dormant phantom routing the Dubai bare form to the old stub.

### Phase 2D.5-A status: ALL 9 WORKSTREAMS APPLIED ✅

- ✅ Workstream #1 (LMB): Day-28, F7 validated
- ✅ Workstream #2 (Liga ACB): Day-29, F7 validated
- ✅ Workstream #3 (Italian LBA): Day-31, F7 validated
- ✅ Workstream #4 (Israeli BSL): Day-31, F7 validated
- ✅ Workstream #5 (Turkish BSL): Day-31, F7 validated
- ✅ Workstream #6 (Greek HEBA A1): Day-33, F7 validated Day-34
- ✅ Workstream #7 (Russian VTB): Day-34, F7 validated Day-35 (21 strict / 5 teams)
- ✅ Workstream #8 (EuroLeague gap-fill): Day-35 apply 2026-06-08T19:55:53Z
- ✅ Workstream #9 (ABA League): Day-35 apply 2026-06-08T19:55:53Z (combined with #8)

**sp.teams Basketball: 2,042** (was 1,981 when Phase 2D.5-A started — +61 teams across 9 workstreams)

**Phase 2D.5-A is fully applied.** F7 for workstreams #8+#9 opens 2026-06-09T09:55:53Z.

### Pending — Day-36 morning agenda

1. EuroLeague + ABA F7 verification (opens ~2026-06-09T09:55:53Z). JOIN template with `country_code IN ('MCO','DEU','FRA','LTU','SRB','MNE','BIH','SVN','CRO','AUT','ROU','UAE')`. Expected: ~30-50 strict resolutions.
2. **Phase 2D.5-A retrospective** — now that all 9 workstreams are applied, document cumulative methodology findings and next-phase decision.
3. Env var drop small workstream — 7th+ consecutive session; decision needed (pick one option and implement).
4. Daily-diff cron wiring — consider automating `daily_diff.py` so measurement gaps don't recur.

---

## Session — 2026-06-05

### Day-34 morning: Greek HEBA A1 F7 verification

F7 count at ~14h post-apply (2026-06-04T23:04:07Z apply → 2026-06-05T~13:04Z sample):
- **25 strict resolutions / 21 distinct team-pairs**
- Apply timestamp filter: `decided_at >= '2026-06-04 23:04:07+00'`, `country_code='GRC'`

Per-team-pair breakdown:
- AEK Athens vs Aris Thessaloniki: 3 resolutions (playoffs ✅)
- Olympiakos BC vs Panathinaikos BC: 3 resolutions (playoffs ✅)
- PAOK BC vs Panathinaikos BC: 3 resolutions ✅
- Olympiakos BC vs Kolossos Rhodes: 2 resolutions ✅
- PAOK BC vs Peristeri: 2 resolutions ✅
- Mykonos vs Panathinaikos BC: 2 resolutions ✅
- Olympiakos BC vs AEK Athens: 2 resolutions ✅
- [7 additional single-resolution pairs]

**8 distinct HEBA manifest teams resolving**: AEK Athens, Aris Thessaloniki, Olympiakos BC, Panathinaikos BC, Kolossos Rhodes, PAOK BC, Peristeri, Mykonos.

EuroCup crossovers confirmed (4 non-GRC teams):
- Fenerbahçe (TUR manifest) vs Olympiakos BC ✅
- Valencia Basket (ESP manifest) vs Panathinaikos BC ✅
- Monaco vs Olympiakos BC ✅
- Unicaja / Unicaja Málaga vs AEK Athens ✅
- Rytas vs AEK Athens ✅

Teams not yet resolving (eliminated from playoffs): Iraklis BC, GS Karditsa, Maroussi BC, Panionios, Promitheas Patras BC Vikos Cola — consistent with Day-32 discovery showing these teams absent from 7-day window.

### Day-34 morning: Daily-diff

Render at 2026-06-05T19:23:34Z. Latest data point still report_date 2026-06-02 (34.7%) — Day-33/34 cron measurements not yet written. HEBA annotation (`phase_2d5a_heba_bootstrap`) now rendering correctly in baseline-shift-events section.

Basketball trajectory point 5 deferred to Day-35 render (HEBA applied 2026-06-04T23:04Z; VTB applied 2026-06-05T20:17Z — both too recent for current window).

### Day-34 morning: Russian VTB United League workstream #7 pre-scope Pattern A.2 discovery

Production discovery query against sp.resolution_log (no_match, Basketball, 7-day window):

| Provider string | Volume/7d | Notes |
|---|---:|---|
| BC Lokomotiv Kuban / Lokomotiv Kuban | ~105 | Two FL provider shapes |
| CSKA Moscow / CSKA Moscow * | ~75+ | Dominant — appears in almost every pair |
| BC Uniks Kazan / Unics Kazan | ~40 | Spelling variants |
| Khimki M. | ~42 | Out-of-roster (not on 2025-26 Wikipedia VTB roster) |
| Chelyabinsk | ~42 | OUT OF SCOPE — not on VTB roster, likely VTB.B regional |
| Enisey | ~7 | |

Total confirmed VTB volume: ~230+ records/7d (higher than Day-33 estimate of 42/7d which was single pair only).

Legacy stub verification query confirmed BACKFILLs: Lokomotiv Kuban (1dae39ae), UNICS Kazan (b1d198b0), Enisey (eef30d44), Zenit Petersburg (d639c09a), Parma Perm (a1973c38), Khimki M. (b2fbeb14), MBA Moscow (1f5f991a).

### Day-34 morning: Russian VTB workstream #7 APPLIED

Apply at **2026-06-05T20:17:21Z**. Runtime 8.3s, 0 errors. Pattern D pre-flight → amendment #22 audit → manifest fix → dry-run → wet apply sequence completed.

**Apply results:**
- **4 INSERTs**: CSKA Moscow, BC Uralmash Yekaterinburg, BC Nizhny Novgorod, BC Avtodor
- **7 BACKFILLs**: Lokomotiv Kuban (1dae39ae), UNICS Kazan (b1d198b0), Enisey (eef30d44), Zenit Petersburg (d639c09a), Parma Perm (a1973c38), Khimki M. (b2fbeb14), **MBA Moscow (1f5f991a — dynamic BACKFILL confirmed at apply time)**
- 37 aliases inserted, 2 deduped within batch, 0 global conflicts
- `existing_teams_loaded`: 2,034 pre-apply → 2,038 post-apply

### Day-34 morning: Pre-apply manifest fix — `mba` bare alias collision

Amendment #22 pre-apply audit discovered: alias `mba` in vtb_seed.py already maps to Mersin Basketbol (64b11777, Turkish team, legacy_bootstrap) under sport_id=3. Inserting `mba` under bootstrap_league_coverage for MBA Moscow would create a pre-existing multi-team_id collision.

**Fix**: removed `MBA` bare alias from MBA Moscow alias list in vtb_seed.py (line 208: `("MBA Moscow", "MBA")` → `("MBA Moscow",)`). Separate patch PR filed (PR #224, claude/vtb-seed-mba-fix).

**New amendment #22 audit finding pattern**: cross-language/cross-country false alias collision (Turkish `mba` abbreviation colliding with Russian `MBA Moscow` abbreviation). The amendment #22 audit caught it pre-apply — methodology working as designed.

### Day-34 morning: Post-apply collision audit — 5 dormant phantoms

Post-apply Pass 1 query revealed **5 collisions** (exactly matching 3 pre-identified + 2 Uralmash surprise variants):

| Alias | Manifest team | Dormant phantom UUID |
|---|---|---|
| avtodor saratov | BC Avtodor | c0766622 (Avtodor Saratov) |
| parma permsky kray | Parma Perm | 065f0ed5 (Parma Permsky Kray) |
| pbc lokomotiv kuban | Lokomotiv Kuban | f4cd06c6 (PBC Lokomotiv-Kuban) |
| uralmash ekaterinburg | BC Uralmash Yekaterinburg | ce125faf (Uralmash Ekaterinburg) |
| uralmash yekaterinburg | BC Uralmash Yekaterinburg | 9684b3a4 (Uralmash Yekaterinburg) |

**Uralmash spelling variants** (`uralmash ekaterinburg` + `uralmash yekaterinburg`) were the 2 surprises beyond the 3 pre-identified. Both Uralmash stubs (ce125faf, 9684b3a4) are separate Phase 2A.5 entities distinct from BC Uralmash Yekaterinburg.

5 individual DELETEs against `bootstrap_league_coverage` source on manifest team_ids. Zero-collision verification confirmed: 0 rows post-remediation.

### Day-34 morning: baseline_shifts annotation INSERT

Pre-flight SELECT confirmed 0 existing rows (amendment #19). INSERT executed successfully.
Row: event_type=`phase_2d5a_vtb_bootstrap`, event_date=2026-06-05.

### Phase 2D.5-A status: 7 of 9 leagues applied

- ✅ Workstream #1 (LMB): Day-28
- ✅ Workstream #2 (Liga ACB): Day-29
- ✅ Workstream #3 (Italian LBA): Day-31
- ✅ Workstream #4 (Israeli BSL): Day-31
- ✅ Workstream #5 (Turkish BSL): Day-31
- ✅ Workstream #6 (Greek HEBA A1): Day-33, F7 validated Day-34 (25 strict / 8 teams + 5 EuroCup crossovers)
- ✅ Workstream #7 (Russian VTB): Day-34 (2026-06-05T20:17:21Z), F7 opens 2026-06-06T10:17:21Z
- ⏳ Workstream #8 (EuroLeague gap-fill): pending
- ⏳ Workstream #9 (Serbian/ABA): ~40/7d

sp.teams Basketball: **2,038**

### Pending — Day-35 morning agenda

1. VTB F7 verification (opens ~2026-06-06T10:17:21Z, ~14h post-apply). JOIN template with `country_code='RUS'`, apply timestamp `'2026-06-05 20:17:21+00'`. Expected: ~50-100 strict resolutions (~230+/7d discovery volume).
2. Day-35 daily-diff — Basketball trajectory point 5 (compounding LBA + Israeli BSL + Turkish BSL + HEBA + VTB inflation per amendment #20)
3. EuroLeague workstream #8 pre-scope Pattern A.2 discovery — gap-fill after #4-7
4. Serbian/ABA workstream #9 pre-scope (~40/7d confirmed)
5. Env var drop small workstream scoping (6th consecutive session)

---

## Session — 2026-06-04

### Day-33 morning: Greek HEBA A1 workstream #6 APPLIED (workstream #6 EMPIRICALLY APPLIED)

Apply at 2026-06-04T23:04:07Z. Runtime 9.8s, 0 errors. Pattern D pre-flight → amendment #22 audit → dry-run → wet apply sequence completed cleanly.

**Apply results:**
- **4 new HEBA A1 canonicals inserted** (sp.teams, sport_id=3, country_code='GRC'): AEK Athens, Aris Thessaloniki, Olympiakos BC, GS Karditsa
- **9 BACKFILLs from Phase 2A.5 Basketball stubs** (created 2026-05-08): Iraklis BC (c17fa0b9), Kolossos Rhodes (ca5f6d4a), Maroussi BC (d8e37aa5), Mykonos (2f32272a), PAOK BC (59eb93a6), Panathinaikos BC (6e1268f8), Panionios (380f47bc), Peristeri BC (6a00a818), Promitheas Patras BC Vikos Cola (eb0e7a18)
- **44 aliases inserted**, 2 deduped within batch, 0 global conflicts
- `bootstrap.heba.pattern_d.ok` confirmed production endpoint pre-write
- `existing_teams_loaded`: 2,030 Basketball teams pre-apply (2,019 post-Turkish BSL + 11 = 2,030 sanity check passes); post-apply: 2,034
- **Highest BACKFILL ratio of Phase 2D.5-A: 69%** (9 of 13 teams)

**baseline_shifts annotation**: row inserted (event_type='phase_2d5a_heba_bootstrap', event_date=2026-06-04). Amendment #19 idempotency discipline applied via pre-flight SELECT (0 rows existed, safe to INSERT).

### Day-33 morning: Amendment #22 pre-apply alias-claim audit

Mandatory pre-apply audit per amendment #22. All 44 manifest aliases scanned against production sp.team_aliases for sport_id=3.

**Result**: 15 pre-existing rows found, all team_count=1 (no multi-team_id collisions). All 15 are legacy_bootstrap rows pointing to Phase 2A.5 BACKFILL target stubs — exactly the team_ids we are BACKFILLing. Zero pre-existing collisions. Safe to apply confirmed.

### Day-33 morning: Post-apply collision audit — 6 dormant phantoms discovered

Post-apply Pass 1 query revealed **6 collisions** (one more than the 5 predicted pre-apply):

| Alias | Collision shape | Dormant phantom UUID |
|---|---|---|
| iraklis | bootstrap_league_coverage (Iraklis BC) vs legacy_bootstrap (Iraklis) | b0602d2c |
| kolossos rodou | bootstrap_league_coverage (Kolossos Rhodes) vs legacy_bootstrap (Kolossos Rodou) | 7260b8e5 |
| maroussi | bootstrap_league_coverage (Maroussi BC) vs legacy_bootstrap (Maroussi) | 11fb2774 |
| peristeri | bootstrap_league_coverage (Peristeri BC) vs legacy_bootstrap (Peristeri) | 0c6092b5 |
| promitheas | bootstrap_league_coverage (Promitheas Patras BC Vikos Cola) vs legacy_bootstrap (Promitheas) | fca05a4b |
| ao mykonou | bootstrap_league_coverage (Mykonos) vs legacy_bootstrap (AO Mykonou) | 01dac308 — **SURPRISE 6th** |

**AO Mykonou (01dac308)** was the unexpected 6th dormant phantom — our manifest included 'ao mykonou' as a Greek transliteration alias for Mykonos, but a separate Phase 2A.5 legacy stub 'AO Mykonou' already held that alias under a different team_id.

**Remediation**: 6 individual DELETEs against bootstrap_league_coverage source on manifest team_ids. Zero-collision verification confirmed: 0 rows from Pass 1 query post-remediation.

### Day-33 morning: New methodology dimension — AO Mykonou surprise

The `ao mykonou` collision adds a 4th HEBA methodology dimension: **legacy stub canonical_name may match our alias transliteration even when the stub team_id is unrelated to our manifest team**. AO Mykonou (01dac308) is a separate Greek club entity distinct from Mykonos (2f32272a). The collision was alias-level, not canonical-level — our 'ao mykonou' alias for Mykonos collided with AO Mykonou's own legacy alias under the same normalized form. Amendment #22 pre-apply audit caught this in the 15 pre-existing rows (team_count=1, so not flagged as collision pre-apply) but post-apply the bootstrap_league_coverage INSERT created the multi-team_id collision.

**Methodology refinement**: amendment #22 pre-apply audit correctly identified the 15 pre-existing aliases but could not predict that inserting our bootstrap_league_coverage rows would create NEW collisions with those existing single-team rows. **The post-apply collision audit remains mandatory regardless of clean pre-apply audit results.**

### Day-33 morning: Daily-diff

Render at 2026-06-04T23:14:58Z. Latest data point: report_date 2026-06-02, 34.7% capability — same as Day-32 (Day-33 measurement pending tonight's cron pass; HEBA was applied at 23:04 UTC, too recent for current window).

Basketball trajectory point 5 will be visible in Day-34 render.

Baseline-shift events rendering correctly in report: LBA, Turkish BSL, Israeli BSL (with Nes Ziona addendum), ACB, LMB all present. HEBA annotation inserted post-render; will appear in Day-34.

sp.resolution_log volume (latest cron): 102,603 rows. Strict: 305. No_match: 89,709 (87%). Review_queue: 12,586.

### Phase 2D.5-A status: 6 of 9 leagues applied

- ✅ Workstream #1 (LMB): Day-28 apply, Day-29 F7 (18 strict / 6 teams)
- ✅ Workstream #2 (Liga ACB): Day-29 apply, Day-30 F7 (41 strict / 11 teams + 2 EuroCup crossovers)
- ✅ Workstream #3 (Italian LBA): Day-31 apply, Day-32 F7 (34 strict / 11 teams)
- ✅ Workstream #4 (Israeli BSL): Day-31 apply, Day-32 F7 (29 strict / 7 teams + 1 EuroCup crossover)
- ✅ Workstream #5 (Turkish BSL): Day-31 apply, Day-32 F7 (35 strict / 13 teams)
- ✅ Workstream #6 (Greek HEBA A1): Day-33 apply 2026-06-04T23:04:07Z (4 INSERT + 9 BACKFILL + 44 aliases); F7 window opens 2026-06-05T13:04:07Z
- ⏳ Workstream #7-9: Russian VTB, EuroLeague (gap-fill), Serbian/ABA

### Pending — Day-34 morning agenda

1. Greek HEBA A1 F7 verification (opens ~13:04 UTC Day-34, ~14h post-apply) — JOIN template with `country_code='GRC'`, apply timestamp 2026-06-04T23:04:07Z. Expected: ~25-50 strict resolutions (playoffs-only window; 5 active teams confirmed in Day-32 discovery)
2. Day-34 daily-diff — Basketball trajectory point 5 (compounding LBA + Israeli BSL + Turkish BSL + HEBA inflation per amendment #20)
3. Russian VTB workstream #7 pre-scope Pattern A.2 discovery — BC Lokomotiv Kuban / CSKA Moscow confirmed at 42 records/7d in Day-33 discovery query
4. Env var drop small workstream scoping (6th consecutive session)

---

## Session — 2026-06-03

### Day-32 morning: F7 pre-remediation verifications (3 workstreams)

F7 counts at ~16-18h post-apply (pre-remediation baseline):
- Italian LBA: 34 strict resolutions / 12 team-pairs / apply 2026-06-02 13:39:51 UTC
- Israeli BSL: 29 strict resolutions / 19 team-pairs / apply 2026-06-02 14:56:10 UTC
- Turkish BSL: 35 strict resolutions / 20 team-pairs / apply 2026-06-02 16:52:01 UTC
- Combined: 98 strict resolutions

**Italian LBA F7 (34 resolutions):**
- 11 distinct manifest teams resolving
- BACKFILL branch validated: Olimpia Milano (14 resolutions), Virtus Bologna (11), Reyer Venezia (5)
- INSERT branch validated: Aquila Basket Trento, Pallacanestro Brescia, Pallacanestro Reggiana, Derthona Basket, Universo Treviso Basket, Dinamo Sassari, Pallacanestro Varese
- Cross-sport collision discipline validated: Olimpia Milano resolving without colliding with Inter Milan / AC Milan (6th empirical validation of Day-22 sport_id partition finding)

**Israeli BSL F7 (29 resolutions — below 50-100 projection):**
- 7 manifest teams resolving: Hapoel HaEmek, Hapoel Jerusalem, Hapoel Tel Aviv, Maccabi Tel Aviv, Bnei Herzliya, Ironi Kiryat Ata, Ironi Ness Ziona
- Real Madrid Baloncesto appeared as EuroLeague crossover (4 resolutions)
- 5 of 7 missing manifest teams have alias collisions (Maccabi Rishon LeZion, Hapoel Be'er Sheva/Dimona, Hapoel Galil Elyon, Elitzur Netanya, Maccabi Ironi Ra'anana) — below-projection explained by collision degradation
- 2 missing teams (Maccabi Ironi Ramat Gan, Hapoel Holon) absent due to fixture-window absence
- Nes Ziona (4456a86f-7757-4069-9376-093a7a76371a) appeared as home_team in breakdown — see dormant phantom discovery below

**Turkish BSL F7 (35 resolutions — within 30-60 projection):**
- 13 manifest teams resolving: Beşiktaş (11 res.), Fenerbahçe (14 res.), Galatasaray (9 res.), Anadolu Efes, Bahçeşehir Koleji, Esenler Erokspor, Bursaspor Basketbol, Karşıyaka Basket, Merkezefendi Basket, Petkim Spor, Trabzonspor (Basketbol)
- Empirical-coverage F2 NEW validated: bare-form aliases (Galatasaray, Fenerbahçe, Beşiktaş) producing strict-tier resolutions
- Dotless-ı handling validated: Karşıyaka Basket + Bahçeşehir Koleji resolving
- Dormant phantom discipline validated: Manisa, Mersin SK, Turk Telekom (3 Option-2-remediated phantoms) resolving correctly to legacy stubs
- EuroLeague crossover: Zalgiris Kaunas (2 resolutions)

### Day-32 morning: Nes Ziona dormant phantom discovery (7th Israeli BSL dormant phantom)

Nes Ziona (UUID: 4456a86f-7757-4069-9376-093a7a76371a) appeared as home_team in Israeli BSL F7 breakdown. Verification query confirmed:
- canonical_name: "Nes Ziona"
- sport_id: 3
- country_code: NULL
- created_at: 2026-05-08 (Phase 2A.5 legacy stub)

Our manifest canonical "Ironi Ness Ziona" diverges from legacy "Nes Ziona" — provider strings sending "Nes Ziona" route to the legacy stub. This is canonical-name fragmentation / dormant phantom discipline, same pattern as Turkish BSL dormant phantoms.

Israeli BSL dormant phantom count: 1 (Nes Ziona only — Turkish BSL dormant phantoms are a separate workstream).

### Day-32 morning: Comprehensive collision audit (Pass 1 + Pass 2)

Pass 1 (multi-team_id collision audit) confirmed 43-finding complete — no additional collisions beyond the Day-31 discovery.

Pass 2 (Phase 2A.5 Basketball legacy stubs, created < 2026-05-28, country_code IS NULL) returned ~1,000+ rows covering the full Phase 2A.5 Basketball population. All 7 dormant phantoms identified; no new dormant phantoms beyond Nes Ziona.

### Day-32 morning: Collision remediation — "43 → 5 → 0" arc

Comprehensive collision remediation completed. Final state: 0 multi-team_id collisions in sp.team_aliases for sport_id=3.

Remediation arc:
1. Day-31 evening (3 DELETEs, already applied): `manisa`, `mersin sk`, `buyukcekmece basketbol` under bootstrap_league_coverage
2. Day-32 morning investigation revealed the 43-collision audit sources column was aggregating across both team_ids, creating misleading {bootstrap_league_coverage, legacy_bootstrap} appearances for many entries
3. Re-ran Pass 1 with simplified query: revealed 26 collisions, then 5, then 0 as investigation proceeded
4. Actual collision shapes discovered:
   - **Shape A**: bare alias on legacy stub under legacy_bootstrap only (`bahcesehir kol`, `anyang jungkwanjang`, and the ACB/LBA bare forms) — no bootstrap_league_coverage row to DELETE; these are read-only legacy collisions
   - **Shape B**: alias_tier write-back collision — resolver auto-wrote alias for manifest team matching a legacy stub alias
5. Final 6 DELETEs (Day-32 morning, alias_tier source):
   - `bc andorra` / alias_tier / MoraBanc Andorra (e79aa98b)
   - `elitzur netanya` / alias_tier / Elitzur Maccabi Netanya (af0002ec)
   - `hapoel galil elyon` / alias_tier / Galil Elyon (c0b1aa48)
   - `maccabi raanana` / alias_tier / Maccabi Raanana (281d160b)
   - `maccabi raanana` / legacy_bootstrap / Maccabi Raanana (281d160b)
   - `maccabi rishon lezion` / alias_tier / Maccabi Rishon (0114dc9f)

**Methodology learning**: the Day-31 "43 collision" finding overstated the remediation scope. The PR #200 INSERT...WHERE NOT EXISTS alias-safety discipline was more effective than the audit suggested — many apparent collisions were legacy-only alias forms that our manifest never inserted under bootstrap_league_coverage. The real collision surface was ~6-9 rows (3 Day-31 DELETEs + 6 Day-32 DELETEs = 9 total), not 43.

### Day-32 morning: Amendment #22 formal documentation

**Pre-flight alias-claim audit before workstream apply is mandatory.** Manifest aliases under `source='bootstrap_league_coverage'` do not block on legacy aliases under `source='legacy_bootstrap'` or `source='alias_tier'` (PR #200 INSERT...WHERE NOT EXISTS only checks within-source). Multi-team_id collisions at the `(alias_normalized, sport_id)` index create strict-tier punt behavior. Future workstreams must run pre-apply audit query identifying any manifest-alias-normalized form that ALREADY has team_id mappings under any source, and resolve collisions before apply (omit the alias OR delete the legacy alias OR accept the alias-tier routing degradation).

Audit query template (run pre-apply, scope to sport_id, scan all sources):

```sql
SELECT ta.alias_normalized, COUNT(DISTINCT ta.team_id) AS team_count,
       ARRAY_AGG(DISTINCT t.canonical_name) AS canonicals,
       ARRAY_AGG(DISTINCT ta.source) AS sources
FROM sp.team_aliases ta
JOIN sp.teams t ON t.id = ta.team_id
WHERE t.sport_id = :target_sport_id
  AND ta.alias_normalized IN (:manifest_alias_normalized_list)
GROUP BY ta.alias_normalized
HAVING COUNT(DISTINCT ta.team_id) > 0;
```

Amendment pile expands from 21 to 22 items.

### Day-32 morning: Daily-diff — report_date 2026-06-02

report_date: 2026-06-02, records: 12,079, matcher_capability_rate: 34.7% (scope-filtered)

**Baseball trajectory point 6 (post-LMB):**
- Day-27 (pre-LMB): 86.7%
- Day-28 (apply day): 85.2%
- Day-29 (1d post): 76.5%
- Day-30 (4d post): 73.6%
- Day-31 (5d post): 71.6%

Continued monotonic decline, no stabilization signal yet. Consistent with compounding-denominator-inflation hypothesis — 7-day rolling window filling with LMB records.

**Basketball trajectory point 4 (post-LBA + Israeli BSL + Turkish BSL):**
- Day-29 (pre-LBA/BSL): 53.3%
- Day-30 (1d post-LBA): 51.5%
- Day-31 (1d post-Israeli BSL + Turkish BSL): 44.6%

Compounding denominator inflation from 3 workstreams applied Day-31.

sp.resolution_log volume (latest cron): 102,603 rows. Strict: 305. No_match: 89,709 (87%). Review_queue: 12,586.

Amendment #20 confirmed: aggregate capability rate denominator-sensitive to record-mix. F7 workstream-specific queries remain canonical methodology validation.

### Day-32 morning: Post-remediation F7 re-measurement

Post-remediation F7 (same apply timestamps, ~20-22h post-apply):
- Italian LBA: 34 (unchanged)
- Israeli BSL: 29 (unchanged)
- Turkish BSL: 35 (unchanged)
- Combined: 98 (unchanged, 0 delta)

Zero delta is expected and correct:
1. F7 window opens at apply timestamp (yesterday) — counts ALL resolutions including pre-remediation period
2. Remediation improvement will manifest in future cron passes (strict-tier now unblocked for previously-collision-degraded records)
3. No regression confirmed — canonical routing intact

### Day-32 morning: Env var drop pattern — promoted to small workstream candidate

5th consecutive session with PowerShell env var drop (Day-29, Day-30, Day-31, Day-32, Day-32 second session). Pattern is now sufficiently consistent to promote from tech-debt observation to small workstream candidate.

Three mitigation options (unchanged from prior sessions):
- `.env` file with python-dotenv auto-load
- PowerShell `$PROFILE` script export
- Convenience script `scripts/setup_env.ps1` reading from gitignored `.env.local`

### Phase 2D.5-A status: 5 of 9 leagues applied

- ✅ Workstream #1 (LMB): Day-28 apply, Day-29 F7 (18 strict / 6 teams)
- ✅ Workstream #2 (Liga ACB): Day-29 apply, Day-30 F7 (41 strict / 11 teams + 2 EuroLeague crossovers)
- ✅ Workstream #3 (Italian LBA): Day-31 morning apply, Day-32 F7 (34 strict / 11 teams)
- ✅ Workstream #4 (Israeli BSL): Day-31 afternoon apply, Day-32 F7 (29 strict / 7 teams + 1 EuroLeague crossover)
- ✅ Workstream #5 (Turkish BSL): Day-31 evening apply, Day-32 F7 (35 strict / 13 teams)
- ⏳ Workstream #6-9: Greek HEBA, Russian VTB, EuroLeague (gap-fill), Serbian/ABA

### Pending — Day-32 afternoon agenda

1. Turkish BSL baseline_shifts annotation INSERT (event_type='phase_2d5a_turkish_bsl_bootstrap', event_date=2026-06-02, apply 2026-06-02T16:52:01Z, 11 INSERTs, 5 BACKFILLs [Anadolu Efes ca2f4866, Bahçeşehir Koleji 052768a0, Bursaspor Basketbol 85c6d6bf, Petkim Spor c2cacf82, Tofas→Tofaş 7f3d7ec1], 6 dormant phantoms [Karşıyaka ff68785a, Turk Telekom d436ec55, Manisa 4de5ac1f, Mersin SK 80aac551, Buyukcekmece cd3ecf89, Bahcesehir Kol. e957ec25], 9 collision remediations total [3 Day-31 + 6 Day-32])
2. Israeli BSL baseline_shifts annotation UPDATE (add Nes Ziona 4456a86f as 7th dormant phantom to notes field)
3. Env var drop small workstream scoping
4. Greek HEBA workstream #6 pre-scope Pattern A.2 discovery

### Day-32 afternoon: Turkish BSL baseline_shifts annotation INSERT

Pre-flight SELECT confirmed 0 existing rows (amendment #19 idempotency discipline). INSERT executed successfully.

Row inserted: event_type='phase_2d5a_turkish_bsl_bootstrap', event_date=2026-06-02.

Key annotation details:
- Apply: 2026-06-02T16:52:01Z, runtime 11.38s, PR #217
- 11 INSERTs: Beşiktaş, Fenerbahçe, Galatasaray, Esenler Erokspor, Manisa Basket, Karşıyaka Basket, Mersin MSK, Büyükçekmece Basketbol, Trabzonspor (Basketbol), Türk Telekom Ankara, Merkezefendi Basket
- 5 BACKFILLs: Anadolu Efes (ca2f4866), Bahçeşehir Koleji (052768a0), Bursaspor Basketbol (85c6d6bf), Petkim Spor (c2cacf82), Tofas→Tofaş (7f3d7ec1)
- 6 dormant phantoms: Karşıyaka (ff68785a), Turk Telekom (d436ec55), Manisa (4de5ac1f), Mersin SK (80aac551), Buyukcekmece (cd3ecf89), Bahcesehir Kol. (e957ec25)
- 9 total collision remediations (3 Day-31 + 6 Day-32)
- F7 verified Day-32: 35 strict resolutions / 20 team-pairs
- Cross-sport collision discipline: bare-form aliases INCLUDED per F2 NEW empirical-coverage discipline

### Day-32 afternoon: Israeli BSL baseline_shifts annotation UPDATE

Existing row e048283e-1e05-4fd2-afaf-a77b8e8b375f updated via notes || append.

Addendum added: Nes Ziona (4456a86f-7757-4069-9376-093a7a76371a) confirmed as 7th Israeli BSL dormant phantom. Phase 2A.5 legacy stub (created 2026-05-08), sport_id=3, country_code=NULL. canonical_name='Nes Ziona' diverges from manifest canonical 'Ironi Ness Ziona'. Canonical-name fragmentation / dormant phantom discipline applies.

### Day-32 afternoon: Greek HEBA workstream #6 pre-scope Pattern A.2 discovery

Per amendment #21: production discovery run BEFORE authoritative-source roster sourcing.

Discovery query against sp.resolution_log (no_match, Basketball, 7-day window) using ILIKE patterns for known HEBA A1 teams.

**Volume finding**: ~50-70 Basketball unresolved records/7d attributable to HEBA — consistent with Day-31 sequencing estimate.

**Active teams in FL coverage (playoffs only — other teams eliminated):**
- Olympiakos: bare form `Olympiacos` + EuroCup form `BC Olympiakos Piraeus` (~41 records/7d combined)
- AEK Athens: bare form `AEK Athens` + EuroCup form `BC AEK Athens` + asterisk variant `AEK Athens *` (~75 records/7d combined — highest volume)
- Aris: bare form `Aris` + EuroCup form `BC Aris Thessaloniki` (~35 records/7d)
- Kolossos Rhodes: bare form `Kolossos Rhodes` + EuroCup form `BC Kolossos Rhodes` (~28 records/7d)
- Panathinaikos: bare form `Panathinaikos` + EuroCup form `Panathinaikos BC` (~14 records/7d)

**Teams NOT appearing in 7-day window** (eliminated or not in FL coverage): Promitheas, Lavrio, Peristeri, Panionios, Iraklis, Maroussi, PAOK — these will need Wikipedia/authoritative-source verification for full 12-team roster.

**Provider string inventory for manifest design:**
- Bare domestic: Olympiacos, AEK Athens, Aris, Panathinaikos, Kolossos Rhodes
- BC-prefixed EuroCup: BC Olympiakos Piraeus, BC AEK Athens, BC Aris Thessaloniki, BC Kolossos Rhodes, Panathinaikos BC
- Asterisk-suffix variants: AEK Athens *, Olympiacos * (belt-and-suspenders aliases needed per Italian LBA Day-30 asterisk finding)

**Cross-sport collision flags** (Greek Super League football overlaps):
All 5 active HEBA teams have prominent Greek Super League football counterparts: Olympiakos FC, Panathinaikos FC, AEK Athens FC, Aris FC. Operator-clarity discipline applies (top-5 Greek football recognition). Amendment #22 pre-apply alias-claim audit required before manifest commit.

**EuroCup crossovers confirmed**: Olympiakos and AEK Athens active in EuroCup — BC-prefixed forms appear in cross-league fixtures (Fenerbahce Istanbul vs BC Olympiakos Piraeus, BC Rytas Vilnius vs BC AEK Athens, Unicaja vs AEK Athens *). These will produce cross-league strict resolutions post-apply, same pattern as Liga ACB EuroLeague crossovers (Day-30) and Turkish BSL EuroLeague crossovers (Day-32 morning).

**Dormant phantom risk**: Olympiakos and Panathinaikos almost certainly exist in Phase 2A.5 legacy stubs (high-profile EuroCup/EuroLeague teams). Pre-apply BACKFILL discovery query required.

**Sequencing note**: Russian VTB (BC Lokomotiv Kuban / CSKA Moscow at 42 records/7d) also confirmed active in same discovery query. VTB remains workstream #7 per Day-31 re-sequencing.

**Decision**: HEBA workstream #6 design deferred to Day-33 morning. Pre-scope discovery is complete; Day-33 opens with Wikipedia 2025-26 HEBA A1 season roster paste → manifest design → Claude Code PR.

### Phase 2D.5-A status: 5 of 9 leagues applied, #6 pre-scope complete

- ✅ Workstream #1 (LMB): Day-28 apply, Day-29 F7 (18 strict / 6 teams)
- ✅ Workstream #2 (Liga ACB): Day-29 apply, Day-30 F7 (41 strict / 11 teams + 2 EuroCup crossovers)
- ✅ Workstream #3 (Italian LBA): Day-31 apply, Day-32 F7 (34 strict / 11 teams)
- ✅ Workstream #4 (Israeli BSL): Day-31 apply, Day-32 F7 (29 strict / 7 teams + 1 EuroCup crossover)
- ✅ Workstream #5 (Turkish BSL): Day-31 apply, Day-32 F7 (35 strict / 13 teams)
- 🟡 Workstream #6 (Greek HEBA A1): pre-scope Pattern A.2 discovery complete Day-32 afternoon; manifest design Day-33
- ⏳ Workstream #7-9: Russian VTB, EuroLeague (gap-fill), Serbian/ABA

### Pending — Day-33 morning agenda

1. Greek HEBA A1 workstream #6 manifest design — operator pastes Wikipedia 2025-26 HEBA A1 season roster; Claude Code drafts manifest + script + tests + scope-doc per amendment #14 single-PR convention
2. Amendment #22 pre-apply alias-claim audit for HEBA manifest (mandatory per amendment #22 before apply)
3. HEBA dormant phantom discovery (Olympiakos/Panathinaikos likely in Phase 2A.5 legacy stubs)
4. Env var drop small workstream scoping (5th consecutive session — promoted to small workstream candidate Day-32 morning)

---

## Session — 2026-06-02

### Day-31 end-of-day: Turkish BSL APPLIED (workstream #5) + CROSS-WORKSTREAM COLLISION FINDING

Apply at 2026-06-02T16:52:01 UTC. Runtime 11.38s, 0 errors. Pattern D pre-flight → dry-run → wet apply sequence completed cleanly. PR #217 merged for workstream design.

**Apply results:**
- **11 new Turkish BSL canonicals inserted** (sp.teams, sport_id=3, country_code='TUR'): Beşiktaş, Fenerbahçe, Galatasaray, Esenler Erokspor, Manisa Basket, Karşıyaka Basket, Mersin MSK, Büyükçekmece Basketbol, Trabzonspor (Basketbol), Türk Telekom Ankara, Merkezefendi Basket
- **5 BACKFILLs from Phase 2A.5 Basketball stubs (2026-05-08)**: Anadolu Efes (ca2f4866-c4ac-4a26-976f-d54401ce8c1d), Bahçeşehir Koleji (052768a0-79b1-4cd9-a823-530c04635324), Bursaspor Basketbol (85c6d6bf-8ffb-4309-b0aa-9ba3d146ad4c), Petkim Spor (c2cacf82-b492-4664-8631-14c2c013de6a), Tofas → Tofaş (7f3d7ec1-c48f-48cf-8b8f-089faec3fc53)
- **65 aliases queued for insert**, 24 deduped within batch (belt-and-suspenders diacritic + dotless-ı pairs)
- `bootstrap.turkish_bsl.pattern_d.ok` confirmed production endpoint pre-write
- `existing_teams_loaded`: 2,019 Basketball teams pre-apply (sanity check: 2,010 post-LBA + 9 BSL INSERTs = 2,019 ✓)

**baseline_shifts annotation: DEFERRED to Day-32 morning** pending systematic resolution of the cross-workstream collision finding documented below.

### Day-31 end-of-day: Cross-workstream alias collision finding (Amendment #22 CANDIDATE)

**During post-apply alias-claim audit, discovered that the PR #200 alias-safety discipline does not catch cross-source collisions.** The INSERT...WHERE NOT EXISTS pattern checks for duplicates within `source='bootstrap_league_coverage'` only. When manifest aliases match `alias_normalized` values that already exist under `source='legacy_bootstrap'` or `source='alias_tier'`, the new rows insert successfully — creating multi-team_id mappings under sport_id=3.

**Comprehensive audit query revealed 43 such collisions across all 5 Phase 2D.5-A Basketball workstreams** (KBL Day-19, Liga ACB Day-29, Italian LBA Day-31 morning, Israeli BSL Day-31 afternoon, Turkish BSL Day-31 evening).

**Runtime impact**: AliasIndex (`resolver/aliases.py:51,111`) is keyed by `(alias_normalized, sport_id)` returning a set of team_ids. When the set has size > 1, strict tier punts (treats as ambiguous). Records that should resolve via strict tier are routing to alias tier instead.

**This is not a data loss event** — records still resolve at alias-tier confidence, just below strict-tier confidence. But it IS a methodology regression for strict-tier coverage that has been silently degrading across Phase 2D.5-A.

**Distribution by workstream** (43 total collisions):

| Workstream | Collisions | Examples |
|---|---:|---|
| KBL (Day-19) | 1 | `anyang jungkwanjang` |
| Liga ACB (Day-29) | 14 | `baskonia`, `baxi manresa`, `bc andorra`, `breogan`, `forca lleida`, `gran canaria`, `joventut badalona`, `morabanc andorra`, `murcia`, `surne bilbao basket`, `unicaja`, `san pablo burgos`, `cb san pablo burgos`, `ucam murcia` |
| Italian LBA (Day-31 morning) | 14 | `basket napoli`, `cantu`, `cremona`, `dolomiti energia trento`, `napoli basket`, `pallacanestro trieste 2004`, `sassari`, `tortona`, `trieste`, `udine`, `apu udine`, `unahotels reggio emilia`, `varese`, `vanoli cremona` |
| Israeli BSL (Day-31 afternoon) | 8 | `elitzur maccabi netanya`, `elitzur netanya`, `galil elyon`, `hapoel beer sheva`, `hapoel galil elyon`, `maccabi raanana`, `maccabi rishon`, `maccabi rishon lezion` |
| Turkish BSL (today) | 4 | `bahcesehir kol`, `besiktas gain`, `merkezefendi`, `tofas bursa` |

**Turkish BSL partial remediation already applied (3 of 4)**: DELETEs against `bootstrap_league_coverage` source for `manisa`, `mersin sk`, `buyukcekmece basketbol` to preserve legacy strict-tier routing (consistent with F1 canonical-name fragmentation / dormant phantom discipline). 4th Turkish collision (`bahcesehir kol`) discovered after initial audit; remediation pending.

**The other 39 collisions are NOT remediated yet** — discovered post-Turkish-BSL-apply via comprehensive audit query. Decision deferred to Day-32 morning systematic remediation workstream.

### Amendment #22 candidate (NEW)

**Pre-flight alias-claim audit before workstream apply is mandatory.** Manifest aliases under `source='bootstrap_league_coverage'` do not block on legacy aliases under `source='legacy_bootstrap'` or `source='alias_tier'` (PR #200 INSERT...WHERE NOT EXISTS only checks within-source). Multi-team_id collisions at the `(alias_normalized, sport_id)` index create strict-tier punt behavior. Future workstreams must run pre-apply audit query identifying any manifest-alias-normalized form that ALREADY has team_id mappings under any source, and resolve collisions before apply (omit the alias OR delete the legacy alias OR accept the alias-tier routing degradation).

**Audit query template** (run pre-apply, scope to sport_id, scan all sources):

```sql
SELECT ta.alias_normalized, COUNT(DISTINCT ta.team_id) AS team_count,
       ARRAY_AGG(DISTINCT t.canonical_name) AS canonicals,
       ARRAY_AGG(DISTINCT ta.source) AS sources
FROM sp.team_aliases ta
JOIN sp.teams t ON t.id = ta.team_id
WHERE t.sport_id = :target_sport_id
  AND ta.alias_normalized IN (:manifest_alias_normalized_list)
GROUP BY ta.alias_normalized
HAVING COUNT(DISTINCT ta.team_id) > 0;
```

Any rows returned indicate pre-existing alias_normalized values that will produce multi-team_id collisions on insert. Resolve before apply.

**Amendment pile expands from 21 to 22 items.** Formal documentation deferred to Day-32 morning along with systematic remediation.

### Day-31 end-of-day: Workstream-level summary

**Phase 2D.5-A status: 5 of 9 leagues applied**
- ✅ Workstream #1 (LMB): Day-28 apply, Day-29 morning F7 validation (18 strict / 6 teams)
- ✅ Workstream #2 (Liga ACB): Day-29 afternoon apply, Day-30 morning F7 validation (41 strict / 11 manifest teams + 2 EuroLeague crossovers); 14 cross-workstream collisions discovered Day-31 evening
- ✅ Workstream #3 (Italian LBA): Day-31 morning apply (13 INSERT + 3 BACKFILL + 86 aliases); 14 cross-workstream collisions discovered Day-31 evening; F7 opens ~03:39 UTC Day-32
- ✅ Workstream #4 (Israeli BSL): Day-31 afternoon apply (9 INSERT + 5 BACKFILL + 43 aliases); 8 cross-workstream collisions discovered Day-31 evening; F7 opens ~04:56 UTC Day-32
- ✅ Workstream #5 (Turkish BSL): Day-31 evening apply (11 INSERT + 5 BACKFILL + 65 aliases); 4 collisions (3 remediated, 1 pending); F7 opens ~06:52 UTC Day-32
- ⏳ Workstream #6-9: Greek HEBA, Russian VTB, EuroLeague (gap-fill), Serbian/ABA

**Day-31 substantive deliverables (16 items)**: Italian LBA apply + LBA annotation + LBA daily-diff + Day-31 morning journal PR #213 + EuroLeague pre-scope discovery + sequencing-decision update PR #214 + FL API discussion + maintenance question discussion + Israeli BSL design PR #215 + Israeli BSL apply + Israeli BSL annotation + Day-31 afternoon journal PR #216 + Turkish BSL discovery + Turkish BSL design PR #217 + Turkish BSL apply + cross-workstream collision finding.

### Day-31 end-of-day: PR state

- Morning batch (PR #213): Italian LBA apply + daily-diff + schema-verification erratum (MERGED)
- Afternoon PR #214: Sequencing decision Day-31 addendum (MERGED)
- Afternoon PR #215: Israeli BSL workstream design (MERGED)
- Afternoon PR #216: Day-31 afternoon journal (MERGED)
- Evening PR #217: Turkish BSL workstream design (MERGED)
- Evening journal (this entry, separate PR)

### Pending — Day-32 morning agenda

1. **F7 verifications for 3 applied workstreams** (Italian LBA + Israeli BSL + Turkish BSL): note F7 results will be ASTERISK-MARKED because collisions affect strict-tier reach. Run F7 BEFORE remediation to measure pre-remediation strict-tier coverage.

2. **Day-32 daily-diff** with collision-state-as-background. Baseball trajectory data point 6, Basketball trajectory data point 4 (compounding LBA + BSL + Turkish BSL inflation per amendment #20).

3. **Systematic cross-workstream collision remediation workstream** (substantive Day-32 morning task, ~1-2 hours):
   - 4th Turkish BSL collision DELETE (`bahcesehir kol`)
   - 39 prior-workstream collision DELETEs across KBL/ACB/LBA/Israeli-BSL
   - Pattern: Option 2 (DELETE manifest aliases that collide with legacy stubs, preserve legacy routing as dormant phantoms per F1 canonical-name fragmentation discipline)
   - Re-verify zero-collision state after each workstream's batch

4. **Post-remediation F7 re-measurement** for all 5 applied workstreams. Compare pre- vs post-remediation strict-tier resolution counts to quantify the methodology-regression magnitude.

5. **Amendment #22 formal documentation** in scope-doc + addition to v1.5 amendment pile (expansion 21 → 22).

6. **Backfill the Turkish BSL baseline_shifts annotation** with final 6-dormant-phantom count + post-remediation alias-state-clean status.

7. **Methodology reflection**: Was the empirical-coverage discipline (F2 NEW from Turkish BSL Day-31 scope-doc §4.1) the right call? Re-examine against the 43-collision empirical signal. Possible refinement: empirical-coverage discipline requires pre-flight alias-claim audit; without it, default to operator-clarity exclusion.

### Day-31 end-of-day: Methodology reflections (post-mortem-shape)

This is the **first Phase 2D.5-A methodology surprise that required pause** rather than push-through resolution. Healthy precedent: when a finding's scope grows beyond the current workstream during end-of-day hours, defer remediation to fresh-attention next-day session rather than compound methodology errors via rushed execution.

The 6th worked example of amendment #12 generalizing (artifact verification over memory) surfaced earlier today (Claude assistant guessed `resolver.normalize_alias` function name + `alias_canonical` column name; both wrong; required Select-String + information_schema verification). The collision finding is the same epistemic shape at a different granularity: cross-source collisions exist as production-data artifacts that pre-flight discovery would have surfaced.

**Pattern A.2 sequencing improvement (amendment #21)** was about pre-scope discovery against authoritative sources. Amendment #22 extends Pattern A.2 to **pre-apply discovery against production state**: not just "is the manifest correct against Wikipedia?" but "what does the production database already say about this alias?"

The collision count distribution (1 / 14 / 14 / 8 / 4) tells a story: workstreams with more aggressive bare-form / sponsor-stripped alias coverage produced more collisions. KBL (1 collision) was small-scope and conservative. Liga ACB and Italian LBA (14 each) had heavy sponsor-stripping. Israeli BSL (8) had 11-city exclusion that limited the collision surface. Turkish BSL (4 pre-remediation) had the F2 NEW empirical-coverage inclusion. Greek HEBA workstream #6 should expect ~5-15 collisions of similar shape (Olympiakos / Panathinaikos / AEK Athens football-overlap teams have Phase 2A.5 legacy stubs).

**No new amendment beyond #22 needed.** The collision pattern is contained by the alias-claim audit discipline. Tomorrow's systematic remediation completes the methodology refinement.

### Day-31 afternoon: Israeli BSL APPLIED + ANNOTATED (workstream #4 EMPIRICALLY APPLIED)

Apply at 2026-06-02T14:56:10 UTC. Runtime 9.05s (fastest Phase 2D.5-A apply yet — LMB 10.7s, ACB 11.66s, LBA 13.3s, BSL 9.05s). 0 errors.

**Apply results:**
- **9 new BSL canonicals inserted** (sp.teams, sport_id=3, country_code='ISR'): Maccabi Tel Aviv, Hapoel Tel Aviv, Hapoel Jerusalem, Maccabi Rishon LeZion, Hapoel Be'er Sheva/Dimona, Ironi Ness Ziona, Hapoel Galil Elyon, Elitzur Netanya, Maccabi Ironi Ra'anana
- **5 BACKFILLs from Phase 2A.5 Basketball stubs (2026-05-08)**: Bnei Herzliya (3e218c54-bdee-4037-ace8-5d015871d2b7), Hapoel HaEmek (fad303be-d053-4780-9296-c13944f06670), Hapoel Holon (e02c83ea-f759-4ca4-863c-e07f037ac231), Ironi Kiryat Ata (9b4994a9-b827-478a-a64e-dba75ebd1f28), Maccabi Ironi Ramat Gan (416f4890-f6c3-4323-9c62-a52f65a8a412)
- **43 aliases inserted**, 11 deduped within batch (belt-and-suspenders apostrophe + hyphen + capitalization pairs), 0 global conflicts (PR #200 alias-safety discipline held)
- `bootstrap.israeli_bsl.pattern_d.ok` confirmed production endpoint pre-write
- `existing_teams_loaded`: 2,010 Basketball teams pre-apply (sanity check: 1,997 LBA-baseline + 13 LBA INSERTs = 2,010 ✓)

**baseline_shifts annotation**: row `e048283e-1e05-4fd2-afaf-a77b8e8b375f` (event_type='phase_2d5a_israeli_bsl_bootstrap', event_date=2026-06-02). Amendment #19 idempotency discipline applied via pre-flight SELECT (0 rows existed, safe to INSERT).

### Day-31 afternoon: Empirical finding — Phase 2A.5 legacy coverage non-correlated with team prominence

Surprising finding from BACKFILL identification. The 5 BSL teams that pre-existed in Phase 2A.5 legacy `public.entities` (2026-05-08 created_at) are:
- Bnei Herzliya (mid-tier, EuroCup participant)
- Hapoel HaEmek (mid-tier regional)
- Hapoel Holon (mid-tier, EuroCup participant historically)
- Ironi Kiryat Ata (mid-tier regional)
- Maccabi Ironi Ramat Gan (mid-tier, EuroCup participant)

Teams that were NOT in Phase 2A.5 legacy (today's INSERTs):
- **Maccabi Tel Aviv** (6× EuroLeague champion — 1977, 1981, 2001, 2004, 2005, 2014; Israeli basketball flagship)
- **Hapoel Tel Aviv** (perennial top-tier, EuroCup champion 2001-02)
- **Hapoel Jerusalem** (former EuroChallenge champion 2003-04, EuroCup champion 2003-04)

**This is counterintuitive.** Conventional expectation: Phase 2A.5 legacy `public.entities` would contain the most prominent international teams (Maccabi Tel Aviv as 6× EuroLeague champion is one of the highest-profile basketball clubs globally). Reality: legacy coverage was NOT prominence-correlated.

**Hypothesis**: Phase 2A.5 legacy data came from a specific provider's coverage snapshot, not an authoritative-source roster. The provider's coverage was bounded by which teams that provider's data feeds included at the time `public.entities` was populated — possibly a specific tournament or league context that happened to include mid-tier Israeli BSL teams but not the EuroLeague-bound flagship teams.

**Implication for future workstreams**: BACKFILL prediction (which manifest teams will be in legacy vs new INSERTs) is NOT reliable based on team prominence. Empirical verification via the dry-run pre-apply discovery is the ONLY reliable signal. Future workstreams should expect surprise: prominent teams may be NEW INSERTs; mid-tier teams may be BACKFILLs.

This is a refinement of the operator's mental model of `public.entities` legacy data composition. No new amendment needed; falls within existing amendment #18 epistemic discipline ("artifact verification, not memory") applied at the workstream prediction level.

### Day-31 afternoon: Phase 2D.5-A re-sequencing per Day-31 discovery (PR #214 merged)

Pre-scope Pattern A.2 discovery query for EuroLeague workstream #4 (per amendment #21) revealed the unresolved Basketball population is dominated by domestic basketball leagues (Israeli BSL ~300/7d, Turkish BSL ~100+/7d, Greek HEBA ~50+/7d, Russian VTB ~150/7d), NOT EuroLeague-proper. EuroLeague-only residual is ~50-80/7d after subtracting domestic-league records.

Per amendment #15 (bootstrap leverage ≠ total-daily-volume; production-data discovery overrides scope-doc defaults), the Day-28 sequencing decision is overridden by Day-31 empirical evidence.

**Original Day-28 sequencing** (deprecated):
- #4: EuroLeague (~250/7d per scope-doc estimate)
- #5-7: PLK, BBL, VTB

**Day-31 re-sequencing** (active, per PR #214 merged Day-31 afternoon):
- #4: Israeli BSL (~300/7d, single country, low methodology risk) — APPLIED TODAY
- #5: Turkish BSL (~100+/7d, single country, top-5-football cross-sport)
- #6: Greek HEBA A1 (~50+/7d, single country, top-5-football cross-sport)
- #7: Russian VTB (~150/7d, single country)
- #8: EuroLeague (cross-country aggregator, ~50-80 residual, gap-fill after #4-7)
- #9: Serbian KLS / ABA League (~40/7d, multi-country ABA)

This is the **second empirical validation of amendment #15** (Day-28 surfaced it on Liga ACB volume estimates; Day-31 surfaces it on EuroLeague composition). PR #214 updated `docs/bootstraps/phase-2d5a-sequencing-decision.md` with the Day-31 addendum.

### Day-31 afternoon: New methodology dimensions captured in Israeli BSL workstream

PR #215 introduced three new methodology dimensions worth pinning:

**1. Within-league bare-prefix discipline (NEW)**

Five Israeli sports-club prefixes (Maccabi, Hapoel, Ironi, Bnei, Elitzur) appear across 4+ BSL teams each. Bare prefix aliases would produce within-league collision. EXCLUDED in manifest:
- "Maccabi" bare (4 BSL teams share)
- "Hapoel" bare (6 BSL teams share)
- "Ironi" bare (4 BSL teams share)
- "Bnei" bare (future-promotion collision risk)
- "Elitzur" bare (Elitzur Yavne in Liga Leumit + future-promotion risk)

This is a stronger form of F2 alias-distinctiveness than previous bootstraps. Italian LBA's "Virtus" exclusion was a single-prefix case; Israeli BSL's 5-prefix exclusion generalizes the pattern.

**2. Apostrophe + slash handling (NEW)**

"Hapoel Be'er Sheva/Dimona" requires both apostrophe-stripped (Beer Sheva) AND apostrophe-retained (Be'er Sheva) variants because the normalizer treats apostrophe as punctuation (stripped to space) which produces DIFFERENT normalized keys:
- "Hapoel Be'er Sheva" → `hapoel be er sheva`
- "Hapoel Beer Sheva" → `hapoel beer sheva`

Both must be aliases. Same pattern for "Ra'anana"/"Raanana".

**3. Highest cross-sport collision count of Phase 2D.5-A so far (11 of 14)**

Israeli BSL: 11 of 14 cities have football counterparts. Comparison:
- LMB Day-28: 0 cross-sport collisions
- Liga ACB Day-29: 2 cities (Real Madrid, Barcelona)
- Italian LBA Day-31 morning: 4 cities (Milano, Bologna, Napoli, Venezia)
- Israeli BSL Day-31 afternoon: 11 cities (Tel Aviv, Jerusalem, Be'er Sheva, Holon, Ra'anana, Ness Ziona, Ramat Gan, Herzliya, Rishon LeZion, Netanya, plus Tel Aviv shared by Maccabi + Hapoel)

The Maccabi/Hapoel/Ironi/Bnei/Elitzur prefix system serves as natural sport-disambiguator: bare cities EXCLUDED, but prefixed forms ("Maccabi Tel Aviv", "Hapoel Tel Aviv") SAFE.

### Day-31 afternoon: Operator-raised question — maintenance for league/team changes over time

Mid-afternoon operator question worth pinning: "would we need to do this as leagues and teams change?"

Yes. Forces driving ongoing maintenance:
- **Annual roster turnover** (~10-30% per league per year): promotion/relegation, new sponsors, team rebrands
- **Production drift detection**: SAME Pattern A.2 discovery query that BUILDS manifests will surface STALE manifests (Trapani Shark replacement, etc.)
- **Provider format changes**: rare but real (asterisk-suffix pattern emergence example from Day-30)

**Recommended maintenance design** (to be implemented post-Phase-2D.5-A):
1. **Monthly discovery-query cron**: parameterized over `baseline_shifts` rows where `event_type LIKE 'phase_2d5a_%'`, reports diff (new high-volume provider strings + zero-strict-resolution manifest teams)
2. **F7 health monitoring**: per-league F7 JOIN queries daily/weekly, sudden drops = manifest staleness signal
3. **Annual roster-refresh runbook**: documented operator workflow using existing idempotent bootstrap scripts

**Cost projection**:
- Tooling: ~2 days one-time investment
- Per-league refresh: ~30 min/year operator time
- For 9 leagues at completion: ~5 hours/year total maintenance

**Deferred to post-Phase-2D.5-A**. Current bootstrap methodology track record (3 leagues applied, 2 validated, declining cost-per-league per amendment #21) doesn't justify maintenance-tooling investment yet. Revisit when Phase 2D.5-A wraps OR when maintenance pain materializes.

Captured as scope-doc follow-up; not a Phase 2D.5-A scope item. Operator-asked-question artifact preserved here for future session reference.

### Day-31 afternoon: Architectural question on FL API vs bootstrap methodology

Mid-afternoon operator question worth pinning: "why are we following this methodology instead of using fl api?"

Three interpretations of "use FL API":
1. **Query FL `/teams` for league rosters**: doesn't solve the actual problem (production strings vary across providers; FL gives one canonical, we need multiple aliases)
2. **Auto-create sp.teams from FL ingestion strings**: rejected by design (trust boundary, cross-sport collision propagation, no semantic anchor); Day-27 PROJECT_STATE entry captures the architectural decision
3. **Use FL tournament_stage_id as anchor**: Kalshi has zero relationship to FL's tournament IDs; cross-provider corroboration value-add requires `sp.team_aliases` as convergence point

**Conclusion**: Bootstrap methodology stays. FL API supplements the work (FL's `HOME_NAME`/`AWAY_NAME` ARE the strings we match against `sp.team_aliases`; `sp.fl_events.sport_id` from Phase 2A.7 IS the FL sport-tier anchor) but doesn't replace it. Cost comparison (60-90 min one-time bootstrap vs 1-2 week architectural pivot) favors bootstrap methodology for current Phase 2D.5-A scope.

Captured as future-session reference; not a methodology change.

### Day-31 afternoon: Phase 2D.5-A progress

**4 of 9 leagues now applied:**
- ✅ Workstream #1 (LMB): Day-28 apply, Day-29 morning F7 validation (18 strict / 6 teams)
- ✅ Workstream #2 (Liga ACB): Day-29 afternoon apply, Day-30 morning F7 validation (41 strict / 11 manifest teams + 2 EuroLeague crossovers)
- ✅ Workstream #3 (Italian LBA): Day-31 morning apply (13 INSERT + 3 BACKFILL + 86 aliases), F7 opens ~03:39 UTC Day-32
- ✅ Workstream #4 (Israeli BSL): Day-31 afternoon apply (9 INSERT + 5 BACKFILL + 43 aliases), F7 opens ~04:56 UTC Day-32
- ⏳ Workstream #5-9: Turkish BSL, Greek HEBA, Russian VTB, EuroLeague (gap-fill), Serbian/ABA

**Cumulative methodology lift since Phase 2D.5-A began (4 leagues applied, 2 empirically F7-validated)**:
- 59 strict resolutions in LMB + ACB combined ~31 hours (~46/day average)
- 26 distinct previously-missing teams now resolving (6 LMB + 10 Liga ACB + 13 LBA INSERTs awaiting F7 + 9 BSL INSERTs awaiting F7 = pre-F7-tally)
- 13 BACKFILL teams successfully promoted across 4 workstreams (3 LMB + 2 ACB + 3 LBA + 5 BSL)

Israeli BSL expected F7 yield: ~50-100 strict resolutions in first 14-17h post-apply (~3× higher than LBA per ~3× higher discovery volume, plus 5 BACKFILLs unlock pre-existing fixture history).

### Day-31 PR state (afternoon)

- Morning batch (PR #213): Italian LBA apply + daily-diff Baseball trajectory + schema-verification erratum (MERGED)
- Afternoon PR #214: Sequencing-decision Day-31 addendum (re-sequencing workstreams #4-9) (MERGED)
- Afternoon PR #215: Israeli BSL workstream design + manifest + script + tests + scope-doc (MERGED)
- Afternoon journal batch (this entry, separate PR)

### Pending — next, operator review (Day-32 morning)

1. **Italian LBA F7 verification** — opens ~03:39 UTC Day-32 (14h post-apply). JOIN template with `country_code='ITA'`, apply timestamp `2026-06-02 13:39:51+00`. Expected: ~25-40 strict resolutions per scope-doc projection.
2. **Israeli BSL F7 verification** — opens ~04:56 UTC Day-32 (14h post-apply). JOIN template with `country_code='ISR'`, apply timestamp `2026-06-02 14:56:10+00`. Expected: ~50-100 strict resolutions per scope-doc projection.
3. **Day-32 daily-diff** — Baseball trajectory data point 6, Basketball trajectory data point 4 (compounding LBA + BSL inflation per amendment #20).
4. **Turkish BSL workstream #5** — pre-scope Pattern A.2 discovery query first (already partially surfaced via Day-31 EuroLeague discovery), then authoritative-source roster, then manifest + script + tests + scope-doc per amendment #14 single-PR convention.

### Day-31 morning: Italian LBA APPLIED + ANNOTATED (workstream #3 EMPIRICALLY APPLIED)

Apply at 2026-06-02T13:39:51 UTC. Runtime 13.3s, 0 errors. Same Pattern D pre-flight → dry-run → wet apply sequence as LMB and Liga ACB.

**Apply results:**
- **13 new LBA canonicals inserted** (sp.teams, sport_id=3, country_code='ITA')
- **3 BACKFILLs**: Olimpia Milano (43f96b2e-9694-44ee-a9dc-31cbf981b99b), Reyer Venezia (51dd1cd7-eff8-4b7b-b16f-810c770a1048), Virtus Bologna (28be3ef3-634a-4abf-bfa9-9b7681e6556c). All three were Phase 2A.5 Basketball entities (created 2026-05-08) without country_code. The bootstrap's three-branch classifier matched them on `normalized_name` against the manifest's canonicals (NFD accent stripping + Italian diacritic handling working as designed). All three are historically-prominent Italian basketball clubs with EuroLeague/EuroCup presence that existed in legacy `public.entities` pre-Phase-2A.5.
- **86 aliases inserted**, 4 deduped within batch (within-manifest duplicates like Cantù+Cantu and Brescia+Brescia*, not cross-team conflicts), 0 global conflicts (PR #200 alias-safety discipline held)
- `bootstrap.lba.pattern_d.ok` confirmed production endpoint pre-write
- `existing_teams_loaded`: 1,997 Basketball teams (sport_id=3) — baseline pre-apply (Liga ACB Day-29 baseline was 1,981, +16 Liga ACB teams between Day-29 and today = 1,997, sanity check passes)

**F1 discipline reaffirmed**: BACKFILL branch updates `country_code` only, NOT `canonical_name`. Olimpia Milano / Reyer Venezia / Virtus Bologna keep their legacy canonicals; current sponsored forms ("EA7 Emporio Armani Milano", "Umana Reyer Venezia", "Virtus Segafredo Bologna") live as aliases attached to the existing rows. Same precedent as LMB's Bravos de León, Liga ACB's Basket Zaragoza / Basquet Girona, KBL's Goyang Sono Skygunners.

**Pattern A.2 sequencing improvement (amendment #21) empirically validated**: 0 rounds of corrections required (vs LMB's 3 rounds). Pre-scope discovery query run BEFORE Wikipedia roster sourcing caught 4 Serie A2/B leakage targets (Fortitudo Bologna, Tezenis Verona, Virtus Roma 1960, Rucker San Vendemiano) and confirmed all 10 in-scope provider forms map to manifest teams. Methodology now generalized cleanly across 3 leagues with progressively cleaner application (LMB 3 rounds → ACB 1 round → LBA 0 rounds).

**baseline_shifts annotation**: row `e937cee8-365c-453e-8cf7-933d0cde4e1c` (event_type='phase_2d5a_lba_bootstrap', event_date=2026-06-02). Amendment #19 idempotency discipline applied: pre-flight SELECT confirmed 0 existing rows before INSERT. No duplicate-INSERT incident.

### Day-31 morning: Schema-verification erratum + amendment #12 worked example #5

While drafting the baseline_shifts INSERT statement, Claude assistant referenced column `expected_delta` (incorrect) instead of `expected_metric_delta` (actual). The INSERT failed with `SQLSTATE 42703` ("column does not exist"). Recovery via `information_schema.columns` query → corrected column name → INSERT succeeded on retry.

**This is the fifth worked example of v1.5 amendment #12 generalizing** (artifact verification over memory):
1. Day-28 morning: Pattern D backport claim stale in journal (amendment #18)
2. Day-29 morning: F7 ILIKE filter false-positive matched NCAA Baseball (amendment #18)
3. Day-29 afternoon: baseline_shifts duplicate INSERT (amendment #19)
4. Day-30 afternoon: Claude Code refused general-knowledge manifest when WebFetch blocked
5. Day-31 morning (now): Claude assistant drafted INSERT from journal-narrative memory rather than schema verification

The pattern: claiming knowledge of an artifact (column name, code state, schema, manifest content) from memory rather than verification. Pre-flight artifact check would have caught all five.

**Cost-asymmetry on this specific case**: schema-verification query ~5 seconds; INSERT failure + error parsing + retry ~30 seconds. Small in isolation; meaningful in aggregate. Already captured by amendments #12 and #18 — no new amendment needed.

### Day-31 morning: Daily-diff 34.71% — multi-day trajectory now clearly multi-factor

Day-31 daily-diff at 13:47 UTC: 12,079 records scanned, **matcher_capability_rate 34.71%** (-2.27pp from Day-30's 36.98%).

**Capability rate progression across 6 measurements:**

| Date | Day-of-week | Records | Capability | Δ |
|---|---|---:|---:|---:|
| Day-26 (2026-05-26) | Tue | 14,401 | 47.6% | baseline |
| Day-27 (2026-05-27) | Wed | 14,847 | 46.5% | -1.1pp |
| Day-28 (2026-05-28) | Thu | 15,160 | 46.4% | -0.1pp |
| Day-29 (2026-05-29) | Fri | 13,197 | 48.3% | +1.9pp |
| Day-30 (2026-06-01) | Sun | 14,732 | 37.0% | -11.3pp |
| Day-31 (2026-06-02) | Tue | 12,079 | 34.7% | -2.3pp |

**Per-sport breakdown (Day-31 vs Day-30):**

| Sport | Day-30 | Day-31 | Δ |
|---|---:|---:|---:|
| Tennis | 19.2% | 15.5% | -3.7pp |
| Baseball | 73.6% | 71.6% | -2.0pp |
| Basketball | 51.5% | 44.6% | -6.9pp |
| Soccer | 75.2% | 68.4% | -6.8pp |
| Hockey | 66.7% | 63.6% | -3.1pp |
| American Football | 51.2% | 48.3% | -2.9pp |
| Football | 9.1% | 28.6% | +19.5pp (small denominator, noisy) |
| Aussie Rules | 3.5% | 6.6% | +3.1pp (small denominator) |
| Cricket | 6.5% | 5.9% | -0.6pp |

**Ingestion volume signal in daily-diff log:**
- Day-30: kalshi 7,096 + fl 7,636 = 14,732 total
- Day-31: kalshi 6,254 + fl 5,825 = 12,079 total
- Total provider records in 24h window: -18% Day-31 vs Day-30

Monday morning has materially fewer new provider records in the rolling 24h ingestion window. This is a separate factor from the weekend-record-mix shift documented in amendment #20.

**Aggregate matcher_capability_rate is now empirically multi-factor sensitive across observed dimensions:**
1. Weekend vs weekday record-mix shift (amendment #20 from Day-30)
2. Total ingestion volume cycling (NEW Day-31 observation — Monday window had -18% provider records)
3. Compounding LMB denominator inflation (Baseball trajectory below)
4. Compounding Liga ACB denominator inflation (Basketball -6.9pp Day-30→Day-31)
5. Tennis re-resolution backlog exhaustion (Day-30 finding holds)

### Day-31 morning: Baseball denominator-inflation hypothesis REFINED — compounding, not stabilizing

Baseball capability trajectory across 5 days post-LMB-apply:

| Date | Days post-LMB | Baseball capability |
|---|---:|---:|
| Day-27 (pre-LMB) | -1 | 86.7% |
| Day-28 (LMB apply day) | 0 | 85.2% |
| Day-29 (1d post) | 1 | 76.5% |
| Day-30 (4d post) | 4 | 73.6% |
| Day-31 (5d post) | 5 | **71.6%** |

**Cumulative drop**: -15.1pp from Day-27 baseline. **Trajectory: monotonic decline, no stabilization signal yet.**

The Day-29 afternoon hypothesis 1 framing ("If it stabilizes around 76-78%, hypothesis 1 [denominator inflation from LMB] is empirically supported") needs **refinement**:

**Refined hypothesis 1**: LMB denominator inflation does NOT produce a one-shot drop with stabilization; it produces a compounding decline as MORE LMB records accumulate in the rolling 7-day measurement window each day. Day-1 captures ~1 day of LMB activity; Day-5 captures ~5 days. The denominator continues to grow while the numerator (strict-tier LMB resolutions, ~18-31/day) stays constant.

**Empirical projection**: at -1.5 to -2pp/day continuing slope, Baseball capability could trough at 60-65% within ~10 more days as the LMB-window-saturation completes. Stabilization expected once the 7-day rolling window is fully LMB-saturated (~Day-34/35).

**F8 framing decision (amendment #20 confirmed)**: F8 success criterion for FL-only league bootstraps is the F7 league-specific JOIN query (showing ≥50% reduction in asymmetric_anchor_failure for that league's attributable records), NOT aggregate sport capability rate. The Day-29 morning LMB F7 (18 strict / 6 teams) and Day-30 morning Liga ACB F7 (41 strict / 16 teams) remain the canonical methodology validation.

### Day-31 morning: Window-overlap methodology observation

Discriminator query (Day-30 vs Day-31 per-sport volume) revealed identical record counts for 4 low-volume sports (Handball 3693, Volleyball 243, Football 84, Lacrosse 63 — all unchanged Day-30→Day-31). Investigation: Day-30 daily-diff window ended 2026-06-01 20:12 UTC; Day-31 daily-diff window started 2026-06-01 13:47 UTC — windows overlap by ~6.5 hours. For sports where all records in the overlap window are the same set, counts are identical (these sports had rare events in the 6.5h overlap).

Most volume changes for high-volume sports are real (Tennis +1,790, Golf +4,068, Baseball +507) since proportional contribution from the overlap is small. Low-volume comparisons (Football 84, Lacrosse 63) are artifact-bound.

**Methodology note**: future discriminator queries between consecutive daily-diff windows should use non-overlapping 23:00-23:00 UTC daily windows (or align windows to a fixed day boundary) to eliminate overlap effects. Not blocking; daily-diff timing is operator-driven and varies with session start time. Filed as low-priority tech-debt observation. No new amendment.

### Day-31 morning: PowerShell env vars dropping continued (3rd consecutive session)

Friction observation, 3rd occurrence. PowerShell `DATABASE_URL`, `EXPECTED_PRODUCTION_DB_NAME`, `EXPECTED_PRODUCTION_DB_HOST` had to be re-set at session start today, same as Day-29 and Day-30. Pattern D pre-flight catches the missing-env case correctly (safety mechanism working).

Three mitigation options still captured (no decision yet):
- `.env` file with python-dotenv auto-load
- PowerShell `$PROFILE` script export
- Convenience script `scripts/setup_env.ps1` reading from gitignored `.env.local`

Pattern: 3 of 3 sessions with this friction. Worth promoting from "tech-debt observation" to "small workstream candidate" if the friction continues into Day-32+.

### Day-31 morning: Phase 2D.5-A progress check

**3 of 6 leagues now applied + 2 of 3 empirically validated:**
- ✅ Workstream #1 (LMB): Day-28 apply, Day-29 morning F7 validation (18 resolutions / 6 teams)
- ✅ Workstream #2 (Liga ACB): Day-29 afternoon apply, Day-30 morning F7 validation (41 resolutions / 11 manifest teams + 2 EuroLeague crossovers)
- 🟡 Workstream #3 (Italian LBA): Day-31 morning apply complete, F7 validation opens ~03:39 UTC Day-32 (~14h post-apply)
- ⏳ Workstream #4-7: EuroLeague, PLK, BBL, VTB+others — sequence per `docs/bootstraps/phase-2d5a-sequencing-decision.md`

**Cumulative methodology lift since Phase 2D.5-A began (LMB + ACB only, LBA F7 pending)**:
- 59 strict resolutions (~46/day average over LMB + ACB applied windows)
- 16 distinct previously-missing teams now resolving (6 LMB + 10 Liga ACB; LBA pending Day-32 F7)
- 5 BACKFILL teams successfully promoted (3 LMB + 2 Liga ACB; 3 more pending LBA F7 = 8 total)

### Day-31 PR state (morning)

- Italian LBA apply (no PR — apply itself is operational, not code change)
- baseline_shifts annotation (no PR — DB row INSERT)
- Morning batch (this entry, separate PR)

### Pending — next, operator review (Day-31 afternoon + Day-32 morning)

1. **EuroLeague workstream #4 pre-scope discovery** — Pattern A.2 sequencing per amendment #21. Run discovery query against production sp.resolution_log for EuroLeague-pattern unresolved records (Olympiacos, Panathinaikos, Fenerbahce, CSKA, Maccabi Tel Aviv, etc.) BEFORE Wikipedia roster sourcing. 10-country multi-country scope; 4-team Liga ACB overlap; methodology now well-rehearsed.
2. **Italian LBA F7 verification (Day-32 morning)** — opens ~03:39 UTC Day-32 (14h post-apply). JOIN template with `country_code='ITA'`, apply timestamp `2026-06-02 13:39:51+00`. Expected: ~25-40 strict resolutions per scope-doc projection.
3. **Day-32 daily-diff** — Baseball trajectory data point 6 (continue refining hypothesis 1). Basketball trajectory data point 4 (compounding from Liga ACB + LBA).
4. **Day-31 afternoon or Day-32 morning journal batch** — F7 LBA validation + EuroLeague workstream progress.

---

## Session — 2026-06-01

### Day-30 afternoon: Italian LBA workstream #3 DESIGN-COMPLETE (PR #211, merged)

Workstream #3 of Phase 2D.5-A. Single PR per amendment #14 (methodology proven on Liga ACB workstream #2). Apply deferred to Day-31 morning per operator fresh-attention discipline.

**Deliverable (PR #211, 1,319 lines net add):**
- `scripts/lba_seed.py` (295 lines) — 16-team manifest, 90 raw aliases / 86 unique-normalized
- `scripts/bootstrap_lba.py` (405 lines) — apply script mirroring `bootstrap_acb.py`, shared `_check_pattern_d_endpoint` import (amendment #17), PR #200 alias-safety discipline
- `tests/test_bootstrap_lba.py` (344 lines) — manifest-shape, diacritic, cross-sport collision, discovery-target, roster-membership tests
- `docs/bootstraps/phase-2d5a-italian-lba.md` (275 lines) — F1-F8 framing, discovery query table, scope decisions, 3 open follow-ups

**Roster source**: operator-verified Wikipedia "2025-26 LBA season" paste (Day-30 afternoon). 16 teams confirmed against Pattern A.2 discovery query results.

**Cross-sport collision discipline expanded to 4 Italian Serie A football overlaps**:
- Milano (AC Milan, Inter Milan) — bare alias EXCLUDED, "Olimpia" / "EA7" / "Armani" sport-disambiguators
- Bologna (Bologna FC) — bare alias EXCLUDED, "Virtus" sport-disambiguator
- Napoli (SSC Napoli) — bare alias EXCLUDED, "Basket" sport-disambiguator
- Venezia (Venezia FC) — bare alias EXCLUDED, "Reyer" sport-disambiguator

Plus within-LBA "Virtus" within-league collision discipline: bare "Virtus" EXCLUDED (multiple Italian basketball Virtus clubs exist).

**Sport_id partition reasoning preserved**: Reggiana (AC Reggiana 1919 football exists) and Udine (Udinese Calcio exists) kept bare per Day-22 sport_id partition finding — matcher-layer safe, and discovery query shows FL sends bare forms (Reggiana 28/7d). Operator-clarity discipline applied to top-five-recognition football overlaps only.

### Day-30 afternoon: Pre-scope discovery query saved Pattern A.2 risk

Methodology improvement worth documenting. The Day-30 pre-scope discovery query (Pattern A.2 discipline) was run BEFORE Wikipedia roster sourcing rather than after. Compare to LMB Day-27 sequence:
- **LMB Day-27**: Wikipedia roster drafted → Pattern A.2 against production found 6 missing + 2 phantom teams → 3 rounds of corrections
- **Italian LBA Day-30**: Pre-scope discovery query run against production FIRST → Wikipedia roster verified against discovery results → 4 out-of-scope Serie A2/B leakage targets identified BEFORE manifest commit → 0 rounds of corrections

The 16 in-scope Wikipedia roster teams matched the in-scope discovery targets cleanly; the 4 out-of-scope discovery hits (Fortitudo Bologna, Verona/Tezenis, Virtus Roma 1960, Rucker San Vendemiano) were correctly classified as Serie A2/B leakage and excluded from manifest BEFORE drafting.

**Implication**: Pattern A.2 pre-scope discovery is more efficient when run before authoritative-source roster sourcing, not after. Future bootstraps should follow Day-30 sequence: production discovery first, authoritative-source verification second.

### Day-30 afternoon: Serie A2/B FL leakage finding (~80 records/week noise)

The Italian LBA discovery query surfaced 4 distinct team patterns at material occurrence rates that DO NOT belong in 2025-26 LBA Serie A:

| Provider string | Occurrences/7d | True league |
|---|---:|---|
| Fortitudo Bologna | 28 | Serie A2 |
| Verona / Verona * | 28 + 14 = 42 | Serie A2 (Tezenis Verona / Scaligera Basket) |
| Virtus Gvm Roma 1960 | 10 | Serie A2/B |
| Rucker San Vendemiano | 6 | Serie A2 |
| **Total noise** | **~80/7d** | **Serie A2/B leakage via FL Basketball sport_id** |

These records get routed to the Basketball matcher (sport_id=3) but have no matching `sp.teams` entries because they're not in LBA Serie A. They will continue to flow to review_queue / no_match indefinitely until either (a) FL ingestion classifies Serie A2/B records under a separate sport_id, or (b) a separate Serie A2 bootstrap workstream is scoped.

Decision: out-of-scope for LBA Serie A workstream #3. Captured in PR #211 §6.3 as follow-up investigation. Phase 2D.5-A scope sticks to top-tier league bootstraps for now; Serie A2 / EuroCup / FIBA Europe Cup secondary-league workstreams are deferred.

### Day-30 afternoon: Tennis investigation reframes Tennis workstream priorities

Three substantive findings from Day-30 afternoon Tennis drilldown via discriminator queries. These refine the morning journal's framing of Tennis as "Sunday challenger/ITF mix harder" + "re-resolution backlog exhausted." Both factors are real; the substantive mechanism is more specific.

**Finding 1: Kalshi Tennis tickers are near-zero strict-tier reachable**

Day-30 production data across all Kalshi Tennis ticker patterns:

| Kalshi Tennis ticker pattern | Records (Day-30) | Strict resolutions | Strict rate |
|---|---:|---:|---:|
| ATP Tour (KXATPMATCH-*) | 845 | 2 | 0.24% |
| ATP Challenger (KXATPCHALLENGERMATCH-*) | 1,584 | 0 | 0.00% |
| WTA Tour (KXWTAMATCH-*) | 864 | 0 | 0.00% |
| ITF Women (KXITFWMATCH-*) | 6,087 | 0 | 0.00% |
| ITF other (KXITF*) | 6,897 | 0 | 0.00% |
| Kalshi other Tennis | 549 | 0 | 0.00% |
| **All Kalshi Tennis combined** | **16,826** | **2** | **0.012%** |

Compare to FL Tennis: 14,325 records / 60 strict resolutions = **0.42%** strict rate.

Implication: Kalshi Tennis uses surname-only abbreviated tickers (e.g., "Rogers" vs "Kalieva") that do NOT match `sp.teams.canonical_name` entries (which are mostly full names or FL-format "Last F. (Country)"). The strict-tier AliasIndex has no surname-to-team_id mapping for these tickers. This empirically confirms the Day-17 Finding 1 framing.

**Finding 2: FL Tennis is the dominant Tennis population**

FL records are 14,325 of 31,151 Tennis records Day-30 (46%). Day-29/Day-30 Tennis capability rates (24.6% / 19.2%) are dominated by FL pipeline performance, not Kalshi pipeline performance.

Tennis workstreams should split into two distinct optimization targets:
- **FL Tennis**: alias completeness within "Last F. (Country)" canonical format → modest lift potential
- **Kalshi Tennis**: surname-aware matching workstream (scope-doc §1.6, deferred) → large lift potential, requires methodology change

**Finding 3: Tennis dedup workstream lift mechanism was alias-tier, not strict-tier**

The Tennis dedup workstream's measured +8.62pp cumulative lift (Day-26 baseline 15.98% → Day-29 24.6%) cannot have come from strict-tier coverage — Day-30 production shows only 62 strict-tier Tennis resolutions in a 24-hour window across all 31,151 Tennis records (FL 60 + Kalshi 2).

The +8.62pp lift was alias-tier auto-apply enablement. Pre-dedup, FL records routed to review_queue under alias-tier collision detection because `sp.teams` contained multiple rows for the same player in different canonical formats (Kalshi-format vs FL-format duplicates). Post-dedup, the same FL records resolve via alias-tier without collision, hitting the auto-apply threshold (0.95-1.00 bucket per Day-21 bimodal histogram).

This refines the morning journal's "two contributing factors" framing. Both factors (Sunday challenger/ITF mix + dedup backlog exhaustion) are real, but the underlying mechanism for Tennis dedup's lift is alias-tier auto-apply, not strict-tier coverage.

**Forward-pointer to Tennis surname workstream**: The empirical ceiling for further Tennis lift via current strict + alias + fuzzy matcher is approximately the FL strict + alias rate (~25-30%). Crossing the ~30% Tennis capability ceiling requires the surname-aware matching workstream from scope-doc §1.6.

### Day-30 afternoon: Claude Code blocker handling — amendment #12 generalizes again

Worked example. Italian LBA workstream #3 manifest sourcing hit a sandbox egress blocker: Claude Code's WebFetch returned HTTP 403 from all 8 candidate authoritative sources (en.wikipedia.org, it.wikipedia.org, proballers.com, legabasket.it, basketball-reference.com, es.wikipedia.org, realgm.com, grokipedia.com). Direct curl from Bash also returned 403, confirming sandbox-side block.

**Claude Code's response (verbatim from session log):**
> "Drafting the manifest from my general-knowledge would replay the LMB 3-round correction cycle. The operator's instructions explicitly bind this work to authoritative-source artifact verification before paste (amendment #12), and the discovery query alone confirms ~10 of 16 — not enough to lock the manifest."

Claude Code presented 3 paths (operator paste, alternate-URL source, explicit-override-to-proceed-from-memory) and stopped rather than drafting from general knowledge. Operator-paste recovery cleanly resumed the workstream within minutes.

**Significance**: amendment #12 ("artifact paste over summary") originally addressed multi-agent verification handoffs. Day-28 morning surfaced it generalizing to journal claims about code state (#18). Day-29 afternoon surfaced it for baseline_shifts annotation idempotency (#19). Today, the agent itself refused to skip artifact verification when the canonical path was blocked, citing the amendment as the gating constraint.

This is the **fourth worked example** of the v1.5 amendment #12 epistemic shape generalizing:
1. Day-28 morning: Pattern D backport claim stale in journal narrative (#18)
2. Day-29 morning: F7 ILIKE filter false-positive matched NCAA Baseball (#18)
3. Day-29 afternoon: baseline_shifts duplicate INSERT (#19)
4. Day-30 afternoon: Claude Code refused general-knowledge manifest draft when WebFetch blocked

The pattern is now sufficiently robust to consider it institutionalized. No new amendment needed; existing amendments #12, #13, #18 cover the discipline.

### v1.5 amendment #21 (NEW)

**Pre-scope discovery query (Pattern A.2) is more efficient when run BEFORE authoritative-source roster sourcing, not after.** LMB Day-27 sequence (Wikipedia draft → Pattern A.2 verification → 3 rounds of corrections) took 6 missing + 2 phantom teams to detect. Italian LBA Day-30 sequence (Pattern A.2 discovery → Wikipedia verification against discovery results → 0 rounds of corrections) caught 4 out-of-scope leakage targets BEFORE manifest commit. Future bootstraps should follow Day-30 sequence: production discovery first, authoritative-source verification second, manifest commit third.

Mitigation captured: this is process improvement, not tooling. Already reflected in `docs/bootstraps/phase-2d5a-italian-lba.md` §2.

Pile expanded from 20 to 21 items.

### Day-30 afternoon: PowerShell env vars dropping between sessions (friction observation, 2nd occurrence)

Friction-pattern observation, not blocking. Today (Day-30) was the second consecutive session where the PowerShell environment variables (`DATABASE_URL`, `EXPECTED_PRODUCTION_DB_NAME`, `EXPECTED_PRODUCTION_DB_HOST`) had to be re-set at session start. Day-29 was the first occurrence.

The Pattern D pre-flight is doing its safety job correctly (refuses to proceed when env vars missing), but the operator workflow could be smoother.

Three mitigation options, captured for future consideration:
- `.env` file with python-dotenv auto-load (requires committing or gitignored secret handling)
- PowerShell `$PROFILE` script export (per-machine setup)
- Convenience script `scripts/setup_env.ps1` reading from gitignored `.env.local`

Not blocking; Pattern D safety mechanism working. Filed as tech-debt observation.

### Day-30 afternoon: Phase 2D.5-A progress check

**3 of 6 leagues design-complete; 2 of 6 applied + validated:**
- ✅ Workstream #1 (LMB): Day-28 apply, Day-29 morning F7 validation (18 resolutions / 6 teams)
- ✅ Workstream #2 (Liga ACB): Day-29 afternoon apply, Day-30 morning F7 validation (41 resolutions / 11 manifest teams + 2 EuroLeague crossovers)
- 🟡 Workstream #3 (Italian LBA): Day-30 afternoon design-complete (PR #211), apply Day-31 morning
- ⏳ Workstream #4-7: EuroLeague, PLK, BBL, VTB+others — sequence per `docs/bootstraps/phase-2d5a-sequencing-decision.md`

**Cumulative methodology lift since Phase 2D.5-A began**:
- 59 LMB+Liga ACB strict resolutions in ~31 hours combined (~46/day average)
- 16 distinct previously-missing teams now resolving (6 LMB + 10 Liga ACB; LBA pending Day-31 apply + F7)
- 5 BACKFILL teams successfully promoted (3 LMB + 2 Liga ACB)

Italian LBA expected F7 yield: ~25-40 strict resolutions in first 14-17 hours post-apply, scaled from Liga ACB's 41/17h with LBA's modestly-higher unresolved-record volume.

### Day-30 PR state (afternoon)

- Morning batch (PR #210): Liga ACB F7 validation + daily-diff record-mix finding + amendment #20 + Tennis observations (MERGED)
- Afternoon batch — Italian LBA scope-doc + manifest + script + tests (PR #211, MERGED)
- Afternoon journal batch (this entry, separate PR)

### Pending — next, operator review (Day-31 morning)

1. **Italian LBA apply (Workstream #3)** — Pattern D pre-flight env verification → dry-run → wet apply → F7 verification (~14-17 hours post-apply) → baseline_shifts annotation (pre-flight existence check per amendment #19; event_type='phase_2d5a_lba_bootstrap').
2. **Liga ACB F7 follow-up + Baseball capability monitoring** — continue tracking whether Baseball capability stabilizes at 76-78% (LMB denominator inflation hypothesis) or bounces back to 80%+ (noise hypothesis). Day-31 daily-diff = data point 3.
3. **Day-31 morning journal batch** — Italian LBA apply + F7 validation results + Day-31 daily-diff observations.

### Day-30 morning: Liga ACB F7 EMPIRICALLY VALIDATED

F7 verification via team_id JOIN against `sp.fixtures` revealed **41 strict-tier Liga ACB resolutions in the ~17-hour post-apply window** (2026-05-29 21:42:54 UTC apply → 2026-06-01 ~14:35 UTC sample point).

Per-team-pair breakdown (28 distinct team-pairs across 41 resolutions):

| Team Pair | Strict Resolutions |
|---|---:|
| Valencia Basket vs Bilbao Basket | 3 |
| Basket Zaragoza vs Valencia Basket | 2 |
| Basquet Girona vs Bàsquet Manresa | 2 |
| Bilbao Basket vs Basquet Girona | 2 |
| Basket Zaragoza vs Fundación CB Granada | 2 |
| Bilbao Basket vs Real Madrid Baloncesto | 2 |
| Bàsquet Manresa vs Basket Zaragoza | 2 |
| Bàsquet Manresa vs Real Madrid Baloncesto | 2 |
| CB Canarias vs Bilbao Basket | 2 |
| Fundación CB Granada vs CB Canarias | 2 |
| Real Madrid Baloncesto vs CB Canarias | 2 |
| Valencia Basket vs Real Madrid Baloncesto | 2 |
| [16 additional single-resolution team pairs across remaining matchups] | 1 each |

**11 distinct Liga ACB teams resolved cleanly via strict-tier AliasIndex**: Basket Zaragoza, Basquet Girona, Bàsquet Manresa, Bilbao Basket, CB Canarias, CB Gran Canaria, FC Barcelona Bàsquet, Força Lleida CE, Fundación CB Granada, Real Madrid Baloncesto, Valencia Basket.

Plus 2 non-manifest teams appearing as opponents in cross-league fixtures: **Panathinaikos BC** (Greek, EuroLeague) and **Rytas** (Lithuanian, EuroLeague). These resolve because they already exist in `sp.teams` from Phase 2A.5 legacy bootstrap or earlier operator approval — and Liga ACB teams are the matched side in those fixtures.

**Both branches of the three-branch classifier validated end-to-end:**
- **INSERT branch**: Bàsquet Manresa, Bilbao Basket, CB Canarias, CB Gran Canaria, FC Barcelona Bàsquet, Força Lleida CE, Fundación CB Granada, Real Madrid Baloncesto, Valencia Basket — all resolving via newly-created `sp.teams` rows with `country_code='ESP'` and accompanying aliases
- **BACKFILL branch**: Basket Zaragoza and Basquet Girona — pre-existing Phase 2A.5 stubs that received `country_code='ESP'` backfill on apply, now resolving via the SAME team_id with new aliases attached

**Cross-sport collision discipline EMPIRICALLY VALIDATED**: Real Madrid Baloncesto resolves 7 times across multiple opponent matchups; FC Barcelona Bàsquet resolves 2 times. The matcher correctly routes "Real Madrid"/"Barcelona" Kalshi/FL provider strings to the Basketball-canonical Baloncesto/Bàsquet entries via sport_id partition rather than colliding with the Soccer canonicals. Day-22 sport_id partition finding now validated in production for both LMB (no overlap) AND Liga ACB (with overlap) — full empirical coverage.

**Cross-league fixture handling EMPIRICALLY VALIDATED**: EuroLeague crossovers (Valencia vs Panathinaikos, CB Canarias vs Rytas) resolve cleanly on the Liga ACB side. When EuroLeague workstream #4 ships, these fixtures will gain full strict-tier coverage on both sides. The 4-team Liga ACB / EuroLeague overlap from sequencing decision is empirically confirmed.

Per-hour rate: 41 strict resolutions / 17 hours = **2.41/hr (vs LMB Day-29 morning's 1.29/hr)** — Liga ACB producing nearly 2× LMB's per-hour strict-tier lift. Projected ~58 strict resolutions/day extrapolated.

This is the **second clean empirical validation of the Phase 2D.5-A methodology**. Methodology has now generalized cleanly across:
- Single-country baseline (LMB)
- Multi-country light + cross-sport collision (Liga ACB)
- INSERT branch (both leagues)
- BACKFILL branch (both leagues)
- FL pipeline integration (both leagues)
- Strict-tier auto-apply (both leagues)
- Cross-league fixture handling (Liga ACB EuroLeague crossovers)

### Day-30 morning: Daily-diff -11.33pp aggregate drop, but methodology is healthy

Daily-diff Day-30 at 20:12 UTC: 14,732 records scanned, **matcher_capability_rate 36.98%** (-11.33pp from Day-29's 48.31%).

Initial reaction to the drop required careful per-sport attribution before drawing conclusions. The hypothesis Liga-ACB-denominator-inflation (parallel to LMB Baseball denominator inflation) was insufficient — Basketball only dropped -1.8pp while the aggregate dropped -11.33pp.

**Per-sport breakdown (Day-30 vs Day-29):**

| Sport | Day-29 | Day-30 | Δ | Day-30 volume Δ |
|---|---:|---:|---:|---:|
| Tennis | 24.6% | 19.2% | -5.4pp | +9.3% |
| Baseball | 76.5% | 73.6% | -2.9pp | +12.3% |
| Soccer | 80.5% | 75.2% | -5.3pp | +12.6% |
| Basketball | 53.3% | 51.5% | -1.8pp | +5.6% |
| Hockey | 71.4% | 66.7% | -4.7pp | small |
| American Football | 54.0% | 51.2% | -2.8pp | +29.4% |
| Aussie Rules | 18.2% | 3.5% | **-14.7pp** | +25.7% |
| Cricket | 9.0% | 6.5% | -2.5pp | +17.1% |
| Football | 13.5% | 9.1% | -4.4pp | small |

### Day-30 morning: Discriminator query reveals Sunday weekend record-mix shift

Per-sport record volume comparison Day-29 (Friday 24h window) vs Day-30 (Sunday 24h window) via `sp.resolution_log`:

| Sport | Day-29 records | Day-30 records | % change |
|---|---:|---:|---:|
| Tennis | 28,488 | 31,151 | +9.3% |
| Baseball | 8,944 | 10,048 | +12.3% |
| Aussie Rules | 1,044 | 1,312 | **+25.7%** |
| American Football | 279 | 361 | **+29.4%** |
| Darts | 2,073 | 2,478 | **+19.5%** |
| Rugby League | 681 | 798 | **+17.2%** |
| Cricket | 1,745 | 2,044 | **+17.1%** |
| Lacrosse | 51 | 63 | **+23.5%** |
| MMA | 3,066 | 3,111 | +1.5% |
| Soccer | 6,025 | 6,786 | +12.6% |

**Pattern is decisive**: Sunday volume grew disproportionately in low-coverage long-tail sports (Aussie Rules 0%, Darts 0%, Rugby League 0%, Cricket 6.5%, American Football 51%). These sports have minimal `sp.teams` coverage (Phase 2A.5 baseline: Aussie Rules 66 teams, Cricket 107, Rugby League 0, Darts 174, American Football 309). When their volume swells with weekend-specific content (NRL, AFL, NCAA baseball regionals, etc.), they pull the aggregate matcher_capability_rate down without indicating any methodology regression.

**Root cause**: Sunday weekend record-mix structurally differs from Friday weekday record-mix. The matcher_capability_rate is a denominator-sensitive lagging indicator; single-day swings can reach ±10pp purely from population composition.

### v1.5 amendment #20 (NEW)

**Aggregate matcher_capability_rate is denominator-sensitive to daily record-mix variation; single-day swings of ±10pp can result purely from weekend vs weekday population composition. Use weekly-window or per-sport rolling-window measurements for methodology evaluation, NOT single-day aggregate.** Day-30's -11.33pp single-day drop attributed to Sunday weekend mix shift toward low-coverage long-tail sports (Aussie Rules, Darts, Rugby League, Cricket, American Football, Lacrosse).

**Canonical methodology validation remains F7 league-specific JOIN queries**, not aggregate capability rate. The F7 query (this morning's Liga ACB validation: 41 strict resolutions / 16 distinct teams) is the empirical ground truth. Aggregate capability rate is useful only when interpreted with per-sport attribution and multi-day windowing.

Mitigation options (not selected — captured for future workstream):
- **Window aggregation**: render_daily_diff_report could compute 7-day rolling averages alongside single-day values, surfacing both the noisy and the stable signal
- **Day-of-week normalization**: track Mon-Sun cycle separately to distinguish weekday vs weekend baselines
- **Per-sport tracking dashboards**: focus methodology evaluation on per-sport rate trajectories rather than aggregate

Pile expanded from 19 to 20 items.

### Day-30 morning: Tennis dedup workstream observations

Tennis volume rose +9.3% Day-29 → Day-30 (28,488 → 31,151 records), but capability dropped -5.4pp (24.6% → 19.2%). Absolute Tennis records resolving Day-30 vs Day-29: ~5,981 vs ~7,008 — a drop of ~1,027 resolutions despite higher input volume.

Two contributing factors (both likely active):
1. **Sunday Tennis mix may be harder**: WTA/ATP/ITF Sunday schedules include more challenger and qualifying matches with surname-only Kalshi tickers, which the matcher cannot resolve without surname-aware logic (Day-17 Finding 1 territory).
2. **Tennis dedup re-resolution backlog may be exhausted**: the +8.62pp cumulative lift through Day-29 came partially from the re-resolution loop draining previously-collision-bound records into strict tier. After 4-5 daily passes, that backlog is exhausted; what's left is the genuinely-hard population.

The Tennis dedup workstream's cumulative +8.62pp lift through Day-29 is HELD — these Day-30 numbers reflect a new harder population, not a regression. But the trajectory suggests Tennis dedup's lift has approached its ceiling within the constraints of current strict-tier matching capability. **Future Tennis lift requires the Tennis surname workstream** (scope-doc §1.6, deferred from Phase 2D.5-A scope) to handle surname-only Kalshi tickers — that's the next Tennis-specific workstream when Phase 2D.5-A wraps.

### Day-30 morning: Phase 2D.5-A progress check

**3 of 6 leagues empirically validated:**
- ✅ Workstream #1 (LMB): Day-28 apply, Day-29 morning F7 validation (18 resolutions / 6 teams)
- ✅ Workstream #2 (Liga ACB): Day-29 afternoon apply, Day-30 morning F7 validation (41 resolutions / 11 manifest teams + 2 EuroLeague crossovers)
- ⏳ Workstream #3 (Italian LBA): scope-doc + manifest sourcing — Day-30 afternoon
- ⏳ Workstream #4-7: EuroLeague, PLK, BBL, VTB+others — sequence per `docs/bootstraps/phase-2d5a-sequencing-decision.md`

**Cumulative methodology lift since Phase 2D.5-A began**:
- 59 LMB+Liga ACB strict resolutions in the past ~31 hours combined (~46/day average)
- 16 distinct previously-missing teams now resolving (6 LMB + 10 Liga ACB)
- 4 BACKFILL teams successfully promoted (3 LMB + 2 Liga ACB Basque Zaragoza/Girona — wait, that's 5 total. Re-checking: LMB had 3 BACKFILLs (Bravos de Leon, Caliente de Durango, Toros de Tijuana); Liga ACB had 2 (Basket Zaragoza, Basquet Girona). Total 5.)

The methodology generalizes cleanly. Italian LBA workstream #3 inherits 17 layers of proven discipline.

### Day-30 PR state (morning)

- Morning batch (this entry): Liga ACB F7 validation + Day-30 daily-diff record-mix finding + amendment #20 + Tennis observations

### Pending — next, operator review (Day-30 afternoon)

1. **Italian LBA scope-doc + manifest sourcing** — workstream #3 of Phase 2D.5-A. Sequencing decision already committed in `docs/bootstraps/phase-2d5a-sequencing-decision.md`. Methodology mirrors Liga ACB closely (cross-sport collision in new country, single-country tighter scope).
2. **Day-30 afternoon journal batch** — Italian LBA scope-doc commit + any related findings.

---

## Session — 2026-05-29

### Day-29 afternoon: Liga ACB apply — workstream #2 EMPIRICALLY APPLIED

Apply at 2026-05-29T21:42:54 UTC. Runtime 11.66s, 0 errors. Same Pattern D pre-flight → dry-run → wet apply sequence as LMB.

**Apply results:**
- **16 new Liga ACB canonicals inserted** (sp.teams, sport_id=3, country_code='ESP' or 'AND')
- **2 BACKFILLs**: Basket Zaragoza and Basquet Girona — both pre-existed since Phase 2A.5 bootstrap (2026-05-08) as Basketball entities without country_code. The bootstrap's three-branch classifier matched them on `normalized_name` against the manifest's canonicals "Casademont Zaragoza" and "Bàsquet Girona" respectively (NFD accent stripping + Spanish diacritic handling working as designed via `resolver/alias_tier/normalize.py:104-108`).
- **83 aliases inserted**, 15 deduped within batch (within-manifest duplicates, not cross-team conflicts), 0 global conflicts (PR #200 alias-safety discipline held)
- `bootstrap.acb.pattern_d.ok` confirmed production endpoint pre-write
- `existing_teams_loaded`: 1,981 Basketball teams (sport_id=3) — baseline pre-apply

**F1 discipline reaffirmed**: BACKFILL branch updates `country_code` only, NOT `canonical_name`. Basket Zaragoza stays "Basket Zaragoza" canonically; the current sponsored name "Casademont Zaragoza" lives as an alias attached to the existing row. Same precedent as KBL's Goyang Sono Skygunners (PR #166 F1 decision) and LMB's BACKFILLs (Bravos de León retaining its canonical despite the manifest using "Bravos de Leon" without diacritic).

**F7 verification post-apply (immediate)**: zero strict resolutions, as expected — query ran ~7 minutes after apply, before any FL or Kalshi cron pass touched Basketball records. Real F7 measurement opens with tomorrow morning's cron data.

### Day-29 afternoon: Daily-diff +1.94pp overall, per-sport tells a more interesting story

Daily-diff Day-29 at 21:32 UTC: 13,197 records scanned, **matcher_capability_rate 48.31%** (+1.94pp from Day-28's 46.37%).

**Per-sport breakdown** (Day-29 vs Day-28, via `python scripts/render_daily_diff_report.py`):

| Sport | Day-29 | Day-28 | Δ | Note |
|---|---:|---:|---:|---|
| Tennis | 24.6% | 21.88% (morning entry) | **+2.7pp** | Tennis dedup lift continues to extend |
| Baseball | 76.5% | 85.17% | **-8.67pp** | Predicted in baseline_shifts annotation |
| Soccer | 80.5% | — | — | Single-day baseline (within typical range) |
| Basketball | 53.3% | — | — | Liga ACB just applied; not yet reflected |
| Hockey | 71.4% | — | — | — |
| American Football | 54.0% | — | — | — |
| Football | 13.5% | — | — | — |
| Cricket | 9.0% | — | — | — |
| Aussie Rules | 18.2% | — | — | — |

**Tennis cumulative lift, Day-26 → Day-29**: 15.98% → 20.15% → 21.88% → **24.6%** = +8.62pp cumulative from pre-dedup baseline. The Tennis dedup workstream's lift is still growing — Day-26 (pre-dedup) was 15.98%, three days later we're +8.62pp above that baseline. Re-resolution loop continuing to drain the collision-routed-from-pre-dedup backlog into strict tier.

**Baseball drop investigation needed but not alarming**: The Day-28 LMB baseline_shifts annotation pre-registered "Baseball matcher_capability_rate may dip slightly as new LMB records previously not in scope start appearing in denominator." The actual -8.67pp drop is larger than "slightly" but qualitatively in the predicted direction.

Two hypotheses for the larger-than-predicted magnitude:
1. **LMB records entering denominator**: Pre-apply, LMB records routed to no_match with NULL home_provider_normalized and were potentially filtered as signal-extraction failures (not counted in the Baseball capability denominator). Post-apply, LMB records pass signal extraction (because alias lookup succeeds on at least one side), enter the resolver pipeline, count toward the Baseball denominator, but most route to review_queue or no_match (only ~18-31/day reach strict tier per Day-29 morning measurement). The denominator grows faster than the numerator, dragging the rate down.
2. **Day-to-day baseline noise**: Baseball capability has varied 76.66% → 83.92% → 86.72% → 85.17% across recent measurements. A swing from 85% to 76% is larger than that band, but baseball game schedule and prop-market mix vary day-to-day.

**Decision**: monitor Baseball capability over the next 3-5 days. If it stabilizes around 76-78%, hypothesis 1 (denominator inflation from LMB) is empirically supported and we should adjust the F8 success criterion framing — Baseball's aggregate capability rate is no longer the right metric to validate LMB lift, since LMB contribution lifts strict resolutions but drags aggregate rate. The LMB-specific JOIN query (from this morning) becomes the canonical F7 measurement, not aggregate Baseball capability.

If it bounces back to 80%+ within 3-5 days, hypothesis 2 (noise) holds and the dip was sampling artifact.

This is a **predicted-direction-larger-magnitude finding** worth investigation but not concerning. The morning's F7 already proved LMB methodology works end-to-end at the resolution level; aggregate capability rate is a denominator-sensitive lagging indicator.

### Day-29 afternoon: baseline_shifts annotation erratum + v1.5 amendment #19

**Erratum**: Liga ACB baseline_shifts annotation was inserted twice (rows daa426d0 at 21:43:44 UTC and 5a7c445b at 21:49:44 UTC) before detection. Initial INSERT happened immediately post-apply following the LMB Day-28 precedent template (with `<timestamp>` placeholder text that we didn't catch at first); subsequent UPDATE corrected the text on both rows, then SELECT revealed the duplicate. DELETE removed row 5a7c445b; row daa426d0 retained as the canonical record.

This is the **third worked example** of the v1.5 amendment #12 epistemic shape generalizing beyond multi-agent manifest verification:
1. **Day-28**: Pattern D backport claim in journal narrative was stale (PR #206 fix)
2. **Day-29 morning**: F7 ILIKE filter false-positive matched NCAA Baseball "Mexico" pattern, required JOIN-based attribution (amendment #18 captured)
3. **Day-29 afternoon (now)**: baseline_shifts INSERT lacked existence pre-check, produced silent duplicate

### v1.5 amendment #19 (NEW)

**Production-state write operations against observability tables (baseline_shifts, dedup_audit, resolver_runs annotations, manual review_queue UPDATEs) require explicit idempotency discipline — either pre-flight existence check or schema-level UNIQUE constraint — same as bootstrap script INSERT semantics.** The Day-29 Liga ACB annotation was inserted twice because no operator workflow guards against duplicate INSERT on (event_type, event_date). Bootstrap scripts have this discipline via Pattern D + ON CONFLICT / NOT EXISTS; operational annotations don't.

Mitigation options (not selected — captured for future workstream):
- Schema-level: add UNIQUE constraint on (event_type, event_date) for baseline_shifts (would have prevented the duplicate at INSERT time)
- Workflow-level: annotation INSERTs require pre-flight `SELECT ... WHERE event_type=? AND event_date=?` check (operator discipline, same as Pattern D pre-flight)
- Tooling-level: dedicated `scripts/annotate_baseline_shift.py` with idempotency built in (parallel to bootstrap scripts' pattern)

Filed as tech-debt note. Not blocking; the dedup cleanup pattern (SELECT to find duplicates → DELETE the redundant row) is a reasonable operator fallback for now.

Pile expanded from 18 to 19 items.

### Day-29 cumulative methodology results

**Phase 2D.5-A progress: 2 of 6 leagues applied**
- ✅ Workstream #1 (LMB): Day-28 apply, Day-29 morning F7 validation (18 resolutions / 6 teams)
- ✅ Workstream #2 (Liga ACB): Day-29 afternoon apply, F7 validation tomorrow morning
- ⏳ Workstream #3 (Italian LBA): scope-doc + manifest sourcing — pending, sequencing decision committed
- ⏳ Workstream #4-7: EuroLeague, PLK, BBL, VTB+others — sequence per `docs/bootstraps/phase-2d5a-sequencing-decision.md`

**Methodology momentum**: Liga ACB applied without surprises — methodology generalized cleanly from LMB. Cross-sport collision discipline (Real Madrid Baloncesto, FC Barcelona Bàsquet canonicals) survived the apply unchanged. BACKFILL branch handled pre-existing Phase 2A.5 stubs (Basket Zaragoza, Basquet Girona) correctly via normalized_name match. PR granularity calibration (PR #204 single-PR delivery) proved out — fewer commits, faster delivery, no quality loss vs LMB's two-PR Day-27 split.

### Day-29 PR state (afternoon)

- **Morning batch (PR #208)**: Pattern D erratum + amendment #18 + LMB F7 validation + FL pipeline finding + FL reason_detail finding + F7 JOIN template (MERGED)
- **Afternoon batch (this entry)**: Liga ACB apply + daily-diff per-sport breakdown + amendment #19 (annotation idempotency) + baseline_shifts dedup erratum

### Pending — next, operator review

1. **Liga ACB F7 verification (tomorrow morning)** — use this morning's JOIN template with apply timestamp 2026-05-29T21:42:54+00 and country_code IN ('ESP', 'AND') filter. Expected: meaningful strict resolutions if Liga ACB games occur in the cron window. Same shape as Day-29 morning LMB F7 (~14 hours post-apply, 18 resolutions / 6 teams).
2. **Baseball capability monitoring** — track Day-30 / Day-31 daily-diff. If Baseball stabilizes at 76-78%, hypothesis 1 (LMB denominator inflation) is supported; adjust F8 framing for FL-only league bootstraps.
3. **Italian LBA scope-doc + manifest sourcing** — sequencing decision already committed (`docs/bootstraps/phase-2d5a-sequencing-decision.md`); execution begins Day-30 or Day-31 post-Liga-ACB-F7-confirmation.

### Day-29 morning: Day-28 erratum (Pattern D backport claim was stale)

The Day-28 journal entry (PR #205) listed "Backport Pattern D pre-flight import to bootstrap_lmb.py" as pending follow-up item #4. Day-28 evening verification (Select-String against `scripts/bootstrap_lmb.py` lines 50-107) confirmed the backport was **already complete** as of Day-27 PR #203 commit `d660b01` ("address 5 review concerns"). Only the docstring's exit-code-3 entry on line 31 lagged — it still listed only `sp.sports missing or doesn't contain 'Baseball'` without the Pattern D failure case.

PR #206 shipped the 1-line docstring fix Day-28 evening. The journal narrative claiming "bootstrap_lmb.py only logs current_database" was drafted from operator/Claude memory rather than from a grep against the actual file state.

**This is v1.5 amendment #12 ("artifact paste over summary") generalizing to journal claims about code state.** The amendment originally addressed multi-agent verification handoffs (LMB manifest verification, 3 rounds before paste). Day-28's Pattern D claim demonstrates that the same epistemic risk applies to journal narratives — stale claims propagate via merge into canonical docs and waste downstream effort (a "backport" PR task that would have been a no-op).

### v1.5 amendment #18 (NEW)

**Journal entries claiming code state must be grounded in artifact verification at write time, not operator/Claude memory of prior discussion.** Stale claims propagate via merge into canonical PROJECT_STATE.md and waste downstream effort. Mitigation: every PROJECT_STATE.md claim of form "X is missing from file Y" or "Y has Z but not W" requires a fresh grep/cat/Select-String artifact paste in the drafting workspace before the claim is committed. Same discipline as amendment #12 (manifest verification), extended to journal-narrative drafting itself.

Pile expanded from 17 to 18 items.

### Day-29 morning: F7 verification confirms LMB apply EMPIRICALLY VALIDATED

F7 verification via team_id JOIN (bypassing the `reason_detail` JSON which is sparse for FL records — see finding below) revealed **18 strict-tier LMB resolutions in the 14-hour post-apply window** (2026-05-28 19:41 UTC → 2026-05-29 09:00 UTC sample point).

| Team Pair | Strict Resolutions |
|---|---:|
| Toros de Tijuana vs Sultanes de Monterrey | 6 |
| Sultanes de Monterrey vs Caliente de Durango | 3 |
| Conspiradores de Querétaro vs Pericos de Puebla | 3 |
| Conspiradores de Querétaro vs Bravos de Leon | 3 |
| Pericos de Puebla vs Bravos de Leon | 3 |

**Six distinct LMB teams resolved cleanly via strict-tier AliasIndex**: Toros de Tijuana, Sultanes de Monterrey, Caliente de Durango, Conspiradores de Querétaro, Pericos de Puebla, Bravos de Leon.

**Both branches of the three-branch classifier validated end-to-end:**
- **INSERT branch**: Sultanes de Monterrey, Conspiradores de Querétaro, Pericos de Puebla — all resolving via newly-created `sp.teams` rows with `country_code='MEX'` and accompanying aliases
- **BACKFILL branch**: Toros de Tijuana, Caliente de Durango, Bravos de Leon — all resolving via pre-existing stub rows that received `country_code='MEX'` backfill on apply

This is the **first empirical validation of the Phase 2D.5-A methodology hypothesis**. The Day-27 9-layer Pattern A.2 investigation predicted that league bootstraps would lift asymmetric_anchor_failure records to strict-tier auto-apply. Day-29 F7 confirms with real production data: 18 records, 6 teams, 100% clean strict resolutions across both INSERT and BACKFILL branches.

LMB-attributable lift projection: ~31 strict resolutions/day extrapolated from the 14h window, against the F8 success criterion of ≥50% reduction in asymmetric_anchor_failure inflow for Baseball over a 7-day window. Full 7-day measurement window opens 2026-06-04.

### Day-29 morning: LMB flows through FL pipeline, not Kalshi

Discovered during F7 attribution drilldown. All 18 LMB strict resolutions have 8-character provider_record_ids (e.g., `ptrmOLkn`, `QHE5Fi7E`, `M9ysjGM5`) — FL provider format. `sp.kalshi_markets WHERE ticker LIKE 'KXLMB%'` returns empty.

**Implication:** Kalshi does not list LMB markets (or uses a different ticker prefix not yet observed). The Day-22 amendment #10 ("FL-only sports have structural review_queue floor at 0.70 without cross-provider corroboration") applies to LMB — alias/fuzzy-tier matches for LMB cap at 0.70 confidence and route to review_queue without strict-tier resolution. Bootstrap value for LMB is **entirely gated on strict-tier coverage**.

The Day-29 result confirms strict-tier is the operational mechanism: 18/18 LMB resolutions came through strict tier via AliasIndex lookup, exactly as the amendment #10 framing predicted.

**No action required** — the manifest's 63 aliases were designed to maximize strict-tier reachability under the FL-only constraint. This finding is documentation, not a methodology problem.

### Day-29 morning: FL strict resolutions have sparse reason_detail JSON

Discovered during F7 attribution attempt. Original F7 query filtered by `reason_detail->>'home_provider_normalized'` and `reason_detail->>'away_provider_normalized'` ILIKE patterns. Result: zero LMB matches via the JSON path, despite 18 actual LMB strict resolutions present in the table.

Investigation: sampled 10 strict-resolved Baseball records via `LIMIT 10`. All 10 records returned NULL for `home_provider_normalized`, `away_provider_normalized`, `home_canonical`, `away_canonical`. Nine were FL records (8-char IDs), one was Kalshi (`KXMLBGAME-26MAY301915ATLCIN`).

**Finding:** FL's strict-tier resolution path writes `reason_detail` without the diagnostic name fields that Kalshi's path populates. The strict resolution itself works correctly (fixture_id is set, team_id references in `sp.fixtures` are correct), but the diagnostic detail in `sp.resolution_log.reason_detail` is sparse.

**Methodology implication:** Future F7 verification queries should JOIN to `sp.fixtures` + `sp.teams` to determine team attribution rather than relying on `reason_detail` JSON fields. The team_id JOIN bypasses the NULL detail and resolves attribution via fixture rows.

**Captured for future F7 query template:**

```sql
-- Use this pattern for league-attributable strict resolution counts:
SELECT count(*)
FROM sp.resolution_log rl
JOIN sp.fixtures f ON f.id = rl.fixture_id
JOIN sp.teams t_home ON t_home.id = f.home_team_id
JOIN sp.teams t_away ON t_away.id = f.away_team_id
WHERE rl.reason_detail->>'sport' = :sport
  AND rl.reason_code = 'strict'
  AND rl.decided_at >= :apply_timestamp
  AND (t_home.country_code = :country OR t_away.country_code = :country);
```

Filed as tech-debt note: investigate why FL strict resolutions skip the parsed-name preservation (PR #138 lifted this for fuzzy-tier; check if strict-tier needs the same fix). Not blocking — current resolution behavior is correct, only the diagnostic shape lags.

### v1.5 amendment pile (after Day-29 morning)

Pile at 18 items. New addition: #18 (journal claims about code state require artifact verification). Other amendments unchanged from Day-28.

### Day-29 PR state (morning)

- **PR for this entry**: this Day-29 morning batch (LMB validation + Pattern D erratum + amendment #18 + FL pipeline finding + FL reason_detail finding)
- Liga ACB apply, daily-diff measurement, and end-of-day journal section to follow

### Pending — next, operator review (Day-29 afternoon)

1. **Daily-diff Day-29** — measure Baseball capability rate change. Expected: small lift (~1pp) on aggregate Baseball metric; full F8 7-day measurement window opens 2026-06-04.
2. **Liga ACB apply** — Pattern D pre-flight → dry-run → wet apply → F7 (using the JOIN pattern above, not reason_detail JSON) → baseline_shifts annotation (event_type='phase_2d5a_acb_bootstrap').
3. **Day-29 afternoon journal batch** — Liga ACB apply results + daily-diff measurement + any other afternoon findings.
4. **Italian LBA scope-doc + manifest sourcing** — if time permits after Liga ACB applies cleanly. Sequencing decision committed in `docs/bootstraps/phase-2d5a-sequencing-decision.md`.

---

## Session — 2026-05-28

### Day-28 morning baseline + Tennis dedup +5.90pp validation

Day-28 daily-diff (Tennis dedup lift HELD AND EXTENDED from Day-27):

| Sport | Day-22 | Day-26 (pre-dedup) | Day-27 (post-dedup partial) | Day-28 (full post-dedup) |
|---|---:|---:|---:|---:|
| Tennis | 27.97% | 15.98% | 20.15% | **21.88%** (+5.90pp cumulative) |
| Baseball | 76.66% | 83.92% | 86.72% | 85.17% (within noise, no leakage from dedup) |
| Overall | 51.02% | 47.59% | 46.51% | 46.37% |

Multi-day apples-to-apples via `metrics->'scope_filtered'->>'matcher_capability_rate_overall'`. The +5.90pp Tennis lift from the Day-26 baseline is the empirical close on the Tennis dedup workstream — extends beyond the +4.17pp partial-window measurement on Day-27, confirming the consolidated player population produces durably fewer collision events.

### LMB bootstrap apply (Phase 2D.5-A first deliverable)

Apply at 19:41 UTC. Clean execution:

- **17 new LMB canonicals inserted** (sp.teams, sport_id=6, country_code='MEX')
- **3 BACKFILLs**: Bravos de León, Caliente de Durango, Toros de Tijuana — already existed as stubs with NULL country_code. Three-branch classifier correctly detected and queued UPDATE (not INSERT). KBL precedent's Phase 1.5 backfill discipline working as designed.
- **63 aliases inserted** (sp.team_aliases, source='bootstrap_league_coverage')
- **0 global conflicts, 0 errors**
- Runtime: 10.7s
- Pattern D pre-flight confirmed production endpoint pre-write

Post-apply production state: 289 baseball teams (was 272 pre-apply, 17 newly inserted), 20 MEX (was 0, comprising 17 INSERTs + 3 BACKFILLs of pre-existing stubs Bravos de León / Caliente de Durango / Toros de Tijuana), 269 untouched.

`sp.baseline_shifts` annotation: `f0f99c99-1c1d-4840-beea-6465bfd03e30` (event_type='phase_2d5a_lmb_bootstrap', event_date=2026-05-28). The phase-prefixed event_type aligns with the bootstrap script's naming convention — future operators searching `sp.baseline_shifts` by event_type can filter `LIKE 'phase_2d5a_%'` to find all data-driven league bootstrap annotations cleanly.

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
