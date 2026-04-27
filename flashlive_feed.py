"""FlashLive Sports feed — live scores via RapidAPI.

Replaces the unreliable SofaScore scraper with a proper API-key
authenticated service. Covers 30+ sports with real-time scores,
game clock, and game state.

Runs as an asyncio background task. Every POLL_INTERVAL seconds
it fetches live events, parses scores, and stores them in the
module-level GAMES dict keyed by a normalized team-name key.

main.py's match_game() function queries GAMES to overlay live
scores on Kalshi event cards.
"""
import asyncio
import logging
import os
import re
import time

try:
    import httpx
except ImportError:
    httpx = None

log = logging.getLogger("flashlive")

API_KEY = os.environ.get("FLASHLIVE_API_KEY", "").strip()
API_HOST = "flashlive-sports.p.rapidapi.com"
BASE_URL = f"https://{API_HOST}"

# Snap-once cache for the announced added-time figure (the "+4" on the
# 4th official's board at 45' / 90'). The broad-poll /v1/events/list
# does NOT carry this — confirmed against a live in-stoppage payload.
# It only surfaces on /v1/events/commentary as a "time"-class entry:
#   {"COMMENT_CLASS":"time","COMMENT_TEXT":"There will be a minimum
#    of 6 min. of added time.","COMMENT_TIME":"90+1'"}
# The figure is announced once per half and never changes, so we snap
# the int the first time we see it and never refetch that half. Lazy
# fetches are triggered from main.py's _live_state builders — i.e.
# we only pay for matches a user is actually rendering.
# Layout: {event_id: {1: int|None, 2: int|None,
#                     "1_tried_ms": int, "2_tried_ms": int}}
# Setting *_tried_ms before kicking off the fetch (whether it
# succeeds or not) so a stoppage match without a parseable comment
# yet doesn't get hit on every render.
_ADDED_TIME_CACHE: dict = {}
_ADDED_TIME_RETRY_MS = 60_000   # if first fetch returned no figure, wait this long before retrying
_ADDED_TIME_INFLIGHT: set = set()  # event_ids currently being fetched (dedup re-entry)

# Adaptive poll cadence. RapidAPI Mega tier ships unlimited monthly
# requests with a 10 req/sec rate cap, so we can run the broad poll
# aggressively without quota anxiety. Defaults sized for "full feature
# parity with Kalshi": 10 s live cadence so scores feel near-realtime,
# 60 s idle cadence so the GAMES dict stays warm between matches.
# Override via env vars if you ever switch tiers.
POLL_INTERVAL = int(os.environ.get("FLASHLIVE_POLL_INTERVAL", "60"))
LIVE_POLL_INTERVAL = int(os.environ.get("FLASHLIVE_LIVE_POLL_INTERVAL", "10"))

# Global FlashLive rate limiter. Mega tier hard-caps at 10 req/sec
# and we have two concurrent code paths hitting the API: the broad
# poll's sequential per-sport-day fetch loop, and the per-event warm
# path that fans out for viewport-visible cards. Each path was paced
# in isolation but together they spiked above the cap, dropping
# random sport fetches with HTTP 429s. Run every FL HTTP call
# through _fl_throttle() so the two paths share one rate budget.
# 200 ms min gap = 5 req/sec sustained, which keeps us comfortably
# under 10/sec even when both paths are active.
_FL_THROTTLE_LOCK = None
_FL_LAST_CALL_TS = 0.0
_FL_MIN_GAP_S = float(os.environ.get("FLASHLIVE_MIN_GAP_S", "0.20"))


async def _fl_throttle():
    global _FL_THROTTLE_LOCK, _FL_LAST_CALL_TS
    if _FL_THROTTLE_LOCK is None:
        _FL_THROTTLE_LOCK = asyncio.Lock()
    async with _FL_THROTTLE_LOCK:
        now = time.time()
        gap = now - _FL_LAST_CALL_TS
        if gap < _FL_MIN_GAP_S:
            await asyncio.sleep(_FL_MIN_GAP_S - gap)
        _FL_LAST_CALL_TS = time.time()

# Poll every FlashLive sport that maps to a Kalshi sport category in
# main.py's _SPORT_SERIES. Each sport = 1 API call per POLL_INTERVAL,
# so this list directly drives quota. IDs verified against
# /v1/sports/list (see /api/debug_fl_sports_list).
# Sports polled on the broad live-feed loop. Each entry costs one
# /v1/events/list call per poll. Mega tier removes the quota concern
# so the full sport list is restored for true cross-sport coverage.
ACTIVE_SPORTS = {
    "1": "Soccer",
    "2": "Tennis",
    "3": "Basketball",
    "4": "Hockey",
    "5": "Football",       # AMERICAN_FOOTBALL on FlashLive
    "6": "Baseball",
    "8": "Rugby",          # RUGBY_UNION (Premiership, French Top 14)
    "13": "Cricket",
    "14": "Darts",
    "16": "Boxing",
    "18": "Aussie Rules",
    "19": "Rugby",         # RUGBY_LEAGUE (NRL, Super League)
    "23": "Golf",
    "28": "MMA",
    "31": "Motorsport",
    "36": "Esports",
}
GAMES: dict = {}    # normalized key → game dict

STATUS = {
    "running": False,
    "last_fetch_ts": None,
    "games": 0,
    "last_error": None,
    "polls": 0,
}

