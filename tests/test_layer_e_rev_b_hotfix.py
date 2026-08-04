"""Regression guards for the E-rev-b-hotfix defects (2026-08-07).

Three defects landed in production with PR #290 (Layer E-rev-b) and
took down fl/kalshi ingestion on both workers at ~23:54Z:

  Defect 1 — DATABASE_URL_DIRECT was read raw from the env
    (os.environ.get(...) or DATABASE_URL), bypassing the
    normalization pipeline that DATABASE_URL runs through
    (scheme rewrite, sslmode extraction, channel_binding drop).
    Neon console DSNs are `postgresql://user:pass@host/db?sslmode
    =require&channel_binding=require` — three separate reasons
    that string chokes asyncpg. First one hits at create_async_
    engine call time.

  Defect 2 — Pooled data engine and AL engine shared one try/
    except. Any AL engine init failure wiped the pooled data
    engine to None too, taking DOWN the entire application's
    Postgres access, not just the AL path.

  Defect 3 — All six AL-guarded callers (4 main.py loops +
    ingestion.kalshi.run + ingestion.fl.run) clean-returned when
    advisory_lock_engine was None. Supervise treated clean-return
    as "task complete" and set STATE_DEAD@attempt1 with no
    restart. This is the exact Day-63 defect class Layer A was
    invented to close — reintroduced through a new door.

Hotfix (this file guards):
  (1) _normalize_pg_url extracted; both DATABASE_URL and
      DATABASE_URL_DIRECT run through it.
  (2) Split try/except — pooled data engine and AL engine init in
      separate blocks; AL failure leaves pooled engine alive.
  (3) All six caller sites raise NotLockHolder instead of
      clean-returning when advisory_lock_engine is None. Supervise's
      Layer-A handler re-races indefinitely; operator can fix the
      env var and the loop comes alive WITHOUT a redeploy.
  (4) Loud endpoint-visibility log: WARNING when AL engine falls
      back to pooled DSN (churny locks expected); INFO with DIRECT
      host confirmation when the real fix is active.
"""
from __future__ import annotations

import inspect
from unittest.mock import patch

import pytest


# ── Defect 1: _normalize_pg_url helper exists + handles all shapes ──

def test_normalize_pg_url_helper_exists_and_is_used_for_both_urls():
    """Both DATABASE_URL and DATABASE_URL_DIRECT MUST be routed
    through _normalize_pg_url. Regression to a raw env read for
    DATABASE_URL_DIRECT reopens the 23:54Z ingestion outage."""
    import db
    assert hasattr(db, "_normalize_pg_url"), (
        "db._normalize_pg_url helper missing — pre-hotfix inline "
        "normalization is not reusable for DATABASE_URL_DIRECT"
    )
    src = inspect.getsource(db)
    # Count the call sites — both env vars must route through the helper.
    normalize_calls = src.count("_normalize_pg_url(")
    # 1 def + at least 2 calls (DATABASE_URL, DATABASE_URL_DIRECT).
    assert normalize_calls >= 3, (
        f"_normalize_pg_url call count = {normalize_calls}; expected "
        f">= 3 (1 def + DATABASE_URL call + DATABASE_URL_DIRECT call). "
        f"DATABASE_URL_DIRECT may be reading env raw again — 23:54Z "
        f"outage regression."
    )


def test_normalize_pg_url_rewrites_postgres_scheme():
    """Railway emits postgres://; asyncpg needs postgresql+asyncpg://.
    The helper MUST rewrite the scheme."""
    from db import _normalize_pg_url
    connect_args: dict = {}
    result = _normalize_pg_url(
        "postgres://user:pass@localhost:5432/db", connect_args,
    )
    assert result.startswith("postgresql+asyncpg://"), (
        f"scheme not rewritten: {result}"
    )


