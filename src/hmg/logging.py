from __future__ import annotations

import json
import logging
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any


class HmgLogger:
    _configured = False
    _log_path: Path | None = None
    _structlog: Any = None

    def __init__(self, name: str) -> None:
        self.name = name
        self._backend = self._build_backend(name)

    @classmethod
    def configure(cls, log_path: Path, level: int = logging.INFO) -> None:
        cls._log_path = log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            import structlog  # type: ignore[import-not-found]
        except ImportError:
            cls._structlog = None
            cls._configured = True
            return

        logging.basicConfig(
            level=level,
            format="%(message)s",
            handlers=[
                logging.FileHandler(log_path, encoding="utf-8"),
                logging.StreamHandler(sys.stderr),
            ],
            force=True,
        )
        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(ensure_ascii=False),
            ],
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=True,
        )
        cls._structlog = structlog
        cls._configured = True

    @classmethod
    def _build_backend(cls, name: str) -> Any:
        if cls._structlog is None:
            return None
        return cls._structlog.get_logger(name)

    def info(self, event: str, **kwargs: Any) -> None:
        self._log("info", event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        self._log("warning", event, **kwargs)

    def exception(self, event: str, **kwargs: Any) -> None:
        kwargs.setdefault("exception", traceback.format_exc())
        self._log("error", event, **kwargs)

    def _log(self, level: str, event: str, **kwargs: Any) -> None:
        if self._backend is None:
            self._print_log(level, event, **kwargs)
            return
        method = getattr(self._backend, level)
        method(event, **kwargs)

    def _print_log(self, level: str, event: str, **kwargs: Any) -> None:
        payload = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "level": level,
            "logger": self.name,
            "event": event,
            **kwargs,
        }
        line = json.dumps(payload, ensure_ascii=False, default=str)
        print(line, file=sys.stderr)
        if self._log_path is not None:
            with self._log_path.open("a", encoding="utf-8") as file:
                file.write(line + "\n")


def configure_logging(log_path: Path, level: int = logging.INFO) -> None:
    HmgLogger.configure(log_path, level)


def get_logger(name: str) -> HmgLogger:
    return HmgLogger(name)
