"""FlashLive response parsers — pure functions that take raw FL
payloads and return our internal compact shapes. No globals, no
external HTTP, no main.py dependencies.

Extracted from main.py during the Day 1 modularization pass. The
source-of-truth for behavior is the test suite under tests/parsers/.
"""


def _bracket_raw_payload(raw):
    """Extract FL's draw-response inner DATA dict for the legacy
    _renderBracket() in static/index.html. The legacy renderer reads
    `data.TABS` and `data.ROUNDS` directly, so we strip the outer
    {DATA: [...]} wrapper to match what it expects.

    Also filters out empty TBD-vs-TBD blocks: FL leaves placeholder
    blocks for not-yet-drawn match slots, and the legacy renderer
    interprets "no home name + no away name" as a Bye block, surfacing
    rows of confusing "Bye / Bye" cards. For the post-league-phase
    knockout we drop those entirely (they'll re-appear once FL fills
    the slot with real participants).

    Returns None when the response shape doesn't match (legacy
    renderer treats null as "no bracket published yet")."""
    if not raw or not isinstance(raw, dict):
        return None
    arr = raw.get("DATA")
    inner = None
    if isinstance(arr, list) and arr and isinstance(arr[0], dict):
        first = arr[0]
        if "DATA" in first and isinstance(first["DATA"], dict):
            inner = first["DATA"]
        elif "TABS" in first or "ROUNDS" in first:
            inner = first
    elif isinstance(arr, dict):
        inner = arr
    if not inner:
        return None

    rounds = inner.get("ROUNDS")
    if not isinstance(rounds, list):
        return inner
    tabs = inner.get("TABS") or {}
    parts = tabs.get("DRAW_EVENT_PARTICIPANTS") or {}

    def _has_name(ord_val):
        if ord_val is None or ord_val == "":
            return False
        return bool((parts.get(str(ord_val)) or "").strip())

    def _has_real_bye(blk):
        # FL marks a true bye on the INFO field. We keep those.
        info = (blk.get("DRAW_EVENT_PARTICIPANT_INFO_HOME") or "") + \
               (blk.get("DRAW_EVENT_PARTICIPANT_INFO_AWAY") or "")
        return "(Bye)" in info or "(bye)" in info.lower()

    cleaned_rounds = []
    for rnd in rounds:
        if not isinstance(rnd, dict):
            continue
        blocks = rnd.get("BLOCKS") or []
        kept = []
        for blk in blocks:
            if not isinstance(blk, dict):
                continue
            home_ok = _has_name(blk.get("DRAW_ROUND_HOME_EVENT_PARTICIPANT"))
            away_ok = _has_name(blk.get("DRAW_ROUND_AWAY_EVENT_PARTICIPANT"))
            if not home_ok and not away_ok and not _has_real_bye(blk):
                continue
            kept.append(blk)
        if kept:
            cleaned = dict(rnd)
            cleaned["BLOCKS"] = kept
            cleaned_rounds.append(cleaned)

    cleaned_inner = dict(inner)
    cleaned_inner["ROUNDS"] = cleaned_rounds
    return cleaned_inner


