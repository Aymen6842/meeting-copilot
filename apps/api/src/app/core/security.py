"""Authentication for routes. Every route requires `CurrentPrincipal` unless it is on the
public allowlist checked by the route-enumeration test (`tests/test_route_security.py`)."""

from typing import Annotated

from fastapi import Depends, Request

from app.auth import service as auth_service
from app.auth.service import Principal
from app.core.dependencies import AppSettings, DbSession


async def require_principal(
    request: Request, session: DbSession, settings: AppSettings
) -> Principal:
    token = request.cookies.get(settings.auth.cookie_name)
    return await auth_service.authenticate(session, token)


CurrentPrincipal = Annotated[Principal, Depends(require_principal)]
