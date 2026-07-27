"""S1-chain diagnostic — pinpoints why Deliverable 1's v1 map lands at
zero pairings on the standalone daily-diff cron.

Runs the exact chain the daily-diff pipeline uses:
  sp.fl_events raw_payload
    → _parse_event
      → _build_games_from_fl_events (games dict)
        → match_game (with explicit games=)
          → _build_kalshi_index_for_sport → v1_map

Prints intermediate sizes and one worked sample per stage so the exact
break-point falls out of one run's paste-back.

Two hypotheses under test (from prior code trace):
  H1: sp.fl_events.raw_payload lacks SPORT_ID (FL's /v1/events/list
      nests SPORT_ID at the tournament level, not per-event).
      _parse_event falls back to SPORT_MAP.get("", "") = "" →
      every game has sport="" → match_game filter drops everything.
  H2: SPORT_MAP label mismatch (SPORT_MAP["5"]="Football" vs Kalshi's
      "American Football"; SPORT_MAP["8"]="Rugby" vs "Rugby Union" /
      "Rugby League") → same filter-drop behavior for those sports.

Read-only. No writes to sp.daily_diff_reports, no state mutation.

Invocation:
    python scripts/diag_s1.py

TARGET_SPORT defaults to "Basketball" (highest expected overlap between
Kalshi's NBA regular season and FL's game coverage — also the sport
where SPORT_MAP alignment IS clean, so a zero here confirms H1). Set
via env var DIAG_SPORT="American Football" to also probe H2 in the
same run.
"""
from __future__ import annotations

import asyncio
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

# Same sys.path setup as scripts/daily_diff.py — makes top-level
# modules (db, observability, resolver, etc.) importable when the
# script is invoked as `python scripts/diag_s1.py` from the project
# root or from /app in the Railway container.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from db import async_session  # noqa: E402
from flashlive_feed import SPORT_MAP, _parse_event, match_game  # noqa: E402
from scripts.daily_diff import (  # noqa: E402
    _KALSHI_WINDOW_SQL,
    _FL_WINDOW_SQL,
    _build_games_from_fl_events,
    _run_legacy_pairings,
)


TARGET_SPORT = os.environ.get("DIAG_SPORT", "Basketball")


