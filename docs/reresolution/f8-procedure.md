# F8 Validation — Re-Resolution Loop End-to-End Test

Dispositive validation for the §7.6 / §7.7 re-resolution loop: prove
the loop catches a newly-added alias and flips a stuck record from
no_match to resolved. Executes in one uninterrupted ~15–20 min
window.

## Context — why F8 is needed and why it's staged

The loop passes daily on structural checks: fires on schedule, runs
under the 5s F6 halt ceiling, exits cleanly with `crashes=0` and
`halt_warnings=[]`. But `candidate_set_size=0` every pass since
Day-45. That means the loop *runs* but hasn't been *observed
responding*: no live natural case exists in the in-window backlog to
watch it work end-to-end.

The backlog composition surfaced this: addressable records in the
3-day `last_seen_at` window are dominated by (a) coverage gaps
(teams not in `sp.teams` at all — bootstrap cases, not alias cases)
and (b) collision cases (teams whose only alias exists but the
record fails on the opposite side or a kickoff gate). No pristine
"team exists, one alias missing" natural case is available.

So F8 is staged: we manufacture the exact condition the loop is
built to respond to, then watch it respond.

## Approach — remove-and-restore

Take a record currently resolving via a single alias. Break it
deliberately, drive the resolver to write a fresh `no_match`
decision (with the team's `team_id` preserved in `reason_detail`),
re-add the alias, watch the next 5-min cron pass flip the record.
The break is reversible within the same session; the record is
back to its original resolved state before session close.

**Structural reason this beats bootstrap-then-alias** (Approach 2):
the loop's Tier-2 LOOSE containment filter requires the added
alias's `team_id` to already appear in the record's prior
`reason_detail`. A bootstrapped brand-new team's `id` has never
been in any prior decision, so the daily cron — not the 5-min
reresolution loop — would flip such a record. The remove-and-
restore path uses a team already known to the resolver, so
Tier-2 fires and the *loop* is genuinely what does the flip.

## Selection criteria — §1 through §2c

All three must pass. §2c was added Day-46 after Attempt 1 hit the
canonical_name-shadowing failure mode (see Appendix — Lessons
Learned).

### §1 — Discovery query (find single-alias-dependency candidates)

```sql
WITH recent_fl AS (
    SELECT
        fle.fl_event_id,
        fle.fixture_id,
        fle.last_seen_at,
        fle.raw_payload,
        fx.home_team_id,
        fx.away_team_id,
        ht.canonical_name AS home_canonical,
        at.canonical_name AS away_canonical,
        ht.sport_id
    FROM sp.fl_events fle
    JOIN sp.fixtures fx ON fx.id = fle.fixture_id
    JOIN sp.teams ht    ON ht.id = fx.home_team_id
    JOIN sp.teams at    ON at.id = fx.away_team_id
    WHERE fle.fixture_id IS NOT NULL
      AND fle.last_seen_at > NOW() - INTERVAL '3 days'
),
alias_counts AS (
    SELECT
        ta.team_id,
        COUNT(*) AS alias_count,
        MIN(ta.id)               AS only_alias_id,
        MIN(ta.alias)            AS only_alias_form,
        MIN(ta.alias_normalized) AS only_alias_normalized,
        MIN(ta.source)           AS only_alias_source
    FROM sp.team_aliases ta
    GROUP BY ta.team_id
)
SELECT
    r.fl_event_id,
    r.fixture_id,
    r.last_seen_at,
    r.raw_payload->>'HOME_NAME' AS fl_home_name,
    r.raw_payload->>'AWAY_NAME' AS fl_away_name,
    CASE
      WHEN h.alias_count = 1 AND COALESCE(a.alias_count, 99) > 1 THEN 'home'
      WHEN a.alias_count = 1 AND COALESCE(h.alias_count, 99) > 1 THEN 'away'
      WHEN h.alias_count = 1 AND a.alias_count = 1                 THEN 'both'
      ELSE NULL
    END AS break_side,
    r.home_team_id,   r.home_canonical, h.alias_count AS home_alias_count,
    h.only_alias_form  AS home_only_alias,  h.only_alias_source AS home_alias_source,
    r.away_team_id,   r.away_canonical, a.alias_count AS away_alias_count,
    a.only_alias_form  AS away_only_alias,  a.only_alias_source AS away_alias_source,
    s.name AS sport
FROM recent_fl r
JOIN sp.sports s ON s.id = r.sport_id
LEFT JOIN alias_counts h ON h.team_id = r.home_team_id
LEFT JOIN alias_counts a ON a.team_id = r.away_team_id
WHERE (h.alias_count = 1 OR a.alias_count = 1)
ORDER BY r.last_seen_at DESC
LIMIT 50;
```

**What to prefer**:

- Non-load-bearing fixtures — national-team friendlies, off-season
  exhibitions, low-tier league one-offs. Avoid records with active
  markets.
- `break_side` is `'home'` or `'away'` (single-side dependency).
  `'both'` also works but doubles the surface area you're touching.
- Alias source: `bootstrap_league_coverage` or `legacy_bootstrap`.
  Avoid `fuzzy_auto` — the matcher can re-derive that variant even
  without the row.

Record the picked values: `{FL_EVENT_ID}`, `{TEAM_ID}` (the break
side's team_id), `{ALIAS_ID}`, `{ALIAS_FORM}`, `{ALIAS_NORMALIZED}`,
`{ALIAS_SOURCE}`, `{FL_BREAK_SIDE_NAME}` (the `HOME_NAME` /
`AWAY_NAME` FL sends for the break side — used by §2c). These are
the placeholders for the rest of the procedure.

### §2a — Single-alias dependency confirmation

```sql
SELECT id, alias, alias_normalized, source, confidence, created_at
FROM sp.team_aliases
WHERE team_id = '{TEAM_ID}'
ORDER BY created_at;
```

**Expected**: exactly one row. If more than one, the candidate is
over-determined — pick a different record from §1.

### §2b — Team_id appears in the record's prior `reason_detail`

The load-bearing check for the Tier-2 LOOSE filter. Without this,
the loop can't see the record as a candidate even after re-add.

```sql
SELECT
    rl.id,
    rl.decided_at,
    rl.reason_code,
    rl.reason_detail->'colliding_home_team_ids'      AS colliding_home,
    rl.reason_detail->'colliding_away_team_ids'      AS colliding_away,
    rl.reason_detail->>'candidate_home_team_id'      AS candidate_home,
    rl.reason_detail->>'candidate_away_team_id'      AS candidate_away,
    rl.reason_detail->>'home_team_id'                AS home_team_id_key,
    rl.reason_detail->>'away_team_id'                AS away_team_id_key,
    rl.reason_detail->'asymmetric_failed_side_candidate_team_ids'
                                                     AS asym_failed
FROM sp.resolution_log rl
WHERE rl.provider = 'fl'
  AND rl.provider_record_id = '{FL_EVENT_ID}'
ORDER BY rl.decided_at DESC
LIMIT 1;
```

**Expected**:

- `reason_code IN ('strict', 'alias', 'fuzzy')` — record currently
  resolves; we'll break it next.
- `{TEAM_ID}` appears in **at least one** of the shown key shapes
  (any one of home/away/colliding/candidate/asymmetric).

If `{TEAM_ID}` is absent from all key shapes: the prior decision
didn't reference it by UUID (can happen with pure strict-tier
name-match paths that don't stamp UUIDs into `reason_detail`). Pick
a different record — the loop won't catch this one after re-add.

### §2c — Canonical-name shadow-prevention check (added Day-46)

**Why this exists**: the alias tier and fuzzy tier build
`CandidateIndex` from `sp.teams.canonical_name`, not from
`sp.team_aliases` (Day-21 architectural finding). Deleting an alias
row breaks *only* the strict tier. If the team's `canonical_name`
also matches the FL provider string post-normalization, the alias
tier immediately re-matches it by name and Tier-1 puts the record
into `review_queue` (or worse, `strict`/`alias`/`fuzzy`) — never
producing the `no_match` decision the loop's Tier-1 filter requires.

**The check**: compare the FL provider string for the break side
against the team's `canonical_name`, normalized. They must differ.

```sql
-- Normalize both strings the same way the resolver does
-- (lowercase, strip diacritics, collapse whitespace).
WITH normed AS (
    SELECT
        lower(regexp_replace(
            unaccent('{FL_BREAK_SIDE_NAME}'),
            '\s+', ' ', 'g'
        )) AS fl_normalized,
        lower(regexp_replace(
            unaccent((SELECT canonical_name
                      FROM sp.teams
                      WHERE id = '{TEAM_ID}')),
            '\s+', ' ', 'g'
        )) AS canonical_normalized
)
SELECT
    fl_normalized,
    canonical_normalized,
    (fl_normalized = canonical_normalized) AS SHADOW_RISK
FROM normed;
```

**Expected**: `SHADOW_RISK = FALSE`. The two strings must NOT be
equal after normalization.

**If `SHADOW_RISK = TRUE`**: pick a different record. Preferred
shapes for what to look for:

- FL sends a shorter form ("Bonn") while canonical is fuller
  ("Telekom Baskets Bonn").
- FL sends an abbreviation while canonical is spelled out.
- Canonical contains sponsor / suffix that FL doesn't send.
- Any material differences in tokens after normalization.

**If `unaccent` isn't available on the DB**, the `unaccent(...)`
wrappers can be dropped for a rougher check (ASCII-only equality)
and diacritics compared by eye against the raw strings; the
strict "must differ" gate holds either way.

## §3 — Snapshot before any write

PowerShell-style, output to a JSON scratch file so §8 comparison is
mechanical:

```powershell
$ts = Get-Date -Format "yyyyMMdd-HHmmss"
$out = "f8-snapshot-{FL_EVENT_ID}-$ts.json"

psql $env:DATABASE_URL -At -c @"
SELECT jsonb_build_object(
    'snapshot_ts',  to_char(NOW() AT TIME ZONE 'UTC',
                            'YYYY-MM-DD HH24:MI:SS') || 'Z',
    'fl_event',     (
        SELECT to_jsonb(fle) FROM sp.fl_events fle
        WHERE fle.fl_event_id = '{FL_EVENT_ID}'
    ),
    'alias_row',    (
        SELECT to_jsonb(ta) FROM sp.team_aliases ta
        WHERE ta.id = '{ALIAS_ID}'
    ),
    'latest_resolution_log', (
        SELECT to_jsonb(rl) FROM sp.resolution_log rl
        WHERE rl.provider = 'fl'
          AND rl.provider_record_id = '{FL_EVENT_ID}'
        ORDER BY rl.decided_at DESC
        LIMIT 1
    ),
    'team_alias_count_before', (
        SELECT COUNT(*) FROM sp.team_aliases
        WHERE team_id = '{TEAM_ID}'
    )
)::text;
"@ > $out

Write-Host "Snapshot written: $out"
Get-Content $out | ConvertFrom-Json | ConvertTo-Json -Depth 10 | Write-Host
```

**Verify**: file exists, all four nested objects populated. Keep
`$out` in scope for §8.

## §0 — Pattern D pre-flight (run before EACH write step)

Amendment #17 discipline. Prefer database identity checks over
network identity — Neon's `inet_server_addr()` returns the
link-local proxy (`169.254.254.254`), not the endpoint (Day-21
lesson), so it can't be used to confirm production.

```sql
SELECT
    current_database() AS db,
    current_setting('server_version') AS pg_version;
```

**Expected**: `db = 'neondb'`.

**Also eyeball**: the shell's `DATABASE_URL` env var must contain
`ep-fragrant-frog-ak3esp11`. This is the endpoint-identity check;
`inet_server_addr()` cannot substitute.

For the `run_resolver_pass.py` / `run_reresolution_pass.py` steps,
the script's own `_check_pattern_d_endpoint` handles this — verify
`EXPECTED_PRODUCTION_DB_NAME=neondb` and
`EXPECTED_PRODUCTION_DB_HOST` are set in the shell before invoking.

## §4 — Break the record

**Pattern D check** (re-run §0). Then:

```sql
BEGIN;

-- Verify exactly what we expect to delete (single row):
SELECT id, team_id, alias, alias_normalized, source
FROM sp.team_aliases
WHERE id = '{ALIAS_ID}';
-- Expected: one row, team_id = '{TEAM_ID}'.

DELETE FROM sp.team_aliases
WHERE id = '{ALIAS_ID}';

-- Verify exactly what we expect to update:
SELECT fl_event_id, fixture_id, last_seen_at
FROM sp.fl_events
WHERE fl_event_id = '{FL_EVENT_ID}';
-- Expected: one row, non-null fixture_id.

UPDATE sp.fl_events
SET fixture_id = NULL
WHERE fl_event_id = '{FL_EVENT_ID}'
  AND fixture_id IS NOT NULL;  -- guard against double-clear

-- Sanity:
SELECT
    (SELECT COUNT(*) FROM sp.team_aliases WHERE id = '{ALIAS_ID}')
        AS alias_rows_remaining,
    (SELECT fixture_id FROM sp.fl_events
     WHERE fl_event_id = '{FL_EVENT_ID}') AS fl_fixture_id_after;
-- Expected: 0, NULL.

COMMIT;
```

If anything in the sanity row looks wrong → **ROLLBACK**, re-check
the snapshot, re-investigate.

## §5 — Force the fresh no_match decision

**Pattern D**: verify PowerShell session env vars — `DATABASE_URL`,
`EXPECTED_PRODUCTION_DB_NAME=neondb`,
`EXPECTED_PRODUCTION_DB_HOST=ep-fragrant-frog-ak3esp11.<region>.aws.neon.tech`.

```powershell
python scripts/run_resolver_pass.py --provider fl --run-mode standalone --limit 50
```

**Why `--limit 50` (Day-45 operator note)**: `--limit 1` assumes our
record is the freshest unresolved FL row — fragile if newer
ingestion landed in between. 50 gives enough headroom while keeping
the pass short. If `--limit 50` doesn't reach the record (verify
next), raise progressively (`--limit 500`, `--limit 5000`).

Confirm the specific record got the fresh decision:

```sql
SELECT id, reason_code, reason_detail, decided_at, resolver_version
FROM sp.resolution_log
WHERE provider = 'fl'
  AND provider_record_id = '{FL_EVENT_ID}'
ORDER BY decided_at DESC
LIMIT 2;
```

**Expected**:

- Most recent row has `reason_code = 'no_match'` AND
  `decided_at` is post-§4.
- Previous row is the original resolved decision (from §3
  snapshot).
- The `reason_detail` on the fresh no_match row contains
  `{TEAM_ID}` in at least one key shape (re-run §2b's precise
  key-shape query against this new row).

**Failure modes**:

- Fresh row's `reason_code ≠ 'no_match'` (e.g. `review_queue` or
  even `alias`): the alias tier / fuzzy tier likely still resolves
  the record. This is the **canonical_name shadow** failure mode
  §2c prevents. If it hits, the selection failed §2c — pick a
  different record, restore this one (§9), start over.
