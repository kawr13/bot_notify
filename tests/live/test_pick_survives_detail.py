"""Выбор оператора не должен слетать после фоновой догрузки деталей."""
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
root = tk.Tk(); root.withdraw(); w = GlpiWindow(root, b)
rec = b.client.get_ticket(2842)
w._remember(rec); w._refresh_list(); w.listbox.selection_set(0); w._on_select()
for _ in range(80):
    root.update(); time.sleep(0.1)
    if len(w.choices) > 5: break
target = next(c for c in w.choices if str(c['items_id']) == '24' and c['itemtype'] == 'User')
w.assign_var.set(target['label'])
print('выбрал        :', w.assign_var.get())
w._got_detail(b.client.get_ticket(2842, refresh=True))   # прилетели детали
print('после деталей :', w.assign_var.get())
print('_picked       :', (w._picked() or {}).get('label'))
w._load_current()
print('после list    :', w.assign_var.get())
w.destroy(); root.destroy()
