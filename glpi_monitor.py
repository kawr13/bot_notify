#!/usr/bin/env python3
"""Фоновый опрос GLPI: новые заявки и периодическая актуализация по команде.

Два цикла в одном потоке:
  * новые заявки — каждые new_interval секунд: сверяем выдачу списка по
    (id, дата изменения), новое тянем деталями в пуле и кладём в очередь GUI;
  * актуализация — по кнопке: активные внутренние заявки, назначенные
    НАШИМ СОТРУДНИКАМ (GLPI_TEAM_* в .env), отправляются в общий чат Bitrix24
    с упоминанием фактического исполнителя. Удалённые/решённые/закрытые
    пропускаем, неизменившиеся не дублируем.

Если команда не задана (GLPI_TEAM_EMAILS пуст) — берём любую назначенную
активную заявку, т.е. «всех, кому что-то назначено».
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from glpi_client import GlpiClient, Ticket

LOGGER = logging.getLogger("bot_notify.glpi_monitor")


def _split(raw: str) -> list[str]:
    return [part.strip().lower() for part in (raw or "").replace(";", ",").split(",")
            if part.strip()]


@dataclass
class MonitorConfig:
    new_interval: float = 30.0
    list_pages: int = 1               # сколько страниц по row_limit тянуть
    row_limit: int = 60               # заявок на одну страницу (glpilist_limit)
    refresh_limit: int = 10           # сколько заявок отправляем за цикл
    refresh_pace: float = 60.0        # пауза МЕЖДУ сообщениями, чтобы не вываливать всё сразу
    category_filter: str = ""         # брать только эту категорию ("Внутренние")
    queue_groups: list[str] = field(default_factory=lambda: ["Первая линия"])
    announce_first: bool = True       # при пустом состоянии показать текущие в GUI
    announce_active_only: bool = True  # решённые/закрытые не считать «новыми»
    show_limit: int = 40              # сколько текущих заявок выводить списком
    attachment_min_kb: int = 40       # файлы мельче этого веса снимать по умолчанию
    team_emails: list[str] = field(default_factory=list)
    team_groups: list[str] = field(default_factory=list)
    state_path: Path | None = None

    @classmethod
    def from_env(cls, env: dict[str, str]) -> "MonitorConfig":
        def num(key: str, default: float) -> float:
            try:
                return float(env.get(key, "") or default)
            except ValueError:
                return default
        return cls(
            new_interval=num("GLPI_NEW_INTERVAL", 30.0),
            list_pages=max(1, int(num("GLPI_LIST_PAGES", 1))),
            row_limit=int(num("GLPI_ROW_LIMIT", 60)),
            refresh_limit=int(num("GLPI_REFRESH_LIMIT", 10)),
            refresh_pace=num("GLPI_REFRESH_PACE", 60.0),
            attachment_min_kb=max(0, int(num("GLPI_ATTACHMENT_MIN_KB", 40))),
            category_filter=(env.get("GLPI_REFRESH_CATEGORY")
                             or env.get("GLPI_CATEGORY_FILTER") or "").strip(),
            queue_groups=[g.strip() for g in (env.get("GLPI_REFRESH_QUEUE_GROUPS", "")
                                              .replace(";", ",")).split(",")
                          if g.strip()] or ["Первая линия"],
            announce_active_only=str(env.get("GLPI_ANNOUNCE_ACTIVE_ONLY", "1")).strip()
                              not in ("0", "false", "no"),
            announce_first=str(env.get("GLPI_ANNOUNCE_FIRST", "1")).strip() not in
                             ("0", "false", "no"),
            show_limit=int(num("GLPI_SHOW_LIMIT", 40)),
            team_emails=_split(env.get("GLPI_TEAM_EMAILS", "")),
            team_groups=[g.strip() for g in (env.get("GLPI_TEAM_GROUPS", "")
                                             .replace(";", ",")).split(",") if g.strip()],
            state_path=Path("data/glpi_state.json"),
        )

    @property
    def team_defined(self) -> bool:
        return bool(self.team_emails or self.team_groups)


def build_message(ticket: Ticket, base_url: str, mention: str = "",
                  links: bool = False) -> str:
    """Текст для чата: заявка + последние комментарии + ссылка + исполнитель.

    `links` — приписать к вложениям прямые ссылки GLPI. По умолчанию НЕ
    приписывается: файлы уходят в чат настоящими вложениями, а простыня
    ссылок только засоряет сообщение. Ссылки пригодятся, если загрузка не
    удалась — их отдельным сообщением шлёт панель.
    """
    link = ticket.url or f"{base_url}/front/ticket.form.php?id={ticket.ticket_id}"
    lines = [f"Заявка #{ticket.ticket_id} — {ticket.name}",
             f"Статус: {ticket.status_name or ticket.status}"]
    if ticket.author:
        lines.append(f"Автор: {ticket.author}")
    if ticket.priority:
        lines.append(f"Приоритет: {ticket.priority}")
    body = (ticket.description or "").strip()
    if body:
        lines += ["", body[:1200]]
    notes = [c for c in (ticket.comments or [])[-3:] if c.get("text")]
    if notes:
        lines.append("")
        lines.append("Последние комментарии:")
        for note in notes:
            author = note.get("author") or "—"
            lines.append(f"  • {author}: {str(note['text'])[:400]}")
    lines += ["", link]
    files = list(getattr(ticket, "attachments", []) or [])
    if files:
        lines.append("")
        lines.append("Вложения:")
        for item in files[:6]:
            mark = "🖼" if item.get("is_image") else "📎"
            size = f" ({item['size']})" if item.get("size") else ""
            name = str(item.get("name") or "файл")
            href = str(item.get("href") or "")
            # ссылки — только по просьбе: обычно файл лежит в чате, и тыкать
            # исполнителя в GLPI незачем
            tail = (f" — {base_url.rstrip('/')}/{href.lstrip('/')}"
                    if (links and href) else "")
            lines.append(f"  {mark} {name}{size}{tail}")
        if len(files) > 6:
            lines.append(f"  … и ещё {len(files) - 6}")
    if mention:
        lines.append(mention)
    return "\n".join(lines)


class GlpiMonitor(threading.Thread):
    """Один поток, два таймера. Останов — stop() + join()."""

    def __init__(self, client: GlpiClient,
                 event_queue: "queue.Queue[tuple[str, Any]]",
                 config: MonitorConfig | None = None,
                 sender: Callable[[str], Any] | None = None,
                 mention_resolver: Callable[[str], int | None] | None = None) -> None:
        super().__init__(name="glpi-monitor", daemon=True)
        self.client = client
        self.queue = event_queue
        self.cfg = config or MonitorConfig()
        self.sender = sender
        self.mention_resolver = mention_resolver
        self.base_url = client.config.base_url
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self.seen: dict[str, str] = {}          # id -> date_mod
        self.refreshed: dict[str, str] = {}     # id -> подпись последнего sendenного
        self._team: dict[str, set[str]] = {}    # {"users": ids, "groups": ids}
        self._team_at = 0.0
        self.candidates_cache: dict[int, tuple[float, list[dict[str, Any]]]] = {}
        self._refresh_busy = False
        self._refresh_cancel = threading.Event()
        if self.cfg.state_path and self.cfg.state_path.exists():
            try:
                raw = json.loads(self.cfg.state_path.read_text(encoding="utf-8"))
                self.seen = {str(k): str(v) for k, v in (raw.get("seen") or {}).items()}
                self.refreshed = {str(k): str(v) for k, v in
                                  (raw.get("refreshed") or {}).items()}
            except Exception:
                LOGGER.warning("состояние GLPI не прочитано, начинаем с чистого листа")

    # ---------------- helpers ----------------
    def rows(self, pages: int | None = None) -> list[Ticket]:
        """Строки списка GLPI: одна страница = cfg.row_limit заявок (один запрос)."""
        return self.client.list_tickets(pages=pages or self.cfg.list_pages,
                                        rows=self.cfg.row_limit)

    def emit(self, kind: str, payload: Any) -> None:
        try:
            self.queue.put_nowait((kind, payload))
        except queue.Full:
            LOGGER.warning("очередь GUI переполнена, событие %s потеряно", kind)

    def _save_state(self) -> None:
        if not self.cfg.state_path:
            return
        try:
            with self._lock:
                payload = {"seen": self.seen, "refreshed": self.refreshed,
                           "saved_at": int(time.time())}
                self.cfg.state_path.parent.mkdir(parents=True, exist_ok=True)
                self.cfg.state_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            LOGGER.exception("не удалось сохранить состояние GLPI")

    @staticmethod
    def _signature(ticket: Ticket) -> str:
        """Меняется, если заявка дописалась — по нему решаем, слать ли снова."""
        last = ticket.comments[-1] if ticket.comments else {}
        return f"{ticket.updated_at}|{len(ticket.comments or [])}|{last.get('date', '')}"

    # ---------------- команда ----------------
    def team_ids(self, sample_ticket: int) -> dict[str, set[str]]:
        """items_id наших сотрудников/групп по почтам и названиям из GLPI."""
        if self._team and time.time() - self._team_at < 3600:
            return self._team
        users: set[str] = set()
        groups: set[str] = set()
        try:
            for entry in self.assign_choices(sample_ticket):
                email = (entry.get("email") or "").lower()
                title = (entry.get("title") or "").lower()
                label = (entry.get("raw") or "").lower()
                if entry["itemtype"] == "User":
                    if not self.cfg.team_emails or email in self.cfg.team_emails \
                            or any(mail in title for mail in self.cfg.team_emails):
                        users.add(str(entry["items_id"]))
                elif entry["itemtype"] == "Group":
                    if not self.cfg.team_groups or label in [g.lower() for g in
                                                             self.cfg.team_groups]:
                        groups.add(str(entry["items_id"]))
        except Exception:
            LOGGER.exception("GLPI: не удалось разобрать команду")
        self._team = {"users": users, "groups": groups}
        self._team_at = time.time()
        LOGGER.info("GLPI: команда — сотрудников %d, групп %d", len(users), len(groups))
        return self._team

    def _is_queue_group(self, group: dict[str, Any]) -> bool:
        """Группа-«очередь»: общая очередь, а не конкретный человек."""
        name = (group.get("name") or "").strip().lower()
        return any(name and (name == q.lower() or q.lower() in name)
                   for q in self.cfg.queue_groups)

    def has_person(self, ticket: Ticket) -> bool:
        """Назначен ли кто-то конкретный.

        Если в исполнителях только группы-очереди (например «Первая линия»),
        заявка считается неразобранной и актуализацию не получает.
        """
        if ticket.assign_users:
            return True
        return any(not self._is_queue_group(g) for g in ticket.assign_groups)

    def refresh_match(self, ticket: Ticket) -> bool:
        """Попадает ли заявка в актуализацию."""
        if not ticket.is_active:
            return False
        if self.cfg.category_filter and self.cfg.category_filter.lower() not in \
                (ticket.category or "").lower():
            return False
        if not self.has_person(ticket):
            return False
        return self.for_team(ticket)

    def for_team(self, ticket: Ticket) -> bool:
        """Назначено ли нашему сотруднику (или любой группе команды)."""
        if not ticket.is_active:
            return False
        if not ticket.assign_users and not ticket.assign_groups:
            return False
        if not self.cfg.team_defined:
            return True                 # команда не задана — берём все назначенные
        team = self._team or {"users": set(), "groups": set()}
        if any(str(u.get("items_id")) in team["users"] for u in ticket.assign_users):
            return True
        return any(str(g.get("items_id")) in team["groups"] for g in ticket.assign_groups)

    def user_email(self, ticket: Ticket, items_id: Any) -> str:
        """Почта сотрудника GLPI (в карточке её нет, берём из списка кандидатов)."""
        for entry in self.assign_choices(ticket.ticket_id):
            if entry["itemtype"] == "User" and str(entry["items_id"]) == str(items_id):
                return entry.get("email") or ""
        return ""

    def mention_text(self, ticket: Ticket) -> str:
        """@исполнителю: BB-код, если в Bitrix24 нашли человека по почте."""
        parts: list[str] = []
        for user in ticket.assign_users:
            name = (user.get("name") or "").strip()
            if not name:
                continue
            email = (user.get("email") or "").strip() or \
                (self.user_email(ticket, user.get("items_id")) or "")
            uid = self.mention_resolver(email) if (self.mention_resolver and email) else None
            parts.append(f"[USER={uid}]@{name}[/USER]" if uid else f"@{name}")
        for group in ticket.assign_groups:
            name = (group.get("name") or "").strip()
            if name:
                parts.append(f"группа {name}")
        if not parts:
            return ""
        return "К исполнителю: " + ", ".join(parts)

    # ---------------- loops ----------------
    def run(self) -> None:
        LOGGER.info("монитор GLPI запущен: опрос новых заявок раз в %.0fs; "
                    "в Bitrix24 пишем только по кнопке", self.cfg.new_interval)
        self.emit("glpi_status", "GLPI: подключаюсь…")
        next_new = 0.0
        announced = bool(self.seen)      # состояние уже есть — показывать нечего
        while not self._stop.is_set():
            now = time.time()
            try:
                if now >= next_new:
                    next_new = now + self.cfg.new_interval
                    first = not self.seen
                    self.poll_new_tickets(first=first)
                    if first and self.cfg.announce_first and not announced:
                        announced = True
                        # окно не должно выглядеть пустым до первой новой заявки
                        self.show_current(only_active=True)
            except Exception as exc:
                LOGGER.warning("GLPI: цикл новых заявок: %s", exc)
                self.emit("glpi_status", f"GLPI: ошибка опроса — {exc}")
            # своего цикла отправки тут нет: актуализацию запускает оператор
            # кнопкой, а сообщения расходятся по одному (start_refresh)
            self._stop.wait(1.0)
        LOGGER.info("монитор GLPI остановлен")

    def stop(self) -> None:
        self._stop.set()

    def poll_new_tickets(self, first: bool = False) -> None:
        """Новые (или изменённые после нашего просмотра) заявки -> в GUI."""
        tickets = self.rows()
        fresh: list[Ticket] = []
        with self._lock:
            for ticket in tickets:
                key = str(ticket.ticket_id)
                stamp = ticket.updated_at or ticket.created_at
                if self.seen.get(key) == stamp:
                    continue
                self.seen[key] = stamp
                # Молча пропускаем историю: первое знакомство с заявкой, которую
                # нашли, лишь увеличив глубину, — не «новая заявка». Сюда же
                # попадают решённые/закрытые: их эфир не интересен.
                if first or (self.cfg.announce_active_only and not ticket.is_active):
                    continue
                fresh.append(ticket)
            if first:
                self._save_state()
        if first:
            self.emit("glpi_status", f"GLPI: наблюдаю за {len(self.seen)} заявками")
            return
        if not fresh:
            return
        details = self.client.get_tickets([t.ticket_id for t in fresh])
        for ticket in fresh:
            record = details.get(ticket.ticket_id) or ticket
            self.emit("glpi_new_ticket", record)
        self._save_state()
        self.emit("glpi_status", f"GLPI: новых заявок — {len(fresh)}")

    # ---------------- актуализация ----------------
    @property
    def refreshing(self) -> bool:
        return self._refresh_busy

    def start_refresh(self, force: bool = True) -> bool:
        """Актуализация отдельным потоком: сообщения уходят по одному,
        с паузой cfg.refresh_pace, и их можно отменить."""
        if self._refresh_busy:
            self.emit("glpi_status", "GLPI: актуализация уже идёт (жми кнопку ещё раз, "
                                     "чтобы остановить)")
            return False
        self._refresh_cancel.clear()
        threading.Thread(target=self._refresh_guarded, kwargs={"force": force},
                         name="glpi-refresh", daemon=True).start()
        return True

    def cancel_refresh(self) -> None:
        self._refresh_cancel.set()
        self.emit("glpi_status", "GLPI: останавливаю актуализацию…")

    def _refresh_guarded(self, force: bool) -> None:
        self._refresh_busy = True
        try:
            self.poll_active_tickets(force=force)
        except Exception as exc:
            LOGGER.warning("GLPI: цикл актуализации: %s", exc)
            self.emit("glpi_status", f"GLPI: ошибка актуализации — {exc}")
        finally:
            self._refresh_busy = False

    def poll_active_tickets(self, force: bool = False) -> None:
        """Активные внутренние заявки наших сотрудников — в чат, по одной,
        раз в cfg.refresh_pace секунд (не «все сразу»)."""
        candidates = [t for t in self.rows()
                      if t.is_active][:self.cfg.refresh_limit * 3]
        if not candidates:
            self.emit("glpi_status", "GLPI: активных заявок нет")
            return
        if self.cfg.team_defined and not self._team:
            self.team_ids(candidates[0].ticket_id)
        else:
            self._team = self._team or {"users": set(), "groups": set()}
        details = self.client.get_tickets([t.ticket_id for t in candidates])
        mine: list[Ticket] = []
        for row in candidates:
            record = details.get(row.ticket_id) or row
            if not record.category:
                # категория приходит только в строке списка, в карточке её нет
                record.category = row.category
            if self.refresh_match(record):
                mine.append(record)
        mine = mine[:self.cfg.refresh_limit]
        if not mine:
            self.emit("glpi_status",
                      "GLPI: под актуализацию ничего не попало (категория «"
                      f"{self.cfg.category_filter or 'любая'}»; заявка только на "
                      f"{ ' / '.join(self.cfg.queue_groups) } не считается)")
            return
        pace = max(0.0, float(self.cfg.refresh_pace))
        self.emit("glpi_status", f"GLPI: актуализация — {len(mine)} заявок, "
                                 f"пауза {pace:.0f} с между сообщениями")
        sent = skipped = 0
        for index, record in enumerate(mine, 1):
            if self._refresh_cancel.is_set() or self._stop.is_set():
                self.emit("glpi_status",
                          f"GLPI: актуализация остановлена на {index - 1} из {len(mine)}")
                break
            key = str(record.ticket_id)
            signature = self._signature(record)
            with self._lock:
                if not force and self.refreshed.get(key) == signature:
                    skipped += 1
                    continue
                self.refreshed[key] = signature
            try:
                self.sender(build_message(record, self.base_url,
                                          self.mention_text(record)))
                sent += 1
                self.emit("glpi_refresh_ticket", record)
                left = len(mine) - index
                self.emit("glpi_status",
                          f"GLPI: актуализация {index}/{len(mine)} — #{record.ticket_id}"
                          + (f", следующая через {pace:.0f} с" if left and pace else ""))
            except Exception:
                LOGGER.exception("GLPI: не удалось отправить #%s", key)
            if index < len(mine) and pace:
                for _ in range(int(pace)):
                    if self._refresh_cancel.is_set() or self._stop.is_set():
                        break
                    time.sleep(1)
        if sent:
            self._save_state()
        if not self._refresh_cancel.is_set():
            self.emit("glpi_status",
                      f"GLPI: актуализация завершена — отправлено {sent}, "
                      f"пропущено {skipped}")

    def show_current(self, only_active: bool = True, pages: int | None = None,
                     limit: int | None = None, with_details: bool = False) -> int:
        """Наполнить список GUI текущими заявками (в чат при этом НИЧЕГО не идёт).

        Нужна потому, что первый цикл монитора только запоминает заявки, иначе
        окно выглядело бы пустым до первой новой заявки.

        Детали (описание, комментарии, исполнитель) по умолчанию НЕ тянем —
        это ~0.3 с на весь список вместо секунд на сорок запросов; карточка
        дотянет их лениво, когда заявку выберут.
        """
        rows = self.rows(pages)
        if only_active:
            rows = [t for t in rows if t.is_active]
        rows = rows[:(limit or self.cfg.show_limit)]
        if not rows:
            self.emit("glpi_status", "GLPI: по заданным условиям заявок нет — "
                                     f"проверь глубину (row_limit={self.cfg.row_limit}, "
                                     f"страниц {self.cfg.list_pages})")
            return 0
        if with_details:
            details = self.client.get_tickets([t.ticket_id for t in rows])
            rows = [details.get(t.ticket_id) or t for t in rows]
        rows.sort(key=lambda t: t.ticket_id, reverse=True)
        self.emit("glpi_ticket_batch", rows)
        self.emit("glpi_status",
                  f"GLPI: показал {len(rows)} активных заявок (глубина "
                  f"{self.cfg.row_limit * max(1, pages or self.cfg.list_pages)} строк)")
        return len(rows)

    # ---------------- для GUI ----------------
    def assign_choices(self, ticket_id: int, max_age: float = 300.0) -> list[dict[str, Any]]:
        """Плоский список «кого назначить» из GLPI (кэш max_age секунд)."""
        hit = self.candidates_cache.get(ticket_id)
        if hit and time.time() - hit[0] < max_age:
            return hit[1]
        try:
            raw = self.client.assign_candidates(ticket_id)
        except Exception:
            LOGGER.exception("GLPI: не получили кандидатов для #%s", ticket_id)
            return []
        flat: list[dict[str, Any]] = []

        def walk(node: dict[str, Any]) -> None:
            itemtype = node.get("itemtype") or ""
            label = (node.get("text") or "").strip()
            if itemtype and label:
                kind = {"User": "", "Group": "группа",
                        "Supplier": "поставщик"}.get(itemtype, "прочее")
                flat.append({
                    "key": f"{itemtype}_{node.get('items_id')}",
                    "itemtype": itemtype,
                    "items_id": node.get("items_id"),
                    "label": label if not kind else f"{label} ({kind})",
                    "raw": label,
                    "email": (node.get("default_email") or "").strip(),
                    "title": (node.get("title") or "").strip(),
                })
            for child in node.get("children") or []:
                walk(child)

        for entry in raw:
            walk(entry)
        self.candidates_cache[ticket_id] = (time.time(), flat)
        return flat
