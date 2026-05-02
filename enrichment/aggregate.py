"""Pure aggregate-enrichment helpers — no globals, no HTTP.

Stateful enrichment (cache-driven _bracket_aggregate_for_event,
warm loops, prewarm task) stays in main.py until those modules are
extracted in a later refactor pass; the functions here are the
ones that take pure data and return pure data, so they can move
now without touching shared state.
"""


def _bracket_from_warm_cache(stage_id: str, bracket_cache: dict):
    """Return the compacted bracket stored in `bracket_cache` for
    `stage_id`, or None if not cached / missing.

    Pure read of the caller-supplied cache dict (no globals here).
    Used as a fallback in /capabilities and /normalized when the
    on-demand /v1/tournaments/standings?type=draw probe returns
    empty / fails / rate-limits — the warm loop fetches and caches
    the bracket every ~5 minutes, so the cache is more reliable
    than a single fresh probe. Relying solely on the probe was
    making UCL Draw tabs silently disappear when the probe blipped
    even though we had the bracket data sitting in memory.
    """
    if not stage_id or not isinstance(bracket_cache, dict):
        return None
    cached = bracket_cache.get(stage_id)
    return (cached or {}).get("bracket")


def _canonical_game_ticker(event_ticker: str, records: list) -> str:
    """Return the GAME-suffix sibling ticker for any market ticker
    sharing the same fixture suffix. Lets us look up one cache
    entry per fixture instead of one per market — so KXUCLBTTS-…,
    KXUCLSPREAD-…, KXUCL1H-… etc. all resolve to KXUCLGAME-…'s
    /normalized payload (and the bracket / aggregate fields it
    carries).

    Returns the original ticker if it already ends in GAME or no
    sibling is found in records. Records are scanned in the live
    Kalshi cache, so this only matches actual co-existing markets
    on the same fixture date+teams.

    Pattern observed across Kalshi soccer/basketball:
      KXUCLGAME-26MAY06BMUPSG   ← the one we want
      KXUCL1H-26MAY06BMUPSG
      KXUCLSPREAD-26MAY06BMUPSG
      KXUCLTOTAL-26MAY06BMUPSG
      KXUCLBTTS-26MAY06BMUPSG
    Same shape for KXNBA{GAME,SPREAD,TOTAL,STL,REB,PTS,TEAMTOTAL}-
    and every other Kalshi sports market family we've seen."""
    et = (event_ticker or "").upper()
    if not et or "-" not in et:
        return et
    series, _, fixture = et.partition("-")
    if not fixture:
        return et
    if series.endswith("GAME"):
        return et  # already canonical
    for r in (records or []):
        rt = (r.get("event_ticker") or "").upper()
        if "-" not in rt:
            continue
        rs, _, rf = rt.partition("-")
        if rs.endswith("GAME") and rf == fixture:
            return rt
    return et  # no sibling — caller should treat as no-op

