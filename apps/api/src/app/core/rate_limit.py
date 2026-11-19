"""Fixed-window rate limiting in Redis.

Each key counts attempts in a window that starts with the first attempt. Keys never contain
personal data: callers pass identifiers through `hashed_key_part`.
"""

import hashlib

import redis.asyncio

from app.core.config import RateLimit
from app.core.errors import RateLimitExceededError

KEY_PREFIX = "rate-limit"


def hashed_key_part(value: str) -> str:
    """Stable, non-reversible stand-in for personal data (such as an email) in a Redis key."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


class RateLimiter:
    def __init__(self, redis_client: redis.asyncio.Redis) -> None:
        self._redis = redis_client

    async def hit(self, key: str, limit: RateLimit) -> None:
        """Count one attempt; raise `RateLimitExceededError` once the limit is passed."""
        full_key = f"{KEY_PREFIX}:{key}"
        async with self._redis.pipeline(transaction=True) as pipeline:
            pipeline.incr(full_key)
            pipeline.expire(full_key, limit.window_seconds, nx=True)  # starts the window once
            pipeline.ttl(full_key)
            count, _, ttl = await pipeline.execute()
        if int(count) > limit.attempts:
            raise RateLimitExceededError(
                "Too many attempts. Try again later.",
                retry_after_seconds=max(int(ttl), 1),
            )
