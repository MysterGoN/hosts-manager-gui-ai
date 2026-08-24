from __future__ import annotations

import functools
import logging
import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")

logger = logging.getLogger(__name__)
_enabled = os.environ.get("HMG_TRACE", "").strip().casefold() in {"1", "true", "yes", "on"}


def configure_tracing(enabled: bool) -> None:
    """Enable timing traces without changing the regular logging level."""
    global _enabled
    _enabled = enabled or os.environ.get("HMG_TRACE", "").strip().casefold() in {"1", "true", "yes", "on"}


def tracing_enabled() -> bool:
    return _enabled


@contextmanager
def trace(node: str, **fields: Any) -> Iterator[None]:
    if not _enabled:
        yield
        return

    started = time.perf_counter()
    common = {"node": node, "thread": threading.current_thread().name, **fields}
    logger.info("trace_started", extra={"hmg_fields": common})
    try:
        yield
    except BaseException as exc:
        logger.info(
            "trace_finished",
            extra={
                "hmg_fields": {
                    **common,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    "status": "error",
                    "error_type": type(exc).__name__,
                }
            },
        )
        raise
    logger.info(
        "trace_finished",
        extra={
            "hmg_fields": {
                **common,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                "status": "ok",
            }
        },
    )


def traced(node: str | None = None) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Trace a logical function while keeping its body free of timing boilerplate."""

    def decorator(function: Callable[P, R]) -> Callable[P, R]:
        trace_name = node or f"{function.__module__}.{function.__qualname__}"

        @functools.wraps(function)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            with trace(trace_name):
                return function(*args, **kwargs)

        return wrapper

    return decorator
