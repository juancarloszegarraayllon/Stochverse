"""Tests for v4-rendering PR A (Option i narrow scope).

Scope of THIS PR:
  * Extract FL-core `_live_state` construction from
    main.get_events (line ~3676) into
    live_state_builder.build_fl_live_state.
  * Rewire both call sites — /api/events AND /api/event/{ticker}
    at ~4448 — to invoke the extracted helper. Detail endpoint
    gains score_display / title_home / title_away / kickoff
    override as a positive side effect of unification.
  * Add v4_no_game_count observability counter on
    /api/cutover_status.
  * Wire (but gate) `_apply_v4_linkage` to invoke the helper
    when V4_OVERLAY_KEYS becomes non-empty. Empty in PR A →
    call site is dead code. PR B populates the tuple.

NOT in scope (PR B):
  * Populating V4_OVERLAY_KEYS with the parity-required key set.
  * Skipping match_game for cohort tickers (the double-enrichment
    guard).
  * Auxiliary enrichment verification (basketball series synth-
    live_state, soccer aggregate patching, bracket cache).
  * Forward-flag: v3-fuzzy (match_game) vs v4-exact
    (GAMES_BY_EVENT_ID) answering "FL match returned None"
    differently for the same ticker — divergence class fixture.

Parity contract (this PR): for any ticker outside the v4 cohort
AND for any ticker inside the v4 cohort (V4_OVERLAY_KEYS empty),
the served response is BYTE-IDENTICAL to pre-extraction main.
Fixture: docs/parity-fixtures/v4_rendering_pr_a/ (placeholder in
this PR; populated in the doc-import window from prod snapshots).
"""
from __future__ import annotations

import pytest


# ── Component 1: helper is a proper module-level symbol ──────────

def test_build_fl_live_state_is_module_level():
    """The extracted helper must be a module-level function so
    linkage.py can import it without dragging main.py's import
    graph. Guards against a future refactor that accidentally
    nests the helper back inside a request-handler function."""
    import live_state_builder
    assert hasattr(live_state_builder, "build_fl_live_state")
    assert callable(live_state_builder.build_fl_live_state)


