"""Регресс: карточка открыта, список обновился — кнопка не должна «терять» заявку."""
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
print('выбрано      :', getattr(w._selected(), 'ticket_id', None))

w._refresh_list()                      # как опрос GLPI раз в 30 с
print('после list   :', 'curselection', w.listbox.curselection() or 'ПУСТО',
      '| _selected ->', getattr(w._selected(), 'ticket_id', None))

other = b.client.get_ticket(2090)      # «прилетела» новая заявка
w.add_ticket(other)
print('после новой  : карточка ->', getattr(w._selected(), 'ticket_id', None),
      '|', w.status_var.get()[:60])

target = next(c for c in w.choices if str(c['items_id']) == '24' and c['itemtype'] == 'User')
w.assign_var.set(target['label'])
w.assign_now()
t0 = time.time()
while time.time() - t0 < 40:
    root.update(); time.sleep(0.1)
    if 'записан' in w.status_var.get() or 'нечего' in w.status_var.get(): break
print('назначить    :', w.status_var.get())
d = b.client.get_ticket(2842, refresh=True)
print('в GLPI       :', [u['name'] for u in d.assign_users], [g['name'] for g in d.assign_groups])
b.client.set_assignee(2842, [36], [1])
print('вернули      :', [u['name'] for u in b.client.get_ticket(2842, refresh=True).assign_users])
w.destroy(); root.destroy()
