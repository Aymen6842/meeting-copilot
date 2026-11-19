"""Auth tables. Private to the auth module: other modules use `app.auth.service`.

Other modules may reference `workspaces.id` in a foreign key by table name, but never import
these classes.
"""

import datetime
import uuid
from typing import Literal, get_args

from sqlalchemy import CheckConstraint, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, created_at_column, uuid_primary_key

Role = Literal["owner", "member"]
ROLES: tuple[str, ...] = get_args(Role)

MAX_EMAIL_LENGTH = 320  # RFC 5321: 64 (local part) + 1 + 255 (domain)
MAX_WORKSPACE_NAME_LENGTH = 200


class Workspace(Base):
    __tablename__ = "workspaces"
    __table_args__ = (CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),)

    id: Mapped[uuid.UUID] = uuid_primary_key()
    name: Mapped[str] = mapped_column(String(MAX_WORKSPACE_NAME_LENGTH))
    created_at: Mapped[datetime.datetime] = created_at_column()


class User(Base):
    __tablename__ = "users"
    # The service lower-cases emails; the database refuses anything else, so uniqueness is
    # case-insensitive without a functional index.
    __table_args__ = (CheckConstraint("email = lower(email)", name="email_lowercase"),)

    id: Mapped[uuid.UUID] = uuid_primary_key()
    email: Mapped[str] = mapped_column(String(MAX_EMAIL_LENGTH), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = created_at_column()


ROLE_LIST_SQL = ", ".join(f"'{role}'" for role in ROLES)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (CheckConstraint(f"role IN ({ROLE_LIST_SQL})", name="role_valid"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    # Indexed on its own: the primary key's leading column is workspace_id, so "which
    # workspaces does this user belong to" would otherwise scan the table.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role: Mapped[Role] = mapped_column(String(20))
    created_at: Mapped[datetime.datetime] = created_at_column()


TOKEN_HASH_LENGTH = 64  # hex SHA-256


class AuthSession(Base):
    """A login. The cookie holds a random token; only its SHA-256 hash is stored here, so a
    copy of the database contains no usable tokens. Logout deletes the row."""

    __tablename__ = "sessions"
    __table_args__ = (CheckConstraint("expires_at > created_at", name="expires_after_creation"),)

    id: Mapped[uuid.UUID] = uuid_primary_key()
    token_hash: Mapped[str] = mapped_column(String(TOKEN_HASH_LENGTH), unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # The workspace this session acts in; checked against memberships on every request.
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime.datetime] = created_at_column()
    expires_at: Mapped[datetime.datetime]
