# FL Home/Away Inversion — Instrumentation & Detector

Diagnostic doc for the FL fixture-inversion class surfaced Day-47. Ships with
the additive `reason_detail` keys in `resolver/matcher.py` after `:180`.
Instrumentation without a detector is just bigger rows; this doc is the
detector.

---

## What we know

**Class**: `sp.fixtures.home_team_id` holds the team FL's `HOME_NAME` names
as away, and vice versa. Exact-match test both directions, no fuzzy judgment
involved. Signature on 100% of the class:

- `first_decision_provider = 'fl'`
- `created_new_fixture = true`
- `kalshi_markets_on_fixture = 0`
- `fl_transitional_path = 'created_null_comp_fixture'`
- `fl_transitional_sport_only = true`
- Created 02:03–02:10 UTC (daily FL cron window)
- Spans Soccer and Basketball

**Sizing** (Day-47, full history, no `last_seen_at` filter): 95 inverted
fixtures total, first appearance week of 2026-05-04. Rate normalized against
FL `created_new_fixture` denominator is stable at ~0.2% weekly (0.19 / 0.34 /
0.49 / 0.06 / 0.11 / 0.04 / 0.09 / 0.14 / 0.39 / 0.16). Poisson noise around
a constant rate; not a bootstrap artifact; has not self-corrected. Expected
volume going forward: ~1–7 qualifying creations per week.

**Inheritance propagates**: 20 of the first 50 lack
`created_new_fixture = true` — they linked to an already-inverted fixture
via strict-tier `find_fixture`. Original defect is upstream of the linkers.

## What's eliminated

Six hypotheses ruled out on read-only trace (Day-47):

1. Kalshi-origin inheritance — `first_decision_provider = 'fl'` on all,
   zero Kalshi markets on the fixture.
2. Swap-probe propagation — no `orientation_flipped = true`; the probe
   (`resolver/matcher.py:207-223`) is a read-only `find_fixture` lookup and
   does not carry swapped orientation into `ensure_fixture`.
3. Writer / `ensure_fixture` transposition — `reason_detail.home_team_id`
   and `sp.fixtures.home_team_id` come from the same locals (assigned at
   `matcher.py:154-155`, stamped into `reason_detail` at `:179-180`, passed
   positionally to `ensure_fixture` at `:228-234`). They cannot disagree,
   and both contradict FL.
4. Extraction branching — `FLResolverModule.extract_signal` at
   `resolver/fl.py:88-97` is straight-line kwargs, no payload-shape,
   sport, or competition branch.
5. Ingestion transform — `ingestion/fl.py:243` stores `raw = event_raw`
   verbatim; `ingestion/base.py:191` copies `raw_payload = r["raw"]`
   verbatim.
6. Participant-id precedence — `_team_candidates` weights `fl_team_id 1.0
   > name 0.9 > shortname 0.7`, and `AliasResolver.resolve()`
   (`resolver/aliases.py:86-120`) short-circuits on first unambiguous hit,
   so a crossed `participant_id` WOULD shadow a correct `HOME_NAME`. But
   `HOME_PARTICIPANT_TEAM_ID` / `AWAY_PARTICIPANT_TEAM_ID` are NULL on all
   11 sampled — the weight-1.0 candidate is absent, `resolve()` falls
   through to the weight-0.9 name candidate. Shortnames corroborate names
   (CLI/LIS, RIV/RAC, BAT/VAL).

## Surviving hypothesis

FL emits crossed `HOME_NAME` / `AWAY_NAME` on some rare event shape at a
steady ~0.2%. Currently unfalsifiable retroactively:

- `sp.fl_events.payload_hash` is overwritten unconditionally on every
  UPSERT (`ingestion/base.py:212`), so the current hash tells us nothing
  about the hash at decision time.