def _compact_bracket(raw):
    """Flatten FL's nested standing_type=draw response into a compact
    shape the frontend can render directly:

      {"rounds": [{"round_num": 4, "label": "1/8-finals",
                   "pairs": [{"home": "ludogorets", "away": "din-minsk",
                              "legs": [{"home":1,"away":0},{"home":2,"away":2}],
                              "winner": "home"|"away"|"draw"|None,
                              "agg_home": 3, "agg_away": 2,
                              "starts_at": 1752691500}, ...]}, ...]}

    Team slugs come from FL's `DRAW_ROUND_EVENT_IDS` strings, formatted
    as "event_id;home_ord;away_ord;ts;H:A;winner_ord;home_slug;away_slug".
    Returns None if the input doesn't look like a bracket payload.
    """
    if not raw:
        return None

    def find_first(obj, key):
        # Depth-first search for a dict carrying this key — the FL
        # wrapper depth varies (DATA[].GROUPS[]., DATA[]., raw).
        if isinstance(obj, dict):
            if key in obj:
                return obj
            for v in obj.values():
                r = find_first(v, key)
                if r is not None:
                    return r
        elif isinstance(obj, list):
            for v in obj:
                r = find_first(v, key)
                if r is not None:
                    return r
        return None

    container = find_first(raw, "ROUNDS") or find_first(raw, "DRAW_ROUNDS")
    if not container:
        return None

    # Round labels live in a sibling TABS container, not on the same node
    # as ROUNDS. Walk the tree to find DRAW_ROUNDS regardless. Same for
    # the participant lookup maps — DRAW_EVENT_PARTICIPANTS gives display
    # names, DRAW_PARTICIPANT_IDS gives FL team IDs (cross-referenceable
    # with standings rows for logos / colors / linking).
    labels_owner = find_first(raw, "DRAW_ROUNDS") or {}
    round_labels = labels_owner.get("DRAW_ROUNDS") or {}
    names_owner = find_first(raw, "DRAW_EVENT_PARTICIPANTS") or {}
    name_by_ord = names_owner.get("DRAW_EVENT_PARTICIPANTS") or {}
    ids_owner = find_first(raw, "DRAW_PARTICIPANT_IDS") or {}
    id_by_ord = ids_owner.get("DRAW_PARTICIPANT_IDS") or {}
    rounds_in = container.get("ROUNDS") or []
    if not isinstance(rounds_in, list):
        return None

    rounds_out = []
    for rnd in rounds_in:
        if not isinstance(rnd, dict):
            continue
        round_num = rnd.get("DRAW_ROUND")
        label = round_labels.get(str(round_num)) if round_num is not None else None
        pairs_out = []
        for blk in (rnd.get("BLOCKS") or []):
            if not isinstance(blk, dict):
                continue

            home_slug = away_slug = None
            legs = []
            # The bracket's home/away perspective is fixed by the block's
            # DRAW_ROUND_HOME_EVENT_PARTICIPANT. Second-leg event_ids list
            # the host first, so we flip the score when leg-home != block-home.
            block_home_ord = str(blk.get("DRAW_ROUND_HOME_EVENT_PARTICIPANT") or "")
            event_ids = blk.get("DRAW_ROUND_EVENT_IDS") or []
            for eid in event_ids:
                if not isinstance(eid, str):
                    continue
                parts = eid.split(";")
                if len(parts) < 8:
                    continue
                leg_home_ord = parts[1]
                score_str = parts[4] or ""
                if ":" in score_str:
                    h, a = score_str.split(":", 1)
                    try:
                        h_int, a_int = int(h), int(a)
                        if block_home_ord and leg_home_ord != block_home_ord:
                            h_int, a_int = a_int, h_int
                        legs.append({"home": h_int, "away": a_int})
                    except ValueError:
                        pass
                if home_slug is None:
                    if block_home_ord and leg_home_ord != block_home_ord:
                        home_slug = parts[7] or None
                        away_slug = parts[6] or None
                    else:
                        home_slug = parts[6] or None
                        away_slug = parts[7] or None

            home_results = blk.get("DRAW_ROUND_HOME_RESULTS") or []
            away_results = blk.get("DRAW_ROUND_AWAY_RESULTS") or []
            if not legs and (home_results or away_results):
                # Single-leg or pre-aggregated form.
                for h, a in zip(home_results, away_results):
                    try:
                        legs.append({"home": int(h), "away": int(a)})
                    except (ValueError, TypeError):
                        pass

            def _sum(seq):
                tot = 0
                for v in seq:
                    try:
                        tot += int(v)
                    except (ValueError, TypeError):
                        pass
                return tot

            agg_home = _sum(home_results) if home_results else (
                sum(l["home"] for l in legs) if legs else None)
            agg_away = _sum(away_results) if away_results else (
                sum(l["away"] for l in legs) if legs else None)

            winner_overall = blk.get("DRAW_ROUND_EVENT_WINNER_OVERALL")
            if winner_overall == "H":
                winner = "home"
            elif winner_overall == "A":
                winner = "away"
            else:
                # KO ties go to penalties / another leg, never end as
                # draws — so a tied aggregate just means the match is
                # pending or in progress. Report null, not "draw".
                winner = None

            # Skip TBD-vs-TBD placeholder slots — they have no slugs and no
            # legs and just inflate the response. Keep half-known pairs so
            # the bracket still shows e.g. "Arsenal vs ?".
            if not home_slug and not away_slug and not legs:
                continue
            home_ord = str(blk.get("DRAW_ROUND_HOME_EVENT_PARTICIPANT") or "")
            away_ord = str(blk.get("DRAW_ROUND_AWAY_EVENT_PARTICIPANT") or "")
            pairs_out.append({
                "home":         home_slug,
                "away":         away_slug,
                "home_name":    name_by_ord.get(home_ord) or None,
                "away_name":    name_by_ord.get(away_ord) or None,
                "home_team_id": id_by_ord.get(home_ord) or None,
                "away_team_id": id_by_ord.get(away_ord) or None,
                "legs":         legs,
                "winner":       winner,
                "agg_home":     agg_home,
                "agg_away":     agg_away,
                "starts_at":    blk.get("DRAW_ROUND_EVENT_START") or blk.get("DRAW_TIME"),
            })

        rounds_out.append({
            "round_num": round_num,
            "label":     label,
            "pairs":     pairs_out,
        })

    return {"rounds": rounds_out} if rounds_out else None