- Fresh row's `reason_detail` has no team_id at all: strict-tier
  no_match records sometimes omit `team_id` entirely. Loop can't
  catch this — pick a different record.

## §6 — Confirm candidacy (tighten the broken window)

Manual dry-run rather than waiting for the next 5-min cron:

```powershell
python scripts/run_reresolution_pass.py --provider fl --dry-run
```

**Expected**: `candidate_set_size ≥ 1` in the stdout summary.

If `candidate_set_size = 0`: either the record didn't produce a
qualifying no_match row (revisit §5), or Tier-2 containment
predicate can't find `{TEAM_ID}` in the reason_detail (revisit
§2b). Do NOT proceed to §7 until candidacy is confirmed.

Also confirm the record stays no_match this pass:

```sql
SELECT reason_code, decided_at
FROM sp.resolution_log
WHERE provider = 'fl' AND provider_record_id = '{FL_EVENT_ID}'
ORDER BY decided_at DESC LIMIT 1;
```

`reason_code = 'no_match'`, `decided_at` unchanged from §5 (a
`--dry-run` writes nothing).

## §7 — Re-add the alias (the alias-add event)

**Pattern D check.** Then:

```sql
BEGIN;

-- Verify absence before re-inserting:
SELECT COUNT(*) FROM sp.team_aliases WHERE id = '{ALIAS_ID}';
-- Expected: 0.

INSERT INTO sp.team_aliases
  (id, team_id, alias, alias_normalized,
   source, confidence, created_at)
VALUES (
    '{ALIAS_ID}',
    '{TEAM_ID}',
    '{ALIAS_FORM}',
    '{ALIAS_NORMALIZED}',
    '{ALIAS_SOURCE}',
    {SNAPSHOT_CONFIDENCE},  -- read from snapshot
    NOW()                   -- LOAD-BEARING — see note below
);

SELECT id, team_id, alias_normalized, source, confidence, created_at
FROM sp.team_aliases WHERE id = '{ALIAS_ID}';

COMMIT;
```

