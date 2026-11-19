from typing import Literal

from pydantic import BaseModel

CheckStatus = Literal["ok", "error"]


class DependencyStatus(BaseModel):
    status: CheckStatus
    duration_ms: int
    error: str | None = None  # error type or "timeout"; never the message, which may hold a URL


class HealthResponse(BaseModel):
    status: Literal["ok", "unavailable"]
    checks: dict[str, DependencyStatus]
