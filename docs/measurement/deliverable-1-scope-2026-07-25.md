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
- **Five first-class diff buckets** written to `sp.daily_diff_reports.report_json` per legacy-flavor (v1 and v2 each):
  - `agree_same_fixture` — v3 and v4 both link the record to the same set of tickers. Exact set equality after namespace normalization.
  - `agree_partial_coverage` — v3 and v4 both link the record to the same fixture but their ticker sets differ (overlapping-unequal). **Benign**: coverage-difference under a shared fixture, not silent wrong-linking. Explicitly separated from `both_pair_different` because folding them together would set Item 7's threshold against noise.
  - `v4_only` — v4 pairs; v3 doesn't. **Improvement**; not a blocker.
  - `legacy_only` — v3 pairs; v4 doesn't. **Regression risk**; this is the cutover bar.
  - `both_pair_different` — both pair, DISJOINT ticker sets under different fixtures. **Silent wrong-linking**; the dangerous class. Distinct from `agree_partial_coverage` by the disjoint-intersection test.
- **Extraction-exclusion classification pass** — records v4's extractor deliberately refused (`extract_signal returned None`) are their own bucket (`v4_extraction_excluded`), never folded into `legacy_only`. From day one, not a follow-up.
- **Namespace realization (post-manual-run correction 2026-07-28)**. v1 and v2 legacy maps emit **event_ticker** (parent-event granularity, e.g. `KXNBAGAME-26MAY04MINSAS`). `sp.kalshi_markets.ticker` — the DB column NAME is "ticker" but its semantic content is ALSO the Kalshi **event_ticker**, populated at `ingestion/kalshi.py:179` via `record.get("event_ticker")`. So v1/v2 legacy maps AND `km.ticker` are already in the same namespace. **No JOIN through `public.markets` / `public.events` is required to normalize.** The initial version of §4.3 (pre-2026-07-28) proposed such a JOIN and dropped ~95% of km rows via `pm.ticker != km.ticker` — corrected in the amended §4.3 below.
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

**Same-window applies to BOTH sides of the v4 query too, not just the legacy side.** v4's pairing lives in `sp.kalshi_markets.fixture_id`. A `SELECT ... JOIN sp.kalshi_markets km ON km.fixture_id = fle.fixture_id` with a window filter only on `fle.last_seen_at` includes km rows from all time — v4's ticker set for a given fl_event would reflect all-time linkage while v1/v2's would reflect just the 24h population. Either constrain `km.last_seen_at` to the same window, or restrict the km join to the sampled kalshi record set explicitly. See §4.3 for the SQL form.

### 3.2 Three-way v1 / v2 / v4, not two-way (mandatory)

