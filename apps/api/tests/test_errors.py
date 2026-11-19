from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel

from app.core.error_handlers import register_error_handlers
from app.core.errors import (
    ConflictError,
    DomainError,
    DomainValidationError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.logging import add_request_logging, configure_logging

ClientFactory = Callable[[FastAPI], AbstractAsyncContextManager[httpx.AsyncClient]]
LogParser = Callable[[str], list[dict[str, object]]]


class LoginRequest(BaseModel):
    email: str
    password: str


def error_app(error: Exception | None = None) -> FastAPI:
    configure_logging("INFO")
    app = FastAPI()
    register_error_handlers(app)
    add_request_logging(app)

    @app.get("/fail")
    async def fail() -> None:
        assert error is not None
        raise error

    @app.post("/login")
    async def login(body: LoginRequest) -> dict[str, str]:
        return {"email": body.email}

    return app


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (NotFoundError("Meeting not found."), 404, "not_found"),
        (ConflictError("Already exists."), 409, "conflict"),
        (PermissionDeniedError("Not yours."), 403, "permission_denied"),
        (DomainValidationError("Too long."), 422, "validation_failed"),
        (DomainError("Generic."), 400, "domain_error"),
    ],
)
async def test_domain_errors_map_to_status_and_envelope(
    error: DomainError, status_code: int, code: str, make_client: ClientFactory
) -> None:
    async with make_client(error_app(error)) as client:
        response = await client.get("/fail")

    assert response.status_code == status_code
    assert response.json() == {"error": {"code": code, "message": error.message}}


async def test_subclass_of_a_mapped_error_uses_its_parent_status(
    make_client: ClientFactory,
) -> None:
    class MeetingNotFoundError(NotFoundError):
        code = "meeting_not_found"

    async with make_client(error_app(MeetingNotFoundError("No such meeting."))) as client:
        response = await client.get("/fail")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "meeting_not_found"


async def test_validation_error_lists_fields_but_never_echoes_input(
    make_client: ClientFactory,
) -> None:
    async with make_client(error_app()) as client:
        response = await client.post(
            "/login", json={"email": "user@example.com", "password": ["hunter2-secret"]}
        )

    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == "validation_failed"
    assert body["error"]["details"][0]["loc"] == ["body", "password"]
    assert "hunter2-secret" not in response.text


async def test_unknown_route_uses_the_envelope(make_client: ClientFactory) -> None:
    async with make_client(error_app()) as client:
        response = await client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Not Found."}}


async def test_wrong_method_uses_the_envelope(make_client: ClientFactory) -> None:
    async with make_client(error_app()) as client:
        response = await client.delete("/login")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"
    assert "allow" in response.headers


async def test_unexpected_error_is_a_generic_500_and_logged_with_request_id(
    capsys: pytest.CaptureFixture[str], make_client: ClientFactory, parse_log_lines: LogParser
) -> None:
    async with make_client(error_app(RuntimeError("db password is hunter2"))) as client:
        response = await client.get("/fail")

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "An unexpected error occurred."}
    }
    assert "hunter2" not in response.text
    [error_line] = [
        line for line in parse_log_lines(capsys.readouterr().out) if "exception" in line
    ]
    assert "RuntimeError" in str(error_line["exception"])
    assert error_line["request_id"]