**Why `created_at = NOW()` (Day-45 operator note)**: the Tier-2
freshness predicate is
`sp.team_aliases.created_at > last_decision.decided_at`. If you set
`created_at` to the snapshot's original past value, the freshness
filter won't fire and the loop won't catch it. **`NOW()` is the
one field that must NOT match the snapshot**; every other field
should. The snapshot's original `created_at` is preserved elsewhere
(the snapshot JSON) for post-hoc reference.

## §8 — The dispositive moment (real cron, not manual)

**Wait for the next real FL cron tick** (up to 5 min). The loop
should:

1. Pick the record up (Tier-1 SQL finds the fresh no_match; Tier-2
   containment fires — `{TEAM_ID}` in `reason_detail` AND
   `team_aliases.created_at > that.decided_at`).
2. Run the matcher.
3. Resolve via the restored alias → fresh `resolution_log` row
   with `reason_code IN ('strict', 'alias', 'fuzzy')`.
4. UPDATE `fl_events.fixture_id` to repopulate.

**Why real cron, not `--apply` (Day-45 operator note)**: a manual
`run_reresolution_pass.py --provider fl --apply` would also work,
but the dispositive test is "what happens in the live production
loop" — waiting for the real cron pass proves the live scheduled
service does the flip, not just the code paths.

