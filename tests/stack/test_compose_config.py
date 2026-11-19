"""Static checks on docker-compose.yml and .env.example. No Docker needed."""

import re

import pytest

from tests.stack.compose_files import (
    ENV_EXAMPLE_FILE,
    REPO_ROOT,
    compose_variables,
    is_one_shot,
    read_env_file,
    services,
)

SECRET_NAME = re.compile(r"PASSWORD|SECRET|_KEY|ROOT_USER")
PLACEHOLDER_PREFIX = "change-me"
PINNED_TAG = re.compile(r":[0-9][^:@]*$")  # a version tag, not "latest" or a bare name
EXPECTED_SERVICES = {"postgres", "redis", "storage", "storage-init", "api", "worker", "web"}


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
    init_command = str(services()["storage-init"]["command"]).lower()

    for forbidden in ("put-bucket-policy", "put-bucket-acl", "public-read", "anonymous"):
        assert forbidden not in init_command
