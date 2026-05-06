"""Deterministic Kalshi event_ticker → Identity parsing.

Phase 1 of /sports v2 (see SPORTS_V2_PLAN.md). Replaces the
title-parsing + fuzzy-team-matching chain that pairs Kalshi
records with FL events. Identity is computed from the structured
ticker grammar documented in KALSHI_AUDIT.md §5.

Public API:
  Identity            — frozen dataclass, hashable, dict-key-safe
  parse_ticker(...)   — Kalshi event_ticker → Identity
  compute_fl_identity(...) — FL event dict → Identity
  match(k_id, fl_id)  — deterministic pairing rule

This module is pure / has no I/O. Safe to import in any context.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import FrozenSet, Optional


# ── Suffix taxonomy ──────────────────────────────────────────────
# Sub-market suffixes appended to a Kalshi series ticker. Used to
# split `KXEPLGAME` → ('KXEPL', 'GAME'). Order matters: longer
# suffixes must come before substrings of themselves so 'TCORNERS'
# doesn't collapse to 'CORNERS', etc. List sourced from
# KALSHI_AUDIT.md §2.
KNOWN_SUFFIXES = (
    # Soccer-specific
    "TCORNERS", "CORNERS", "ADVANCE", "BTTS",
    # Multi-leg / sub-fixture markers
    "TOTALMAPS", "SETWINNER",
    # Generic sub-markets
    "SPREAD", "TOTAL",
    # Fixture-level
    "MATCH", "GAME",
    # Time-period markers
    "HALFTIME", "OVERTIME",
    "1H", "2H",
    "1Q", "2Q", "3Q", "4Q",
    # Baseball-specific
    "F5", "RFI",
    # Esports
    "MAP",
    # MMA per-fight sub-markets
    "FIGHT", "DISTANCE", "ROUNDS", "VICROUND", "MOV", "MOF",
    # Outright marker
    "OUTRIGHT",
)

# 3-letter month codes used in Kalshi's YYMMDD ticker dates.
_MONTH_CODES = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


# ── Identity ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class Identity:
    """Deterministic Kalshi-or-FL fixture/market identity.

    Hashable + frozen so it can be used as a dict key or set member.
    The `kind` field discriminates on which subset of fields is
    populated:

      per_fixture  — date, abbr_block, [time]
      per_leg      — same as per_fixture + leg_n (set/map number)
      series       — year, abbr_block, round_n (NBA/NHL playoff series)
      tournament   — handle, year (Golf/NASCAR per-race; Esports event)
      outright     — year (or date for date-keyed outright)
      unparsed     — raw_suffix only; ticker didn't match any grammar

    For FL identities, `kind=per_fixture` is the only one we compute;
    `fl_orientations` carries both possible concatenations of
    SHORTNAME_HOME/AWAY since Kalshi's abbr_block can use either order.
    """
    kind: str
    sport: str
    series_base: str = ""
    date: Optional[date] = None
    time: Optional[str] = None                  # "HHMM" UTC
    abbr_block: Optional[str] = None            # raw concatenated abbrs from ticker
    fl_orientations: Optional[FrozenSet[str]] = None  # FL-side: {h+a, a+h}
    leg_n: Optional[int] = None
    handle: Optional[str] = None
    year: Optional[int] = None
    round_n: Optional[int] = None
    raw_suffix: str = ""                        # post-series-prefix remainder


# ── Outright-only series prefixes ────────────────────────────────
# Series whose ticker SHAPE is G1 (date + alphabetic block) but
# whose semantics are outrights / player-or-manager futures, not
# h2h fixtures. Listed here so parse_ticker classifies them as
# kind="outright" instead of mis-classifying as per_fixture.
# Sourced from KALSHI_AUDIT.md §5 sport-specific outright variations.
_OUTRIGHT_SERIES_PREFIXES = (
    # Player movement futures (suffix block = player surname handle)
    "KXJOIN",            # KXJOINCLUB-26OCT02RODRYGO, KXJOINLEAGUE-26OCT02MSALAH
    "KXNEXTTEAM",        # KXNEXTTEAMNFL-27JALLEN, KXNEXTTEAMNBA-26LJAM
    "KXNEXTMANAGER",     # KXNEXTMANAGEREPL-26CFC
    # Manager / coach futures
    "KXMANAGERSOUT",     # KXMANAGERSOUT-26AUG01EPL
    "KXCOACHOUT",        # KXCOACHOUTNBADATE-27SKER
    "KXCOACHONDATE",     # KXCOACHONDATE-NE26
    # Trade / transfer / retirement
    "KXMLBTRADE", "KXNFLTRADE", "KXSOCCERTRANSFER",
    "KXF1RETIRE", "KXARODGRETIRE", "KXKELCERETIRE",
    "KXLBJRETIRE", "KXPERSONUNRETIRE",
    # Per-player binary "will X happen"
    "KXSOCCERPLAY", "KXPLAYWC", "KXPGACURRY", "KXPGATIGER",
    "KXSCOTTIESLAM", "KXLAMINEYAMAL", "KXBRYSONCOURSERECORDS",
    "KXFURYJOSHUA", "KXFLOYDTYSONFIGHT", "KXMCGREGORFIGHTNEXT",
    "KXCALCFO", "KXSORONDO",
    # Award / honor (per-player handle)
    "KXGRANDSLAMJ", "KXGRANDSLAM",
    "KXNYKCOACH",
    # Cards / events / appearances
    "KXCARDPRESENCE", "KXSPORTSOWNERLBJ", "KXNBAATTEND",
    "KXSHAI20PTREC", "KXNBA2KCOVER", "KXNDJOINCONF",
    "KXDONATEMRBEAST", "KXCOVEREA", "KXPGAMAJORWIN",
    # Stadium / location / team-existence futures
    "KX1STHOMEGAME", "KXRELOCATION", "KXNBASEATTLE",
    "KXSONICS", "KXCITYNBA", "KXNBATEAM",
    # World Cup / Olympics specials
    "KXFIFAUSPULL", "KXWCIRAN", "KXWCLOCATION",
    "KXWCMESSIRONALDO", "KXWCROUND", "KXWCSQUAD", "KXWCGROUP",
    # Per-player futures with custom prefixes
    "KXSTARTINGQB", "KXNBARETURN",
    "KXNBADRAFT", "KXNFLDRAFT",
    "KXNBAPLAYOFF",       # playoff-related outright counts
    # Misc / cross-sport novelty
    "KXEUROVISIONISRAELBAN", "KXPIZZASCORE",
    "KXCOLLEGEGAMEDAYGUEST", "KXWSOPENTRANTS",
    "KXQUADRUPLEDOUBLE", "KXARSENALCUPS",
    "KXPAVIAPRESEASON", "KXMLSJOIN",
    "KXRANKLISTFF", "KXOWGRRANK", "KXCHESSFIDERATING",
    "KXLIVOCCUR",         # "Will LIV Golf tournament happen in X"
    # Season-level "will X happen" futures (no per-fixture pairing)
    "KXWNBADELAY",        # "Will at least 1 game be played in WNBA season?"
    "KXWNBAGAMESPLAYED",  # WNBA games-played count futures
    "KXWNBADRAFT",        # KXWNBADRAFT1, KXWNBADRAFTTOP3 — draft outrights
    "KXMARMAD",           # March Madness bracket outrights
    "KXNCAAMBNEXTCOACH",  # NCAA basketball coaching changes
    "KXSTEPHDEAL",        # Steph Curry contract futures
)


def _is_outright_series(series_base: str) -> bool:
    """True if series_base matches a known outright-only prefix.

    Short-circuits per_fixture classification for tickers whose
    shape is G1/G7 but whose semantics are outrights.
    """
    s = (series_base or "").upper()
    if not s:
        return False
    return any(s.startswith(p) for p in _OUTRIGHT_SERIES_PREFIXES)


# ── Series-base extraction ───────────────────────────────────────

def strip_known_suffix(series_ticker: str) -> tuple[str, str]:
    """Return (series_base, suffix). Empty suffix if none matches.

    Examples:
      KXEPLGAME      → (KXEPL, GAME)
      KXNBA1H        → (KXNBA, 1H)
      KXMLBF5        → (KXMLB, F5)
      KXIPL          → (KXIPL, "")
      KXMLBWINS-WSH  → (KXMLBWINS-WSH, "")  # team-suffixed bases keep the dash
    """
    s = (series_ticker or "").upper()
    for suf in KNOWN_SUFFIXES:
        if s.endswith(suf) and len(s) > len(suf):
            return (s[:-len(suf)], suf)
    return (s, "")


# ── Ticker grammar patterns ──────────────────────────────────────
# Order: most specific first. Caller tries each in turn.

# Date components
_DATE = r"(\d{2})([A-Z]{3})(\d{2})"
# HHMM time
_TIME = r"(\d{4})"
# Variable-length team / player abbr block (allows digits — e.g.
# Mainz 05 → M05UNI, Dota 2 → 1WINMOUZ).
_ABBRS = r"([A-Z0-9]+?)"
# Trailing leg / map / set number
_LEG = r"-(\d+)"

PATTERN_LEG_DATE_TIME = re.compile(rf"^{_DATE}{_TIME}{_ABBRS}{_LEG}$")
PATTERN_LEG_DATE      = re.compile(rf"^{_DATE}{_ABBRS}{_LEG}$")
PATTERN_DATE_TIME     = re.compile(rf"^{_DATE}{_TIME}{_ABBRS}$")
PATTERN_DATE_TEAMS    = re.compile(rf"^{_DATE}([A-Z0-9]+)$")  # greedy abbr
PATTERN_SERIES        = re.compile(r"^(\d{2})([A-Z][A-Z0-9]*?)R(\d+)$")
PATTERN_DATE_ONLY     = re.compile(rf"^{_DATE}$")
PATTERN_YEAR_4        = re.compile(r"^(\d{4})$")
PATTERN_YEAR_2        = re.compile(r"^(\d{1,3})$")
# Year-prefixed handle (KXLIVOCCUR-26LIGLA, KXRYDERCUPCAPTAIN-2027USA,
# KXCHESSNORWAY-26WOMEN, KXF1RETIRE-30VERSTAPPEN). 2-4 digit year +
# alpha-led handle. Documented in KALSHI_AUDIT.md §5 as a sport-
# specific outright variation.
PATTERN_YEAR_HANDLE   = re.compile(r"^(\d{2,4})([A-Z][A-Z0-9]*)$")
# Handle: leading letter, alphanumeric body. Year often suffixed.
PATTERN_HANDLE        = re.compile(r"^([A-Z][A-Z0-9]*)$")
# Handle with trailing year (e.g. PGC26 → handle=PGC, year=26)
PATTERN_HANDLE_YEAR   = re.compile(r"^([A-Z][A-Z0-9]*?)(\d{2,4})$")


def _parse_date(yy: str, mmm: str, dd: str) -> Optional[date]:
    """`26MAY07` → `date(2026, 5, 7)`. 2-digit year → 2000-2099."""
    m = _MONTH_CODES.get(mmm.upper())
    if m is None:
        return None
    try:
        return date(2000 + int(yy), m, int(dd))
    except ValueError:
        return None


def _strip_series_prefix(event_ticker: str, series_ticker: str) -> str:
    """Return the part of event_ticker after the series prefix + dash.

    Accepts either form for series_ticker:
      - full series ticker incl. suffix (e.g. "KXEPLGAME") — production form
      - suffix-stripped base (e.g. "KXEPL") — sometimes from probes

    Examples:
      ev=KXEPLGAME-26MAY11OKCLAL, ser=KXEPLGAME → 26MAY11OKCLAL
      ev=KXEPLGAME-26MAY11OKCLAL, ser=KXEPL     → 26MAY11OKCLAL
      ev=KXIPL-26,                ser=KXIPL    → 26
      ev=KXMLBWINS-WSH-26,        ser=KXMLBWINS-WSH → 26
    """
    full = (event_ticker or "").upper()
    series = (series_ticker or "").upper()

    if not series or not full.startswith(series):
        # Mismatch — fallback: take what's after first dash
        if "-" in full:
            return full.split("-", 1)[1]
        return full

    rest = full[len(series):]

    # Case 1: dash immediately follows (full series_ticker passed in)
    if rest.startswith("-"):
        return rest[1:]

    # Case 2: a known suffix is glued on, then a dash
    # (caller passed the suffix-stripped base, not the full series ticker)
    for suf in KNOWN_SUFFIXES:
        if rest.startswith(suf):
            after = rest[len(suf):]
            if after.startswith("-"):
                return after[1:]
            # Suffix matched but no dash — return what's left
            return after

    # Case 3: no recognizable boundary
    return rest.lstrip("-")


# ── Public: parse_ticker ─────────────────────────────────────────

def parse_ticker(event_ticker: str, series_ticker: str, sport: str) -> Identity:
    """Parse a Kalshi event_ticker into deterministic Identity.

    Implements every grammar pattern documented in KALSHI_AUDIT.md §5.
    Returns Identity with `kind` set to one of:
      per_fixture / per_leg / series / tournament / outright / unparsed

    Returns Identity(kind='unparsed') for tickers that don't match any
    pattern; never raises. The unparseable raw_suffix is preserved
    on the Identity so callers can log it.
    """
    if not event_ticker:
        return Identity(kind="unparsed", sport=sport, series_base="")

    series_base, _ = strip_known_suffix(series_ticker or "")
    suffix = _strip_series_prefix(event_ticker, series_ticker or "")

    # ── Outright series short-circuit ────────────────────────────
    # Some series have ticker shapes that LOOK like G1/G7 (date +
    # alphabetic block) but are outrights — player handles, manager
    # codes, novelty futures. Skip per_fixture classification entirely
    # for these and route directly to outright with date and handle
    # preserved. Without this, KXJOINCLUB-26OCT02RODRYGO would
    # mis-classify as a per_fixture with abbr_block="RODRYGO" and
    # show up as an unpaired Soccer fixture in /sports.
    if _is_outright_series(series_base):
        # Try to extract date + handle from the suffix; fall back to
        # plain handle / year codes.
        if (m := PATTERN_DATE_TEAMS.match(suffix)):
            yy, mmm, dd, handle = m.groups()
            d = _parse_date(yy, mmm, dd)
            if d is not None:
                return Identity(
                    kind="outright", sport=sport, series_base=series_base,
                    date=d, handle=handle, raw_suffix=suffix,
                )
        if (m := PATTERN_DATE_TIME.match(suffix)):
            yy, mmm, dd, hhmm, handle = m.groups()
            d = _parse_date(yy, mmm, dd)
            if d is not None:
                return Identity(
                    kind="outright", sport=sport, series_base=series_base,
                    date=d, time=hhmm, handle=handle, raw_suffix=suffix,
                )
        if (m := PATTERN_DATE_ONLY.match(suffix)):
            yy, mmm, dd = m.groups()
            d = _parse_date(yy, mmm, dd)
            if d is not None:
                return Identity(
                    kind="outright", sport=sport, series_base=series_base,
                    date=d, raw_suffix=suffix,
                )
        if (m := PATTERN_YEAR_4.match(suffix)):
            return Identity(
                kind="outright", sport=sport, series_base=series_base,
                year=int(m.group(1)), raw_suffix=suffix,
            )
        if (m := PATTERN_YEAR_2.match(suffix)):
            return Identity(
                kind="outright", sport=sport, series_base=series_base,
                year=int(m.group(1)), raw_suffix=suffix,
            )
        if (m := PATTERN_YEAR_HANDLE.match(suffix)):
            return Identity(
                kind="outright", sport=sport, series_base=series_base,
                year=int(m.group(1)), handle=m.group(2),
                raw_suffix=suffix,
            )
        if (m := PATTERN_HANDLE_YEAR.match(suffix)):
            return Identity(
                kind="outright", sport=sport, series_base=series_base,
                handle=m.group(1), year=int(m.group(2)),
                raw_suffix=suffix,
            )
        if (m := PATTERN_HANDLE.match(suffix)):
            return Identity(
                kind="outright", sport=sport, series_base=series_base,
                handle=m.group(1), raw_suffix=suffix,
            )
        # Last-resort: outright with raw_suffix preserved
        return Identity(
            kind="outright", sport=sport, series_base=series_base,
            raw_suffix=suffix,
        )

    # Most-specific patterns first.

    # G_LEG_DATE_TIME — esports MAP markets:
    #   KXLOLMAP-26MAY071500ZYBSLY-1
    if (m := PATTERN_LEG_DATE_TIME.match(suffix)):
        yy, mmm, dd, hhmm, abbrs, n = m.groups()
        d = _parse_date(yy, mmm, dd)
        if d is not None:
            return Identity(
                kind="per_leg", sport=sport, series_base=series_base,
                date=d, time=hhmm, abbr_block=abbrs,
                leg_n=int(n), raw_suffix=suffix,
            )

    # G_LEG_DATE — tennis SETWINNER markets:
    #   KXATPSETWINNER-26MAY05HIJBAS-1
    if (m := PATTERN_LEG_DATE.match(suffix)):
        yy, mmm, dd, abbrs, n = m.groups()
        d = _parse_date(yy, mmm, dd)
        if d is not None:
            return Identity(
                kind="per_leg", sport=sport, series_base=series_base,
                date=d, abbr_block=abbrs,
                leg_n=int(n), raw_suffix=suffix,
            )

    # G7 — date + time + teams (MLB, esports, AFL, intl basketball/hockey):
    #   KXMLBGAME-26MAY071540PITAZ
    if (m := PATTERN_DATE_TIME.match(suffix)):
        yy, mmm, dd, hhmm, abbrs = m.groups()
        d = _parse_date(yy, mmm, dd)
        if d is not None:
            return Identity(
                kind="per_fixture", sport=sport, series_base=series_base,
                date=d, time=hhmm, abbr_block=abbrs,
                raw_suffix=suffix,
            )

    # G1 — date + teams (most sports):
    #   KXEPLGAME-26MAY19CFCTOT
    if (m := PATTERN_DATE_TEAMS.match(suffix)):
        yy, mmm, dd, abbrs = m.groups()
        d = _parse_date(yy, mmm, dd)
        if d is not None:
            return Identity(
                kind="per_fixture", sport=sport, series_base=series_base,
                date=d, abbr_block=abbrs,
                raw_suffix=suffix,
            )

    # G_SERIES — NBA/NHL playoff series:
    #   KXNBASERIES-26LALOKCR2
    if (m := PATTERN_SERIES.match(suffix)):
        yy, abbrs, n = m.groups()
        return Identity(
            kind="series", sport=sport, series_base=series_base,
            year=int(yy), abbr_block=abbrs,
            round_n=int(n), raw_suffix=suffix,
        )

    # G_DATE_ONLY — date-keyed outright (Tennis #1 ranking):
    #   KXATP1RANK-26DEC31
    if (m := PATTERN_DATE_ONLY.match(suffix)):
        yy, mmm, dd = m.groups()
        d = _parse_date(yy, mmm, dd)
        if d is not None:
            return Identity(
                kind="outright", sport=sport, series_base=series_base,
                date=d, raw_suffix=suffix,
            )

    # G3 — 4-digit year (rare; KXPLAYTOGETHERJBJT-2027)
    if (m := PATTERN_YEAR_4.match(suffix)):
        return Identity(
            kind="outright", sport=sport, series_base=series_base,
            year=int(m.group(1)), raw_suffix=suffix,
        )

    # G4 — 1-3 digit year code (KXUCL-26)
    if (m := PATTERN_YEAR_2.match(suffix)):
        return Identity(
            kind="outright", sport=sport, series_base=series_base,
            year=int(m.group(1)), raw_suffix=suffix,
        )

    # G_YEAR_HANDLE — year-prefixed handle outrights (sport-specific
    # variants from §5): KXLIVOCCUR-26LIGLA, KXRYDERCUPCAPTAIN-2027USA,
    # KXCHESSNORWAY-26WOMEN, KXF1RETIRE-30VERSTAPPEN, etc.
    if (m := PATTERN_YEAR_HANDLE.match(suffix)):
        return Identity(
            kind="outright", sport=sport, series_base=series_base,
            year=int(m.group(1)), handle=m.group(2),
            raw_suffix=suffix,
        )

    # G_TOURNAMENT_HANDLE — Golf, NASCAR, Esports event handles:
    #   KXPGATOUR-PGC26 → handle=PGC, year=26
    #   KXCS2-ASIA26    → handle=ASIA, year=26
    if (m := PATTERN_HANDLE_YEAR.match(suffix)):
        return Identity(
            kind="tournament", sport=sport, series_base=series_base,
            handle=m.group(1), year=int(m.group(2)),
            raw_suffix=suffix,
        )

    # G5 — pure alphabetic handle (no year):
    #   KXR6-SLC26 (already matched above), KXOVERWATCH-CCT26 (above)
    #   Pure-alpha falls through here.
    if (m := PATTERN_HANDLE.match(suffix)):
        return Identity(
            kind="tournament", sport=sport, series_base=series_base,
            handle=m.group(1), raw_suffix=suffix,
        )

    # Unparsed — preserve raw_suffix for diagnostics.
    return Identity(
        kind="unparsed", sport=sport, series_base=series_base,
        raw_suffix=suffix,
    )


# ── Public: compute_fl_identity ──────────────────────────────────

# ── FL → Kalshi abbreviation alias map ───────────────────────────
# FL and Kalshi don't always agree on team shortnames. NBA is the
# worst offender (FL's "OKL"/"LAK" vs Kalshi's "OKC"/"LAL"). Per
# Phase 5 punch list 2026-05-05.
#
# Normalization is APPLIED at FL → Identity time. Both the original
# FL abbr AND the normalized form get added to fl_orientations so
# the identity match accepts either side. Kalshi tickers stay the
# canonical reference; aliases only translate FL's vocabulary in.
#
# Add new entries as gaps surface via /api/_debug/sports_join_diff.
_FL_ABBR_ALIASES: dict[str, dict] = {
    "Basketball": {
        # NBA — Kalshi uses canonical 3-letter, FL sometimes diverges
        "LAK": "LAL",      # Lakers
        "LA":  "LAL",
        "OKL": "OKC",      # Thunder
        "GSW": "GS",       # Warriors (Kalshi sometimes uses GS)
        "GS":  "GSW",      # reverse — also try GSW form
        "NOP": "NO",       # Pelicans
        "NO":  "NOP",
        "NYK": "NY",       # Knicks
        "NY":  "NYK",
        "PHO": "PHX",      # Suns
        "PHX": "PHO",
        "SAS": "SA",       # Spurs
        "SA":  "SAS",
        "WAS": "WSH",      # Wizards
        "WSH": "WAS",
        "UTA": "UTH",      # Jazz
        "UTH": "UTA",
        "BKN": "BRK",      # Nets
        "BRK": "BKN",
    },
    "Soccer": {
        # Conmebol clubs (Copa Libertadores / Sudamericana / domestic
        # leagues). Bidirectional pairs cover the common Kalshi vs FL
        # divergence — Kalshi tends to use the most-common-fan-form
        # 3-letter, FL sometimes uses an alternate. Add new entries
        # as `/api/_debug/registry_diff` surfaces unpaired Conmebol
        # pairs with shortname disagreement.
        #
        # Argentina
        "BOC": "BJN", "BJN": "BOC",       # Boca Juniors
        "BOJ": "BOC", "BOCJU": "BOC",
        "RIV": "RVP", "RVP": "RIV",       # River Plate
        "RIP": "RIV",
        "RAC": "RCG", "RCG": "RAC",       # Racing Club
        "IND": "IDA", "IDA": "IND",       # Independiente (Arg) —
                                           # collides with Independiente
                                           # del Valle (Ecu); resolved
                                           # by date+league context.
        "SLO": "SLZ", "SLZ": "SLO",       # San Lorenzo
        "EST": "EDP", "EDP": "EST",       # Estudiantes
        "VEL": "VLZ", "VLZ": "VEL",       # Velez Sarsfield
        "LAN": "LNS", "LNS": "LAN",       # Lanus
        # Brazil
        "FLA": "FLM", "FLM": "FLA",       # Flamengo
        "PAL": "PLM", "PLM": "PAL",       # Palmeiras
        "COR": "CTH", "CTH": "COR",       # Corinthians
        "SAN": "SNS", "SNS": "SAN",       # Santos
        "INT": "INC", "INC": "INT",       # Internacional
        "GRE": "GRM", "GRM": "GRE",       # Gremio
        "FLU": "FLN", "FLN": "FLU",       # Fluminense (avoid FLM
                                           # collision with Flamengo)
        "BOT": "BFR", "BFR": "BOT",       # Botafogo
        "SAO": "SPL", "SPL": "SAO",       # Sao Paulo
        "ATM": "AMI", "AMI": "ATM",       # Atletico Mineiro
        # Ecuador / Colombia / Venezuela / Peru / Bolivia / Paraguay
        "BSC": "BAR", "BAR": "BSC",       # Barcelona SC (Ecu) —
                                           # collides with FC Barcelona;
                                           # date+league disambiguates.
        "IDV": "IND",                      # Independiente del Valle
                                           # (one-way — IND already
                                           # mapped above).
        "LDU": "LIQ", "LIQ": "LDU",       # LDU Quito
        "EME": "CSE", "CSE": "EME",       # Emelec
        "NAC": "NCU", "NCU": "NAC",       # Nacional (Uruguay)
        "PEN": "PNL", "PNL": "PEN",       # Penarol
        "OLI": "OLA", "OLA": "OLI",       # Olimpia (Paraguay)
        "CER": "CPO", "CPO": "CER",       # Cerro Porteno
        # Always Ready (Bolivia) — FL ships 'ALW' (from "Always"),
        # Kalshi ships 'ARE' (from "Always REady") in the
        # CONMEBOLLIB tickers (verified via /api/_debug/unpaired_pairs
        # — KXCONMEBOLLIBGAME-26MAY05ARELAN). ALR/ARB are older
        # forms kept for back-compat in case Kalshi rolls back to
        # the Spanish-style abbreviation. Multi-value list lets one
        # FL abbr expand to all known Kalshi counterparts at once.
        "ALW": ["ALR", "ARE", "ARB"],
        "ARE": ["ALW", "ALR", "ARB"],
        "ALR": ["ALW", "ARE", "ARB"],
        "ARB": ["ALW", "ALR", "ARE"],
        "BOL": "BLV", "BLV": "BOL",       # Bolivar
        "TST": "STR", "STR": "TST",       # The Strongest
        "ANA": "ANL", "ANL": "ANA",       # Atletico Nacional (Col)
        "MIL": "MFC", "MFC": "MIL",       # Millonarios
        "JUN": "JBQ", "JBQ": "JUN",       # Junior Barranquilla
        # Venezuela — top-flight Liga FUTVE clubs
        "PUE": "PCB", "PCB": "PUE",       # Puerto Cabello
        "PCA": "PUE",                      # Puerto Cabello alternate
        "CAR": "CFC", "CFC": "CAR",       # Caracas FC
        "DTA": "DEP", "DEP": "DTA",       # Deportivo Tachira
        "MET": "MTR", "MTR": "MET",       # Metropolitanos
        "MIN": "MNR", "MNR": "MIN",       # Mineros de Guayana
        # Peru — Liga 1 + Cienciano-style smaller clubs
        "CIE": "CCN", "CCN": "CIE",       # Cienciano
        "CIN": "CIE",                      # Cienciano alternate
        "ALI": "ALN", "ALN": "ALI",       # Alianza Lima
        "UNI": "UNV", "UNV": "UNI",       # Universitario
        "SCC": "SPC", "SPC": "SCC",       # Sporting Cristal
        "MEL": "FBC", "FBC": "MEL",       # Melgar
        # Lanus alternates (already had LAN↔LNS above)
        "LNU": "LAN", "LANS": "LAN",
        # La Liga short-form pairs (Atletico Madrid is the canonical
        # 'Atletico' / 'Atletico Madrid' / 'Atl. Madrid' spread; FL
        # ships ATM, Kalshi sometimes ships ATL).
        "ATL": "ATM",                      # one-way to avoid double-
                                           # mapping with Atletico
                                           # Mineiro.
        "PSG": "PAR", "PAR": "PSG",       # Paris Saint-Germain
                                           # (Kalshi ships PSG, FL
                                           # sometimes uses PAR).
        # Bayern Munich — FL ships 'BAY' (from "Bayern"), Kalshi
        # ships 'BMU' (from "BayernMUnich") for UCL-side tickers
        # like KXUCLGOAL / KXUCLCORNERS / KXUCLBTTS / KXUCLADVANCE
        # that share the same abbr_block as KXUCLGAME but escape
        # the title_match tier when their title shape differs from
        # "Bayern Munich vs PSG". Confirmed via 2026-05-06 prod
        # audit (DUPLICATE: kalshi-h2h-KXUCLGOAL-26MAY06BMUPSG +
        # FL WzCGgkEU).
        "BAY": "BMU", "BMU": "BAY",
    },
    # Other sports get populated as gaps surface.
}


def normalize_fl_abbr(sport: str, abbr: str) -> set[str]:
    """Return all known forms of `abbr` for `sport`, including the original.

    Used by compute_fl_identity to expand fl_orientations beyond the
    raw FL shortname so Kalshi's abbr_block matches under either
    convention. Returns at minimum `{abbr}` if no aliases are known.

    Alias values may be a single string OR a list/tuple/set — the
    list form is used when one FL abbr has multiple Kalshi-side
    counterparts (e.g. Always Ready: FL `ALW` → Kalshi can ship
    `ALR` (Bolivia/Spanish full) or `ARE` (English contraction)
    depending on series). Public — Phase C's kalshi_registry_seed
    imports this to expand team aliases at seed time.
    """
    forms = {abbr}
    aliases = _FL_ABBR_ALIASES.get(sport, {})
    if abbr in aliases:
        v = aliases[abbr]
        if isinstance(v, (list, tuple, set)):
            forms.update(v)
        else:
            forms.add(v)
    return forms


def compute_fl_identity(fl_event: dict, sport: str) -> Optional[Identity]:
    """Build an Identity for an FL events-list event.

    Returns None if SHORTNAME_HOME / SHORTNAME_AWAY / START_TIME are
    missing — callers should treat that as "no Identity, no pairing".

    The identity stores BOTH abbr orientations in `fl_orientations`
    because Kalshi's abbr_block can be home+away OR away+home depending
    on the title shape (vs vs at). Each side is also expanded through
    `normalize_fl_abbr` so per-sport alias maps catch FL/Kalshi
    shortname divergence (NBA: FL "OKL" → Kalshi "OKC", etc.).
    `match()` checks membership.
    """
    home = (fl_event.get("SHORTNAME_HOME") or "").upper().strip()
    away = (fl_event.get("SHORTNAME_AWAY") or "").upper().strip()
    start_ts = fl_event.get("START_TIME") or fl_event.get("START_UTIME")

    if not (home and away and start_ts):
        return None

    try:
        dt = datetime.fromtimestamp(int(start_ts), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None

    # Cross-product of all known forms × both orientations.
    home_forms = normalize_fl_abbr(sport, home)
    away_forms = normalize_fl_abbr(sport, away)
    orientations = frozenset(
        h + a
        for h in home_forms
        for a in away_forms
    ) | frozenset(
        a + h
        for h in home_forms
        for a in away_forms
    )

    return Identity(
        kind="per_fixture",
        sport=sport,
        date=dt.date(),
        time=dt.strftime("%H%M"),
        fl_orientations=orientations,
    )


# ── Public: match ────────────────────────────────────────────────

def match(k: Identity, fl: Identity,
          fuzz_days: int = 1, fuzz_min: int = 30) -> bool:
    """Pairing rule: deterministic equality on parsed identities.

    Per KALSHI_AUDIT.md §7. Returns True iff:
      - both are per_fixture
      - same sport
      - dates agree within ±fuzz_days (timezone-drift tolerance)
      - kalshi.abbr_block matches one of fl.fl_orientations
      - if both have time (G7 sport, e.g. MLB doubleheader):
          times agree within ±fuzz_min minutes

    Note: per-leg (set/map) identities can be matched to their parent
    per_fixture by stripping leg_n; that's not done here — it's a
    separate Identity-level operation.
    """
    if k.kind != "per_fixture" or fl.kind != "per_fixture":
        return False
    if k.sport != fl.sport:
        return False
    if k.date is None or fl.date is None:
        return False
    if abs((k.date - fl.date).days) > fuzz_days:
        return False
    # Abbr orientation match
    if not k.abbr_block or not fl.fl_orientations:
        return False
    if k.abbr_block not in fl.fl_orientations:
        return False
    # Time check (only when both sides carry time)
    if k.time and fl.time:
        try:
            kt = int(k.time[:2]) * 60 + int(k.time[2:])
            ft = int(fl.time[:2]) * 60 + int(fl.time[2:])
            if abs(kt - ft) > fuzz_min:
                return False
        except (ValueError, IndexError):
            pass  # bad time format — don't fail the match
    return True


# ── Helper: per_leg → parent per_fixture ─────────────────────────

def parent_fixture_identity(leg: Identity) -> Optional[Identity]:
    """Given a per_leg Identity (tennis set, esports map), return the
    Identity that the parent per_fixture would have. Useful for
    grouping sub-markets to their parent fixture.

    For non-leg identities, returns None.
    """
    if leg.kind != "per_leg":
        return None
    return Identity(
        kind="per_fixture", sport=leg.sport, series_base=leg.series_base,
        date=leg.date, time=leg.time, abbr_block=leg.abbr_block,
        raw_suffix=leg.raw_suffix.rsplit("-", 1)[0]
            if leg.raw_suffix else "",
    )
