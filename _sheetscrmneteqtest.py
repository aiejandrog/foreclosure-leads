#!/usr/bin/env python
"""_sheetscrmneteqtest — the team sheet never prints the whole property value as "Net equity".

Run:  python _sheetscrmneteqtest.py    (exit 0 = safe)

Ticket F-data-04 (audit 2026-11-13): build_rows wrote Net equity = value whenever the judgment was
0. A judgment of 0 is "not known" (ju=1) or "not entered yet" (lis pendens), never "nothing owed",
so a 400,000 house showed 400,000 of equity on the list the callers read. Net equity and Equity %
are now blank unless a judgment is known; Equity % still shows a baked `eq` when the row has one.

Invented rows only, temp DEALFLOW_DIR, no network. sheets_crm is imported, never run.
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix='sheetscrmneteq_')
os.environ['DEALFLOW_DIR'] = TMP
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sheets_crm as S        # noqa: E402

FAILS = []


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + (('  -- ' + str(detail)[:300]) if not cond else ''))
    if not cond:
        FAILS.append(name)


S._load = lambda _p, default: default            # no ledgers: nothing is closed
NEQ = S.HEADERS.index('Net equity')
EQP = S.HEADERS.index('Equity %')
VAL = S.HEADERS.index('Value')
JUD = S.HEADERS.index('Judgment owed')
CASE = S.HEADERS.index('Case')


def row(case, **kw):
    r = {'case': case, 'addr': '100 NW 1st Ave', 'owners': 'Example Owner', 'county': 'Miami-Dade',
         'auction': '2099-06-01', 'days': 30, 'value': 400000, 'judg': 0}
    r.update(kw)
    return r


SRC = [
    row('2099-000001-CA-01', ju=1),                                  # judgment amount unknown
    row('2099-000002-CA-01', st='LP', eq=None, eqfake=True),          # lis pendens, no judgment yet
    row('2099-000003-CA-01', judg=150000),                            # judgment known
    row('2099-000004-CA-01', ju=1, eq=40),                            # unknown judgment, eq baked
    row('2099-000005-CA-01', value=0, judg=0),                        # no value at all
]
NOTES = {r['case']: {'status': 'Working'} for r in SRC}

with contextlib.redirect_stdout(io.StringIO()):
    out = S.build_rows(SRC, NOTES, {}, {})
by = {x[CASE]: x for x in out}
check('all 5 invented rows earn a sheet row', len(by) == 5, len(by))


def cell(case, col):
    return (by.get(case) or [None] * len(S.HEADERS))[col]


c1, c2, c3, c4, c5 = (r['case'] for r in SRC)
check('unknown judgment (ju=1, judg 0): Net equity is blank, not the value',
      cell(c1, NEQ) == '', cell(c1, NEQ))
check('unknown judgment (ju=1, judg 0): Equity % is blank', cell(c1, EQP) == '', cell(c1, EQP))
check('lis pendens (judg 0): Net equity is blank, not the value', cell(c2, NEQ) == '', cell(c2, NEQ))
check('lis pendens (judg 0): Equity % is blank', cell(c2, EQP) == '', cell(c2, EQP))
check('judg 0 rows still show the Value and a blank Judgment owed',
      cell(c1, VAL) == 400000 and cell(c1, JUD) == '' and cell(c2, VAL) == 400000 and cell(c2, JUD) == '',
      (cell(c1, VAL), cell(c1, JUD), cell(c2, VAL), cell(c2, JUD)))
check('known judgment: Net equity is value minus judgment', cell(c3, NEQ) == 250000, cell(c3, NEQ))
check('known judgment: Equity % is computed', cell(c3, EQP) == 62, cell(c3, EQP))
check('judg 0 with a baked eq: Net equity blank, Equity % keeps the baked eq',
      cell(c4, NEQ) == '' and cell(c4, EQP) == 40, (cell(c4, NEQ), cell(c4, EQP)))
check('no value: both blank', cell(c5, NEQ) == '' and cell(c5, EQP) == '', (cell(c5, NEQ), cell(c5, EQP)))

print()
print('==== %d FAIL(S) ====' % len(FAILS) if FAILS else '==== all sheets CRM net-equity checks passed ====')
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAILS else 0)
