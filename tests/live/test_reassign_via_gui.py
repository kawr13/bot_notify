"""GUI-проверка: выбрать ДРУГОГО исполнителя и отправить в чат."""
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
rec = b.client.get_ticket(2842)
print('до      :', [u['name'] for u in rec.assign_users], [g['name'] for g in rec.assign_groups])
w._remember(rec); w._refresh_list()
w.listbox.selection_set(0); w._on_select()
for _ in range(80):
    root.update(); time.sleep(0.1)
    if len(w.choices) > 5:
        break
target = next((c for c in w.choices if c['itemtype'] == 'User'
               and str(c['items_id']) == '24'), None)
print('выбираю :', target['label'] if target else 'НЕТ ТАКОГО В СПИСКЕ')
w.assign_var.set(target['label'])
w.send_to_bot()
t0 = time.time()
for _ in range(400):
    root.update(); time.sleep(0.1)
    s = w.status_var.get()
    if 'отправлено' in s or 'не отправлено' in s or time.time() - t0 > 35:
        break
print('статус  :', w.status_var.get())
print('пометка :', w.assign_note.cget('text'))
d = b.client.get_ticket(2842, refresh=True)
print('в GLPI  :', [u['name'] for u in d.assign_users], [g['name'] for g in d.assign_groups])
b.client.set_assignee(2842, [36], [1])      # вернуть как было
d = b.client.get_ticket(2842, refresh=True)
print('вернули :', [u['name'] for u in d.assign_users], [g['name'] for g in d.assign_groups])
w.destroy(); root.destroy()
