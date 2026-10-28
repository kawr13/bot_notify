#!/usr/bin/env python3
"""GLPI (help.basisport.ru, 11.0.7) client over the HTML session.

Legacy REST is disabled on this instance and HLAPI needs an OAuth client we are
not allowed to register, so everything goes through the web session:

  * login      POST /front/login.php (+ _glpi_csrf_token from the form)
  * list       GET  /front/ticket.php  (server rendered table, 15 rows/page)
  * detail     GET  /ajax/common.tabs.php?_glpi_tab=Ticket$main  (~0.4 s, ~450 KB)
  * actors     POST /ajax/actors.php   (needs _idor_token from the tab HTML)
  * assignee   POST /front/ticket.form.php (update=1 + _users_id_assign[...])

Every POST needs the one-shot X-Glpi-Csrf-Token header taken from
<meta property="glpi:csrf_token">; a failed check does not burn it, a successful
POST does, so it is re-read before each write.
"""
from __future__ import annotations

import html
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import urljoin

import requests
import urllib3

urllib3.disable_warnings()

LOGGER = logging.getLogger("bot_notify.glpi")

ACTIVE_STATUSES = {1, 2, 3, 4, 10}
STATUS_NAMES = {
    1: "Новая", 2: "В работе (назначена)", 3: "В работе (запланирована)",
    4: "Ожидание", 5: "Решена", 6: "Закрыто", 10: "Согласование",
}
# td[data-searchopt-content-id] ids of the list grid
COL = {"id": 2, "name": 1, "status": 12, "updated": 19, "created": 15,
       "priority": 3, "author": 4, "assign": 5, "category": 7, "sla": 18}

# GLPI рисует статусы иконками-глифами, и «Решена» приходит как «Pешена» с
# латинской p. Без подмены омографов статус не опознаётся, а закрытые заявки
# попадают в «активные».
_HOMOGLYPH = str.maketrans({"p": "р", "c": "с", "o": "о", "e": "е", "a": "а",
                            "x": "х", "y": "у", "k": "к", "m": "м", "t": "т"})


# --------------------------------------------------------------------------
# JS object literal -> python (window.actors is JS, not JSON: bare keys, 'quotes')
# --------------------------------------------------------------------------
class JsParseError(ValueError):
    pass


def parse_js_object(src: str, start: int = 0) -> tuple[Any, int]:
    """Parse one JS object/array literal beginning at `start`; return (value, end)."""
    i, n = start, len(src)

    def skip() -> None:
        nonlocal i
        while i < n and src[i] in " \t\r\n":
            i += 1

    def string(quote: str) -> str:
        nonlocal i
        i += 1
        out: list[str] = []
        while i < n:
            ch = src[i]
            if ch == "\\":
                nxt = src[i + 1] if i + 1 < n else ""
                if nxt == "u" and re.match(r"[0-9a-fA-F]{4}", src[i + 2:i + 6]):
                    out.append(chr(int(src[i + 2:i + 6], 16)))
                    i += 6
                    continue
                if nxt == "x" and re.match(r"[0-9a-fA-F]{2}", src[i + 2:i + 4]):
                    out.append(chr(int(src[i + 2:i + 4], 16)))
                    i += 4
                    continue
                out.append({"n": "\n", "t": "\t", "r": "\r", "b": "\b",
                            "f": "\f", "0": "\x00"}.get(nxt, nxt))
                i += 2
                continue
            if ch == quote:
                i += 1
                return "".join(out)
            out.append(ch)
            i += 1
        raise JsParseError("unterminated string")

    def value() -> Any:
        nonlocal i
        skip()
        if i >= n:
            raise JsParseError("eof")
        ch = src[i]
        if ch in "'\"":
            return string(ch)
        if ch == "{":
            return obj()
        if ch == "[":
            return arr()
        m = re.match(r"-?\d+(?:\.\d+)?", src[i:])
        if m:
            i += m.end()
            txt = m.group(0)
            return float(txt) if "." in txt else int(txt)
        for word, val in (("true", True), ("false", False), ("null", None),
                          ("undefined", None), ("Infinity", None)):
            if src.startswith(word, i):
                i += len(word)
                return val
        raise JsParseError(f"unexpected {src[i:i+20]!r}")

    def key() -> str:
        nonlocal i
        skip()
        if src[i] in "'\"":
            return string(src[i])
        m = re.match(r"[A-Za-z_$][\w$]*", src[i:])
        if not m:
            raise JsParseError(f"bad key at {src[i:i+30]!r}")
        i += m.end()
        return m.group(0)

    def obj() -> dict:
        nonlocal i
        i += 1
        out: dict[str, Any] = {}
        while True:
            skip()
            if i >= n:
                raise JsParseError("unterminated object")
            if src[i] == "}":
                i += 1
                return out
            if src[i] == ",":
                i += 1
                continue
            k = key()
            skip()
            if i < n and src[i] == ":":
                i += 1
                out[k] = value()
            else:  # shorthand property
                out[k] = True

    def arr() -> list:
        nonlocal i
        i += 1
        out: list[Any] = []
        while True:
            skip()
            if i >= n:
                raise JsParseError("unterminated array")
            if src[i] == "]":
                i += 1
                return out
            if src[i] == ",":
                i += 1
                continue
            out.append(value())

    return value(), i


