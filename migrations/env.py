import asyncio
import os
import sys
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Make the project root importable so we can pull in sp_models and
# reuse db.py's URL normalization (Neon sslmode/channel_binding
# handling, libpq → asyncpg param translation, etc.).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Single source of truth for DATABASE_URL handling: db.py already
# parses the URL, strips unsupported query params, translates
# sslmode to asyncpg's ssl kwarg, and auto-forces SSL for Neon /
# Supabase hosts. Reuse it so alembic and the app connect identically.
try:
    from db import DATABASE_URL as _DB_URL, _connect_args as _DB_CONNECT_ARGS
except Exception:
    _DB_URL = os.environ.get("DATABASE_URL", "").strip()
    _DB_CONNECT_ARGS = {}
    if _DB_URL.startswith("postgres://"):
        _DB_URL = _DB_URL.replace("postgres://", "postgresql+asyncpg://", 1)
    elif _DB_URL.startswith("postgresql://") and "+asyncpg" not in _DB_URL:
        _DB_URL = _DB_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

if _DB_URL:
    config.set_main_option("sqlalchemy.url", _DB_URL)

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import the SP architecture metadata so autogenerate sees our tables.
# The legacy `models.py` lives in the public schema and is managed
# manually (no Alembic ownership) — we deliberately do NOT import it
# here so autogenerate cannot drop or alter the legacy tables.
from sp_models import SPBase, SCHEMA  # noqa: E402

target_metadata = SPBase.metadata


def include_object(obj, name, type_, reflected, compare_to):
    """Restrict autogenerate to the SP schema.

    Without this, alembic would propose dropping every legacy table
    (entities, events, markets, prices, etc.) on every revision since
    they're not in target_metadata. The schema filter keeps the SP
    rebuild and the legacy data layer in their own lanes.
    """
    if type_ == "table":
        return obj.schema == SCHEMA
    if hasattr(obj, "table") and obj.table is not None:
        return obj.table.schema == SCHEMA
    return True

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        include_object=include_object,
        version_table_schema=SCHEMA,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    # Make sure the sp schema exists before alembic tries to create
    # its own version table inside it. The CREATE SCHEMA runs inside
    # whatever transaction the outer caller has open — alembic's
    # commit at the end picks it up.
    connection.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        include_object=include_object,
        version_table_schema=SCHEMA,
        # Critical for async + asyncpg: we want alembic to own and
        # commit the transaction itself rather than leaving it to the
        # outer async context. Without this, the sync proxy inside
        # run_sync completes its work, the SAVEPOINT commits, the
        # async connection closes — and asyncpg silently rolls back
        # because no COMMIT ever made it to the server. transaction_
        # per_migration=True flips alembic to begin/commit per migration
        # script, which we want anyway for safer partial-progress on
        # multi-migration runs.
        transaction_per_migration=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Online migration runner for async drivers (asyncpg).

    Connection lifecycle:
      1. Open a transaction-managed connection via connectable.begin().
         This is .begin(), NOT .connect(), so the transaction's commit
         on clean __aexit__ is explicit and dispatched to the driver.
      2. run_sync hands a sync proxy to do_run_migrations, which runs
         CREATE SCHEMA + alembic's per-migration transactions.
      3. On clean exit, the outer .begin() context commits — this is
         what actually sends COMMIT to asyncpg. With .connect()'s
         begin-once mode, the commit isn't reliably propagated across
         the sync/async boundary inside run_sync, and DDL silently
         rolls back at connection close. Documented gotcha.
    """
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=_DB_CONNECT_ARGS,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
        # Belt-and-suspenders: explicitly commit the outer transaction
        # so the CREATE SCHEMA (which runs before alembic's
        # transaction_per_migration block) is guaranteed to land.
        # Idempotent — alembic's own transaction management has
        # already committed each migration script's DDL by this point.
        await connection.commit()

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
