"""_phonelessqueuetest -- reports/phoneless_queue.py on an invented fixture in a temp dir.
Run:  DEALFLOW_DIR=<tmp> python _phonelessqueuetest.py   (no network, no real data)"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'reports'))
T = tempfile.mkdtemp(prefix='phoneless_')
os.environ.setdefault('DEALFLOW_DIR', T)
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(T, 'no_bk_cache.json')
import phoneless_queue as Q  # noqa: E402

FAILS = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if not ok else ''))
    if not ok:
        FAILS.append(name)


def lead(case, **kw):
    d = {'case': case, 'county': 'MIAMI-DADE', 'st': 'FC', 'ftype': 'MORTGAGE', 'auction': '11/20/2026',
         'days': 29, 'tier': 'B', 'score': 50, 'value': 400000, 'eq': 40, 'owners': 'TEST OWNER ' + case,
         'addr': '100 NW 1 ST, MIAMI, FL 33101', 'pkey': 'C' + case}
    d.update(kw)
    return d


ROWS = [
    lead('2099-000101-CA-01', phones=['3055550101'], phdnc=[False], phsrc=['st']),          # dialable
    lead('2099-000102-CA-01', days=10, eq=60),                                             # <=14, 50%+
    lead('2099-000103-CA-01', days=10, eq=30),                                             # <=14, 25-50
    lead('CACE24-000104', days=45, eq=None, county='BROWARD'),                         # 31-60, unknown
    lead('502024CA000105XXXXMB', days=9999, st='LP', auction='', filedDate='10/20/2026', eq=20,
         county='PALM BEACH'),                                                             # LP
    lead('2099-000106-CA-01', days=20, ftype='HOA', eq=5),                                 # 15-30, 0-25
    lead('2099-000107-CA-01', phones=['3055550107'], phdnc=[True], phsrc=['st']),          # DNC: not queued
    lead('2099-000108-CA-01', saleBkAct=True),                                             # stay: gated
    lead('2099-000109-CA-01', days=10, eq=-10),                                            # <=14, negative
    lead('2099-000110-CA-01', phones=['3055550110'], phdnc=[False], phsrc=['ag']),         # agent only
]
res = Q.analyse(ROWS, {})
ids = [q['c'] for q in res['queue']]
rec('counts: 10 rows, 1 dialable, 7 phoneless, 2 gated-out',
    (res['rows'], res['dialable'], res['phoneless'], res['gated']) == (10, 1, 7, 2), res)
rec('DNC-only and stayed leads are never queued', not ({'2099-000107-CA-01', '2099-000108-CA-01'} & set(ids)), ids)
rec('order: soonest bucket, then equity band, then days',
    ids == ['2099-000102-CA-01', '2099-000103-CA-01', '2099-000109-CA-01', '2099-000110-CA-01', '2099-000106-CA-01', 'CACE24-000104', '502024CA000105XXXXMB'], ids)
rec('by county', Q._count(res['queue'], 'county') == {'MIAMI-DADE': 5, 'BROWARD': 1, 'PALM BEACH': 1},
    Q._count(res['queue'], 'county'))
rec('case type splits LP / HOA / MORTGAGE',
    Q._count(res['queue'], 'type') == {'MORTGAGE': 5, 'LP': 1, 'HOA': 1}, Q._count(res['queue'], 'type'))
txt = Q.render(res, 3)
rec('output carries no phone numbers or owner names', '305555' not in txt and 'TEST OWNER' not in txt)
rec('top cuts the id list', '... 4 more' in txt, txt[-80:])
p = os.path.join(T, 'rows.json')
json.dump(ROWS, open(p, 'w'))
before = sorted(os.listdir(T))
rc = Q.main(['--input', p, '--optouts', os.path.join(T, 'none.json')])
rec('CLI runs and writes nothing', rc == 0 and sorted(os.listdir(T)) == before, (rc, os.listdir(T)))
res2 = Q.analyse(ROWS, {'2099-000102-CA-01': True})
rec('an opted-out case leaves the queue', '2099-000102-CA-01' not in [q['c'] for q in res2['queue']])
print('\n%d failure(s)' % len(FAILS))
sys.exit(1 if FAILS else 0)
