import datetime
import uuid
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from app.meetings.models import (
    MAX_CONTENT_TYPE_LENGTH,
    MAX_TITLE_LENGTH,
    AudioFormat,
    LanguageHint,
    MeetingStatus,
)

MAX_FILENAME_LENGTH = 255
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100

Title = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TITLE_LENGTH)
]
Filename = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_FILENAME_LENGTH)
]
ContentType = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_CONTENT_TYPE_LENGTH),
]


class CreateMeetingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title
    language_hint: LanguageHint = "auto"
    consent_confirmed: bool = Field(
        description="Every participant agreed to be recorded. Must be true."
    )
    filename: Filename = Field(
        description="Name of the file to upload; its extension sets the format."
    )
    content_type: ContentType = Field(
        description="The file's type as the browser reports it; the upload must send the same."
    )
    size_bytes: Annotated[int, Field(gt=0, description="Size of the file to upload.")]
    duration_ms: Annotated[
        int | None, Field(gt=0, description="Duration read by the browser, if known.")
    ] = None
    recorded_at: AwareDatetime | None = None


class AudioFileResponse(BaseModel):
    format: AudioFormat
    content_type: str
    size_bytes: int | None


class MeetingResponse(BaseModel):
    id: uuid.UUID
    title: str
    language_hint: LanguageHint
    status: MeetingStatus
    recorded_at: datetime.datetime | None
    duration_ms: int | None
    consent_confirmed_at: datetime.datetime
    created_at: datetime.datetime
    audio: AudioFileResponse | None


class UploadInstructions(BaseModel):
    """Send exactly this request, with these headers, before `expires_at`."""

    method: Literal["PUT"]
    url: str
    headers: dict[str, str]
    expires_at: datetime.datetime


class CreateMeetingResponse(BaseModel):
    meeting: MeetingResponse
    upload: UploadInstructions


class MeetingListResponse(BaseModel):
    items: list[MeetingResponse]
    next_offset: int | None = Field(
        description="Pass as `offset` for the next page; null at the end."
    )
