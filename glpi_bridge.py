#!/usr/bin/env python3
"""Склейка GLPI <-> Bitrix24: клиент GLPI, монитор, клиент Bitrix24, общая очередь."""
from __future__ import annotations

import logging
import queue
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bitrix24_client import Bitrix24Client, Bitrix24Config
from glpi_client import GlpiClient, GlpiConfig
from glpi_monitor import GlpiMonitor, MonitorConfig

LOGGER = logging.getLogger("bot_notify.glpi")


@dataclass
class GlpiBridge:
    env: dict[str, str]
    queue: "queue.Queue[tuple[str, Any]]" = field(default_factory=queue.Queue)
    client: GlpiClient = None                      # type: ignore[assignment]
    bitrix: Bitrix24Client = None                  # type: ignore[assignment]
    monitor: GlpiMonitor = None                    # type: ignore[assignment]
    base_url: str = ""
    started: bool = False

    def __post_init__(self) -> None:
        self.glpi_config = GlpiConfig.from_env(self.env)
        self.b24_config = Bitrix24Config.from_env(self.env)
        self.monitor_config = MonitorConfig.from_env(self.env)
        if self.monitor_config.state_path is None:
            self.monitor_config.state_path = Path("data/glpi_state.json")
        self.client = GlpiClient(self.glpi_config)
        self.bitrix = Bitrix24Client(self.b24_config)
        self.base_url = self.glpi_config.base_url
        self.monitor = GlpiMonitor(self.client, self.queue, self.monitor_config,
                                   sender=self.enqueue_chat,
                                   mention_resolver=self.bitrix.user_by_email)

    # ---------------- отправка ----------------
    def enqueue_chat(self, text: str) -> None:
        """Монитор отдаёт сюда авто-актуализацию; уходим в чат асинхронно."""
        self.bitrix.send_async(text)

    def mention_for(self, pick: dict[str, Any]) -> str:
        """Упоминание выбранного в GUI исполнителя (для ручных отправок)."""
        label = (pick.get("label") or "").strip()
        if not label:
            return ""
        uid = None
        if pick.get("itemtype") == "User" and pick.get("email"):
            uid = self.bitrix.user_by_email(pick["email"])
        if uid:
            return f"К исполнителю: [USER={uid}]@{label}[/USER]"
        return f"К исполнителю: @{label}"

    # ---------------- жизненный цикл ----------------
    def start(self) -> bool:
        if self.started:
            return True
        try:
            self.client.login()
        except Exception as exc:
            LOGGER.error("GLPI: вход не удался — %s", exc)
            self.queue.put_nowait(("glpi_status", f"GLPI: вход не удался — {exc}"))
            return False
        if not self.b24_config.ready:
            self.queue.put_nowait(("glpi_status",
                                   "Bitrix24 не настроен — заявки копим, не отправляя"))
        self.monitor.start()
        self.started = True
        return True

    def stop(self) -> None:
        if self.started:
            self.monitor.stop()
            self.monitor.join(timeout=5)
            self.started = False

    def status(self) -> str:
        ok_bitrix, info_bitrix = (False, "не настроен")
        if self.b24_config.ready:
            ok_bitrix, info_bitrix = self.bitrix.ping()
        return (f"GLPI {self.base_url} | Bitrix24: "
                f"{'ок' if ok_bitrix else info_bitrix} | "
                f"чат {self.b24_config.dialog_id or '—'}")
