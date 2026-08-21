from __future__ import annotations

import csv
import ipaddress
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path, PurePath
from typing import Literal, TypedDict

import idna

from hmg.logging import get_logger
from hmg.settings import get_settings

APP_NAME = "Hosts Manager GUI"
STATE_VERSION = 2
DEFAULT_GROUP_ID = "default"
DEFAULT_GROUP_NAME = "Default"
MANAGED_START = "###### HMG START ######"
MANAGED_END = "###### HMG END ######"
GENERATED_AT_PREFIX = "# Generated at "

DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?!-)[A-Za-z0-9_*.:-]{1,63}(?<!-)(?:\.(?!-)[A-Za-z0-9_*-]{1,63}(?<!-))*\.?$")
logger = get_logger(__name__)


class EntryDiff(TypedDict):
    added_domains: list[str]
    added_ips: dict[str, list[str]]
    removed_domains: list[str]


ImportFormat = Literal["json", "hosts", "delimited"]


class ElevatedWriteError(RuntimeError):
    pass


def hosts_path() -> Path:
    if platform.system().lower().startswith("win"):
        root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        return Path(root) / "System32" / "drivers" / "etc" / "hosts"
    return Path("/etc/hosts")


def state_path() -> Path:
    return get_settings().data_path / "state.json"


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def normalize_domain(domain: str) -> str:
    return domain.strip().lower().rstrip(".")


def domain_to_ascii(domain: str) -> str:
    normalized = normalize_domain(domain)
    labels: list[str] = []
    for label in normalized.split("."):
        if label.isascii() and not label.startswith("xn--"):
            labels.append(label)
            continue
        labels.append(idna.encode(label, uts46=True, std3_rules=True).decode("ascii"))
    return ".".join(labels)


def display_domain(domain: str) -> str:
    """Return a readable Unicode representation without changing the canonical key."""
    labels: list[str] = []
    for label in domain.split("."):
        if not label.lower().startswith("xn--"):
            labels.append(label)
            continue
        try:
            labels.append(idna.decode(label))
        except idna.IDNAError:
            labels.append(label)
    return ".".join(labels)


def domain_sort_key(domain: str) -> tuple[str, ...]:
    """Sort domains by labels from the top-level domain towards the host label."""
    return tuple(reversed(display_domain(domain).casefold().split(".")))


def host_entry_sort_key(entry: HostEntry) -> tuple[int, int, tuple[str, ...]]:
    address = ipaddress.ip_address(entry.selected_ip)
    return address.version, int(address), domain_sort_key(entry.domain)


def split_ips(value: str) -> list[str]:
    parts = re.split(r"[;\s]+", value.strip())
    return [p.strip() for p in parts if p.strip()]


def validate_ip(value: str) -> str:
    value = value.strip()
    try:
        ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError(f"Некорректный IP-адрес: {value}") from exc
    return value


def validate_domain(value: str) -> str:
    normalized = normalize_domain(value)
    if not normalized:
        raise ValueError("Домен не указан")
    try:
        domain = domain_to_ascii(normalized)
    except idna.IDNAError as exc:
        raise ValueError(f"Некорректное имя домена или хоста: {value}") from exc
    # hosts can contain local aliases without dots. We allow them.
    if not DOMAIN_RE.match(domain):
        raise ValueError(f"Некорректное имя домена или хоста: {value}")
    return domain


@dataclass
class HostGroup:
    id: str
    name: str
    enabled: bool = True

    def __post_init__(self) -> None:
        self.id = self.id.strip()
        self.name = self.name.strip()
        if not self.id:
            raise ValueError("Идентификатор группы не указан")
        if not self.name:
            raise ValueError("Название группы не указано")


def default_group() -> HostGroup:
    return HostGroup(DEFAULT_GROUP_ID, DEFAULT_GROUP_NAME)


