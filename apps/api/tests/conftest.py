import json
import logging
import os
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

import httpx
import pytest
import redis.asyncio
from fastapi import FastAPI

from app.core.config import Settings, load_settings
from app.main import create_app
from tests.fixtures.containers import Storage

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "config"

# Placeholder connection values: unit tests never open these connections.
REQUIRED_ENVIRONMENT = {
    "APP_DATABASE_URL": "postgresql+asyncpg://test-user:test-password@localhost:5432/test",
    "APP_REDIS_URL": "redis://:test-password@localhost:6379/0",
    "APP_STORAGE__ENDPOINT_URL": "http://localhost:9000",
    "APP_STORAGE__ACCESS_KEY": "test-access-key",
    "APP_STORAGE__SECRET_KEY": "test-secret-key",
    "APP_STORAGE__BUCKET": "test-bucket",
}


@pytest.fixture(autouse=True)
def isolated_app_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test with no APP_* variables from the developer's shell."""
    for name in list(os.environ):
        if name.startswith("APP_"):
            monkeypatch.delenv(name)


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    """`configure_logging` changes global loggers; put them back after each test."""
    names = ["", "uvicorn", "uvicorn.error", "uvicorn.access"]
    saved = {
        name: (
            logging.getLogger(name).handlers[:],
            logging.getLogger(name).level,
            logging.getLogger(name).propagate,
        )
        for name in names
    }
    yield
    for name, (handlers, level, propagate) in saved.items():
        logger = logging.getLogger(name)
        logger.handlers = handlers
        logger.setLevel(level)
        logger.propagate = propagate


@pytest.fixture
def app_environment(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """The environment `load_settings` needs, with the ci profile."""
    monkeypatch.setenv("APP_PROFILE", "ci")
    monkeypatch.setenv("APP_CONFIG_DIR", str(CONFIG_DIR))
    for name, value in REQUIRED_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


@pytest.fixture
def settings(app_environment: pytest.MonkeyPatch) -> Settings:
    return load_settings(CONFIG_DIR)


@asynccontextmanager
async def client_for(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """HTTP client calling the app in-process. Server errors become 500 responses.

    https, so the client sends back the session cookie, which is marked Secure."""
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        yield client


def json_log_lines(output: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in output.splitlines() if line.startswith("{")]


ClientFactory = Callable[[FastAPI], AbstractAsyncContextManager[httpx.AsyncClient]]
LogParser = Callable[[str], list[dict[str, object]]]


@pytest.fixture
def make_client() -> ClientFactory:
    return client_for


@pytest.fixture
def parse_log_lines() -> LogParser:
    return json_log_lines


@pytest.fixture
def integration_environment(
    app_environment: pytest.MonkeyPatch,
    migrated_database_url: str,
    redis_url: str,
    storage_container: Storage,
) -> pytest.MonkeyPatch:
    """The app's environment pointed at the shared test containers."""
    app_environment.setenv("APP_DATABASE_URL", migrated_database_url)
    app_environment.setenv("APP_REDIS_URL", redis_url)
    app_environment.setenv("APP_STORAGE__ENDPOINT_URL", storage_container.endpoint_url)
    app_environment.setenv("APP_STORAGE__ACCESS_KEY", storage_container.access_key)
    app_environment.setenv("APP_STORAGE__SECRET_KEY", storage_container.secret_key)
    app_environment.setenv("APP_STORAGE__BUCKET", storage_container.bucket)
    return app_environment


@asynccontextmanager
async def live_client(redis_url: str) -> AsyncIterator[httpx.AsyncClient]:
    """The full app (lifespan included) built from the current environment.

    Redis is flushed first so rate-limit counters never leak between tests. Database rows
    persist across tests: use unique emails."""
    flush_client = redis.asyncio.Redis.from_url(redis_url)
    await flush_client.flushdb()
    await flush_client.aclose()
    app = create_app()
    async with app.router.lifespan_context(app), client_for(app) as client:
        yield client


ApiClientFactory = Callable[[], AbstractAsyncContextManager[httpx.AsyncClient]]


@pytest.fixture
def make_api_client(
    integration_environment: pytest.MonkeyPatch, redis_url: str
) -> ApiClientFactory:
    """For tests that set APP_* overrides first, then build the app."""
    return lambda: live_client(redis_url)


@pytest.fixture
async def api_client(make_api_client: ApiClientFactory) -> AsyncIterator[httpx.AsyncClient]:
    async with make_api_client() as client:
        yield client
