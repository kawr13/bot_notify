"""Сквозная проверка кнопки «Отправить в чат» по-настоящему."""
import sys, time, pathlib
sys.path.insert(0, '.')
from dotenv import dotenv_values
from glpi_bridge import GlpiBridge
import tkinter as tk
from glpi_panel import GlpiWindow

env = {k: str(v) for k, v in dotenv_values('.env').items() if v}
b = GlpiBridge(env=env)
b.monitor_config.state_path = pathlib.Path('data/glpi_state_probe.json')
b.monitor.cfg.state_path = b.monitor_config.state_path
b.client.login()
root = tk.Tk(); root.withdraw()
w = GlpiWindow(root, b)
rows = [r for r in b.monitor.rows() if r.is_active][:6]
rec = b.client.get_ticket(rows[4].ticket_id)
print(f'заявка #{rec.ticket_id}: назначено={rec.assignee_names!r}')
w._remember(rec); w._refresh_list()
w.listbox.selection_set(0); w._on_select()
for _ in range(60):
    root.update(); time.sleep(0.1)
    if len(w.choices) > 5:
        break
print('поле:', repr(w.assign_var.get()), '| _busy =', w._busy)
t0 = time.time()
w.send_to_bot()
print('send_to_bot() вызвана, _busy =', w._busy)
for _ in range(220):
    root.update(); time.sleep(0.1)
    s = w.status_var.get()
    if 'отправлено' in s or 'не отправлено' in s or time.time() - t0 > 20:
        break
print('статус :', w.status_var.get())
print('пометка:', w.assign_note.cget('text'))
print('_busy  :', w._busy, '| состояние кнопки:', w.chat_btn.instate(['disabled']))
d = b.client.get_ticket(rec.ticket_id, refresh=True)
print('в GLPI сейчас:', [u['name'] for u in d.assign_users], [g['name'] for g in d.assign_groups])
w.destroy(); root.destroy()
