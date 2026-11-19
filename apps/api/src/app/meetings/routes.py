import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, status

from app.core.dependencies import AppSettings, DbSession, Storage
from app.core.error_handlers import ErrorResponse
from app.core.security import CurrentPrincipal
from app.meetings import service as meetings_service
from app.meetings.schemas import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    CreateMeetingRequest,
    CreateMeetingResponse,
    MeetingListResponse,
    MeetingResponse,
    UploadInstructions,
)

router = APIRouter(prefix="/meetings", tags=["meetings"])

# Any: FastAPI's OpenAPI response specification is an untyped mapping.
ResponseSpecs = dict[int | str, dict[str, Any]]


def error_responses(*status_codes: int) -> ResponseSpecs:
    return {
        code: {"model": ErrorResponse} for code in (status.HTTP_401_UNAUTHORIZED, *status_codes)
    }


def meeting_response(meeting: meetings_service.MeetingDetails) -> MeetingResponse:
    return MeetingResponse.model_validate(meeting, from_attributes=True)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(
        status.HTTP_422_UNPROCESSABLE_CONTENT, status.HTTP_503_SERVICE_UNAVAILABLE
    ),
    summary="Create a meeting; returns a pre-signed URL to upload its recording",
)
async def create_meeting(
    body: CreateMeetingRequest,
    principal: CurrentPrincipal,
    session: DbSession,
    storage: Storage,
    settings: AppSettings,
) -> CreateMeetingResponse:
    created = await meetings_service.create_meeting(
        session,
        storage,
        settings.meetings,
        principal,
        meetings_service.NewMeeting(**body.model_dump()),
    )
    upload = created.upload
    return CreateMeetingResponse(
        meeting=meeting_response(created.meeting),
        upload=UploadInstructions(
            method="PUT", url=upload.url, headers=dict(upload.headers), expires_at=upload.expires_at
        ),
    )


@router.post(
    "/{meeting_id}/upload-complete",
    responses=error_responses(
        status.HTTP_404_NOT_FOUND,
        status.HTTP_409_CONFLICT,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        status.HTTP_503_SERVICE_UNAVAILABLE,
    ),
    summary="Confirm the recording was uploaded; checks the stored file",
)
async def complete_upload(
    meeting_id: uuid.UUID,
    principal: CurrentPrincipal,
    session: DbSession,
    storage: Storage,
    settings: AppSettings,
) -> MeetingResponse:
    meeting = await meetings_service.complete_upload(
        session, storage, settings.meetings, principal, meeting_id
    )
    return meeting_response(meeting)


@router.get("", responses=error_responses(), summary="The workspace's meetings, newest first")
async def list_meetings(
    principal: CurrentPrincipal,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MeetingListResponse:
    page = await meetings_service.list_meetings(session, principal, limit, offset)
    return MeetingListResponse(
        items=[meeting_response(meeting) for meeting in page.items], next_offset=page.next_offset
    )


@router.get(
    "/{meeting_id}",
    responses=error_responses(status.HTTP_404_NOT_FOUND),
    summary="One meeting with its recording's details",
)
async def get_meeting(
    meeting_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession
) -> MeetingResponse:
    return meeting_response(await meetings_service.get_meeting(session, principal, meeting_id))