Confirm:

```sql
-- The resolver_runs row showing the loop caught it:
SELECT run_id, started_at, finished_at,
       extra->>'candidate_set_size' AS candidate_set_size,
       extra->>'auto_applies'       AS auto_applies,
       extra->>'no_match'           AS no_match,
       extra->>'latency_total_ms'   AS total_ms
FROM sp.resolver_runs
WHERE provider = 'fl'
  AND run_mode = 'live'
  AND started_at > (SELECT created_at FROM sp.team_aliases
                    WHERE id = '{ALIAS_ID}')
ORDER BY started_at DESC
LIMIT 3;
-- Expected: a pass with candidate_set_size ≥ 1, auto_applies ≥ 1.

-- The fresh resolution_log row from the loop:
SELECT id, reason_code, reason_detail, decided_at, resolver_version
FROM sp.resolution_log
WHERE provider = 'fl' AND provider_record_id = '{FL_EVENT_ID}'
ORDER BY decided_at DESC
LIMIT 1;
-- Expected: reason_code IN ('strict', 'alias', 'fuzzy'),
--           decided_at post-§7.

-- The fl_events row showing fixture_id repopulated:
SELECT fl_event_id, fixture_id, last_seen_at
FROM sp.fl_events WHERE fl_event_id = '{FL_EVENT_ID}';
-- Expected: fixture_id NOT NULL, matches snapshot original.
```

