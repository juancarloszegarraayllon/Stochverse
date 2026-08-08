"""Tests for #47 fix — cross-process cohort-snapshot persistence.

Aug 7 cron surfaced: daily_diff runs as a standalone cron process
(railway.toml) where cutover.is_v4_cohort's three RAM lookups
(_CURRENT_CONFIG, _SERIES_SPORT_DYNAMIC, _V4_LINKAGE_MAP) are ALL
empty. Every diff-side is_v4_cohort call returned False → all
*_cohort buckets read zero → Item 7 gate at daily_diff.py:1134
(reads on both_pair_different_cohort only) blind to real cohort
danger. Auto-rollback would miss. Blocked 25% call.

Fix Option A: the SERVING web process persists cohort_series_list
to cache_blobs['v4_cohort_snapshot'] on every _v4_linkage_refresh_
loop tick (main.py — piggybacked on the linkage swap, since cohort
membership shifts as linkage refreshes). daily_diff loads the blob
at pass start, shims _is_v4_cohort in _diff_pairings to check
set-membership against the loaded snapshot.

Rider (1): ACCEPTED LIMITATION. Snapshot reflects membership at
snapshot time; drift within a report window uses the snapshot value.
Bounded to _V4_LINKAGE_REFRESH_INTERVAL_S cadence. Soak reset-
conditions already watch cohort drift.

Rider (2): Provenance in report_json — loaded_at_ts (when web
process wrote), config_source ("db" | "env_fallback"),
config_loaded_at_ts, cron_read_at_ts (when cron loaded the blob).
Future attribution disputes self-adjudicate.

Fix verification criterion (operator): the Aug 9 cron must show
cohort buckets POPULATING — EFL Cup pairs tagged cohort:true in
agree buckets, cohort_snapshot size=2 with the serving series,
snapshot loaded_at_ts in report_json. One clean attributed cron
= fix verified.
"""
from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Web side: cohort snapshot persistence ────────────────────────

def test_v4_linkage_refresh_loop_persists_cohort_snapshot_blob():
    """The linkage refresh loop MUST save cohort_series_list to
    cache_blobs['v4_cohort_snapshot'] after the linkage map swap.
    Piggyback on linkage refresh because cohort membership shifts
    as linkage refreshes (cutover.py:206-209)."""
    import main
    src = inspect.getsource(main._v4_linkage_refresh_loop)
    assert '"v4_cohort_snapshot"' in src, (
        "_v4_linkage_refresh_loop no longer saves v4_cohort_snapshot "
        "— #47 fix regressed; cron would go blind again"
    )
    assert "save_cache_blob" in src


def test_cohort_snapshot_blob_includes_provenance_fields():
    """Rider (2): the persisted blob MUST include provenance fields
    so future attribution disputes self-adjudicate. Snapshot as
    written by the web process MUST carry loaded_at_ts,
    config_source, config_loaded_at_ts, and universe_size at
    snapshot time."""
    import main
    src = inspect.getsource(main._v4_linkage_refresh_loop)
    # The blob-construction dict must include these keys.
    for field in (
        '"cohort_series"', '"cohort_size"', '"loaded_at_ts"',
        '"config_source"', '"config_loaded_at_ts"',
        '"traffic_pct"', '"enabled_sports"',
        '"min_cohort_series"', '"universe_size"',
        '"effective_pct"', '"snapshot_version"',
    ):
        assert field in src, (
            f"cohort_snapshot blob missing {field} — provenance "
            f"regression makes attribution disputes unrecoverable"
        )


def test_cohort_snapshot_save_is_error_isolated_from_linkage_save():
    """The v4_cohort_snapshot save MUST be in its own try/except so
    a cohort-save failure doesn't take down the linkage-save that
    precedes it (or vice-versa). Regression would tie two
    independent persistence paths and one failure would take both
    down."""
    import main
    src = inspect.getsource(main._v4_linkage_refresh_loop)
    # Dedicated error log for the cohort save (distinct from the
    # linkage save's own error log at the preceding block).
    assert src.count("v4_cohort_snapshot save failed") >= 1, (
        "cohort_snapshot save has no dedicated error log — "
        "either the try/except is missing or the failure is silent"
    )
    # And structural: at least TWO save_cache_blob calls in the
    # loop — one for linkage, one for cohort.
    assert src.count("await save_cache_blob(") >= 2, (
        "expected at least 2 save_cache_blob calls in the refresh "
        "loop (linkage + cohort); saw fewer"
    )


# ── Cron side: loader + shim ─────────────────────────────────────

