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
t0 = time.time()
w._open_link()
print(f"_open_link вернулся за {time.time()-t0:.2f} с")
print('статус:', w.status_var.get())
w.destroy(); root.destroy()
