"""Auth module public interface: registration, login, logout and session authentication.

Other modules use `Principal` (through `app.core.security.CurrentPrincipal`) and nothing else
from here. Logs carry user ids, never emails, passwords or tokens.
"""

import asyncio
import datetime
import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import repository
from app.auth.models import Role
from app.auth.passwords import (
    hash_password,
    needs_rehash,
    spend_verification_time,
    verify_password,
)
from app.auth.repository import AccountRecord as Account
from app.core.config import AuthSettings
from app.core.errors import (
    AuthenticationError,
    ConflictError,
    InvalidCredentialsError,
    PermissionDeniedError,
)
from app.core.rate_limit import RateLimiter, hashed_key_part

__all__ = [
    "Account",
    "NewSession",
    "Principal",
    "authenticate",
    "get_account",
    "login",
    "logout",
    "register",
]

logger = logging.getLogger(__name__)

SESSION_TOKEN_BYTES = 32
MAX_TOKEN_LENGTH = 128  # anything longer is not one of ours; reject before hashing
PERSONAL_WORKSPACE_NAME = "Personal workspace"
NOT_AUTHENTICATED = "Authentication required."
INVALID_CREDENTIALS = "Email or password is incorrect."
EMAIL_TAKEN = "An account with this email already exists."


@dataclass(frozen=True)
class Principal:
    """Who is calling, and in which workspace. Every workspace-owned query filters on
    `workspace_id`."""

    user_id: uuid.UUID
    workspace_id: uuid.UUID
    role: Role


@dataclass(frozen=True)
class NewSession:
    token: str
    expires_at: datetime.datetime
    account: Account


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def register(
    session: AsyncSession,
    limiter: RateLimiter,
    settings: AuthSettings,
    client_ip: str,
    email: str,
    password: str,
) -> Account:
    """Create a user with a personal workspace they own. Does not log them in."""
    await limiter.hit(f"register:ip:{hashed_key_part(client_ip)}", settings.register_limit_per_ip)
    if await repository.email_exists(session, email):
        raise ConflictError(EMAIL_TAKEN)

    password_hash = await asyncio.to_thread(hash_password, password)
    try:
        user = await repository.add_user(session, email, password_hash)
        workspace = await repository.add_workspace(session, PERSONAL_WORKSPACE_NAME)
        await repository.add_membership(session, workspace.id, user.id, "owner")
        await session.commit()
    except IntegrityError as error:  # a concurrent registration with the same email won
        await session.rollback()
        if "uq_users_email" in str(error.orig):
            raise ConflictError(EMAIL_TAKEN) from error
        raise

    logger.info("user registered", extra={"user_id": str(user.id)})
    return Account(
        user_id=user.id,
        email=user.email,
        created_at=user.created_at,
        workspace_id=workspace.id,
        workspace_name=workspace.name,
        role="owner",
    )


async def login(
    session: AsyncSession,
    limiter: RateLimiter,
    settings: AuthSettings,
    client_ip: str,
    email: str,
    password: str,
) -> NewSession:
    """Check the credentials and open a session. Wrong email and wrong password are
    indistinguishable, in the response and in timing."""
    await limiter.hit(f"login:ip:{hashed_key_part(client_ip)}", settings.login_limit_per_ip)
    await limiter.hit(f"login:email:{hashed_key_part(email)}", settings.login_limit_per_email)

    user = await repository.get_user_by_email(session, email)
    if user is None:
        await asyncio.to_thread(spend_verification_time, password)
        logger.info("login failed")
        raise InvalidCredentialsError(INVALID_CREDENTIALS)
    if not await asyncio.to_thread(verify_password, user.password_hash, password):
        logger.info("login failed", extra={"user_id": str(user.id)})
        raise InvalidCredentialsError(INVALID_CREDENTIALS)

    if needs_rehash(user.password_hash):
        new_hash = await asyncio.to_thread(hash_password, password)
        await repository.set_password_hash(session, user.id, new_hash)

    workspace_id = await repository.default_workspace_id(session, user.id)
    if workspace_id is None:
        raise PermissionDeniedError("This account does not belong to any workspace.")

    token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
    expires_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
        hours=settings.session_ttl_hours
    )
    await repository.delete_expired_sessions(session, user.id)
    await repository.add_session(session, hash_token(token), user.id, workspace_id, expires_at)
    account = await repository.get_account(session, user.id, workspace_id)
    if account is None:  # the membership was removed between the two queries
        raise AuthenticationError(NOT_AUTHENTICATED)
    await session.commit()

    logger.info(
        "login succeeded", extra={"user_id": str(user.id), "workspace_id": str(workspace_id)}
    )
    return NewSession(token=token, expires_at=expires_at, account=account)


async def authenticate(session: AsyncSession, token: str | None) -> Principal:
    """Resolve a session token. Missing, malformed, unknown and expired tokens, and sessions
    whose user has left the workspace, all raise the same `AuthenticationError`."""
    if not token or len(token) > MAX_TOKEN_LENGTH:
        raise AuthenticationError(NOT_AUTHENTICATED)
    membership = await repository.find_session_membership(session, hash_token(token))
    if membership is None:
        raise AuthenticationError(NOT_AUTHENTICATED)
    return Principal(
        user_id=membership.user_id, workspace_id=membership.workspace_id, role=membership.role
    )


async def logout(session: AsyncSession, token: str) -> None:
    await repository.delete_session(session, hash_token(token))
    await session.commit()


async def get_account(session: AsyncSession, principal: Principal) -> Account:
    account = await repository.get_account(session, principal.user_id, principal.workspace_id)
    if account is None:
        raise AuthenticationError(NOT_AUTHENTICATED)
    return account
