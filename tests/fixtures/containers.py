"""Real infrastructure for integration tests, started once per test session (Testcontainers).

Images come from docker-compose.yml, so tests always run the versions the stack runs.

- `postgres_container`, `redis_container`, `storage_container`: the raw containers.
- `migrated_database_url`: a database migrated to head once per session.
- `db_session`: a session on that database; everything it does is rolled back afterwards.
- `create_database`: makes fresh, empty databases (for migration tests).
"""

import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
import pytest
import yaml
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import HttpWaitStrategy

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
ALEMBIC_INI = REPO_ROOT / "apps" / "api" / "alembic.ini"

STORAGE_ACCESS_KEY = "integration-access-key"
STORAGE_SECRET_KEY = "integration-secret-key"
STORAGE_BUCKET = "integration-bucket"


@dataclass(frozen=True)
class Storage:
    endpoint_url: str
    access_key: str
    secret_key: str
    bucket: str


def compose_image(service: str) -> str:
    compose: dict[str, Any] = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))  # Any: YAML
    image: str = compose["services"][service]["image"]
    return image


def alembic_config(database_url: str) -> AlembicConfig:
    config = AlembicConfig(str(ALEMBIC_INI))
    config.attributes["database_url"] = database_url
    config.attributes["configure_logging"] = False  # keep pytest's log capture intact
    return config


def migrate(database_url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(database_url), revision)


def downgrade(database_url: str, revision: str = "base") -> None:
    command.downgrade(alembic_config(database_url), revision)


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    with PostgresContainer(compose_image("postgres"), driver=None) as container:
        yield container


@pytest.fixture(scope="session")
def postgres_url(postgres_container: PostgresContainer) -> str:
    """asyncpg URL of the container's default (unmigrated) database."""
    return postgres_container.get_connection_url(driver="asyncpg")


@pytest.fixture(scope="session")
def redis_container() -> Iterator[RedisContainer]:
    with RedisContainer(compose_image("redis")) as container:
        yield container


@pytest.fixture(scope="session")
def redis_url(redis_container: RedisContainer) -> str:
    host = redis_container.get_container_host_ip()
    return f"redis://{host}:{redis_container.get_exposed_port(6379)}/0"


@pytest.fixture(scope="session")
def storage_container() -> Iterator[Storage]:
    container = (
        DockerContainer(compose_image("storage"))
        .with_env("RUSTFS_ACCESS_KEY", STORAGE_ACCESS_KEY)
        .with_env("RUSTFS_SECRET_KEY", STORAGE_SECRET_KEY)
        .with_exposed_ports(9000)
        .waiting_for(HttpWaitStrategy(9000, "/health/ready").for_status_code(200))
    )
    with container:
        host = container.get_container_host_ip()
        storage = Storage(
            endpoint_url=f"http://{host}:{container.get_exposed_port(9000)}",
            access_key=STORAGE_ACCESS_KEY,
            secret_key=STORAGE_SECRET_KEY,
            bucket=STORAGE_BUCKET,
        )
        s3 = boto3.client(
            "s3",
            endpoint_url=storage.endpoint_url,
            region_name="us-east-1",
            aws_access_key_id=storage.access_key,
            aws_secret_access_key=storage.secret_key,
        )
        s3.create_bucket(Bucket=storage.bucket)
        yield storage


def create_empty_database(container: PostgresContainer) -> str:
    """Create a uniquely named database and return its name."""
    name = f"test_{uuid.uuid4().hex[:12]}"
    run_psql(container, f'CREATE DATABASE "{name}"')
    return name


def drop_database(container: PostgresContainer, name: str) -> None:
    run_psql(container, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def run_psql(container: PostgresContainer, sql: str) -> None:
    result = container.exec(["psql", "-U", container.username, "-d", "postgres", "-c", sql])
    if result.exit_code != 0:
        raise RuntimeError(f"psql failed: {result.output.decode(errors='replace')}")


def database_url(container: PostgresContainer, name: str) -> str:
    default_url = container.get_connection_url(driver="asyncpg")
    return default_url.rsplit("/", 1)[0] + f"/{name}"


@pytest.fixture
def create_database(postgres_container: PostgresContainer) -> Iterator[Callable[[], str]]:
    """Factory for fresh, empty databases; each is dropped after the test."""
    created: list[str] = []

    def create() -> str:
        name = create_empty_database(postgres_container)
        created.append(name)
        return database_url(postgres_container, name)

    yield create
    for name in created:
        drop_database(postgres_container, name)


@pytest.fixture(scope="session")
def migrated_database_url(postgres_container: PostgresContainer) -> str:
    url = database_url(postgres_container, create_empty_database(postgres_container))
    migrate(url)
    return url


@pytest.fixture
async def db_session(migrated_database_url: str) -> AsyncIterator[AsyncSession]:
    """A session whose work, commits included, is rolled back when the test ends.

    The session runs inside an outer transaction; its own commits become savepoints.
    """
    engine = create_async_engine(migrated_database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(
                bind=connection,
                join_transaction_mode="create_savepoint",
                expire_on_commit=False,
            )
            try:
                yield session
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()
