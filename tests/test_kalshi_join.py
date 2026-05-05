"""pytest suite for kalshi_join.py.

Phase 3 verification per SPORTS_V2_PLAN.md:
  - build_kalshi_index correctly groups records by date
  - join_with_fl pairs FL events with their Kalshi records
  - find_unpaired_buckets surfaces Kalshi-only fixtures
  - Critical regression coverage:
      * Multi-market fixture (GAME + TOTAL + SPREAD + BTTS share key)
      * Per-leg records group with their parent fixture
      * Multi-fixture date with no cross-talk
      * MLB doubleheader: time disambiguation
      * Truly Kalshi-only fixtures end up in unpaired_buckets
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from kalshi_identity import compute_fl_identity, parse_ticker
from kalshi_join import (
    Pairing, build_kalshi_index, join_with_fl, find_unpaired_buckets,
    join_pipeline, _canonical_fixture_key, _record_target_identity,
)


def _ts(y: int, mo: int, d: int, h: int = 0, mi: int = 0) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp())


# ── Fixtures (Kalshi cache record shape) ─────────────────────────

def _krec(ticker: str, series: str, sport: str = "Soccer", **extra) -> dict:
    """Build a minimal Kalshi cache record."""
    base = {
        "event_ticker": ticker,
        "series_ticker": series,
        "_sport": sport,
        "title": f"Title for {ticker}",
        "outcomes": [],
    }
    base.update(extra)
    return base


def _flev(home: str, away: str, sh_home: str, sh_away: str,
          start_unix: int, **extra) -> dict:
    """Build a minimal FL event."""
    base = {
        "EVENT_ID": f"fl_{sh_home}{sh_away}_{start_unix}",
        "HOME_NAME": home, "AWAY_NAME": away,
        "SHORTNAME_HOME": sh_home, "SHORTNAME_AWAY": sh_away,
        "START_TIME": start_unix,
    }
    base.update(extra)
    return base


# ── _record_target_identity ──────────────────────────────────────

class TestRecordTargetIdentity:

    def test_per_fixture_record(self):
        r = _krec("KXEPLGAME-26MAY19CFCTOT", "KXEPLGAME")
        ident = _record_target_identity(r, "Soccer")
        assert ident is not None
        assert ident.kind == "per_fixture"
        assert ident.date == date(2026, 5, 19)
        assert ident.abbr_block == "CFCTOT"

    def test_per_leg_routes_to_parent(self):
        r = _krec(
            "KXLOLMAP-26MAY071500ZYBSLY-1", "KXLOLMAP", sport="Esports",
        )
        ident = _record_target_identity(r, "Esports")
        assert ident is not None
        assert ident.kind == "per_fixture"
        assert ident.date == date(2026, 5, 7)
        assert ident.abbr_block == "ZYBSLY"

    def test_outright_returns_none(self):
        r = _krec("KXUCL-26", "KXUCL")
        assert _record_target_identity(r, "Soccer") is None

    def test_unparsed_returns_none(self):
        r = _krec("GIBBERISH-XYZ", "GIBBERISH")
        assert _record_target_identity(r, "Soccer") is None


# ── build_kalshi_index ───────────────────────────────────────────

class TestBuildIndex:

    def test_filters_by_sport(self):
        records = [
            _krec("KXEPLGAME-26MAY19CFCTOT", "KXEPLGAME", sport="Soccer"),
            _krec("KXNBAGAME-26MAY11OKCLAL", "KXNBAGAME", sport="Basketball"),
        ]
        idx = build_kalshi_index(records, "Soccer")
        # Only Soccer record should appear
        all_records = [r for items in idx.values() for _, r in items]
        assert len(all_records) == 1
        assert all_records[0]["_sport"] == "Soccer"

    def test_groups_sub_markets_by_fixture_date(self):
        """GAME + TOTAL + SPREAD + BTTS for Bayern-PSG share a date bucket."""
        records = [
            _krec("KXUCLGAME-26MAY06BMUPSG",   "KXUCLGAME"),
            _krec("KXUCLTOTAL-26MAY06BMUPSG",  "KXUCLTOTAL"),
            _krec("KXUCLSPREAD-26MAY06BMUPSG", "KXUCLSPREAD"),
            _krec("KXUCLBTTS-26MAY06BMUPSG",   "KXUCLBTTS"),
        ]
        idx = build_kalshi_index(records, "Soccer")
        bucket = idx[("Soccer", date(2026, 5, 6))]
        assert len(bucket) == 4

    def test_per_leg_groups_with_parent(self):
        """Esports MAP records should fall in the same bucket as
        their parent GAME records."""
        records = [
            _krec("KXLOLGAME-26MAY071500ZYBSLY",   "KXLOLGAME", sport="Esports"),
            _krec("KXLOLMAP-26MAY071500ZYBSLY-1",  "KXLOLMAP",  sport="Esports"),
            _krec("KXLOLMAP-26MAY071500ZYBSLY-2",  "KXLOLMAP",  sport="Esports"),
        ]
        idx = build_kalshi_index(records, "Esports")
        bucket = idx[("Esports", date(2026, 5, 7))]
        assert len(bucket) == 3

    def test_skips_outrights(self):
        """Outright records (year codes only) shouldn't be in the per-fixture index."""
        records = [
            _krec("KXEPLGAME-26MAY19CFCTOT", "KXEPLGAME"),
            _krec("KXUCL-26", "KXUCL"),                    # outright
            _krec("KXBALLONDOR-26", "KXBALLONDOR"),         # outright
        ]
        idx = build_kalshi_index(records, "Soccer")
        all_records = [r for items in idx.values() for _, r in items]
        assert len(all_records) == 1

    def test_separate_dates_separate_buckets(self):
        """Records on different dates land in different buckets."""
        records = [
            _krec("KXEPLGAME-26MAY19CFCTOT", "KXEPLGAME"),
            _krec("KXEPLGAME-26MAY20BHATOT", "KXEPLGAME"),
        ]
        idx = build_kalshi_index(records, "Soccer")
        assert len(idx) == 2
        assert ("Soccer", date(2026, 5, 19)) in idx
        assert ("Soccer", date(2026, 5, 20)) in idx