**That's the F8 dispositive moment.** The live production loop saw
the alias-add event, picked the record up, ran the matcher, flipped
it from no_match to resolved.

## §9 — Verify after = before

```sql
SELECT jsonb_build_object(
    'fl_event',   (SELECT to_jsonb(fle) FROM sp.fl_events fle
                   WHERE fle.fl_event_id = '{FL_EVENT_ID}'),
    'alias_row',  (SELECT to_jsonb(ta) FROM sp.team_aliases ta
                   WHERE ta.id = '{ALIAS_ID}'),
    'team_alias_count_after',
        (SELECT COUNT(*) FROM sp.team_aliases WHERE team_id = '{TEAM_ID}')
)::text;
```

Compare against the snapshot from §3. Expected drift:

| Field                              | Restored? |
|---|---|
| `fl_events.fixture_id`             | ✓ same UUID |
| `fl_events.last_seen_at`           | may be later (re-ingestion; OK) |
| `team_aliases.id`                  | ✓ same UUID |
| `team_aliases.team_id`             | ✓ same UUID |
| `team_aliases.alias`               | ✓ same |
| `team_aliases.alias_normalized`    | ✓ same |
| `team_aliases.source`              | ✓ same |
| `team_aliases.confidence`          | ✓ same |
| `team_aliases.created_at`          | **NEW** (post §7) — expected |
| `team_alias_count`                 | ✓ same |
| Latest `resolution_log`            | new row, resolved family — expected accretion |

