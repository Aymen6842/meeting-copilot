from collections.abc import Mapping
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.core.dependencies import AppSettings
from app.health.schemas import HealthResponse
from app.health.service import DependencyCheck, check_health

router = APIRouter(tags=["health"])


def get_dependency_checks(request: Request) -> Mapping[str, DependencyCheck]:
    checks: Mapping[str, DependencyCheck] = request.app.state.dependency_checks
    return checks


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthResponse}},
    summary="Report whether Postgres, Redis and object storage are reachable",
)
async def health(
    response: Response,
    settings: AppSettings,
    checks: Annotated[Mapping[str, DependencyCheck], Depends(get_dependency_checks)],
) -> HealthResponse:
    report = await check_health(checks, settings.health.check_timeout_ms)
    if report.status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return report
