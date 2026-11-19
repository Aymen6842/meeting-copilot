"""Dependency checks behind `GET /health`. Checks run concurrently, each with a timeout."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING

import redis.asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.health.schemas import DependencyStatus, HealthResponse

if TYPE_CHECKING:  # stub-only package, installed with the dev dependencies
    from mypy_boto3_s3 import S3Client

DependencyCheck = Callable[[], Awaitable[None]]

logger = logging.getLogger(__name__)


def build_dependency_checks(
    settings: Settings,
    engine: AsyncEngine,
    redis_client: redis.asyncio.Redis,
    s3_client: "S3Client",
) -> dict[str, DependencyCheck]:
    bucket = settings.storage.bucket

    async def check_postgres() -> None:
        # Through the shared pool, so the check also proves the app can get a connection.
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def check_redis() -> None:
        await redis_client.ping()

    async def check_storage() -> None:
        await asyncio.to_thread(s3_client.head_bucket, Bucket=bucket)

    return {"postgres": check_postgres, "redis": check_redis, "storage": check_storage}


async def check_health(checks: Mapping[str, DependencyCheck], timeout_ms: int) -> HealthResponse:
    names = list(checks)
    results = await asyncio.gather(*(run_check(name, checks[name], timeout_ms) for name in names))
    statuses = dict(zip(names, results, strict=True))
    all_ok = all(status.status == "ok" for status in statuses.values())
    return HealthResponse(status="ok" if all_ok else "unavailable", checks=statuses)


async def run_check(name: str, check: DependencyCheck, timeout_ms: int) -> DependencyStatus:
    started = time.perf_counter_ns()
    error: str | None = None
    try:
        async with asyncio.timeout(timeout_ms / 1000):
            await check()
    except TimeoutError:
        error = "timeout"
    except Exception as exception:  # any failure means "unhealthy", never a crash
        error = type(root_cause(exception)).__name__
    duration_ms = (time.perf_counter_ns() - started) // 1_000_000
    if error is not None:
        logger.warning("dependency check failed", extra={"dependency": name, "error": error})
        return DependencyStatus(status="error", duration_ms=duration_ms, error=error)
    return DependencyStatus(status="ok", duration_ms=duration_ms)


def root_cause(exception: BaseException) -> BaseException:
    """Innermost explicitly chained cause: SQLAlchemy wraps driver errors in DBAPIError."""
    seen: set[int] = set()
    while exception.__cause__ is not None and id(exception) not in seen:
        seen.add(id(exception))
        exception = exception.__cause__
    return exception
