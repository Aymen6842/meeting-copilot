"""Meeting tables. Private to the meetings module: other modules use `app.meetings.service`.

`workspaces.id` and `users.id` are referenced by foreign key only; this module never imports
the auth models or queries their tables.
"""

import datetime
import uuid
from typing import Literal, get_args

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, created_at_column, uuid_primary_key

MeetingStatus = Literal["awaiting_upload", "uploaded", "probing", "ready", "failed"]
MEETING_STATUSES: tuple[str, ...] = get_args(MeetingStatus)

LanguageHint = Literal["auto", "en", "fr", "ar-tn", "mixed"]
LANGUAGE_HINTS: tuple[str, ...] = get_args(LanguageHint)

# "original" is the uploaded file, never modified; "normalized" is the 16 kHz mono copy.
AssetKind = Literal["original", "normalized"]
ASSET_KINDS: tuple[str, ...] = get_args(AssetKind)

AudioFormat = Literal["wav", "mp3", "m4a", "mp4", "webm"]
AUDIO_FORMATS: tuple[str, ...] = get_args(AudioFormat)

MAX_TITLE_LENGTH = 200
MAX_CONTENT_TYPE_LENGTH = 100
MAX_STORAGE_KEY_LENGTH = 512


def sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class Meeting(Base):
    __tablename__ = "meetings"
    __table_args__ = (
        CheckConstraint("length(trim(title)) > 0", name="title_not_blank"),
        CheckConstraint(f"status IN ({sql_list(MEETING_STATUSES)})", name="status_valid"),
        CheckConstraint(
            f"language_hint IN ({sql_list(LANGUAGE_HINTS)})", name="language_hint_valid"
        ),
        # Spec: an upload requires confirming that every participant consented.
        CheckConstraint("consent_confirmed", name="consent_required"),
        CheckConstraint("duration_ms IS NULL OR duration_ms > 0", name="duration_positive"),
        # Listing a workspace's meetings, newest first (ids are time-ordered UUIDv7).
        Index("ix_meetings_workspace_id_id", "workspace_id", "id"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    # Kept when the user is deleted: the meeting belongs to the workspace.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str] = mapped_column(String(MAX_TITLE_LENGTH))
    language_hint: Mapped[LanguageHint] = mapped_column(String(10))
    status: Mapped[MeetingStatus] = mapped_column(
        String(20), server_default=text("'awaiting_upload'")
    )
    recorded_at: Mapped[datetime.datetime | None]
    # Integer milliseconds (ADR-0003). Measured by the probe job; empty until then.
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    consent_confirmed: Mapped[bool]
    consent_confirmed_at: Mapped[datetime.datetime] = created_at_column()
    created_at: Mapped[datetime.datetime] = created_at_column()


class AudioAsset(Base):
    """A file in object storage. The original is never modified."""

    __tablename__ = "audio_assets"
    __table_args__ = (
        UniqueConstraint("meeting_id", "kind", name="uq_audio_assets_meeting_id_kind"),
        CheckConstraint(f"kind IN ({sql_list(ASSET_KINDS)})", name="kind_valid"),
        CheckConstraint(f"format IN ({sql_list(AUDIO_FORMATS)})", name="format_valid"),
        CheckConstraint("size_bytes IS NULL OR size_bytes > 0", name="size_positive"),
        CheckConstraint("sample_rate IS NULL OR sample_rate > 0", name="sample_rate_positive"),
        CheckConstraint("sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'", name="sha256_hex"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    meeting_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"))
    kind: Mapped[AssetKind] = mapped_column(String(20))
    storage_key: Mapped[str] = mapped_column(String(MAX_STORAGE_KEY_LENGTH), unique=True)
    format: Mapped[AudioFormat] = mapped_column(String(10))
    content_type: Mapped[str] = mapped_column(String(MAX_CONTENT_TYPE_LENGTH))
    # Set when the upload is confirmed; the probe job fills sample_rate and sha256.
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sample_rate: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime.datetime] = created_at_column()