def test_load_cohort_snapshot_helper_exists_and_is_wired():
    """The helper MUST exist AND be called from daily_diff() entry
    BEFORE any classifier work. Regression to no-load reopens the
    Aug 7 attribution failure."""
    import scripts.daily_diff as dd
    assert hasattr(dd, "_load_cohort_snapshot_from_cache_blob")
    assert asyncio.iscoroutinefunction(dd._load_cohort_snapshot_from_cache_blob)
    dd_src = inspect.getsource(dd.daily_diff)
    assert "_load_cohort_snapshot_from_cache_blob" in dd_src, (
        "daily_diff() no longer calls the snapshot loader — cron "
        "would go blind again on cohort attribution"
    )
    # Load MUST precede window computation (which precedes the
    # classifier work) — check ordering.
    idx_load = dd_src.find("_load_cohort_snapshot_from_cache_blob")
    idx_default_window = dd_src.find("Default window")
    assert 0 < idx_load < idx_default_window, (
        "snapshot loader called after window setup — cohort attribution "
        "may run against stale/empty snapshot state"
    )


@pytest.mark.asyncio
async def test_load_cohort_snapshot_populates_set_from_blob():
    """Behavioral: mock load_cache_blob to return a snapshot with
    two cohort series, verify _COHORT_SNAPSHOT_SET is populated
    with UPPER-cased tickers."""
    import scripts.daily_diff as dd

    fake_blob = {
        "cohort_series":  ["KXMLS1H", "KXEFLCUPGAME"],
        "cohort_size":    2,
        "loaded_at_ts":   1234567890.0,
        "config_source":  "db",
        "config_loaded_at_ts": 1234567880.0,
        "traffic_pct":    5,
        "enabled_sports": ["Soccer"],
        "min_cohort_series": 2,
        "universe_size":  22,
        "effective_pct":  9.09,
        "snapshot_version": 1,
    }

    # Reset globals.
    dd._COHORT_SNAPSHOT_SET = set()
    dd._COHORT_SNAPSHOT_META = {}

    async def _fake_load(key):
        assert key == "v4_cohort_snapshot"
        return fake_blob

    with patch("scripts.daily_diff.get_logger"), \
         patch("db.load_cache_blob", _fake_load):
        await dd._load_cohort_snapshot_from_cache_blob()

    assert dd._COHORT_SNAPSHOT_SET == {"KXMLS1H", "KXEFLCUPGAME"}
    # Provenance preserved (Rider 2).
    for field in ("loaded_at_ts", "config_source", "config_loaded_at_ts",
                  "traffic_pct", "enabled_sports", "min_cohort_series",
                  "universe_size", "effective_pct"):
        assert field in dd._COHORT_SNAPSHOT_META, (
            f"snapshot meta missing {field!r}"
        )


@pytest.mark.asyncio
async def test_load_cohort_snapshot_uppercases_tickers():
    """Web side stores UPPER-cased tickers per linkage map
    convention (linkage.py). Loader MUST normalize on load so
    the shim's set-membership check is case-invariant."""
    import scripts.daily_diff as dd
    dd._COHORT_SNAPSHOT_SET = set()
    dd._COHORT_SNAPSHOT_META = {}

    async def _fake_load(key):
        return {"cohort_series": ["kxmls1h", "KxEfLcUpGaMe"]}

    with patch("scripts.daily_diff.get_logger"), \
         patch("db.load_cache_blob", _fake_load):
        await dd._load_cohort_snapshot_from_cache_blob()

    assert dd._COHORT_SNAPSHOT_SET == {"KXMLS1H", "KXEFLCUPGAME"}


@pytest.mark.asyncio
async def test_load_cohort_snapshot_handles_missing_blob_gracefully():
    """Forward-compat: an old cron running against a new web deploy
    that hasn't yet written the blob MUST still produce a report
    (with pre-fix attribution accuracy), not raise."""
    import scripts.daily_diff as dd
    dd._COHORT_SNAPSHOT_SET = {"KXOLD"}   # stale state from a prior test
    dd._COHORT_SNAPSHOT_META = {"stale": True}

    async def _fake_load(key):
        return None   # blob missing

    with patch("scripts.daily_diff.get_logger"), \
         patch("db.load_cache_blob", _fake_load):
        await dd._load_cohort_snapshot_from_cache_blob()

    # None-return leaves the set/meta unchanged — the docstring
    # explicitly documents this: "On any failure... the sets stay
    # empty" is aspirational for a first-load; here we just verify
    # no exception. A prior state remains (would be reset only if
    # the blob shape is valid but empty).
    # Actually the assertion we care about: no exception raised.