# ── join_with_fl ─────────────────────────────────────────────────

class TestJoinWithFL:

    def test_basic_pairing(self):
        """Single FL event paired to a single Kalshi GAME record."""
        records = [
            _krec("KXUCLGAME-26MAY05ARSATM", "KXUCLGAME", title="Atletico at Arsenal"),
        ]
        fl_events = [
            _flev("Arsenal", "Atl. Madrid", "ARS", "ATM",
                  _ts(2026, 5, 5, 15, 0)),
        ]
        idx = build_kalshi_index(records, "Soccer")
        pairings, unpaired = join_with_fl(fl_events, idx, "Soccer")
        assert len(pairings) == 1
        assert len(pairings[0].kalshi_records) == 1
        assert unpaired == []

    def test_multi_market_pairing(self):
        """One FL event should pair with all sub-markets of the fixture."""
        records = [
            _krec("KXUCLGAME-26MAY05ARSATM",   "KXUCLGAME"),
            _krec("KXUCLTOTAL-26MAY05ARSATM",  "KXUCLTOTAL"),
            _krec("KXUCLSPREAD-26MAY05ARSATM", "KXUCLSPREAD"),
            _krec("KXUCLBTTS-26MAY05ARSATM",   "KXUCLBTTS"),
            _krec("KXUCL1H-26MAY05ARSATM",     "KXUCL1H"),
        ]
        fl_events = [
            _flev("Arsenal", "Atl. Madrid", "ARS", "ATM",
                  _ts(2026, 5, 5, 15, 0)),
        ]
        idx = build_kalshi_index(records, "Soccer")
        pairings, unpaired = join_with_fl(fl_events, idx, "Soccer")
        assert len(pairings) == 1
        assert len(pairings[0].kalshi_records) == 5
        assert unpaired == []

    def test_orientation_swap(self):
        """FL home/away in either order matches Kalshi ABBR concatenation."""
        records = [
            # Kalshi ticker has ARSATM (Arsenal first)
            _krec("KXUCLGAME-26MAY05ARSATM", "KXUCLGAME"),
        ]
        # FL event has Atletico as HOME and Arsenal as AWAY
        fl_events = [
            _flev("Atl. Madrid", "Arsenal", "ATM", "ARS",
                  _ts(2026, 5, 5, 15, 0)),
        ]
        idx = build_kalshi_index(records, "Soccer")
        pairings, _ = join_with_fl(fl_events, idx, "Soccer")
        assert len(pairings) == 1, "Orientation should be matched in either direction"

    def test_mlb_doubleheader(self):
        """Same teams, same date, two different times = two FL events,
        two pairings, no cross-contamination."""
        records = [
            _krec(
                "KXMLBGAME-26MAY071240PITAZ", "KXMLBGAME",
                sport="Baseball",
            ),
            _krec(
                "KXMLBGAME-26MAY071800PITAZ", "KXMLBGAME",
                sport="Baseball",
            ),
        ]
        fl_events = [
            _flev("Pittsburgh", "Arizona", "PIT", "AZ",
                  _ts(2026, 5, 7, 12, 40)),
            _flev("Pittsburgh", "Arizona", "PIT", "AZ",
                  _ts(2026, 5, 7, 18, 0)),
        ]
        idx = build_kalshi_index(records, "Baseball")
        pairings, unpaired = join_with_fl(fl_events, idx, "Baseball")
        assert len(pairings) == 2
        # Game 1 paired to its 12:40 Kalshi record only
        for p in pairings:
            assert len(p.kalshi_records) == 1
        # Game 1 FL paired to game 1 Kalshi (the 1240 one)
        ts_to_ticker = {
            p.fl_identity.time: p.kalshi_records[0]["event_ticker"]
            for p in pairings
        }
        assert "1240" in ts_to_ticker[None] if None in ts_to_ticker else True
        # Tickers should match by time
        assert ts_to_ticker.get("1240") == "KXMLBGAME-26MAY071240PITAZ"
        assert ts_to_ticker.get("1800") == "KXMLBGAME-26MAY071800PITAZ"

    def test_unpaired_record_appears_in_unpaired(self):
        """A Kalshi record without an FL counterpart must end up in unpaired."""
        records = [
            _krec("KXUCLGAME-26MAY05ARSATM", "KXUCLGAME"),
            _krec("KXUCLGAME-26MAY06BMUPSG", "KXUCLGAME"),  # no FL pair
        ]
        fl_events = [
            _flev("Arsenal", "Atl. Madrid", "ARS", "ATM",
                  _ts(2026, 5, 5, 15, 0)),
        ]
        idx = build_kalshi_index(records, "Soccer")
        pairings, unpaired = join_with_fl(fl_events, idx, "Soccer")
        assert len(pairings) == 1
        assert len(unpaired) == 1
        assert unpaired[0]["event_ticker"] == "KXUCLGAME-26MAY06BMUPSG"

    def test_no_double_pairing(self):
        """A single Kalshi record can only pair with one FL event,
        even if two FL events have the same identity."""
        records = [
            _krec("KXUCLGAME-26MAY05ARSATM", "KXUCLGAME"),
        ]
        # Two identical FL events (could happen from FL data anomalies)
        fl_events = [
            _flev("Arsenal", "Atl. Madrid", "ARS", "ATM",
                  _ts(2026, 5, 5, 15, 0), EVENT_ID="fl_1"),
            _flev("Arsenal", "Atl. Madrid", "ARS", "ATM",
                  _ts(2026, 5, 5, 15, 0), EVENT_ID="fl_2"),
        ]
        idx = build_kalshi_index(records, "Soccer")
        pairings, unpaired = join_with_fl(fl_events, idx, "Soccer")
        # Only one FL event gets the pairing; the other is unpaired
        # This isn't ideal but it's deterministic and prevents
        # the same Kalshi record from being rendered twice.
        total_kalshi_in_pairings = sum(len(p.kalshi_records) for p in pairings)
        assert total_kalshi_in_pairings == 1, (
            "A single Kalshi record must not be claimed by multiple FL events"
        )

    def test_date_fuzz_within_one_day(self):
        """Cross-timezone fixture: Kalshi date 2026-05-05, FL UTC date 2026-05-04."""
        records = [
            _krec("KXUCLGAME-26MAY05ARSATM", "KXUCLGAME"),
        ]
        fl_events = [
            # FL date is 2026-05-04 (local time, day before Kalshi's UTC)
            _flev("Arsenal", "Atl. Madrid", "ARS", "ATM",
                  _ts(2026, 5, 4, 22, 0)),
        ]
        idx = build_kalshi_index(records, "Soccer")
        pairings, _ = join_with_fl(fl_events, idx, "Soccer", fuzz_days=1)
        assert len(pairings) == 1, "1-day fuzz should match"

    def test_date_fuzz_two_days_off_no_match(self):
        """2-day gap should NOT match (rejected by ticker date check)."""
        records = [
            _krec("KXUCLGAME-26MAY05ARSATM", "KXUCLGAME"),
        ]
        fl_events = [
            _flev("Arsenal", "Atl. Madrid", "ARS", "ATM",
                  _ts(2026, 5, 7, 15, 0)),
        ]
        idx = build_kalshi_index(records, "Soccer")
        pairings, unpaired = join_with_fl(fl_events, idx, "Soccer", fuzz_days=1)
        assert len(pairings) == 0
        assert len(unpaired) == 1


