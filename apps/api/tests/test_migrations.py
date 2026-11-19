"""Migrations against real Postgres: up, down, up again, and no drift from the models.

These tests are synchronous because Alembic's env.py runs its own event loop.
"""

import asyncio
from collections.abc import Callable

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.model_registry import all_models_metadata
from tests.fixtures.containers import alembic_config, downgrade, migrate

APP_TABLES = {"workspaces", "users", "memberships"}

# Every relation (table, index, sequence, view) and user-defined type in the public schema,
# except Alembic's own bookkeeping table and its primary key.
SCHEMA_OBJECTS_SQL = """
SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname NOT IN ('alembic_version', 'alembic_version_pkc')
UNION ALL
SELECT t.typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
WHERE n.nspname = 'public' AND t.typtype IN ('e', 'd', 'c')
  AND t.typrelid = 0
"""


def query_names(database_url: str, sql: str) -> set[str]:
    async def run() -> set[str]:
        engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                return {row[0] for row in await connection.execute(text(sql))}
        finally:
            await engine.dispose()

    return asyncio.run(run())


def schema_differences(database_url: str) -> list[object]:
    def compare(connection: Connection) -> list[object]:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "compare_server_default": True}
        )
        return list(compare_metadata(context, all_models_metadata()))

    async def run() -> list[object]:
        engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(compare)
        finally:
            await engine.dispose()

    return asyncio.run(run())


def public_tables(database_url: str) -> set[str]:
    return query_names(database_url, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")


def test_there_is_exactly_one_migration_head() -> None:
    script = ScriptDirectory.from_config(alembic_config("postgresql+asyncpg://unused"))

    assert len(script.get_heads()) == 1


@pytest.mark.integration
def test_upgrade_creates_the_tables(create_database: Callable[[], str]) -> None:
    url = create_database()

    migrate(url)

    assert public_tables(url) == APP_TABLES | {"alembic_version"}


@pytest.mark.integration
def test_downgrade_to_base_leaves_nothing_behind(create_database: Callable[[], str]) -> None:
    url = create_database()
    migrate(url)

    downgrade(url)

    assert query_names(url, SCHEMA_OBJECTS_SQL) == set()


@pytest.mark.integration
def test_upgrade_works_again_after_a_full_downgrade(create_database: Callable[[], str]) -> None:
    url = create_database()
    migrate(url)
    downgrade(url)

    migrate(url)

    assert public_tables(url) == APP_TABLES | {"alembic_version"}


@pytest.mark.integration
def test_models_match_the_migrated_schema(create_database: Callable[[], str]) -> None:
    url = create_database()
    migrate(url)

    assert schema_differences(url) == []
