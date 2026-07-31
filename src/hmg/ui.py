from __future__ import annotations

import difflib
import re
import shutil
import signal
import sys
from pathlib import Path

from PySide6.QtCore import QModelIndex, QPoint, QSignalBlocker, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QFontDatabase,
    QPainter,
    QPaintEvent,
    QPen,
    QTextCursor,
    QTextFormat,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from hmg.core import (
    APP_NAME,
    DEFAULT_GROUP_ID,
    ElevatedWriteError,
    EntryDiff,
    HostEntry,
    HostGroup,
    build_preserve_hosts_text,
    hosts_path,
    load_state_with_groups,
    merge_entries,
    new_group,
    normalize_groups,
    parse_hosts_text,
    parse_import_text,
    read_hosts_file,
    replace_entries,
    save_state,
    state_path,
    validate_domain,
    validate_ip,
    write_hosts,
    write_hosts_elevated,
)
from hmg.logging import configure_logging, get_logger
from hmg.settings import (
    LOG_LEVELS,
    MAX_LOG_RETENTION_SECONDS,
    AppSettings,
    default_data_dir,
    default_log_dir,
    get_settings,
    is_packaged,
    save_settings,
    settings_file_path,
)
from hmg.sources import (
    Origins,
    PairOrigin,
    SourceChangeSummary,
    SourceFetchResult,
    UrlSource,
    apply_source,
    clone_entries,
    fetch_source,
    load_sources_state,
    mark_domain_manual,
    normalize_origins,
    prepare_sources_update,
    remove_domain_origins,
    save_sources_state,
    summarize_source_changes,
    validate_source_url,
)

logger = get_logger(__name__)

DiffRow = tuple[str, str, str | None, str | None]
DiffStats = dict[str, int]
EntriesSnapshot = tuple[tuple[str, tuple[str, ...], str, bool], ...]
HostsSnapshot = tuple[tuple[str, str], ...]

SIZE_UNITS = {"KB": 1024, "MB": 1024**2, "GB": 1024**3}
RETENTION_UNITS = {"min": 60, "h": 60 * 60, "d": 24 * 60 * 60}
MAX_LOG_SIZE_BYTES = 1024**4
SOURCE_FILTER_MANUAL = "__manual__"

DIFF_COLORS = {
    "added": "#234D37",
    "removed": "#573338",
    "changed": "#554A25",
}
HOSTS_TABLE_FONT_SIZE = 13

APP_STYLE = """
QWidget {
    background: #121318;
    color: #E7E8EE;
    font-size: 14px;
}
QMainWindow, QDialog { background: #121318; }
QLabel#title {
    font-size: 24px;
    font-weight: 700;
    color: #F7F7FB;
}
QLabel#subtitle, QLabel#hint {
    color: #999DAA;
}
QLabel#sectionTitle {
    color: #F1F1F5;
    font-size: 16px;
    font-weight: 600;
}
QFrame#card, QGroupBox {
    background: #1A1C23;
    border: 1px solid #2A2D37;
    border-radius: 12px;
}
QFrame#card QLabel { background: transparent; }
QGroupBox {
    margin-top: 10px;
    padding: 18px 12px 12px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 6px;
}
QGroupBox QLabel, QGroupBox QCheckBox { background: transparent; }
QPushButton {
    min-height: 38px;
    padding: 0 16px;
    border: 1px solid #353945;
    border-radius: 8px;
    background: #242731;
    color: #E9EAF0;
    font-weight: 600;
}
QPushButton:hover { background: #2C303B; border-color: #464B5A; }
QPushButton:pressed { background: #20232B; }
QPushButton:disabled {
    background: #1C1E25;
    border-color: #292C35;
    color: #666A75;
}
QPushButton#primary {
    background: #7C5CFC;
    border-color: #7C5CFC;
    color: white;
}
QPushButton#primary:hover { background: #8B6DFF; }
QPushButton#danger { color: #FF8A94; }
QPushButton#danger:disabled { color: #666A75; }
QToolButton {
    min-width: 24px;
    min-height: 24px;
    border: 0;
    border-radius: 6px;
    background: transparent;
    color: #C9CBD3;
    font-weight: 700;
}
QToolButton:hover { background: #303440; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QListWidget {
    background: #16181E;
    border: 1px solid #323640;
    border-radius: 8px;
    padding: 8px;
    selection-background-color: #8067E8;
    selection-color: white;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QListWidget:focus {
    border: 1px solid #7C5CFC;
}
QComboBox { padding: 6px 10px; }
QComboBox::drop-down { border: 0; width: 26px; }
QComboBox QAbstractItemView {
    background: #242731;
    border: 1px solid #3B3F4A;
    selection-background-color: #654BD2;
}
QTableWidget {
    background: #1A1C23;
    alternate-background-color: #1D1F27;
    border: 1px solid #2A2D37;
    border-radius: 10px;
    gridline-color: transparent;
    selection-background-color: #312A51;
    selection-color: #FFFFFF;
}
QTableWidget::item { padding: 8px; border-bottom: 1px solid #252832; }
QHeaderView::section {
    background: #20232B;
    color: #AEB1BC;
    padding: 10px;
    border: 0;
    border-bottom: 1px solid #30333D;
    font-weight: 600;
}
QCheckBox::indicator, QRadioButton::indicator {
    width: 18px;
    height: 18px;
}
QCheckBox::indicator {
    border: 2px solid #626673;
    border-radius: 5px;
    background: #17191F;
}
QCheckBox::indicator:checked {
    background: #7C5CFC;
    border-color: #7C5CFC;
}
QRadioButton::indicator {
    border: 2px solid #626673;
    border-radius: 10px;
}
QRadioButton::indicator:checked {
    background: #7C5CFC;
    border: 4px solid #262932;
}
QScrollBar:vertical {
    width: 10px;
    background: transparent;
}
QScrollBar::handle:vertical {
    background: #3A3E49;
    border-radius: 5px;
    min-height: 30px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal {
    height: 10px;
    background: transparent;
}
QScrollBar::handle:horizontal {
    background: #3A3E49;
    border-radius: 5px;
    min-width: 30px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
"""


def monospace_font(point_size: int | None = None) -> QFont:
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    if point_size is not None:
        font.setPointSize(point_size)
    return font


def entries_snapshot(entries: dict[str, HostEntry]) -> EntriesSnapshot:
    return tuple(
        (domain, tuple(entry.ips), entry.selected_ip, entry.enabled) for domain, entry in sorted(entries.items())
    )


def hosts_snapshot(
    entries: dict[str, HostEntry],
    groups: list[HostGroup] | None = None,
) -> HostsSnapshot:
    enabled_groups = None if groups is None else {group.id for group in groups if group.enabled}
    return tuple(
        sorted(
            (entry.domain, entry.selected_ip)
            for entry in entries.values()
            if entry.enabled and (enabled_groups is None or entry.group_id in enabled_groups)
        )
    )


def has_unapplied_hosts_changes(
    entries: dict[str, HostEntry],
    groups: list[HostGroup],
    applied_snapshot: HostsSnapshot,
) -> bool:
    return hosts_snapshot(entries, groups) != applied_snapshot


def count_unapplied_hosts_changes(
    entries: dict[str, HostEntry],
    groups: list[HostGroup],
    applied_snapshot: HostsSnapshot,
) -> int:
    current = dict(hosts_snapshot(entries, groups))
    applied = dict(applied_snapshot)
    return sum(current.get(domain) != applied.get(domain) for domain in current.keys() | applied.keys())


def entry_matches_filters(
    entry: HostEntry,
    origins: Origins,
    *,
    query: str = "",
    state_filter: str = "",
    group_id: str = "",
    source_id: str = "",
) -> bool:
    normalized_query = query.strip().casefold()
    if (
        normalized_query
        and normalized_query not in entry.domain.casefold()
        and not any(normalized_query in ip.casefold() for ip in entry.ips)
    ):
        return False
    if state_filter == "enabled" and not entry.enabled:
        return False
    if state_filter == "disabled" and entry.enabled:
        return False
    if group_id and entry.group_id != group_id:
        return False
    if source_id:
        entry_origins = [origins.get((entry.domain, ip)) for ip in entry.ips]
        if source_id == SOURCE_FILTER_MANUAL:
            return any(origin is None or origin.manual for origin in entry_origins)
        return any(origin is not None and source_id in origin.source_ids for origin in entry_origins)
    return True


def save_internal_state(
    entries: dict[str, HostEntry],
    groups: list[HostGroup],
    sources: list[UrlSource],
    origins: Origins,
) -> None:
    save_state(entries, groups)
    save_sources_state(sources, origins)


def move_entries_to_group(
    entries: dict[str, HostEntry],
    domains: list[str],
    group_id: str,
    groups: list[HostGroup],
) -> list[str]:
    if group_id not in {group.id for group in groups}:
        raise ValueError("Unknown group")
    moved: list[str] = []
    for domain in dict.fromkeys(domains):
        entry = entries.get(domain)
        if entry is None or entry.group_id == group_id:
            continue
        entry.group_id = group_id
        moved.append(domain)
    return moved


def remove_group(
    entries: dict[str, HostEntry],
    groups: list[HostGroup],
    group_id: str,
) -> tuple[list[HostGroup], list[str]]:
    if group_id == DEFAULT_GROUP_ID:
        raise ValueError("Default group cannot be removed")
    if group_id not in {group.id for group in groups}:
        return groups, []
    moved = [entry.domain for entry in entries.values() if entry.group_id == group_id]
    for domain in moved:
        entries[domain].group_id = DEFAULT_GROUP_ID
    return [group for group in groups if group.id != group_id], sorted(moved)


