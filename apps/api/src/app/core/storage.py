"""Object storage: the only code in the API that talks to the S3 API (ADR-0006).

Browsers move audio bytes directly to and from storage through short-lived pre-signed URLs;
the API only signs URLs, inspects objects and deletes them.

Two S3 clients are used. `client` makes the API's own calls, on the internal endpoint.
`signing_client` only signs URLs, with the public endpoint that browsers can reach: SigV4
signs the host, so a URL signed for `storage:9000` would be rejected when used on
`localhost:9000`. Signing is local and never touches the network.

boto3 is synchronous, so every network call runs in a worker thread.
"""

import asyncio
import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote

from botocore.exceptions import BotoCoreError, ClientError

from app.core.clients import create_s3_client
from app.core.config import Settings
from app.core.errors import DomainValidationError, StorageUnavailableError

if TYPE_CHECKING:  # stub-only package, installed with the dev dependencies
    from mypy_boto3_s3 import S3Client

logger = logging.getLogger(__name__)

# Upload formats accepted by stage 1 (spec: wav, mp3, m4a, mp4, webm).
ALLOWED_EXTENSIONS = frozenset({"wav", "mp3", "m4a", "mp4", "webm"})

NOT_FOUND_ERROR_CODES = frozenset({"404", "NoSuchKey", "NotFound"})


def meeting_original_key(workspace_id: uuid.UUID, meeting_id: uuid.UUID, extension: str) -> str:
    """Key of a meeting's uploaded file. Every key starts with its workspace's prefix."""
    normalized = extension.strip().lower().removeprefix(".")
    if normalized not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise DomainValidationError(f"Unsupported file format. Allowed formats: {allowed}.")
    return f"{workspace_prefix(workspace_id)}meetings/{meeting_id}/original.{normalized}"


def workspace_prefix(workspace_id: uuid.UUID) -> str:
    # str(UUID) is always canonical hex and hyphens, so a key cannot escape its prefix.
    return f"workspaces/{uuid.UUID(str(workspace_id))}/"


@dataclass(frozen=True)
class PresignedRequest:
    """What a browser needs to make one request: send exactly these headers."""

    method: Literal["PUT", "GET"]
    url: str
    headers: Mapping[str, str]
    expires_at: datetime


@dataclass(frozen=True)
class ObjectInfo:
    size_bytes: int
    content_type: str | None


class ObjectStorage:
    def __init__(
        self,
        client: "S3Client",
        signing_client: "S3Client",
        bucket: str,
        upload_url_ttl_seconds: int,
        download_url_ttl_seconds: int,
    ) -> None:
        self._client = client
        self._signing_client = signing_client
        self._bucket = bucket
        self._upload_ttl = upload_url_ttl_seconds
        self._download_ttl = download_url_ttl_seconds

    def presigned_upload(
        self, key: str, content_type: str, expires_in_seconds: int | None = None
    ) -> PresignedRequest:
        """A PUT URL bound to `content_type`: any other Content-Type is rejected by storage.

        A pre-signed PUT cannot cap the size; check it with `object_info` after the upload."""
        expires_in = self._upload_ttl if expires_in_seconds is None else expires_in_seconds
        expires_at = expiry(expires_in)
        url = self._signing_client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self._bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=expires_in,
            HttpMethod="PUT",
        )
        return PresignedRequest("PUT", url, {"Content-Type": content_type}, expires_at)

    def presigned_download(
        self, key: str, filename: str | None = None, expires_in_seconds: int | None = None
    ) -> PresignedRequest:
        """A GET URL. With `filename`, the browser saves the file under that name."""
        expires_in = self._download_ttl if expires_in_seconds is None else expires_in_seconds
        expires_at = expiry(expires_in)
        params = {"Bucket": self._bucket, "Key": key}
        if filename is not None:
            params["ResponseContentDisposition"] = attachment_disposition(filename)
        url = self._signing_client.generate_presigned_url(
            "get_object", Params=params, ExpiresIn=expires_in, HttpMethod="GET"
        )
        return PresignedRequest("GET", url, {}, expires_at)

    async def object_info(self, key: str) -> ObjectInfo | None:
        """Size and type of an object, or None if it does not exist."""

        def head() -> ObjectInfo | None:
            try:
                response = self._client.head_object(Bucket=self._bucket, Key=key)
            except ClientError as error:
                if error.response.get("Error", {}).get("Code") in NOT_FOUND_ERROR_CODES:
                    return None
                raise
            return ObjectInfo(response["ContentLength"], response.get("ContentType"))

        return await self._call("head_object", head)

    async def delete(self, key: str) -> None:
        """Delete an object. Deleting one that does not exist is not an error."""

        def delete_object() -> None:
            self._client.delete_object(Bucket=self._bucket, Key=key)

        await self._call("delete_object", delete_object)

    def close(self) -> None:
        """Close the signing client if it is our own; the lifespan closes the shared one."""
        if self._signing_client is not self._client:
            self._signing_client.close()

    async def _call[T](self, operation: str, call: Callable[[], T]) -> T:
        try:
            return await asyncio.to_thread(call)
        except (BotoCoreError, ClientError) as error:
            logger.warning(
                "storage request failed",
                extra={"operation": operation, "error_type": type(error).__name__},
            )
            raise StorageUnavailableError("Storage is temporarily unavailable.") from error


def expiry(expires_in_seconds: int) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=expires_in_seconds)


def attachment_disposition(filename: str) -> str:
    # RFC 6266 / 5987: percent-encode everything, so quotes or newlines cannot break the header.
    return f"attachment; filename*=UTF-8''{quote(filename, safe='')}"


def create_object_storage(settings: Settings, client: "S3Client") -> ObjectStorage:
    """Storage for the API. `client` is the shared one from the lifespan."""
    storage = settings.storage
    signing_client = (
        client
        if storage.signing_endpoint_url == storage.endpoint_url
        else create_s3_client(settings, endpoint_url=storage.signing_endpoint_url)
    )
    return ObjectStorage(
        client=client,
        signing_client=signing_client,
        bucket=storage.bucket,
        upload_url_ttl_seconds=storage.upload_url_ttl_seconds,
        download_url_ttl_seconds=storage.download_url_ttl_seconds,
    )
