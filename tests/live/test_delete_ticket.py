"""Живая проверка кнопки «Удалить заявку» (GLPI).

ЗАПУСКАЙ ТОЛЬКО С НОМЕРОМ заявки, которую НЕ ЖАЛКО:

    venv\\Scripts\\python.exe tests\\live\\test_delete_ticket.py 2851

Без номера скрипт ничего не удаляет. Сначала проверяет, что отказ ничего не
меняет, потом — что подтверждение удаляет заявку из GLPI и из окна.
GLPI без права PURGE делает мягкое удаление: восстановить может админ.
"""
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from dotenv import dotenv_values                       # noqa: E402

import glpi_panel                                      # noqa: E402
import tkinter as tk                                   # noqa: E402
from glpi_bridge import GlpiBridge                     # noqa: E402
from glpi_panel import GlpiWindow                      # noqa: E402

if len(sys.argv) < 2 or not sys.argv[1].isdigit():
    raise SystemExit("нужен номер заявки, которую можно удалить: "
                     "python tests/live/test_delete_ticket.py 2851")
TID = int(sys.argv[1])

env = {k: str(v) for k, v in dotenv_values('.env').items() if v}
bridge = GlpiBridge(env=env)
bridge.monitor_config.state_path = pathlib.Path('data/glpi_state_probe.json')
bridge.monitor.cfg.state_path = bridge.monitor_config.state_path
bridge.client.login()

root = tk.Tk()
root.withdraw()
win = GlpiWindow(root, bridge)
win._remember(bridge.client.get_ticket(TID, refresh=True))
win._refresh_list()
win.listbox.selection_set(0)
win._on_select()


def pump(seconds: float, until) -> str:
    start = time.time()
    while time.time() - start < seconds:
        root.update()
        time.sleep(0.15)
        if until(win.status_var.get()):
            break
    return win.status_var.get()


# --- отказ удалять ---------------------------------------------------------
answers = iter([False])
glpi_panel.messagebox.askyesno = lambda *a, **k: next(answers)
win.delete_now()
for _ in range(20):
    root.update()
    time.sleep(0.1)
print('после отказа :', 'в списке' if TID in win.records else 'ПРОПАЛА БЕЗ СПРОСА')

# --- подтверждаем ----------------------------------------------------------
answers = iter([True])
glpi_panel.messagebox.askyesno = lambda *a, **k: next(answers)
win.delete_now()
print('статус       :', pump(60, lambda s: 'удалена' in s or 'не удалил' in s))
print('в окне       :', 'есть' if TID in win.records else 'убрана из списка')
print('карточка     :', repr(win.ticket_var.get()))
time.sleep(1)
print('в GLPI списке:', any(t.ticket_id == TID for t in bridge.client.list_tickets(rows=60)))
win.destroy()
root.destroy()