- No payload-history table exists for `sp.fl_events`.
- Strict-tier `reason_detail` stamps ids only (`home_team_id`,
  `away_team_id`) — no names, unlike the alias tier
  (`home_provider_normalized`, `home_canonical`) and the fuzzy tier which
  do capture the string form.
- `payload_changed_after_decision` was TRUE on 11/11 sampled, but the
  non-inverted control on the same path is 2381 TRUE / 684 FALSE (78%
  baseline). P(11/11 | null) ≈ 0.06 — not dispositive.

## Instrumentation shipped

`resolver/matcher.py` at the strict-tier gate, after ids stamp
(`:179-180`), freezes the extractor's view of both sides:

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

Additive JSONB. No schema migration. All strict-tier decisions —
including future FL creations of fixtures — carry a frozen snapshot of
what the extractor saw at decision time, independent of any later
`raw_payload` overwrite.

## Detector queries

### Prerequisites — extensions

Both detector queries use only:

- `lower(...)` — always available.
- `pg_trgm` (installed via migration `b8e1f4c2a7d3_phase_2f0_1_pg_trgm_extension`)
  — used only in the mis-resolution follow-up notes at the bottom, not in
  the core detectors below.

There is **no `unaccent` dependency**. `unaccent()` is not installed in
the migration chain, and every diagnostic query run to size the class
Day-47 used bare `lower()`. If a future refinement wants diacritic-
insensitive equality, add `CREATE EXTENSION IF NOT EXISTS unaccent` as
a migration first — do not introduce it inline into a doc query.

### Column names — one-time reminder

- `sp.resolution_log.provider_record_id` (Text) — join column to
  provider records. NOT `record_id`.
- `sp.fl_events.fl_event_id` (Text, PK) — join into fl_events. NOT
  `record_id`.
- `sp.resolution_log.provider` distinguishes FL vs Kalshi decisions.

### Handling append-only `sp.resolution_log`

`sp.resolution_log` is append-only — multiple rows per
`(provider, provider_record_id)`. A naïve join to `sp.fixtures` inflates
both numerator and denominator. The ratio survives (both inflate
together), but the absolute row count does not.

Both detectors below dedupe to **one row per record** via
`DISTINCT ON (provider, provider_record_id) ... ORDER BY decided_at ASC`,
picking the **creating decision** (the one that first linked or created
the fixture). `count(DISTINCT sp.fixtures.id)` works for the aggregate
rate but loses the per-record fields the candidate-snapshot query needs,
so `DISTINCT ON` is the shared building block.

### 1. Weekly-rate detector — the normalized rate

Runs on any cadence. The class is defined by exact home/away match on
canonical name, no fuzzy judgment. If the rate spikes materially above
the ~0.2% baseline, escalate.

```sql
WITH creating_decisions AS (
  -- One row per (provider, provider_record_id): the earliest decision
  -- that carries fl_transitional_path='created_null_comp_fixture'.
  SELECT DISTINCT ON (rl.provider, rl.provider_record_id)
    rl.provider,
    rl.provider_record_id,
    rl.fixture_id,
    rl.decided_at,
    date_trunc('week', rl.decided_at) AS week
  FROM sp.resolution_log rl
  WHERE rl.reason_detail->>'provider'             = 'fl'
    AND (rl.reason_detail->>'created_new_fixture')::boolean IS TRUE
    AND rl.reason_detail->>'fl_transitional_path' = 'created_null_comp_fixture'
  ORDER BY rl.provider, rl.provider_record_id, rl.decided_at ASC
),
inverted AS (
  SELECT
    c.week,
    c.fixture_id
  FROM creating_decisions c
  JOIN sp.fixtures  f  ON f.id = c.fixture_id
  JOIN sp.teams     th ON th.id = f.home_team_id
  JOIN sp.teams     ta ON ta.id = f.away_team_id
  JOIN sp.fl_events fle
    ON fle.fl_event_id = c.provider_record_id
  WHERE lower(th.canonical_name) = lower(fle.raw_payload->>'AWAY_NAME')
    AND lower(ta.canonical_name) = lower(fle.raw_payload->>'HOME_NAME')
)
SELECT
  c.week,
  count(DISTINCT c.fixture_id)                                             AS creations,
  count(DISTINCT inv.fixture_id)                                           AS inverted,
  round(
    100.0 * count(DISTINCT inv.fixture_id)::numeric
          / NULLIF(count(DISTINCT c.fixture_id), 0),
    2
  )                                                                        AS pct
FROM creating_decisions c
LEFT JOIN inverted inv USING (fixture_id)
GROUP BY c.week
ORDER BY c.week DESC;
```

