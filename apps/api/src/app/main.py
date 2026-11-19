"""API application factory. Served with `uvicorn --factory app.main:create_app`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.clients import create_redis_client, create_s3_client
from app.core.config import Settings, load_settings
from app.core.error_handlers import register_error_handlers
from app.core.logging import add_request_logging, configure_logging
from app.health.routes import router as health_router
from app.health.service import build_dependency_checks


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or load_settings()
    configure_logging(resolved_settings.logging.level)

    app = FastAPI(title="Meeting Copilot API", version="0.0.0", lifespan=lifespan)
    app.state.settings = resolved_settings
    register_error_handlers(app)
    add_request_logging(app)
    app.include_router(health_router)
    return app


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    redis_client = create_redis_client(settings)
    s3_client = create_s3_client(settings)
    app.state.dependency_checks = build_dependency_checks(settings, redis_client, s3_client)
    try:
        yield
    finally:
        await redis_client.aclose()
        s3_client.close()
