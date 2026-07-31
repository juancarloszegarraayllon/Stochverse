"""Sanity guard for the SP project's Postgres advisory-lock keys.

Two loops sharing a key = one silently permanently skipped: the
second loop's `pg_try_advisory_lock(same_key)` returns True (it's
the same session that already holds the lock — no, WRONG: different
sessions, so it returns False), the loop logs "skipping" and never
runs. Under WEB_CONCURRENCY=2 the same-key collision silently halves
the collided loops' throughput and nobody notices because the
skipping-log line is expected for the non-holder worker.

Cheap uniqueness assertion catches a copy-paste at PR review time
instead of at 03:00 UTC when a report goes empty.

Also guards the reserved-range convention: every key must sit in the
`0x5350_F1XX` band we chose for this project so a stray unrelated
advisory-lock call inside SQLAlchemy or a migration tool doesn't
collide with an ingestion key.
"""
from __future__ import annotations


def test_advisory_lock_keys_are_distinct():
    """All exported advisory-lock keys must be unique — a duplicate
    means two singletons would race on the same key and one loop
    would silently never run."""
    from ingestion import base as _base
    keys = {
        name: getattr(_base, name)
        for name in dir(_base)
        if name.startswith("ADVISORY_LOCK_")
    }
    assert keys, "No ADVISORY_LOCK_* constants exported from ingestion.base"
    # Invert: value → [names]. Any bucket with >1 name is a collision.
    inverse: dict = {}
    for name, val in keys.items():
        inverse.setdefault(val, []).append(name)
    dupes = {v: n for v, n in inverse.items() if len(n) > 1}
    assert not dupes, (
        f"Duplicate advisory-lock keys: {dupes}. Pick a fresh value "
        f"in the SP\\xF1\\x00 band (0x5350_F1XX) for the new entry."
    )


def test_advisory_lock_keys_are_in_project_range():
    """Every ADVISORY_LOCK_* must sit in the reserved 0x5350_F1XX
    band. Values outside the band risk colliding with advisory-lock
    calls issued by other tooling (SQLAlchemy migrations, extension
    installers) that we don't control.

    Range: 0x5350_F100 through 0x5350_F1FF inclusive (256 slots,
    ample runway for phase-2/3 additions)."""
    from ingestion import base as _base
    RANGE_LO = 0x5350_F100
    RANGE_HI = 0x5350_F1FF
    out_of_range: dict = {}
    for name in dir(_base):
        if not name.startswith("ADVISORY_LOCK_"):
            continue
        val = getattr(_base, name)
        if not isinstance(val, int) or not (RANGE_LO <= val <= RANGE_HI):
            out_of_range[name] = hex(val) if isinstance(val, int) else val
    assert not out_of_range, (
        f"Advisory-lock keys outside the SP\\xF1\\x00 band "
        f"[{hex(RANGE_LO)}, {hex(RANGE_HI)}]: {out_of_range}. "
        f"Pick a value in-range so a stray external advisory-lock "
        f"call can't collide with an ingestion key."
    )


def test_surface_c_constants_present():
    """Task #21 Surface C added two new keys. If they get renamed or
    removed, the two loops fall back to unlocked (both workers run),
    reintroducing the ~50% duplication regression. Explicit presence
    test so a rename shows up here, not silently in production."""
    from ingestion.base import (
        ADVISORY_LOCK_SCORE_FLUSH,
        ADVISORY_LOCK_PRICE_PRUNE,
    )
    assert isinstance(ADVISORY_LOCK_SCORE_FLUSH, int)
    assert isinstance(ADVISORY_LOCK_PRICE_PRUNE, int)
    assert ADVISORY_LOCK_SCORE_FLUSH != ADVISORY_LOCK_PRICE_PRUNE


def test_standings_walk_constants_present():
    """Day-62 standings-walk PR added two new keys. Same regression
    guard as Surface C — rename or removal reintroduces both-worker
    duplication on ~215k/day of standings calls."""
    from ingestion.base import (
        ADVISORY_LOCK_BRACKET_WALK,
        ADVISORY_LOCK_MULTI_STAGE_DISC,
    )
    assert isinstance(ADVISORY_LOCK_BRACKET_WALK, int)
    assert isinstance(ADVISORY_LOCK_MULTI_STAGE_DISC, int)
    assert ADVISORY_LOCK_BRACKET_WALK != ADVISORY_LOCK_MULTI_STAGE_DISC
    # And distinct from the pre-existing pair, guaranteed by the
    # test_advisory_lock_keys_are_distinct sweep above but stated
    # explicitly here so a renamed constant fails BOTH tests.
    from ingestion.base import (
        ADVISORY_LOCK_SCORE_FLUSH,
        ADVISORY_LOCK_PRICE_PRUNE,
    )
    assert ADVISORY_LOCK_BRACKET_WALK not in (
        ADVISORY_LOCK_SCORE_FLUSH, ADVISORY_LOCK_PRICE_PRUNE,
    )
    assert ADVISORY_LOCK_MULTI_STAGE_DISC not in (
        ADVISORY_LOCK_SCORE_FLUSH, ADVISORY_LOCK_PRICE_PRUNE,
    )
