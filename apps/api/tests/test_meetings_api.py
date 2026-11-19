"""Meetings through the full app, on real Postgres, Redis and RustFS.

Uploads go straight to storage with a plain HTTP client, the way a browser sends them.
"""

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager

import httpx
import pytest

pytestmark = pytest.mark.integration

ApiClientFactory = Callable[[], AbstractAsyncContextManager[httpx.AsyncClient]]

PASSWORD = "correct horse battery staple"
AUDIO = b"ID3" + bytes(range(256)) * 8
NOT_FOUND = {"error": {"code": "not_found", "message": "Meeting not found."}}
UNSUPPORTED_FORMAT = "Unsupported file format. Allowed formats: m4a, mp3, mp4, wav, webm."


def meeting_request(**overrides: object) -> dict[str, object]:
    return {
        "title": "Kick-off with Acme",
        "language_hint": "fr",
        "consent_confirmed": True,
        "filename": "kickoff.mp3",
        "content_type": "audio/mpeg",
        "size_bytes": len(AUDIO),
    } | overrides


async def log_in_new_user(client: httpx.AsyncClient) -> None:
    email = f"user-{uuid.uuid4().hex[:12]}@example.com"
    credentials = {"email": email, "password": PASSWORD}
    assert (await client.post("/auth/register", json=credentials)).status_code == 201
    assert (await client.post("/auth/login", json=credentials)).status_code == 200


@pytest.fixture
async def alice(make_api_client: ApiClientFactory) -> AsyncIterator[httpx.AsyncClient]:
    async with make_api_client() as client:
        await log_in_new_user(client)
        yield client


@pytest.fixture
async def bob(make_api_client: ApiClientFactory) -> AsyncIterator[httpx.AsyncClient]:
    """Another user, in another workspace."""
    async with make_api_client() as client:
        await log_in_new_user(client)
        yield client


@pytest.fixture
async def browser() -> AsyncIterator[httpx.AsyncClient]:
    """Talks to storage directly, with no API cookies."""
    async with httpx.AsyncClient(timeout=10) as client:
        yield client


async def create(client: httpx.AsyncClient, **overrides: object) -> httpx.Response:
    return await client.post("/meetings", json=meeting_request(**overrides))


async def upload(browser: httpx.AsyncClient, created: dict[str, object], body: bytes) -> int:
    instructions = created["upload"]
    assert isinstance(instructions, dict)
    response = await browser.request(
        instructions["method"], instructions["url"], content=body, headers=instructions["headers"]
    )
    return response.status_code


async def created_and_uploaded(
    client: httpx.AsyncClient, browser: httpx.AsyncClient
) -> dict[str, object]:
    created = (await create(client)).json()
    assert await upload(browser, created, AUDIO) == 200
    completed = await client.post(f"/meetings/{created['meeting']['id']}/upload-complete")
    assert completed.status_code == 200
    meeting: dict[str, object] = completed.json()
    return meeting


def assert_validation_error(response: httpx.Response, message: str) -> None:
    assert response.status_code == 422
    assert response.json()["error"] == {"code": "validation_failed", "message": message}


async def test_create_returns_the_meeting_and_an_upload_url(alice: httpx.AsyncClient) -> None:
    response = await create(alice, title="  Kick-off with Acme ")

    body = response.json()
    assert response.status_code == 201
    assert body["meeting"]["title"] == "Kick-off with Acme"
    assert body["meeting"]["status"] == "awaiting_upload"
    assert body["meeting"]["language_hint"] == "fr"
    assert body["meeting"]["duration_ms"] is None
    assert body["meeting"]["audio"] == {
        "format": "mp3",
        "content_type": "audio/mpeg",
        "size_bytes": None,
    }
    assert body["upload"]["method"] == "PUT"
    assert body["upload"]["headers"] == {"Content-Type": "audio/mpeg"}
    assert body["meeting"]["id"] in body["upload"]["url"]


async def test_upload_then_complete_marks_the_meeting_uploaded(
    alice: httpx.AsyncClient, browser: httpx.AsyncClient
) -> None:
    meeting = await created_and_uploaded(alice, browser)

    assert meeting["status"] == "uploaded"
    assert meeting["audio"] == {
        "format": "mp3",
        "content_type": "audio/mpeg",
        "size_bytes": len(AUDIO),
    }
    detail = await alice.get(f"/meetings/{meeting['id']}")
    assert detail.status_code == 200
    assert detail.json() == meeting


async def test_completing_twice_gives_the_same_result(
    alice: httpx.AsyncClient, browser: httpx.AsyncClient
) -> None:
    meeting = await created_and_uploaded(alice, browser)

    again = await alice.post(f"/meetings/{meeting['id']}/upload-complete")

    assert again.status_code == 200
    assert again.json() == meeting


async def test_completing_before_uploading_is_a_conflict(alice: httpx.AsyncClient) -> None:
    meeting_id = (await create(alice)).json()["meeting"]["id"]

    response = await alice.post(f"/meetings/{meeting_id}/upload-complete")

    assert response.status_code == 409
    assert response.json()["error"]["message"] == (
        "No uploaded file found. Upload the file, then try again."
    )
    assert (await alice.get(f"/meetings/{meeting_id}")).json()["status"] == "awaiting_upload"


