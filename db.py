"""Database connection and session factory.

Reads DATABASE_URL from the environment (auto-injected by Railway
when the PostgreSQL plugin is linked to the service). Also works
with external managed providers like Neon, Supabase, etc. Falls
back gracefully: if DATABASE_URL is missing, the app runs in pure
in-memory mode exactly as before.
"""
import os
import logging
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

log = logging.getLogger("db")

# asyncpg doesn't understand libpq-style query params like
# `sslmode=require` or `channel_binding=require` that Neon / Supabase
# append to their connection strings by default. Extract them from
# the URL and translate into a connect_args dict that asyncpg can
# consume.
_connect_args: dict = {}


def _normalize_pg_url(raw_url: str, connect_args: dict) -> str:
    """Turn a Neon/Supabase/Railway console-pasted Postgres URL into
    something asyncpg can actually consume, and mutate `connect_args`
    with the corresponding kwargs.

    E-rev-b-hotfix (2026-08-07): extracted from the original inline
    block that ran ONLY for DATABASE_URL. DATABASE_URL_DIRECT was
    added by E-rev-b for the AL engine's direct-endpoint binding but
    was read raw from the env (`os.environ.get(...) or DATABASE_URL`)
    — bypassing THIS normalization. Consequence: a console-pasted
    Neon direct DSN (`postgresql://…?sslmode=require&channel_binding=
    require`) tripped every failure gate — wrong scheme (needs the
    +asyncpg suffix), unknown query params (asyncpg rejects sslmode
    in the DSN and doesn't know channel_binding), sslmode=require
    not surfaced into connect_args["ssl"]. Result at 23:54Z was AL
    engine init failure inside the shared try/except (see below),
    which then wiped the pooled data engine too → ingestion dark on
    both workers → 4-read-verification failure.

    Contract: same string in → same string out, but scheme rewritten
    to postgresql+asyncpg:// and libpq-style query params extracted
    into connect_args. Neon/Supabase hostnames get ssl="require" as
    an implicit default when no sslmode is provided. Empty raw_url
    returns "".

    Idempotent: safe to call on an already-normalized URL (scheme
    check is prefix-based; already-stripped query params are absent
    from qs so the pops are no-ops)."""
    if not raw_url:
        return ""
    # Normalize scheme — Railway emits postgres://, asyncpg needs
    # postgresql+asyncpg://. Managed providers usually emit
    # postgresql:// which we also rewrite.
    if raw_url.startswith("postgres://"):
        raw_url = raw_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif raw_url.startswith("postgresql://"):
        raw_url = raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    try:
        parsed = urlparse(raw_url)
        qs = dict(parse_qsl(parsed.query))
        # sslmode: asyncpg accepts ssl="require" / "allow" /
        # "disable" etc. via its own "ssl" connect kwarg. Translate
        # from libpq-style names.
        sslmode = qs.pop("sslmode", None)
        if sslmode:
            connect_args["ssl"] = sslmode if sslmode != "disable" else False
        # channel_binding: libpq-only param, asyncpg ignores it.
        qs.pop("channel_binding", None)
        # If this is a Neon / Supabase hostname with no explicit
        # sslmode, force SSL on — both providers require it.
        if "ssl" not in connect_args and parsed.hostname and any(
            marker in parsed.hostname
            for marker in ("neon.tech", "supabase.co", "supabase.com")
        ):
            connect_args["ssl"] = "require"
        new_query = urlencode(qs)
        return urlunparse(parsed._replace(query=new_query))
    except Exception as e:
        log.warning("failed to parse Postgres URL (using raw): %s", e)
        return raw_url


DATABASE_URL = _normalize_pg_url(
    os.environ.get("DATABASE_URL", ""), _connect_args,
)

# ── Server-side defenses against transaction / connection leaks ──
#
# Postgres GUC parameters set per-connection via asyncpg's
# server_settings. Defends production against the class of bugs
# where application code holds a transaction open indefinitely
# (cancellation, asyncpg state issues, slow loops inside
# session.begin() blocks). Postgres self-kills offenders rather
# than letting them stall the connection pool.
#
# These complement (don't replace) fixing the underlying loops to
# chunk into shorter transactions. Even after refactor, these
# remain as the floor that keeps a single bug from cascading into
# pool exhaustion.
_server_settings = _connect_args.setdefault("server_settings", {})
_server_settings.setdefault("idle_in_transaction_session_timeout", "60000")  # 60s — kills BEGIN-but-no-activity
_server_settings.setdefault("statement_timeout", "60000")                    # 60s — kills hung individual queries
_server_settings.setdefault("lock_timeout", "30000")                         # 30s — kills waits for advisory/row locks
_server_settings.setdefault("application_name", "stochverse-web")            # surfaces in pg_stat_activity for triage

# Layer D (2026-08-03): CLIENT-side per-command ceiling. The server-
# side statement_timeout/lock_timeout/idle_in_transaction_session_timeout
# floors above kill runaway QUERIES, but they require a live connection
# and the server actually executing. On a dead TCP socket (Neon compute
# autoscale-to-zero + TCP-idle-timeout by an intermediate proxy is a
# documented scenario), asyncpg's socket read blocks forever waiting
# for bytes that never come. command_timeout is asyncpg's CLIENT-side
# floor — cancels the query after N seconds regardless of whether the
# server is reachable. Set LARGER than server statement_timeout so
# server-side kills happen first (clean error path); client-side is
# the last-resort ceiling for dead-socket cases. 2026-08-01 Kalshi
# in-pass hang was the failure mode this defends against.
_connect_args.setdefault("command_timeout", 90.0)