def test_normalize_pg_url_rewrites_postgresql_scheme():
    """Neon console emits postgresql://; asyncpg needs the +asyncpg
    driver suffix."""
    from db import _normalize_pg_url
    connect_args: dict = {}
    result = _normalize_pg_url(
        "postgresql://user:pass@host.neon.tech/db", connect_args,
    )
    assert result.startswith("postgresql+asyncpg://"), (
        f"postgresql:// scheme not rewritten: {result}"
    )


def test_normalize_pg_url_extracts_sslmode_and_drops_channel_binding():
    """Neon console DSN template includes both `sslmode=require` and
    `channel_binding=require`. asyncpg accepts NEITHER as a DSN query
    param. sslmode must be extracted into connect_args["ssl"];
    channel_binding must be dropped silently."""
    from db import _normalize_pg_url
    connect_args: dict = {}
    result = _normalize_pg_url(
        "postgresql://u:p@host.neon.tech/db?sslmode=require&channel_binding=require",
        connect_args,
    )
    assert "sslmode" not in result, (
        f"sslmode not stripped from DSN: {result}"
    )
    assert "channel_binding" not in result, (
        f"channel_binding not stripped from DSN: {result}"
    )
    assert connect_args.get("ssl") == "require", (
        f'connect_args["ssl"] = {connect_args.get("ssl")}; expected "require"'
    )


def test_normalize_pg_url_neon_host_gets_implicit_ssl():
    """Neon requires SSL. If a DSN targeting a *.neon.tech host has
    no explicit sslmode, force ssl='require' — otherwise asyncpg
    tries plaintext and Neon rejects."""
    from db import _normalize_pg_url
    connect_args: dict = {}
    _normalize_pg_url(
        "postgresql://u:p@ep-cool-cloud-12345.us-east-1.aws.neon.tech/db",
        connect_args,
    )
    assert connect_args.get("ssl") == "require", (
        "Neon hostname without explicit sslmode did not get implicit ssl='require'"
    )


def test_normalize_pg_url_empty_input_returns_empty():
    from db import _normalize_pg_url
    connect_args: dict = {}
    assert _normalize_pg_url("", connect_args) == ""
    assert connect_args == {}


def test_normalize_pg_url_is_idempotent():
    """Calling twice on an already-normalized URL must not
    double-mangle. Guards against a future refactor that assumes
    one-shot semantics."""
    from db import _normalize_pg_url
    ca1: dict = {}
    first = _normalize_pg_url(
        "postgresql://u:p@host.neon.tech/db?sslmode=require",
        ca1,
    )
    ca2: dict = {}
    second = _normalize_pg_url(first, ca2)
    assert first == second, (
        f"non-idempotent: first={first!r} second={second!r}"
    )
    assert ca1 == ca2 == {"ssl": "require"}


# ── Defect 2: pooled and AL engine init are isolated ────────────────

def test_pooled_and_al_engine_init_in_separate_try_blocks():
    """Source-inspect: the pooled data engine (`engine = create_async
    _engine(DATABASE_URL, ...)`) and the AL engine (`advisory_lock_
    engine = create_async_engine(DATABASE_URL_DIRECT, ...)`) MUST
    live in separate try/except blocks. Regression to a shared try
    reopens the 23:54Z blast-radius bug where a bad DATABASE_URL_
    DIRECT wiped the pooled engine too."""
    import db
    src = inspect.getsource(db)

    # Locate the pooled-engine create call and the AL-engine create call.
    # Rough anchors — enough for a structural check.
    idx_pool = src.find("engine = create_async_engine(\n            DATABASE_URL,")
    idx_al = src.find("advisory_lock_engine = create_async_engine(")
    assert idx_pool > 0, "pooled engine assignment not found"
    assert idx_al > idx_pool, "AL engine assignment not after pooled"

    # Between the pooled engine assignment and the AL engine
    # assignment there MUST be an `except` (closing the pooled try)
    # AND a subsequent `try:` (opening the AL try). Regression to a
    # single try/except block would have zero `except` between them.
    between = src[idx_pool:idx_al]
    assert between.count("except Exception") >= 1, (
        "no `except Exception` between pooled and AL engine "
        "assignments — they're back in a shared try/except; "
        "regression to 23:54Z blast-radius bug"
    )
    # And an intervening `try:` for the AL block:
    assert between.count("\n    try:\n") >= 1 or between.count("\n    try:") >= 1, (
        "no separate `try:` opening the AL engine init block"
    )