def extract_js_object(page: str, pattern: str) -> Any:
    """Find `pattern` (must end right before the '{') and parse the object after it."""
    m = re.search(pattern, page)
    if not m:
        return None
    brace = page.find("{", m.end() - 1 if page[m.end() - 1] == "{" else m.end())
    if brace < 0:
        return None
    try:
        return parse_js_object(page, brace)[0]
    except JsParseError as exc:
        LOGGER.warning("JS parse fail for %s: %s", pattern, exc)
        return None


# --------------------------------------------------------------------------
@dataclass(slots=True)
class GlpiConfig:
    base_url: str = "https://help.basisport.ru:56780"
    login: str = ""
    password: str = ""
    verify_ssl: bool = False
    timeout: int = 30
    page_size: int = 15
    list_limit: int = 60              # строк за один запрос (glpilist_limit)
    detail_workers: int = 10
    category_filter: str = ""          # e.g. "Внутренние" for "внутренние заявки"
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(cls, env: dict[str, str]) -> "GlpiConfig":
        def flag(key: str, default: bool) -> bool:
            raw = (env.get(key) or "").strip().lower()
            return default if raw in ("", "none") else raw not in ("0", "false", "no")

        return cls(
            base_url=(env.get("GLPI_URL") or cls.base_url).rstrip("/"),
            login=env.get("GLPI_USER", ""),
            password=env.get("GLPI_PASSWORD", ""),
            verify_ssl=flag("GLPI_VERIFY_SSL", False),
            detail_workers=int(env.get("GLPI_DETAIL_WORKERS") or 6),
            category_filter=(env.get("GLPI_CATEGORY_FILTER") or "").strip(),
        )


@dataclass(slots=True)
class Ticket:
    """Ticket summary + optional detail payload."""

    ticket_id: int
    name: str = ""
    status: int = 0
    status_name: str = ""
    priority: str = ""
    author: str = ""
    assign: str = ""
    category: str = ""
    created_at: str = ""
    updated_at: str = ""
    solved_at: str = ""
    closed_at: str = ""
    is_deleted: bool = False
    url: str = ""
    description: str = ""
    comments: list[dict[str, Any]] = field(default_factory=list)
    assign_users: list[dict[str, Any]] = field(default_factory=list)
    assign_groups: list[dict[str, Any]] = field(default_factory=list)
    raw_item: dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return not self.is_deleted and self.status in ACTIVE_STATUSES

    @property
    def assignee_names(self) -> str:
        names = [u.get("name") or f"user#{u.get('items_id')}" for u in self.assign_users]
        names += [g.get("name") or f"group#{g.get('items_id')}" for g in self.assign_groups]
        return ", ".join(n for n in names if n)

    def as_record_text(self, base_url: str) -> str:
        link = self.url or f"{base_url}/front/ticket.form.php?id={self.ticket_id}"
        parts = [f"#{self.ticket_id} {self.name}",
                 f"Статус: {self.status_name or self.status}",
                 f"Приоритет: {self.priority}" if self.priority else "",
                 f"Автор: {self.author}" if self.author else "",
                 f"Назначено: {self.assignee_names}" if self.assignee_names else "",
                 f"Создана: {self.created_at}" if self.created_at else "",
                 "", self.description.strip(), "", link]
        return "\n".join(p for p in parts if p != "").strip()


