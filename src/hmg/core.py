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
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TypedDict

APP_NAME = "Hosts Manager GUI"
STATE_VERSION = 1
MANAGED_START = "# >>> hosts-manager-gui >>>"
MANAGED_END = "# <<< hosts-manager-gui <<<"
INLINE_MARK = "# managed-by=hosts-manager-gui"

DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?!-)[A-Za-z0-9_*.:-]{1,63}(?<!-)(?:\.(?!-)[A-Za-z0-9_*-]{1,63}(?<!-))*\.?$")


class EntryDiff(TypedDict):
    added_domains: list[str]
    added_ips: dict[str, list[str]]
    removed_domains: list[str]


class ElevatedWriteError(RuntimeError):
    pass


def hosts_path() -> Path:
    if platform.system().lower().startswith("win"):
        root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        return Path(root) / "System32" / "drivers" / "etc" / "hosts"
    return Path("/etc/hosts")


def state_path() -> Path:
    if platform.system().lower().startswith("win"):
        base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
        return base / "HostsManagerGUI" / "state.json"
    if platform.system().lower() == "darwin":
        return Path.home() / "Library" / "Application Support" / "HostsManagerGUI" / "state.json"
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "hosts-manager-gui" / "state.json"


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def normalize_domain(domain: str) -> str:
    return domain.strip().lower().rstrip(".")


def split_ips(value: str) -> list[str]:
    parts = re.split(r"[;\s]+", value.strip())
    return [p.strip() for p in parts if p.strip()]


def validate_ip(value: str) -> str:
    value = value.strip()
    try:
        ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError(f"Invalid IP address: {value}") from exc
    return value


def validate_domain(value: str) -> str:
    domain = normalize_domain(value)
    if not domain:
        raise ValueError("Domain is empty")
    # hosts can contain local aliases without dots. We allow them.
    if not DOMAIN_RE.match(domain):
        raise ValueError(f"Invalid domain/host name: {value}")
    return domain


@dataclass
class HostEntry:
    domain: str
    ips: list[str] = field(default_factory=list)
    selected_ip: str = ""
    enabled: bool = True

    def __post_init__(self) -> None:
        self.domain = validate_domain(self.domain)
        clean_ips: list[str] = []
        for ip in self.ips:
            ip = validate_ip(ip)
            if ip not in clean_ips:
                clean_ips.append(ip)
        self.ips = clean_ips
        if not self.ips:
            raise ValueError(f"Domain {self.domain!r} must have at least one IP")
        if self.selected_ip:
            self.selected_ip = validate_ip(self.selected_ip)
        if not self.selected_ip or self.selected_ip not in self.ips:
            self.selected_ip = self.ips[0]

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
            raise ValueError(f"Domain {self.domain!r} must have at least one IP")
        old = set(self.ips)
        new = set(clean)
        added = sorted(new - old)
        removed = sorted(old - new)
        self.ips = clean
        if self.selected_ip not in self.ips:
            self.selected_ip = self.ips[0]
        return added, removed

    def active_line(self) -> str:
        return f"{self.selected_ip}\t{self.domain}\t{INLINE_MARK}"

    def disabled_line(self) -> str:
        return f"# {self.selected_ip}\t{self.domain}\t{INLINE_MARK}; disabled"


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
    has_managed_block = any(line.strip() == MANAGED_START for line in text.splitlines())
    in_managed_block = False

    for line in text.splitlines():
        if line.strip() == MANAGED_START:
            in_managed_block = True
            continue
        if line.strip() == MANAGED_END:
            in_managed_block = False
            continue

        # If a managed block exists, prefer it as source of truth.
        # Otherwise parse normal active hosts lines too.
        should_parse = in_managed_block or not has_managed_block
        if not should_parse:
            continue

        for domain, ip, enabled in parse_hosts_line(line, allow_disabled=in_managed_block):
            if domain not in entries:
                entries[domain] = HostEntry(domain=domain, ips=[ip], selected_ip=ip, enabled=enabled)
            else:
                entries[domain].add_ips([ip])
                # Prefer active mapping as selected if any active duplicate exists.
                if enabled:
                    entries[domain].selected_ip = ip
                    entries[domain].enabled = True
    return entries


def load_state() -> dict[str, HostEntry]:
    path = state_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        entries: dict[str, HostEntry] = {}
        for item in payload.get("entries", []):
            entry = HostEntry(
                domain=item["domain"],
                ips=list(item.get("ips", [])),
                selected_ip=item.get("selected_ip", ""),
                enabled=bool(item.get("enabled", True)),
            )
            entries[entry.domain] = entry
        return entries
    except Exception:
        return {}