Rationale: **cheap localization of any v4 disagreement**. If v4 disagrees with v1 but agrees with v2, the disagreement is title-parse flavor (v1 uses `match_game`'s fuzzy title-based pairing). If v4 disagrees with v2 but agrees with v1, the disagreement is identity-parse flavor (v2 uses `kalshi_identity.parse_ticker`). If v4 disagrees with both, the issue is deeper — probably in the resolver's own logic or in a class of records neither v3 flavor handles.

Every v4 disagreement will need triage anyway. Cheap localization is worth the marginal lines — probably ~30 lines beyond a two-way diff.

**Shape**: two diff invocations, one per legacy flavor (v1 vs v4, v2 vs v4), both stored in `report_json` under separate keys (`legacy_v1_diff`, `legacy_v2_diff`). Optionally a third `legacy_v1_vs_v2` sub-key that reuses the existing internal endpoint's math (that's essentially free — it's already implemented in `main.py:8390-8440`).

### 3.3 Five buckets first-class in the schema, not derived at read time (mandatory)

Store the counts directly in `report_json`, not computed from a raw disagreement list by whoever reads the report later. Also see §3.5 for the equal / overlap / disjoint classification rule that partitions the two agreement buckets from the dangerous class.

**Aggregation shape**: bucket counts in `report_json.legacy_v1_diff` / `legacy_v2_diff` are **summed across all sports** — one top-level number per bucket per legacy-flavor. Per-sport breakdown is out-of-scope for this ship (§2). Deliverable 2 already reports per-sport metrics in its own dimensions; this workstream produces the total-population diff first so Item 7's threshold can be set against a single dimension. The per-sport fan-out in `_measure` (§4.5) computes bucket counts per sport internally, then sums across sports before writing — the same numbers a follow-up per-sport-diff PR would surface directly.

**Schema** (per legacy flavor):

```json
{
  "legacy_v1_diff": {
    "agree_same_fixture":       <int>,
    "agree_partial_coverage":   <int>,
    "v4_only":                  <int>,
    "legacy_only":              <int>,
    "both_pair_different":      <int>,
    "v4_extraction_excluded":   <int>,
    "total_evaluated":          <int>,
    "sample_disagreements": {
      "agree_partial_coverage":  [{"fl_event_id", "legacy_tickers", "v4_tickers", "overlap"}, ...] (top-N),
      "legacy_only":             [{"fl_event_id", "legacy_tickers"}, ...] (top-N per bucket),
      "both_pair_different":     [{"fl_event_id", "legacy_tickers", "v4_tickers"}, ...],
      "v4_extraction_excluded":  [{"fl_event_id", "excluded_tickers"}, ...],
      "v4_only":                 [{"fl_event_id", "v4_tickers"}, ...]
    }
  },
  "legacy_v2_diff": { ...same shape... }
}
```

**Why first-class**: the operator's Day-54 point verbatim — "both pair, different fixture" is the silent-wrong-linking class, and if it's computed ad-hoc by whoever reads the report, it will eventually be computed wrong or not at all. Same argument for the other four; especially:
- `agree_partial_coverage` — easily conflated with `both_pair_different` if the overlap test isn't applied cleanly at write time. Folding them together sets Item 7's threshold against noise (v4 gaining a market under an agreed fixture reads as a regression). Explicitly split at write time.
- `v4_extraction_excluded` — easily conflated with `legacy_only` if the classification isn't applied. Correct exclusion of a KXMLBMENTION ticker reads as regression signal.

`total_evaluated` and the bucket counts must sum to `total_evaluated` exactly. A written invariant that the diff pipeline must satisfy pre-write.

### 3.5 Agreement classification rule: equal / overlap / disjoint

The two agreement buckets (`agree_same_fixture`, `agree_partial_coverage`) and the dangerous class (`both_pair_different`) are partitioned by the following test, applied AFTER namespace-normalization (§3.1) and extraction-exclusion (§3.4):

Given `legacy_effective` and `v4_tickers` (both non-empty sets of event_tickers under a common `fl_event_id`):

| Test | Bucket | Semantic |
|---|---|---|
| `legacy_effective == v4_tickers` | `agree_same_fixture` | Full agreement |
| `legacy_effective & v4_tickers` non-empty AND unequal | `agree_partial_coverage` | Same fixture, different ticker coverage — benign |
| `legacy_effective & v4_tickers` empty (disjoint) | `both_pair_different` | Different fixtures under same fl_event_id — silent wrong-linking, dangerous |

Rationale for the middle bucket: after normalization, overlapping ticker sets under one `fl_event_id` almost always mean "v3 and v4 both agree this fl_event corresponds to Kalshi event X; they differ only on whether some additional related event Y is also linked." That's a coverage boundary (v4 knows about Y that v3 doesn't, or vice versa), not a linkage disagreement. **The dangerous bucket must mean different fixture, not same fixture with different coverage**, or Item 7's threshold gets set against noise.

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

### 4.3 Add `_query_v4_pairings(session, kalshi_pks, fl_pks)` to `daily_diff.py` — CORRECTED 2026-07-28

**File**: `scripts/daily_diff.py`. **LOC**: ~35.

Reconstruct v4's current pairing state by joining `sp.fl_events` and `sp.kalshi_markets` on `fixture_id`, scoped to D2's LOADED PK SETS (kalshi_pks + fl_pks). Two disciplines applied:

1. **Namespace realized (post-manual-run correction)**: `sp.kalshi_markets.ticker` semantically stores event_ticker (via `ingestion/kalshi.py:179`). Direct `array_agg(DISTINCT km.ticker)` is already event_ticker — no JOIN through `public.markets`/`public.events` needed. The prior version's JOIN chain caused ~95% of km rows to silently drop (`pm.ticker != km.ticker` mismatch) and produced a near-empty v4 map on the initial manual run.

2. **Same-population by PK set, not by predicate re-evaluation**: v4's ticker sets come from EXACTLY the km rows D2's `_KALSHI_WINDOW_SQL` loaded, not from a re-evaluated `last_seen_at ∈ window` predicate at a slightly-later moment. Insulates from PR #260's active-row `last_seen_at` bumps that would otherwise exclude still-active fixture-linked rows on any historical cron run (window ended before run). Strict same-window by construction.

