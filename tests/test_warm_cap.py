"""Guards the FLASHLIVE_WARM_CAP default and its use in the
/api/events?warm= handler.

Rationale (PR #276 addendum): the request-path warm fans out to N
parallel FL /v1/events/data probes wrapped in a 2s wait_for. Under
the 0.2s per-worker throttle gap (Mega 10 rps shared per key ÷ 2
workers), only N × 0.2s worth of probes complete before the timeout
fires — so any N > 10 is wasted resolver work. The cap MUST default
10, and the handler MUST slice the incoming ticker list against
that cap, not a hardcoded 30.
"""
from __future__ import annotations

import inspect


def test_flashlive_warm_cap_default_matches_throttle_budget():
    """Default MUST be 10 — 10 probes × 0.2s throttle gap = 2s
    _warm_specific_events wait ceiling. Regression to 30 would
    silently waste ~66% of the request-path resolver work under
    the aligned gap."""
    import main
    assert main._FLASHLIVE_WARM_CAP == 10, (
        f"_FLASHLIVE_WARM_CAP = {main._FLASHLIVE_WARM_CAP}; expected 10 "
        f"(matches 2s / 0.2s = 10 concurrent probes finishable within "
        f"the _warm_specific_events wait_for ceiling). Regression here "
        f"reintroduces the wasted-warm PR #276 addendum fixed."
    )


def test_events_handler_uses_the_cap_constant_not_a_hardcoded_number():
    """The /api/events handler must slice `warm.split(",")` against
    `_FLASHLIVE_WARM_CAP`, not a hardcoded literal. Direct source
    inspection because standing up the FastAPI test client + faking
    the entire cache + FL client for one slice assertion is heavier
    than this contract deserves."""
    import main
    src = inspect.getsource(main)
    # Must reference the constant on the slice.
    assert "warm.split(\",\") if t][:_FLASHLIVE_WARM_CAP]" in src, (
        "The /api/events?warm= handler no longer slices against "
        "_FLASHLIVE_WARM_CAP — either the constant was renamed or a "
        "hardcoded literal crept back in. Either way, the warm cap is "
        "no longer env-tunable and the throttle-budget contract is "
        "no longer testable from this file."
    )
    # Sanity: the pre-fix hardcoded [:30] must not survive alongside
    # the cap reference (would indicate a partial refactor).
    assert 'warm.split(",") if t][:30]' not in src, (
        "Pre-fix hardcoded [:30] slice still present in main.py — "
        "the cap-constant refactor was only partially applied, warm "
        "cap silently defaults to 30 in whichever branch survived."
    )
