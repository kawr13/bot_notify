#!/usr/bin/env python3
"""Отправка сообщений в чат Bitrix24 от имени бота (входящий вебхук + botToken).

Особенности портала (см. docs/bitrix24-bot.md):
  * бот зарегистрирован с eventMode=fetch — публичный URL не нужен;
  * параметр botToken обязателен в каждом вызове imbot.v2.*;
  * упомянуть человека можно только BB-кодом [USER=id]@Имя[/USER] — портал
    рендерит его обычным текстом, подсветки не будет (проверено на проде).
"""
from __future__ import annotations

import base64
import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable

import requests
import urllib3

urllib3.disable_warnings()

LOGGER = logging.getLogger("bot_notify.bitrix")


@dataclass
class Bitrix24Config:
    webhook: str = ""            # https://portal/rest/<user>/<secret>/
    bot_token: str = ""          # botToken из imbot.v2.bot.info
    bot_id: int = 0              # ID зарегистрированного бота
    dialog_id: str = ""          # "chat7018" — групповой чат, "81" — диалог с человеком
    dialog_alt: str = ""         # второй адрес для переключателя в окне
    timeout: int = 25
    verify_ssl: bool = True

    @classmethod
    def from_env(cls, env: dict[str, str]) -> "Bitrix24Config":
        def clean(value: str) -> str:
            return (value or "").strip().strip('"').strip("'")

        return cls(
            webhook=clean(env.get("BITRIX_WEBHOOK", "")),
            bot_token=clean(env.get("BITRIX_BOT_TOKEN", "")),
            bot_id=int(clean(env.get("BITRIX_BOT_ID", "0")) or 0),
            dialog_id=clean(env.get("BITRIX_DIALOG_ID", "")),
            dialog_alt=clean(env.get("BITRIX_DIALOG_ID_ALT", "")),
            timeout=int(clean(env.get("BITRIX_TIMEOUT", "25")) or 25),
            verify_ssl=clean(env.get("BITRIX_VERIFY_SSL", "1")) not in ("0", "false", "no"),
        )

    @property
    def ready(self) -> bool:
        return bool(self.webhook and self.bot_token and self.bot_id and self.dialog_id)


