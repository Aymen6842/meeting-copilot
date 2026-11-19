"""Checks against the running compose stack. Opt-in: `uv run pytest -m stack`.

Start the stack first with `docker compose up -d --build`.
"""

import json
import subprocess
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING

import boto3
import pytest
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from botocore.exceptions import ClientError

from tests.stack.compose_files import (
    ENV_EXAMPLE_FILE,
    REPO_ROOT,
    is_one_shot,
    read_env_file,
    services,
)

if TYPE_CHECKING:  # stub-only package, installed with the dev dependencies
    from mypy_boto3_s3 import S3Client

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


def app_storage_client() -> "S3Client":
    """S3 client with the API's own credentials, as storage-init created them."""
    return boto3.client(
        "s3",
        endpoint_url=STORAGE_URL,
        region_name="us-east-1",
        aws_access_key_id=env_value("STORAGE_APP_ACCESS_KEY"),
        aws_secret_access_key=env_value("STORAGE_APP_SECRET_KEY"),
    )


def compose_output(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed docker compose command, test-defined arguments
        ["docker", "compose", *arguments],  # noqa: S607 - docker from PATH
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_app_user_can_manage_objects_in_the_bucket() -> None:
    client, bucket, key = app_storage_client(), bucket_name(), f"stack-test/{uuid.uuid4()}.wav"

    client.head_bucket(Bucket=bucket)
    client.put_object(Bucket=bucket, Key=key, Body=b"audio", ContentType="audio/wav")
    assert client.head_object(Bucket=bucket, Key=key)["ContentLength"] == len(b"audio")
    assert client.get_object(Bucket=bucket, Key=key)["Body"].read() == b"audio"
    client.delete_object(Bucket=bucket, Key=key)
    with pytest.raises(ClientError, match="404"):
        client.head_object(Bucket=bucket, Key=key)


ForbiddenCall = Callable[["S3Client", str], object]
FORBIDDEN_CALLS: dict[str, ForbiddenCall] = {
    "create a bucket": lambda client, _: client.create_bucket(Bucket=f"other-{uuid.uuid4().hex}"),
    "delete the bucket": lambda client, bucket: client.delete_bucket(Bucket=bucket),
    "change the bucket policy": lambda client, bucket: client.put_bucket_policy(
        Bucket=bucket, Policy="{}"
    ),
    "change the bucket cors": lambda client, bucket: client.delete_bucket_cors(Bucket=bucket),
}


@pytest.mark.parametrize("call", FORBIDDEN_CALLS.values(), ids=FORBIDDEN_CALLS.keys())
def test_app_user_cannot_administer_storage(call: ForbiddenCall) -> None:
    with pytest.raises(ClientError, match="AccessDenied"):
        call(app_storage_client(), bucket_name())


def test_app_user_sees_only_its_bucket() -> None:
    buckets = app_storage_client().list_buckets()["Buckets"]

    assert [bucket.get("Name") for bucket in buckets] == [bucket_name()]


def test_app_user_cannot_use_the_admin_api() -> None:
    result = compose_output(
        "run",
        "--rm",
        "--no-deps",
        "--entrypoint",
        "sh",
        "storage-init",
        "-c",
        'rc alias set app "$STORAGE_ENDPOINT" "$STORAGE_APP_ACCESS_KEY" "$STORAGE_APP_SECRET_KEY"'
        " >/dev/null && rc admin user ls app",
    )

    assert result.returncode != 0
    assert "denied" in (result.stdout + result.stderr).lower()


def test_api_connects_with_the_app_user_not_root() -> None:
    result = compose_output("exec", "-T", "api", "printenv", "APP_STORAGE__ACCESS_KEY")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == env_value("STORAGE_APP_ACCESS_KEY")
    assert result.stdout.strip() != env_value("STORAGE_ROOT_USER")


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