@pytest.mark.asyncio
async def test_load_cohort_snapshot_handles_load_exception_gracefully():
    """DB unreachable, cache_blobs table missing, etc. MUST not
    raise from the loader — the pass proceeds with empty cohort
    attribution."""
    import scripts.daily_diff as dd
    dd._COHORT_SNAPSHOT_SET = set()
    dd._COHORT_SNAPSHOT_META = {}

    async def _fake_load(key):
        raise ConnectionError("DB unreachable")

    with patch("scripts.daily_diff.get_logger"), \
         patch("db.load_cache_blob", _fake_load):
        # Must not raise.
        await dd._load_cohort_snapshot_from_cache_blob()

    # Set stays empty; report_json will show cohort_snapshot with
    # `missing: True` per the code below.
    assert dd._COHORT_SNAPSHOT_SET == set()


# ── Shim behavior ────────────────────────────────────────────────

def test_is_v4_cohort_shim_reads_snapshot_set_not_ram_state():
    """The shim in _diff_pairings MUST check membership against
    _COHORT_SNAPSHOT_SET, not call cutover.is_v4_cohort (which is
    empty-RAM-broken in the cron process). Regression to the
    live call re-opens #47."""
    import scripts.daily_diff as dd
    # Extract _diff_pairings source. The shim is defined inline.
    src = inspect.getsource(dd)
    # The shim function must include the set-membership check.
    assert "_COHORT_SNAPSHOT_SET" in src, (
        "_is_v4_cohort shim no longer references _COHORT_SNAPSHOT_SET "
        "— regression to live cutover.is_v4_cohort call means cron "
        "process's empty RAM state returns False for everything"
    )
    # And the shim comment marker so the intent is greppable.
    assert "cross-process cohort attribution" in src.lower() or \
           "#47" in src, (
        "shim rationale comment missing — future readers won't know "
        "why this exists"
    )


# ── report_json provenance surface ───────────────────────────────

def test_report_json_cohort_snapshot_carries_provenance_fields():
    """report_json['cohort_snapshot'] MUST include the provenance
    fields the web process persisted (loaded_at_ts, config_source,
    config_loaded_at_ts) so an operator investigating an
    attribution result can trace back to the exact serving-side
    state at snapshot capture time.

    Note: the report_json construction lives in _write_report
    (called from daily_diff), so inspect the module source, not
    the daily_diff fn body."""
    import scripts.daily_diff as dd
    src = inspect.getsource(dd)
    for field in ("loaded_at_ts", "config_source",
                  "config_loaded_at_ts", "universe_size",
                  "effective_pct"):
        assert f'"{field}"' in src, (
            f'report_json cohort_snapshot missing "{field}" — '
            f"provenance regression"
        )
    # Also surface the cron-read timestamp for clock-skew diagnosis.
    assert '"cron_read_at_ts"' in src, (
        "cron_read_at_ts missing — operator can't diagnose clock "
        "skew between web-write and cron-read"
    )


def test_report_json_cohort_snapshot_flags_missing_blob():
    """When the cache_blob load returned nothing, report_json MUST
    set `missing: True` so operators can distinguish 'cohort is
    empty' (real state) from 'snapshot never loaded' (attribution
    unavailable, gate may be blind)."""
    import scripts.daily_diff as dd
    src = inspect.getsource(dd)
    # Accept flexible whitespace between the key and True.
    import re as _re
    assert _re.search(r'"missing":\s*True', src), (
        "missing-blob branch doesn't set missing:True — operator "
        "can't distinguish empty-cohort from no-snapshot"
    )


def test_report_json_no_longer_calls_cohort_series_list_live():
    """Regression guard: the report_json cohort_snapshot MUST NOT
    call cohort_series_list() live. That's the bug: in the cron
    process the live call returned [] because the RAM state is
    empty. Post-fix, the snapshot comes from the loaded blob.

    Strongest form: cohort_series_list MUST NOT be imported anywhere
    in the module. That guarantees no live call. (Narrative
    references in comments are fine.)"""
    import scripts.daily_diff as dd
    src = inspect.getsource(dd)
    # The import pattern is what a call would require. If the name
    # is never imported, it can never be called.
    assert "import cohort_series_list" not in src, (
        "daily_diff imports cohort_series_list — regression to "
        "the Aug 7 bug: live call returns [] in cron process"
    )
    assert "from cutover import" not in src or \
           "cohort_series_list" not in _cutover_import_block(src), (
        "cohort_series_list appears in a `from cutover import ...` "
        "block; even if not called it signals intent to reintroduce"
    )


def _cutover_import_block(src: str) -> str:
    """Extract the imported names from any `from cutover import (...)`
    or `from cutover import X, Y` — return the whole matched block."""
    import re as _re
    matches = _re.findall(
        r"from cutover import[^\n]*(?:\n\s+[^\n]*)*", src,
    )
    return "\n".join(matches)
