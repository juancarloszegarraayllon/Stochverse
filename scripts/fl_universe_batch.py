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

## Day-37 BBL gate findings (post-build refinement)

**Finding 1 — Full-pool fragmentation scan is authoritative.**
Manual harvester-surfaced fragmentation is INCOMPLETE. BBL gate run
exposed 11 pairs (7 ALIAS-LINK + 4 MERGE-REQUIRED) where manual hand
analysis had found only 5+2=7. The harvester didn't surface every
partner sp.teams row, so manual spot-checks missed real fragmentation.
The orchestrator's `find_all_fragmentation_pairs_pure` over the full
distinctive-token partner pool catches them all — including the
critical Vechta / Rasta Vechta misclassification the manual pass made
(Vechta WAS classified alias-link by hand; production verify showed
both sides have substantial fixtures → MERGE-REQUIRED). Had the
manual plan been applied, 8 fixtures of Rasta Vechta history would
have been silently corrupted. **Lesson: full-pool scan over
harvester-surfaced pairs.**

**Finding 2 — ALIAS-LINK is a two-part operation, not zero-migration.**
The dormant phantom still owns its `canonical_name`; an alias add on
the live winner collides with that ownership unless the phantom is
removed first. The two-part operation:
  1. DELETE the 0-fixture dormant phantom (releases its canonical-name
     claim; no fixture history to migrate — the rule's precondition
     guarantees 0 fixtures).
  2. Add the phantom's canonical as alias on the live winner team_id.

Implementation: the orchestrator collects ALIAS-LINK phantom team_ids
into `phantoms_to_release` and passes them as `excluded_team_ids` to
`audit_alias_collisions`, which treats those team_ids' rows as gone.
The per-league output now includes `phantom_release.md` for Part 1
and the existing `aliases_audited.md` for Part 2 (with "clean" relabeled
to make the post-phantom-release precondition explicit).

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

## Curated-target mode (--leagues-file)

Path B durable artifact. The 525-group --enumerate-only recon
confirmed ~475 of FL's basketball groups are non-senior-club noise
(national teams, youth, women's, 3x3, cups). Blind enumeration is
wrong; the curated file is the right answer + a reusable annual-
refresh target list.

File format — one entry per line:

    # Top European basketball leagues — annual refresh batch 1
    Germany|BBL
    Spain|Liga ACB
    Italy|LBA
    France|Pro A LNB
    # Greece (commented out until next season starts)
    # Greece|Basket League

Matching: case-insensitive exact match on (country, league_name)
against the same grouping /v1/tournaments/list produces.

Behavior:
  - Takes PRECEDENCE over --league-hint / --country-hint (logged
    warning if both given; hints ignored).
  - --max-leagues still works as a safety cap on top
    (e.g. --leagues-file X --max-leagues 3 for a 3-league smoke).
  - Unmatched entries logged as WARNING and surfaced in
    index.md / index.json so the operator sees what didn't crawl.
  - Batch continues on individual unmatched entries — a typo or
    off-season league shouldn't kill the run.
  - If ZERO entries match, hard error (likely typo on every line).

Read-only. No DB writes.

    DATABASE_URL=<url> FLASHLIVE_API_KEY=<key> \\
      python scripts/fl_universe_batch.py \\
        --sport-id 3 \\
        --leagues-file ./leagues_eu_batch1.txt \\
        --max-leagues 3 \\
        --out-dir ./batch_eu_smoke/

## Reconnaissance mode (--enumerate-only)

Before any full crawl, dump the complete FL catalog for a sport to
scope filtering strategy. Per Day-N+1 5-league staging finding,
blind enumeration front-loads national-team / youth / women's /
qualifier tournaments (AfroBasket, AfroCan, AfroBasket Women,
African Championship U18, etc.) rather than senior club leagues.

    FLASHLIVE_API_KEY=<key> python scripts/fl_universe_batch.py \\
      --sport-id 3 \\
      --enumerate-only \\
      --out-dir ./recon_basketball/

Writes enumeration.md + enumeration.json. No standings or team_data
calls. DATABASE_URL not required in this mode. ~1 FL call.

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
    # Day-37 BBL gate Finding 2: ALIAS-LINK is a two-part operation.
    # phantoms_to_release contains team_ids that must be DELETEd from
    # sp.teams BEFORE the alias proposals are applied. Each phantom
    # corresponds to an ALIAS-LINK verdict's dormant_phantom_team_id.
    phantoms_to_release: list[str] = field(default_factory=list)
    elapsed_sec: float = 0.0


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

    # ── Phantom-release plan (Day-37 BBL gate Finding 2) ──
    # ALIAS-LINK is a two-part operation: (1) DELETE 0-fixture dormant
    # phantom (releases its claim on the canonical_name); (2) add the
    # phantom's canonical as alias on the live winner. The phantom is
    # 0 fixtures by definition (the Day-37 rule's precondition) so
    # there is no fixture history to migrate.
    # Forward the phantom team_ids to the collision audit via
    # excluded_team_ids — emitting an alias for a name owned only by
    # a soon-to-be-released phantom is safe, not colliding.
    phantoms_to_release: list[str] = [
        v.dormant_phantom_team_id for v in verdicts
        if v.classification == "ALIAS-LINK" and v.dormant_phantom_team_id
    ]

    # Collision audit.
    report = await audit_alias_collisions(
        session=session,
        proposed_aliases=proposals,
        sport_id=sport_id,
        excluded_team_ids=phantoms_to_release,
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
        phantoms_to_release=phantoms_to_release,
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
    }, indent=2, ensure_ascii=False), encoding="utf-8")

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
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    # classification.md
    _write_classification_md(league_dir / "classification.md", bundle)

    # fragmentation.md
    _write_fragmentation_md(league_dir / "fragmentation.md", bundle)

    # aliases_audited.md
    _write_aliases_md(league_dir / "aliases_audited.md", bundle)

    # phantom_release.md (Day-37 BBL gate Finding 2 — two-part operation)
    _write_phantom_release_md(league_dir / "phantom_release.md", bundle)

    # seed.py.draft
    _write_seed_draft(league_dir / "seed.py.draft", bundle)

    return league_dir


