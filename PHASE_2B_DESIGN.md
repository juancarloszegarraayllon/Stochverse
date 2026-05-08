# Phase 2B Design — Strict-tier Resolver

Status: implemented. Phase 2B (PR #82) shipped the matcher; Phase 2A.6
(addendum below) shipped the competition gate before the first
parallel-run pass.

Reference: SP Architecture v1.4 §7 (Resolution Layer) and §13.2 (locked decisions).

---

## Phase 2A.6 addendum — Competition gate (added 2026-05-08)

Phase 2B's initial implementation was competition-blind: the matcher
read `signal.competition_hint` only into `reason_detail` for logging
and never resolved it. With `sp.fixtures.competition_id` becoming
nullable in migration `bdf12a30e49b`, the strict tier had silently
degraded into "competition-blind" rather than the design's intended
sport-only fallback. Caught before the first parallel-run pass.

**2A.6 ships, in order, before parallel-run starts:**

1. `scripts/bootstrap_sp_competitions.py` — seeds `sp.competitions`
   from distinct `(sport, series_base)` tuples in `sp.kalshi_markets`,
   keyed by `kalshi_identity.strip_known_suffix(series_ticker)`.
   Each row gets `kalshi_series_bases=[base]`. Idempotent on the
   union of all `kalshi_series_bases` arrays already seeded.
2. `resolver/competitions.py` — `CompetitionResolver` bulk-load +
   `(provider, hint) → (competition_id, kind)` lookup. Returns one
   of `'explicit'`, `'no_hint'`, `'unresolvable'`.
3. `resolver/matcher.py` — Phase 2A.6 competition gate (per provider):
   - **Kalshi `'explicit'`**: pass competition_id through to
     `find_fixture` (equal-or-NULL filter) and `ensure_fixture`
     (write on create).
   - **Kalshi `'no_hint'`**: sport-only fallback ALLOWED, logged as
     `kalshi_no_hint_sport_only: true`.
   - **Kalshi `'unresolvable'`**: strict tier FAILS (`fail_reason=
     'kalshi_competition_unresolvable'`). Bypassing this would
     silently link to the wrong fixture — re-run the bootstrap
     against fresh Kalshi data instead.
   - **FL**: transitional sport-only path. FL competitions can't be
     cleanly seeded until Phase 2C (raw_payload doesn't carry
     tournament-level sport_id), so every successful FL strict
     match stamps `fl_transitional_sport_only: true` for trivial
     day-7 audit + 2C reconciliation queries.
4. `resolver/kalshi.py` — `competition_hint` is now `series_ticker`
   (the canonical Kalshi-side identifier). `_soccer_comp` (human
   display) is preserved on `raw_signals['soccer_comp']` only.

`RESOLVER_VERSION` bumped from `strict@2b.0` → `strict@2a.6` so
historical decisions identify which gate produced them.

`find_fixture` filter is intentionally `(competition_id = filter
OR competition_id IS NULL)`: avoids forking one logical fixture
into two when FL (NULL comp) creates a fixture before Kalshi (with
explicit comp) arrives. `find_fixture` returns
`(fixture_id, fixture_competition_id)` so the matcher can audit
the filter outcome; the flags below make the Phase 2C reconciliation
trivially queryable.

**Audit flags on `resolution_log.reason_detail` (Phase 2A.6):**

- `linked_to_null_comp_fixture: true` + `null_comp_fixture_pending_backfill: <uuid>`
  — Kalshi explicit-comp signal linked to a NULL-comp fixture.
  Phase 2C backfill query: one line off `resolution_log`.
- `fl_transitional_path: matched_null_comp_fixture` (typical case)
- `fl_transitional_path: matched_existing_comp_fixture` (Kalshi
  created the fixture earlier with explicit comp; FL is now joining
  sport-only — comp asymmetry to verify in 2C)
- `fl_transitional_path: created_null_comp_fixture` (FL was first;
  new row created with NULL comp, awaits 2C)

---

## Scope

Phase 2B implements **only the strict tier** of the central matcher:
exact alias on both teams + kickoff drift ≤ 30 minutes + competition
match. Auto-applies at confidence 0.98 when all conditions hold.
Records that don't satisfy strict-tier conditions stay with
`fixture_id IS NULL` until the alias tier (2C) or fuzzy tier (2D)
ships.

**Out of scope for 2B:**
- Alias / fuzzy / corroboration tiers (2C–2D)
- Three-loop runner with `LISTEN/NOTIFY` (2E)
- Admin review-queue UI (2F)
- Resolver diff tooling (2G)

The strict tier is intentionally narrow — high precision, lower
recall. Coverage rises as later tiers ship.

---

## Three sharpenings — confirmations

### 1. Atomic UPSERT pattern — confirmed

2B writes the provider's `fixture_id` link AND the `sp.resolution_log`
row in **one transaction**:

```python
async with session.begin():
    # Step 1: ensure the canonical fixture exists. UPSERT keyed on
    # (home_team_id, away_team_id, date(kickoff_at)) — see §5.4 of
    # the architecture doc. Returns sp.fixtures.id.
    fixture_id = await ensure_fixture(
        session, home_team_id, away_team_id, kickoff_at, competition_id,
    )

    # Step 2: link the provider record. UPDATE on the relevant
    # provider table, keyed by the provider's primary identifier.
    await session.execute(
        update(sp_kalshi_markets_table)
        .where(sp_kalshi_markets_table.c.ticker == provider_record_id)
        .values(fixture_id=fixture_id)
    )

    # Step 3: append to resolution_log. Same transaction — if any
    # of the three steps fails, all three roll back together.
    session.add(ResolutionLog(
        run_id=run_id,
        provider="kalshi",
        provider_record_id=provider_record_id,
        fixture_id=fixture_id,
        confidence=0.98,
        reason_code="strict",
        reason_detail={...},
        resolver_version=RESOLVER_VERSION,
    ))
    # Implicit commit on __aexit__.
```

This makes 2E's polling query self-clearing. The query

```sql
SELECT ticker FROM sp.kalshi_markets
 WHERE fixture_id IS NULL
   AND last_seen_at > $last_run_ts
```

never re-selects a row that the strict tier just resolved, because
the UPDATE in step 2 lands in the same commit as the resolver_log row
in step 3. No race window where a row is "linked but not logged" or
"logged but not linked."

#### `ensure_fixture` semantics — pinned to "DO NOTHING + re-fetch"

The resolver MUST NOT modify fixture metadata (scores, state, venue,
score_source, score_as_of, neutral_ground, behind_closed_doors,
stage, tie_id) on conflict. Those fields are owned by score-aware
ingestion paths or by future state-update paths; the resolver's only
job is "ensure a fixture row exists for this team-pair + kickoff."

Implementation:

```python
async def ensure_fixture(
    session,
    home_team_id, away_team_id,
    kickoff_at, competition_id,
) -> UUID:
    """Ensure a sp.fixtures row exists for this match. Return its id.

    Strict semantics:
      - INSERT a new fixture if no existing row matches the resolver
        lookup key (home_team_id, away_team_id, date(kickoff_at)).
      - DO NOTHING on conflict — never overwrite metadata. Score,
        state, venue, etc. are owned by ingestion paths or other
        update flows, not the resolver.
      - Returns the fixture_id (existing row's id on conflict; new
        row's id on insert).

    Two-step pattern: INSERT ... ON CONFLICT DO NOTHING RETURNING id,
    then if RETURNING is empty (conflict path), SELECT to fetch the
    existing row's id by the same lookup key.
    """
    stmt = pg_insert(Fixture.__table__).values(
        id=uuid.uuid4(),
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        competition_id=competition_id,
        kickoff_at=kickoff_at,
        # NOTE: state/score/venue/etc. NOT set here — DEFAULTs apply
        # only on INSERT. On conflict path we never touch them.
    ).on_conflict_do_nothing(
        index_elements=["home_team_id", "away_team_id"],
        # ... + a date(kickoff_at) constraint; see §5.4 index
    ).returning(Fixture.__table__.c.id)

    row = (await session.execute(stmt)).first()
    if row is not None:
        return row.id

    # Conflict path: re-fetch existing row by the lookup key.
    sel = select(Fixture.__table__.c.id).where(
        Fixture.__table__.c.home_team_id == home_team_id,
        Fixture.__table__.c.away_team_id == away_team_id,
        # date(kickoff_at) match — drift logic was already applied
        # by find_fixture before we got here, so this exact-match
        # lookup is correct.
    )
    return (await session.execute(sel)).scalar_one()
```

Audit-friendly: `resolution_log.reason_detail` records whether the
resolver's atomic transaction created a new fixture (insert path) or
linked to an existing one (conflict path). Lets a post-2B audit count
"fixtures created by the resolver" vs "fixtures created elsewhere."

### 2. Parallel-run success / failure criteria — locked numbers

7-day observation window after 2B ships.

**Per-provider FP comparator differs.** Kalshi has the legacy
`kalshi_join.pair_via_registry` as an automatic comparator. FL was
new in Phase 1B — there's no legacy auto-comparator. So we use two
separate techniques: automated diff for Kalshi, human spot-check for
FL.

#### Kalshi metrics (automated, daily diff vs legacy)

| Metric | Computed as | Threshold UP | Threshold DOWN / escalate |
|---|---|---|---|
| **Kalshi false-positive rate** | (resolver auto-applied where legacy_kalshi_join paired differently) / (total resolver auto-applies on Kalshi) | **> 1.0%** for any 24h window → tighten kickoff drift to 15 min, re-evaluate | — |
| **Kalshi strict-tier coverage** | (Kalshi strict-tier auto-applies) / (total Kalshi per_fixture records) | — | **< 60%** sustained → review extraction; possibly relax competition-match or bootstrap more aliases |

#### FL metrics (operator spot-check, daily)

| Metric | Method | Threshold |
|---|---|---|
| **FL spot-check error rate** | Operator picks 5 random FL strict-tier auto-applies per day from `sp.resolution_log WHERE provider='fl' AND reason_code='strict'`, manually verifies the chosen fixture matches the underlying real-world game (cross-reference with FL's web UI or another source). | **≥ 1 of 5** wrong on any day → halt FL parallel-run, investigate. **0 of 5 wrong** for 5 consecutive days → FL precision is acceptable; resume normal cadence. |
| **FL strict-tier coverage** | Same as Kalshi — auto-applies / (total FL events ingested per pass). | **< 60%** sustained → review extraction. |

The FL spot-check is documented as an operator runbook step, not
a metric the resolver itself computes. ~5 minutes per day. Cross-
provider corroboration (Phase 2D) will replace it with an automated
signal once FL events can be matched against Kalshi events for the
same fixture.

#### Cross-provider metrics (apply to both)

| Metric | Computed as | Threshold |
|---|---|---|
| **Provider→link latency p95** | resolution_log.decided_at - provider table's last_changed_at | **> 5 min** → switch from polling-loop to LISTEN/NOTIFY (2E.fix) |
| **Resolver crash rate** | supervised task crash count / day | **> 5/day** → halt parallel-run; investigate before re-enabling |
| **Review-queue load** | review_queue_inserts / day | n/a in 2B (strict-only never routes to review); relevant from 2C onward |

These numbers go into 2B's PR description verbatim. At day 7 the
Kalshi comparator + FL spot-check are read off and we either lock
the configuration or adjust per the tables.

### 3. Admin UI auth pattern — locked

Per architecture v1.2 §13.2 (locked decision):

> Roll-your-own — single password + session cookie. Internal tool with
> one admin user. Auth0/Clerk solve a problem we don't have. Revisit
> when a second admin user is a concrete need.

Phase 2F implements:
- `ADMIN_PASSWORD_HASH` env var (bcrypt-hashed)
- `SESSION_SECRET` env var (random 32 bytes)
- `/admin/login` POST → verify against hash → set HttpOnly Secure
  session cookie (`SameSite=Lax`, signed with `SESSION_SECRET`)
- `/admin/logout` → clear cookie
- Middleware on `/admin/*` → verify cookie, redirect to login on miss

~30 lines of FastAPI middleware + a Jinja2 login template.
**Confirmed: this is the auth pattern; not a new design question
in 2F.**

---

## Question A — Strict tier match logic

### Answer (definitive)

Strict tier requires **all four** to hold simultaneously:

1. **Both teams resolved via exact alias hit.** Each team candidate
   from the FixtureSignal is normalized (per `resolver._normalize`)
   and looked up against `sp.team_aliases.alias_normalized`. The
   highest-weight candidate that resolves to a `team_id` wins per
   side. Both sides must resolve.
2. **Kickoff drift ≤ 30 minutes** between the FixtureSignal's
   `kickoff_at` and the candidate fixture's `kickoff_at`.
   Hard-coded for strict tier — does not use the per-sport
   `auto_link_drift_minutes` (that's an alias-tier concept in 2C).
3. **Competition match.** Either:
   - The signal's `competition_hint` maps to the candidate fixture's
     `competition_id` via the `sp.competitions.kalshi_series_bases` /
     `fl_tournament_stage_ids` JSONB array, OR
   - Both signals point to the same `sport_id` AND no competition
     hint exists on either side (sport-only match — rare).
4. **Both kickoff_confidences ≥ 0.85.** A signal with
   `kickoff_confidence = 0.6` (date-only ticker fallback) is too
   loose for strict tier. Demoted to alias-tier territory in 2C.

If any condition fails, strict tier passes — `fixture_id` stays
`NULL` on the provider record. No `resolution_log` row is written
(strict-tier passes are silent; 2C+ logs `no_match` for records
they also can't resolve).

### How are teams resolved? Pre-seeded, NOT created.

Strict tier **never creates teams**. If a candidate fails to
resolve via the alias table, the entire match attempt fails (passes
to 2C+). Rationale: strict tier is supposed to be the highest-
precision tier. Creating a team mid-match is a soft signal — it
belongs in alias-tier (2C) or in the human-approved review queue (2F).

**Teams come from the bootstrap migration (Question B below)**, plus
later additions via approved review-queue entries (2F) or alias-tier
auto-creation (2C).

### Strict-tier kickoff drift: 30 minutes (architecture default)

Architecture §7.3 specifies "kickoff within 30 minutes" for strict
tier. Sticking with that:
- Tighter (5–15 min): false-positive risk extremely low, but
  excludes legitimate matches where Kalshi's `_kickoff_dt` differs
  from FL's `START_TIME` by minutes (observed: 10–20 min variance
  is common for the same fixture).
- Looser (60+ min): no longer "strict"; bleeds into alias-tier
  territory.

30 min is the published default. Re-evaluate post parallel-run if
false-positive rate > 1.0% (per criteria above).

### Strict-tier confidence: 0.98

Architecture §7.4 specifies confidence ≥ 0.95 for strict. Setting
**0.98** specifically: leaves headroom for 1.0 to mean "human-
verified" (set by review-queue approvals in 2F). Strict-tier
auto-applies are high-confidence but not human-verified, so 0.98
captures that subtle distinction.

---

## Question B — Bootstrap from legacy `public.entities` / `public.entity_aliases`

### Answer (definitive): Yes, bootstrap. One-time migration step.

Reasoning:
- `public.entities` + `public.entity_aliases` have months of
  curated alias data accumulated through legacy entity discovery.
  Throwing it away to "start clean" means 0% strict-tier coverage
  on day 1, ramping slowly only as 2C+ alias-tier or 2F manual
  approvals fill the gap.
- Bootstrapping inherits any data quality issues from legacy. Mitigation:
  every bootstrapped alias gets `source='legacy_bootstrap'` and
  `confidence=0.95`. The 0.05 gap from 1.0 (vs. `source='human_curated'`
  which is 1.0) gives us a soft signal to revisit if a bootstrapped
  alias starts producing false positives.

### Bootstrap mechanics

A new script `scripts/bootstrap_sp_teams.py`:

1. **Seed `sp.sports`** with a small finite set: Soccer, Tennis,
   Basketball, Hockey, American Football, Baseball, Handball,
   Cricket, Volleyball, Rugby Union, Aussie Rules, Rugby League,
   MMA, Boxing, Golf, Snooker, Darts. Each row gets the per-sport
   `auto_link_drift_minutes` from architecture §5.4.

2. **Migrate teams from `public.entities`:**
   ```sql
   INSERT INTO sp.teams (id, sport_id, canonical_name, normalized_name, country_code)
   SELECT
     gen_random_uuid(),
     (SELECT id FROM sp.sports WHERE name = e.sport),
     e.canonical_name,
     <normalize(e.canonical_name)>,
     NULL  -- public.entities doesn't carry country_code
   FROM public.entities e
   WHERE e.entity_type = 'team' AND e.sport IS NOT NULL;
   ```
   Skip rows where `e.sport` doesn't map to a known `sp.sports.name` —
   log them, don't fail.

3. **Migrate aliases from `public.entity_aliases`:**
   - For each alias row, look up the corresponding `sp.teams.id` via
     a temporary `legacy_entity_id → sp_team_id` mapping table built
     in step 2.
   - Insert into `sp.team_aliases` with:
     - `alias_normalized` = re-normalize via `resolver._normalize.normalize_name`
       (the legacy `normalized` column was generated by a different
       routine; we re-do it for consistency)
     - `source = 'legacy_bootstrap'`
     - `confidence = 0.95`
   - Skip rows whose `entity_id` doesn't have a corresponding
     `sp.teams` row (e.g., entity is a player, league, or asset).
     Log the count.

4. **Idempotent re-run:** the script uses `ON CONFLICT (alias_normalized,
   source) DO NOTHING` so running it twice produces no duplicates.

Estimated duration: 30 seconds against production Neon (legacy
tables have ~tens of thousands of entities, not millions).

The script ships in the same PR as 2B, but as a **separate operator
action** — `make bootstrap-sp-teams` or
`python scripts/bootstrap_sp_teams.py`. Not invoked automatically
on deploy.

### Why not on-the-fly creation in strict tier

- Strict tier should fail loudly when teams are unknown, not
  silently create. Failed strict-tier matches fall through to
  alias-tier in 2C, which is the right place for soft creation.
- Creating teams in a high-traffic write path adds race-condition
  surface (two concurrent strict matches creating the same team).
  The bootstrap script runs once, single-threaded, in a controlled
  context.
- Auditing "where did this team come from?" is much easier with a
  clean source tag from a one-time bootstrap than with mid-flight
  creation entries scattered through `resolution_log`.

---

## Implementation sketch (for review, not yet code)

### File layout

```
resolver/
  __init__.py           — existing (Phase 2A)
  types.py              — existing
  protocol.py           — existing
  _normalize.py         — existing
  fl.py                 — existing (extract_signal)
  kalshi.py             — existing (extract_signal)
  matcher.py            — NEW (2B): central matcher, strict tier only
  fixtures.py           — NEW (2B): ensure_fixture UPSERT helper
  aliases.py            — NEW (2B): alias-table query helper
  competitions.py       — NEW (2B): competition-hint resolution

scripts/
  bootstrap_sp_teams.py — NEW (2B): one-time legacy → sp.* migration

migrations/versions/
  <timestamp>_<rev>_seed_sports_table.py  — NEW (2B): seed sp.sports
                                             with the finite list

tests/
  test_resolver_2b.py   — NEW (2B): matcher unit tests + fixture
                           UPSERT tests + bootstrap script tests
```

### `resolver/matcher.py` shape

```python
class StrictMatcher:
    """Strict tier only — exact alias both sides + drift ≤ 30 min +
    competition match + kickoff_confidence ≥ 0.85.

    Returns MatchResult with reason_code='strict' on hit.
    Returns MatchResult with reason_code='no_match' on miss.
    No team creation, no review queue routing — those are 2C+/2F."""

    KICKOFF_DRIFT_SEC = 30 * 60
    MIN_KICKOFF_CONFIDENCE = 0.85
    AUTO_APPLY_CONFIDENCE = 0.98

    async def match(
        self,
        session: AsyncSession,
        signal: FixtureSignal,
    ) -> MatchResult:
        # Step 1: kickoff_confidence gate
        if signal.kickoff_confidence < self.MIN_KICKOFF_CONFIDENCE:
            return MatchResult(reason_code=ReasonCode.NO_MATCH, ...)

        # Step 2: resolve both teams via aliases
        home_team_id = await resolve_team(session, signal.home_team_candidates)
        away_team_id = await resolve_team(session, signal.away_team_candidates)
        if home_team_id is None or away_team_id is None:
            return MatchResult(reason_code=ReasonCode.NO_MATCH, ...)

        # Step 3: resolve competition
        competition_id = await resolve_competition(session, signal.competition_hint, signal.sport)
        if competition_id is None:
            return MatchResult(reason_code=ReasonCode.NO_MATCH, ...)

        # Step 4: find candidate fixture by (home, away, kickoff ±30min, competition)
        fixture_id = await find_fixture(
            session, home_team_id, away_team_id,
            signal.kickoff_at, self.KICKOFF_DRIFT_SEC,
            competition_id,
        )

        if fixture_id is None:
            # No existing fixture — create one (this is the only place
            # 2B creates fixtures, never teams)
            fixture_id = await ensure_fixture(
                session, home_team_id, away_team_id,
                signal.kickoff_at, competition_id,
            )

        return MatchResult(
            fixture_id=fixture_id,
            confidence=self.AUTO_APPLY_CONFIDENCE,
            reason_code=ReasonCode.STRICT,
            reason_detail={
                "home_team_id":      str(home_team_id),
                "away_team_id":      str(away_team_id),
                "competition_id":    str(competition_id),
                "kickoff_drift_sec": ...,
                "alias_kinds":       {"home": "...", "away": "..."},
            },
            resolver_version=RESOLVER_VERSION,
        )
```

### Integration with 2A's extraction

The 2B matcher consumes 2A's `FixtureSignal` directly. No changes to
`resolver/fl.py` or `resolver/kalshi.py`. The matcher is a separate
module that the (eventual 2E) runner glues together with extraction
and DB writes.

### Where 2B is wired

For 2B's parallel-run period, the matcher runs from a **standalone
script** (`scripts/run_resolver_pass.py`) that an operator invokes
manually or via cron. Not yet wired into the live web service —
that's 2E's job.

```bash
# One-shot: resolve all currently-unresolved provider records
DATABASE_URL=... python scripts/run_resolver_pass.py --provider kalshi
DATABASE_URL=... python scripts/run_resolver_pass.py --provider fl
```

Each invocation:
1. Fetches `fixture_id IS NULL` rows from the provider table
2. Runs `extract_signal` per row (Phase 2A pure function)
3. Runs `StrictMatcher.match` per signal (Phase 2B new)
4. On hit: atomic transaction writes `fixture_id` + `resolution_log`
5. On miss: skips silently (2C+ will pick up next time)

This is a contained surface for parallel-run. After 2D ships, 2E
folds the matcher into the live runner.

---

## Test plan

### Unit tests (`tests/test_resolver_2b.py`, no DB)

- `StrictMatcher.match` with mocked session returning various
  alias / fixture states
- Kickoff confidence gate (< 0.85 returns no_match)
- Single-team-resolved vs both-resolved branching
- Competition resolution: explicit hint, sport-only fallback, miss
- Drift window enforcement (29:59 OK, 30:01 not)

### Integration tests (`tests/test_resolver_2b_integration.py`, against docker-compose Postgres)

- Bootstrap script populates `sp.teams` + `sp.team_aliases` from
  fixtures of legacy data shape
- `ensure_fixture` is idempotent (re-running with same args returns
  same UUID, no duplicate row)
- Atomic transaction: simulating a crash mid-match produces zero
  partial state (no orphaned `fixture_id` linkage without `resolution_log`)
- 7-day-of-data smoke: feed ~7000 Kalshi records + ~19k FL records
  through the matcher; verify strict-tier coverage ≥ 60% (alarm
  threshold)

### Replay tests (architecture §12.2)

- Save 24h of `sp.kalshi_markets.raw_payload` snapshots
- Run `StrictMatcher` against each
- Compare to legacy `kalshi_join` pairings
- Assert: false-positive rate < 1.0% on the saved corpus

---

## Implementation order — locked

**Two PRs, sequential.** 2A.5 ships first; 2B follows after 2A.5 is
verified in production.

### Phase 2A.5 — Bootstrap (ships first)

Standalone PR. Verifiable in isolation.

**Contents:**
- Migration: seed `sp.sports` with the finite list (Soccer, Tennis,
  Basketball, Hockey, American Football, Baseball, Handball,
  Cricket, Volleyball, Rugby Union, Aussie Rules, Rugby League,
  MMA, Boxing, Golf, Snooker, Darts), each with the per-sport
  `auto_link_drift_minutes` from architecture §5.4.
- `scripts/bootstrap_sp_teams.py` — one-time migration from
  `public.entities` (entity_type='team') and `public.entity_aliases`
  into `sp.teams` and `sp.team_aliases` with `source='legacy_bootstrap'`,
  `confidence=0.95`. Idempotent. Logs counts of inserted/skipped per
  table.
- Tests against docker-compose Postgres with fixture data shaped
  like the legacy tables.
- DEPLOYMENT.md update: how to run the bootstrap, how to verify counts.
- Makefile target: `make bootstrap-sp-teams`.

**Operator action after merge:**
1. `alembic upgrade head` → applies the sp.sports seed migration.
2. `DATABASE_URL=<prod-Neon> python scripts/bootstrap_sp_teams.py`
   → runs once. Outputs per-sport team counts and per-source alias
   counts.
3. Verify in psql:
   ```sql
   SELECT name, count(*) FILTER (WHERE t.id IS NOT NULL) AS teams,
          count(a.id) AS aliases
   FROM sp.sports s
   LEFT JOIN sp.teams t ON t.sport_id = s.id
   LEFT JOIN sp.team_aliases a ON a.team_id = t.id
   GROUP BY 1 ORDER BY 1;
   ```
4. **Document the baseline counts in PROJECT_STATE.md** — those
   become the "expected coverage" benchmark when 2B's parallel-run
   begins.

**Merge gate for 2A.5:** team counts and alias counts are non-zero
and reasonable for each sport with active data. If any sport has
zero teams (e.g., Snooker — we may not have legacy data for it),
that sport will have 0% strict-tier coverage in 2B; document it
and decide whether to seed manually or wait for the alias tier (2C).

### Phase 2B — Matcher (ships after 2A.5 baseline is verified)

Standalone PR. Implementation follows the design above:

**Contents:**
- `resolver/matcher.py` — `StrictMatcher` class
- `resolver/fixtures.py` — `ensure_fixture` + `find_fixture` helpers
- `resolver/aliases.py` — alias-table query helper
- `resolver/competitions.py` — competition-hint resolver
- `scripts/run_resolver_pass.py` — operator-invoked or cron-scheduled
  one-shot pass over `fixture_id IS NULL` records
- New `sp.resolver_runs` table (see Open question 3 below) for
  storing daily diff metrics
- Tests: unit (mocked sessions), integration (docker-compose), replay
  (24h corpus diff vs legacy)
- DEPLOYMENT.md update: how to run a parallel-run pass, how to
  query the metrics

### Why this order

- Bootstrap is verifiable in isolation. Spot-check
  `sp.teams`/`sp.team_aliases` row counts before any matcher code runs.
- Establishes baseline alias coverage as a number before strict-tier
  turns on. If coverage is unexpectedly low, we catch it before the
  parallel-run goes uninformative.
- Bootstrap isn't reversible the way code is — easier to debug in a
  contained PR.

The "dead code without 2B" objection isn't strong: bootstrap is a
manual script, sits unused until invoked. Shipping it first costs
nothing.

## Open questions resolved

1. ~~Bootstrap as part of 2B PR, or separate?~~ **Separate. 2A.5
   ships first.** (Per Pushback 3.)
2. ~~Should bootstrap include `public.markets`?~~ **Defer to 2C.**
   Strict tier doesn't need sub-market identity; the match is
   event-level. 2C's alias tier may want sub-market data for
   matching sub-market candidates; bootstrap that table at that point.
3. ~~Cron / schedule for `run_resolver_pass.py`?~~ **Ad-hoc operator
   + daily cron, with metrics persisted to a queryable table.**

   Specifically: `run_resolver_pass.py` runs daily at 02:00 UTC via
   a one-shot script (operator-scheduled — see DEPLOYMENT.md).
   Each pass produces a row in a new `sp.resolver_runs` table:

   ```sql
   CREATE TABLE sp.resolver_runs (
       id                  bigserial PRIMARY KEY,
       run_id              uuid NOT NULL,
       resolver_version    text NOT NULL,
       provider            text NOT NULL,         -- 'fl' | 'kalshi'
       run_mode            text NOT NULL,         -- 'standalone' | 'cron' | 'live'
       started_at          timestamptz NOT NULL,
       finished_at         timestamptz,
       records_scanned     integer NOT NULL DEFAULT 0,
       auto_applies        integer NOT NULL DEFAULT 0,
       no_match            integer NOT NULL DEFAULT 0,
       crashes             integer NOT NULL DEFAULT 0,
       legacy_diff_count   integer,               -- Kalshi only; NULL for FL
       legacy_diff_details jsonb,                 -- which provider records differed
       latency_p95_ms      integer,
       extra               jsonb DEFAULT '{}'::jsonb
   );
   CREATE INDEX ON sp.resolver_runs (provider, started_at DESC);
   CREATE INDEX ON sp.resolver_runs (run_mode, started_at DESC);
   ```

   `run_mode` values:
   - `'standalone'` — operator-invoked one-shot via
     `python scripts/run_resolver_pass.py`. Used during development
     and ad-hoc backfill.
   - `'cron'` — scheduled invocation of the same script (daily at
     02:00 UTC during the parallel-run period). Indistinguishable
     from 'standalone' code-path-wise; the env or arg sets the tag
     so day-7 reports can filter to just the cron-driven series.
   - `'live'` — emitted by the Phase 2E runner once it folds the
     matcher into the always-on ingestion lifecycle. Distinct mode
     so post-2E activity doesn't conflate with parallel-run metrics.

   At day 7, the parallel-run report is one query, **filtered to
   parallel-run modes only**:

   ```sql
   SELECT
     provider,
     SUM(records_scanned)                                    AS scanned,
     SUM(auto_applies)                                       AS auto_applies,
     SUM(legacy_diff_count)                                  AS diffs,
     ROUND(100.0 * SUM(legacy_diff_count) / NULLIF(SUM(auto_applies), 0), 2) AS fp_pct,
     ROUND(100.0 * SUM(auto_applies) / NULLIF(SUM(records_scanned), 0), 2)   AS coverage_pct
   FROM sp.resolver_runs
   WHERE started_at > NOW() - INTERVAL '7 days'
     AND run_mode IN ('standalone', 'cron')   -- exclude 'live' (post-2E)
   GROUP BY 1
   ORDER BY 1;
   ```

   No grepping Railway logs. Trends are SQL-queryable. Phase 2E's
   live runner extends this same table with per-loop entries.

   The `sp.resolver_runs` table goes in **2B's migration** (since it's
   the resolver itself producing the rows), not 2A.5's.

---

## Sign-off checklist (revised)

Reviewer (you): all confirmed in your endorsement. Recording for the
record:

- [x] Atomic transaction pattern in §1, **with `ensure_fixture` pinned
      to DO-NOTHING-plus-re-fetch semantics** (Pushback 1 addressed).
- [x] Parallel-run criteria in §2, **scoped per-provider**: Kalshi
      uses automated diff vs legacy; FL uses operator spot-check
      (5 random/day) until 2D's cross-provider corroboration replaces
      it (Pushback 2 addressed).
- [x] Auth pattern for 2F locked per §13.2.
- [x] Question A — strict tier conditions, no team creation, 30 min
      drift, 0.98 confidence.
- [x] Question B — bootstrap from `public.entities` /
      `public.entity_aliases` with `source='legacy_bootstrap'` /
      `confidence=0.95`.
- [x] **Implementation order: 2A.5 (bootstrap) ships first, then 2B
      (matcher) after baseline is verified in production**
      (Pushback 3 addressed).
- [x] Test plan — accepted.
- [x] `public.markets` bootstrap deferred to 2C.
- [x] `run_resolver_pass.py` daily-cron with metrics persisted to
      new `sp.resolver_runs` table (queryable; no Railway log
      grep at day 7).

Implementation order:
1. **2A.5 PR** — bootstrap script + sp.sports seed migration + tests
   + DEPLOYMENT.md update. Ship, run in production, verify alias
   counts, document baseline in PROJECT_STATE.md.
2. **2B PR** — matcher + standalone runner + sp.resolver_runs
   migration + parallel-run metrics query + tests. Ships after
   2A.5 baseline is verified.
