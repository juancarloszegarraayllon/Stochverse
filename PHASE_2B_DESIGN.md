# Phase 2B Design — Strict-tier Resolver

Status: design doc, awaiting review. Implementation begins only after sign-off.

Reference: SP Architecture v1.4 §7 (Resolution Layer) and §13.2 (locked decisions).

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

### 2. Parallel-run success / failure criteria — locked numbers

7-day observation window after 2B ships. The strict tier runs side-
by-side with the legacy `kalshi_join.pair_via_registry`. Each day
emits a diff report with these metrics:

| Metric | Computed as | Threshold to push threshold UP (auto-apply more conservative) | Threshold to push DOWN / expand staffing |
|---|---|---|---|
| **False-positive rate** | (resolver auto-applied a fixture_id where legacy_kalshi_join paired to a different fixture) / (total resolver auto-applies) | **> 1.0%** for any 24h window → tighten kickoff drift to 15 min, re-evaluate | — |
| **Review-queue load** | review_queue_inserts / day (will be 0 in 2B since strict-only doesn't route to review) | — | — (n/a in 2B; relevant from 2C onward) |
| **Strict-tier coverage** | (strict-tier auto-applies) / (total provider records ingested per pass) | — | **< 60%** sustained → review extraction; possibly bootstrap more aliases or relax competition-match requirement |
| **Latency: provider record → fixture_id link** | last_changed_at - resolution_log.decided_at | — | **p95 > 5 min** → switch from polling-loop to LISTEN/NOTIFY (2E.fix) |
| **Resolver crash rate** | supervised task crash count / day | — | **> 5/day** → halt parallel-run; investigate before re-enabling |

These numbers go into 2B's PR description verbatim. At day 7 we
read them off the dashboard and either lock the configuration or
adjust per the table.

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

## Open questions for you to confirm

1. **Bootstrap as part of 2B PR, or separate 2A.5 PR shipped first?**
   I lean: same PR. Bootstrap is dead code unless 2B is also live.
   But if you want to run bootstrap in production and verify the
   alias counts before 2B's matcher activates, separate PRs is the
   safer order.
2. **Should bootstrap include `public.markets`?** That table has
   sub-market identity — `(event_id, ticker, label, entity_id)`.
   For strict-tier matching we don't need it (the match is
   event-level, not sub-market). But we may want it for Phase 2C
   alias tier when matching sub-market candidates. Recommend:
   defer to 2C; bootstrap only `public.entities` + `public.entity_aliases`
   in 2B.
3. **Cron / schedule for `run_resolver_pass.py` during parallel-run?**
   I recommend: ad-hoc operator invocation initially, plus a daily
   cron at 02:00 UTC that runs the diff against legacy and emits
   the day's metrics. After 7 days, shift to 2E's live runner.

---

## Sign-off checklist

Reviewer (you): tick when answered:

- [ ] Atomic transaction pattern in §1 looks right.
- [ ] Parallel-run criteria in §2 are the right thresholds.
- [ ] Auth pattern for 2F locked per §3.
- [ ] Question A answer — strict tier conditions, no team creation, 30 min drift, 0.98 confidence — accepted.
- [ ] Question B answer — bootstrap from `public.entities` / `public.entity_aliases` with `source='legacy_bootstrap'` / `confidence=0.95` — accepted.
- [ ] Implementation sketch (file layout, matcher shape, standalone-script wiring) — accepted.
- [ ] Test plan — accepted.
- [ ] Open question 1 — bootstrap as same PR vs separate.
- [ ] Open question 2 — bootstrap `public.markets` deferred to 2C.
- [ ] Open question 3 — cron pattern during parallel-run.

Once all ticks: implementation begins as a single PR matching this
design.
