"""Factories for clients to shared infrastructure, created once per process in the lifespan.

The database engine lives in `core/db.py`; `core/storage.py` wraps the S3 client.
"""

from typing import TYPE_CHECKING

import redis.asyncio
from boto3 import client as boto3_client
from botocore.config import Config

from app.core.config import Settings

if TYPE_CHECKING:  # stub-only package, installed with the dev dependencies
    from mypy_boto3_s3 import S3Client

CONNECT_TIMEOUT_SECONDS = 2
READ_TIMEOUT_SECONDS = 5


def create_redis_client(settings: Settings) -> redis.asyncio.Redis:
    return redis.asyncio.Redis.from_url(
        settings.redis_url.get_secret_value(),
        socket_connect_timeout=CONNECT_TIMEOUT_SECONDS,
        socket_timeout=READ_TIMEOUT_SECONDS,
    )


def create_s3_client(settings: Settings, endpoint_url: str | None = None) -> "S3Client":
    """`endpoint_url` overrides the configured one, e.g. the public host for signing URLs."""
    storage = settings.storage
    return boto3_client(
        "s3",
        endpoint_url=endpoint_url or storage.endpoint_url,
        region_name=storage.region,
        aws_access_key_id=storage.access_key.get_secret_value(),
        aws_secret_access_key=storage.secret_key.get_secret_value(),
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
            read_timeout=READ_TIMEOUT_SECONDS,
            retries={"max_attempts": 1, "mode": "standard"},
        ),
    )