Expected: ~0.2% weekly. Alert threshold: any week ≥ 1.0% on a denominator
≥ 200 (denominator floor filters low-N noise).

Caveat on the `lower()` equality: it will miss an inversion where the
canonical carries diacritics or punctuation the FL string omits (or vice
versa). That's the same limitation the Day-47 sizing query had; the 95
figure is a lower bound on that ground. Live inversions with an
`extracted_home_candidates` snapshot don't depend on this comparison —
they use detector 2 below.

### 2. Candidate-snapshot comparison — dispositive on new inversions

Runs against decisions written after the instrumentation lands.
Reason: the snapshot is what the extractor SAW; the fixture is what
resolution produced. If they disagree in the specific shape below, FL
sent us a crossed payload — source-side guard needed. If they agree, the
inversion originated downstream of extraction and the trace has a gap.

The filter `kind = 'name'` matches the literal emitted by
`FLResolverModule._team_candidates` (`resolver/fl.py:141`) and covered
by `tests/test_resolver_2b.py::test_extracted_candidates_stamped_into_reason_detail`.

```sql
WITH creating_decisions AS (
  -- One row per record: earliest transitional-created FL decision that
  -- carries the new extracted_home_candidates key.
  SELECT DISTINCT ON (rl.provider, rl.provider_record_id)
    rl.provider,
    rl.provider_record_id,
    rl.fixture_id,
    rl.reason_detail,
    rl.decided_at
  FROM sp.resolution_log rl
  WHERE rl.reason_detail->>'provider'             = 'fl'
    AND (rl.reason_detail->>'created_new_fixture')::boolean IS TRUE
    AND rl.reason_detail->>'fl_transitional_path' = 'created_null_comp_fixture'
    AND rl.reason_detail ? 'extracted_home_candidates'
  ORDER BY rl.provider, rl.provider_record_id, rl.decided_at ASC
),
snapshots AS (
  SELECT
    c.provider_record_id                            AS fl_event_id,
    c.fixture_id,
    c.decided_at,
    (
      SELECT elem ->> 'raw'
      FROM jsonb_array_elements(c.reason_detail->'extracted_home_candidates') elem
      WHERE elem ->> 'kind' = 'name'
      LIMIT 1
    )                                               AS home_name_at_decision,
    (
      SELECT elem ->> 'raw'
      FROM jsonb_array_elements(c.reason_detail->'extracted_away_candidates') elem
      WHERE elem ->> 'kind' = 'name'
      LIMIT 1
    )                                               AS away_name_at_decision
  FROM creating_decisions c
)
SELECT
  s.fl_event_id,
  s.decided_at,
  s.home_name_at_decision,
  th.canonical_name                                 AS fixture_home_canonical,
  s.away_name_at_decision,
  ta.canonical_name                                 AS fixture_away_canonical,
  -- The dispositive column. TRUE means the extractor SAW crossed inputs
  -- (FL sent us the inversion). FALSE on an inverted fixture means
  -- extraction was clean and the inversion originated between extraction
  -- and INSERT — trace gap.
  (
    lower(th.canonical_name) = lower(s.away_name_at_decision)
    AND
    lower(ta.canonical_name) = lower(s.home_name_at_decision)
  )                                                 AS extractor_saw_crossed_input
FROM snapshots s
JOIN sp.fixtures  f   ON f.id = s.fixture_id
JOIN sp.teams     th  ON th.id = f.home_team_id
JOIN sp.teams     ta  ON ta.id = f.away_team_id
-- Restrict to actually-inverted fixtures: canonical of the fixture's
-- home_team_id must match the payload's AWAY_NAME. Join fle here (not
-- in the primary CTE) so the snapshot data survives if the fl_events
-- row is later purged, but drop the row from the detector output when
-- the current payload doesn't match the inversion signature.
JOIN sp.fl_events fle ON fle.fl_event_id = s.fl_event_id
WHERE lower(th.canonical_name) = lower(fle.raw_payload->>'AWAY_NAME')
  AND lower(ta.canonical_name) = lower(fle.raw_payload->>'HOME_NAME')
ORDER BY s.decided_at DESC;
```