engine = None
async_session = None

# Layer E-rev-b (2026-08-06): AL engine on the DIRECT Neon endpoint.
#
# Rev-a (PR #289) misdiagnosed. Master root cause: DATABASE_URL points
# at Neon's pgbouncer POOLED endpoint (p11-pooler.c-3.us-west-2.aws.
# neon.tech, transaction-mode). Session-level advisory locks
# (pg_try_advisory_lock) are FUNDAMENTALLY INCOMPATIBLE with pgbouncer
# transaction-mode pooling: after each txn boundary, pgbouncer can
# rebind the app-side connection to a DIFFERENT physical backend. The
# lock lives on the backend that acquired it; the next statement on
# our "same connection" may hit a different backend that has no lock.
#
# Rev-a additionally used SQLAlchemy AsyncSession.commit(), which
# returns the DBAPI connection to the pool. Under NullPool that
# closes the connection instantly → session-level lock released at
# acquisition time → every acquire was a no-op → the "restored
# holder/non_holder split" was actually workers churning through
# ephemeral acquires.
#
# Fix (two parts):
#   1. DATABASE_URL_DIRECT — separate env var pointing at Neon's
#      DIRECT endpoint (strip `-pooler` from the host). Data engine
#      stays on pooled DATABASE_URL for throughput; ONLY the AL
#      engine uses direct. Falls back to DATABASE_URL if unset (dev
#      environments, single-endpoint providers).
#   2. Pinned AsyncConnection instead of AsyncSession — see
#      ingestion.base.acquire_lock_connection_bounded. AsyncConnection
#      holds the underlying DBAPI connection for its explicit CM
#      lifetime; commit()/rollback() end transactions but do NOT
#      release the connection.
#
# NullPool retained on the AL engine as belt-and-braces: with direct
# endpoint + pinned AsyncConnection, we don't need pooling for AL
# (one persistent connection per holder task); NullPool ensures no
# pool-side reuse can accidentally hand our AL connection to another
# caller.
# E-rev-b-hotfix (2026-08-07): DATABASE_URL_DIRECT is now
# NORMALIZED through the same helper as DATABASE_URL. Pre-hotfix,
# a raw env read shipped console-pasted DSNs (postgresql://,
# sslmode=require, channel_binding=require) straight into
# create_async_engine, which chokes on any of the three defects.
# 23:54Z evidence: AL engine init failed inside the shared try/
# except, taking down the pooled data engine too → ingestion dark.
#
# The AL engine gets its OWN _al_connect_args dict so if DIRECT
# has different SSL semantics from DATABASE_URL (unlikely but
# possible), they don't cross-contaminate.
_al_connect_args: dict = {}
DATABASE_URL_DIRECT = _normalize_pg_url(
    os.environ.get("DATABASE_URL_DIRECT", "") or os.environ.get("DATABASE_URL", ""),
    _al_connect_args,
)
# Copy scalar connect_args from the pooled dict (command_timeout,
# etc.); skip `ssl` (per-DSN via _al_normalize) and `server_settings`
# (must be deep-copied so the AL's application_name override doesn't
# mutate the pooled dict — subtle bug caught by test_application_name_set).
for _k, _v in _connect_args.items():
    if _k in ("ssl", "server_settings"):
        continue
    _al_connect_args.setdefault(_k, _v)
# Deep-copy server_settings so we can override application_name
# without mutating the pooled dict.
_al_ss: dict = dict(_connect_args.get("server_settings", {}))
_al_ss["application_name"] = "stochverse-web-al"
_al_connect_args["server_settings"] = _al_ss

engine = None
async_session = None

advisory_lock_engine = None
# Retained as None for import-compat during the E-rev-b transition;
# do NOT use — see ingestion.base.acquire_lock_connection_bounded.
# Kept until every caller has migrated to the pinned-connection API.
advisory_lock_session = None

# E-rev-b-hotfix: pooled data engine and AL engine now init in
# SEPARATE try/except blocks. Pre-hotfix a shared try wiped the
# pooled engine to None whenever the AL engine's init failed — the
# whole database went dark for a mis-typed DATABASE_URL_DIRECT env
# var. Post-hotfix, an AL failure leaves the pooled engine alive
# and the AL callers raise NotLockHolder (see
# acquire_lock_connection_bounded's engine=None guard) so supervise
# re-races instead of dying.
if DATABASE_URL:
    try:
        from sqlalchemy.ext.asyncio import (
            create_async_engine,
            async_sessionmaker,
        )
        # Keep pool small so we stay well under providers' connection
        # limits (Railway: 20, Neon free: 100, Supabase free: 60).
        engine = create_async_engine(
            DATABASE_URL,
            pool_size=5,
            max_overflow=2,
            # pool_pre_ping: issues a cheap SELECT 1 before handing
            # out a pooled connection. Catches stale/dead sockets
            # without a full exception round-trip.
            pool_pre_ping=True,
            # pool_recycle: force-recycle connections older than N
            # seconds. Defends against provider restarts that
            # invalidate every connection in the pool.
            pool_recycle=300,
            # Layer D (2026-08-03): explicit pool_timeout — was
            # relying on SQLAlchemy's implicit 30s default. Named
            # here so any tuning is source-visible; complements
            # asyncpg's command_timeout (per-command ceiling) and
            # ingestion's task-level asyncio.wait_for wraps.
            pool_timeout=30.0,
            connect_args=_connect_args,
        )
        async_session = async_sessionmaker(engine, expire_on_commit=False)
        log.info(
            "pooled data engine created (pool_size=5, pool_recycle=300; "
            "ssl=%s)",
            _connect_args.get("ssl", "default"),
        )
    except Exception as e:
        log.error(
            "POOLED DATA ENGINE INIT FAILED — application will run in "
            "memory-only mode: %s", e,
        )
        engine = None
        async_session = None