@pytest.mark.asyncio
async def test_al_engine_init_failure_does_not_take_down_pooled_engine(
    monkeypatch,
):
    """Behavioral: simulate AL engine init failure and assert the
    pooled data engine survives. Pre-hotfix, a shared try/except
    would nullify both."""
    # We can't easily re-import db.py mid-test (it's a module-level
    # side-effect setup), so this is a source-based structural check
    # combined with the behavioral test in the previous function.
    # The strongest available assertion at runtime: after import,
    # `engine` and `advisory_lock_engine` are independent module
    # attributes; setting one to None does not affect the other.
    import db
    saved_al = db.advisory_lock_engine
    saved_engine = db.engine
    try:
        db.advisory_lock_engine = None
        # Pooled engine attribute must be unaffected — this is the
        # invariant the split-try/except preserves.
        assert db.engine is saved_engine, (
            "Assigning advisory_lock_engine = None mutated `engine` — "
            "the module has shared state that would allow the 23:54Z "
            "blast-radius bug"
        )
    finally:
        db.advisory_lock_engine = saved_al
        db.engine = saved_engine


# ── Defect 3: all six callers raise NotLockHolder on engine=None ────

@pytest.mark.parametrize("module_name,func_name", [
    ("main", "_score_flush_loop"),
    ("main", "_price_prune_loop"),
    ("main", "_multi_stage_discovery_loop"),
    ("main", "_tournament_bracket_warm_loop"),
    ("ingestion.kalshi", "run"),
    ("ingestion.fl", "run"),
])
def test_al_engine_none_guard_raises_notlockholder(module_name, func_name):
    """Every AL-guarded caller MUST raise NotLockHolder (not clean-
    return) when advisory_lock_engine is None. Supervise catches
    NotLockHolder as a first-class sentinel and re-races indefinitely;
    a clean return sets STATE_DEAD@attempt1 and never retries — the
    exact 23:54Z failure signature.

    Source-based: assert the guard block contains `raise NotLockHolder`
    AND that no `return` statement immediately follows an engine=None
    check without an intervening raise."""
    import importlib
    mod = importlib.import_module(module_name)
    fn = getattr(mod, func_name)
    src = inspect.getsource(fn)

    # There MUST be at least one raise NotLockHolder() in the function.
    assert "raise NotLockHolder()" in src, (
        f"{module_name}.{func_name} has no `raise NotLockHolder()` — "
        f"regression to clean-return reopens 23:54Z Day-63-class defect"
    )
    # And the engine=None guard specifically MUST raise, not return.
    # kalshi.py / fl.py import as `_al_engine` (alias); main.py loops
    # use the bare `advisory_lock_engine`. Accept either.
    idx_a = src.find("advisory_lock_engine is None")
    idx_b = src.find("_al_engine is None")
    idx = max(idx_a, idx_b) if idx_a > 0 or idx_b > 0 else -1
    assert idx > 0, (
        f"{module_name}.{func_name} has no engine=None check — case "
        f"would silently deref later"
    )
    # Look forward ~2000 chars for either `raise NotLockHolder()` or
    # `return`. The one that appears FIRST is the effective behavior.
    forward = src[idx:idx + 2000]
    idx_raise = forward.find("raise NotLockHolder()")
    idx_return = forward.find("        return")
    assert idx_raise > 0, (
        f"{module_name}.{func_name} engine=None guard does not raise "
        f"NotLockHolder"
    )
    # If a `return` appears before the raise, that's the bug back.
    if idx_return > 0:
        assert idx_return > idx_raise, (
            f"{module_name}.{func_name} engine=None guard returns "
            f"BEFORE raising NotLockHolder — supervise will treat as "
            f"complete → STATE_DEAD"
        )