class Bitrix24Client:
    """Потонобезопасный: requests.Session потокоустойчив для наших сценариев,
    но блокировку на отправку всё же держим — порядок сообщений важнее скорости."""

    def __init__(self, config: Bitrix24Config) -> None:
        self.config = config
        self.session = requests.Session()
        self.session.verify = config.verify_ssl
        self._lock = threading.Lock()
        self._email_cache: dict[str, int | None] = {}
        self._title_cache: dict[str, str] = {}

    # ---------------- low level ----------------
    def call(self, method: str, **params: Any) -> dict[str, Any]:
        if not self.config.webhook:
            return {"error": "BITRIX_WEBHOOK не задан"}
        payload = {k: v for k, v in params.items() if v is not None}
        if method.lower().startswith("imbot") and self.config.bot_token:
            payload.setdefault("botToken", self.config.bot_token)
        try:
            resp = self.session.post(
                f"{self.config.webhook.rstrip('/')}/{method}.json",
                data=payload, timeout=self.config.timeout)
        except requests.RequestException as exc:
            return {"error": f"сеть: {exc}"}
        try:
            return resp.json()
        except ValueError:
            return {"error": f"не JSON (HTTP {resp.status_code}): {resp.text[:200]}"}

    # ---------------- high level ----------------
    def send(self, text: str, dialog_id: str | None = None) -> tuple[bool, str]:
        """Отправить текст в чат. Возвращает (ok, комментарий/ошибка)."""
        dialog = str(dialog_id or self.config.dialog_id or "")
        if not self.config.ready:
            LOGGER.info("Bitrix24 не настроен, сообщение пропущено: %s", text[:80])
            return False, "Bitrix24 не настроен"
        if not dialog:
            return False, "не указан dialogId"
        data = {
            "botId": self.config.bot_id,
            "dialogId": dialog,
            "fields[message]": text,
            "fields[urlPreview]": "N",
        }
        with self._lock:
            result = self.call("imbot.v2.Chat.Message.send", **data)
        if result.get("error") or result.get("error_description"):
            message = str(result.get("error_description") or result.get("error"))
            LOGGER.warning("Bitrix24: сообщение не отправлено: %s", message)
            return False, message
        chat_id = result.get("result")
        # портал отвечает либо id числом, либо {'id': 420626, 'uuidMap': []}
        if isinstance(chat_id, dict):
            chat_id = chat_id.get("id")
        if not isinstance(chat_id, (int, str)) or chat_id in (0, "0", False):
            # бот обязан состоять в чате, иначе портал вернёт 0
            LOGGER.warning("Bitrix24: странный ответ %s", str(result)[:160])
            return False, f"портал ответил: {str(result)[:120]}"
        LOGGER.info("Bitrix24: сообщение отправлено в %s (id %s)", dialog, chat_id)
        return True, str(chat_id)


    def call_json(self, method: str, body: dict[str, Any]) -> dict[str, Any]:
        """Вызов с телом JSON: загрузка файла принимает только так."""
        if not self.config.webhook:
            return {"error": "BITRIX_WEBHOOK не задан"}
        try:
            resp = self.session.post(
                f"{self.config.webhook.rstrip('/')}/{method}.json",
                json=body, timeout=max(self.config.timeout, 120))
        except requests.RequestException as exc:
            return {"error": f"сеть: {exc}"}
        try:
            return resp.json()
        except ValueError:
            return {"error": f"не JSON (HTTP {resp.status_code}): {resp.text[:200]}"}

    def send_file(self, blob: bytes, filename: str, text: str = "",
                  dialog_id: str | None = None) -> tuple[bool, str]:
        """Загрузить файл в чат — методом `im.v2.File.upload`.

        Multipart-частью файл НЕ уходит: портал молча игнорирует незнакомые
        поля и присылает сообщение без вложения (проверено на проде).
        Метод сам делает три шага: загрузка на диск, прикрепление к чату,
        отправка сообщения. Содержимое — base64 в fields.content, до 100 МБ.

        Scope — `im`, работает под владельцем вебхука, поэтому в чате файл
        появляется ОТ ИМЕНИ ПОЛЬЗОВАТЕЛЯ, а не бота: иначе коробка не умеет.
        """
        dialog = str(dialog_id or self.config.dialog_id or "")
        if not self.config.ready:
            return False, "Bitrix24 не настроен"
        if not dialog:
            return False, "не указан dialogId"
        if len(blob) > 100 * 1024 * 1024:
            return False, "файл больше 100 МБ (лимит метода)"
        body = {
            "dialogId": dialog,
            "fields": {
                "name": filename,
                "content": base64.b64encode(blob).decode("ascii"),
                "message": (text or filename)[:2000],
            },
        }
        with self._lock:
            result = self.call_json("im.v2.File.upload", body)
        if result.get("error") or result.get("error_description"):
            code = str(result.get("error") or "")
            message = str(result.get("error_description") or code)
            if "scope" in message.lower() or code == "insufficient_scope":
                message = ('вебхуку не разрешены «Чаты» (scope im) — включите '
                           'это право в настройках входящего вебхука')
            LOGGER.warning("Bitrix24: файл %s не загружен: %s", filename, message)
            return False, message
        res = result.get("result")
        mid = ""
        if isinstance(res, dict):
            mid = str(res.get("messageId") or res.get("id") or "")
        elif isinstance(res, (int, str)):
            mid = str(res)
        if not mid and not res:
            return False, f"портал ответил: {str(result)[:120]}"
        LOGGER.info("Bitrix24: файл %s загружен в %s (message %s)",
                    filename, dialog, mid or "—")
        return True, mid or "ok"

    def send_async(self, text: str, dialog_id: str | None = None,
                   on_done: Callable[[bool, str], None] | None = None) -> None:
        def worker() -> None:
            try:
                ok, info = self.send(text, dialog_id)
            except Exception as exc:              # поток не должен умищать GUI
                LOGGER.exception("Bitrix24: сбой отправки: %s", exc)
                ok, info = False, str(exc)
            if on_done:
                try:
                    on_done(ok, info)
                except Exception:
                    LOGGER.exception("Bitrix24: сбой колбэка отправки")
        threading.Thread(target=worker, name="b24-send", daemon=True).start()

    def user_by_email(self, email: str) -> int | None:
        """Bitrix24 USER_ID по почте (нужен скоуп user.get; иначе None)."""
        email = (email or "").strip().lower()
        if not email:
            return None
        with self._lock:
            cache = self._email_cache
        if email in cache:
            return cache[email]
        found: int | None = None
        result = self.call("user.get", **{"select": ["ID", "EMAIL"],
                                         "filter": f"{email}", "limit": 5})
        rows = result.get("result")
        if isinstance(rows, list):
            for row in rows:
                if str(row.get("ID", "")).isdigit():
                    found = int(row["ID"])
                    break
        with self._lock:
            cache[email] = found
        return found

    def chat_title(self, dialog_id: str) -> str:
        """Название группового чата по `dialogId`, чтобы подписывать адресата именем.

        Личный диалог (числовой `dialogId`) проходит мимо: имени там нет, и
        подпись остаётся прежней. Значение кэшируется, в том числе пустое —
        иначе на каждый перерисованный список ушёл бы новый запрос.
        """
        value = str(dialog_id or "").strip()
        if not value.startswith("chat"):
            return ""
        with self._lock:
            if value in self._title_cache:
                return self._title_cache[value]
        result = self.call("im.v2.Chat.get", chatId=value[4:])
        body = result.get("result")
        chat = body.get("chat") if isinstance(body, dict) else None
        name = str((chat or {}).get("name") or "").strip()
        with self._lock:
            self._title_cache[value] = name
        return name


    def ping(self) -> tuple[bool, str]:
        """Проверка транспорта: список ботов портала."""
        if not self.config.webhook:
            return False, "BITRIX_WEBHOOK не задан"
        result = self.call("imbot.v2.Bot.list")
        if result.get("error"):
            return False, str(result.get("error_description") or result.get("error"))
        bots = result.get("result") or []
        return True, f"ботов на портале: {len(bots)}"


def mention(user_id: int | str, name: str) -> str:
    """BB-код упоминания. Портал показывает текст, но ссылка остаётся."""
    return f"[USER={user_id}]@{name}[/USER]"