else:
    log.info("DATABASE_URL not set — running in memory-only mode")

# AL engine has its OWN try/except — independent of the pooled data
# engine. Failure here does NOT touch `engine` / `async_session`;
# callers (acquire_lock_connection_bounded) see advisory_lock_engine
# is None and raise NotLockHolder → supervise re-races. Once the
# operator fixes DATABASE_URL_DIRECT, the retry succeeds without a
# redeploy.
if DATABASE_URL_DIRECT:
    try:
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool
        # Layer E-rev-b: AL engine on DIRECT endpoint (bypass pgbouncer
        # txn-mode multiplexing) + NullPool. Callers acquire via
        # advisory_lock_engine.connect() (pinned AsyncConnection).
        # advisory_lock_session sessionmaker intentionally NOT created —
        # session.commit releases the DBAPI connection under NullPool,
        # breaking the lock (rev-a defect).
        advisory_lock_engine = create_async_engine(
            DATABASE_URL_DIRECT,
            poolclass=NullPool,
            connect_args=_al_connect_args,
        )
        # LOUD endpoint visibility (E-rev-b-hotfix): operator MUST be
        # able to tell at a glance whether AL is on the DIRECT
        # endpoint or fell back to pooled. Same-as-pooled means
        # session-level advisory locks are on pgbouncer transaction-
        # mode substrate — CHURNY LOCKS EXPECTED (see Day-65 evidence
        # in PR-Layer-E-rev-b commit message). DIRECT means the real
        # fix is active.
        try:
            _al_host = urlparse(DATABASE_URL_DIRECT).hostname or "unknown"
        except Exception:
            _al_host = "unparseable"
        if DATABASE_URL_DIRECT == DATABASE_URL:
            log.warning(
                "advisory_lock_engine SAME AS POOLED (host=%s) — "
                "session-level advisory locks are on pgbouncer "
                "transaction-mode substrate; CHURNY LOCKS EXPECTED. "
                "Set DATABASE_URL_DIRECT to Neon's DIRECT endpoint "
                "(strip `-pooler` from host) for real single-holder "
                "semantics.",
                _al_host,
            )
        else:
            log.info(
                "advisory_lock_engine on DIRECT endpoint (host=%s; "
                "NullPool; ssl=%s) — single-holder semantics active",
                _al_host, _al_connect_args.get("ssl", "default"),
            )
    except Exception as e:
        log.error(
            "AL ENGINE INIT FAILED — advisory-lock-guarded ingestion "
            "loops (score_flush, price_prune, bracket_walk, "
            "multi_stage_disc, fl, kalshi) will raise NotLockHolder "
            "and supervise will re-race indefinitely until this is "
            "fixed. Check DATABASE_URL_DIRECT format (must be a valid "
            "Postgres URL; console-paste form accepted). Error: %s",
            e,
        )
        advisory_lock_engine = None


async def init_db():
    """Create all tables if they don't exist. Called once on startup.
    Safe to call repeatedly — uses CREATE TABLE IF NOT EXISTS."""
    if engine is None:
        return
    try:
        from models import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        log.info("database tables ensured")
    except Exception as e:
        log.error("failed to initialize database tables: %s", e)


