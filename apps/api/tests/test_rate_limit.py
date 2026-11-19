"""Rate limiter against real Redis."""

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
import redis.asyncio

from app.core.config import RateLimit
from app.core.errors import RateLimitExceededError
from app.core.rate_limit import RateLimiter, hashed_key_part

pytestmark = pytest.mark.integration


@pytest.fixture
async def limiter(redis_url: str) -> AsyncIterator[RateLimiter]:
    client = redis.asyncio.Redis.from_url(redis_url)
    yield RateLimiter(client)
    await client.aclose()


def unique_key() -> str:
    return f"test:{uuid.uuid4().hex}"


async def test_allows_up_to_the_limit_then_refuses(limiter: RateLimiter) -> None:
    key = unique_key()
    limit = RateLimit(attempts=3, window_seconds=60)
    for _ in range(3):
        await limiter.hit(key, limit)

    with pytest.raises(RateLimitExceededError) as raised:
        await limiter.hit(key, limit)

    assert 0 < raised.value.retry_after_seconds <= 60


async def test_keys_are_counted_separately(limiter: RateLimiter) -> None:
    limit = RateLimit(attempts=1, window_seconds=60)
    await limiter.hit(unique_key(), limit)

    await limiter.hit(unique_key(), limit)  # a different key is unaffected


async def test_window_resets_after_it_expires(limiter: RateLimiter) -> None:
    key = unique_key()
    limit = RateLimit(attempts=1, window_seconds=1)
    await limiter.hit(key, limit)
    with pytest.raises(RateLimitExceededError):
        await limiter.hit(key, limit)

    await asyncio.sleep(1.2)

    await limiter.hit(key, limit)


def test_hashed_key_part_hides_personal_data() -> None:
    part = hashed_key_part("ada@example.com")

    assert "ada" not in part
    assert part == hashed_key_part("ada@example.com")
