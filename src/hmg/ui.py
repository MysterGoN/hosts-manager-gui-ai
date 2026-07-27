from __future__ import annotations

import difflib
import re
import sys
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, QUrl
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
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
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from hmg.core import (
    APP_NAME,
    ElevatedWriteError,
    EntryDiff,
    HostEntry,
    build_preserve_hosts_text,
    hosts_path,
    load_state,
    merge_entries,
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

logger = get_logger(__name__)

DiffRow = tuple[str, str, str | None, str | None]
DiffStats = dict[str, int]

DIFF_COLORS = {
    "added": "#234D37",
    "removed": "#573338",
    "changed": "#554A25",
}

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
QPushButton#primary {
    background: #7C5CFC;
    border-color: #7C5CFC;
    color: white;
}
QPushButton#primary:hover { background: #8B6DFF; }
QPushButton#danger { color: #FF8A94; }
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
            side == "before"
            and not before_line
            and bool(after_line)
            and after_tag in {"added", "changed"}
        ) or (
            side == "after"
            and not after_line
            and bool(before_line)
            and before_tag in {"removed", "changed"}
        )
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


class ImportDialog(QDialog):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.result_data: tuple[str, dict[str, HostEntry]] | None = None
        self.setWindowTitle("Импорт записей")
        self.resize(760, 580)

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

        label_row = QHBoxLayout()
        label_row.addWidget(QLabel("Данные для импорта"))
        label_row.addStretch()
        load_button = QPushButton("Выбрать файл")
        load_button.clicked.connect(self.load_file)
        label_row.addWidget(load_button)
        layout.addLayout(label_row)
        self.import_text = QTextEdit()
        self.import_text.setFont(monospace_font())
        self.import_text.setPlaceholderText("example.local  127.0.0.1")
        layout.addWidget(self.import_text, 1)

        buttons = dialog_buttons(self, "Импортировать")
        buttons.accepted.disconnect()
        buttons.accepted.connect(self.parse_and_accept)
        layout.addWidget(buttons)

    def load_file(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "Импорт записей",
            "",
            "Поддерживаемые файлы (*.csv *.tsv *.json *.txt);;Все файлы (*)",
        )
        if not path:
            return
        logger.info("import_file_selected", path=path)
        try:
            self.import_text.setPlainText(Path(path).read_text(encoding="utf-8-sig"))
        except Exception as exc:
            logger.warning("import_file_read_failed", path=path, error=str(exc))
            QMessageBox.critical(self, APP_NAME, f"Не удалось прочитать файл:\n{exc}")

    def parse_and_accept(self) -> None:
        try:
            entries = parse_import_text(self.import_text.toPlainText())
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, f"Импорт не удался:\n{exc}")
            return
        mode = "merge" if self.merge_radio.isChecked() else "replace"
        self.result_data = (mode, entries)
        logger.info("import_dialog_accepted", mode=mode, entries_count=len(entries))
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


