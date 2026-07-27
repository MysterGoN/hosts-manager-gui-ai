from datetime import datetime, timedelta
from pathlib import Path

import pytest

from hmg.logging import cleanup_old_logs, configure_logging, get_logger
from hmg.settings import AppSettings


def make_settings(tmp_path: Path, **overrides: object) -> AppSettings:
    values: dict[str, object] = {
        "data_dir": str(tmp_path / "data"),
        "log_dir": str(tmp_path / "logs"),
        "log_level": "INFO",
        "log_max_bytes": 1024,
        "log_backup_count": 2,
        "log_retention_days": 30,
        "log_to_file_in_dev": False,
    }
    values.update(overrides)
    return AppSettings(**values)  # type: ignore[arg-type]


def test_development_logging_writes_to_stdout_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = make_settings(tmp_path)
    log_path = configure_logging(settings, packaged=False)

    get_logger("test").info("event_happened", value=1)

    captured = capsys.readouterr()
    assert '"event": "event_happened"' in captured.out
    assert captured.err == ""
    assert log_path is None
    assert not settings.log_path.exists()


def test_packaged_logging_writes_to_rotating_file_without_stderr(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = make_settings(tmp_path)
    log_path = configure_logging(settings, packaged=True)

    get_logger("test").info("event_happened", value=1)

    captured = capsys.readouterr()
    assert captured.err == ""
    assert log_path is not None
    assert '"value": 1' in log_path.read_text(encoding="utf-8")


def test_file_logging_rotates_by_configured_size(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, log_max_bytes=200, log_backup_count=2)
    configure_logging(settings, packaged=True)
    logger = get_logger("rotation")

    for index in range(20):
        logger.info("long_event", index=index, payload="x" * 100)

    assert (settings.log_path / "hmg.log.1").exists()


def test_old_rotated_logs_are_removed_by_retention_period(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    old_log = log_dir / "hmg.log.2"
    old_log.write_text("old", encoding="utf-8")
    old_timestamp = (datetime.now() - timedelta(days=40)).timestamp()
    old_log.touch()
    import os

    os.utime(old_log, (old_timestamp, old_timestamp))

    cleanup_old_logs(log_dir, 30)

    assert not old_log.exists()
