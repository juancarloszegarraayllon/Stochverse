"""Pure helpers used by /api/event/<t>/normalized.

Collapse FL probe results into capability flags, normalize state
strings to the frontend's vocabulary, and extract team-id sets from
standings/bracket payloads for cross-bracket fingerprinting.

All functions are pure: input is data, output is data, no globals,
no HTTP, no main.py-internal dependencies.
"""


def _fl_has_data(resp) -> bool:
    """Decide whether a FlashLive response has 'real' data or is
    null/empty. Used by /api/event/{ticker}/capabilities to gate per-
    event tab visibility. A response counts as having data when it's
    a dict whose DATA contains a non-empty list with at least one
    non-empty item — empty arrays, all-null entries, or completely
    missing DATA all count as no data.
    """
    if not resp or not isinstance(resp, dict):
        return False
    data = resp.get("DATA")
    if data is None:
        # Some endpoints (e.g. summary-incidents) return a list at
        # the top level; treat the whole response as the payload.
        if isinstance(resp.get("INCIDENTS"), list) and resp["INCIDENTS"]:
            return True
        return False
    if isinstance(data, list):
        if not data:
            return False
        # Walk each entry: if any has nested ITEMS / GROUPS / ROWS /
        # MEMBERS / FORMATIONS with at least one element, the
        # endpoint has data. Predicted-lineups in particular ships
        # PLAYERS: [] for tennis even though DATA itself is non-empty
        # — that's "structure without content" and shouldn't gate a
        # tab on.
        nested_keys = ("ITEMS", "GROUPS", "ROWS", "MEMBERS",
                       "FORMATIONS", "PLAYERS", "RESULT_HOME")
        for entry in data:
            if not isinstance(entry, dict):
                if entry:
                    return True
                continue
            for k in nested_keys:
                v = entry.get(k)
                if isinstance(v, list) and v:
                    return True
                if isinstance(v, dict):
                    for inner in v.values():
                        if isinstance(inner, list) and inner:
                            return True
                if v not in (None, "", [], {}):
                    return True
            # Direct scalar payload (e.g. set-by-set summary rows
            # with RESULT_HOME / MATCH_TIME_PART_1 fields).
            for k, v in entry.items():
                if k in ("STAGE_NAME", "TAB_NAME", "FORMATION_NAME"):
                    continue
                if v not in (None, "", [], {}):
                    return True
        return False
    if isinstance(data, dict):
        return bool(data)
    return bool(data)

def _normalized_state(g: dict) -> str:
    s = (g.get("state") or "").lower()
    if s == "in":
        return "live"
    if s == "post":
        return "final"
    return "scheduled"


def _capabilities_from_probes(probe_results: dict) -> dict:
    """Collapse the per-endpoint probe map into clean has_* flags."""
    return {
        "has_summary":           probe_results.get("summary", False)
                                  or probe_results.get("summary_incidents", False),
        "has_stats":             probe_results.get("statistics", False),
        "has_lineups":           probe_results.get("lineups", False),
        "has_predicted_lineups": probe_results.get("predicted_lineups", False),
        "has_player_stats":      probe_results.get("player_stats", False),
        "has_missing_players":   probe_results.get("missing_players", False),
        "has_commentary":        probe_results.get("commentary", False),
        "has_h2h":               probe_results.get("h2h", False),
        "has_news":              probe_results.get("news", False),
        "has_odds":              probe_results.get("odds", False),
        "has_video":             probe_results.get("highlights", False),
        "has_report":            probe_results.get("report", False),
        "has_standings":         any(probe_results.get(f"standings_{t}", False)
                                      for t in ("overall", "home", "away",
                                                 "form", "top_scores",
                                                 "overall_live")),
        "has_bracket":           probe_results.get("standings_draw", False),
    }

def _standings_team_ids(raw):
    """Pull TEAM_IDs from a raw FL standings response. Used to
    fingerprint a league so we can match brackets against it."""
    if not raw or not isinstance(raw, dict):
        return set()
    tids = set()
    for grp in (raw.get("DATA") or []):
        if not isinstance(grp, dict):
            continue
        for r in (grp.get("ROWS") or []):
            if isinstance(r, dict):
                tid = r.get("TEAM_ID")
                if tid:
                    tids.add(tid)
    return tids


def _bracket_team_ids(raw):
    """Pull participant TEAM/PARTICIPANT IDs from a raw FL draw
    response. Walks the tree because FL nests the participant map
    differently per response variant (TABS vs DRAW vs root)."""
    if not raw:
        return set()
    tids = set()
    def walk(o):
        if isinstance(o, dict):
            v = o.get("DRAW_PARTICIPANT_IDS")
            if isinstance(v, dict):
                for pid in v.values():
                    if isinstance(pid, str) and pid:
                        tids.add(pid)
            for child in o.values():
                walk(child)
        elif isinstance(o, list):
            for child in o:
                walk(child)
    walk(raw)
    return tids

