"""Tests for Task #21 Surface B b5.ii — per-sport adaptive-cadence
FL polling.

Pre-b5.ii: one broad poll every 10s if ANY game was live anywhere.
Post-b5.ii: each sport polls TODAY on its own cadence — 5s when live
or pre-game within PREGAME_PROMOTE_WINDOW_S, sticky for LIVE_STICKY_S
after the last live observation, else 60s. Tomorrow's schedule polls
once every 300s across all sports.

Contract this file locks in:
  1. Cadence gate is time-based (LIVE_STICKY_S window since last live
     observation), NOT per-poll hysteresis. Halftime / rain-delay gaps
     don't demote.
  2. Pre-game promotion — state=="pre" with scheduled_kickoff_ms
     within PREGAME_PROMOTE_WINDOW_S counts as "live" for the cadence
     gate.
  3. Per-sport state is partitioned — Basketball live doesn't
     sticky-promote Tennis.
  4. Warm-start GAMES seeds _SPORT_LIVE_STATE so a live sport in the
     warm cache starts at LIVE_POLL_INTERVAL, no 60s ramp-up beat.
  5. _fetch_one_sport respects _fl_throttle — a refactor removing
     the throttle from the per-sport path would silently blow past
     RapidAPI's rate cap.
"""
from __future__ import annotations

import asyncio
import time

import pytest


@pytest.fixture(autouse=True)
def clean_state():
    """Reset per-sport state and GAMES between tests."""
    import flashlive_feed as fl
    fl._SPORT_LIVE_STATE.clear()
    fl.GAMES.clear()
    yield
    fl._SPORT_LIVE_STATE.clear()
    fl.GAMES.clear()


# ── Cadence gate ────────────────────────────────────────────────

def test_cadence_defaults_to_idle_with_no_observations():
    """A sport with zero live-observation history is idle-cadence.
    Prevents an accidental default-to-fast that would waste calls
    on sports with no matches at all."""
    import flashlive_feed as fl
    now = 1_800_000_000.0
    assert fl._cadence_for_sport("Basketball", now) == fl.POLL_INTERVAL


def test_cadence_promotes_immediately_on_live_observation():
    """A single live observation drops the sport to LIVE_POLL_INTERVAL
    on the very next cadence check — no ramp-up, no averaging."""
    import flashlive_feed as fl
    now = 1_800_000_000.0
    live_game = {"state": "in", "sport": "Basketball"}
    fl._observe_sport_state("Basketball", [live_game], now)
    assert fl._cadence_for_sport("Basketball", now) == fl.LIVE_POLL_INTERVAL


def test_cadence_sticky_across_intervening_idle_polls():
    """Time-based hysteresis contract: a live observation at t=0
    keeps the sport at LIVE_POLL_INTERVAL for the entire LIVE_STICKY_S
    window, even if intervening polls show zero live games (halftime,
    between innings, rain delay, medical timeout).

    Regression guard against the 3-poll/15s hysteresis that would
    have demoted a halftime match. LIVE_STICKY_S defaults to 30 min
    per the b5.ii spec."""
    import flashlive_feed as fl
    t0 = 1_800_000_000.0
    live_game = {"state": "in", "sport": "Basketball"}
    idle_game = {"state": "post", "sport": "Basketball"}

    # First observation: live.
    fl._observe_sport_state("Basketball", [live_game], t0)
    assert fl._cadence_for_sport("Basketball", t0) == fl.LIVE_POLL_INTERVAL

    # Simulate 12 subsequent polls at 5s intervals with ZERO live
    # games (halftime gap). Every check within the sticky window
    # stays at fast cadence.
    for i in range(1, 13):
        t = t0 + i * fl.LIVE_POLL_INTERVAL
        fl._observe_sport_state("Basketball", [idle_game], t)
        assert fl._cadence_for_sport("Basketball", t) == fl.LIVE_POLL_INTERVAL, (
            f"Poll {i} at t+{i*fl.LIVE_POLL_INTERVAL}s demoted prematurely — "
            f"time-based hysteresis contract broken"
        )


def test_cadence_demotes_after_sticky_window_expires():
    """After LIVE_STICKY_S seconds with no fresh live observation,
    the sport drops back to POLL_INTERVAL. Confirms the window
    actually closes — a stuck-open window would waste calls
    indefinitely after the last live match ended."""
    import flashlive_feed as fl
    t0 = 1_800_000_000.0
    live_game = {"state": "in", "sport": "Basketball"}

    fl._observe_sport_state("Basketball", [live_game], t0)
    # Advance beyond the sticky window with no further observations.
    t_expired = t0 + fl.LIVE_STICKY_S + 1
    assert fl._cadence_for_sport("Basketball", t_expired) == fl.POLL_INTERVAL


# ── Pre-game promotion ─────────────────────────────────────────

def test_pregame_within_window_counts_as_live():
    """A state=="pre" game whose kickoff is within
    PREGAME_PROMOTE_WINDOW_S counts as live for cadence promotion.
    Prevents kickoff detection from lagging up to 60s on a
    speed-differentiated product.

    Uses Basketball (not in _LIVE_POLL_INTERVAL_BY_SPORT) so the
    assertion targets the default LIVE_POLL_INTERVAL — Soccer/Tennis
    get their own dedicated payload-aware-override tests below."""
    import flashlive_feed as fl
    now = 1_800_000_000.0
    pregame = {
        "state": "pre",
        "sport": "Basketball",
        # Kickoff in 20 min — inside the 30-min window.
        "scheduled_kickoff_ms": int((now + 20 * 60) * 1000),
    }
    fl._observe_sport_state("Basketball", [pregame], now)
    assert fl._cadence_for_sport("Basketball", now) == fl.LIVE_POLL_INTERVAL


