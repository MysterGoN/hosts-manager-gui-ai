from __future__ import annotations

import json
import logging
import sys
import traceback
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from hmg.settings import AppSettings, is_packaged, log_level_value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        fields = getattr(record, "hmg_fields", {})
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class HmgLogger:
    def __init__(self, name: str) -> None:
        self.name = name
        self._logger = logging.getLogger(name)

    def info(self, event: str, **kwargs: Any) -> None:
        self._log(logging.INFO, event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        self._log(logging.WARNING, event, **kwargs)

    def exception(self, event: str, **kwargs: Any) -> None:
        kwargs.setdefault("exception", traceback.format_exc())
        self._log(logging.ERROR, event, **kwargs)

    def _log(self, level: int, event: str, **kwargs: Any) -> None:
        self._logger.log(level, event, extra={"hmg_fields": kwargs})


def configure_logging(settings: AppSettings, *, packaged: bool | None = None) -> Path | None:
    packaged_mode = is_packaged() if packaged is None else packaged
    formatter = JsonFormatter()
    handlers: list[logging.Handler] = []
    log_path: Path | None = None

    if not packaged_mode:
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(formatter)
        handlers.append(stream)

    if packaged_mode or settings.log_to_file_in_dev:
        settings.log_path.mkdir(parents=True, exist_ok=True)
        log_path = settings.log_path / "hmg.log"
        cleanup_old_logs(settings.log_path, settings.log_retention_seconds)
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

    logging.basicConfig(
        level=log_level_value(settings),
        handlers=handlers or [logging.NullHandler()],
        force=True,
    )
    return log_path


def cleanup_old_logs(log_dir: Path, retention_seconds: int, *, now: datetime | None = None) -> None:
    if not log_dir.exists():
        return
    cutoff = (now or datetime.now()) - timedelta(seconds=retention_seconds)
    for path in log_dir.glob("hmg.log.*"):
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime)
            if modified < cutoff:
                path.unlink()
        except OSError:
            continue


def get_logger(name: str) -> HmgLogger:
    return HmgLogger(name)