@dataclass
class HostEntry:
    domain: str
    ips: list[str] = field(default_factory=list)
    selected_ip: str = ""
    enabled: bool = True
    group_id: str = DEFAULT_GROUP_ID

    def __post_init__(self) -> None:
        self.domain = validate_domain(self.domain)
        clean_ips: list[str] = []
        for ip in self.ips:
            ip = validate_ip(ip)
            if ip not in clean_ips:
                clean_ips.append(ip)
        self.ips = clean_ips
        if not self.ips:
            raise ValueError(f"Для домена {self.domain!r} нужен хотя бы один IP")
        if self.selected_ip:
            self.selected_ip = validate_ip(self.selected_ip)
        if not self.selected_ip or self.selected_ip not in self.ips:
            self.selected_ip = self.ips[0]
        self.group_id = self.group_id.strip() or DEFAULT_GROUP_ID

    def add_ips(self, new_ips: Iterable[str]) -> list[str]:
        added: list[str] = []
        for ip in new_ips:
            ip = validate_ip(ip)
            if ip not in self.ips:
                self.ips.append(ip)
                added.append(ip)
        if not self.selected_ip and self.ips:
            self.selected_ip = self.ips[0]
        return added

    def set_ips_replace(self, new_ips: Iterable[str]) -> tuple[list[str], list[str]]:
        clean: list[str] = []
        for ip in new_ips:
            ip = validate_ip(ip)
            if ip not in clean:
                clean.append(ip)
        if not clean:
            raise ValueError(f"Для домена {self.domain!r} нужен хотя бы один IP")
        old = set(self.ips)
        new = set(clean)
        added = sorted(new - old)
        removed = sorted(old - new)
        self.ips = clean
        if self.selected_ip not in self.ips:
            self.selected_ip = self.ips[0]
        return added, removed

    def active_line(self) -> str:
        return f"{self.selected_ip}\t{self.domain}"

    def disabled_line(self) -> str:
        return f"# {self.selected_ip}\t{self.domain}"


def parse_hosts_line(line: str, allow_disabled: bool = False) -> list[tuple[str, str, bool]]:
    """Return list of (domain, ip, enabled)."""
    raw = line.rstrip("\n")
    stripped = raw.strip()
    enabled = True

    if not stripped:
        return []

    if stripped.startswith("#"):
        if not allow_disabled:
            return []
        maybe = stripped[1:].strip()
        if not maybe or maybe.startswith("#"):
            return []
        stripped = maybe
        enabled = False

    # Remove inline comment after extracting potential disabled marker.
    body = stripped.split("#", 1)[0].strip()
    if not body:
        return []

    parts = body.split()
    if len(parts) < 2:
        return []

    ip_raw = parts[0]
    try:
        ip = validate_ip(ip_raw)
    except ValueError:
        return []

    result: list[tuple[str, str, bool]] = []
    for domain_raw in parts[1:]:
        try:
            domain = validate_domain(domain_raw)
        except ValueError:
            continue
        result.append((domain, ip, enabled))
    return result


def parse_hosts_text(text: str) -> dict[str, HostEntry]:
    entries: dict[str, HostEntry] = {}
    in_managed_block = False

    for line in text.splitlines():
        if line.strip() == MANAGED_START:
            in_managed_block = True
            continue
        if line.strip() == MANAGED_END:
            in_managed_block = False
            continue

        if not in_managed_block:
            continue

        for domain, ip, enabled in parse_hosts_line(line, allow_disabled=True):
            if domain not in entries:
                entries[domain] = HostEntry(domain=domain, ips=[ip], selected_ip=ip, enabled=enabled)
            else:
                entries[domain].add_ips([ip])
                # Prefer active mapping as selected if any active duplicate exists.
                if enabled:
                    entries[domain].selected_ip = ip
                    entries[domain].enabled = True
    logger.info("hosts_text_parsed", entries_count=len(entries))
    return entries


def normalize_groups(
    entries: dict[str, HostEntry],
    groups: Iterable[HostGroup],
) -> list[HostGroup]:
    normalized = [default_group()]
    ids = {DEFAULT_GROUP_ID}
    names = {DEFAULT_GROUP_NAME.casefold()}
    for group in groups:
        if group.id == DEFAULT_GROUP_ID:
            normalized[0].enabled = bool(group.enabled)
            continue
        name_key = group.name.casefold()
        if group.id in ids or name_key in names:
            continue
        normalized.append(HostGroup(group.id, group.name, bool(group.enabled)))
        ids.add(group.id)
        names.add(name_key)
    for entry in entries.values():
        if entry.group_id not in ids:
            entry.group_id = DEFAULT_GROUP_ID
    return normalized


def new_group(name: str) -> HostGroup:
    return HostGroup(uuid.uuid4().hex, name)