# ── find_unpaired_buckets ────────────────────────────────────────

class TestFindUnpairedBuckets:

    def test_groups_records_by_fixture(self):
        """All sub-markets for the same Kalshi-only fixture should
        end up in one bucket."""
        unpaired_records = [
            _krec("KXUCLGAME-26MAY06BMUPSG",   "KXUCLGAME"),
            _krec("KXUCLTOTAL-26MAY06BMUPSG",  "KXUCLTOTAL"),
            _krec("KXUCLSPREAD-26MAY06BMUPSG", "KXUCLSPREAD"),
            _krec("KXUCLBTTS-26MAY06BMUPSG",   "KXUCLBTTS"),
        ]
        buckets = find_unpaired_buckets(unpaired_records, "Soccer")
        assert len(buckets) == 1
        only_bucket = next(iter(buckets.values()))
        assert len(only_bucket) == 4

    def test_separate_fixtures_separate_buckets(self):
        unpaired_records = [
            _krec("KXUCLGAME-26MAY06BMUPSG", "KXUCLGAME"),
            _krec("KXUCLGAME-26MAY07RMACAR", "KXUCLGAME"),
        ]
        buckets = find_unpaired_buckets(unpaired_records, "Soccer")
        assert len(buckets) == 2

    def test_skips_non_per_fixture(self):
        """Outrights / unparsed records should be filtered out."""
        unpaired_records = [
            _krec("KXUCL-26", "KXUCL"),                    # outright
            _krec("KXBALLONDOR-26", "KXBALLONDOR"),         # outright
        ]
        buckets = find_unpaired_buckets(unpaired_records, "Soccer")
        assert len(buckets) == 0


