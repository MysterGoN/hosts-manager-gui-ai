from __future__ import annotations

import difflib
import json
import os
import platform
import re
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

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

ENABLED_MARK = "🟢"
logger = get_logger(__name__)

DiffRow = tuple[str, str, str | None, str | None]
DiffStats = dict[str, int]

LIGHT_DIFF_COLORS = {
    "added": "#d8f5d1",
    "removed": "#ffd9d9",
    "changed": "#fff3bf",
}
DARK_DIFF_COLORS = {
    "added": "#294936",
    "removed": "#563438",
    "changed": "#514722",
}


class ChangePreview(tk.Toplevel):
    def __init__(self, parent: tk.Tk, title: str, diff: EntryDiff, confirm_text: str = "Применить") -> None:
        super().__init__(parent)
        self.title(title)
        self.geometry("760x520")
        self.minsize(640, 420)
        self.result = False
        self.transient(parent)
        self.grab_set()

        frame = ttk.Frame(self, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        text = tk.Text(frame, wrap="word")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        lines = self.format_diff(diff)
        text.insert("1.0", "\n".join(lines))
        text.configure(state="disabled")

        buttons = ttk.Frame(self, padding=(10, 0, 10, 10))
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="Отмена", command=self.cancel).pack(side=tk.RIGHT)
        ttk.Button(buttons, text=confirm_text, command=self.ok).pack(side=tk.RIGHT, padx=(0, 8))

        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.wait_window(self)

    @staticmethod
    def format_diff(diff: EntryDiff) -> list[str]:
        lines: list[str] = []
        added_domains = diff.get("added_domains", [])
        removed_domains = diff.get("removed_domains", [])
        added_ips = diff.get("added_ips", {})

        lines.append("Добавленные записи:")
        if added_domains:
            lines.extend([f"  + {d}" for d in sorted(added_domains)])
        else:
            lines.append("  — нет")

        if removed_domains is not None:
            lines.append("")
            lines.append("Удаленные записи:")
            if removed_domains:
                lines.extend([f"  - {d}" for d in sorted(removed_domains)])
            else:
                lines.append("  — нет")

        lines.append("")
        lines.append("IP, добавленные к существующим доменам:")
        if added_ips:
            for domain in sorted(added_ips):
                lines.append(f"  {domain}:")
                lines.extend([f"    + {ip}" for ip in added_ips[domain]])
        else:
            lines.append("  — нет")
        return lines

    def ok(self) -> None:
        self.result = True
        self.destroy()

    def cancel(self) -> None:
        self.result = False
        self.destroy()


class HostsDiffPreview(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Tk,
        before: str,
        after: str,
        confirm_text: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.title("Предпросмотр изменений hosts")
        self.geometry("1100x700")
        self.minsize(820, 520)
        self.result = False
        self.transient(parent)
        self.grab_set()
        self.diff_rows = build_side_by_side_diff(before, after)

        root = ttk.Frame(self, padding=10)
        root.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(root)
        header.pack(fill=tk.X)
        ttk.Label(header, text="Сейчас").pack(side=tk.LEFT, expand=True, fill=tk.X)
        ttk.Label(header, text="После сохранения").pack(side=tk.LEFT, expand=True, fill=tk.X)

        self.status_var = tk.StringVar(value=format_diff_status(summarize_diff_rows(self.diff_rows)))
        ttk.Label(root, textvariable=self.status_var).pack(fill=tk.X, pady=(6, 0))

        body = ttk.Frame(root)
        body.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        self.before_text = tk.Text(body, wrap="none", width=1, font=("Menlo", 12))
        self.after_text = tk.Text(body, wrap="none", width=1, font=("Menlo", 12))
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.scroll_both)
        self.before_text.configure(yscrollcommand=scroll.set)
        self.after_text.configure(yscrollcommand=scroll.set)
        self.before_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.after_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.configure_tags(self.before_text)
        self.configure_tags(self.after_text)
        self.insert_diff()

        buttons = ttk.Frame(root)
        buttons.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(buttons, text="Закрыть", command=self.cancel).pack(side=tk.RIGHT)
        if confirm_text:
            ttk.Button(buttons, text=confirm_text, command=self.ok).pack(side=tk.RIGHT, padx=(0, 8))

        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.wait_window(self)

    @staticmethod
    def configure_tags(widget: tk.Text) -> None:
        colors = diff_colors_for_widget(widget)
        for tag, background in colors.items():
            widget.tag_configure(tag, background=background)
        # Diff backgrounds must not cover the native text-selection highlight.
        widget.tag_raise("sel")

    def insert_diff(self) -> None:
        self.before_text.delete("1.0", tk.END)
        self.after_text.delete("1.0", tk.END)

        for line_number, (before_line, after_line, before_tag, after_tag) in enumerate(self.diff_rows, start=1):
            self.before_text.insert(tk.END, before_line + "\n")
            self.after_text.insert(tk.END, after_line + "\n")
            self.apply_line_tag(self.before_text, line_number, before_line, before_tag)
            self.apply_line_tag(self.after_text, line_number, after_line, after_tag)

        self.before_text.configure(state="disabled")
        self.after_text.configure(state="disabled")

    @staticmethod
    def apply_line_tag(widget: tk.Text, line_number: int, line: str, tag: str | None) -> None:
        if tag and line:
            widget.tag_add(tag, f"{line_number}.0", f"{line_number}.end")

    def scroll_both(self, *args: str) -> None:
        self.before_text.yview(*args)
        self.after_text.yview(*args)

    def ok(self) -> None:
        self.result = True
        self.destroy()

    def cancel(self) -> None:
        self.result = False
        self.destroy()


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


