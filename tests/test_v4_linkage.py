"""Tests for Phase 3 v4 linkage-cache PR — GAMES_BY_EVENT_ID
secondary index, `_apply_v4_linkage`, warm-load, cohort-aware
daily_diff filter, effective_pct, MIN-COHORT floor on linked
universe.

Scope of THIS PR:
  * Linkage infrastructure (bg-refreshed map, warm-load, secondary
    index) end-to-end.
  * `_apply_v4_linkage` stamps observability markers (_v4_linked +
    _v4_fl_event_id); does NOT overlay v3 live-state fields
    (deferred to v4-rendering follow-up).
  * Cohort membership drawn from LINKED universe.
  * daily_diff twelve-bucket schema + cohort_snapshot in report_json.
  * /api/cutover_status effective_pct + universe/linked_universe
    delta.
"""
from __future__ import annotations

import inspect

import pytest


# ── Component 1: FL secondary index ───────────────────────────────

def test_games_by_event_id_populated_on_parse(monkeypatch):
    """When _merge_parsed_events_into_games processes an event with
    an event_id, GAMES_BY_EVENT_ID gets a SAME-DICT reference (not
    a copy) so downstream mutations propagate to both indices."""
    import flashlive_feed as fl
    fl.GAMES.clear()
    fl.GAMES_BY_EVENT_ID.clear()

    # Fake an FL event that _parse_event will accept.
    fake_event = {
        "EVENT_ID":       "test-evt-42",
        "HOME_NAME":      "Team A", "AWAY_NAME": "Team B",
        "HOME_SCORE_CURRENT": 1, "AWAY_SCORE_CURRENT": 0,
        "STAGE_TYPE":     "STARTED", "STAGE": "1",
        "START_TIME":     1_800_000_000,
        "_sport":         "Basketball",
    }
    parsed, _ = fl._merge_parsed_events_into_games([fake_event])
    assert parsed == 1
    # Both indices populated.
    assert len(fl.GAMES) == 1
    assert "test-evt-42" in fl.GAMES_BY_EVENT_ID
    # Same object (not a copy).
    game_via_primary = next(iter(fl.GAMES.values()))
    game_via_secondary = fl.GAMES_BY_EVENT_ID["test-evt-42"]
    assert game_via_primary is game_via_secondary, (
        "GAMES_BY_EVENT_ID must hold the SAME dict as GAMES, not a "
        "copy — mutations propagate via reference"
    )


def test_games_by_event_id_swept_on_stale(monkeypatch):
    """Stale sweep drops from GAMES_BY_EVENT_ID in sync with GAMES.
    Guards against a stale FL entry surviving in the secondary
    index and getting served to a v4 request after the primary
    index's canonical view already GC'd it."""
    import flashlive_feed as fl
    fl.GAMES.clear()
    fl.GAMES_BY_EVENT_ID.clear()

    import time
    stale_ts = int((time.time() - 700) * 1000)  # 700s ago > 600s cutoff
    fl.GAMES["Basketball:teama:teamb"] = {
        "event_id":       "stale-evt",
        "captured_at_ms": stale_ts,
        "sport":          "Basketball",
    }
    fl.GAMES_BY_EVENT_ID["stale-evt"] = fl.GAMES["Basketball:teama:teamb"]

    removed = fl._sweep_stale_games(time.time())
    assert removed == 1
    assert "stale-evt" not in fl.GAMES_BY_EVENT_ID
    assert len(fl.GAMES) == 0


# ── Component 2/4: _apply_v4_linkage semantics ────────────────────

def test_apply_v4_linkage_no_op_on_missing_event_ticker(monkeypatch):
    import linkage
    record: dict = {}   # no event_ticker key
    linkage._apply_v4_linkage(record)
    assert "_v4_linked" not in record
    assert "_v4_fl_event_id" not in record


def test_apply_v4_linkage_no_op_on_missing_linkage_entry(monkeypatch):
    """Cold cache or genuinely unresolved market — both surface as
    linkage-map-miss. Must be silent no-op (no exception, no counter
    increment)."""
    import linkage
    monkeypatch.setattr("linkage._V4_LINKAGE_MAP", {})
    record = {"event_ticker": "KXFAKE001-CARD"}
    linkage._apply_v4_linkage(record)
    assert "_v4_linked" not in record


