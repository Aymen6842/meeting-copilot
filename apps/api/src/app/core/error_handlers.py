"""The one place where exceptions become HTTP responses.

Every error response has the same shape: `{"error": {"code", "message", "details"}}`.
"""

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException

from app.core.errors import (
    AuthenticationError,
    ConflictError,
    DomainError,
    DomainValidationError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitExceededError,
)

STATUS_BY_DOMAIN_ERROR: dict[type[DomainError], int] = {
    NotFoundError: HTTPStatus.NOT_FOUND,
    ConflictError: HTTPStatus.CONFLICT,
    PermissionDeniedError: HTTPStatus.FORBIDDEN,
    DomainValidationError: HTTPStatus.UNPROCESSABLE_ENTITY,
    AuthenticationError: HTTPStatus.UNAUTHORIZED,
    RateLimitExceededError: HTTPStatus.TOO_MANY_REQUESTS,
}

# Fields of a validation error that are safe to return: never "input" (it may be a password)
# and never "ctx" (it may hold arbitrary objects).
SAFE_VALIDATION_FIELDS = ("loc", "msg", "type")


class ValidationIssue(BaseModel):
    loc: list[str | int]
    msg: str
    type: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[ValidationIssue] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


def status_for(error: DomainError) -> int:
    for error_type in type(error).__mro__:
        if error_type in STATUS_BY_DOMAIN_ERROR:
            return STATUS_BY_DOMAIN_ERROR[error_type]
    return HTTPStatus.BAD_REQUEST


def error_response(
    status_code: int, code: str, message: str, details: list[ValidationIssue] | None = None
) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message, details=details))
    return JSONResponse(status_code=status_code, content=body.model_dump(exclude_none=True))


def safe_validation_issues(
    errors: list[dict[str, Any]],  # Any: pydantic's error dicts are loosely typed
) -> list[ValidationIssue]:
    return [
        ValidationIssue(**{field: error[field] for field in SAFE_VALIDATION_FIELDS})
        for error in errors
    ]


async def handle_domain_error(request: Request, error: DomainError) -> JSONResponse:
    response = error_response(status_for(error), error.code, error.message)
    if isinstance(error, RateLimitExceededError):
        response.headers["Retry-After"] = str(error.retry_after_seconds)
    return response


async def handle_request_validation_error(
    request: Request, error: RequestValidationError
) -> JSONResponse:
    return error_response(
        HTTPStatus.UNPROCESSABLE_ENTITY,
        "validation_failed",
        "The request is invalid.",
        safe_validation_issues(list(error.errors())),
    )


async def handle_http_exception(request: Request, error: HTTPException) -> JSONResponse:
    status = HTTPStatus(error.status_code)
    code = status.phrase.lower().replace(" ", "_")
    response = error_response(error.status_code, code, status.phrase + ".")
    if error.headers:
        response.headers.update(error.headers)
    return response


async def handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
    # The traceback is logged by the request middleware, which knows the request id.
    return error_response(
        HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", "An unexpected error occurred."
    )


def register_error_handlers(app: FastAPI) -> None:
    app.exception_handler(DomainError)(handle_domain_error)
    app.exception_handler(RequestValidationError)(handle_request_validation_error)
    app.exception_handler(HTTPException)(handle_http_exception)
    app.exception_handler(Exception)(handle_unexpected_error)
