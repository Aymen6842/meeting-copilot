import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from typing import Annotated

import httpx
import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.auth.models import Workspace
from app.core.config import Settings, load_settings
from app.core.db import create_engine, create_session_factory, get_session

ClientFactory = Callable[[FastAPI], AbstractAsyncContextManager[httpx.AsyncClient]]
Session = Annotated[AsyncSession, Depends(get_session)]
UNCOMMITTED_NAME = f"never committed {uuid.uuid4()}"


def test_engine_uses_the_configured_pool(settings: Settings) -> None:
    engine = create_engine(settings)

    assert engine.pool.size() == settings.database.pool_size  # type: ignore[attr-defined]
    assert engine.url.password == "test-password"


@pytest.fixture
async def engine(
    migrated_database_url: str, app_environment: pytest.MonkeyPatch
) -> AsyncIterator[AsyncEngine]:
    app_environment.setenv("APP_DATABASE_URL", migrated_database_url)
    engine = create_engine(load_settings())
    yield engine
    await engine.dispose()


def session_app(engine: AsyncEngine) -> FastAPI:
    app = FastAPI()
    app.state.session_factory = create_session_factory(engine)

    @app.get("/select-one")
    async def select_one(session: Session) -> dict[str, int]:
        return {"value": (await session.execute(text("SELECT 1"))).scalar_one()}

    @app.post("/workspaces-without-commit")
    async def add_without_commit(session: Session) -> dict[str, str]:
        session.add(Workspace(name=UNCOMMITTED_NAME))
        await session.flush()
        return {"status": "flushed"}

    return app


@pytest.mark.integration
async def test_get_session_gives_a_working_session(
    engine: AsyncEngine, make_client: ClientFactory
) -> None:
    async with make_client(session_app(engine)) as client:
        response = await client.get("/select-one")

    assert response.json() == {"value": 1}


@pytest.mark.integration
async def test_uncommitted_work_is_rolled_back_when_the_request_ends(
    engine: AsyncEngine, make_client: ClientFactory
) -> None:
    async with make_client(session_app(engine)) as client:
        response = await client.post("/workspaces-without-commit")

    async with create_session_factory(engine)() as session:
        count = await session.scalar(  # other tests commit workspaces; count only ours
            select(func.count()).select_from(Workspace).where(Workspace.name == UNCOMMITTED_NAME)
        )
    assert response.json() == {"status": "flushed"}
    assert count == 0


@pytest.mark.integration
async def test_sessions_are_returned_to_the_pool(
    engine: AsyncEngine, make_client: ClientFactory
) -> None:
    async with make_client(session_app(engine)) as client:
        for _ in range(3):
            await client.get("/select-one")

    assert engine.pool.checkedout() == 0  # type: ignore[attr-defined]
