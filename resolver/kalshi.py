"""Kalshi resolver module — extract_signal from sp.kalshi_markets.raw_payload.

Per architecture v1.4 §7.2. Reads the Kalshi event-level cache record
shape produced by ingestion.kalshi and returns a standardized
FixtureSignal for the central matcher.

Phase 2A: extract_signal only. No database reads or writes.

Kalshi raw_payload shape (validated by ingestion.schema_validation.KalshiMarketValidator):

    {
      "event_ticker":             "KXUCLGAME-26MAY07BAYPSG",
      "series_ticker":            "KXUCLGAME",
      "title":                    "Bayern Munich vs PSG",
      "category":                 "Sports",
      "_sport":                   "Soccer",
      "_soccer_comp":             "Champions League",
      "_kickoff_dt":              "2026-05-07T19:00:00+00:00",
      "expected_expiration_time": "2026-05-07T22:00:00+00:00",
      "outcomes":                 [...],   # sub-markets with prices
      ...
    }

The kalshi_identity.parse_ticker() helper produces the Identity dict
with abbr_block / parsed_home_abbr / parsed_away_abbr — already stored
on the row by ingestion.kalshi. This resolver re-parses to keep the
extraction self-contained (matches the architecture's "pure function
over raw payload" rule).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ._normalize import normalize_name
from .types import FixtureSignal, TeamCandidate


RESOLVER_VERSION = "kalshi@2a.0"


# Phase 2C.2.6 — prop-bet title suffixes.
#
# Kalshi sub-market tickers often have G1 ticker shape (date + abbr
# block) AND parse_ticker classifies them per_fixture (the sub-market
# suffix in KNOWN_SUFFIXES handles the ticker side), but the TITLE
# carries the prop-bet identifier as the trailing segment after ": ".
# Examples observed in the soccer dry-run (Phase 2C.2.5):
#
#   "Brighton: Spreads"
#   "Wolfsburg: Spreads"
#   "Seattle: Totals"
#   "Portland: Totals"
#   "Elche: Both Teams to Score"
#
# These are prop bets, not game markets — the alias tier should NOT
# match them to fixtures; they polluted the soccer dry-run as
# anchor_failed (clean filter target) or as 0.80-confidence
# review_queue rows (partially-matched single-team props masquerading
# as game markets).
#
# Two-layer defense, mirroring 2C.1:
#   - extract_signal returns None for these (counted as
#     signal_extraction_skipped — matcher never sees them).
#   - The alias tier's anchor-failed counter remains a backstop
#     audit signal: if a future prop-suffix slips through, it'll
#     show up there.
#
# Extension pattern: when the post-2C.4 alias_no_team_resemblance
# fail_reason audit shows new suffixes climbing, add them here.
# Each addition needs a regression test in tests/test_resolver_2a.py.
_KALSHI_PROP_TITLE_SUFFIXES: tuple[str, ...] = (
    # User-named (PR feedback after 2C.2.5 dry-run):
    "Total",
    "Totals",
    "Spread",
    "Spreads",
    "Game Spread",
    "First Goalscorer",
    "Both Teams to Score",
    "Exact Match Score",
    # Tennis-specific (Phase 2D.2.6 — added after 2D.2.5 dry-run
    # showed records like "Alexander Bublik: Total Games" reaching
    # anchor_failed instead of extraction_skipped). "Game Spread"
    # was already in the list from 2C.2.6 (soccer); reused here.
    "Total Games",
    "Set Winner",
    "Match Winner",
    "Tiebreak",
)
_KALSHI_PROP_TITLE_SUFFIXES_LOWER: frozenset[str] = frozenset(
    s.lower() for s in _KALSHI_PROP_TITLE_SUFFIXES
)


def _is_prop_market_title(title: str) -> bool:
    """True if the title's trailing ': <suffix>' segment matches a
    known prop-bet identifier. Case-insensitive.

    Match shape: rsplit on the LAST ": " — the prop suffix lives at
    the title's tail. Handles:
      "Brighton: Spreads"             → match
      "Bayern Munich vs PSG"          → no colon → no match
      "Group A: Round 1: Team vs Team"→ last segment is the game,
                                        not a prop type → no match
      "Brighton: Total"               → match (singular variant)
      "Bayern Munich vs PSG: Spreads" → match (game-level prop)
    """
    if not title or ": " not in title:
        return False
    suffix = title.rsplit(": ", 1)[-1].strip().lower()
    return suffix in _KALSHI_PROP_TITLE_SUFFIXES_LOWER


class KalshiResolverModule:
    """ResolverModule for Kalshi provider records."""

    @property
    def provider(self) -> str:
        return "kalshi"

    def extract_signal(
        self,
        raw_record: dict[str, Any],
        *,
        sport_override: str | None = None,
    ) -> FixtureSignal | None:
        """Pull a FixtureSignal from one Kalshi event cache record.

        `sport_override`: if the caller already knows the canonical
        sport code, pass it. Otherwise we read `_sport` off the
        record (set by main.py's get_data() classification). Empty
        string when neither is available — the matcher degrades.

        Returns None for records that aren't per_fixture (outrights,
        series, tournaments). The strict-tier matcher only operates
        on per_fixture identities.
        """
        from kalshi_identity import parse_ticker

        event_ticker = (raw_record.get("event_ticker") or "").strip()
        if not event_ticker:
            return None

        series_ticker = (raw_record.get("series_ticker") or "").strip()
        sport = sport_override if sport_override is not None else (
            raw_record.get("_sport") or ""
        )

        ident = parse_ticker(event_ticker, series_ticker, sport)
        if ident.kind != "per_fixture":
            return None

        # Phase 2C.2.6: prop-bet sub-market detection by title suffix.
        # parse_ticker can classify "Brighton: Spreads"-shaped tickers
        # as per_fixture because the abbr_block + date pattern looks
        # like a game; the title is what reveals it's a prop. Filter
        # here so the alias tier never sees these.
        title = raw_record.get("title") or ""
        if _is_prop_market_title(title):
            return None

        home_candidates, away_candidates = self._team_candidates(
            title=title,
            abbr_block=ident.abbr_block or "",
        )

        kickoff_at, kickoff_confidence = self._kickoff(raw_record, ident)

        # competition_hint: use series_ticker (the stable Kalshi-side
        # competition identifier). bootstrap_sp_competitions seeds
        # sp.competitions.kalshi_series_bases keyed off series_base
        # (= strip_known_suffix(series_ticker)), and CompetitionResolver
        # applies the same strip on lookup. _soccer_comp ("Champions
        # League" etc.) is human display text and is preserved in
        # raw_signals for diagnostics, not for matching.
        competition_hint = series_ticker or None

        return FixtureSignal(
            provider=self.provider,
            provider_record_id=event_ticker,
            sport=sport,
            home_team_candidates=home_candidates,
            away_team_candidates=away_candidates,
            kickoff_at=kickoff_at,
            kickoff_confidence=kickoff_confidence,
            competition_hint=competition_hint,
            raw_signals={
                "series_ticker": series_ticker,
                "abbr_block":    ident.abbr_block,
                "kind":          ident.kind,
                "raw_suffix":    ident.raw_suffix,
                "title":         raw_record.get("title"),
                "category":      raw_record.get("category"),
                "soccer_comp":   raw_record.get("_soccer_comp"),
            },
        )

    @staticmethod
    def _team_candidates(
        *,
        title: str,
        abbr_block: str,
    ) -> tuple[list[TeamCandidate], list[TeamCandidate]]:
        """Extract home + away candidates from a Kalshi record.

        Two signals available:
          1. Title shape — "Home vs Away" / "Away at Home" / "Home @ Away".
             Title parsing is best-effort; legacy `_HEAD_TO_HEAD_TITLE_RE`
             handles the common cases. The away-first 'X at Y' notation
             is canonicalized so home is always returned first in the tuple.
          2. abbr_block — concatenated abbrs, no separator. Direction-
             ambiguous (could be home+away OR away+home depending on
             series convention). The matcher resolves orientation by
             trying both against FL's SHORTNAME_HOME/AWAY.

        Returns (home_candidates, away_candidates). The abbr_block is
        added to BOTH sides as a low-weight 'kalshi_abbr' candidate so
        the matcher can score it against either side independently.
        """
        # Title parsing — minimal regex inline rather than importing
        # from main.py (extraction must be importable without main).
        import re
        title_re = re.compile(
            r"\s+(?:vs\.?|v\.?|@|at)\s+",
            flags=re.IGNORECASE,
        )

        home_candidates: list[TeamCandidate] = []
        away_candidates: list[TeamCandidate] = []

        if title:
            m = title_re.search(title)
            if m:
                left = title[:m.start()].strip()
                right = title[m.end():].strip()
                separator = m.group(0).strip().lower()
                if separator in ("at", "@"):
                    # 'X at Y' = X away, Y home
                    home_name, away_name = right, left
                else:
                    # 'X vs Y' = X home, Y away (Kalshi convention)
                    home_name, away_name = left, right

                if home_name:
                    home_candidates.append(TeamCandidate(
                        raw=home_name,
                        normalized=normalize_name(home_name),
                        kind="name",
                        weight=0.85,
                    ))
                if away_name:
                    away_candidates.append(TeamCandidate(
                        raw=away_name,
                        normalized=normalize_name(away_name),
                        kind="name",
                        weight=0.85,
                    ))

        if abbr_block:
            # Direction-blind abbr signal — matcher tries both orientations.
            # Lower weight (0.6) reflects ambiguity; FL SHORTNAME match
            # at exact alias confirms orientation.
            abbr_cand = TeamCandidate(
                raw=abbr_block,
                normalized=abbr_block.upper(),
                kind="kalshi_abbr",
                weight=0.6,
            )
            home_candidates.append(abbr_cand)
            away_candidates.append(abbr_cand)

        return home_candidates, away_candidates

    @staticmethod
    def _kickoff(raw: dict, ident: Any) -> tuple[datetime | None, float]:
        """Extract kickoff datetime + confidence.

        Preferred: _kickoff_dt (ISO 8601 UTC string from get_data()'s
        classification). Confidence 1.0.

        Fallback: identity.date + identity.time (parsed from ticker
        e.g. '26MAY07BAYPSG' → 2026-05-07; '26MAY071540PITAZ' →
        2026-05-07 15:40). Confidence depends on whether time was
        present (0.85 for date+time, 0.6 for date only).
        """
        # Path A: explicit _kickoff_dt
        kdt_str = raw.get("_kickoff_dt")
        if kdt_str:
            try:
                return datetime.fromisoformat(kdt_str), 1.0
            except (TypeError, ValueError):
                pass

        # Path B: from ticker via parse_ticker's Identity
        if getattr(ident, "date", None):
            from datetime import time, timezone as tz
            d = ident.date
            time_str = getattr(ident, "time", None)
            if time_str and len(time_str) == 4:
                try:
                    hh = int(time_str[:2])
                    mm = int(time_str[2:])
                    return (
                        datetime.combine(d, time(hh, mm), tzinfo=tz.utc),
                        0.85,
                    )
                except (TypeError, ValueError):
                    pass
            # Date only — confidence 0.6, kickoff at 18:00 UTC fallback
            # mirrors the legacy synth_event behavior.
            return (
                datetime.combine(d, time(18, 0), tzinfo=tz.utc),
                0.6,
            )

        return None, 0.0
