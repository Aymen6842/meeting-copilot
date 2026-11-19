"""Meeting tables against real Postgres: the constraints that protect data."""

import uuid

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.meetings.models import AudioAsset, Meeting

pytestmark = pytest.mark.integration


async def insert_returning_id(session: AsyncSession, sql: str, **parameters: str) -> uuid.UUID:
    """Workspaces and users belong to the auth module; create them without its models."""
    new_id = await session.scalar(text(sql), parameters)
    assert isinstance(new_id, uuid.UUID)
    return new_id


async def new_workspace(session: AsyncSession) -> uuid.UUID:
    return await insert_returning_id(
        session, "INSERT INTO workspaces (name) VALUES ('Test workspace') RETURNING id"
    )


async def new_user(session: AsyncSession) -> uuid.UUID:
    return await insert_returning_id(
        session,
        "INSERT INTO users (email, password_hash) VALUES (:email, 'x') RETURNING id",
        email=f"user-{uuid.uuid4().hex[:12]}@example.com",
    )


async def add_meeting(session: AsyncSession, **overrides: object) -> Meeting:
    values: dict[str, object] = {
        "workspace_id": await new_workspace(session),
        "created_by": await new_user(session),
        "title": "Weekly sync",
        "language_hint": "en",
        "consent_confirmed": True,
    }
    meeting = Meeting(**(values | overrides))
    session.add(meeting)
    await session.flush()
    await session.refresh(meeting)
    return meeting


def original(meeting: Meeting, key: str | None = None) -> AudioAsset:
    return AudioAsset(
        meeting_id=meeting.id,
        kind="original",
        storage_key=key or f"workspaces/x/meetings/{meeting.id}/original.wav",
        format="wav",
        content_type="audio/wav",
    )


async def test_new_meeting_awaits_its_upload(db_session: AsyncSession) -> None:
    meeting = await add_meeting(db_session)

    assert meeting.status == "awaiting_upload"
    assert meeting.duration_ms is None
    assert meeting.consent_confirmed_at.tzinfo is not None


async def test_consent_is_required(db_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError, match="ck_meetings_consent_required"):
        await add_meeting(db_session, consent_confirmed=False)


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"status": "deleted"}, "ck_meetings_status_valid"),
        ({"language_hint": "de"}, "ck_meetings_language_hint_valid"),
        ({"title": "  "}, "ck_meetings_title_not_blank"),
        ({"duration_ms": 0}, "ck_meetings_duration_positive"),
    ],
)
async def test_meeting_values_are_checked(
    db_session: AsyncSession, overrides: dict[str, object], constraint: str
) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        await add_meeting(db_session, **overrides)


async def test_a_meeting_has_one_original_file(db_session: AsyncSession) -> None:
    meeting = await add_meeting(db_session)
    db_session.add(original(meeting, "key-a"))
    await db_session.flush()
    db_session.add(original(meeting, "key-b"))

    with pytest.raises(IntegrityError, match="uq_audio_assets_meeting_id_kind"):
        await db_session.flush()


async def test_storage_keys_are_unique(db_session: AsyncSession) -> None:
    first, second = await add_meeting(db_session), await add_meeting(db_session)
    db_session.add(original(first, "same-key"))
    await db_session.flush()
    db_session.add(original(second, "same-key"))

    with pytest.raises(IntegrityError, match="uq_audio_assets_storage_key"):
        await db_session.flush()


@pytest.mark.parametrize(
    ("field", "value", "constraint"),
    [
        ("format", "flac", "ck_audio_assets_format_valid"),
        ("kind", "copy", "ck_audio_assets_kind_valid"),
        ("size_bytes", 0, "ck_audio_assets_size_positive"),
        ("sample_rate", 0, "ck_audio_assets_sample_rate_positive"),
        ("sha256", "not-a-hash", "ck_audio_assets_sha256_hex"),
    ],
)
async def test_asset_values_are_checked(
    db_session: AsyncSession, field: str, value: object, constraint: str
) -> None:
    asset = original(await add_meeting(db_session))
    setattr(asset, field, value)
    db_session.add(asset)

    with pytest.raises(IntegrityError, match=constraint):
        await db_session.flush()


async def test_deleting_a_workspace_deletes_its_meetings_and_files(
    db_session: AsyncSession,
) -> None:
    meeting = await add_meeting(db_session)
    db_session.add(original(meeting))
    await db_session.flush()

    await db_session.execute(
        text("DELETE FROM workspaces WHERE id = :id"), {"id": meeting.workspace_id}
    )

    remaining = await db_session.scalar(
        select(func.count()).select_from(AudioAsset).where(AudioAsset.meeting_id == meeting.id)
    )
    assert remaining == 0
    db_session.expunge_all()
    assert await db_session.get(Meeting, meeting.id) is None


async def test_deleting_the_creator_keeps_the_meeting(db_session: AsyncSession) -> None:
    meeting = await add_meeting(db_session)

    await db_session.execute(text("DELETE FROM users WHERE id = :id"), {"id": meeting.created_by})

    db_session.expunge_all()
    kept = await db_session.get(Meeting, meeting.id)
    assert kept is not None
    assert kept.created_by is None


async def test_deleting_a_meeting_deletes_its_files(db_session: AsyncSession) -> None:
    meeting = await add_meeting(db_session)
    db_session.add(original(meeting))
    await db_session.flush()

    await db_session.execute(delete(Meeting).where(Meeting.id == meeting.id))

    remaining = await db_session.scalar(
        select(func.count()).select_from(AudioAsset).where(AudioAsset.meeting_id == meeting.id)
    )
    assert remaining == 0