def split_measurement(total: int, units: dict[str, int]) -> tuple[int, str]:
    for unit, factor in reversed(units.items()):
        if total >= factor and total % factor == 0:
            return total // factor, unit
    unit, factor = next(iter(units.items()))
    return max(1, (total + factor - 1) // factor), unit


def combine_measurement(value: int, unit: str, units: dict[str, int]) -> int:
    return value * units[unit]


def reconcile_persisted_entries(
    state_entries: dict[str, HostEntry],
    hosts_entries: dict[str, HostEntry],
    *,
    state_available: bool = True,
) -> tuple[dict[str, HostEntry], dict[str, HostEntry]]:
    """Restore desired state while retaining the state currently applied to hosts."""
    desired_entries = clone_entries(state_entries if state_available else hosts_entries)
    applied_entries: dict[str, HostEntry] = {}
    for domain, hosts_entry in hosts_entries.items():
        state_entry = state_entries.get(domain)
        if state_entry is None:
            applied_entries[domain] = clone_entries({domain: hosts_entry})[domain]
            continue
        applied_entry = clone_entries({domain: state_entry})[domain]
        applied_entry.add_ips(hosts_entry.ips)
        applied_entry.selected_ip = hosts_entry.selected_ip
        applied_entry.enabled = hosts_entry.enabled
        applied_entries[domain] = applied_entry
    return desired_entries, applied_entries


def delete_entries_by_domain(
    entries: dict[str, HostEntry],
    origins: Origins,
    domains: list[str],
) -> list[str]:
    removed: list[str] = []
    for domain in dict.fromkeys(domains):
        if domain not in entries:
            continue
        del entries[domain]
        remove_domain_origins(origins, domain)
        removed.append(domain)
    return removed


def delete_entries_from_internal_state(
    entries: dict[str, HostEntry],
    origins: Origins,
    sources: list[UrlSource],
    domains: list[str],
    groups: list[HostGroup] | None = None,
) -> list[str]:
    removed = delete_entries_by_domain(entries, origins, domains)
    if removed:
        if groups is None:
            save_state(entries)
            save_sources_state(sources, origins)
        else:
            save_internal_state(entries, groups, sources, origins)
    return removed


def format_pair_origin(origin: PairOrigin | None, sources: list[UrlSource]) -> str:
    if origin is None:
        return "Вручную"
    labels = ["Вручную"] if origin.manual else []
    known_ids = {source.id for source in sources}
    labels.extend(source.name for source in sources if source.id in origin.source_ids)
    labels.extend(sorted(origin.source_ids - known_ids))
    return " · ".join(labels) or "Вручную"


class DiffTextEdit(QPlainTextEdit):
    """Read-only diff pane with subtle row and line-number separators."""

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QPainter(self.viewport())
        painter.setPen(QPen(QColor("#292C35"), 1))

        block = self.firstVisibleBlock()
        while block.isValid():
            geometry = self.blockBoundingGeometry(block).translated(self.contentOffset())
            if geometry.top() > self.viewport().height():
                break
            if geometry.bottom() >= 0:
                y = round(geometry.bottom())
                painter.drawLine(0, y, self.viewport().width(), y)
            block = block.next()

        number_cursor = QTextCursor(self.document())
        number_cursor.setPosition(min(5, self.document().characterCount() - 1))
        number_width = self.cursorRect(number_cursor).left()
        painter.setPen(QPen(QColor("#30333D"), 1))
        painter.drawLine(number_width, 0, number_width, self.viewport().height())


def dialog_buttons(parent: QDialog, confirm_text: str) -> QDialogButtonBox:
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
    cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
    cancel.setText("Отмена")
    confirm = buttons.addButton(confirm_text, QDialogButtonBox.ButtonRole.AcceptRole)
    confirm.setObjectName("primary")
    buttons.accepted.connect(parent.accept)
    buttons.rejected.connect(parent.reject)
    return buttons


class ChangePreview(QDialog):
    def __init__(self, parent: QWidget, title: str, diff: EntryDiff, confirm_text: str = "Применить") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(720, 500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)

        text = QTextEdit()
        text.setReadOnly(True)
        text.setFont(monospace_font())
        text.setPlainText("\n".join(self.format_diff(diff)))
        layout.addWidget(text)
        layout.addWidget(dialog_buttons(self, confirm_text))

    @staticmethod
    def format_diff(diff: EntryDiff) -> list[str]:
        lines = ["Добавленные записи:"]
        added_domains = diff.get("added_domains", [])
        removed_domains = diff.get("removed_domains", [])
        added_ips = diff.get("added_ips", {})
        lines.extend([f"  + {domain}" for domain in sorted(added_domains)] or ["  — нет"])
        lines.extend(["", "Удалённые записи:"])
        lines.extend([f"  − {domain}" for domain in sorted(removed_domains)] or ["  — нет"])
        lines.extend(["", "IP, добавленные к существующим доменам:"])
        if added_ips:
            for domain in sorted(added_ips):
                lines.append(f"  {domain}:")
                lines.extend(f"    + {ip}" for ip in added_ips[domain])
        else:
            lines.append("  — нет")
        return lines


class HostsDiffPreview(QDialog):
    def __init__(
        self,
        parent: QWidget,
        before: str,
        after: str,
        confirm_text: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Предпросмотр изменений hosts")
        self.resize(1180, 760)
        self.diff_rows = build_side_by_side_diff(before, after)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        title = QLabel("Изменения в hosts")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        status = QLabel(format_diff_status(summarize_diff_rows(self.diff_rows)))
        status.setObjectName("subtitle")
        layout.addWidget(status)

        panes = QWidget()
        panes_layout = QHBoxLayout(panes)
        panes_layout.setContentsMargins(0, 0, 0, 0)
        panes_layout.setSpacing(0)
        before_panel, self.before_text = self._build_diff_panel(panes, "Сейчас")
        after_panel, self.after_text = self._build_diff_panel(panes, "После сохранения")
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setStyleSheet("color: #30333D;")
        panes_layout.addWidget(before_panel, 1)
        panes_layout.addWidget(divider)
        panes_layout.addWidget(after_panel, 1)
        layout.addWidget(panes, 1)

        self._insert_diff()
        self.before_text.verticalScrollBar().valueChanged.connect(self.after_text.verticalScrollBar().setValue)
        self.after_text.verticalScrollBar().valueChanged.connect(self.before_text.verticalScrollBar().setValue)
        self.before_text.horizontalScrollBar().valueChanged.connect(self.after_text.horizontalScrollBar().setValue)
        self.after_text.horizontalScrollBar().valueChanged.connect(self.before_text.horizontalScrollBar().setValue)

        buttons = QDialogButtonBox()
        close = buttons.addButton("Закрыть", QDialogButtonBox.ButtonRole.RejectRole)
        close.clicked.connect(self.reject)
        if confirm_text:
            confirm = buttons.addButton(confirm_text, QDialogButtonBox.ButtonRole.AcceptRole)
            confirm.setObjectName("primary")
            confirm.clicked.connect(self.accept)
        layout.addWidget(buttons)

    @staticmethod
    def _build_diff_panel(parent: QWidget, title: str) -> tuple[QWidget, DiffTextEdit]:
        panel = QWidget(parent)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(title)
        label.setObjectName("subtitle")
        layout.addWidget(label)
        editor = DiffTextEdit()
        editor.setReadOnly(True)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        editor.setFont(monospace_font(12))
        layout.addWidget(editor)
        return panel, editor

    def _insert_diff(self) -> None:
        self.before_text.setPlainText("\n".join(format_numbered_diff_side(self.diff_rows, side="before")))
        self.after_text.setPlainText("\n".join(format_numbered_diff_side(self.diff_rows, side="after")))
        self.before_text.setExtraSelections(self._diff_selections(self.before_text, 2))
        self.after_text.setExtraSelections(self._diff_selections(self.after_text, 3))

    def _diff_selections(self, editor: QPlainTextEdit, tag_index: int) -> list[QTextEdit.ExtraSelection]:
        selections: list[QTextEdit.ExtraSelection] = []
        for line_number, row in enumerate(self.diff_rows):
            tag = row[tag_index]
            if not tag:
                continue
            selection = QTextEdit.ExtraSelection()
            cursor = QTextCursor(editor.document().findBlockByLineNumber(line_number))
            cursor.select(QTextCursor.SelectionType.LineUnderCursor)
            selection.cursor = cursor
            selection.format.setBackground(QColor(DIFF_COLORS[tag]))
            selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            selections.append(selection)
        return selections


def build_side_by_side_diff(before: str, after: str) -> list[DiffRow]:
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    matcher = difflib.SequenceMatcher(a=before_lines, b=after_lines, autojunk=False)
    rows: list[DiffRow] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        left = before_lines[i1:i2]
        right = after_lines[j1:j2]
        if tag == "equal":
            rows.extend(align_diff_lines(left, right))
        elif tag == "delete":
            rows.extend(align_diff_lines(left, ["" for _line in left], before_tag="removed"))
        elif tag == "insert":
            rows.extend(align_diff_lines(["" for _line in right], right, after_tag="added"))
        elif tag == "replace":
            rows.extend(align_diff_lines(left, right, before_tag="changed", after_tag="changed"))
    return rows


def format_numbered_diff_side(rows: list[DiffRow], side: str) -> list[str]:
    """Add source-specific line numbers while keeping diff placeholders aligned."""
    if side not in {"before", "after"}:
        raise ValueError(f"Unknown diff side: {side}")

    result: list[str] = []
    line_number = 0
    for before_line, after_line, before_tag, after_tag in rows:
        is_placeholder = (
            side == "before" and not before_line and bool(after_line) and after_tag in {"added", "changed"}
        ) or (side == "after" and not after_line and bool(before_line) and before_tag in {"removed", "changed"})
        if is_placeholder:
            result.append("      ")
            continue

        line_number += 1
        line = before_line if side == "before" else after_line
        result.append(f"{line_number:>4}  {line}")
    return result


def align_diff_lines(
    before_lines: list[str],
    after_lines: list[str],
    before_tag: str | None = None,
    after_tag: str | None = None,
) -> list[DiffRow]:
    rows: list[DiffRow] = []
    max_len = max(len(before_lines), len(after_lines))
    for index in range(max_len):
        before_line = before_lines[index] if index < len(before_lines) else ""
        after_line = after_lines[index] if index < len(after_lines) else ""
        rows.append((before_line, after_line, before_tag if before_line else None, after_tag if after_line else None))
    return rows


def summarize_diff_rows(rows: list[DiffRow]) -> DiffStats:
    stats = {"added": 0, "removed": 0, "changed": 0}
    for _before_line, _after_line, before_tag, after_tag in rows:
        if after_tag == "added":
            stats["added"] += 1
        elif before_tag == "removed":
            stats["removed"] += 1
        elif before_tag == "changed" or after_tag == "changed":
            stats["changed"] += 1
    return stats


def format_diff_status(stats: DiffStats) -> str:
    if not any(stats.values()):
        return "Изменений нет"
    return f"Добавлено строк: {stats['added']}  Удалено строк: {stats['removed']}  Изменено строк: {stats['changed']}"


class EntryDialog(QDialog):
    def __init__(self, parent: QWidget, entry: HostEntry | None = None) -> None:
        super().__init__(parent)
        self.entry = entry
        self.result_entry: HostEntry | None = None
        self.setWindowTitle("Редактировать запись" if entry else "Добавить запись")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        title = QLabel(self.windowTitle())
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        form = QFormLayout()
        form.setVerticalSpacing(12)
        self.domain_edit = QLineEdit(entry.domain if entry else "")
        self.domain_edit.setFont(monospace_font())
        self.domain_edit.setPlaceholderText("example.local")
        form.addRow("Домен", self.domain_edit)
        self.ips_edit = QTextEdit()
        self.ips_edit.setFont(monospace_font())
        self.ips_edit.setMinimumHeight(130)
        self.ips_edit.setPlaceholderText("127.0.0.1\n::1")
        if entry:
            self.ips_edit.setPlainText("\n".join(entry.ips))
        form.addRow("IP-адреса", self.ips_edit)
        hint = QLabel("Один IP на строку или через точку с запятой / пробел")
        hint.setObjectName("hint")
        form.addRow("", hint)
        self.enabled_check = QCheckBox("Запись включена")
        self.enabled_check.setChecked(entry.enabled if entry else True)
        form.addRow("", self.enabled_check)
        layout.addLayout(form)

        buttons = dialog_buttons(self, "Сохранить" if entry else "Добавить")
        buttons.accepted.disconnect()
        buttons.accepted.connect(self.validate_and_accept)
        layout.addWidget(buttons)
        self.domain_edit.setFocus()

    def validate_and_accept(self) -> None:
        try:
            domain = validate_domain(self.domain_edit.text())
            ips: list[str] = []
            for part in re.split(r"[;\s]+", self.ips_edit.toPlainText()):
                if part.strip():
                    ip = validate_ip(part)
                    if ip not in ips:
                        ips.append(ip)
            if not ips:
                raise ValueError("Нужно указать хотя бы один IP")
            self.result_entry = HostEntry(domain, ips, ips[0], self.enabled_check.isChecked())
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            return
        self.accept()


class ImportDropTextEdit(QTextEdit):
    file_dropped = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile():
            self.file_dropped.emit(urls[0].toLocalFile())
            event.acceptProposedAction()
            return
        super().dropEvent(event)


class ImportDialog(QDialog):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.result_data: tuple[str, dict[str, HostEntry]] | None = None
        self.parsed_entries: dict[str, HostEntry] | None = None
        self.setWindowTitle("Импорт записей")
        self.setMinimumSize(720, 620)
        self.resize(800, 680)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        title = QLabel("Импорт записей")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        mode_box = QGroupBox("Режим импорта")
        mode_layout = QHBoxLayout(mode_box)
        self.merge_radio = QRadioButton("Обновить существующие")
        self.replace_radio = QRadioButton("Заменить всё")
        self.merge_radio.setChecked(True)
        mode_layout.addWidget(self.merge_radio)
        mode_layout.addWidget(self.replace_radio)
        mode_layout.addStretch()
        layout.addWidget(mode_box)

        formats = QGroupBox("Поддерживаемые форматы")
        formats_layout = QVBoxLayout(formats)
        formats_hint = QLabel(
            'TXT: domain IP · CSV/TSV: столбцы domain и ip/ips · JSON: [{"domain": "example.local", "ip": "127.0.0.1"}]'
        )
        formats_hint.setWordWrap(True)
        formats_hint.setObjectName("hint")
        formats_layout.addWidget(formats_hint)
        layout.addWidget(formats)

        label_row = QHBoxLayout()
        label_row.addWidget(QLabel("Данные для импорта"))
        label_row.addStretch()
        load_button = QPushButton("Выбрать файл")
        load_button.clicked.connect(self.load_file)
        label_row.addWidget(load_button)
        layout.addLayout(label_row)
        self.import_text = ImportDropTextEdit()
        self.import_text.setFont(monospace_font())
        self.import_text.setPlaceholderText("Перетащите сюда .txt, .csv, .tsv или .json\n\nexample.local  127.0.0.1")
        self.import_text.file_dropped.connect(self.load_path)
        layout.addWidget(self.import_text, 1)

        self.import_status = QLabel("Добавьте данные или перетащите файл")
        self.import_status.setWordWrap(True)
        self.import_status.setObjectName("hint")
        layout.addWidget(self.import_status)

        buttons = dialog_buttons(self, "Импортировать")
        buttons.accepted.disconnect()
        buttons.accepted.connect(self.parse_and_accept)
        self.import_button = next(
            button
            for button in buttons.buttons()
            if buttons.buttonRole(button) == QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.import_button.setEnabled(False)
        layout.addWidget(buttons)
        self.validation_timer = QTimer(self)
        self.validation_timer.setSingleShot(True)
        self.validation_timer.setInterval(250)
        self.validation_timer.timeout.connect(self.refresh_import_stats)
        self.import_text.textChanged.connect(self.validation_timer.start)

    def load_file(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "Импорт записей",
            "",
            "Поддерживаемые файлы (*.csv *.tsv *.json *.txt);;Все файлы (*)",
        )
        if not path:
            return
        self.load_path(path)

    def load_path(self, path: str) -> None:
        logger.info("import_file_selected", path=path)
        try:
            self.import_text.setPlainText(Path(path).read_text(encoding="utf-8-sig"))
        except Exception as exc:
            logger.warning("import_file_read_failed", path=path, error=str(exc))
            self.parsed_entries = None
            self.import_button.setEnabled(False)
            self.import_status.setStyleSheet("color: #FF9A9A;")
            self.import_status.setText(f"Не удалось прочитать файл: {exc}")

    def refresh_import_stats(self) -> None:
        try:
            entries = parse_import_text(self.import_text.toPlainText())
        except Exception as exc:
            self.parsed_entries = None
            self.import_button.setEnabled(False)
            self.import_status.setStyleSheet("color: #FF9A9A;")
            self.import_status.setText(f"Ошибка: {exc}")
            return
        domains_count = len(entries)
        pairs_count = sum(len(entry.ips) for entry in entries.values())
        self.parsed_entries = entries
        self.import_button.setEnabled(True)
        self.import_status.setStyleSheet("color: #8ED6A8;")
        self.import_status.setText(f"Распознано доменов: {domains_count} · связей домен/IP: {pairs_count}")

    def parse_and_accept(self) -> None:
        self.refresh_import_stats()
        if self.parsed_entries is None:
            return
        mode = "merge" if self.merge_radio.isChecked() else "replace"
        self.result_data = (mode, self.parsed_entries)
        logger.info("import_dialog_accepted", mode=mode, entries_count=len(self.parsed_entries))
        self.accept()


class SelectIpDialog(QDialog):
    def __init__(self, parent: QWidget, entry: HostEntry) -> None:
        super().__init__(parent)
        self.selected_ip: str | None = None
        self.setWindowTitle(f"Выбор IP для {entry.domain}")
        self.resize(440, 380)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        label = QLabel(entry.domain)
        label.setObjectName("sectionTitle")
        layout.addWidget(label)
        self.list_widget = QListWidget()
        self.list_widget.setFont(monospace_font())
        self.list_widget.addItems(entry.ips)
        if entry.selected_ip in entry.ips:
            self.list_widget.setCurrentRow(entry.ips.index(entry.selected_ip))
        layout.addWidget(self.list_widget)
        buttons = dialog_buttons(self, "Выбрать")
        buttons.accepted.disconnect()
        buttons.accepted.connect(self.choose)
        layout.addWidget(buttons)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self.choose())

    def choose(self) -> None:
        item = self.list_widget.currentItem()
        if item is None:
            QMessageBox.warning(self, APP_NAME, "Выберите IP")
            return
        self.selected_ip = item.text()
        self.accept()


class SourceEditDialog(QDialog):
    def __init__(self, parent: QWidget, source: UrlSource | None = None) -> None:
        super().__init__(parent)
        self.result_source: UrlSource | None = None
        self.source = source
        self.setWindowTitle("Изменить источник" if source else "Добавить источник")
        self.setMinimumWidth(580)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        title = QLabel(self.windowTitle())
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        form = QFormLayout()
        self.name_edit = QLineEdit(source.name if source else "")
        self.name_edit.setPlaceholderText("Рабочие домены")
        form.addRow("Название", self.name_edit)
        self.url_edit = QLineEdit(source.url if source else "")
        self.url_edit.setPlaceholderText("https://example.com/hosts")
        form.addRow("URL", self.url_edit)
        self.enabled_check = QCheckBox("Использовать при синхронизации")
        self.enabled_check.setChecked(source.enabled if source else True)
        form.addRow("", self.enabled_check)
        layout.addLayout(form)
        buttons = dialog_buttons(self, "Сохранить")
        buttons.accepted.disconnect()
        buttons.accepted.connect(self.validate_and_accept)
        layout.addWidget(buttons)

    def validate_and_accept(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, APP_NAME, "Укажите название источника")
            return
        try:
            url = validate_source_url(self.url_edit.text())
        except ValueError as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            return
        self.result_source = UrlSource(
            id=self.source.id if self.source else UrlSource(name, url).id,
            name=name,
            url=url,
            enabled=self.enabled_check.isChecked(),
        )
        self.accept()


class SourcesDialog(QDialog):
    def __init__(self, parent: QWidget, sources: list[UrlSource]) -> None:
        super().__init__(parent)
        self.sources = [UrlSource(source.name, source.url, source.enabled, source.id) for source in sources]
        self.setWindowTitle("Управление URL-источниками")
        self.resize(900, 540)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        title = QLabel("Управление URL-источниками")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        hint = QLabel(
            "Добавляйте, отключайте и меняйте порядок источников. "
            "Загрузка данных запускается отдельно из главного окна."
        )
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        layout.addWidget(hint)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Активен", "Название", "URL"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(lambda _index: self.edit_source())
        layout.addWidget(self.table, 1)

        edit_row = QHBoxLayout()
        add_button = QPushButton("Добавить")
        add_button.clicked.connect(self.add_source)
        edit_row.addWidget(add_button)
        edit_button = QPushButton("Изменить")
        edit_button.clicked.connect(self.edit_source)
        edit_row.addWidget(edit_button)
        delete_button = QPushButton("Удалить")
        delete_button.setObjectName("danger")
        delete_button.clicked.connect(self.delete_source)
        edit_row.addWidget(delete_button)
        up_button = QPushButton("↑")
        up_button.setToolTip("Поднять источник выше")
        up_button.clicked.connect(lambda: self.move_source(-1))
        edit_row.addWidget(up_button)
        down_button = QPushButton("↓")
        down_button.setToolTip("Опустить источник ниже")
        down_button.clicked.connect(lambda: self.move_source(1))
        edit_row.addWidget(down_button)
        edit_row.addStretch()
        layout.addLayout(edit_row)

        layout.addWidget(dialog_buttons(self, "Сохранить"))
        self.refresh_table()

    def refresh_table(self) -> None:
        self.table.setRowCount(0)
        for row, source in enumerate(self.sources):
            self.table.insertRow(row)
            check = QCheckBox()
            check.setChecked(source.enabled)
            check.stateChanged.connect(lambda state, source_id=source.id: self.set_source_enabled(source_id, state))
            cell = QWidget()
            cell.setStyleSheet("background: transparent;")
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(10, 0, 10, 0)
            cell_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell_layout.addWidget(check)
            self.table.setCellWidget(row, 0, cell)
            name = QTableWidgetItem(source.name)
            name.setData(Qt.ItemDataRole.UserRole, source.id)
            self.table.setItem(row, 1, name)
            self.table.setItem(row, 2, QTableWidgetItem(source.url))

    def set_source_enabled(self, source_id: str, state: int) -> None:
        for source in self.sources:
            if source.id == source_id:
                source.enabled = state == Qt.CheckState.Checked.value
                return

    def selected_source(self) -> UrlSource | None:
        item = self.table.item(self.table.currentRow(), 1)
        if item is None:
            QMessageBox.warning(self, APP_NAME, "Выберите источник")
            return None
        source_id = str(item.data(Qt.ItemDataRole.UserRole))
        return next((source for source in self.sources if source.id == source_id), None)

    def add_source(self) -> None:
        dialog = SourceEditDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result_source:
            self.sources.append(dialog.result_source)
            self.refresh_table()

    def edit_source(self) -> None:
        source = self.selected_source()
        if source is None:
            return
        dialog = SourceEditDialog(self, source)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result_source:
            index = next(index for index, item in enumerate(self.sources) if item.id == source.id)
            self.sources[index] = dialog.result_source
            self.refresh_table()
            self.table.selectRow(index)

    def delete_source(self) -> None:
        source = self.selected_source()
        if source is None:
            return
        answer = QMessageBox.question(
            self,
            APP_NAME,
            f"Удалить источник «{source.name}»?\nЕго уникальные связи будут удалены.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.sources = [item for item in self.sources if item.id != source.id]
            self.refresh_table()

    def move_source(self, offset: int) -> None:
        source = self.selected_source()
        if source is None:
            return
        index = next(index for index, item in enumerate(self.sources) if item.id == source.id)
        new_index = index + offset
        if not 0 <= new_index < len(self.sources):
            return
        self.sources[index], self.sources[new_index] = self.sources[new_index], self.sources[index]
        self.refresh_table()
        self.table.selectRow(new_index)


class SourceSyncDialog(QDialog):
    def __init__(self, parent: QWidget, sources: list[UrlSource]) -> None:
        super().__init__(parent)
        self.result_action: str | None = None
        self.active_sources = [source for source in sources if source.enabled]
        self.setWindowTitle("Загрузка из URL")
        self.setMinimumSize(680, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        title = QLabel("Загрузка из URL")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        hint = QLabel(
            "Источники будут загружены по очереди. Перед изменением local state "
            "вы увидите результат каждого источника и общий preview."
        )
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        layout.addWidget(hint)

        mode_box = QGroupBox("Режим")
        mode_layout = QVBoxLayout(mode_box)
        self.update_radio = QRadioButton("Загрузить новые — ничего не удалять")
        self.sync_radio = QRadioButton("Синхронизировать — удалить исчезнувшие связи источников")
        self.replace_radio = QRadioButton("Заменить целиком — оставить только данные активных источников")
        self.sync_radio.setChecked(True)
        mode_layout.addWidget(self.update_radio)
        mode_layout.addWidget(self.sync_radio)
        mode_layout.addWidget(self.replace_radio)
        layout.addWidget(mode_box)

        sources_label = QLabel(f"Активные источники: {len(self.active_sources)}")
        sources_label.setObjectName("subtitle")
        layout.addWidget(sources_label)
        source_list = QListWidget()
        for index, source in enumerate(self.active_sources, start=1):
            source_list.addItem(f"{index}. {source.name}  —  {source.url}")
        layout.addWidget(source_list, 1)

        buttons = dialog_buttons(self, "Загрузить и проверить")
        buttons.accepted.disconnect()
        buttons.accepted.connect(self.choose_action)
        layout.addWidget(buttons)

    def choose_action(self) -> None:
        if self.update_radio.isChecked():
            self.result_action = "update"
        elif self.replace_radio.isChecked():
            self.result_action = "replace"
        else:
            self.result_action = "sync"
        self.accept()


def format_source_change_summary(summary: SourceChangeSummary) -> list[str]:
    lines = [
        f"Новых доменов: {len(summary.added_domains)}",
        f"Удалённых доменов: {len(summary.removed_domains)}",
        f"Добавленных связей домен/IP: {len(summary.added_pairs)}",
        f"Удалённых связей домен/IP: {len(summary.removed_pairs)}",
        f"Изменений происхождения: {len(summary.changed_origins)}",
    ]
    sections: list[tuple[str, list[str]]] = [
        ("Новые домены", summary.added_domains),
        ("Удалённые домены", summary.removed_domains),
        ("Добавленные связи", [f"{domain}  {ip}" for domain, ip in summary.added_pairs]),
        ("Удалённые связи", [f"{domain}  {ip}" for domain, ip in summary.removed_pairs]),
        (
            "Изменённое происхождение",
            [f"{domain}  {ip}" for domain, ip in summary.changed_origins],
        ),
    ]
    for title, values in sections:
        if values:
            lines.extend(["", f"{title}:", *[f"  {value}" for value in values]])
    if not summary.has_changes:
        lines.extend(["", "Изменений во внутреннем состоянии нет."])
    return lines


class SourceSyncPreview(QDialog):
    def __init__(
        self,
        parent: QWidget,
        action: str,
        results: list[SourceFetchResult],
        summary: SourceChangeSummary,
        *,
        can_apply: bool,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Предпросмотр загрузки из URL")
        self.setMinimumSize(860, 680)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        title = QLabel("Результаты источников")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        failed_count = sum(not result.succeeded for result in results)
        status = QLabel(
            f"Успешно: {len(results) - failed_count} · С ошибкой: {failed_count} · Режим: {self.action_label(action)}"
        )
        status.setObjectName("subtitle")
        layout.addWidget(status)

        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["Источник", "Результат", "Домены", "Подробности"])
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for row, result in enumerate(results):
            table.insertRow(row)
            table.setItem(row, 0, QTableWidgetItem(result.source.name))
            table.setItem(row, 1, QTableWidgetItem("Готово" if result.succeeded else "Ошибка"))
            domain_count = len(result.entries) if result.entries is not None else 0
            table.setItem(row, 2, QTableWidgetItem(str(domain_count) if result.succeeded else "—"))
            details = result.source.url if result.succeeded else (result.error or "Неизвестная ошибка")
            detail_item = QTableWidgetItem(details)
            detail_item.setToolTip(details)
            table.setItem(row, 3, detail_item)
        layout.addWidget(table, 1)

        preview_label = QLabel("Изменения local state")
        preview_label.setObjectName("sectionTitle")
        layout.addWidget(preview_label)
        preview = QTextEdit()
        preview.setReadOnly(True)
        preview.setFont(monospace_font())
        preview.setPlainText("\n".join(format_source_change_summary(summary)))
        layout.addWidget(preview, 1)

        if not can_apply:
            warning_text = (
                "Полная замена не может быть применена: исправьте ошибки источников и запустите загрузку повторно."
                if action == "replace"
                else "Нет ни одного успешно загруженного источника. Local state не будет изменён."
            )
            warning = QLabel(warning_text)
            warning.setWordWrap(True)
            warning.setStyleSheet("color: #FF9A9A;")
            layout.addWidget(warning)

        buttons = QDialogButtonBox()
        close = buttons.addButton("Закрыть", QDialogButtonBox.ButtonRole.RejectRole)
        close.clicked.connect(self.reject)
        apply_button = buttons.addButton("Применить к local state", QDialogButtonBox.ButtonRole.AcceptRole)
        apply_button.setObjectName("primary")
        apply_button.setEnabled(can_apply)
        apply_button.clicked.connect(self.accept)
        layout.addWidget(buttons)

    @staticmethod
    def action_label(action: str) -> str:
        return {
            "update": "загрузить новые",
            "sync": "синхронизировать",
            "replace": "заменить целиком",
        }[action]


class GroupsDialog(QDialog):
    def __init__(
        self,
        parent: QWidget,
        groups: list[HostGroup],
        entries: dict[str, HostEntry],
    ) -> None:
        super().__init__(parent)
        self.groups = [HostGroup(group.id, group.name, group.enabled) for group in groups]
        self.entry_counts = {
            group.id: sum(entry.group_id == group.id for entry in entries.values()) for group in groups
        }
        self.result_groups: list[HostGroup] | None = None
        self.setWindowTitle("Группы")
        self.setMinimumSize(620, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        title = QLabel("Группы")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        hint = QLabel(
            "Группа влияет только на отображение и итоговую запись в hosts. "
            "Индивидуальные переключатели доменов при этом не изменяются."
        )
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        layout.addWidget(hint)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Активна", "Название", "Записей"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.doubleClicked.connect(lambda _index: self.rename_group())
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        add_button = QPushButton("Добавить")
        add_button.clicked.connect(self.add_group)
        actions.addWidget(add_button)
        rename_button = QPushButton("Переименовать")
        rename_button.clicked.connect(self.rename_group)
        actions.addWidget(rename_button)
        delete_button = QPushButton("Удалить")
        delete_button.setObjectName("danger")
        delete_button.clicked.connect(self.delete_group)
        actions.addWidget(delete_button)
        up_button = QPushButton("↑")
        up_button.setToolTip("Поднять группу выше")
        up_button.clicked.connect(lambda: self.move_group(-1))
        actions.addWidget(up_button)
        down_button = QPushButton("↓")
        down_button.setToolTip("Опустить группу ниже")
        down_button.clicked.connect(lambda: self.move_group(1))
        actions.addWidget(down_button)
        actions.addStretch()
        layout.addLayout(actions)

        buttons = dialog_buttons(self, "Сохранить")
        buttons.accepted.disconnect()
        buttons.accepted.connect(self.accept_groups)
        layout.addWidget(buttons)
        self.refresh_table()

    def refresh_table(self, selected_id: str | None = None) -> None:
        self.table.setRowCount(0)
        for row, group in enumerate(self.groups):
            self.table.insertRow(row)
            check = QCheckBox()
            check.setChecked(group.enabled)
            check.setToolTip("Исключить или включить всю группу в hosts")
            check.stateChanged.connect(lambda state, group_id=group.id: self.set_group_enabled(group_id, state))
            cell = QWidget()
            cell.setStyleSheet("background: transparent;")
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(10, 0, 10, 0)
            cell_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell_layout.addWidget(check)
            self.table.setCellWidget(row, 0, cell)
            name = QTableWidgetItem(group.name)
            name.setData(Qt.ItemDataRole.UserRole, group.id)
            self.table.setItem(row, 1, name)
            count = QTableWidgetItem(str(self.entry_counts.get(group.id, 0)))
            count.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 2, count)
            if group.id == selected_id:
                self.table.selectRow(row)

    def selected_group(self) -> HostGroup | None:
        item = self.table.item(self.table.currentRow(), 1)
        if item is None:
            QMessageBox.warning(self, APP_NAME, "Выберите группу")
            return None
        group_id = str(item.data(Qt.ItemDataRole.UserRole))
        return next((group for group in self.groups if group.id == group_id), None)

    def set_group_enabled(self, group_id: str, state: int) -> None:
        group = next((group for group in self.groups if group.id == group_id), None)
        if group:
            group.enabled = state == Qt.CheckState.Checked.value

    def unique_name(self, name: str, excluded_id: str | None = None) -> bool:
        key = name.casefold()
        return all(group.id == excluded_id or group.name.casefold() != key for group in self.groups)

    def prompt_name(self, title: str, current: str = "", excluded_id: str | None = None) -> str | None:
        name, accepted = QInputDialog.getText(self, title, "Название группы", text=current)
        if not accepted:
            return None
        name = name.strip()
        if not name:
            QMessageBox.warning(self, APP_NAME, "Название группы не может быть пустым")
            return None
        if not self.unique_name(name, excluded_id):
            QMessageBox.warning(self, APP_NAME, "Группа с таким названием уже существует")
            return None
        return name

    def add_group(self) -> None:
        name = self.prompt_name("Новая группа")
        if name is None:
            return
        group = new_group(name)
        self.groups.append(group)
        self.entry_counts[group.id] = 0
        self.refresh_table(group.id)

    def rename_group(self) -> None:
        group = self.selected_group()
        if group is None:
            return
        if group.id == DEFAULT_GROUP_ID:
            QMessageBox.information(self, APP_NAME, "Группу Default нельзя переименовать")
            return
        name = self.prompt_name("Переименовать группу", group.name, group.id)
        if name is None:
            return
        group.name = name
        self.refresh_table(group.id)

    def delete_group(self) -> None:
        group = self.selected_group()
        if group is None:
            return
        if group.id == DEFAULT_GROUP_ID:
            QMessageBox.information(self, APP_NAME, "Группу Default нельзя удалить")
            return
        count = self.entry_counts.get(group.id, 0)
        answer = QMessageBox.question(
            self,
            APP_NAME,
            f"Удалить группу «{group.name}»?\nЗаписи ({count}) будут перенесены в Default.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.groups = [item for item in self.groups if item.id != group.id]
        self.entry_counts[DEFAULT_GROUP_ID] = self.entry_counts.get(DEFAULT_GROUP_ID, 0) + count
        self.entry_counts.pop(group.id, None)
        self.refresh_table(DEFAULT_GROUP_ID)

    def move_group(self, offset: int) -> None:
        group = self.selected_group()
        if group is None:
            return
        if group.id == DEFAULT_GROUP_ID:
            QMessageBox.information(self, APP_NAME, "Группу Default нельзя перемещать")
            return
        index = next(index for index, item in enumerate(self.groups) if item.id == group.id)
        new_index = index + offset
        if new_index <= 0 or new_index >= len(self.groups):
            return
        self.groups[index], self.groups[new_index] = self.groups[new_index], self.groups[index]
        self.refresh_table(group.id)

    def accept_groups(self) -> None:
        self.result_groups = self.groups
        self.accept()


class SettingsDialog(QDialog):
    def __init__(self, parent: QWidget, settings: AppSettings) -> None:
        super().__init__(parent)
        self.result_settings: AppSettings | None = None
        self.setWindowTitle("Настройки")
        self.setMinimumSize(820, 680)
        self.resize(860, 720)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        title = QLabel("Настройки")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        paths = QGroupBox("Хранение данных")
        paths_form = QFormLayout(paths)
        paths_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        paths_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        self.data_dir_edit = QLineEdit(settings.data_dir)
        self.data_dir_edit.setMinimumWidth(440)
        paths_form.addRow("Каталог данных", self._path_picker(self.data_dir_edit))
        data_hint = QLabel("Здесь хранятся state.json и sources.json")
        data_hint.setObjectName("hint")
        paths_form.addRow("", data_hint)
        config_hint = QLabel(f"Основной конфиг: {settings_file_path()}")
        config_hint.setObjectName("hint")
        config_hint.setWordWrap(True)
        paths_form.addRow("", config_hint)
        defaults = QPushButton("Вернуть системные каталоги")
        defaults.clicked.connect(self.restore_default_paths)
        paths_form.addRow("", defaults)
        layout.addWidget(paths)

        logs = QGroupBox("Логи")
        logs_form = QFormLayout(logs)
        logs_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        logs_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        self.log_dir_edit = QLineEdit(settings.log_dir)
        self.log_dir_edit.setMinimumWidth(440)
        logs_form.addRow("Каталог логов", self._path_picker(self.log_dir_edit))
        self.log_level_combo = QComboBox()
        self.log_level_combo.addItems(LOG_LEVELS)
        self.log_level_combo.setCurrentText(settings.log_level)
        logs_form.addRow("Уровень", self.log_level_combo)
        self.log_size_spin = QSpinBox()
        self.log_size_unit_combo = QComboBox()
        self.log_size_unit_combo.addItems(list(SIZE_UNITS))
        size_value, size_unit = split_measurement(settings.log_max_bytes, SIZE_UNITS)
        self.log_size_unit_combo.setCurrentText(size_unit)
        self.update_log_size_range(size_unit)
        self.log_size_spin.setValue(size_value)
        self.log_size_unit_combo.currentTextChanged.connect(self.update_log_size_range)
        logs_form.addRow(
            "Размер одного файла",
            self._measurement_input(self.log_size_spin, self.log_size_unit_combo),
        )
        self.log_backups_spin = QSpinBox()
        self.log_backups_spin.setRange(1, 100)
        self.log_backups_spin.setValue(settings.log_backup_count)
        logs_form.addRow("Количество архивов", self.log_backups_spin)
        self.log_retention_spin = QSpinBox()
        self.log_retention_unit_combo = QComboBox()
        self.log_retention_unit_combo.addItems(list(RETENTION_UNITS))
        retention_value, retention_unit = split_measurement(
            settings.log_retention_seconds,
            RETENTION_UNITS,
        )
        self.log_retention_unit_combo.setCurrentText(retention_unit)
        self.update_log_retention_range(retention_unit)
        self.log_retention_spin.setValue(retention_value)
        self.log_retention_unit_combo.currentTextChanged.connect(self.update_log_retention_range)
        logs_form.addRow(
            "Срок хранения",
            self._measurement_input(self.log_retention_spin, self.log_retention_unit_combo),
        )
        self.dev_file_check = QCheckBox("Писать в файл также в режиме разработки")
        self.dev_file_check.setChecked(settings.log_to_file_in_dev)
        logs_form.addRow("", self.dev_file_check)
        mode_hint = QLabel(
            "Текущий режим: packaged — только файл" if is_packaged() else "Текущий режим: development — stdout"
        )
        mode_hint.setObjectName("hint")
        logs_form.addRow("", mode_hint)
        layout.addWidget(logs)

        buttons = dialog_buttons(self, "Сохранить")
        buttons.accepted.disconnect()
        buttons.accepted.connect(self.validate_and_accept)
        layout.addWidget(buttons)

    def _path_picker(self, line_edit: QLineEdit) -> QWidget:
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(line_edit, 1)
        browse = QPushButton("Выбрать")
        browse.clicked.connect(lambda: self.choose_directory(line_edit))
        row.addWidget(browse)
        return widget

    def _measurement_input(self, spin: QSpinBox, unit_combo: QComboBox) -> QWidget:
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        spin.setMinimumWidth(160)
        row.addWidget(spin, 1)
        unit_combo.setMinimumWidth(80)
        row.addWidget(unit_combo)
        return widget

    def update_log_size_range(self, unit: str) -> None:
        factor = SIZE_UNITS[unit]
        minimum = max(1, (64 * 1024 + factor - 1) // factor)
        self.log_size_spin.setRange(minimum, MAX_LOG_SIZE_BYTES // factor)

    def update_log_retention_range(self, unit: str) -> None:
        factor = RETENTION_UNITS[unit]
        self.log_retention_spin.setRange(1, MAX_LOG_RETENTION_SECONDS // factor)

    def choose_directory(self, line_edit: QLineEdit) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Выберите каталог", line_edit.text())
        if selected:
            line_edit.setText(selected)

    def restore_default_paths(self) -> None:
        self.data_dir_edit.setText(str(default_data_dir()))
        self.log_dir_edit.setText(str(default_log_dir()))

    def validate_and_accept(self) -> None:
        settings = AppSettings(
            data_dir=self.data_dir_edit.text().strip(),
            log_dir=self.log_dir_edit.text().strip(),
            log_level=self.log_level_combo.currentText(),
            log_max_bytes=combine_measurement(
                self.log_size_spin.value(),
                self.log_size_unit_combo.currentText(),
                SIZE_UNITS,
            ),
            log_backup_count=self.log_backups_spin.value(),
            log_retention_seconds=combine_measurement(
                self.log_retention_spin.value(),
                self.log_retention_unit_combo.currentText(),
                RETENTION_UNITS,
            ),
            log_to_file_in_dev=self.dev_file_check.isChecked(),
        )
        try:
            settings.validate()
        except ValueError as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            return
        self.result_settings = settings
        self.accept()


class HostsApp(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1100, 720)
        self.setMinimumSize(880, 640)
        self.hosts_file = hosts_path()
        self.entries: dict[str, HostEntry] = {}
        self.groups: list[HostGroup] = []
        self.sources: list[UrlSource] = []
        self.origins: Origins = {}
        self._applied_hosts_snapshot: HostsSnapshot = ()
        self._collapsed_group_ids: set[str] = set()
        self._refreshing = False

        self.load_initial_data()
        self.create_widgets()
        self.refresh_table()
        logger.info("app_initialized", hosts_file=str(self.hosts_file), entries_count=len(self.entries))

    def load_initial_data(self) -> None:
        logger.info("initial_data_load_started")
        state_available = state_path().exists()
        state_entries, self.groups = load_state_with_groups()
        try:
            hosts_entries = parse_hosts_text(read_hosts_file(self.hosts_file))
        except Exception:
            logger.exception("hosts_entries_load_failed", hosts_file=str(self.hosts_file))
            hosts_entries = {}
        self.entries, applied_entries = reconcile_persisted_entries(
            state_entries,
            hosts_entries,
            state_available=state_available,
        )
        self.sources, stored_origins = load_sources_state()
        self.origins = normalize_origins(self.entries, stored_origins)
        self._applied_hosts_snapshot = hosts_snapshot(applied_entries)
        logger.info(
            "initial_data_loaded",
            state_entries_count=len(state_entries),
            hosts_entries_count=len(hosts_entries),
            visible_entries_count=len(self.entries),
            groups_count=len(self.groups),
            has_pending_hosts_changes=has_unapplied_hosts_changes(
                self.entries,
                self.groups,
                self._applied_hosts_snapshot,
            ),
        )

    def create_widgets(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("Hosts Manager")
        title.setObjectName("title")
        subtitle = QLabel("Управляйте локальными доменами без ручного редактирования hosts")
        subtitle.setObjectName("subtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch()
        refresh = QPushButton("Перечитать с диска")
        refresh.setToolTip("Заново прочитать local state и системный hosts")
        refresh.clicked.connect(self.reload)
        header.addWidget(refresh)
        settings_button = QPushButton("Настройки")
        settings_button.clicked.connect(self.manage_settings)
        header.addWidget(settings_button)
        root.addLayout(header)

        file_card = QFrame()
        file_card.setObjectName("card")
        file_layout = QHBoxLayout(file_card)
        file_layout.setContentsMargins(16, 12, 16, 12)
        file_label = QLabel(f"Файл hosts   {self.hosts_file}")
        file_label.setObjectName("subtitle")
        file_layout.addWidget(file_label)
        file_layout.addStretch()
        state_button = QPushButton("Папка состояния")
        state_button.clicked.connect(self.open_state_folder)
        file_layout.addWidget(state_button)
        root.addWidget(file_card)

        toolbar = QHBoxLayout()
        add_button = QPushButton("＋  Добавить")
        add_button.setObjectName("primary")
        add_button.clicked.connect(self.add_entry)
        toolbar.addWidget(add_button)
        import_button = QPushButton("Импорт")
        import_button.clicked.connect(self.import_entries)
        toolbar.addWidget(import_button)
        sources_button = QPushButton("Источники")
        sources_button.setToolTip("Управление URL-источниками")
        sources_button.clicked.connect(self.manage_sources)
        toolbar.addWidget(sources_button)
        sync_button = QPushButton("Загрузка из URL")
        sync_button.setToolTip("Загрузить активные источники и проверить изменения")
        sync_button.clicked.connect(self.synchronize_sources)
        toolbar.addWidget(sync_button)
        groups_button = QPushButton("Группы")
        groups_button.clicked.connect(self.manage_groups)
        toolbar.addWidget(groups_button)
        toolbar.addStretch()
        root.addLayout(toolbar)

        filters = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Поиск по домену или IP")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(lambda _text: self.refresh_table())
        filters.addWidget(self.search_edit, 1)
        self.state_filter = QComboBox()
        self.state_filter.addItem("Все записи", "")
        self.state_filter.addItem("Включённые", "enabled")
        self.state_filter.addItem("Отключённые", "disabled")
        self.state_filter.currentIndexChanged.connect(lambda _index: self.refresh_table())
        filters.addWidget(self.state_filter)
        self.group_filter = QComboBox()
        self.group_filter.currentIndexChanged.connect(lambda _index: self.refresh_table())
        filters.addWidget(self.group_filter)
        self.source_filter = QComboBox()
        self.source_filter.currentIndexChanged.connect(lambda _index: self.refresh_table())
        filters.addWidget(self.source_filter)
        root.addLayout(filters)
        self.refresh_filter_options()

        context_bar = QFrame()
        context_bar.setObjectName("card")
        context_layout = QHBoxLayout(context_bar)
        context_layout.setContentsMargins(12, 8, 12, 8)
        self.selection_label = QLabel("Выбрано: 0")
        self.selection_label.setObjectName("subtitle")
        context_layout.addWidget(self.selection_label)
        context_layout.addStretch()
        self.edit_button = QPushButton("Изменить")
        self.edit_button.clicked.connect(self.edit_entry)
        context_layout.addWidget(self.edit_button)
        self.move_button = QPushButton("В группу")
        self.move_button.setToolTip("Переместить все выбранные записи")
        self.move_button.clicked.connect(self.move_selected_entries)
        context_layout.addWidget(self.move_button)
        self.enable_button = QPushButton("Включить")
        self.enable_button.clicked.connect(lambda: self.set_selected_entries_enabled(True))
        context_layout.addWidget(self.enable_button)
        self.disable_button = QPushButton("Отключить")
        self.disable_button.clicked.connect(lambda: self.set_selected_entries_enabled(False))
        context_layout.addWidget(self.disable_button)
        self.delete_button = QPushButton("Удалить")
        self.delete_button.setObjectName("danger")
        self.delete_button.setToolTip("Удалить все выбранные строки")
        self.delete_button.clicked.connect(self.delete_entry)
        context_layout.addWidget(self.delete_button)
        root.addWidget(context_bar)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Включено", "Домен", "Активный IP", "Происхождение", "Всего IP"])
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setToolTip("Для выбора нескольких строк используйте Ctrl/Cmd или Shift")
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(50)
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.doubleClicked.connect(self.edit_table_row)
        self.table.itemSelectionChanged.connect(self.update_selection_actions)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_table_context_menu)
        root.addWidget(self.table, 1)

        footer = QHBoxLayout()
        self.hosts_status = QLabel()
        self.hosts_status.setToolTip("Локальное состояние сохраняется автоматически")
        footer.addWidget(self.hosts_status)
        footer.addStretch()
        preview = QPushButton("Предпросмотр")
        preview.clicked.connect(self.preview_hosts)
        footer.addWidget(preview)
        save = QPushButton("Сохранить в hosts")
        save.setObjectName("primary")
        save.clicked.connect(self.save_managed_block)
        footer.addWidget(save)
        root.addLayout(footer)
        self.update_selection_actions()
        self.refresh_hosts_status()

    def refresh_filter_options(self) -> None:
        def replace_options(
            combo: QComboBox,
            options: list[tuple[str, str]],
        ) -> None:
            selected = combo.currentData()
            blocker = QSignalBlocker(combo)
            combo.clear()
            for label, value in options:
                combo.addItem(label, value)
            selected_index = combo.findData(selected)
            combo.setCurrentIndex(selected_index if selected_index >= 0 else 0)
            del blocker

        replace_options(
            self.group_filter,
            [("Все группы", "")] + [(group.name, group.id) for group in self.groups],
        )
        replace_options(
            self.source_filter,
            [("Все источники", ""), ("Вручную", SOURCE_FILTER_MANUAL)]
            + [(source.name, source.id) for source in self.sources],
        )

    def refresh_table(self, selected_domain: str | None = None) -> None:
        self._refreshing = True
        self.refresh_filter_options()
        blocker = QSignalBlocker(self.table)
        self.table.setRowCount(0)
        query = self.search_edit.text()
        state_filter = str(self.state_filter.currentData() or "")
        group_filter = str(self.group_filter.currentData() or "")
        source_filter = str(self.source_filter.currentData() or "")
        filters_active = bool(query.strip() or state_filter or group_filter or source_filter)
        for group in self.groups:
            total_entries = sum(entry.group_id == group.id for entry in self.entries.values())
            group_entries = sorted(
                (
                    (domain, entry)
                    for domain, entry in self.entries.items()
                    if entry.group_id == group.id
                    and entry_matches_filters(
                        entry,
                        self.origins,
                        query=query,
                        state_filter=state_filter,
                        group_id=group_filter,
                        source_id=source_filter,
                    )
                ),
                key=lambda item: item[0],
            )
            if not group_entries and filters_active:
                continue
            header_row = self.table.rowCount()
            self.table.insertRow(header_row)
            self.table.setSpan(header_row, 0, 1, 5)
            group_cell = QWidget()
            group_cell.setObjectName("groupHeader")
            group_cell.setStyleSheet(
                "QWidget#groupHeader { background: #20232B; "
                "border-top: 1px solid #343845; border-bottom: 1px solid #343845; }"
            )
            group_layout = QHBoxLayout(group_cell)
            group_layout.setContentsMargins(14, 4, 14, 4)
            collapse = QToolButton()
            is_collapsed = group.id in self._collapsed_group_ids
            collapse.setText("▸" if is_collapsed else "▾")
            collapse.setToolTip("Развернуть группу" if is_collapsed else "Свернуть группу")
            collapse.setAccessibleName(collapse.toolTip())
            collapse.clicked.connect(lambda _checked=False, group_id=group.id: self.toggle_group_collapsed(group_id))
            group_layout.addWidget(collapse)
            group_check = QCheckBox()
            group_check.setChecked(group.enabled)
            group_check.setToolTip("Включить или исключить группу из hosts, не меняя отдельные записи")
            group_check.stateChanged.connect(lambda state, group_id=group.id: self.set_group_enabled(group_id, state))
            group_layout.addWidget(group_check)
            group_label = QLabel(group.name)
            group_label.setStyleSheet("font-weight: 700; color: #F1F1F5; background: transparent;")
            group_layout.addWidget(group_label)
            count_text = f"{len(group_entries)} из {total_entries}" if filters_active else f"{total_entries} записей"
            count_label = QLabel(count_text)
            count_label.setObjectName("hint")
            group_layout.addWidget(count_label)
            group_layout.addStretch()
            self.table.setCellWidget(header_row, 0, group_cell)
            self.table.setRowHeight(header_row, 40)

            if is_collapsed:
                continue
            for domain, entry in group_entries:
                row = self.table.rowCount()
                self.table.insertRow(row)

                check = QCheckBox()
                check.setChecked(entry.enabled)
                check.setToolTip("Включить или отключить запись")
                check.stateChanged.connect(lambda state, name=domain: self.set_enabled(name, state))
                check_cell = QWidget()
                check_cell.setStyleSheet("background: transparent;")
                check_layout = QHBoxLayout(check_cell)
                check_layout.setContentsMargins(12, 0, 12, 0)
                check_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
                check_layout.addWidget(check)
                self.table.setCellWidget(row, 0, check_cell)

                domain_item = QTableWidgetItem(domain)
                domain_item.setFont(monospace_font(HOSTS_TABLE_FONT_SIZE))
                domain_item.setData(Qt.ItemDataRole.UserRole, domain)
                domain_item.setToolTip(domain)
                self.table.setItem(row, 1, domain_item)

                combo = QComboBox()
                combo.setFont(monospace_font(HOSTS_TABLE_FONT_SIZE))
                combo.setStyleSheet(f"font-size: {HOSTS_TABLE_FONT_SIZE}pt;")
                combo.addItems(entry.ips)
                combo.setCurrentText(entry.selected_ip)
                combo.setToolTip("\n".join(entry.ips))
                combo.currentTextChanged.connect(lambda ip, name=domain: self.set_selected_ip(name, ip))
                self.table.setCellWidget(row, 2, combo)

                origin = QTableWidgetItem(
                    format_pair_origin(self.origins.get((domain, entry.selected_ip)), self.sources)
                )
                origin.setToolTip(origin.text())
                self.table.setItem(row, 3, origin)

                count = QTableWidgetItem(str(len(entry.ips)))
                count.setFont(monospace_font(HOSTS_TABLE_FONT_SIZE))
                count.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, 4, count)
                if not group.enabled:
                    muted = QColor("#777B87")
                    for item in (domain_item, origin, count):
                        item.setForeground(muted)
                if domain == selected_domain:
                    self.table.selectRow(row)
        self.table.scrollToTop()
        del blocker
        self._refreshing = False
        self.update_selection_actions()
        self.refresh_hosts_status()

    def toggle_group_collapsed(self, group_id: str) -> None:
        if group_id in self._collapsed_group_ids:
            self._collapsed_group_ids.remove(group_id)
        else:
            self._collapsed_group_ids.add(group_id)
        self.refresh_table()

    def edit_table_row(self, index: QModelIndex) -> None:
        domain_item = self.table.item(index.row(), 1)
        if domain_item is None or domain_item.data(Qt.ItemDataRole.UserRole) is None:
            return
        self.table.selectRow(index.row())
        self.edit_entry()

    def update_selection_actions(self) -> None:
        domains = self.selected_domains()
        selected_count = len(domains)
        self.selection_label.setText(f"Выбрано: {selected_count}")
        self.edit_button.setEnabled(selected_count == 1)
        for button in (
            self.move_button,
            self.enable_button,
            self.disable_button,
            self.delete_button,
        ):
            button.setEnabled(selected_count > 0)

    def set_selected_entries_enabled(self, enabled: bool) -> None:
        domains = self.selected_domains()
        changed = False
        for domain in domains:
            entry = self.entries.get(domain)
            if entry is not None and entry.enabled != enabled:
                entry.enabled = enabled
                changed = True
        if not changed:
            return
        self.persist_internal_state()
        logger.info(
            "selected_entries_toggled",
            enabled=enabled,
            entries_count=len(domains),
        )
        self.refresh_table()

    def refresh_hosts_status(self) -> None:
        count = count_unapplied_hosts_changes(
            self.entries,
            self.groups,
            self._applied_hosts_snapshot,
        )
        if count:
            self.hosts_status.setText(f"Не применено в hosts: {count}")
            self.hosts_status.setStyleSheet(
                "color: #F0C674; background: #302A1D; border-radius: 7px; padding: 6px 10px;"
            )
        else:
            self.hosts_status.setText("Hosts актуален")
            self.hosts_status.setStyleSheet(
                "color: #8FD6A8; background: #1D3025; border-radius: 7px; padding: 6px 10px;"
            )

    def show_table_context_menu(self, position: QPoint) -> None:
        index = self.table.indexAt(position)
        if not index.isValid():
            return
        domain_item = self.table.item(index.row(), 1)
        if domain_item is None:
            return
        domain = domain_item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(domain, str) or domain not in self.entries:
            return
        entry = self.entries[domain]
        origin_item = self.table.item(index.row(), 3)
        menu = QMenu(self)
        copy_domain = menu.addAction("Копировать домен")
        copy_ip = menu.addAction("Копировать активный IP")
        copy_origin = menu.addAction("Копировать происхождение")
        selected = menu.exec(self.table.viewport().mapToGlobal(position))
        if selected == copy_domain:
            QApplication.clipboard().setText(domain)
        elif selected == copy_ip:
            QApplication.clipboard().setText(entry.selected_ip)
        elif selected == copy_origin and origin_item is not None:
            QApplication.clipboard().setText(origin_item.text())

    def set_enabled(self, domain: str, state: int) -> None:
        if not self._refreshing and domain in self.entries:
            self.entries[domain].enabled = state == Qt.CheckState.Checked.value
            self.persist_internal_state()
            self.refresh_hosts_status()
            if self.state_filter.currentData():
                QTimer.singleShot(0, self.refresh_table)
            logger.info("entry_toggled", domain=domain, enabled=self.entries[domain].enabled)

    def set_group_enabled(self, group_id: str, state: int) -> None:
        if self._refreshing:
            return
        group = next((group for group in self.groups if group.id == group_id), None)
        if group is None:
            return
        group.enabled = state == Qt.CheckState.Checked.value
        self.persist_internal_state()
        logger.info("group_toggled", group_id=group_id, enabled=group.enabled)
        self.refresh_table()

    def set_selected_ip(self, domain: str, ip: str) -> None:
        if not self._refreshing and domain in self.entries and ip in self.entries[domain].ips:
            self.entries[domain].selected_ip = ip
            for row in range(self.table.rowCount()):
                item = self.table.item(row, 1)
                if item and item.data(Qt.ItemDataRole.UserRole) == domain:
                    origin = format_pair_origin(self.origins.get((domain, ip)), self.sources)
                    origin_item = QTableWidgetItem(origin)
                    origin_item.setToolTip(origin)
                    self.table.setItem(row, 3, origin_item)
                    break
            self.persist_internal_state()
            self.refresh_hosts_status()
            logger.info("entry_selected_ip_changed", domain=domain, selected_ip=ip)

    def selected_domains(self) -> list[str]:
        selected_rows = sorted(self.table.selectionModel().selectedRows(1), key=lambda index: index.row())
        return [
            str(index.data(Qt.ItemDataRole.UserRole))
            for index in selected_rows
            if index.data(Qt.ItemDataRole.UserRole) is not None
        ]

    def selected_domain(self, warn: bool = True) -> str | None:
        domains = self.selected_domains()
        if len(domains) == 1:
            return domains[0]
        if warn:
            message = "Сначала выберите домен" if not domains else "Для этого действия выберите только один домен"
            QMessageBox.warning(self, APP_NAME, message)
        return None

    def manage_groups(self) -> None:
        dialog = GroupsDialog(self, self.groups, self.entries)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.result_groups is None:
            return
        self.groups = normalize_groups(self.entries, dialog.result_groups)
        self.persist_internal_state()
        self.refresh_table()
        logger.info("groups_updated", groups_count=len(self.groups))

    def move_selected_entries(self) -> None:
        domains = self.selected_domains()
        if not domains:
            QMessageBox.warning(self, APP_NAME, "Сначала выберите хотя бы один домен")
            return
        labels = [group.name for group in self.groups]
        current_group_id = self.entries[domains[0]].group_id
        current_index = next(
            (index for index, group in enumerate(self.groups) if group.id == current_group_id),
            0,
        )
        label, accepted = QInputDialog.getItem(
            self,
            "Переместить в группу",
            f"Новая группа для записей ({len(domains)})",
            labels,
            current_index,
            False,
        )
        if not accepted:
            return
        group = next(group for group in self.groups if group.name == label)
        moved = move_entries_to_group(self.entries, domains, group.id, self.groups)
        if not moved:
            return
        self.persist_internal_state()
        self.refresh_table(moved[0] if len(moved) == 1 else None)
        logger.info("entries_moved_to_group", group_id=group.id, moved_count=len(moved))

    def reload(self) -> None:
        self.load_initial_data()
        self.refresh_table()
        logger.info("data_reloaded_from_disk")

    def add_entry(self) -> None:
        dialog = EntryDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_entry:
            return
        entry = dialog.result_entry
        if entry.domain in self.entries:
            added = self.entries[entry.domain].add_ips(entry.ips)
            QMessageBox.information(
                self,
                APP_NAME,
                f"Домен уже существует. Добавленные IP: {', '.join(added) if added else 'нет'}",
            )
        else:
            self.entries[entry.domain] = entry
        mark_domain_manual(self.origins, self.entries[entry.domain])
        self.persist_internal_state()
        logger.info("entry_added", domain=entry.domain, ips_count=len(entry.ips), entries_count=len(self.entries))
        self.refresh_table(entry.domain)

    def edit_entry(self) -> None:
        domain = self.selected_domain()
        if not domain:
            return
        old = self.entries[domain]
        dialog = EntryDialog(self, old)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_entry:
            return
        new = dialog.result_entry
        if new.domain != domain and new.domain in self.entries:
            QMessageBox.critical(self, APP_NAME, "Запись с таким доменом уже существует")
            return
        if old.selected_ip in new.ips:
            new.selected_ip = old.selected_ip
        new.group_id = old.group_id
        del self.entries[domain]
        remove_domain_origins(self.origins, domain)
        self.entries[new.domain] = new
        mark_domain_manual(self.origins, new)
        self.persist_internal_state()
        logger.info("entry_edited", old_domain=domain, new_domain=new.domain, ips_count=len(new.ips))
        self.refresh_table(new.domain)

    def delete_entry(self) -> None:
        domains = self.selected_domains()
        if not domains:
            QMessageBox.warning(self, APP_NAME, "Сначала выберите хотя бы один домен")
            return
        if len(domains) == 1:
            question = f"Удалить {domains[0]}?"
        else:
            visible_domains = "\n".join(f"• {domain}" for domain in domains[:8])
            remainder = len(domains) - 8
            if remainder > 0:
                visible_domains += f"\n• …и ещё {remainder}"
            question = f"Удалить выбранные записи ({len(domains)})?\n\n{visible_domains}"
        answer = QMessageBox.question(self, APP_NAME, question)
        if answer == QMessageBox.StandardButton.Yes:
            removed = delete_entries_from_internal_state(
                self.entries,
                self.origins,
                self.sources,
                domains,
                self.groups,
            )
            logger.info(
                "entries_deleted",
                domains=removed,
                removed_count=len(removed),
                entries_count=len(self.entries),
            )
            self.refresh_table()

    def import_entries(self) -> None:
        dialog = ImportDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_data:
            return
        mode, incoming = dialog.result_data
        try:
            if mode == "merge":
                new_entries, diff = merge_entries(self.entries, incoming)
                title = "Предпросмотр импорта: обновление"
            else:
                new_entries, diff = replace_entries(self.entries, incoming)
                title = "Предпросмотр импорта: замена"
        except Exception as exc:
            logger.warning("import_failed", mode=mode, error=str(exc))
            QMessageBox.critical(self, APP_NAME, f"Импорт не удался:\n{exc}")
            return
        preview = ChangePreview(self, title, diff, "Применить импорт")
        if preview.exec() == QDialog.DialogCode.Accepted:
            self.entries = new_entries
            if mode == "replace":
                self.origins = {
                    (domain, ip): PairOrigin(manual=True) for domain, entry in self.entries.items() for ip in entry.ips
                }
            else:
                for domain in incoming:
                    mark_domain_manual(self.origins, self.entries[domain])
            self.refresh_table()
            self.persist_internal_state()
            QMessageBox.information(
                self,
                APP_NAME,
                "Импорт применён. Нажмите «Сохранить в hosts», чтобы записать изменения.",
            )

    def build_preview_texts(self) -> tuple[str, str]:
        original = read_hosts_file(self.hosts_file)
        content = build_preserve_hosts_text(original, self.entries, self.groups)
        stats = summarize_diff_rows(build_side_by_side_diff(original, content))
        logger.info(
            "hosts_preview_built",
            hosts_file=str(self.hosts_file),
            entries_count=len(self.entries),
            diff_added=stats["added"],
            diff_removed=stats["removed"],
            diff_changed=stats["changed"],
        )
        return original, content

    def preview_hosts(self) -> None:
        try:
            original, content = self.build_preview_texts()
            HostsDiffPreview(self, original, content).exec()
        except Exception as exc:
            logger.exception("hosts_preview_failed")
            QMessageBox.critical(self, APP_NAME, f"Не удалось построить предпросмотр:\n{exc}")

    def save_managed_block(self) -> None:
        try:
            original, content = self.build_preview_texts()
            dialog = HostsDiffPreview(self, original, content, "Сохранить")
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.write_content(content)
        except Exception as exc:
            logger.exception("hosts_save_failed")
            QMessageBox.critical(self, APP_NAME, f"Сохранение не удалось:\n{exc}")

    def write_content(self, content: str) -> None:
        try:
            backup = write_hosts(self.hosts_file, content)
        except PermissionError:
            backup = self.write_content_elevated(content)
        self.persist_internal_state()
        self._applied_hosts_snapshot = hosts_snapshot(self.entries, self.groups)
        self.refresh_hosts_status()
        logger.info("hosts_saved", hosts_file=str(self.hosts_file), backup=str(backup))
        QMessageBox.information(self, APP_NAME, f"Hosts сохранён.\nРезервная копия:\n{backup}")

    def write_content_elevated(self, content: str) -> Path:
        answer = QMessageBox.warning(
            self,
            APP_NAME,
            "Для записи нужны права администратора.\n\nЗапросить права и продолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            raise PermissionError("Пользователь отменил запрос прав администратора")
        try:
            return write_hosts_elevated(self.hosts_file, content)
        except ElevatedWriteError as exc:
            raise PermissionError(f"Не удалось получить права администратора: {exc}") from exc

    def open_state_folder(self) -> None:
        folder = state_path().parent
        folder.mkdir(parents=True, exist_ok=True)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            QMessageBox.information(self, APP_NAME, f"Папка состояния:\n{folder}")

    def manage_settings(self) -> None:
        current = get_settings()
        dialog = SettingsDialog(self, current)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.result_settings is None:
            return
        updated = dialog.result_settings

        if updated.data_path != current.data_path:
            answer = QMessageBox.question(
                self,
                APP_NAME,
                "Скопировать текущие файлы данных в новый каталог?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Yes:
                try:
                    self.copy_data_files(current.data_path, updated.data_path)
                except OSError as exc:
                    QMessageBox.critical(self, APP_NAME, f"Не удалось скопировать данные:\n{exc}")
                    return
        try:
            updated.data_path.mkdir(parents=True, exist_ok=True)
            if is_packaged() or updated.log_to_file_in_dev:
                updated.log_path.mkdir(parents=True, exist_ok=True)
            save_settings(updated)
            log_path = configure_logging(updated)
        except OSError as exc:
            QMessageBox.critical(self, APP_NAME, f"Не удалось сохранить настройки:\n{exc}")
            return
        logger.info("settings_updated", data_dir=updated.data_dir, log_dir=updated.log_dir)
        destination = str(log_path) if log_path else "stderr"
        QMessageBox.information(self, APP_NAME, f"Настройки сохранены.\nТекущий вывод логов: {destination}")

    @staticmethod
    def copy_data_files(source_dir: Path, destination_dir: Path) -> None:
        destination_dir.mkdir(parents=True, exist_ok=True)
        for filename in ("state.json", "sources.json"):
            source = source_dir / filename
            destination = destination_dir / filename
            if source.exists() and not destination.exists():
                shutil.copy2(source, destination)

    def persist_internal_state(self) -> None:
        save_internal_state(
            self.entries,
            self.groups,
            self.sources,
            self.origins,
        )

    def manage_sources(self) -> None:
        previous_sources = {source.id: source for source in self.sources}
        dialog = SourcesDialog(self, self.sources)
        result = dialog.exec()
        if result != QDialog.DialogCode.Accepted:
            return
        self.sources = dialog.sources

        removed_ids = set(previous_sources) - {source.id for source in self.sources}
        for source_id in removed_ids:
            self.entries, self.origins = apply_source(
                self.entries,
                self.origins,
                previous_sources[source_id],
                {},
                remove_missing=True,
            )
        if removed_ids:
            self.refresh_table()
        self.persist_internal_state()

    def synchronize_sources(self) -> None:
        active_sources = [source for source in self.sources if source.enabled]
        if not active_sources:
            QMessageBox.warning(
                self,
                APP_NAME,
                "Нет активных URL-источников. Добавьте или включите их в окне «Источники».",
            )
            return
        dialog = SourceSyncDialog(self, self.sources)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.result_action is None:
            return
        self.apply_sources_action(dialog.result_action)

    def apply_sources_action(self, action: str) -> None:
        active_sources = [source for source in self.sources if source.enabled]
        progress = QProgressDialog("Загрузка источников…", "Отмена", 0, len(active_sources), self)
        progress.setWindowTitle("URL-источники")
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        results: list[SourceFetchResult] = []
        canceled = False
        for index, source in enumerate(active_sources):
            progress.setLabelText(
                f"Загрузка «{source.name}»\n{source.url}\n\nОтмена остановит операцию перед следующим источником."
            )
            progress.setValue(index)
            QApplication.processEvents()
            if progress.wasCanceled():
                canceled = True
                break
            try:
                entries = fetch_source(source)
                results.append(SourceFetchResult(source, entries=entries))
            except Exception as exc:
                logger.warning(
                    "url_source_fetch_failed",
                    source_id=source.id,
                    source_name=source.name,
                    error=str(exc),
                )
                results.append(SourceFetchResult(source, error=str(exc)))
            progress.setValue(index + 1)
            QApplication.processEvents()
            if progress.wasCanceled():
                canceled = True
                break
        progress.close()
        if canceled:
            logger.info("url_sources_fetch_canceled", completed_sources=len(results))
            QMessageBox.information(
                self,
                APP_NAME,
                "Загрузка отменена. Внутреннее состояние не изменено.",
            )
            return

        successful_count = sum(result.succeeded for result in results)
        can_apply = successful_count > 0 and not (action == "replace" and successful_count != len(results))
        if can_apply:
            candidate_entries, candidate_origins = prepare_sources_update(
                self.entries,
                self.origins,
                results,
                action,
            )
        else:
            candidate_entries = clone_entries(self.entries)
            candidate_origins = normalize_origins(candidate_entries, self.origins)
        summary = summarize_source_changes(
            self.entries,
            self.origins,
            candidate_entries,
            candidate_origins,
        )
        preview = SourceSyncPreview(
            self,
            action,
            results,
            summary,
            can_apply=can_apply,
        )
        if preview.exec() != QDialog.DialogCode.Accepted:
            return

        self.entries = candidate_entries
        self.origins = candidate_origins
        self.refresh_table()
        self.persist_internal_state()
        logger.info(
            "url_sources_applied",
            action=action,
            successful_sources=successful_count,
            failed_sources=len(results) - successful_count,
            entries_count=len(self.entries),
        )
        QMessageBox.information(
            self,
            APP_NAME,
            "Внутреннее состояние обновлено. Файл hosts не изменён.\n\n"
            "Используйте «Предпросмотр», чтобы проверить diff, и "
            "«Сохранить в hosts», чтобы применить изменения вручную.",
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        if not has_unapplied_hosts_changes(
            self.entries,
            self.groups,
            self._applied_hosts_snapshot,
        ):
            event.accept()
            return

        answer = QMessageBox.warning(
            self,
            "Есть неприменённые изменения",
            "Все изменения сохранены в local state, но ещё не применены в hosts.\n\n"
            "Закрыть приложение без применения в hosts?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            logger.info("app_closed_with_unapplied_hosts_changes")
            event.accept()
        else:
            event.ignore()


def main() -> int:
    settings = get_settings()
    log_path = configure_logging(settings)
    logger.info(
        "app_starting",
        mode="packaged" if is_packaged() else "development",
        log_path=str(log_path) if log_path else None,
        data_dir=settings.data_dir,
    )
    application = QApplication.instance()
    if application is None:
        application = QApplication(sys.argv)
    assert isinstance(application, QApplication)
    application.setApplicationName(APP_NAME)
    application.setStyle("Fusion")
    application.setStyleSheet(APP_STYLE)
    window = HostsApp()
    sigint_timer = install_sigint_handler(application, window)
    window.show()
    result = application.exec()
    sigint_timer.stop()
    logger.info("app_stopped")
    return result


def install_sigint_handler(application: QApplication, window: HostsApp) -> QTimer:
    """Let Ctrl+C follow the regular window-close flow, including dirty checks."""

    def close_from_terminal(_signal_number: int, _frame: object) -> None:
        modal = application.activeModalWidget()
        if isinstance(modal, QDialog):
            modal.reject()
        QTimer.singleShot(0, window.close)

    signal.signal(signal.SIGINT, close_from_terminal)
    timer = QTimer(application)
    timer.timeout.connect(lambda: None)
    timer.start(200)
    return timer
