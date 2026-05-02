"""Regression tests for enrichment.aggregate._aggregate_from_bracket.

Encodes the exact failure modes that bit us this week. If any of
these go red, that bug has reappeared.
"""
from enrichment.aggregate import _aggregate_from_bracket


# ─────────────────────────────────────────────────────────────────
# Tiered scoring: when multiple bracket pairs match the title's
# teams, the matcher must return the one with the strongest match
# AND prefer active (winner==None) over already-decided pairs.
#
# Real scenario: Toluca played LA Galaxy in QFs (winner=home, agg
# 7-2) AND LA FC in SFs (winner=null, leg 1 played 1-2). The card
# title "Toluca vs Los Angeles F" should resolve to the SF pair.
# Previous bug: matcher returned the QF pair (first hit), so card
# showed AGG 7-2 instead of AGG 1-2.
# ─────────────────────────────────────────────────────────────────
TOLUCA_LAFC_BRACKET = {
    "rounds": [
        {
            "round_num": 3,
            "label": "Quarter-finals",
            "pairs": [
                {
                    "home": "toluca",
                    "away": "los-angeles-galaxy",
                    "home_name": "Toluca",
                    "away_name": "Los Angeles Galaxy",
                    "agg_home": 7, "agg_away": 2,
                    "winner": "home",
                    "legs": [{"home": 4, "away": 2}, {"home": 3, "away": 0}],
                },
            ],
        },
        {
            "round_num": 2,
            "label": "Semi-finals",
            "pairs": [
                {
                    "home": "toluca",
                    "away": "los-angeles-fc",
                    "home_name": "Toluca",
                    "away_name": "Los Angeles FC",
                    "agg_home": 1, "agg_away": 2,
                    "winner": None,
                    "legs": [{"home": 1, "away": 2}],
                },
            ],
        },
    ],
}


def test_toluca_lafc_resolves_to_active_sf_not_completed_qf():
    result = _aggregate_from_bracket(
        TOLUCA_LAFC_BRACKET, "Toluca", "Los Angeles F"
    )
    assert result is not None
    assert result["round_name"] == "Semi-finals", \
        "must pick the active SF pair, not the completed QF"
    assert result["aggregate_home"] == 1
    assert result["aggregate_away"] == 2
    assert result["aggregate_winner"] is None
    assert result["leg_number"] == 2  # leg 1 played, leg 2 pending
    assert result["is_two_leg"] is True


# ─────────────────────────────────────────────────────────────────
# Slug match: when the bracket's full team name has no overlap with
# the Kalshi short form, the slug field saves us.
# Real scenario: Bayern Munich vs PSG. FL bracket shows
# home_name="Bayern Munich"/slug="bayern-munich",
# away_name="Paris Saint-Germain"/slug="psg". Title "PSG" has zero
# substring overlap with "paris saint-germain", but matches the
# slug "psg" directly.
# ─────────────────────────────────────────────────────────────────
def test_psg_matches_via_slug_when_full_name_long():
    bracket = {"rounds": [{"round_num": 1, "label": "Final", "pairs": [
        {
            "home": "bayern-munich",
            "away": "psg",
            "home_name": "Bayern Munich",
            "away_name": "Paris Saint-Germain",
            "agg_home": 4, "agg_away": 5,
            "winner": None,
            "legs": [{"home": 2, "away": 3}, {"home": 2, "away": 2}],
        },
    ]}]}
    result = _aggregate_from_bracket(bracket, "Bayern Munich", "PSG")
    assert result is not None
    assert result["aggregate_home"] == 4
    assert result["aggregate_away"] == 5


# ─────────────────────────────────────────────────────────────────
# Acronym fallback: even if FL ships only the long form (no short
# slug), the matcher reduces "Paris Saint-Germain" to its initials
# "PSG" and matches against the Kalshi 3-letter form.
# ─────────────────────────────────────────────────────────────────
def test_psg_matches_via_acronym_when_no_slug_match():
    bracket = {"rounds": [{"round_num": 1, "label": "Final", "pairs": [
        {
            "home": "paris-saint-germain",  # long slug
            "away": "bayern-munich",
            "home_name": "Paris Saint-Germain",
            "away_name": "Bayern Munich",
            "agg_home": 5, "agg_away": 4,
            "winner": None,
            "legs": [{"home": 5, "away": 4}],
        },
    ]}]}
    result = _aggregate_from_bracket(bracket, "PSG", "Bayern Munich")
    assert result is not None
    # Title order: PSG home, Bayern away. Bracket has Paris home,
    # Bayern away. So bracket's "same" orientation matches title's
    # orientation. PSG = 5, Bayern = 4.
    assert result["aggregate_home"] == 5
    assert result["aggregate_away"] == 4


# ─────────────────────────────────────────────────────────────────
# Prefix matcher: "Atletico" (Kalshi short) prefix-matches
# "Atletico Madrid" (FL full).
# ─────────────────────────────────────────────────────────────────
def test_atletico_prefix_matches_atletico_madrid():
    bracket = {"rounds": [{"round_num": 2, "label": "Semi-finals", "pairs": [
        {
            "home": "atl-madrid",
            "away": "arsenal",
            "home_name": "Atl. Madrid",
            "away_name": "Arsenal",
            "agg_home": 1, "agg_away": 1,
            "winner": None,
            "legs": [{"home": 1, "away": 1}],
        },
    ]}]}
    result = _aggregate_from_bracket(bracket, "Arsenal", "Atletico")
    assert result is not None
    # Title: Arsenal home, Atletico away. Bracket: Atl. Madrid home,
    # Arsenal away. Swapped orientation; agg flips.
    assert result["aggregate_home"] == 1  # Arsenal
    assert result["aggregate_away"] == 1  # Atletico


# ─────────────────────────────────────────────────────────────────
# No false positive: title teams that don't appear in the bracket
# at all return None — even if the bracket has *some* team with
# overlapping characters.
# ─────────────────────────────────────────────────────────────────
def test_no_match_returns_none():
    bracket = {"rounds": [{"round_num": 2, "label": "Semi-finals", "pairs": [
        {
            "home": "psg", "away": "bayern-munich",
            "home_name": "PSG", "away_name": "Bayern Munich",
            "agg_home": 5, "agg_away": 4,
            "winner": None,
            "legs": [{"home": 5, "away": 4}],
        },
    ]}]}
    result = _aggregate_from_bracket(
        bracket, "Real Madrid", "Manchester City"
    )
    assert result is None


# ─────────────────────────────────────────────────────────────────
# Empty / malformed inputs return None without raising.
# ─────────────────────────────────────────────────────────────────
def test_empty_bracket_returns_none():
    assert _aggregate_from_bracket(None, "A", "B") is None
    assert _aggregate_from_bracket({}, "A", "B") is None
    assert _aggregate_from_bracket({"rounds": []}, "A", "B") is None
    assert _aggregate_from_bracket(
        {"rounds": [{"pairs": []}]}, "A", "B"
    ) is None


def test_pair_without_aggregate_fields_returns_none():
    bracket = {"rounds": [{"round_num": 2, "label": "Semi-finals", "pairs": [
        {
            "home": "toluca",
            "away": "los-angeles-fc",
            "home_name": "Toluca",
            "away_name": "Los Angeles FC",
            # No agg_home / agg_away — match exists but no aggregate
            "agg_home": None,
            "agg_away": None,
            "winner": None,
            "legs": [],
        },
    ]}]}
    result = _aggregate_from_bracket(bracket, "Toluca", "Los Angeles F")
    assert result is None