### What each outcome means

- `extractor_saw_crossed_input = TRUE` — FL emitted a payload with
  `HOME_NAME` and `AWAY_NAME` transposed. Source-side guard needed at
  the extractor: cross-check `HOME_PARTICIPANT_TEAM_ID` (when present)
  against `HOME_NAME`, or reject/log records failing an FL-side sanity
  probe. This is the surviving hypothesis being confirmed.

- `extractor_saw_crossed_input = FALSE` on an inverted fixture — the
  extractor saw clean input, resolution produced the correct
  `(home_id, away_id)`, but the fixture INSERT ended up crossed. Would
  require re-tracing between `matcher.match()` and `ensure_fixture`
  since the current trace covers those. Not the expected outcome given
  what's been ruled out — but the point of the detector is that it
  distinguishes.

## What NOT to do

**Do not backfill the 95.** Cause is unknown; any rewrite is a guess at
which side is correct, and the rewrite destroys the evidence a future
detector run needs. When the class is understood, backfill decision
becomes tractable — until then, leave them.

**Do not add a payload-history table or `payload_hash_at_decision` yet.**
Both were considered and skipped. The candidate snapshot alone is
sufficient to distinguish extractor-saw-crossed from resolver-crossed,
and expected volume (~1–7/week) makes the wait for live evidence short.
Revisit if a second occurrence class appears.

## Expected timeline

Instrumentation lands → next FL cron pass (02:00 UTC) exercises the new
keys on the day's fresh unresolved records → any new inversion carries
`extracted_home_candidates` / `extracted_away_candidates`. First
dispositive row available within 1–7 days of merge given the ~0.2%
rate. Run the candidate-snapshot query weekly until the first hit; then
the fix is determined by which side of the FALSE/TRUE it lands on.

## `reason_detail` size growth

The stamp adds one JSONB list per side per strict-tier decision. FL
typically emits 2–3 candidates per side (participant_id when present,
name, shortname); Kalshi typically 1–2. Six candidate dicts per
decision at ~50 bytes each ≈ 300 bytes added per row. `sp.resolution_log`
is not GIN-indexed on `reason_detail` today (indexes at
`sp_models.py:402-406` are on `run_id`, `(provider, provider_record_id)`,
`fixture_id`, `decided_at`), so the growth cost is table-only.
`resolution_log` retention is architecturally forever per §7.6, so the
growth compounds — negligible in absolute terms (300 bytes × few million
rows/year) but worth revisiting if a future PR proposes GIN-indexing
`reason_detail`.

## Cross-references

- Sizing methodology and rejection of the six hypotheses:
  PROJECT_STATE.md Day-47.
- The strict-tier stamp point in code: `resolver/matcher.py`
  after `:180`.
- The unit test that guards the stamp shape and the JSON round-trip:
  `tests/test_resolver_2b.py::test_extracted_candidates_stamped_into_reason_detail`.
- Why `payload_hash` doesn't help: `ingestion/base.py:208-216`.
- Why participant-id precedence WOULD shadow if it were populated:
  `resolver/fl.py:127-167` + `resolver/aliases.py:86-120`.
