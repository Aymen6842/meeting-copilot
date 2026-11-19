from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.core.config import (
    PROFILES,
    ConfigurationError,
    Settings,
    load_settings,
    merge_dicts,
    read_yaml_mapping,
)

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"

# The real default.yaml with known values for the keys these tests check, so new settings
# sections never need to be copied in here.
DEFAULT_OVERRIDES = {
    "transcription": {
        "provider": "local",
        "model": "default-model",
        "compute_type": "int8",
        "device": "cuda",
    },
    "worker": {"chunk_seconds": 600},
}

PROFILE_YAML = """
transcription:
  model: profile-model
  device: cpu
"""


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    default = merge_dicts(read_yaml_mapping(CONFIG_DIR / "default.yaml"), DEFAULT_OVERRIDES)
    (tmp_path / "profiles").mkdir()
    (tmp_path / "default.yaml").write_text(yaml.safe_dump(default), encoding="utf-8")
    (tmp_path / "profiles" / "ci.yaml").write_text(PROFILE_YAML, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("profile", PROFILES)
def test_every_shipped_profile_loads(profile: str, app_environment: pytest.MonkeyPatch) -> None:
    app_environment.setenv("APP_PROFILE", profile)

    settings = load_settings(CONFIG_DIR)

    assert settings.profile == profile


def test_ci_profile_uses_the_fake_provider_on_cpu(app_environment: pytest.MonkeyPatch) -> None:
    settings = load_settings(CONFIG_DIR)

    assert settings.transcription.provider == "fake"
    assert settings.transcription.device == "cpu"


def test_profile_overrides_default(config_dir: Path, app_environment: pytest.MonkeyPatch) -> None:
    settings = load_settings(config_dir)

    assert settings.transcription.model == "profile-model"
    assert settings.transcription.device == "cpu"


def test_environment_overrides_profile(
    config_dir: Path, app_environment: pytest.MonkeyPatch
) -> None:
    app_environment.setenv("APP_TRANSCRIPTION__MODEL", "env-model")

    settings = load_settings(config_dir)

    assert settings.transcription.model == "env-model"


def test_environment_overrides_default(
    config_dir: Path, app_environment: pytest.MonkeyPatch
) -> None:
    app_environment.setenv("APP_WORKER__CHUNK_SECONDS", "120")

    settings = load_settings(config_dir)

    assert settings.worker.chunk_seconds == 120


def test_overrides_keep_sibling_keys(config_dir: Path, app_environment: pytest.MonkeyPatch) -> None:
    app_environment.setenv("APP_TRANSCRIPTION__MODEL", "env-model")

    transcription = load_settings(config_dir).transcription

    assert transcription.device == "cpu"  # from the profile
    assert transcription.compute_type == "int8"  # from the default
    assert transcription.provider == "local"  # from the default


def test_unknown_key_in_config_file_is_rejected(
    config_dir: Path, app_environment: pytest.MonkeyPatch
) -> None:
    (config_dir / "profiles" / "ci.yaml").write_text(
        "transcription:\n  modle: typo\n", encoding="utf-8"
    )

    with pytest.raises(ValidationError, match="modle"):
        load_settings(config_dir)


def test_out_of_range_value_is_rejected(
    config_dir: Path, app_environment: pytest.MonkeyPatch
) -> None:
    app_environment.setenv("APP_UNCERTAINTY__WORD_THRESHOLD", "1.5")

    with pytest.raises(ValidationError, match="word_threshold"):
        load_settings(config_dir)


def test_missing_profile_is_a_clear_error(
    config_dir: Path, app_environment: pytest.MonkeyPatch
) -> None:
    app_environment.delenv("APP_PROFILE")

    with pytest.raises(ConfigurationError, match="APP_PROFILE must be one of ci, laptop, desktop"):
        load_settings(config_dir)


def test_unknown_profile_is_a_clear_error(
    config_dir: Path, app_environment: pytest.MonkeyPatch
) -> None:
    app_environment.setenv("APP_PROFILE", "server")

    with pytest.raises(ConfigurationError, match="'server'"):
        load_settings(config_dir)


def test_missing_profile_file_is_a_clear_error(
    config_dir: Path, app_environment: pytest.MonkeyPatch
) -> None:
    app_environment.setenv("APP_PROFILE", "desktop")

    with pytest.raises(ConfigurationError, match=r"desktop\.yaml"):
        load_settings(config_dir)


def test_missing_secret_is_rejected(config_dir: Path, app_environment: pytest.MonkeyPatch) -> None:
    app_environment.delenv("APP_DATABASE_URL")

    with pytest.raises(ValidationError, match="database_url"):
        load_settings(config_dir)


def test_secrets_never_appear_in_repr(app_environment: pytest.MonkeyPatch) -> None:
    rendered = repr(load_settings(CONFIG_DIR))

    for secret in ("test-password", "test-access-key", "test-secret-key"):
        assert secret not in rendered


def test_merge_dicts_merges_nested_mappings_without_mutating_inputs() -> None:
    base = {"a": {"x": 1, "y": 2}, "b": 1}
    override = {"a": {"y": 3}, "c": 4}

    merged = merge_dicts(base, override)

    assert merged == {"a": {"x": 1, "y": 3}, "b": 1, "c": 4}
    assert base == {"a": {"x": 1, "y": 2}, "b": 1}


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("APP_MEETINGS__MAX_UPLOAD_BYTES", str(5 * 1024**3 + 1)),  # over one S3 PUT
        ("APP_MEETINGS__MAX_UPLOAD_BYTES", "0"),
        ("APP_MEETINGS__MAX_DURATION_MS", "0"),
    ],
)
def test_meeting_limits_are_checked(
    app_environment: pytest.MonkeyPatch, name: str, value: str
) -> None:
    app_environment.setenv(name, value)

    with pytest.raises(ValidationError, match="max_"):
        load_settings(CONFIG_DIR)


def test_default_meeting_limits_match_the_spec(settings: Settings) -> None:
    assert settings.meetings.max_duration_ms == 3 * 3_600_000
    assert settings.meetings.max_upload_bytes == 4 * 1024**3
