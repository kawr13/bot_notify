#!/usr/bin/env python3
"""Telegram Notifier module for Bot Notify Desktop."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import requests

LOGGER = logging.getLogger("bot_notify.telegram")


class TelegramNotifier:
    """Telegram notification sender."""

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        users_path: Path | None = None,
    ) -> None:
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.users_path = users_path or Path("users.json")
        self.enabled = bool(self.bot_token and self.chat_id)
        self._users: list[int] = []
        self._load_users()

    def _load_users(self) -> None:
        """Load Telegram users from file."""
        if not self.users_path.exists():
            return
        try:
            payload = json.loads(self.users_path.read_text(encoding="utf-8"))
            users_list = payload.get("users_list", [])
            if isinstance(users_list, list):
                self._users = [int(x) for x in users_list if str(x).strip()]
        except (json.JSONDecodeError, ValueError, IOError) as exc:
            LOGGER.error("Failed to load Telegram users: %s", exc)

    def get_users(self) -> list[int]:
        """Get Telegram user IDs."""
        return self._users

    def add_user(self, user_id: int) -> bool:
        """Add user ID."""
        if user_id in self._users:
            return False
        self._users.append(user_id)
        self._save_users()
        return True

    def remove_user(self, user_id: int) -> bool:
        """Remove user ID."""
        if user_id not in self._users:
            return False
        self._users.remove(user_id)
        self._save_users()
        return True

    def _save_users(self) -> None:
        """Save users to file."""
        try:
            with open(self.users_path, "w", encoding="utf-8") as f:
                json.dump({"users_list": self._users}, f, ensure_ascii=False, indent=2)
        except IOError as exc:
            LOGGER.error("Failed to save Telegram users: %s", exc)

    def detect_telegram_proxy(self) -> str | None:
        """Detect Telegram proxy from environment or auto-detect."""
        proxy = os.getenv("TELEGRAM_PROXY", "").strip()
        if proxy:
            return proxy

        candidates = [
            "http://127.0.0.1:7890",
            "http://127.0.0.1:7897",
            "http://127.0.0.1:8080",
            "http://127.0.0.1:9090",
        ]
        for candidate in candidates:
            parsed = requests.utils.urlparse(candidate)
            if parsed.hostname and parsed.port:
                try:
                    import socket
                    with socket.create_connection(
                        (parsed.hostname, parsed.port), timeout=0.5
                    ):
                        LOGGER.info("Auto-detected Telegram proxy: %s", candidate)
                        return candidate
                except OSError:
                    continue
        return None

    def format_message(self, record: Any) -> str:
        lines = [
            f"📧 <b>{self._escape_html(record.subject)}</b>",
            f"<i>{self._escape_html(record.sender)}</i>",
        ]
        # Helpdesk ticket fields
        if getattr(record, "ticket_id", None):
            lines.append(f"<b>Заявка №{record.ticket_id}</b>: {self._escape_html(record.ticket_title or '')}")
        if getattr(record, "ticket_authors", None):
            author = self._escape_html(record.ticket_authors)
            if getattr(record, "ticket_user_email", None):
                author += f" ({self._escape_html(record.ticket_user_email)})"
            lines.append(f"<b>Автор:</b> {author}")
        if getattr(record, "ticket_creationdate", None):
            lines.append(f"<b>Дата:</b> {self._escape_html(record.ticket_creationdate)}")
        if getattr(record, "ticket_category", None):
            lines.append(f"<b>Категория:</b> {self._escape_html(record.ticket_category)}")
        if getattr(record, "ticket_priority", None):
            lines.append(f"<b>Приоритет:</b> {self._escape_html(record.ticket_priority)}")
        if getattr(record, "ticket_url", None):
            lines.append(f"<b>Ссылка:</b> <a href=\"{record.ticket_url}\">открыть</a>")
        lines.append("")
        lines.append(f"<b>Контракты:</b> {', '.join(record.contracts[:3]) if record.contracts else 'нет'}")
        lines.append(f"<b>ИНН:</b> {', '.join(record.inns[:3]) if record.inns else 'нет'}")
        lines.append(f"<b>TicketID:</b> {', '.join(record.ticket_ids[:3]) if record.ticket_ids else 'нет'}")
        if record.links:
            lines.append("")
            lines.append("<b>Ссылки:</b>")
            for link in record.links[:3]:
                lines.append(f"  • <a href=\"{link}\">{link}</a>")
        return "\n".join(lines)
        """Format notification for Telegram."""
        lines = [
            f"📧 <b>{self._escape_html(record.subject)}</b>",
            f"<i>{self._escape_html(record.sender)}</i>",
            "",
            f"<b>Контракты:</b> {', '.join(record.contracts[:3]) if record.contracts else 'нет'}",
            f"<b>ИНН:</b> {', '.join(record.inns[:3]) if record.inns else 'нет'}",
            f"<b>TicketID:</b> {', '.join(record.ticket_ids[:3]) if record.ticket_ids else 'нет'}",
        ]
        if record.links:
            lines.append("")
            lines.append("<b>Ссылки:</b>")
            for link in record.links[:3]:
                lines.append(f"  • <a href=\"{link}\">{link}</a>")

        return "\n".join(lines)

    def _escape_html(self, text: str) -> str:
        """Escape HTML special characters."""
        if not text:
            return ""
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    async def send_async(self, record: Any) -> bool:
        """Send notification to Telegram asynchronously."""
        if not self.enabled:
            LOGGER.warning("Telegram notifier is disabled")
            return False

        message = self.format_message(record)
        max_length = 4096
        if len(message) > max_length:
            message = message[: max_length - 3] + "..."

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        proxy = self.detect_telegram_proxy()
        proxies = None
        if proxy:
            proxies = {"https": proxy, "http": proxy}

        try:
            response = await asyncio.to_thread(
                requests.post,
                url,
                json=payload,
                timeout=30,
                proxies=proxies,
            )
            result = response.json()

            if not result.get("ok"):
                LOGGER.error(
                    "Telegram API error: %s",
                    result.get("description", "unknown error"),
                )
                return False

            LOGGER.info("Telegram notification sent")
            return True

        except requests.RequestException as exc:
            LOGGER.error("Failed to send Telegram notification: %s", exc)
            return False

    def send(self, record: Any) -> bool:
        """Send notification to Telegram synchronously."""
        import asyncio

        return asyncio.run(self.send_async(record))

    def send_to_all_users(self, record: Any) -> list[bool]:
        """Send notification to all registered users."""
        results = []
        for user_id in self._users:
            result = self.send_to_user(user_id, record)
            results.append(result)
        return results

    def send_to_user(self, user_id: int, record: Any) -> bool:
        """Send notification to specific user."""
        if not self.bot_token:
            return False

        message = self.format_message(record)
        max_length = 4096
        if len(message) > max_length:
            message = message[: max_length - 3] + "..."

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": str(user_id),
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        proxy = self.detect_telegram_proxy()
        proxies = None
        if proxy:
            proxies = {"https": proxy, "http": proxy}

        try:
            response = requests.post(
                url,
                json=payload,
                timeout=30,
                proxies=proxies,
            )
            result = response.json()

            if not result.get("ok"):
                LOGGER.error(
                    "Telegram API error for user %s: %s",
                    user_id,
                    result.get("description", "unknown error"),
                )
                return False

            LOGGER.info("Telegram notification sent to user %s", user_id)
            return True

        except requests.RequestException as exc:
            LOGGER.error(
                "Failed to send Telegram notification to user %s: %s",
                user_id,
                exc,
            )
            return False
