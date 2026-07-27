from pathlib import Path

import pytest

import hmg.core as core_module
import hmg.settings as settings_module
from hmg.settings import AppSettings, get_settings, load_settings, save_settings


def test_settings_round_trip_in_platform_config_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "config" / "settings.json"
    monkeypatch.setattr(settings_module, "settings_file_path", lambda: path)
    settings_module._settings = None
    expected = AppSettings(
        data_dir=str(tmp_path / "custom-data"),
        log_dir=str(tmp_path / "custom-logs"),
        log_level="DEBUG",
        log_max_bytes=2 * 1024 * 1024,
        log_backup_count=7,
        log_retention_days=14,
        log_to_file_in_dev=True,
    )

    save_settings(expected)

    assert load_settings() == expected
    assert get_settings() == expected


def test_invalid_settings_file_falls_back_to_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{bad json", encoding="utf-8")
    monkeypatch.setattr(settings_module, "settings_file_path", lambda: path)
    monkeypatch.setattr(settings_module, "default_data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(settings_module, "default_log_dir", lambda: tmp_path / "logs")

    loaded = load_settings()

    assert loaded.data_path == tmp_path / "data"
    assert loaded.log_path == tmp_path / "logs"


def test_application_environment_can_override_initial_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HMG_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("HMG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HMG_LOG_DIR", str(tmp_path / "logs"))

    assert settings_module.settings_file_path() == tmp_path / "config" / "settings.json"
    assert settings_module.default_data_dir() == tmp_path / "data"
    assert settings_module.default_log_dir() == tmp_path / "logs"


def test_core_state_file_follows_configured_data_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = AppSettings(data_dir=str(tmp_path / "custom"), log_dir=str(tmp_path / "logs"))
    monkeypatch.setattr(settings_module, "_settings", configured)

    assert core_module.state_path() == tmp_path / "custom" / "state.json"
