"""Object storage against a real RustFS container, with requests made the way a browser does."""

import asyncio
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest

from app.core.clients import create_s3_client
from app.core.config import load_settings
from app.core.storage import ObjectStorage, create_object_storage, meeting_original_key
from tests.fixtures.containers import Storage

pytestmark = pytest.mark.integration

AUDIO = b"RIFF\x24\x00\x00\x00WAVEfmt " + bytes(range(256)) * 4
CONTENT_TYPE = "audio/wav"


@pytest.fixture
def storage(app_environment: pytest.MonkeyPatch, storage_container: Storage) -> ObjectStorage:
    app_environment.setenv("APP_STORAGE__ENDPOINT_URL", storage_container.endpoint_url)
    app_environment.setenv("APP_STORAGE__ACCESS_KEY", storage_container.access_key)
    app_environment.setenv("APP_STORAGE__SECRET_KEY", storage_container.secret_key)
    app_environment.setenv("APP_STORAGE__BUCKET", storage_container.bucket)
    settings = load_settings()
    return create_object_storage(settings, create_s3_client(settings))


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=10) as client:
        yield client


def new_key() -> str:
    return meeting_original_key(uuid.uuid4(), uuid.uuid4(), "wav")


async def upload(storage: ObjectStorage, http: httpx.AsyncClient, key: str) -> httpx.Response:
    request = storage.presigned_upload(key, CONTENT_TYPE)
    return await http.put(request.url, content=AUDIO, headers=dict(request.headers))


async def test_upload_through_a_presigned_url_then_verify(
    storage: ObjectStorage, http: httpx.AsyncClient
) -> None:
    key = new_key()

    response = await upload(storage, http, key)

    assert response.status_code == 200
    info = await storage.object_info(key)
    assert info is not None
    assert info.size_bytes == len(AUDIO)
    assert info.content_type == CONTENT_TYPE


async def test_download_through_a_presigned_url_returns_the_same_bytes(
    storage: ObjectStorage, http: httpx.AsyncClient
) -> None:
    key = new_key()
    await upload(storage, http, key)

    response = await http.get(storage.presigned_download(key, filename="Kick-off.wav").url)

    assert response.status_code == 200
    assert response.content == AUDIO
    assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''Kick-off.wav"


async def test_delete_removes_the_object_and_is_idempotent(
    storage: ObjectStorage, http: httpx.AsyncClient
) -> None:
    key = new_key()
    await upload(storage, http, key)

    await storage.delete(key)

    assert await storage.object_info(key) is None
    assert (await http.get(storage.presigned_download(key).url)).status_code == 404
    await storage.delete(key)  # already gone: no error


async def test_missing_object_has_no_info(storage: ObjectStorage) -> None:
    assert await storage.object_info(new_key()) is None


async def test_expired_upload_url_is_rejected(
    storage: ObjectStorage, http: httpx.AsyncClient
) -> None:
    key = new_key()
    request = storage.presigned_upload(key, CONTENT_TYPE, expires_in_seconds=1)
    await asyncio.sleep(2)

    response = await http.put(request.url, content=AUDIO, headers=dict(request.headers))

    assert response.status_code == 403
    assert await storage.object_info(key) is None


async def test_expired_download_url_is_rejected(
    storage: ObjectStorage, http: httpx.AsyncClient
) -> None:
    key = new_key()
    await upload(storage, http, key)
    request = storage.presigned_download(key, expires_in_seconds=1)
    await asyncio.sleep(2)

    response = await http.get(request.url)

    assert response.status_code == 403
    assert AUDIO not in response.content


async def test_upload_with_another_content_type_is_rejected(
    storage: ObjectStorage, http: httpx.AsyncClient
) -> None:
    key = new_key()
    request = storage.presigned_upload(key, CONTENT_TYPE)

    response = await http.put(request.url, content=b"<html>", headers={"Content-Type": "text/html"})

    assert response.status_code == 403
    assert await storage.object_info(key) is None


async def test_a_signed_url_cannot_be_pointed_at_another_key(
    storage: ObjectStorage, http: httpx.AsyncClient
) -> None:
    own_key, other_key = new_key(), new_key()
    request = storage.presigned_upload(own_key, CONTENT_TYPE)
    tampered = request.url.replace(own_key, other_key)

    response = await http.put(tampered, content=AUDIO, headers=dict(request.headers))

    assert response.status_code == 403
    assert await storage.object_info(other_key) is None