@pytest.mark.parametrize("consent", [False, None])
async def test_creating_without_consent_is_rejected(
    alice: httpx.AsyncClient, consent: bool | None
) -> None:
    request = meeting_request()
    if consent is None:
        del request["consent_confirmed"]
    else:
        request["consent_confirmed"] = consent

    response = await alice.post("/meetings", json=request)

    assert response.status_code == 422
    if consent is False:
        assert_validation_error(response, "Confirm that every participant agreed to be recorded.")
    assert (await alice.get("/meetings")).json()["items"] == []


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"filename": "notes.txt", "content_type": "text/plain"}, UNSUPPORTED_FORMAT),
        ({"filename": "meeting.flac", "content_type": "audio/flac"}, UNSUPPORTED_FORMAT),
        ({"filename": "recording", "content_type": "audio/mpeg"}, UNSUPPORTED_FORMAT),
        (
            {"filename": "meeting.mp3", "content_type": "text/html"},
            "The file type does not match the file name.",
        ),
        ({"duration_ms": 10_800_001}, "Recordings longer than 3 hours are not supported."),
        ({"size_bytes": 4 * 1024**3 + 1}, "Files larger than 4 GiB are not supported."),
    ],
    ids=["text", "flac", "no-extension", "type-mismatch", "over-3-hours", "over-4-gib"],
)
async def test_unsupported_files_are_rejected_with_a_clear_message(
    alice: httpx.AsyncClient, overrides: dict[str, object], message: str
) -> None:
    response = await create(alice, **overrides)

    assert_validation_error(response, message)
    assert (await alice.get("/meetings")).json()["items"] == []


async def test_exactly_three_hours_is_accepted(alice: httpx.AsyncClient) -> None:
    response = await create(alice, duration_ms=10_800_000)

    assert response.status_code == 201


async def test_an_upload_larger_than_the_limit_is_rejected_and_deleted(
    integration_environment: pytest.MonkeyPatch,
    make_api_client: ApiClientFactory,
    browser: httpx.AsyncClient,
) -> None:
    integration_environment.setenv("APP_MEETINGS__MAX_UPLOAD_BYTES", "1024")
    async with make_api_client() as client:
        await log_in_new_user(client)
        created = (await create(client, size_bytes=1000)).json()  # declares less than it sends
        assert await upload(browser, created, b"x" * 2048) == 200
        meeting_id = created["meeting"]["id"]

        rejected = await client.post(f"/meetings/{meeting_id}/upload-complete")
        retried = await client.post(f"/meetings/{meeting_id}/upload-complete")

    assert_validation_error(rejected, "Files larger than 1024 bytes are not supported.")
    assert retried.status_code == 409  # the oversized file is gone; the meeting can retry
    assert retried.json()["error"]["code"] == "conflict"


async def test_an_empty_upload_is_rejected(
    alice: httpx.AsyncClient, browser: httpx.AsyncClient
) -> None:
    created = (await create(alice)).json()
    assert await upload(browser, created, b"") == 200

    response = await alice.post(f"/meetings/{created['meeting']['id']}/upload-complete")

    assert_validation_error(response, "The uploaded file is empty.")


async def test_another_workspace_cannot_see_or_touch_a_meeting(
    alice: httpx.AsyncClient, bob: httpx.AsyncClient, browser: httpx.AsyncClient
) -> None:
    created = (await create(alice)).json()
    meeting_id = created["meeting"]["id"]

    bob_list = await bob.get("/meetings")
    bob_detail = await bob.get(f"/meetings/{meeting_id}")
    bob_complete = await bob.post(f"/meetings/{meeting_id}/upload-complete")
    unknown_detail = await bob.get(f"/meetings/{uuid.uuid4()}")

    assert bob_list.json()["items"] == []
    assert bob_detail.status_code == bob_complete.status_code == 404
    # Indistinguishable from a meeting that does not exist.
    assert bob_detail.json() == bob_complete.json() == unknown_detail.json() == NOT_FOUND

    assert await upload(browser, created, AUDIO) == 200
    assert (await bob.post(f"/meetings/{meeting_id}/upload-complete")).status_code == 404
    alice_view = await alice.get(f"/meetings/{meeting_id}")
    assert alice_view.json()["status"] == "awaiting_upload"  # bob's call changed nothing


async def test_list_is_newest_first_and_paginated(alice: httpx.AsyncClient) -> None:
    ids = [
        (await create(alice, title=f"Meeting {number}")).json()["meeting"]["id"]
        for number in range(3)
    ]

    first_page = (await alice.get("/meetings", params={"limit": 2})).json()
    last_page = (await alice.get("/meetings", params={"limit": 2, "offset": 2})).json()

    assert [item["id"] for item in first_page["items"]] == [ids[2], ids[1]]
    assert first_page["next_offset"] == 2
    assert [item["id"] for item in last_page["items"]] == [ids[0]]
    assert last_page["next_offset"] is None


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
async def test_list_rejects_invalid_paging(
    alice: httpx.AsyncClient, params: dict[str, int]
) -> None:
    assert (await alice.get("/meetings", params=params)).status_code == 422


async def test_a_malformed_meeting_id_is_a_validation_error(alice: httpx.AsyncClient) -> None:
    assert (await alice.get("/meetings/not-a-uuid")).status_code == 422


async def test_meeting_responses_never_reveal_the_storage_key(
    alice: httpx.AsyncClient, browser: httpx.AsyncClient
) -> None:
    meeting = await created_and_uploaded(alice, browser)
    listing = await alice.get("/meetings")

    for text in (str(meeting), listing.text):
        assert "workspaces/" not in text
        assert "storage_key" not in text
