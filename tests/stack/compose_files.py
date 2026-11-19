"""Helpers for reading the compose file and the env example from the repository root."""

import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
ENV_EXAMPLE_FILE = REPO_ROOT / ".env.example"

# ${NAME}, ${NAME:?message}, ${NAME:-default}; "$${NAME}" is escaped and resolved in-container.
COMPOSE_VARIABLE = re.compile(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*)")


def load_compose() -> dict[str, Any]:  # Any: parsed YAML has no fixed schema
    loaded = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def services() -> dict[str, dict[str, Any]]:  # Any: parsed YAML has no fixed schema
    compose_services = load_compose()["services"]
    assert isinstance(compose_services, dict)
    return compose_services


def is_one_shot(service: dict[str, Any]) -> bool:  # Any: parsed YAML has no fixed schema
    return service.get("restart") == "no"


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name, separator, value = stripped.partition("=")
        assert separator, f"malformed line in {path.name}: {line!r}"
        values[name.strip()] = value.strip()
    return values


def compose_variables() -> set[str]:
    return set(COMPOSE_VARIABLE.findall(COMPOSE_FILE.read_text(encoding="utf-8")))