def _aggregate_from_bracket(bracket_data, title_home: str, title_away: str):
    """Walk a compact bracket payload for a pair matching the two
    title teams (in either orientation) and return aggregate / leg
    metadata in the FIXTURE's home/away orientation.

    Used for future-fixture knockout ties where FL hasn't loaded the
    upcoming match yet but the league's /v1/tournaments/standings
    bracket already carries the leg-1 score. The compact bracket the
    /normalized endpoint produces is the source — we read from its
    in-memory cache so this is essentially free when the panel has
    been opened in the same process.

    Returns None when no matching pair is found, or when the matched
    pair hasn't started yet (no aggregate posted)."""
    if not bracket_data or not isinstance(bracket_data, dict):
        return None
    rounds = bracket_data.get("rounds") or []
    if not isinstance(rounds, list) or not rounds:
        return None
    h_low = (title_home or "").lower()
    a_low = (title_away or "").lower()
    h_key = h_low.split()[0] if h_low else ""
    a_key = a_low.split()[0] if a_low else ""
    h_prefix = (h_key or h_low)[:3] if (h_key or h_low) else ""
    a_prefix = (a_key or a_low)[:3] if (a_key or a_low) else ""

    def _match_score(name: str, slug: str, full: str, key: str, prefix: str) -> int:
        """Match the title's home/away against a bracket pair's name
        AND slug, returning a quality score (0 = no match, higher =
        stronger). Multi-tier so we can rank candidate pairs and pick
        the strongest match — important when several pairs partially
        match (Toluca played Los Angeles Galaxy in QFs *and* Los
        Angeles FC in SFs; "los angeles f" is a substring of FC but
        only a prefix of Galaxy, so FC wins).

        Tiers:
          100 — full title token is substring of name/slug
                ("los angeles f" ⊂ "los angeles fc")
           50 — first-word "key" matches as substring
                ("atl" key in "atletico-madrid")
           30 — acronym match (PSG → Paris Saint-Germain initials)
           10 — short prefix matches (3-char prefix as substring)
            0 — no match
        """
        if not full:
            return 0

        def _tiered(n: str) -> int:
            if not n:
                return 0
            n = n.lower()
            if full in n:
                return 100
            # Acronym before key — for short tokens we'd rather treat
            # "psg" as initials of "Paris Saint-Germain" (acronym=30)
            # than as a 3-char prefix substring (=10).
            if 2 <= len(full) <= 4 and full.isalnum():
                words = (
                    n.replace("-", " ")
                     .replace("_", " ")
                     .replace(".", " ")
                     .split()
                )
                if words:
                    expanded = []
                    for w in words:
                        if 1 <= len(w) <= 2:
                            expanded.extend(list(w))
                        else:
                            expanded.append(w)
                    initials = "".join(w[0] for w in expanded if w)
                    if initials and full == initials:
                        return 30
            if key and key != full and len(key) >= 4 and key in n:
                return 50
            if prefix and len(prefix) >= 3 and prefix in n:
                return 10
            return 0

        s = _tiered(name)
        if slug:
            s = max(s, _tiered(slug.replace("-", " ").replace("_", " ")))
        return s

    # Collect every candidate pair with its match score and
    # active/finished status, then pick the best. "Best" = highest
    # combined home+away score, with a bonus for active pairs
    # (winner == None) so the in-progress fixture wins over an
    # earlier completed round between the same teams.
    candidates: list = []
    for rnd in rounds:
        if not isinstance(rnd, dict):
            continue
        for pair in (rnd.get("pairs") or []):
            if not isinstance(pair, dict):
                continue
            p_home_name = pair.get("home_name") or ""
            p_away_name = pair.get("away_name") or ""
            p_home_slug = pair.get("home") or ""
            p_away_slug = pair.get("away") or ""
            same_h = _match_score(p_home_name, p_home_slug, h_low, h_key, h_prefix)
            same_a = _match_score(p_away_name, p_away_slug, a_low, a_key, a_prefix)
            swap_h = _match_score(p_home_name, p_home_slug, a_low, a_key, a_prefix)
            swap_a = _match_score(p_away_name, p_away_slug, h_low, h_key, h_prefix)
            if same_h and same_a:
                candidates.append(
                    (same_h + same_a, "same", rnd, pair)
                )
            if swap_h and swap_a:
                candidates.append(
                    (swap_h + swap_a, "swapped", rnd, pair)
                )
    if not candidates:
        return None
    # Sort: highest score first; among equal scores, prefer pairs
    # whose winner is still null (active fixture) over decided ones.
    candidates.sort(
        key=lambda c: (-c[0], 0 if c[3].get("winner") is None else 1)
    )
    _score, orientation, rnd, pair = candidates[0]
    same = (orientation == "same")
    agg_h = pair.get("agg_home")
    agg_a = pair.get("agg_away")
    if agg_h is None or agg_a is None:
        return None
    if same:
        fixture_agg_home, fixture_agg_away = int(agg_h), int(agg_a)
    else:
        fixture_agg_home, fixture_agg_away = int(agg_a), int(agg_h)
    legs_played = len(pair.get("legs") or [])
    leg_number = max(1, min(2, legs_played + 1))
    agg_winner = None
    w = pair.get("winner")
    if w == "home":
        agg_winner = "home" if same else "away"
    elif w == "away":
        agg_winner = "away" if same else "home"
    return {
        "is_two_leg":       True,
        "aggregate_home":   fixture_agg_home,
        "aggregate_away":   fixture_agg_away,
        "leg_number":       leg_number,
        "round_name":       rnd.get("label") or "",
        "aggregate_winner": agg_winner,
    }

