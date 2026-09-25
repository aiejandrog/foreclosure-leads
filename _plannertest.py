"""_plannertest -- the morning agenda's gates on synthetic leads. No data files, no browser.

Run: python _plannertest.py
Covers the 2026-09-25 fixes: the Carlos cluster and the Portfolio table honour the opt-out ledger,
diligence hold and the reps' notes; a HARD no never surfaces; a SOFT no is retired except on its one
event-driven resurface; the LP sentinel (days=9999) is not a late sale; countdowns are recomputed
from the sale-date string against --date.
"""
import datetime as dt
import os
import sys
import tempfile

sys.argv = [sys.argv[0]]
import morning_planner as MP

pass_n = fail_n = 0
def T(name, ok, got=None):
    global pass_n, fail_n
    if ok: pass_n += 1; print('  PASS', name)
    else: fail_n += 1; print('  FAIL', name, '' if got is None else '[got %r]' % (got,))

TODAY = dt.date(2026, 9, 25)
MP._TODAY = TODAY
MP._DG_TALLY = None
MP._agenda_safe = lambda r: not r.get('_hold')          # isolate: diligence gate is its own module
MP._deeply_underwater = lambda r, floor=-25.0: False

def lead(case, days, zip_='33155', owner=None, **kw):
    owner = owner or ('Owner%s, Test' % case)   # the cluster dedupes by owner
    d = dict(case=case, owner=owner, addr='%s SW 8 ST, MIAMI, FL- %s' % (case[-3:], zip_),
             auction=(TODAY + dt.timedelta(days=days)).isoformat() if days < 9999 else '',
             days=days, tier='A', county='MIAMI-DADE', phones=['3055550%03d' % int(case[-3:])],
             emails=['%s@example.com' % case.lower()], value=400000)
    d.update(kw); return d

print('== _days recomputes from the date string ==')
T('auction 20 days out -> 20', MP._days(lead('C001', 20)) == 20, MP._days(lead('C001', 20)))
MP._TODAY = TODAY + dt.timedelta(days=5)
T('--date moves the countdown (20 -> 15)', MP._days(lead('C001', 20)) == 15, MP._days(lead('C001', 20)))
MP._TODAY = TODAY
T('no date string + baked 0 -> unknown, not "sale today"', MP._days({'days': 0}) is None)
T('LP sentinel stays 9999', MP._days(lead('L001', 9999)) == 9999)
T('LP sentinel is not a late sale', MP._late_count([lead('L001', 9999), lead('C002', 60)]) == 1)

print('== Carlos cluster honours every gate ==')
MP._OPTOUTS = {'_dealflow_notes': 1, 'notes': {'C103': {'status': 'DO NOT CONTACT'}}}
MP._NOTES = {'C104': {'status': 'Dead'}, 'C105': {'no': 'hard', 'status': 'DO NOT CONTACT'},
             'C106': {'no': 'soft', 'noAt': 1, 'noWasLp': False, 'resurf': 0},   # dated lead, 20d out -> retired
             'C107': {'no': 'soft', 'noAt': 1, 'noWasLp': False, 'resurf': 0},   # 10d out -> T-14 resurface
             'C108': {'no': 'soft', 'noAt': 1, 'noWasLp': True,  'resurf': 0},   # LP lead that GOT a date
             'C109': {'no': 'soft', 'noAt': 1, 'noWasLp': False, 'resurf': 1},   # spent
             'C110': {'status': 'Not interested'}}                               # legacy, no date -> retired
leads = [lead('C101', 20), lead('C102', 21), lead('C103', 22), lead('C104', 23), lead('C105', 24),
         lead('C106', 20), lead('C107', 10), lead('C108', 30), lead('C109', 10),
         lead('C110', 9999), lead('C111', 25, _hold=True)]
z, _, rows = MP._carlos_cluster(leads, exclude_zips=(), max_days=45, min_cluster=2, top_n=20)
got = sorted(r['case'] for r in rows)
T('cluster = clean leads + the two open resurfaces only', got == ['C101', 'C102', 'C107', 'C108'], got)
T('ledger DNC (C103) excluded', 'C103' not in got)
T('rep Dead (C104) excluded', 'C104' not in got)
T('hard no (C105) excluded', 'C105' not in got)
T('soft no 20d out (C106) retired', 'C106' not in got)
T('soft no inside T-14 (C107) resurfaces', 'C107' in got)
T('LP soft no with a new date (C108) resurfaces', 'C108' in got)
T('spent resurface (C109) retired', 'C109' not in got)
T('diligence hold (C111) excluded', 'C111' not in got)

print('== Portfolio table honours the same gates ==')
pl = [lead('P201', 20, emails=['same@example.com']), lead('P202', 21, emails=['same@example.com']),
      lead('P203', 22, emails=['dnc@example.com']), lead('P204', 23, emails=['dnc@example.com'])]
MP._OPTOUTS = {'notes': {'@dnc@example.com': {'status': 'DO NOT CONTACT'}}}
MP._NOTES = {}
ports = MP._portfolios(pl)
T('opted-out portfolio owner is absent', all(p['email'] != 'dnc@example.com' for p in ports), [p['email'] for p in ports])
T('clean portfolio owner is present', any(p['email'] == 'same@example.com' for p in ports))

print('== phone-keyed opt-out from the notes ==')
MP._NOTES = {'#3055550301': {'status': 'DO NOT CONTACT', 'optout': '2026-09-20'}}
T('a #digits DNC note blocks the lead that carries that phone', MP._notes_blocked(lead('C301', 20)) is True)
T('an unrelated lead is not blocked', MP._notes_blocked(lead('C302', 20)) is False)

print('\n%d passed, %d failed' % (pass_n, fail_n))
sys.exit(1 if fail_n else 0)
