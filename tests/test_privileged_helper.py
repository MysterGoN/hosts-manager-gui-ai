import json
import os
import socket
import threading
from pathlib import Path

import pytest

import hmg.privileged_helper as helper


def test_privileged_helper_writes_only_its_system_hosts_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "hosts"
    target.write_text("old\n", encoding="utf-8")
    token_path = tmp_path / "token.json"
    token_path.write_text('{"token": "secret"}', encoding="utf-8")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    monkeypatch.setattr(helper, "hosts_path", lambda: target)

    result: list[int] = []
    worker = threading.Thread(
        target=lambda: result.append(helper.serve(port, token_path, 10, os.getpid())),
    )
    worker.start()
    connection, _address = listener.accept()
    stream = connection.makefile("rwb")

    hello = json.loads(stream.readline().decode("utf-8"))
    assert hello == {"token": "secret"}
    stream.write(b'{"action":"write_hosts","content":"new\\n"}\n')
    stream.flush()
    response = json.loads(stream.readline().decode("utf-8"))
    assert response["ok"] is True
    assert target.read_text(encoding="utf-8") == "new\n"
    assert Path(response["backup"]).read_text(encoding="utf-8") == "old\n"

    stream.write(b'{"action":"close"}\n')
    stream.flush()
    worker.join(timeout=2)
    stream.close()
    connection.close()
    listener.close()

    assert result == [0]


def test_privileged_helper_rejects_excessive_session_lifetime(tmp_path: Path) -> None:
    assert helper.serve(1234, tmp_path / "missing", helper.MAX_TTL_SECONDS + 1, os.getpid()) == 2
