from __future__ import annotations

import hashlib
import hmac
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path, PurePath
from typing import Any

from hmg.sources import system_ssl_context
from hmg.tracing import traced

REPOSITORY = "MysterGoN/hosts-manager-gui-ai"
LATEST_RELEASE_API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
API_VERSION = "2026-03-10"
MAX_RELEASE_METADATA_BYTES = 1024 * 1024
MAX_INSTALLER_BYTES = 2 * 1024 * 1024
MAX_CHECKSUMS_BYTES = 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")

VersionTuple = tuple[int, int, int]


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    download_url: str
    size: int
    digest: str | None = None


@dataclass(frozen=True)
class ReleaseInfo:
    tag_name: str
    version: VersionTuple
    name: str
    notes: str
    html_url: str
    assets: dict[str, ReleaseAsset]

    @property
    def download_base_url(self) -> str:
        return f"https://github.com/{REPOSITORY}/releases/download/{self.tag_name}"


@dataclass(frozen=True)
class PreparedUpdate:
    release: ReleaseInfo
    installer_path: Path
    archive_path: Path
    checksums_path: Path


@dataclass(frozen=True)
class UpdateLaunch:
    command: list[str]
    environment: dict[str, str]
    creation_flags: int = 0
    start_new_session: bool = False


def parse_version(value: str) -> VersionTuple:
    match = VERSION_PATTERN.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"Некорректная версия: {value!r}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def current_version() -> str:
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and isinstance(bundle_dir, str):
        pyproject_path = Path(bundle_dir) / "pyproject.toml"
    else:
        pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if pyproject_path.exists():
        try:
            import tomllib

            payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
            project_value = payload["project"]["version"]
            if isinstance(project_value, str):
                parse_version(project_value)
                return project_value
        except (KeyError, OSError, ValueError):
            pass
    try:
        installed_version = package_version("hosts-manager-gui")
    except PackageNotFoundError:
        return "0.0.0"
    parse_version(installed_version)
    return installed_version


def is_update_available(release: ReleaseInfo, installed_version: str | None = None) -> bool:
    return release.version > parse_version(installed_version or current_version())


def parse_release_payload(payload: Any) -> ReleaseInfo:
    if not isinstance(payload, dict):
        raise ValueError("GitHub вернул некорректное описание релиза")

    tag_name = _required_string(payload, "tag_name")
    version = parse_version(tag_name)
    html_url = _required_string(payload, "html_url")
    expected_release_url = f"https://github.com/{REPOSITORY}/releases/tag/{tag_name}"
    if html_url != expected_release_url:
        raise ValueError("GitHub вернул неожиданный URL релиза")

    raw_assets = payload.get("assets")
    if not isinstance(raw_assets, list):
        raise ValueError("GitHub не вернул список Assets")
    assets: dict[str, ReleaseAsset] = {}
    expected_download_prefix = f"https://github.com/{REPOSITORY}/releases/download/{tag_name}/"
    for item in raw_assets:
        if not isinstance(item, dict):
            raise ValueError("GitHub вернул некорректный Asset")
        name = _required_string(item, "name")
        download_url = _required_string(item, "browser_download_url")
        if not download_url.startswith(expected_download_prefix):
            raise ValueError(f"GitHub вернул неожиданный URL для Asset {name!r}")
        size = item.get("size")
        if not isinstance(size, int) or size < 0:
            raise ValueError(f"GitHub вернул некорректный размер Asset {name!r}")
        digest = item.get("digest")
        if digest is not None and not isinstance(digest, str):
            raise ValueError(f"GitHub вернул некорректный digest Asset {name!r}")
        assets[name] = ReleaseAsset(name, download_url, size, digest)

    return ReleaseInfo(
        tag_name=tag_name,
        version=version,
        name=str(payload.get("name") or tag_name),
        notes=str(payload.get("body") or ""),
        html_url=html_url,
        assets=assets,
    )


