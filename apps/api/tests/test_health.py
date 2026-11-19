import asyncio
import time
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import Settings, load_settings
from app.health.routes import get_dependency_checks
from app.health.service import DependencyCheck, check_health, root_cause
from app.main import create_app

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"

ClientFactory = Callable[[FastAPI], AbstractAsyncContextManager[httpx.AsyncClient]]


async def healthy() -> None:
    return None


async def refused() -> None:
    raise ConnectionRefusedError("could not reach postgresql://user:hunter2@db:5432")


async def hangs() -> None:
    await asyncio.sleep(10)


def app_with_checks(settings: Settings, checks: Mapping[str, DependencyCheck]) -> FastAPI:
    app = create_app(settings)
    app.dependency_overrides[get_dependency_checks] = lambda: checks
    return app


async def test_all_dependencies_ok_returns_200(
    settings: Settings, make_client: ClientFactory
) -> None:
    checks = {"postgres": healthy, "redis": healthy, "storage": healthy}

    async with make_client(app_with_checks(settings, checks)) as client:
        response = await client.get("/health")

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "ok"
    assert set(body["checks"]) == {"postgres", "redis", "storage"}
    assert all(check["status"] == "ok" for check in body["checks"].values())


async def test_failing_dependency_returns_503_with_error_type_only(
    settings: Settings, make_client: ClientFactory
) -> None:
    checks = {"postgres": refused, "redis": healthy, "storage": healthy}

    async with make_client(app_with_checks(settings, checks)) as client:
        response = await client.get("/health")

    body = response.json()
    assert response.status_code == 503
    assert body["status"] == "unavailable"
    assert body["checks"]["postgres"] == {
        "status": "error",
        "duration_ms": body["checks"]["postgres"]["duration_ms"],
        "error": "ConnectionRefusedError",
    }
    assert body["checks"]["redis"]["status"] == "ok"
    assert "hunter2" not in response.text


async def test_hanging_dependency_times_out(
    app_environment: pytest.MonkeyPatch, make_client: ClientFactory
) -> None:
    app_environment.setenv("APP_HEALTH__CHECK_TIMEOUT_MS", "50")
    settings = load_settings(CONFIG_DIR)
    checks = {"postgres": healthy, "redis": hangs, "storage": healthy}

    async with make_client(app_with_checks(settings, checks)) as client:
        response = await client.get("/health")

    assert response.status_code == 503
    assert response.json()["checks"]["redis"]["error"] == "timeout"


async def test_checks_run_concurrently() -> None:
    async def slow() -> None:
        await asyncio.sleep(0.2)

    started = time.perf_counter()
    report = await check_health({"a": slow, "b": slow, "c": slow}, timeout_ms=2000)
    elapsed = time.perf_counter() - started

    assert report.status == "ok"
    assert elapsed < 0.5  # sequential would take at least 0.6 s


def test_root_cause_unwraps_explicitly_chained_errors() -> None:
    driver_error = ConnectionRefusedError("driver error")
    wrapper = RuntimeError("wrapper")
    wrapper.__cause__ = driver_error

    assert root_cause(wrapper) is driver_error


def test_root_cause_of_an_unchained_error_is_itself() -> None:
    error = ValueError("plain")

    assert root_cause(error) is error


async def test_wrapped_failure_is_reported_by_its_root_cause_type() -> None:
    async def wrapped_refusal() -> None:
        try:
            raise ConnectionRefusedError("could not connect")
        except ConnectionRefusedError as driver_error:
            raise RuntimeError("wrapped by a library") from driver_error

    report = await check_health({"postgres": wrapped_refusal}, timeout_ms=1000)

    assert report.checks["postgres"].error == "ConnectionRefusedError"
