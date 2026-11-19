"""Every route requires authentication unless it is on this explicit allowlist.

Adding a public route means adding it here, in review, on purpose.
"""

import re
import uuid
from collections.abc import Iterator

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import iter_route_contexts

from app.core.config import Settings
from app.core.security import require_principal
from app.main import create_app

PUBLIC_ROUTES = {
    ("GET", "/health"),
    ("POST", "/auth/register"),
    ("POST", "/auth/login"),
    # Interactive API docs (milestone 1, task 3).
    ("GET", "/docs"),
    ("GET", "/docs/oauth2-redirect"),
    ("GET", "/openapi.json"),
    ("GET", "/redoc"),
}

NOT_AUTHENTICATED = {"error": {"code": "not_authenticated", "message": "Authentication required."}}


def endpoints(app: FastAPI) -> Iterator[tuple[str, str, Dependant | None]]:
    """(method, full path, effective dependencies) for every route, included routers too.

    `iter_route_contexts` descends into included routers (which, since FastAPI 0.165, are not
    flattened into `app.routes`) and applies their prefixes and dependencies. Plain Starlette
    routes, such as the docs, have no dependencies. HEAD is implied by GET and skipped."""
    for context in iter_route_contexts(app.routes):
        assert context.path is not None
        dependant: Dependant | None = getattr(context, "dependant", None)
        for method in sorted(context.methods or set()):
            if method != "HEAD":
                yield method, context.path, dependant


def requires_principal(dependant: Dependant) -> bool:
    return any(
        dependency.call is require_principal or requires_principal(dependency)
        for dependency in dependant.dependencies
    )


def protected_endpoints(app: FastAPI) -> list[tuple[str, str]]:
    return sorted(
        (method, path) for method, path, _ in endpoints(app) if (method, path) not in PUBLIC_ROUTES
    )


def test_allowlist_has_no_stale_entries(settings: Settings) -> None:
    existing = {(method, path) for method, path, _ in endpoints(create_app(settings))}

    assert existing >= PUBLIC_ROUTES


def test_there_are_protected_routes_to_check(settings: Settings) -> None:
    # Guards the guard: if enumeration silently found nothing, every other test would pass.
    assert {("GET", "/me"), ("POST", "/auth/logout")} <= set(
        protected_endpoints(create_app(settings))
    )


def unguarded_endpoints(app: FastAPI) -> list[tuple[str, str]]:
    return [
        (method, path)
        for method, path, dependant in endpoints(app)
        if (method, path) not in PUBLIC_ROUTES
        and not (dependant is not None and requires_principal(dependant))
    ]


def test_every_non_public_route_depends_on_the_current_principal(settings: Settings) -> None:
    unguarded = unguarded_endpoints(create_app(settings))

    assert unguarded == [], f"routes without CurrentPrincipal: {unguarded}"


def test_the_guard_catches_an_unguarded_route(settings: Settings) -> None:
    app = create_app(settings)
    router = APIRouter(prefix="/forgotten")

    @router.get("/items/{item_id}")
    async def forgotten_auth(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    app.include_router(router)

    assert unguarded_endpoints(app) == [("GET", "/forgotten/items/{item_id}")]


@pytest.mark.integration
async def test_every_non_public_route_refuses_anonymous_requests(
    settings: Settings, api_client: httpx.AsyncClient
) -> None:
    for method, path in protected_endpoints(create_app(settings)):
        concrete_path = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), path)

        response = await api_client.request(method, concrete_path)

        assert response.status_code == 401, f"{method} {path} -> {response.status_code}"
        assert response.json() == NOT_AUTHENTICATED, f"{method} {path}"
