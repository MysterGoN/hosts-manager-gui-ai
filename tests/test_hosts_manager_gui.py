import platform
import re
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

import hmg.core as hmg
import hmg.ui as ui_module
from hmg.sources import Origins, PairOrigin, UrlSource
from hmg.ui import (
    RETENTION_UNITS,
    SIZE_UNITS,
    SOURCE_FILTER_MANUAL,
    HostsApp,
    build_side_by_side_diff,
    collapse_unchanged_diff_rows,
    combine_measurement,
    count_unapplied_hosts_changes,
    delete_entries_by_domain,
    delete_entries_from_internal_state,
    entries_snapshot,
    entry_matches_filters,
    format_diff_status,
    format_displayed_diff_side,
    format_numbered_diff_side,
    has_unapplied_hosts_changes,
    hosts_snapshot,
    move_entries_to_group,
    reconcile_persisted_entries,
    remove_group,
    save_internal_state,
    split_measurement,
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
    assert "disabled.test" not in rendered
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


def test_build_preserve_hosts_text_omits_entries_from_disabled_groups() -> None:
    groups = [
        hmg.HostGroup(hmg.DEFAULT_GROUP_ID, hmg.DEFAULT_GROUP_NAME),
        hmg.HostGroup("work", "Work", enabled=False),
    ]
    entries = {
        "default.test": hmg.HostEntry("default.test", ["127.0.0.1"]),
        "work.test": hmg.HostEntry("work.test", ["10.0.0.1"], group_id="work"),
    }

    rendered = hmg.build_preserve_hosts_text("", entries, groups)

    assert "default.test" in rendered
    assert "work.test" not in rendered


def test_state_without_groups_is_migrated_to_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        '{"version": 1, "entries": [{"domain": "example.test", "ips": ["127.0.0.1"]}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(hmg, "state_path", lambda: path)

    entries, groups = hmg.load_state_with_groups()

    assert [(group.id, group.name) for group in groups] == [(hmg.DEFAULT_GROUP_ID, hmg.DEFAULT_GROUP_NAME)]
    assert entries["example.test"].group_id == hmg.DEFAULT_GROUP_ID


def test_state_round_trip_preserves_groups(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "state.json"
    monkeypatch.setattr(hmg, "state_path", lambda: path)
    monkeypatch.setattr(hmg, "hosts_path", lambda: tmp_path / "hosts")
    groups = [
        hmg.HostGroup(hmg.DEFAULT_GROUP_ID, hmg.DEFAULT_GROUP_NAME, enabled=False),
        hmg.HostGroup("work", "Work"),
    ]
    entries = {
        "example.test": hmg.HostEntry(
            "example.test",
            ["127.0.0.1"],
            group_id="work",
        )
    }

    hmg.save_state(entries, groups)
    loaded_entries, loaded_groups = hmg.load_state_with_groups()

    assert loaded_entries["example.test"].group_id == "work"
    assert [(group.id, group.name, group.enabled) for group in loaded_groups] == [
        (hmg.DEFAULT_GROUP_ID, hmg.DEFAULT_GROUP_NAME, False),
        ("work", "Work", True),
    ]


def test_normalize_groups_keeps_default_first_and_repairs_missing_group() -> None:
    entries = {
        "example.test": hmg.HostEntry(
            "example.test",
            ["127.0.0.1"],
            group_id="missing",
        )
    }
    groups = [
        hmg.HostGroup("work", "Work"),
        hmg.HostGroup(hmg.DEFAULT_GROUP_ID, "Renamed Default", enabled=False),
    ]

    normalized = hmg.normalize_groups(entries, groups)

    assert [(group.id, group.name) for group in normalized] == [
        (hmg.DEFAULT_GROUP_ID, hmg.DEFAULT_GROUP_NAME),
        ("work", "Work"),
    ]
    assert not normalized[0].enabled
    assert entries["example.test"].group_id == hmg.DEFAULT_GROUP_ID


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


def test_parse_import_text_reports_invalid_delimited_line() -> None:
    with pytest.raises(ValueError, match=r"Строка 2: .*Invalid IP address"):
        hmg.parse_import_text("example.test 127.0.0.1\nbroken.test not-an-ip\n")


def test_parse_import_text_reports_invalid_json_position() -> None:
    with pytest.raises(ValueError, match=r"Строка 2, колонка"):
        hmg.parse_import_text('[\n  {"domain": "example.test", "ip": }\n]')


def test_merge_entries_keeps_existing_state_and_adds_new_ips() -> None:
    base = {
        "example.test": hmg.HostEntry(
            "example.test",
            ["127.0.0.1"],
            selected_ip="127.0.0.1",
            enabled=False,
            group_id="work",
        )
    }
    incoming = {
        "example.test": hmg.HostEntry("example.test", ["127.0.0.1", "10.0.0.1"]),
        "new.test": hmg.HostEntry("new.test", ["10.0.0.2"]),
    }

    merged, diff = hmg.merge_entries(base, incoming)

    assert not merged["example.test"].enabled
    assert merged["example.test"].selected_ip == "127.0.0.1"
    assert merged["example.test"].group_id == "work"
    assert merged["example.test"].ips == ["127.0.0.1", "10.0.0.1"]
    assert merged["new.test"].group_id == hmg.DEFAULT_GROUP_ID
    assert diff["added_domains"] == ["new.test"]
    assert diff["added_ips"] == {"example.test": ["10.0.0.1"]}


def test_replace_entries_preserves_existing_enabled_state() -> None:
    base = {
        "example.test": hmg.HostEntry(
            "example.test",
            ["127.0.0.1"],
            selected_ip="127.0.0.1",
            enabled=False,
            group_id="work",
        ),
        "removed.test": hmg.HostEntry("removed.test", ["127.0.0.2"]),
    }
    incoming = {"example.test": hmg.HostEntry("example.test", ["10.0.0.1"])}

    replaced, diff = hmg.replace_entries(base, incoming)

    assert set(replaced) == {"example.test"}
    assert not replaced["example.test"].enabled
    assert replaced["example.test"].group_id == "work"
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
    assert format_diff_status(stats) == "Добавлено: 1 · Удалено: 0 · Изменено: 1"


def test_replace_block_classifies_surplus_lines_as_added_or_removed() -> None:
    added_rows = build_side_by_side_diff("old\n", "changed\nadded\n")
    removed_rows = build_side_by_side_diff("old\nremoved\n", "changed\n")

    assert added_rows == [
        ("old", "changed", "changed", "changed"),
        ("", "added", None, "added"),
    ]
    assert removed_rows == [
        ("old", "changed", "changed", "changed"),
        ("removed", "", "removed", None),
    ]


def test_diff_statistics_ignore_generated_at_change() -> None:
    rows = build_side_by_side_diff(
        "# Generated at old\n127.0.0.1 localhost\n",
        "# Generated at new\n127.0.0.1 localhost\n",
    )

    assert rows[0][2:] == ("service", "service")
    assert summarize_diff_rows(rows) == {"added": 0, "removed": 0, "changed": 0}
    assert format_diff_status(summarize_diff_rows(rows)) == "Изменений нет"


def test_large_unchanged_diff_sections_are_collapsed_with_real_line_numbers() -> None:
    before_lines = [f"line {index}" for index in range(1, 31)]
    after_lines = list(before_lines)
    after_lines[14] = "changed line"
    rows = build_side_by_side_diff("\n".join(before_lines), "\n".join(after_lines))

    displayed = collapse_unchanged_diff_rows(rows, context=2, collapse_threshold=5)
    before = format_displayed_diff_side(displayed, "before")
    after = format_displayed_diff_side(displayed, "after")

    assert sum(row.is_collapsed for row in displayed) == 2
    assert any("15  line 15" in line for line in before)
    assert any("15  changed line" in line for line in after)
    assert before[0].startswith("      ⋯")
    assert before[-1].startswith("      ⋯")


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

    assert entries_snapshot(entries) == (("example.test", ("127.0.0.1", "10.0.0.1"), "10.0.0.1", False),)


def test_hosts_snapshot_respects_entry_and_group_switches() -> None:
    groups = [
        hmg.HostGroup(hmg.DEFAULT_GROUP_ID, hmg.DEFAULT_GROUP_NAME),
        hmg.HostGroup("disabled-group", "Disabled", enabled=False),
    ]
    entries = {
        "active.test": hmg.HostEntry("active.test", ["127.0.0.1"]),
        "disabled.test": hmg.HostEntry("disabled.test", ["10.0.0.1"], enabled=False),
        "group.test": hmg.HostEntry(
            "group.test",
            ["192.168.1.1"],
            group_id="disabled-group",
        ),
    }

    assert hosts_snapshot(entries, groups) == (("active.test", "127.0.0.1"),)
    assert entries["group.test"].enabled


def test_state_only_ip_change_does_not_count_as_unapplied_hosts_change() -> None:
    groups = [hmg.HostGroup(hmg.DEFAULT_GROUP_ID, hmg.DEFAULT_GROUP_NAME)]
    entries = {"example.test": hmg.HostEntry("example.test", ["127.0.0.1"])}
    applied = hosts_snapshot(entries, groups)

    entries["example.test"].add_ips(["10.0.0.1"])

    assert not has_unapplied_hosts_changes(entries, groups, applied)

    entries["example.test"].selected_ip = "10.0.0.1"

    assert has_unapplied_hosts_changes(entries, groups, applied)


def test_count_unapplied_hosts_changes_counts_domains_not_diff_lines() -> None:
    groups = [hmg.HostGroup(hmg.DEFAULT_GROUP_ID, hmg.DEFAULT_GROUP_NAME)]
    entries = {
        "changed.test": hmg.HostEntry("changed.test", ["127.0.0.1"]),
        "new.test": hmg.HostEntry("new.test", ["10.0.0.1"]),
        "disabled.test": hmg.HostEntry("disabled.test", ["192.168.1.1"], enabled=False),
    }
    applied = (("changed.test", "10.0.0.2"), ("removed.test", "::1"))

    assert count_unapplied_hosts_changes(entries, groups, applied) == 3


def test_entry_filters_match_domain_ip_state_group_and_source() -> None:
    entry = hmg.HostEntry(
        "api.example.test",
        ["127.0.0.1", "10.0.0.1"],
        group_id="work",
    )
    origins: Origins = {
        ("api.example.test", "127.0.0.1"): PairOrigin(source_ids={"primary"}),
        ("api.example.test", "10.0.0.1"): PairOrigin(manual=True),
    }

    assert entry_matches_filters(entry, origins, query="API.EXAMPLE")
    assert entry_matches_filters(entry, origins, query="10.0.0")
    assert entry_matches_filters(entry, origins, state_filter="enabled")
    assert not entry_matches_filters(entry, origins, state_filter="disabled")
    assert entry_matches_filters(entry, origins, group_id="work")
    assert not entry_matches_filters(entry, origins, group_id="archive")
    assert entry_matches_filters(entry, origins, source_id="primary")
    assert entry_matches_filters(entry, origins, source_id=SOURCE_FILTER_MANUAL)
    assert not entry_matches_filters(entry, origins, source_id="secondary")


def test_move_multiple_entries_between_groups() -> None:
    groups = [
        hmg.HostGroup(hmg.DEFAULT_GROUP_ID, hmg.DEFAULT_GROUP_NAME),
        hmg.HostGroup("work", "Work"),
    ]
    entries = {
        "first.test": hmg.HostEntry("first.test", ["127.0.0.1"]),
        "second.test": hmg.HostEntry("second.test", ["10.0.0.1"]),
    }

    moved = move_entries_to_group(entries, ["first.test", "second.test"], "work", groups)
    returned = move_entries_to_group(entries, ["first.test"], hmg.DEFAULT_GROUP_ID, groups)

    assert moved == ["first.test", "second.test"]
    assert returned == ["first.test"]
    assert entries["first.test"].group_id == hmg.DEFAULT_GROUP_ID
    assert entries["second.test"].group_id == "work"


def test_remove_group_moves_its_entries_to_default() -> None:
    groups = [
        hmg.HostGroup(hmg.DEFAULT_GROUP_ID, hmg.DEFAULT_GROUP_NAME),
        hmg.HostGroup("work", "Work"),
    ]
    entries = {
        "work.test": hmg.HostEntry("work.test", ["127.0.0.1"], group_id="work"),
    }

    remaining, moved = remove_group(entries, groups, "work")

    assert [group.id for group in remaining] == [hmg.DEFAULT_GROUP_ID]
    assert moved == ["work.test"]
    assert entries["work.test"].group_id == hmg.DEFAULT_GROUP_ID


def test_default_group_cannot_be_removed() -> None:
    with pytest.raises(ValueError, match="Default"):
        remove_group({}, [hmg.HostGroup(hmg.DEFAULT_GROUP_ID, hmg.DEFAULT_GROUP_NAME)], hmg.DEFAULT_GROUP_ID)


@pytest.mark.parametrize(
    ("total", "expected"),
    [
        (64 * 1024, (64, "KB")),
        (5 * 1024**2, (5, "MB")),
        (2 * 1024**3, (2, "GB")),
    ],
)
def test_log_size_measurement_uses_common_units(
    total: int,
    expected: tuple[int, str],
) -> None:
    value, unit = split_measurement(total, SIZE_UNITS)

    assert (value, unit) == expected
    assert combine_measurement(value, unit, SIZE_UNITS) == total


@pytest.mark.parametrize(
    ("total", "expected"),
    [
        (30 * 60, (30, "min")),
        (12 * 60 * 60, (12, "h")),
        (30 * 24 * 60 * 60, (30, "d")),
    ],
)
def test_log_retention_measurement_uses_common_units(
    total: int,
    expected: tuple[int, str],
) -> None:
    value, unit = split_measurement(total, RETENTION_UNITS)

    assert (value, unit) == expected
    assert combine_measurement(value, unit, RETENTION_UNITS) == total


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


def test_save_internal_state_persists_entries_groups_sources_and_origins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = {"example.test": hmg.HostEntry("example.test", ["127.0.0.1"])}
    groups = [hmg.HostGroup(hmg.DEFAULT_GROUP_ID, hmg.DEFAULT_GROUP_NAME)]
    sources = [UrlSource("Primary", "https://example.test/hosts", id="source")]
    origins: Origins = {("example.test", "127.0.0.1"): PairOrigin(manual=True)}
    calls: list[str] = []

    def fake_save_state(
        current_entries: dict[str, hmg.HostEntry],
        current_groups: list[hmg.HostGroup],
    ) -> None:
        assert current_entries is entries
        assert current_groups is groups
        calls.append("state")

    def fake_save_sources_state(
        current_sources: list[UrlSource],
        current_origins: Origins,
    ) -> None:
        assert current_sources is sources
        assert current_origins is origins
        calls.append("sources")

    monkeypatch.setattr(ui_module, "save_state", fake_save_state)
    monkeypatch.setattr(ui_module, "save_sources_state", fake_save_sources_state)

    save_internal_state(entries, groups, sources, origins)

    assert calls == ["state", "sources"]


def test_entry_toggle_auto_saves_internal_state() -> None:
    class NoFilter:
        @staticmethod
        def currentData() -> str:
            return ""

    class ToggleApp:
        _refreshing = False
        entries = {"example.test": hmg.HostEntry("example.test", ["127.0.0.1"])}
        state_filter = NoFilter()
        persist_calls = 0

        def persist_internal_state(self) -> None:
            self.persist_calls += 1

        @staticmethod
        def refresh_hosts_status() -> None:
            return

    app = ToggleApp()

    HostsApp.set_enabled(app, "example.test", Qt.CheckState.Unchecked.value)  # type: ignore[arg-type]

    assert not app.entries["example.test"].enabled
    assert app.persist_calls == 1


def test_selected_ip_change_auto_saves_internal_state() -> None:
    class EmptyTable:
        @staticmethod
        def rowCount() -> int:
            return 0

    class IpApp:
        _refreshing = False
        entries = {
            "example.test": hmg.HostEntry(
                "example.test",
                ["127.0.0.1", "10.0.0.1"],
            )
        }
        table = EmptyTable()
        sources: list[UrlSource] = []
        origins: Origins = {}
        persist_calls = 0

        def persist_internal_state(self) -> None:
            self.persist_calls += 1

        @staticmethod
        def refresh_hosts_status() -> None:
            return

    app = IpApp()

    HostsApp.set_selected_ip(app, "example.test", "10.0.0.1")  # type: ignore[arg-type]

    assert app.entries["example.test"].selected_ip == "10.0.0.1"
    assert app.persist_calls == 1


def test_bulk_toggle_updates_selected_entries_and_saves_once() -> None:
    class BulkApp:
        entries = {
            "first.test": hmg.HostEntry("first.test", ["127.0.0.1"]),
            "second.test": hmg.HostEntry("second.test", ["10.0.0.1"], enabled=False),
        }
        persist_calls = 0
        refresh_calls = 0

        @staticmethod
        def selected_domains() -> list[str]:
            return ["first.test", "second.test"]

        def persist_internal_state(self) -> None:
            self.persist_calls += 1

        def refresh_table(self) -> None:
            self.refresh_calls += 1

    app = BulkApp()

    HostsApp.set_selected_entries_enabled(app, False)  # type: ignore[arg-type]

    assert not app.entries["first.test"].enabled
    assert not app.entries["second.test"].enabled
    assert app.persist_calls == 1
    assert app.refresh_calls == 1


def test_canceling_sources_dialog_does_not_apply_its_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = [UrlSource("Primary", "https://example.test/hosts", id="source")]

    class RejectedSourcesDialog:
        action = None

        def __init__(self, _parent: object, _sources: list[UrlSource]) -> None:
            self.sources: list[UrlSource] = []

        def exec(self) -> QDialog.DialogCode:
            return QDialog.DialogCode.Rejected

    class DummyApp:
        sources = original
        entries = {"example.test": hmg.HostEntry("example.test", ["127.0.0.1"])}
        origins: Origins = {("example.test", "127.0.0.1"): PairOrigin(source_ids={"source"})}
        groups = [hmg.HostGroup(hmg.DEFAULT_GROUP_ID, hmg.DEFAULT_GROUP_NAME)]
        persist_calls = 0

        def persist_internal_state(self) -> None:
            self.persist_calls += 1

    monkeypatch.setattr(ui_module, "SourcesDialog", RejectedSourcesDialog)
    app = DummyApp()

    HostsApp.manage_sources(app)  # type: ignore[arg-type]

    assert app.sources == original
    assert app.persist_calls == 0
    assert "example.test" in app.entries


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
