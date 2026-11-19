"""Storage keys, URL signing and error mapping. No storage server needed: signing is local."""

import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from app.core.clients import create_s3_client
from app.core.config import Settings, load_settings
from app.core.error_handlers import status_for
from app.core.errors import DomainValidationError, StorageUnavailableError
from app.core.storage import (
    ALLOWED_EXTENSIONS,
    ObjectStorage,
    attachment_disposition,
    create_object_storage,
    meeting_original_key,
)

WORKSPACE_ID = uuid.UUID("0193a1b2-0000-7000-8000-000000000001")
MEETING_ID = uuid.UUID("0193a1b2-0000-7000-8000-000000000002")


def storage_for(settings: Settings) -> ObjectStorage:
    return create_object_storage(settings, create_s3_client(settings))


@pytest.fixture
def compose_like_settings(app_environment: pytest.MonkeyPatch) -> Settings:
    """Internal endpoint for the API, public endpoint for browsers, as in docker compose."""
    app_environment.setenv("APP_STORAGE__ENDPOINT_URL", "http://storage:9000")
    app_environment.setenv("APP_STORAGE__PUBLIC_ENDPOINT_URL", "http://localhost:9000")
    return load_settings()


def test_meeting_key_is_namespaced_by_workspace() -> None:
    key = meeting_original_key(WORKSPACE_ID, MEETING_ID, "wav")

    assert key == f"workspaces/{WORKSPACE_ID}/meetings/{MEETING_ID}/original.wav"


@pytest.mark.parametrize("extension", ["WAV", ".mp3", " m4a ", ".MP4"])
def test_meeting_key_normalises_the_extension(extension: str) -> None:
    key = meeting_original_key(WORKSPACE_ID, MEETING_ID, extension)

    assert key.rsplit(".", 1)[1] in ALLOWED_EXTENSIONS


@pytest.mark.parametrize("extension", ["", "exe", "wav/../../x", "flac", "wav.exe", "..", "mp3 x"])
def test_meeting_key_rejects_unsupported_formats(extension: str) -> None:
    with pytest.raises(DomainValidationError, match="Allowed formats: m4a, mp3, mp4, wav, webm"):
        meeting_original_key(WORKSPACE_ID, MEETING_ID, extension)


@given(st.uuids(), st.uuids(), st.sampled_from(sorted(ALLOWED_EXTENSIONS)))
def test_meeting_key_always_stays_inside_its_workspace(
    workspace_id: uuid.UUID, meeting_id: uuid.UUID, extension: str
) -> None:
    key = meeting_original_key(workspace_id, meeting_id, extension)

    assert key.startswith(f"workspaces/{workspace_id}/meetings/{meeting_id}/")
    assert key.split("/") == [
        "workspaces",
        str(workspace_id),
        "meetings",
        str(meeting_id),
        f"original.{extension}",
    ]


@given(st.text(max_size=20))
def test_any_extension_gives_an_allowed_key_or_a_validation_error(extension: str) -> None:
    try:
        key = meeting_original_key(WORKSPACE_ID, MEETING_ID, extension)
    except DomainValidationError:
        return
    assert key.removeprefix(f"workspaces/{WORKSPACE_ID}/meetings/{MEETING_ID}/original.") in (
        ALLOWED_EXTENSIONS
    )


def test_upload_url_is_signed_for_the_public_host(compose_like_settings: Settings) -> None:
    upload = storage_for(compose_like_settings).presigned_upload("some/key.wav", "audio/wav")

    url = urlsplit(upload.url)
    assert upload.method == "PUT"
    assert f"{url.scheme}://{url.netloc}" == "http://localhost:9000"
    assert url.path == "/test-bucket/some/key.wav"


def test_upload_url_binds_the_content_type_and_expires(compose_like_settings: Settings) -> None:
    before = datetime.now(UTC)
    upload = storage_for(compose_like_settings).presigned_upload("some/key.wav", "audio/wav")

    query = parse_qs(urlsplit(upload.url).query)
    ttl = compose_like_settings.storage.upload_url_ttl_seconds
    assert upload.headers == {"Content-Type": "audio/wav"}
    assert "content-type" in query["X-Amz-SignedHeaders"][0].split(";")
    assert query["X-Amz-Expires"] == [str(ttl)]
    assert before + timedelta(seconds=ttl) <= upload.expires_at
    assert upload.expires_at <= datetime.now(UTC) + timedelta(seconds=ttl)


def test_download_url_uses_its_own_lifetime(compose_like_settings: Settings) -> None:
    download = storage_for(compose_like_settings).presigned_download("some/key.wav")

    query = parse_qs(urlsplit(download.url).query)
    assert download.method == "GET"
    assert download.headers == {}
    assert urlsplit(download.url).netloc == "localhost:9000"
    assert query["X-Amz-Expires"] == [str(compose_like_settings.storage.download_url_ttl_seconds)]


def test_download_url_can_name_the_saved_file(compose_like_settings: Settings) -> None:
    download = storage_for(compose_like_settings).presigned_download(
        "some/key.wav", filename="Kick-off.wav"
    )

    query = parse_qs(urlsplit(download.url).query)
    assert query["response-content-disposition"] == ["attachment; filename*=UTF-8''Kick-off.wav"]


def test_attachment_filename_cannot_inject_header_content() -> None:
    disposition = attachment_disposition('a"; evil=1\r\nX-Injected: yes.wav')

    encoded = disposition.removeprefix("attachment; filename*=UTF-8''")
    assert encoded != disposition
    for character in ('"', ";", " ", "\r", "\n"):
        assert character not in encoded


def test_without_a_public_endpoint_urls_use_the_internal_one(settings: Settings) -> None:
    upload = storage_for(settings).presigned_upload("some/key.wav", "audio/wav")

    assert urlsplit(upload.url).netloc == urlsplit(settings.storage.endpoint_url).netloc


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("APP_STORAGE__UPLOAD_URL_TTL_SECONDS", "0"),
        ("APP_STORAGE__UPLOAD_URL_TTL_SECONDS", "3601"),
        ("APP_STORAGE__DOWNLOAD_URL_TTL_SECONDS", "-5"),
        ("APP_STORAGE__DOWNLOAD_URL_TTL_SECONDS", "86400"),
    ],
)
def test_url_lifetimes_must_be_short(
    app_environment: pytest.MonkeyPatch, name: str, value: str
) -> None:
    app_environment.setenv(name, value)

    with pytest.raises(ValidationError, match="url_ttl_seconds"):
        load_settings()


async def test_unreachable_storage_is_a_storage_unavailable_error(
    app_environment: pytest.MonkeyPatch,
) -> None:
    app_environment.setenv("APP_STORAGE__ENDPOINT_URL", "http://127.0.0.1:1")
    storage = storage_for(load_settings())

    with pytest.raises(StorageUnavailableError) as raised:
        await storage.object_info("some/key.wav")

    assert raised.value.message == "Storage is temporarily unavailable."
    assert status_for(raised.value) == 503
