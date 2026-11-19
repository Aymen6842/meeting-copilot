"""Register, login, logout and /me through the full app, on real Postgres and Redis."""

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager

import httpx
import pytest
from argon2 import PasswordHasher
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.integration

ApiClientFactory = Callable[[], AbstractAsyncContextManager[httpx.AsyncClient]]

PASSWORD = "correct horse battery staple"
COOKIE_NAME = "session"
NOT_AUTHENTICATED = {"error": {"code": "not_authenticated", "message": "Authentication required."}}
INVALID_CREDENTIALS = {
    "error": {"code": "invalid_credentials", "message": "Email or password is incorrect."}
}


def unique_email() -> str:
    return f"user-{uuid.uuid4().hex[:12]}@example.com"


async def register(
    client: httpx.AsyncClient, email: str, password: str = PASSWORD
) -> httpx.Response:
    return await client.post("/auth/register", json={"email": email, "password": password})


async def login(client: httpx.AsyncClient, email: str, password: str = PASSWORD) -> httpx.Response:
    return await client.post("/auth/login", json={"email": email, "password": password})


async def registered_and_logged_in(client: httpx.AsyncClient) -> str:
    email = unique_email()
    assert (await register(client, email)).status_code == 201
    assert (await login(client, email)).status_code == 200
    return email


def session_token(client: httpx.AsyncClient) -> str:
    token = client.cookies.get(COOKIE_NAME)
    assert token
    return token


@pytest.fixture
async def database(migrated_database_url: str) -> AsyncIterator[AsyncEngine]:
    """Direct database access, to set up states the API cannot reach (expiry, removal)."""
    engine = create_async_engine(migrated_database_url, poolclass=NullPool)
    yield engine
    await engine.dispose()


async def execute(database: AsyncEngine, sql: str, **parameters: object) -> None:
    async with database.begin() as connection:
        await connection.execute(text(sql), parameters)


async def test_register_creates_an_owned_personal_workspace(api_client: httpx.AsyncClient) -> None:
    email = unique_email()

    response = await register(api_client, f"  {email.upper()} ")

    body = response.json()
    assert response.status_code == 201
    assert body["user"]["email"] == email
    assert body["workspace"]["name"] == "Personal workspace"
    assert body["workspace"]["role"] == "owner"
    assert COOKIE_NAME not in response.cookies  # registering does not log in


async def test_register_rejects_an_existing_email_in_any_case(
    api_client: httpx.AsyncClient,
) -> None:
    email = unique_email()
    await register(api_client, email)

    response = await register(api_client, email.upper())

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


async def test_register_rejects_a_short_password_without_echoing_it(
    api_client: httpx.AsyncClient,
) -> None:
    response = await register(api_client, unique_email(), password="short-pw")

    assert response.status_code == 422
    assert "short-pw" not in response.text


async def test_login_sets_a_locked_down_session_cookie(api_client: httpx.AsyncClient) -> None:
    email = unique_email()
    await register(api_client, email)

    response = await login(api_client, email)

    assert response.status_code == 200
    assert response.json()["user"]["email"] == email
    cookie = response.headers["set-cookie"]
    for attribute in ("HttpOnly", "Secure", "SameSite=lax", "Path=/", "Max-Age=604800"):
        assert attribute in cookie


async def test_me_returns_the_logged_in_account(api_client: httpx.AsyncClient) -> None:
    email = await registered_and_logged_in(api_client)

    response = await api_client.get("/me")

    assert response.status_code == 200
    assert response.json()["user"]["email"] == email
    assert response.json()["workspace"]["role"] == "owner"