@traced("updater.fetch_latest_release")
def fetch_latest_release(timeout: float = 15.0) -> ReleaseInfo:
    request = urllib.request.Request(
        LATEST_RELEASE_API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"HostsManagerGUI/{current_version()}",
            "X-GitHub-Api-Version": API_VERSION,
        },
    )
    try:
        with urllib.request.urlopen(  # noqa: S310
            request,
            timeout=timeout,
            context=system_ssl_context(),
        ) as response:
            raw = _read_limited(response, MAX_RELEASE_METADATA_BYTES, "Описание релиза слишком большое")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ConnectionError(
                "GitHub Release не найден (HTTP 404). Репозиторий и опубликованный Release "
                "должны быть публичными; приватные Releases без токена недоступны."
            ) from exc
        if exc.code == 403:
            raise ConnectionError(
                "GitHub API отклонил запрос (HTTP 403). Возможно, временно исчерпан лимит запросов."
            ) from exc
        raise ConnectionError(f"GitHub API вернул HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise ConnectionError(f"Не удалось подключиться к GitHub: {exc.reason}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("GitHub вернул некорректный JSON релиза") from exc
    return parse_release_payload(payload)


def installer_asset_name(system: str | None = None) -> str:
    current_system = (system or platform.system()).lower()
    if current_system == "darwin":
        return "install-macos.sh"
    if current_system.startswith("win"):
        return "install-windows.ps1"
    if current_system == "linux":
        return "install-linux.sh"
    raise ValueError(f"Автоматическое обновление не поддерживается на платформе {current_system!r}")


def archive_asset_name(system: str | None = None) -> str:
    current_system = (system or platform.system()).lower()
    if current_system == "darwin":
        return "hosts-manager-gui-macos.tar.gz"
    if current_system.startswith("win"):
        return "hosts-manager-gui-windows.zip"
    if current_system == "linux":
        return "hosts-manager-gui-linux.tar.gz"
    raise ValueError(f"Автоматическое обновление не поддерживается на платформе {current_system!r}")


def checksum_for_asset(checksums: str, asset_name: str) -> str:
    for line in checksums.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        digest, filename = parts
        if filename.lstrip("*") == asset_name and SHA256_PATTERN.fullmatch(digest):
            return digest.lower()
    raise ValueError(f"В SHA256SUMS нет контрольной суммы для {asset_name}")


@traced("updater.prepare_update")
def prepare_update(release: ReleaseInfo, system: str | None = None) -> PreparedUpdate:
    installer_name = installer_asset_name(system)
    archive_name = archive_asset_name(system)
    try:
        installer_asset = release.assets[installer_name]
        archive_asset = release.assets[archive_name]
        checksums_asset = release.assets["SHA256SUMS"]
    except KeyError as exc:
        raise ValueError(f"В релизе {release.tag_name} нет необходимых файлов обновления") from exc

    temporary_dir = Path(tempfile.mkdtemp(prefix="hmg-update-"))
    installer_path = temporary_dir / installer_name
    archive_path = temporary_dir / archive_name
    checksums_path = temporary_dir / "SHA256SUMS"
    try:
        installer_bytes = download_asset(installer_asset, MAX_INSTALLER_BYTES)
        archive_bytes = download_asset(archive_asset, MAX_ARCHIVE_BYTES)
        checksums_bytes = download_asset(checksums_asset, MAX_CHECKSUMS_BYTES)
        try:
            checksums = checksums_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Файл SHA256SUMS имеет некорректную кодировку") from exc
        for asset_name, content in (
            (installer_name, installer_bytes),
            (archive_name, archive_bytes),
        ):
            expected_digest = checksum_for_asset(checksums, asset_name)
            actual_digest = hashlib.sha256(content).hexdigest()
            if not hmac.compare_digest(actual_digest, expected_digest):
                raise ValueError(f"SHA-256 файла {asset_name} не совпадает")
        installer_path.write_bytes(installer_bytes)
        archive_path.write_bytes(archive_bytes)
        checksums_path.write_bytes(checksums_bytes)
        if installer_path.suffix == ".sh":
            installer_path.chmod(0o700)
    except Exception:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise
    return PreparedUpdate(release, installer_path, archive_path, checksums_path)


@traced("updater.download_asset")
def download_asset(asset: ReleaseAsset, maximum_bytes: int, timeout: float = 30.0) -> bytes:
    if asset.size > maximum_bytes:
        raise ValueError(f"Asset {asset.name!r} превышает допустимый размер")
    request = urllib.request.Request(
        asset.download_url,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": f"HostsManagerGUI/{current_version()}",
        },
    )
    with urllib.request.urlopen(  # noqa: S310
        request,
        timeout=timeout,
        context=system_ssl_context(),
    ) as response:
        raw = _read_limited(response, maximum_bytes, f"Asset {asset.name!r} слишком большой")
    if len(raw) != asset.size:
        raise ValueError(f"Размер скачанного Asset {asset.name!r} не совпадает")
    if asset.digest:
        algorithm, separator, expected = asset.digest.partition(":")
        if separator and algorithm == "sha256" and SHA256_PATTERN.fullmatch(expected):
            actual = hashlib.sha256(raw).hexdigest()
            if not hmac.compare_digest(actual, expected.lower()):
                raise ValueError(f"Digest Asset {asset.name!r} не совпадает")
    return raw