def load_state_with_groups() -> tuple[dict[str, HostEntry], list[HostGroup]]:
    path = state_path()
    if not path.exists():
        logger.info("state_load_missing", path=str(path))
        return {}, [default_group()]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        entries: dict[str, HostEntry] = {}
        for item in payload.get("entries", []):
            entry = HostEntry(
                domain=item["domain"],
                ips=list(item.get("ips", [])),
                selected_ip=item.get("selected_ip", ""),
                enabled=bool(item.get("enabled", True)),
                group_id=str(item.get("group_id", DEFAULT_GROUP_ID)),
            )
            entries[entry.domain] = entry
        groups: list[HostGroup] = []
        for item in payload.get("groups", []):
            groups.append(
                HostGroup(
                    id=str(item["id"]),
                    name=str(item["name"]),
                    enabled=bool(item.get("enabled", True)),
                )
            )
        groups = normalize_groups(entries, groups)
        logger.info(
            "state_loaded",
            path=str(path),
            entries_count=len(entries),
            groups_count=len(groups),
        )
        return entries, groups
    except Exception as exc:
        logger.warning("state_load_failed", path=str(path), error=str(exc))
        return {}, [default_group()]


def load_state() -> dict[str, HostEntry]:
    entries, _groups = load_state_with_groups()
    return entries


