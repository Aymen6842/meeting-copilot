from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import httpx
from fastapi import FastAPI

from app.core.config import Settings
from app.main import create_app

ClientFactory = Callable[[FastAPI], AbstractAsyncContextManager[httpx.AsyncClient]]


async def test_interactive_docs_are_served(settings: Settings, make_client: ClientFactory) -> None:
    async with make_client(create_app(settings)) as client:
        response = await client.get("/docs")

    assert response.status_code == 200
    assert "swagger" in response.text.lower()


async def test_schema_documents_both_health_responses(
    settings: Settings, make_client: ClientFactory
) -> None:
    async with make_client(create_app(settings)) as client:
        schema = (await client.get("/openapi.json")).json()

    responses = schema["paths"]["/health"]["get"]["responses"]
    assert {"200", "503"} <= set(responses)
