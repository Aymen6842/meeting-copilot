from typing import Any

from fastapi import APIRouter, Request, Response, status

from app.auth import service as auth_service
from app.auth.schemas import (
    AccountResponse,
    LoginRequest,
    RegisterRequest,
    UserResponse,
    WorkspaceResponse,
)
from app.core.config import AuthSettings
from app.core.dependencies import AppSettings, DbSession, Limiter, client_ip
from app.core.error_handlers import ErrorResponse
from app.core.security import CurrentPrincipal

router = APIRouter(tags=["auth"])

# Any: FastAPI's OpenAPI response specification is an untyped mapping.
ResponseSpecs = dict[int | str, dict[str, Any]]

UNAUTHENTICATED: ResponseSpecs = {status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse}}
RATE_LIMITED: ResponseSpecs = {status.HTTP_429_TOO_MANY_REQUESTS: {"model": ErrorResponse}}


def account_response(account: auth_service.Account) -> AccountResponse:
    return AccountResponse(
        user=UserResponse(id=account.user_id, email=account.email, created_at=account.created_at),
        workspace=WorkspaceResponse(
            id=account.workspace_id, name=account.workspace_name, role=account.role
        ),
    )


def set_session_cookie(
    response: Response, settings: AuthSettings, new: auth_service.NewSession
) -> None:
    response.set_cookie(
        key=settings.cookie_name,
        value=new.token,
        max_age=settings.session_ttl_hours * 3600,
        expires=new.expires_at,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response, settings: AuthSettings) -> None:
    response.delete_cookie(
        key=settings.cookie_name,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


@router.post(
    "/auth/register",
    status_code=status.HTTP_201_CREATED,
    responses={status.HTTP_409_CONFLICT: {"model": ErrorResponse}, **RATE_LIMITED},
    summary="Create an account and its personal workspace (does not log in)",
)
async def register(
    body: RegisterRequest,
    request: Request,
    session: DbSession,
    settings: AppSettings,
    limiter: Limiter,
) -> AccountResponse:
    account = await auth_service.register(
        session, limiter, settings.auth, client_ip(request), body.email, body.password
    )
    return account_response(account)


@router.post(
    "/auth/login",
    responses={**UNAUTHENTICATED, **RATE_LIMITED},
    summary="Log in; sets the session cookie",
)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    limiter: Limiter,
) -> AccountResponse:
    new_session = await auth_service.login(
        session, limiter, settings.auth, client_ip(request), body.email, body.password
    )
    set_session_cookie(response, settings.auth, new_session)
    return account_response(new_session.account)


@router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=UNAUTHENTICATED,
    summary="End the current session and clear the cookie",
)
async def logout(
    principal: CurrentPrincipal,  # only a valid session can be ended
    request: Request,
    session: DbSession,
    settings: AppSettings,
) -> Response:
    await auth_service.logout(session, request.cookies[settings.auth.cookie_name])
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookie(response, settings.auth)
    return response


@router.get("/me", responses=UNAUTHENTICATED, summary="The current user and workspace")
async def me(principal: CurrentPrincipal, session: DbSession) -> AccountResponse:
    return account_response(await auth_service.get_account(session, principal))