class GlpiClient:
    """Thread-safe enough for one monitor thread + GUI actions."""

    LOGIN_MARKERS = ("name=\"login_password\"", "bx-authform", "Вход", "lostPassword")

    def __init__(self, config: GlpiConfig) -> None:
        self.config = config
        self.session = requests.Session()
        self.session.verify = config.verify_ssl
        self.session.headers.update({"User-Agent": "BotNotifyDesktop/1.0 (GLPI bridge)"})
        pool = max(4, config.detail_workers)
        adapter = requests.adapters.HTTPAdapter(pool_connections=pool, pool_maxsize=pool * 2)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self._lock = threading.RLock()
        self._logged_in = False
        self._meta_csrf: str | None = None
        self._meta_csrf_ts = 0.0
        self._idor_cache: dict[int, tuple[str, float]] = {}
        self._tabs_cache: tuple[int, str, float] | None = None

    # ---------------- session ----------------
    def _url(self, path: str) -> str:
        return urljoin(self.config.base_url + "/", path.lstrip("/"))

    def login(self, force: bool = False) -> None:
        with self._lock:
            if self._logged_in and not force:
                return
            if not self.config.login or not self.config.password:
                raise RuntimeError("GLPI_USER/GLPI_PASSWORD не заданы в .env")
            resp = self.session.get(self._url("/index.php"), timeout=self.config.timeout)
            m = re.search(r'name="_glpi_csrf_token" value="([^"]+)"', resp.text)
            if not m:
                raise RuntimeError("не нашли _glpi_csrf_token на странице входа")
            # allow_redirects=False on purpose: GLPI rotates the session cookie on
            # the 302 and requests would drop the new cookie value.
            self.session.post(
                self._url("/front/login.php"),
                data={"login_name": self.config.login,
                      "login_password": self.config.password,
                      "_glpi_csrf_token": m.group(1), "noAUTO": "1", "redirect": ""},
                allow_redirects=False, timeout=self.config.timeout,
            )
            probe = self.session.get(self._url("/front/ticket.php"), timeout=self.config.timeout)
            if self._looks_like_login(probe.text):
                raise RuntimeError("GLPI отдал страницу логина — неверные учётные данные")
            self._logged_in = True
            self._meta_csrf = None
            LOGGER.info("GLPI: сессия установлена для %s", self.config.login)

    def _looks_like_login(self, text: str) -> bool:
        return 'name="login_password"' in text and "ticket" not in text[:2000].lower()

    def _ensure(self, resp: requests.Response) -> requests.Response:
        """Re-login once if the proxy/session threw us back to the login page."""
        if self._looks_like_login(resp.text):
            LOGGER.info("GLPI: сессия сброшена, перелогинимся")
            self.login(force=True)
        return resp

    # ---------------- csrf ----------------
    def meta_csrf(self, refresh: bool = False) -> str:
        """Read the one-shot token from <meta property="glpi:csrf_token">."""
        with self._lock:
            if not refresh and self._meta_csrf and time.time() - self._meta_csrf_ts < 300:
                return self._meta_csrf
            self.login()
            resp = self.session.get(
                self._url("/ajax/getGlpiCode.php") if False else self._url("/front/ticket.php"),
                timeout=self.config.timeout)
            self._ensure(resp)
            m = re.search(r'<meta property="glpi:csrf_token" content="([0-9a-f]+)"', resp.text)
            if not m:
                raise RuntimeError("не нашли glpi:csrf_token в HTML")
            self._meta_csrf = m.group(1)
            self._meta_csrf_ts = time.time()
            return self._meta_csrf

    def _post(self, path: str, data: dict[str, Any], *, referer: str = "",
              retries: int = 2, xhr: bool = False) -> requests.Response:
        self.login()
        for attempt in range(retries + 1):
            token = self.meta_csrf(refresh=attempt > 0)
            headers = {"X-Glpi-Csrf-Token": token}
            body = dict(data)
            if xhr:
                # ajax/*.php отвечает 200 с ПУСТЫМ телом без этого заголовка
                headers["X-Requested-With"] = "XMLHttpRequest"
            else:
                # Обычный POST: GLPI читает токен из тела, заголовок игнорирует,
                # без поля _glpi_csrf_token апдейт отвечает 403
                body["_glpi_csrf_token"] = token
            if referer:
                headers["Referer"] = referer
            resp = self.session.post(self._url(path), data=body, headers=headers,
                                     timeout=self.config.timeout)
            if resp.status_code == 403 and "Действие недоступно" in resp.text and attempt < retries:
                LOGGER.info("GLPI: CSRF принят за протухший, повтор (popitka %d)", attempt + 1)
                continue
            return resp
        raise RuntimeError("не удалось пройти CSRF")

    # ---------------- list ----------------
    def list_tickets(self, pages: int = 1, include_deleted: bool = False,
                     rows: int | None = None) -> list[Ticket]:
        """Список заявок. Одна страница = `rows` строк: GLPI слушается параметр
        `glpilist_limit`, поэтому 60 заявок приходят ОДНИМ запросом за ~0.3 с
        вместо четырёх по 15.

        Пустой явный критерий отключает фильтр `status=notold`, иначе было бы
        видно 58 заявок из 987.
        """
        self.login()
        limit = int(rows or self.config.list_limit or 15)
        out: list[Ticket] = []
        for page_no in range(max(1, pages)):
            params = {
                "itemtype": "Ticket",
                "criteria[0][field]": 1, "criteria[0][searchtype]": "contains",
                "criteria[0][value]": "",
                "sort[0]": 19, "order[0]": "DESC",
                "glpilist_limit": limit,
                "start": page_no * limit,
            }
            if include_deleted:
                params["is_deleted"] = 1
            resp = self.session.get(self._url("/front/ticket.php"), params=params,
                                    timeout=self.config.timeout)
            self._ensure(resp)
            out.extend(self._parse_list(resp.text))
        return out

    def _parse_list(self, page: str) -> list[Ticket]:
        rows: list[Ticket] = []
        table = re.search(r"<table[^>]*search-results[^>]*>(.*?)</table>", page, re.S)
        if not table:
            return rows
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", table.group(1), re.S):
            cells = {}
            for cid, raw in re.findall(
                    r'data-searchopt-content-id="(\d+)"[^>]*>(.*?)</td>', tr, re.S):
                cells[int(cid)] = self._clean_cell(raw)
            if not cells:
                continue
            digits = re.sub(r"[^\d]", "", cells.get(COL["id"], ""))
            tid = int(digits) if digits else 0
            if not tid:
                continue
            status_raw = cells.get(COL["status"], "")
            status = self._status_id(status_raw)
            rows.append(Ticket(
                ticket_id=tid, name=cells.get(COL["name"], ""),
                status=status,
                # показываем правильное написание: GLPI отдаёт «Pешена» с латинской p
                status_name=STATUS_NAMES.get(status, status_raw),
                priority=cells.get(COL["priority"], ""),
                author=cells.get(COL["author"], ""),
                assign=cells.get(COL["assign"], ""),
                category=cells.get(COL["category"], ""),
                created_at=cells.get(COL["created"], ""),
                updated_at=cells.get(COL["updated"], ""),
                url=f"{self.config.base_url}/front/ticket.form.php?id={tid}",
            ))
        return rows

    @staticmethod
    def _clean_cell(raw: str) -> str:
        """Cell HTML carries hover tooltips; cut at the first injected block."""
        cut = re.split(r"<script|<a id='tooltiplink|<div id='content", raw, maxsplit=1)[0]
        # GLPI 11 renders status icons as <i class="ti ti-..."> whose CSS content
        # is a private-use glyph; leaving it in corrupts the status text.
        cut = re.sub(r"""<i\b[^>]*class=['"][^'"]*(?:ti |itilstatus|fas |far )[^'"]*['"][^>]*>.*?</i>""",
                     " ", cut, flags=re.S)
        text = re.sub(r"<[^>]+>", " ", cut)
        text = html.unescape(text).replace("\xa0", " ").replace("\u200b", "")
        text = "".join(c for c in text if not 0xE000 <= ord(c) <= 0xF8FF)
        return re.sub(r"\s+", " ", text).strip(" \t\r\n·")

    @staticmethod
    def _strip(value: str) -> str:
        """HTML -> one line of plain text."""
        text = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
        return re.sub(r"\s+", " ", text).strip()

    # Латинские омографы: GLPI рендерит статусы иконками-глифами, и «Решена»
    # приходит как «Pешена» (латинская p) — без подмены статус не узнать
    @staticmethod
    def _status_id(name: str) -> int:
        raw = (name or "").lower().translate(_HOMOGLYPH)
        for sid, sname in STATUS_NAMES.items():
            if sname.lower() in raw:
                return sid
        return 0

    # ---------------- detail ----------------
    def _tabs_html(self, ticket_id: int, refresh: bool = False) -> str:
        """common.tabs.php payload, briefly cached: assign_candidates() needs both
        the parsed item and the raw page, and refetching 420 KB costs a round trip.

        КЭШ — самое коварное место клиента: перечитать заявку сразу ПОСЛЕ
        правки без refresh = увидеть состояние ДО правки и решить, что GLPI
        «молча не применил» назначенного.
        """
        cached = self._tabs_cache
        if (not refresh and cached and cached[0] == ticket_id
                and time.time() - cached[2] < self.CACHE_TTL):
            return cached[1]
        self.login()
        resp = self.session.get(
            self._url("/ajax/common.tabs.php"),
            params={"_glpi_tab": "Ticket$main", "_target": "/front/ticket.form.php",
                    "_itemtype": "Ticket", "id": ticket_id, "_": int(time.time())},
            headers={"X-Requested-With": "XMLHttpRequest"},
            timeout=self.config.timeout)
        self._ensure(resp)
        self._tabs_cache = (ticket_id, resp.text, time.time())
        return resp.text

    def forget(self, ticket_id: int) -> None:
        """Забыть кэш по заявке — звать после любой записи в GLPI."""
        if self._tabs_cache and self._tabs_cache[0] == ticket_id:
            self._tabs_cache = None
        self._idor_cache.pop(ticket_id, None)

    def get_ticket(self, ticket_id: int, refresh: bool = False) -> Ticket:
        """Fast detail: one request (~0.3 s, ~420 KB) -> description, actors, comments."""
        return self._parse_detail(self._tabs_html(ticket_id, refresh=refresh), ticket_id)

    def get_tickets(self, ids: Iterable[int]) -> dict[int, Ticket]:
        ids = list(ids)
        if not ids:
            return {}
        with ThreadPoolExecutor(max_workers=self.config.detail_workers) as pool:
            return {t.ticket_id: t for t in pool.map(self.get_ticket, ids)}

    def _parse_detail(self, page: str, ticket_id: int) -> Ticket:
        # "item" is plain JSON here; "actors" is a JS literal (bare keys, trailing
        # commas, \u0040 escapes) assigned WITHOUT the window. prefix in this build.
        item = extract_js_object(page, r"[,{\s]item:\s*(?=\{)") or {}
        actors = extract_js_object(page, r"(?:window\.)?actors\s*=\s*(?=\{)") or {}

        status = int(item.get("status") or 0)
        ticket = Ticket(
            ticket_id=ticket_id,
            name=self._strip(item.get("name") or ""),
            status=status, status_name=STATUS_NAMES.get(status, str(item.get("status", ""))),
            priority=str(item.get("priority", "")),
            created_at=str(item.get("date", "")), updated_at=str(item.get("date_mod", "")),
            solved_at=str(item.get("solvedate") or ""), closed_at=str(item.get("closedate") or ""),
            is_deleted=bool(int(item.get("is_deleted") or 0)),
            url=f"{self.config.base_url}/front/ticket.form.php?id={ticket_id}",
            raw_item=item,
        )
        ticket.description = self._timeline_description(page)
        ticket.comments = self._timeline_comments(page)
        # The JS "actors" blob has no display names; the actor <select> options do.
        opts = self._actor_options(page, "select-assign")
        if opts:
            ticket.assign_users = [a for a in opts if a["itemtype"] == "User"]
            ticket.assign_groups = [a for a in opts if a["itemtype"] == "Group"]
        else:
            ticket.assign_users = [a for a in actors.get("assign", [])
                                   if a.get("itemtype") == "User"]
            ticket.assign_groups = [a for a in actors.get("assign", [])
                                    if a.get("itemtype") == "Group"]
        for group in ticket.assign_groups:
            group.setdefault("name", "")
        return ticket

    @staticmethod
    def _actor_options(page: str, testid: str) -> list[dict[str, Any]]:
        """Actor <select> (data-testid=select-assign/-requester/-observer) holds
        itemtype, items_id, notification flag, e-mail and the display name."""
        block = re.search(r'<select[^>]*data-testid="%s"[^>]*>(.*?)</select>' % testid,
                          page, re.S)
        if not block:
            return []
        out: list[dict[str, Any]] = []
        for om in re.finditer(r"<option\b([^>]*)>(.*?)</option>", block.group(1), re.S):
            attrs, label = om.group(1), om.group(2)

            def attr(name: str) -> str:
                am = re.search(r'%s=["\']([^"\']*)["\']' % name, attrs)
                return am.group(1) if am else ""

            value = attr("value")
            if not value:
                continue
            out.append({
                "id": value,
                "itemtype": attr("data-itemtype"),
                "items_id": attr("data-items-id"),
                "use_notification": int(attr("data-use-notification") or 0),
                "default_email": attr("data-default-email"),
                "alternative_email": attr("data-alternative-email"),
                "name": html.unescape(re.sub(r"<[^>]+>", "", label)).strip(),
            })
        return out

    # ---------------- timeline ----------------
    @staticmethod
    def _blocks(page: str, testid: str) -> list[dict[str, str]]:
        out = []
        for m in re.finditer(r'<div[^>]*class="[^"]*timeline-item[^"]*"[^>]*>', page):
            tag = m.group(0)
            if testid not in tag:
                continue
            start = m.end()
            chunk = page[start:start + 40000]
            out.append({"tag": tag, "body": chunk})
        return out

    @staticmethod
    def _rich_text(chunk: str) -> str:
        m = re.search(r'<div[^>]*class="[^"]*rich_text_container[^"]*"[^>]*>', chunk)
        if not m:
            return ""
        body = chunk[m.end():]
        body = re.split(r"<div[^>]*class=\"[^\"]*timeline-item", body, maxsplit=1)[0]
        body = re.sub(r"<br\s*/?>", "\n", body)
        body = re.sub(r"</p>", "\n", body)
        body = re.sub(r"<li[^>]*>", "\n- ", body)
        text = re.sub(r"<[^>]+>", "", body)
        text = html.unescape(text).replace("\xa0", " ").replace("\u200b", "")
        # В исходном HTML между тегами — переносы и табы разметки; без
        # нормализации строк в чат уходят десятки пустых «строк»
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
        return "\n".join(line for line in lines if line).strip()

    def _timeline_description(self, page: str) -> str:
        for block in self._blocks(page, "timeline-ITILContent"):
            text = self._rich_text(block["body"])
            if text:
                return text
        return ""

    def _timeline_comments(self, page: str) -> list[dict[str, Any]]:
        """ITILFollowup blocks. Private/internal comments are right aligned
        (timeline-content t-right + ms-auto on the item) - verified on this box."""
        out: list[dict[str, Any]] = []
        for m in re.finditer(
                r'<div[^>]*class="([^"]*timeline-item[^"]*)"[^>]*'
                r'data-itemtype="ITILFollowup"[^>]*data-items-id="(\d+)"[^>]*>', page):
            cls, items_id = m.group(1), m.group(2)
            chunk = page[m.end():m.end() + 40000]
            text = self._rich_text(chunk)
            if not text:
                continue
            # Internal (private) comment: its OWN timeline-content is aligned
            # right (t-right). ms-auto also sits on unrelated inner elements, so
            # trust the block's timeline-content and only fall back to the class.
            content_cls = re.search(r'class="([^"]*timeline-content[^"]*)"', chunk)
            is_private = bool(content_cls and "t-right" in content_cls.group(1))
            if not content_cls:
                is_private = "ms-auto" in cls
            author, author_id = "", ""
            au = re.search(r'/front/user\.form\.php\?id=(\d+)"[^>]*>([^<]{2,60})<', chunk)
            if au:
                author_id, author = au.group(1), html.unescape(au.group(2)).strip()
            date = ""
            ad = re.search(r'datetime="([^"]+)"', chunk) or re.search(
                r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2})", chunk)
            if ad:
                date = ad.group(1)
            out.append({"comment_id": int(items_id), "author": author,
                        "author_id": author_id, "date": date,
                        "private": bool(is_private), "text": text})
        return out

    # ---------------- actors / assign ----------------
    @staticmethod
    def _actor_idors(page: str) -> list[str]:
        """IDOR-токены из конфигов ajax/actors.php.

        В карточке их несколько (по одному на актёра), рабочий — последний;
        с чужим токеном GLPI отвечает 200 и пустое тело.
        """
        out: list[str] = []
        for m in re.finditer("ajax/actors.php", page):
            hit = re.search("_idor_token.{0,4}?([0-9a-f]{20,})",
                          page[m.end():m.end() + 1500])
            if hit and hit.group(1) not in out:
                out.append(hit.group(1))
        return out

    @staticmethod
    def _find_idor(page: str) -> str:
        m = re.search("_idor_token.{0,4}?([0-9a-f]{20,})", page)
        return m.group(1) if m else ""

    def idor_pool(self, ticket_id: int) -> list[str]:
        """Все IDOR-токены актёров заявки (рабочий — последний)."""
        cached = self._idor_cache.get(ticket_id)
        if cached and time.time() - cached[1] < 600:
            return cached[0]
        pool = self._actor_idors(self._tabs_html(ticket_id))
        if not pool:
            raise RuntimeError("не нашли _idor_token в карточке заявки")
        self._idor_cache[ticket_id] = (pool, time.time())
        return pool

    def assign_candidates(self, ticket_id: int, search: str = "") -> list[dict[str, Any]]:
        """Кого можно назначить на заявку (люди, группы, поставщики)."""
        referer = self._url(f"/front/ticket.form.php?id={ticket_id}")
        detail = self.get_ticket(ticket_id)
        base: dict[str, Any] = {
            "action": "getActors", "actortype": "assign",
            # all | own_ticket | have_ticket
            "users_right": "own_ticket", "entity_restrict": 0, "searchText": search,
            "itiltemplate_class": "TicketTemplate", "itiltemplates_id": 1,
            "itemtype": "Ticket", "items_id": ticket_id,
            # без returned_itemtypes[] / page GLPI отвечает 200 с ПУСТЫМ телом
            "returned_itemtypes[]": ["User", "Group", "Supplier"],
            "page": 1,
        }
        # Session::validateIDOR() сверяет каждый item[...] с сохранённой заявкой,
        # поэтому item отправляем целиком (скаляры; вложенности теряются)
        for key, val in (detail.raw_item or {}).items():
            if isinstance(val, (dict, list)):
                continue
            base[f"item[{key}]"] = "" if val is None else val

        # Какой из конфигов актёров нужный — угадать нельзя, перебираем,
        # начиная с последнего (он обычно про исполнителя)
        for idor in reversed(self.idor_pool(ticket_id)):
            resp = self._post("/ajax/actors.php", dict(base, _idor_token=idor),
                              referer=referer, xhr=True)
            if resp.status_code != 200 or not resp.text.strip():
                continue
            try:
                payload = resp.json()
            except ValueError:
                LOGGER.warning("actors.php вернул не JSON: %s", resp.text[:200])
                return []
            return payload.get("results", []) or []
        LOGGER.warning("actors.php: ни один IDOR-токен не вернул данных")
        return []

    ACTOR_TYPES = ("requester", "assign", "observer")
    CACHE_TTL = 30

    def _actors_blob(self, ticket_id: int) -> dict[str, list[dict[str, Any]]]:
        """window.actors заявки: {'requester': [...], 'assign': [...], 'observer': [...]}.

        Меняя исполнителя, нельзя терять заявителя и наблюдателей — GLPI
        перезаписывает АКТОРОВ ЦЕЛИКОМ из поля _actors.
        """
        page = self._tabs_html(ticket_id)
        blob = extract_js_object(page, r"(?:window\.)?actors\s*=\s*(?=\{)") or {}
        out: dict[str, list[dict[str, Any]]] = {}
        for kind in self.ACTOR_TYPES:
            value = blob.get(kind)
            out[kind] = [dict(a) for a in value] if isinstance(value, list) else []
        return out

    @staticmethod
    def _actor(itemtype: str, items_id: Any, old: dict[str, Any] | None = None,
               email: str = "") -> dict[str, Any]:
        old = old or {}
        return {
            "itemtype": itemtype,
            "items_id": str(items_id),
            "use_notification": bool(old.get("use_notification", False)),
            "default_email": old.get("default_email") or email or "",
            "alternative_email": old.get("alternative_email") or "",
        }

    def set_assignee(self, ticket_id: int, users: Iterable[int | str],
                     groups: Iterable[int | str] = (),
                     suppliers: Iterable[int | str] = (),
                     disable_notifications: bool = True) -> bool:
        """Заменить список «Назначено» (users/groups/suppliers — items_id).

        GLPI 11 принимает актёров НЕ старыми полями _users_id_assign[...], а
        одним скрытым полем `_actors` с JSON {requester, assign, observer}.
        С прошлыми полями портал отвечает 302 и МОЛЧА ничего не меняет —
        проверить это можно только назначив ДРУГОГО человека (no-op тест ничего
        не доказывает).
        """
        users, groups, suppliers = list(users), list(groups), list(suppliers)
        blob = self._actors_blob(ticket_id)
        known: dict[str, dict[str, Any]] = {}
        for kind in self.ACTOR_TYPES:
            for actor in blob[kind]:
                known[f"{actor.get('itemtype')}_{actor.get('items_id')}"] = actor
        wanted = ([("User", u) for u in users] + [("Group", g) for g in groups]
                  + [("Supplier", s) for s in suppliers])
        emails: dict[str, str] = {}
        if any(kind == "User" and f"User_{i}" not in known for kind, i in wanted):
            for entry in self.assign_candidates(ticket_id):
                if entry.get("itemtype") == "User" and entry.get("email"):
                    emails[str(entry["items_id"])] = entry["email"]
        blob["assign"] = [self._actor(kind, items_id, known.get(f"{kind}_{items_id}"),
                                      emails.get(str(items_id), ""))
                          for kind, items_id in wanted]
        data: dict[str, Any] = {
            "id": ticket_id,
            "update": "Обновить",
            "_actors": json.dumps(blob, ensure_ascii=False),
            "_skip_default_actor": "1",
        }
        if disable_notifications:
            data["_disablenotif"] = "1"
        referer = self._url(f"/front/ticket.form.php?id={ticket_id}")
        resp = self._post("/front/ticket.form.php", data, referer=referer)
        if "Действие недоступно" in resp.text:
            LOGGER.warning("GLPI: правку #%s не приняли (CSRF или права)", ticket_id)
            return False
        # единственный честный признак успеха — перечитать заявку, причём
        # МИМО кэша: иначе увидим себя до правки и соврём оператору
        self.forget(ticket_id)
        want = sorted(f"{kind}_{items_id}" for kind, items_id in wanted)
        got: list[str] = []
        for attempt in range(4):
            if attempt:
                time.sleep(0.6)
            self.forget(ticket_id)
            after = self.get_ticket(ticket_id, refresh=True)
            got = sorted(f"{a.get('itemtype')}_{a.get('items_id')}"
                         for a in list(after.assign_users) + list(after.assign_groups))
            if got == want:
                return True
        LOGGER.warning("GLPI: назначено не применилось: хотели %s, стало %s",
                       want, got)
        return False
