"""FL-driven roster bootstrap PILOT — German BBL only.

Phase 2D.5-A post-mortem follow-up. Tests whether FL's own team-master-
data endpoints can replace the manual Wikipedia-paste step of the
bootstrap methodology. Canonical+country layer ONLY — alias variants
still come from Pattern A.2 production discovery.

## Pipeline

  1. Walk `/v1/sports/list` → resolve Basketball sport_id (3)
  2. Walk `/v1/tournaments/list?sport_id=3` → find LEAGUE_NAME matching
     `--league-hint` (default 'Bundesliga'), filtered to Germany
  3. Walk `/v1/tournaments/standings?tournament_stage_id=Y&
     tournament_season_id=Z&standing_type=overall` → roster team_ids
  4. Per team_id, call `/v1/teams/data?sport_id=3&team_id=T` →
     canonical name + country
  5. Cross-reference against `sp.teams` (sport_id=3):
       - SKIP   — normalized_name match AND country_code already set
       - BACKFILL — normalized_name match AND country_code IS NULL
       - INSERT — no normalized_name match (new team)
  6. Write outputs (NO DB writes; PILOT is observation-only):
       - <out_dir>/fl_bbl_intermediate.json — raw FL crawl data
       - <out_dir>/fl_bbl_classification.md — INSERT/BACKFILL/SKIP table
       - <out_dir>/bbl_seed.py.draft — draft manifest in seed-file shape

## Usage

    DATABASE_URL=<url> FLASHLIVE_API_KEY=<key> \\
        python scripts/fl_universe_seed.py --out-dir ./pilot_output/

    # Override league hint if 'Bundesliga' doesn't match:
    python scripts/fl_universe_seed.py --league-hint 'BBL' --out-dir ...

    # Skip the sp.teams cross-reference (FL crawl only):
    python scripts/fl_universe_seed.py --no-classify --out-dir ...

## Exit codes

  0 — success (all outputs written)
  1 — FLASHLIVE_API_KEY missing or DATABASE_URL missing (when needed)
  2 — bad CLI args
  3 — FL stage_id resolution failed (no BBL match in /v1/tournaments/list)
  4 — FL standings returned no teams (stage_id valid but empty roster)

## Pilot scope

Explicitly NOT in this pilot:
  - Alias variants (sponsor / asterisk / diacritic / transliteration)
  - Amendment #22 collision audit automation
  - Cross-sport / cross-league generalization
  - Production writes

The operator reviews the draft manifest + classification, layers in
aliases per existing Pattern A.2 methodology, runs the standard
bootstrap workflow, and F7-compares against the manual benchmark.

## Build-time notes

Mirrors `scripts/bootstrap_heba.py` patterns (env vars, async_session,
normalize_name). Reuses `flashlive_feed._fl_get` (the existing
RapidAPI-throttled GET wrapper) so we inherit Mega-tier rate-limit
behavior + provider_api_call instrumentation.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from db import async_session  # noqa: E402
from observability import get_logger  # noqa: E402
from resolver._normalize import normalize_name  # noqa: E402


BASKETBALL_SPORT_ID = "3"  # per enrichment/stage_discovery.py:21


# ──────────────────────────────────────────────────────────────────────
# Data shapes
# ──────────────────────────────────────────────────────────────────────


@dataclass
class FLTeam:
    """One FL team master record after `/v1/teams/data` resolution."""
    team_id: str
    fl_name: str
    country: str
    raw: dict = field(default_factory=dict)

    @property
    def normalized(self) -> str:
        return normalize_name(self.fl_name)


@dataclass
class ClassifiedTeam:
    fl: FLTeam
    classification: str  # 'INSERT' | 'BACKFILL' | 'SKIP'
    sp_team_id: str | None = None
    sp_canonical: str | None = None
    sp_country_code: str | None = None
    notes: str = ""


# ──────────────────────────────────────────────────────────────────────
# FL crawl
# ──────────────────────────────────────────────────────────────────────


async def discover_bbl_stage(
    league_hint: str,
    country_hint: str,
    log,
) -> dict:
    """Walk /v1/tournaments/list to find the BBL stage + season IDs.

    Returns {'stage_id', 'season_id', 'stage_name', 'league_name',
    'country'} or empty dict if no match.

    Prefers exact LEAGUE_NAME match within Germany. Falls back to
    substring match. Logs every candidate considered so the operator
    can refine --league-hint if pilot defaults miss.
    """
    from flashlive_feed import _fl_get

    resp = await _fl_get(
        "/v1/tournaments/list",
        {"sport_id": BASKETBALL_SPORT_ID, "locale": "en_INT"},
    )
    if not isinstance(resp, dict):
        log.error("fl_universe.tournaments_list.empty", resp_type=type(resp).__name__)
        return {}

    data = resp.get("DATA") or []
    if not isinstance(data, list):
        log.error("fl_universe.tournaments_list.bad_shape")
        return {}

    hint_lower = league_hint.lower().strip()
    country_lower = country_hint.lower().strip()

    candidates: list[dict] = []
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
        if country_hint and country_lower not in country_name.lower():
            continue

        stages = entry.get("STAGES") or []
        if not isinstance(stages, list):
            continue

        league_lower = league_name.lower()
        if hint_lower == league_lower:
            league_score = 100
        elif hint_lower in league_lower:
            league_score = 80
        elif league_lower in hint_lower:
            league_score = 60
        else:
            continue

        for stage in stages:
            if not isinstance(stage, dict):
                continue
            stage_id = stage.get("STAGE_ID")
            stage_name = stage.get("STAGE_NAME") or ""
            if not stage_id:
                continue
            candidates.append({
                "stage_id": stage_id,
                "season_id": season_id,
                "stage_name": stage_name,
                "league_name": league_name,
                "country": country_name,
                "_league_score": league_score,
                "_stage_score": _stage_rank(stage_name),
            })

    log.info(
        "fl_universe.tournaments_list.candidates",
        count=len(candidates),
        league_hint=league_hint,
        country_hint=country_hint,
    )
    for c in candidates:
        log.info(
            "fl_universe.candidate",
            league=c["league_name"],
            country=c["country"],
            stage=c["stage_name"],
            stage_id=c["stage_id"],
            season_id=c["season_id"],
            score=(c["_league_score"], c["_stage_score"]),
        )

    if not candidates:
        return {}

    # Prefer highest league score, then highest stage score (regular
    # season > playoff > qualification etc.)
    candidates.sort(
        key=lambda c: (c["_league_score"], c["_stage_score"]),
        reverse=True,
    )
    best = candidates[0]
    return {
        "stage_id": best["stage_id"],
        "season_id": best["season_id"],
        "stage_name": best["stage_name"],
        "league_name": best["league_name"],
        "country": best["country"],
    }


def _stage_rank(stage_name: str) -> int:
    """Lifted from enrichment/stage_discovery.py — prefer regular-
    season / league-stage stages over playoffs / qualification when
    selecting the canonical roster source."""
    s = (stage_name or "").lower()
    if any(kw in s for kw in ("group", "league phase", "league stage",
                              "regular season", "regular_season")):
        return 50
    if "playoff" in s or "play-off" in s or "play off" in s:
        return 35
    if any(kw in s for kw in ("final", "knockout", "round of")):
        return 30
    if "qualif" in s or "preliminary" in s or "qualifying" in s:
        return 10
    return 25


async def fetch_bbl_roster(stage_id: str, season_id: str, log) -> list[dict]:
    """Pull /v1/tournaments/standings → list of team participants.

    Returns list of dicts with at least 'team_id' and 'team_name'.
    Falls back gracefully if FL's response shape varies (logs each
    skip).
    """
    from flashlive_feed import _fl_get

    params = {
        "tournament_stage_id": stage_id,
        "standing_type": "overall",
    }
    if season_id:
        params["tournament_season_id"] = season_id

    resp = await _fl_get("/v1/tournaments/standings", params)
    if not isinstance(resp, dict):
        log.error("fl_universe.standings.empty", resp_type=type(resp).__name__)
        return []

    teams: list[dict] = []
    data = resp.get("DATA") or []
    if not isinstance(data, list):
        log.error("fl_universe.standings.bad_shape", data_type=type(data).__name__)
        return []

    # FL standings shape (observed):
    #   DATA: [{ROWS: [{TEAM_ID, TEAM_NAME, ...}, ...], ...}, ...]
    # Defensive parser — walk all nested arrays looking for TEAM_ID.
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

    log.info(
        "fl_universe.standings.parsed",
        team_count=len(teams),
        stage_id=stage_id,
        season_id=season_id,
    )
    return teams


async def fetch_team_detail(team_id: str, log) -> FLTeam | None:
    """Pull /v1/teams/data per team_id → canonical name + country."""
    from flashlive_feed import _fl_get

    resp = await _fl_get(
        "/v1/teams/data",
        {"sport_id": BASKETBALL_SPORT_ID, "team_id": team_id},
    )
    if not isinstance(resp, dict):
        log.warning("fl_universe.teams_data.empty", team_id=team_id)
        return None

    data = resp.get("DATA")
    if not isinstance(data, dict):
        # Some FL responses wrap the team dict directly; try a few shapes.
        if isinstance(resp.get("TEAM"), dict):
            data = resp["TEAM"]
        else:
            log.warning("fl_universe.teams_data.bad_shape", team_id=team_id)
            return None

    name = (data.get("NAME") or data.get("TEAM_NAME")
            or data.get("PARTICIPANT_NAME") or "").strip()
    country = (data.get("COUNTRY_NAME") or data.get("COUNTRY")
               or "").strip()

    if not name:
        log.warning("fl_universe.teams_data.no_name", team_id=team_id,
                    keys=list(data.keys()))
        return None

    return FLTeam(team_id=team_id, fl_name=name, country=country,
                  raw=data)


# ──────────────────────────────────────────────────────────────────────
# sp.teams cross-reference
# ──────────────────────────────────────────────────────────────────────


async def classify_against_sp_teams(
    fl_teams: list[FLTeam],
    log,
) -> list[ClassifiedTeam]:
    """Cross-reference FL teams against sp.teams (sport_id=3)."""
    if async_session is None:
        log.error("fl_universe.no_db_session")
        return []

    async with async_session() as session:
        row = (await session.execute(
            text("SELECT id FROM sp.sports WHERE name = 'Basketball'"),
        )).first()
        if row is None:
            log.error("fl_universe.basketball_sport_missing")
            return []
        basketball_sport_id = row.id

        existing = (await session.execute(
            text(
                "SELECT id, canonical_name, normalized_name, country_code "
                "FROM sp.teams WHERE sport_id = :sport_id"
            ),
            {"sport_id": basketball_sport_id},
        )).all()
        by_normalized: dict[str, dict] = {
            r.normalized_name: {
                "id": str(r.id),
                "canonical": r.canonical_name,
                "country_code": r.country_code,
            }
            for r in existing
        }
        log.info("fl_universe.sp_teams_loaded",
                 count=len(by_normalized))

    classified: list[ClassifiedTeam] = []
    for fl in fl_teams:
        norm = fl.normalized
        if not norm:
            classified.append(ClassifiedTeam(
                fl=fl, classification="SKIP",
                notes="FL name normalizes to empty",
            ))
            continue
        match = by_normalized.get(norm)
        if match is None:
            classified.append(ClassifiedTeam(
                fl=fl, classification="INSERT",
                notes="no sp.teams normalized_name match",
            ))
            continue
        cc = match["country_code"]
        if cc is None or cc == "":
            classified.append(ClassifiedTeam(
                fl=fl, classification="BACKFILL",
                sp_team_id=match["id"],
                sp_canonical=match["canonical"],
                sp_country_code=cc,
                notes="match found; country_code is NULL",
            ))
        else:
            classified.append(ClassifiedTeam(
                fl=fl, classification="SKIP",
                sp_team_id=match["id"],
                sp_canonical=match["canonical"],
                sp_country_code=cc,
                notes="match found; country_code already set",
            ))
    return classified


# ──────────────────────────────────────────────────────────────────────
# Output writers
# ──────────────────────────────────────────────────────────────────────


def write_intermediate_json(
    out_dir: Path,
    stage_meta: dict,
    fl_teams: list[FLTeam],
) -> Path:
    path = out_dir / "fl_bbl_intermediate.json"
    payload = {
        "sport_id": BASKETBALL_SPORT_ID,
        "stage_meta": stage_meta,
        "teams": [
            {
                "team_id": t.team_id,
                "fl_canonical": t.fl_name,
                "country": t.country,
                "normalized": t.normalized,
                "raw": t.raw,
            }
            for t in fl_teams
        ],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return path


def write_classification_report(
    out_dir: Path,
    stage_meta: dict,
    classified: list[ClassifiedTeam],
) -> Path:
    path = out_dir / "fl_bbl_classification.md"
    counts = {"INSERT": 0, "BACKFILL": 0, "SKIP": 0}
    for c in classified:
        counts[c.classification] = counts.get(c.classification, 0) + 1

    lines: list[str] = []
    lines.append("# FL-driven BBL bootstrap pilot — classification report")
    lines.append("")
    lines.append(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    lines.append("")
    lines.append("## FL discovery IDs")
    lines.append("")
    lines.append(f"- sport_id: `{BASKETBALL_SPORT_ID}` (Basketball)")
    lines.append(f"- league_name: `{stage_meta.get('league_name', '?')}`")
    lines.append(f"- country: `{stage_meta.get('country', '?')}`")
    lines.append(f"- stage_name: `{stage_meta.get('stage_name', '?')}`")
    lines.append(f"- stage_id: `{stage_meta.get('stage_id', '?')}`")
    lines.append(f"- season_id: `{stage_meta.get('season_id', '?')}`")
    lines.append("")
    lines.append("## Classification summary")
    lines.append("")
    lines.append(f"- INSERT: {counts['INSERT']}")
    lines.append(f"- BACKFILL: {counts['BACKFILL']}")
    lines.append(f"- SKIP: {counts['SKIP']}")
    lines.append(f"- **Total**: {len(classified)}")
    lines.append("")
    lines.append("## Per-team detail")
    lines.append("")
    lines.append("| Class | FL team_id | FL canonical | Country | "
                 "sp.teams match | sp country_code | Notes |")
    lines.append("|---|---|---|---|---|---|---|")
    for c in classified:
        lines.append(
            f"| {c.classification} | `{c.fl.team_id}` | {c.fl.fl_name} | "
            f"{c.fl.country} | {c.sp_canonical or '—'} | "
            f"{c.sp_country_code or '—'} | {c.notes} |"
        )
    lines.append("")
    lines.append("## Methodology comparison reminder")
    lines.append("")
    lines.append("Operator runs F7 against this pilot's apply and compares "
                 "vs manual-methodology benchmark from Phase 2D.5-A "
                 "workstreams #1-9 (LMB / ACB / LBA / Israeli BSL / Turkish "
                 "BSL / HEBA / VTB / EuroLeague gap-fill / ABA). Key "
                 "questions:")
    lines.append("")
    lines.append("1. Is FL's BBL roster complete vs Wikipedia 2025-26 "
                 "Basketball Bundesliga roster?")
    lines.append("2. Did FL country populate `country_code='DEU'` "
                 "cleanly on all teams?")
    lines.append("3. INSERT/BACKFILL/SKIP distribution — how does it "
                 "compare to manual benchmarks (HEBA was 4/9/0)?")
    lines.append("4. F7 strict-resolution count delta vs projected from "
                 "Day-31 BBL estimate (~110/7d)")
    lines.append("")
    path.write_text("\n".join(lines))
    return path


def write_draft_manifest(
    out_dir: Path,
    stage_meta: dict,
    classified: list[ClassifiedTeam],
) -> Path:
    """Emit a draft seed-file in the established Phase 2D.5-A shape.

    Draft includes canonical + country_code only — aliases are an empty
    tuple per pilot scope. Operator layers in alias variants per
    Pattern A.2 production discovery before apply.
    """
    path = out_dir / "bbl_seed.py.draft"
    relevant = [c for c in classified
                if c.classification in ("INSERT", "BACKFILL")]

    lines: list[str] = []
    lines.append('"""German BBL (Basketball Bundesliga) seed manifest — '
                 'Phase 2D.5-A workstream #10 PILOT.')
    lines.append('')
    lines.append('AUTOGENERATED DRAFT from scripts/fl_universe_seed.py.')
    lines.append(f'FL stage_id: {stage_meta.get("stage_id", "?")}, '
                 f'season_id: {stage_meta.get("season_id", "?")}')
    lines.append('')
    lines.append('PILOT SCOPE: canonical_name + country_code ONLY.')
    lines.append('Aliases tuple is empty for each team. Operator layers '
                 'alias variants per Pattern A.2 production discovery '
                 'before apply.')
    lines.append('"""')
    lines.append('from __future__ import annotations')
    lines.append('')
    lines.append('')
    lines.append('BBL_ALIAS_SOURCE = "bootstrap_league_coverage"')
    lines.append('')
    lines.append('')
    lines.append('# Format: (canonical_name, country_code, aliases_tuple, notes)')
    lines.append('# Aliases tuple intentionally EMPTY in PILOT draft — operator')
    lines.append('# layers alias variants per Pattern A.2 discovery.')
    lines.append('BBL_TEAMS_SEED: list[tuple[str, str, tuple[str, ...], str]] = [')
    for c in relevant:
        cc = _country_to_iso3(c.fl.country) or "DEU"
        cls = c.classification
        note = f"PILOT {cls} from FL team_id={c.fl.team_id}"
        if cls == "BACKFILL" and c.sp_team_id:
            note += f"; legacy sp.teams.id={c.sp_team_id}"
        # Escape any embedded quotes in canonical
        canonical_safe = c.fl.fl_name.replace('"', '\\"')
        lines.append(f'    ("{canonical_safe}", "{cc}",')
        lines.append('     (),')
        lines.append(f'     "{note}"),')
        lines.append('')
    lines.append(']')
    path.write_text("\n".join(lines))
    return path