```sql
SELECT
  fle.fl_event_id,
  array_agg(DISTINCT km.ticker) AS event_tickers
FROM sp.fl_events fle
JOIN sp.kalshi_markets km ON km.fixture_id = fle.fixture_id
WHERE fle.fixture_id IS NOT NULL
  AND fle.fl_event_id = ANY(:fl_pks)
  AND km.ticker      = ANY(:kalshi_pks)
GROUP BY fle.fl_event_id;
```

Parameters `kalshi_pks` and `fl_pks` are the actual `sp.kalshi_markets.ticker` and `sp.fl_events.fl_event_id` values D2's kalshi_rows/fl_rows already carry (extracted in Python before this query fires). Postgres `ANY(array)` handles 10k+-item arrays without issue.

**All-sports at once, partition in Python**: no `s.name = :sport` filter. One query returns fl_events across every sport in D2's population; per-sport partitioning happens in `_measure` via an `fl_id → sport_name` lookup built from `fl_rows`. Avoids the sp.sports JOIN entirely — which is fortunate because pre-2A.7 rows with `sport_id NULL` would have been dropped by the sport-filtered variant.

Returns `dict[str, set[str]]` (event_ticker sets) — same shape and namespace as v1/v2 maps for direct comparability. FL records with `fixture_id NULL` (not yet resolved by v4) don't appear — those become `legacy_only` if v1/v2 paired them, or drop entirely if neither v3 flavor paired them either.

### 4.4 Add `_diff_pairings(legacy_map, v4_map, extractor, kalshi_records)` — the classification pass

**File**: `scripts/daily_diff.py`. **LOC**: ~120 (including the invariant check, sample capture, and the equal/overlap/disjoint partition).

Pure function, unit-testable. Handles all five buckets + `v4_extraction_excluded` from §3.4. **Both maps must be in the same namespace (event_ticker)** — see §3.1 and §4.3. If callers pass mixed namespaces the intersection tests below silently collapse to disjoint-everywhere; add the `test_fl_event_id_key_format_consistency` check (§4.6) at pipeline-integration time to fail loud instead of quiet.

```python
def _diff_pairings(
    legacy_map: dict[str, set[str]],   # {fl_event_id: {event_ticker, ...}}
    v4_map: dict[str, set[str]],       # same shape, same namespace
    extractor,
    kalshi_records: list[dict],
) -> dict:
    """Bucket every fl_event_id present in either map into one of six
    dispositions. Extraction-excluded records (v4 correctly refused
    the signal) are separated from legacy_only regressions. Overlapping
    non-equal ticker sets under a common fl_event_id are separated
    from disjoint sets per §3.5.

    Returns the report_json sub-dict per §3.3 schema.
    """
    # Build set of event_tickers v4's extractor deliberately refused.
    # Namespace: event_ticker, matching what legacy_map and v4_map carry.
    v4_excluded_tickers = {
        r.get("event_ticker") for r in kalshi_records
        if extractor.extract_signal(r) is None
    }

    all_fl_ids = set(legacy_map.keys()) | set(v4_map.keys())
    buckets = {
        "agree_same_fixture":     0,
        "agree_partial_coverage": 0,
        "v4_only":                0,
        "legacy_only":            0,
        "both_pair_different":    0,
        "v4_extraction_excluded": 0,
    }
    # Sample every non-agree bucket AND agree_partial_coverage (so the
    # operator can eyeball whether coverage-diff samples look benign).
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

        legacy_effective = legacy_non_excluded  # legacy tickers v4 could evaluate

        # Five-bucket classification on the non-excluded set. §3.5's
        # equal/overlap/disjoint partition splits the two-both-paired
        # case into agree_same_fixture / agree_partial_coverage /
        # both_pair_different.
        if legacy_effective and v4_tickers:
            if legacy_effective == v4_tickers:
                buckets["agree_same_fixture"] += 1
            elif legacy_effective & v4_tickers:
                # Non-empty intersection, unequal sets — coverage
                # difference under a shared fixture. BENIGN class,
                # separated from both_pair_different so Item 7's
                # threshold isn't set against benign coverage noise.
                buckets["agree_partial_coverage"] += 1
                if len(samples["agree_partial_coverage"]) < SAMPLE_N:
                    samples["agree_partial_coverage"].append({
                        "fl_event_id":    fl_id,
                        "legacy_tickers": sorted(legacy_effective),
                        "v4_tickers":     sorted(v4_tickers),
                        "overlap":        sorted(legacy_effective & v4_tickers),
                    })
            else:
                # Disjoint sets — different fixtures under the same
                # fl_event_id. DANGEROUS class: silent wrong-linking.
                buckets["both_pair_different"] += 1
                if len(samples["both_pair_different"]) < SAMPLE_N:
                    samples["both_pair_different"].append({
                        "fl_event_id":    fl_id,
                        "legacy_tickers": sorted(legacy_effective),
                        "v4_tickers":     sorted(v4_tickers),
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

**File**: `scripts/daily_diff.py`. **LOC**: ~60.

`_measure` fans out per sport, calling `_run_legacy_pairings` and `_diff_pairings` twice per sport (once for v1, once for v2). Per-sport counts are **summed across sports** into a single top-level dict per legacy-flavor, matching the §3.3 aggregation shape:

```python
# Per-sport fan-out; each sport produces its own v1_diff and v2_diff sub-dict.
per_sport_v1_diffs: list[dict] = []
per_sport_v2_diffs: list[dict] = []
for sport in sports_in_window:
    kalshi_records_for_sport = [r for r in kalshi_records if r.get("_sport") == sport]
    fl_events_for_sport = [e for e in fl_events if e.get("sport") == sport]
    v1_map, v2_map = _run_legacy_pairings(sport, kalshi_records_for_sport, fl_events_for_sport)
    v4_map = _query_v4_pairings_for_sport(session, sport, window_start, window_end)
    per_sport_v1_diffs.append(_diff_pairings(v1_map, v4_map, kalshi_extractor, kalshi_records_for_sport))
    per_sport_v2_diffs.append(_diff_pairings(v2_map, v4_map, kalshi_extractor, kalshi_records_for_sport))

