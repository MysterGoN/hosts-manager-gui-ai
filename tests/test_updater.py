from __future__ import annotations

import hashlib
import sys
import urllib.error
from email.message import Message
from pathlib import Path

import pytest

import hmg.updater as updater
from hmg.updater import (
    PreparedUpdate,
    ReleaseAsset,
    ReleaseInfo,
    archive_asset_name,
    build_update_launch,
    checksum_for_asset,
    current_version,
    fetch_latest_release,
    is_update_available,
    parse_release_payload,
    parse_version,
    prepare_update,
)


def release_info(*, assets: dict[str, ReleaseAsset] | None = None) -> ReleaseInfo:
    return ReleaseInfo(
        tag_name="v0.4.0",
        version=(0, 4, 0),
        name="v0.4.0",
        notes="Update notes",
        html_url="https://github.com/MysterGoN/hosts-manager-gui/releases/tag/v0.4.0",
        assets=assets or {},
    )


def test_parse_version_and_update_comparison() -> None:
    assert parse_version("v0.4.0") == (0, 4, 0)
    assert parse_version("0.3.9") == (0, 3, 9)
    assert is_update_available(release_info(), "0.3.0")
    assert not is_update_available(release_info(), "0.4.0")

    with pytest.raises(ValueError, match="Некорректная версия"):
        parse_version("0.4")


def test_platform_archive_names() -> None:
    assert archive_asset_name("Linux") == "hosts-manager-gui-linux.tar.gz"
    assert archive_asset_name("Windows") == "hosts-manager-gui-windows.zip"
    assert archive_asset_name("Darwin") == "hosts-manager-gui-macos.tar.gz"


def test_current_version_matches_project_metadata() -> None:
    assert parse_version(current_version())


def test_current_version_uses_bundled_pyproject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "9.8.7"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert current_version() == "9.8.7"


def test_parse_release_payload_validates_release_and_assets() -> None:
    payload = {
        "tag_name": "v0.4.0",
        "name": "Version 0.4.0",
        "body": "Notes",
        "html_url": "https://github.com/MysterGoN/hosts-manager-gui/releases/tag/v0.4.0",
        "assets": [
            {
                "name": "install-linux.sh",
                "browser_download_url": (
                    "https://github.com/MysterGoN/hosts-manager-gui/releases/download/v0.4.0/install-linux.sh"
                ),
                "size": 100,
                "digest": "sha256:" + "a" * 64,
            }
        ],
    }

    parsed = parse_release_payload(payload)

    assert parsed.tag_name == "v0.4.0"
    assert parsed.version == (0, 4, 0)
    assert parsed.name == "Version 0.4.0"
    assert parsed.assets["install-linux.sh"].size == 100


def test_parse_release_payload_rejects_unexpected_asset_url() -> None:
    payload = {
        "tag_name": "v0.4.0",
        "html_url": "https://github.com/MysterGoN/hosts-manager-gui/releases/tag/v0.4.0",
        "assets": [
            {
                "name": "install-linux.sh",
                "browser_download_url": "https://example.test/install-linux.sh",
                "size": 100,
            }
        ],
    }

    with pytest.raises(ValueError, match="неожиданный URL"):
        parse_release_payload(payload)


def test_fetch_latest_release_explains_private_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_request(*_args: object, **_kwargs: object) -> None:
        raise urllib.error.HTTPError(
            updater.LATEST_RELEASE_API_URL,
            404,
            "Not Found",
            hdrs=Message(),
            fp=None,
        )

    monkeypatch.setattr("hmg.updater.urllib.request.urlopen", fail_request)

    with pytest.raises(ConnectionError, match="приватные Releases без токена недоступны"):
        fetch_latest_release()


def test_checksum_for_asset_selects_exact_filename() -> None:
    digest = "a" * 64
    checksums = f"{'b' * 64}  other.sh\n{digest}  install-linux.sh\n"

    assert checksum_for_asset(checksums, "install-linux.sh") == digest


def test_prepare_update_downloads_and_verifies_installer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installer = b"#!/usr/bin/env bash\necho update\n"
    archive = b"application archive"
    installer_digest = hashlib.sha256(installer).hexdigest()
    archive_digest = hashlib.sha256(archive).hexdigest()
    checksums = (f"{installer_digest}  install-linux.sh\n{archive_digest}  hosts-manager-gui-linux.tar.gz\n").encode()
    assets = {
        "install-linux.sh": ReleaseAsset("install-linux.sh", "https://example.test/installer", len(installer)),
        "hosts-manager-gui-linux.tar.gz": ReleaseAsset(
            "hosts-manager-gui-linux.tar.gz",
            "https://example.test/archive",
            len(archive),
        ),
        "SHA256SUMS": ReleaseAsset("SHA256SUMS", "https://example.test/checksums", len(checksums)),
    }

    def fake_download(asset: ReleaseAsset, _maximum_bytes: int) -> bytes:
        return {
            "install-linux.sh": installer,
            "hosts-manager-gui-linux.tar.gz": archive,
            "SHA256SUMS": checksums,
        }[asset.name]

    update_dir = tmp_path / "hmg-update-test"

    def fake_mkdtemp(*, prefix: str) -> str:
        assert prefix == "hmg-update-"
        update_dir.mkdir()
        return str(update_dir)

    monkeypatch.setattr(updater, "download_asset", fake_download)
    monkeypatch.setattr("hmg.updater.tempfile.mkdtemp", fake_mkdtemp)

    prepared = prepare_update(release_info(assets=assets), system="Linux")

    assert prepared.installer_path.read_bytes() == installer
    assert prepared.archive_path.read_bytes() == archive
    assert prepared.checksums_path.read_bytes() == checksums
    assert prepared.installer_path.stat().st_mode & 0o700 == 0o700


def test_build_update_launch_uses_current_install_location(tmp_path: Path) -> None:
    prepared = PreparedUpdate(
        release_info(),
        tmp_path / "install-linux.sh",
        tmp_path / "hosts-manager-gui-linux.tar.gz",
        tmp_path / "SHA256SUMS",
    )

    linux = build_update_launch(
        prepared,
        Path("/opt/hosts-manager-gui/hosts-manager-gui"),
        123,
        system="Linux",
    )
    windows = build_update_launch(
        PreparedUpdate(
            release_info(),
            Path("C:/Temp/install-windows.ps1"),
            Path("C:/Temp/hosts-manager-gui-windows.zip"),
            Path("C:/Temp/SHA256SUMS"),
        ),
        Path("C:/Users/Test/AppData/Local/Programs/HostsManagerGUI/hosts-manager-gui.exe"),
        456,
        system="Windows",
    )
    macos = build_update_launch(
        PreparedUpdate(
            release_info(),
            tmp_path / "install-macos.sh",
            tmp_path / "hosts-manager-gui-macos.tar.gz",
            tmp_path / "SHA256SUMS",
        ),
        Path("/Users/test/Applications/Hosts Manager GUI.app/Contents/MacOS/hosts-manager-gui"),
        789,
        system="Darwin",
    )

    assert linux.command == ["/bin/bash", str(prepared.installer_path)]
    assert linux.environment["HMG_INSTALL_DIR"] == "/opt/hosts-manager-gui"
    assert linux.environment["HMG_WAIT_PID"] == "123"
    assert linux.environment["HMG_ARCHIVE_PATH"] == str(prepared.archive_path)
    assert linux.start_new_session
    assert "-ArchivePath" in windows.command
    assert "-WaitForProcessId" in windows.command
    assert windows.command[-1] == "456"
    assert macos.environment["HMG_APPLICATIONS_DIR"] == "/Users/test/Applications"
