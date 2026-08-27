"""Загрузка настроек из config.yaml и переменных окружения."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

def _find_root() -> Path:
    """Корень проекта.

    Раскладка может быть двух видов: код в подпапке src/ (как в репозитории)
    либо всё вповалку в одной папке (так получается при загрузке файлов
    через веб-интерфейс GitHub). Ориентируемся на config.yaml.
    """
    here = Path(__file__).resolve().parent
    for candidate in (here.parent, here):
        if (candidate / "config.yaml").exists():
            return candidate
    return here.parent


ROOT = _find_root()
load_dotenv(ROOT / ".env")


class ConfigError(RuntimeError):
    """Что-то не заполнено или заполнено неправильно."""


@dataclass
class Settings:
    direct_token: str
    direct_client_login: str | None
    metrika_token: str | None
    metrika_counter_id: str | None
    metrika_goal_ids: list[str]
    mode: str
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    rules: dict = field(default_factory=dict)

    @property
    def apply_changes(self) -> bool:
        return self.mode == "apply"

    @property
    def metrika_enabled(self) -> bool:
        return bool(self.metrika_token and self.metrika_counter_id)


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def load_settings(config_path: Path | None = None) -> Settings:
    path = config_path or (ROOT / "config.yaml")
    with open(path, encoding="utf-8") as fh:
        rules = yaml.safe_load(fh) or {}

    direct_token = _env("DIRECT_TOKEN")
    if not direct_token:
        raise ConfigError(
            "Не задан DIRECT_TOKEN. Это OAuth-токен Яндекс Директа — "
            "впиши его в файл .env рядом с проектом (или в переменные окружения, "
            "если запускаешь в облаке)."
        )

    mode = _env("MODE", "dry-run").lower()
    if mode not in {"dry-run", "apply"}:
        raise ConfigError(f"MODE должен быть 'dry-run' или 'apply', а не '{mode}'.")

    goals = [g.strip() for g in _env("METRIKA_GOAL_IDS").split(",") if g.strip()]

    return Settings(
        direct_token=direct_token,
        direct_client_login=_env("DIRECT_CLIENT_LOGIN") or None,
        metrika_token=_env("METRIKA_TOKEN") or None,
        metrika_counter_id=_env("METRIKA_COUNTER_ID") or None,
        metrika_goal_ids=goals,
        mode=mode,
        telegram_bot_token=_env("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=_env("TELEGRAM_CHAT_ID") or None,
        rules=rules,
    )