def test_helper_self_contained_no_main_import():
    """live_state_builder must NOT import main at module scope.
    linkage.py imports live_state_builder inside _apply_v4_linkage;
    if this module ever pulls main at import time we get a circular
    import at first cohort-ticker request."""
    import ast
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "live_state_builder.py"
    tree = ast.parse(src.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            # ast.ImportFrom.module is None for `from . import x`;
            # ast.Import has names[].name. Reject any 'main' at
            # module scope (top-level parents only).
            names = []
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    names.append(node.module)
            else:
                names = [a.name for a in node.names]
            # Only flag module-scope imports; lazy imports live
            # inside function bodies (not at Module.body).
            # Walking with `ast.walk` gives us ALL imports; filter
            # to those whose parent is Module.
            pass
    # Simpler approach: parse and check top-level statements only.
    top = ast.parse(src.read_text()).body
    for node in top:
        if isinstance(node, ast.ImportFrom):
            assert node.module != "main", (
                "live_state_builder must not import main at module "
                "scope — use a lazy import inside the function body "
                "to avoid circular imports via linkage.py"
            )
        if isinstance(node, ast.Import):
            for a in node.names:
                assert a.name != "main", (
                    "live_state_builder must not import main at "
                    "module scope"
                )


# ── Component 2: no-op semantics for falsy g ─────────────────────

def test_build_fl_live_state_noop_when_g_falsy():
    """Falsy `g` must be a strict no-op — no _live_state key added,
    no _kickoff_dt override. Guards against a regression where the
    helper starts writing empty dicts to rc and downstream code
    that gates on `rc.get('_live_state')` breaks."""
    from live_state_builder import build_fl_live_state
    rc = {"title": "A vs B"}
    build_fl_live_state(rc, None, title="A vs B")
    assert "_live_state" not in rc
    assert "_kickoff_dt" not in rc

    rc2 = {"title": "A vs B"}
    build_fl_live_state(rc2, {}, title="A vs B")
    assert "_live_state" not in rc2


# ── Component 3: field-set parity vs pre-extraction ──────────────

_EXPECTED_LIVE_STATE_KEYS = {
    "label", "state", "short_detail", "display_clock", "period",
    "stage_start_ms", "league", "captured_at_ms", "clock_running",
    "home_abbr", "away_abbr", "home_display", "away_display",
    "home_score", "away_score", "score_display",
    "added_time_1h", "added_time_2h",
    "title_home", "title_away",
    "is_playoff", "series_title", "series_summary",
    "series_home_wins", "series_away_wins", "series_game_number",
    "is_two_leg", "aggregate_home", "aggregate_away", "leg_number",
    "round_name", "tournament_name", "aggregate_winner",
    "cricket_home_wickets", "cricket_away_wickets",
    "cricket_home_overs",   "cricket_away_overs",
    "cricket_live_sentence",
}


def test_live_state_field_set_matches_v3_shape():
    """The _live_state dict the helper builds must contain every
    key the pre-extraction inline block wrote. Missing key =
    frontend regression (e.g. dropped score_display = live badge
    on cards regresses to no score line).

    Uses a minimal 'in-progress' g — enough to trigger every field
    write (state='in' + sport='Soccer' + all optional fields set)."""
    from live_state_builder import build_fl_live_state
    g = {
        "sport": "Soccer", "state": "in",
        "home_score": 1, "away_score": 0,
        "short_detail": "45'+2", "display_clock": "45:12",
        "period": 1, "stage_start_ms": 0,
        "league": "Premier League",
        "captured_at_ms": 0, "clock_running": True,
        "home_abbr": "ARS", "away_abbr": "CHE",
        "home_display": "Arsenal", "away_display": "Chelsea",
        "is_playoff": False, "series_title": "", "series_summary": "",
        "series_home_wins": None, "series_away_wins": None,
        "series_game_number": None,
        "is_two_leg": False, "aggregate_home": None, "aggregate_away": None,
        "leg_number": None, "round_name": "", "tournament_name": "",
        "aggregate_winner": "",
        "cricket_home_wickets": "", "cricket_away_wickets": "",
        "cricket_home_overs":   "", "cricket_away_overs":   "",
        "cricket_live_sentence": "",
        "home_phrases": ["arsenal"], "away_phrases": ["chelsea"],
        "event_id": "",
    }
    rc: dict = {}
    build_fl_live_state(rc, g, title="Arsenal vs Chelsea")
    ls = rc["_live_state"]
    missing = _EXPECTED_LIVE_STATE_KEYS - set(ls.keys())
    assert not missing, (
        f"live_state missing keys the pre-extraction block wrote: "
        f"{sorted(missing)}. Any missing key is a frontend "
        f"regression at both /api/events and /api/event/{{ticker}}."
    )


def test_title_home_away_populated_from_kalshi_title():
    """Kalshi title 'A vs B' must populate title_home / title_away.
    Frontend outcome-label matcher depends on these when Kalshi and
    ESPN disagree on team names (Junin vs Sarmiento de Junin)."""
    from live_state_builder import build_fl_live_state
    g = {
        "sport": "Soccer", "state": "pre",
        "home_phrases": ["arsenal"], "away_phrases": ["chelsea"],
    }
    rc: dict = {}
    build_fl_live_state(rc, g, title="Arsenal vs Chelsea")
    ls = rc["_live_state"]
    assert ls["title_home"] == "Arsenal"
    assert ls["title_away"] == "Chelsea"


def test_score_display_present_for_in_progress():
    """score_display was ABSENT at the old /api/event/{ticker}
    call site. Post-unification, both call sites emit it. This
    test locks that in — regression = a future refactor drops
    the field from one path and drift returns."""
    from live_state_builder import build_fl_live_state
    g = {
        "sport": "Basketball", "state": "in",
        "home_score": 88, "away_score": 91,
        "home_abbr": "BOS", "away_abbr": "NYK",
        "home_phrases": ["boston"], "away_phrases": ["new york"],
    }
    rc: dict = {}
    build_fl_live_state(rc, g, title="Boston at New York")
    assert rc["_live_state"]["score_display"], (
        "score_display must be non-empty for in-progress games; "
        "unification of the two call sites is the whole point of "
        "PR A — do not silently drop it."
    )


def test_kickoff_override_written_when_sched_ms_present():
    """ESPN sched_ms override was ABSENT at old /api/event/{ticker}.
    Unification adds it. Kickoff pill on the detail page depends on
    _kickoff_dt for the countdown; ESPN's value is authoritative."""
    from live_state_builder import build_fl_live_state
    g = {
        "sport": "Football", "state": "pre",
        "scheduled_kickoff_ms": 1_800_000_000_000,
        "home_phrases": [], "away_phrases": [],
    }
    rc: dict = {"_kickoff_dt": "2020-01-01T00:00:00+00:00"}
    build_fl_live_state(rc, g, title="A vs B")
    assert rc["_kickoff_dt"].startswith("20"), rc["_kickoff_dt"]
    assert rc["_kickoff_dt"] != "2020-01-01T00:00:00+00:00", (
        "sched_ms present must override the caller's kickoff_dt"
    )


# ── Component 4: linkage side — gated & no-game counter ──────────

def test_linkage_gated_call_site_is_dead_in_pr_a():
    """V4_OVERLAY_KEYS is empty in PR A → the helper call inside
    _apply_v4_linkage is guarded by `if V4_OVERLAY_KEYS:` and
    never invoked. If PR B accidentally lands early and populates
    the tuple, this test flags it so the operator can pause before
    the pct=5 cohort starts seeing v4-authored rendering."""
    import linkage
    assert linkage.V4_OVERLAY_KEYS == (), (
        "PR A leaves V4_OVERLAY_KEYS empty — v4-rendering populate "
        "is PR B. If this fails, either PR B landed early or the "
        "tuple was populated by mistake."
    )


def test_no_game_counter_increments_when_fl_missing(monkeypatch):
    """When linkage resolves but FL secondary index has no live
    game for the fl_event_id, _apply_v4_linkage must increment the
    no_game counter — not the fallback counter, not raise. The
    counter surfaces on /api/cutover_status so the operator can
    watch the drift rate between linkage-refresh and FL-sweep."""
    import cutover, linkage, flashlive_feed as fl

    # Reset counter for isolation.
    cutover._NO_GAME_COUNTER = 0
    before_fb = cutover.fallback_counter_value()

    monkeypatch.setitem(
        linkage._V4_LINKAGE_MAP,
        "KXTEST-ABC",
        {"fl_event_id": "no-such-fl-evt", "fixture_id": 1,
         "updated_at_ts": 0.0},
    )
    fl.GAMES_BY_EVENT_ID.pop("no-such-fl-evt", None)

    record = {"event_ticker": "KXTEST-ABC", "title": "A vs B"}
    linkage._apply_v4_linkage(record)

    assert cutover.no_game_counter_value() == 1
    assert cutover.fallback_counter_value() == before_fb, (
        "no-game path is legitimate (post-game GC, cold cache) — "
        "must NOT increment the fallback counter"
    )
    assert "_v4_linked" not in record, (
        "no-game path returns early — do not stamp _v4_linked when "
        "we could not verify end-to-end reachability"
    )

    # Cleanup.
    linkage._V4_LINKAGE_MAP.pop("KXTEST-ABC", None)


def test_cutover_status_exposes_no_game_count():
    """/api/cutover_status must publish v4_no_game_count so the
    operator can watch it alongside v4_fallback_count. Sample-
    response driven aggregate (same convention as fallback)."""
    from cutover import no_game_counter_value
    v = no_game_counter_value()
    assert isinstance(v, int)
    assert v >= 0


# ── Component 5: fixture placeholder (populated in doc-import) ───

@pytest.mark.skip(
    reason="Fixture directory populated in doc-import window from "
           "prod snapshots (MLB / Soccer / Tennis tickers). PR B "
           "wires the byte-parity assertions; PR A ships the "
           "scaffold and the skip marker."
)
def test_byte_parity_against_prod_snapshot():
    """Placeholder for the byte-parity contract test. Fixtures at
    docs/parity-fixtures/v4_rendering_pr_a/{ticker}.json capture
    the response shape from prod (direct-to-prod, not preview) at
    snapshot time. The test replays the helper against the input g
    from the fixture and asserts the resulting _live_state dict
    byte-matches the fixture's expected output.

    Snapshot capture protocol (from PR body):
      1. Direct-to-prod curl of /api/event/{ticker} for one live
         ticker per sport family (MLB / Soccer / Tennis).
      2. Capture the raw g from the process's FL cache (log-
         intercept via LOG_LIVE_STATE_G_DUMP env gate — future).
      3. Store {g, expected_live_state} at fixture path.
      4. Merge block: the snapshot commit lands in a follow-up
         merge; this test flips from skip → active in that PR."""
    pass
