"""Tests for Task #43 — score_flush 120s-ceiling stagger + instrumentation.

Two things ship in this PR:

  (c) LINKAGE-LOOP STAGGER — `_v4_linkage_refresh_loop` default
      cadence 30s → 60s + 15s startup jitter. Kills the guaranteed
      30s co-schedule collision with `_score_flush_loop` on the
      pooled engine (pool_size=5). #288 design flaw, fixed on
      principle without waiting for timing data.

  (diag) PER-SUBSTEP INSTRUMENTATION — `_score_flush_pass` gains
      per-substep monotonic timing brackets (sync_scores_to_db,
      upsert_entities, refresh_alias_sport_cache) + a `phase` tag
      threaded from the caller loop so the pass_timeout log line
      identifies whether a trip was on the plain "flush" cycle or
      the every-5th "flush+seed" cycle. Env-gated
      LOG_SCORE_FLUSH_TIMINGS=1 (default off) — dark launch pattern
      same as LOG_RESPONSE_SIZE. Post-deploy read attributes the
      real cost source; #43(b) bulk-rewrite ships only if timings
      stay near 120s ceiling post-stagger.
"""
from __future__ import annotations

import inspect


# ── (c) Linkage-loop stagger ─────────────────────────────────────

def test_linkage_refresh_default_interval_is_60_seconds():
    """`_V4_LINKAGE_REFRESH_INTERVAL_S` default MUST be 60 (not 30)
    to break the guaranteed 30s co-schedule collision with
    _score_flush_loop's own 30s cadence. Env override preserved for
    operators who want tighter cadence."""
    import main
    src = inspect.getsource(main)
    # The assignment MUST read the env with default "60".
    assert 'os.environ.get("V4_LINKAGE_REFRESH_INTERVAL_S", "60")' in src, (
        "V4_LINKAGE_REFRESH_INTERVAL_S default is not 60 — the "
        "#288 design flaw (30s co-schedule collision with "
        "score_flush) reopens; upsert_entities chunk checkouts on "
        "the pooled engine will contend with linkage-refresh reads "
        "and the 120s ceiling trips return"
    )


def test_linkage_refresh_has_startup_jitter():
    """`_V4_LINKAGE_STARTUP_JITTER_S` MUST exist and default > 0.
    Even with identical env intervals, jitter ensures the two 30s-
    cadence loops don't align on their first collision after boot."""
    import main
    src = inspect.getsource(main)
    assert 'os.environ.get("V4_LINKAGE_STARTUP_JITTER_S"' in src, (
        "_V4_LINKAGE_STARTUP_JITTER_S env-configurable jitter "
        "constant missing"
    )
    # Assert the loop body sleeps for jitter before entering while True.
    loop_src = inspect.getsource(main._v4_linkage_refresh_loop)
    assert "_V4_LINKAGE_STARTUP_JITTER_S" in loop_src, (
        "_v4_linkage_refresh_loop no longer references the startup "
        "jitter constant — regression to guaranteed 30s collision"
    )
    assert "asyncio.sleep(_V4_LINKAGE_STARTUP_JITTER_S)" in loop_src, (
        "startup jitter sleep missing from the linkage loop body"
    )


def test_linkage_refresh_startup_jitter_default_is_15_seconds():
    """Default matches the 15s startup sleep score_flush already
    has, so the two loops boot-desync by exactly one interval."""
    import main
    src = inspect.getsource(main)
    assert 'os.environ.get("V4_LINKAGE_STARTUP_JITTER_S", "15")' in src


# ── (diag) LOG_SCORE_FLUSH_TIMINGS env gate ──────────────────────

def test_log_score_flush_timings_default_off():
    """Ships DARK. Regression to default-on would double the log
    volume in steady state — exactly the wrong behavior for a
    diagnostic flag."""
    import main
    src = inspect.getsource(main)
    assert 'os.environ.get(\n    "LOG_SCORE_FLUSH_TIMINGS", "0",\n)' in src or \
           'os.environ.get("LOG_SCORE_FLUSH_TIMINGS", "0")' in src, (
        "LOG_SCORE_FLUSH_TIMINGS default is not '0' — regression"
    )


# ── (diag) Phase attribution ─────────────────────────────────────

def test_score_flush_pass_takes_phase_argument():
    """`_score_flush_pass` MUST accept a `phase` argument so the
    caller can compute the phase BEFORE the pass and log it on
    timeout (when the pass never returns).

    Source check because the function is defined as a closure inside
    _score_flush_loop — we can't import it directly."""
    import main
    src = inspect.getsource(main._score_flush_loop)
    # Assert the def signature includes phase.
    assert "async def _score_flush_pass(phase: str)" in src or \
           "async def _score_flush_pass(phase)" in src, (
        "_score_flush_pass no longer takes a phase argument — "
        "pass_timeout log line can't attribute the trip to "
        "flush-vs-flush+seed"
    )