def _write_phantom_release_md(path: Path, bundle: LeagueBundle) -> None:
    """Per Day-37 BBL gate Finding 2: ALIAS-LINK is a two-part operation.
    Part 1 (this file) — DELETE 0-fixture dormant phantoms whose claim
    on the canonical_name must be released before Part 2 can land.
    Part 2 — aliases_audited.md's clean set.

    The collision audit already excluded these phantom team_ids when
    classifying proposals, so the aliases shown as "clean" become valid
    AFTER phantom-release executes.
    """
    lines = [
        f"# {bundle.league_info.league_name} — Phantom-release plan",
        "",
        "## Day-37 BBL gate Finding 2 — two-part ALIAS-LINK operation",
        "",
        "ALIAS-LINK is not a zero-operation. The dormant phantom",
        "still owns its canonical_name; an alias add on the winner",
        "collides with that ownership unless the phantom is removed",
        "first.",
        "",
        "Two-part operation:",
        "  1. (this file) DELETE the 0-fixture dormant phantoms",
        "     listed below. Each is 0 fixtures by definition of the",
        "     Day-37 ALIAS-LINK rule — no fixture history to migrate.",
        "  2. (aliases_audited.md) Add the phantom's canonical as",
        "     alias on the live winner team_id. The collision audit",
        "     for this league EXCLUDED the phantom team_ids before",
        "     classifying — so the 'clean' alias set is valid post-",
        "     phantom-release.",
        "",
    ]
    if not bundle.phantoms_to_release:
        lines.append("No phantoms to release for this league "
                     "(no ALIAS-LINK verdicts).")
        path.write_text("\n".join(lines), encoding="utf-8")
        return

    # Build a phantom_id → verdict lookup for context.
    by_phantom = {
        v.dormant_phantom_team_id: v
        for v in bundle.fragmentation_verdicts
        if v.classification == "ALIAS-LINK" and v.dormant_phantom_team_id
    }
    lines.append(f"## {len(bundle.phantoms_to_release)} phantom(s) to release")
    lines.append("")
    lines.append(
        "| Phantom team_id | Phantom canonical | "
        "Fixtures (must be 0) | Winner team_id | Winner canonical | "
        "Alias to add post-release |"
    )
    lines.append("|---|---|---:|---|---|---|")
    for phantom_id in bundle.phantoms_to_release:
        v = by_phantom.get(phantom_id)
        if not v:
            lines.append(
                f"| `{phantom_id}` | (verdict-not-found) | ? | "
                "? | ? | ? |"
            )
            continue
        # Identify which side is phantom vs winner.
        if v.pair.anchor.team_id == phantom_id:
            phantom_canon = v.pair.anchor.canonical_name
            phantom_fc = v.anchor_fixture_count
            winner_canon = v.pair.partner.canonical_name
        else:
            phantom_canon = v.pair.partner.canonical_name
            phantom_fc = v.partner_fixture_count
            winner_canon = v.pair.anchor.canonical_name
        lines.append(
            f"| `{phantom_id}` | {phantom_canon} | {phantom_fc} | "
            f"`{v.canonical_winner_team_id}` | {winner_canon} | "
            f"{v.proposed_alias_form} |"
        )
    lines.append("")
    lines.append("## Suggested ordered execution")
    lines.append("")
    lines.append("```sql")
    lines.append("-- Part 1: release the dormant phantoms (0 fixtures by")
    lines.append("--         definition — no FK-cascade migration needed)")
    for phantom_id in bundle.phantoms_to_release:
        lines.append(f"DELETE FROM sp.teams WHERE id = '{phantom_id}';")
    lines.append("")
    lines.append("-- Part 2: apply the alias-link additions per the")
    lines.append("--         clean set in aliases_audited.md (insert")
    lines.append("--         each as sp.team_aliases row on the")
    lines.append("--         canonical winner team_id).")
    lines.append("```")
    lines.append("")
    lines.append("**Verification (after both parts):**")
    lines.append("- Re-run amendment #22 collision audit; expect 0 collisions.")
    lines.append("- Confirm no `sp.fixtures` rows referenced the released")
    lines.append("  phantoms (each was 0 fixtures pre-release; the rule's")
    lines.append("  precondition guarantees this).")
    path.write_text("\n".join(lines), encoding="utf-8")


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
    path.write_text("\n".join(lines), encoding="utf-8")


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
        path.write_text("\n".join(lines), encoding="utf-8")
        return

    alias_links = [
        v for v in bundle.fragmentation_verdicts
        if v.classification == "ALIAS-LINK"
    ]
    if alias_links:
        lines.append("## ALIAS-LINK candidates (Day-37 LOCKED rule)")
        lines.append("")
        lines.append(
            "**Two-part operation (Day-37 BBL gate Finding 2):** each "
            "ALIAS-LINK below requires (1) DELETE the dormant phantom "
            "team_id (releases its claim on the canonical_name), then "
            "(2) add the phantom's canonical as alias on the winner. "
            "See `phantom_release.md` for the consolidated Part-1 plan "
            "and `aliases_audited.md` for the Part-2 alias set "
            "(collision-audited with phantoms excluded — clean post-release)."
        )
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
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_aliases_md(path: Path, bundle: LeagueBundle) -> None:
    r = bundle.collision_report
    has_phantoms = bool(bundle.phantoms_to_release)
    clean_label = (
        "Clean post-phantom-release (safe to emit AFTER Part 1)"
        if has_phantoms
        else "Clean (safe to emit)"
    )
    lines = [
        f"# {bundle.league_info.league_name} — Alias proposals (collision-audited)",
        "",
    ]
    if has_phantoms:
        lines.append(
            f"**Day-37 BBL gate Finding 2 — two-part operation.** This "
            f"audit excluded {len(bundle.phantoms_to_release)} dormant "
            "phantom team_id(s) per the ALIAS-LINK plan in "
            "`phantom_release.md`. The 'clean' set below is valid AFTER "
            "Part 1 (phantom DELETEs) executes. Apply order is mandatory."
        )
        lines.append("")
    lines.append(f"- Total proposed: {len(bundle.proposed_aliases)}")
    lines.append(f"- {clean_label}: {len(r.clean)}")
    lines.append(f"- Same team already present: {len(r.same_team_already_present)}")
    lines.append(f"- Colliding (dropped): {len(r.colliding)}")
    lines.append("")
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
    path.write_text("\n".join(lines), encoding="utf-8")


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
    path.write_text("\n".join(lines), encoding="utf-8")


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


