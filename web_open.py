"""Открыть ссылку в браузере — и не зависнуть.

`webbrowser.open()` на Windows достаёт команду обработчика http/s из реестра и
ЗАПУСКАЕТ ЕЁ С ОЖИДАНИЕМ (GenericBrowser → subprocess.Popen(...).wait()). Если
обработчик — обычный браузер (ChromeHTML), процесс живёт до закрытия браузера,
и вызов не возвращается НИКОГДА. Вызывается он из кнопки, то есть из главного
потока Tkinter: окно замирает, а выглядит это ровно как «кнопка не работает».

os.startfile — это ShellExecute: вернул управление и свободен.
"""

from __future__ import annotations

import logging
import os
import sys

LOGGER = logging.getLogger(__name__)


def open_url(url: str) -> tuple[bool, str]:
    """Правда — браузер открыли. Иначе — причина для сообщения оператору."""
    if not url:
        return False, "пустая ссылка"
    try:
        if sys.platform.startswith("win"):
            os.startfile(url)                       # type: ignore[attr-defined]
            return True, ""
        import webbrowser                           # не для Windows — там всё ок
        ok = webbrowser.open(url)
        return bool(ok), "" if ok else "браузер не найден"
    except Exception as exc:                        # noqa: BLE001
        LOGGER.warning("не удалось открыть %s: %s", url, exc)
        return False, str(exc)
