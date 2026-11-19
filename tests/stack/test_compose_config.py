"""Static checks on docker-compose.yml and .env.example. No Docker needed."""

import json
import re
from urllib.parse import urlsplit

import pytest

from tests.stack.compose_files import (
    ENV_EXAMPLE_FILE,
    REPO_ROOT,
    compose_variables,
    is_one_shot,
    read_env_file,
    services,
)

STORAGE_INIT_DIR = REPO_ROOT / "infra" / "storage"
IN_CONTAINER_TMP = "/tmp"  # noqa: S108 - a path inside the storage-init container, not here
APP_POLICY_ACTIONS = {"s3:ListBucket", "s3:GetObject", "s3:PutObject", "s3:DeleteObject"}
SECRET_NAME = re.compile(r"PASSWORD|SECRET|_KEY|ROOT_USER")
PLACEHOLDER_PREFIX = "change-me"
PINNED_TAG = re.compile(r":[0-9][^:@]*$")  # a version tag, not "latest" or a bare name
EXPECTED_SERVICES = {
    "postgres",
    "redis",
    "storage",
    "storage-init",
    "migrate",
    "api",
    "worker",
    "web",
}


def test_stack_has_every_expected_service() -> None:
    assert set(services()) == EXPECTED_SERVICES


@pytest.mark.parametrize(
    "name", sorted(name for name, service in services().items() if not is_one_shot(service))
)
def test_long_running_service_has_healthcheck(name: str) -> None:
    healthcheck = services()[name].get("healthcheck")

    assert healthcheck, f"{name} has no healthcheck"
    assert healthcheck.get("test"), f"{name} healthcheck has no test command"


@pytest.mark.parametrize(
    "name", sorted(name for name, service in services().items() if "image" in service)
)
def test_image_is_pinned_to_a_version(name: str) -> None:
    image = services()[name]["image"]

    assert PINNED_TAG.search(image), f"{name} uses an unpinned image: {image}"


@pytest.mark.parametrize(
    "dockerfile",
    ["apps/api/Dockerfile", "apps/worker/Dockerfile", "apps/web/Dockerfile"],
)
def test_dockerfile_base_images_are_pinned(dockerfile: str) -> None:
    text = (REPO_ROOT / dockerfile).read_text(encoding="utf-8")
    images = re.findall(r"^FROM\s+(\S+)", text, flags=re.MULTILINE)
    images += re.findall(r"COPY\s+--from=(\S+)", text)

    assert images
    for image in images:
        assert PINNED_TAG.search(image), f"{dockerfile} uses an unpinned image: {image}"


@pytest.mark.parametrize(
    "name", sorted(name for name, service in services().items() if service.get("ports"))
)
def test_published_ports_bind_to_localhost_only(name: str) -> None:
    for port in services()[name]["ports"]:
        assert str(port).startswith("127.0.0.1:"), f"{name} publishes {port} on all interfaces"


def test_every_compose_variable_is_documented_in_env_example() -> None:
    missing = compose_variables() - set(read_env_file(ENV_EXAMPLE_FILE))

    assert not missing, f"add to .env.example: {sorted(missing)}"


def test_env_example_has_no_unused_variables() -> None:
    unused = set(read_env_file(ENV_EXAMPLE_FILE)) - compose_variables()

    assert not unused, f"not used by docker-compose.yml: {sorted(unused)}"


def test_env_example_secrets_are_placeholders() -> None:
    secrets = {
        name: value
        for name, value in read_env_file(ENV_EXAMPLE_FILE).items()
        if SECRET_NAME.search(name)
    }

    assert secrets, "expected credential variables in .env.example"
    for name, value in secrets.items():
        assert value.startswith(PLACEHOLDER_PREFIX), f"{name} must be a {PLACEHOLDER_PREFIX} value"


def test_real_env_file_is_gitignored() -> None:
    ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert ".env" in ignored
    assert "!.env.example" in ignored


def test_storage_init_never_grants_public_access() -> None:
    setup = (STORAGE_INIT_DIR / "init.sh").read_text(encoding="utf-8").lower()
    setup += (STORAGE_INIT_DIR / "app-policy.json").read_text(encoding="utf-8").lower()

    for forbidden in ("anonymous", "public", "put-bucket-policy", '"principal"'):
        assert forbidden not in setup


def test_only_storage_and_its_init_receive_the_root_credentials() -> None:
    receivers = {
        name
        for name, service in services().items()
        if "STORAGE_ROOT_" in json.dumps(service.get("environment", {}))
    }

    assert receivers == {"storage", "storage-init"}


@pytest.mark.parametrize("name", ["api", "worker"])
def test_app_services_use_the_bucket_scoped_storage_user(name: str) -> None:
    environment = services()[name]["environment"]

    assert environment["APP_STORAGE__ACCESS_KEY"] == "${STORAGE_APP_ACCESS_KEY}"
    assert environment["APP_STORAGE__SECRET_KEY"] == "${STORAGE_APP_SECRET_KEY}"


def test_app_policy_allows_only_object_access_in_the_bucket() -> None:
    policy = json.loads((STORAGE_INIT_DIR / "app-policy.json").read_text(encoding="utf-8"))
    grants = {
        (action, resource)
        for statement in policy["Statement"]
        if statement["Effect"] == "Allow"
        for action in statement["Action"]
        for resource in statement["Resource"]
    }

    assert {statement["Effect"] for statement in policy["Statement"]} == {"Allow"}
    assert {action for action, _ in grants} == APP_POLICY_ACTIONS
    assert grants == {("s3:ListBucket", "arn:aws:s3:::__BUCKET__")} | {
        (action, "arn:aws:s3:::__BUCKET__/*") for action in APP_POLICY_ACTIONS - {"s3:ListBucket"}
    }


def test_storage_init_runs_the_setup_script_from_a_read_only_mount() -> None:
    init = services()["storage-init"]

    assert init["image"] == services()["storage"]["image"]
    assert init["volumes"] == ["./infra/storage:/init:ro"]
    assert init["entrypoint"] == ["sh", "/init/init.sh"]
    assert "set -eu" in (STORAGE_INIT_DIR / "init.sh").read_text(encoding="utf-8")


def test_storage_init_keeps_the_root_login_off_disk() -> None:
    init = services()["storage-init"]

    assert init["environment"]["HOME"] == IN_CONTAINER_TMP
    assert IN_CONTAINER_TMP in init["tmpfs"]


@pytest.mark.parametrize("name", ["api", "worker"])
def test_app_services_start_only_after_migrations_succeed(name: str) -> None:
    depends_on = services()[name]["depends_on"]

    assert depends_on["migrate"]["condition"] == "service_completed_successfully"


def test_migrate_receives_only_the_database_url() -> None:
    assert set(services()["migrate"]["environment"]) == {"APP_DATABASE_URL"}


def test_presigned_urls_are_signed_for_the_published_storage_port() -> None:
    public_url = urlsplit(services()["api"]["environment"]["APP_STORAGE__PUBLIC_ENDPOINT_URL"])
    published_ports = [str(port) for port in services()["storage"]["ports"]]

    assert public_url.hostname == "localhost"
    assert f"127.0.0.1:{public_url.port}:9000" in published_ports