# ──────────────────────────────────────────────────────────────────────
# leagues-file (curated explicit-target list)
# ──────────────────────────────────────────────────────────────────────


def load_leagues_file(path: Path) -> list[tuple[str, str]]:
    """Parse a leagues-file: one entry per line as 'Country|League_Name'
    (FL's exact COUNTRY_NAME / LEAGUE_NAME labels). Lines starting with
    `#` are comments; blank lines ignored. Duplicates silently de-duped.

    Returns list of (country, league_name) tuples in FILE ORDER —
    matched groups are processed in the order they appear.

    Raises ValueError on malformed lines (missing `|` separator or
    empty country/league).
    """
    entries: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    # utf-8-sig tolerates a BOM at the head of file (some editors
    # insert one) so line 1 doesn't break with a phantom 0xEF byte.
    for line_no, raw in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1,
    ):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "|" not in line:
            raise ValueError(
                f"leagues-file line {line_no}: missing '|' separator: "
                f"{line!r}"
            )
        parts = line.split("|", 1)
        country = parts[0].strip()
        league = parts[1].strip()
        if not country or not league:
            raise ValueError(
                f"leagues-file line {line_no}: empty country or "
                f"league: {line!r}"
            )
        key = (country.lower(), league.lower())
        if key in seen:
            continue
        seen.add(key)
        entries.append((country, league))
    return entries


