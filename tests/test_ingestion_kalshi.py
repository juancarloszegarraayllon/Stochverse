"""Tests for the SP Architecture Kalshi ingestion module (Phase 1C).

Covers ticker parsing → resolver-fields extraction and the
boundary-validator. The DB-writing path (UPSERT) is exercised by
shared tests against `upsert_provider_record` in
tests/test_ingestion_fl.py — same primitive.
"""
from __future__ import annotations

import pytest

from ingestion.kalshi import _extract_resolver_fields
from ingestion.schema_validation import (
    KalshiMarketValidator,
    validate_or_drift,
)


# ── _extract_resolver_fields ────────────────────────────────────

class TestExtractResolverFields:
    def test_per_fixture_game(self):
        record = {
            "event_ticker":  "KXEPLGAME-26MAY07ARSCHE",
            "series_ticker": "KXEPLGAME",
            "_sport":        "Soccer",
        }
        fields = _extract_resolver_fields(record)
        assert fields["market_type"] == "game"
        assert fields["series_ticker"] == "KXEPLGAME"
        assert fields["abbr_block"] == "ARSCHE"

    def test_per_fixture_total_classified_as_game(self):
        # market_type intentionally collapses sub-market suffixes
        # into 'game' — finer distinction lives in series_ticker.
        record = {
            "event_ticker":  "KXEPLTOTAL-26MAY07ARSCHE",
            "series_ticker": "KXEPLTOTAL",
            "_sport":        "Soccer",
        }
        fields = _extract_resolver_fields(record)
        assert fields["market_type"] == "game"
        assert fields["series_ticker"] == "KXEPLTOTAL"

    def test_outright_classification(self):
        record = {
            "event_ticker":  "KXBALLONDOR-26MESSI",
            "series_ticker": "KXBALLONDOR",
            "_sport":        "Soccer",
        }
        fields = _extract_resolver_fields(record)
        assert fields["market_type"] == "outright"

    def test_unparsed_ticker(self):
        # Bogus ticker shape should not crash; extractor returns 'unparsed'.
        record = {
            "event_ticker":  "GIBBERISH",
            "series_ticker": "",
            "_sport":        "",
        }
        fields = _extract_resolver_fields(record)
        # Could be 'unparsed' or 'outright' (last-resort outright path
        # in parse_ticker for non-empty suffix). Just ensure it's a
        # known classification, not a crash.
        assert fields["market_type"] in {
            "unparsed", "outright", "series", "tournament", "leg", "game",
        }

    def test_missing_fields_dont_crash(self):
        record = {"event_ticker": "KXEPLGAME-26MAY07ARSCHE"}
        # No series_ticker, no _sport — should still return a dict.
        fields = _extract_resolver_fields(record)
        assert isinstance(fields, dict)
        assert "market_type" in fields


# ── KalshiMarketValidator ───────────────────────────────────────

class TestKalshiMarketValidator:
    def test_valid_record_passes(self):
        record = {
            "event_ticker":  "KXEPLGAME-26MAY07ARSCHE",
            "series_ticker": "KXEPLGAME",
            "title":         "Arsenal vs Chelsea",
            "category":      "Sports",
            "_sport":        "Soccer",
        }
        parsed, drift = validate_or_drift(
            provider="kalshi", record_kind="market",
            record_id=record["event_ticker"],
            raw=record, validator=KalshiMarketValidator,
        )
        assert drift is False
        assert parsed.event_ticker == record["event_ticker"]

    def test_missing_event_ticker_fails(self):
        record = {"title": "no ticker here"}
        parsed, drift = validate_or_drift(
            provider="kalshi", record_kind="market", record_id="?",
            raw=record, validator=KalshiMarketValidator,
        )
        assert drift is True
        assert parsed is None

    def test_extra_fields_allowed(self):
        # Kalshi adds new fields over time; they should pass.
        record = {
            "event_ticker":   "KXEPLGAME-26MAY07ARSCHE",
            "FUTURE_FIELD":   {"some": "thing"},
            "another_thing":  [1, 2, 3],
        }
        _, drift = validate_or_drift(
            provider="kalshi", record_kind="market",
            record_id=record["event_ticker"],
            raw=record, validator=KalshiMarketValidator,
        )
        assert drift is False

    def test_only_event_ticker_required(self):
        # Everything else is optional — match the legacy cache shape.
        record = {"event_ticker": "X"}
        parsed, drift = validate_or_drift(
            provider="kalshi", record_kind="market", record_id="X",
            raw=record, validator=KalshiMarketValidator,
        )
        assert drift is False
        assert parsed.event_ticker == "X"
