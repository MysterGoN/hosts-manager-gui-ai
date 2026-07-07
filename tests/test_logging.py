from pathlib import Path

import pytest

from hmg.logging import HmgLogger


def test_logger_falls_back_to_print_backend(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log_path = tmp_path / "hmg.log"
    HmgLogger._structlog = None
    HmgLogger.configure(log_path)

    logger = HmgLogger("test")
    logger.info("event_happened", value=1)

    captured = capsys.readouterr()
    assert '"event": "event_happened"' in captured.err
    assert '"value": 1' in log_path.read_text(encoding="utf-8")
