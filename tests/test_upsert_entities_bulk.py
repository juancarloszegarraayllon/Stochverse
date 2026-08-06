"""Tests for Task #43(b) — upsert_entities bulk rewrite +
survivor-bias-trap regression guard.

The #43 investigation proved that upsert_entities' per-row loop
(2 + N_aliases round-trips per team, ~3-3.5k round-trips at
flashlive peak) consistently blew past _score_flush_loop's 120s
ceiling. Every seed cycle timed out. Entity/alias seeding was
effectively DEAD for 3 days after PR #289 shipped the ceiling —
new teams first seen post-Aug-4 never made it into the entities /
entity_aliases tables.

Rewrite: 3 round-trips per chunk, not per team.
  1. INSERT ... ON CONFLICT DO NOTHING RETURNING id, canonical_name
     (batch VALUES, single execute → rows returned only for NEW).
  2. SELECT id, canonical_name WHERE canonical_name IN (missing)
     (fetches ids for pre-existing rows the RETURNING skipped).
  3. INSERT ... ON CONFLICT DO NOTHING (batch VALUES for all
     aliases with resolved entity_ids; SQLAlchemy dispatches as
     insertmanyvalues).
Target: <5s per seed cycle at ~500-team flashlive peak.

The pre-fix bracket log fired ONLY on completion — a hung
upsert_entities left ZERO evidence in the substep stream. That
survivor bias hid the bug for 3 days. This PR closes it by adding
a `status=started` log at entry + wrapping the completion log in
try/finally so `status=completed` fires even when the outer
pass_timeout cancels the await.
"""
from __future__ import annotations

import inspect
import re

import pytest


# ── Bulk-shape regression guards (source-inspected) ─────────────

def test_upsert_entities_uses_batch_values_not_per_row_loop():
    """Regression guard against a future refactor that reintroduces
    the per-row loop. The rewrite MUST call `pg_insert(Entity).values(
    ent_values)` on a LIST, not on kwargs — batch shape."""
    from db import upsert_entities
    src = inspect.getsource(upsert_entities)
    # Batch shape: values built as a list, then passed to pg_insert.
    assert "ent_values = [" in src, (
        "upsert_entities entity insert is not batched — regression "
        "to per-row shape reopens #43 (60-180s at flashlive peak)"
    )
    assert "pg_insert(Entity).values(ent_values)" in src, (
        "batch ent_values list not passed to pg_insert as-is; "
        "regression to kwargs shape means per-row semantics"
    )


def test_upsert_entities_uses_batch_values_for_aliases():
    """Same guard for the alias insert. Both MUST be batch."""
    from db import upsert_entities
    src = inspect.getsource(upsert_entities)
    assert "alias_values: list = []" in src or "alias_values = [" in src
    assert "pg_insert(EntityAlias).values(alias_values)" in src, (
        "alias insert not batched — regression to per-row shape "
        "reintroduces the N_aliases-per-team round-trip amplifier"
    )


def test_upsert_entities_deduplicates_alias_batch():
    """Because ON CONFLICT DO NOTHING resolves conflicts AFTER
    row parsing, sending duplicate (alias, source) rows in the SAME
    batch INSERT would violate the unique constraint BEFORE the
    conflict handler kicks in. The rewrite MUST dedupe within the
    batch to avoid this."""
    from db import upsert_entities
    src = inspect.getsource(upsert_entities)
    assert "seen_in_batch" in src, (
        "no intra-batch dedup — a chunk containing repeat (alias, "
        "source) pairs would fail with unique-constraint violation "
        "before ON CONFLICT can DO NOTHING them"
    )


def test_upsert_entities_uses_returning_for_new_inserts():
    """RETURNING id, canonical_name on the entity insert lets us
    count new_entities without an extra COUNT round-trip AND gives
    us ids for new rows without a SELECT. Regression removes this
    optimization AND breaks the new_entities counter."""
    from db import upsert_entities
    src = inspect.getsource(upsert_entities)
    assert ".returning(Entity.id, Entity.canonical_name)" in src, (
        "RETURNING clause missing from entity insert — new_entities "
        "counter breaks + extra round-trip on lookup"
    )


def test_upsert_entities_selects_only_missing_ids_not_all():
    """Post-RETURNING optimization: only SELECT for canonical_names
    NOT already in name_to_id (i.e., the conflicts). Regression to
    unconditional SELECT-all wastes a round-trip when most rows
    are new. Cheap correctness invariant."""
    from db import upsert_entities
    src = inspect.getsource(upsert_entities)
    assert "missing_names" in src, (
        "SELECT for pre-existing ids doesn't filter to missing_names "
        "— regression to redundant lookup for already-known rows"
    )


def test_upsert_entities_chunk_size_bounds_failure_blast_radius():
    """CHUNK_SIZE governs mid-chunk failure blast radius (rollback
    loses ≤CHUNK_SIZE teams, not the whole call). Keep bounded even
    though bulk shape allows much larger chunks."""
    from db import upsert_entities
    src = inspect.getsource(upsert_entities)
    assert "CHUNK_SIZE = 100" in src, (
        "CHUNK_SIZE drifted — regression could either make mid-chunk "
        "failures too costly (chunk too big) or lose the bulk speedup "
        "(chunk too small)"
    )


# ── Completion-bracket assertion (operator's explicit ask) ──────

