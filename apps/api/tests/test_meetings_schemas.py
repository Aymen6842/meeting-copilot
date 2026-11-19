"""Meeting request validation and the format rules. No infrastructure needed."""

from typing import get_args

import pytest
from pydantic import ValidationError

from app.core.errors import DomainValidationError
from app.core.storage import ALLOWED_EXTENSIONS
from app.meetings.models import AudioFormat
from app.meetings.schemas import CreateMeetingRequest
from app.meetings.service import (
    FORMAT_CONTENT_TYPES,
    describe_bytes,
    describe_duration,
    file_format,
)

VALID_REQUEST = {
    "title": "Kick-off with Acme",
    "consent_confirmed": True,
    "filename": "kickoff.mp3",
    "content_type": "audio/mpeg",
    "size_bytes": 1024,
}


def test_format_lists_agree_across_modules() -> None:
    assert set(FORMAT_CONTENT_TYPES) == set(get_args(AudioFormat)) == ALLOWED_EXTENSIONS


@pytest.mark.parametrize(
    ("filename", "content_type", "expected"),
    [
        ("call.wav", "audio/wav", "wav"),
        ("Call.MP3", "audio/mpeg", "mp3"),
        ("voice memo.m4a", "audio/x-m4a", "m4a"),
        ("screen.recording.mp4", "video/mp4", "mp4"),
        ("browser.webm", "audio/webm; codecs=opus", "webm"),
        ("browser.webm", "Video/WebM", "webm"),
    ],
)
def test_file_format_accepts_matching_name_and_type(
    filename: str, content_type: str, expected: str
) -> None:
    assert file_format(filename, content_type) == expected


@pytest.mark.parametrize("filename", ["notes.txt", "recording", "audio.flac", "wav", ".mp3.exe"])
def test_file_format_rejects_unsupported_formats(filename: str) -> None:
    with pytest.raises(DomainValidationError) as raised:
        file_format(filename, "audio/mpeg")

    assert raised.value.message == (
        "Unsupported file format. Allowed formats: m4a, mp3, mp4, wav, webm."
    )


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [("call.wav", "audio/mpeg"), ("call.mp3", "text/html"), ("call.mp4", "application/zip")],
)
def test_file_format_rejects_a_type_that_does_not_match_the_name(
    filename: str, content_type: str
) -> None:
    with pytest.raises(DomainValidationError, match="does not match the file name"):
        file_format(filename, content_type)


@pytest.mark.parametrize(
    ("size", "text"),
    [(4 * 1024**3, "4 GiB"), (512 * 1024**2, "512 MiB"), (1000, "1000 bytes")],
)
def test_describe_bytes(size: int, text: str) -> None:
    assert describe_bytes(size) == text


@pytest.mark.parametrize(
    ("duration_ms", "text"),
    [
        (10_800_000, "3 hours"),
        (3_600_000, "1 hour"),
        (90 * 60_000, "90 minutes"),
        (1500, "1500 ms"),
    ],
)
def test_describe_duration(duration_ms: int, text: str) -> None:
    assert describe_duration(duration_ms) == text


def test_request_strips_the_title() -> None:
    request = CreateMeetingRequest.model_validate({**VALID_REQUEST, "title": "  Weekly sync  "})

    assert request.title == "Weekly sync"
    assert request.language_hint == "auto"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "   "),
        ("title", "x" * 201),
        ("size_bytes", 0),
        ("duration_ms", 0),
        ("language_hint", "de"),
        ("recorded_at", "2026-11-19T10:00:00"),  # no time zone
        ("filename", ""),
    ],
)
def test_request_rejects_invalid_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError, match=field):
        CreateMeetingRequest.model_validate({**VALID_REQUEST, field: value})


@pytest.mark.parametrize("field", ["title", "consent_confirmed", "filename", "size_bytes"])
def test_request_requires_field(field: str) -> None:
    incomplete = {name: value for name, value in VALID_REQUEST.items() if name != field}

    with pytest.raises(ValidationError, match=field):
        CreateMeetingRequest.model_validate(incomplete)


def test_request_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="workspace_id"):
        CreateMeetingRequest.model_validate({**VALID_REQUEST, "workspace_id": "x"})
