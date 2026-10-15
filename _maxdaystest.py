"""_maxdaystest -- the dial queue's sale-date window (call_mode.MAX_DAYS). Fixtures are fake.

Run:  python _maxdaystest.py    (exit 0 = pass)

2026-10-15: the window went from 60 to 120 days (the auction scrape horizon), so a lead whose sale
is 61-120 days out reaches the phone now instead of when it ages in. A sale already passed, or past
the window, still never ships; LP rows never had a date cut and still do not.
"""
import os
import sys

os.environ.pop('CALLMODE_MAX_DAYS', None)
os.environ.setdefault('DEALFLOW_BK_ALLOW_UNCHECKED', '0')
import call_mode as CM  # noqa: E402

fails, ran = [], []


def chk(name, cond):
    ran.append(name)
    if not cond:
        fails.append(name)


def row(case, days, **kw):
    r = {'case': case, 'county': 'MIAMI-DADE', 'st': 'FC', 'owners': 'ROE,MARY', 'oname': 'Mary Roe',
         'addr': '10 EAST ST, Miami, FL 33101', 'folio': '0101010101010', 'value': 300000, 'judg': 100000,
         'eq': 60, 'days': days, 'auction': '01/01/2027', 'phones': ['3055550101'], 'phdnc': [False],
         'phsrc': ['st'], 'phrank': [''], 'phbest': 0, 'emails': [], 'vac': False, 'warn': ''}
    r.update(kw)
    return r


def shipped(rows, **kw):
    out, _n = CM.call_rows(rows, **kw)
    return {r.get('c') for r in out}


chk('default window is 120 days', CM.MAX_DAYS == 120)
s = shipped([row('2026-000001-CA-01', 30), row('2026-000002-CA-01', 90),
             row('2026-000003-CA-01', 120), row('2026-000004-CA-01', 121),
             row('2026-000005-CA-01', -1)])
chk('30 days out ships', '2026-000001-CA-01' in s)
chk('90 days out ships (was cut at 60)', '2026-000002-CA-01' in s)
chk('120 days out ships', '2026-000003-CA-01' in s)
chk('121 days out does not ship', '2026-000004-CA-01' not in s)
chk('a passed sale does not ship', '2026-000005-CA-01' not in s)
chk('an explicit max_days still wins',
    '2026-000002-CA-01' not in shipped([row('2026-000002-CA-01', 90)], max_days=60))
chk('LP rows with no date are not cut',
    '2026-000006-CA-01' in shipped([row('2026-000006-CA-01', 9999, st='LP', auction='')]))
os.environ['CALLMODE_MAX_DAYS'] = '45'
chk('env override reads', CM._env_max_days() == 45)
os.environ['CALLMODE_MAX_DAYS'] = 'junk'
chk('junk env falls back to 120', CM._env_max_days() == 120)
os.environ['CALLMODE_MAX_DAYS'] = '0'
chk('zero falls back to 120', CM._env_max_days() == 120)
os.environ.pop('CALLMODE_MAX_DAYS', None)
chk('the 46+ lane has no 60-day ceiling', "_dayLane(r,46,9998)" in open(CM.__file__, encoding='utf-8').read())

for f in fails:
    print('  FAILED: ' + f)
print('%d checks, %d failed' % (len(ran), len(fails)))
sys.exit(1 if fails else 0)