def _compact_standings(raw):
    """Strip FL standings to the fields the frontend actually renders.
    Returns {"groups": [...], "meta": {...}} or None.

    Per-row fields: rank, name, team_id, image_url, played, wins,
    goals, points, qualification (q1/q2/null), tuc (color code).

    Top-level meta carries the qualification legend (color → label
    map) and tie-breaker note(s). FL's META block lives in two
    places depending on the competition — top-level on most domestic
    leagues (LaLiga, LaLiga2, Premier League, Serie A) and per-group
    on multi-group cup formats (UCL "Main" group). Walk both."""
    if not raw or not isinstance(raw, dict):
        return None
    groups_in = raw.get("DATA")
    if not isinstance(groups_in, list):
        return None
    groups_out = []
    # Try top-level META first — most leagues live here. Per-group
    # META overrides if non-empty. Collect into meta_out.
    meta_out = _extract_standings_meta(raw.get("META"))
    for grp in groups_in:
        if not isinstance(grp, dict):
            continue
        rows_out = []
        for r in (grp.get("ROWS") or []):
            if not isinstance(r, dict):
                continue
            rows_out.append({
                "rank":          r.get("RANKING"),
                "name":          r.get("TEAM_NAME"),
                "team_id":       r.get("TEAM_ID"),
                "image_url":     r.get("TEAM_IMAGE_PATH") or "",
                "played":        r.get("MATCHES_PLAYED"),
                "wins":          r.get("WINS"),
                "goals":         r.get("GOALS"),
                "points":        r.get("POINTS"),
                "qualification": r.get("TEAM_QUALIFICATION"),
                "tuc":           r.get("TUC") or "",
            })
        if rows_out:
            groups_out.append({
                "name": grp.get("GROUP") or "Main",
                "rows": rows_out,
            })
        # Per-group META falls back to enrich top-level META when
        # only one or the other is populated. Same across groups in
        # practice — first non-empty wins.
        if not meta_out:
            meta_out = _extract_standings_meta(grp.get("META"))
    if not groups_out:
        return None
    out = {"groups": groups_out}
    if meta_out:
        out["meta"] = meta_out
    return out


def _extract_standings_meta(meta):
    """Pull qualification_legend + decisions out of a FL standings
    META block. Returns {} if neither is present, so callers can
    treat falsy as "try the other location"."""
    if not isinstance(meta, dict):
        return {}
    qi = meta.get("QUALIFICATION_INFO") or {}
    legend = []
    if isinstance(qi, dict):
        for color, info in qi.items():
            # FL shape: {"004682": ["q1", "Promotion - …", "004682"]}
            if isinstance(info, list) and info:
                legend.append({
                    "color":         color,
                    "qualification": info[0] if len(info) > 0 else "",
                    "label":         info[1] if len(info) > 1 else "",
                })
    decisions = meta.get("DECISIONS") or []
    if not isinstance(decisions, list):
        decisions = []
    if legend or decisions:
        return {
            "qualification_legend": legend,
            "decisions":            [str(x) for x in decisions if x],
        }
    return {}


def _compact_top_scorers(raw, limit=20):
    """Strip FL top-scorer rows to {rank, name, team, goals, assists}.
    Caps the list at `limit` and returns {rows, total, has_more} so the
    frontend can show a "more" button without bloating the default
    payload. Pass limit=0 for unlimited.

    Handles both wrapped ({DATA: [{ROWS: [...]}]}) and direct ({ROWS:
    [...]}) shapes since FL's response varies."""
    if not raw or not isinstance(raw, dict):
        return None
    rows = raw.get("ROWS")
    if not rows:
        data = raw.get("DATA")
        if isinstance(data, list) and data and isinstance(data[0], dict):
            rows = data[0].get("ROWS")
    if not isinstance(rows, list):
        return None
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        out.append({
            "rank":    r.get("TS_RANK"),
            "name":    r.get("TS_PLAYER_NAME_PA") or r.get("TS_PLAYER_NAME"),
            "team":    r.get("TEAM_NAME"),
            "goals":   r.get("TS_PLAYER_GOALS"),
            "assists": r.get("TS_PLAYER_ASISTS"),
        })
    if not out:
        return None
    total = len(out)
    if limit and limit > 0 and total > limit:
        return {"rows": out[:limit], "total": total, "has_more": True}
    return {"rows": out, "total": total, "has_more": False}