def test_score_flush_pass_upsert_entities_bracket_has_started_and_completed():
    """LOAD-BEARING regression guard against the survivor-bias trap
    that hid #43 for 3 days. The upsert_entities timing bracket MUST
    log a `status=started` line BEFORE the await and a `status=
    completed` line inside a try/finally AFTER — so a hung call
    (e.g., cancelled by outer pass_timeout) still leaves a
    `status=started` line in the substep stream. A completion-only
    bracket masks hangs entirely."""
    import main
    src = inspect.getsource(main._score_flush_loop)

    # Locate the upsert_entities block in _score_flush_pass.
    idx_call = src.find("await upsert_entities(all_teams)")
    assert idx_call > 0, "upsert_entities call missing entirely"

    # Look backward for `status=started` (must appear BEFORE the
    # await); the block prefix is small so 1500 chars back is enough.
    before = src[max(0, idx_call - 1500):idx_call]
    assert "status=started" in before, (
        "no `status=started` log line BEFORE upsert_entities await — "
        "survivor bias hidden hang for 3 days; regression is a repeat "
        "of the #43 failure mode"
    )
    assert "name=upsert_entities" in before, (
        "the started line doesn't reference upsert_entities; grep "
        "attribution regressed"
    )

    # Look forward for `status=completed` inside a `try/finally`.
    after = src[idx_call:idx_call + 1500]
    assert "status=completed" in after, (
        "no `status=completed` log line AFTER upsert_entities await"
    )
    # Structural: the completed log MUST be inside a finally clause
    # so cancellation-mid-await still emits it.
    assert "finally:" in after, (
        "upsert_entities await not wrapped in try/finally — a "
        "cancellation-mid-await would swallow the completed log, "
        "reintroducing survivor bias"
    )


def test_score_flush_pass_all_three_substeps_use_started_completed_pattern():
    """Symmetric guard for sync_scores_to_db + upsert_entities +
    refresh_alias_sport_cache. All three MUST use the started+
    completed pattern; regression on any one hides its hang."""
    import main
    src = inspect.getsource(main._score_flush_loop)
    for substep in ("sync_scores_to_db", "upsert_entities",
                    "refresh_alias_sport_cache"):
        # Both status states must appear paired with this substep name.
        # Regex is lenient: any char (including newlines, quote
        # boundaries, whitespace) between `name=X` and the status
        # token — Python source splits log-line literals across
        # multiple lines with adjacent-string concatenation.
        started_re = re.compile(
            rf'name={substep}\b.{{0,300}}status=started',
            re.DOTALL,
        )
        completed_re = re.compile(
            rf'name={substep}\b.{{0,300}}status=completed',
            re.DOTALL,
        )
        assert started_re.search(src), (
            f"substep {substep!r} missing status=started bracket — "
            f"survivor-bias trap open on this substep"
        )
        assert completed_re.search(src), (
            f"substep {substep!r} missing status=completed bracket"
        )


def test_score_flush_pass_completed_logs_are_all_inside_try_finally():
    """The completed log for EACH substep MUST be inside a try/
    finally so cancellation-mid-await doesn't skip it. Count the
    finally: clauses in the _score_flush_pass region — expect >= 3
    (one per substep)."""
    import main
    src = inspect.getsource(main._score_flush_loop)
    # Isolate the _score_flush_pass region.
    idx_pass_def = src.find("async def _score_flush_pass(phase: str)")
    idx_holder = src.find("async with holder_heartbeat(", idx_pass_def)
    assert idx_pass_def > 0 and idx_holder > idx_pass_def
    pass_region = src[idx_pass_def:idx_holder]
    finally_count = pass_region.count("finally:")
    assert finally_count >= 3, (
        f"_score_flush_pass has {finally_count} `finally:` clauses; "
        f"expected >= 3 (one per substep: sync_scores_to_db, "
        f"upsert_entities, refresh_alias_sport_cache). Missing "
        f"finally = missing completion log on cancel = survivor bias."
    )


# ── Behavioral: bulk rewrite handles new + existing + empty ─────

@pytest.mark.asyncio
async def test_upsert_entities_returns_early_when_teams_empty():
    """Guard: empty input MUST short-circuit without touching the
    session (which the caller may not have set up)."""
    from db import upsert_entities
    # Should not raise even if session/engine are None (memory-only mode).
    await upsert_entities([])


@pytest.mark.asyncio
async def test_upsert_entities_skips_teams_without_canonical_name():
    """Guard: teams without a canonical_name are filtered upfront
    so all three round-trips see a consistent set. Passing a mix
    of valid + no-canon teams MUST NOT raise; only valid rows
    should be considered."""
    from db import upsert_entities
    # We can't easily test the DB path in this env; but the pre-loop
    # filter is deterministic — assert it via source shape.
    src = inspect.getsource(upsert_entities)
    assert 'valid = [t for t in chunk if t.get("canonical_name")]' in src, (
        "chunk-level valid filter missing — the SELECT/INSERT batches "
        "could disagree on which rows to include"
    )


def test_new_entities_and_new_aliases_counted_from_returning():
    """The new_entities counter derives from the entity INSERT's
    RETURNING result (rows only appear for actual inserts under ON
    CONFLICT DO NOTHING). new_aliases similarly. Regression to
    session.execute().rowcount is unreliable on ON CONFLICT DO
    NOTHING and would count wrong."""
    from db import upsert_entities
    src = inspect.getsource(upsert_entities)
    assert "chunk_entities = len(new_rows)" in src, (
        "new_entities counter no longer uses len(RETURNING rows)"
    )
    assert "chunk_aliases = len(al_result.all())" in src, (
        "new_aliases counter no longer uses len(RETURNING rows)"
    )