async def diagnose() -> None:
    window_end = datetime.now(timezone.utc)
    window_start = window_end - timedelta(hours=24)
    bind = {"window_start": window_start, "window_end": window_end}

    if async_session is None:
        print("!! async_session is None — DATABASE_URL not set or engine unavailable")
        return

    async with async_session() as session:
        kalshi_rows = (
            await session.execute(text(_KALSHI_WINDOW_SQL), bind)
        ).all()
        fl_rows = (
            await session.execute(text(_FL_WINDOW_SQL), bind)
        ).all()

    print(f"[1] Window population — kalshi={len(kalshi_rows)}, fl={len(fl_rows)}")

    # ---- Partition kalshi by _sport (raw_payload enriched-cache field) ----
    kalshi_records = [
        dict(r.raw_payload) for r in kalshi_rows
        if r.raw_payload and isinstance(r.raw_payload, dict)
    ]
    kalshi_by_sport: dict = defaultdict(list)
    for r in kalshi_records:
        s = r.get("_sport")
        if s:
            kalshi_by_sport[s].append(r)
    print(f"[2] Kalshi partition sports: {sorted(kalshi_by_sport)}")

    # ---- Partition fl_events by sport_name (from sp.sports JOIN) ----
    fl_by_sport: dict = defaultdict(list)
    for r in fl_rows:
        if (
            r.raw_payload
            and isinstance(r.raw_payload, dict)
            and r.sport_name
        ):
            fl_by_sport[r.sport_name].append(dict(r.raw_payload))
    print(f"[3] FL partition sports (from sp.sports JOIN): {sorted(fl_by_sport)}")

    intersect = sorted(set(kalshi_by_sport) & set(fl_by_sport))
    print(f"[4] sports_to_diff intersection: {intersect}")

    if TARGET_SPORT not in intersect:
        print(
            f"!! TARGET_SPORT={TARGET_SPORT!r} NOT in intersection — "
            f"set DIAG_SPORT to one of {intersect}"
        )
        return

    kalshi_target = kalshi_by_sport[TARGET_SPORT]
    fl_target = fl_by_sport[TARGET_SPORT]
    print(
        f"\n[5] {TARGET_SPORT}: kalshi={len(kalshi_target)}, fl={len(fl_target)}"
    )

    # ---- Sample fl_event raw_payload — key existence check (H1 probe) ----
    sample_fl = fl_target[0]
    print(f"\n[6] Sample fl_event raw_payload key/value probe:")
    for k in (
        "EVENT_ID",
        "HOME_NAME",
        "AWAY_NAME",
        "SHORTNAME_HOME",
        "SHORTNAME_AWAY",
        "SPORT_ID",
        "START_UTIME",
        "START_TIME",
        "_sport",
    ):
        v = sample_fl.get(k, "<MISSING>")
        print(f"      {k:16s} = {v!r}"[:120])

    # ---- Run _parse_event on the sample directly ----
    parsed = _parse_event(sample_fl)
    print(f"\n[7] _parse_event(sample) returned:")
    if parsed:
        for k in (
            "event_id",
            "sport",
            "home_name",
            "away_name",
            "home_phrases",
            "away_phrases",
        ):
            print(f"      {k:16s} = {parsed.get(k)!r}"[:120])
    else:
        print("      None — parse failed entirely")

    # ---- SPORT_MAP diagnostic (H1 + H2 pivot) ----
    sport_id = str(sample_fl.get("SPORT_ID", ""))
    mapped = SPORT_MAP.get(sport_id, "<not in SPORT_MAP>")
    print(f"\n[8] SPORT_MAP diagnostic:")
    print(f"      raw SPORT_ID:        {sport_id!r}")
    print(f"      SPORT_MAP[SPORT_ID]: {mapped!r}")
    print(f"      partition sport:     {TARGET_SPORT!r}")
    print(f"      MATCH?               {mapped == TARGET_SPORT}")

    # ---- Build games dict ----
    games = _build_games_from_fl_events(fl_target)
    print(f"\n[9] _build_games_from_fl_events → len(games) = {len(games)}")
    if games:
        sample_key, sample_game = next(iter(games.items()))
        print(f"      Sample key: {sample_key!r}")
        print(f"      Sample game.sport:        {sample_game.get('sport')!r}")
        print(f"      Sample game.home_phrases: {sample_game.get('home_phrases')!r}")
        print(f"      Sample game.away_phrases: {sample_game.get('away_phrases')!r}")

    # match_game's sport-filter test (the exact predicate that drops
    # every candidate when sport labels don't align).
    matching_sport = sum(
        1 for g in games.values() if g.get("sport") == TARGET_SPORT
    )
    print(
        f"[10] games where g['sport'] == {TARGET_SPORT!r}: "
        f"{matching_sport} / {len(games)}"
    )

    # ---- Try one real match_game call ----
    if kalshi_target:
        sample_k = kalshi_target[0]
        title = sample_k.get("title", "") or ""
        print(f"\n[11] Sample kalshi title: {title!r}")
        result = match_game(title, TARGET_SPORT, games=games)
        print(
            f"     match_game(title, {TARGET_SPORT!r}, games=games) = "
            f"{result!r}"[:200]
        )

    # ---- Full _run_legacy_pairings — the end-to-end call daily_diff uses ----
    v1_map, v2_map = _run_legacy_pairings(
        TARGET_SPORT,
        kalshi_target,
        fl_target,
        games=games,
    )
    print(
        f"\n[12] _run_legacy_pairings({TARGET_SPORT}): "
        f"v1={len(v1_map)} pairings, v2={len(v2_map)} pairings"
    )


if __name__ == "__main__":
    asyncio.run(diagnose())
