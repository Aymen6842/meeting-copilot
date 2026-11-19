"""Data access for the meetings module. Private: only `app.meetings.service` uses it.

Every query takes `workspace_id` and filters on it, so a meeting from another workspace is
indistinguishable from one that does not exist.
"""

import datetime
import uuid

from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.meetings.models import AudioAsset, AudioFormat, LanguageHint, Meeting

MeetingRow = tuple[Meeting, AudioAsset | None]


async def add_meeting(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    created_by: uuid.UUID,
    title: str,
    language_hint: LanguageHint,
    recorded_at: datetime.datetime | None,
) -> Meeting:
    meeting = Meeting(
        workspace_id=workspace_id,
        created_by=created_by,
        title=title,
        language_hint=language_hint,
        recorded_at=recorded_at,
        consent_confirmed=True,
    )
    session.add(meeting)
    await session.flush()
    await session.refresh(meeting)  # load server defaults: status, timestamps
    return meeting


async def add_original_asset(
    session: AsyncSession,
    meeting_id: uuid.UUID,
    storage_key: str,
    audio_format: AudioFormat,
    content_type: str,
) -> AudioAsset:
    asset = AudioAsset(
        meeting_id=meeting_id,
        kind="original",
        storage_key=storage_key,
        format=audio_format,
        content_type=content_type,
    )
    session.add(asset)
    await session.flush()
    return asset


def workspace_meetings(workspace_id: uuid.UUID) -> Select[Meeting, AudioAsset]:
    """The workspace's meetings, each with its original file (if any). Every read starts here."""
    return (
        select(Meeting, AudioAsset)
        .outerjoin(
            AudioAsset, (AudioAsset.meeting_id == Meeting.id) & (AudioAsset.kind == "original")
        )
        .where(Meeting.workspace_id == workspace_id)
        # Sessions keep objects after commit; always load the stored values.
        .execution_options(populate_existing=True)
    )


async def get_meeting(
    session: AsyncSession, workspace_id: uuid.UUID, meeting_id: uuid.UUID
) -> MeetingRow | None:
    statement = workspace_meetings(workspace_id).where(Meeting.id == meeting_id)
    row = (await session.execute(statement)).one_or_none()
    return None if row is None else (row[0], row[1])


async def list_meetings(
    session: AsyncSession, workspace_id: uuid.UUID, limit: int, offset: int
) -> list[MeetingRow]:
    """Newest first. Ids are time-ordered UUIDv7, so ordering by id is creation order."""
    statement = (
        workspace_meetings(workspace_id).order_by(Meeting.id.desc()).limit(limit).offset(offset)
    )
    return [(row[0], row[1]) for row in await session.execute(statement)]


async def mark_uploaded(
    session: AsyncSession, workspace_id: uuid.UUID, meeting_id: uuid.UUID, size_bytes: int
) -> bool:
    """Move the meeting from awaiting_upload to uploaded. Returns False if another request
    already did, so only one caller ever acts on the transition."""
    moved = await session.scalar(
        update(Meeting)
        .where(
            Meeting.workspace_id == workspace_id,
            Meeting.id == meeting_id,
            Meeting.status == "awaiting_upload",
        )
        .values(status="uploaded")
        .returning(Meeting.id)
    )
    if moved is None:
        return False
    await session.execute(
        update(AudioAsset)
        .where(AudioAsset.meeting_id == meeting_id, AudioAsset.kind == "original")
        .values(size_bytes=size_bytes)
    )
    return True
