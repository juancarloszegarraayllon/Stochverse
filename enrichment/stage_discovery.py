"""FL tournament-stage discovery — resolve (sport, league_name) →
tournament_stage_id(s) by walking FL's master `/v1/tournaments/list`
response. Cached in caches.state._FL_TOURNAMENTS_CACHE so the heavy
list call only fires every few hours per sport.

These functions are *async* but otherwise self-contained: input is
pure data (sport + league_hint), output is pure data, the only side
effect is mutating _FL_TOURNAMENTS_CACHE which is a shared dict
imported from caches.state.
"""
import time
from caches.state import _FL_TOURNAMENTS_CACHE, _FL_TOURNAMENTS_TTL


# Internal sport name → FL sport_id, for tournaments-list lookup.
# Mirrors flashlive_feed.ACTIVE_SPORTS but inverted; kept in sync
# manually because flashlive_feed's map is also keyed by sport_id
# rather than name.
_SPORT_NAME_TO_FL_ID = {
    "Soccer": "1", "Tennis": "2", "Basketball": "3", "Hockey": "4",
    "Football": "5", "Baseball": "6", "Rugby": "8",
    "Cricket": "13", "Darts": "14", "Boxing": "16",
    "Aussie Rules": "18", "Golf": "23", "Table Tennis": "25",
    "MMA": "28", "Motorsport": "31", "Esports": "36",
}


async def _fl_tournaments_for_sport(sport_id: str) -> list:
    """Return FL's tournament list for a given sport_id, cached for
    _FL_TOURNAMENTS_TTL.

    /v1/tournaments/list response shape (verified via probe):
      {
        "DATA": [
          {
            "LEAGUE_NAME": "Champions League",
            "COUNTRY_NAME": "Europe",
            "ACTUAL_TOURNAMENT_SEASON_ID": "YJYK8L05",
            "GROUP_ID": "8bP2bXmH",
            "STAGES": [
              {"STAGE_ID": "...", "STAGE_NAME": "Group Stage", "OUT": "2"},
              {"STAGE_ID": "...", "STAGE_NAME": "Knockout", ...},
              ...
            ]
          },
          ...
        ]
      }

    We flatten this into one entry per (league, stage) so the
    matcher can score stage names alongside league names — for cup
    competitions we want the knockout stage_id, not the qualification
    one.
    """
    if not sport_id:
        return []
    cached = _FL_TOURNAMENTS_CACHE.get(sport_id)
    if cached and (time.time() - cached["ts"]) < _FL_TOURNAMENTS_TTL:
        return cached["tournaments"]
    try:
        from flashlive_feed import _fl_get
        resp = await _fl_get("/v1/tournaments/list",
                              {"sport_id": sport_id,
                               "locale": "en_INT"})
        tournaments: list = []
        if isinstance(resp, dict):
            data = resp.get("DATA") or []
            if isinstance(data, list):
                for entry in data:
                    if not isinstance(entry, dict):
                        continue
                    league_name = entry.get("LEAGUE_NAME") or ""
                    country_name = entry.get("COUNTRY_NAME") or ""
                    season_id = (entry.get("ACTUAL_TOURNAMENT_SEASON_ID")
                                 or entry.get("TOURNAMENT_SEASON_ID")
                                 or "")
                    stages = entry.get("STAGES") or []
                    if not isinstance(stages, list):
                        continue
                    for stage in stages:
                        if not isinstance(stage, dict):
                            continue
                        stage_id = stage.get("STAGE_ID")
                        if not stage_id:
                            continue
                        tournaments.append({
                            "LEAGUE_NAME":  league_name,
                            "COUNTRY_NAME": country_name,
                            "SEASON_ID":    season_id,
                            "STAGE_ID":     stage_id,
                            "STAGE_NAME":   stage.get("STAGE_NAME") or "",
                        })
        _FL_TOURNAMENTS_CACHE[sport_id] = {
            "tournaments": tournaments,
            "ts": time.time(),
        }
        return tournaments
    except Exception:
        return []


