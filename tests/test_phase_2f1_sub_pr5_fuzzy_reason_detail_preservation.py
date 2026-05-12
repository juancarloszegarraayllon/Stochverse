"""Phase 2F.1 sub-PR #5 — fuzzy tier reason_detail preservation
regression tests.

Today's France/Senegal smoke test on PR #133 (Kalshi ticker
`KXWCGAME-26JUN16FRASEN`) surfaced that
`resolver/fuzzy_tier/matcher.py:217-221` early-returns on anchor
failure without preserving the parsed home/away names in
reason_detail:

    if home_match.anchor_failed or away_match.anchor_failed:
        reason_detail["home_anchor_failed"] = home_match.anchor_failed
        reason_detail["away_anchor_failed"] = away_match.anchor_failed
        return self._no_match(
            reason_detail, fail_reason="fuzzy_no_team_resemblance",
        )
    # ↓ ONLY set BELOW the early-return:
    reason_detail["home_canonical"] = home_match.canonical_name
    reason_detail["away_canonical"] = away_match.canonical_name

By contrast, the alias tier preserves them at
`resolver/alias_tier/matcher.py:208-211`:

    if home_match.anchor_failed or away_match.anchor_failed:
        reason_detail["home_anchor_failed"] = home_match.anchor_failed
        reason_detail["away_anchor_failed"] = away_match.anchor_failed
        reason_detail["home_provider_normalized"] = home_struct.raw
        reason_detail["away_provider_normalized"] = away_struct.raw
        return self._no_match(
            reason_detail, fail_reason="alias_no_team_resemblance",
        )

Effect on downstream: `admin/queries.py:_build_suggested_aliases`
falls back to `reason_detail["{side}_provider_normalized"]` or
`["{side}_canonical"]` to query the suggest-alias trigram lookup.
For fuzzy_no_team_resemblance records, both are missing → the
function returns an empty dict → template's wrong empty-state
message fires (sub-PR #4.1 fixes the template; this PR fixes the
underlying resolver-side data loss).

This file captures the contract: after sub-PR #5 ships, every
fuzzy_no_team_resemblance MatchResult must have parsed names in
reason_detail. Unit tests against the matcher directly; no DB
required for the resolver-side regression.
"""
from __future__ import annotations

import pytest


class TestFuzzyTierPreservesParsedNamesOnAnchorFailure:
    """The fuzzy tier must preserve the provider-supplied parsed
    names in reason_detail BEFORE returning fuzzy_no_team_resemblance,
    so the anchor_failed admin surface can suggest alias candidates
    or surface them in the operator-action handoff.

    These tests fail at the head of this branch — that's the contract
    sub-PR #5 must satisfy. Implementation lands in subsequent
    commits."""

    def test_fuzzy_no_team_resemblance_preserves_home_provider_normalized(self):
        """When home_match.anchor_failed is True, the reason_detail
        on the returned MatchResult must contain
        `home_provider_normalized` set to the raw provider home
        string (StructuredName.raw)."""
        result = _run_fuzzy_against_anchor_failure_fixture()
        assert result.reason_code.value == "no_match"
        assert result.reason_detail.get("fail_reason") == "fuzzy_no_team_resemblance"
        # The parsed home-side name must be preserved.
        assert "home_provider_normalized" in result.reason_detail, (
            "Sub-PR #5 contract: fuzzy_no_team_resemblance must "
            "preserve home_provider_normalized (match alias tier "
            "pattern at alias_tier/matcher.py:208-211)."
        )
        assert result.reason_detail["home_provider_normalized"], (
            "home_provider_normalized must be non-empty when set "
            "(empty string defeats downstream suggest-alias lookup)."
        )

    def test_fuzzy_no_team_resemblance_preserves_away_provider_normalized(self):
        result = _run_fuzzy_against_anchor_failure_fixture()
        assert "away_provider_normalized" in result.reason_detail, (
            "Sub-PR #5 contract: fuzzy_no_team_resemblance must "
            "preserve away_provider_normalized."
        )
        assert result.reason_detail["away_provider_normalized"]

    def test_fuzzy_no_team_resemblance_preserves_home_canonical(self):
        """When home_match exists (anchor_failed=True but the match
        attempt produced a best-effort canonical_name), preserve that
        too — it gives the operator a second signal about what the
        matcher thought it might be."""
        result = _run_fuzzy_against_anchor_failure_fixture()
        # home_canonical is set whenever home_match has a non-None
        # canonical_name; not required to exist for every anchor
        # failure path, but if home_match has one, preserve it.
        # The fixture is constructed so home_match.canonical_name is
        # set (best-effort fuzzy match below the anchor threshold).
        assert "home_canonical" in result.reason_detail, (
            "Sub-PR #5 contract: when fuzzy_match has a "
            "canonical_name, preserve it on anchor-failure return."
        )

    def test_alias_tier_pattern_remains_intact(self):
        """Sanity check: sub-PR #5 must not regress the alias tier's
        already-correct preservation of home_provider_normalized /
        away_provider_normalized at alias_tier/matcher.py:208-211."""
        result = _run_alias_against_anchor_failure_fixture()
        assert result.reason_code.value == "no_match"
        assert result.reason_detail.get("fail_reason") == "alias_no_team_resemblance"
        assert "home_provider_normalized" in result.reason_detail
        assert "away_provider_normalized" in result.reason_detail


# ── Fixtures ───────────────────────────────────────────────────


def _run_fuzzy_against_anchor_failure_fixture():
    """Construct a FixtureSignal that the fuzzy tier will route to
    fuzzy_no_team_resemblance (both sides fail to anchor against
    sp.teams). Returns the MatchResult.

    Implementation-deferred: this fixture wires up a minimal
    FuzzyTierMatcher instance with stubbed sp.teams lookup that
    forces anchor_failed=True. Real implementation lands in sub-PR
    #5's first non-test commit."""
    pytest.skip(
        "Fixture not yet wired — sub-PR #5 implementation step 1 "
        "is to build this fixture, which exercises the fuzzy tier's "
        "anchor-failure early-return path."
    )


def _run_alias_against_anchor_failure_fixture():
    """Same shape, alias tier. Used by the regression test that
    sub-PR #5 must not break the alias tier's already-correct
    preservation."""
    pytest.skip(
        "Fixture not yet wired — see sub-PR #5 implementation step 1."
    )
