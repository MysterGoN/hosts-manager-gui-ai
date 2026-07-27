import ssl
from pathlib import Path

import pytest

import hmg.sources as source_module
from hmg.core import HostEntry
from hmg.sources import (
    PairOrigin,
    UrlSource,
    apply_source,
    load_sources_state,
    mark_domain_manual,
    parse_url_source_text,
    save_sources_state,
    system_ssl_context,
)
from hmg.ui import format_pair_origin


def test_parse_url_source_supports_standard_hosts_format_and_aliases() -> None:
    entries = parse_url_source_text(
        "127.0.0.1 example.test alias.test # comment\n"
        "10.0.0.1 example.test\n"
    )

    assert entries["example.test"].ips == ["127.0.0.1", "10.0.0.1"]
    assert entries["alias.test"].ips == ["127.0.0.1"]


def test_sync_preserves_disabled_state_while_domain_still_exists() -> None:
    source = UrlSource("Primary", "https://example.test/hosts", id="primary")
    entries = {"example.test": HostEntry("example.test", ["127.0.0.1"], enabled=False)}
    origins = {("example.test", "127.0.0.1"): PairOrigin(source_ids={"primary"})}
    incoming = {"example.test": HostEntry("example.test", ["10.0.0.1"])}

    result, tracking = apply_source(entries, origins, source, incoming, remove_missing=True)

    assert not result["example.test"].enabled
    assert result["example.test"].ips == ["10.0.0.1"]
    assert tracking[("example.test", "10.0.0.1")].source_ids == {"primary"}


def test_domain_returning_after_complete_removal_is_enabled_by_default() -> None:
    source = UrlSource("Primary", "https://example.test/hosts", id="primary")
    entries = {"example.test": HostEntry("example.test", ["127.0.0.1"], enabled=False)}
    origins = {("example.test", "127.0.0.1"): PairOrigin(source_ids={"primary"})}

    removed_entries, removed_origins = apply_source(entries, origins, source, {}, remove_missing=True)
    returned_entries, _origins = apply_source(
        removed_entries,
        removed_origins,
        source,
        {"example.test": HostEntry("example.test", ["127.0.0.1"])},
        remove_missing=True,
    )

    assert "example.test" not in removed_entries
    assert returned_entries["example.test"].enabled


def test_same_pair_tracks_multiple_sources_independently() -> None:
    first = UrlSource("First", "https://first.test/hosts", id="first")
    second = UrlSource("Second", "https://second.test/hosts", id="second")
    incoming = {"example.test": HostEntry("example.test", ["127.0.0.1"])}

    entries, origins = apply_source({}, {}, first, incoming, remove_missing=True)
    entries, origins = apply_source(entries, origins, second, incoming, remove_missing=True)
    entries, origins = apply_source(entries, origins, first, {}, remove_missing=True)

    assert entries["example.test"].ips == ["127.0.0.1"]
    assert origins[("example.test", "127.0.0.1")].source_ids == {"second"}


def test_first_source_preserves_its_ip_order_and_selection() -> None:
    source = UrlSource("Primary", "https://example.test/hosts", id="primary")
    incoming = {
        "example.test": HostEntry(
            "example.test",
            ["10.0.0.2", "10.0.0.1"],
            selected_ip="10.0.0.2",
        )
    }

    entries, _origins = apply_source({}, {}, source, incoming, remove_missing=True)

    assert entries["example.test"].ips == ["10.0.0.2", "10.0.0.1"]
    assert entries["example.test"].selected_ip == "10.0.0.2"


def test_different_ips_from_multiple_sources_are_removed_independently() -> None:
    first = UrlSource("First", "https://first.test/hosts", id="first")
    second = UrlSource("Second", "https://second.test/hosts", id="second")
    entries, origins = apply_source(
        {},
        {},
        first,
        {"example.test": HostEntry("example.test", ["127.0.0.1"])},
        remove_missing=True,
    )
    entries["example.test"].enabled = False
    entries, origins = apply_source(
        entries,
        origins,
        second,
        {"example.test": HostEntry("example.test", ["10.0.0.1"])},
        remove_missing=True,
    )

    entries, origins = apply_source(entries, origins, first, {}, remove_missing=True)

    assert entries["example.test"].ips == ["10.0.0.1"]
    assert not entries["example.test"].enabled
    assert origins[("example.test", "10.0.0.1")].source_ids == {"second"}


def test_manual_edit_protects_pairs_from_source_removal() -> None:
    source = UrlSource("Primary", "https://example.test/hosts", id="primary")
    entry = HostEntry("example.test", ["127.0.0.1", "10.0.0.1"])
    entries = {"example.test": entry}
    origins = {
        ("example.test", "127.0.0.1"): PairOrigin(source_ids={"primary"}),
        ("example.test", "10.0.0.1"): PairOrigin(source_ids={"primary"}),
    }
    mark_domain_manual(origins, entry)

    result, tracking = apply_source(entries, origins, source, {}, remove_missing=True)

    assert result["example.test"].ips == ["127.0.0.1", "10.0.0.1"]
    assert all(origin.manual for origin in tracking.values())


def test_sources_and_pair_origins_round_trip_in_separate_state_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "sources.json"
    monkeypatch.setattr(source_module, "sources_state_path", lambda: path)
    sources = [UrlSource("Primary", "https://example.test/hosts", enabled=False, id="primary")]
    origins = {
        ("example.test", "127.0.0.1"): PairOrigin(manual=True, source_ids={"primary", "secondary"})
    }

    save_sources_state(sources, origins)
    loaded_sources, loaded_origins = load_sources_state()

    assert loaded_sources == sources
    assert loaded_origins == origins


def test_pair_origin_label_lists_manual_and_all_known_sources() -> None:
    sources = [
        UrlSource("Primary", "https://primary.test/hosts", id="first"),
        UrlSource("Backup", "https://backup.test/hosts", id="second"),
    ]
    origin = PairOrigin(manual=True, source_ids={"first", "second"})

    assert format_pair_origin(origin, sources) == "Вручную · Primary · Backup"


def test_source_ssl_context_keeps_verification_enabled() -> None:
    context = system_ssl_context()

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
