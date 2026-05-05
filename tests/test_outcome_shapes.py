"""pytest suite for outcome_shapes.py.

Phase 2 verification per SPORTS_V2_PLAN.md:
  - shape_for() returns expected rule for known buckets
  - render_outcomes() normalizes record.outcomes[] correctly
  - outcomes_with_shape() validates + sorts per rule
  - Snapshot sweep over outcome_shapes_*.json: every observed
    bucket either has a rule (and validates), or is recorded as
    a known unmapped bucket so we don't pretend the audit is
    complete when it isn't.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from outcome_shapes import (
    OutcomeShape, LabelKind,
    shape_for, render_outcomes, outcomes_with_shape,
    known_buckets, _to_cents,
)

SNAPSHOTS = Path(__file__).resolve().parent.parent / "kalshi_probe" / "snapshots"


# ── shape_for lookup ─────────────────────────────────────────────

class TestShapeFor:

    def test_soccer_game(self):
        s = shape_for("Soccer", "GAME", "")
        assert s is not None
        assert s.expected_count == 3
        assert s.label_kind == LabelKind.TEAM_OR_TIE
        assert s.has_tie is True

    def test_basketball_game_no_tie(self):
        s = shape_for("Basketball", "GAME", "")
        assert s is not None
        assert s.expected_count == 2
        assert s.has_tie is False

    def test_baseball_f5_has_tie(self):
        """F5 includes tie because a 5-inning split can be tied."""
        s = shape_for("Baseball", "F5", "First 5 Innings")
        assert s is not None
        assert s.has_tie is True
        assert s.expected_count == 3

    def test_mlb_doubleheader_total_range(self):
        s = shape_for("Baseball", "TOTAL", "Total Runs")
        assert s is not None
        assert isinstance(s.expected_count, tuple)

    def test_soccer_btts_binary(self):
        s = shape_for("Soccer", "BTTS", "Both Teams to Score")
        assert s is not None
        assert s.expected_count == 1
        assert s.label_kind == LabelKind.YES_NO_IMPLIED

    def test_unknown_returns_none(self):
        assert shape_for("Soccer", "GIBBERISH", "Nonexistent") is None
        assert shape_for("Quidditch", "GAME", "") is None

    def test_lookup_falls_back_to_market_type_only(self):
        """Player-prop entries are keyed on (sport, '', market_type)."""
        s = shape_for("Basketball", "", "Steals")
        assert s is not None
        assert s.label_kind == LabelKind.TEAM_THRESHOLD

    def test_lookup_falls_back_to_suffix_only(self):
        """When market_type is empty but suffix is meaningful."""
        s = shape_for("Boxing", "", "")
        assert s is not None
        assert s.expected_count == 2

    def test_rugby_has_tie(self):
        s = shape_for("Rugby", "MATCH", "")
        assert s is not None
        assert s.has_tie is True

    def test_mma_distance_binary(self):
        s = shape_for("MMA", "DISTANCE", "Go the Distance")
        assert s is not None
        assert s.expected_count == 1
        assert s.label_kind == LabelKind.YES_NO_IMPLIED


# ── _to_cents normalization ──────────────────────────────────────

class TestToCents:

    def test_int_passthrough(self):
        assert _to_cents(42) == 42
        assert _to_cents(0) == 0

    def test_float_truncates(self):
        assert _to_cents(42.7) == 42

    def test_dollar_string_to_cents(self):
        assert _to_cents("0.4200") == 42
        assert _to_cents("1.0000") == 100
        assert _to_cents("0.5000") == 50

    def test_integer_string(self):
        assert _to_cents("42") == 42

    def test_none_returns_none(self):
        assert _to_cents(None) is None

    def test_empty_string_returns_none(self):
        assert _to_cents("") is None
        assert _to_cents("   ") is None

    def test_garbage_returns_none(self):
        assert _to_cents("not a number") is None
        assert _to_cents([1, 2]) is None


# ── render_outcomes ──────────────────────────────────────────────

class TestRenderOutcomes:

    def _record(self, outcomes):
        return {"outcomes": outcomes}

    def test_compact_fields(self):
        rec = self._record([
            {"label": "Arsenal", "_yb": 47, "_ya": 50, "_na": 51,
             "ticker": "KX-1"},
            {"label": "Atletico", "_yb": 28, "_ya": 30, "_na": 70,
             "ticker": "KX-2"},
        ])
        outs = render_outcomes(rec)
        assert len(outs) == 2
        assert outs[0]["label"] == "Arsenal"
        assert outs[0]["prob"] == 47
        assert outs[0]["yes"] == 50
        assert outs[0]["no"] == 51
        assert outs[0]["ticker"] == "KX-1"

    def test_dollar_string_fields(self):
        rec = self._record([
            {"label": "Bayern Munich",
             "yes_bid_dollars": "0.4200",
             "yes_ask_dollars": "0.4500",
             "no_ask_dollars":  "0.5500"},
        ])
        outs = render_outcomes(rec)
        assert len(outs) == 1
        assert outs[0]["prob"] == 42
        assert outs[0]["yes"] == 45
        assert outs[0]["no"] == 55

    def test_skip_empty_label(self):
        rec = self._record([
            {"label": "", "_yb": 50},
            {"label": "PSG", "_yb": 50},
        ])
        outs = render_outcomes(rec)
        assert len(outs) == 1
        assert outs[0]["label"] == "PSG"

    def test_skip_non_dict_outcome(self):
        rec = self._record(["not a dict", None, {"label": "X", "_yb": 10}])
        outs = render_outcomes(rec)
        assert len(outs) == 1

    def test_uses_underscore_outcomes(self):
        """Cache builder may use _outcomes when outcomes is missing."""
        rec = {"_outcomes": [{"label": "A", "_yb": 50}]}
        outs = render_outcomes(rec)
        assert len(outs) == 1
        assert outs[0]["label"] == "A"

    def test_fallback_no_prices(self):
        """Empty record returns empty list, not crash."""
        outs = render_outcomes({})
        assert outs == []


# ── outcomes_with_shape — validation + sorting ───────────────────

class TestOutcomesWithShape:

    def test_soccer_winner_3_outcomes_validates(self):
        rec = {"outcomes": [
            {"label": "Arsenal",  "_yb": 47},
            {"label": "Atletico", "_yb": 28},
            {"label": "Tie",      "_yb": 26},
        ]}
        result = outcomes_with_shape(rec, "Soccer", "GAME", "")
        assert result["shape"].expected_count == 3
        assert result["validates"] is True
        # Tie should be sorted last
        assert result["outcomes"][-1]["label"] == "Tie"

    def test_count_mismatch_warns(self):
        """Wrong count → validates=False + warning logged."""
        rec = {"outcomes": [
            {"label": "Arsenal",  "_yb": 50},
            {"label": "Atletico", "_yb": 50},
            # Missing Tie
        ]}
        result = outcomes_with_shape(rec, "Soccer", "GAME", "")
        assert result["validates"] is False
        assert any("count" in w for w in result["warnings"])

    def test_unknown_shape_no_warning_about_count(self):
        rec = {"outcomes": [{"label": "X", "_yb": 50}]}
        result = outcomes_with_shape(rec, "Underwater Basket Weaving", "", "")
        assert result["shape"] is None
        assert result["validates"] is True   # nothing to validate against
        assert any("unknown" in w for w in result["warnings"])

    def test_range_count_validates(self):
        """Range-typed expected_count accepts any value in range."""
        # NBA TOTAL has range (9, 12) — feed 11 outcomes
        rec = {"outcomes": [
            {"label": f"Over {i}.5 points scored", "_yb": 50}
            for i in range(11)
        ]}
        result = outcomes_with_shape(rec, "Basketball", "TOTAL", "Total Points")
        assert result["validates"] is True

    def test_range_count_lower_bound_violation(self):
        rec = {"outcomes": [
            {"label": "Over 1.5", "_yb": 50},
        ]}
        result = outcomes_with_shape(rec, "Basketball", "TOTAL", "Total Points")
        assert result["validates"] is False

    def test_no_tie_no_sorting(self):
        """For 2-outcome team shapes, render order preserved."""
        rec = {"outcomes": [
            {"label": "Lakers",         "_yb": 50},
            {"label": "Oklahoma City",  "_yb": 50},
        ]}
        result = outcomes_with_shape(rec, "Basketball", "GAME", "")
        assert result["validates"] is True
        # Should preserve the original order — first stays first
        assert result["outcomes"][0]["label"] == "Lakers"


# ── Snapshot sweep — every observed bucket should have a rule ────

class TestSnapshotCoverage:
    """Walk all outcome_shapes_*.json snapshots. For each bucket
    observed in production, either:
      (a) a rule exists and outcome_count_distribution falls within
          the expected_count, OR
      (b) the bucket is documented as an outright (suffix='', market_type='')
          which gets skipped intentionally — outright shapes are
          variable by definition.
    Anything else fails the test with bucket details so we can
    extend the rule table.
    """

    @staticmethod
    def _collect_buckets():
        """Yield (sport, base, suffix, market_type, count_dist, n_records).

        count_dist is {outcome_count: records_with_that_count}.
        """
        for path in sorted(SNAPSHOTS.glob("outcome_shapes_*.json")):
            try:
                data = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            sport = data.get("sport_filter") or path.stem.replace(
                "outcome_shapes_", "").replace("_", " ").title()
            for b in (data.get("buckets") or []):
                yield (
                    sport,
                    b.get("series_base", ""),
                    b.get("suffix", ""),
                    b.get("market_type", ""),
                    b.get("outcome_count_distribution") or {},
                    b.get("record_count", 0),
                )

    def test_every_per_fixture_bucket_has_a_rule(self):
        """Buckets with non-empty suffix or non-empty market_type — i.e.
        per-fixture / sub-market — must have a shape rule. Empty
        suffix + empty market_type buckets are outrights; rules optional.
        """
        unrules = []
        for sport, base, suffix, mt, dist, n in self._collect_buckets():
            # Outright = both suffix and market_type empty. Skip.
            if not suffix and not mt:
                continue
            rule = shape_for(sport, suffix, mt)
            if rule is None:
                unrules.append((sport, base, suffix, mt, n))

        assert not unrules, (
            f"{len(unrules)} sub-market bucket(s) have no shape rule:\n"
            + "\n".join(
                f"  {s} | base={b} suffix={su!r} mt={m!r} ({n} records)"
                for s, b, su, m, n in unrules[:30]
            )
            + ("\n  ... (more)" if len(unrules) > 30 else "")
        )

    def test_outcome_counts_match_rules(self):
        """For buckets that DO have a rule, the most-common outcome
        count in production must validate against the rule.
        """
        violations = []
        for sport, base, suffix, mt, dist, n in self._collect_buckets():
            if not suffix and not mt:
                continue
            rule = shape_for(sport, suffix, mt)
            if rule is None:
                continue  # covered by other test
            # Convert string keys (JSON) to int
            for count_str, recs in dist.items():
                count = int(count_str)
                if isinstance(rule.expected_count, int):
                    if count != rule.expected_count:
                        violations.append((
                            sport, base, suffix, mt, count,
                            rule.expected_count, recs))
                else:
                    lo, hi = rule.expected_count
                    if not (lo <= count <= hi):
                        violations.append((
                            sport, base, suffix, mt, count,
                            rule.expected_count, recs))

        assert not violations, (
            f"{len(violations)} outcome-count violations:\n"
            + "\n".join(
                f"  {s} | base={b} suffix={su!r} mt={m!r}: "
                f"saw {c} outcomes ({r} records), rule expects {e}"
                for s, b, su, m, c, e, r in violations[:30]
            )
        )