def test_apply_v4_linkage_no_op_on_missing_fl_game(monkeypatch):
    """Linkage exists but FL game GC'd (stale sweep dropped it).
    Also silent no-op — cohort record just serves v3."""
    import linkage, flashlive_feed as fl
    monkeypatch.setattr("linkage._V4_LINKAGE_MAP", {
        "KXFAKE001-CARD": {
            "fl_event_id": "gcd-evt", "fixture_id": 1, "updated_at_ts": 0.0,
        },
    })
    fl.GAMES_BY_EVENT_ID.clear()   # no entry for "gcd-evt"
    record = {"event_ticker": "KXFAKE001-CARD"}
    linkage._apply_v4_linkage(record)
    assert "_v4_linked" not in record


def test_apply_v4_linkage_stamps_markers_on_success(monkeypatch):
    """Happy path: event_ticker → linkage → GAMES_BY_EVENT_ID all
    resolve. Marker fields land on the record for daily_diff
    attribution and operator log grepping."""
    import linkage, flashlive_feed as fl
    monkeypatch.setattr("linkage._V4_LINKAGE_MAP", {
        "KXFAKE001-CARD": {
            "fl_event_id": "live-evt-99", "fixture_id": 42, "updated_at_ts": 0.0,
        },
    })
    fl.GAMES_BY_EVENT_ID.clear()
    fl.GAMES_BY_EVENT_ID["live-evt-99"] = {
        "event_id":  "live-evt-99",
        "sport":     "Soccer",
        "state":     "in",
        "home_score": 2, "away_score": 1,
    }
    record = {"event_ticker": "KXFAKE001-CARD"}
    linkage._apply_v4_linkage(record)
    assert record.get("_v4_linked") is True
    assert record.get("_v4_fl_event_id") == "live-evt-99"


def test_apply_v4_linkage_does_not_overlay_live_state_this_pr():
    """SCOPE CONTRACT for THIS PR: V4_OVERLAY_KEYS is empty. Full
    v3 live-state parity is the v4-rendering follow-up PR's scope.
    Regression here would ship untested field-level rendering
    divergence."""
    import linkage
    assert linkage.V4_OVERLAY_KEYS == (), (
        f"V4_OVERLAY_KEYS = {linkage.V4_OVERLAY_KEYS!r}. This PR "
        f"scoped V4_OVERLAY_KEYS empty — actual v3 field overlay "
        f"requires refactoring v3's match_game sites and is the "
        f"v4-rendering follow-up PR's scope. Regression here ships "
        f"untested rendering divergence between v3-served and "
        f"v4-served cards."
    )


def test_apply_v4_linkage_uses_uppercase_event_ticker_key(monkeypatch):
    """Linkage map is UPPER-keyed (matches Kalshi ticker convention).
    A record with a lowercase event_ticker must still resolve via
    uppercasing at the lookup site — not doing so silently
    misses linked cards."""
    import linkage, flashlive_feed as fl
    monkeypatch.setattr("linkage._V4_LINKAGE_MAP", {
        "KXFAKE001-CARD": {
            "fl_event_id": "e", "fixture_id": 1, "updated_at_ts": 0.0,
        },
    })
    fl.GAMES_BY_EVENT_ID.clear()
    fl.GAMES_BY_EVENT_ID["e"] = {"event_id": "e"}
    record = {"event_ticker": "kxfake001-card"}   # lowercase
    linkage._apply_v4_linkage(record)
    assert record.get("_v4_linked") is True


# ── Component 5: warm-load contract ───────────────────────────────

def test_apply_v4_linkage_warm_load_from_cache_blob_shape():
    """Snapshot shape written to cache_blobs['v4_linkage_snapshot']
    must match the shape _V4_LINKAGE_MAP expects on read.
    Regression = warm-load succeeds but populated map has wrong
    shape → every lookup falls through silently."""
    import inspect
    import main
    src = inspect.getsource(main._v4_linkage_refresh_loop)
    # Must call save_cache_blob('v4_linkage_snapshot', new_map)
    assert '"v4_linkage_snapshot"' in src, (
        "refresh loop does not persist snapshot to cache_blobs — "
        "warm-load will always cold-start on redeploy"
    )
    # And the warm-load must read the SAME key.
    warm_src = inspect.getsource(main._load_v4_linkage_from_db)
    assert '"v4_linkage_snapshot"' in warm_src


