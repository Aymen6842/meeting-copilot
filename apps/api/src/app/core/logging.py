"""Structured JSON logging shared by the whole API.

Pass context through `extra`, for example
`logger.info("job queued", extra={"job_id": job_id, "meeting_id": meeting_id})`.
Never log audio content, transcript text or credentials.
"""

import json
import logging
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from datetime import UTC, datetime

from fastapi import FastAPI, Request, Response

REQUEST_ID_HEADER = "X-Request-ID"
HEALTH_PATH = "/health"
MAX_INCOMING_REQUEST_ID_LENGTH = 64

current_request_id: ContextVar[str | None] = ContextVar("current_request_id", default=None)

# Attributes every LogRecord has; anything else on a record came from `extra`.
# "color_message" is uvicorn's terminal-coloured copy of the message.
_EXCLUDED_RECORD_ATTRIBUTES = frozenset(
    vars(logging.LogRecord("", logging.INFO, "", 0, "", None, None))
) | {"message", "asctime", "taskName", "color_message"}

request_logger = logging.getLogger("app.request")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = current_request_id.get()
        if request_id is not None:
            payload["request_id"] = request_id
        for key, value in vars(record).items():
            if key not in _EXCLUDED_RECORD_ATTRIBUTES and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str) -> None:
    """Send every log record, uvicorn's included, to stdout as one JSON object per line."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
    # The request middleware logs every request with its request id; uvicorn's access log would
    # duplicate each line. With no handlers and no propagation, uvicorn skips it entirely.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers = []
    access_logger.propagate = False


def add_request_logging(app: FastAPI) -> None:
    """Log one line per request and tag every log line in it with a request id."""

    @app.middleware("http")
    async def log_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = incoming_request_id(request) or uuid.uuid4().hex
        token = current_request_id.set(request_id)
        started = time.perf_counter_ns()
        try:
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = request_id
            log_request_completed(request, response.status_code, started)
            return response
        except Exception:
            request_logger.exception("unhandled error")
            log_request_completed(request, 500, started)
            raise
        finally:
            current_request_id.reset(token)


def incoming_request_id(request: Request) -> str | None:
    """Reuse a caller's request id only if it is short and plainly formatted."""
    value = request.headers.get(REQUEST_ID_HEADER, "")
    is_safe = 0 < len(value) <= MAX_INCOMING_REQUEST_ID_LENGTH and all(
        character.isalnum() or character in "-_" for character in value
    )
    return value if is_safe else None


def log_request_completed(request: Request, status_code: int, started: int) -> None:
    duration_ms = (time.perf_counter_ns() - started) // 1_000_000
    # The container health check calls /health every few seconds; only failures are news.
    is_routine_health_check = request.url.path == HEALTH_PATH and status_code < 400
    request_logger.log(
        logging.DEBUG if is_routine_health_check else logging.INFO,
        "request completed",
        extra={
            "method": request.method,
            "path": request.url.path,  # no query string: it may carry user input
            "status_code": status_code,
            "duration_ms": duration_ms,
        },
    )