def filter_groups_by_leagues_file(
    sorted_groups: list[list[LeagueCandidate]],
    entries: list[tuple[str, str]],
    log,
) -> tuple[list[list[LeagueCandidate]], list[tuple[str, str]]]:
    """Match curated entries against the FL grouping. Case-insensitive
    exact match on (country, league_name).

    Returns (matched_groups_in_file_order, unmatched_entries). Each
    unmatched entry is logged as a warning so the operator sees the
    miss in real time without aborting the batch.
    """
    # Build (country_lower, league_lower) → group lookup.
    # First-seen wins (FL shouldn't have dupes, but defensive).
    lookup: dict[tuple[str, str], list[LeagueCandidate]] = {}
    for group in sorted_groups:
        if not group:
            continue
        first = group[0]
        key = (first.country.lower(), first.league_name.lower())
        if key not in lookup:
            lookup[key] = group

    matched: list[list[LeagueCandidate]] = []
    unmatched: list[tuple[str, str]] = []
    for country, league in entries:
        key = (country.lower(), league.lower())
        group = lookup.get(key)
        if group is None:
            log.warning(
                "fl_batch.leagues_file.not_found",
                country=country,
                league=league,
            )
            unmatched.append((country, league))
            continue
        matched.append(group)
    log.info("fl_batch.leagues_file.matched",
             entries_in=len(entries),
             matched=len(matched),
             unmatched=len(unmatched))
    return matched, unmatched


# ──────────────────────────────────────────────────────────────────────
# Enumeration-only mode (--enumerate-only)
# ──────────────────────────────────────────────────────────────────────


