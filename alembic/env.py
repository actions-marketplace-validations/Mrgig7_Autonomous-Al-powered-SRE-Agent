"""Alembic environment configuration."""

import asyncio
import os
from logging.config import fileConfig

import sre_agent.models.fix_pipeline as _fix_pipeline_models
import sre_agent.models.user as _user_models
from sqlalchemy import pool
from sqlalchemy.engine import Connection

# Import models for autogenerate support
from sre_agent.models.events import Base

from alembic import context

_ = (_fix_pipeline_models, _user_models)

# this is the Alembic Config object
config = context.config

# Override sqlalchemy.url from environment variable
database_url = os.getenv("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Model's MetaData object for autogenerate support
target_metadata = Base.metadata

# Detect if the URL uses an async driver
_url = config.get_main_option("sqlalchemy.url") or ""
_is_async = "+asyncpg" in _url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well. By skipping the Engine creation
    we don't even need a DBAPI to be available.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations with the given connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table="alembic_version",
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with async engine."""
    from sqlalchemy.ext.asyncio import async_engine_from_config

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_sync_migrations() -> None:
    """Run migrations in 'online' mode with sync engine."""
    from sqlalchemy import engine_from_config

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        do_run_migrations(connection)


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    if _is_async:
        asyncio.run(run_async_migrations())
    else:
        run_sync_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
