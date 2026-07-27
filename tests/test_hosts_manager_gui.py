import platform
import re
from pathlib import Path

import pytest

import hmg.core as hmg
import hmg.ui as ui_module
from hmg.sources import Origins, PairOrigin, UrlSource
from hmg.ui import (
    HostsApp,
    build_side_by_side_diff,
    delete_entries_by_domain,
    delete_entries_from_internal_state,
    entries_snapshot,
    format_diff_status,
    format_numbered_diff_side,
    reconcile_persisted_entries,
    summarize_diff_rows,
)


def test_host_entry_normalizes_domain_deduplicates_ips_and_selects_first_ip() -> None:
    entry = hmg.HostEntry(
        domain="Example.TEST.",
        ips=["127.0.0.1", "127.0.0.1", "::1"],
    )

    assert entry.domain == "example.test"
    assert entry.ips == ["127.0.0.1", "::1"]
    assert entry.selected_ip == "127.0.0.1"


def test_host_entry_rejects_empty_ip_list() -> None:
    with pytest.raises(ValueError, match="must have at least one IP"):
        hmg.HostEntry(domain="example.test", ips=[])


def test_set_ips_replace_updates_selected_ip_when_removed() -> None:
    entry = hmg.HostEntry(
        domain="example.test",
        ips=["127.0.0.1", "192.168.1.10"],
        selected_ip="192.168.1.10",
    )

    added, removed = entry.set_ips_replace(["10.0.0.5", "127.0.0.1"])

    assert added == ["10.0.0.5"]
    assert removed == ["192.168.1.10"]
    assert entry.ips == ["10.0.0.5", "127.0.0.1"]
    assert entry.selected_ip == "10.0.0.5"


def test_parse_hosts_line_extracts_multiple_aliases() -> None:
    parsed = hmg.parse_hosts_line("127.0.0.1 example.test alias.local # comment")

    assert parsed == [
        ("example.test", "127.0.0.1", True),
        ("alias.local", "127.0.0.1", True),
    ]


def test_parse_hosts_line_can_read_disabled_managed_line() -> None:
    parsed = hmg.parse_hosts_line(
        "# 127.0.0.1 disabled.test # managed-by=hosts-manager-gui; disabled",
        allow_disabled=True,
    )

    assert parsed == [("disabled.test", "127.0.0.1", False)]


def test_parse_hosts_text_reads_only_managed_block() -> None:
    text = "\n".join(
        [
            "127.0.0.1 unmanaged.test",
            hmg.MANAGED_START,
            "10.0.0.1 managed.test # managed-by=hosts-manager-gui",
            "# 10.0.0.2 disabled.test # managed-by=hosts-manager-gui; disabled",
            hmg.MANAGED_END,
            "192.168.1.10 ignored-after-block.test",
        ]
    )

    entries = hmg.parse_hosts_text(text)

    assert set(entries) == {"managed.test", "disabled.test"}
    assert entries["managed.test"].enabled
    assert not entries["disabled.test"].enabled


def test_parse_hosts_text_ignores_hosts_without_managed_block() -> None:
    entries = hmg.parse_hosts_text("127.0.0.1 unmanaged.test\n")

    assert entries == {}


def test_build_preserve_hosts_text_replaces_only_managed_block() -> None:
    original = "\n".join(
        [
            "127.0.0.1 localhost",
            "192.168.1.10 example.test old-alias",
            hmg.MANAGED_START,
            "10.0.0.1 old-managed.test # managed-by=hosts-manager-gui",
            hmg.MANAGED_END,
        ]
    )
    entries = {
        "example.test": hmg.HostEntry("example.test", ["10.0.0.2"]),
        "disabled.test": hmg.HostEntry("disabled.test", ["10.0.0.3"], enabled=False),
    }

    rendered = hmg.build_preserve_hosts_text(original, entries)

    assert "127.0.0.1 localhost" in rendered
    assert "192.168.1.10 example.test old-alias" in rendered
    assert "10.0.0.2\texample.test" in rendered
    assert "# 10.0.0.3\tdisabled.test" in rendered
    assert "# managed-by=hosts-manager-gui" not in rendered
    assert "old-managed.test" not in rendered


