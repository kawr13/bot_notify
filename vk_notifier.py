#!/usr/bin/env python3
"""VK Notifier module for Bot Notify Desktop."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import requests

from app import get_vk_token

LOGGER = logging.getLogger("bot_notify.vk")
VK_RECIPIENTS_PATH = Path("data/vk_recipients.json")


class VkNotifier:
    """VKontakte notification sender."""

    API_URL = "https://api.vk.com/method"
    API_VERSION = "5.199"

    def __init__(self) -> None:
        self.token = get_vk_token()
        self.group_id = os.getenv("VK_GROUP_ID", "").strip() or None
        env_peer_ids = self._parse_int_list(os.getenv("VK_PEER_IDS", ""))
        file_peer_ids = self._load_saved_peer_ids()
        self.peer_ids = sorted(set(env_peer_ids) | set(file_peer_ids))
        self._lock = threading.Lock()
        self._member_cache: list[int] | None = None

    @property
    def enabled(self) -> bool:
        with self._lock:
            has_peers = bool(self.peer_ids)
        return bool(self.token and (has_peers or self.group_id))

    def send_async(self, record: Any) -> bool:
        """Send notification asynchronously."""
        if not self.enabled:
            return False
        threading.Thread(
            target=self.send,
            args=(record,),
            name="vk-send",
            daemon=True,
        ).start()
        return True

    def send(self, record: Any) -> None:
        """Send notification synchronously."""
        if not self.enabled or not self.token:
            return

        recipients = self._resolve_recipients()
        if not recipients:
            LOGGER.warning("VK включен, но не найдено получателей.")
            return

        message = self._format_message(record)
        for index, peer_id in enumerate(recipients):
            payload = {
                "peer_id": peer_id,
                "message": message,
                "random_id": int(time.time() * 1000) + index,
                "access_token": self.token,
                "v": self.API_VERSION,
            }
            try:
                response = requests.post(
                    f"{self.API_URL}/messages.send",
                    data=payload,
                    timeout=15,
                )
                response.raise_for_status()
                data = response.json()
                if "error" in data:
                    error_info = data["error"]
                    LOGGER.warning(
                        "VK не отправил сообщение peer_id=%s: %s",
                        peer_id,
                        error_info.get("error_msg", "unknown error"),
                    )
                time.sleep(0.04)
            except requests.RequestException as exc:
                LOGGER.warning("Не удалось отправить уведомление в VK: %s", exc)
                break

    def _resolve_recipients(self) -> list[int]:
        with self._lock:
            recipients = set(self.peer_ids)
        recipients.update(self._fetch_group_members())
        return sorted(recipients)

    def get_peer_ids(self) -> list[int]:
        with self._lock:
            return list(self.peer_ids)

    def set_peer_ids(self, peer_ids: list[int]) -> None:
        cleaned = sorted({int(item) for item in peer_ids if int(item) != 0})
        with self._lock:
            self.peer_ids = cleaned
        write_json(VK_RECIPIENTS_PATH, {"peer_ids": cleaned})

    def _load_saved_peer_ids(self) -> list[int]:
        if not VK_RECIPIENTS_PATH.exists():
            return []
        try:
            payload = json.loads(VK_RECIPIENTS_PATH.read_text(encoding="utf-8"))
            raw_ids = payload.get("peer_ids", []) if isinstance(payload, dict) else payload
            return sorted(
                {
                    int(item)
                    for item in raw_ids
                    if str(item).strip() and int(item) != 0
                }
            )
        except (ValueError, TypeError, json.JSONDecodeError):
            LOGGER.warning("vk_recipients.json поврежден")
            return []

    def _fetch_group_members(self) -> list[int]:
        if not self.token or not self.group_id:
            return []
        if self._member_cache is not None:
            return self._member_cache

        members: list[int] = []
        offset = 0
        while True:
            params = {
                "group_id": self.group_id,
                "offset": offset,
                "count": 1000,
                "access_token": self.token,
                "v": self.API_VERSION,
            }
            response = requests.get(f"{self.API_URL}/groups.getMembers", params=params, timeout=20)
            response.raise_for_status()
            data = response.json()
            if "error" in data:
                LOGGER.warning("VK groups.getMembers failed: %s", data["error"].get("error_msg"))
                break

            items = data.get("response", {}).get("items", [])
            if not items:
                break
            members.extend(int(item) for item in items)
            if len(items) < 1000:
                break
            offset += 1000

        self._member_cache = sorted(set(members))
        if self._member_cache:
            LOGGER.info("VK: найдено участников: %s", len(self._member_cache))
        return self._member_cache

    @staticmethod
    def _format_message(record: Any) -> str:
        lines = [
            f"Новая почта: {record.subject}",
            f"От: {record.sender}",
        ]
        # Helpdesk ticket fields
        if getattr(record, "ticket_id", None):
            lines.append(f"Заявка №{record.ticket_id}: {record.ticket_title or ''}")
        if getattr(record, "ticket_authors", None):
            author = record.ticket_authors
            if getattr(record, "ticket_user_email", None):
                author += f" ({record.ticket_user_email})"
            lines.append(f"Автор: {author}")
        if getattr(record, "ticket_creationdate", None):
            lines.append(f"Дата: {record.ticket_creationdate}")
        if getattr(record, "ticket_category", None):
            lines.append(f"Категория: {record.ticket_category}")
        if getattr(record, "ticket_priority", None):
            lines.append(f"Приоритет: {record.ticket_priority}")
        if getattr(record, "ticket_url", None):
            lines.append(f"Ссылка: {record.ticket_url}")
        lines.append("")
        lines.append(record.preview)
        if record.contracts:
            lines.extend(["", "Контракты:", *record.contracts[:3]])
        if record.inns:
            lines.extend(["", "ИНН:", *record.inns[:3]])
        if record.ticket_ids:
            lines.extend(["", "TicketID:", *record.ticket_ids[:3]])
        if record.links:
            lines.extend(["", "Ссылки:", *record.links[:3]])
        return "\n".join(lines).strip()

    @staticmethod
    def _parse_int_list(raw_value: str) -> list[int]:
        if not raw_value:
            return []
        result: list[int] = []
        for chunk in raw_value.split(","):
            item = chunk.strip()
            if not item:
                continue
            try:
                result.append(int(item))
            except ValueError:
                LOGGER.warning("Не удалось разобрать peer_id: %s", item)
        return result


def write_json(path: Path, payload: Any) -> None:
    """Write JSON atomically using temp file."""
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)