def write_enumeration(
    out_dir: Path,
    groups: list[list[LeagueCandidate]],
    metadata: dict,
) -> tuple[Path, Path]:
    """Reconnaissance output: full catalog of FL's enumeration for a
    sport_id, with NO standings/team_data calls.

    Per Day-N+1 5-league staging finding: blind enumeration front-
    loads national-team / youth / women's / qualifier tournaments
    (AfroBasket, AfroCan, AfroBasket Women, African Championship U18).
    Operator filters from this catalog before running the actual
    crawl.

    Sort: country (asc, "Unknown" last), then league_name (asc).
    """
    md_path = out_dir / "enumeration.md"
    json_path = out_dir / "enumeration.json"

    # Normalize each group into a row record. Stages within a group
    # are de-duplicated by (stage_id, stage_name) to keep the row
    # compact.
    rows: list[dict] = []
    for group in groups:
        if not group:
            continue
        first = group[0]
        seen_stage_ids: set[str] = set()
        stages: list[dict] = []
        for c in group:
            if c.stage_id in seen_stage_ids:
                continue
            seen_stage_ids.add(c.stage_id)
            stages.append({
                "stage_id": c.stage_id,
                "stage_name": c.stage_name,
            })
        rows.append({
            "country": first.country or "Unknown",
            "league_name": first.league_name,
            "season_id": first.season_id,
            "stage_count": len(stages),
            "stages": stages,
        })

    def _country_sort_key(c: str) -> tuple[int, str]:
        # "Unknown" last; otherwise alphabetical case-insensitive.
        if not c or c.lower() == "unknown":
            return (1, "")
        return (0, c.lower())

    rows.sort(
        key=lambda r: (_country_sort_key(r["country"]),
                       (r["league_name"] or "").lower()),
    )

    md_lines: list[str] = []
    md_lines.append(
        f"# FL universe enumeration — sport_id={metadata['sport_id']}"
    )
    md_lines.append("")
    md_lines.append(
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}"
    )
    md_lines.append(
        f"Mode: --enumerate-only (no standings/team_data fetches)"
    )
    md_lines.append(
        f"FL calls: {metadata['fl_call_count']}"
    )
    md_lines.append(
        f"Total league groups: {len(rows)}"
    )
    if metadata.get("league_hint"):
        md_lines.append(
            f"League hint filter: `{metadata['league_hint']}`"
        )
    if metadata.get("country_hint"):
        md_lines.append(
            f"Country hint filter: `{metadata['country_hint']}`"
        )
    md_lines.append(f"Elapsed: {metadata['elapsed_sec']:.2f}s")
    md_lines.append("")
    md_lines.append("## Country distribution")
    md_lines.append("")
    country_counts = Counter(r["country"] for r in rows)
    md_lines.append("| Country | League groups |")
    md_lines.append("|---|---:|")
    for country, count in sorted(
        country_counts.items(),
        key=lambda kv: (_country_sort_key(kv[0]), kv[0]),
    ):
        md_lines.append(f"| {country} | {count} |")
    md_lines.append("")
    md_lines.append(
        "## Per-group catalog (sorted by country, then league name)"
    )
    md_lines.append("")
    md_lines.append(
        "| Country | League | Stages | Stage names |"
    )
    md_lines.append("|---|---|---:|---|")
    for r in rows:
        stage_names = ", ".join(s["stage_name"] or "(unnamed)"
                                for s in r["stages"])
        # Escape pipe chars in names so the table renders.
        league_safe = (r["league_name"] or "").replace("|", "\\|")
        stage_safe = stage_names.replace("|", "\\|")
        md_lines.append(
            f"| {r['country']} | {league_safe} | {r['stage_count']} | "
            f"{stage_safe} |"
        )
    md_lines.append("")
    md_lines.append(
        "## Filter strategy guidance (operator)"
    )
    md_lines.append("")
    md_lines.append(
        "Recommended: review this catalog and identify the senior-"
        "club-league subset worth bootstrapping. Common noise patterns "
        "to filter:"
    )
    md_lines.append(
        "  - National-team tournaments (AfroBasket, EuroBasket, "
        "AmeriCup, FIBA Asia Cup, etc.)"
    )
    md_lines.append(
        "  - Youth competitions (U18, U19, U20, Junior, Espoirs)"
    )
    md_lines.append(
        "  - Women's competitions (often labeled with 'Women', 'W', "
        "'Femenina', 'Damen', 'Femminile')"
    )
    md_lines.append(
        "  - Qualification rounds (already de-prioritized by "
        "stage-rank, but the league-group itself may also be a "
        "qualifier-only tournament)"
    )
    md_lines.append(
        "  - International cups vs domestic top tiers (different scope)"
    )
    md_lines.append("")
    md_lines.append(
        "Once the subset is identified, re-run without --enumerate-"
        "only, optionally with --league-hint / --country-hint to "
        "narrow the crawl."
    )

    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    json_path.write_text(json.dumps(
        {"metadata": metadata, "groups": rows},
        indent=2, ensure_ascii=False,
    ), encoding="utf-8")
    return md_path, json_path


