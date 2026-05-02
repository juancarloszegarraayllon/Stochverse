"""Tests for enrichment.normalized_helpers."""
from enrichment.normalized_helpers import (
    _fl_has_data,
    _normalized_state,
    _capabilities_from_probes,
    _standings_team_ids,
    _bracket_team_ids,
)


# ─── _fl_has_data ──────────────────────────────────────────────
def test_fl_has_data_falsy_inputs():
    assert _fl_has_data(None) is False
    assert _fl_has_data({}) is False
    assert _fl_has_data("string") is False
    assert _fl_has_data([]) is False


def test_fl_has_data_empty_DATA_is_false():
    assert _fl_has_data({"DATA": []}) is False
    assert _fl_has_data({"DATA": None}) is False


def test_fl_has_data_dict_DATA_truthy():
    assert _fl_has_data({"DATA": {"foo": "bar"}}) is True


def test_fl_has_data_nested_items():
    """Predicted-lineups response with PLAYERS:[] under each entry
    should count as no-data (structure without content)."""
    resp = {"DATA": [{"FORMATION_NAME": "4-3-3", "PLAYERS": []}]}
    assert _fl_has_data(resp) is False


def test_fl_has_data_nested_with_content():
    resp = {"DATA": [{"PLAYERS": [{"id": "p1"}]}]}
    assert _fl_has_data(resp) is True


def test_fl_has_data_top_level_incidents():
    """summary-incidents responses sometimes ship the payload at
    the root rather than under DATA."""
    resp = {"INCIDENTS": [{"time": "10", "type": "goal"}]}
    assert _fl_has_data(resp) is True


def test_fl_has_data_strips_metadata_keys():
    """Entries with only STAGE_NAME / TAB_NAME / FORMATION_NAME
    are 'structure without content' and shouldn't gate a tab."""
    resp = {"DATA": [{"STAGE_NAME": "Match"}]}
    assert _fl_has_data(resp) is False


def test_fl_has_data_with_real_scalar_payload():
    resp = {"DATA": [{"STAGE_NAME": "Match", "RESULT_HOME": 2}]}
    assert _fl_has_data(resp) is True


# ─── _normalized_state ─────────────────────────────────────────
def test_normalized_state_in_to_live():
    assert _normalized_state({"state": "in"}) == "live"


def test_normalized_state_post_to_final():
    assert _normalized_state({"state": "post"}) == "final"


def test_normalized_state_default_scheduled():
    assert _normalized_state({"state": ""}) == "scheduled"
    assert _normalized_state({"state": "pre"}) == "scheduled"
    assert _normalized_state({}) == "scheduled"


# ─── _capabilities_from_probes ─────────────────────────────────
def test_capabilities_summary_combines_two_probes():
    """has_summary is true if EITHER summary or summary_incidents
    has data — the tab-strip builder shows the Summary tab when
    EITHER endpoint returns content."""
    assert _capabilities_from_probes(
        {"summary": False, "summary_incidents": True}
    )["has_summary"] is True
    assert _capabilities_from_probes(
        {"summary": True, "summary_incidents": False}
    )["has_summary"] is True
    assert _capabilities_from_probes(
        {"summary": False, "summary_incidents": False}
    )["has_summary"] is False


def test_capabilities_standings_aggregates_subtypes():
    """has_standings is true if ANY standing subtype has data."""
    assert _capabilities_from_probes(
        {"standings_overall": True}
    )["has_standings"] is True
    assert _capabilities_from_probes(
        {"standings_form": True}
    )["has_standings"] is True
    assert _capabilities_from_probes(
        {}
    )["has_standings"] is False


# ─── _standings_team_ids ───────────────────────────────────────
def test_standings_team_ids_extracts_from_groups():
    raw = {"DATA": [
        {"GROUP": "A", "ROWS": [
            {"TEAM_ID": "t1", "TEAM_NAME": "Team A"},
            {"TEAM_ID": "t2", "TEAM_NAME": "Team B"},
        ]},
        {"GROUP": "B", "ROWS": [
            {"TEAM_ID": "t3", "TEAM_NAME": "Team C"},
        ]},
    ]}
    assert _standings_team_ids(raw) == {"t1", "t2", "t3"}


def test_standings_team_ids_empty_safe():
    assert _standings_team_ids(None) == set()
    assert _standings_team_ids({}) == set()
    assert _standings_team_ids({"DATA": []}) == set()


# ─── _bracket_team_ids ─────────────────────────────────────────
def test_bracket_team_ids_walks_nested_structure():
    """FL nests DRAW_PARTICIPANT_IDS at varying depths across
    response variants. The walker must find them anywhere."""
    raw = {
        "DATA": [{
            "TABS": {
                "DRAW_PARTICIPANT_IDS": {
                    "1": "team-arsenal",
                    "2": "team-atletico",
                },
            },
            "ROUNDS": [],
        }],
    }
    assert _bracket_team_ids(raw) == {"team-arsenal", "team-atletico"}


def test_bracket_team_ids_top_level():
    raw = {
        "DRAW_PARTICIPANT_IDS": {"1": "psg", "2": "bayern"},
    }
    assert _bracket_team_ids(raw) == {"psg", "bayern"}


def test_bracket_team_ids_empty_safe():
    assert _bracket_team_ids(None) == set()
    assert _bracket_team_ids({}) == set()
    assert _bracket_team_ids({"DRAW_PARTICIPANT_IDS": {}}) == set()
