"""FL resolver module — extract_signal from sp.fl_events.raw_payload.

Per architecture v1.4 §7.2. Reads the FlashLive event shape produced
by ingestion.fl and returns a standardized FixtureSignal for the
central matcher.

Phase 2A: extract_signal only. No database reads or writes; pure
function over the raw payload. Phase 2B (strict tier) is what
actually runs the matcher and writes resolution_log entries.

FL raw_payload shape (validated by ingestion.schema_validation.FLEventValidator):

    {
      "EVENT_ID":                 "abc123",          # FL's primary key
      "HOME_NAME":                "Bayern Munich",
      "AWAY_NAME":                "PSG",
      "SHORTNAME_HOME":           "BAY",
      "SHORTNAME_AWAY":           "PSG",
      "HOME_PARTICIPANT_TEAM_ID": ["fl-team-uuid-home"],
      "AWAY_PARTICIPANT_TEAM_ID": ["fl-team-uuid-away"],
      "START_TIME":               1778191200,        # unix epoch UTC
      "STAGE_TYPE":               "SCHEDULED",
      ...
    }

Tournament context (NAME_PART_1 region, NAME_PART_2 league) is held by
the parent tournament dict in the FL response. ingestion.fl writes one
row per event with the tournament denormalized into raw_payload via
the legacy `_FL_TEAM_HINTS` flow — but for clean Phase 2A scope, we
require the caller to pass tournament context separately. Keeps
extract_signal pure over the per-event dict.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ._normalize import normalize_name
from .alias_tier.normalize import INDIVIDUAL_SPORT_CODES
from .types import FixtureSignal, TeamCandidate


# Stable resolver version. Bump when extraction logic changes
# semantically (a new candidate kind, new normalization rule).
# Logged with every decision so replay can reconstruct what the
# extractor saw at decision time.
RESOLVER_VERSION = "fl@2a.0"


def _is_doubles_pair_signal(sport: str, raw_record: dict[str, Any]) -> bool:
    """True when the FL payload describes a doubles pair (or MMA
    tag-team / Darts pair) rather than a single-fixture head-to-head.

    Predicate: sport is in `INDIVIDUAL_SPORT_CODES` (case-insensitive
    match on the display name FL runners pass — 'Tennis' / 'MMA' /
    'Darts', plus the preventive set 'Boxing' / 'Golf' / 'Snooker')
    AND HOME_NAME or AWAY_NAME contains a '/'.

    Structurally-unmatchable: sp.teams has no doubles-pair entity;
    the matcher would walk ~40 single-player canonicals, correctly
    fail, and route to review_queue every nightly cron. Same
    treatment as Kalshi's KXMLBMENTION exclusion via
    `_OUTRIGHT_SERIES_PREFIXES` — reject at extraction so the
    matcher never sees it and the daily cron doesn't burn cycles.

    Validated FL population (Day-48): 6,448 pending review_queue
    rows split Tennis 6,213 / MMA 187 / Darts 48, all
    `reason_detail.is_personal = true`. Soccer 22 + Basketball 4
    slash-names are is_personal=false lookalikes (SJK Akatemia/2,
    PPJ/Ruoholahti) — the sport gate correctly excludes them.

    Preventive coverage on Boxing/Golf/Snooker: zero observed rows
    today, but structurally-same shape if FL emits a tag-team boxing
    match, pairs match play in golf, or a doubles snooker frame.
    Firing preventively costs nothing (no false-positive population)
    and avoids a follow-up PR on discovery.
    """
    if sport.lower() not in INDIVIDUAL_SPORT_CODES:
        return False
    home = raw_record.get("HOME_NAME") or ""
    away = raw_record.get("AWAY_NAME") or ""
    return "/" in home or "/" in away


class FLResolverModule:
    """ResolverModule for FlashLive provider records.

    Stateless. Same payload → same FixtureSignal. Drop-in for the
    Protocol contract in resolver.protocol.
    """

    @property
    def provider(self) -> str:
        return "fl"

    def extract_signal(
        self,
        raw_record: dict[str, Any],
        *,
        tournament_context: dict[str, Any] | None = None,
        sport: str = "",
    ) -> FixtureSignal | None:
        """Pull a FixtureSignal from one FL event payload.

        `tournament_context` (optional): the parent tournament dict
        from FL's /v1/events/list response, with NAME / NAME_PART_1 /
        NAME_PART_2 / TOURNAMENT_STAGE_ID. Used as competition_hint.
        Pass None when called from a path that has the event but
        not the tournament wrapper; the matcher will fall back to
        sport-only matching.

        `sport`: canonical sport code ('soccer', 'tennis', etc.).
        Required for the central matcher's competition + sport
        filtering. Pass '' when unknown — the matcher will degrade
        gracefully.

        Returns None if the record lacks an EVENT_ID (the only
        absolutely-required field).
        """
        event_id = (raw_record.get("EVENT_ID") or "").strip()
        if not event_id:
            return None

        # Doubles-pair / tag-team / pairs guard (Day-48). sp.teams
        # has no doubles-pair entity; reject at extraction so neither
        # the daily cron nor the reresolution loop burns cycles
        # matching what will always fail. Same shape as Kalshi's
        # KXMLBMENTION exclusion. See _is_doubles_pair_signal above.
        if _is_doubles_pair_signal(sport, raw_record):
            return None

        home_candidates = self._team_candidates(
            name=raw_record.get("HOME_NAME"),
            shortname=raw_record.get("SHORTNAME_HOME"),
            participant_ids=raw_record.get("HOME_PARTICIPANT_TEAM_ID"),
        )
        away_candidates = self._team_candidates(
            name=raw_record.get("AWAY_NAME"),
            shortname=raw_record.get("SHORTNAME_AWAY"),
            participant_ids=raw_record.get("AWAY_PARTICIPANT_TEAM_ID"),
        )

        kickoff_at, kickoff_confidence = self._kickoff(raw_record)

        competition_hint = None
        if tournament_context:
            competition_hint = (
                tournament_context.get("TOURNAMENT_STAGE_ID")
                or tournament_context.get("NAME")
                or None
            )

        return FixtureSignal(
            provider=self.provider,
            provider_record_id=event_id,
            sport=sport,
            home_team_candidates=home_candidates,
            away_team_candidates=away_candidates,
            kickoff_at=kickoff_at,
            kickoff_confidence=kickoff_confidence,
            competition_hint=competition_hint,
            raw_signals={
                "stage_type":    raw_record.get("STAGE_TYPE"),
                "stage":         raw_record.get("STAGE"),
                "tournament":    (tournament_context or {}).get("NAME"),
                "name_part_1":   (tournament_context or {}).get("NAME_PART_1"),
                "name_part_2":   (tournament_context or {}).get("NAME_PART_2"),
            },
        )

    @staticmethod
    def _team_candidates(
        *,
        name: str | None,
        shortname: str | None,
        participant_ids: list | None,
    ) -> list[TeamCandidate]:
        """Build the candidate list for one side of a fixture.

        Order matters for matcher tie-breaking — strongest signals
        first. fl_team_id is strongest (FL's own identifier),
        followed by full canonical name, then shortname.
        """
        out: list[TeamCandidate] = []
        if isinstance(participant_ids, list):
            for pid in participant_ids:
                pid_str = str(pid).strip() if pid is not None else ""
                if pid_str:
                    out.append(TeamCandidate(
                        raw=pid_str,
                        normalized=pid_str,  # IDs aren't normalized; matched verbatim
                        kind="fl_team_id",
                        weight=1.0,
                    ))
        if name:
            out.append(TeamCandidate(
                raw=name,
                normalized=normalize_name(name),
                kind="name",
                weight=0.9,
            ))
        if shortname:
            sn = shortname.strip()
            if sn:
                out.append(TeamCandidate(
                    raw=sn,
                    normalized=sn.upper(),  # FL shortnames are already uppercase abbrs
                    kind="shortname",
                    weight=0.7,
                ))
        return out

    @staticmethod
    def _kickoff(raw: dict) -> tuple[datetime | None, float]:
        """Extract kickoff datetime + confidence from FL fields.

        FL ships START_TIME (preferred) and START_UTIME (alternate);
        both are unix epoch UTC. Either being present yields
        confidence 1.0. None of them present yields (None, 0.0).
        """
        for key in ("START_TIME", "START_UTIME"):
            v = raw.get(key)
            if v is None:
                continue
            try:
                return (
                    datetime.fromtimestamp(int(v), tz=timezone.utc),
                    1.0,
                )
            except (TypeError, ValueError, OSError):
                continue
        return None, 0.0