Everything except `created_at` and log accretion should match. If
anything else drifted, investigate before considering the test
complete.

## Emergency restore (if any step fails)

If §5–§8 shows a failure that doesn't self-correct, restore the
snapshot immediately:

```sql
BEGIN;

INSERT INTO sp.team_aliases
  (id, team_id, alias, alias_normalized,
   source, confidence, created_at)
VALUES (
    '{ALIAS_ID}', '{TEAM_ID}',
    '{ALIAS_FORM}', '{ALIAS_NORMALIZED}',
    '{ALIAS_SOURCE}', {SNAPSHOT_CONFIDENCE},
    '{SNAPSHOT_CREATED_AT}'  -- restore original timestamp
)
ON CONFLICT (id) DO UPDATE
SET team_id          = EXCLUDED.team_id,
    alias            = EXCLUDED.alias,
    alias_normalized = EXCLUDED.alias_normalized,
    source           = EXCLUDED.source,
    confidence       = EXCLUDED.confidence,
    created_at       = EXCLUDED.created_at;

UPDATE sp.fl_events
SET fixture_id = '{SNAPSHOT_FIXTURE_ID}'
WHERE fl_event_id = '{FL_EVENT_ID}'
  AND fixture_id IS DISTINCT FROM '{SNAPSHOT_FIXTURE_ID}';

-- Verify:
SELECT
    (SELECT COUNT(*) FROM sp.team_aliases WHERE id = '{ALIAS_ID}')
        AS alias_row_present,
    (SELECT fixture_id FROM sp.fl_events
     WHERE fl_event_id = '{FL_EVENT_ID}') AS fl_fixture_id;
-- Expected: 1, '{SNAPSHOT_FIXTURE_ID}'.

COMMIT;
```

Log-side accretion (extra rows in `sp.resolution_log`) is
harmless — the loop re-resolves strict on the next pass.

---

