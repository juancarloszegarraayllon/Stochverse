"""FL-core `_live_state` construction — extracted from main.py:3676-3827
per v4-rendering PR A (Option i narrow scope).

Sole home for the FL-derived `_live_state` dict build. Both call
sites — /api/events (main.get_events) and /api/event/{ticker}
(main.get_event_detail) — invoke `build_fl_live_state(rc, g, title=)`
so the field set stays in ONE place. Before extraction the two
call sites had drifted: /api/event/{ticker} was missing
score_display, title_home, title_away and the ESPN sched_ms
kickoff override. Unifying doubles the loud-failure surface — any
future divergence surfaces at both endpoints, not one.

Deliberately self-contained: helper deps (compact_label, added-
time cache accessors) come in via lazy imports inside the fn body
so this module can be imported from `linkage.py` (which the v4
serving path already imports) without pulling flashlive_feed into
the linkage module's import graph.

PR B (v4-rendering populate) wires `linkage._apply_v4_linkage` to
call this helper when V4_OVERLAY_KEYS becomes non-empty. In PR A
V4_OVERLAY_KEYS stays `()` so the linkage-side call site is dead
code — this module is only reached via the v3 path (get_events +
get_event_detail direct calls). That's intentional: the extraction
lands FIRST so PR B has a stable target to wire against; the
serving byte-shape is unchanged for both v3 and v4 tickers.
"""
from __future__ import annotations

import re as _re
from datetime import datetime as _dt, timezone as _tz


def _normalize_scores(g: dict) -> tuple[str, str]:
    """Source-agnostic score gate. Scores are only meaningful for
    games in-progress or finished — pre-game state always yields
    empty strings, so a feed reporting '0' for a scheduled fixture
    cannot leak a phantom 0-0 into the UI.

    Mirrors main._normalize_scores; kept in-module so the extracted
    helper has zero main.py imports."""
    state = (g or {}).get("state", "")
    if state == "in":
        return (g.get("home_score", "") or "0", g.get("away_score", "") or "0")
    if state == "post":
        return (g.get("home_score", ""), g.get("away_score", ""))
    return ("", "")


def _needs_flip(title: str, g: dict) -> bool:
    """True if home/away orientation should be flipped to match the
    Kalshi title order. Uses whichever team phrase appears first in
    the normalized title to decide."""
    if not g:
        return False
    try:
        from flashlive_feed import _normalize
        tl = _normalize(title or "")
    except Exception:
        tl = (title or "").lower()

    def _first_pos(phrases):
        best = -1
        for p in phrases or ():
            if not p:
                continue
            idx = tl.find(p)
            if idx >= 0 and (best == -1 or idx < best):
                best = idx
        return best

    home_pos = _first_pos(g.get("home_phrases", []))
    away_pos = _first_pos(g.get("away_phrases", []))
    if home_pos >= 0 and (away_pos < 0 or home_pos < away_pos):
        return False
    return True


def _flip_score_pairs(label: str) -> str:
    """Flip each 'H-A' pair in a space-separated tennis label
    ('6-3 4-5 30-0' → '3-6 5-4 0-30') so the per-set breakdown
    matches the Kalshi-title orientation of score_display."""
    if not label:
        return label
    flipped = []
    for p in label.split():
        if "-" in p:
            a, b = p.split("-", 1)
            flipped.append(f"{b}-{a}")
        else:
            flipped.append(p)
    return " ".join(flipped)


def _score_display(title: str, g: dict) -> str:
    """Ordered score string whose team order matches how the teams
    appear in the Kalshi event title."""
    if not g:
        return ""
    hs, as_ = _normalize_scores(g)
    if hs == "" or as_ == "":
        return ""
    ha = g.get("home_abbr", "") or "HOME"
    aa = g.get("away_abbr", "") or "AWAY"
    if _needs_flip(title, g):
        return f"{aa} {as_} - {ha} {hs}"
    return f"{ha} {hs} - {aa} {as_}"