class HostsApp(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1100, 720)
        self.setMinimumSize(880, 560)
        self.hosts_file = hosts_path()
        self.entries: dict[str, HostEntry] = {}
        self._refreshing = False

        self.load_initial_data()
        self.create_widgets()
        self.refresh_table()
        logger.info("app_initialized", hosts_file=str(self.hosts_file), entries_count=len(self.entries))

    def load_initial_data(self) -> None:
        logger.info("initial_data_load_started")
        state_entries = load_state()
        try:
            hosts_entries = parse_hosts_text(read_hosts_file(self.hosts_file))
        except Exception:
            logger.exception("hosts_entries_load_failed", hosts_file=str(self.hosts_file))
            hosts_entries = {}
        self.entries = {}
        for domain, entry in hosts_entries.items():
            state_entry = state_entries.get(domain)
            if state_entry:
                state_entry.enabled = entry.enabled
                state_entry.selected_ip = entry.selected_ip
                state_entry.add_ips(entry.ips)
                self.entries[domain] = state_entry
            else:
                self.entries[domain] = entry
        logger.info(
            "initial_data_loaded",
            state_entries_count=len(state_entries),
            hosts_entries_count=len(hosts_entries),
            visible_entries_count=len(self.entries),
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
        refresh = QPushButton("Обновить")
        refresh.clicked.connect(self.reload)
        header.addWidget(refresh)
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
        edit_button = QPushButton("Изменить")
        edit_button.clicked.connect(self.edit_entry)
        toolbar.addWidget(edit_button)
        delete_button = QPushButton("Удалить")
        delete_button.setObjectName("danger")
        delete_button.clicked.connect(self.delete_entry)
        toolbar.addWidget(delete_button)
        import_button = QPushButton("Импорт")
        import_button.clicked.connect(self.import_entries)
        toolbar.addWidget(import_button)
        toolbar.addStretch()
        root.addLayout(toolbar)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Включено", "Домен", "Активный IP", "Всего IP"])
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(50)
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.doubleClicked.connect(lambda _index: self.edit_entry())
        root.addWidget(self.table, 1)

        footer = QHBoxLayout()
        hint = QLabel("При сохранении система автоматически запросит права администратора")
        hint.setObjectName("hint")
        footer.addWidget(hint)
        footer.addStretch()
        preview = QPushButton("Предпросмотр")
        preview.clicked.connect(self.preview_hosts)
        footer.addWidget(preview)
        save = QPushButton("Сохранить в hosts")
        save.setObjectName("primary")
        save.clicked.connect(self.save_managed_block)
        footer.addWidget(save)
        root.addLayout(footer)

    def refresh_table(self, selected_domain: str | None = None) -> None:
        self._refreshing = True
        blocker = QSignalBlocker(self.table)
        self.table.setRowCount(0)
        for row, (domain, entry) in enumerate(sorted(self.entries.items())):
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
            domain_item.setFont(monospace_font())
            domain_item.setData(Qt.ItemDataRole.UserRole, domain)
            self.table.setItem(row, 1, domain_item)

            combo = QComboBox()
            combo.setFont(monospace_font())
            combo.addItems(entry.ips)
            combo.setCurrentText(entry.selected_ip)
            combo.currentTextChanged.connect(lambda ip, name=domain: self.set_selected_ip(name, ip))
            self.table.setCellWidget(row, 2, combo)

            count = QTableWidgetItem(str(len(entry.ips)))
            count.setFont(monospace_font())
            count.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 3, count)
            if domain == selected_domain:
                self.table.selectRow(row)
        del blocker
        self._refreshing = False

    def set_enabled(self, domain: str, state: int) -> None:
        if not self._refreshing and domain in self.entries:
            self.entries[domain].enabled = state == Qt.CheckState.Checked.value
            logger.info("entry_toggled", domain=domain, enabled=self.entries[domain].enabled)

    def set_selected_ip(self, domain: str, ip: str) -> None:
        if not self._refreshing and domain in self.entries and ip in self.entries[domain].ips:
            self.entries[domain].selected_ip = ip
            logger.info("entry_selected_ip_changed", domain=domain, selected_ip=ip)

    def selected_domain(self, warn: bool = True) -> str | None:
        row = self.table.currentRow()
        item = self.table.item(row, 1) if row >= 0 else None
        if item:
            return str(item.data(Qt.ItemDataRole.UserRole))
        if warn:
            QMessageBox.warning(self, APP_NAME, "Сначала выберите домен")
        return None

    def reload(self) -> None:
        answer = QMessageBox.question(
            self,
            APP_NAME,
            "Обновить данные из состояния и файла hosts?\nНесохранённые изменения будут потеряны.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.load_initial_data()
        self.refresh_table()

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
        del self.entries[domain]
        self.entries[new.domain] = new
        logger.info("entry_edited", old_domain=domain, new_domain=new.domain, ips_count=len(new.ips))
        self.refresh_table(new.domain)

    def delete_entry(self) -> None:
        domain = self.selected_domain()
        if not domain:
            return
        answer = QMessageBox.question(self, APP_NAME, f"Удалить {domain}?")
        if answer == QMessageBox.StandardButton.Yes:
            del self.entries[domain]
            logger.info("entry_deleted", domain=domain, entries_count=len(self.entries))
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
            self.refresh_table()
            save_state(self.entries)
            QMessageBox.information(
                self,
                APP_NAME,
                "Импорт применён. Нажмите «Сохранить в hosts», чтобы записать изменения.",
            )

    def build_preview_texts(self) -> tuple[str, str]:
        original = read_hosts_file(self.hosts_file)
        content = build_preserve_hosts_text(original, self.entries)
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
        save_state(self.entries)
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


def main() -> int:
    log_path = state_path().parent / "hmg.log"
    configure_logging(log_path)
    logger.info("app_starting", log_path=str(log_path))
    application = QApplication.instance()
    if application is None:
        application = QApplication(sys.argv)
    assert isinstance(application, QApplication)
    application.setApplicationName(APP_NAME)
    application.setStyle("Fusion")
    application.setStyleSheet(APP_STYLE)
    window = HostsApp()
    window.show()
    result = application.exec()
    logger.info("app_stopped")
    return result
