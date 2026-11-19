"""Application settings.

Precedence, lowest to highest:
1. `config/default.yaml`
2. `config/profiles/$APP_PROFILE.yaml`
3. environment variables: `APP_` prefix, `__` between nested keys
   (for example `APP_TRANSCRIPTION__MODEL=small`).

Secrets (database, Redis and storage credentials) come only from the environment.
"""

import os
from pathlib import Path
from typing import Annotated, Any, Literal, get_args

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

Profile = Literal["ci", "laptop", "desktop"]
PROFILES: tuple[str, ...] = get_args(Profile)

Probability = Annotated[float, Field(ge=0.0, le=1.0)]
PositiveInt = Annotated[int, Field(gt=0)]


class ConfigurationError(Exception):
    """The configuration files or environment are missing or invalid."""


class SettingsSection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TranscriptionSettings(SettingsSection):
    provider: Literal["local", "hosted", "fake", "cached"]
    model: str
    compute_type: Literal["float16", "int8", "int8_float16", "float32"]
    device: Literal["cuda", "cpu"]
    language_hint: Literal["auto", "en", "fr", "ar-tn", "mixed"]


class DiarizationSettings(SettingsSection):
    enabled: bool
    min_speakers: PositiveInt | None = None
    max_speakers: PositiveInt | None = None


class UncertaintySettings(SettingsSection):
    word_threshold: Probability
    segment_threshold: Probability


class WorkerSettings(SettingsSection):
    gpu_concurrency: PositiveInt
    chunk_seconds: PositiveInt
    max_attempts: PositiveInt


class LoggingSettings(SettingsSection):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class HealthSettings(SettingsSection):
    check_timeout_ms: PositiveInt


class DatabaseSettings(SettingsSection):
    pool_size: PositiveInt
    max_overflow: Annotated[int, Field(ge=0)]
    pool_timeout_seconds: PositiveInt


class StorageSettings(SettingsSection):
    endpoint_url: str
    access_key: SecretStr
    secret_key: SecretStr
    bucket: str
    region: str = "us-east-1"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_nested_delimiter="__",
        extra="forbid",
        frozen=True,
    )

    profile: Profile
    database_url: SecretStr
    database: DatabaseSettings
    redis_url: SecretStr
    storage: StorageSettings
    transcription: TranscriptionSettings
    diarization: DiarizationSettings
    uncertainty: UncertaintySettings
    worker: WorkerSettings
    logging: LoggingSettings
    health: HealthSettings

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Environment first, so it overrides the merged YAML passed in as init values.
        # No .env file source: compose and CI inject the environment explicitly.
        return env_settings, init_settings


def load_settings(config_dir: Path | None = None) -> Settings:
    profile = os.environ.get("APP_PROFILE", "")
    if profile not in PROFILES:
        choices = ", ".join(PROFILES)
        raise ConfigurationError(f"APP_PROFILE must be one of {choices}; got {profile!r}")

    directory = config_dir or Path(os.environ.get("APP_CONFIG_DIR", "config"))
    layered = merge_dicts(
        read_yaml_mapping(directory / "default.yaml"),
        read_yaml_mapping(directory / "profiles" / f"{profile}.yaml"),
    )
    return Settings(**layered)


def read_yaml_mapping(path: Path) -> dict[str, Any]:  # Any: YAML values are untyped until validated
    if not path.is_file():
        raise ConfigurationError(f"configuration file not found: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ConfigurationError(f"configuration file must contain a mapping: {path}")
    return loaded


def merge_dicts(
    base: dict[str, Any],  # Any: YAML values are untyped until validated
    override: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged
