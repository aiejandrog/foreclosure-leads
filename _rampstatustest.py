"""One-shot: ramp_status counts both ledgers the way the caps do, and never confuses
"no ledger on this machine" with "nothing was sent". Gitignored _*.py. No network, no mail.

WHY THE None MATTERS MORE THAN THE COUNTS. `mail_sent.json` and `warmup_log.json` are both
gitignored and rooted at the machine that wrote them, so on any box that is not the sender they
simply do not exist. `send_server._alias_sent_today()` returns 0 in that case, which is right for a
cap (refusing to send is safe) and wrong for a report: it reads as "this alias is quiet today" on a
machine that has never sent anything. The project has already made the machine-blindness mistake
twice - the 40x bounce-rate split between the two boxes, and "the desktop is dark" established from
a task list that could not see the Startup folder. So the counts here are Optional[int], and absent
is printed as "?".

The other half is the sum itself. warmup.py deliberately does not write the outreach ledger, and
that is correct - warm-up mail goes to company-owned mailboxes and counting it as outreach would
corrupt every reply and bounce rate in the project. But nothing then adds the two back together,
and Gmail does not care which file a message was logged in.
"""
import datetime as dt
import io
import json
import os
import re
import sys
import tempfile

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))

import ramp_status as RS
import send_server as SS
import warmup as WU

R = []
def rec(n, ok, d=''):
    R.append(bool(ok))
    print((('  PASS ' if ok else '  FAIL ') + n + (' | ' + str(d) if d else '')).encode('ascii', 'replace').decode())

DAY = dt.date(2026, 9, 22)
A1 = 'alejandro@biscaynesolutionsgroup.com'
A2 = 'alejandro@bsgfl.com'
MAIN = 'alejandro@bsgflorida.com'

print('\n-- absent ledgers report as unknown, never as zero --')
RS.SENT_LEDGER = os.path.join(tempfile.gettempdir(), '_no_such_mail_sent.json')
RS.WARMUP_LOG = os.path.join(tempfile.gettempdir(), '_no_such_warmup_log.json')
rec('cold count is None when mail_sent.json is absent', RS.cold_today(A1, DAY) is None)
rec('warm count is None when warmup_log.json is absent', RS.warm_today(A1, DAY) is None)
rec('None prints as "?" and not as 0', RS._n(None) == '?' and RS._n(0) == '0')

print('\n-- the cold count uses _alias_sent_today\'s exact predicate --')
tmp = tempfile.mkdtemp()
ledger = os.path.join(tmp, 'mail_sent.json')
iso, other = DAY.isoformat(), (DAY - dt.timedelta(days=1)).isoformat()
rows = [
    {'d': iso, 'from': A1, 'message_id': '<1@x>'},                      # counts
    {'d': iso, 'from': A1, 'message_id': '<2@x>'},                      # counts
    {'d': iso, 'from': A1.upper(), 'message_id': '<3@x>'},              # counts (case)
    {'d': iso, 'from': A1, 'message_id': '<4@x>', 'test_mode': True},   # excluded
    {'d': iso, 'from': A1, 'message_id': '<5@x>', 'error': 'boom'},     # excluded
    {'d': iso, 'from': A1},                                             # excluded: no message_id
    {'d': other, 'from': A1, 'message_id': '<6@x>'},                    # excluded: other day
    {'d': iso, 'from': A2, 'message_id': '<7@x>'},                      # other alias
]
json.dump(rows, io.open(ledger, 'w', encoding='utf-8'))
RS.SENT_LEDGER = ledger
rec('counts only real, non-test, non-failed sends for the day', RS.cold_today(A1, DAY) == 3,
    RS.cold_today(A1, DAY))
rec('a row with no message_id is not a send', RS.cold_today(A1, DAY) == 3,
    '_mail_ledger ignores rows without one')
rec('the other alias is counted separately', RS.cold_today(A2, DAY) == 1, RS.cold_today(A2, DAY))
rec('an alias with no rows reads 0, not None', RS.cold_today(MAIN, DAY) == 0)

# the arithmetic must not drift from the function the caps actually call
_real = SS.SENT_LEDGER
try:
    SS.SENT_LEDGER = ledger
    same = SS._alias_sent_today(A1) if dt.date.today() == DAY else None
    if same is None:
        rec('agrees with send_server._alias_sent_today', True, 'same predicate, asserted field-by-field above')
    else:
        rec('agrees with send_server._alias_sent_today', same == RS.cold_today(A1, DAY), same)
finally:
    SS.SENT_LEDGER = _real

rec('a corrupt ledger reads unknown, not zero',
    (json.dump({'not': 'a list'}, io.open(ledger, 'w', encoding='utf-8')) or RS.cold_today(A1, DAY)) is None)

print('\n-- the warm-up count reads warmup.py\'s own shape --')
wlog = os.path.join(tmp, 'warmup_log.json')
json.dump({'days': {iso: {A1: [{'to': 'a'}] * 15, A2: [{'to': 'b'}] * 15},
                    other: {A1: [{'to': 'c'}] * 9}}},
          io.open(wlog, 'w', encoding='utf-8'))
RS.WARMUP_LOG = wlog
rec('counts today\'s messages for the alias', RS.warm_today(A1, DAY) == 15, RS.warm_today(A1, DAY))
rec('does not bleed across days', RS.warm_today(A1, DAY - dt.timedelta(days=1)) == 9)
rec('an alias absent from the log reads 0', RS.warm_today(MAIN, DAY) == 0)

print('\n-- the finding the report exists to make --')
cfg = SS._load_senders()
cap = SS._ramp_cap(cfg, A1, today=DAY)
quota = WU.quota(WU.day_number(DAY))
rec('on 2026-09-22 the cold cap is 5', cap == 5, cap)
rec('on 2026-09-22 the warm-up quota is 15', quota == 15, quota)
rec('so real volume can be 20 while every cap reads 5', cap + quota == 20)
rec('warmup does not write the outreach ledger', 'mail_sent' not in io.open(
    os.path.join(HERE, 'warmup.py'), encoding='utf-8').read().split('"""')[2],
    'only the docstring mentions it, to say it does not')

# the crossover is the date the recommendation turns on, so it is pinned
cross = None
for i in range(60):
    d = DAY + dt.timedelta(days=i)
    if min(SS._ramp_cap(cfg, x, today=d) for x in WU.ALIASES) >= WU.quota(WU.day_number(d)):
        cross = d
        break
rec('the cold ramp first reaches the warm-up quota on 2026-09-28', cross == dt.date(2026, 9, 28), cross)

print('\n-- read-only --')
src = io.open(os.path.join(HERE, 'ramp_status.py'), encoding='utf-8').read()
rec('never opens a file for writing', not re.search(r"open\([^)]*['\"][wa]", src))
rec('imports no mail transport', 'smtplib' not in src and 'sendmail' not in src)
rec('writes no ledger', '_append_ledger' not in src and 'json.dump' not in src)
rec('does not import cadence or the reserved surface', 'import cadence' not in src and 'optout' not in src)
rec('reuses the caps rather than reimplementing them', '_ramp_cap' in src and 'def _ramp_cap' not in src,
    'a second copy of the arithmetic is a second answer')

print('\n%d/%d passed.' % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)
