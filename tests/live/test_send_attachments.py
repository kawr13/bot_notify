"""Живая проверка: заявка с вложениями -> в чат уходит текст + каждый файл.

    venv\\Scripts\\python.exe tests\\live\\test_send_attachments.py 2090

Скрипт ОТПРАВЛЯЕТ в чат по-настоящему (диалог берётся из .env, обычно это
личный диалог с ботом — проверь BITRIX_DIALOG_ID, если боишься засорить общий
чат). GLPI-заявку он не меняет, если в ней уже стоит человек: исполнитель
остаётся тот же, что был.
"""
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from dotenv import dotenv_values                       # noqa: E402

import tkinter as tk                                   # noqa: E402
from glpi_bridge import GlpiBridge                     # noqa: E402
from glpi_panel import GlpiWindow                      # noqa: E402

TID = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 2090

env = {k: str(v) for k, v in dotenv_values('.env').items() if v}
bridge = GlpiBridge(env=env)
bridge.monitor_config.state_path = pathlib.Path('data/glpi_state_probe.json')
bridge.monitor.cfg.state_path = bridge.monitor_config.state_path
bridge.client.login()

root = tk.Tk()
root.withdraw()
win = GlpiWindow(root, bridge)
record = bridge.client.get_ticket(TID, refresh=True)
print(f'заявка #{TID}: вложений {len(record.attachments)} ->',
      [(a["name"][:24], a["size"]) for a in record.attachments])
win._remember(record)
win._refresh_list()
win.listbox.selection_set(0)
win._on_select()
for _ in range(80):
    root.update()
    time.sleep(0.1)
    if len(win.choices) > 3:
        break
print('пометка окна :', win.files_note.cget('text'))
win.send_to_bot()
start = time.time()
while time.time() - start < 180:
    root.update()
    time.sleep(0.15)
    if 'отправлено' in win.status_var.get() or 'не отправлено' in win.status_var.get():
        break
print('статус       :', win.status_var.get())
print('итог         :', win.assign_note.cget('text'))
print('\nСпроси себя: видно ли в чате сами ФАЙЛЫ, а не только строчку «вложений'
      ' отправлено N»? Портал принимает multipart молча, проверить это глазами —')
print('единственный способ на этой коробке (imbot.v2.Chat.Message.list тут не читает).')
win.destroy()
root.destroy()