def _slice_top_scorers(payload: dict, limit: int) -> dict:
    """Apply a per-request top_scorers_limit to a cached payload
    without mutating it. The cache stores the full list (limit=0); the
    HTTP layer slices on the way out so successive "Show more" clicks
    don't trigger 17 FL re-probes per pagination step.

    limit <= 0 → return all rows. has_more reflects whether the slice
    truncated the cached list."""
    ts = (payload.get("data") or {}).get("standings", {}).get("top_scorers")
    if not ts or not isinstance(ts.get("rows"), list):
        return payload
    rows = ts["rows"]
    total = ts.get("total", len(rows))
    if limit and limit > 0 and total > limit:
        sliced = {"rows": rows[:limit], "total": total, "has_more": True}
    else:
        sliced = {"rows": rows, "total": total, "has_more": False}
    # Shallow-copy down to the field we changed so we don't poison the cache.
    out = dict(payload)
    out["data"] = dict(payload["data"])
    out["data"]["standings"] = dict(payload["data"]["standings"])
    out["data"]["standings"]["top_scorers"] = sliced
    return out


def _parse_flashlive_lineups(fl_data):
    """Parse FlashLive lineups. Handles both soccer (DATA[0]=home, DATA[1]=away)
    and NHL (each entry has FORMATIONS with FORMATION_LINE 1=home, 2=away)."""
    try:
        data = fl_data if isinstance(fl_data, dict) else {}
        items = data.get("DATA") or []
        if not isinstance(items, list) or not items:
            return None
        # Check if this is NHL-style (FORMATION_LINE separates teams)
        # or soccer-style (DATA[0]=home, DATA[1]=away)
        first = items[0] if items else {}
        formations = first.get("FORMATIONS") or []
        is_nhl_style = False
        for f in formations:
            if isinstance(f, dict) and f.get("FORMATION_LINE") is not None:
                is_nhl_style = True
                break
        if is_nhl_style:
            home_players = []
            away_players = []
            home_subs = []
            away_subs = []
            home_coaches = []
            away_coaches = []
            home_formation = ""
            away_formation = ""
            for entry in items:
                fname = entry.get("FORMATION_NAME") or ""
                fname_low = fname.lower()
                # Distinguish three categories: starters, subs, coaches.
                is_coach_section = "coach" in fname_low
                is_sub_section = "substit" in fname_low
                for f in (entry.get("FORMATIONS") or []):
                    fline = f.get("FORMATION_LINE", 0)
                    if is_coach_section:
                        target_list = home_coaches if fline == 1 else (
                            away_coaches if fline == 2 else None
                        )
                    elif is_sub_section:
                        target_list = home_subs if fline == 1 else (
                            away_subs if fline == 2 else None
                        )
                    else:
                        target_list = home_players if fline == 1 else (
                            away_players if fline == 2 else None
                        )
                    if target_list is None:
                        continue
                    # Extract formation (e.g. "1-4-4-2") from FORMATION_DISPOSTION
                    disp = f.get("FORMATION_DISPOSTION") or ""
                    if disp and not (is_sub_section or is_coach_section):
                        if fline == 1 and not home_formation:
                            home_formation = disp
                        elif fline == 2 and not away_formation:
                            away_formation = disp
                    for p in (f.get("MEMBERS") or []):
                        player_type = p.get("PLAYER_TYPE")
                        pos = ""
                        if player_type == 3 or "(G)" in (p.get("PLAYER_FULL_NAME") or ""):
                            pos = "G"
                        target_list.append({
                            "name": p.get("PLAYER_FULL_NAME") or p.get("SHORT_NAME") or "",
                            "jerseyNumber": p.get("PLAYER_NUMBER"),
                            "position": pos,
                            # LPR: FlashLive's match rating (string,
                            # e.g. "7.3"). LRR: man-of-the-match rank
                            # ("1"/"2"/"3"). INCIDENTS: int codes for
                            # goals/cards/subs/assists. Pass through
                            # raw so the frontend renders them.
                            "rating": p.get("LPR"),
                            "rrank": p.get("LRR"),
                            "incidents": p.get("INCIDENTS") or [],
                        })
            if not home_players and not away_players:
                return None
            return {
                "home": {
                    "formation": home_formation,
                    "players": home_players,
                    "substitutes": home_subs,
                    "coaches": home_coaches,
                },
                "away": {
                    "formation": away_formation,
                    "players": away_players,
                    "substitutes": away_subs,
                    "coaches": away_coaches,
                },
            }
        else:
            # Soccer-style: DATA[0]=home, DATA[1]=away
            result = {}
            for idx, side in enumerate(["home", "away"]):
                if idx >= len(items):
                    break
                entry = items[idx]
                if not isinstance(entry, dict):
                    continue
                formation = entry.get("FORMATION_NAME") or ""
                starters = []
                subs = []
                for f in (entry.get("FORMATIONS") or []):
                    for p in (f.get("MEMBERS") or []):
                        pos = ""
                        pos_id = p.get("PLAYER_POSITION_ID")
                        ptype = p.get("PLAYER_TYPE")
                        if ptype == 3:
                            pos = "GK"
                        elif pos_id == 2:
                            pos = "DEF"
                        elif pos_id == 3:
                            pos = "MID"
                        elif pos_id == 4:
                            pos = "FWD"
                        player = {
                            "name": p.get("PLAYER_FULL_NAME") or p.get("SHORT_NAME") or "",
                            "jerseyNumber": p.get("PLAYER_NUMBER"),
                            "position": pos,
                            # See NHL branch above — LPR / LRR /
                            # INCIDENTS power the inline ratings and
                            # event icons in the lineup view.
                            "rating": p.get("LPR"),
                            "rrank": p.get("LRR"),
                            "incidents": p.get("INCIDENTS") or [],
                        }
                        if p.get("PLAYER_POSITION_ID") == 2:
                            subs.append(player)
                        else:
                            starters.append(player)
                result[side] = {"formation": formation, "players": starters, "substitutes": subs}
            return result if result else None
    except Exception:
        return None