async def test_me_without_a_session_is_401(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get("/me")

    assert response.status_code == 401
    assert response.json() == NOT_AUTHENTICATED


async def test_logout_ends_the_session_for_good(api_client: httpx.AsyncClient) -> None:
    await registered_and_logged_in(api_client)
    token = session_token(api_client)

    response = await api_client.post("/auth/logout")

    assert response.status_code == 204
    assert "Max-Age=0" in response.headers["set-cookie"]
    api_client.cookies.set(COOKIE_NAME, token)  # replay the old cookie
    replay = await api_client.get("/me")
    assert replay.status_code == 401
    assert replay.json() == NOT_AUTHENTICATED


async def test_wrong_password_and_unknown_email_look_identical(
    api_client: httpx.AsyncClient,
) -> None:
    email = unique_email()
    await register(api_client, email)

    wrong_password = await login(api_client, email, password="wrong password entirely")
    unknown_email = await login(api_client, unique_email())

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json() == INVALID_CREDENTIALS


@pytest.mark.parametrize(
    "token", ["", "made-up-token", "x" * 129, "%00%0d%0a"], ids=["empty", "unknown", "long", "junk"]
)
async def test_invalid_session_tokens_get_the_same_401(
    api_client: httpx.AsyncClient, token: str
) -> None:
    api_client.cookies.set(COOKIE_NAME, token)

    response = await api_client.get("/me")

    assert response.status_code == 401
    assert response.json() == NOT_AUTHENTICATED


async def test_expired_session_gets_the_same_401(
    api_client: httpx.AsyncClient, database: AsyncEngine
) -> None:
    email = await registered_and_logged_in(api_client)
    await execute(
        database,
        "UPDATE sessions SET created_at = now() - interval '2 days',"
        " expires_at = now() - interval '1 day'"
        " WHERE user_id = (SELECT id FROM users WHERE email = :email)",
        email=email,
    )

    response = await api_client.get("/me")

    assert response.status_code == 401
    assert response.json() == NOT_AUTHENTICATED


async def test_session_stops_working_when_the_user_leaves_the_workspace(
    api_client: httpx.AsyncClient, database: AsyncEngine
) -> None:
    email = await registered_and_logged_in(api_client)
    await execute(
        database,
        "DELETE FROM memberships WHERE user_id = (SELECT id FROM users WHERE email = :email)",
        email=email,
    )

    response = await api_client.get("/me")

    assert response.status_code == 401
    assert response.json() == NOT_AUTHENTICATED


async def test_login_upgrades_a_hash_made_with_old_parameters(
    api_client: httpx.AsyncClient, database: AsyncEngine
) -> None:
    email = unique_email()
    await register(api_client, email)
    weak_hash = PasswordHasher(time_cost=1, memory_cost=8 * 1024).hash(PASSWORD)
    await execute(
        database,
        "UPDATE users SET password_hash = :hash WHERE email = :email",
        hash=weak_hash,
        email=email,
    )

    assert (await login(api_client, email)).status_code == 200

    async with database.connect() as connection:
        stored = await connection.scalar(
            text("SELECT password_hash FROM users WHERE email = :email"), {"email": email}
        )
    assert stored != weak_hash
    assert PasswordHasher().check_needs_rehash(stored) is False


async def test_password_hash_never_appears_in_responses(api_client: httpx.AsyncClient) -> None:
    email = unique_email()
    responses = [await register(api_client, email), await login(api_client, email)]
    responses.append(await api_client.get("/me"))

    for response in responses:
        assert "$argon2" not in response.text
        assert "password" not in response.text


async def test_login_is_rate_limited_per_email(
    integration_environment: pytest.MonkeyPatch, make_api_client: ApiClientFactory
) -> None:
    integration_environment.setenv("APP_AUTH__LOGIN_LIMIT_PER_EMAIL__ATTEMPTS", "3")
    async with make_api_client() as client:
        email = unique_email()
        await register(client, email)
        for _ in range(3):
            assert (
                await login(client, email, password="wrong password entirely")
            ).status_code == 401

        blocked = await login(client, email)  # even the correct password is refused now

        assert blocked.status_code == 429
        assert blocked.json()["error"]["code"] == "too_many_requests"
        assert 0 < int(blocked.headers["retry-after"]) <= 900
        other_account = await login(client, unique_email())
        assert other_account.status_code == 401  # other emails are unaffected


async def test_register_is_rate_limited_per_ip(
    integration_environment: pytest.MonkeyPatch, make_api_client: ApiClientFactory
) -> None:
    integration_environment.setenv("APP_AUTH__REGISTER_LIMIT_PER_IP__ATTEMPTS", "2")
    async with make_api_client() as client:
        for _ in range(2):
            assert (await register(client, unique_email())).status_code == 201

        response = await register(client, unique_email())

        assert response.status_code == 429
        assert "retry-after" in response.headers
