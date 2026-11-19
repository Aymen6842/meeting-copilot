"""GET /health against real Postgres, Redis and RustFS (shared session containers)."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import httpx
import pytest
from fastapi import FastAPI
from testcontainers.community.postgres import PostgresContainer

from app.main import create_app

pytestmark = pytest.mark.integration

ClientFactory = Callable[[FastAPI], AbstractAsyncContextManager[httpx.AsyncClient]]


async def get_health(app: FastAPI, make_client: ClientFactory) -> httpx.Response:
    async with app.router.lifespan_context(app), make_client(app) as client:
        return await client.get("/health")


async def test_health_is_ok_when_every_dependency_is_reachable(
    integration_environment: pytest.MonkeyPatch, make_client: ClientFactory
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
    integration_environment: pytest.MonkeyPatch, make_client: ClientFactory
) -> None:
    integration_environment.setenv("APP_STORAGE__BUCKET", "bucket-that-does-not-exist")

    response = await get_health(create_app(), make_client)

    body = response.json()
    assert response.status_code == 503
    assert body["checks"]["storage"]["status"] == "error"
    assert body["checks"]["postgres"]["status"] == "ok"


async def test_health_reports_wrong_database_credentials(
    integration_environment: pytest.MonkeyPatch,
    postgres_container: PostgresContainer,
    migrated_database_url: str,
    make_client: ClientFactory,
) -> None:
    wrong_url = migrated_database_url.replace(
        f":{postgres_container.password}@", ":wrong-password@"
    )
    integration_environment.setenv("APP_DATABASE_URL", wrong_url)

    response = await get_health(create_app(), make_client)

    body = response.json()
    assert response.status_code == 503
    assert body["checks"]["postgres"]["error"] == "InvalidPasswordError"
    assert "wrong-password" not in response.text