def diff_colors_for_widget(widget: tk.Text) -> dict[str, str]:
    """Choose muted diff colors that retain contrast with the current text theme."""
    try:
        red, green, blue = widget.winfo_rgb(widget.cget("background"))
        luminance = (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 65535
    except tk.TclError:
        luminance = 1.0
    return DARK_DIFF_COLORS if luminance < 0.5 else LIGHT_DIFF_COLORS


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


class EntryDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk, entry: HostEntry | None = None) -> None:
        super().__init__(parent)
        self.title("Редактировать запись" if entry else "Добавить запись")
        self.geometry("520x300")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.result: HostEntry | None = None

        frame = ttk.Frame(self, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Домен:").grid(row=0, column=0, sticky="w")
        self.domain_var = tk.StringVar(value=entry.domain if entry else "")
        domain_entry = ttk.Entry(frame, textvariable=self.domain_var, width=50)
        domain_entry.grid(row=0, column=1, sticky="ew", pady=4)

        ttk.Label(frame, text="IP:").grid(row=1, column=0, sticky="nw")
        self.ips_text = tk.Text(frame, width=42, height=6)
        self.ips_text.grid(row=1, column=1, sticky="ew", pady=4)
        if entry:
            self.ips_text.insert("1.0", "\n".join(entry.ips))

        help_label = ttk.Label(frame, text="Один IP на строку или через ; / пробелы")
        help_label.grid(row=2, column=1, sticky="w")

        self.enabled_var = tk.BooleanVar(value=entry.enabled if entry else True)
        ttk.Checkbutton(frame, text="Включено", variable=self.enabled_var).grid(row=3, column=1, sticky="w", pady=8)

        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, columnspan=2, sticky="e", pady=(16, 0))
        ttk.Button(buttons, text="Отмена", command=self.cancel).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="OK", command=self.ok).pack(side=tk.RIGHT, padx=(0, 8))

        frame.columnconfigure(1, weight=1)
        domain_entry.focus_set()
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.wait_window(self)

    def ok(self) -> None:
        try:
            domain = validate_domain(self.domain_var.get())
            ips_raw = self.ips_text.get("1.0", tk.END)
            ips: list[str] = []
            for part in re.split(r"[;\s]+", ips_raw):
                part = part.strip()
                if part:
                    ip = validate_ip(part)
                    if ip not in ips:
                        ips.append(ip)
            if not ips:
                raise ValueError("Нужно указать хотя бы один IP")
            self.result = HostEntry(domain=domain, ips=ips, selected_ip=ips[0], enabled=self.enabled_var.get())
            self.destroy()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)

    def cancel(self) -> None:
        self.result = None
        self.destroy()


class ImportDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk) -> None:
        super().__init__(parent)
        self.title("Импорт записей")
        self.geometry("760x560")
        self.minsize(640, 460)
        self.transient(parent)
        self.grab_set()
        self.result: tuple[str, dict[str, HostEntry]] | None = None

        root = ttk.Frame(self, padding=12)
        root.pack(fill=tk.BOTH, expand=True)

        mode_frame = ttk.LabelFrame(root, text="Режим")
        mode_frame.pack(fill=tk.X)
        self.mode_var = tk.StringVar(value="merge")
        ttk.Radiobutton(mode_frame, text="Обновить", value="merge", variable=self.mode_var).pack(
            side=tk.LEFT,
            padx=(8, 16),
            pady=8,
        )
        ttk.Radiobutton(mode_frame, text="Заменить", value="replace", variable=self.mode_var).pack(
            side=tk.LEFT,
            pady=8,
        )

        ttk.Label(root, text="Текст для импорта").pack(anchor="w", pady=(12, 4))
        text_frame = ttk.Frame(root)
        text_frame.pack(fill=tk.BOTH, expand=True)
        self.import_text = tk.Text(text_frame, wrap="none", height=14)
        y_scroll = ttk.Scrollbar(text_frame, orient="vertical", command=self.import_text.yview)
        x_scroll = ttk.Scrollbar(text_frame, orient="horizontal", command=self.import_text.xview)
        self.import_text.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.import_text.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)

        buttons = ttk.Frame(root)
        buttons.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(buttons, text="Импорт из файла", command=self.load_file).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Отмена", command=self.cancel).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Импорт", command=self.ok).pack(side=tk.RIGHT, padx=(0, 8))

        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.wait_window(self)

    def load_file(self) -> None:
        path_str = filedialog.askopenfilename(
            parent=self,
            title="Импорт записей",
            filetypes=[
                ("Поддерживаемые файлы", "*.csv *.tsv *.json *.txt"),
                ("CSV/TSV", "*.csv *.tsv"),
                ("JSON", "*.json"),
                ("Все файлы", "*.*"),
            ],
        )
        if not path_str:
            return
        logger.info("import_file_selected", path=path_str)
        try:
            text = Path(path_str).read_text(encoding="utf-8-sig")
        except Exception as exc:
            logger.warning("import_file_read_failed", path=path_str, error=str(exc))
            messagebox.showerror(APP_NAME, f"Не удалось прочитать файл:\n{exc}", parent=self)
            return
        self.import_text.delete("1.0", tk.END)
        self.import_text.insert("1.0", text)

    def ok(self) -> None:
        try:
            text = self.import_text.get("1.0", tk.END)
            entries = parse_import_text(text)
        except Exception as exc:
            logger.warning("import_dialog_parse_failed", error=str(exc))
            messagebox.showerror(APP_NAME, f"Импорт не удался:\n{exc}", parent=self)
            return
        logger.info("import_dialog_accepted", mode=self.mode_var.get(), entries_count=len(entries))
        self.result = (self.mode_var.get(), entries)
        self.destroy()

    def cancel(self) -> None:
        self.result = None
        self.destroy()


class SelectIpDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk, entry: HostEntry) -> None:
        super().__init__(parent)
        self.title(f"Выбор IP для {entry.domain}")
        self.geometry("420x300")
        self.transient(parent)
        self.grab_set()
        self.result: str | None = None

        frame = ttk.Frame(self, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text=f"Домен: {entry.domain}").pack(anchor="w")
        self.listbox = tk.Listbox(frame, height=8)
        self.listbox.pack(fill=tk.BOTH, expand=True, pady=8)
        for ip in entry.ips:
            self.listbox.insert(tk.END, ip)
        try:
            self.listbox.selection_set(entry.ips.index(entry.selected_ip))
            self.listbox.see(entry.ips.index(entry.selected_ip))
        except ValueError:
            self.listbox.selection_set(0)

        buttons = ttk.Frame(frame)
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="Отмена", command=self.cancel).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Выбрать", command=self.ok).pack(side=tk.RIGHT, padx=(0, 8))
        self.listbox.bind("<Double-Button-1>", lambda _e: self.ok())
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.wait_window(self)

    def ok(self) -> None:
        sel = self.listbox.curselection()  # type: ignore[no-untyped-call]
        if not sel:
            messagebox.showwarning(APP_NAME, "Выберите IP", parent=self)
            return
        self.result = self.listbox.get(sel[0])
        self.destroy()

    def cancel(self) -> None:
        self.result = None
        self.destroy()


class HostsApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.geometry("980x620")
        self.minsize(820, 480)

        self.hosts_file = hosts_path()
        self.entries: dict[str, HostEntry] = {}
        self.ip_editor: ttk.Combobox | None = None
        self.ip_editor_domain: str | None = None

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
        root = ttk.Frame(self, padding=10)
        root.pack(fill=tk.BOTH, expand=True)

        top = ttk.Frame(root)
        top.pack(fill=tk.X)
        ttk.Label(top, text=f"Файл hosts: {self.hosts_file}").pack(side=tk.LEFT)
        ttk.Button(top, text="Обновить", command=self.reload).pack(side=tk.RIGHT)

        style = ttk.Style(self)
        style.configure("Hosts.Treeview", rowheight=30)

        columns = ("row_number", "enabled", "domain", "selected_ip")
        self.tree = ttk.Treeview(root, columns=columns, show="headings", selectmode="browse")
        self.tree.configure(style="Hosts.Treeview")
        self.tree.heading("row_number", text="#")
        self.tree.heading("enabled", text="Включено")
        self.tree.heading("domain", text="Домен")
        self.tree.heading("selected_ip", text="IP")
        self.tree.column("row_number", width=52, minwidth=44, anchor="center", stretch=False)
        self.tree.column("enabled", width=76, minwidth=76, anchor="center", stretch=False)
        self.tree.column("domain", width=420, minwidth=220)
        self.tree.column("selected_ip", width=220, minwidth=140)
        self.tree.bind("<Button-1>", self.on_tree_click)
        self.tree.pack(fill=tk.BOTH, expand=True, pady=10)

        buttons = ttk.Frame(root)
        buttons.pack(fill=tk.X)

        ttk.Button(buttons, text="Добавить", command=self.add_entry).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Изменить", command=self.edit_entry).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Удалить", command=self.delete_entry).pack(side=tk.LEFT)
        ttk.Separator(buttons, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=8)
        ttk.Button(buttons, text="Импорт", command=self.import_entries).pack(side=tk.LEFT)

        save_buttons = ttk.Frame(root)
        save_buttons.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(save_buttons, text="Предпросмотр", command=self.preview_hosts).pack(side=tk.LEFT)
        ttk.Button(save_buttons, text="Сохранить HMG-блок", command=self.save_managed_block).pack(side=tk.LEFT, padx=4)
        ttk.Button(save_buttons, text="Открыть папку состояния", command=self.open_state_folder).pack(side=tk.RIGHT)

        hint = (
            "Подсказка: запись в /etc/hosts или Windows hosts обычно требует прав администратора. "
            "При сохранении приложение запросит их автоматически."
        )
        ttk.Label(root, text=hint).pack(fill=tk.X, pady=(8, 0))

    def refresh_table(self) -> None:
        self.close_ip_editor()
        self.tree.delete(*self.tree.get_children())
        for row_number, (domain, entry) in enumerate(sorted(self.entries.items()), start=1):
            self.tree.insert(
                "",
                tk.END,
                iid=domain,
                values=(row_number, ENABLED_MARK if entry.enabled else "", domain, entry.selected_ip),
            )

    def on_tree_click(self, event: tk.Event[tk.Misc]) -> str | None:
        if self.tree.identify("region", event.x, event.y) != "cell":  # type: ignore[no-untyped-call]
            self.close_ip_editor()
            return None

        domain = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if not domain:
            self.close_ip_editor()
            return None

        self.tree.selection_set(domain)
        if column == "#2":
            self.entries[domain].enabled = not self.entries[domain].enabled
            self.refresh_table()
            self.tree.selection_set(domain)
            return "break"
        if column == "#4":
            self.show_ip_editor(domain)
            return "break"

        self.close_ip_editor()
        return None

    def show_ip_editor(self, domain: str) -> None:
        self.close_ip_editor()
        entry = self.entries[domain]
        bbox = self.tree.bbox(domain, "selected_ip")
        if not bbox:
            return

        x, y, width, height = bbox
        editor = ttk.Combobox(self.tree, values=entry.ips, state="readonly")
        editor.set(entry.selected_ip)
        editor.place(x=x, y=y, width=width, height=height)
        editor.focus_set()
        self.ip_editor = editor
        self.ip_editor_domain = domain

        def apply_selection() -> None:
            selected_ip = editor.get()
            if selected_ip in entry.ips:
                entry.selected_ip = selected_ip
                self.refresh_table()
                self.tree.selection_set(domain)

        editor.bind("<<ComboboxSelected>>", lambda _event: apply_selection())
        editor.bind("<Return>", lambda _event: apply_selection())
        editor.bind("<Escape>", lambda _event: self.close_ip_editor())
        editor.bind("<FocusOut>", lambda _event: self.close_ip_editor(apply=True))
        editor.event_generate("<Button-1>")

    def close_ip_editor(self, apply: bool = False) -> None:
        if self.ip_editor is not None:
            if apply and self.ip_editor_domain is not None:
                selected_ip = self.ip_editor.get()
                entry = self.entries.get(self.ip_editor_domain)
                if entry and selected_ip in entry.ips:
                    entry.selected_ip = selected_ip
            self.ip_editor.destroy()
            self.ip_editor = None
            self.ip_editor_domain = None

    def selected_domain(self) -> str | None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning(APP_NAME, "Сначала выберите домен", parent=self)
            return None
        return sel[0]

    def reload(self) -> None:
        if not messagebox.askyesno(
            APP_NAME,
            "Обновить данные из состояния и файла hosts? Несохраненные изменения будут потеряны.",
            parent=self,
        ):
            return
        logger.info("reload_requested")
        self.load_initial_data()
        self.refresh_table()

    def add_entry(self) -> None:
        dlg = EntryDialog(self)
        if not dlg.result:
            return
        entry = dlg.result
        if entry.domain in self.entries:
            added = self.entries[entry.domain].add_ips(entry.ips)
            messagebox.showinfo(
                APP_NAME,
                f"Домен уже существует. Добавленные IP: {', '.join(added) if added else 'нет'}",
                parent=self,
            )
        else:
            self.entries[entry.domain] = entry
        logger.info("entry_added", domain=entry.domain, ips_count=len(entry.ips), entries_count=len(self.entries))
        self.refresh_table()

    def edit_entry(self) -> None:
        domain = self.selected_domain()
        if not domain:
            return
        old = self.entries[domain]
        dlg = EntryDialog(self, old)
        if not dlg.result:
            return
        new = dlg.result
        if new.domain != domain and new.domain in self.entries:
            messagebox.showerror(APP_NAME, "Запись с таким доменом уже существует", parent=self)
            return
        # Preserve selected IP when possible.
        if old.selected_ip in new.ips:
            new.selected_ip = old.selected_ip
        del self.entries[domain]
        self.entries[new.domain] = new
        logger.info(
            "entry_edited", old_domain=domain, new_domain=new.domain, ips_count=len(new.ips), enabled=new.enabled
        )
        self.refresh_table()
        self.tree.selection_set(new.domain)

    def delete_entry(self) -> None:
        domain = self.selected_domain()
        if not domain:
            return
        if messagebox.askyesno(APP_NAME, f"Удалить {domain}?", parent=self):
            del self.entries[domain]
            logger.info("entry_deleted", domain=domain, entries_count=len(self.entries))
            self.refresh_table()

    def toggle_entry(self) -> None:
        domain = self.selected_domain()
        if not domain:
            return
        self.entries[domain].enabled = not self.entries[domain].enabled
        logger.info("entry_toggled", domain=domain, enabled=self.entries[domain].enabled)
        self.refresh_table()
        self.tree.selection_set(domain)

    def select_ip(self) -> None:
        domain = self.selected_domain()
        if not domain:
            return
        entry = self.entries[domain]
        dlg = SelectIpDialog(self, entry)
        if dlg.result:
            entry.selected_ip = dlg.result
            logger.info("entry_selected_ip_changed", domain=domain, selected_ip=entry.selected_ip)
            self.refresh_table()
            self.tree.selection_set(domain)

    def import_entries(self) -> None:
        import_dialog = ImportDialog(self)
        if not import_dialog.result:
            return

        mode, incoming = import_dialog.result
        logger.info("import_requested", mode=mode, incoming_count=len(incoming))
        try:
            if mode == "merge":
                new_entries, diff = merge_entries(self.entries, incoming)
                title = "Предпросмотр импорта: обновление"
            elif mode == "replace":
                new_entries, diff = replace_entries(self.entries, incoming)
                title = "Предпросмотр импорта: замена"
            else:
                raise ValueError(f"Неизвестный режим импорта: {mode}")
        except Exception as exc:
            logger.warning("import_failed", mode=mode, error=str(exc))
            messagebox.showerror(APP_NAME, f"Импорт не удался:\n{exc}", parent=self)
            return

        preview_dialog = ChangePreview(self, title, diff, confirm_text="Применить импорт")
        if preview_dialog.result:
            self.entries = new_entries
            self.refresh_table()
            save_state(self.entries)
            logger.info("import_applied", mode=mode, entries_count=len(self.entries))
            messagebox.showinfo(
                APP_NAME,
                "Импорт применен к локальному состоянию. Используйте сохранение, чтобы записать HMG-блок в hosts.",
                parent=self,
            )

    def build_preview_texts(self) -> tuple[str, str]:
        self.close_ip_editor(apply=True)
        original = read_hosts_file(self.hosts_file)
        content = build_preserve_hosts_text(original, self.entries)
        diff_stats = summarize_diff_rows(build_side_by_side_diff(original, content))
        logger.info(
            "hosts_preview_built",
            hosts_file=str(self.hosts_file),
            before_bytes=len(original.encode("utf-8")),
            after_bytes=len(content.encode("utf-8")),
            entries_count=len(self.entries),
            diff_added=diff_stats["added"],
            diff_removed=diff_stats["removed"],
            diff_changed=diff_stats["changed"],
        )
        return original, content

    def preview_hosts(self) -> None:
        try:
            original, content = self.build_preview_texts()
            HostsDiffPreview(self, original, content)
        except Exception as exc:
            logger.exception("hosts_preview_failed")
            messagebox.showerror(APP_NAME, f"Не удалось построить предпросмотр:\n{exc}", parent=self)

    def save_managed_block(self) -> None:
        try:
            original, content = self.build_preview_texts()
            dlg = HostsDiffPreview(self, original, content, confirm_text="Сохранить")
            if dlg.result:
                logger.info("hosts_save_confirmed", entries_count=len(self.entries))
                self.write_content(content)
        except Exception as exc:
            logger.exception("hosts_save_failed")
            messagebox.showerror(APP_NAME, f"Сохранение не удалось:\n{exc}", parent=self)

    def write_content(self, content: str) -> None:
        try:
            backup = write_hosts(self.hosts_file, content)
        except PermissionError:
            logger.info("hosts_write_permission_denied", hosts_file=str(self.hosts_file))
            backup = self.write_content_elevated(content)
        save_state(self.entries)
        logger.info("hosts_saved", hosts_file=str(self.hosts_file), backup=str(backup), entries_count=len(self.entries))
        messagebox.showinfo(APP_NAME, f"HMG-блок сохранен. Резервная копия создана:\n{backup}", parent=self)

    def write_content_elevated(self, content: str) -> Path:
        message = (
            "Для записи в файл hosts нужны права администратора.\n\nЗапросить права сейчас и продолжить сохранение?"
        )
        if not messagebox.askyesno(APP_NAME, message, icon="warning", parent=self):
            logger.info("elevated_write_cancelled", hosts_file=str(self.hosts_file))
            raise PermissionError("Пользователь отменил запрос прав администратора")
        try:
            logger.info("elevated_write_requested", hosts_file=str(self.hosts_file))
            return write_hosts_elevated(self.hosts_file, content)
        except ElevatedWriteError as exc:
            logger.warning("elevated_write_failed", hosts_file=str(self.hosts_file), error=str(exc))
            raise PermissionError(f"Не удалось получить права администратора: {exc}") from exc

    def open_state_folder(self) -> None:
        folder = state_path().parent
        folder.mkdir(parents=True, exist_ok=True)
        try:
            if platform.system().lower().startswith("win"):
                os.startfile(folder)  # type: ignore[attr-defined]
            elif platform.system().lower() == "darwin":
                os.system(f"open {json.dumps(str(folder))}")
            else:
                os.system(f"xdg-open {json.dumps(str(folder))}")
        except Exception:
            messagebox.showinfo(APP_NAME, f"Папка состояния:\n{folder}", parent=self)


def main() -> int:
    log_path = state_path().parent / "hmg.log"
    configure_logging(log_path)
    logger.info("app_starting", log_path=str(log_path))
    app = HostsApp()
    app.mainloop()
    logger.info("app_stopped")
    return 0
