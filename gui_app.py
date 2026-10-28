#!/usr/bin/env python3
"""GUI Application module for Bot Notify Desktop."""

from __future__ import annotations

import json
import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from utilites import extract_entities
from web_open import open_url

try:
    import winsound
except ImportError:
    winsound = None


def write_json(path: Path, payload: Any) -> None:
    """Write JSON atomically using temp file."""
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)


class BotNotifyDesktopApp:
    """Main GUI application window."""

    BG = "#F4EFE7"
    PANEL = "#FFFDF8"
    ACCENT = "#1E4D4F"
    TEXT = "#1B1B1B"
    MUTED = "#5E5A55"

    def __init__(
        self,
        config: Any,
        store: Any,
        vk_notifier: Any,
        telegram_notifier: Any | None = None,
        glpi: Any | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.vk_notifier = vk_notifier
        self.telegram_notifier = telegram_notifier
        self.glpi = glpi
        self.glpi_window: Any | None = None
        self.event_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.monitor_stop_event = threading.Event()
        self.monitor: Any | None = None
        self._monitor_thread: threading.Thread | None = None
        self.filtered_records: list[Any] = list(self.store.history)
        self.disable_popup_toasts = self._load_popup_setting()

        self.root = tk.Tk()
        self.root.title(config.program_name)
        self.root.geometry("1280x780")
        self.root.minsize(1080, 680)
        self.root.configure(bg=self.BG)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.status_var = tk.StringVar(value="Готов к работе")
        self.summary_var = tk.StringVar(value=self._build_summary())
        self.search_var = tk.StringVar(value="")

        self._configure_theme()
        self._build_ui()
        self._load_history_into_tree()

        self.search_var.trace_add("write", lambda *_: self.apply_filter())
        self.root.after(250, self.drain_events)
        self.start_monitor()

    def _configure_theme(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("App.TFrame", background=self.BG)
        style.configure("Card.TFrame", background=self.PANEL)
        style.configure(
            "Accent.TButton",
            background=self.ACCENT,
            foreground="white",
            borderwidth=0,
            padding=(14, 8),
        )
        style.map("Accent.TButton", background=[("active", "#163A3C")])
        style.configure("Treeview", rowheight=34, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI Semibold", 10))

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, style="App.TFrame", padding=18)
        container.pack(fill="both", expand=True)

        header = ttk.Frame(container, style="Card.TFrame", padding=18)
        header.pack(fill="x", pady=(0, 14))

        title = tk.Label(
            header,
            text="Bot Notify Desktop",
            font=("Bahnschrift SemiBold", 24),
            bg=self.PANEL,
            fg=self.ACCENT,
        )
        title.grid(row=0, column=0, sticky="w")

        subtitle = tk.Label(
            header,
            text="Письма превращаются в локальные уведомления на ПК",
            font=("Segoe UI", 10),
            bg=self.PANEL,
            fg=self.MUTED,
        )
        subtitle.grid(row=1, column=0, sticky="w", pady=(4, 0))

        actions = ttk.Frame(header, style="Card.TFrame")
        actions.grid(row=0, column=1, rowspan=2, sticky="e")
        ttk.Button(
            actions, text="Старт", style="Accent.TButton", command=self.start_monitor
        ).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Стоп", command=self.stop_monitor).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(
            actions, text="Тест", command=self.create_test_notification
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            actions, text="Настройки", command=self.open_settings_window
        ).pack(side="left", padx=(0, 8))
        self.glpi_btn = ttk.Button(
            actions, text="Заявки GLPI", command=self.open_glpi_window
        )
        self.glpi_btn.pack(side="left", padx=(0, 8))
        if self.glpi is not None and getattr(self.glpi, "started", False):
            self.glpi_btn.state(["!disabled"])
        ttk.Button(
            actions, text="Открыть ссылку", command=self.open_first_link
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            actions, text="Копировать", command=self.copy_selected
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            actions, text="Очистить журнал", command=self.clear_history
        ).pack(side="left")

        header.columnconfigure(0, weight=1)

        info_bar = ttk.Frame(container, style="Card.TFrame", padding=14)
        info_bar.pack(fill="x", pady=(0, 14))

        tk.Label(
            info_bar,
            textvariable=self.status_var,
            font=("Segoe UI Semibold", 10),
            bg=self.PANEL,
            fg=self.TEXT,
        ).pack(anchor="w")

        tk.Label(
            info_bar,
            textvariable=self.summary_var,
            font=("Segoe UI", 10),
            bg=self.PANEL,
            fg=self.MUTED,
        ).pack(anchor="w", pady=(4, 0))

        search_card = ttk.Frame(container, style="Card.TFrame", padding=14)
        search_card.pack(fill="x", pady=(0, 14))

        tk.Label(
            search_card,
            text="Поиск по теме, отправителю или тексту",
            font=("Segoe UI Semibold", 10),
            bg=self.PANEL,
            fg=self.TEXT,
        ).pack(anchor="w")
        search_entry = tk.Entry(
            search_card,
            textvariable=self.search_var,
            font=("Segoe UI", 11),
            relief="flat",
            bg="white",
            fg=self.TEXT,
            insertbackground=self.TEXT,
        )
        search_entry.pack(fill="x", pady=(8, 0), ipady=8)

        content = ttk.Panedwindow(container, orient="horizontal")
        content.pack(fill="both", expand=True)

        left_card = ttk.Frame(content, style="Card.TFrame", padding=10)
        right_card = ttk.Frame(content, style="Card.TFrame", padding=14)
        content.add(left_card, weight=3)
        content.add(right_card, weight=4)

        columns = ("created_at", "sender", "subject")
        self.tree = ttk.Treeview(
            left_card, columns=columns, show="headings", selectmode="browse"
        )
        self.tree.heading("created_at", text="Получено")
        self.tree.heading("sender", text="Отправитель")
        self.tree.heading("subject", text="Тема")
        self.tree.column("created_at", width=150, anchor="w")
        self.tree.column("sender", width=220, anchor="w")
        self.tree.column("subject", width=360, anchor="w")
        self.tree.pack(fill="both", expand=True, side="left")
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)

        scrollbar = ttk.Scrollbar(
            left_card, orient="vertical", command=self.tree.yview
        )
        scrollbar.pack(fill="y", side="right")
        self.tree.configure(yscrollcommand=scrollbar.set)

        detail_title = tk.Label(
            right_card,
            text="Детали письма",
            font=("Bahnschrift SemiBold", 18),
            bg=self.PANEL,
            fg=self.ACCENT,
        )
        detail_title.pack(anchor="w")

        self.meta_label = tk.Label(
            right_card,
            text="Выбери уведомление слева",
            font=("Segoe UI", 10),
            bg=self.PANEL,
            fg=self.MUTED,
            justify="left",
        )
        self.meta_label.pack(anchor="w", pady=(4, 10))

        self.detail_text = tk.Text(
            right_card,
            wrap="word",
            font=("Consolas", 10),
            bg="#FFFEFC",
            fg=self.TEXT,
            relief="flat",
            padx=14,
            pady=14,
            insertbackground=self.TEXT,
        )
        self.detail_text.pack(fill="both", expand=True)
        self.detail_text.tag_configure(
            "heading", font=("Segoe UI Semibold", 11), foreground=self.ACCENT
        )
        self.detail_text.tag_configure("body", spacing1=2, spacing3=4)
        self.detail_text.configure(state="disabled")

    def _build_summary(self) -> str:
        vk_state = "включен" if self.vk_notifier.enabled else "выключен"
        recipients_count = len(self.vk_notifier.get_peer_ids())
        proxy_state = self.config.imap_http_proxy or "direct"
        popup_state = "выкл" if self.disable_popup_toasts else "вкл"
        return (
            f"Ящик: {self.config.imap_username} | "
            f"Сервер: {self.config.imap_server}:{self.config.imap_port} | "
            f"Интервал: {self.config.poll_seconds} сек | "
            f"IMAP proxy: {proxy_state} | "
            f"VK: {vk_state} | "
            f"VK получателей: {recipients_count} | "
            f"Popup: {popup_state} | "
            f"Записей: {len(self.store.history)}"
        )

    def _load_popup_setting(self) -> bool:
        settings_path = Path("data/app_settings.json")
        if not settings_path.exists():
            return False
        try:
            payload = json.loads(settings_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return False
            return bool(payload.get("disable_popup_toasts", False))
        except (TypeError, ValueError, json.JSONDecodeError):
            return False

    def _save_popup_setting(self) -> None:
        settings_path = Path("data/app_settings.json")
        write_json(settings_path, {"disable_popup_toasts": self.disable_popup_toasts})

    def _load_history_into_tree(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)

        seen_ids: set[str] = set()
        for record in self.filtered_records:
            if record.record_id not in seen_ids:
                self.tree.insert(
                    "",
                    "end",
                    iid=record.record_id,
                    values=(record.created_at, record.sender, record.subject),
                )
                seen_ids.add(record.record_id)

        if self.filtered_records:
            first = self.filtered_records[0]
            self.tree.selection_set(first.record_id)
            self.tree.focus(first.record_id)
            self.render_detail(first)

    def apply_filter(self) -> None:
        query = self.search_var.get().strip().lower()
        if not query:
            self.filtered_records = list(self.store.history)
        else:
            self.filtered_records = [
                record
                for record in self.store.history
                if query in record.subject.lower()
                or query in record.sender.lower()
                or query in record.body.lower()
            ]
        self._load_history_into_tree()

    def start_monitor(self) -> None:
        if self._monitor_thread and self._monitor_thread.is_alive():
            self.status_var.set("Монитор уже запущен")
            return
        
        from imap_monitor import ImapMonitor
        
        self.monitor_stop_event = threading.Event()
        self.monitor = ImapMonitor(
            config=self.config,
            store=self.store,
            event_queue=self.event_queue,
        )
        thread = threading.Thread(
            target=self.monitor.run,
            name="imap-monitor",
            daemon=True,
        )
        thread.start()
        self._monitor_thread = thread
        self.status_var.set("Монитор запущен")
    
    def stop_monitor(self) -> None:
        if not self.monitor:
            self.status_var.set("Монитор ещё не запускался")
            return
        if hasattr(self.monitor, "stop"):
            self.monitor.stop()
        self.status_var.set("Останавливаю монитор")


    def drain_events(self) -> None:
        try:
            while True:
                event_type, payload = self.event_queue.get_nowait()
                if event_type == "status":
                    self.status_var.set(str(payload))
                elif event_type == "notification":
                    self.handle_new_notification(payload)
        except queue.Empty:
            pass
        finally:
            self.summary_var.set(self._build_summary())
            self.root.after(250, self.drain_events)

    def handle_new_notification(self, record: Any) -> None:
        self.store.append_history(record)
        self.apply_filter()
        self.tree.selection_set(record.record_id)
        self.tree.focus(record.record_id)
        self.tree.see(record.record_id)
        self.render_detail(record)
        if self._should_show_popup_toast():
            self.show_toast(record)
        self.play_alert()
        self.vk_notifier.send_async(record)

    def play_alert(self) -> None:
        # Determine sound file path (support PyInstaller frozen exe)
        if getattr(sys, "frozen", False):
            base_dir = Path(sys.executable).resolve().parent
        else:
            base_dir = Path()
        sound_path = base_dir / "sound.wav"
        if winsound and sound_path.exists():
            try:
                winsound.PlaySound(
                    str(sound_path),
                    winsound.SND_FILENAME | winsound.SND_ASYNC,
                )
                return
            except RuntimeError:
                pass
        if winsound:
            winsound.MessageBeep()

    def _should_show_popup_toast(self) -> bool:
        if self.disable_popup_toasts:
            return False
        if self._is_windows_notification_suppressed():
            return False
        return not self._is_external_fullscreen_active()

    def _is_windows_notification_suppressed(self) -> bool:
        if os.name != "nt":
            return False
        try:
            shell32 = ctypes.windll.shell32
            state = ctypes.c_int(0)
            result = shell32.SHQueryUserNotificationState(
                ctypes.byref(state)
            )
            if result != 0:
                return False
            return state.value in {2, 3, 4}
        except Exception:
            return False

    def _is_external_fullscreen_active(self) -> bool:
        if os.name != "nt":
            return False
        try:
            user32 = ctypes.windll.user32
            foreground_window = user32.GetForegroundWindow()
            if not foreground_window:
                return False

            foreground_pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(
                foreground_window, ctypes.byref(foreground_pid)
            )
            if foreground_pid.value == os.getpid():
                return False

            if user32.IsIconic(foreground_window):
                return False

            class RECT(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]

            class MONITORINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("rcMonitor", RECT),
                    ("rcWork", RECT),
                    ("dwFlags", wintypes.DWORD),
                ]

            rect = RECT()
            if not user32.GetWindowRect(
                foreground_window, ctypes.byref(rect)
            ):
                return False

            monitor = user32.MonitorFromWindow(
                foreground_window, 2
            )
            if not monitor:
                return False

            monitor_info = MONITORINFO()
            monitor_info.cbSize = ctypes.sizeof(MONITORINFO)
            if not user32.GetMonitorInfoW(
                monitor, ctypes.byref(monitor_info)
            ):
                return False

            tolerance = 2
            return (
                abs(rect.left - monitor_info.rcMonitor.left) <= tolerance
                and abs(rect.top - monitor_info.rcMonitor.top) <= tolerance
                and abs(rect.right - monitor_info.rcMonitor.right)
                <= tolerance
                and abs(rect.bottom - monitor_info.rcMonitor.bottom)
                <= tolerance
            )
        except Exception:
            return False

    def show_toast(self, record: Any) -> None:
        toast = tk.Toplevel(self.root)
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        toast.configure(bg=self.ACCENT)

        width = 420
        height = 180
        index = len(self.toast_windows)
        x = max(
            20,
            self.root.winfo_screenwidth()
            - width
            - 24,
        )
        y = max(
            20,
            self.root.winfo_screenheight()
            - height
            - 80
            - index * (height + 12),
        )
        toast.geometry(f"{width}x{height}+{x}+{y}")

        frame = tk.Frame(toast, bg=self.ACCENT, padx=16, pady=16)
        frame.pack(fill="both", expand=True)

        tk.Label(
            frame,
            text=record.subject,
            font=("Bahnschrift SemiBold", 15),
            bg=self.ACCENT,
            fg="white",
            wraplength=360,
            justify="left",
        ).pack(anchor="w")

        meta = f"{record.sender} | {record.created_at}"
        tk.Label(
            frame,
            text=meta,
            font=("Segoe UI", 9),
            bg=self.ACCENT,
            fg="#D8F0ED",
            wraplength=360,
            justify="left",
        ).pack(anchor="w", pady=(6, 10))

        details: list[str] = []
        # Helpdesk ticket fields
        if getattr(record, "ticket_id", None):
            details.append(f"Заявка №{record.ticket_id}: {record.ticket_title or ''}")
        if getattr(record, "ticket_authors", None):
            author = record.ticket_authors
            if getattr(record, "ticket_user_email", None):
                author += f" ({record.ticket_user_email})"
            details.append(f"Автор: {author}")
        if getattr(record, "ticket_priority", None):
            details.append(f"Приоритет: {record.ticket_priority}")
        if record.contracts:
            details.append(
                "Контракт: " + ", ".join(record.contracts[:3])
            )
        if record.inns:
            details.append("ИНН: " + ", ".join(record.inns[:3]))
        if record.ticket_ids:
            details.append(
                "TicketID: " + ", ".join(record.ticket_ids[:3])
            )
        if not details:
            details.append(record.preview)
        if record.contracts:
            details.append(
                "Контракт: " + ", ".join(record.contracts[:3])
            )
        if record.inns:
            details.append("ИНН: " + ", ".join(record.inns[:3]))
        if record.ticket_ids:
            details.append(
                "TicketID: " + ", ".join(record.ticket_ids[:3])
            )
        if not details:
            details.append(record.preview)

        tk.Label(
            frame,
            text="\n".join(details),
            font=("Segoe UI", 10),
            bg=self.ACCENT,
            fg="white",
            wraplength=360,
            justify="left",
        ).pack(anchor="w")

        def focus_record(_: Any = None) -> None:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
            self.tree.selection_set(record.record_id)
            self.tree.focus(record.record_id)
            self.render_detail(record)
            if toast.winfo_exists():
                toast.destroy()

        frame.bind("<Button-1>", focus_record)
        toast.bind("<Button-1>", focus_record)

        self.toast_windows.append(toast)

        def close_toast() -> None:
            if toast in self.toast_windows:
                self.toast_windows.remove(toast)
            if toast.winfo_exists():
                toast.destroy()

        toast.after(12000, close_toast)

    def on_tree_select(self, _: Any) -> None:
        record = self.get_selected_record()
        if record:
            self.render_detail(record)

    def get_selected_record(self) -> Any | None:
        selected = self.tree.selection()
        if not selected:
            return None
        record_id = selected[0]
        return next(
            (
                item
                for item in self.store.history
                if item.record_id == record_id
            ),
            None,
        )

    def render_detail(self, record: Any) -> None:
        meta_lines = [
            f"Отправитель: {record.sender}",
            f"Тема: {record.subject}",
            f"Дата письма: {record.received_at}",
            f"Получено: {record.created_at}",
        ]
        self.meta_label.configure(text="\n".join(meta_lines))

        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")

        self._insert_detail_block("Кратко", record.preview)
        if record.contracts:
            self._insert_detail_block(
                "Контракты", ", ".join(record.contracts)
            )
        if record.inns:
            self._insert_detail_block("ИНН", ", ".join(record.inns))
        if record.ticket_ids:
            self._insert_detail_block(
                "TicketID", ", ".join(record.ticket_ids)
            )
        # Helpdesk ticket fields
        if getattr(record, "ticket_id", None):
            self._insert_detail_block("Заявка №", record.ticket_id)
        if getattr(record, "ticket_title", None):
            self._insert_detail_block("Тема заявки", record.ticket_title)
        if getattr(record, "ticket_authors", None):
            author_text = record.ticket_authors
            if getattr(record, "ticket_user_email", None):
                author_text += f" ({record.ticket_user_email})"
            self._insert_detail_block("Автор", author_text)
        if getattr(record, "ticket_creationdate", None):
            self._insert_detail_block("Дата создания", record.ticket_creationdate)
        if getattr(record, "ticket_category", None):
            self._insert_detail_block("Категория", record.ticket_category)
        if getattr(record, "ticket_assigntogroups", None):
            self._insert_detail_block("Группа", record.ticket_assigntogroups)
        if getattr(record, "ticket_priority", None):
            self._insert_detail_block("Приоритет", record.ticket_priority)
        if getattr(record, "ticket_url", None):
            self._insert_detail_block("Ссылка на заявку", record.ticket_url)
        if record.links:
            self._insert_detail_block("Ссылки", "\n".join(record.links))
        self._insert_detail_block("Текст", record.body)


        self.detail_text.configure(state="disabled")

    def _insert_detail_block(self, heading: str, text: str) -> None:
        self.detail_text.insert("end", f"{heading}\n", "heading")
        self.detail_text.insert("end", f"{text.strip()}\n\n", "body")

    def copy_selected(self) -> None:
        record = self.get_selected_record()
        if not record:
            messagebox.showinfo("Копирование", "Выберите уведомление.")
            return

        payload = (
            f"Тема: {record.subject}\n"
            f"Отправитель: {record.sender}\n"
            f"Дата: {record.received_at}\n\n"
            f"{record.body}"
        )
        self.root.clipboard_clear()
        self.root.clipboard_append(payload)
        self.status_var.set("Текст скопирован")

    def open_first_link(self) -> None:
        record = self.get_selected_record()
        if not record or not record.links:
            messagebox.showinfo("Ссылки", "Нет ссылок.")
            return

        ok, info = open_url(record.links[0])
        if ok:
            self.status_var.set("Открыл ссылку")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(record.links[0])
        self.status_var.set(f"Браузер не открылся: {info} — ссылку копировал")

    def clear_history(self) -> None:
        if not messagebox.askyesno(
            "Очистить журнал", "Удалить историю уведомлений?"
        ):
            return

        self.store.clear_history()
        self.apply_filter()
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        self.detail_text.configure(state="disabled")
        self.meta_label.configure(text="Журнал очищен")
        self.status_var.set("История очищена")

    def open_settings_window(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("Настройки")
        window.geometry("520x420")
        window.minsize(480, 360)
        window.configure(bg=self.PANEL)
        window.transient(self.root)
        window.grab_set()

        description = (
            "Добавьте peer_id VK получателей.\n"
            "Формат: 123456, id123456 или https://vk.com/id123456"
        )
        tk.Label(
            window,
            text=description,
            font=("Segoe UI", 10),
            bg=self.PANEL,
            fg=self.MUTED,
            justify="left",
            wraplength=470,
        ).pack(anchor="w", padx=16, pady=(14, 8))

        popup_disabled_var = tk.BooleanVar(
            value=self.disable_popup_toasts
        )
        tk.Checkbutton(
            window,
            text="Отключить pop-up уведомления",
            variable=popup_disabled_var,
            bg=self.PANEL,
            fg=self.TEXT,
            activebackground=self.PANEL,
            activeforeground=self.TEXT,
            selectcolor="white",
            font=("Segoe UI", 10),
            wraplength=470,
            justify="left",
        ).pack(anchor="w", padx=16, pady=(0, 8))

        input_row = ttk.Frame(window, style="Card.TFrame")
        input_row.pack(fill="x", padx=16, pady=(0, 8))

        peer_var = tk.StringVar()
        peer_entry = tk.Entry(
            input_row,
            textvariable=peer_var,
            font=("Segoe UI", 11),
            relief="flat",
            bg="white",
            fg=self.TEXT,
            insertbackground=self.TEXT,
        )
        peer_entry.pack(side="left", fill="x", expand=True, ipady=6)

        list_frame = ttk.Frame(window, style="Card.TFrame")
        list_frame.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        listbox = tk.Listbox(
            list_frame,
            font=("Consolas", 11),
            activestyle="none",
            selectmode="extended",
            bg="white",
            fg=self.TEXT,
            relief="flat",
        )
        listbox.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(
            list_frame, orient="vertical", command=listbox.yview
        )
        scrollbar.pack(side="right", fill="y")
        listbox.configure(yscrollcommand=scrollbar.set)

        for peer_id in self.vk_notifier.get_peer_ids():
            listbox.insert("end", str(peer_id))

        def add_peer() -> None:
            raw_value = peer_var.get().strip()
            peer_id = self._parse_vk_peer_input(raw_value)
            if peer_id is None:
                messagebox.showwarning(
                    "Настройки VK",
                    "Некорректный peer_id",
                    parent=window,
                )
                return
            existing = {
                int(item)
                for item in listbox.get(0, "end")
            }
            if peer_id in existing:
                self.status_var.set(
                    f"VK получатель {peer_id} уже добавлен"
                )
                peer_var.set("")
                return
            listbox.insert("end", str(peer_id))
            peer_var.set("")
            self.status_var.set(
                f"Добавлен VK получатель {peer_id}"
            )

        def remove_selected() -> None:
            selected = list(listbox.curselection())
            if not selected:
                return
            for index in reversed(selected):
                listbox.delete(index)

        def clear_all() -> None:
            if not messagebox.askyesno(
                "Настройки VK", "Удалить всех получателей?", parent=window
            ):
                return
            listbox.delete(0, "end")

        buttons_row = ttk.Frame(window, style="Card.TFrame")
        buttons_row.pack(fill="x", padx=16, pady=(0, 8))

        ttk.Button(
            buttons_row, text="Добавить", command=add_peer
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            buttons_row,
            text="Удалить",
            command=remove_selected,
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            buttons_row, text="Очистить", command=clear_all
        ).pack(side="left")

        footer = ttk.Frame(window, style="Card.TFrame")
        footer.pack(fill="x", padx=16, pady=(4, 14))

        def save_and_close() -> None:
            peer_ids = sorted(
                {
                    int(value)
                    for value in listbox.get(0, "end")
                }
            )
            self.vk_notifier.set_peer_ids(peer_ids)
            self.disable_popup_toasts = popup_disabled_var.get()
            self._save_popup_setting()
            self.status_var.set(
                f"Сохранено VK получателей: {len(peer_ids)}"
            )
            self.summary_var.set(self._build_summary())
            window.destroy()

        ttk.Button(
            footer,
            text="Сохранить",
            style="Accent.TButton",
            command=save_and_close,
        ).pack(side="right")
        ttk.Button(
            footer, text="Отмена", command=window.destroy
        ).pack(side="right", padx=(0, 8))

        peer_entry.bind("<Return>", lambda _: add_peer())
        window.bind("<Escape>", lambda _: window.destroy())
        peer_entry.focus_set()

    @staticmethod
    def _parse_vk_peer_input(raw_value: str) -> int | None:
        if not raw_value:
            return None

        value = raw_value.strip()
        if not value:
            return None

        value = re.sub(
            r"^https?://(m\.)?vk\.com/", "", value, flags=re.IGNORECASE
        ).strip("/")
        if value.startswith("id") and value[2:].isdigit():
            return int(value[2:])

        if value.lstrip("-").isdigit():
            peer_id = int(value)
            return peer_id if peer_id != 0 else None

        return None

    def create_test_notification(self) -> None:
        from imap_monitor import NotificationRecord
        
        body = (
            "Тестовое письмо.\n"
            "Контракт ABCD 1234567.\n"
            "ИНН 7701234567.\n"
            "Ссылка https://example.com/ticket?TicketID=42."
        )
        entities = extract_entities(body)
        
        record = NotificationRecord(
            record_id=f"test-{int(time.time() * 1000)}",
            uid=0,
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            received_at="Тестовый режим",
            sender="Bot Notify Desktop",
            subject="Проверка уведомлений",
            preview=body[:180],
            body=body,
            links=entities["urls"],
            contracts=entities["contracts"],
            inns=entities["inns"],
            ticket_ids=entities["ticket_ids"],
            # Helpdesk ticket fields
            ticket_id="12345",
            ticket_title="Тестовая заявка на проверку",
            ticket_url="https://helpdesk.example.com/ticket/12345",
            ticket_authors="Иванов Иван",
            ticket_user_email="ivanov@example.com",
            ticket_creationdate="2025-04-06 15:00",
            ticket_category="Техническая поддержка",
            ticket_assigntogroups="Отдел IT",
            ticket_priority="Высокий",
            ticket_content="Текст тестовой заявки.",
            is_helpdesk=True,
        )
        
        self.handle_new_notification(record)
        self.status_var.set("Тестовое уведомление создано")

    def open_glpi_window(self) -> None:
        """Окно новых заявок GLPI и отправки в чат Bitrix24."""
        if self.glpi is None:
            self.status_var.set("GLPI не настроен (нет GLPI_URL / GLPI_USER в .env)")
            return
        if self.glpi_window is not None and self.glpi_window.winfo_exists():
            self.glpi_window.deiconify()
            self.glpi_window.lift()
            return
        from glpi_panel import GlpiWindow
        self.glpi_window = GlpiWindow(self.root, self.glpi)
        if not self.glpi.started:
            self.glpi_window.status_var.set("GLPI: поднимаем связь…")
            import threading
            threading.Thread(target=self._start_glpi, daemon=True).start()

    def _start_glpi(self) -> None:
        ok = self.glpi.start()
        info = self.glpi.status() if ok else "GLPI: вход не удался"
        self.root.after(0, lambda: self.status_var.set(info))

    def on_close(self) -> None:
        self.stop_monitor()
        if self.glpi is not None:
            try:
                self.glpi.stop()
            except Exception:
                pass
        self.root.after(150, self.root.destroy)

    def run(self) -> None:
        self.root.mainloop()
