"""GET /health against real Postgres, Redis and RustFS containers (Testcontainers).

Images come from docker-compose.yml so tests and the stack always run the same versions.
"""

from collections.abc import Callable, Iterator
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any

import boto3
import httpx
import pytest
import yaml
from fastapi import FastAPI
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import HttpWaitStrategy

from app.main import create_app

pytestmark = pytest.mark.integration

ClientFactory = Callable[[FastAPI], AbstractAsyncContextManager[httpx.AsyncClient]]

COMPOSE_FILE = Path(__file__).resolve().parents[3] / "docker-compose.yml"
STORAGE_ACCESS_KEY = "integration-access-key"
STORAGE_SECRET_KEY = "integration-secret-key"  # noqa: S105 - throwaway container credential
BUCKET = "integration-bucket"


def compose_image(service: str) -> str:
    compose: dict[str, Any] = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))  # Any: YAML
    image: str = compose["services"][service]["image"]
    return image


@pytest.fixture(scope="module")
def postgres() -> Iterator[PostgresContainer]:
    with PostgresContainer(compose_image("postgres"), driver=None) as container:
        yield container


@pytest.fixture(scope="module")
def redis_container() -> Iterator[RedisContainer]:
    with RedisContainer(compose_image("redis")) as container:
        yield container


@pytest.fixture(scope="module")
def storage() -> Iterator[DockerContainer]:
    container = (
        DockerContainer(compose_image("storage"))
        .with_env("RUSTFS_ACCESS_KEY", STORAGE_ACCESS_KEY)
        .with_env("RUSTFS_SECRET_KEY", STORAGE_SECRET_KEY)
        .with_exposed_ports(9000)
        .waiting_for(HttpWaitStrategy(9000, "/health/ready").for_status_code(200))
    )
    with container:
        yield container


def storage_url(storage: DockerContainer) -> str:
    return f"http://{storage.get_container_host_ip()}:{storage.get_exposed_port(9000)}"


@pytest.fixture(scope="module")
def bucket(storage: DockerContainer) -> str:
    s3 = boto3.client(
        "s3",
        endpoint_url=storage_url(storage),
        region_name="us-east-1",
        aws_access_key_id=STORAGE_ACCESS_KEY,
        aws_secret_access_key=STORAGE_SECRET_KEY,
    )
    s3.create_bucket(Bucket=BUCKET)
    return BUCKET


@pytest.fixture
def real_environment(
    app_environment: pytest.MonkeyPatch,
    postgres: PostgresContainer,
    redis_container: RedisContainer,
    storage: DockerContainer,
    bucket: str,
) -> pytest.MonkeyPatch:
    redis_host = redis_container.get_container_host_ip()
    redis_port = redis_container.get_exposed_port(6379)
    app_environment.setenv("APP_DATABASE_URL", postgres.get_connection_url(driver="asyncpg"))
    app_environment.setenv("APP_REDIS_URL", f"redis://{redis_host}:{redis_port}/0")
    app_environment.setenv("APP_STORAGE__ENDPOINT_URL", storage_url(storage))
    app_environment.setenv("APP_STORAGE__ACCESS_KEY", STORAGE_ACCESS_KEY)
    app_environment.setenv("APP_STORAGE__SECRET_KEY", STORAGE_SECRET_KEY)
    app_environment.setenv("APP_STORAGE__BUCKET", bucket)
    return app_environment


async def get_health(app: FastAPI, make_client: ClientFactory) -> httpx.Response:
    async with app.router.lifespan_context(app), make_client(app) as client:
        return await client.get("/health")


async def test_health_is_ok_when_every_dependency_is_reachable(
    real_environment: pytest.MonkeyPatch, make_client: ClientFactory
) -> None:
    response = await get_health(create_app(), make_client)

    body = response.json()
    assert response.status_code == 200, body
    assert {name: check["status"] for name, check in body["checks"].items()} == {
        "postgres": "ok",
        "redis": "ok",
        "storage": "ok",
    }


async def test_health_reports_a_missing_bucket(
    real_environment: pytest.MonkeyPatch, make_client: ClientFactory
) -> None:
    real_environment.setenv("APP_STORAGE__BUCKET", "bucket-that-does-not-exist")

    response = await get_health(create_app(), make_client)

    body = response.json()
    assert response.status_code == 503
    assert body["checks"]["storage"]["status"] == "error"
    assert body["checks"]["postgres"]["status"] == "ok"


async def test_health_reports_wrong_database_credentials(
    real_environment: pytest.MonkeyPatch,
    postgres: PostgresContainer,
    make_client: ClientFactory,
) -> None:
    wrong_url = postgres.get_connection_url(driver="asyncpg").replace(
        f":{postgres.password}@", ":wrong-password@"
    )
    real_environment.setenv("APP_DATABASE_URL", wrong_url)

    response = await get_health(create_app(), make_client)

    body = response.json()
    assert response.status_code == 503
    assert body["checks"]["postgres"]["error"] == "InvalidPasswordError"
    assert "wrong-password" not in response.text
