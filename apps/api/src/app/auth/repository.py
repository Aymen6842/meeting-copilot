"""Data access for the auth module. Private: only `app.auth.service` uses it."""

import datetime
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import AuthSession, Membership, Role, User, Workspace


@dataclass(frozen=True)
class SessionMembership:
    user_id: uuid.UUID
    workspace_id: uuid.UUID
    role: Role


@dataclass(frozen=True)
class AccountRecord:
    user_id: uuid.UUID
    email: str
    created_at: datetime.datetime
    workspace_id: uuid.UUID
    workspace_name: str
    role: Role


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.scalars(select(User).where(User.email == email))
    return result.one_or_none()


async def email_exists(session: AsyncSession, email: str) -> bool:
    found = await session.scalar(select(User.id).where(User.email == email))
    return found is not None


async def add_user(session: AsyncSession, email: str, password_hash: str) -> User:
    user = User(email=email, password_hash=password_hash)
    session.add(user)
    await session.flush()
    return user


async def add_workspace(session: AsyncSession, name: str) -> Workspace:
    workspace = Workspace(name=name)
    session.add(workspace)
    await session.flush()
    return workspace


async def add_membership(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID, role: Role
) -> None:
    session.add(Membership(workspace_id=workspace_id, user_id=user_id, role=role))
    await session.flush()


async def set_password_hash(session: AsyncSession, user_id: uuid.UUID, password_hash: str) -> None:
    await session.execute(
        update(User).where(User.id == user_id).values(password_hash=password_hash)
    )


async def default_workspace_id(session: AsyncSession, user_id: uuid.UUID) -> uuid.UUID | None:
    """The workspace a new session acts in: the user's oldest owned one, else their oldest."""
    owner_first = (Membership.role != "owner").asc()
    return await session.scalar(
        select(Membership.workspace_id)
        .where(Membership.user_id == user_id)
        .order_by(owner_first, Membership.created_at)
        .limit(1)
    )


async def add_session(
    session: AsyncSession,
    token_hash: str,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    expires_at: datetime.datetime,
) -> None:
    session.add(
        AuthSession(
            token_hash=token_hash,
            user_id=user_id,
            workspace_id=workspace_id,
            expires_at=expires_at,
        )
    )
    await session.flush()


async def delete_session(session: AsyncSession, token_hash: str) -> None:
    await session.execute(delete(AuthSession).where(AuthSession.token_hash == token_hash))


async def delete_expired_sessions(session: AsyncSession, user_id: uuid.UUID) -> None:
    await session.execute(
        delete(AuthSession).where(
            AuthSession.user_id == user_id, AuthSession.expires_at <= func.now()
        )
    )


async def find_session_membership(
    session: AsyncSession, token_hash: str
) -> SessionMembership | None:
    """The session's user and workspace, if the session is unexpired and the user is still a
    member of that workspace. The database clock decides expiry."""
    row = (
        await session.execute(
            select(Membership.user_id, Membership.workspace_id, Membership.role)
            .join(
                AuthSession,
                (AuthSession.user_id == Membership.user_id)
                & (AuthSession.workspace_id == Membership.workspace_id),
            )
            .where(AuthSession.token_hash == token_hash, AuthSession.expires_at > func.now())
        )
    ).one_or_none()
    if row is None:
        return None
    return SessionMembership(user_id=row.user_id, workspace_id=row.workspace_id, role=row.role)


async def get_account(
    session: AsyncSession, user_id: uuid.UUID, workspace_id: uuid.UUID
) -> AccountRecord | None:
    row = (
        await session.execute(
            select(
                User.id,
                User.email,
                User.created_at,
                Workspace.id.label("workspace_id"),
                Workspace.name,
                Membership.role,
            )
            .join(Membership, Membership.user_id == User.id)
            .join(Workspace, Workspace.id == Membership.workspace_id)
            .where(User.id == user_id, Workspace.id == workspace_id)
        )
    ).one_or_none()
    if row is None:
        return None
    return AccountRecord(
        user_id=row.id,
        email=row.email,
        created_at=row.created_at,
        workspace_id=row.workspace_id,
        workspace_name=row.name,
        role=row.role,
    )