def test_build_preserve_hosts_text_keeps_generated_at_when_entries_are_unchanged() -> None:
    entries = {"example.test": hmg.HostEntry("example.test", ["10.0.0.2"])}
    original = hmg.build_preserve_hosts_text("", entries)
    original = re.sub(r"# Generated at .+", "# Generated at 2000-01-01T00:00:00", original)

    rendered = hmg.build_preserve_hosts_text(original, entries)

    assert rendered == original


def test_build_preserve_hosts_text_updates_generated_at_when_entries_change() -> None:
    original_entries = {"example.test": hmg.HostEntry("example.test", ["10.0.0.2"])}
    original = hmg.build_preserve_hosts_text("", original_entries)
    original = re.sub(r"# Generated at .+", "# Generated at 2000-01-01T00:00:00", original)
    changed_entries = {"example.test": hmg.HostEntry("example.test", ["10.0.0.3"])}

    rendered = hmg.build_preserve_hosts_text(original, changed_entries)

    assert "# Generated at 2000-01-01T00:00:00" not in rendered
    assert "10.0.0.3\texample.test" in rendered


def test_parse_csv_file_supports_header_and_semicolon_ip_list(tmp_path: Path) -> None:
    path = tmp_path / "hosts.csv"
    path.write_text('domain,ips\nExample.TEST,"127.0.0.1; 192.168.1.10"\n', encoding="utf-8")

    entries = hmg.parse_csv_file(path)

    assert entries["example.test"].ips == ["127.0.0.1", "192.168.1.10"]


def test_parse_csv_file_merges_duplicate_domains(tmp_path: Path) -> None:
    path = tmp_path / "hosts.csv"
    path.write_text(
        "example.test,127.0.0.1\nexample.test,192.168.1.10\n",
        encoding="utf-8",
    )

    entries = hmg.parse_csv_file(path)

    assert entries["example.test"].ips == ["127.0.0.1", "192.168.1.10"]


def test_parse_import_text_supports_whitespace_delimiter() -> None:
    entries = hmg.parse_import_text("example.test     127.0.0.1\nother.test ::1\n")

    assert entries["example.test"].ips == ["127.0.0.1"]
    assert entries["other.test"].ips == ["::1"]


def test_parse_import_text_supports_tsv_and_semicolon_delimiters() -> None:
    tsv_entries = hmg.parse_import_text("example.test\t127.0.0.1\n")
    semicolon_entries = hmg.parse_import_text("example.test;127.0.0.1\n")

    assert tsv_entries["example.test"].ips == ["127.0.0.1"]
    assert semicolon_entries["example.test"].ips == ["127.0.0.1"]


def test_parse_import_text_supports_json_array() -> None:
    entries = hmg.parse_import_text('[{"domain": "example.test", "ip": "127.0.0.1"}]')

    assert entries["example.test"].ips == ["127.0.0.1"]


def test_merge_entries_keeps_existing_state_and_adds_new_ips() -> None:
    base = {
        "example.test": hmg.HostEntry(
            "example.test",
            ["127.0.0.1"],
            selected_ip="127.0.0.1",
            enabled=False,
        )
    }
    incoming = {
        "example.test": hmg.HostEntry("example.test", ["127.0.0.1", "10.0.0.1"]),
        "new.test": hmg.HostEntry("new.test", ["10.0.0.2"]),
    }

    merged, diff = hmg.merge_entries(base, incoming)

    assert not merged["example.test"].enabled
    assert merged["example.test"].selected_ip == "127.0.0.1"
    assert merged["example.test"].ips == ["127.0.0.1", "10.0.0.1"]
    assert diff["added_domains"] == ["new.test"]
    assert diff["added_ips"] == {"example.test": ["10.0.0.1"]}


def test_replace_entries_preserves_existing_enabled_state() -> None:
    base = {
        "example.test": hmg.HostEntry(
            "example.test",
            ["127.0.0.1"],
            selected_ip="127.0.0.1",
            enabled=False,
        ),
        "removed.test": hmg.HostEntry("removed.test", ["127.0.0.2"]),
    }
    incoming = {"example.test": hmg.HostEntry("example.test", ["10.0.0.1"])}

    replaced, diff = hmg.replace_entries(base, incoming)

    assert set(replaced) == {"example.test"}
    assert not replaced["example.test"].enabled
    assert replaced["example.test"].selected_ip == "10.0.0.1"
    assert diff["removed_domains"] == ["removed.test"]
    assert diff["added_ips"] == {"example.test": ["10.0.0.1"]}


