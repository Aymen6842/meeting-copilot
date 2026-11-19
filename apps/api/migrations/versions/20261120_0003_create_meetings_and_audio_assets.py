"""Create meetings and audio assets.

A meeting belongs to a workspace and records that every participant consented. Its audio
files live in object storage; `audio_assets` holds their keys and properties, one original
and later one normalized copy per meeting. Durations are integer milliseconds (ADR-0003).

Revision ID: 0003
Revises: 0002
Create date: 2026-11-20 00:10:36.395390
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "meetings",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("language_hint", sa.String(length=10), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'awaiting_upload'"),
            nullable=False,
        ),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("consent_confirmed", sa.Boolean(), nullable=False),
        sa.Column(
            "consent_confirmed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "language_hint IN ('auto', 'en', 'fr', 'ar-tn', 'mixed')",
            name=op.f("ck_meetings_language_hint_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('awaiting_upload', 'uploaded', 'probing', 'ready', 'failed')",
            name=op.f("ck_meetings_status_valid"),
        ),
        sa.CheckConstraint("consent_confirmed", name=op.f("ck_meetings_consent_required")),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms > 0", name=op.f("ck_meetings_duration_positive")
        ),
        sa.CheckConstraint("length(trim(title)) > 0", name=op.f("ck_meetings_title_not_blank")),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_meetings_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_meetings_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_meetings")),
    )
    op.create_index(op.f("ix_meetings_created_by"), "meetings", ["created_by"], unique=False)
    op.create_index("ix_meetings_workspace_id_id", "meetings", ["workspace_id", "id"], unique=False)
    op.create_table(
        "audio_assets",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("meeting_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("format", sa.String(length=10), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sample_rate", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "format IN ('wav', 'mp3', 'm4a', 'mp4', 'webm')",
            name=op.f("ck_audio_assets_format_valid"),
        ),
        sa.CheckConstraint(
            "kind IN ('original', 'normalized')", name=op.f("ck_audio_assets_kind_valid")
        ),
        sa.CheckConstraint(
            "sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_audio_assets_sha256_hex")
        ),
        sa.CheckConstraint(
            "sample_rate IS NULL OR sample_rate > 0",
            name=op.f("ck_audio_assets_sample_rate_positive"),
        ),
        sa.CheckConstraint(
            "size_bytes IS NULL OR size_bytes > 0", name=op.f("ck_audio_assets_size_positive")
        ),
        sa.ForeignKeyConstraint(
            ["meeting_id"],
            ["meetings.id"],
            name=op.f("fk_audio_assets_meeting_id_meetings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audio_assets")),
        sa.UniqueConstraint("meeting_id", "kind", name="uq_audio_assets_meeting_id_kind"),
        sa.UniqueConstraint("storage_key", name=op.f("uq_audio_assets_storage_key")),
    )


def downgrade() -> None:
    op.drop_table("audio_assets")
    op.drop_index("ix_meetings_workspace_id_id", table_name="meetings")
    op.drop_index(op.f("ix_meetings_created_by"), table_name="meetings")
    op.drop_table("meetings")
