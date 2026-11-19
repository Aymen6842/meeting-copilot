"""GET /health against real Postgres, Redis and RustFS (shared session containers)."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import httpx
import pytest
from fastapi import FastAPI
from testcontainers.community.postgres import PostgresContainer

from app.main import create_app
from tests.fixtures.containers import Storage

pytestmark = pytest.mark.integration

ClientFactory = Callable[[FastAPI], AbstractAsyncContextManager[httpx.AsyncClient]]


@pytest.fixture
def real_environment(
    app_environment: pytest.MonkeyPatch,
    postgres_url: str,
    redis_url: str,
    storage_container: Storage,
) -> pytest.MonkeyPatch:
    app_environment.setenv("APP_DATABASE_URL", postgres_url)
    app_environment.setenv("APP_REDIS_URL", redis_url)
    app_environment.setenv("APP_STORAGE__ENDPOINT_URL", storage_container.endpoint_url)
    app_environment.setenv("APP_STORAGE__ACCESS_KEY", storage_container.access_key)
    app_environment.setenv("APP_STORAGE__SECRET_KEY", storage_container.secret_key)
    app_environment.setenv("APP_STORAGE__BUCKET", storage_container.bucket)
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
    postgres_container: PostgresContainer,
    postgres_url: str,
    make_client: ClientFactory,
) -> None:
    wrong_url = postgres_url.replace(f":{postgres_container.password}@", ":wrong-password@")
    real_environment.setenv("APP_DATABASE_URL", wrong_url)

    response = await get_health(create_app(), make_client)

    body = response.json()
    assert response.status_code == 503
    assert body["checks"]["postgres"]["error"] == "InvalidPasswordError"
    assert "wrong-password" not in response.text