def test_pregame_outside_window_does_not_promote():
    """A pre-game whose kickoff is more than PREGAME_PROMOTE_WINDOW_S
    away must NOT promote the sport. Every sport has scheduled matches
    tomorrow / next week — if any of those flipped a sport to fast
    cadence, b5.ii's savings evaporate."""
    import flashlive_feed as fl
    now = 1_800_000_000.0
    far_pregame = {
        "state": "pre",
        "sport": "Soccer",
        # Kickoff in 4 hours — well outside the 30-min window.
        "scheduled_kickoff_ms": int((now + 4 * 3600) * 1000),
    }
    fl._observe_sport_state("Soccer", [far_pregame], now)
    assert fl._cadence_for_sport("Soccer", now) == fl.POLL_INTERVAL


def test_pregame_with_missing_kickoff_ts_does_not_promote():
    """Defensive: a pre-game with no scheduled_kickoff_ms (data
    quality issue upstream) MUST NOT promote — otherwise every FL
    payload missing the field would flip the sport to fast forever."""
    import flashlive_feed as fl
    now = 1_800_000_000.0
    bad_pregame = {"state": "pre", "sport": "Soccer", "scheduled_kickoff_ms": 0}
    fl._observe_sport_state("Soccer", [bad_pregame], now)
    assert fl._cadence_for_sport("Soccer", now) == fl.POLL_INTERVAL


# ── Per-sport partition ────────────────────────────────────────

def test_per_sport_state_is_partitioned():
    """A live observation for Basketball must NOT sticky-promote
    Tennis. Cross-sport leakage would defeat the whole point of
    per-sport adaptive cadence — one live evening in one sport
    would pin ALL sports at fast."""
    import flashlive_feed as fl
    now = 1_800_000_000.0
    fl._observe_sport_state("Basketball", [{"state": "in", "sport": "Basketball"}], now)
    fl._observe_sport_state("Tennis", [{"state": "post", "sport": "Tennis"}], now)
    assert fl._cadence_for_sport("Basketball", now) == fl.LIVE_POLL_INTERVAL
    assert fl._cadence_for_sport("Tennis", now) == fl.POLL_INTERVAL


# ── Warm-start seeding ─────────────────────────────────────────

def test_warm_start_seeds_sport_state_from_games():
    """Startup seeds _SPORT_LIVE_STATE from warm-loaded GAMES so a
    sport with live games in the cache-blob starts at
    LIVE_POLL_INTERVAL, no 60s ramp-up beat.

    Without this, the first per-sport worker tick fires 60s after
    startup for every sport — the warm-cache had live games but
    they'd sit at idle cadence until the first poll refreshed the
    state. That's exactly the "kickoff detection can't lag 60s"
    problem the operator flagged."""
    import flashlive_feed as fl
    now = 1_800_000_000.0
    # Populate GAMES the same way warm-start would.
    fl.GAMES["Basketball:lakers:celtics"] = {
        "state": "in",
        "sport": "Basketball",
    }
    fl.GAMES["Tennis:djokovic:sinner"] = {
        "state": "post",
        "sport": "Tennis",
    }
    fl._seed_sport_state_from_warm_games(now)
    assert fl._cadence_for_sport("Basketball", now) == fl.LIVE_POLL_INTERVAL, (
        "Warm-loaded live Basketball game failed to seed live state — "
        "worker will pay a 60s beat before ramping up"
    )
    assert fl._cadence_for_sport("Tennis", now) == fl.POLL_INTERVAL, (
        "Post-game Tennis warmly incorrectly promoted — pre-game window "
        "leaked into a post-game observation"
    )


# ── Throttle contract preservation ─────────────────────────────

def test_fetch_one_sport_respects_throttle(monkeypatch):
    """_fetch_one_sport MUST await _fl_throttle before hitting the
    wire. A refactor removing the throttle from the per-sport path
    would let 17 parallel sport loops burst past RapidAPI Mega's
    10 rps ceiling and eat 429s.

    Verifies by counting throttle calls and confirming one-per-fetch."""
    import flashlive_feed as fl

    call_count = {"n": 0}

    async def _counting_throttle():
        call_count["n"] += 1

    class _FakeResponse:
        status_code = 200
        text = ""
        def json(self):
            return {"DATA": []}

    class _FakeClient:
        async def get(self, url, headers=None, params=None):
            return _FakeResponse()

    monkeypatch.setattr(fl, "_fl_throttle", _counting_throttle)
    monkeypatch.setattr(fl, "API_KEY", "test-key")

    async def _run():
        client = _FakeClient()
        for _ in range(3):
            await fl._fetch_one_sport(client, "3", "Basketball", "0")

    asyncio.run(_run())
    assert call_count["n"] == 3, (
        f"Expected 3 throttle calls (one per fetch), got {call_count['n']}. "
        f"The throttle was bypassed on the per-sport path — RapidAPI 10 rps "
        f"ceiling can now be exceeded with 17 parallel sport loops."
    )