def test_write_hosts_creates_backup_and_writes_content(tmp_path: Path) -> None:
    hosts_path = tmp_path / "hosts"
    hosts_path.write_text("old\n", encoding="utf-8")

    backup = hmg.write_hosts(hosts_path, "new\n")

    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == "old\n"
    assert hosts_path.read_text(encoding="utf-8") == "new\n"


def test_elevated_write_shell_script_quotes_paths() -> None:
    script = hmg.elevated_write_shell_script(
        Path("/tmp/hosts dir/hosts"),
        Path("/tmp/source file"),
        Path("/tmp/hosts dir/hosts.backup"),
    )

    assert "mkdir -p '/tmp/hosts dir'" in script
    assert "cp -p '/tmp/hosts dir/hosts' '/tmp/hosts dir/hosts.backup'" in script
    assert "cat '/tmp/source file' > '/tmp/hosts dir/hosts'" in script


def test_write_hosts_elevated_uses_platform_runner_and_removes_temp_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Path, Path, Path]] = []

    def fake_runner(path: Path, temp_path: Path, backup: Path) -> None:
        calls.append((path, temp_path, backup))
        assert temp_path.read_text(encoding="utf-8") == "new\n"

    hosts_path = tmp_path / "hosts"
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(hmg, "run_elevated_write_macos", fake_runner)

    backup = hmg.write_hosts_elevated(hosts_path, "new\n")

    assert backup.name.startswith("hosts.")
    assert backup.name.endswith(".bak")
    assert calls == [(hosts_path, calls[0][1], backup)]
    assert not calls[0][1].exists()


def test_side_by_side_diff_tags_only_actual_changed_lines() -> None:
    rows = build_side_by_side_diff("127.0.0.1 localhost\n", "127.0.0.1 localhost\n10.0.0.1 example.test\n")

    assert rows == [
        ("127.0.0.1 localhost", "127.0.0.1 localhost", None, None),
        ("", "10.0.0.1 example.test", None, "added"),
    ]


def test_side_by_side_diff_status_counts_changed_lines() -> None:
    rows = build_side_by_side_diff("a\nb\nold\n", "a\nnew\nold\nextra\n")
    stats = summarize_diff_rows(rows)

    assert stats == {"added": 1, "removed": 0, "changed": 1}
    assert format_diff_status(stats) == "Добавлено строк: 1  Удалено строк: 0  Изменено строк: 1"


def test_diff_line_numbers_follow_each_file_and_skip_placeholders() -> None:
    rows = build_side_by_side_diff("first\nremoved\nlast\n", "first\nadded\nlast\nextra\n")

    assert format_numbered_diff_side(rows, "before") == [
        "   1  first",
        "   2  removed",
        "   3  last",
        "      ",
    ]
    assert format_numbered_diff_side(rows, "after") == [
        "   1  first",
        "   2  added",
        "   3  last",
        "   4  extra",
    ]


def test_entries_snapshot_tracks_all_editable_host_state() -> None:
    entries = {
        "example.test": hmg.HostEntry(
            "example.test",
            ["127.0.0.1", "10.0.0.1"],
            selected_ip="10.0.0.1",
            enabled=False,
        )
    }

    assert entries_snapshot(entries) == (
        ("example.test", ("127.0.0.1", "10.0.0.1"), "10.0.0.1", False),
    )


def test_reconcile_persisted_entries_keeps_state_not_yet_applied_to_hosts() -> None:
    state_entries = {
        "existing.test": hmg.HostEntry("existing.test", ["127.0.0.1"]),
        "pending.test": hmg.HostEntry("pending.test", ["10.0.0.1"]),
    }
    hosts_entries = {"existing.test": hmg.HostEntry("existing.test", ["127.0.0.1"])}

    desired, applied = reconcile_persisted_entries(state_entries, hosts_entries)

    assert set(desired) == {"existing.test", "pending.test"}
    assert set(applied) == {"existing.test"}
    assert entries_snapshot(desired) != entries_snapshot(applied)


