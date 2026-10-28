import sys, time, pathlib
sys.path.insert(0, '.')
from dotenv import dotenv_values
from glpi_bridge import GlpiBridge
import glpi_panel
import tkinter as tk
from glpi_panel import GlpiWindow
env = {k: str(v) for k, v in dotenv_values('.env').items() if v}
b = GlpiBridge(env=env)
b.monitor_config.state_path = pathlib.Path('data/glpi_state_probe.json')
b.monitor.cfg.state_path = b.monitor_config.state_path
b.client.login()
root = tk.Tk(); root.withdraw(); w = GlpiWindow(root, b)
glpi_panel.messagebox.showerror = lambda *a, **k: print('ОКНО ОШИБКИ:', str(a[1])[:200])
rec = b.client.get_ticket(2854, refresh=True)
w._remember(rec); w._refresh_list(); w.listbox.selection_set(0); w._on_select()
for _ in range(40):
    root.update(); time.sleep(0.1)
    if w.file_vars: break
print('чекбоксы файлов:', [(a['name'][:20], a['is_image'], bool(v.get())) for a, v in w.file_vars])
w.send_to_bot()
t0 = time.time()
while time.time() - t0 < 120:
    root.update(); time.sleep(0.15)
    if 'отправлено' in w.status_var.get() or 'не отправлено' in w.status_var.get(): break
print('статус:', w.status_var.get())
print('итог  :', w.assign_note.cget('text'))
w.destroy(); root.destroy()
