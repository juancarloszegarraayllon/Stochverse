# Track A Deliverable 1 — Legacy-vs-New Pairing Comparison (v3-vs-v4 diff)

Phase 2 Track A Deliverable 1 scope doc. Closes Gate #2 (the "daily diff until acceptable" §11.3 item) by adding the missing comparison dimension to `scripts/daily_diff.py`. Deliverable 2 (new-resolver-standalone telemetry) shipped and has been running at 03:00 UTC via the `daily-diff` service in `railway.toml`; Deliverable 1 is the legacy-comparison layer that has been marked as `future` in the docstring since day one and — until this workstream — was silently absent.

This doc is **scope only**. No code lands here. Same discipline as `docs/dedup/lmb-2026-07-19.md`: the doc is what caught the LMB dedup's rollback bug before it shipped; expected to catch the equivalent bug here.

---

## 1. Context and motivation

### What Gate #2 actually requires

SP Architecture v1.4 §13.1: **"do not cut over until diff is acceptable."** The "diff" is the empirical comparison between the current production pairing (v3, which pairs at request time inside `main.py` and `flashlive_feed.py`) and the new resolver stack (v4 — `StrictMatcher` + `AliasTierMatcher` + `FuzzyTierMatcher` writing `sp.fixtures` / `sp.fl_events.fixture_id` / `sp.kalshi_markets.fixture_id`). "Acceptable" is an operator-owned threshold; it can only be defined against a diff that produces an agreement metric.

### What actually ships today

`scripts/daily_diff.py` (Deliverable 2) runs the NEW resolver stack against 24h of records and reports outcomes standalone: per-sport capability rates, confidence histogram, resolution_log volume, queue metrics. That's a useful telemetry pipeline for tuning the new resolver in isolation — but it produces zero agreement-with-v3 signal. `sp.daily_diff_reports.legacy_comparison_present` is hardcoded `False` at `scripts/daily_diff.py:1048`. Docstring line 15 flags the absent piece: `"(Deliverable 1, future): also runs the legacy Tier 1-4 resolver for AGREE/disagree comparison"`.

Without Deliverable 1, **the acceptable-threshold cannot mean "v4 matches v3 on X% of decisions"** because we don't measure v4-vs-v3 agreement. Any systematic disagreement (v4 links fixture X to team Y where v3 links X to team Z) is silent until users see it at 5% traffic.

Item 7 in the reanchor's §11.3 checklist (PR #254) noted "acceptable undefined." Corrected Day-54: TWO blanks — no threshold AND no comparison dimension. This workstream fills the second.

### Why this workstream is bounded (days, not weeks)

Day-54 read of `main.py` established that v3's real production pairing is already exposed as a **callable pure function**. Two variants live in the codebase, both already called from production paths:

- **v1** — `main.py:_build_kalshi_index_for_sport(sport_name)` at `:7806`. Returns `{fl_event_id: [kalshi_records]}` via `flashlive_feed.match_game` (title-based pairing).
- **v2** — `kalshi_join.build_kalshi_index(records, sport)` + `join_with_fl(fl_events, index, sport)`. Returns `{fl_event_id: [kalshi_records]}` via `kalshi_identity.parse_ticker` (identity-based pairing).

An existing internal endpoint at `main.py:8330+` already does a v1-vs-v2 diff and produces a report shape (`v1.pairings_sample`, `v2.pairings_sample`, `diff.v2_only_pairings`, `diff.v1_only_pairings`) that is 80% of what Deliverable 1 needs. **This work is extending an existing diff to add v4 as a third dimension**, not building a comparison pipeline from scratch.

Three-way (v1 vs v2 vs v4), not two-way, per the operator decision Day-54: v4 disagreements are cheaper to triage if we can localize them ("v4 disagrees with v1 but agrees with v2" → title-parse-flavor issue; "v4 disagrees with both" → deeper issue).

### The load-bearing thing this doc gets right

Every past Track-A pipeline has been a two-population diff — read one thing, read another thing, compare. The load-bearing failure mode this doc protects against is the exact class Day-53's cost investigation named: **descriptions are not evidence.** Specifically:

