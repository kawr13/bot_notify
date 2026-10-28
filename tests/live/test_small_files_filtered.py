"""Живая проверка: вложения мельче порога не уходят в чат.

Берёт заявку с картинками разного веса (по умолчанию #2854: скриншот ~119 КБ
и подпись-логотип ~14 КБ) и проверяет три вещи:

1. вес доехал до карточки — оператор видит «14 КБ», а не «картинка #36»;
2. мелочь снята ещё до отправки;
3. даже если галочка простояла включённой (карточка догружается фоном),
   фильтр повторяется при выгрузке, и в итоге читается честно.

Запуск:  venv/Scripts/python.exe tests/live/test_small_files_filtered.py [id]
Заявка должна быть активной и с вложениями — иначе проверка бессмысленна.
"""
import sys, time, pathlib

sys.path.insert(0, '.')
from dotenv import dotenv_values
from glpi_bridge import GlpiBridge
import tkinter as tk
from glpi_panel import GlpiWindow

tid = int(sys.argv[1]) if len(sys.argv) > 1 else 2854
env = {k: str(v) for k, v in dotenv_values('.env').items() if v}
b = GlpiBridge(env=env)
b.monitor_config.state_path = pathlib.Path('data/glpi_state_probe.json')
b.monitor.cfg.state_path = b.monitor_config.state_path
b.client.login()

root = tk.Tk()
root.withdraw()
w = GlpiWindow(root, b)
floor = int(getattr(w.cfg, 'attachment_min_kb', 0) or 0)
print(f'порог фильтра: {floor} КБ')


def is_small(item) -> bool:
    size = int(item.get('size_bytes') or 0)
    return bool(floor) and bool(size) and size < floor * 1024


rec = b.client.get_ticket(tid, refresh=True)
w._remember(rec)
w._refresh_list()
w.listbox.selection_set(0)
w._on_select()

# ждём не чекбоксов, а замеренных весов: до замера фильтр слепой
t0 = time.time()
while time.time() - t0 < 40:
    root.update()
    time.sleep(0.15)
    if w.file_vars and all(i.get('size_bytes') for i, _ in w.file_vars):
        break

# и ещё немного: пересборка галочек приходит отдельным событием в UI-очереди,
# иначе печатали бы состояние в момент гонки
t0 = time.time()
while time.time() - t0 < 15:
    root.update()
    time.sleep(0.15)
    if w.file_vars and all(bool(var.get()) == (not is_small(item))
                           for item, var in w.file_vars):
        break

print('файлы и вес:')
for item, var in w.file_vars:
    small = is_small(item)
    flag = 'снята' if not var.get() else 'отправится'
    print(f"  {'-' if small else '+'} {item['name'][:28]:30} "
          f"{int(item.get('size_bytes') or 0):>9} Б  {flag}")

picked = [item for item, var in w.file_vars if var.get()]
small_picked = [i for i in picked if is_small(i)]

w.send_to_bot()
t0 = time.time()
while time.time() - t0 < 240:
    root.update()
    time.sleep(0.2)
    note = w.assign_note.cget('text')
    if 'отправлено' in note or 'не отправлено' in note:
        break

note = w.assign_note.cget('text')
print('ИТОГ:', note)
# уйти должно всё выбранное минус мелочь: снятая до отправки в чат не
# попадает в принципе, а включённой оставалась бы только руками
import re
match = re.search(r'вложений отправлено (\d+)', note)
sent_n = int(match.group(1)) if match else -1
expect = len(picked) - len(small_picked)
print(f'выбрано {len(picked)}, из них мелочи {len(small_picked)}, должно уйти {expect}')
print('ВЕРДИКТ:', 'ОК: мелочь в чат не ушла' if sent_n == expect else
      f'ПРОВАЛ: ушло {sent_n}, ожидалось {expect}')
w.destroy()
root.destroy()