def _sum_diff_dicts(diffs: list[dict]) -> dict:
    """Sum bucket counts across sports; concatenate + truncate samples
    to SAMPLE_N total. Preserves the §3.3 schema at report-json level."""
    bucket_keys = ["agree_same_fixture", "agree_partial_coverage",
                   "v4_only", "legacy_only", "both_pair_different",
                   "v4_extraction_excluded"]
    summed = {k: sum(d[k] for d in diffs) for k in bucket_keys}
    summed["total_evaluated"] = sum(d["total_evaluated"] for d in diffs)
    # Concatenate sample lists across sports; truncate to SAMPLE_N per
    # bucket so report_json size stays bounded regardless of sport count.
    all_sample_keys = set().union(*(d["sample_disagreements"].keys() for d in diffs))
    summed["sample_disagreements"] = {
        k: [s for d in diffs for s in d["sample_disagreements"].get(k, [])][:SAMPLE_N]
        for k in all_sample_keys
    }
    return summed

report_json["legacy_v1_diff"] = _sum_diff_dicts(per_sport_v1_diffs)
report_json["legacy_v2_diff"] = _sum_diff_dicts(per_sport_v2_diffs)
```

**Why per-sport fan-out even though we report summed**: (a) reuses Deliverable 2's existing per-sport records slicing, (b) `_run_legacy_pairings` and `_query_v4_pairings` are naturally sport-scoped (their existing signatures take a sport_name), (c) makes the per-sport-diff followup a schema addition rather than a refactor. Summed-across-sports is what ships in `report_json`; per-sport counts stay in the per-sport intermediate dicts and can be surfaced in a follow-up PR that changes `_sum_diff_dicts` into `_organize_diff_dicts_by_sport`.

`_write_report` gains one edit: `"legacy_present": True` at line 1048 (currently hardcoded `False`). That's the schema signal that Gate #2's diff-shaped requirement is met.

### 4.6 Tests

**File**: `tests/test_daily_diff.py` (extend). **LOC**: ~80.

Core `_diff_pairings` classification:
- `test_diff_pairings_bucket_invariant` — bucket counts sum to `total_evaluated`.
- `test_diff_pairings_extraction_exclusion_isolates` — a legacy pairing whose only Kalshi tickers are v4-excluded goes to `v4_extraction_excluded`, not `legacy_only`.
- `test_diff_pairings_full_agreement` — identical maps → all `agree_same_fixture`; zero in the other buckets.
- `test_diff_pairings_partial_coverage_not_dangerous` — same fl_id, ticker sets `{A,B}` vs `{A,C}` (overlap = `{A}`, unequal) → `agree_partial_coverage`, NOT `both_pair_different`. Guards against the operator-caught regression where benign coverage-diff was landing in the dangerous class.
- `test_diff_pairings_dangerous_class_is_disjoint_only` — same fl_id, ticker sets `{A,B}` vs `{C,D}` (disjoint) → `both_pair_different`. Also verify `{A}` vs `{B}` (single-element disjoint) → `both_pair_different`.
- `test_diff_pairings_v4_only` — v4-only fl_id → `v4_only`.
- `test_diff_pairings_legacy_only` — legacy-only fl_id (with non-excluded tickers) → `legacy_only`.

Refactor safety:
- `test_build_kalshi_index_for_sport_records_param` — refactor: default preserves cache-read behavior; explicit `records` bypasses cache.

Namespace and window discipline (pipeline-level, integration-shaped):
- `test_v4_query_returns_event_ticker_namespace` — feed a fixture with markets `-MIN` and `-SAS` under event_ticker `X`. Assert the returned map has `{"X"}` (event_ticker), not `{"X-MIN", "X-SAS"}` (market_ticker). Would have caught the operator-caught BLOCKER on day one.
- `test_v4_query_respects_km_window` — insert a `sp.kalshi_markets` row with `last_seen_at` outside the window; assert its event_ticker is NOT in the returned map. Guards against the same-window violation in the JOIN.
- `test_fl_event_id_key_format_consistency` — call `_run_legacy_pairings` and `_query_v4_pairings` against the same fixture data; assert the returned dict keys have the same type and format across v1, v2, and v4 (e.g. all `str`, matching case). Cross-namespace fl_event_id keys silently produce empty intersections that read as `both_pair_different` — this test fails loud when a future change to any of the three sources drifts the key format.

Aggregation shape:
- `test_sum_diff_dicts_preserves_bucket_invariant` — sum across two per-sport dicts; assert the summed `total_evaluated == sum(each.total_evaluated)` AND `sum(summed.bucket_counts) == summed.total_evaluated`.
- `test_sum_diff_dicts_truncates_samples` — sum three per-sport dicts each carrying 20 samples in one bucket; assert summed dict has ≤ `SAMPLE_N` samples in that bucket.

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
- **Do NOT** compare ticker sets across namespaces. v1/v2 emit `event_ticker`; `sp.kalshi_markets.ticker` is market-granularity. Any comparison without §4.3's normalization pass produces universally-disjoint sets → every dual-pairing lands in `both_pair_different`, which reads as maximum-danger regression signal. Namespace mismatch is the failure mode that would poison every downstream Item 7 threshold decision.
- **Do NOT** compute buckets from raw disagreement lists at read time. Pre-compute at write time per §3.3.
- **Do NOT** fold `v4_extraction_excluded` into `legacy_only`. Ever. Even if the count looks small "for now" — the exact failure mode this bucket exists to prevent is the count LOOKING like regression signal.
- **Do NOT** fold `agree_partial_coverage` into `both_pair_different`. Ever. Coverage-difference under a shared fixture is benign; conflating it with silent wrong-linking sets Item 7's threshold against noise and forces the operator to eyeball every disagreement sample to distinguish real from benign. The equal / overlap / disjoint partition (§3.5) must be applied at write time.
- **Do NOT** compare `_cache` state against `sp.*` state. Same-window or nothing. Applies to both sides: legacy pairings run against the same 24h `sp.kalshi_markets` records the v4 query pulls, AND v4's JOIN carries a `km.last_seen_at BETWEEN :window_start AND :window_end` clause (not just `fle.last_seen_at`). A window filter on only one side is worse than no filter — it invisibly compares populations of different sizes.
- **Do NOT** ship Deliverable 1 without the `total_evaluated == sum(buckets)` invariant check. Bad math in the diff produces the same false-confidence as the Day-53 circular tie-break.

---

## 5. Post-ship — reading the report

Once Deliverable 1 is shipping daily reports, the operator has the raw material to set Item 7's threshold. Suggested reading discipline (not proposing numbers — same reason as the reanchor left Item 7 blank):

- `both_pair_different` count is the most important number in the report. Post-fix, this bucket ONLY contains disjoint-ticker-set disagreements — records where v4 confidently disagrees with v3 on which fixture the fl_event corresponds to, invisibly. It is the silent-wrong-linking class. Suggested minimum for cutover: some operator-defined tolerance, probably measured in records/day rather than percentage.
- `agree_partial_coverage` count is expected to be non-zero and benign. Represents fl_events where v3 and v4 agree on the fixture but differ on which markets under that fixture are linked (v4 adds a market v3 didn't know about, or v3 has stale linkage to a market v4 has since retired). Sample the list to confirm — but the count itself is not a cutover blocker unless it moves suddenly. Item 7's threshold should be set on `both_pair_different`, NOT on `agree_partial_coverage + both_pair_different`.
- `legacy_only` count is the second most important. Records where v3 pairs and v4 doesn't. Some fraction of these will be legitimate v4 improvements (v3 pairing was wrong); some will be actual regressions. Sampling from the `sample_disagreements.legacy_only` list is the manual-triage path.
- `v4_only` count is expected to be non-zero and rising as v4's coverage exceeds v3's on new sports. Not a blocker; if anything, evidence Gate #2's underlying purpose (v4 is better than v3) is being met.
- `v4_extraction_excluded` count should be stable at the population size of KXMLBMENTION + doubles + props. Sharp movements here would indicate a new extraction-exclusion class shipped or an existing one broken.

The `sample_disagreements` block gives the operator concrete records to eyeball. Not the full disagreement set (which could be thousands of records/day at peak); the top-N samples per bucket, capped at some N that keeps `report_json` under a reasonable size ceiling. `SAMPLE_N = 30` in the draft code; adjustable.

### 5.1 Expected v2 noise classes (attribution key for `both_pair_different` triage)

**Item 7 threshold context**: v2's date-blind matching produces a small recurring background of `both_pair_different` records that are v2 defects, NOT v4 defects. v2 is being decommissioned, so we document these classes rather than fix them — the threshold-setter needs to know which slice of the `v2_diff.both_pair_different` count is attributable noise floor vs actual v4 defect signal.

Known v2 noise classes as of Day-60 (2 lifetime occurrences, both triaged, v4 record still clean):

- **Mention/outright class** (rHTxr6dU, Day-57 → PR #270): v2 pairs an fl_event's team pair to a Kalshi outright-mentions series (e.g. `KXMLBMENTION`) when the substring-based mention exclusion isn't triggered. Closed as a v4 fix (extend `_OUTRIGHT_SERIES_SUBSTRINGS`); the class shouldn't reappear post-#270. **Verification pattern**: check whether the Kalshi series is a `*MENTION*` ticker.
- **Adjacent-day-series class** (CUjfSzVI, Day-60): v2 joins an fl_event to the same team-pair's tickers on an ADJACENT day when a back-to-back series is in play. Cause is date-blind team-pair joining — v2 collapses `STL@TOR JUL31` and `STL@TOR AUG1` into one match because the team-pair key is date-agnostic. Recurring shape (MLB series, NBA back-to-backs, NHL home stands) so expect this class at low volume as long as v2 runs. **Verification pattern**: compare `fl_event.START_UTIME` UTC-date against the Kalshi series date component in the ticker (e.g. `26AUG011507STLTOR` → Aug 1); mismatch confirms v2 date-blindness.

**Reading protocol for the report** — when a `v2_diff.both_pair_different` count moves past the operator-set threshold, sample the top-N records and classify each into:

1. Known v2 noise class (mention, adjacent-day-series, future classes as documented) → attribute to noise floor, does not count against Item 7 threshold.
2. New shape not on this list → first-real-v4-defect candidate; halt cutover, add to this doc as a new noise class or ship the v4 fix.

v1's `both_pair_different` bucket does not have this issue at meaningful volume — v1 was the earlier iteration and its known noise classes are subsumed under v2's; v1 danger counts of 0 across the last N days confirm.

---

## 6. Success criteria

Deliverable 1 is considered shipped when:

1. `sp.daily_diff_reports.legacy_comparison_present = True` on every row written after the code lands.
2. `report_json` for every row includes `legacy_v1_diff` and `legacy_v2_diff` sub-dicts with the six bucket counts (`agree_same_fixture`, `agree_partial_coverage`, `v4_only`, `legacy_only`, `both_pair_different`, `v4_extraction_excluded`) per §3.3 schema, summed across sports per §4.5.
3. `bucket_sum == total_evaluated` invariant holds on every row (pipeline asserts pre-write; corrupt runs raise rather than silently persist).
4. `v4_extraction_excluded` count is non-zero on FL cron reports (proves the classification pass fires; population is at least the KXMLBMENTION + doubles rate).
5. **Smoke evidence that namespace normalization works**: `agree_same_fixture + agree_partial_coverage` count on the first report is materially above zero for a sport with known-overlapping v3/v4 coverage (e.g. NBA regular-season games). If this bucket is near-zero on day one, the ticker-namespace normalization has silently regressed to the pre-fix state and every diff lands in `both_pair_different`. Fail loud, don't debug in production.
6. `sp.baseline_shifts` row exists documenting the measurement expansion per §4.7.
7. Gate #2 status in `PROJECT_STATE.md` moves from "REOPENED Day-54 — HALF-BUILT" to "CLOSED — Deliverable 1 shipped 2026-07-28; threshold SET 2026-08-01."

**THRESHOLD SET 2026-08-01 (Day-61, operator-approved).** Evidence basis: 7 consecutive cron reports, danger range 0-2/day 100% v2-attributed (3 adjudications: rHTxr6dU mention → PR #270, CUjfSzVI + 4We9ZQLk adjacent-day-series → §5.1 documented), v1 identity 7/7, coverage +30/day. Four gates:

1. **PRIMARY — `both_pair_different` ≤ 3/day PER FLAVOR** (`v1_danger` and `v2_danger` counted each, not summed), AND every occurrence must be triage-verified to a documented §5.1 v2 defect class via the START_UTIME playbook. Untriaged records count against the gate until triaged. **ANY v4 adjudication = automatic hold regardless of count.**
2. **SECONDARY — `legacy_only` gated on trend, not count**: `fixture_linked_in_window_count` must be non-declining week-over-week at each flag decision.
3. **INSTRUMENT-HEALTH PRECONDITIONS** — `per_sport_errors = 0` AND v1 identity (`agree_same_fixture + agree_partial_coverage + v4_only = fixture_linked_in_window_count`) holding on any report used for a flag decision. Sick instrument = no decision.
4. **ROLLOUT CADENCE** — daily report read at each stage (5% → 25% → 100%); any breach freezes the current percentage (no rollback; v3 serves the remainder).

Gate readings are on the reports this scope doc's measurement dimension produces — this workstream owns the instrument, the four-gate spec owns the decisions the instrument feeds. Phase 3 opens with the flag-wiring, soccer-first rollout plan, and 5% flip plan documented in `PROJECT_STATE.md` phase status header.

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

- **D2/D1 window-predicate semantics — cron populations exclude still-active rows**. D2's `_KALSHI_WINDOW_SQL` / `_FL_WINDOW_SQL` load rows via `last_seen_at >= :window_start AND last_seen_at < :window_end`. Deliverable 1's `_query_v4_pairings` inherits this via PK-scope (D1 sees exactly what D2 loaded). Under the 03:00 UTC cron with a 24h historical window, rows still active at run time have `last_seen_at ≈ NOW() > window_end` and are EXCLUDED (PR #260 bumps `last_seen_at` on every ingestion pass regardless of content change). Consequence: **the cron population is dominated by records that concluded / delisted / went stale during the window; still-active records at run time are absent.** Manual runs with a `window_end` at or after "now" capture active records too. Two different populations for the "same" nominal window. Possibly acceptable as a documented choice (concluded-events-only gives more stable measurement), but **must be a decision, not a default**. Options: (a) keep + document explicitly so the first cron report's different character doesn't read as regression, (b) change the anchor to `last_changed_at` (bumps only on content change, more stable), (c) extend the predicate to include "still-active at run time" rows. Decision is a separate workstream — this scope doc does not resolve it. Tracked as task #25.
- **Sport-level diff breakdowns**. Not required to close Gate #2; natural extension once Deliverable 1 is shipping.
- **Alerting on `both_pair_different` step-changes**. Once trend data exists, a Nth-percentile step-detector is worth wiring. Not urgent; the manual weekly-read discipline works at current volume.
- **Automated cutover gating**. Threshold now SET (see §6 above) — `/api/v4` traffic-flag flips can be conditioned on the four-gate spec. Implementation deferred to Phase 3's flag-wiring workstream. Gate semantics locked as **hard-block on primary** (untriaged `both_pair_different` > 3/day per flavor OR any v4 adjudication → stage-freeze) plus **soft-warn on secondary** (declining `fixture_linked_in_window_count` trend → alert for operator review, no automatic freeze).
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