def write_index(out_dir: Path, bundles: list[LeagueBundle],
                failed: list[tuple[str, str, str]],
                metadata: dict,
                unmatched_leagues_file: list[tuple[str, str]] | None = None,
                ) -> tuple[Path, Path]:
    """Top-level summary across all leagues processed.

    `unmatched_leagues_file` (optional): entries from --leagues-file
    that didn't match any FL enumeration group. Surfaced in both
    index.md and index.json so the operator sees what didn't crawl.
    """
    unmatched_leagues_file = unmatched_leagues_file or []
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
    total_phantoms_to_release = sum(
        len(b.phantoms_to_release) for b in bundles
    )

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
        f"- Phantoms to release (Part 1 of ALIAS-LINK): "
        f"{total_phantoms_to_release}",
        f"- Clean aliases (post-audit, post-phantom-release): "
        f"{total_clean}",
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

    if unmatched_leagues_file:
        md_lines.append("")
        md_lines.append(
            "## Unmatched leagues-file entries (WARNING — did NOT crawl)"
        )
        md_lines.append("")
        md_lines.append(
            "These entries appeared in `--leagues-file` but did not match "
            "any group in FL's `/v1/tournaments/list` enumeration "
            "(case-insensitive exact match on country + league_name). "
            "Possible causes:"
        )
        md_lines.append(
            "- Typo in the file vs FL's exact COUNTRY_NAME / LEAGUE_NAME "
            "labels. Re-verify against `--enumerate-only` output."
        )
        md_lines.append(
            "- League is off-season / not currently in FL's enumeration "
            "for this sport_id."
        )
        md_lines.append(
            "- FL relabeled the league since the curated file was written. "
            "Update the file to match current FL labels."
        )
        md_lines.append("")
        md_lines.append("| Country (file) | League (file) |")
        md_lines.append("|---|---|")
        for country, league in unmatched_leagues_file:
            md_lines.append(f"| {country} | {league} |")

    md_path.write_text("\n".join(md_lines), encoding="utf-8")

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
            "phantoms_to_release": total_phantoms_to_release,
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
                "phantoms_to_release": list(b.phantoms_to_release),
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
        "unmatched_leagues_file": [
            {"country": country, "league_name": league}
            for country, league in unmatched_leagues_file
        ],
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    return md_path, json_path


# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────