def build_fl_live_state(rc: dict, g: dict, *, title: str) -> None:
    """Populate rc['_live_state'] from FL/ESPN game dict g and, when
    ESPN's scheduled_kickoff_ms is present, override rc['_kickoff_dt']
    with it.

    Mutates rc in place. Caller must have already:
      - selected g via match_game (FL) or ESPN match_game
      - applied the wrong-date guard (18h delta gate)
      - applied _enrich_soccer_aggregate when sport is Soccer

    No-op when g is falsy — caller-side `if g:` guard is redundant
    but harmless; keeping it lets callers preserve their existing
    control flow verbatim."""
    if not g:
        return

    # Lazy imports — see module docstring. Kept per-call rather than
    # module-scope so `linkage.py` can import this module without
    # dragging flashlive_feed into its import graph.
    try:
        from flashlive_feed import (
            compact_label,
            ensure_added_time_cached as _fl_ensure_added_time,
            get_added_time as _fl_get_added_time,
        )
    except Exception:
        compact_label = None
        _fl_ensure_added_time = None
        _fl_get_added_time = None

    # Base compact label from the feed. For tennis we flip the
    # per-set pairs to match the Kalshi title order so the
    # "6-3 4-5 30-0" breakdown lines up with the "ALC 1 - SIN 1"
    # summary to its left.
    base_label = compact_label(g) if compact_label else ""
    if g.get("sport") == "Tennis" and _needs_flip(title, g):
        base_label = _flip_score_pairs(base_label)

    home_score_n, away_score_n = _normalize_scores(g)

    # Soccer announced added-time ("+4" board) — snap-once cache.
    # Trigger a non-blocking fetch when this match is in regulation
    # stoppage (period 1 past 44 min, period 2 past 89 min). The
    # first user request lands a fetch, the next lands the figure
    # from cache. Fire-and-forget so it doesn't add latency.
    _added_1h = None
    _added_2h = None
    if g.get("sport") == "Soccer" and g.get("state") == "in":
        _evid = g.get("event_id") or ""
        _per  = g.get("period", 0)
        _stage_ms = g.get("stage_start_ms", 0) or 0
        if _evid and _stage_ms and _per in (1, 2) and _fl_ensure_added_time:
            import time as _t_added
            _elapsed_min = max(
                0, int((_t_added.time() * 1000 - _stage_ms) / 60000)
            )
            _threshold = 44 if _per == 1 else 89
            if _elapsed_min >= _threshold:
                _fl_ensure_added_time(_evid, _per)
        if _evid and _fl_get_added_time:
            _added_1h = _fl_get_added_time(_evid, 1)
            _added_2h = _fl_get_added_time(_evid, 2)

    rc["_live_state"] = {
        "label":          base_label,
        "state":          g.get("state", ""),
        "short_detail":   g.get("short_detail", ""),
        "display_clock":  g.get("display_clock", ""),
        "period":         g.get("period", 0),
        "stage_start_ms": g.get("stage_start_ms", 0),
        "league":         g.get("league", ""),
        "captured_at_ms": g.get("captured_at_ms", 0),
        "clock_running":  g.get("clock_running", True),
        "home_abbr":      g.get("home_abbr", ""),
        "away_abbr":      g.get("away_abbr", ""),
        "home_display":   g.get("home_display", ""),
        "away_display":   g.get("away_display", ""),
        "home_score":     home_score_n,
        "away_score":     away_score_n,
        "score_display":  _score_display(title, g),
        "added_time_1h":  _added_1h,
        "added_time_2h":  _added_2h,
        # Title-derived team names so the frontend can match outcome
        # labels even when Kalshi uses a different name than ESPN
        # (e.g. "Junin" vs "Sarmiento de Junín").
        "title_home":     "",
        "title_away":     "",
        # Playoff series metadata (only ESPN games surface these;
        # SofaScore/SportsDB matches leave them empty).
        "is_playoff":         bool(g.get("is_playoff")),
        "series_title":       g.get("series_title", ""),
        "series_summary":     g.get("series_summary", ""),
        "series_home_wins":   g.get("series_home_wins"),
        "series_away_wins":   g.get("series_away_wins"),
        "series_game_number": g.get("series_game_number"),
        # Two-leg knockout aggregate (soccer cup ties).
        "is_two_leg":         bool(g.get("is_two_leg")),
        "aggregate_home":     g.get("aggregate_home"),
        "aggregate_away":     g.get("aggregate_away"),
        "leg_number":         g.get("leg_number"),
        "round_name":         g.get("round_name", ""),
        "tournament_name":    g.get("tournament_name", "") or g.get("league", ""),
        "aggregate_winner":   g.get("aggregate_winner", ""),
        # Cricket-specific score parts. When a Cricket match is
        # live, raw home_score/away_score carry the runs only
        # ("225", "212"); the wickets+overs portion ("/6 (20)")
        # lives in these companion fields. The frontend's
        # buildScoreMap formats the full "225/6 (20)" for outcome
        # rows when these are populated.
        "cricket_home_wickets": g.get("cricket_home_wickets", ""),
        "cricket_away_wickets": g.get("cricket_away_wickets", ""),
        "cricket_home_overs":   g.get("cricket_home_overs", ""),
        "cricket_away_overs":   g.get("cricket_away_overs", ""),
        "cricket_live_sentence": g.get("cricket_live_sentence", ""),
    }

    # Parse team names from the Kalshi title ("A vs B") and assign
    # to title_home / title_away using flip.
    _parts = _re.split(
        r'\s+(?:vs\.?|v|at)\s+', title, maxsplit=1, flags=_re.IGNORECASE
    )
    if len(_parts) == 2:
        _flip = _needs_flip(title, g)
        if _flip:
            rc["_live_state"]["title_home"] = _parts[1].strip()
            rc["_live_state"]["title_away"] = _parts[0].strip()
        else:
            rc["_live_state"]["title_home"] = _parts[0].strip()
            rc["_live_state"]["title_away"] = _parts[1].strip()

    # Tennis: attach structured per-player data so the frontend can
    # render a vertical 2-row scoreboard instead of the single-line
    # breakdown. Flip sides when the Kalshi title lists the away
    # player first.
    if g.get("sport") == "Tennis":
        # FlashLive provides tennis data directly as a pre-built
        # dict; ESPN uses separate fields.
        fl_tennis = g.get("tennis")
        if fl_tennis and fl_tennis.get("row1_name"):
            rc["_live_state"]["tennis"] = fl_tennis
        else:
            flip = _needs_flip(title, g)
            home_key, away_key = ("away", "home") if flip else ("home", "away")
            rc["_live_state"]["tennis"] = {
                "row1_name":   g.get(f"tennis_{home_key}_name", ""),
                "row2_name":   g.get(f"tennis_{away_key}_name", ""),
                "row1_sets":   g.get(f"tennis_{home_key}_sets", ""),
                "row2_sets":   g.get(f"tennis_{away_key}_sets", ""),
                "row1_games":  g.get(f"tennis_{home_key}_games", ""),
                "row2_games":  g.get(f"tennis_{away_key}_games", ""),
                "row1_point":  g.get(f"tennis_{home_key}_point", ""),
                "row2_point":  g.get(f"tennis_{away_key}_point", ""),
                "set_history": [
                    {
                        "set":  s.get("set"),
                        "row1": s.get(home_key),
                        "row2": s.get(away_key),
                    }
                    for s in (g.get("tennis_set_history") or [])
                ],
                "server": (
                    "row1" if g.get("tennis_server") == home_key
                    else ("row2" if g.get("tennis_server") == away_key else "")
                ),
            }

    # ESPN scheduled_kickoff_ms → override DURATION-based kickoff
    # estimate. Kalshi's expected_expiration_time varies per match,
    # so no fixed DURATION can be universally accurate; ESPN's date
    # field and SofaScore's startTimestamp are authoritative.
    sched_ms = g.get("scheduled_kickoff_ms")
    if sched_ms:
        try:
            rc["_kickoff_dt"] = _dt.fromtimestamp(
                sched_ms / 1000, tz=_tz.utc
            ).isoformat()
        except Exception:
            pass
