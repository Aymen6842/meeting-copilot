import json
import logging
import sys
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import httpx
import pytest
from fastapi import FastAPI, Response

from app.core.logging import (
    REQUEST_ID_HEADER,
    JsonFormatter,
    add_request_logging,
    configure_logging,
)

ClientFactory = Callable[[FastAPI], AbstractAsyncContextManager[httpx.AsyncClient]]
LogParser = Callable[[str], list[dict[str, object]]]


def format_record(message: str, **extra: object) -> dict[str, object]:
    record = logging.LogRecord("test.logger", logging.INFO, __file__, 1, message, None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    parsed: dict[str, object] = json.loads(JsonFormatter().format(record))
    return parsed


def logging_app() -> FastAPI:
    configure_logging("INFO")
    app = FastAPI()
    add_request_logging(app)

    @app.get("/items/{item_id}")
    async def read_item(item_id: int) -> dict[str, int]:
        logging.getLogger("app.items").info("item read", extra={"item_id": item_id})
        return {"item_id": item_id}

    return app


def test_record_is_one_json_object_with_standard_fields() -> None:
    payload = format_record("hello")

    assert payload["message"] == "hello"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.logger"
    assert str(payload["timestamp"]).endswith("+00:00")


def test_extra_fields_are_included() -> None:
    payload = format_record("job queued", job_id="job-1", meeting_id="meeting-1")

    assert payload["job_id"] == "job-1"
    assert payload["meeting_id"] == "meeting-1"


def test_uvicorn_color_message_is_dropped() -> None:
    payload = format_record("Started server process", color_message="\x1b[36mStarted\x1b[0m")

    assert "color_message" not in payload


def test_exception_is_included() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord(
            "test", logging.ERROR, __file__, 1, "failed", None, sys.exc_info()
        )

    payload = json.loads(JsonFormatter().format(record))

    assert "ValueError: boom" in payload["exception"]


def test_configure_logging_sends_uvicorn_logs_through_the_json_handler(
    capsys: pytest.CaptureFixture[str], parse_log_lines: LogParser
) -> None:
    configure_logging("INFO")

    logging.getLogger("uvicorn.error").info("server started")

    [line] = parse_log_lines(capsys.readouterr().out)
    assert line["logger"] == "uvicorn.error"
    assert line["message"] == "server started"


def test_uvicorn_access_log_is_silenced(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")

    access_logger = logging.getLogger("uvicorn.access")
    access_logger.info("GET /health 200")

    assert not access_logger.hasHandlers()  # uvicorn checks this before logging access lines
    assert capsys.readouterr().out == ""


async def test_successful_health_checks_log_at_debug_and_failures_at_info(
    capsys: pytest.CaptureFixture[str], make_client: ClientFactory, parse_log_lines: LogParser
) -> None:
    configure_logging("INFO")
    app = FastAPI()
    add_request_logging(app)
    healthy = True

    @app.get("/health")
    async def health() -> Response:
        return Response(status_code=200 if healthy else 503)

    async with make_client(app) as client:
        await client.get("/health")
        healthy = False
        await client.get("/health")

    request_lines = [
        line
        for line in parse_log_lines(capsys.readouterr().out)
        if line["message"] == "request completed"
    ]
    assert [line["status_code"] for line in request_lines] == [503]


async def test_each_request_is_logged_once_without_query_string(
    capsys: pytest.CaptureFixture[str], make_client: ClientFactory, parse_log_lines: LogParser
) -> None:
    async with make_client(logging_app()) as client:
        await client.get("/items/7", params={"search": "private words"})

    request_lines = [
        line
        for line in parse_log_lines(capsys.readouterr().out)
        if line["message"] == "request completed"
    ]
    assert len(request_lines) == 1
    line = request_lines[0]
    assert line["method"] == "GET"
    assert line["path"] == "/items/7"
    assert line["status_code"] == 200
    assert isinstance(line["duration_ms"], int)
    assert "private words" not in json.dumps(line)


async def test_request_id_is_returned_and_tags_every_line_of_the_request(
    capsys: pytest.CaptureFixture[str], make_client: ClientFactory, parse_log_lines: LogParser
) -> None:
    async with make_client(logging_app()) as client:
        response = await client.get("/items/7")

    request_id = response.headers[REQUEST_ID_HEADER]
    lines = [  # the test's own HTTP client logs too; keep the app's lines
        line
        for line in parse_log_lines(capsys.readouterr().out)
        if str(line["logger"]).startswith("app.")
    ]
    assert {line["message"] for line in lines} == {"item read", "request completed"}
    assert all(line["request_id"] == request_id for line in lines)


async def test_safe_incoming_request_id_is_reused(make_client: ClientFactory) -> None:
    async with make_client(logging_app()) as client:
        response = await client.get("/items/7", headers={REQUEST_ID_HEADER: "abc-123_DEF"})

    assert response.headers[REQUEST_ID_HEADER] == "abc-123_DEF"


@pytest.mark.parametrize("unsafe_id", ["has spaces", "x" * 65, "line\\nbreak", "semi;colon"])
async def test_unsafe_incoming_request_id_is_replaced(
    unsafe_id: str, make_client: ClientFactory
) -> None:
    async with make_client(logging_app()) as client:
        response = await client.get("/items/7", headers={REQUEST_ID_HEADER: unsafe_id})

    assert response.headers[REQUEST_ID_HEADER] != unsafe_id
    assert len(response.headers[REQUEST_ID_HEADER]) == 32
