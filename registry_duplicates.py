"""Registry duplicate-candidate detection — Phase D operator tooling.

Surfaces likely duplicates in an `IdentityRegistry`:

  - **Team candidates**: pairs of canonical Teams in the same sport
    whose canonical_name + aliases share enough tokens to suggest
    they refer to the same real-world team. Common cause:
    `seed_kalshi_records` pairs Kalshi tickers via guarded fuzzy
    while `seed_from_fl_response` writes a slightly different slug,
    leaving two `team:<sport>:<slug>` entities for the same club.

  - **Fixture candidates**: pairs of canonical Fixtures in the same
    `(sport, local_date)` bucket whose home/away team-id pair overlaps
    on both sides. Common cause: same real-world game seeded by two
    different FL events, or a reschedule that changed the canonical
    fixture id (Phase C2e — different time component).

Used by `/api/_debug/registry_duplicates` and the cross-sport
`/api/_debug/registry_duplicates_all`. Read-only — does NOT mutate
the registry. Resolution (merging duplicates, retiring stale aliases)
is a separate operator action.
"""
from __future__ import annotations
from typing import Optional


def _name_tokens(s: str, min_len: int = 3) -> set[str]:
    if not s:
        return set()
    out = set()
    cur: list[str] = []
    for ch in s.lower():
        if ch.isalnum():
            cur.append(ch)
        else:
            if cur:
                tok = "".join(cur)
                if len(tok) >= min_len:
                    out.add(tok)
                cur = []
    if cur:
        tok = "".join(cur)
        if len(tok) >= min_len:
            out.add(tok)
    return out


def _team_token_set(team) -> set[str]:
    """Union of tokens from canonical_name and every alias."""
    tokens = _name_tokens(team.canonical_name)
    for a in team.aliases:
        tokens |= _name_tokens(a)
    return tokens


def find_duplicate_team_candidates(registry,
                                     sport: Optional[str] = None,
                                     min_overlap: float = 0.5) -> list:
    """Return pairs of canonical Teams that likely refer to the same
    real-world team.

    Detection: token-set Jaccard on canonical_name + aliases. Pairs
    above `min_overlap` are flagged. Same-team_id self-comparison
    skipped. When `sport` is provided, restricts to teams within
    that sport (no cross-sport candidates).

    Returns a list of dicts:
      {
        "a": team_a.id,
        "b": team_b.id,
        "a_name": team_a.canonical_name,
        "b_name": team_b.canonical_name,
        "overlap": float (Jaccard, 0..1),
        "common_tokens": sorted list of shared tokens,
      }

    Sorted by overlap descending, then by (a, b) for stability.
    """
    teams = list(registry._teams.values())
    if sport is not None:
        teams = [t for t in teams if t.sport == sport]
    out: list = []
    cache: dict = {}
    for i in range(len(teams)):
        a = teams[i]
        ta = cache.get(a.id)
        if ta is None:
            ta = _team_token_set(a)
            cache[a.id] = ta
        if not ta:
            continue
        for j in range(i + 1, len(teams)):
            b = teams[j]
            if a.sport != b.sport:
                continue
            tb = cache.get(b.id)
            if tb is None:
                tb = _team_token_set(b)
                cache[b.id] = tb
            if not tb:
                continue
            shared = ta & tb
            if not shared:
                continue
            union = ta | tb
            jaccard = len(shared) / len(union)
            if jaccard < min_overlap:
                continue
            out.append({
                "a": a.id,
                "b": b.id,
                "a_name": a.canonical_name,
                "b_name": b.canonical_name,
                "overlap": round(jaccard, 3),
                "common_tokens": sorted(shared),
            })
    out.sort(key=lambda d: (-d["overlap"], d["a"], d["b"]))
    return out


def find_duplicate_fixture_candidates(registry,
                                        sport: Optional[str] = None) -> list:
    """Return pairs of canonical Fixtures that likely refer to the
    same real-world game.

    Detection: same `(sport, local_date)` bucket with matching team
    pair (set comparison on home/away team_ids — orientation-blind,
    so PHI-vs-ATH and ATH-vs-PHI on the same date flag as duplicates).
    When `sport` is provided, restricts to that sport.

    Common cause: two FL events seeded two fixtures for the same
    real game, or a reschedule changed the canonical fixture id
    (Phase C2e — different HHMM component for the same matchup).

    Returns a list of dicts:
      {
        "a": fixture_a.id,
        "b": fixture_b.id,
        "sport": str,
        "date": str (ISO),
        "team_pair": [team_id, team_id]  (sorted),
      }

    Sorted by (sport, date, a, b).
    """
    fixtures = list(registry._fixtures.values())
    if sport is not None:
        fixtures = [f for f in fixtures if f.sport == sport]

    # Bucket by (sport, local_date or when).
    buckets: dict = {}
    for f in fixtures:
        d = f.local_date or f.when
        if d is None:
            continue
        key = (f.sport, d.isoformat())
        buckets.setdefault(key, []).append(f)

    out: list = []
    for (s, dstr), fxs in buckets.items():
        if len(fxs) < 2:
            continue
        for i in range(len(fxs)):
            a = fxs[i]
            a_pair = frozenset({a.home_team_id, a.away_team_id})
            for j in range(i + 1, len(fxs)):
                b = fxs[j]
                if a.id == b.id:
                    continue
                b_pair = frozenset({b.home_team_id, b.away_team_id})
                if a_pair != b_pair:
                    continue
                out.append({
                    "a": a.id,
                    "b": b.id,
                    "sport": s,
                    "date": dstr,
                    "team_pair": sorted(a_pair),
                })
    out.sort(key=lambda d: (d["sport"], d["date"], d["a"], d["b"]))
    return out


def summarize_for_notification(team_candidates: list,
                                 fixture_candidates: list,
                                 max_examples: int = 3) -> dict:
    """Build a compact, alert-friendly payload.

    Suitable for posting to a Discord/Slack webhook or rendering in
    an email body. Caps example lists so the message stays small.
    """
    return {
        "duplicate_team_candidates_count":    len(team_candidates),
        "duplicate_fixture_candidates_count": len(fixture_candidates),
        "top_team_candidates": [
            {"a": c["a"], "b": c["b"], "overlap": c["overlap"]}
            for c in team_candidates[:max_examples]
        ],
        "top_fixture_candidates": [
            {"a": c["a"], "b": c["b"], "date": c["date"]}
            for c in fixture_candidates[:max_examples]
        ],
    }