async def sync_events_to_db(records):
    """Upsert Kalshi events and their markets into the database.

    Called after each get_data() cache rebuild (~every 30 min) from a
    background thread via asyncio.run(...). Creates its own engine
    because the caller's event loop is distinct from the main
    FastAPI loop — can't share the main loop's async_session.

    Uses PostgreSQL ON CONFLICT … DO UPDATE for idempotent upserts.

    Transaction strategy: process records in fixed-size chunks. Each
    chunk = one short-lived transaction. The original implementation
    wrapped the entire loop (5000+ events × ~10 outcomes each) in a
    single session.begin() block, holding one transaction open for
    30+ minutes against Neon. Production transaction-leak incident
    on 2026-05-08 traced to that pattern.

    Engine lifecycle: try/finally ensures _engine.dispose() ALWAYS
    runs, including on cancellation paths the previous version
    missed. Without this, a cancelled call leaks 2 pool connections
    until process exit.
    """
    if not DATABASE_URL:
        return

    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    # CHUNK_SIZE governs per-transaction scope. ~50 events × ~10
    # outcomes each = ~500 round-trips per chunk = ~30s @ 60ms RT.
    # Stays well under the engine-level statement_timeout (60s) and
    # leaves headroom for occasional latency spikes.
    CHUNK_SIZE = 50

    _engine = None
    try:
        _engine = create_async_engine(
            DATABASE_URL,
            pool_size=2,
            connect_args=_connect_args,  # inherits server_settings timeouts
        )
        _session = async_sessionmaker(_engine, expire_on_commit=False)
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from sqlalchemy import select
        from models import Event, Market
        from datetime import datetime, timezone as tz

        def _parse_dt(val):
            if not val:
                return None
            try:
                from datetime import datetime as _dt
                return _dt.fromisoformat(val)
            except Exception:
                return None

        ev_count = 0
        mk_count = 0
        chunks_committed = 0
        chunks_failed = 0

        for chunk_start in range(0, len(records), CHUNK_SIZE):
            chunk = records[chunk_start:chunk_start + CHUNK_SIZE]
            chunk_ev = 0
            chunk_mk = 0
            try:
                async with _session() as session:
                    async with session.begin():
                        for r in chunk:
                            et = r.get("event_ticker", "")
                            if not et:
                                continue

                            # Upsert event
                            ev_vals = {
                                "platform": "kalshi",
                                "event_ticker": et,
                                "series_ticker": r.get("series_ticker"),
                                "title": r.get("title", "")[:500],
                                "category": r.get("category"),
                                "sport": r.get("_sport") or None,
                                "subcat": r.get("_subcat") or None,
                                "kickoff_dt": _parse_dt(r.get("_kickoff_dt")),
                                "exp_dt": _parse_dt(r.get("_exp_dt")),
                                "close_dt": _parse_dt(r.get("_close_dt")),
                                "is_live": bool(r.get("_is_live")),
                                "updated_at": datetime.now(tz.utc),
                            }
                            stmt = pg_insert(Event).values(**ev_vals)
                            stmt = stmt.on_conflict_do_update(
                                constraint="uq_platform_event",
                                set_={
                                    "title": stmt.excluded.title,
                                    "category": stmt.excluded.category,
                                    "sport": stmt.excluded.sport,
                                    "subcat": stmt.excluded.subcat,
                                    "kickoff_dt": stmt.excluded.kickoff_dt,
                                    "exp_dt": stmt.excluded.exp_dt,
                                    "close_dt": stmt.excluded.close_dt,
                                    "is_live": stmt.excluded.is_live,
                                    "updated_at": stmt.excluded.updated_at,
                                },
                            )
                            await session.execute(stmt)
                            chunk_ev += 1

                            # Upsert markets (outcomes)
                            for o in r.get("outcomes", []):
                                mk_ticker = o.get("ticker", "")
                                if not mk_ticker:
                                    continue
                                # Use a subquery to get the event_id from
                                # the just-upserted event row. Avoids a
                                # separate SELECT round-trip.
                                subq = select(Event.id).where(
                                    Event.platform == "kalshi",
                                    Event.event_ticker == et,
                                ).scalar_subquery()
                                mk_stmt = pg_insert(Market).values(
                                    ticker=mk_ticker,
                                    label=o.get("label", "")[:200],
                                    event_id=subq,
                                )
                                mk_stmt = mk_stmt.on_conflict_do_update(
                                    index_elements=["ticker"],
                                    set_={
                                        "label": mk_stmt.excluded.label,
                                        "event_id": mk_stmt.excluded.event_id,
                                    },
                                )
                                await session.execute(mk_stmt)
                                chunk_mk += 1
                # Chunk committed via session.begin() __aexit__.
                ev_count += chunk_ev
                mk_count += chunk_mk
                chunks_committed += 1
            except Exception as e:
                # Per-chunk failure does NOT abort the whole call.
                # The chunk's writes have rolled back; subsequent
                # chunks proceed.
                chunks_failed += 1
                log.warning(
                    "sync_events_to_db: chunk %d failed (%d records), continuing: %s",
                    chunk_start // CHUNK_SIZE, len(chunk), e,
                )

        log.info(
            "db sync: %d events, %d markets upserted "
            "(%d chunks ok, %d chunks failed)",
            ev_count, mk_count, chunks_committed, chunks_failed,
        )
    except Exception as e:
        # Outermost catch — fires only if engine creation or imports
        # failed. Per-chunk errors are caught above.
        log.error("db sync setup failed: %s", e)
    finally:
        # ALWAYS dispose the engine, including on cancellation paths.
        # The previous implementation only disposed on the success
        # path (and an inner-try-except in the failure path that
        # itself could fail) — leaked 2 pool connections per cancelled
        # call until process exit.
        if _engine is not None:
            try:
                await _engine.dispose()
            except Exception as e:
                log.warning("sync_events_to_db: engine.dispose() failed: %s", e)


