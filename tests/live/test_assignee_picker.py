import sys, time, pathlib
sys.path.insert(0,'.')
from dotenv import dotenv_values
from glpi_bridge import GlpiBridge
import tkinter as tk
from glpi_panel import GlpiWindow
env={k:str(v) for k,v in dotenv_values('.env').items() if v}
b = GlpiBridge(env=env)
b.monitor_config.state_path = pathlib.Path('data/glpi_state_probe.json')
b.monitor.cfg.state_path = b.monitor_config.state_path
b.client.login()
root = tk.Tk(); root.withdraw(); w = GlpiWindow(root, b)
rows = [r for r in b.monitor.rows() if r.is_active][:5]
rec = b.client.get_ticket(rows[3].ticket_id)   # заявка с человеком в исполнителях
print(f'заявка #{rec.ticket_id}: назначено={rec.assignee_names!r}')
w._remember(rec); w._refresh_list()
w.listbox.selection_set(0); w._on_select()
for _ in range(60):
    root.update(); time.sleep(0.1)
    if len(w.choices) > 5: break
print('в поле выбрано :', repr(w.assign_var.get()))
print('_picked()      :', w._picked())
print('в списке людей :', sum(1 for c in w.choices if c["itemtype"] == "User"),
      '| групп:', sum(1 for c in w.choices if c["itemtype"] == "Group"),
      '| всего:', len(w.choices))
print('первые 5:', [c["label"] for c in w.choices[:5]])
w.destroy(); root.destroy()
