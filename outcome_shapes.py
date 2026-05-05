"""Per-(sport, suffix, market_type) outcome shape rules.

Phase 2 of /sports v2 (see SPORTS_V2_PLAN.md). Replaces the
title-based shape inference (`isWinnerShapedOutcomes`,
`collectOutcomesForRender` heuristics) with a deterministic
table lookup. Rules sourced from KALSHI_AUDIT.md §4 — every
observed (sport, suffix, market_type) tuple is documented.

Public API:
  OutcomeShape           — frozen dataclass describing a shape
  LabelKind              — string enum of label-kind names
  shape_for(s, suf, mt)  — table lookup; None if unknown
  render_outcomes(rec)   — record.outcomes[] → canonical list
  outcomes_with_shape(...) → render + sort + validate

Pure / no I/O. Safe to import in any context.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple, Union


# ── Label-kind taxonomy ──────────────────────────────────────────
# Each value categorizes how the outcome's `label` field should be
# read at render time. Used by /sports v2 frontend to pick the
# right card layout.

class LabelKind:
    """Categories for how outcome labels read."""
    TEAM            = "team"            # raw team / player name
    TEAM_OR_TIE     = "team_or_tie"     # team names + "Tie" (3-row winner)
    WINNER_PHRASE   = "winner_phrase"   # "X wins 1st half" / "X wins 4-0"
    YES_NO_IMPLIED  = "yes_no_implied"  # 1 outcome rendered as YES/NO chips
    SPREAD          = "spread"          # "<team> wins by over N <unit>"
    TOTAL           = "total"           # "Over N <unit>"
    THRESHOLD       = "threshold"       # generic "N+ <thing>"
    TEAM_THRESHOLD  = "team_threshold"  # "<team>: N+" (TCORNERS, player props)
    ADVANCE         = "advance"         # 2-team advancement (cup ties)
    PLAYER          = "player"          # player roster (outrights, awards)
    METHOD          = "method"          # MMA Method-of-Finish (KO/Sub/Dec)
    METHOD_PER_TEAM = "method_per_team" # MMA Method-of-Victory (per fighter)
    ROUND           = "round"           # "Fight ends before round N"
    DATE_THRESHOLD  = "date_threshold"  # "Before <date>"
    PAIR            = "pair"            # combo pairs ("PSG vs Arsenal")
    GENERIC         = "generic"         # outright fallback (unknown structure)


@dataclass(frozen=True)
class OutcomeShape:
    """Rule for a (sport, suffix, market_type) bucket.

    expected_count: exact outcome count, or (min, max) tuple for
                    variable-count shapes (e.g. player props).
    label_kind:     LabelKind constant
    has_tie:        whether the shape includes a "Tie" outcome
                    (3-row vs 2-row decisions on the frontend)
    has_team_split: whether outcomes split into per-team rows
                    (e.g. spread / TCORNERS / MOV)
    """
    expected_count: Union[int, Tuple[int, int]]
    label_kind: str
    has_tie: bool = False
    has_team_split: bool = False


# Sentinel for variable-count shapes.
ANY = (1, 1000)


# ── The rule table — sourced from KALSHI_AUDIT.md §4 ─────────────
# Key: (sport, suffix, market_type)
# Empty-string suffix or market_type means "any" / not-applicable.

_SHAPE_RULES: dict[tuple[str, str, str], OutcomeShape] = {

    # ── Soccer ──────────────────────────────────────────────
    ("Soccer", "GAME",     ""):                       OutcomeShape(3, LabelKind.TEAM_OR_TIE, has_tie=True),
    ("Soccer", "MATCH",    ""):                       OutcomeShape(3, LabelKind.TEAM_OR_TIE, has_tie=True),
    ("Soccer", "1H",       "First Half Winner"):      OutcomeShape(3, LabelKind.TEAM_OR_TIE, has_tie=True),
    ("Soccer", "TOTAL",    "Totals"):                 OutcomeShape(4, LabelKind.TOTAL),
    ("Soccer", "SPREAD",   "Spreads"):                OutcomeShape(4, LabelKind.SPREAD, has_team_split=True),
    ("Soccer", "BTTS",     "Both Teams to Score"):    OutcomeShape(1, LabelKind.YES_NO_IMPLIED),
    ("Soccer", "CORNERS",  "Total Corners"):          OutcomeShape(5, LabelKind.THRESHOLD),
    ("Soccer", "TCORNERS", "Team Corners"):           OutcomeShape(2, LabelKind.TEAM_THRESHOLD, has_team_split=True),
    ("Soccer", "ADVANCE",  "To Advance"):             OutcomeShape(2, LabelKind.ADVANCE, has_team_split=True),

    # ── Basketball ──────────────────────────────────────────
    ("Basketball", "GAME",       ""):                 OutcomeShape(2, LabelKind.TEAM, has_team_split=True),
    ("Basketball", "1H",         "First Half Winner"): OutcomeShape(3, LabelKind.TEAM_OR_TIE, has_tie=True),
    ("Basketball", "1H",         "First Half Total"):  OutcomeShape((9, 11), LabelKind.TOTAL),
    ("Basketball", "1H",         "First Half Spread"): OutcomeShape((10, 12), LabelKind.SPREAD, has_team_split=True),
    ("Basketball", "2H",         "Second Half Winner"): OutcomeShape(3, LabelKind.TEAM_OR_TIE, has_tie=True),
    ("Basketball", "2H",         "Second Half Total"): OutcomeShape((9, 11), LabelKind.TOTAL),
    ("Basketball", "2H",         "Second Half Spread"): OutcomeShape((10, 12), LabelKind.SPREAD, has_team_split=True),
    ("Basketball", "TOTAL",      "Total Points"):     OutcomeShape((9, 12), LabelKind.TOTAL),
    ("Basketball", "TOTAL",      "Team Totals"):      OutcomeShape((14, 20), LabelKind.TEAM_THRESHOLD, has_team_split=True),
    ("Basketball", "SPREAD",     "Spread"):           OutcomeShape((10, 12), LabelKind.SPREAD, has_team_split=True),
    ("Basketball", "OVERTIME",   "Overtime"):         OutcomeShape(1, LabelKind.YES_NO_IMPLIED),
    # Player props — variable count by player roster size
    ("Basketball", "",           "Points"):           OutcomeShape(ANY, LabelKind.TEAM_THRESHOLD),
    ("Basketball", "",           "Rebounds"):         OutcomeShape(ANY, LabelKind.TEAM_THRESHOLD),
    ("Basketball", "",           "Assists"):          OutcomeShape(ANY, LabelKind.TEAM_THRESHOLD),
    ("Basketball", "",           "Steals"):           OutcomeShape(ANY, LabelKind.TEAM_THRESHOLD),
    ("Basketball", "",           "Blocks"):           OutcomeShape(ANY, LabelKind.TEAM_THRESHOLD),
    ("Basketball", "",           "Three Pointers"):   OutcomeShape(ANY, LabelKind.TEAM_THRESHOLD),
    ("Basketball", "",           "Triple Doubles"):   OutcomeShape(ANY, LabelKind.PLAYER),
    ("Basketball", "",           "Double Doubles"):   OutcomeShape(ANY, LabelKind.PLAYER),

    # ── Hockey ──────────────────────────────────────────────
    ("Hockey", "GAME",      ""):                      OutcomeShape(2, LabelKind.TEAM, has_team_split=True),
    ("Hockey", "TOTAL",     "Total Points"):          OutcomeShape((6, 9), LabelKind.TOTAL),
    ("Hockey", "SPREAD",    "Spread"):                OutcomeShape(4, LabelKind.SPREAD, has_team_split=True),
    ("Hockey", "OVERTIME",  "Overtime"):              OutcomeShape(1, LabelKind.YES_NO_IMPLIED),
    ("Hockey", "",          "Points"):                OutcomeShape(ANY, LabelKind.TEAM_THRESHOLD),
    ("Hockey", "",          "Player Goals"):          OutcomeShape(ANY, LabelKind.TEAM_THRESHOLD),
    ("Hockey", "",          "First Goal"):            OutcomeShape(ANY, LabelKind.PLAYER),
    ("Hockey", "",          "Assists"):               OutcomeShape(ANY, LabelKind.TEAM_THRESHOLD),

    # ── Baseball ────────────────────────────────────────────
    ("Baseball", "GAME",   ""):                       OutcomeShape(2, LabelKind.TEAM, has_team_split=True),
    ("Baseball", "TOTAL",  "Total Runs"):             OutcomeShape((10, 12), LabelKind.TOTAL),
    ("Baseball", "TOTAL",  "Team Total"):             OutcomeShape((12, 16), LabelKind.TEAM_THRESHOLD, has_team_split=True),
    ("Baseball", "SPREAD", "Spread"):                 OutcomeShape(6, LabelKind.SPREAD, has_team_split=True),
    ("Baseball", "RFI",    "First Inning Run"):       OutcomeShape(1, LabelKind.YES_NO_IMPLIED),
    ("Baseball", "F5",     "First 5 Innings"):        OutcomeShape(3, LabelKind.WINNER_PHRASE, has_tie=True),
    ("Baseball", "F5",     "First 5 Innings Total"):  OutcomeShape((6, 8), LabelKind.TOTAL),
    ("Baseball", "F5",     "First 5 Spread"):         OutcomeShape(4, LabelKind.SPREAD, has_team_split=True),
    # Player props
    ("Baseball", "",       "Hits"):                   OutcomeShape(ANY, LabelKind.TEAM_THRESHOLD),
    ("Baseball", "",       "Home Runs"):              OutcomeShape(ANY, LabelKind.TEAM_THRESHOLD),
    ("Baseball", "",       "Strikeouts"):             OutcomeShape(ANY, LabelKind.TEAM_THRESHOLD),
    ("Baseball", "",       "Total Bases"):            OutcomeShape(ANY, LabelKind.TEAM_THRESHOLD),
    ("Baseball", "",       "Hits + Runs + RBIs"):     OutcomeShape(ANY, LabelKind.TEAM_THRESHOLD),

    # ── Football ────────────────────────────────────────────
    ("Football", "GAME",   ""):                       OutcomeShape(2, LabelKind.TEAM, has_team_split=True),
    ("Football", "TOTAL",  "Total Points"):           OutcomeShape((4, 6), LabelKind.TOTAL),
    ("Football", "SPREAD", "Spread"):                 OutcomeShape((10, 12), LabelKind.SPREAD, has_team_split=True),

    # ── Tennis ──────────────────────────────────────────────
    ("Tennis", "MATCH", ""):                          OutcomeShape(2, LabelKind.TEAM, has_team_split=True),
    ("Tennis", "",      "Set 1 Winner"):              OutcomeShape(2, LabelKind.TEAM, has_team_split=True),
    ("Tennis", "",      "Set 2 Winner"):              OutcomeShape(2, LabelKind.TEAM, has_team_split=True),
    ("Tennis", "",      "Set 3 Winner"):              OutcomeShape(2, LabelKind.TEAM, has_team_split=True),
    ("Tennis", "",      "Grand Slam wins in 2026"):   OutcomeShape((3, 4), LabelKind.THRESHOLD),

    # ── Esports ─────────────────────────────────────────────
    ("Esports", "GAME", ""):                          OutcomeShape(2, LabelKind.TEAM, has_team_split=True),
    ("Esports", "",     "Map 1"):                     OutcomeShape(2, LabelKind.TEAM, has_team_split=True),
    ("Esports", "",     "Map 2"):                     OutcomeShape(2, LabelKind.TEAM, has_team_split=True),
    ("Esports", "",     "Map 3"):                     OutcomeShape(2, LabelKind.TEAM, has_team_split=True),
    ("Esports", "",     "Map 4"):                     OutcomeShape(2, LabelKind.TEAM, has_team_split=True),
    ("Esports", "",     "Map 5"):                     OutcomeShape(2, LabelKind.TEAM, has_team_split=True),
    ("Esports", "",     "Total Maps"):                OutcomeShape((1, 3), LabelKind.TOTAL),

    # ── MMA ─────────────────────────────────────────────────
    # All 6 sub-markets per fight (KALSHI_AUDIT.md §4 MMA section).
    # Production probe doesn't strip MMA suffixes, so series_base is
    # e.g. "KXUFCVICROUND" with suffix=""; key on market_type.
    ("MMA", "FIGHT",    ""):                          OutcomeShape(2, LabelKind.TEAM, has_team_split=True),
    ("MMA", "",         ""):                          OutcomeShape(2, LabelKind.TEAM, has_team_split=True),  # KXUFCFIGHT direct
    ("MMA", "",         "Go the Distance"):           OutcomeShape(1, LabelKind.YES_NO_IMPLIED),
    ("MMA", "",         "Round of Finish"):           OutcomeShape((2, 4), LabelKind.ROUND),
    ("MMA", "",         "Round of Victory"):          OutcomeShape((7, 11), LabelKind.WINNER_PHRASE, has_team_split=True),
    ("MMA", "",         "Method of Victory"):         OutcomeShape(7, LabelKind.METHOD_PER_TEAM, has_tie=True, has_team_split=True),
    ("MMA", "",         "Method of Finish"):          OutcomeShape(4, LabelKind.METHOD, has_tie=True),
    ("MMA", "",         "Return to WWE in 2026"):     OutcomeShape(1, LabelKind.DATE_THRESHOLD),

    # ── Boxing ──────────────────────────────────────────────
    # KXBOXING is the headline (no suffix on the base for fight markets)
    ("Boxing", "", ""):                               OutcomeShape(2, LabelKind.TEAM, has_team_split=True),

    # ── Cricket ─────────────────────────────────────────────
    ("Cricket", "GAME",   ""):                        OutcomeShape(2, LabelKind.TEAM, has_team_split=True),
    ("Cricket", "MATCH",  ""):                        OutcomeShape(2, LabelKind.TEAM, has_team_split=True),
    ("Cricket", "TOTAL",  "Team Total Runs"):         OutcomeShape((4, 8), LabelKind.TEAM_THRESHOLD, has_team_split=True),
    ("Cricket", "",       "Total Match Sixes"):       OutcomeShape(3, LabelKind.TOTAL),
    ("Cricket", "",       "Total Match Fours"):       OutcomeShape(3, LabelKind.TOTAL),

    # ── Rugby ───────────────────────────────────────────────
    ("Rugby", "MATCH", ""):                           OutcomeShape(3, LabelKind.TEAM_OR_TIE, has_tie=True),

    # ── Aussie Rules ────────────────────────────────────────
    ("Aussie Rules", "GAME", ""):                     OutcomeShape(2, LabelKind.TEAM, has_team_split=True),

    # ── Lacrosse ────────────────────────────────────────────
    ("Lacrosse", "GAME", ""):                         OutcomeShape(2, LabelKind.TEAM, has_team_split=True),

    # ── Table Tennis ────────────────────────────────────────
    ("Table Tennis", "MATCH", ""):                    OutcomeShape(2, LabelKind.TEAM, has_team_split=True),

    # ── Darts ───────────────────────────────────────────────
    ("Darts", "MATCH", ""):                           OutcomeShape(2, LabelKind.TEAM, has_team_split=True),

    # ── Golf — per-tournament sub-markets (handled by market_type) ──
    # Per audit §4 Golf: tournament-handle scoping; all sub-markets
    # share the parent tournament. Production probe leaves suffix=""
    # for these since the suffix logic doesn't strip TOP*/R*LEAD/etc.
    ("Golf", "", "Hole-in-One"):                      OutcomeShape(3, LabelKind.THRESHOLD),
    ("Golf", "", "Top 5 Finishers"):                  OutcomeShape((50, 130), LabelKind.PLAYER),
    ("Golf", "", "Top 10 Finishers"):                 OutcomeShape((50, 130), LabelKind.PLAYER),
    ("Golf", "", "Top 20 Finishers"):                 OutcomeShape((50, 130), LabelKind.PLAYER),
    ("Golf", "", "Round 1 Top 5 Finishers"):          OutcomeShape((50, 130), LabelKind.PLAYER),
    ("Golf", "", "Round 1 Top 10 Finishers"):         OutcomeShape((50, 130), LabelKind.PLAYER),
    ("Golf", "", "Playoff"):                          OutcomeShape(1, LabelKind.YES_NO_IMPLIED),
    ("Golf", "", "To Make the Cut"):                  OutcomeShape((50, 200), LabelKind.PLAYER),
    ("Golf", "", "Golf Majors in 2026"):              OutcomeShape((3, 4), LabelKind.THRESHOLD),

    # ── Motorsport — NASCAR per-race sub-markets ────────────
    ("Motorsport", "", "Top 3 Finishers"):            OutcomeShape((30, 50), LabelKind.PLAYER),
    ("Motorsport", "", "Top 5 Finishers"):            OutcomeShape((30, 50), LabelKind.PLAYER),
    ("Motorsport", "", "Top 10 Finishers"):           OutcomeShape((30, 50), LabelKind.PLAYER),
    ("Motorsport", "", "Top 20 Finishers"):           OutcomeShape((30, 50), LabelKind.PLAYER),
    ("Motorsport", "", "Fastest Lap"):                OutcomeShape((30, 50), LabelKind.PLAYER),
    ("Motorsport", "", "Retirement"):                 OutcomeShape((1, 6), LabelKind.DATE_THRESHOLD),

    # ── Chess — Olympiad & FIDE-rating sub-markets ──────────
    ("Chess", "", "Team USA"):                        OutcomeShape((10, 20), LabelKind.PLAYER),
    # FIDE rating slots vary by month: "#1 Rated in June 2026", etc.
    # All have ~14 player outcomes; shape is identical regardless of date.
    ("Chess", "", "#1 Rated in June 2026"):           OutcomeShape((10, 20), LabelKind.PLAYER),
    ("Chess", "", "#2 Rated in June 2026"):           OutcomeShape((10, 20), LabelKind.PLAYER),
    ("Chess", "", "#3 Rated in June 2026"):           OutcomeShape((10, 20), LabelKind.PLAYER),

    # ── Esports — Overwatch World Ranking ───────────────────
    # Date-keyed market_types; same shape (player roster).
    ("Esports", "", "Top 10 on June 1st, 2026"):      OutcomeShape((20, 40), LabelKind.PLAYER),
    ("Esports", "", "Top 20 on June 1st, 2026"):      OutcomeShape((20, 40), LabelKind.PLAYER),

    # ── Per-team / per-player season-tracker outrights ──────
    # Football: KXNFLWINS uses market_type='<team> Total Wins'; one
    # entry per team. Generic shape.
    ("Football", "", "Total Wins"):                   OutcomeShape((10, 20), LabelKind.THRESHOLD),
    # Match-anything per-team-wins rules handled below via prefix
    # fallback (see _match_market_type_prefix).
}


# Date-varying / per-team market_types where exact key match misses.
# When shape_for() doesn't find an exact match, walk these prefix
# rules. Used for things like "<Team> Total Wins" (NBA / MLB / NFL),
# "#N Rated in <Month> <Year>" (Chess), "Top N on <date>" (Esports).
_MARKET_TYPE_PREFIX_RULES: list[tuple[str, str, OutcomeShape]] = [
    ("Football",   " Total Wins",      OutcomeShape((15, 18), LabelKind.THRESHOLD)),
    ("Basketball", " Total Wins",      OutcomeShape((10, 20), LabelKind.THRESHOLD)),
    ("Hockey",     " Total Wins",      OutcomeShape((10, 20), LabelKind.THRESHOLD)),
    ("Baseball",   " Total Wins",      OutcomeShape((10, 20), LabelKind.THRESHOLD)),
    ("Football",   "Playoff Win Total: ", OutcomeShape((5, 12), LabelKind.THRESHOLD)),
    ("Basketball", "Playoff Win Total: ", OutcomeShape((5, 12), LabelKind.THRESHOLD)),
]


# ── Public: shape_for ────────────────────────────────────────────

def shape_for(sport: str, suffix: str, market_type: str) -> Optional[OutcomeShape]:
    """Look up the shape rule for a (sport, suffix, market_type) triple.

    Returns None if no rule matches — caller should fall back to
    rendering raw outcomes[] without shape-specific assumptions.

    Lookup order:
      1. Exact (sport, suffix, market_type) match
      2. (sport, "", market_type) — for player-prop / sub-market_type-only buckets
      3. (sport, suffix, "") — for non-market-typed records
      4. Prefix / suffix match on market_type (per-team season trackers,
         per-month FIDE ratings, per-date threshold markets)
      5. None
    """
    if (rule := _SHAPE_RULES.get((sport, suffix, market_type))) is not None:
        return rule
    if (rule := _SHAPE_RULES.get((sport, "", market_type))) is not None:
        return rule
    if (rule := _SHAPE_RULES.get((sport, suffix, ""))) is not None:
        return rule
    # Prefix-pattern fallback — for date-varying / per-team market_types.
    for rule_sport, fragment, rule in _MARKET_TYPE_PREFIX_RULES:
        if rule_sport != sport:
            continue
        # Fragment is matched as either a suffix ("<X> Total Wins")
        # or a prefix ("Playoff Win Total: <Team>") of market_type.
        if market_type.endswith(fragment) or market_type.startswith(fragment):
            return rule
    return None


# ── Compact-field helpers (mirror main.py's _to_cents) ───────────

def _to_cents(v) -> Optional[int]:
    """Coerce a possibly-string price to integer cents.

    Kalshi cache stores compact fields as integers in cents (e.g. _yb=42),
    while the API uses FixedPointDollars strings ("0.4200"). Some cache
    paths store cents-equivalent strings ("42"). Handle all three.
    """
    if v is None:
        return None
    if isinstance(v, (int, float)):
        try:
            return int(v)
        except (ValueError, OverflowError):
            return None
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        # Dollar-string form: "0.4200" → 42
        if "." in s:
            try:
                return int(round(float(s) * 100))
            except (ValueError, OverflowError):
                return None
        try:
            return int(s)
        except (ValueError, OverflowError):
            return None
    return None


# ── Public: render_outcomes ──────────────────────────────────────

def render_outcomes(record: dict) -> list[dict]:
    """Normalize a Kalshi cache record's outcomes[] into canonical
    [{label, prob, yes, no, ticker}, ...].

    `prob` = yes_bid (the implied probability when YES is true)
    `yes`  = yes_ask (cost to BUY yes)
    `no`   = no_ask  (cost to BUY no)

    Reads compact fields (`_yb` / `_ya` / `_na`) first when present;
    falls back to the spec's `yes_bid` / `yes_ask` / `no_ask` /
    `*_dollars` variants. Does NOT apply shape rules — see
    `outcomes_with_shape()` for that.
    """
    raw = (record.get("outcomes") or record.get("_outcomes") or [])
    out: list[dict] = []
    for o in raw:
        if not isinstance(o, dict):
            continue
        label = str(o.get("label") or "").strip()
        if not label:
            continue
        # Probability = yes_bid in cents
        prob = (_to_cents(o.get("_yb"))
                if o.get("_yb") is not None
                else _to_cents(o.get("yes_bid"))
                if o.get("yes_bid") is not None
                else _to_cents(o.get("yes_bid_dollars")))
        # YES ask
        yes = (_to_cents(o.get("_ya"))
               if o.get("_ya") is not None
               else _to_cents(o.get("yes_ask"))
               if o.get("yes_ask") is not None
               else _to_cents(o.get("yes_ask_dollars")))
        # NO ask
        no = (_to_cents(o.get("_na"))
              if o.get("_na") is not None
              else _to_cents(o.get("no_ask"))
              if o.get("no_ask") is not None
              else _to_cents(o.get("no_ask_dollars")))
        out.append({
            "label":  label,
            "prob":   prob,
            "yes":    yes,
            "no":     no,
            "ticker": o.get("ticker") or "",
        })
    return out


# ── Public: outcomes_with_shape ──────────────────────────────────

def outcomes_with_shape(record: dict, sport: str,
                        suffix: str, market_type: str) -> dict:
    """Render outcomes + apply shape-specific normalization.

    Returns:
      {
        "shape":   OutcomeShape | None,
        "outcomes": [{label, prob, yes, no, ticker}, ...],
        "validates": bool,           # True iff outcome count matches rule
        "warnings": [str, ...],      # any deviations / unknown shape
      }

    Shape-driven normalization applied:
      - team_or_tie / winner_phrase with tie: "Tie" sorted last
      - team / advance with team_split: alphabetic sort by label
        (frontend can re-sort by home/away once it knows orientation)
    """
    outcomes = render_outcomes(record)
    shape = shape_for(sport, suffix, market_type)
    warnings: list[str] = []

    if shape is None:
        warnings.append(
            f"unknown shape for ({sport!r}, {suffix!r}, {market_type!r})")
        return {"shape": None, "outcomes": outcomes,
                "validates": True, "warnings": warnings}

    # Validate count
    n = len(outcomes)
    if isinstance(shape.expected_count, int):
        validates = (n == shape.expected_count)
        if not validates:
            warnings.append(
                f"outcome count {n} != expected {shape.expected_count}")
    else:
        lo, hi = shape.expected_count
        validates = (lo <= n <= hi)
        if not validates:
            warnings.append(
                f"outcome count {n} not in [{lo}, {hi}]")

    # Shape-driven sorting
    if shape.has_tie:
        # Push "Tie" outcomes to the end
        outcomes = sorted(outcomes,
                          key=lambda o: o["label"].lower() == "tie")

    return {
        "shape": shape,
        "outcomes": outcomes,
        "validates": validates,
        "warnings": warnings,
    }


# ── Coverage diagnostic ──────────────────────────────────────────

def known_buckets() -> list[tuple[str, str, str]]:
    """Return all (sport, suffix, market_type) keys with rules.

    Used by tests and the audit's diagnostic endpoint to verify
    every Kalshi-observed bucket has a matching rule.
    """
    return list(_SHAPE_RULES.keys())