async def sync_scores_to_db(source, games):
    """Replace all game_scores rows for a given source with the current
    live snapshot.  Called after each feed poll cycle (~10-30s).

    Each game dict should have: sport, league, home_display, away_display,
    home_score, away_score, state, period, display_clock, short_detail,
    clock_running, scheduled_kickoff_ms.
    """
    if not DATABASE_URL or async_session is None:
        return
    try:
        from models import GameScore
        from sqlalchemy import delete
        from datetime import datetime, timezone as tz

        async with async_session() as session:
            async with session.begin():
                # Wipe previous snapshot for this source
                await session.execute(
                    delete(GameScore).where(GameScore.source == source)
                )
                if games:
                    session.add_all([
                        GameScore(
                            source=source,
                            sport=g.get("sport", ""),
                            league=g.get("league"),
                            home_name=g.get("home_display") or g.get("home_name"),
                            away_name=g.get("away_display") or g.get("away_name"),
                            home_score=str(g.get("home_score", "")) if g.get("home_score") is not None else None,
                            away_score=str(g.get("away_score", "")) if g.get("away_score") is not None else None,
                            state=g.get("state"),
                            period=g.get("period"),
                            display_clock=g.get("display_clock"),
                            detail=g.get("short_detail"),
                            clock_running=bool(g.get("clock_running")),
                            scheduled_kickoff_ms=g.get("scheduled_kickoff_ms"),
                            captured_at=datetime.now(tz.utc),
                        )
                        for g in games
                    ])
        log.info("score sync [%s]: %d games written", source, len(games))
    except Exception as e:
        log.error("score sync [%s] failed: %s", source, e)


async def batch_insert_prices(rows):
    """Insert a batch of price snapshots into the prices table.

    Called every ~10s by the WebSocket flush task with accumulated
    updates. Uses a dedicated engine (same pattern as sync_events)
    since this runs in the main asyncio loop alongside the WS client.

    Each row is a dict with: market_ticker, yes_bid, yes_ask,
    no_bid, no_ask, last_price, source ('ws').

    Retries once on transient connection errors (Neon cold-start,
    stale pool, connection reset) by disposing the engine pool and
    reattempting. Tracks consecutive failures so the WS flush loop
    can surface the health via /api/ws_status.
    """
    if not DATABASE_URL or not rows:
        return
    global _flush_health
    for attempt in range(2):
        try:
            from models import Price
            async with async_session() as session:
                async with session.begin():
                    session.add_all([
                        Price(
                            market_ticker=r.get("market_ticker", ""),
                            yes_bid=r.get("yes_bid"),
                            yes_ask=r.get("yes_ask"),
                            no_bid=r.get("no_bid"),
                            no_ask=r.get("no_ask"),
                            last_price=r.get("last_price"),
                            volume=r.get("volume"),
                            open_interest=r.get("open_interest"),
                            source="ws",
                        )
                        for r in rows
                    ])
            log.info("price flush: %d rows inserted", len(rows))
            _flush_health["last_ok"] = __import__("time").time()
            _flush_health["consecutive_errors"] = 0
            _flush_health["last_error"] = None
            return
        except Exception as e:
            _flush_health["consecutive_errors"] = _flush_health.get("consecutive_errors", 0) + 1
            _flush_health["last_error"] = f"{type(e).__name__}: {e}"
            _flush_health["last_error_ts"] = __import__("time").time()
            log.error("price flush failed (attempt %d): %s", attempt + 1, e)
            if attempt == 0:
                # Dispose pool so next attempt gets fresh connections.
                try:
                    if engine is not None:
                        await engine.dispose()
                except Exception:
                    pass
                import asyncio as _a
                await _a.sleep(0.5)

# Tracks flush pipeline health — exposed via /api/ws_status so we
# can diagnose "WS is alive but writes aren't landing" scenarios
# without having to check Railway logs.
_flush_health: dict = {
    "last_ok": None,
    "consecutive_errors": 0,
    "last_error": None,
    "last_error_ts": None,
}


# ── Price data retention ─────────────────────────────────────────
# Neon free tier = 512 MB. At ~46 MB/hr of tick data, we can keep
# ~10 hours before hitting the wall. Default retention of 6 hours
# provides a safe margin and covers the 1H + 6H chart timeframes.
# The 24H and 7D tabs show partial data — acceptable for free tier.
# Upgrade to Neon Launch ($19/mo, 10 GB) for 7+ days of history.
PRICE_RETENTION_HOURS = int(os.environ.get("PRICE_RETENTION_HOURS", "6"))


_snapshots_table_ensured = False


async def _ensure_snapshots_table():
    """Idempotently create the snapshots table via raw DDL. Self-heals
    when a container started up before models.py was updated to include
    Snapshot, so init_db() ran without it. Uses CREATE TABLE IF NOT
    EXISTS so it's safe to call on every request (we cache after the
    first success)."""
    global _snapshots_table_ensured
    if _snapshots_table_ensured or engine is None:
        return None
    ddl = """
    CREATE TABLE IF NOT EXISTS snapshots (
        id VARCHAR(16) PRIMARY KEY,
        section TEXT NOT NULL,
        event_ticker TEXT,
        data JSONB NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        expires_at TIMESTAMP WITH TIME ZONE NOT NULL
    );
    """
    try:
        from sqlalchemy import text as _text
        async with engine.begin() as conn:
            await conn.execute(_text(ddl))
        _snapshots_table_ensured = True
        log.info("snapshots table ensured (raw DDL)")
        return None
    except Exception as e:
        log.warning("ensure snapshots table failed: %s", e)
        return str(e)[:400]