def save_state(entries: dict[str, HostEntry]) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": STATE_VERSION,
        "hosts_path": str(hosts_path()),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "entries": [asdict(e) for e in sorted(entries.values(), key=lambda x: x.domain)],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_hosts_file(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


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


def filter_domains_from_unmanaged(lines: list[str], domains: set[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        parsed = parse_hosts_line(line, allow_disabled=False)
        if not parsed:
            out.append(line)
            continue
        domains_on_line = {domain for domain, _ip, _enabled in parsed}
        if domains_on_line & domains:
            # Skip the whole line to avoid duplicate active mappings.
            continue
        out.append(line)
    return out


def build_managed_block(entries: dict[str, HostEntry]) -> str:
    lines = [
        MANAGED_START,
        f"# Managed by {APP_NAME}. Edit through the app when possible.",
        f"# Generated at {datetime.now().isoformat(timespec='seconds')}",
    ]
    for entry in sorted(entries.values(), key=lambda x: x.domain):
        lines.append(entry.active_line() if entry.enabled else entry.disabled_line())
    lines.append(MANAGED_END)
    return "\n".join(lines) + "\n"


def build_preserve_hosts_text(original_text: str, entries: dict[str, HostEntry]) -> str:
    lines = original_text.splitlines()
    lines = remove_managed_block(lines)
    lines = filter_domains_from_unmanaged(lines, set(entries.keys()))
    base = "\n".join(lines).rstrip()
    block = build_managed_block(entries).rstrip()
    if base:
        return base + "\n\n" + block + "\n"
    return block + "\n"


def build_overwrite_hosts_text(entries: dict[str, HostEntry]) -> str:
    header = [
        "# hosts file generated by Hosts Manager GUI",
        f"# Generated at {datetime.now().isoformat(timespec='seconds')}",
        "# Warning: this file was written in overwrite mode.",
        "",
    ]
    return "\n".join(header) + build_managed_block(entries)


def write_hosts(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_name(f"{path.name}.{now_stamp()}.bak")
    if path.exists():
        shutil.copy2(path, backup)
    else:
        backup.write_text("", encoding="utf-8")
    with path.open("w", encoding="utf-8", newline="\n") as file:
        file.write(content)
    return backup


def write_hosts_elevated(path: Path, content: str) -> Path:
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
        return backup
    finally:
        temp_path.unlink(missing_ok=True)


def elevated_write_shell_script(path: Path, temp_path: Path, backup: Path) -> str:
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
    script = elevated_write_shell_script(path, temp_path, backup)
    result = subprocess.run(
        ["osascript", "-e", f"do shell script {json.dumps(script)} with administrator privileges"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ElevatedWriteError(result.stderr.strip() or result.stdout.strip() or "Administrator authorization failed")


def run_elevated_write_linux(path: Path, temp_path: Path, backup: Path) -> None:
    if shutil.which("pkexec") is None:
        raise ElevatedWriteError("pkexec is not available")
    script = elevated_write_shell_script(path, temp_path, backup)
    result = subprocess.run(
        ["pkexec", "sh", "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ElevatedWriteError(result.stderr.strip() or result.stdout.strip() or "Administrator authorization failed")


def run_elevated_write_windows(path: Path, temp_path: Path, backup: Path) -> None:
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
            raise ElevatedWriteError(
                result.stderr.strip() or result.stdout.strip() or "Administrator authorization failed"
            )
    finally:
        script_path.unlink(missing_ok=True)


def merge_entries(
    base: dict[str, HostEntry],
    incoming: dict[str, HostEntry],
) -> tuple[dict[str, HostEntry], EntryDiff]:
    result = {domain: HostEntry(e.domain, list(e.ips), e.selected_ip, e.enabled) for domain, e in base.items()}
    added_domains: list[str] = []
    added_ips: dict[str, list[str]] = {}

    for domain, imported in incoming.items():
        if domain not in result:
            result[domain] = HostEntry(imported.domain, list(imported.ips), imported.selected_ip, True)
            added_domains.append(domain)
        else:
            added = result[domain].add_ips(imported.ips)
            if added:
                added_ips[domain] = added
            # Keep enabled state and selected IP unchanged for existing domains.
    return result, {"added_domains": added_domains, "added_ips": added_ips, "removed_domains": []}


def replace_entries(
    base: dict[str, HostEntry],
    incoming: dict[str, HostEntry],
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
            )
            added, _removed = current.set_ips_replace(imported.ips)
            if added:
                added_ips[domain] = added
            result[domain] = current
        else:
            result[domain] = HostEntry(imported.domain, list(imported.ips), imported.selected_ip, True)
    return result, {"added_domains": added_domains, "removed_domains": removed_domains, "added_ips": added_ips}


def parse_csv_file(path: Path) -> dict[str, HostEntry]:
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        raise ValueError("CSV file is empty")

    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel

    rows = list(csv.reader(text.splitlines(), dialect))
    if not rows:
        raise ValueError("CSV file contains no rows")

    header = [c.strip().lower() for c in rows[0]]
    has_header = "domain" in header and ("ip" in header or "ips" in header)

    data_rows: list[tuple[str, str]] = []
    if has_header:
        domain_idx = header.index("domain")
        ip_idx = header.index("ip") if "ip" in header else header.index("ips")
        for row in rows[1:]:
            if not row or all(not c.strip() for c in row):
                continue
            if len(row) <= max(domain_idx, ip_idx):
                raise ValueError(f"Bad CSV row: {row}")
            data_rows.append((row[domain_idx], row[ip_idx]))
    else:
        for row in rows:
            if not row or all(not c.strip() for c in row):
                continue
            if len(row) < 2:
                raise ValueError(f"Bad CSV row without header: {row}")
            data_rows.append((row[0], row[1]))

    entries: dict[str, HostEntry] = {}
    for domain_raw, ips_raw in data_rows:
        domain = validate_domain(domain_raw)
        ips = split_ips(ips_raw)
        if not ips:
            raise ValueError(f"No IPs for domain {domain}")
        ips = [validate_ip(ip) for ip in ips]
        if domain not in entries:
            entries[domain] = HostEntry(domain=domain, ips=ips, selected_ip=ips[0], enabled=True)
        else:
            entries[domain].add_ips(ips)
    return entries
