"""Tests for the Day-60 combined follow-up to Task #21 Surface B —
throttle gap alignment, minute-mark herd desync, payload-aware
per-sport override.

Three concerns landed together in one PR because they share the same
economics: gap alignment prevents 429s, herd desync spreads calls
under the aligned gap, payload-aware override caps bandwidth on the
heavy always-live sports (Tennis/Soccer own >85% of billed bytes).
Verifying them in one test file keeps the contract co-located.
"""
from __future__ import annotations

import pytest


# ── Throttle gap alignment ──────────────────────────────────────

def test_fl_min_gap_matches_shared_key_ceiling():
    """RapidAPI Mega caps at 10 rps SHARED PER KEY. Both Railway
    workers share one key → combined 10 rps → per-worker 5 rps →
    per-worker gap 0.2s.

    Env-tunable via FLASHLIVE_MIN_GAP_S — this test asserts the
    DEFAULT is 0.2 so a redeploy without env overrides doesn't
    quietly regress to a burst-happy value. If the key ever gets
    its own dedicated Mega subscription, drop to 0.1 (10 rps each);
    if the shared cap ever tightens, raise; either way update the
    default AND this test together."""
    import flashlive_feed as fl
    assert fl._FL_MIN_GAP_S == 0.2, (
        f"_FL_MIN_GAP_S = {fl._FL_MIN_GAP_S}; expected 0.2 (5 rps per "
        f"worker × 2 workers = 10 rps combined, aligned to Mega's "
        f"shared per-key ceiling). Regression would let the two "
        f"workers combined burst 8× over the ceiling → 429 storms."
    )


# ── Payload-aware per-sport override ────────────────────────────

def test_soccer_and_tennis_capped_at_10s_by_default():
    """Payload-aware refinement: Tennis and Soccer own >85% of billed
    bandwidth (Tennis ~1.08 MB/call, Soccer ~421 KB/call). Post-b5.ii
    they'd have doubled to 5s cadence and swamped the tomorrow-decoupling
    win. Cap them at 10s (pre-b5.ii speed → zero staleness regression)
    while Basketball/Baseball/Hockey keep the 5s upgrade.

    This test locks the DEFAULT value; env vars
    FLASHLIVE_LIVE_POLL_TENNIS_S / FLASHLIVE_LIVE_POLL_SOCCER_S can
    dial post-deploy without a code change."""
    import flashlive_feed as fl
    assert fl._LIVE_POLL_INTERVAL_BY_SPORT.get("Tennis") == 10, (
        "Tennis cadence override missing or wrong — payload-aware "
        "refinement was the ~41% bandwidth cut for the combined PR"
    )
    assert fl._LIVE_POLL_INTERVAL_BY_SPORT.get("Soccer") == 10, (
        "Soccer cadence override missing or wrong — same rationale"
    )


def test_cadence_for_sport_uses_per_sport_override_when_live():
    """When a sport IS in the override dict AND has a fresh live
    observation, _cadence_for_sport must return the OVERRIDE value,
    not the LIVE_POLL_INTERVAL default. Regression here means
    Tennis/Soccer silently drop back to 5s → bandwidth doubles → the
    combined PR's savings disappear."""
    import flashlive_feed as fl
    now = 1_800_000_000.0
    fl._SPORT_LIVE_STATE.clear()
    try:
        fl._observe_sport_state("Tennis", [{"state": "in", "sport": "Tennis"}], now)
        assert fl._cadence_for_sport("Tennis", now) == 10, (
            "Tennis override not applied — falling back to "
            f"LIVE_POLL_INTERVAL ({fl.LIVE_POLL_INTERVAL})"
        )
    finally:
        fl._SPORT_LIVE_STATE.clear()


def test_cadence_for_sport_falls_back_to_default_for_unmapped_sport():
    """Sports NOT in _LIVE_POLL_INTERVAL_BY_SPORT must still get the
    global LIVE_POLL_INTERVAL default when live. Otherwise adding a
    single override entry would silently break every other sport."""
    import flashlive_feed as fl
    now = 1_800_000_000.0
    fl._SPORT_LIVE_STATE.clear()
    try:
        fl._observe_sport_state("Basketball", [{"state": "in", "sport": "Basketball"}], now)
        assert fl._cadence_for_sport("Basketball", now) == fl.LIVE_POLL_INTERVAL, (
            "Basketball is not in the override dict; must fall back "
            "to LIVE_POLL_INTERVAL — got a different value, suggesting "
            "an accidental global-default change"
        )
        # Explicit assertion of the default so a co-change of both
        # LIVE_POLL_INTERVAL and this fallback path is caught here.
        assert fl.LIVE_POLL_INTERVAL == 5
    finally:
        fl._SPORT_LIVE_STATE.clear()