def test_reconcile_persisted_entries_keeps_intentionally_empty_state() -> None:
    hosts_entries = {"deleted.test": hmg.HostEntry("deleted.test", ["127.0.0.1"])}

    desired, applied = reconcile_persisted_entries(
        {},
        hosts_entries,
        state_available=True,
    )

    assert desired == {}
    assert set(applied) == {"deleted.test"}


def test_reconcile_persisted_entries_uses_hosts_when_state_file_is_missing() -> None:
    hosts_entries = {"existing.test": hmg.HostEntry("existing.test", ["127.0.0.1"])}

    desired, applied = reconcile_persisted_entries(
        {},
        hosts_entries,
        state_available=False,
    )

    assert set(desired) == {"existing.test"}
    assert set(applied) == {"existing.test"}


def test_reconcile_persisted_entries_uses_hosts_state_as_applied_snapshot() -> None:
    state_entries = {
        "example.test": hmg.HostEntry(
            "example.test",
            ["127.0.0.1", "10.0.0.1"],
            selected_ip="10.0.0.1",
            enabled=True,
        )
    }
    hosts_entries = {
        "example.test": hmg.HostEntry(
            "example.test",
            ["127.0.0.1"],
            selected_ip="127.0.0.1",
            enabled=False,
        )
    }

    desired, applied = reconcile_persisted_entries(state_entries, hosts_entries)

    assert desired["example.test"].selected_ip == "10.0.0.1"
    assert desired["example.test"].enabled
    assert applied["example.test"].ips == ["127.0.0.1", "10.0.0.1"]
    assert applied["example.test"].selected_ip == "127.0.0.1"
    assert not applied["example.test"].enabled


def test_delete_entries_by_domain_removes_all_selected_entries_and_origins() -> None:
    entries = {
        "first.test": hmg.HostEntry("first.test", ["127.0.0.1"]),
        "second.test": hmg.HostEntry("second.test", ["10.0.0.1"]),
        "keep.test": hmg.HostEntry("keep.test", ["::1"]),
    }
    origins = {
        ("first.test", "127.0.0.1"): PairOrigin(manual=True),
        ("second.test", "10.0.0.1"): PairOrigin(source_ids={"source"}),
        ("keep.test", "::1"): PairOrigin(manual=True),
    }

    removed = delete_entries_by_domain(
        entries,
        origins,
        ["second.test", "first.test", "second.test", "missing.test"],
    )

    assert removed == ["second.test", "first.test"]
    assert set(entries) == {"keep.test"}
    assert set(origins) == {("keep.test", "::1")}


def test_delete_entries_from_internal_state_persists_entries_and_origins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = {
        "delete.test": hmg.HostEntry("delete.test", ["127.0.0.1"]),
        "keep.test": hmg.HostEntry("keep.test", ["::1"]),
    }
    origins: Origins = {
        ("delete.test", "127.0.0.1"): PairOrigin(source_ids={"source"}),
        ("keep.test", "::1"): PairOrigin(manual=True),
    }
    sources = [UrlSource("Primary", "https://example.test/hosts", id="source")]
    saved_entries: list[set[str]] = []
    saved_origins: list[set[tuple[str, str]]] = []

    def fake_save_state(current_entries: dict[str, hmg.HostEntry]) -> None:
        saved_entries.append(set(current_entries))

    def fake_save_sources_state(_sources: list[UrlSource], current_origins: Origins) -> None:
        saved_origins.append(set(current_origins))

    monkeypatch.setattr(ui_module, "save_state", fake_save_state)
    monkeypatch.setattr(ui_module, "save_sources_state", fake_save_sources_state)

    removed = delete_entries_from_internal_state(
        entries,
        origins,
        sources,
        ["delete.test"],
    )

    assert removed == ["delete.test"]
    assert saved_entries == [{"keep.test"}]
    assert saved_origins == [{("keep.test", "::1")}]


def test_copy_data_files_preserves_existing_destination_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "state.json").write_text("new state", encoding="utf-8")
    (source / "sources.json").write_text("new sources", encoding="utf-8")
    (destination / "state.json").write_text("existing state", encoding="utf-8")

    HostsApp.copy_data_files(source, destination)

    assert (destination / "state.json").read_text(encoding="utf-8") == "existing state"
    assert (destination / "sources.json").read_text(encoding="utf-8") == "new sources"
