from __future__ import annotations

import json
import socket
import sys
import time
from pathlib import Path
from typing import Any, Protocol

from hmg.core import hosts_path, write_hosts

MAX_PROTOCOL_BYTES = 12 * 1024 * 1024
MAX_TTL_SECONDS = 60 * 60


class MessageStream(Protocol):
    def write(self, buffer: bytes, /) -> int: ...

    def flush(self) -> None: ...

    def readline(self, size: int = -1, /) -> bytes: ...

    def close(self) -> None: ...


def _send_message(stream: MessageStream, payload: dict[str, Any]) -> None:
    stream.write(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
    stream.flush()


def _read_message(stream: MessageStream) -> dict[str, Any] | None:
    line = stream.readline(MAX_PROTOCOL_BYTES + 1)
    if not line or len(line) > MAX_PROTOCOL_BYTES:
        return None
    payload = json.loads(line.decode("utf-8"))
    return payload if isinstance(payload, dict) else None


def serve(port: int, token_path: Path, ttl_seconds: int) -> int:
    if not 0 < ttl_seconds <= MAX_TTL_SECONDS or not 0 < port <= 65535:
        return 2
    try:
        token_payload = json.loads(token_path.read_text(encoding="utf-8"))
        token = str(token_payload["token"])
    except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError):
        return 2

    connection = socket.create_connection(("127.0.0.1", port), timeout=30)
    stream = connection.makefile("rwb")
    started_at = time.monotonic()
    try:
        _send_message(stream, {"token": token})
        while time.monotonic() - started_at < ttl_seconds:
            remaining = ttl_seconds - (time.monotonic() - started_at)
            connection.settimeout(max(0.1, remaining))
            try:
                request = _read_message(stream)
            except (OSError, ValueError, json.JSONDecodeError):
                break
            if request is None or request.get("action") == "close":
                break
            if request.get("action") != "write_hosts" or not isinstance(request.get("content"), str):
                _send_message(stream, {"ok": False, "error": "Недопустимая команда"})
                continue
            try:
                backup = write_hosts(hosts_path(), request["content"])
                _send_message(stream, {"ok": True, "backup": str(backup)})
            except Exception as exc:
                _send_message(stream, {"ok": False, "error": str(exc)})
    finally:
        stream.close()
        connection.close()
    return 0


def main(arguments: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if arguments is None else arguments)
    if len(args) != 3:
        return 2
    try:
        port = int(args[0])
        token_path = Path(args[1])
        ttl_seconds = int(args[2])
    except ValueError:
        return 2
    return serve(port, token_path, ttl_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