async def create_snapshot(section: str, data: dict, event_ticker: str = "",
                          ttl_days: int = 30):
    """Persist a snapshot and return (slug, None) on success or
    (None, error_message) on failure so the caller can surface the
    real error instead of swallowing it."""
    if not DATABASE_URL or async_session is None:
        return None, "database not configured (DATABASE_URL missing)"
    ensure_err = await _ensure_snapshots_table()
    if ensure_err:
        return None, f"failed to create snapshots table: {ensure_err}"
    try:
        import secrets
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        from models import Snapshot
        slug = secrets.token_urlsafe(8)[:12]
        expires = _dt.now(_tz.utc) + _td(days=ttl_days)
        async with async_session() as session:
            async with session.begin():
                session.add(Snapshot(
                    id=slug,
                    section=section,
                    event_ticker=event_ticker or None,
                    data=data,
                    expires_at=expires,
                ))
        return slug, None
    except Exception as e:
        log.error("create_snapshot failed: %s", e)
        return None, str(e)[:400]


async def get_snapshot(slug: str):
    """Return snapshot dict {section, data, created_at, expires_at,
    event_ticker} or None if not found / expired."""
    if not DATABASE_URL or async_session is None:
        return None
    await _ensure_snapshots_table()
    try:
        from datetime import datetime as _dt, timezone as _tz
        from models import Snapshot
        from sqlalchemy import select
        async with async_session() as session:
            stmt = select(Snapshot).where(Snapshot.id == slug)
            result = await session.execute(stmt)
            snap = result.scalar_one_or_none()
            if snap is None:
                return None
            if snap.expires_at and snap.expires_at < _dt.now(_tz.utc):
                return None
            return {
                "id": snap.id,
                "section": snap.section,
                "event_ticker": snap.event_ticker,
                "data": snap.data,
                "created_at": snap.created_at.isoformat() if snap.created_at else None,
                "expires_at": snap.expires_at.isoformat() if snap.expires_at else None,
            }
    except Exception as e:
        log.error("get_snapshot failed: %s", e)
        return None


async def prune_old_prices():
    """Delete price rows older than PRICE_RETENTION_HOURS. Called
    by the periodic pruning task and exposed via /api/prune."""
    if not DATABASE_URL or async_session is None:
        return 0
    try:
        from sqlalchemy import delete
        from models import Price
        from datetime import datetime, timezone as tz, timedelta
        cutoff = datetime.now(tz.utc) - timedelta(hours=PRICE_RETENTION_HOURS)
        async with async_session() as session:
            async with session.begin():
                result = await session.execute(
                    delete(Price).where(Price.captured_at < cutoff)
                )
                deleted = result.rowcount
        if deleted > 0:
            log.info("pruned %d price rows older than %dh", deleted, PRICE_RETENTION_HOURS)
        return deleted
    except Exception as e:
        log.error("price prune failed: %s", e)
        return -1


async def upsert_entities(teams):
    """Upsert teams into the entities + entity_aliases tables.

    `teams` is a list of dicts from entity_seeder.extract_teams():
      canonical_name, entity_type, sport, league, aliases [...]

    Uses ON CONFLICT DO NOTHING for entities (keyed on canonical_name)
    and ON CONFLICT DO NOTHING for aliases (keyed on alias+source),
    so this is safe to call repeatedly with the same data.

    Transaction strategy: process teams in fixed-size chunks. Each
    chunk = one short-lived transaction. This bounds the lock
    duration on entity_aliases so a slow inner loop cannot hold a
    transaction open for 30+ minutes (production transaction-leak
    incident on 2026-05-08 traced to this function — single mega-
    transaction wrapping thousands of inserts).

    On failure mid-chunk, the chunk's writes roll back via the
    session.begin() context's __aexit__. Subsequent chunks proceed
    normally — partial progress is preserved across chunks but
    NEVER mid-chunk.
    """
    if not DATABASE_URL or async_session is None or not teams:
        return
    try:
        from models import Entity, EntityAlias
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from sqlalchemy import select

        # ENTITY_UPSERT_CHUNK governs the per-transaction scope.
        # 100 teams × ~5 aliases each = ~600 round-trips per chunk,
        # well under the 60s statement_timeout / idle_in_transaction
        # ceiling on Neon. Tune downward if Neon latency ever spikes.
        CHUNK_SIZE = 100

        new_entities = 0
        new_aliases = 0
        chunks_committed = 0
        chunks_failed = 0

        for chunk_start in range(0, len(teams), CHUNK_SIZE):
            chunk = teams[chunk_start:chunk_start + CHUNK_SIZE]
            chunk_entities = 0
            chunk_aliases = 0
            try:
                async with async_session() as session:
                    async with session.begin():
                        for t in chunk:
                            canon = t.get("canonical_name", "")
                            if not canon:
                                continue

                            # Upsert entity (DO NOTHING on conflict — first
                            # writer wins the canonical name).
                            stmt = pg_insert(Entity).values(
                                canonical_name=canon,
                                entity_type=t.get("entity_type", "team"),
                                sport=t.get("sport"),
                                league=t.get("league"),
                            ).on_conflict_do_nothing(
                                index_elements=["canonical_name"],
                            )
                            result = await session.execute(stmt)
                            if result.rowcount > 0:
                                chunk_entities += 1

                            # Fetch the entity id (may have been created earlier).
                            row = await session.execute(
                                select(Entity.id).where(
                                    Entity.canonical_name == canon
                                )
                            )
                            entity_id = row.scalar_one_or_none()
                            if entity_id is None:
                                continue

                            # Upsert aliases
                            for a in t.get("aliases", []):
                                alias_stmt = pg_insert(EntityAlias).values(
                                    entity_id=entity_id,
                                    alias=a["alias"],
                                    source=a["source"],
                                    normalized=a["normalized"],
                                ).on_conflict_do_nothing(
                                    constraint="uq_alias_source",
                                )
                                r = await session.execute(alias_stmt)
                                if r.rowcount > 0:
                                    chunk_aliases += 1
                # Clean __aexit__ from session.begin() commits the chunk;
                # async_session() __aexit__ closes the session & returns
                # the connection to the pool.
                new_entities += chunk_entities
                new_aliases += chunk_aliases
                chunks_committed += 1
            except Exception as e:
                # Per-chunk failure does NOT abort the whole call.
                # The chunk's writes have rolled back via context-manager
                # __aexit__; subsequent chunks proceed.
                chunks_failed += 1
                log.warning(
                    "upsert_entities: chunk %d failed (%d teams), continuing: %s",
                    chunk_start // CHUNK_SIZE, len(chunk), e,
                )

        if new_entities or new_aliases or chunks_failed:
            log.info(
                "entity seed: %d new entities, %d new aliases "
                "(%d chunks ok, %d chunks failed)",
                new_entities, new_aliases, chunks_committed, chunks_failed,
            )
    except Exception as e:
        # Outermost catch — only fires if the chunk loop itself can't
        # start (e.g., import failure). Per-chunk errors are caught
        # above and don't reach here.
        log.error("entity seed setup failed: %s", e)


