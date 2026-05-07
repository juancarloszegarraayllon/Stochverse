"""Tests for the SP Architecture Kalshi WS ingestion module (Phase 1D).

Covers price-extraction + diff logic + the SQL builder. The actual
DB write path is exercised against docker-compose Postgres in
integration tests; here we verify the SQL shape compiles cleanly
and the diff produces the expected change set.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from ingestion.kalshi_ws import (
    ADVISORY_LOCK_KALSHI_WS,
    _WS_PRICE_FIELDS,
    _extract_price_dict,
    _flush_changes,
)


# ── _extract_price_dict ─────────────────────────────────────────

class TestExtractPriceDict:
    def test_full_set_of_fields(self):
        live = {
            "yes_bid": 50, "yes_ask": 52, "no_bid": 48, "no_ask": 50,
            "last_price": 51, "volume": 1000, "open_interest": 5000,
            "ignored_field": "x",
        }
        out = _extract_price_dict(live)
        for k in _WS_PRICE_FIELDS:
            assert k in out
            assert out[k] == live[k]
        assert "ignored_field" not in out

    def test_partial_fields(self):
        live = {"yes_bid": 50, "yes_ask": 52}
        out = _extract_price_dict(live)
        assert out == {"yes_bid": 50, "yes_ask": 52}

    def test_none_values_dropped(self):
        live = {"yes_bid": 50, "yes_ask": None, "no_bid": 0}
        out = _extract_price_dict(live)
        # 0 is a valid value and should be kept; None is dropped.
        assert out == {"yes_bid": 50, "no_bid": 0}

    def test_no_price_fields_returns_none(self):
        live = {"some_other_field": "x"}
        assert _extract_price_dict(live) is None

    def test_non_dict_returns_none(self):
        assert _extract_price_dict(None) is None
        assert _extract_price_dict("string") is None
        assert _extract_price_dict([1, 2, 3]) is None


# ── Lock key distinct from the REST loop ─────────────────────────

class TestAdvisoryLockKey:
    def test_distinct_from_kalshi_rest(self):
        from ingestion.base import ADVISORY_LOCK_KALSHI
        assert ADVISORY_LOCK_KALSHI_WS != ADVISORY_LOCK_KALSHI


# ── _flush_changes SQL shape ────────────────────────────────────

class TestFlushChangesSql:
    @pytest.mark.asyncio
    async def test_empty_changes_no_op(self):
        mock_session = AsyncMock()
        result = await _flush_changes(mock_session, {})
        assert result == 0
        mock_session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_change_uses_simple_update(self):
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute = AsyncMock(return_value=mock_result)

        changes = {"KX-A": {"yes_bid": 50}}
        rowcount = await _flush_changes(mock_session, changes)
        assert rowcount == 1
        # One execute call.
        assert mock_session.execute.call_count == 1
        # The SQL is the single-row UPDATE branch (no FROM VALUES).
        call_args = mock_session.execute.call_args
        sql_text = str(call_args[0][0])
        assert "FROM (VALUES" not in sql_text
        assert "WHERE ticker = :ticker" in sql_text
        # Bindings include ticker + prices (json string).
        bindings = call_args[0][1]
        assert bindings["ticker"] == "KX-A"
        assert json.loads(bindings["prices"]) == {"yes_bid": 50}

    @pytest.mark.asyncio
    async def test_multi_change_uses_values_clause(self):
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 3
        mock_session.execute = AsyncMock(return_value=mock_result)

        changes = {
            "KX-A": {"yes_bid": 50},
            "KX-B": {"yes_ask": 75},
            "KX-C": {"last_price": 100},
        }
        rowcount = await _flush_changes(mock_session, changes)
        assert rowcount == 3
        assert mock_session.execute.call_count == 1
        call_args = mock_session.execute.call_args
        sql_text = str(call_args[0][0])
        assert "FROM (VALUES" in sql_text
        # Three (:tN, :pN) placeholders.
        assert sql_text.count("(:t") == 3
        assert sql_text.count(":p") >= 3
        # Bindings include all six keys (3 tickers, 3 prices).
        bindings = call_args[0][1]
        assert {"t0", "t1", "t2", "p0", "p1", "p2"} <= set(bindings.keys())

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_rows_match(self):
        """Tickers not yet in sp.kalshi_markets get rowcount=0."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 0
        mock_session.execute = AsyncMock(return_value=mock_result)

        rowcount = await _flush_changes(
            mock_session, {"KX-NEW": {"yes_bid": 1}},
        )
        assert rowcount == 0
