from __future__ import annotations

import json
import ssl
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import truststore

from hmg.core import HostEntry, parse_hosts_line, parse_import_text, state_path
from hmg.logging import get_logger

logger = get_logger(__name__)
MAX_SOURCE_BYTES = 10 * 1024 * 1024

PairKey = tuple[str, str]


@dataclass
class UrlSource:
    name: str
    url: str
    enabled: bool = True
    id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass
class PairOrigin:
    manual: bool = False
    source_ids: set[str] = field(default_factory=set)


Origins = dict[PairKey, PairOrigin]


def sources_state_path() -> Path:
    return state_path().with_name("sources.json")


def validate_source_url(value: str) -> str:
    url = value.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL источника должен начинаться с http:// или https://")
    return url


def load_sources_state() -> tuple[list[UrlSource], Origins]:
    path = sources_state_path()
    if not path.exists():
        return [], {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        sources = [
            UrlSource(
                id=str(item["id"]),
                name=str(item["name"]),
                url=validate_source_url(str(item["url"])),
                enabled=bool(item.get("enabled", True)),
            )
            for item in payload.get("sources", [])
        ]
        origins: Origins = {}
        for item in payload.get("origins", []):
            origins[(str(item["domain"]), str(item["ip"]))] = PairOrigin(
                manual=bool(item.get("manual", False)),
                source_ids={str(source_id) for source_id in item.get("source_ids", [])},
            )
        return sources, origins
    except Exception as exc:
        logger.warning("sources_state_load_failed", path=str(path), error=str(exc))
        return [], {}


def save_sources_state(sources: list[UrlSource], origins: Origins) -> None:
    path = sources_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "sources": [
            {"id": source.id, "name": source.name, "url": source.url, "enabled": source.enabled} for source in sources
        ],
        "origins": [
            {
                "domain": domain,
                "ip": ip,
                "manual": origin.manual,
                "source_ids": sorted(origin.source_ids),
            }
            for (domain, ip), origin in sorted(origins.items())
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_origins(entries: dict[str, HostEntry], origins: Origins) -> Origins:
    valid_pairs = {(domain, ip) for domain, entry in entries.items() for ip in entry.ips}
    normalized = {
        pair: PairOrigin(origin.manual, set(origin.source_ids))
        for pair, origin in origins.items()
        if pair in valid_pairs
    }
    for pair in valid_pairs:
        normalized.setdefault(pair, PairOrigin(manual=True))
    return normalized


def mark_domain_manual(origins: Origins, entry: HostEntry) -> None:
    for pair in [pair for pair in origins if pair[0] == entry.domain]:
        del origins[pair]
    for ip in entry.ips:
        origins[(entry.domain, ip)] = PairOrigin(manual=True)


def remove_domain_origins(origins: Origins, domain: str) -> None:
    for pair in [pair for pair in origins if pair[0] == domain]:
        del origins[pair]


def clone_entries(entries: dict[str, HostEntry]) -> dict[str, HostEntry]:
    return {
        domain: HostEntry(
            entry.domain,
            list(entry.ips),
            entry.selected_ip,
            entry.enabled,
            entry.group_id,
        )
        for domain, entry in entries.items()
    }


def apply_source(
    entries: dict[str, HostEntry],
    origins: Origins,
    source: UrlSource,
    incoming: dict[str, HostEntry],
    *,
    remove_missing: bool,
) -> tuple[dict[str, HostEntry], Origins]:
    result = clone_entries(entries)
    tracking = normalize_origins(result, origins)
    incoming_pairs = {(domain, ip) for domain, entry in incoming.items() for ip in entry.ips}
    previous_states = {
        domain: (entry.enabled, entry.selected_ip, entry.group_id)
        for domain, entry in result.items()
        if domain in incoming
    }

    if remove_missing:
        old_pairs = {pair for pair, origin in tracking.items() if source.id in origin.source_ids}
        for pair in old_pairs - incoming_pairs:
            origin = tracking[pair]
            origin.source_ids.discard(source.id)
            if not origin.source_ids and not origin.manual:
                del tracking[pair]
                _remove_pair(result, pair)

    for domain, incoming_entry in incoming.items():
        domain_was_missing = domain not in result
        for ip in incoming_entry.ips:
            entry = result.get(domain)
            if entry is None:
                enabled, _selected_ip, group_id = previous_states.get(
                    domain,
                    (True, ip, incoming_entry.group_id),
                )
                entry = HostEntry(
                    domain,
                    [ip],
                    selected_ip=ip,
                    enabled=enabled,
                    group_id=group_id,
                )
                result[domain] = entry
            elif ip not in entry.ips:
                entry.add_ips([ip])
            tracking.setdefault((domain, ip), PairOrigin()).source_ids.add(source.id)
        if domain_was_missing and incoming_entry.selected_ip in result[domain].ips:
            result[domain].selected_ip = incoming_entry.selected_ip

    for domain, (enabled, selected_ip, group_id) in previous_states.items():
        entry = result.get(domain)
        if entry:
            entry.enabled = enabled
            entry.group_id = group_id
            if selected_ip in entry.ips:
                entry.selected_ip = selected_ip
    return result, tracking


def _remove_pair(entries: dict[str, HostEntry], pair: PairKey) -> None:
    domain, ip = pair
    entry = entries.get(domain)
    if entry is None or ip not in entry.ips:
        return
    remaining = [candidate for candidate in entry.ips if candidate != ip]
    if not remaining:
        del entries[domain]
        return
    entry.ips = remaining
    if entry.selected_ip == ip:
        entry.selected_ip = remaining[0]


def replace_from_sources(
    source_entries: list[tuple[UrlSource, dict[str, HostEntry]]],
) -> tuple[dict[str, HostEntry], Origins]:
    entries: dict[str, HostEntry] = {}
    origins: Origins = {}
    for source, incoming in source_entries:
        entries, origins = apply_source(entries, origins, source, incoming, remove_missing=True)
    return entries, origins


def parse_url_source_text(text: str) -> dict[str, HostEntry]:
    if not text.strip():
        raise ValueError("Источник вернул пустой ответ")
    if text.lstrip().startswith(("[", "{")):
        return parse_import_text(text)

    entries: dict[str, HostEntry] = {}
    for line in text.splitlines():
        for domain, ip, _enabled in parse_hosts_line(line):
            if domain in entries:
                entries[domain].add_ips([ip])
            else:
                entries[domain] = HostEntry(domain, [ip])
    if entries:
        return entries
    return parse_import_text(text)


def system_ssl_context() -> ssl.SSLContext:
    """Use native OS trust roots while retaining certificate and hostname checks."""
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def fetch_source(source: UrlSource, timeout: float = 15.0) -> dict[str, HostEntry]:
    request = urllib.request.Request(
        validate_source_url(source.url),
        headers={"User-Agent": "HostsManagerGUI/1.0", "Accept": "text/plain, application/json, text/csv, */*"},
    )
    ssl_context = system_ssl_context()
    with urllib.request.urlopen(request, timeout=timeout, context=ssl_context) as response:  # noqa: S310
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > MAX_SOURCE_BYTES:
            raise ValueError(f"Источник {source.name!r} превышает лимит 10 МБ")
        raw = response.read(MAX_SOURCE_BYTES + 1)
        if len(raw) > MAX_SOURCE_BYTES:
            raise ValueError(f"Источник {source.name!r} превышает лимит 10 МБ")
        charset = response.headers.get_content_charset() or "utf-8"
    return parse_url_source_text(raw.decode(charset, errors="replace"))