# ── Entity-based sport classifier ─────────────────────────────────
# Instead of relying solely on a hardcoded series_ticker → sport
# mapping in main.py, we auto-classify events by matching team
# aliases in the event title. Gets updated every entity-seed cycle.
#
# Format:  { normalized_alias_string : sport }
# Example: { "lakers": "Basketball", "arsenal": "Soccer", ... }
ALIAS_SPORT_CACHE: dict = {}


async def refresh_alias_sport_cache():
    """Load all entity aliases + their sports into ALIAS_SPORT_CACHE.
    Called after each entity seed cycle so the cache stays current."""
    if not DATABASE_URL or async_session is None:
        return
    try:
        from models import Entity, EntityAlias
        from sqlalchemy import select
        async with async_session() as session:
            # Join aliases to entities on entity_id, fetching sport
            # and the already-normalized alias string.
            stmt = select(
                EntityAlias.normalized,
                Entity.sport,
            ).join(Entity, EntityAlias.entity_id == Entity.id)
            rows = (await session.execute(stmt)).all()
            new_cache: dict = {}
            for norm, sport in rows:
                if not norm or not sport:
                    continue
                # If an alias maps to multiple sports (rare), first
                # writer wins. Could be improved with voting later.
                if norm not in new_cache:
                    new_cache[norm] = sport
            # Atomic replace so lookups never see a half-built cache.
            global ALIAS_SPORT_CACHE
            ALIAS_SPORT_CACHE = new_cache
            log.info("alias→sport cache refreshed: %d aliases", len(new_cache))
    except Exception as e:
        log.error("alias→sport cache refresh failed: %s", e)


