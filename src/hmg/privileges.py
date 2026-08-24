from __future__ import annotations

import json
import os
import platform
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, Protocol

from hmg.logging import get_logger
from hmg.settings import is_packaged
from hmg.tracing import traced

logger = get_logger(__name__)
MAX_PROTOCOL_BYTES = 12 * 1024 * 1024


class PrivilegedSessionError(RuntimeError):
    pass


class MessageStream(Protocol):
    def write(self, buffer: bytes, /) -> int: ...

    def flush(self) -> None: ...

    def readline(self, size: int = -1, /) -> bytes: ...

    def close(self) -> None: ...


def _send_message(stream: MessageStream, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
    if len(encoded) > MAX_PROTOCOL_BYTES:
        raise PrivilegedSessionError("Данные для записи превышают допустимый размер")
    stream.write(encoded)
    stream.flush()


def _read_message(stream: MessageStream) -> dict[str, Any]:
    line = stream.readline(MAX_PROTOCOL_BYTES + 1)
    if not line or len(line) > MAX_PROTOCOL_BYTES:
        raise PrivilegedSessionError("Привилегированная сессия неожиданно завершилась")
    payload = json.loads(line.decode("utf-8"))
    if not isinstance(payload, dict):
        raise PrivilegedSessionError("Некорректный ответ привилегированной сессии")
    return payload


def _helper_command(port: int, token_path: Path, ttl_seconds: int) -> list[str]:
    arguments = ["--elevated-helper", str(port), str(token_path), str(ttl_seconds)]
    if is_packaged():
        return [sys.executable, *arguments]
    return [sys.executable, "-m", "hmg.privileged_helper", *arguments[1:]]


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _launch_helper(command: list[str]) -> subprocess.Popen[str] | None:
    system = platform.system().lower()
    if system == "darwin":
        shell_command = f"{shlex.join(command)} >/dev/null 2>&1 &"
        result = subprocess.run(
            ["osascript", "-e", f"do shell script {json.dumps(shell_command)} with administrator privileges"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise PrivilegedSessionError(
                result.stderr.strip() or result.stdout.strip() or "Не удалось получить права администратора"
            )
        return None
    if system.startswith("win"):
        executable = _powershell_literal(command[0])
        argument_line = _powershell_literal(subprocess.list2cmdline(command[1:]))
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"Start-Process -FilePath {executable} -ArgumentList {argument_line} -Verb RunAs",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise PrivilegedSessionError(
                result.stderr.strip() or result.stdout.strip() or "Не удалось получить права администратора"
            )
        return None
    if shutil.which("pkexec") is None:
        raise PrivilegedSessionError("Утилита pkexec недоступна")
    return subprocess.Popen(
        ["pkexec", *command],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )


class PrivilegedSession:
    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds
        self.started_at = time.monotonic()
        self._socket: socket.socket | None = None
        self._stream: MessageStream | None = None
        self._process: subprocess.Popen[str] | None = None
        self._start()

    @property
    def active(self) -> bool:
        return (
            self._socket is not None
            and self._stream is not None
            and time.monotonic() - self.started_at < self.ttl_seconds
        )

    @traced("privileges.start_session")
    def _start(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(90)
        token = secrets.token_urlsafe(32)
        descriptor, raw_token_path = tempfile.mkstemp(prefix="hmg-auth-", suffix=".json")
        token_path = Path(raw_token_path)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as token_file:
                json.dump({"token": token}, token_file)
            os.chmod(token_path, 0o600)
            self._process = _launch_helper(
                _helper_command(listener.getsockname()[1], token_path, self.ttl_seconds)
            )
            connection, _address = listener.accept()
            connection.settimeout(30)
            stream = connection.makefile("rwb")
            hello = _read_message(stream)
            if not secrets.compare_digest(str(hello.get("token", "")), token):
                stream.close()
                connection.close()
                raise PrivilegedSessionError("Не удалось проверить привилегированную сессию")
            self._socket = connection
            self._stream = stream
            self.started_at = time.monotonic()
            logger.info("privileged_session_started", ttl_seconds=self.ttl_seconds)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.close()
            raise PrivilegedSessionError(f"Не удалось запустить привилегированную сессию: {exc}") from exc
        finally:
            listener.close()
            token_path.unlink(missing_ok=True)

    @traced("privileges.write_hosts")
    def write(self, content: str) -> Path:
        if not self.active or self._stream is None:
            raise PrivilegedSessionError("Срок привилегированной сессии истёк")
        try:
            _send_message(self._stream, {"action": "write_hosts", "content": content})
            response = _read_message(self._stream)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.close()
            raise PrivilegedSessionError(f"Привилегированная сессия прервана: {exc}") from exc
        if not response.get("ok"):
            raise PrivilegedSessionError(str(response.get("error") or "Не удалось записать hosts"))
        return Path(str(response["backup"]))

    def close(self) -> None:
        stream = self._stream
        self._stream = None
        connection = self._socket
        self._socket = None
        if stream is not None:
            with suppress(OSError, PrivilegedSessionError):
                _send_message(stream, {"action": "close"})
            stream.close()
        if connection is not None:
            connection.close()
        if self._process is not None and self._process.poll() is None:
            with suppress(subprocess.TimeoutExpired):
                self._process.wait(timeout=1)
        self._process = None


_session: PrivilegedSession | None = None


def authorization_session_active(ttl_seconds: int) -> bool:
    return _session is not None and _session.ttl_seconds == ttl_seconds and _session.active


def write_hosts_with_session(content: str, ttl_seconds: int) -> Path:
    global _session
    if _session is None or _session.ttl_seconds != ttl_seconds or not _session.active:
        close_authorization_session()
        _session = PrivilegedSession(ttl_seconds)
    try:
        return _session.write(content)
    except PrivilegedSessionError:
        close_authorization_session()
        raise


def close_authorization_session() -> None:
    global _session
    if _session is not None:
        _session.close()
        logger.info("privileged_session_closed")
    _session = None