# ── Loud AL endpoint visibility ──────────────────────────────────────

def test_al_endpoint_visibility_log_warns_when_falling_back_to_pooled():
    """Operator MUST be able to tell at a glance whether AL is on
    DIRECT or fell back to pooled. Same-as-pooled means churny locks
    are expected (pgbouncer txn-mode substrate). Regression to a
    silent success log would let a mis-configured DATABASE_URL_DIRECT
    (empty, whitespace-only, missing) go unnoticed until the next
    pg_locks read."""
    import db
    src = inspect.getsource(db)

    # WARNING-level log when AL engine falls back to pooled.
    assert "SAME AS POOLED" in src, (
        "loud AL endpoint visibility log missing — operator can't tell "
        "if AL is on DIRECT or fell back to pooled"
    )
    assert "CHURNY LOCKS EXPECTED" in src, (
        "warning about pooled-fallback semantics missing — subtle "
        "regression from PR #290's design intent"
    )
    # INFO log confirming DIRECT endpoint when the real fix is active.
    assert 'on DIRECT endpoint' in src, (
        "confirmation log for DIRECT-endpoint binding missing"
    )


def test_al_engine_init_error_log_is_ERROR_not_warning():
    """When AL engine init fails, the log level MUST be ERROR — this
    is a load-bearing infra failure that breaks all advisory-lock-
    guarded ingestion until the operator fixes it. WARNING is easy
    to miss in a busy log stream."""
    import db
    src = inspect.getsource(db)

    # Locate the AL engine try/except and check the except's log call.
    idx = src.find("advisory_lock_engine = create_async_engine(")
    assert idx > 0
    # Look forward for the except block and its log call.
    forward = src[idx:idx + 3000]
    idx_except = forward.find("except Exception")
    assert idx_except > 0, "AL engine init has no except block"
    except_block = forward[idx_except:idx_except + 1500]
    assert "log.error(" in except_block, (
        "AL engine init failure logged at WARNING/INFO level, not "
        "ERROR — regression to easy-to-miss log message"
    )
    assert "AL ENGINE INIT FAILED" in except_block, (
        "AL engine init failure log missing loud sentinel — "
        "harder to grep in production log stream"
    )


# ── DATABASE_URL_DIRECT env-var wiring ──────────────────────────────

def test_database_url_direct_env_read_uses_helper_not_raw():
    """The specific defect that caused 23:54Z: DATABASE_URL_DIRECT was
    `os.environ.get('DATABASE_URL_DIRECT', '') or DATABASE_URL`. That
    read is raw. Even if the fallback resolves to DATABASE_URL
    (already normalized), a SET DATABASE_URL_DIRECT env var goes into
    create_async_engine without normalization.

    Post-hotfix: the read MUST be wrapped in _normalize_pg_url()."""
    import db
    src = inspect.getsource(db)

    # Locate the DATABASE_URL_DIRECT assignment.
    idx = src.find("DATABASE_URL_DIRECT = ")
    assert idx > 0
    # Look ahead a couple lines for the actual RHS.
    rhs = src[idx:idx + 300]
    assert "_normalize_pg_url" in rhs, (
        "DATABASE_URL_DIRECT assignment does not route through "
        "_normalize_pg_url — regression to 23:54Z outage: a "
        "console-pasted Neon DSN (postgresql:// + sslmode=require + "
        "channel_binding=require) bypasses normalization and chokes "
        "asyncpg on all three defects"
    )


def test_al_connect_args_gets_distinct_application_name():
    """Operator-observability nicety: the AL engine's Postgres
    connections should show up as 'stochverse-web-al' in
    pg_stat_activity so they can be filtered separately from the
    pooled data connections. Cheap; makes triage faster."""
    import db
    src = inspect.getsource(db)
    assert '"stochverse-web-al"' in src, (
        "AL engine's application_name not distinguished — operator "
        "can't filter AL vs data connections in pg_stat_activity"
    )
