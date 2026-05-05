"""Competition → timezone mapping for canonical local_date computation.

Per Phase C2d of SPORTS_V2_PLAN.md. Resolves the multi-game-series
false-positive class (NBA playoffs back-to-back games, KBO 3-game
series, MLB 3-4 game series) by anchoring each Fixture's canonical
date to its LOCAL game date rather than its UTC start_time date.

The matching pattern this fixes:
    FL ships UTC start_time. For an evening Argentine game (21:00 ART),
    UTC = next day 00:00. Kalshi tickers use the LOCAL game date
    (ART = May 5). Without timezone awareness, the FL fixture's UTC
    date (May 6) doesn't match the Kalshi ticker date (May 5).

    With timezone awareness, the Fixture's local_date is computed
    using the competition's home timezone (ART), giving local_date
    = May 5, which DOES match the Kalshi ticker.

This same mechanism cleanly disambiguates NBA Game 1 from Game 2:
each game has its own FL fixture with its own local_date in the
home arena's timezone, so Kalshi's MAY05 ticker pairs only to
Game 1's fixture (local_date May 5) and MAY07 ticker pairs only
to Game 2's fixture (local_date May 7). No cross-game gluing.

API:
    competition_tz(competition_name, sport) -> str
        Returns an IANA timezone name (e.g. 'America/New_York').
    compute_local_date(utc_ts, tz_name) -> date
        Convert UTC epoch seconds + tz to a local calendar date.

Limitations (known):
    * Per-LEAGUE timezone, not per-TEAM. NBA West Coast late-evening
      games (>9 PM PT crossing into next day ET) will get wrong
      local_date because we use ET as NBA default. Per-team TZ map
      is a follow-up — most NBA games are not affected since they
      tip off 7-9 PM local and ET-equivalent local dates align.
    * Tennis tournaments use the venue timezone, not the player's;
      defaults to UTC for now (most tennis pairs at exact UTC date
      anyway since matches don't cross midnight UTC frequently).
"""
from __future__ import annotations
from datetime import date, datetime
from zoneinfo import ZoneInfo