# FlashLive sport IDs → our sport names. Verified against
# /v1/sports/list. Earlier versions of this map had the IDs from 7
# onwards wildly wrong (7 was labelled Rugby but FlashLive 7 is
# HANDBALL, etc.) — the bug was hidden because ACTIVE_SPORTS only
# polled IDs 1-4 and 6, but anywhere SPORT_MAP.get(SPORT_ID) was
# called for an event without a _sport tag, sports past 6 were
# mislabelled. SPORT_MAP is a superset of ACTIVE_SPORTS so freshly
# returned events always get a label, even if we change polling.
SPORT_MAP = {
    "1": "Soccer",
    "2": "Tennis",
    "3": "Basketball",
    "4": "Hockey",
    "5": "Football",
    "6": "Baseball",
    "8": "Rugby",
    "13": "Cricket",
    "14": "Darts",
    "16": "Boxing",
    "18": "Aussie Rules",
    "19": "Rugby",
    "23": "Golf",
    "28": "MMA",
    "31": "Motorsport",
    "32": "Motorsport",   # AUTORACING — same Kalshi bucket
    "33": "Motorsport",   # MOTORACING — same Kalshi bucket
    "36": "Esports",
}


def _normalize(s: str) -> str:
    """Normalize a team/player name for matching."""
    import unicodedata
    if not s:
        return ""
    s = unicodedata.normalize("NFD", str(s).lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    for rm in (" fc", " sc", " cf", " afc", " united", " city"):
        s = s.replace(rm, "")
    return s.strip()


def match_game(title: str, sport: str = ""):
    """Find a FlashLive game matching a Kalshi event title.
    Returns a game dict or None."""
    if not GAMES or not title:
        return None
    norm_title = _normalize(title)
    best = None
    best_score = 0
    for key, g in GAMES.items():
        if sport and g.get("sport") != sport:
            continue
        home_phrases = g.get("home_phrases", [])
        away_phrases = g.get("away_phrases", [])
        score = 0
        for phrase in home_phrases:
            if phrase and phrase in norm_title:
                score += len(phrase)
        for phrase in away_phrases:
            if phrase and phrase in norm_title:
                score += len(phrase)
        if score > best_score:
            best_score = score
            best = g
    return best if best_score >= 4 else None


def compact_label(g: dict) -> str:
    """Build a short label like 'BOS 3 - NYR 2'."""
    if not g:
        return ""
    ha = g.get("home_abbr") or g.get("home_name", "")[:3].upper()
    aa = g.get("away_abbr") or g.get("away_name", "")[:3].upper()
    hs = g.get("home_score", "")
    as_ = g.get("away_score", "")
    if hs == "" and as_ == "":
        return ""
    return f"{ha} {hs} - {aa} {as_}"


async def _fetch_live_events(days=("0",)):
    """Fetch events from FlashLive for the requested indent_days values.

    Defaults to today only — that's the live-feed hot path, hitting the
    FlashLive edge len(ACTIVE_SPORTS) times per call. Tomorrow's events
    are fetched on a slower loop (TOMORROW_REFRESH_INTERVAL) since
    schedules don't change minute-to-minute.
    """
    if not API_KEY or httpx is None:
        return []
    headers = {
        "x-rapidapi-key": API_KEY,
        "x-rapidapi-host": API_HOST,
    }
    all_events = []
    raw_samples = []
    errors = []
    async with httpx.AsyncClient(timeout=15.0) as client:
        for sport_id, sport_name in ACTIVE_SPORTS.items():
          for day in days:
            try:
                # Global rate limiter — shared with the warm path's
                # per-event _fl_get calls so the two code paths can't
                # burst past Mega's 10 req/sec ceiling combined.
                await _fl_throttle()
                r = await client.get(
                    f"{BASE_URL}/v1/events/list",
                    headers=headers,
                    params={
                        "sport_id": sport_id,
                        "indent_days": day,
                        "timezone": "-4",
                        "locale": "en_INT",
                    },
                )
                if r.status_code == 200:
                    data = r.json()
                    top_data = data.get("DATA", []) if isinstance(data, dict) else data
                    # FlashLive nests events inside tournament groups.
                    # Each item in DATA can be a tournament header or
                    # an event. Events have an EVENT_ID field.
                    for item in (top_data if isinstance(top_data, list) else []):
                        if isinstance(item, dict):
                            if item.get("EVENT_ID"):
                                # Direct event
                                item["_sport"] = sport_name
                                all_events.append(item)
                            # Check for nested events
                            for k in ("EVENTS", "events", "ITEMS", "items"):
                                nested = item.get(k)
                                if isinstance(nested, list):
                                    for ev in nested:
                                        if isinstance(ev, dict):
                                            ev["_sport"] = sport_name
                                            ev["_league"] = item.get("SHORT_NAME") or item.get("NAME_PART_2") or ""
                                            ev["_country"] = item.get("COUNTRY_NAME") or ""
                                            ev["_tournament_id"] = str(item.get("TOURNAMENT_ID") or ev.get("TOURNAMENT_ID") or "")
                                            ev["_tournament_season_id"] = str(item.get("TOURNAMENT_SEASON_ID") or ev.get("TOURNAMENT_SEASON_ID") or "")
                                            ev["_tournament_stage_id"] = str(item.get("TOURNAMENT_STAGE_ID") or ev.get("TOURNAMENT_STAGE_ID") or "")
                                            all_events.append(ev)
                    # Save raw sample for debugging
                    if len(raw_samples) < 2 and top_data:
                        first = top_data[0] if isinstance(top_data, list) and top_data else {}
                        raw_samples.append({
                            "sport": sport_name,
                            "total_items": len(top_data) if isinstance(top_data, list) else 0,
                            "first_item_keys": list(first.keys())[:20] if isinstance(first, dict) else "?",
                            "first_item_preview": str(first)[:800],
                        })
                else:
                    errors.append(f"{sport_name}: HTTP {r.status_code} - {r.text[:200]}")
            except Exception as e:
                errors.append(f"{sport_name}: {str(e)[:200]}")
    STATUS["last_error"] = errors[0] if errors else ("no events found" if not all_events else None)
    STATUS["all_errors"] = errors[:5]
    STATUS["raw_samples"] = raw_samples
    return all_events


def _parse_event(ev):
    """Parse a FlashLive event into our standard game dict format."""
    try:
        event_id = ev.get("EVENT_ID") or ""
        home_name = ev.get("HOME_NAME") or ev.get("HOME_PARTICIPANT_NAME_ONE") or ""
        away_name = ev.get("AWAY_NAME") or ev.get("AWAY_PARTICIPANT_NAME_ONE") or ""
        # Strip trailing asterisks (FlashLive marks home team with *)
        home_name = home_name.rstrip(" *")
        away_name = away_name.rstrip(" *")
        home_score = str(ev.get("HOME_SCORE_CURRENT") or ev.get("HOME_SCORE_FULL") or "")
        away_score = str(ev.get("AWAY_SCORE_CURRENT") or ev.get("AWAY_SCORE_FULL") or "")
        sport = ev.get("_sport") or SPORT_MAP.get(str(ev.get("SPORT_ID", "")), "")

        # FlashLive ships TWO classification fields per event:
        #   STAGE_TYPE — broad: "LIVE" / "SCHEDULED" / "FINISHED"
        #   STAGE      — specific period: "FIRST_HALF" / "SECOND_SET" / "Q3"
        # The earlier code used `stage = STAGE_TYPE or STAGE` for
        # everything which clobbered period detection on soccer/etc.
        # (STAGE_TYPE="LIVE" → period_map.get("LIVE", 0) = 0 → the
        # frontend's data-live-period gate failed → no clock badge,
        # no tick interpolation). Keep both fields and use whichever
        # is right for each downstream lookup.
        stage_type = str(ev.get("STAGE_TYPE") or "").upper()
        stage      = str(ev.get("STAGE") or stage_type or "").upper()
        game_time = ev.get("GAME_TIME")
        # FlashLive ships GAME_TIME as integer minutes (often null when
        # they don't have a precise count), but every live event
        # includes STAGE_START_TIME — Unix seconds when the current
        # period kicked off. The frontend uses this to compute the
        # match minute precisely + format stoppage time naturally
        # (45+3', 90+5') without relying on FL minute snapshots.
        stage_start_raw = ev.get("STAGE_START_TIME")
        try:
            stage_start_ms = int(float(stage_start_raw)) * 1000 if stage_start_raw else 0
        except (ValueError, TypeError):
            stage_start_ms = 0

        live_stages = {"LIVE", "FIRST_HALF", "SECOND_HALF", "FIRST_SET",
                       "SECOND_SET", "THIRD_SET", "FOURTH_SET", "FIFTH_SET",
                       "FIRST_PERIOD", "SECOND_PERIOD", "THIRD_PERIOD",
                       "OVERTIME", "FIRST_QUARTER", "SECOND_QUARTER",
                       "THIRD_QUARTER", "FOURTH_QUARTER", "HALFTIME",
                       "INNING", "BREAK_TIME", "AWAITING_EXTRA_TIME",
                       "EXTRA_TIME_FIRST_HALF", "EXTRA_TIME_SECOND_HALF",
                       "AWAITING_PENALTIES", "PENALTIES"}
        finished_stages = {"FINISHED", "AFTER_PENALTIES", "AFTER_EXTRA_TIME",
                          "AWARDED", "ABANDONED", "CANCELLED", "RETIRED",
                          "WALKOVER", "POSTPONED"}

        # Live/finished classification — accept either field. Some
        # leagues only ship STAGE_TYPE, some only STAGE; either being
        # in the live or finished set means the match has the
        # corresponding state.
        if stage_type in live_stages or stage in live_stages:
            state = "in"
        elif stage_type in finished_stages or stage in finished_stages:
            state = "post"
        else:
            state = "pre"

        # Soccer minute offsets per period for the STAGE_START_TIME
        # math below. Values are minutes elapsed in the match when
        # the period kicks off.
        SOCCER_PERIOD_OFFSETS = {
            "FIRST_HALF": 0, "SECOND_HALF": 45,
            "EXTRA_TIME_FIRST_HALF": 90, "EXTRA_TIME_SECOND_HALF": 105,
        }
        SOCCER_PERIOD_END = {
            "FIRST_HALF": 45, "SECOND_HALF": 90,
            "EXTRA_TIME_FIRST_HALF": 105, "EXTRA_TIME_SECOND_HALF": 120,
        }
        stage_labels = {
            "FIRST_HALF": "1st Half",
            "SECOND_HALF": "2nd Half",
            "HALFTIME": "Halftime",
            "FIRST_PERIOD": "1st Period",
            "SECOND_PERIOD": "2nd Period",
            "THIRD_PERIOD": "3rd Period",
            "OVERTIME": "Overtime",
            "FIRST_QUARTER": "Q1",
            "SECOND_QUARTER": "Q2",
            "THIRD_QUARTER": "Q3",
            "FOURTH_QUARTER": "Q4",
            "FIRST_SET": "Set 1",
            "SECOND_SET": "Set 2",
            "THIRD_SET": "Set 3",
            "FOURTH_SET": "Set 4",
            "FIFTH_SET": "Set 5",
            "BREAK_TIME": "Break",
            "PENALTIES": "Penalties",
        }
        # Game clock / minute display
        game_time_str = str(game_time or "")
        if game_time_str and game_time_str not in ("-1", "0", "", "None"):
            display_clock = f"{game_time_str}'"
        elif sport == "Soccer" and state == "in" and stage_start_ms and stage in SOCCER_PERIOD_OFFSETS:
            # GAME_TIME is unreliable on FlashLive — frequently null,
            # often stale. STAGE_START_TIME gives the exact second the
            # current period kicked off, so we derive the minute fresh
            # and let the frontend tick interpolation refine the
            # in-minute count without us needing to ship seconds.
            elapsed_secs = max(0, int(time.time()) - int(stage_start_ms / 1000))
            elapsed_min = elapsed_secs // 60
            base = SOCCER_PERIOD_OFFSETS[stage]
            end = SOCCER_PERIOD_END[stage]
            minute = max(base + 1, base + elapsed_min)
            if minute > end:
                # Stoppage — keep counting past the period mark, render
                # as "45+3'" / "90+5'" so the badge reads natural.
                display_clock = f"{end}+{minute - end}'"
            else:
                display_clock = f"{minute}'"
        elif state == "in":
            # Tennis sets, NBA quarters, hockey periods, soccer
            # halftime, etc. — descriptive label, no clock.
            display_clock = stage_labels.get(stage, stage.replace("_", " ").title())
        elif state == "post":
            display_clock = "FT"
        else:
            display_clock = ""

        short_detail = display_clock or ("FT" if state == "post" else "")

        # League from parent tournament or event fields
        league = ev.get("_league") or ev.get("TOURNAMENT_NAME") or ""
        country = ev.get("_country") or ev.get("COUNTRY_NAME") or ""

        # Abbreviations
        home_abbr = ev.get("SHORTNAME_HOME") or (home_name[:3].upper() if home_name else "")
        away_abbr = ev.get("SHORTNAME_AWAY") or (away_name[:3].upper() if away_name else "")

        # Period from stage
        period_map = {"FIRST_HALF": 1, "SECOND_HALF": 2, "HALFTIME": 1,
                      "FIRST_PERIOD": 1, "SECOND_PERIOD": 2, "THIRD_PERIOD": 3,
                      "OVERTIME": 4, "FIRST_QUARTER": 1, "SECOND_QUARTER": 2,
                      "THIRD_QUARTER": 3, "FOURTH_QUARTER": 4}
        period = period_map.get(stage, 0)

        # Scheduled start
        start_ts = ev.get("START_UTIME") or ev.get("START_TIME") or 0
        try:
            start_ms = int(float(start_ts)) * 1000 if start_ts else 0
        except (ValueError, TypeError):
            start_ms = 0

        # Normalized phrases for matching
        home_norm = _normalize(home_name)
        away_norm = _normalize(away_name)
        home_phrases = [home_norm]
        away_phrases = [away_norm]
        # Add short versions for matching
        for w in home_norm.split():
            if len(w) >= 4:
                home_phrases.append(w)
        for w in away_norm.split():
            if len(w) >= 4:
                away_phrases.append(w)

        tournament_id = str(ev.get("_tournament_id") or ev.get("TOURNAMENT_ID") or "")
        tournament_season_id = str(ev.get("_tournament_season_id") or ev.get("TOURNAMENT_SEASON_ID") or "")
        tournament_stage_id = str(ev.get("_tournament_stage_id") or ev.get("TOURNAMENT_STAGE_ID") or "")

        result = {
            "event_id": event_id,
            "sport": sport,
            "league": league,
            "country": country,
            "tournament_id": tournament_id,
            "tournament_season_id": tournament_season_id,
            "tournament_stage_id": tournament_stage_id,
            "home_name": home_name,
            "away_name": away_name,
            "home_score": home_score,
            "away_score": away_score,
            "home_abbr": home_abbr,
            "away_abbr": away_abbr,
            "state": state,
            "display_clock": display_clock,
            "short_detail": short_detail,
            "period": period,
            "stage_start_ms": stage_start_ms,
            "scheduled_kickoff_ms": start_ms,
            "home_phrases": home_phrases,
            "away_phrases": away_phrases,
            "captured_at_ms": int(time.time() * 1000),
            "_raw_keys": list(ev.keys()) if isinstance(ev, dict) else [],
            "_raw_preview": str(ev)[:3000] if isinstance(ev, dict) else "",
            # Surface INFO_NOTICE directly so /api/flashlive_status can
            # report it without truncation. Soccer matches in stoppage
            # may carry the announced added-time figure here ("4 minutes
            # added" / "+4") — once we see the actual format from a
            # real match in stoppage we'll wire it into the badge.
            "info_notice": (ev.get("INFO_NOTICE") or "").strip(),
        }
        # Playoff series state. FlashLive ships it as free-text in
        # INFO_NOTICE (observed live: "Kitchener Rangers leads series
        # 2-0." / "Series tied 2-2." / "Boston wins series 4-1.").
        # Extract numeric wins so the frontend SERIES pill can render
        # the compact "SERIES KIT 2-0" form instead of falling back to
        # the verbose summary string. ROUND ("Semi-finals" /
        # "Quarter-finals" / "Final") feeds round_name for context.
        info_notice = (ev.get("INFO_NOTICE") or "").strip()
        result["round_name"] = (ev.get("ROUND") or "").strip()
        if info_notice:
            leads_match = re.search(
                r'(.+?)\s+leads\s+series\s+(\d+)\s*[-:]\s*(\d+)',
                info_notice, re.IGNORECASE,
            )
            tied_match = re.search(
                r'series\s+tied\s+(\d+)\s*[-:]\s*(\d+)',
                info_notice, re.IGNORECASE,
            )
            wins_match = re.search(
                r'(.+?)\s+wins?\s+series\s+(\d+)\s*[-:]\s*(\d+)',
                info_notice, re.IGNORECASE,
            )
            summary = info_notice.rstrip(".").strip()
            if leads_match:
                leader = _normalize(leads_match.group(1))
                lead_w = int(leads_match.group(2))
                trail_w = int(leads_match.group(3))
                result["is_playoff"] = True
                result["series_summary"] = summary
                # Decide which side leads by name match — use `in`
                # both directions so abbreviations / partial names
                # still bind to the right team.
                home_norm = _normalize(home_name) if home_name else ""
                away_norm = _normalize(away_name) if away_name else ""
                if home_norm and (leader in home_norm or home_norm in leader):
                    result["series_home_wins"] = lead_w
                    result["series_away_wins"] = trail_w
                elif away_norm and (leader in away_norm or away_norm in leader):
                    result["series_home_wins"] = trail_w
                    result["series_away_wins"] = lead_w
            elif tied_match:
                result["is_playoff"] = True
                result["series_home_wins"] = int(tied_match.group(1))
                result["series_away_wins"] = int(tied_match.group(2))
                result["series_summary"] = summary
            elif wins_match:
                # Series ended — winner gets the higher number.
                winner = _normalize(wins_match.group(1))
                w_w = int(wins_match.group(2))
                l_w = int(wins_match.group(3))
                result["is_playoff"] = True
                result["series_summary"] = summary
                home_norm = _normalize(home_name) if home_name else ""
                away_norm = _normalize(away_name) if away_name else ""
                if home_norm and (winner in home_norm or home_norm in winner):
                    result["series_home_wins"] = w_w
                    result["series_away_wins"] = l_w
                elif away_norm and (winner in away_norm or away_norm in winner):
                    result["series_home_wins"] = l_w
                    result["series_away_wins"] = w_w
            elif "playoff" in info_notice.lower() or result["round_name"].lower() in (
                "semi-finals", "quarter-finals", "final", "finals", "1/8-finals",
                "1/16-finals", "round of 16",
            ):
                # Playoff context but no parseable series score —
                # surface what we have so the frontend pill renders
                # at least the round + summary.
                result["is_playoff"] = True
                result["series_summary"] = summary
        # Tennis: build per-set scoring data
        if sport == "Tennis" and home_name and away_name:
            set_history = []
            for si in range(1, 6):
                hs = ev.get(f"HOME_SCORE_PART_{si}")
                as_ = ev.get(f"AWAY_SCORE_PART_{si}")
                if hs is not None or as_ is not None:
                    set_history.append({
                        "set": si,
                        "row1": str(hs) if hs is not None else "",
                        "row2": str(as_) if as_ is not None else "",
                    })
            # Current game point and server come from FlashLive's
            # live event payload — keys discovered via /api/flashlive_status
            # ?sport=Tennis on a live match:
            #   HOME_SCORE_PART_GAME / AWAY_SCORE_PART_GAME → 0/15/30/40/A
            #   SERVICE → "1" home, "2" away, "" between points
            home_point = ev.get("HOME_SCORE_PART_GAME")
            away_point = ev.get("AWAY_SCORE_PART_GAME")
            svc = str(ev.get("SERVICE") or "").strip().upper()
            if svc in ("1", "HOME", "H"):
                server = "row1"
            elif svc in ("2", "AWAY", "A"):
                server = "row2"
            else:
                server = ""
            result["tennis"] = {
                "row1_name": home_name,
                "row2_name": away_name,
                "row1_sets": home_score if home_score not in ("", "None") else "0",
                "row2_sets": away_score if away_score not in ("", "None") else "0",
                "row1_games": set_history[-1]["row1"] if set_history else "",
                "row2_games": set_history[-1]["row2"] if set_history else "",
                "row1_point": str(home_point) if home_point not in (None, "", "None") else "",
                "row2_point": str(away_point) if away_point not in (None, "", "None") else "",
                "set_history": set_history,
                "server": server,
            }
        return result
    except Exception as e:
        log.debug("parse error: %s", e)
        return None


async def _fl_get(path: str, params: dict = None):
    """Shared GET helper for FlashLive API calls."""
    if not API_KEY or httpx is None:
        return None
    headers = {"x-rapidapi-key": API_KEY, "x-rapidapi-host": API_HOST}
    if params is None:
        params = {}
    params.setdefault("locale", "en_INT")
    # Global rate limiter — shared with the broad-poll loop so warm
    # fan-out + scheduled poll calls can't burst past Mega's cap.
    await _fl_throttle()
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.get(f"{BASE_URL}{path}", headers=headers, params=params)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
    return None


async def fetch_event_h2h(event_id: str):
    if not event_id: return None
    return await _fl_get("/v1/events/h2h", {"event_id": event_id})


async def fetch_event_stats(event_id: str):
    if not event_id: return None
    return await _fl_get("/v1/events/statistics", {"event_id": event_id})


async def fetch_event_lineups(event_id: str):
    if not event_id: return None
    return await _fl_get("/v1/events/lineups", {"event_id": event_id})


async def fetch_event_summary(event_id: str):
    """Fetch match incidents (goals, cards, subs)."""
    if not event_id: return None
    data = await _fl_get("/v1/events/summary-incidents", {"event_id": event_id})
    if not data:
        data = await _fl_get("/v1/events/summary", {"event_id": event_id})
    return data


async def fetch_event_commentary(event_id: str):
    if not event_id: return None
    return await _fl_get("/v1/events/commentary", {"event_id": event_id})


# ---- Added-time (4th official's board) parsing -----------------------
#
# Two formats observed in /v1/events/commentary on a live-in-stoppage
# payload (Espanyol-Levante 26 Apr 2026):
#
#   1H announcement at 45':
#     {"COMMENT_TIME":"45'","COMMENT_CLASS":"time",
#      "COMMENT_TEXT":"2 min. of stoppage-time to be played."}
#
#   2H announcement at 90+1':
#     {"COMMENT_TIME":"90+1'","COMMENT_CLASS":"time",
#      "COMMENT_TEXT":"There will be a minimum of 6 min. of added time."}
#
# Both: COMMENT_CLASS == "time" + a leading-or-mid integer followed by
# "min" and either "stoppage-time" or "added time". Regex below handles
# both, with the "(?:minimum of\s+)?" branch absorbing the 90' phrasing.
_ADDED_TIME_RE = re.compile(
    r"(?:minimum of\s+)?(\d+)\s*min\.?\s*of\s*(?:stoppage-time|added time)",
    re.IGNORECASE,
)


def _parse_added_time_from_commentary(data) -> dict:
    """Walk a /v1/events/commentary response and return announced added
    time per half: {1: int|None, 2: int|None}.

    The half is inferred from COMMENT_TIME — anything starting with "45"
    is a 1H announcement, anything starting with "90" is 2H. Earlier or
    later prefixes are ignored (extra-time periods would announce at
    105/120 but we don't render those yet).
    """
    out = {1: None, 2: None}
    if not isinstance(data, dict):
        return out
    items = data.get("DATA") or []
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        if (it.get("COMMENT_CLASS") or "").lower() != "time":
            continue
        text = it.get("COMMENT_TEXT") or ""
        m = _ADDED_TIME_RE.search(text)
        if not m:
            continue
        try:
            mins = int(m.group(1))
        except (TypeError, ValueError):
            continue
        ctime = str(it.get("COMMENT_TIME") or "")
        if ctime.startswith("45"):
            if out[1] is None:
                out[1] = mins
        elif ctime.startswith("90"):
            if out[2] is None:
                out[2] = mins
    return out


def _parse_added_time_from_summary(data) -> dict:
    """Walk a /v1/events/summary response and return announced added
    time per half: {1: int|None, 2: int|None}.

    Handles INJURY_TIME / STOPPAGE_TIME / ADDED_TIME incident types in
    the DATA[].ITEMS[] structure. The +N value can live in several
    length-style fields (LENGTH, INCIDENT_LENGTH, INCIDENT_VALUE,
    VALUE) depending on the league — first numeric field wins. If no
    explicit length field is present, falls back to extracting "+N"
    from the INCIDENT_TIME string itself ("45+2'", "90+5'").

    Half is inferred from the surrounding STAGE_NAME ("1st Half" /
    "2nd Half") or, failing that, from the leading minute in
    INCIDENT_TIME (45 → 1H, 90 → 2H).
    """
    out = {1: None, 2: None}
    if not isinstance(data, dict):
        return out
    stages = data.get("DATA") or data.get("data") or []
    if not isinstance(stages, list):
        return out
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        stage_name = str(stage.get("STAGE_NAME") or "").lower()
        # Determine half from stage name when possible.
        stage_half = None
        if "1st" in stage_name or "first" in stage_name:
            stage_half = 1
        elif "2nd" in stage_name or "second" in stage_name:
            stage_half = 2
        for inc in (stage.get("ITEMS") or stage.get("items") or []):
            if not isinstance(inc, dict):
                continue
            inc_type = str(inc.get("INCIDENT_TYPE") or "").upper()
            if not ("INJURY" in inc_type or "STOPPAGE" in inc_type
                    or "ADDED" in inc_type):
                continue
            mins = None
            for fld in ("LENGTH", "INCIDENT_LENGTH", "INCIDENT_VALUE",
                        "VALUE", "MIN", "MINUTES"):
                v = inc.get(fld)
                if v is None:
                    continue
                try:
                    mins = int(str(v).strip().lstrip("+"))
                    break
                except (TypeError, ValueError):
                    continue
            # Fallback: extract "+N" from INCIDENT_TIME ("45+2'" form).
            if mins is None:
                t = str(inc.get("INCIDENT_TIME") or inc.get("TIME") or "")
                m = re.search(r"\+\s*(\d+)", t)
                if m:
                    try:
                        mins = int(m.group(1))
                    except (TypeError, ValueError):
                        pass
            if mins is None or not (1 <= mins <= 15):
                continue  # noise filter — added time never exceeds ~10
            half = stage_half
            if half is None:
                t = str(inc.get("INCIDENT_TIME") or inc.get("TIME") or "")
                if t.startswith("45"):
                    half = 1
                elif t.startswith("90"):
                    half = 2
            if half == 1 and out[1] is None:
                out[1] = mins
            elif half == 2 and out[2] is None:
                out[2] = mins
    return out


def get_added_time(event_id: str, period: int):
    """Return the cached added-time figure for an event's half, or None.
    Used by the frontend-state builders in main.py."""
    if not event_id or period not in (1, 2):
        return None
    entry = _ADDED_TIME_CACHE.get(event_id)
    if not entry:
        return None
    return entry.get(period)


async def _fetch_and_cache_added_time(event_id: str):
    """Background fetch + parse + snap into _ADDED_TIME_CACHE. Once the
    figure for a given half is non-None it stays — the 4th official
    only announces once per half and the number never changes.

    Two-source: FL commentary text first (top-flight European leagues
    typically carry the explicit "X min. of stoppage-time" quote), then
    falls back to FL summary INJURY_TIME incidents (covers a third tier
    of leagues whose summary feed has a typed incident but whose
    commentary feed is sparse or absent). Either source can fill either
    half independently."""
    if not event_id:
        return
    if event_id in _ADDED_TIME_INFLIGHT:
        return
    _ADDED_TIME_INFLIGHT.add(event_id)
    try:
        data = await fetch_event_commentary(event_id)
        parsed = _parse_added_time_from_commentary(data)
        # Summary-incident fallback — only fired when commentary
        # missed at least one half. Same snap-once semantics; if
        # commentary already filled both halves we don't pay for a
        # second HTTP call.
        if parsed[1] is None or parsed[2] is None:
            try:
                summary = await fetch_event_summary(event_id)
                sparsed = _parse_added_time_from_summary(summary)
                if parsed[1] is None: parsed[1] = sparsed[1]
                if parsed[2] is None: parsed[2] = sparsed[2]
            except Exception as e:
                log.debug("summary added-time fallback failed for %s: %s", event_id, e)
        entry = _ADDED_TIME_CACHE.setdefault(event_id, {})
        now_ms = int(time.time() * 1000)
        # Only overwrite a half's value if we have a fresh int for it
        # (preserves a previously snapped figure even if a later
        # commentary fetch happens to drop the announcement entry).
        if parsed[1] is not None and entry.get(1) is None:
            entry[1] = parsed[1]
        if parsed[2] is not None and entry.get(2) is None:
            entry[2] = parsed[2]
        entry["1_tried_ms"] = now_ms
        entry["2_tried_ms"] = now_ms
    except Exception as e:
        log.debug("added-time fetch failed for %s: %s", event_id, e)
    finally:
        _ADDED_TIME_INFLIGHT.discard(event_id)


def ensure_added_time_cached(event_id: str, period: int) -> None:
    """Trigger a non-blocking, snap-once fetch of the announced
    added-time figure for a soccer match in stoppage. Safe to call
    every render — early-returns if the half is already cached or a
    recent attempt is still cooling down. Re-fetches at most once per
    _ADDED_TIME_RETRY_MS when the previous attempt found no figure
    (the announcement may not have shipped yet)."""
    if not event_id or period not in (1, 2):
        return
    entry = _ADDED_TIME_CACHE.get(event_id) or {}
    if entry.get(period) is not None:
        return  # snapped — never refetch
    tried_ms = entry.get(f"{period}_tried_ms", 0)
    now_ms = int(time.time() * 1000)
    if tried_ms and (now_ms - tried_ms) < _ADDED_TIME_RETRY_MS:
        return  # cooling down
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no event loop in this context (e.g. sync request handler)
    loop.create_task(_fetch_and_cache_added_time(event_id))


async def fetch_event_news(event_id: str):
    if not event_id: return None
    return await _fl_get("/v1/events/news", {"event_id": event_id})


async def fetch_standings(tournament_stage_id: str, season_id: str = ""):
    """Fetch league standings. Requires tournament_stage_id."""
    if not tournament_stage_id: return None
    params = {"tournament_stage_id": tournament_stage_id, "standing_type": "overall"}
    if season_id:
        params["tournament_season_id"] = season_id
    return await _fl_get("/v1/tournaments/standings", params)


async def fetch_top_scorers(tournament_stage_id: str, season_id: str = ""):
    """Fetch top scorers. Same endpoint as standings, different standing_type."""
    if not tournament_stage_id: return None
    params = {"tournament_stage_id": tournament_stage_id, "standing_type": "top_scores"}
    if season_id:
        params["tournament_season_id"] = season_id
    return await _fl_get("/v1/tournaments/standings", params)


def find_flashlive_event_id(title: str, sport: str = ""):
    """Find the FlashLive EVENT_ID for a game matching the title."""
    g = match_game(title, sport)
    if g:
        return g.get("event_id")
    return None


def find_flashlive_game(title: str, sport: str = ""):
    """Find the full FlashLive game dict matching the title."""
    return match_game(title, sport)


async def search_flashlive_event(title: str, sport: str = ""):
    """On-demand search when the background feed doesn't have the event
    (e.g. tomorrow's matches). Uses the search endpoint."""
    # First try the cached GAMES
    g = match_game(title, sport)
    if g:
        return g
    # Search FlashLive
    import re
    parts = re.split(r'\s+(?:vs\.?|v|at)\s+', title, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) < 2:
        return None
    query = parts[0].strip() + " " + parts[1].strip()
    data = await _fl_get("/v1/search/multi-search", {"query": query})
    if not data:
        return None
    if isinstance(data, list):
        results = data
    elif isinstance(data, dict):
        results = data.get("DATA") or data.get("data") or []
    else:
        return None
    if not isinstance(results, list):
        return None
    for item in results:
        if not isinstance(item, dict):
            continue
        if item.get("TYPE") != "event" and item.get("type") != "event":
            continue
        ev = item
        event_id = ev.get("ID") or ev.get("EVENT_ID") or ""
        if not event_id:
            continue
        # Try to get tournament info from the search result
        stage_id = ev.get("TOURNAMENT_STAGE_ID") or ""
        season_id = ev.get("TOURNAMENT_SEASON_ID") or ev.get("TOURNAMENT_ID") or ""
        home = ev.get("HOME_NAME") or ev.get("PARTICIPANT_HOME") or ""
        away = ev.get("AWAY_NAME") or ev.get("PARTICIPANT_AWAY") or ""
        league = ev.get("TOURNAMENT_NAME") or ev.get("LEAGUE") or ""
        return {
            "event_id": event_id,
            "home_name": home,
            "away_name": away,
            "sport": sport,
            "league": league,
            "tournament_id": season_id,
            "tournament_stage_id": stage_id,
            "tournament_season_id": season_id,
            "state": "pre",
        }
    return None


async def run_flashlive_feed():
    """Background task: poll FlashLive for live scores."""
    if not API_KEY:
        log.info("FLASHLIVE_API_KEY not set — FlashLive feed disabled")
        return
    if httpx is None:
        log.warning("httpx not installed — FlashLive feed disabled")
        return

    STATUS["running"] = True
    log.info("FlashLive feed starting (live poll: %ds, idle poll: %ds)",
             LIVE_POLL_INTERVAL, POLL_INTERVAL)

    while True:
        try:
            # Mega tier: fetch today + tomorrow on every poll for full
            # coverage. Tomorrow's events surface in the calendar
            # immediately when FlashLive lists them.
            events = await _fetch_live_events(days=("0", "1"))
            parsed = 0
            new_games = {}
            for ev in events:
                g = _parse_event(ev)
                if g and g.get("home_name") and g.get("away_name"):
                    key = f"{g['sport']}:{_normalize(g['home_name'])}:{_normalize(g['away_name'])}"
                    new_games[key] = g
                    parsed += 1
            # Merge into GAMES rather than clear+update — that earlier
            # clear left GAMES briefly empty between the two lines (any
            # /api/events request landing in the gap saw no _live_state
            # for any event), and any single sport-day FL request that
            # transiently hiccupped would drop matches from GAMES until
            # the next clean poll, flickering LIVE badges + scores on
            # cards. Now: new entries overwrite old, and a stale-entry
            # sweep removes anything we haven't seen in 10 minutes so
            # the dict doesn't grow unbounded.
            GAMES.update(new_games)
            stale_cutoff_ms = int((time.time() - 600) * 1000)
            stale_keys = [k for k, g in list(GAMES.items())
                          if (g.get("captured_at_ms") or 0) < stale_cutoff_ms]
            stale_event_ids = {GAMES[k].get("event_id") for k in stale_keys
                               if GAMES.get(k, {}).get("event_id")}
            for k in stale_keys:
                GAMES.pop(k, None)
            # Drop the added-time snapshot for any event that just left
            # GAMES — keeps the cache bounded to the live universe.
            for eid in stale_event_ids:
                _ADDED_TIME_CACHE.pop(eid, None)
            STATUS["games"] = len(GAMES)
            STATUS["last_fetch_ts"] = time.time()
            STATUS["polls"] += 1
            if parsed:
                log.info("FlashLive: %d games across all sports (kept %d, swept %d stale)",
                         parsed, len(GAMES), len(stale_keys))
        except Exception as e:
            STATUS["last_error"] = str(e)[:200]
            log.error("FlashLive poll error: %s", e)

        # Adaptive cadence — fast when at least one game is currently
        # live so scores update at near-Kalshi refresh speed, slow
        # otherwise to spare the API quota.
        has_live = any(g.get("state") == "in" for g in GAMES.values())
        sleep_for = LIVE_POLL_INTERVAL if has_live else POLL_INTERVAL
        STATUS["last_sleep_s"] = sleep_for
        await asyncio.sleep(sleep_for)