def _country_to_iso3(country_name: str) -> str | None:
    """Minimal pilot-scope country → ISO3 map. BBL is single-country
    (Germany → DEU) so this is mostly a defensive stub. Extend per
    sport/league when generalizing the pilot."""
    if not country_name:
        return None
    m = {
        "germany": "DEU",
        "deutschland": "DEU",
        "spain": "ESP",
        "italy": "ITA",
        "israel": "ISR",
        "turkey": "TUR",
        "greece": "GRC",
        "russia": "RUS",
        "france": "FRA",
        "lithuania": "LTU",
        "monaco": "MCO",
        "uae": "UAE",
        "united arab emirates": "UAE",
        "serbia": "SRB",
        "montenegro": "MNE",
        "bosnia and herzegovina": "BIH",
        "bosnia-herzegovina": "BIH",
        "slovenia": "SVN",
        "croatia": "CRO",
        "austria": "AUT",
        "romania": "ROU",
        "mexico": "MEX",
        "andorra": "AND",
    }
    return m.get(country_name.strip().lower())


# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────


async def run(args, log) -> int:
    if not os.environ.get("FLASHLIVE_API_KEY", "").strip():
        print("ERROR: FLASHLIVE_API_KEY not set in environment",
              file=sys.stderr)
        return 1

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()

    # ── Step 1+2: Discover BBL stage_id + season_id ─────────────
    log.info("fl_universe.discover.start",
             league_hint=args.league_hint,
             country_hint=args.country_hint)
    stage_meta = await discover_bbl_stage(
        league_hint=args.league_hint,
        country_hint=args.country_hint,
        log=log,
    )
    if not stage_meta or not stage_meta.get("stage_id"):
        print(
            f"ERROR: No FL stage_id matched league_hint="
            f"{args.league_hint!r} in country={args.country_hint!r}. "
            "Try a different --league-hint (e.g. 'BBL' or "
            "'Basketball Bundesliga') or omit --country-hint.",
            file=sys.stderr,
        )
        return 3
    log.info("fl_universe.discover.resolved",
             stage_id=stage_meta["stage_id"],
             season_id=stage_meta["season_id"],
             stage_name=stage_meta["stage_name"],
             league_name=stage_meta["league_name"])

    # ── Step 3: Pull roster ─────────────────────────────────────
    roster = await fetch_bbl_roster(
        stage_id=stage_meta["stage_id"],
        season_id=stage_meta["season_id"],
        log=log,
    )
    if not roster:
        print(
            f"ERROR: FL standings returned no teams for stage_id="
            f"{stage_meta['stage_id']!r}. Stage may be empty (off-season) "
            "or shape may have changed — re-run with FL_OBS=1 to inspect.",
            file=sys.stderr,
        )
        return 4

    # ── Step 4: Per-team detail ─────────────────────────────────
    fl_teams: list[FLTeam] = []
    for entry in roster:
        tid = entry["team_id"]
        team = await fetch_team_detail(tid, log)
        if team:
            fl_teams.append(team)
        else:
            # Fallback: use the standings-row name with no country.
            fallback_name = entry.get("team_name") or ""
            if fallback_name:
                fl_teams.append(FLTeam(
                    team_id=tid,
                    fl_name=fallback_name,
                    country="",
                    raw={"_fallback": "from_standings_only"},
                ))
                log.info("fl_universe.team_detail.fallback",
                         team_id=tid, fallback_name=fallback_name)

    log.info("fl_universe.roster.complete",
             fl_team_count=len(fl_teams),
             roster_size=len(roster))

    # ── Step 5: Classify ────────────────────────────────────────
    if args.no_classify:
        classified = [
            ClassifiedTeam(fl=t, classification="(skipped)",
                           notes="--no-classify")
            for t in fl_teams
        ]
        log.info("fl_universe.classify.skipped")
    else:
        if not os.environ.get("DATABASE_URL", "").strip():
            print("ERROR: DATABASE_URL not set; --no-classify needed "
                  "for FL-only crawl.",
                  file=sys.stderr)
            return 1
        classified = await classify_against_sp_teams(fl_teams, log)

    # ── Step 6: Outputs ─────────────────────────────────────────
    json_path = write_intermediate_json(out_dir, stage_meta, fl_teams)
    md_path = write_classification_report(out_dir, stage_meta, classified)
    seed_path = write_draft_manifest(out_dir, stage_meta, classified)

    elapsed = time.monotonic() - started
    log.info("fl_universe.complete",
             elapsed_sec=round(elapsed, 2),
             out_dir=str(out_dir))

    print(f"\nFL BBL pilot complete in {elapsed:.1f}s.")
    print(f"  FL stage_id:    {stage_meta['stage_id']}")
    print(f"  FL season_id:   {stage_meta['season_id']}")
    print(f"  FL roster size: {len(fl_teams)}")
    counts = {"INSERT": 0, "BACKFILL": 0, "SKIP": 0}
    for c in classified:
        counts[c.classification] = counts.get(c.classification, 0) + 1
    print(f"  Classifications: INSERT={counts.get('INSERT', 0)} "
          f"BACKFILL={counts.get('BACKFILL', 0)} "
          f"SKIP={counts.get('SKIP', 0)}")
    print(f"\nOutputs:")
    print(f"  - {json_path}")
    print(f"  - {md_path}")
    print(f"  - {seed_path}  (rename to scripts/bbl_seed.py after review)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="FL-driven roster bootstrap PILOT — German BBL.",
    )
    parser.add_argument(
        "--league-hint", default="Bundesliga",
        help="LEAGUE_NAME substring to match in /v1/tournaments/list. "
             "Default 'Bundesliga'. Try 'BBL' or 'Basketball Bundesliga' "
             "if default doesn't resolve.",
    )
    parser.add_argument(
        "--country-hint", default="Germany",
        help="COUNTRY_NAME substring filter. Empty string disables.",
    )
    parser.add_argument(
        "--out-dir", default="./pilot_output",
        help="Directory for generated JSON / md / draft seed files.",
    )
    parser.add_argument(
        "--no-classify", action="store_true",
        help="Skip the sp.teams cross-reference (FL crawl only; useful "
             "when DATABASE_URL isn't available).",
    )
    args = parser.parse_args(argv)
    log = get_logger("fl_universe_seed")
    return asyncio.run(run(args, log))


if __name__ == "__main__":
    sys.exit(main())
