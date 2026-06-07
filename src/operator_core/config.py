from __future__ import annotations

import os
from dataclasses import dataclass


_TRUE_VALUES = {"1", "true", "yes", "on"}


def _get_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _get_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_VALUES


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw.strip())


def _split_csv_env(name: str) -> tuple[str, ...]:
    raw = _get_str(name)
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class AppSettings:
    env: str
    log_level: str
    runtime_mode: str
    active_project: str
    content_ops_live_actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class TelegramSettings:
    enabled: bool
    bot_token: str
    allowed_user_ids: tuple[str, ...]
    allowed_chat_ids: tuple[str, ...]


@dataclass(frozen=True)
class AirtableSettings:
    enabled: bool
    api_key: str
    project_base_ids: dict[str, str]

    def get_base_id(self, project_key: str) -> str:
        return self.project_base_ids.get(project_key, "").strip()


@dataclass(frozen=True)
class OpenAISettings:
    enabled: bool
    api_key: str
    model: str
    base_url: str
    timeout_seconds: int


@dataclass(frozen=True)
class Settings:
    app: AppSettings
    telegram: TelegramSettings
    airtable: AirtableSettings
    openai: OpenAISettings

    @property
    def env(self) -> str:
        return self.app.env

    @property
    def log_level(self) -> str:
        return self.app.log_level

    @property
    def runtime_mode(self) -> str:
        return self.app.runtime_mode

    @property
    def active_project(self) -> str:
        return self.app.active_project

    def validate(self) -> None:
        errors: list[str] = []

        if not self.app.active_project:
            errors.append("OPERATOR_ACTIVE_PROJECT must not be empty")

        if self.telegram.enabled and not self.telegram.bot_token:
            errors.append(
                "TELEGRAM_BOT_TOKEN is required when OPERATOR_TELEGRAM_ENABLED=true"
            )

        if (
            self.telegram.enabled
            and not self.telegram.allowed_user_ids
            and not self.telegram.allowed_chat_ids
        ):
            errors.append(
                "ALLOWED_TELEGRAM_USER_IDS or ALLOWED_TELEGRAM_CHAT_IDS is required "
                "when OPERATOR_TELEGRAM_ENABLED=true (bot must not accept all senders)"
            )

        if self.airtable.enabled and not self.airtable.api_key:
            errors.append(
                "AIRTABLE_API_KEY is required when OPERATOR_AIRTABLE_ENABLED=true"
            )

        if self.airtable.enabled and not self.airtable.get_base_id(self.app.active_project):
            errors.append(
                "Airtable base ID is required for the active project when OPERATOR_AIRTABLE_ENABLED=true"
            )

        if self.openai.enabled and not self.openai.api_key:
            errors.append(
                "OPENAI_API_KEY is required when OPERATOR_OPENAI_ENABLED=true"
            )

        if self.openai.enabled and not self.openai.model:
            errors.append(
                "OPENAI_MODEL is required when OPERATOR_OPENAI_ENABLED=true"
            )

        if self.openai.timeout_seconds <= 0:
            errors.append("OPENAI_TIMEOUT_SECONDS must be greater than 0")

        if errors:
            raise ValueError(
                "Invalid operator core configuration:\n- " + "\n- ".join(errors)
            )


def load_settings() -> Settings:
    settings = Settings(
        app=AppSettings(
            env=_get_str("ENV", "development"),
            log_level=_get_str("LOG_LEVEL", "INFO"),
            runtime_mode=_get_str("OPERATOR_RUNTIME_MODE", "service"),
            active_project=_get_str("OPERATOR_ACTIVE_PROJECT", "everydayengel"),
            content_ops_live_actions=_split_csv_env("OPERATOR_CONTENT_OPS_LIVE_ACTIONS"),
        ),
        telegram=TelegramSettings(
            enabled=_get_bool("OPERATOR_TELEGRAM_ENABLED", False),
            bot_token=_get_str("TELEGRAM_BOT_TOKEN"),
            allowed_user_ids=_split_csv_env("ALLOWED_TELEGRAM_USER_IDS"),
            allowed_chat_ids=_split_csv_env("ALLOWED_TELEGRAM_CHAT_IDS"),
        ),
        airtable=AirtableSettings(
            enabled=_get_bool("OPERATOR_AIRTABLE_ENABLED", False),
            api_key=_get_str("AIRTABLE_API_KEY"),
            project_base_ids={
                "everydayengel": _get_str("EVERYDAYENGEL_AIRTABLE_BASE_ID"),
                "analytics": _get_str("ANALYTICS_AIRTABLE_BASE_ID"),
            },
        ),
        openai=OpenAISettings(
            enabled=_get_bool("OPERATOR_OPENAI_ENABLED", False),
            api_key=_get_str("OPENAI_API_KEY"),
            model=_get_str("OPENAI_MODEL"),
            base_url=_get_str("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            timeout_seconds=_get_int("OPENAI_TIMEOUT_SECONDS", 30),
        ),
    )

    settings.validate()
    return settings
