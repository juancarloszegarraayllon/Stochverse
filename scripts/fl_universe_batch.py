"""Phase 2D.5-A engine — Component 3 batch orchestrator.

End-to-end batch crawl of an entire sport's leagues via FL:
  1. Enumerate leagues via /v1/tournaments/list
  2. Per league: pick league-table stage (regular season > knockout
     per stage_rank), with 404-fallback across candidate stages
  3. Harvest roster via /v1/tournaments/standings →
     /v1/teams/data per team (in-process cache)
  4. Cross-reference vs sp.teams: classify INSERT / BACKFILL / SKIP
     with name_count + verify-don't-trust columns (sport_id,
     country_code, created_at) on every match
  5. Fragmentation detection via resolver.fragmentation:
     find_all_fragmentation_pairs_pure scoped to anchor + partners,
     classify_fragmentation_pair_pure with fixture counts pulled in
     one batch SQL query per league
  6. Build proposed aliases from BACKFILL FL-canonical-as-alias and
     fragmentation ALIAS-LINK dormant-phantom-canonical-as-alias.
     Pipe through resolver.collision_audit.audit_alias_collisions.
  7. Emit per-league bundles + top-level index.

Wires three engine primitives:
  - resolver.collision_audit (Component 1 — amendment #22 audit)
  - resolver.text_match (Component 2 — distinctive-token primitive,
                         used transitively via fragmentation)
  - resolver.fragmentation (Component 3 primitive — Day-37 LOCKED rule)

FL discovery primitives (enumerate_leagues, fetch_roster_with_fallback,
fetch_team_detail, _stage_rank) are inlined here rather than imported
from scripts/fl_universe_seed.py — the pilot script lives on a separate
branch (claude/fl-universe-seed-pilot). Duplication accepted; unify at
consolidation, don't fight cross-branch imports.

## Output structure

  <out_dir>/
    index.md                          # human-readable summary across leagues
    index.json                        # structured equivalent
    leagues/
      <country>--<league_slug>/
        stage_meta.json               # FL discovery context (stage_id,
                                      # season_id, candidates tried,
                                      # 404-fallback path)
        fl_intermediate.json          # raw FL roster + team_data payloads
        classification.md             # INSERT/BACKFILL/SKIP per team
                                      # with name_count + verify-don't-
                                      # trust columns
        fragmentation.md              # alias-link verdicts +
                                      # merge-required flags
        aliases_audited.md            # proposed aliases → collision audit
        seed.py.draft                 # manifest skeleton

## Operator's first-validation gate

Re-run BBL through the full batch path:

    DATABASE_URL=<url> FLASHLIVE_API_KEY=<key> \\
      python scripts/fl_universe_batch.py \\
        --sport-id 3 \\
        --max-leagues 1 \\
        --league-hint BBL \\
        --country-hint Germany \\
        --out-dir ./batch_output_bbl/

Expected output reproducing Day-36 BBL pilot + harvester + fragmentation
results we already verified by hand:
  - stage_meta.json: Main stage_id selected over Play Offs / Play-in
  - 18-team roster
  - 15/15 BACKFILL classifications
  - 7 fragmentation pairs flagged (5 alias-link + 2 merge-required —
    Rostock 5+3 and Hamburg 2+1)
  - Collision audit emits clean alias set with BACKFILL FL-canonicals

If the orchestrator reproduces the manual BBL results, batch crawl
unlocks. If not, debug before scaling.

## Constraints (per Day-37 brief)

  - NO auto-apply. Read-only against sp.teams + sp.fixtures.
  - Respect flashlive_feed._fl_get rate-limiting (RapidAPI Mega tier
    inherits 0.025s sustained gap by default).
  - FL canonical stays an ALIAS, never canonical_name.
  - Verify-don't-trust columns on every match: sport_id,
    country_code, created_at, name_count.

## Exit codes

  0 — success (outputs written)
  1 — DATABASE_URL or FLASHLIVE_API_KEY missing
  2 — bad CLI args
  3 — FL tournaments/list returned no leagues for sport_id
  4 — All leagues failed (no roster harvestable across any candidate
      stage)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from db import async_session  # noqa: E402
from observability import get_logger  # noqa: E402
from resolver._normalize import normalize_name  # noqa: E402
from resolver.collision_audit import (  # noqa: E402
    ProposedAlias,
    audit_alias_collisions,
    propose_alias,
)
from resolver.fragmentation import (  # noqa: E402
    SPTeamLite,
    classify_fragmentation_pair_pure,
    find_all_fragmentation_pairs_pure,
)


# ──────────────────────────────────────────────────────────────────────
# Data shapes
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LeagueCandidate:
    """One stage candidate from /v1/tournaments/list."""
    league_name: str
    country: str
    season_id: str
    stage_id: str
    stage_name: str
    league_score: int   # 100/80/60/0 vs hint
    stage_score: int    # per _stage_rank


@dataclass
class LeagueInfo:
    """Resolved league + the stage we ended up using."""
    league_name: str
    country: str
    season_id: str
    chosen_stage_id: str
    chosen_stage_name: str
    candidates_tried: list[LeagueCandidate] = field(default_factory=list)
    fallback_path: list[str] = field(default_factory=list)
    slug: str = ""


@dataclass
class FLTeam:
    """FL team master record after /v1/teams/data resolution."""
    team_id: str
    fl_canonical: str   # FL's NAME field — provider short-form (Amendment #24)
    country: str
    raw: dict = field(default_factory=dict)

    @property
    def normalized(self) -> str:
        return normalize_name(self.fl_canonical)


@dataclass
class ClassifiedTeam:
    """FL team + sp.teams cross-reference verdict."""
    fl: FLTeam
    classification: str  # INSERT | BACKFILL | SKIP
    sp_team_id: str | None = None
    sp_canonical: str | None = None
    sp_country_code: str | None = None
    sp_sport_id: int | None = None
    sp_created_at: str | None = None
    name_count: int = 0  # how many sp.teams rows share this normalized name
    notes: str = ""


# ──────────────────────────────────────────────────────────────────────
# Slugify
# ──────────────────────────────────────────────────────────────────────


_SLUG_PUNCT = re.compile(r"[^a-z0-9]+")


def slugify(s: str) -> str:
    """FS-safe lowercase slug. NFD-strip accents, replace non-
    alphanumeric with dash, collapse, trim."""
    if not s:
        return "unknown"
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = _SLUG_PUNCT.sub("-", s.lower()).strip("-")
    return s or "unknown"


# ──────────────────────────────────────────────────────────────────────
# FL discovery — inlined from pilot
# ──────────────────────────────────────────────────────────────────────


def _stage_rank(stage_name: str) -> int:
    """Prefer regular-season stages for roster harvest. Inlined from
    pilot — keeps Amendment #23 (Play Offs 404 → Main success)
    documented in scripts/fl_universe_seed.py."""
    s = (stage_name or "").lower().strip()
    if not s:
        return 25
    REGULAR_KEYWORDS = (
        "main", "regular season", "regular_season", "regular-season",
        "league phase", "league stage", "league table",
        "league_phase", "league_stage", "group", "season",
    )
    if any(kw == s or kw in s for kw in REGULAR_KEYWORDS):
        return 100
    KNOCKOUT_KEYWORDS = (
        "play offs", "play-offs", "play-off", "play off", "playoff",
        "playoffs", "play in", "play-in", "play ins", "play-ins",
        "knockout", "knock-out", "knock out",
        "final", "semifinal", "semi-final", "semi final",
        "quarterfinal", "quarter-final", "quarter final",
        "round of", "round_of",
    )
    if any(kw in s for kw in KNOCKOUT_KEYWORDS):
        return 5
    QUALIFICATION_KEYWORDS = (
        "qualif", "preliminary", "qualifying", "pre-season",
        "pre_season", "preseason",
    )
    if any(kw in s for kw in QUALIFICATION_KEYWORDS):
        return 15
    return 50


async def enumerate_leagues(
    sport_id: str,
    league_hint: str,
    country_hint: str,
    log,
) -> list[list[LeagueCandidate]]:
    """Return list of candidate-lists — one per unique (country,
    league_name) group, each sorted by stage_rank (regular-season
    first).

    If league_hint and/or country_hint are non-empty, candidates
    are filtered + scored against them; empty strings mean
    "enumerate all".

    Returned outer list is sorted by best league_score (hint match
    quality) then best stage_score (regular-season preference).
    """
    from flashlive_feed import _fl_get

    resp = await _fl_get(
        "/v1/tournaments/list",
        {"sport_id": sport_id, "locale": "en_INT"},
    )
    if not isinstance(resp, dict):
        log.error("fl_batch.tournaments_list.empty",
                  resp_type=type(resp).__name__)
        return []
    data = resp.get("DATA") or []
    if not isinstance(data, list):
        log.error("fl_batch.tournaments_list.bad_shape")
        return []

    league_hint_lower = league_hint.lower().strip()
    country_hint_lower = country_hint.lower().strip()

    # Group candidates per (country, league_name).
    groups: dict[tuple[str, str], list[LeagueCandidate]] = {}

    for entry in data:
        if not isinstance(entry, dict):
            continue
        league_name = (entry.get("LEAGUE_NAME") or "").strip()
        country_name = (entry.get("COUNTRY_NAME") or "").strip()
        season_id = (
            entry.get("ACTUAL_TOURNAMENT_SEASON_ID")
            or entry.get("TOURNAMENT_SEASON_ID")
            or ""
        )
        if not league_name:
            continue
        if country_hint_lower and country_hint_lower not in country_name.lower():
            continue

        # League score against hint (100/80/60/empty hint passes all).
        league_lower = league_name.lower()
        if not league_hint_lower:
            league_score = 50  # neutral when no hint
        elif league_hint_lower == league_lower:
            league_score = 100
        elif league_hint_lower in league_lower:
            league_score = 80
        elif league_lower in league_hint_lower:
            league_score = 60
        else:
            continue  # hint set + no match → skip

        stages = entry.get("STAGES") or []
        if not isinstance(stages, list):
            continue
        for stage in stages:
            if not isinstance(stage, dict):
                continue
            stage_id = stage.get("STAGE_ID")
            stage_name = stage.get("STAGE_NAME") or ""
            if not stage_id:
                continue
            cand = LeagueCandidate(
                league_name=league_name,
                country=country_name,
                season_id=season_id,
                stage_id=stage_id,
                stage_name=stage_name,
                league_score=league_score,
                stage_score=_stage_rank(stage_name),
            )
            key = (country_name, league_name)
            groups.setdefault(key, []).append(cand)

    log.info("fl_batch.tournaments_list.grouped",
             league_groups=len(groups),
             league_hint=league_hint or "(any)",
             country_hint=country_hint or "(any)")

    # Sort each group's candidates by stage rank (best first).
    sorted_groups: list[list[LeagueCandidate]] = []
    for cands in groups.values():
        cands.sort(key=lambda c: (c.league_score, c.stage_score),
                   reverse=True)
        sorted_groups.append(cands)

    # Sort outer list by best candidate per group.
    sorted_groups.sort(
        key=lambda lst: (lst[0].league_score, lst[0].stage_score),
        reverse=True,
    )
    return sorted_groups


async def fetch_roster_with_fallback(
    candidates: Sequence[LeagueCandidate],
    log,
) -> tuple[LeagueCandidate | None, list[dict], list[str]]:
    """Try /v1/tournaments/standings against each candidate stage in
    rank order. Return (chosen_candidate, roster, fallback_path).

    fallback_path is the list of stage_names tried before success
    (for the operator's stage_meta.json).
    """
    from flashlive_feed import _fl_get

    fallback_path: list[str] = []
    for cand in candidates:
        fallback_path.append(cand.stage_name)
        params = {
            "tournament_stage_id": cand.stage_id,
            "standing_type": "overall",
        }
        if cand.season_id:
            params["tournament_season_id"] = cand.season_id

        resp = await _fl_get("/v1/tournaments/standings", params)
        if not isinstance(resp, dict):
            log.warning("fl_batch.standings.empty",
                        stage_id=cand.stage_id,
                        stage_name=cand.stage_name)
            continue

        data = resp.get("DATA") or []
        if not isinstance(data, list):
            continue

        teams: list[dict] = []
        seen: set[str] = set()

        def _walk(node):
            if isinstance(node, dict):
                tid = (node.get("TEAM_ID") or node.get("PARTICIPANT_ID")
                       or node.get("ID") or "")
                tname = (node.get("TEAM_NAME") or node.get("PARTICIPANT_NAME")
                         or node.get("NAME") or "")
                if tid and tid not in seen:
                    seen.add(tid)
                    teams.append({"team_id": tid, "team_name": tname,
                                  "raw_row": node})
                for v in node.values():
                    _walk(v)
            elif isinstance(node, list):
                for item in node:
                    _walk(item)

        _walk(data)
        if teams:
            log.info("fl_batch.standings.success",
                     stage_id=cand.stage_id,
                     stage_name=cand.stage_name,
                     team_count=len(teams),
                     fallback_steps=len(fallback_path) - 1)
            return cand, teams, fallback_path
        log.warning("fl_batch.standings.empty_roster",
                    stage_id=cand.stage_id,
                    stage_name=cand.stage_name)

    return None, [], fallback_path


async def fetch_team_detail(
    team_id: str,
    sport_id: str,
    log,
    cache: dict[str, FLTeam],
) -> FLTeam | None:
    """Fetch /v1/teams/data with in-process cache. The cache is a
    dict keyed on team_id; entries persist across leagues in a single
    run (FL team_ids are global, so a team appearing in two leagues
    only resolves once)."""
    if team_id in cache:
        return cache[team_id]

    from flashlive_feed import _fl_get
    resp = await _fl_get(
        "/v1/teams/data",
        {"sport_id": sport_id, "team_id": team_id},
    )
    if not isinstance(resp, dict):
        log.warning("fl_batch.teams_data.empty", team_id=team_id)
        return None

    data = resp.get("DATA")
    if not isinstance(data, dict):
        if isinstance(resp.get("TEAM"), dict):
            data = resp["TEAM"]
        else:
            log.warning("fl_batch.teams_data.bad_shape", team_id=team_id)
            return None

    name = (data.get("NAME") or data.get("TEAM_NAME")
            or data.get("PARTICIPANT_NAME") or "").strip()
    country = (data.get("COUNTRY_NAME") or data.get("COUNTRY")
               or "").strip()
    if not name:
        log.warning("fl_batch.teams_data.no_name", team_id=team_id,
                    keys=list(data.keys()))
        return None

    team = FLTeam(team_id=team_id, fl_canonical=name,
                  country=country, raw=data)
    cache[team_id] = team
    return team


# ──────────────────────────────────────────────────────────────────────
# sp.teams cross-reference
# ──────────────────────────────────────────────────────────────────────


async def load_sp_teams_for_sport(
    session, sport_id: int, log,
) -> tuple[list[SPTeamLite], dict[str, list[SPTeamLite]]]:
    """Bulk-load every sp.teams row for the given sport. Returns
    (all_teams, by_normalized) where by_normalized maps
    normalized_name → list of teams sharing that name (name_count =
    len of list).

    SPTeamLite is reused from resolver.fragmentation; the same shape
    feeds both classification and fragmentation detection.
    """
    rows = (await session.execute(
        text(
            "SELECT id::text AS team_id, canonical_name, "
            "       normalized_name, country_code, "
            "       to_char(created_at, 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') "
            "         AS created_at "
            "FROM sp.teams WHERE sport_id = :sport_id"
        ),
        {"sport_id": sport_id},
    )).all()
    all_teams = [
        SPTeamLite(
            team_id=r.team_id,
            canonical_name=r.canonical_name,
            normalized_name=r.normalized_name,
            country_code=r.country_code,
            created_at=r.created_at or "",
        )
        for r in rows
    ]
    by_normalized: dict[str, list[SPTeamLite]] = {}
    for t in all_teams:
        by_normalized.setdefault(t.normalized_name, []).append(t)
    log.info("fl_batch.sp_teams.loaded",
             sport_id=sport_id, count=len(all_teams))
    return all_teams, by_normalized


def classify_team_pure(
    fl: FLTeam,
    by_normalized: dict[str, list[SPTeamLite]],
    sport_id: int,
) -> ClassifiedTeam:
    """Pure classification: INSERT / BACKFILL / SKIP, with verify-
    don't-trust columns on any match."""
    normed = fl.normalized
    if not normed:
        return ClassifiedTeam(
            fl=fl, classification="SKIP",
            notes="FL canonical normalizes to empty",
        )
    matches = by_normalized.get(normed, [])
    if not matches:
        return ClassifiedTeam(
            fl=fl, classification="INSERT",
            notes="no normalized_name match in sp.teams",
        )
    name_count = len(matches)
    # Pick the first match (caller is alerted via name_count > 1 if
    # ambiguous — multi-team_id spot-check).
    sp = matches[0]
    classification = "SKIP" if sp.country_code else "BACKFILL"
    notes = (
        "match found; country_code already set"
        if sp.country_code
        else "match found; country_code is NULL"
    )
    if name_count > 1:
        notes += (
            f"; AMBIGUOUS: {name_count} sp.teams rows share this "
            "normalized_name — operator spot-check required"
        )
    return ClassifiedTeam(
        fl=fl, classification=classification,
        sp_team_id=sp.team_id,
        sp_canonical=sp.canonical_name,
        sp_country_code=sp.country_code,
        sp_sport_id=sport_id,
        sp_created_at=sp.created_at,
        name_count=name_count,
        notes=notes,
    )


# ──────────────────────────────────────────────────────────────────────
# Fixture counts (batch SQL for fragmentation classification)
# ──────────────────────────────────────────────────────────────────────


async def load_fixture_counts(
    session, team_ids: Sequence[str], log,
) -> dict[str, int]:
    """Single batch query: for each team_id, count fixtures it
    appears in on either side."""
    if not team_ids:
        return {}
    rows = (await session.execute(
        text(
            "SELECT t.id::text AS team_id, "
            "       COALESCE((SELECT count(*) FROM sp.fixtures f "
            "                  WHERE f.home_team_id = t.id "
            "                     OR f.away_team_id = t.id), 0) "
            "         AS fixture_count "
            "FROM sp.teams t "
            "WHERE t.id::text = ANY(:team_ids)"
        ),
        {"team_ids": list(team_ids)},
    )).all()
    counts = {r.team_id: int(r.fixture_count) for r in rows}
    log.info("fl_batch.fixture_counts.loaded", count=len(counts))
    return counts


# ──────────────────────────────────────────────────────────────────────
# Per-league processing
# ──────────────────────────────────────────────────────────────────────


@dataclass
class LeagueBundle:
    """End-to-end per-league results."""
    league_info: LeagueInfo
    fl_teams: list[FLTeam]
    classified: list[ClassifiedTeam]
    fragmentation_verdicts: list
    fragmentation_pair_count: int
    alias_link_count: int
    merge_required_count: int
    proposed_aliases: list[ProposedAlias]
    collision_report: Any  # CollisionReport
    elapsed_sec: float


async def process_league(
    candidates: list[LeagueCandidate],
    sport_id: int,
    sport_id_str: str,
    session,
    sp_teams_all: list[SPTeamLite],
    by_normalized: dict[str, list[SPTeamLite]],
    team_data_cache: dict[str, FLTeam],
    log,
) -> LeagueBundle | None:
    """Crawl one league: roster → classify → fragmentation → aliases
    → collision audit. Returns None if all candidate stages failed."""
    started = time.monotonic()
    log.info("fl_batch.league.start",
             league=candidates[0].league_name,
             country=candidates[0].country,
             candidate_count=len(candidates))

    chosen, roster, fallback_path = await fetch_roster_with_fallback(
        candidates=candidates, log=log,
    )
    if not chosen or not roster:
        log.warning(
            "fl_batch.league.no_roster",
            league=candidates[0].league_name,
            country=candidates[0].country,
            attempts=len(candidates),
        )
        return None

    league_info = LeagueInfo(
        league_name=chosen.league_name,
        country=chosen.country,
        season_id=chosen.season_id,
        chosen_stage_id=chosen.stage_id,
        chosen_stage_name=chosen.stage_name,
        candidates_tried=list(candidates),
        fallback_path=fallback_path,
        slug=f"{slugify(chosen.country)}--{slugify(chosen.league_name)}",
    )

    # Per-team detail (cache-aware).
    fl_teams: list[FLTeam] = []
    for entry in roster:
        tid = entry["team_id"]
        team = await fetch_team_detail(
            team_id=tid, sport_id=sport_id_str, log=log,
            cache=team_data_cache,
        )
        if team:
            fl_teams.append(team)
        else:
            fallback_name = entry.get("team_name") or ""
            if fallback_name:
                ft = FLTeam(
                    team_id=tid, fl_canonical=fallback_name,
                    country="",
                    raw={"_fallback": "from_standings_only"},
                )
                team_data_cache[tid] = ft
                fl_teams.append(ft)

    # Classify.
    classified = [
        classify_team_pure(
            fl=t, by_normalized=by_normalized, sport_id=sport_id,
        )
        for t in fl_teams
    ]

    # Fragmentation: scan classified BACKFILL/SKIP teams' partners
    # against the full sp.teams list. Anchor set = the sp.teams
    # rows we matched to FL roster; partners come from the broader
    # sport population.
    anchor_team_ids = {
        c.sp_team_id for c in classified
        if c.sp_team_id is not None
    }
    anchor_sp_teams = [
        t for t in sp_teams_all if t.team_id in anchor_team_ids
    ]
    # Pair detection over the full sport_id population — partners
    # may be Phase 2A.5 phantoms not in our FL roster (the BBL
    # Oldenburg ↔ EWE Baskets Oldenburg shape).
    # We scope to: anchor + every sp.teams row sharing at least one
    # distinctive token with an anchor.
    from resolver.text_match import distinctive_tokens
    anchor_tokens_union: set[str] = set()
    for a in anchor_sp_teams:
        anchor_tokens_union.update(distinctive_tokens(a.normalized_name))
    partner_pool = [
        t for t in sp_teams_all
        if t.team_id not in anchor_team_ids
        and (set(distinctive_tokens(t.normalized_name))
             & anchor_tokens_union)
    ]
    fragmentation_scan_set = list(anchor_sp_teams) + partner_pool
    raw_pairs = find_all_fragmentation_pairs_pure(fragmentation_scan_set)
    # Filter to pairs that include at least one anchor (so we only
    # flag fragmentation for teams we just harvested).
    pairs = [
        p for p in raw_pairs
        if (p.anchor.team_id in anchor_team_ids
            or p.partner.team_id in anchor_team_ids)
    ]

    # Fixture counts batch query.
    pair_team_ids: set[str] = set()
    for p in pairs:
        pair_team_ids.add(p.anchor.team_id)
        pair_team_ids.add(p.partner.team_id)
    fixture_counts = await load_fixture_counts(
        session=session, team_ids=list(pair_team_ids), log=log,
    )

    verdicts = [
        classify_fragmentation_pair_pure(
            pair=p,
            anchor_fixture_count=fixture_counts.get(p.anchor.team_id, 0),
            partner_fixture_count=fixture_counts.get(p.partner.team_id, 0),
        )
        for p in pairs
    ]
    alias_link_count = sum(
        1 for v in verdicts if v.classification == "ALIAS-LINK"
    )
    merge_required_count = sum(
        1 for v in verdicts if v.classification == "MERGE-REQUIRED"
    )

    # Build proposed aliases:
    #   - BACKFILL: FL canonical → alias on the matched sp_team_id
    #     (only if FL canonical normalizes differently than sp canonical;
    #     otherwise the FL form already matches and is a no-op).
    #   - ALIAS-LINK fragmentation: dormant phantom's canonical → alias
    #     on the canonical winner.
    proposals: list[ProposedAlias] = []
    for c in classified:
        if c.classification != "BACKFILL" or not c.sp_team_id:
            continue
        norm_fl = c.fl.normalized
        if not norm_fl:
            continue
        norm_sp = normalize_name(c.sp_canonical or "")
        if norm_fl == norm_sp:
            continue  # no-op: FL form already matches sp canonical
        proposals.append(propose_alias(
            alias_normalized=norm_fl,
            raw_alias=c.fl.fl_canonical,
            target_team_id=c.sp_team_id,
        ))
    for v in verdicts:
        if v.classification != "ALIAS-LINK":
            continue
        if not v.proposed_alias_form or not v.canonical_winner_team_id:
            continue
        proposals.append(propose_alias(
            alias_normalized=normalize_name(v.proposed_alias_form),
            raw_alias=v.proposed_alias_form,
            target_team_id=v.canonical_winner_team_id,
        ))

    # Collision audit.
    report = await audit_alias_collisions(
        session=session,
        proposed_aliases=proposals,
        sport_id=sport_id,
    )
    log.info(
        "fl_batch.league.collision_audit",
        league=league_info.league_name,
        summary=report.summarize(),
    )

    elapsed = time.monotonic() - started
    log.info(
        "fl_batch.league.complete",
        league=league_info.league_name,
        slug=league_info.slug,
        elapsed_sec=round(elapsed, 2),
        fl_teams=len(fl_teams),
        insert=sum(1 for c in classified if c.classification == "INSERT"),
        backfill=sum(1 for c in classified
                     if c.classification == "BACKFILL"),
        skip=sum(1 for c in classified if c.classification == "SKIP"),
        alias_link=alias_link_count,
        merge_required=merge_required_count,
        clean_aliases=len(report.clean),
        colliding_aliases=len(report.colliding),
    )

    return LeagueBundle(
        league_info=league_info,
        fl_teams=fl_teams,
        classified=classified,
        fragmentation_verdicts=verdicts,
        fragmentation_pair_count=len(verdicts),
        alias_link_count=alias_link_count,
        merge_required_count=merge_required_count,
        proposed_aliases=proposals,
        collision_report=report,
        elapsed_sec=elapsed,
    )


# ──────────────────────────────────────────────────────────────────────
# Output writers
# ──────────────────────────────────────────────────────────────────────


def write_league_bundle(out_dir: Path, bundle: LeagueBundle) -> Path:
    """Write all per-league artifacts under <out_dir>/leagues/<slug>/."""
    league_dir = out_dir / "leagues" / bundle.league_info.slug
    league_dir.mkdir(parents=True, exist_ok=True)

    # stage_meta.json
    (league_dir / "stage_meta.json").write_text(json.dumps({
        "league_name": bundle.league_info.league_name,
        "country": bundle.league_info.country,
        "season_id": bundle.league_info.season_id,
        "chosen_stage_id": bundle.league_info.chosen_stage_id,
        "chosen_stage_name": bundle.league_info.chosen_stage_name,
        "fallback_path": bundle.league_info.fallback_path,
        "candidates_tried": [
            {
                "league_name": c.league_name,
                "stage_id": c.stage_id,
                "stage_name": c.stage_name,
                "league_score": c.league_score,
                "stage_score": c.stage_score,
            }
            for c in bundle.league_info.candidates_tried
        ],
    }, indent=2, ensure_ascii=False))

    # fl_intermediate.json
    (league_dir / "fl_intermediate.json").write_text(json.dumps({
        "league_name": bundle.league_info.league_name,
        "teams": [
            {
                "team_id": t.team_id,
                "fl_canonical": t.fl_canonical,
                "country": t.country,
                "normalized": t.normalized,
                "raw": t.raw,
            }
            for t in bundle.fl_teams
        ],
    }, indent=2, ensure_ascii=False))

    # classification.md
    _write_classification_md(league_dir / "classification.md", bundle)

    # fragmentation.md
    _write_fragmentation_md(league_dir / "fragmentation.md", bundle)

    # aliases_audited.md
    _write_aliases_md(league_dir / "aliases_audited.md", bundle)

    # seed.py.draft
    _write_seed_draft(league_dir / "seed.py.draft", bundle)

    return league_dir


def _write_classification_md(path: Path, bundle: LeagueBundle) -> None:
    counts = Counter(c.classification for c in bundle.classified)
    lines = [
        f"# {bundle.league_info.league_name} — Classification",
        "",
        f"Country: {bundle.league_info.country}",
        f"Stage: {bundle.league_info.chosen_stage_name} "
        f"({bundle.league_info.chosen_stage_id})",
        f"Roster size: {len(bundle.fl_teams)}",
        "",
        "## Summary",
        "",
        f"- INSERT: {counts.get('INSERT', 0)}",
        f"- BACKFILL: {counts.get('BACKFILL', 0)}",
        f"- SKIP: {counts.get('SKIP', 0)}",
        "",
        "## Per-team detail (verify-don't-trust columns)",
        "",
        "| Class | FL team_id | FL canonical | Country | "
        "sp.teams canonical | sp.team_id | sp_country_code | "
        "sp_sport_id | created_at | name_count | Notes |",
        "|---|---|---|---|---|---|---|---|---|---:|---|",
    ]
    for c in bundle.classified:
        lines.append(
            f"| {c.classification} | `{c.fl.team_id}` | "
            f"{c.fl.fl_canonical} | {c.fl.country} | "
            f"{c.sp_canonical or '—'} | "
            f"`{c.sp_team_id or '—'}` | "
            f"{c.sp_country_code or '—'} | "
            f"{c.sp_sport_id if c.sp_sport_id is not None else '—'} | "
            f"{c.sp_created_at or '—'} | "
            f"{c.name_count} | "
            f"{c.notes} |"
        )
    path.write_text("\n".join(lines))


def _write_fragmentation_md(path: Path, bundle: LeagueBundle) -> None:
    lines = [
        f"# {bundle.league_info.league_name} — Fragmentation",
        "",
        f"Pairs detected: {bundle.fragmentation_pair_count}",
        f"- ALIAS-LINK (auto-proposable): {bundle.alias_link_count}",
        f"- MERGE-REQUIRED (operator task): "
        f"{bundle.merge_required_count}",
        "",
    ]
    if bundle.fragmentation_pair_count == 0:
        lines.append("No fragmentation pairs detected. Per Day-37 rule, "
                     "no alias-link proposals or merge flags from this "
                     "league.")
        path.write_text("\n".join(lines))
        return

    alias_links = [
        v for v in bundle.fragmentation_verdicts
        if v.classification == "ALIAS-LINK"
    ]
    if alias_links:
        lines.append("## ALIAS-LINK candidates (Day-37 LOCKED rule)")
        lines.append("")
        lines.append(
            "| Anchor | Anchor fixtures | Partner | Partner fixtures | "
            "Canonical winner team_id | Dormant phantom team_id | "
            "Proposed alias | Shared distinctive |"
        )
        lines.append("|---|---:|---|---:|---|---|---|---|")
        for v in alias_links:
            shared = ", ".join(v.pair.shared_distinctive_tokens)
            lines.append(
                f"| {v.pair.anchor.canonical_name} | "
                f"{v.anchor_fixture_count} | "
                f"{v.pair.partner.canonical_name} | "
                f"{v.partner_fixture_count} | "
                f"`{v.canonical_winner_team_id}` | "
                f"`{v.dormant_phantom_team_id}` | "
                f"{v.proposed_alias_form} | "
                f"{shared} |"
            )
        lines.append("")

    merges = [
        v for v in bundle.fragmentation_verdicts
        if v.classification == "MERGE-REQUIRED"
    ]
    if merges:
        lines.append("## MERGE-REQUIRED — operator task (Tennis-dedup machinery)")
        lines.append("")
        lines.append(
            "| Anchor | Anchor fixtures | Partner | Partner fixtures | "
            "Notes |"
        )
        lines.append("|---|---:|---|---:|---|")
        for v in merges:
            lines.append(
                f"| {v.pair.anchor.canonical_name} | "
                f"{v.anchor_fixture_count} | "
                f"{v.pair.partner.canonical_name} | "
                f"{v.partner_fixture_count} | "
                f"{v.notes} |"
            )
    path.write_text("\n".join(lines))


def _write_aliases_md(path: Path, bundle: LeagueBundle) -> None:
    r = bundle.collision_report
    lines = [
        f"# {bundle.league_info.league_name} — Alias proposals (collision-audited)",
        "",
        f"- Total proposed: {len(bundle.proposed_aliases)}",
        f"- Clean (safe to emit): {len(r.clean)}",
        f"- Same team already present: {len(r.same_team_already_present)}",
        f"- Colliding (dropped): {len(r.colliding)}",
        "",
    ]
    if r.clean:
        lines.append("## Clean — safe to emit")
        lines.append("")
        lines.append("| alias_normalized | raw_alias | target_team_id |")
        lines.append("|---|---|---|")
        for p in r.clean:
            lines.append(
                f"| `{p.alias_normalized}` | {p.raw_alias} | "
                f"`{p.target_team_id}` |"
            )
        lines.append("")
    if r.colliding:
        lines.append("## Colliding — dropped from emit set")
        lines.append("")
        lines.append("| alias | proposed target | conflict team_id | "
                     "conflict canonical | source |")
        lines.append("|---|---|---|---|---|")
        for c in r.colliding:
            for m in c.conflicting_mappings:
                lines.append(
                    f"| `{c.proposed.alias_normalized}` | "
                    f"`{c.proposed.target_team_id}` | "
                    f"`{m.team_id}` | {m.canonical_name} | "
                    f"{m.source} |"
                )
        lines.append("")
    if r.same_team_already_present:
        lines.append("## Same team already present — bootstrap dedup will handle")
        lines.append("")
        lines.append("| alias | target_team_id |")
        lines.append("|---|---|")
        for p in r.same_team_already_present:
            lines.append(
                f"| `{p.alias_normalized}` | `{p.target_team_id}` |"
            )
    path.write_text("\n".join(lines))


def _write_seed_draft(path: Path, bundle: LeagueBundle) -> None:
    """Manifest skeleton. INSERTs leave canonical BLANK (operator fills);
    BACKFILLs preserve existing sp.teams canonical. FL canonical goes in
    aliases tuple (Amendment #24: FL is alias, never canonical_name)."""
    lines = [
        f'"""{bundle.league_info.country} — '
        f'{bundle.league_info.league_name} seed manifest DRAFT.',
        '',
        f'Autogenerated by scripts/fl_universe_batch.py.',
        f'FL stage_id: {bundle.league_info.chosen_stage_id}',
        f'FL stage_name: {bundle.league_info.chosen_stage_name}',
        f'FL season_id: {bundle.league_info.season_id}',
        '',
        'PILOT SKELETON ONLY — operator review required before apply.',
        'INSERT canonicals are BLANK (Amendment #24: FL provides',
        'structure not identity; operator fills canonical_name).',
        'BACKFILL canonicals preserve the existing sp.teams canonical.',
        'FL canonical is in the aliases tuple as a starting point;',
        'layer in additional alias variants per Pattern A.2 production',
        'discovery before apply.',
        '"""',
        'from __future__ import annotations',
        '',
        '',
        'LEAGUE_ALIAS_SOURCE = "bootstrap_league_coverage"',
        '',
        '',
        '# Format: (canonical_name, country_code, aliases_tuple, notes)',
        'LEAGUE_TEAMS_SEED: list[tuple[str, str, tuple[str, ...], str]] = [',
    ]
    # Relevant = INSERT + BACKFILL (skip SKIP).
    for c in bundle.classified:
        if c.classification not in ("INSERT", "BACKFILL"):
            continue
        if c.classification == "INSERT":
            canonical = "TODO_OPERATOR_FILL"
        else:
            canonical = c.sp_canonical or "TODO_OPERATOR_FILL"
        country_code = _country_to_iso3(c.fl.country) or "UNKNOWN"
        fl_alias = c.fl.fl_canonical.replace('"', '\\"')
        canonical_safe = canonical.replace('"', '\\"')
        note_parts = [f"PILOT {c.classification} from FL team_id="
                      f"{c.fl.team_id}"]
        if c.classification == "BACKFILL" and c.sp_team_id:
            note_parts.append(f"sp.teams.id={c.sp_team_id}")
        if c.name_count > 1:
            note_parts.append(
                f"NAME_COUNT={c.name_count} (ambiguous — spot-check)"
            )
        note = "; ".join(note_parts).replace('"', '\\"')
        lines.append(f'    ("{canonical_safe}", "{country_code}",')
        lines.append(f'     ("{fl_alias}",),')
        lines.append(f'     "{note}"),')
        lines.append('')
    lines.append(']')
    path.write_text("\n".join(lines))


def _country_to_iso3(country_name: str) -> str | None:
    if not country_name:
        return None
    m = {
        "germany": "DEU", "deutschland": "DEU",
        "spain": "ESP", "italy": "ITA", "israel": "ISR",
        "turkey": "TUR", "greece": "GRC", "russia": "RUS",
        "france": "FRA", "lithuania": "LTU", "monaco": "MCO",
        "uae": "UAE", "united arab emirates": "UAE",
        "serbia": "SRB", "montenegro": "MNE",
        "bosnia and herzegovina": "BIH", "bosnia-herzegovina": "BIH",
        "slovenia": "SVN", "croatia": "CRO", "austria": "AUT",
        "romania": "ROU", "mexico": "MEX", "andorra": "AND",
        "usa": "USA", "united states": "USA",
        "great britain": "GBR", "united kingdom": "GBR",
        "england": "GBR",
        "poland": "POL", "czech republic": "CZE", "czechia": "CZE",
        "netherlands": "NLD", "belgium": "BEL", "portugal": "PRT",
        "switzerland": "CHE", "argentina": "ARG", "brazil": "BRA",
        "australia": "AUS", "japan": "JPN", "south korea": "KOR",
        "china": "CHN", "philippines": "PHL", "puerto rico": "PRI",
    }
    return m.get(country_name.strip().lower())


def write_index(out_dir: Path, bundles: list[LeagueBundle],
                failed: list[tuple[str, str, str]],
                metadata: dict) -> tuple[Path, Path]:
    """Top-level summary across all leagues processed."""
    md_path = out_dir / "index.md"
    json_path = out_dir / "index.json"

    total_insert = sum(
        sum(1 for c in b.classified if c.classification == "INSERT")
        for b in bundles
    )
    total_backfill = sum(
        sum(1 for c in b.classified if c.classification == "BACKFILL")
        for b in bundles
    )
    total_skip = sum(
        sum(1 for c in b.classified if c.classification == "SKIP")
        for b in bundles
    )
    total_alias_link = sum(b.alias_link_count for b in bundles)
    total_merge_required = sum(b.merge_required_count for b in bundles)
    total_clean = sum(len(b.collision_report.clean) for b in bundles)
    total_colliding = sum(len(b.collision_report.colliding) for b in bundles)

    md_lines = [
        f"# FL universe batch crawl — index",
        "",
        f"sport_id: {metadata['sport_id']}",
        f"leagues attempted: {metadata['leagues_attempted']}",
        f"leagues succeeded: {len(bundles)}",
        f"leagues failed (no roster): {len(failed)}",
        f"FL calls: {metadata['fl_calls']}",
        f"team_data cache hits: {metadata['cache_hits']}",
        f"elapsed: {metadata['elapsed_sec']:.2f}s",
        "",
        "## Totals across all succeeded leagues",
        "",
        f"- INSERT: {total_insert}",
        f"- BACKFILL: {total_backfill}",
        f"- SKIP: {total_skip}",
        f"- Fragmentation ALIAS-LINK: {total_alias_link}",
        f"- Fragmentation MERGE-REQUIRED: {total_merge_required}",
        f"- Clean aliases (post-audit): {total_clean}",
        f"- Colliding aliases (dropped): {total_colliding}",
        "",
        "## Per-league summary",
        "",
        "| Country | League | Stage | Teams | INSERT | BACKFILL | "
        "SKIP | A-L | Merge | Clean | Coll | Elapsed |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for b in bundles:
        counts = Counter(c.classification for c in b.classified)
        md_lines.append(
            f"| {b.league_info.country} | {b.league_info.league_name} | "
            f"{b.league_info.chosen_stage_name} | {len(b.fl_teams)} | "
            f"{counts.get('INSERT', 0)} | {counts.get('BACKFILL', 0)} | "
            f"{counts.get('SKIP', 0)} | {b.alias_link_count} | "
            f"{b.merge_required_count} | {len(b.collision_report.clean)} | "
            f"{len(b.collision_report.colliding)} | {b.elapsed_sec:.1f}s |"
        )
    if failed:
        md_lines.append("")
        md_lines.append("## Failed leagues (no roster across any candidate stage)")
        md_lines.append("")
        md_lines.append("| Country | League | Reason |")
        md_lines.append("|---|---|---|")
        for c, l, reason in failed:
            md_lines.append(f"| {c} | {l} | {reason} |")

    md_path.write_text("\n".join(md_lines))

    json_path.write_text(json.dumps({
        "metadata": metadata,
        "totals": {
            "leagues_succeeded": len(bundles),
            "leagues_failed": len(failed),
            "insert": total_insert,
            "backfill": total_backfill,
            "skip": total_skip,
            "fragmentation_alias_link": total_alias_link,
            "fragmentation_merge_required": total_merge_required,
            "aliases_clean": total_clean,
            "aliases_colliding": total_colliding,
        },
        "leagues": [
            {
                "slug": b.league_info.slug,
                "country": b.league_info.country,
                "league_name": b.league_info.league_name,
                "stage_name": b.league_info.chosen_stage_name,
                "teams": len(b.fl_teams),
                "classification": {
                    cls: sum(1 for c in b.classified
                             if c.classification == cls)
                    for cls in ("INSERT", "BACKFILL", "SKIP")
                },
                "fragmentation": {
                    "alias_link": b.alias_link_count,
                    "merge_required": b.merge_required_count,
                },
                "aliases": {
                    "clean": len(b.collision_report.clean),
                    "colliding": len(b.collision_report.colliding),
                    "same_team": len(b.collision_report.same_team_already_present),
                },
                "elapsed_sec": b.elapsed_sec,
            }
            for b in bundles
        ],
        "failed": [
            {"country": c, "league_name": l, "reason": reason}
            for c, l, reason in failed
        ],
    }, indent=2, ensure_ascii=False))

    return md_path, json_path


# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────


async def run(args, log) -> int:
    if not os.environ.get("FLASHLIVE_API_KEY", "").strip():
        print("ERROR: FLASHLIVE_API_KEY not set", file=sys.stderr)
        return 1
    if not os.environ.get("DATABASE_URL", "").strip():
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        return 1
    if async_session is None:
        print("ERROR: DATABASE_URL did not produce a session",
              file=sys.stderr)
        return 1

    out_dir = Path(args.out_dir)
    (out_dir / "leagues").mkdir(parents=True, exist_ok=True)

    started = time.monotonic()

    sport_id_str = str(args.sport_id)
    sport_id_int = int(args.sport_id)

    # Track FL call count for index metadata. We approximate by counting
    # cache lookups vs misses on /v1/teams/data; tournaments_list +
    # standings calls are constant-bounded.
    fl_calls = 0  # incremented as we go; cheap counter

    # League enumeration.
    log.info("fl_batch.enumerate.start",
             sport_id=sport_id_str,
             league_hint=args.league_hint or "(any)",
             country_hint=args.country_hint or "(any)")
    sorted_groups = await enumerate_leagues(
        sport_id=sport_id_str,
        league_hint=args.league_hint,
        country_hint=args.country_hint,
        log=log,
    )
    fl_calls += 1
    if not sorted_groups:
        print(
            f"ERROR: /v1/tournaments/list returned no leagues for "
            f"sport_id={sport_id_str} (after hint filters: "
            f"league={args.league_hint!r}, country={args.country_hint!r}).",
            file=sys.stderr,
        )
        return 3

    capped_groups = (
        sorted_groups[:args.max_leagues]
        if args.max_leagues > 0 else sorted_groups
    )
    log.info("fl_batch.enumerate.complete",
             total_groups=len(sorted_groups),
             will_process=len(capped_groups))

    # Bulk-load sp.teams ONCE for the sport.
    async with async_session() as session:
        sp_teams_all, by_normalized = await load_sp_teams_for_sport(
            session=session, sport_id=sport_id_int, log=log,
        )

    team_data_cache: dict[str, FLTeam] = {}
    bundles: list[LeagueBundle] = []
    failed: list[tuple[str, str, str]] = []

    for idx, candidates in enumerate(capped_groups, start=1):
        log.info("fl_batch.processing",
                 idx=idx, of=len(capped_groups),
                 league=candidates[0].league_name,
                 country=candidates[0].country)
        # FL calls per league: 1 standings + N teams_data (cached
        # across leagues for shared team_ids). Approximate.
        cache_size_before = len(team_data_cache)
        async with async_session() as session:
            bundle = await process_league(
                candidates=candidates,
                sport_id=sport_id_int,
                sport_id_str=sport_id_str,
                session=session,
                sp_teams_all=sp_teams_all,
                by_normalized=by_normalized,
                team_data_cache=team_data_cache,
                log=log,
            )
        if bundle is None:
            failed.append((
                candidates[0].country,
                candidates[0].league_name,
                f"no roster across {len(candidates)} candidate stage(s)",
            ))
            # Still count FL calls (standings attempts × candidates).
            fl_calls += len(candidates)
            continue
        new_teams_fetched = len(team_data_cache) - cache_size_before
        # standings attempts before success: count fallback_path.
        fl_calls += len(bundle.league_info.fallback_path)
        fl_calls += new_teams_fetched
        write_league_bundle(out_dir, bundle)
        bundles.append(bundle)

    if not bundles and failed:
        print(
            f"ERROR: All {len(failed)} attempted leagues failed (no "
            "roster harvestable from any candidate stage). Check FL "
            "stage labels — Amendment #23: standings exists only for "
            "league-table stages.",
            file=sys.stderr,
        )
        return 4

    elapsed = time.monotonic() - started

    # Cache stats: each team_data_cache entry = 1 FL call.
    # cache_hits is the number of roster team_ids that hit the cache
    # (cross-league shared team_id), approximated.
    total_roster_lookups = sum(len(b.fl_teams) for b in bundles)
    cache_misses = len(team_data_cache)
    cache_hits = max(0, total_roster_lookups - cache_misses)

    metadata = {
        "sport_id": sport_id_int,
        "league_hint": args.league_hint,
        "country_hint": args.country_hint,
        "max_leagues": args.max_leagues,
        "leagues_attempted": len(capped_groups),
        "leagues_succeeded": len(bundles),
        "leagues_failed": len(failed),
        "fl_calls": fl_calls,
        "cache_hits": cache_hits,
        "elapsed_sec": elapsed,
    }
    md_path, json_path = write_index(
        out_dir=out_dir, bundles=bundles, failed=failed,
        metadata=metadata,
    )

    print(f"\nFL batch crawl complete in {elapsed:.1f}s.")
    print(f"  Leagues succeeded: {len(bundles)}")
    print(f"  Leagues failed:    {len(failed)}")
    print(f"  FL calls:          ~{fl_calls}")
    print(f"  cache hits:        {cache_hits}")
    print(f"\nOutputs:")
    print(f"  - {md_path}")
    print(f"  - {json_path}")
    print(f"  - {out_dir / 'leagues' / '<slug>'} per-league bundles")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="FL universe batch crawl — Component 3 orchestrator "
                    "(read-only; no DB writes; no auto-apply).",
    )
    parser.add_argument("--sport-id", type=int, default=3,
                        help="sp.sports.id to crawl. Default 3 = "
                             "Basketball (validate here before "
                             "generalizing per Day-37 brief).")
    parser.add_argument("--max-leagues", type=int, default=0,
                        help="Cap on leagues processed. 0 = no cap. "
                             "Set --max-leagues 1 with --league-hint "
                             "BBL + --country-hint Germany to reproduce "
                             "the BBL pilot through the full batch "
                             "pipeline (operator's first-validation "
                             "gate).")
    parser.add_argument("--league-hint", default="",
                        help="Substring/exact match against "
                             "LEAGUE_NAME. Empty = enumerate all.")
    parser.add_argument("--country-hint", default="",
                        help="Substring match against COUNTRY_NAME. "
                             "Empty = enumerate all countries.")
    parser.add_argument("--out-dir", default="./batch_output",
                        help="Top-level output directory.")
    args = parser.parse_args(argv)
    log = get_logger("fl_universe_batch")
    return asyncio.run(run(args, log))


if __name__ == "__main__":
    sys.exit(main())