def _parse_flashlive_incidents(fl_data):
    """Parse FlashLive summary-incidents into our timeline format.
    Format: DATA[].ITEMS[].INCIDENT_PARTICIPANTS[].{INCIDENT_TYPE, PARTICIPANT_NAME}
    Also handles simpler summary format as fallback."""
    try:
        data = fl_data if isinstance(fl_data, dict) else {}
        stages = data.get("DATA") or data.get("data") or []
        if not isinstance(stages, list):
            return []
        incidents = []
        for stage in stages:
            if not isinstance(stage, dict):
                continue
            # Add period header from stage data
            stage_name = stage.get("STAGE_NAME") or ""
            rh = stage.get("RESULT_HOME")
            ra = stage.get("RESULT_AWAY")
            if stage_name:
                score_text = f"{rh} - {ra}" if rh is not None and ra is not None else ""
                incidents.append({
                    "time": "", "type": "period", "icon": "",
                    "player": stage_name, "assist": "", "score": score_text,
                    "side": "neutral", "text": stage_name,
                    "isHome": None, "homeScore": rh, "awayScore": ra,
                })
            for inc in (stage.get("ITEMS") or stage.get("items") or []):
                if not isinstance(inc, dict):
                    continue
                minute = inc.get("INCIDENT_TIME") or inc.get("TIME") or ""
                side_val = inc.get("INCIDENT_TEAM") or inc.get("HOME_AWAY") or ""
                side = "home" if str(side_val) in ("1", "home") else "away"
                # summary-incidents: participants nested
                participants = inc.get("INCIDENT_PARTICIPANTS") or []
                if participants:
                    for p in participants:
                        if not isinstance(p, dict):
                            continue
                        inc_type = str(p.get("INCIDENT_TYPE") or "").lower()
                        player = p.get("PARTICIPANT_NAME") or ""
                        icon = ""
                        label = ""
                        if "goal" in inc_type:
                            icon = "⚽"; label = "Goal"
                        elif "yellow" in inc_type:
                            icon = "\U0001f7e8"; label = "Yellow Card"
                        elif "red" in inc_type:
                            icon = "\U0001f7e5"; label = "Red Card"
                        elif "subst" in inc_type:
                            icon = "\U0001f504"; label = "Substitution"
                        elif "penalty" in inc_type or "missed" in inc_type:
                            icon = "P"; label = "Penalty"
                        else:
                            continue
                        incidents.append({
                            "time": str(minute), "type": label, "icon": icon,
                            "player": player, "assist": "", "score": "", "side": side,
                        })
                else:
                    # Simpler format fallback
                    inc_type = str(inc.get("INCIDENT_TYPE") or inc.get("type") or "").lower()
                    player = inc.get("PLAYER_NAME") or inc.get("PARTICIPANT_NAME") or ""
                    assist = inc.get("ASSIST_NAME") or inc.get("ASSIST1_NAME") or ""
                    icon = ""; label = ""
                    if "goal" in inc_type:
                        icon = "⚽"; label = "Goal"
                    elif "yellow" in inc_type:
                        icon = "\U0001f7e8"; label = "Yellow Card"
                    elif "red" in inc_type:
                        icon = "\U0001f7e5"; label = "Red Card"
                    elif "subst" in inc_type:
                        icon = "\U0001f504"; label = "Substitution"
                    elif "penalty" in inc_type:
                        icon = "P"; label = "Penalty"
                    else:
                        continue
                    incidents.append({
                        "time": str(minute), "type": label, "icon": icon,
                        "player": player, "assist": assist,
                        "score": inc.get("SCORE") or "", "side": side,
                    })
        return incidents
    except Exception:
        return []



