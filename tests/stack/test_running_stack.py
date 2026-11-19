"""Checks against the running compose stack. Opt-in: `uv run pytest -m stack`.

Start the stack first with `docker compose up -d --build`.
"""

import json
import subprocess
import urllib.error
import urllib.request
import uuid

import pytest
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory

from tests.stack.compose_files import (
    ENV_EXAMPLE_FILE,
    REPO_ROOT,
    is_one_shot,
    read_env_file,
    services,
)

pytestmark = pytest.mark.stack

STORAGE_URL = "http://127.0.0.1:9000"
ALEMBIC_INI = REPO_ROOT / "apps" / "api" / "alembic.ini"


def compose_status() -> dict[str, dict[str, object]]:
    result = subprocess.run(
        ["docker", "compose", "ps", "--all", "--format", "json"],  # noqa: S607 - docker from PATH
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    return {row["Service"]: row for row in rows}


def env_value(name: str) -> str:
    env_file = REPO_ROOT / ".env"
    return read_env_file(env_file if env_file.exists() else ENV_EXAMPLE_FILE)[name]


def bucket_name() -> str:
    return env_value("STORAGE_BUCKET")


def upload_preflight_allowed_origin(origin: str) -> str | None:
    """Origin the storage allows for a browser PUT from `origin`, or None if refused."""
    request = urllib.request.Request(  # noqa: S310 - fixed http URL
        f"{STORAGE_URL}/{bucket_name()}/upload.wav",
        method="OPTIONS",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    allowed_origin: str | None
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            allowed_origin = response.headers.get("Access-Control-Allow-Origin")
    except urllib.error.HTTPError as error:
        allowed_origin = error.headers.get("Access-Control-Allow-Origin")
    return allowed_origin


def anonymous_get(path: str) -> tuple[int, str]:
    request = urllib.request.Request(f"{STORAGE_URL}{path}")  # noqa: S310 - fixed http URL
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def test_long_running_services_are_healthy() -> None:
    status = compose_status()

    for name, service in services().items():
        if is_one_shot(service):
            continue
        assert name in status, f"{name} is not running"
        assert status[name]["Health"] == "healthy", f"{name}: {status[name]['Status']}"


def test_storage_init_finished_successfully() -> None:
    init = compose_status()["storage-init"]

    assert init["State"] == "exited"
    assert init["ExitCode"] == 0


def test_bucket_exists_for_authenticated_clients() -> None:
    # Reuses the storage-init service: same AWS CLI image, credentials and bucket name.
    result = subprocess.run(
        [  # noqa: S607 - docker from PATH
            "docker",
            "compose",
            "run",
            "--rm",
            "--no-deps",
            "storage-init",
            'aws --endpoint-url http://storage:9000 s3api head-bucket --bucket "$STORAGE_BUCKET"',
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_bucket_refuses_anonymous_listing() -> None:
    status_code, body = anonymous_get(f"/{bucket_name()}")

    assert status_code == 403
    assert "AccessDenied" in body


def test_storage_does_not_reveal_which_buckets_exist() -> None:
    # Anonymous callers get the same answer for a missing bucket as for ours.
    status_code, body = anonymous_get(f"/no-such-bucket-{uuid.uuid4().hex[:12]}")

    assert status_code == 403
    assert "AccessDenied" in body


def test_storage_allows_browser_uploads_from_the_frontend() -> None:
    web_origin = env_value("WEB_ORIGIN")

    assert upload_preflight_allowed_origin(web_origin) == web_origin


def test_storage_refuses_browser_uploads_from_other_origins() -> None:
    assert upload_preflight_allowed_origin("https://unknown-site.example") is None


def test_objects_refuse_anonymous_reads() -> None:
    status_code, _ = anonymous_get(f"/{bucket_name()}/any-object.wav")

    assert status_code == 403


def test_migrate_finished_successfully() -> None:
    migrate = compose_status()["migrate"]

    assert migrate["State"] == "exited"
    assert migrate["ExitCode"] == 0


def test_database_is_at_the_latest_migration() -> None:
    script = ScriptDirectory.from_config(AlembicConfig(str(ALEMBIC_INI)))
    result = subprocess.run(
        [  # noqa: S607 - docker from PATH
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres",
            "sh",
            "-c",
            'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA'
            ' -c "SELECT version_num FROM alembic_version"',
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == script.get_current_head()