async def _find_stage_via_tournaments_list(sport: str,
                                             league_hint: str) -> dict:
    """Look up tournament_stage_id from FL's master tournament list.
    Last-ditch fallback when no current FL match in the league is
    loaded. Returns {stage_id, season_id, league_name, country} or
    empty dict when nothing matches.

    Scoring layered:
      Tier 1 (league match): exact LEAGUE_NAME == hint best,
                             substring match acceptable.
      Tier 2 (stage match):  among matching leagues, prefer stages
                             named "Knockout" / "Round" / "Final"
                             over "Qualification" / "Preliminary".
    """
    sport_id = _SPORT_NAME_TO_FL_ID.get(sport, "")
    if not sport_id or not league_hint:
        return {}
    tournaments = await _fl_tournaments_for_sport(sport_id)
    hint = league_hint.lower()
    # Stage-name preference ranking. Higher = preferred. Knockout /
    # final-round stages carry the bracket data we want for cup
    # competitions; qualification rounds rarely do.
    def _stage_rank(stage_name: str) -> int:
        s = (stage_name or "").lower()
        if any(kw in s for kw in ("final", "knockout", "round of")):
            return 50
        if any(kw in s for kw in ("group", "league phase", "league stage")):
            return 40
        if "playoff" in s or "play-off" in s or "play off" in s:
            return 35
        if "qualif" in s or "preliminary" in s or "qualifying" in s:
            return 10
        return 25  # neutral / unrecognized
    best = None
    best_score = (0, 0)  # (league_score, stage_score)
    for t in tournaments:
        league = (t.get("LEAGUE_NAME") or "").lower()
        stage  = t.get("STAGE_NAME") or ""
        if not league:
            continue
        if league == hint:
            league_score = 100
        elif hint in league:
            league_score = 80
        elif league in hint:
            league_score = 60
        else:
            continue
        stage_score = _stage_rank(stage)
        score = (league_score, stage_score)
        if score > best_score:
            best_score = score
            best = t
    if not best:
        return {}
    return {
        "stage_id":    best.get("STAGE_ID", ""),
        "season_id":   best.get("SEASON_ID", ""),
        "league_name": best.get("LEAGUE_NAME", ""),
        "country":     best.get("COUNTRY_NAME", ""),
        "stage_name":  best.get("STAGE_NAME", ""),
    }


async def _find_all_stages_for_league(sport: str,
                                        league_hint: str) -> list:
    """Return every stage of FL's master tournaments list whose
    LEAGUE_NAME matches `league_hint`. Used to find the right bracket
    stage when a competition has multiple parallel stages (e.g. UCL
    has League Phase, Knockout Phase, and Play Offs all distinct).

    Prefers exact LEAGUE_NAME match — substring fallback would pull in
    "CAF Champions League", "AFC Champions League", "Champions League
    Women" etc. when the hint is just "Champions League"."""
    sport_id = _SPORT_NAME_TO_FL_ID.get(sport, "")
    if not sport_id or not league_hint:
        return []
    tournaments = await _fl_tournaments_for_sport(sport_id)
    hint = league_hint.lower().strip()
    exact, fuzzy = [], []
    for t in tournaments:
        league = (t.get("LEAGUE_NAME") or "").lower().strip()
        if not league:
            continue
        sid = t.get("STAGE_ID", "")
        if not sid:
            continue
        entry = {
            "stage_id":    sid,
            "season_id":   t.get("SEASON_ID", ""),
            "stage_name":  t.get("STAGE_NAME", ""),
            "league_name": t.get("LEAGUE_NAME", ""),
            "country":     t.get("COUNTRY_NAME", ""),
        }
        if league == hint:
            exact.append(entry)
        elif hint in league or league in hint:
            fuzzy.append(entry)
    return exact or fuzzy
