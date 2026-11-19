"""Meetings module public interface: create a meeting, confirm its upload, list and read.

The browser uploads the recording straight to object storage with a pre-signed URL; the API
never handles audio bytes. Every function is scoped to the caller's workspace: a meeting from
another workspace is reported as not found. Logs carry meeting ids, never titles or file names.
"""

import datetime
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import Principal
from app.core.config import MeetingsSettings
from app.core.errors import ConflictError, DomainValidationError, NotFoundError
from app.core.storage import ObjectStorage, PresignedRequest, meeting_original_key
from app.meetings import repository
from app.meetings.models import (
    AudioAsset,
    AudioFormat,
    LanguageHint,
    Meeting,
    MeetingStatus,
)

__all__ = [
    "FORMAT_CONTENT_TYPES",
    "AudioFile",
    "CreatedMeeting",
    "MeetingDetails",
    "MeetingPage",
    "NewMeeting",
    "complete_upload",
    "create_meeting",
    "get_meeting",
    "list_meetings",
]

logger = logging.getLogger(__name__)

# Content types browsers report for each accepted format. Parameters such as
# "; codecs=opus" are allowed and ignored here; the full value is signed into the upload URL.
FORMAT_CONTENT_TYPES: dict[AudioFormat, frozenset[str]] = {
    "wav": frozenset({"audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave"}),
    "mp3": frozenset({"audio/mpeg", "audio/mp3"}),
    "m4a": frozenset({"audio/mp4", "audio/x-m4a", "audio/m4a"}),
    "mp4": frozenset({"video/mp4", "audio/mp4"}),
    "webm": frozenset({"audio/webm", "video/webm"}),
}

CONSENT_REQUIRED = "Confirm that every participant agreed to be recorded."
MEETING_NOT_FOUND = "Meeting not found."
NO_UPLOAD_FOUND = "No uploaded file found. Upload the file, then try again."
EMPTY_UPLOAD = "The uploaded file is empty."
CONTENT_TYPE_MISMATCH = "The file type does not match the file name."

MS_PER_HOUR = 3_600_000
MS_PER_MINUTE = 60_000
BYTES_PER_GIB = 1024**3
BYTES_PER_MIB = 1024**2


@dataclass(frozen=True)
class AudioFile:
    format: AudioFormat
    content_type: str
    size_bytes: int | None  # known once the upload is confirmed


@dataclass(frozen=True)
class MeetingDetails:
    id: uuid.UUID
    title: str
    language_hint: LanguageHint
    status: MeetingStatus
    recorded_at: datetime.datetime | None
    duration_ms: int | None
    consent_confirmed_at: datetime.datetime
    created_at: datetime.datetime
    audio: AudioFile | None


@dataclass(frozen=True)
class NewMeeting:
    title: str
    language_hint: LanguageHint
    consent_confirmed: bool
    filename: str
    content_type: str
    size_bytes: int
    duration_ms: int | None  # as reported by the browser; the probe job measures the truth
    recorded_at: datetime.datetime | None


@dataclass(frozen=True)
class CreatedMeeting:
    meeting: MeetingDetails
    upload: PresignedRequest


@dataclass(frozen=True)
class MeetingPage:
    items: list[MeetingDetails]
    next_offset: int | None


async def create_meeting(
    session: AsyncSession,
    storage: ObjectStorage,
    settings: MeetingsSettings,
    principal: Principal,
    new: NewMeeting,
) -> CreatedMeeting:
    """Record the meeting and return a pre-signed URL for uploading its file."""
    if not new.consent_confirmed:
        raise DomainValidationError(CONSENT_REQUIRED)
    audio_format = file_format(new.filename, new.content_type)
    check_size(new.size_bytes, settings)
    if new.duration_ms is not None and new.duration_ms > settings.max_duration_ms:
        raise DomainValidationError(too_long_message(settings))

    meeting = await repository.add_meeting(
        session,
        workspace_id=principal.workspace_id,
        created_by=principal.user_id,
        title=new.title,
        language_hint=new.language_hint,
        recorded_at=new.recorded_at,
    )
    key = meeting_original_key(principal.workspace_id, meeting.id, audio_format)
    asset = await repository.add_original_asset(
        session, meeting.id, key, audio_format, new.content_type
    )
    await session.commit()

    logger.info("meeting created", extra={"meeting_id": str(meeting.id)})
    upload = storage.presigned_upload(key, new.content_type)
    return CreatedMeeting(meeting=details(meeting, asset), upload=upload)