def test_override_only_applies_while_within_sticky_window():
    """Payload-aware override must NOT keep a sport at fast cadence
    once its LIVE_STICKY_S window expires — otherwise Tennis/Soccer
    would sit at 10s forever after their last live observation."""
    import flashlive_feed as fl
    now = 1_800_000_000.0
    fl._SPORT_LIVE_STATE.clear()
    try:
        fl._observe_sport_state("Tennis", [{"state": "in", "sport": "Tennis"}], now)
        # Advance beyond the sticky window; must demote to POLL_INTERVAL
        # regardless of the per-sport override.
        t_expired = now + fl.LIVE_STICKY_S + 1
        assert fl._cadence_for_sport("Tennis", t_expired) == fl.POLL_INTERVAL, (
            "Payload-aware override leaked past LIVE_STICKY_S — sport "
            "would stay at 10s forever, not just during active windows"
        )
    finally:
        fl._SPORT_LIVE_STATE.clear()


# ── Herd desync (per-sport phase offset + per-worker jitter) ───

def test_stagger_distributes_sports_across_poll_interval_window():
    """Deterministic per-sport slot: index × (POLL_INTERVAL / count).
    With 17 sports over 60s that's 3.53s slot width. Every sport
    must land in a DIFFERENT slot — collisions would defeat the
    whole point of the stagger."""
    import flashlive_feed as fl
    N = 17
    offsets = [
        fl._herd_desync_initial_sleep_s(i, N, worker_jitter_s=0.0)
        for i in range(N)
    ]
    # Sport 0 fires immediately, sport 16 fires at ~56.5s (well under
    # the 60s POLL_INTERVAL wrap).
    assert offsets[0] == 0.0
    assert offsets[N - 1] < fl.POLL_INTERVAL, (
        f"Last sport's offset {offsets[N-1]} ≥ POLL_INTERVAL "
        f"({fl.POLL_INTERVAL}) — the stagger wraps past the wake "
        f"window, defeating the desync"
    )
    # Every slot strictly increasing → no two sports collide.
    for i in range(1, N):
        assert offsets[i] > offsets[i - 1], (
            f"Slot collision at index {i}: {offsets[i-1]} → {offsets[i]}. "
            f"Two sports would fire on the same second, reintroducing "
            f"the minute-mark thundering herd."
        )


def test_stagger_slot_width_matches_spec():
    """Slot width must be POLL_INTERVAL / sport_count — 3.53s for
    17 sports at 60s. Guards against a refactor that changes the
    slot-width formula (e.g. hardcoding a slot count of 10 that
    happens to work today but breaks when a sport is added)."""
    import flashlive_feed as fl
    expected_slot_width = fl.POLL_INTERVAL / 17.0
    offset_a = fl._herd_desync_initial_sleep_s(3, 17, worker_jitter_s=0.0)
    offset_b = fl._herd_desync_initial_sleep_s(4, 17, worker_jitter_s=0.0)
    assert abs((offset_b - offset_a) - expected_slot_width) < 1e-6, (
        f"Slot width {offset_b - offset_a} != expected "
        f"{expected_slot_width}; the desync formula changed"
    )


def test_worker_jitter_shifts_the_entire_stagger():
    """Per-worker jitter must ADD to every sport's offset uniformly
    — the deterministic per-sport slots separate sports WITHIN a
    worker; jitter separates the two workers FROM EACH OTHER.

    If jitter accidentally REPLACED the slot instead of shifting it,
    both workers would go back to firing all 17 sports at the same
    small set of times — the pre-fix herd."""
    import flashlive_feed as fl
    N = 17
    base = fl._herd_desync_initial_sleep_s(5, N, worker_jitter_s=0.0)
    jittered = fl._herd_desync_initial_sleep_s(5, N, worker_jitter_s=1.7)
    assert abs(jittered - (base + 1.7)) < 1e-6, (
        f"Jitter did not additively shift the offset: base={base}, "
        f"jittered={jittered}, expected {base + 1.7}. Jitter replaced "
        f"or scaled the offset instead of shifting it — desync broken"
    )


def test_herd_desync_defensive_zero_sport_count():
    """Defensive: sport_count=0 (empty ACTIVE_SPORTS, e.g. a test
    fixture that clears the map) must not divide-by-zero. Returns
    just the worker jitter — the loop still starts, degenerately."""
    import flashlive_feed as fl
    assert fl._herd_desync_initial_sleep_s(0, 0, worker_jitter_s=0.7) == 0.7
