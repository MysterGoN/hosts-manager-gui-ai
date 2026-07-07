import platform
from pathlib import Path

import pytest

import hmg.core as hmg


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
    assert "10.0.0.2\texample.test\t# managed-by=hosts-manager-gui" in rendered
    assert "# 10.0.0.3\tdisabled.test\t# managed-by=hosts-manager-gui; disabled" in rendered
    assert "old-managed.test" not in rendered


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