async def run(args, log) -> int:
    if not os.environ.get("FLASHLIVE_API_KEY", "").strip():
        print("ERROR: FLASHLIVE_API_KEY not set", file=sys.stderr)
        return 1
    # DATABASE_URL is NOT required in --enumerate-only mode
    # (no sp.teams lookups, no fixture queries).
    if not args.enumerate_only:
        if not os.environ.get("DATABASE_URL", "").strip():
            print("ERROR: DATABASE_URL not set", file=sys.stderr)
            return 1
        if async_session is None:
            print("ERROR: DATABASE_URL did not produce a session",
                  file=sys.stderr)
            return 1

    out_dir = Path(args.out_dir)
    if args.enumerate_only:
        out_dir.mkdir(parents=True, exist_ok=True)
    else:
        (out_dir / "leagues").mkdir(parents=True, exist_ok=True)

    started = time.monotonic()

    sport_id_str = str(args.sport_id)
    sport_id_int = int(args.sport_id)

    # ── Parse --leagues-file early (validate before any FL call) ──
    leagues_file_entries: list[tuple[str, str]] | None = None
    if args.leagues_file:
        try:
            leagues_file_entries = load_leagues_file(Path(args.leagues_file))
        except (ValueError, FileNotFoundError, OSError) as exc:
            print(f"ERROR: leagues-file invalid: {exc}",
                  file=sys.stderr)
            return 2
        log.info("fl_batch.leagues_file.loaded",
                 path=args.leagues_file,
                 entry_count=len(leagues_file_entries))
        if not leagues_file_entries:
            print(
                f"ERROR: leagues-file {args.leagues_file!r} parsed "
                "OK but produced zero entries (only comments/blank "
                "lines?).",
                file=sys.stderr,
            )
            return 2
        # Hint conflict: warn + ignore. The curated file IS the
        # restriction.
        if args.league_hint or args.country_hint:
            log.warning(
                "fl_batch.leagues_file.hints_ignored",
                league_hint=args.league_hint,
                country_hint=args.country_hint,
                note="--leagues-file takes precedence; hints ignored",
            )
        effective_league_hint = ""
        effective_country_hint = ""
    else:
        effective_league_hint = args.league_hint
        effective_country_hint = args.country_hint

    # Track FL call count for index metadata. We approximate by counting
    # cache lookups vs misses on /v1/teams/data; tournaments_list +
    # standings calls are constant-bounded.
    fl_calls = 0  # incremented as we go; cheap counter

    # League enumeration.
    log.info("fl_batch.enumerate.start",
             sport_id=sport_id_str,
             league_hint=effective_league_hint or "(any)",
             country_hint=effective_country_hint or "(any)",
             leagues_file=args.leagues_file or "(none)")
    sorted_groups = await enumerate_leagues(
        sport_id=sport_id_str,
        league_hint=effective_league_hint,
        country_hint=effective_country_hint,
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

    # ── --enumerate-only early return ────────────────────────────
    # Reconnaissance mode: emit the full catalog and stop. No
    # standings / team_data calls; no DB lookups.
    if args.enumerate_only:
        elapsed = time.monotonic() - started
        meta = {
            "sport_id": sport_id_int,
            "league_hint": args.league_hint,
            "country_hint": args.country_hint,
            "fl_call_count": fl_calls,
            "total_groups": len(sorted_groups),
            "elapsed_sec": elapsed,
        }
        md_path, json_path = write_enumeration(
            out_dir=out_dir, groups=sorted_groups, metadata=meta,
        )
        print(f"\nFL enumeration-only complete in {elapsed:.1f}s.")
        print(f"  sport_id: {sport_id_int}")
        print(f"  league groups: {len(sorted_groups)}")
        print(f"  FL calls: {fl_calls}")
        print(f"\nOutputs:")
        print(f"  - {md_path}")
        print(f"  - {json_path}")
        return 0

    # ── --leagues-file filter (curated explicit-target list) ──
    unmatched_leagues_file: list[tuple[str, str]] = []
    if leagues_file_entries is not None:
        filtered_groups, unmatched_leagues_file = (
            filter_groups_by_leagues_file(
                sorted_groups=sorted_groups,
                entries=leagues_file_entries,
                log=log,
            )
        )
        if not filtered_groups and unmatched_leagues_file:
            print(
                f"ERROR: leagues-file produced 0 matches against FL "
                f"enumeration ({len(unmatched_leagues_file)} entries, "
                "none matched). Re-run with --enumerate-only to verify "
                "exact FL labels.",
                file=sys.stderr,
            )
            return 3
        sorted_groups = filtered_groups

    capped_groups = (
        sorted_groups[:args.max_leagues]
        if args.max_leagues > 0 else sorted_groups
    )
    log.info("fl_batch.enumerate.complete",
             total_groups=len(sorted_groups),
             will_process=len(capped_groups),
             unmatched_leagues_file=len(unmatched_leagues_file))

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
        "leagues_file": args.leagues_file or None,
        "leagues_file_entries": (
            len(leagues_file_entries)
            if leagues_file_entries is not None else None
        ),
        "leagues_file_unmatched": len(unmatched_leagues_file),
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
        unmatched_leagues_file=unmatched_leagues_file,
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
    parser.add_argument("--leagues-file", default="",
                        help="Path to a curated leagues file. Format: "
                             "one entry per line as 'Country|League_Name' "
                             "(FL's exact COUNTRY_NAME / LEAGUE_NAME "
                             "labels from --enumerate-only output). "
                             "Lines starting with '#' are comments; "
                             "blank lines ignored. Case-insensitive "
                             "exact match. TAKES PRECEDENCE over "
                             "--league-hint / --country-hint (hints "
                             "ignored when this is set). --max-leagues "
                             "still works as a safety cap on top. "
                             "Unmatched entries logged as WARNING and "
                             "surfaced in index.md; batch continues.")
    parser.add_argument("--enumerate-only", action="store_true",
                        help="Reconnaissance mode: emit a catalog of "
                             "all leagues FL exposes for --sport-id, "
                             "WITHOUT calling /v1/tournaments/standings "
                             "or /v1/teams/data. Writes enumeration.md "
                             "(human-readable) + enumeration.json "
                             "(structured) to --out-dir, then stops. "
                             "Use to scope filtering strategy before "
                             "running the actual crawl. Read-only; ~1 "
                             "FL call. DATABASE_URL not required in "
                             "this mode.")
    args = parser.parse_args(argv)
    log = get_logger("fl_universe_batch")
    return asyncio.run(run(args, log))


if __name__ == "__main__":
    sys.exit(main())