# Competition-name substring → IANA timezone. Most-specific first
# (a substring match is sufficient to pin down the league). Order
# of evaluation IS the priority: first hit wins. Add carefully.
_COMPETITION_PATTERNS_TZ: list = [
    # ── Soccer (top) ──────────────────────────────────────────
    ("Champions League",       "Europe/London"),
    ("Europa League",          "Europe/London"),
    ("Premier League",         "Europe/London"),
    ("La Liga",                "Europe/Madrid"),
    ("LaLiga",                 "Europe/Madrid"),
    ("Bundesliga",             "Europe/Berlin"),
    ("Serie A",                "Europe/Rome"),
    ("Ligue 1",                "Europe/Paris"),
    ("Eredivisie",             "Europe/Amsterdam"),
    ("Primeira Liga",          "Europe/Lisbon"),
    # ── Soccer (regional) ─────────────────────────────────────
    ("MLS",                    "America/New_York"),
    ("Major League Soccer",    "America/New_York"),
    ("CONMEBOL",               "America/Argentina/Buenos_Aires"),
    ("Libertadores",           "America/Argentina/Buenos_Aires"),
    ("Sudamericana",           "America/Argentina/Buenos_Aires"),
    ("Brasileirao",            "America/Sao_Paulo"),
    ("Saudi",                  "Asia/Riyadh"),
    ("AFC",                    "Asia/Tokyo"),  # rough — AFC zone varies
    ("J1 League",              "Asia/Tokyo"),
    ("J. League",              "Asia/Tokyo"),
    ("K League",               "Asia/Seoul"),
    ("A-League",               "Australia/Sydney"),
    # ── Basketball ────────────────────────────────────────────
    ("NBA",                    "America/New_York"),
    ("WNBA",                   "America/New_York"),
    ("NCAA",                   "America/New_York"),
    ("Euroleague",             "Europe/Madrid"),
    ("EuroLeague",             "Europe/Madrid"),
    ("BBL",                    "Europe/Berlin"),
    ("ACB",                    "Europe/Madrid"),
    ("ABA",                    "Europe/Belgrade"),
    ("CBA",                    "Asia/Shanghai"),
    ("KBL",                    "Asia/Seoul"),
    ("JBL",                    "Asia/Tokyo"),
    ("BSL",                    "Europe/Istanbul"),
    ("NZ NBL",                 "Pacific/Auckland"),
    ("NBL",                    "Australia/Sydney"),
    ("LNB",                    "Europe/Paris"),
    ("FIBA",                   "Europe/Geneva"),  # rough — varies
    # ── Hockey ────────────────────────────────────────────────
    ("NHL",                    "America/New_York"),
    ("AHL",                    "America/New_York"),
    ("KHL",                    "Europe/Moscow"),
    ("SHL",                    "Europe/Stockholm"),
    ("Liiga",                  "Europe/Helsinki"),
    ("Czech Extraliga",        "Europe/Prague"),
    ("DEL",                    "Europe/Berlin"),
    # ── Baseball ──────────────────────────────────────────────
    ("MLB",                    "America/New_York"),
    ("KBO",                    "Asia/Seoul"),
    ("CPBL",                   "Asia/Taipei"),
    ("NPB",                    "Asia/Tokyo"),
    ("LMB",                    "America/Mexico_City"),
    # ── Football ──────────────────────────────────────────────
    ("NFL",                    "America/New_York"),
    ("CFL",                    "America/Toronto"),
    # ── Australian Rules ──────────────────────────────────────
    ("AFL",                    "Australia/Melbourne"),
    ("Aussie Rules",           "Australia/Melbourne"),
    # ── Cricket ───────────────────────────────────────────────
    ("IPL",                    "Asia/Kolkata"),
    ("Big Bash",               "Australia/Sydney"),
    ("County Championship",    "Europe/London"),
    # ── Rugby ─────────────────────────────────────────────────
    ("Premiership Rugby",      "Europe/London"),
    ("Top 14",                 "Europe/Paris"),
    ("Super Rugby",            "Pacific/Auckland"),
    ("NRL",                    "Australia/Sydney"),
]


# Per-sport fallback when no competition pattern matches. Order
# of preference: most-common region for the sport.
_SPORT_DEFAULT_TZ: dict = {
    "Soccer":       "Europe/London",
    "Basketball":   "America/New_York",
    "Baseball":     "America/New_York",
    "Hockey":       "America/New_York",
    "Football":     "America/New_York",
    "Tennis":       "UTC",
    "Esports":      "UTC",
    "MMA":          "America/New_York",
    "Boxing":       "America/New_York",
    "Golf":         "America/New_York",
    "Motorsport":   "Europe/London",
    "Cricket":      "Asia/Kolkata",
    "Rugby":        "Europe/London",
    "Aussie Rules": "Australia/Melbourne",
}


def competition_tz(competition_name: str, sport: str) -> str:
    """Resolve an IANA timezone for the given competition + sport.

    Walks `_COMPETITION_PATTERNS_TZ` in declaration order, returns
    the first substring match (case-insensitive). Falls back to
    `_SPORT_DEFAULT_TZ` for the sport. Final fallback is `'UTC'`.
    """
    name_upper = (competition_name or "").upper()
    for substr, tz in _COMPETITION_PATTERNS_TZ:
        if substr.upper() in name_upper:
            return tz
    return _SPORT_DEFAULT_TZ.get(sport, "UTC")


def compute_local_date(utc_ts: int, tz_name: str) -> date:
    """Convert UTC epoch seconds → local calendar date in `tz_name`.

    ZoneInfo handles DST transitions automatically. If `tz_name` is
    invalid, raises a ZoneInfoNotFoundError; callers should validate
    the tz_name comes from `competition_tz()` (which only emits
    known-valid IANA names).
    """
    tz = ZoneInfo(tz_name)
    return datetime.fromtimestamp(utc_ts, tz=tz).date()
