from __future__ import annotations

import argparse
import os
import subprocess
import sys

SCALE_FACTORS = ("1", "1.25", "1.5", "2")


def check_scale(scale: str) -> int:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_SCALE_FACTOR"] = scale

    from PySide6.QtGui import QKeySequence, QShortcut
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QLineEdit,
        QPlainTextEdit,
        QSpinBox,
        QTableWidget,
        QTextEdit,
        QToolButton,
        QWidget,
    )

    from hmg.core import HostEntry, default_group
    from hmg.settings import default_settings
    from hmg.sources import SourceChangeSummary, SourceFetchResult, UrlSource
    from hmg.ui import (
        APP_STYLE,
        EntryDialog,
        GroupsDialog,
        HostsApp,
        HostsDiffPreview,
        ImportDialog,
        SettingsDialog,
        SourceEditDialog,
        SourcesDialog,
        SourceSyncDialog,
        SourceSyncPreview,
        hosts_snapshot,
    )

    application = QApplication.instance()
    if application is None:
        application = QApplication([])
    assert isinstance(application, QApplication)
    application.setStyle("Fusion")
    application.setStyleSheet(APP_STYLE)
    parent = QWidget()

    source = UrlSource("Primary", "https://example.test/hosts", id="primary")
    result = SourceFetchResult(
        source,
        entries={"example.test": HostEntry("example.test", ["127.0.0.1"])},
    )
    summary = SourceChangeSummary(
        added_domains=["example.test"],
        removed_domains=[],
        added_pairs=[("example.test", "127.0.0.1")],
        removed_pairs=[],
        changed_origins=[],
    )
    long_before = "\n".join(f"127.0.0.1 before-{index}.test" for index in range(30))
    long_after = long_before.replace("before-15.test", "changed-15.test")
    entries = {"example.test": HostEntry("example.test", ["127.0.0.1"])}

    class SmokeHostsApp(HostsApp):
        def load_initial_data(self) -> None:
            self.entries = {"example.test": HostEntry("example.test", ["127.0.0.1", "::1"])}
            self.groups = [default_group()]
            self.sources = [source]
            self.origins = {}
            self._applied_hosts_snapshot = hosts_snapshot(self.entries, self.groups)

    main_window = SmokeHostsApp()
    dialogs: list[QWidget] = [
        main_window,
        EntryDialog(parent),
        ImportDialog(parent),
        SourceEditDialog(parent),
        SourcesDialog(parent, [source]),
        SourceSyncDialog(parent, [source]),
        SourceSyncPreview(parent, "sync", [result], summary, can_apply=True),
        GroupsDialog(parent, [default_group()], entries),
        SettingsDialog(parent, default_settings()),
        HostsDiffPreview(parent, long_before, long_after, "Сохранить в hosts"),
    ]

    failures: list[str] = []
    shortcuts = {
        shortcut.key().toString(QKeySequence.SequenceFormat.PortableText)
        for shortcut in main_window.findChildren(QShortcut)
    }
    required_shortcuts = {"Ctrl+N", "Ctrl+I", "Ctrl+F", "Ctrl+P", "Ctrl+S", "Ctrl+,", "F5", "Del", "Esc"}
    if missing_shortcuts := required_shortcuts - shortcuts:
        failures.append(f"HostsApp: missing shortcuts {sorted(missing_shortcuts)}")
    for dialog in dialogs:
        dialog.show()
        application.processEvents()
        minimum = dialog.minimumSizeHint()
        actual = dialog.size()
        if actual.width() < minimum.width() or actual.height() < minimum.height():
            failures.append(
                f"{type(dialog).__name__}: actual={actual.width()}x{actual.height()}, "
                f"minimum={minimum.width()}x{minimum.height()}"
            )
        for child_type in (QComboBox, QLineEdit, QPlainTextEdit, QTableWidget, QTextEdit):
            for child in dialog.findChildren(child_type):
                if child.isVisible() and not child.accessibleName() and not isinstance(child.parent(), QSpinBox):
                    failures.append(f"{type(dialog).__name__}: {type(child).__name__} has no accessible name")
        for checkbox in dialog.findChildren(QCheckBox):
            if checkbox.isVisible() and not checkbox.text() and not checkbox.accessibleName():
                failures.append(f"{type(dialog).__name__}: unlabeled checkbox has no accessible name")
        for tool_button in dialog.findChildren(QToolButton):
            is_unlabeled_icon = tool_button.text() in {"↑", "↓", "▸", "▾"} and not tool_button.accessibleName()
            if tool_button.isVisible() and is_unlabeled_icon:
                failures.append(f"{type(dialog).__name__}: icon-only button has no accessible name")
        dialog.close()

    if failures:
        for failure in failures:
            print(f"scale {scale}: {failure}", file=sys.stderr)
        return 1
    print(f"scale {scale}: {len(dialogs)} dialogs passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-check Qt dialogs at common scale factors")
    parser.add_argument("--child-scale", choices=SCALE_FACTORS)
    args = parser.parse_args()
    if args.child_scale:
        return check_scale(args.child_scale)

    for scale in SCALE_FACTORS:
        environment = os.environ.copy()
        result = subprocess.run(
            [sys.executable, __file__, "--child-scale", scale],
            env=environment,
            check=False,
        )
        if result.returncode != 0:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
