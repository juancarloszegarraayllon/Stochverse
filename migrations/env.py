import asyncio
import os
import sys
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Make the project root importable so we can pull in sp_models.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Inject DATABASE_URL from the environment so the same env var that
# powers the application also drives migrations. Normalize the scheme
# the same way db.py does — Railway emits postgres:// and asyncpg
# wants postgresql+asyncpg://.
_db_url = os.environ.get("DATABASE_URL", "").strip()
if _db_url:
    if _db_url.startswith("postgres://"):
        _db_url = _db_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif _db_url.startswith("postgresql://") and "+asyncpg" not in _db_url:
        _db_url = _db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    config.set_main_option("sqlalchemy.url", _db_url)

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
    # its own version table inside it.
    connection.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        include_object=include_object,
        version_table_schema=SCHEMA,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
