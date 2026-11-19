"""Auth tables against real Postgres: generated values and the constraints that protect data."""

import uuid

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Membership, User, Workspace

pytestmark = pytest.mark.integration

PASSWORD_HASH = "$argon2id$placeholder"


async def add_user(session: AsyncSession, email: str = "ada@example.com") -> User:
    user = User(email=email, password_hash=PASSWORD_HASH)
    session.add(user)
    await session.flush()
    return user


async def add_workspace(session: AsyncSession, name: str = "Ada's workspace") -> Workspace:
    workspace = Workspace(name=name)
    session.add(workspace)
    await session.flush()
    return workspace


async def membership_count(session: AsyncSession) -> int:
    count = await session.scalar(select(func.count()).select_from(Membership))
    return count or 0


async def test_ids_are_uuidv7_and_created_at_is_set(db_session: AsyncSession) -> None:
    workspace = await add_workspace(db_session)
    await db_session.refresh(workspace)

    assert workspace.id.version == 7
    assert workspace.created_at.tzinfo is not None


async def test_ids_increase_in_creation_order(db_session: AsyncSession) -> None:
    first = await add_workspace(db_session, "First")
    second = await add_workspace(db_session, "Second")

    assert first.id < second.id


async def test_email_must_be_unique(db_session: AsyncSession) -> None:
    await add_user(db_session, "ada@example.com")

    with pytest.raises(IntegrityError, match="uq_users_email"):
        await add_user(db_session, "ada@example.com")


async def test_email_must_be_stored_lower_case(db_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError, match="ck_users_email_lowercase"):
        await add_user(db_session, "Ada@Example.com")


async def test_workspace_name_cannot_be_blank(db_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError, match="ck_workspaces_name_not_blank"):
        await add_workspace(db_session, "   ")


async def test_role_must_be_known(db_session: AsyncSession) -> None:
    user = await add_user(db_session)
    workspace = await add_workspace(db_session)
    db_session.add(Membership(workspace_id=workspace.id, user_id=user.id, role="admin"))

    with pytest.raises(IntegrityError, match="ck_memberships_role_valid"):
        await db_session.flush()


async def test_membership_needs_an_existing_workspace(db_session: AsyncSession) -> None:
    user = await add_user(db_session)
    db_session.add(Membership(workspace_id=uuid.uuid4(), user_id=user.id, role="owner"))

    with pytest.raises(IntegrityError, match="fk_memberships_workspace_id_workspaces"):
        await db_session.flush()


async def test_deleting_a_workspace_deletes_its_memberships(db_session: AsyncSession) -> None:
    user = await add_user(db_session)
    workspace = await add_workspace(db_session)
    db_session.add(Membership(workspace_id=workspace.id, user_id=user.id, role="owner"))
    await db_session.flush()

    await db_session.execute(delete(Workspace).where(Workspace.id == workspace.id))

    assert await membership_count(db_session) == 0
    assert await db_session.get(User, user.id) is not None


async def test_deleting_a_user_deletes_their_memberships(db_session: AsyncSession) -> None:
    user = await add_user(db_session)
    workspace = await add_workspace(db_session)
    db_session.add(Membership(workspace_id=workspace.id, user_id=user.id, role="member"))
    await db_session.flush()

    await db_session.execute(delete(User).where(User.id == user.id))

    assert await membership_count(db_session) == 0