# ── join_pipeline (integration smoke test) ───────────────────────

class TestJoinPipeline:

    def test_end_to_end_realistic(self):
        cache_records = [
            _krec("KXUCLGAME-26MAY05ARSATM",   "KXUCLGAME"),
            _krec("KXUCLTOTAL-26MAY05ARSATM",  "KXUCLTOTAL"),
            _krec("KXUCLSPREAD-26MAY05ARSATM", "KXUCLSPREAD"),
            _krec("KXUCLGAME-26MAY06BMUPSG",   "KXUCLGAME"),  # no FL pair
            _krec("KXUCL-26",                   "KXUCL"),     # outright (skipped)
            _krec("KXNBAGAME-26MAY07OKCLAL",   "KXNBAGAME",
                  sport="Basketball"),                          # wrong sport
        ]
        fl_events = [
            _flev("Arsenal", "Atl. Madrid", "ARS", "ATM",
                  _ts(2026, 5, 5, 15, 0)),
        ]
        result = join_pipeline(cache_records, fl_events, "Soccer")

        # 4 Soccer per_fixture records (3 paired + 1 unpaired);
        # outright excluded from per_fixture count.
        assert result["sport"] == "Soccer"
        assert result["kalshi_per_fixture_records"] == 4
        assert result["fl_events"] == 1

        # 1 pairing with 3 Kalshi records
        assert len(result["pairings"]) == 1
        p = result["pairings"][0]
        assert p["fl_short"] == ["ARS", "ATM"]
        assert p["kalshi_count"] == 3

        # 1 unpaired bucket (Bayern-PSG)
        assert len(result["unpaired_buckets"]) == 1
        ub = result["unpaired_buckets"][0]
        assert ub["kalshi_count"] == 1


# ── Critical regression coverage ─────────────────────────────────

class TestRegressionFixtures:
    """Cases that broke v1 — these must work in v2."""

    def test_arsenal_atletico_pairs(self):
        """Past v1 bug: corroboration rejected this match."""
        records = [
            _krec("KXUCLGAME-26MAY05ARSATM", "KXUCLGAME"),
            _krec("KXUCLTOTAL-26MAY05ARSATM", "KXUCLTOTAL"),
            _krec("KXUCLSPREAD-26MAY05ARSATM", "KXUCLSPREAD"),
        ]
        fl = [_flev("Arsenal", "Atl. Madrid", "ARS", "ATM",
                    _ts(2026, 5, 5, 15, 0))]
        idx = build_kalshi_index(records, "Soccer")
        pairings, unpaired = join_with_fl(fl, idx, "Soccer")
        assert len(pairings) == 1
        assert len(pairings[0].kalshi_records) == 3
        assert unpaired == []

    def test_bayern_psg_pairs(self):
        """Past v1 bug: needed prefix-matching workaround in v5."""
        records = [
            _krec("KXUCLGAME-26MAY06BMUPSG", "KXUCLGAME"),
        ]
        fl = [_flev("Bayern Munich", "PSG", "BMU", "PSG",
                    _ts(2026, 5, 6, 15, 0))]
        idx = build_kalshi_index(records, "Soccer")
        pairings, _ = join_with_fl(fl, idx, "Soccer")
        assert len(pairings) == 1

    def test_kalshi_only_fixture_emits(self):
        """Future fixture FL hasn't shipped — must surface as unpaired."""
        records = [
            _krec("KXUCLGAME-26MAY13RMACAR", "KXUCLGAME"),
        ]
        fl = []  # FL doesn't have this fixture yet
        idx = build_kalshi_index(records, "Soccer")
        pairings, unpaired = join_with_fl(fl, idx, "Soccer")
        assert pairings == []
        assert len(unpaired) == 1
        # And it should bucket
        buckets = find_unpaired_buckets(unpaired, "Soccer")
        assert len(buckets) == 1
