"""FL → IdentityRegistry seeder — Phase B.

Walks a FlashLive events-list response and populates the canonical
registry with teams, competitions, and fixtures. FL is canonical for
fixture metadata per the source precedence policy
(SPORTS_V2_PLAN.md Phase 5+ section).

Phase B scope (this module):
    1. seed_team_from_fl_event       — register both home and away
    2. seed_competition_from_fl      — register competition from
                                       FL tournament metadata
    3. seed_fixture_from_fl_event    — register fixture, link teams
                                       and competition
    4. seed_from_fl_response         — top-level: walk FL "DATA"
                                       array (tournaments → events)
                                       and populate everything

Phase B explicitly does NOT:
    * Touch v2 / v1 production code paths.
    * Read live FL data — that's the caller's job. Tests pass
      synthetic FL fixture dicts; production callers will plug
      this seeder into the existing `flashlive_feed._fl_get`
      pipeline in a later phase.
    * Reconcile across sports — each call is per-sport.

Slug strategy:
    Team slug   = slugify(HOME_NAME)  — long-form canonical name.
                  SHORTNAME_HOME stored as an alias on the team.
                  Rationale: keeping HOME_NAME-derived slugs means
                  the canonical ID survives FL abbr-convention changes
                  (LAK→LAL, etc.); only the alias set updates.
    Comp slug   = slugify(NAME) — full FL tournament name. Stable
                  across the season.
    Fixture id  = composed from team slugs + date (registry-built).

FL natural identifiers registered as aliases:
    source='fl', external_id=fl_event['EVENT_ID']
                 → fixture canonical id
    source='fl', external_id=fl_tournament['TOURNAMENT_STAGE_ID']
                 → competition canonical id

Future source mappers (Kalshi, Polymarket, OddsAPI) will register
their own aliases against the same canonical entities — this is
how the registry collapses N×N pairwise matching into N translators
each pointing at one canonical entity layer.

Idempotency: every helper is idempotent. Calling
seed_from_fl_response twice with the same data leaves the registry
unchanged after the second call (counts identical, no duplicates).
Updates to FL fields (rescheduled kickoff, etc.) bump fixture
version per IdentityRegistry.register_fixture's contract.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional

from identity_registry import (
    IdentityRegistry,
    Team, Competition, Fixture,
    slugify,
)
from competition_timezones import competition_tz, compute_local_date


# ── Team seeding ─────────────────────────────────────────────────

def seed_team_from_fl_event(registry: IdentityRegistry,
                              fl_event: dict, sport: str,
                              side: str) -> Optional[Team]:
    """Register one team (home or away) from a FL event dict.

    `side` ∈ {'home', 'away'}.

    Returns the registered Team, or None if FL didn't ship enough
    data to identify the team (missing both long name and shortname).
    Idempotent — repeated calls with the same input merge any new
    aliases into the existing Team and return it.
    """
    if side == "home":
        long_name = (fl_event.get("HOME_NAME") or "").strip()
        short = (fl_event.get("SHORTNAME_HOME") or "").strip().upper()
    elif side == "away":
        long_name = (fl_event.get("AWAY_NAME") or "").strip()
        short = (fl_event.get("SHORTNAME_AWAY") or "").strip().upper()
    else:
        raise ValueError(f"side must be 'home' or 'away', got {side!r}")

    # Need at least one of long_name / short to seed.
    if not long_name and not short:
        return None

    canonical_name = long_name or short
    slug = slugify(long_name) or slugify(short)
    if not slug:
        return None

    aliases = set()
    if short:
        aliases.add(short)
    return registry.register_team(
        sport=sport, canonical_name=canonical_name,
        slug=slug, aliases=aliases,
    )


# ── Competition seeding ──────────────────────────────────────────

def seed_competition_from_fl(registry: IdentityRegistry,
                              fl_tournament: dict, sport: str
                              ) -> Optional[Competition]:
    """Register a competition from a FL tournament dict.

    Reads NAME / NAME_PART_2 / TOURNAMENT_STAGE_ID. Slug is derived
    from the most specific name available (NAME, falling back to
    NAME_PART_2). Stage ID registered as an alias under source='fl'
    so subsequent FL data flows resolve through the registry.

    Returns the Competition, or None if neither name nor stage_id
    is present.
    """
    name = (fl_tournament.get("NAME") or "").strip()
    name2 = (fl_tournament.get("NAME_PART_2") or "").strip()
    stage_id = (fl_tournament.get("TOURNAMENT_STAGE_ID") or "").strip()

    canonical_name = name or name2
    slug = slugify(canonical_name)
    if not slug:
        # Last-resort: use stage_id as the slug source. Better than
        # dropping the entry entirely.
        slug = slugify(stage_id)
        canonical_name = canonical_name or stage_id
    if not slug or not canonical_name:
        return None

    aliases = set()
    if stage_id:
        aliases.add(stage_id)

    comp = registry.register_competition(
        sport=sport, canonical_name=canonical_name,
        slug=slug, aliases=aliases,
    )
    # Wire the FL stage_id into the alias index too, so external
    # callers can resolve_through_alias('fl', stage_id) → Competition.
    if stage_id:
        registry.register_alias(
            source="fl", external_id=stage_id,
            canonical_id=comp.id, method="strict",
            confidence=1.0,
        )
    return comp


# ── Fixture seeding ──────────────────────────────────────────────

def seed_fixture_from_fl_event(registry: IdentityRegistry,
                                 fl_event: dict, sport: str,
                                 competition_id: Optional[str] = None,
                                 fl_tournament: Optional[dict] = None
                                 ) -> Optional[Fixture]:
    """Register a fixture from a FL event dict.

    Requires both teams to be seedable (via seed_team_from_fl_event)
    and a START_TIME (or START_UTIME) to be present. Returns the
    Fixture, or None if any of these prerequisites fail.

    Phase C2d: the fixture's canonical date is the LOCAL game date,
    not the UTC date. The local timezone is resolved from
    `fl_tournament['NAME']` via `competition_tz()`. Falls back to
    UTC if no tournament context (or no NAME) is supplied — matches
    pre-C2d behavior so callers that don't pass tournament still
    work, just without timezone-aware disambiguation.

    The FL EVENT_ID is registered as an alias under source='fl' so
    fl-keyed lookups resolve to the canonical fixture.
    """
    home = seed_team_from_fl_event(registry, fl_event, sport, "home")
    away = seed_team_from_fl_event(registry, fl_event, sport, "away")
    if home is None or away is None:
        return None

    start_ts = fl_event.get("START_TIME") or fl_event.get("START_UTIME")
    if start_ts is None:
        return None
    try:
        start_ts = int(start_ts)
    except (TypeError, ValueError):
        return None

    # Local-date resolution: pull tz from the tournament's name when
    # available; UTC fallback. Wrapped in try/except so a malformed
    # tz string doesn't take the whole seeder down.
    tz_name = "UTC"
    if fl_tournament is not None:
        tz_name = competition_tz(
            fl_tournament.get("NAME") or "", sport,
        )
    try:
        when = compute_local_date(start_ts, tz_name)
    except (OSError, ValueError, KeyError):
        # Fall back to UTC date if the tz lookup or epoch conversion
        # fails for any reason.
        try:
            when = datetime.fromtimestamp(
                start_ts, tz=timezone.utc,
            ).date()
        except (TypeError, ValueError, OSError):
            return None

    fixture = registry.register_fixture(
        sport=sport, when=when,
        home_team_id=home.id, away_team_id=away.id,
        start_time_utc=start_ts,
        competition_id=competition_id,
    )
    fl_event_id = (fl_event.get("EVENT_ID") or "").strip()
    if fl_event_id:
        registry.register_alias(
            source="fl", external_id=fl_event_id,
            canonical_id=fixture.id, method="strict",
            confidence=1.0,
        )
    return fixture


# ── Top-level: full FL response ──────────────────────────────────

def seed_from_fl_response(registry: IdentityRegistry,
                           fl_response: dict, sport: str
                           ) -> dict:
    """Walk a full FlashLive events-list response and populate the
    registry. Returns a stats dict for observability:
        {
          'tournaments_seeded': int,
          'fixtures_seeded':    int,
          'teams_seeded':       int,
          'fixtures_skipped':   int,
        }

    `fl_response` shape (subset, what we actually read):
        {
          'DATA': [
            {
              'TOURNAMENT_STAGE_ID': str,
              'NAME': str,
              'NAME_PART_1': str,    # optional; ignored here
              'NAME_PART_2': str,    # optional fallback name
              'COUNTRY_NAME': str,   # optional; not used in seed yet
              'EVENTS': [
                {
                  'EVENT_ID': str,
                  'HOME_NAME': str,
                  'AWAY_NAME': str,
                  'SHORTNAME_HOME': str,
                  'SHORTNAME_AWAY': str,
                  'START_TIME': int,    # epoch seconds, UTC
                  'STAGE_TYPE': str,    # optional; not used yet
                },
                ...
              ],
            },
            ...
          ],
        }
    """
    stats = {
        "tournaments_seeded": 0,
        "fixtures_seeded":    0,
        "teams_seeded":       0,
        "fixtures_skipped":   0,
    }
    teams_before = registry.stats()["teams"]

    data = fl_response.get("DATA") or []
    if not isinstance(data, list):
        return stats

    for tournament in data:
        if not isinstance(tournament, dict):
            continue
        comp = seed_competition_from_fl(registry, tournament, sport)
        if comp is not None:
            stats["tournaments_seeded"] += 1

        events = tournament.get("EVENTS") or []
        if not isinstance(events, list):
            continue
        for ev in events:
            if not isinstance(ev, dict):
                continue
            fx = seed_fixture_from_fl_event(
                registry, ev, sport,
                competition_id=(comp.id if comp else None),
                fl_tournament=tournament,
            )
            if fx is not None:
                stats["fixtures_seeded"] += 1
            else:
                stats["fixtures_skipped"] += 1

    stats["teams_seeded"] = registry.stats()["teams"] - teams_before
    return stats