def save_state(entries: dict[str, HostEntry], groups: Iterable[HostGroup] | None = None) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized_groups = normalize_groups(entries, groups or [default_group()])
    payload = {
        "version": STATE_VERSION,
        "hosts_path": str(hosts_path()),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "groups": [asdict(group) for group in normalized_groups],
        "entries": [asdict(e) for e in sorted(entries.values(), key=lambda x: x.domain)],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(
        "state_saved",
        path=str(path),
        entries_count=len(entries),
        groups_count=len(normalized_groups),
    )


def read_hosts_file(path: Path) -> str:
    if not path.exists():
        logger.info("hosts_file_missing", path=str(path))
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    logger.info("hosts_file_read", path=str(path), bytes_count=len(text.encode("utf-8")))
    return text


def remove_managed_block(lines: list[str]) -> list[str]:
    out: list[str] = []
    in_block = False
    for line in lines:
        if line.strip() == MANAGED_START:
            in_block = True
            continue
        if line.strip() == MANAGED_END:
            in_block = False
            continue
        if not in_block:
            out.append(line)
    return out


def extract_managed_block(lines: list[str]) -> list[str] | None:
    block: list[str] = []
    in_block = False
    for line in lines:
        if line.strip() == MANAGED_START:
            block = [line]
            in_block = True
            continue
        if in_block:
            block.append(line)
            if line.strip() == MANAGED_END:
                return block
    return None


def build_managed_block(
    entries: dict[str, HostEntry],
    groups: Iterable[HostGroup] | None = None,
) -> str:
    lines = [
        MANAGED_START,
        f"# Managed by {APP_NAME}. Edit through the app when possible.",
        f"{GENERATED_AT_PREFIX}{datetime.now().isoformat(timespec='seconds')}",
    ]
    group_list = list(groups) if groups is not None else [default_group()]
    rendered_group = False
    for group in group_list:
        if not group.enabled:
            continue
        group_entries = [
            entry for entry in entries.values() if entry.enabled and (entry.group_id == group.id or groups is None)
        ]
        if not group_entries:
            continue
        if rendered_group:
            lines.append("")
        safe_group_name = " ".join(group.name.splitlines())
        lines.append(f"# Group: {safe_group_name}")
        lines.extend(entry.active_line() for entry in sorted(group_entries, key=host_entry_sort_key))
        rendered_group = True
    lines.append(MANAGED_END)
    return "\n".join(lines) + "\n"


def build_preserve_hosts_text(
    original_text: str,
    entries: dict[str, HostEntry],
    groups: Iterable[HostGroup] | None = None,
) -> str:
    lines = original_text.splitlines()
    current_block = extract_managed_block(lines)
    block = build_managed_block(entries, groups).rstrip()
    new_block = block.splitlines()
    if current_block and any(line.strip().startswith(GENERATED_AT_PREFIX) for line in current_block):
        current_content = [line for line in current_block if not line.strip().startswith(GENERATED_AT_PREFIX)]
        new_content = [line for line in new_block if not line.strip().startswith(GENERATED_AT_PREFIX)]
        if current_content == new_content:
            return original_text

    lines = remove_managed_block(lines)
    base = "\n".join(lines).rstrip()
    if base:
        return base + "\n\n" + block + "\n"
    return block + "\n"


def write_hosts(path: Path, content: str) -> Path:
    logger.info("hosts_write_started", path=str(path), bytes_count=len(content.encode("utf-8")))
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_name(f"{path.name}.{now_stamp()}.bak")
    if path.exists():
        shutil.copy2(path, backup)
    else:
        backup.write_text("", encoding="utf-8")
    with path.open("w", encoding="utf-8", newline="\n") as file:
        file.write(content)
    logger.info("hosts_write_finished", path=str(path), backup=str(backup))
    return backup


def write_hosts_elevated(path: Path, content: str) -> Path:
    logger.info("hosts_elevated_write_started", path=str(path), bytes_count=len(content.encode("utf-8")))
    backup = path.with_name(f"{path.name}.{now_stamp()}.bak")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", delete=False) as file:
        file.write(content)
        temp_path = Path(file.name)

    try:
        system = platform.system().lower()
        if system == "darwin":
            run_elevated_write_macos(path, temp_path, backup)
        elif system.startswith("win"):
            run_elevated_write_windows(path, temp_path, backup)
        else:
            run_elevated_write_linux(path, temp_path, backup)
        logger.info("hosts_elevated_write_finished", path=str(path), backup=str(backup), platform=system)
        return backup
    finally:
        temp_path.unlink(missing_ok=True)


def elevated_write_shell_script(path: PurePath, temp_path: PurePath, backup: PurePath) -> str:
    parent = shlex.quote(str(path.parent))
    target = shlex.quote(str(path))
    temp = shlex.quote(str(temp_path))
    backup_path = shlex.quote(str(backup))
    return (
        f"mkdir -p {parent} && "
        f"if test -e {target}; then cp -p {target} {backup_path}; else : > {backup_path}; fi && "
        f"cat {temp} > {target}"
    )


def run_elevated_write_macos(path: Path, temp_path: Path, backup: Path) -> None:
    logger.info("hosts_elevated_write_platform", platform="macos", path=str(path), backup=str(backup))
    script = elevated_write_shell_script(path, temp_path, backup)
    result = subprocess.run(
        ["osascript", "-e", f"do shell script {json.dumps(script)} with administrator privileges"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.warning("hosts_elevated_write_failed", platform="macos", returncode=result.returncode)
        raise ElevatedWriteError(
            result.stderr.strip() or result.stdout.strip() or "Не удалось получить права администратора"
        )


def run_elevated_write_linux(path: Path, temp_path: Path, backup: Path) -> None:
    if shutil.which("pkexec") is None:
        logger.warning("hosts_elevated_write_unavailable", platform="linux", tool="pkexec")
        raise ElevatedWriteError("Утилита pkexec недоступна")
    logger.info("hosts_elevated_write_platform", platform="linux", path=str(path), backup=str(backup))
    script = elevated_write_shell_script(path, temp_path, backup)
    result = subprocess.run(
        ["pkexec", "sh", "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.warning("hosts_elevated_write_failed", platform="linux", returncode=result.returncode)
        raise ElevatedWriteError(
            result.stderr.strip() or result.stdout.strip() or "Не удалось получить права администратора"
        )


def run_elevated_write_windows(path: Path, temp_path: Path, backup: Path) -> None:
    logger.info("hosts_elevated_write_platform", platform="windows", path=str(path), backup=str(backup))
    script_path = temp_path.with_suffix(".ps1")
    script_path.write_text(
        "\n".join(
            [
                "$ErrorActionPreference = 'Stop'",
                f"$target = {json.dumps(str(path))}",
                f"$temp = {json.dumps(str(temp_path))}",
                f"$backup = {json.dumps(str(backup))}",
                "New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null",
                "if (Test-Path -LiteralPath $target) {",
                "    Copy-Item -LiteralPath $target -Destination $backup -Force",
                "} else {",
                "    New-Item -ItemType File -Force -Path $backup | Out-Null",
                "}",
                "Copy-Item -LiteralPath $temp -Destination $target -Force",
            ]
        ),
        encoding="utf-8",
    )
    try:
        script_arg = "'" + str(script_path).replace("'", "''") + "'"
        command = (
            "Start-Process -FilePath PowerShell "
            f"-ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',{script_arg}) "
            "-Verb RunAs -Wait"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.warning("hosts_elevated_write_failed", platform="windows", returncode=result.returncode)
            raise ElevatedWriteError(
                result.stderr.strip() or result.stdout.strip() or "Не удалось получить права администратора"
            )
    finally:
        script_path.unlink(missing_ok=True)


def merge_entries(
    base: dict[str, HostEntry],
    incoming: dict[str, HostEntry],
    target_group_id: str | None = None,
) -> tuple[dict[str, HostEntry], EntryDiff]:
    result = {
        domain: HostEntry(e.domain, list(e.ips), e.selected_ip, e.enabled, e.group_id) for domain, e in base.items()
    }
    added_domains: list[str] = []
    added_ips: dict[str, list[str]] = {}

    for domain, imported in incoming.items():
        if domain not in result:
            result[domain] = HostEntry(
                imported.domain,
                list(imported.ips),
                imported.selected_ip,
                True,
                target_group_id or imported.group_id,
            )
            added_domains.append(domain)
        else:
            added = result[domain].add_ips(imported.ips)
            if added:
                added_ips[domain] = added
            # Keep enabled state and selected IP unchanged for existing domains.
            if target_group_id is not None:
                result[domain].group_id = target_group_id
    logger.info(
        "entries_merged",
        base_count=len(base),
        incoming_count=len(incoming),
        result_count=len(result),
        added_domains_count=len(added_domains),
        changed_domains_count=len(added_ips),
    )
    return result, {"added_domains": added_domains, "added_ips": added_ips, "removed_domains": []}


def replace_entries(
    base: dict[str, HostEntry],
    incoming: dict[str, HostEntry],
    target_group_id: str | None = None,
) -> tuple[dict[str, HostEntry], EntryDiff]:
    result: dict[str, HostEntry] = {}
    added_domains = sorted(set(incoming) - set(base))
    removed_domains = sorted(set(base) - set(incoming))
    added_ips: dict[str, list[str]] = {}

    for domain, imported in incoming.items():
        if domain in base:
            current = HostEntry(
                base[domain].domain,
                list(base[domain].ips),
                base[domain].selected_ip,
                base[domain].enabled,
                base[domain].group_id,
            )
            added, _removed = current.set_ips_replace(imported.ips)
            if added:
                added_ips[domain] = added
            if target_group_id is not None:
                current.group_id = target_group_id
            result[domain] = current
        else:
            result[domain] = HostEntry(
                imported.domain,
                list(imported.ips),
                imported.selected_ip,
                True,
                target_group_id or imported.group_id,
            )
    logger.info(
        "entries_replaced",
        base_count=len(base),
        incoming_count=len(incoming),
        result_count=len(result),
        added_domains_count=len(added_domains),
        removed_domains_count=len(removed_domains),
        changed_domains_count=len(added_ips),
    )
    return result, {"added_domains": added_domains, "removed_domains": removed_domains, "added_ips": added_ips}


def parse_import_file(path: Path) -> dict[str, HostEntry]:
    entries = parse_import_text(path.read_text(encoding="utf-8-sig"))
    logger.info("import_file_parsed", path=str(path), entries_count=len(entries))
    return entries


def parse_csv_file(path: Path) -> dict[str, HostEntry]:
    return parse_import_file(path)


def parse_import_text(text: str) -> dict[str, HostEntry]:
    if not text.strip():
        raise ValueError("Данные для импорта не указаны")

    import_format = detect_import_format(text)
    if import_format == "json":
        entries = parse_json_import_text(text)
        logger.info("import_text_parsed", format="json", entries_count=len(entries))
        return entries
    if import_format == "hosts":
        entries = parse_hosts_import_text(text)
        logger.info("import_text_parsed", format="hosts", entries_count=len(entries))
        return entries

    entries = parse_delimited_import_text(text)
    logger.info("import_text_parsed", format="delimited", entries_count=len(entries))
    return entries


def detect_import_format(text: str) -> ImportFormat:
    """Detect JSON, standard hosts (IP first), or domain-first delimited input."""
    if not text.strip():
        raise ValueError("Данные для импорта не указаны")

    stripped = text.lstrip()
    if stripped.startswith("[") or stripped.startswith("{"):
        return "json"

    for line in text.splitlines():
        body = line.strip()
        if not body or body.startswith("#"):
            continue
        body = body.split("#", 1)[0].strip()
        if not body:
            continue
        first_column = body.split(maxsplit=1)[0]
        try:
            validate_ip(first_column)
        except ValueError:
            return "delimited"
        return "hosts"
    return "delimited"


def parse_hosts_import_text(text: str) -> dict[str, HostEntry]:
    entries: dict[str, HostEntry] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parsed = parse_hosts_line(line)
        if not parsed:
            raise ValueError(f"Строка {line_number}: ожидаются IP и хотя бы один домен")
        for domain, ip, _enabled in parsed:
            if domain in entries:
                entries[domain].add_ips([ip])
            else:
                entries[domain] = HostEntry(domain=domain, ips=[ip], selected_ip=ip, enabled=True)
    if not entries:
        raise ValueError("Данные для импорта не содержат записей hosts")
    return entries


def parse_json_import_text(text: str) -> dict[str, HostEntry]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Строка {exc.lineno}, колонка {exc.colno}: некорректный JSON") from exc
    if not isinstance(payload, list):
        raise ValueError("JSON для импорта должен быть массивом объектов")

    entries: dict[str, HostEntry] = {}
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Элемент JSON {index}: ожидается объект")
        domain_raw = item.get("domain")
        ip_raw = item.get("ip", item.get("ips"))
        if not isinstance(domain_raw, str) or not isinstance(ip_raw, str):
            raise ValueError(f"Элемент JSON {index}: нужны строковые поля domain и ip/ips")
        try:
            add_import_row(entries, domain_raw, ip_raw)
        except ValueError as exc:
            raise ValueError(f"Элемент JSON {index}: {exc}") from exc
    return entries


def parse_delimited_import_text(text: str) -> dict[str, HostEntry]:
    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = None

    rows: list[tuple[int, list[str]]] = []
    if dialect:
        reader = csv.reader(text.splitlines(), dialect)
        previous_line = 0
        for row in reader:
            rows.append((previous_line + 1, row))
            previous_line = reader.line_num
    else:
        rows = split_whitespace_rows(text)
    if not rows:
        raise ValueError("Данные для импорта не содержат строк")

    header = [c.strip().lower() for c in rows[0][1]]
    has_header = "domain" in header and ("ip" in header or "ips" in header)

    data_rows: list[tuple[int, str, str]] = []
    if has_header:
        domain_idx = header.index("domain")
        ip_idx = header.index("ip") if "ip" in header else header.index("ips")
        for line_number, row in rows[1:]:
            if not row or all(not c.strip() for c in row):
                continue
            if len(row) <= max(domain_idx, ip_idx):
                raise ValueError(f"Строка {line_number}: не хватает столбцов domain и ip/ips")
            data_rows.append((line_number, row[domain_idx], row[ip_idx]))
    else:
        for line_number, row in rows:
            if not row or all(not c.strip() for c in row):
                continue
            if len(row) < 2:
                raise ValueError(f"Строка {line_number}: ожидаются домен и IP")
            data_rows.append((line_number, row[0], row[1]))

    entries: dict[str, HostEntry] = {}
    for line_number, domain_raw, ips_raw in data_rows:
        try:
            add_import_row(entries, domain_raw, ips_raw)
        except ValueError as exc:
            raise ValueError(f"Строка {line_number}: {exc}") from exc
    return entries


def split_whitespace_rows(text: str) -> list[tuple[int, list[str]]]:
    rows: list[tuple[int, list[str]]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        rows.append((line_number, re.split(r"\s+", stripped, maxsplit=1)))
    return rows


def add_import_row(entries: dict[str, HostEntry], domain_raw: str, ips_raw: str) -> None:
    domain = validate_domain(domain_raw)
    ips = split_ips(ips_raw)
    if not ips:
        raise ValueError(f"Для домена {domain} не указаны IP-адреса")
    ips = [validate_ip(ip) for ip in ips]
    if domain not in entries:
        entries[domain] = HostEntry(domain=domain, ips=ips, selected_ip=ips[0], enabled=True)
    else:
        entries[domain].add_ips(ips)
