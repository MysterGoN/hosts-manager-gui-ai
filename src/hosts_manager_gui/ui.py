from __future__ import annotations

import json
import os
import platform
import re
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from hosts_manager_gui.core import (
    APP_NAME,
    EntryDiff,
    HostEntry,
    build_overwrite_hosts_text,
    build_preserve_hosts_text,
    hosts_path,
    load_state,
    merge_entries,
    parse_csv_file,
    parse_hosts_text,
    read_hosts_file,
    replace_entries,
    save_state,
    state_path,
    validate_domain,
    validate_ip,
    write_hosts,
)


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

        self.load_initial_data()
        self.create_widgets()
        self.refresh_table()

    def load_initial_data(self) -> None:
        state_entries = load_state()
        try:
            hosts_entries = parse_hosts_text(read_hosts_file(self.hosts_file))
        except Exception:
            hosts_entries = {}

        # State keeps multi-IP candidates. Hosts file contributes new external records.
        if state_entries:
            self.entries = state_entries
            for domain, entry in hosts_entries.items():
                if domain not in self.entries:
                    self.entries[domain] = entry
        else:
            self.entries = hosts_entries

    def create_widgets(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill=tk.BOTH, expand=True)

        top = ttk.Frame(root)
        top.pack(fill=tk.X)
        ttk.Label(top, text=f"Файл hosts: {self.hosts_file}").pack(side=tk.LEFT)
        ttk.Button(top, text="Обновить", command=self.reload).pack(side=tk.RIGHT)

        columns = ("enabled", "selected_ip", "ips")
        self.tree = ttk.Treeview(root, columns=columns, show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Домен")
        self.tree.heading("enabled", text="Включено")
        self.tree.heading("selected_ip", text="Выбранный IP")
        self.tree.heading("ips", text="Доступные IP")
        self.tree.column("#0", width=270, minwidth=180)
        self.tree.column("enabled", width=90, anchor="center")
        self.tree.column("selected_ip", width=150)
        self.tree.column("ips", width=420)
        self.tree.pack(fill=tk.BOTH, expand=True, pady=10)

        buttons = ttk.Frame(root)
        buttons.pack(fill=tk.X)

        ttk.Button(buttons, text="Добавить", command=self.add_entry).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Изменить", command=self.edit_entry).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Удалить", command=self.delete_entry).pack(side=tk.LEFT)
        ttk.Separator(buttons, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=8)
        ttk.Button(buttons, text="Вкл / выкл", command=self.toggle_entry).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Выбрать IP", command=self.select_ip).pack(side=tk.LEFT, padx=4)
        ttk.Separator(buttons, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=8)
        ttk.Button(buttons, text="Импорт CSV: объединить", command=lambda: self.import_csv("merge")).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Импорт CSV: заменить", command=lambda: self.import_csv("replace")).pack(
            side=tk.LEFT,
            padx=4,
        )

        save_buttons = ttk.Frame(root)
        save_buttons.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(save_buttons, text="Сохранить: оставить прочие строки", command=self.save_preserve).pack(
            side=tk.LEFT
        )
        ttk.Button(save_buttons, text="Сохранить: перезаписать hosts", command=self.save_overwrite).pack(
            side=tk.LEFT,
            padx=4,
        )
        ttk.Button(save_buttons, text="Открыть папку состояния", command=self.open_state_folder).pack(side=tk.RIGHT)

        hint = (
            "Подсказка: запись в /etc/hosts или Windows hosts обычно требует прав администратора. "
            "Если сохранение не удалось, запустите приложение с sudo/от имени администратора."
        )
        ttk.Label(root, text=hint).pack(fill=tk.X, pady=(8, 0))

    def refresh_table(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for domain, entry in sorted(self.entries.items()):
            self.tree.insert(
                "",
                tk.END,
                iid=domain,
                text=domain,
                values=("да" if entry.enabled else "нет", entry.selected_ip, "; ".join(entry.ips)),
            )

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
        self.refresh_table()
        self.tree.selection_set(new.domain)

    def delete_entry(self) -> None:
        domain = self.selected_domain()
        if not domain:
            return
        if messagebox.askyesno(APP_NAME, f"Удалить {domain}?", parent=self):
            del self.entries[domain]
            self.refresh_table()

    def toggle_entry(self) -> None:
        domain = self.selected_domain()
        if not domain:
            return
        self.entries[domain].enabled = not self.entries[domain].enabled
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
            self.refresh_table()
            self.tree.selection_set(domain)

    def import_csv(self, mode: str) -> None:
        path_str = filedialog.askopenfilename(
            parent=self,
            title="Импорт CSV hosts",
            filetypes=[("CSV-файлы", "*.csv"), ("Все файлы", "*.*")],
        )
        if not path_str:
            return
        try:
            incoming = parse_csv_file(Path(path_str))
            if mode == "merge":
                new_entries, diff = merge_entries(self.entries, incoming)
                title = "Предпросмотр импорта CSV: объединение"
            elif mode == "replace":
                new_entries, diff = replace_entries(self.entries, incoming)
                title = "Предпросмотр импорта CSV: замена"
            else:
                raise ValueError(f"Неизвестный режим импорта: {mode}")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Импорт не удался:\n{exc}", parent=self)
            return

        dlg = ChangePreview(self, title, diff, confirm_text="Применить импорт")
        if dlg.result:
            self.entries = new_entries
            self.refresh_table()
            save_state(self.entries)
            messagebox.showinfo(
                APP_NAME,
                "Импорт применен к локальному состоянию. Используйте сохранение, чтобы записать файл hosts.",
                parent=self,
            )

    def save_preserve(self) -> None:
        try:
            original = read_hosts_file(self.hosts_file)
            content = build_preserve_hosts_text(original, self.entries)
            backup = write_hosts(self.hosts_file, content)
            save_state(self.entries)
            messagebox.showinfo(APP_NAME, f"Сохранено. Резервная копия создана:\n{backup}", parent=self)
        except PermissionError:
            messagebox.showerror(
                APP_NAME,
                "Недостаточно прав. Запустите приложение от администратора/root.\n\n"
                "Пример для macOS/Linux:\n  sudo python -m hosts_manager_gui\n\n"
                "Windows: запустите Command Prompt/PowerShell от имени администратора.",
                parent=self,
            )
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Сохранение не удалось:\n{exc}", parent=self)

    def save_overwrite(self) -> None:
        warning = (
            "Файл hosts будет полностью перезаписан только записями, показанными в приложении.\n\n"
            "Сначала будет создана резервная копия. Продолжить?"
        )
        if not messagebox.askyesno(APP_NAME, warning, icon="warning", parent=self):
            return
        try:
            content = build_overwrite_hosts_text(self.entries)
            backup = write_hosts(self.hosts_file, content)
            save_state(self.entries)
            messagebox.showinfo(
                APP_NAME,
                f"Сохранено в режиме перезаписи. Резервная копия создана:\n{backup}",
                parent=self,
            )
        except PermissionError:
            messagebox.showerror(
                APP_NAME,
                "Недостаточно прав. Запустите приложение от администратора/root.\n\n"
                "Пример для macOS/Linux:\n  sudo python -m hosts_manager_gui\n\n"
                "Windows: запустите Command Prompt/PowerShell от имени администратора.",
                parent=self,
            )
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Сохранение не удалось:\n{exc}", parent=self)

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
    app = HostsApp()
    app.mainloop()
    return 0
