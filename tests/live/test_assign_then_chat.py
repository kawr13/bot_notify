"""«Назначить в GLPI», а потом сразу «Отправить в чат» — обе по очереди."""
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

def pump(what, limit=40):
    t0 = time.time()
    while time.time() - t0 < limit:
        root.update(); time.sleep(0.1)
        if what in w.status_var.get():
            return w.status_var.get()
    return 'ТАЙМАУТ: ' + w.status_var.get()

rec = b.client.get_ticket(2842)
print('до      :', [u['name'] for u in rec.assign_users], [g['name'] for g in rec.assign_groups])
w._remember(rec); w._refresh_list()
w.listbox.selection_set(0); w._on_select()
for _ in range(80):
    root.update(); time.sleep(0.1)
    if len(w.choices) > 5:
        break
target = next((c for c in w.choices if c['itemtype'] == 'User' and str(c['items_id']) == '24'), None)
w.assign_var.set(target['label'])
print('выбрал  :', w.assign_var.get())

w.assign_now()
print('назнач. :', pump('записан', 40))
d = b.client.get_ticket(2842, refresh=True)
print('в GLPI  :', [u['name'] for u in d.assign_users], [g['name'] for g in d.assign_groups])
print('_busy   :', w._busy, '| кнопка в чат:', 'выключена' if w.chat_btn.instate(['disabled']) else 'рабочая')

print('\nтеперь сразу в чат:')
w.send_to_bot()
print('отправка:', pump('отправлено', 40))
print('_busy   :', w._busy)
b.client.set_assignee(2842, [36], [1])
d = b.client.get_ticket(2842, refresh=True)
print('\nвернули :', [u['name'] for u in d.assign_users], [g['name'] for g in d.assign_groups])
w.destroy(); root.destroy()