async def complete_upload(
    session: AsyncSession,
    storage: ObjectStorage,
    settings: MeetingsSettings,
    principal: Principal,
    meeting_id: uuid.UUID,
) -> MeetingDetails:
    """Check the uploaded file and mark the meeting uploaded. Safe to call again."""
    meeting, asset = await require_meeting(session, principal, meeting_id)
    if meeting.status != "awaiting_upload" or asset is None:
        return details(meeting, asset)

    info = await storage.object_info(asset.storage_key)
    if info is None:
        raise ConflictError(NO_UPLOAD_FOUND)
    if info.size_bytes == 0 or info.size_bytes > settings.max_upload_bytes:
        await storage.delete(asset.storage_key)  # the meeting stays open for another upload
        raise DomainValidationError(
            EMPTY_UPLOAD if info.size_bytes == 0 else too_large_message(settings)
        )

    if await repository.mark_uploaded(session, principal.workspace_id, meeting.id, info.size_bytes):
        logger.info("meeting upload completed", extra={"meeting_id": str(meeting.id)})
    await session.commit()
    meeting, asset = await require_meeting(session, principal, meeting_id)
    return details(meeting, asset)


async def get_meeting(
    session: AsyncSession, principal: Principal, meeting_id: uuid.UUID
) -> MeetingDetails:
    meeting, asset = await require_meeting(session, principal, meeting_id)
    return details(meeting, asset)


async def list_meetings(
    session: AsyncSession, principal: Principal, limit: int, offset: int
) -> MeetingPage:
    """Newest first. `next_offset` is None on the last page."""
    rows = await repository.list_meetings(session, principal.workspace_id, limit + 1, offset)
    items = [details(meeting, asset) for meeting, asset in rows[:limit]]
    return MeetingPage(items=items, next_offset=offset + limit if len(rows) > limit else None)


async def require_meeting(
    session: AsyncSession, principal: Principal, meeting_id: uuid.UUID
) -> repository.MeetingRow:
    row = await repository.get_meeting(session, principal.workspace_id, meeting_id)
    if row is None:
        raise NotFoundError(MEETING_NOT_FOUND)
    return row


def file_format(filename: str, content_type: str) -> AudioFormat:
    """The format named by the file's extension, checked against its content type."""
    extension = filename.rsplit(".", 1)[-1].strip().lower() if "." in filename else ""
    for audio_format in FORMAT_CONTENT_TYPES:
        if audio_format == extension:
            break
    else:
        allowed = ", ".join(sorted(FORMAT_CONTENT_TYPES))
        raise DomainValidationError(f"Unsupported file format. Allowed formats: {allowed}.")
    base_type = content_type.split(";", 1)[0].strip().lower()
    if base_type not in FORMAT_CONTENT_TYPES[audio_format]:
        raise DomainValidationError(CONTENT_TYPE_MISMATCH)
    return audio_format


def check_size(size_bytes: int, settings: MeetingsSettings) -> None:
    if size_bytes > settings.max_upload_bytes:
        raise DomainValidationError(too_large_message(settings))


def too_large_message(settings: MeetingsSettings) -> str:
    return f"Files larger than {describe_bytes(settings.max_upload_bytes)} are not supported."


def too_long_message(settings: MeetingsSettings) -> str:
    return (
        f"Recordings longer than {describe_duration(settings.max_duration_ms)} are not supported."
    )


def describe_bytes(size: int) -> str:
    if size % BYTES_PER_GIB == 0:
        return f"{size // BYTES_PER_GIB} GiB"
    if size % BYTES_PER_MIB == 0:
        return f"{size // BYTES_PER_MIB} MiB"
    return f"{size} bytes"


def describe_duration(duration_ms: int) -> str:
    if duration_ms % MS_PER_HOUR == 0:
        hours = duration_ms // MS_PER_HOUR
        return f"{hours} hour" if hours == 1 else f"{hours} hours"
    if duration_ms % MS_PER_MINUTE == 0:
        return f"{duration_ms // MS_PER_MINUTE} minutes"
    return f"{duration_ms} ms"


def details(meeting: Meeting, asset: AudioAsset | None) -> MeetingDetails:
    audio = (
        None
        if asset is None
        else AudioFile(
            format=asset.format, content_type=asset.content_type, size_bytes=asset.size_bytes
        )
    )
    return MeetingDetails(
        id=meeting.id,
        title=meeting.title,
        language_hint=meeting.language_hint,
        status=meeting.status,
        recorded_at=meeting.recorded_at,
        duration_ms=meeting.duration_ms,
        consent_confirmed_at=meeting.consent_confirmed_at,
        created_at=meeting.created_at,
        audio=audio,
    )