def _parse_flashlive_stats(fl_data, title, sport):
    """Parse FlashLive statistics response into our standard stats
    format for the sidebar panel.

    FlashLive's stats endpoint nests data three levels deep:
      DATA[].STAGE_NAME              (Match / 1st Half / 2nd Half)
        .GROUPS[].GROUP_LABEL        (Top stats / Shots / Attack / ...)
          .ITEMS[].INCIDENT_NAME     (Total shots, Ball possession, ...)

    The original parser flattened all three levels into a single
    deduped list, which is what the existing `stats` field used to
    feed. We keep that for backward compatibility and add a new
    `stats_grouped` payload that preserves the full structure so the
    frontend can render stage sub-tabs and group section headers
    matching FlashScore's stats view.
    """
    try:
        import re
        parts = re.split(r'\s+(?:vs\.?|v|at)\s+', title, maxsplit=1, flags=re.IGNORECASE)
        home = parts[0].strip() if len(parts) >= 2 else "Home"
        away = parts[1].strip() if len(parts) >= 2 else "Away"
        flat_list: list = []  # legacy flat shape (Match-stage only)
        stages: list = []     # nested shape: [{name, groups: [{label, items: [{name, home, away}]}]}]
        data = fl_data if isinstance(fl_data, list) else fl_data.get("DATA", [])
        if isinstance(data, list):
            for stage in data:
                if not isinstance(stage, dict):
                    continue
                stage_name = stage.get("STAGE_NAME") or stage.get("name") or "Match"
                stage_groups: list = []
                groups_iter = stage.get("GROUPS") or [stage]
                for sg in groups_iter:
                    if not isinstance(sg, dict):
                        continue
                    group_label = sg.get("GROUP_LABEL") or sg.get("LABEL") or ""
                    group_items: list = []
                    for item in (sg.get("ITEMS") or sg.get("items") or [sg]):
                        if not isinstance(item, dict):
                            continue
                        name = (item.get("INCIDENT_NAME") or item.get("NAME")
                                or item.get("name") or "")
                        if not name:
                            continue
                        hval = (item.get("VALUE_HOME") or item.get("HOME")
                                or item.get("home") or "0")
                        aval = (item.get("VALUE_AWAY") or item.get("AWAY")
                                or item.get("away") or "0")
                        row = {
                            "name": str(name),
                            "home": str(hval),
                            "away": str(aval),
                        }
                        group_items.append(row)
                        # Legacy flat list: only the Match stage (avoids
                        # duplicating stats across halves) and dedup by name.
                        if stage_name == "Match":
                            flat_list.append(row)
                    if group_items:
                        stage_groups.append({
                            "label": group_label,
                            "items": group_items,
                        })
                if stage_groups:
                    stages.append({
                        "name": str(stage_name),
                        "groups": stage_groups,
                    })
        # Deduplicate the legacy flat list (existing renderer expects
        # one row per stat name).
        seen = set()
        deduped: list = []
        for s in flat_list:
            if s["name"] not in seen:
                seen.add(s["name"])
                deduped.append(s)
        if not deduped and not stages:
            return None
        return {
            "home": home,
            "away": away,
            "sport": sport,
            "stats": deduped,
            "stats_grouped": stages,
            "source": "flashlive",
        }
    except Exception:
        return None