## Appendix — Lessons learned (Attempt 1, Day-46)

### The canonical_name shadow failure

**Record picked**: `MRQznWTj` (Warwick Senators vs Geraldton
Buccaneers, FL, Basketball). Break side: away = Geraldton
Buccaneers. Alias count = 1, source `legacy_bootstrap`. §2b passed
on the pre-break decision.

**Break executed cleanly**: DELETE 1 alias row, UPDATE 1 `fl_events`
row to `fixture_id = NULL`. Pattern D confirmed `neondb`.

**Forced fresh decision surfaced the shadow**: the pass wrote TWO
decisions at the same timestamp:

- `strict@2a.6` → `no_match`, `fail_reason=alias_resolution_incomplete`,
  `away_resolved=false`. `reason_detail` carried NO team_id at all.
- `alias@2c.0` → `review_queue`, `away_team_id=f3cca7c9` **RESOLVED
  via canonical-name match**, `home_collision=true` with
  `colliding_home_team_ids=[92b83146 (Warwick Senators),
  5948f38d]`.

**Two independent disqualifiers** meant the loop could not catch
this record:

1. Latest decision was `review_queue`, not `no_match` → Tier-1
   filter (`reason_code = 'no_match'`) excludes it.
2. The `no_match` row's `reason_detail` had no team_id → even if
   the latest had been no_match, Tier-2 containment had nothing to
   match the re-added alias against.

**Root cause**: the alias tier and fuzzy tier build
`CandidateIndex` from `sp.teams.canonical_name`, not from
`sp.team_aliases` (Day-21 architectural finding — re-surfaced
here). Geraldton's canonical name (`"Geraldton Buccaneers"`)
matched the FL provider string exactly, so removing the alias row
from `sp.team_aliases` broke **only** the strict tier; the alias
tier immediately re-matched by canonical name.

**Fix applied**: §2c added above. Selection must confirm the FL
provider string differs from the canonical name (post-normalization)
so the alias delete produces a genuinely-unresolved record.

### Warwick Senators latent collision (side finding)

While walking the failed forced-decision output, a latent collision
surfaced: Warwick Senators (`92b83146`) has a
`colliding_home_team_ids` entry with `5948f38d`, only visible once
the strict tier stopped resolving the record. Masked in normal
operation. Not blocking F8 — noted as the shape of thing a
review-queue drain would systematically expose. Logged in
PROJECT_STATE Day-46.

### Runner has no record-targeting flag

`run_resolver_pass.py` accepts `--provider`, `--run-mode`,
`--limit` only. No `--record-id` or equivalent. `--limit 50` did
not reach `MRQznWTj` (43,878 unresolved FL records; ordered by
`last_seen_at DESC`, our record wasn't among the freshest 50).
`--limit 5000` succeeded.

For future F8 attempts, prefer:

- A recently-broken record whose `last_seen_at` will make it float
  toward the top of the ORDER BY. Since §4 sets `fixture_id = NULL`
  but doesn't change `last_seen_at`, this reduces to "the record's
  own `last_seen_at` must be recent enough to hit the `--limit`
  window."
- OR raise `--limit` progressively (`50 → 500 → 5000`) until the
  record's fresh no_match row appears (§5 verification query).

### Restore cleanliness

Post-restore verification matched snapshot byte-for-byte for the
`sp.fl_events` row and the `sp.team_aliases` row (all fields
including original `created_at`). The only residue is two
append-only `sp.resolution_log` rows from the forced pass — expected
accretion, harmless.

---

## Pointer

Full re-resolution loop scope: `docs/reresolution/scope-2026-06-17.md`.
This F8 procedure exercises the loop's headline mechanism (F1 + F1a
Tier-2 LOOSE containment) end-to-end. F7 Part B was settled Day-44
on alias-velocity evidence (0 alias adds / 7 days → passive flips ~0
by design); F8 is the complementary dispositive test that the
mechanism works when the condition it responds to is present.
