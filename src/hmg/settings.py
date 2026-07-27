from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from platformdirs import PlatformDirs

APP_ID = "HostsManagerGUI"
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


def platform_directories() -> PlatformDirs:
    return PlatformDirs(APP_ID, appauthor=False, roaming=True, opinion=True)


def settings_file_path() -> Path:
    override = os.environ.get("HMG_CONFIG_DIR")
    if override:
        return Path(override).expanduser() / "settings.json"
    return Path(platform_directories().user_config_dir) / "settings.json"


def default_data_dir() -> Path:
    override = os.environ.get("HMG_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return Path(platform_directories().user_data_dir)


def default_log_dir() -> Path:
    override = os.environ.get("HMG_LOG_DIR")
    if override:
        return Path(override).expanduser()
    return Path(platform_directories().user_log_dir)


@dataclass(frozen=True)
class AppSettings:
    data_dir: str
    log_dir: str
    log_level: str = "INFO"
    log_max_bytes: int = 5 * 1024 * 1024
    log_backup_count: int = 5
    log_retention_days: int = 30
    log_to_file_in_dev: bool = False

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir).expanduser()

    @property
    def log_path(self) -> Path:
        return Path(self.log_dir).expanduser()

    def validate(self) -> AppSettings:
        if not self.data_dir.strip():
            raise ValueError("Каталог данных не указан")
        if not self.log_dir.strip():
            raise ValueError("Каталог логов не указан")
        if self.log_level not in LOG_LEVELS:
            raise ValueError(f"Недопустимый уровень логирования: {self.log_level}")
        if self.log_max_bytes < 64 * 1024:
            raise ValueError("Максимальный размер лога должен быть не меньше 64 КБ")
        if not 1 <= self.log_backup_count <= 100:
            raise ValueError("Количество архивов должно быть от 1 до 100")
        if not 1 <= self.log_retention_days <= 3650:
            raise ValueError("Срок хранения логов должен быть от 1 до 3650 дней")
        return self


_settings: AppSettings | None = None


def default_settings() -> AppSettings:
    return AppSettings(data_dir=str(default_data_dir()), log_dir=str(default_log_dir()))


def load_settings() -> AppSettings:
    path = settings_file_path()
    if not path.exists():
        return default_settings()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        defaults = default_settings()
        return AppSettings(
            data_dir=str(payload.get("data_dir", defaults.data_dir)),
            log_dir=str(payload.get("log_dir", defaults.log_dir)),
            log_level=str(payload.get("log_level", defaults.log_level)).upper(),
            log_max_bytes=int(payload.get("log_max_bytes", defaults.log_max_bytes)),
            log_backup_count=int(payload.get("log_backup_count", defaults.log_backup_count)),
            log_retention_days=int(payload.get("log_retention_days", defaults.log_retention_days)),
            log_to_file_in_dev=bool(payload.get("log_to_file_in_dev", defaults.log_to_file_in_dev)),
        ).validate()
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return default_settings()


def get_settings(*, refresh: bool = False) -> AppSettings:
    global _settings
    if _settings is None or refresh:
        _settings = load_settings()
    return _settings


def save_settings(settings: AppSettings) -> None:
    global _settings
    settings.validate()
    path = settings_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
    _settings = settings


def is_packaged() -> bool:
    return bool(getattr(sys, "frozen", False))


def log_level_value(settings: AppSettings) -> int:
    return int(getattr(logging, settings.log_level, logging.INFO))