def _normalize_title(s: str) -> str:
    """Same normalization as entity_seeder: lowercase + strip accents."""
    import unicodedata
    if not s:
        return ""
    s = unicodedata.normalize("NFD", str(s).lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.strip()


def get_sport_from_entities(title: str) -> str:
    """Synchronous lookup — scans a Kalshi event title for any
    entity alias and returns the matched entity's sport. Falls
    back to an empty string if no match.

    Intended as a fallback when the hardcoded series_ticker mapping
    returns nothing. Fast O(n) scan over aliases in the cache.
    """
    if not title or not ALIAS_SPORT_CACHE:
        return ""
    norm = _normalize_title(title)
    if not norm:
        return ""
    # Prefer longer aliases so "los angeles lakers" wins over "los".
    # Sorting aliases by length descending on every call would be
    # expensive — instead we sort once when building the cache, but
    # we can still do best-effort by checking all matches and
    # returning the longest hit.
    best_len = 0
    best_sport = ""
    for alias, sport in ALIAS_SPORT_CACHE.items():
        if len(alias) <= best_len:
            continue
        # Whole-word match: alias surrounded by word boundaries.
        # Fast check: alias must appear + not be inside a longer word.
        idx = norm.find(alias)
        if idx < 0:
            continue
        # Left boundary
        if idx > 0 and norm[idx - 1].isalnum():
            continue
        # Right boundary
        end = idx + len(alias)
        if end < len(norm) and norm[end].isalnum():
            continue
        best_len = len(alias)
        best_sport = sport
    return best_sport


# ───────────────────────────────────────────────────────────────────
# Cross-deploy cache persistence
#
# Each Railway redeploy spins up a fresh container with empty in-process
# state — flashlive_feed.GAMES (live scores), _SERIES_TO_STAGE_CACHE
# (soccer aggregate stage IDs), etc. Until the new container's first
# poll completes (~10-30s for FL), users see no LIVE pills and no
# aggregate pills. The healthcheck-gate approach failed because Kalshi
# warmup time was too variable for Railway's healthcheck timeout.
#
# Instead we persist the hot caches to Postgres on a slow timer + on
# every poll completion, and read them on container startup. The new
# container is born warm — the cold-start window disappears for users
# even if its own poll hasn't completed yet.
#
# Schema is intentionally generic — one table holds all cache blobs
# keyed by name. Each blob is JSONB so we can inspect it from psql
# during debugging, and so it's portable across Postgres providers.
# ───────────────────────────────────────────────────────────────────

_cache_blobs_table_ensured = False


async def _ensure_cache_blobs_table():
    """Idempotently create the cache_blobs table. Same self-heal
    pattern as _ensure_snapshots_table — safe to call repeatedly."""
    global _cache_blobs_table_ensured
    if _cache_blobs_table_ensured or engine is None:
        return None
    ddl = """
    CREATE TABLE IF NOT EXISTS cache_blobs (
        key TEXT PRIMARY KEY,
        data JSONB NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    );
    """
    try:
        from sqlalchemy import text as _text
        async with engine.begin() as conn:
            await conn.execute(_text(ddl))
        _cache_blobs_table_ensured = True
        log.info("cache_blobs table ensured")
        return None
    except Exception as e:
        log.warning("ensure cache_blobs table failed: %s", e)
        return str(e)[:400]


async def save_cache_blob(key: str, data) -> bool:
    """Upsert a cache blob. Returns True on success, False on failure
    (logged). Failure is non-fatal — caller continues with in-process
    state, the next save attempt will retry."""
    if not DATABASE_URL or async_session is None or engine is None:
        return False
    err = await _ensure_cache_blobs_table()
    if err:
        return False
    try:
        from sqlalchemy import text as _text
        sql = _text(
            "INSERT INTO cache_blobs (key, data, updated_at) "
            "VALUES (:key, CAST(:data AS JSONB), NOW()) "
            "ON CONFLICT (key) DO UPDATE SET "
            "data = EXCLUDED.data, updated_at = NOW()"
        )
        import json as _json
        async with engine.begin() as conn:
            await conn.execute(sql, {"key": key, "data": _json.dumps(data)})
        return True
    except Exception as e:
        log.warning("save_cache_blob(%s) failed: %s", key, e)
        return False


async def load_cache_blob(key: str):
    """Return the stored JSON value for `key`, or None if missing /
    DB unavailable. Caller decides what to do with None — typically
    fall through to building cache from scratch."""
    if not DATABASE_URL or engine is None:
        return None
    err = await _ensure_cache_blobs_table()
    if err:
        return None
    try:
        from sqlalchemy import text as _text
        sql = _text("SELECT data FROM cache_blobs WHERE key = :key")
        async with engine.begin() as conn:
            r = await conn.execute(sql, {"key": key})
            row = r.first()
            return row[0] if row else None
    except Exception as e:
        log.warning("load_cache_blob(%s) failed: %s", key, e)
        return None


async def load_v4_linkage_rows():
    """Bulk read of the sp.kalshi_markets → sp.fl_events linkage
    view for the Phase 3 v4 pathway. Called from the background
    _v4_linkage_refresh_loop in main.py — NEVER from the request
    path (per the architectural constraint: serving is DB-free).

    Returns a list of dicts with keys: event_ticker, fixture_id,
    fl_event_id. Returns None on failure (DB unavailable, query
    error) — caller keeps the previous _V4_LINKAGE_MAP alive.

    Query scope: last 7 days of active markets, resolver-linked
    (fixture_id IS NOT NULL). ~10-50k rows steady-state.
    """
    if not DATABASE_URL or engine is None:
        return None
    try:
        from sqlalchemy import text as _text
        sql = _text(
            "SELECT km.ticker AS event_ticker, "
            "       km.fixture_id, "
            "       fle.fl_event_id "
            "FROM sp.kalshi_markets km "
            "JOIN sp.fl_events fle "
            "  ON km.fixture_id = fle.fixture_id "
            "WHERE km.fixture_id IS NOT NULL "
            "  AND km.last_seen_at > NOW() - INTERVAL '7 days'"
        )
        async with engine.begin() as conn:
            r = await conn.execute(sql)
            rows = r.all()
            return [
                {
                    "event_ticker": row[0],
                    "fixture_id":   row[1],
                    "fl_event_id":  row[2],
                }
                for row in rows
            ]
    except Exception as e:
        log.warning("load_v4_linkage_rows failed: %s", e)
        return None


async def load_cutover_config_row():
    """Read the sp.cutover_config singleton row. Returns None on any
    failure (DB unavailable, table missing, no row). Called from the
    background _cutover_config_refresh_loop in main.py — NEVER from
    the request path.

    Returns a dict with keys: traffic_pct, enabled_sports (list[str]),
    min_cohort_series. Field names match the migration schema.
    """
    if not DATABASE_URL or engine is None:
        return None
    try:
        from sqlalchemy import text as _text
        sql = _text(
            "SELECT traffic_pct, enabled_sports, min_cohort_series "
            "FROM sp.cutover_config WHERE id = 1"
        )
        async with engine.begin() as conn:
            r = await conn.execute(sql)
            row = r.first()
            if row is None:
                return None
            return {
                "traffic_pct":       int(row[0]),
                "enabled_sports":    tuple(row[1] or ()),
                "min_cohort_series": int(row[2]),
            }
    except Exception as e:
        log.warning("load_cutover_config_row failed: %s", e)
        return None