# ── Component 3: bg refresh loop wired ────────────────────────────

def test_v4_linkage_refresh_registered_in_health():
    from ingestion.health import INGESTION_TASK_NAMES, DEFAULT_STALENESS_THRESHOLDS_S
    assert "v4_linkage_refresh" in INGESTION_TASK_NAMES, (
        "v4_linkage_refresh missing from INGESTION_TASK_NAMES — "
        "/api/ingestion_status won't surface it"
    )
    # Threshold matches 30s cadence × 6 = 180s.
    assert DEFAULT_STALENESS_THRESHOLDS_S["v4_linkage_refresh"] == 180


def test_v4_linkage_refresh_wired_via_supervise(monkeypatch):
    """The refresh loop MUST be spawned under supervise() (Layer A/
    D/C semantics). Bare create_task regresses to pre-Day-63 shape
    and any transient failure could silently kill the loop."""
    import inspect
    import main
    startup_src = inspect.getsource(main.startup_event)
    assert '_supervise(\n        "v4_linkage_refresh"' in startup_src or \
           '_supervise("v4_linkage_refresh"' in startup_src, (
        "v4_linkage_refresh not wrapped in supervise() at spawn — "
        "regression to pre-Day-63 death-door class"
    )


# ── Component 6: cohort-aware daily_diff filter ───────────────────

def test_daily_diff_twelve_bucket_schema_present():
    """_diff_pairings emits twelve-bucket schema (six semantic ×
    cohort/out_of_cohort). Backward-compat: _sum_diff_dicts also
    emits the six semantic aggregates so existing render script
    keeps working."""
    import inspect
    import scripts.daily_diff as dd
    src = inspect.getsource(dd._diff_pairings)
    assert '"both_pair_different_cohort"' in src or \
           'f"{bk}_cohort"' in src, (
        "_diff_pairings missing _cohort suffix — Item 7 gate reads "
        "on `..._cohort` counts wouldn't populate"
    )
    assert 'is_v4_cohort' in src, (
        "_diff_pairings does not import cutover.is_v4_cohort — "
        "cohort attribution regressed to always-out-of-cohort"
    )


def test_daily_diff_sum_emits_backward_compat_aggregates():
    """_sum_diff_dicts emits BOTH the twelve _cohort/_out_of_cohort
    buckets AND the six unsuffixed aggregate counts. Existing
    readers (render_daily_diff_report, monitoring dashboards)
    read the unsuffixed six — must keep working post-Phase-3."""
    import scripts.daily_diff as dd
    fake_diffs = [{
        "both_pair_different_cohort": 2,
        "both_pair_different_out_of_cohort": 5,
        "agree_same_fixture_cohort": 100,
        "agree_same_fixture_out_of_cohort": 200,
        "total_evaluated": 307,
        "sample_disagreements": {},
    }]
    summed = dd._sum_diff_dicts(fake_diffs)
    # New twelve-bucket fields.
    assert summed["both_pair_different_cohort"] == 2
    assert summed["both_pair_different_out_of_cohort"] == 5
    # BC unsuffixed aggregate = sum of both cohort halves.
    assert summed["both_pair_different"] == 7, (
        f"backward-compat aggregate = {summed['both_pair_different']}, "
        f"expected 7 (2 cohort + 5 out_of_cohort). Regression means "
        f"existing readers of the unsuffixed key see wrong or missing "
        f"counts."
    )
    assert summed["agree_same_fixture"] == 300


def test_daily_diff_cohort_snapshot_recorded_in_report_json():
    """report_json['cohort_snapshot'] must be populated on every
    cron report so historical reads can attribute cohort membership
    at report-generation time (is_v4_cohort semantics could shift
    later; the snapshot is durable evidence of who was in cohort
    then)."""
    import inspect
    import scripts.daily_diff as dd
    src = inspect.getsource(dd._measure)
    assert '"cohort_snapshot"' in src, (
        "report_json missing cohort_snapshot — historical reports "
        "can't be re-interpreted if cohort semantics change"
    )
    assert '"truncated"' in src, (
        "cohort_snapshot missing truncated flag — silent truncation "
        "at 500 series would look identical to complete list to a "
        "reader unaware of the cap"
    )


