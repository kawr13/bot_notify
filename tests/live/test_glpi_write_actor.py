import sys, time
sys.path.insert(0,'.')
from dotenv import dotenv_values
from glpi_client import GlpiClient, GlpiConfig
c = GlpiClient(GlpiConfig.from_env({k:str(v) for k,v in dotenv_values('.env').items() if v}))
c.login()
tid = 2842
d0 = c.get_ticket(tid)
print('было   :', [(u['items_id'], u['name']) for u in d0.assign_users],
      [(g['items_id'], g['name']) for g in d0.assign_groups])
# назначаем Кибардин Виктор (24) вместо Онзулс (36), группу оставляем
ok = c.set_assignee(tid, [24], [g['items_id'] for g in d0.assign_groups],
                    disable_notifications=True)
d1 = c.get_ticket(tid)
print(f'set_assignee -> {ok}')
print('стало    :', [(u['items_id'], u['name']) for u in d1.assign_users],
      [(g['items_id'], g['name']) for g in d1.assign_groups])
print('ИЗМЕНИЛОСЬ:', sorted(u['items_id'] for u in d1.assign_users) != sorted(u['items_id'] for u in d0.assign_users))
# возвращаем как было
c.set_assignee(tid, [u['items_id'] for u in d0.assign_users],
               [g['items_id'] for g in d0.assign_groups], disable_notifications=True)
d2 = c.get_ticket(tid)
print('вернули  :', [(u['items_id'], u['name']) for u in d2.assign_users])
