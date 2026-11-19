"""FastAPI dependencies shared by every module's routes."""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import get_session
from app.core.rate_limit import RateLimiter
from app.core.storage import ObjectStorage


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_rate_limiter(request: Request) -> RateLimiter:
    limiter: RateLimiter = request.app.state.rate_limiter
    return limiter


def get_storage(request: Request) -> ObjectStorage:
    storage: ObjectStorage = request.app.state.storage
    return storage


def client_ip(request: Request) -> str:
    """The direct peer address. X-Forwarded-For is ignored: any client can forge it. Behind a
    reverse proxy, configure uvicorn's --forwarded-allow-ips instead."""
    return request.client.host if request.client else "unknown"


AppSettings = Annotated[Settings, Depends(get_settings)]
DbSession = Annotated[AsyncSession, Depends(get_session)]
Limiter = Annotated[RateLimiter, Depends(get_rate_limiter)]
Storage = Annotated[ObjectStorage, Depends(get_storage)]