# ── Component 7: effective_pct + linked-universe ─────────────────

@pytest.mark.parametrize("cohort_size,universe_size,expected", [
    (2,  30,   6.67),
    (5,  30,  16.67),
    (8,  30,  26.67),
    (0,  30,   0.0),
    (2,   0,   0.0),   # empty universe → 0.0, no ZeroDivisionError
    (30, 30, 100.0),
])
def test_effective_pct_computed_correctly(cohort_size, universe_size, expected):
    import cutover
    got = cutover.compute_effective_pct(cohort_size, universe_size)
    assert abs(got - expected) < 0.01, (
        f"compute_effective_pct({cohort_size}, {universe_size}) = "
        f"{got}, expected {expected}"
    )


def test_cutover_status_exposes_effective_pct_and_linked_delta(monkeypatch):
    """After Day-64 fold, /api/cutover_status shows the DELTA
    between raw sport universe (~360 Soccer today) and linked
    universe (~30) so operator can see how much of the classified
    sport is actually exercisable via v4."""
    from fastapi.testclient import TestClient
    import main
    import cutover, linkage

    # Universe of 30 series, 10 of which are linked.
    monkeypatch.setattr(
        "main._SERIES_SPORT_DYNAMIC",
        {f"KXFAKE{i:03d}": "Soccer" for i in range(30)},
    )
    monkeypatch.setattr(
        "linkage._V4_LINKAGE_MAP",
        {
            f"KXFAKE{i:03d}-GAME1": {
                "fl_event_id": f"fl{i}", "fixture_id": i, "updated_at_ts": 0.0,
            }
            for i in range(10)
        },
    )
    cutover.set_current_config(cutover.CutoverConfig(
        traffic_pct=20, enabled_sports=("Soccer",),
        min_cohort_series=2, source="db",
        loaded_at_ts=1_800_000_000.0,
    ))
    client = TestClient(main.app)
    resp = client.get("/api/cutover_status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["universe_size"] == 30
    assert body["linked_universe_size"] == 10
    # 20% of 10 = 2 → cohort_size=max(2,2)=2 → effective_pct=20.0
    assert body["cohort_size"] == 2
    assert body["cohort_linked_size"] == 2, (
        "cohort_linked_size ≡ cohort_size by construction (cohort "
        "is drawn from linked universe post-Day-64). Regression "
        "means the two diverged — cohort has non-linked members."
    )
    assert body["effective_pct"] == 20.0
    # linkage state fields present.
    assert "linkage_map_size" in body
    assert body["linkage_map_size"] == 10


# ── Regression: is_v4_cohort short-circuits on linkage miss ──────

def test_is_v4_cohort_false_when_series_has_no_linkage(monkeypatch):
    """Day-64 fold contract: sport-scoped series without a linkage
    entry is NOT in cohort — otherwise it would waste a cohort slot
    on a card that would silently fall through to v3."""
    import cutover, linkage
    monkeypatch.setattr(
        "main._SERIES_SPORT_DYNAMIC",
        {"KXLINKED": "Soccer", "KXUNLINKED": "Soccer"},
    )
    monkeypatch.setattr(
        "linkage._V4_LINKAGE_MAP",
        {"KXLINKED-GAME1": {"fl_event_id": "e", "fixture_id": 1, "updated_at_ts": 0.0}},
    )
    cutover.set_current_config(cutover.CutoverConfig(
        traffic_pct=100, enabled_sports=("Soccer",),
        min_cohort_series=1, source="test", loaded_at_ts=0.0,
    ))
    assert cutover.is_v4_cohort("KXLINKED") is True
    assert cutover.is_v4_cohort("KXUNLINKED") is False, (
        "KXUNLINKED has sport=Soccer AND pct=100 but NO linkage — "
        "must NOT be in cohort under the Day-64 linked-universe rule."
    )