def build_update_launch(
    prepared: PreparedUpdate,
    executable_path: PurePath,
    process_id: int,
    *,
    system: str | None = None,
) -> UpdateLaunch:
    current_system = (system or platform.system()).lower()
    environment = os.environ.copy()
    if current_system.startswith("win"):
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(prepared.installer_path),
            "-ReleaseBaseUrl",
            prepared.release.download_base_url,
            "-InstallDir",
            str(executable_path.parent),
            "-ArchivePath",
            str(prepared.archive_path),
            "-ChecksumsPath",
            str(prepared.checksums_path),
            "-WaitForProcessId",
            str(process_id),
        ]
        creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return UpdateLaunch(command, environment, creation_flags=creation_flags)

    environment["HMG_RELEASE_BASE_URL"] = prepared.release.download_base_url
    environment["HMG_ARCHIVE_PATH"] = str(prepared.archive_path)
    environment["HMG_CHECKSUMS_PATH"] = str(prepared.checksums_path)
    environment["HMG_WAIT_PID"] = str(process_id)
    if current_system == "linux":
        environment["HMG_INSTALL_DIR"] = str(executable_path.parent)
    elif current_system == "darwin":
        app_bundle = next((parent for parent in executable_path.parents if parent.suffix == ".app"), None)
        if app_bundle is not None:
            environment["HMG_APPLICATIONS_DIR"] = str(app_bundle.parent)
    else:
        raise ValueError(f"Автоматическое обновление не поддерживается на платформе {current_system!r}")
    return UpdateLaunch(
        ["/bin/bash", str(prepared.installer_path)],
        environment,
        start_new_session=True,
    )


def launch_update(prepared: PreparedUpdate, executable_path: Path | None = None) -> None:
    launch = build_update_launch(
        prepared,
        executable_path or Path(sys.executable).resolve(),
        os.getpid(),
    )
    log_path = prepared.installer_path.parent / "update.log"
    with log_path.open("ab") as log_file:
        subprocess.Popen(  # noqa: S603
            launch.command,
            env=launch.environment,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=launch.creation_flags,
            start_new_session=launch.start_new_session,
            close_fds=True,
        )


def _read_limited(response: Any, maximum_bytes: int, error_message: str) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length and int(content_length) > maximum_bytes:
        raise ValueError(error_message)
    raw = bytes(response.read(maximum_bytes + 1))
    if len(raw) > maximum_bytes:
        raise ValueError(error_message)
    return raw


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"GitHub не вернул поле {key!r}")
    return value
