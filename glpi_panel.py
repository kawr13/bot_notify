#!/usr/bin/env python3
"""Окно «Заявки GLPI»: список, превью текста, выбор исполнителя, отправка.

Верхняя панель — режим: глубина опроса (страницы GLPI), показывать ли только
активные, слать ли актуализацию в чат и как часто. Кнопка «Актуализировать
сейчас» прогоняет цикл вручную, без учёта «уже отправляли».

Ручная отправка: выбрать заявку -> «Отправить в чат» (готовит карточку: текст +
ссылка + список «Назначено») -> выбрать исполнителя -> «Отправить боту»
(сообщение в Bitrix24 + исполнитель сохраняется в GLPI).
"""
from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from glpi_monitor import build_message
from web_open import open_url

LOGGER = logging.getLogger("bot_notify.glpi_panel")


class GlpiWindow(tk.Toplevel):
    def __init__(self, master: tk.Misc, bridge: Any) -> None:
        super().__init__(master)
        self.bridge = bridge                      # GlpiBridge: monitor + bitrix + client
        self.cfg = bridge.monitor.cfg
        self.title("Заявки GLPI")
        self.geometry("1240x760")
        self.minsize(1000, 580)
        self.configure(bg="#f4f6fa")
        self.records: dict[int, Any] = {}         # id -> Ticket
        self.order: list[int] = []
        # заявку держим здесь: Listbox по list_var.set() пересобирается и
        # без выделения, а оператор всё это время смотрит на карточку
        self._selected_id: int | None = None
        self.choices: list[dict[str, Any]] = []
        self._busy = False

        self.status_var = tk.StringVar(value="Ожидаем новые заявки…")
        self.assign_var = tk.StringVar()
        self._build_ui()
        self.after(200, self._drain)
        self.protocol("WM_DELETE_WINDOW", self.withdraw)

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        head = tk.Frame(self, bg="#f4f6fa")
        head.pack(fill="x", padx=16, pady=(14, 6))
        tk.Label(head, text="Новые заявки GLPI", font=("Bahnschrift SemiBold", 18),
                 bg="#f4f6fa", fg="#1f3c88").pack(side="left")
        self.count_var = tk.StringVar(value="0 шт.")
        tk.Label(head, textvariable=self.count_var, font=("Segoe UI", 11),
                 bg="#f4f6fa", fg="#6b7280").pack(side="left", padx=10)
        ttk.Button(head, text="Обновить список", command=self._clear_list
                   ).pack(side="right")
        ttk.Button(head, text="Показать текущие", command=self.show_current
                   ).pack(side="right", padx=(0, 8))
        ttk.Button(head, text="Получить новые", command=self._poll_now
                   ).pack(side="right", padx=(0, 8))

        # --- режим работы ---
        mode = tk.Frame(self, bg="#eef2f9", highlightthickness=1,
                        highlightbackground="#d8dee9")
        mode.pack(fill="x", padx=16, pady=(0, 8))

        def label(text: str) -> tk.Label:
            return tk.Label(mode, text=text, bg="#eef2f9", fg="#475569",
                            font=("Segoe UI", 9))

        tk.Label(mode, text="РЕЖИМ", bg="#eef2f9", fg="#1f3c88",
                 font=("Segoe UI", 9, "bold")).pack(side="left", padx=(12, 10),
                                                    pady=6)
        label("Заявок за раз").pack(side="left")
        self.rows_var = tk.IntVar(value=int(self.cfg.row_limit))
        spin = ttk.Spinbox(mode, from_=15, to=300, increment=15, width=4,
                           textvariable=self.rows_var, command=self._apply_mode)
        spin.pack(side="left", padx=(4, 4))
        spin.bind("<Return>", lambda e: self._apply_mode())
        self.pages_hint = label(self._total_pages_hint())
        self.pages_hint.pack(side="left", padx=(0, 14))

        self.active_only_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(mode, text="только активные", variable=self.active_only_var,
                        command=self._apply_mode).pack(side="left", padx=(0, 12))

        label("пауза между заявками").pack(side="left")
        self.pace_var = tk.IntVar(value=int(self.cfg.refresh_pace))
        ttk.Spinbox(mode, from_=0, to=900, increment=15, width=4,
                    textvariable=self.pace_var,
                    command=self._apply_mode).pack(side="left", padx=(4, 3))
        label("сек.").pack(side="left", padx=(0, 12))
        ttk.Button(mode, text="Актуализировать сейчас",
                   command=self.refresh_now).pack(side="left", padx=(0, 10))
        label("куда").pack(side="left")
        self._dialog_opts, _current = self._dialog_choices()
        self.dialog_var = tk.StringVar(value=_current)
        # у ttk.Combobox нет -command: только событие <<ComboboxSelected>>
        box = ttk.Combobox(mode, textvariable=self.dialog_var,
                           values=[text for text, _ in self._dialog_opts],
                           state="readonly", width=20)
        box.bind("<<ComboboxSelected>>", lambda e: self._pick_dialog())
        box.pack(side="left", padx=(4, 10))
        self.team_hint = label(self._team_text())
        self.team_hint.pack(side="left", padx=(6, 0))

        body = tk.PanedWindow(self, orient="horizontal", bg="#f4f6fa", sashwidth=6)
        body.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        left = tk.Frame(body, bg="#ffffff", highlightthickness=1,
                        highlightbackground="#d8dee9")
        body.add(left, minsize=280, width=360)
        tk.Label(left, text="Поступили", bg="#ffffff", fg="#6b7280",
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=10, pady=(8, 2))
        row = tk.Frame(left, bg="#ffffff")
        row.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self.list_var = tk.StringVar()
        self.listbox = tk.Listbox(row, listvariable=self.list_var, activestyle="none",
                                  font=("Segoe UI", 10), selectmode="browse",
                                  relief="flat", highlightthickness=0,
                                  selectbackground="#1f3c88", selectforeground="#fff")
        scroll = ttk.Scrollbar(row, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        right = tk.Frame(body, bg="#ffffff", highlightthickness=1,
                         highlightbackground="#d8dee9")
        body.add(right, minsize=420)
        bar = tk.Frame(right, bg="#ffffff")
        bar.pack(fill="x", padx=12, pady=(10, 4))
        self.ticket_var = tk.StringVar(value="—")
        tk.Label(bar, textvariable=self.ticket_var, font=("Bahnschrift SemiBold", 14),
                 bg="#ffffff", fg="#1f3c88").pack(side="left")
        self.link_btn = ttk.Button(bar, text="Открыть в браузере", state="disabled",
                                   command=self._open_link)
        self.link_btn.pack(side="right")

        self.text = tk.Text(right, wrap="word", font=("Consolas", 10), relief="flat",
                            padx=12, pady=10, bg="#fbfcfe", fg="#111827",
                            highlightthickness=0)
        self.text.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        assign_bar = tk.Frame(right, bg="#ffffff")
        assign_bar.pack(fill="x", padx=12, pady=(0, 6))
        tk.Label(assign_bar, text="Назначено:", bg="#ffffff", fg="#6b7280",
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        self.assign_box = ttk.Combobox(assign_bar, textvariable=self.assign_var,
                                       state="readonly", width=52, values=[])
        self.assign_box.pack(side="left", padx=(8, 6))
        ttk.Button(assign_bar, text="Назначить в GLPI",
                   command=self.assign_now).pack(side="left")

        actions = tk.Frame(right, bg="#ffffff")
        actions.pack(fill="x", padx=12, pady=(0, 12))
        # в чат пишет ТОЛЬКО эта кнопка: сама заявка никуда не уходит
        self.chat_btn = ttk.Button(actions, text="Отправить в чат", state="disabled",
                                   command=self.send_to_bot)
        self.chat_btn.pack(side="left")
        self.bot_btn = ttk.Button(actions, text="Все исполнители", state="disabled",
                                  command=self._load_candidates)
        self.bot_btn.pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Копировать текст", command=self._copy).pack(
            side="left", padx=(8, 0))
        self.assign_note = tk.Label(actions, text="", bg="#ffffff", fg="#0f766e",
                                    font=("Segoe UI", 9))
        self.assign_note.pack(side="left", padx=12)

        # строка с вложениями и опасной кнопкой — отдельно, чтобы не путались
        extra = tk.Frame(right, bg="#ffffff")
        extra.pack(fill="x", padx=12, pady=(0, 10))
        tk.Label(extra, text="Отправить файлы:", bg="#ffffff", fg="#6b7280",
                 font=("Segoe UI", 9, "bold")).pack(side="left")
        # под каждую заявку набор файлов свой — пересобираем в _render
        self.files_frame = tk.Frame(extra, bg="#ffffff")
        self.files_frame.pack(side="left", padx=(6, 10))
        self.file_vars: list[tuple[dict[str, Any], tk.BooleanVar]] = []
        self.files_note = tk.Label(extra, text="", bg="#ffffff", fg="#6b7280",
                                   font=("Segoe UI", 9))
        self.files_note.pack(side="left")
        ttk.Button(extra, text="Удалить заявку",
                   command=self.delete_now).pack(side="right")

        status = tk.Label(self, textvariable=self.status_var, anchor="w",
                          bg="#e9edf5", fg="#374151", font=("Segoe UI", 9), padx=12,
                          pady=6)
        status.pack(fill="x", side="bottom")

    # ---------------- режим ----------------
    def _total_pages_hint(self) -> str:
        total = 0
        try:
            total = len(self.bridge.monitor.seen)
        except Exception:
            total = 0
        depth = int(self.cfg.row_limit) * max(1, int(self.cfg.list_pages))
        return f"смотрим по {depth} последним, знаю {total}"

    @staticmethod
    def _dialog_label(value: str) -> str:
        return f"общий чат ({value})" if value.startswith("chat") \
            else f"мой диалог ({value})"

    def _dialog_choices(self) -> tuple[list[tuple[str, str]], str]:
        """Куда слать: личный диалог и/или общий чат (BITRIX_DIALOG_ID[_ALT])."""
        cfg = getattr(self.bridge, "b24_config", None)
        ids: list[str] = []
        for value in (getattr(cfg, "dialog_id", ""), getattr(cfg, "dialog_alt", "")):
            value = (value or "").strip()
            if value and value not in ids:
                ids.append(value)
        opts = [(self._dialog_label(value), value) for value in ids]
        current = (getattr(cfg, "dialog_id", "") or "").strip()
        return opts, (self._dialog_label(current) if current else "")

    def _pick_dialog(self) -> None:
        chosen = self.dialog_var.get()
        for text, value in self._dialog_opts:
            if text == chosen:
                self.bridge.bitrix.config.dialog_id = value
                self.status_var.set(f"Bitrix24: теперь шлём в {value}")
                return

    def _team_text(self) -> str:
        emails = getattr(self.cfg, "team_emails", []) or []
        groups = getattr(self.cfg, "team_groups", []) or []
        if not emails and not groups:
            return "кому: любая назначенная активная заявка"
        parts = []
        if groups:
            parts.append("группы: " + ", ".join(groups))
        if emails:
            parts.append("почты: " + ", ".join(emails))
        return "кому — " + "; ".join(parts)

    def _apply_mode(self) -> None:
        try:
            rows = max(15, min(300, int(self.rows_var.get())))
            pace = max(0, int(self.pace_var.get()))
        except (ValueError, tk.TclError):
            return
        self.cfg.row_limit = rows
        self.cfg.refresh_pace = float(pace)
        self.pages_hint.config(text=self._total_pages_hint())
        self.status_var.set(f"GLPI: в опросе {rows} последних заявок; в чат — только "
                            f"по кнопке, {pace} с между заявками")

    def show_current(self) -> None:
        """Заполнить список текущими заявками (в чат ничего не уходит)."""
        only_active = bool(self.active_only_var.get())

        def work() -> None:
            try:
                self.bridge.monitor.show_current(only_active=only_active)
            except Exception as exc:
                self._ui(lambda: self.status_var.set(f"GLPI: {exc}"))
        self._run(work, "выгружаем текущие заявки из GLPI…")

    def refresh_now(self) -> None:
        """Кнопка «Актуализировать сейчас»: заявки уходят ПО ОДНОЙ, с паузой,
        а не свалкой; повторное нажатие предлагает остановить."""
        mon = self.bridge.monitor
        if mon.refreshing:
            if messagebox.askyesno("Актуализация идёт",
                                   "Остановить и не досылать остальные заявки?"):
                mon.cancel_refresh()
            return
        messagebox.showinfo(
            "Актуализация",
            f"В чат уйдут активные внутренние заявки, назначенные людям "
            f"(минимум {self.cfg.refresh_limit}).\n"
            f"По одной, пауза {int(self.cfg.refresh_pace)} с между сообщениями.\n"
            f"Остановить можно этой же кнопкой.")
        mon.start_refresh(force=True)
        self.status_var.set("GLPI: актуализация запущена…")

    # ---------------- очередь событий ----------------
    def _drain(self) -> None:
        """Разбор очереди событий из рабочих потоков.

        Это кольцо — сердце окна: умрёт от любого исключения (а их шлют
        из фоновых потоков), и всё встаёт: статус молчит, ответы из Bitrix24
        не обрабатываются, а кнопка «Отправить в чат» навсегда остаётся
        выключенной — ровно «кнопка ничего не делает». Поэтому ловим ВСЁ
        и перезаказываем себя при любом раскладе.
        """
        q = getattr(self.bridge, "queue", None)
        if q is not None:
            while True:
                try:
                    kind, payload = q.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._event(kind, payload)
                except Exception:
                    LOGGER.exception("GLPI: событие %s не обработано", kind)
        try:
            self.after(250, self._drain)
        except tk.TclError:          # окно закрыли
            pass

    def _event(self, kind: str, payload: Any) -> None:
        if kind == "glpi_new_ticket":
            self.add_ticket(payload)
        elif kind == "glpi_ticket_batch":
            for record in payload:
                self._remember(record)
            self._refresh_list()
            # первую заявку берём себе, только если оператор ничего не держит
            if self.order and self._selected_id not in self.records:
                self.listbox.selection_set(0)
                self._on_select()
        elif kind == "glpi_ui":
            payload()
        elif kind == "glpi_status":
            self.status_var.set(str(payload))
        elif kind == "glpi_refresh_ticket":
            self.status_var.set(f"GLPI: #{payload.ticket_id} актуализирована в чат")

    # ---------------- данные ----------------
    def _remember(self, ticket: Any) -> bool:
        """Правда — заявку показали. Решённые/закрытые при включённом
        фильтре в список не попадают и не остаются в нём."""
        try:
            only_active = bool(self.active_only_var.get())
        except (AttributeError, tk.TclError):
            only_active = True
        if only_active and not ticket.is_active:
            if ticket.ticket_id in self.records:
                self.records.pop(ticket.ticket_id, None)
                if ticket.ticket_id in self.order:
                    self.order.remove(ticket.ticket_id)
            return False
        if ticket.ticket_id not in self.records:
            self.order.append(ticket.ticket_id)
        self.records[ticket.ticket_id] = ticket
        return True

    def add_ticket(self, ticket: Any) -> None:
        if not self._remember(ticket):
            self._refresh_list()
            self.status_var.set(f"GLPI: #{ticket.ticket_id} — {ticket.status_name}, "
                                f"в список не добавляем")
            return
        self._refresh_list()
        # Новую заявку показываем, НО карточку у оператора не отбираем: иначе
        # «Назначить в GLPI» ушло бы по ПРИЛЕТЕВШЕЙ заявке вместо той, кому
        # он уже выбрал исполнителя.
        if self._busy or self._selected_id in self.records:
            self.status_var.set(f"GLPI: новая заявка #{ticket.ticket_id} — {ticket.name}"
                                " (в списке, карточку не переключаю)")
            return
        self.status_var.set(f"GLPI: новая заявка #{ticket.ticket_id} — {ticket.name}")
        self.listbox.selection_set(self.order.index(ticket.ticket_id))
        self._on_select()

    def _refresh_list(self) -> None:
        lines = []
        for tid in self.order:
            rec = self.records[tid]
            lines.append(f"#{tid} · {rec.name[:50]} · {rec.status_name}")
        self.list_var.set(tuple(lines))
        self.count_var.set(f"{len(self.order)} шт.")
        # list_var.set() перебирает содержимое Listbox и СНIMAET выделение.
        # Без возврата выделения _selected() слепнет на каждом обновлении
        # списка, а это раз в 30 с: кнопки «Назначить в GLPI» и «в чат»
        # начинали отвечать «сначала выбери заявку» по открытой заявке.
        # (выделение — единственное, на что опирался выбор заявки)
        if self._selected_id in self.order:
            idx = self.order.index(self._selected_id)
            self.listbox.selection_set(idx)
            self.listbox.see(idx)

    def _selected(self) -> Any | None:
        sel = self.listbox.curselection()
        if sel and sel[0] < len(self.order):
            self._selected_id = self.order[sel[0]]
        # выделения может и не быть (см. _refresh_list) — карточка-то на экране,
        # так что держимся за последнюю выбранную заявку, а не делаем вид,
        # что оператор ничего не выбирал
        if self._selected_id in self.records:
            return self.records[self._selected_id]
        return None

    def _on_select(self, _: Any = None) -> None:
        ticket = self._selected()
        if ticket is None:
            return
        self._detail_token = ticket.ticket_id
        self._selected_id = ticket.ticket_id
        self.ticket_var.set(f"#{ticket.ticket_id} {ticket.name}")
        self._render(ticket)
        self.chat_btn.state(["!disabled"])
        self.link_btn.state(["!disabled"])
        self.bot_btn.state(["!disabled"])
        self.assign_note.config(text="")
        self.assign_box.set("")
        self.assign_box["values"] = []
        self.choices = []
        self._load_current()
        # сразу тянем ВСЕХ исполнителей из GLPI, а не только назначенных —
        # иначе переключать исполнителя было бы не на кого
        self._load_candidates()
        if not ticket.raw_item:      # список приходит без деталей — догружаем
            self._load_detail(ticket.ticket_id)

    def _render(self, ticket: Any) -> None:
        self._set_text(build_message(ticket, self.bridge.base_url,
                                     self.bridge.monitor.mention_text(ticket)))
        files = list(getattr(ticket, "attachments", []) or [])
        self._rebuild_file_checks(files)

    def _rebuild_file_checks(self, files: list[dict[str, Any]]) -> None:
        """Чекбокс на каждый файл: в описание GLPI часто притаскивает логотип
        из подписи, и решать, слать его в чат или нет, должен оператор."""
        for child in self.files_frame.winfo_children():
            child.destroy()
        self.file_vars = []
        if not files:
            self.files_note.config(text="файлов нет")
            return
        for item in files[:8]:
            var = tk.BooleanVar(value=True)
            title = str(item.get("name") or "файл")
            if len(title) > 34:
                title = title[:31] + "…"
            if item.get("size"):
                title += f" ({item['size']})"
            tk.Checkbutton(self.files_frame, text=("🖼 " if item.get("is_image") else "📎 ") + title,
                           variable=var, bg="#ffffff", fg="#374151",
                           activebackground="#ffffff",
                           font=("Segoe UI", 9)).pack(anchor="w")
            self.file_vars.append((item, var))
        more = len(files) - 8
        self.files_note.config(text=(f"+{more} не показаны" if more > 0 else
                                     f"всего {len(files)}"))

    def _load_detail(self, ticket_id: int) -> None:
        """Карточка заявки дотягивается отдельно: список без неё читается за
        доли секунды, а детали нужны только на выбранную заявку."""
        def work() -> None:
            try:
                record = self.bridge.client.get_ticket(ticket_id)
            except Exception as exc:
                self._ui(lambda: self.status_var.set(f"GLPI: #{ticket_id} — {exc}"))
                return
            self._ui(lambda: self._got_detail(record))
        threading.Thread(target=work, daemon=True).start()

    def _got_detail(self, record: Any) -> None:
        self.records[record.ticket_id] = record
        if getattr(self, "_detail_token", None) == record.ticket_id:
            self._render(record)
            self._load_current()
        self._refresh_list()

    def _set_text(self, value: str) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", value)
        self.text.configure(state="disabled")

    def _current_assignees(self, ticket: Any) -> list[dict[str, Any]]:
        out = [{"key": f"User_{u.get('items_id')}", "itemtype": "User",
                "items_id": u.get("items_id"), "label": u.get("name") or "",
                "email": "", "title": ""} for u in ticket.assign_users]
        out += [{"key": f"Group_{g.get('items_id')}", "itemtype": "Group",
                 "items_id": g.get("items_id"), "label": g.get("name") or "",
                 "email": "", "title": ""} for g in ticket.assign_groups]
        return out

    def _set_choices(self, entries: list[dict[str, Any]]) -> None:
        """Список «Назначено»: подписи не должны повторяться, иначе выбор
        неоднозначен, и по подписи не угадаешь, кого имели в виду."""
        self._by_label = {}
        labels: list[str] = []
        for entry in entries:
            label = entry["label"] or f"#{entry.get('items_id')}"
            if label in self._by_label:
                tail = (entry.get("email") or "").split("@")[0] or entry.get("items_id")
                label = f"{label} [{tail}]"
                entry = dict(entry, label=label)
            self._by_label[label] = entry
            labels.append(label)
        self.choices = [self._by_label[label] for label in labels]
        self.assign_box["values"] = labels

    def _primary_assignee(self) -> dict[str, Any] | None:
        """Кто в заявке главный: сначала человек, потом любая группа."""
        current = getattr(self, "_current", []) or []
        for entry in current:
            if entry["itemtype"] == "User":
                return entry
        return current[0] if current else None

    def _load_current(self) -> None:
        ticket = self._selected()
        if ticket is None:
            return
        current = self._current_assignees(ticket)
        self._current = current
        keep = self.assign_var.get().strip()
        # Список перечитывается заново, но терять уже показанных кандидатов
        # (и тем более выбор оператора) нельзя: детали прилетают асинхронно,
        # и без этого выбранного человека просто «возвращало» обратно.
        merged = list(current)
        for entry in self.choices:
            if not any(entry["key"] == c["key"] for c in merged):
                merged.append(entry)
        self._set_choices(merged)
        # в поле — ОДИН исполнитель: строка «Иванов · Первая линия» не совпадает
        # ни с одним пунктом списка, и кнопка считала, что исполнителя не выбрали
        primary = self._primary_assignee()
        if keep and keep in self._by_label:
            self.assign_var.set(keep)
        else:
            self.assign_var.set(primary["label"] if primary else "")
        self.bot_btn.state(["!disabled"])

    def _load_candidates(self) -> None:
        ticket = self._selected()
        if ticket is None:
            return

        def work() -> None:
            try:
                flat = self.bridge.monitor.assign_choices(ticket.ticket_id)
            except Exception:
                LOGGER.exception("не удалось загрузить кандидатов")
                return
            self._ui(lambda: self._apply_candidates(flat))
        self._run(work, "загружаем исполнителей…")

    def _apply_candidates(self, flat: list[dict[str, Any]]) -> None:
        if not flat:
            self.status_var.set("GLPI: список исполнителей пуст")
            return
        # сначала те, кто уже назначен, потом все остальные из GLPI
        merged = list(getattr(self, "_current", []) or [])
        for entry in flat:
            if not any(entry["key"] == c["key"] for c in merged):
                merged.append(entry)
        keep = self.assign_var.get()
        self._set_choices(merged)
        if keep and keep in self._by_label:
            self.assign_var.set(keep)
        else:
            primary = self._primary_assignee()
            self.assign_var.set(primary["label"] if primary else "")
        self.status_var.set(f"GLPI: доступно исполнителей — {len(merged)}")

    # ---------------- действия ----------------
    def send_to_bot(self) -> None:
        """«Отправить в чат» — единственный путь в Bitrix24 из ручной карточки:
        сначала исполнитель, потом сообщение."""
        ticket = self._selected()
        if ticket is None or self._busy:
            return
        pick = self._picked()
        if pick is None:
            messagebox.showinfo("Нужен исполнитель",
                                "В заявке никого не назначено — выбери, кому она, "
                                "иначе в чат не шлём.")
            return
        self._busy = True
        self.chat_btn.state(["disabled"])
        self.status_var.set(f"#{ticket.ticket_id}: отправляем…")
        # что выбрано из файлов — считаем ЗДЕСЬ, в главном потоке: tk-переменные
        # из рабочего потока читаются с «RuntimeError: main thread is not in main loop»
        wanted = [item for item, var in getattr(self, "file_vars", []) if var.get()]
        # страховка: ответ из Битрикс24 мог и не прийти — кнопка не должна
        # оставаться выключенной. На вложения времени нужно больше (каждый
        # файл — выгрузка из GLPI и отдельная загрузка в чат).
        self.after(60_000 * (1 + len(wanted)), self._unstick)

        def done(saved: bool, info: str, partial: bool = False) -> None:
            def ui() -> None:
                self._busy = False
                self.chat_btn.state(["!disabled"])
                tail = f" · {note[0]}" if note[0] else ""
                if ok_send[0] and partial:
                    # карточка в чате есть, файлы нет. Это НЕ «не отправлено»,
                    # но и молчать нельзя: оператор должен видеть причину
                    self.assign_note.config(text=("карточка в чате, "
                                                 f"файлы не ушли{tail}"))
                    self.status_var.set(f"#{ticket.ticket_id}: карточка ушла, "
                                        "файлы — нет")
                    messagebox.showerror("Файлы не ушли в чат",
                                         f"Заявка #{ticket.ticket_id}: {info}")
                elif ok_send[0]:
                    self.assign_note.config(
                        text=((f"исполнитель сохранён: {pick['label']}" if saved
                                else "в чат ушло, но GLPI не сохранил исполнитель")
                              ) + tail)
                    self.status_var.set(f"#{ticket.ticket_id}: отправлено в чат")
                else:
                    self.assign_note.config(text="")
                    self.status_var.set(f"#{ticket.ticket_id}: не отправлено — {info}")
                    # Молчаливый отказ — ровно то, за что кнопку считают сломанной:
                    # причину показываем на экране, а не в строчке статуса.
                    messagebox.showerror("Не отправлено",
                                         f"Заявка #{ticket.ticket_id}: {info}")
            self._ui(ui)

        ok_send = [False]
        note = [""]

        def work() -> None:
            try:
                # если исполнитель уже стоит в заявке — не переписываем её вовсе
                already = any(c.get("key") == pick.get("key")
                              for c in getattr(self, "_current", []) or [])
                saved = True if already else self._save_assignee(ticket, pick)
                text = build_message(ticket, self.bridge.base_url,
                                     self.bridge.mention_for(pick))
                # здесь мы уже в рабочем потоке, поэтому ждём спокойно:
                # вложения шлются ПОСЛЕ текста, иначе в чате был бы файл
                # без подписи
                ok, info = self.bridge.bitrix.send(text)
                sent, failed = (0, [])
                if ok and wanted:
                    sent, failed = self._send_attachments(ticket, wanted)
                if sent or failed:
                    note[0] = f"вложений отправлено {sent}"
                    if failed:
                        note[0] += f", не ушло {len(failed)}"
                if ok and failed:
                    # файлы не легли — вторым сообщением уходят ссылки, чтобы
                    # исполнитель не остался с заявкой без вложений
                    self.bridge.bitrix.send(self.links_fallback(ticket, failed))
                ok_send[0] = ok
                done(saved,
                     "; ".join(f"{f['name']}: {f['reason']}" for f in failed)[:200]
                     if failed else info,
                     partial=bool(ok and failed))
            except Exception as exc:
                LOGGER.exception("отправка #%s не состоялась", ticket.ticket_id)
                # без done() кнопка осталась бы «занята» до перезапуска окна
                done(False, f"ошибка: {exc}")
        self._run(work, "отправляем…")

    def _send_attachments(self, ticket: Any, wanted: list[dict[str, Any]]) -> tuple[int, list[str]]:
        """Переслать выбранные файлы в чат: каждый — отдельным сообщением.

        Файлы в GLPI закрыты входом, поэтому качаем их СВОЕЙ сессией и только
        потом отдаём порталу методом im.v2.File.upload.
        """
        files = list(wanted or [])
        if not files:
            return 0, []
        self._ui(lambda: self.status_var.set(
            f"#{ticket.ticket_id}: пересылаю вложения ({len(files)})…"))
        sent, failed = 0, []
        for item in files:
            name = str(item.get("name") or f"document_{item.get('doc_id')}")
            try:
                blob, real_name = self.bridge.client.download_document(item["href"])
            except Exception as exc:
                LOGGER.warning("GLPI: вложение %s не скачан: %s", name, exc)
                failed.append({"name": name, "reason": str(exc),
                               "href": str(item.get("href") or "")})
                continue
            ok, info = self.bridge.bitrix.send_file(
                blob, real_name or name,
                text=f"#{ticket.ticket_id} · {name}")
            if ok:
                sent += 1
            else:
                failed.append({"name": name, "reason": str(info),
                               "href": str(item.get("href") or "")})
        if failed:
            LOGGER.warning("Bitrix24: не все вложения ушли: %s",
                           "; ".join(f"{f['name']}: {f['reason']}" for f in failed))
        return sent, failed

    def links_fallback(self, ticket: Any, failed: list[dict[str, Any]]) -> str:
        """Запасное сообщение: файлы не легли — пусть будут ссылки.

        Файлы в GLPI закрыты входом, но у исполнителя этот вход есть, и
        картинка открывается одним тапом. Так что заявка не остаётся вовсе
        без вложений, даже когда портал отбил загрузку.
        """
        base = self.bridge.base_url.rstrip("/")
        lines = [f"#{ticket.ticket_id}: файлы не ушли в чат — вот они ссылками:"]
        for item in failed:
            href = item.get("href") or ""
            mark = "🖼" if str(item.get("name", "")).startswith("картинка") else "📎"
            tail = f" — {base}/{href.lstrip('/')}" if href else f" ({item.get('reason')})"
            lines.append(f"  {mark} {item.get('name') or 'файл'}{tail}")
        return "\n".join(lines)

    def delete_now(self) -> None:
        """«Удалить заявку» — то же действие, что кнопка в карточке GLPI.

        Без права PURGE GLPI делает мягкое удаление (is_deleted=1), но из окна
        это всё равно не откатить — поэтому без подтверждения не обходимся.
        """
        ticket = self._selected()
        if ticket is None:
            messagebox.showinfo("Выбери заявку", "Сначала выбери заявку в списке.")
            return
        if self._busy:
            self.status_var.set("Подожди: предыдущая операция ещё идёт")
            return
        if not messagebox.askyesno(
                "Удалить заявку?",
                f"#{ticket.ticket_id} {ticket.name}\n\n"
                "Заявка будет удалена из GLPI, из окна это не отменить.\nУдалить?"):
            return
        self._busy = True
        self.chat_btn.state(["disabled"])

        def work() -> None:
            err = ""
            try:
                ok = self.bridge.client.delete_ticket(int(ticket.ticket_id))
            except Exception as exc:
                LOGGER.exception("GLPI: не удалось удалить #%s", ticket.ticket_id)
                ok, err = False, str(exc)

            def ui() -> None:
                self._idle()
                if ok:
                    self.forget_ticket(ticket.ticket_id)
                    self.status_var.set(f"#{ticket.ticket_id}: заявка удалена из GLPI")
                else:
                    self.status_var.set(f"#{ticket.ticket_id}: GLPI не удалил")
                    messagebox.showerror(
                        "Не удалено",
                        f"Заявка #{ticket.ticket_id}: "
                        f"{err or 'GLPI не принял удаление (не хватает прав?)'}")
            self._ui(ui)
        self._run(work, f"удаляем #{ticket.ticket_id}…")
        self.after(60_000, self._unstick)

    def forget_ticket(self, ticket_id: int) -> None:
        """Заявки больше нет — убираем её из окна и из состояния монитора."""
        self.records.pop(ticket_id, None)
        if ticket_id in self.order:
            self.order.remove(ticket_id)
        if self._selected_id == ticket_id:
            self._selected_id = None
        key = str(ticket_id)
        self.bridge.monitor.seen.pop(key, None)
        self.bridge.monitor.refreshed.pop(key, None)
        self._refresh_list()
        self.ticket_var.set("")
        self._set_text("")
        self.assign_box.set("")
        self.assign_box["values"] = []
        self.files_note.config(text="")
        self._rebuild_file_checks([])
        for btn in (self.chat_btn, self.link_btn, self.bot_btn):
            btn.state(["disabled"])

    def assign_now(self) -> None:
        """«Назначить в GLPI» — записать выбранного исполнителя, в чат НЕ пишем.

        Раньше эта кнопка была проткнута к _load_current (обновить список
        исполнителей) и поэтому «ничего не назначала».
        """
        ticket = self._selected()
        if ticket is None:
            messagebox.showinfo("Выбери заявку", "Сначала выбери заявку в списке.")
            return
        if self._busy:
            self.status_var.set("Подожди: предыдущая операция ещё идёт")
            return
        pick = self._picked()
        if pick is None:
            messagebox.showinfo("Нужен исполнитель", "Выбери, кому назначить заявку.")
            return
        if any(c.get("key") == pick.get("key")
               for c in getattr(self, "_current", []) or []):
            self.assign_note.config(text=f"{pick['label']} уже в исполнителях")
            self.status_var.set(f"#{ticket.ticket_id}: назначать нечего")
            return
        self._busy = True
        self.chat_btn.state(["disabled"])
        self.assign_note.config(text="")

        def work() -> None:
            ok = self._save_assignee(ticket, pick)

            def ui() -> None:
                self._idle()
                self.assign_note.config(
                    text=f"назначен: {pick['label']}" if ok else "GLPI не принял")
                self.status_var.set(f"#{ticket.ticket_id}: "
                                    + (f"исполнитель записан ({pick['label']})" if ok
                                       else "не записано"))
                if not ok:
                    messagebox.showerror(
                        "Не назначено",
                        f"Заявка #{ticket.ticket_id}: GLPI не записал "
                        f"исполнителя {pick['label']} (смотри журнал)")
            self._ui(ui)
        self._run(work, f"назначаем {pick['label']}…")
        self.after(60_000, self._unstick)

    def _save_assignee(self, ticket: Any, pick: dict[str, Any]) -> bool:
        """Записать исполнителя в GLPI (группы оставляем как были)."""
        groups = [g.get("items_id") for g in ticket.assign_groups]
        users = [pick["items_id"]]
        if pick["itemtype"] == "Group":
            users, groups = [], [pick["items_id"]]
        try:
            ok = self.bridge.client.set_assignee(int(ticket.ticket_id), users, groups,
                                                 disable_notifications=True)
            if ok:
                self.bridge.monitor.refreshed.pop(str(ticket.ticket_id), None)
                # карточка не должна врать: исполнитель в GLPI уже другой
                try:
                    fresh = self.bridge.client.get_ticket(int(ticket.ticket_id),
                                                          refresh=True)
                    ticket.assign_users = fresh.assign_users
                    ticket.assign_groups = fresh.assign_groups
                    self._current = self._current_assignees(fresh)
                except Exception:
                    LOGGER.debug("не удалось перечитать #%s", ticket.ticket_id)
            return ok
        except Exception:
            LOGGER.exception("GLPI: не удалось назначить #%s", ticket.ticket_id)
            return False

    def _picked(self) -> dict[str, Any] | None:
        label = self.assign_var.get().strip()
        entry = getattr(self, "_by_label", {}).get(label)
        if entry is None and label:
            for item in self.choices:
                if item["label"] == label:
                    entry = item
                    break
        if entry is not None:
            return entry
        # исполнитель уже назначен и оператор его не менял — заставлять
        # выбирать заново незачем
        return self._primary_assignee()

    def _ui(self, fn) -> None:
        """Из рабочего потока в Tkinter лезть нельзя — периодическое
        «RuntimeError: main thread is not in main loop» как раз от этого.
        Задачу кладём в общую очередь, выполнит её _drain в основном потоке."""
        self.bridge.queue.put(("glpi_ui", fn))

    def _run(self, work, status: str) -> None:
        """Фоновая задача. Исключение из work() не имеет права оставлять GUI",
        "в состоянии «занято»: кнопка «в чат» выглядела бы сломанной навсегда."""
        self.status_var.set(status)

        def guard() -> None:
            try:
                work()
            except Exception as exc:
                LOGGER.exception("фоновая задача упала")
                self._ui(lambda: self.status_var.set(f"Сбой: {exc}"))
                self._ui(self._idle)

        threading.Thread(target=guard, daemon=True).start()

    def _idle(self) -> None:
        """Вернуть кнопку «Отправить в чат» в рабочее состояние."""
        self._busy = False
        self.chat_btn.state(["!disabled"])

    def _unstick(self) -> None:
        """Если операция так и не завершилась — всё равно разблокировать."""
        if not self._busy:
            return
        LOGGER.warning("GLPI: операция зависла — возвращаем кнопку")
        self._idle()
        self.status_var.set("Прошла минута ответа — кнопку вернул, можно снова")

    def _copy(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self.text.get("1.0", "end").strip())
        self.status_var.set("Текст заявки в буфере обмена")

    def _open_link(self) -> None:
        """Ссылку открываем через ShellExecute: webbrowser.open() на Windows
        запускает браузер и ЖДЁТ его — главный поток окна вставал навсегда."""
        ticket = self._selected()
        if ticket is None:
            messagebox.showinfo("Выбери заявку", "Сначала выбери заявку в списке.")
            return
        url = ticket.url or (f"{self.bridge.base_url}/front/ticket.form.php"
                             f"?id={ticket.ticket_id}")
        ok, info = open_url(url)
        if ok:
            self.status_var.set(f"#{ticket.ticket_id}: открыл в браузере")
            return
        # без браузера оператор не должен терять ссылку
        self.clipboard_clear()
        self.clipboard_append(url)
        self.status_var.set(f"Браузер не открылся: {info}")
        messagebox.showwarning(
            "Открой ссылку сам",
            f"Браузер не открылся ({info}).\nСсылку положил в буфер обмена:\n{url}")

    def _clear_list(self) -> None:
        self.order.clear()
        self.records.clear()
        self._selected_id = None
        self._refresh_list()
        self.status_var.set("Список очищен")

    def _poll_now(self) -> None:
        def work() -> None:
            try:
                self.bridge.monitor.poll_new_tickets()
            except Exception as exc:
                self._ui(lambda: self.status_var.set(f"GLPI: {exc}"))
        self._run(work, "опрашиваем GLPI…")
