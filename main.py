#!/usr/bin/env python3
"""Bot Notify Desktop - Main application (refactored)."""

from __future__ import annotations

import configparser
import json
import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import dotenv_values, load_dotenv

from gui_app import BotNotifyDesktopApp
from imap_monitor import (
    AppConfig,
    RuntimeStore,
    decode_mime_text,
    detect_local_http_proxy_url,
    write_json,
)
from vk_notifier import VkNotifier

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
STATE_PATH = DATA_DIR / "state.json"
HISTORY_PATH = DATA_DIR / "notifications_history.json"
VK_RECIPIENTS_PATH = DATA_DIR / "vk_recipients.json"
APP_SETTINGS_PATH = DATA_DIR / "app_settings.json"
CONFIG_JSON_PATH = BASE_DIR / "config.json"
SOUND_PATH = BASE_DIR / "sound.wav"

DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)


def configure_logging() -> logging.Logger:
    """Configure application logging."""
    logger = logging.getLogger("bot_notify")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(threadName)s | %(message)s"
    )

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "application.log",
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


LOGGER = configure_logging()


def main() -> None:
    """Main application entry point."""
    try:
        config = AppConfig.load()
    except Exception as exc:
        LOGGER.exception("Ошибка загрузки конфигурации: %s", exc)
        if sys.platform == "win32":
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Bot Notify Desktop", f"Не удалось загрузить конфиг:\n{exc}")
            root.destroy()
        return

    LOGGER.info(
        "Запуск приложения для %s через %s:%s",
        config.imap_username,
        config.imap_server,
        config.imap_port,
    )

    store = RuntimeStore(STATE_PATH, HISTORY_PATH)
    vk_notifier = VkNotifier()

    # GLPI -> Bitrix24. Ключи берём из окружения, а не из config.ini
    env: dict[str, str] = {k: str(v) for k, v in os.environ.items() if v is not None}
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        env.update({k: str(v) for k, v in dotenv_values(env_file).items() if v})
    glpi = None
    if env.get("GLPI_URL") and env.get("GLPI_USER"):
        try:
            from glpi_bridge import GlpiBridge
            glpi = GlpiBridge(env=env)
            LOGGER.info("GLPI: мост собран, %s", glpi.base_url)
        except Exception as exc:
            LOGGER.exception("GLPI: мост не собран: %s", exc)
    else:
        LOGGER.info("GLPI: GLPI_URL/GLPI_USER не заданы, модуль заявок отключён")

    app = BotNotifyDesktopApp(
        config=config,
        store=store,
        vk_notifier=vk_notifier,
        glpi=glpi,
    )
    app.run()


if __name__ == "__main__":
    main()