- If Deliverable 1 compares v3 CURRENT-STATE against v4 CURRENT-STATE, we are comparing two live populations that may not overlap — same class of error as the Day-53 circular tie-break query (~6,500 silently-wrong fixtures if we'd shipped it). Wrong-window is a subtler version of wrong-source.
- If Deliverable 1 doesn't bucket the diff cleanly, whoever reads the report will compute buckets ad-hoc and get them wrong. Especially "both pair, different fixture" (silent-wrong-linking, the DANGEROUS class). First-class in the schema or it drifts.
- If Deliverable 1 treats v4's deliberate extraction exclusions (KXMLBMENTION, doubles pairs, prop markets) as "regressions vs v3," the first report reads "regression detected" on ~28 KXMLBMENTION records/week and the operator wastes a day understanding they're correct behavior.

Each of these gets a dedicated section below.

---

## 2. Scope boundaries

### In scope

- **Extend `scripts/daily_diff.py`** to run v1 + v2 legacy pairings against the same 24h window Deliverable 2 already samples.
- **Query v4's current pairings** from `sp.fixtures` / `sp.fl_events.fixture_id` / `sp.kalshi_markets.fixture_id` for the same window.
- **Four first-class diff buckets** written to `sp.daily_diff_reports.report_json` per legacy-flavor (v1 and v2 each):
  - `agree_same_fixture` — v3 and v4 both link the record to the same canonical fixture
  - `v4_only` — v4 pairs; v3 doesn't. **Improvement**; not a blocker.
  - `legacy_only` — v3 pairs; v4 doesn't. **Regression risk**; this is the cutover bar.
  - `both_pair_different` — both pair, DIFFERENT fixtures. **Silent wrong-linking**; the dangerous class.
- **Extraction-exclusion classification pass** — records v4's extractor deliberately refused (`extract_signal returned None`) are their own bucket (`v4_extraction_excluded`), never folded into `legacy_only`. From day one, not a follow-up.
- **Same-window discipline** — refactor `_build_kalshi_index_for_sport` to accept a records-list parameter (~15-line signature change) so v1 pairing runs against the same 24h `sp.kalshi_markets` snapshot Deliverable 2 already pulls. Not `_cache` (which is live, would compare two different populations and call it a diff).
- **Flip `legacy_comparison_present`** in `_write_report` to `True` once the diff pipeline runs cleanly. That's the schema-signal that Gate #2's diff-shaped requirement is met.

### Out of scope (deferred)

- **Defining the "acceptable" threshold**. This doc produces the measurement dimension; Item 7's threshold is operator-owned and gets set from N days of report data once Deliverable 1 has been shipping.
- **Automated cutover gating on report values**. Once the threshold exists, some future workstream can gate `/api/v4` traffic-flag flips on the diff report. Not this workstream.
- **Sport-level breakdowns of the diff buckets**. Deliverable 2 already reports per-sport metrics; adding per-sport diff buckets is a natural extension but not required to close Gate #2. Ship the total-population diff first; per-sport falls out easily as a follow-up.
- **Historical backfill of Deliverable 1 reports over prior 24h windows**. The comparison shape is intended for going-forward measurement; retroactively running v3 pairing against 30-day-old cache state is neither cheap nor clean. If threshold-setting benefits from a longer trailing window, run Deliverable 1 for N days and use those N days.

### Non-goals

- **Fixing v1/v2 pairing bugs surfaced by Deliverable 1**. If v1 title-parse fails on a class v4 handles correctly, that's a v3 defect, not a v4 defect. Deliverable 1 reports the disagreement; whether to backfix v3 is orthogonal (probably not — v3 is being decommissioned).
- **Making v3 and v4 agree on prop markets / doubles / mentions**. v4's extraction exclusions are correct; v3 doesn't have them. Deliverable 1 buckets these explicitly (see §5), doesn't try to reconcile.

---

## 3. The three specifications

Per operator's Day-54 decisions, three specifications are load-bearing and must be preserved through implementation:

### 3.1 Same-window refactor (mandatory)

`_build_kalshi_index_for_sport` currently reads `_cache.get("data_all") or _cache.get("data")` internally (`main.py:7829`). For Deliverable 1 to compare same-window against same-window, this function must accept a records-list parameter and stop reading `_cache` on its own.

**~15-line signature change**:

```python
def _build_kalshi_index_for_sport(
    sport_name: str,
    records: list[dict] | None = None,  # NEW: default preserves existing callers
) -> dict:
    ...
    if records is None:
        get_data()
        records = _cache.get("data_all") or _cache.get("data") or []
    # (existing loop body unchanged)
```

Existing callers (`main.py:8350`, `main.py` in the internal diff endpoint) work unchanged because the default preserves current behavior. Deliverable 1 passes the 24h `sp.kalshi_markets` records explicitly.

`kalshi_join.build_kalshi_index` already accepts `records` as its first parameter (`main.py:8370`). No refactor needed for v2.

**Why this is load-bearing**: current-state-vs-current-state was rejected explicitly Day-54 — "comparing two different populations and calling the difference a diff — exactly the class of error we've spent two weeks catching." Same-window discipline is the same principle as read-don't-derive applied to time: don't compare snapshots from different moments.

### 3.2 Three-way v1 / v2 / v4, not two-way (mandatory)

Rationale: **cheap localization of any v4 disagreement**. If v4 disagrees with v1 but agrees with v2, the disagreement is title-parse flavor (v1 uses `match_game`'s fuzzy title-based pairing). If v4 disagrees with v2 but agrees with v1, the disagreement is identity-parse flavor (v2 uses `kalshi_identity.parse_ticker`). If v4 disagrees with both, the issue is deeper — probably in the resolver's own logic or in a class of records neither v3 flavor handles.

Every v4 disagreement will need triage anyway. Cheap localization is worth the marginal lines — probably ~30 lines beyond a two-way diff.

**Shape**: two diff invocations, one per legacy flavor (v1 vs v4, v2 vs v4), both stored in `report_json` under separate keys (`legacy_v1_diff`, `legacy_v2_diff`). Optionally a third `legacy_v1_vs_v2` sub-key that reuses the existing internal endpoint's math (that's essentially free — it's already implemented in `main.py:8390-8440`).

### 3.3 Four buckets first-class in the schema, not derived at read time (mandatory)

Store the counts directly in `report_json`, not computed from a raw disagreement list by whoever reads the report later.

**Schema** (per legacy flavor):

```json
{
  "legacy_v1_diff": {
    "agree_same_fixture":     <int>,
    "v4_only":                <int>,
    "legacy_only":            <int>,
    "both_pair_different":    <int>,
    "v4_extraction_excluded": <int>,
    "total_evaluated":        <int>,
    "sample_disagreements": {
      "legacy_only":        [{"fl_event_id", "kalshi_tickers"}, ...] (top-N per bucket),
      "both_pair_different": [{"fl_event_id", "legacy_tickers", "v4_tickers"}, ...],
      "v4_extraction_excluded": [{"fl_event_id", "reason"}, ...]
    }
  },
  "legacy_v2_diff": { ...same shape... }
}
```

**Why first-class**: the operator's Day-54 point verbatim — "both pair, different fixture" is the silent-wrong-linking class, and if it's computed ad-hoc by whoever reads the report, it will eventually be computed wrong or not at all. Same argument for the other three; especially `v4_extraction_excluded` which is easily conflated with `legacy_only` if the classification isn't cleanly applied at write time.

`total_evaluated` and the bucket counts must sum to `total_evaluated` exactly. A written invariant that the diff pipeline must satisfy pre-write.

### 3.4 Extraction-exclusion classification pass — from day one

v4's extractor deliberately refuses to return a signal for structurally-unmatchable records: prop markets (`KALSHI_PROP_MARKET_SEGMENTS` at `resolver/fuzzy_tier/matcher.py`), doubles pairs (`_is_doubles_pair_signal` at `resolver/fl.py`), mention markets (`KXMLBMENTION` in `_OUTRIGHT_SERIES_PREFIXES` at `kalshi_identity.py`). These are CORRECT REFUSALS — v4 knows these records can't be paired to any canonical fixture.

v1/v2 do not have these exclusions. `match_game` will happily produce a pairing for a KXMLBMENTION ticker if its title parses to a team name that appears in an FL fixture. That pairing is semantically wrong; v4's refusal is correct.

Without an explicit classification pass, a "diff" that treats "v3 paired, v4 didn't" as `legacy_only` would count ~28 KXMLBMENTION records/week as regression signal. Same argument for doubles (~6,448 pre-existing rows on the FL side, filtered daily at ingest) and prop markets. Cumulatively this is dozens-to-hundreds of records/week that look like regressions but are correct exclusions.

**Bucket contract**:

- If `extractor.extract_signal(record) is None` for a Kalshi ticker, the ticker's disposition is `v4_extraction_excluded` regardless of whether v1/v2 paired it. Never counted as `legacy_only`.
- `v4_extraction_excluded` is its own count line in the report; NOT summed into `legacy_only`.
- Operator can inspect the sample to confirm the exclusions are correct-behavior classes (KXMLBMENTION, doubles, props) and not accidental exclusions of legitimate records.

If the extractor gains a new exclusion class later (say, another structurally-unmatchable ticker family), this bucket's count grows — visible as a change but never mislabeled as regression.

---

## 4. Implementation plan (scope, not code)

Ordered as the implementation would proceed. Each step includes an approximate LOC estimate; total ~200-300 lines including tests.

### 4.1 Refactor `_build_kalshi_index_for_sport` to accept records

**File**: `main.py:7806`. **LOC**: ~15.

Add optional `records: list[dict] | None = None` parameter. Default preserves existing behavior (read from `_cache`). Deliverable 1 passes the 24h `sp.kalshi_markets` records explicitly.

Regression test: existing callers unchanged; new caller passing an explicit records list produces the same output shape as the default path when fed the same records.

### 4.2 Add `_run_legacy_pairings(sport, records)` to `daily_diff.py`

**File**: `scripts/daily_diff.py`. **LOC**: ~50.

```python
def _run_legacy_pairings(
    sport_name: str,
    kalshi_records: list[dict],
    fl_events: list[dict],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Run v1 (title-parse) and v2 (identity-parse) pairing algorithms
    against the given records. Return (v1_map, v2_map) where each map
    is {fl_event_id: set(kalshi_ticker)}.

    Same-window discipline: caller passes the records list explicitly;
    both algorithms operate on the same population.
    """
    # v1 via _build_kalshi_index_for_sport (needs the refactor from 4.1)
    from main import _build_kalshi_index_for_sport
    v1_index = _build_kalshi_index_for_sport(sport_name, records=kalshi_records)
    v1_map = {
        fl_id: {r.get("event_ticker", "") for r in recs}
        for fl_id, recs in v1_index.items()
    }

    # v2 via kalshi_join
    from kalshi_join import build_kalshi_index, join_with_fl
    v2_index = build_kalshi_index(kalshi_records, sport_name)
    v2_pairings, _v2_unpaired = join_with_fl(fl_events, v2_index, sport_name)
    v2_map = {
        p.fl_event.get("EVENT_ID", ""): {
            r.get("event_ticker", "") for r in p.kalshi_records
        }
        for p in v2_pairings
        if p.fl_event.get("EVENT_ID")
    }

    return v1_map, v2_map
```

### 4.3 Add `_query_v4_pairings(session, window_start, window_end)` to `daily_diff.py`

**File**: `scripts/daily_diff.py`. **LOC**: ~40.

Reconstruct v4's current pairing state by joining `sp.fl_events` and `sp.kalshi_markets` on `fixture_id`. Same 24h window Deliverable 2 already uses.

```sql
SELECT fle.fl_event_id, array_agg(km.ticker) AS kalshi_tickers
FROM sp.fl_events fle
JOIN sp.kalshi_markets km ON km.fixture_id = fle.fixture_id
WHERE fle.fixture_id IS NOT NULL
  AND fle.last_seen_at BETWEEN :window_start AND :window_end
GROUP BY fle.fl_event_id;
```

Returns `dict[str, set[str]]` — same shape as v1/v2 maps for direct comparability. FL records with `fixture_id NULL` (not yet resolved by v4) don't appear — those become `legacy_only` if v1/v2 paired them, or drop entirely if neither v3 flavor paired them either.

### 4.4 Add `_diff_pairings(legacy_map, v4_map, extractor, kalshi_records)` — the classification pass

**File**: `scripts/daily_diff.py`. **LOC**: ~100 (including the invariant check and sample capture).

Pure function, unit-testable. Handles all four buckets + `v4_extraction_excluded` from step 3.4.

```python
def _diff_pairings(
    legacy_map: dict[str, set[str]],
    v4_map: dict[str, set[str]],
    extractor,
    kalshi_records: list[dict],
) -> dict:
    """Bucket every fl_event_id present in either map into one of five
    dispositions. Extraction-excluded records (v4 correctly refused
    the signal) are separated from legacy_only regressions.

    Returns the report_json sub-dict per §3.3 schema.
    """
    # Build set of kalshi tickers v4's extractor deliberately refused.
    v4_excluded_tickers = {
        r.get("event_ticker") for r in kalshi_records
        if extractor.extract_signal(r) is None
    }

    all_fl_ids = set(legacy_map.keys()) | set(v4_map.keys())
    buckets = {
        "agree_same_fixture": 0,
        "v4_only": 0,
        "legacy_only": 0,
        "both_pair_different": 0,
        "v4_extraction_excluded": 0,
    }
    samples = {k: [] for k in buckets if k != "agree_same_fixture"}
    SAMPLE_N = 30

    for fl_id in all_fl_ids:
        legacy_tickers = legacy_map.get(fl_id, set())
        v4_tickers = v4_map.get(fl_id, set())

        # Extraction-exclusion first — a legacy pairing whose Kalshi
        # side is entirely v4-excluded tickers is NOT a regression.
        legacy_non_excluded = legacy_tickers - v4_excluded_tickers
        if legacy_tickers and not legacy_non_excluded and not v4_tickers:
            buckets["v4_extraction_excluded"] += 1
            if len(samples["v4_extraction_excluded"]) < SAMPLE_N:
                samples["v4_extraction_excluded"].append({
                    "fl_event_id": fl_id,
                    "excluded_tickers": sorted(legacy_tickers),
                })
            continue

        # Standard four-bucket classification on the non-excluded set.
        legacy_effective = legacy_non_excluded  # legacy pairings v4 could evaluate
        if legacy_effective and v4_tickers:
            if legacy_effective == v4_tickers:
                buckets["agree_same_fixture"] += 1
            else:
                buckets["both_pair_different"] += 1
                if len(samples["both_pair_different"]) < SAMPLE_N:
                    samples["both_pair_different"].append({
                        "fl_event_id":     fl_id,
                        "legacy_tickers":  sorted(legacy_effective),
                        "v4_tickers":      sorted(v4_tickers),
                    })
        elif v4_tickers:
            buckets["v4_only"] += 1
            if len(samples["v4_only"]) < SAMPLE_N:
                samples["v4_only"].append({
                    "fl_event_id": fl_id,
                    "v4_tickers":  sorted(v4_tickers),
                })
        elif legacy_effective:
            buckets["legacy_only"] += 1
            if len(samples["legacy_only"]) < SAMPLE_N:
                samples["legacy_only"].append({
                    "fl_event_id":    fl_id,
                    "legacy_tickers": sorted(legacy_effective),
                })

    total = sum(buckets.values())
    assert total == len(all_fl_ids), (
        f"invariant violated: bucket sum {total} != all_fl_ids "
        f"count {len(all_fl_ids)}"
    )

    return {**buckets, "total_evaluated": total,
            "sample_disagreements": samples}
```

**Invariant asserted at write time**: bucket counts sum to `total_evaluated`. If violated, the pipeline raises rather than writing a corrupt report. Same read-don't-derive discipline as the LMB dedup's `count(DISTINCT fixture_id) == expected` guard.

### 4.5 Wire it into `_measure` and `_write_report`

**File**: `scripts/daily_diff.py`. **LOC**: ~40.

`_measure` fans out per sport, calling `_run_legacy_pairings` and `_diff_pairings` twice per sport (once for v1, once for v2). Aggregates results into `report_json`:

```python
report_json["legacy_v1_diff"] = _diff_pairings(v1_map, v4_map, kalshi_extractor, kalshi_records)
report_json["legacy_v2_diff"] = _diff_pairings(v2_map, v4_map, kalshi_extractor, kalshi_records)
```

`_write_report` gains one edit: `"legacy_present": True` at line 1048 (currently hardcoded `False`). That's the schema signal that Gate #2's diff-shaped requirement is met.

### 4.6 Tests

**File**: `tests/test_daily_diff.py` (extend). **LOC**: ~50.

- `test_diff_pairings_bucket_invariant` — bucket counts sum to total.
- `test_diff_pairings_extraction_exclusion_isolates` — a legacy pairing whose only Kalshi tickers are v4-excluded goes to `v4_extraction_excluded`, not `legacy_only`.
- `test_diff_pairings_agreement_case` — identical maps → all `agree_same_fixture`.
- `test_diff_pairings_dangerous_class` — same fl_id, different ticker sets → `both_pair_different`.
- `test_diff_pairings_v4_only` — v4-only fl_id → `v4_only`.
- `test_build_kalshi_index_for_sport_records_param` — refactor: default preserves cache-read behavior; explicit `records` bypasses cache.

### 4.7 Baseline shift annotation

Add a `sp.baseline_shifts` row at first-report-with-diff time:

```sql
INSERT INTO sp.baseline_shifts (event_type, event_date, affected_population, expected_metric_delta, notes, created_by)
VALUES (
  'measurement_expansion',
  DATE '<first-Deliverable-1-report-date>',
  'sp.daily_diff_reports.report_json — new dimensions legacy_v1_diff + legacy_v2_diff. legacy_comparison_present flips False → True.',
  'No underlying resolver-behavior change; measurement dimension added. Historical reports lack the new fields; comparisons across the pre/post boundary must account for this.',
  'source_tag=deliverable_1_2026_07_25. Gate #2 close. PR #<TBD>. Item 7 acceptable-threshold still operator-owned; this workstream produces the measurement dimension only.',
  'PR #<TBD>'
);
```

### 4.8 What NOT to do

- **Do NOT** hit `/api/events` over HTTP to fetch v3 pairings. The functions are directly importable; HTTP adds latency, error surface, and coupling to the FastAPI server being reachable during the daily cron. Direct import is strictly better.
- **Do NOT** compute buckets from raw disagreement lists at read time. Pre-compute at write time per §3.3.
- **Do NOT** fold `v4_extraction_excluded` into `legacy_only`. Ever. Even if the count looks small "for now" — the exact failure mode this bucket exists to prevent is the count LOOKING like regression signal.
- **Do NOT** compare `_cache` state against `sp.*` state. Same-window or nothing.
- **Do NOT** ship Deliverable 1 without the `total_evaluated == sum(buckets)` invariant check. Bad math in the diff produces the same false-confidence as the Day-53 circular tie-break.

---

## 5. Post-ship — reading the report

Once Deliverable 1 is shipping daily reports, the operator has the raw material to set Item 7's threshold. Suggested reading discipline (not proposing numbers — same reason as the reanchor left Item 7 blank):

- `both_pair_different` count is the most important number in the report. It's the silent-wrong-linking class — records where v4 confidently disagrees with v3, invisibly. Suggested minimum for cutover: some operator-defined tolerance, probably measured in records/day rather than percentage.
- `legacy_only` count is the second most important. Records where v3 pairs and v4 doesn't. Some fraction of these will be legitimate v4 improvements (v3 pairing was wrong); some will be actual regressions. Sampling from the `sample_disagreements.legacy_only` list is the manual-triage path.
- `v4_only` count is expected to be non-zero and rising as v4's coverage exceeds v3's on new sports. Not a blocker; if anything, evidence Gate #2's underlying purpose (v4 is better than v3) is being met.
- `v4_extraction_excluded` count should be stable at the population size of KXMLBMENTION + doubles + props. Sharp movements here would indicate a new extraction-exclusion class shipped or an existing one broken.

The `sample_disagreements` block gives the operator concrete records to eyeball. Not the full disagreement set (which could be thousands of records/day at peak); the top-N samples per bucket, capped at some N that keeps `report_json` under a reasonable size ceiling. `SAMPLE_N = 30` in the draft code; adjustable.

---

## 6. Success criteria

Deliverable 1 is considered shipped when:

1. `sp.daily_diff_reports.legacy_comparison_present = True` on every row written after the code lands.
2. `report_json` for every row includes `legacy_v1_diff` and `legacy_v2_diff` sub-dicts with the five bucket counts per §3.3 schema.
3. `bucket_sum == total_evaluated` invariant holds on every row (pipeline asserts pre-write; corrupt runs raise rather than silently persist).
4. `v4_extraction_excluded` count is non-zero on FL cron reports (proves the classification pass fires; population is at least the KXMLBMENTION + doubles rate).
5. `sp.baseline_shifts` row exists documenting the measurement expansion per §4.7.
6. Gate #2 status in `PROJECT_STATE.md` moves from "REOPENED Day-54 — HALF-BUILT" to "CLOSED Day-N — Deliverable 1 shipped; Item 7 threshold still operator-owned."

Item 7's acceptable-threshold gets set separately, from N days of report data, by the operator. This workstream does not set the threshold — it produces the measurement dimension the threshold gets set against.

---

## 7. Rollback

Rollback is trivial because Deliverable 1 is an ADDITIVE change to `report_json`:

- Revert `scripts/daily_diff.py` to its pre-Deliverable-1 shape.
- Post-revert reports omit `legacy_v1_diff` / `legacy_v2_diff` from `report_json` and write `legacy_present = False`.
- Historical reports written under Deliverable 1 remain in the table; they're not corrupt, just carrying additional fields the reverted code doesn't produce.
- `sp.baseline_shifts` row from §4.7 stays; it accurately documents that a measurement dimension was added and (per rollback) removed.

No schema migration to reverse, no data cleanup. Same reason `sp.daily_diff_reports.report_json` was made a JSONB blob: measurement dimensions can be added and removed without schema change.

---

## 8. Followups (not in this PR)

- **Sport-level diff breakdowns**. Not required to close Gate #2; natural extension once Deliverable 1 is shipping.
- **Alerting on `both_pair_different` step-changes**. Once trend data exists, a Nth-percentile step-detector is worth wiring. Not urgent; the manual weekly-read discipline works at current volume.
- **Automated cutover gating**. When Item 7's threshold is set, `/api/v4` traffic-flag flips can be conditioned on `both_pair_different <= threshold AND legacy_only <= threshold`. Separate scope, needs product decision on gate semantics (soft-warn vs hard-block).
- **`v1_vs_v2` sub-diff surfaced explicitly**. The math already exists at `main.py:8390-8440`. Adding it to `report_json` as `legacy_v1_vs_v2` gives triage a "both v3 flavors disagree with each other AND with v4" three-way split. Marginal work; deferrable.

---

## 9. Pointer

- Precedent: `docs/dedup/lmb-2026-07-19.md` (methodology + snapshot-first discipline).
- Deliverable 2 baseline: `scripts/daily_diff.py` (current, 1,168 lines; Deliverable-1 additions target ~200-300 lines net).
- v3 pairing sources: `main.py:_build_kalshi_index_for_sport` (v1 title-parse), `kalshi_join.build_kalshi_index` + `join_with_fl` (v2 identity-parse).
- Existing v1-vs-v2 diff endpoint (reference shape): `main.py:8330+`.
- v4 pairing source: `sp.fl_events.fixture_id` joined against `sp.kalshi_markets.fixture_id`.
- Gate #2 status pre-Deliverable-1: `PROJECT_STATE.md` phase-status header (post-PR #255 correction: "REOPENED Day-54 — HALF-BUILT").
- Methodology: descriptions-are-not-evidence family (`docs/dedup/lmb-2026-07-19.md` §13; will be consolidated per Day-54 operator directive).
