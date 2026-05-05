"""Phase C2d tests — competition_timezones helper.

Direct unit tests on the tz lookup + local_date computation.
Covers the cases that motivated the work:
  * Soccer CONMEBOL evening games crossing midnight UTC
  * NBA East/Central/West-coast late-evening cases
  * KBO/Korean baseball encoding
  * Default fallbacks for unknown competitions
"""
from __future__ import annotations
from datetime import date, datetime, timezone

import pytest

from competition_timezones import competition_tz, compute_local_date


def _ts(y, mo, d, h=0, mi=0):
    """Epoch seconds for a UTC datetime, mirroring the helper used
    in test_fl_registry_seed."""
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp())


# ── competition_tz ───────────────────────────────────────────────

class TestCompetitionTzLookup:

    def test_soccer_conmebol_pattern(self):
        """CONMEBOL Libertadores → Argentina TZ."""
        tz = competition_tz("CONMEBOL Libertadores", "Soccer")
        assert tz == "America/Argentina/Buenos_Aires"

    def test_soccer_ucl_pattern(self):
        tz = competition_tz("Champions League - Play Offs", "Soccer")
        assert tz == "Europe/London"

    def test_soccer_la_liga_pattern(self):
        tz = competition_tz("La Liga", "Soccer")
        assert tz == "Europe/Madrid"

    def test_basketball_nba_pattern(self):
        tz = competition_tz("NBA - Play Offs", "Basketball")
        assert tz == "America/New_York"

    def test_baseball_kbo_pattern(self):
        tz = competition_tz("KBO Regular Season", "Baseball")
        assert tz == "Asia/Seoul"

    def test_baseball_mlb_pattern(self):
        tz = competition_tz("MLB Regular Season", "Baseball")
        assert tz == "America/New_York"

    def test_case_insensitive_match(self):
        """Substring match is case-insensitive."""
        assert competition_tz("nba play offs", "Basketball") \
            == "America/New_York"
        assert competition_tz("KBO REG", "Baseball") \
            == "Asia/Seoul"

    def test_falls_back_to_sport_default(self):
        """Unknown competition name → sport default."""
        assert competition_tz("Mystery League", "Basketball") \
            == "America/New_York"
        assert competition_tz("Some Weird Comp", "Cricket") \
            == "Asia/Kolkata"

    def test_falls_back_to_utc_for_unknown_sport(self):
        """Unknown sport + unknown comp → UTC."""
        assert competition_tz("Whatever", "Curling") == "UTC"

    def test_empty_competition_name(self):
        """Empty name still returns sport default."""
        assert competition_tz("", "Basketball") == "America/New_York"
        assert competition_tz(None, "Basketball") == "America/New_York"


# ── compute_local_date ───────────────────────────────────────────

class TestComputeLocalDate:

    def test_same_day_utc_no_offset(self):
        """UTC start at 12:00 → local date in UTC tz is same day."""
        ts = _ts(2026, 5, 5, 12, 0)
        assert compute_local_date(ts, "UTC") == date(2026, 5, 5)

    def test_argentina_evening_crosses_midnight_utc(self):
        """The Soccer CONMEBOL case from the diff:
        FL UTC start = May 6 00:00. Argentine local time = May 5
        21:00. local_date in ART should be May 5, NOT May 6.
        """
        # May 6 00:00 UTC = May 5 21:00 ART (UTC-3)
        ts = _ts(2026, 5, 6, 0, 0)
        ld = compute_local_date(ts, "America/Argentina/Buenos_Aires")
        assert ld == date(2026, 5, 5)

    def test_argentina_late_evening_crosses_midnight_utc(self):
        """REC-SAN case: FL UTC = May 6 00:30 → ART = May 5 21:30."""
        ts = _ts(2026, 5, 6, 0, 30)
        ld = compute_local_date(ts, "America/Argentina/Buenos_Aires")
        assert ld == date(2026, 5, 5)

    def test_nba_east_coast_evening_in_et(self):
        """NBA East-Coast 7 PM ET game (typical):
        FL UTC start = May 5 23:00 → ET = May 5 19:00 (EDT).
        local_date in ET should be May 5.
        """
        # May 5 23:00 UTC = May 5 19:00 EDT (UTC-4 in DST)
        ts = _ts(2026, 5, 5, 23, 0)
        ld = compute_local_date(ts, "America/New_York")
        assert ld == date(2026, 5, 5)

    def test_kbo_afternoon_in_kst(self):
        """KBO afternoon game (Korea):
        FL UTC start = May 5 05:00 → KST = May 5 14:00.
        local_date in KST = May 5.
        """
        ts = _ts(2026, 5, 5, 5, 0)
        ld = compute_local_date(ts, "Asia/Seoul")
        assert ld == date(2026, 5, 5)

    def test_kbo_other_day_doesnt_collapse(self):
        """KBO next-day game: UTC = May 6 05:00 → KST May 6.
        Different from May 5 case — confirms tz conversion isn't
        just a constant offset hack."""
        ts = _ts(2026, 5, 6, 5, 0)
        ld = compute_local_date(ts, "Asia/Seoul")
        assert ld == date(2026, 5, 6)

    def test_dst_transition_handled(self):
        """ZoneInfo handles DST automatically. NY in March (EST,
        UTC-5) vs May (EDT, UTC-4) differ by an hour."""
        # March 1 2026 — EST, UTC-5
        ts_winter = _ts(2026, 3, 1, 4, 0)  # UTC, = 23:00 Feb 28 EST
        assert compute_local_date(ts_winter, "America/New_York") \
            == date(2026, 2, 28)
        # May 1 2026 — EDT, UTC-4
        ts_summer = _ts(2026, 5, 1, 4, 0)  # UTC, = 00:00 May 1 EDT
        assert compute_local_date(ts_summer, "America/New_York") \
            == date(2026, 5, 1)