def test_score_flush_loop_computes_phase_before_pass():
    """Caller MUST compute phase BEFORE calling _score_flush_pass so
    the pass_timeout log line has the phase even when the pass
    never returns. Counter is bumped-then-reset in the caller, not
    inside the pass."""
    import main
    src = inspect.getsource(main._score_flush_loop)
    # Order matters: phase MUST be assigned before await asyncio.wait_for(_score_flush_pass(phase), ...).
    idx_phase_assign = src.find('phase = "flush+seed"')
    idx_pass_call = src.find("_score_flush_pass(phase)")
    assert idx_phase_assign > 0 and idx_pass_call > 0
    assert idx_phase_assign < idx_pass_call, (
        "phase must be computed BEFORE the pass call — pass_timeout "
        "log needs it in the timeout branch"
    )


def test_pass_timeout_log_includes_phase():
    """The pass_timeout warning at ingestion.task.pass_timeout MUST
    include `phase=<X>` so log grep can distinguish the two cycles."""
    import main
    src = inspect.getsource(main._score_flush_loop)
    # Find the pass_timeout warning template.
    idx = src.find('"ingestion.task.pass_timeout task=score_flush ')
    assert idx > 0, "pass_timeout log template not found"
    # Check phase is included in the format string within a few
    # hundred chars.
    template_region = src[idx:idx + 400]
    assert "phase=%s" in template_region, (
        "pass_timeout log line missing phase=%s — timeout "
        "attribution regressed"
    )


# ── (diag) Per-substep timing brackets ───────────────────────────

def test_score_flush_pass_wraps_all_three_substeps_in_monotonic_brackets():
    """Every substep (sync_scores_to_db, upsert_entities,
    refresh_alias_sport_cache) MUST be wrapped in
    `time.monotonic()` bookends with an INFO log to
    "score_flush_timings" (gated by _LOG_SCORE_FLUSH_TIMINGS).
    Regression: any un-instrumented substep is a diagnostic hole."""
    import main
    src = inspect.getsource(main._score_flush_loop)
    for substep in ("sync_scores_to_db", "upsert_entities",
                    "refresh_alias_sport_cache"):
        assert f"name={substep}" in src, (
            f"substep {substep!r} missing its timing log line — "
            f"diagnostic hole (can't attribute a timeout to it)"
        )
    # And the gate: the log-emit conditional must reference the
    # env-gated flag.
    assert "_LOG_SCORE_FLUSH_TIMINGS" in src, (
        "timing log emits not gated by _LOG_SCORE_FLUSH_TIMINGS — "
        "steady-state log stream would be polluted"
    )
    # And the logger name must be distinct for grep.
    assert '"score_flush_timings"' in src, (
        "timing logger name not 'score_flush_timings' — grep "
        "cardinality unbounded"
    )


def test_load_v4_linkage_rows_wall_time_instrumented():
    """The linkage-refresh loop's DB read MUST be time-bracketed so
    the operator can measure pool-contention severity after the
    stagger lands. Same env gate as score_flush timings."""
    import main
    src = inspect.getsource(main._v4_linkage_refresh_loop)
    assert "load_v4_linkage_rows" in src
    assert "monotonic()" in src, (
        "load_v4_linkage_rows call not wrapped in monotonic() — "
        "cannot measure linkage-read latency"
    )
    assert "_LOG_SCORE_FLUSH_TIMINGS" in src, (
        "linkage timing log not gated by _LOG_SCORE_FLUSH_TIMINGS "
        "— should share the score_flush diagnostic gate so operator "
        "flips one env and reads both signals in the same window"
    )
    # Log line goes to the same "score_flush_timings" logger for
    # correlated grep.
    assert '"score_flush_timings"' in src


# ── Phase-only-triggers-seed regression guard ─────────────────────

def test_seed_work_only_runs_when_phase_is_flush_plus_seed():
    """Regression guard: the entity-seed block (extract_teams +
    upsert_entities + refresh_alias_sport_cache) MUST be gated on
    `phase == "flush+seed"`. If the guard drifts, seed work fires
    every cycle (5× overhead) and the 120s ceiling explodes."""
    import main
    src = inspect.getsource(main._score_flush_loop)
    idx = src.find('if phase == "flush+seed":')
    assert idx > 0, (
        "entity-seed block no longer guarded by "
        "`if phase == \"flush+seed\"` — regression to per-cycle seed "
        "work"
    )
    # And extract_teams / upsert_entities appear AFTER the guard.
    idx_extract = src.find("extract_teams", idx)
    idx_upsert = src.find("upsert_entities", idx)
    assert 0 < idx < idx_extract
    assert 0 < idx < idx_upsert
