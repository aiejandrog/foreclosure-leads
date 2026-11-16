"""_caseagetest -- Jesse's case-age rule in the Call Mode dial queue.

Run:  python _caseagetest.py   (exit 0 = safe; no network, invented leads only)

WHAT THIS PINS
  1. case_year() reads the filing year from a Miami-Dade, Broward or Palm Beach case number and
     refuses anything else (a tax deed's '2026-3206TD' is not a filing).
  2. old_case_year(): 2023 and earlier is old; 2024 on is fresh; tax deeds and balloons are never old.
  3. Inside a time band an old case waits behind every fresh one, even when its equity is bigger;
     it is never dropped; across bands the band still wins.
  4. CALLMODE_CASE_AGE=0 turns the demotion off.
  5. The row carries the year and the card draws an OLD CASE chip.
"""
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import call_mode as CM  # noqa: E402

fails, checks = [], []


def rec(name, ok, extra=''):
    checks.append(name)
    extra = str(extra)
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + extra[:200]) if extra else ''))
    if not ok:
        fails.append(name)


Y = CM.case_year
rec('1a: Miami-Dade', Y('2026-018040-CA-01') == 2026 and Y('2019-004455-CA-01') == 2019)
rec('1b: Broward, with and without dashes', Y('CACE-26-018544') == 2026 and Y('CACE19004455') == 2019 and Y('COWE-24-058776') == 2024)
rec('1c: Palm Beach, both spellings', Y('502026CA012740XXXAMB') == 2026 and Y('50-2021-CC-012740-XXXA-MB') == 2021)
rec('1d: a tax deed certificate year and junk are not a filing year', Y('2026-3206TD') is None and Y('') is None and Y(None) is None and Y('hello') is None)
O = CM.old_case_year
rec('2a: 2023 and earlier is old, 2024 on is fresh', O('2023-001000-CA-01') == 2023 and O('2024-001000-CA-01') is None and O('CACE-26-000001') is None)
rec('2b: a tax deed or a balloon is never an old case', O('2019-001000-CA-01', 'TD') is None and O('2019-001000-CA-01', 'BAL') is None)

# ---- 3 / 4: the queue ------------------------------------------------------------------------------
def lead(case, days, eq, **kw):
    d = {'case': case, 'owners': 'TEST OWNER %s' % case[-4:], 'addr': '1 TEST ST, MIAMI, FL 33101', 'phones': ['(305) 555-0100'],
         'phtype': 'mobile', 'days': days, 'auction': '12/20/2026', 'value': 400000, 'eq': eq, 'county': 'MIAMI-DADE'}
    d.update(kw)
    return d


src = open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
i = src.index("    _ca = os.environ.get('CALLMODE_CASE_AGE'")
key_src = src[i:src.index('    for _r in out:', i)]
rec('3a: the sort key puts the case-age term right after the band', "_band(r), (1 if (_ca and r.get('cy')) else 0)," in key_src, key_src[:200])
rows = [{'c': 'OLD', 'd': 20, 'e': 90, 'cy': 2021, 'eqv': 1}, {'c': 'FRESH', 'd': 20, 'e': 40, 'eqv': 1},
        {'c': 'OLD2', 'd': 60, 'e': 95, 'cy': 2019}, {'c': 'FRESHFAR', 'd': 60, 'e': 10}]


def order(rows, ca=True):
    _band = lambda r: 0 if r['d'] <= 45 else 1
    return [r['c'] for r in sorted(rows, key=lambda r: (_band(r), (1 if (ca and r.get('cy')) else 0), 0 if r.get('eqv') else 1, -(r.get('e') or 0), r['d']))]


rec('3b: inside a band the fresh case beats the old one with more equity', order(rows)[:2] == ['FRESH', 'OLD'], order(rows))
rec('3c: the band still wins across bands', order(rows) == ['FRESH', 'OLD', 'FRESHFAR', 'OLD2'], order(rows))
rec('3d: nothing is dropped', len(order(rows)) == 4)
rec('4a: the switch is wired to CALLMODE_CASE_AGE', "os.environ.get('CALLMODE_CASE_AGE', '1') != '0'" in key_src)
rec('4b: switched off, equity decides again', order(rows, ca=False)[:2] == ['OLD', 'FRESH'], order(rows, ca=False))

# ---- 5. the row and the card ------------------------------------------------------------------------------
slim = [lead('2021-004455-CA-01', 12, 80), lead('2026-018040-CA-01', 12, 30)]
try:
    out = CM.call_rows(slim, optouts=set(), deads=set())
    rows_out = out[0] if isinstance(out, tuple) else out
    by = {r['c']: r for r in rows_out}
    rec('5a: the old case carries its year and the fresh one carries none',
        by.get('2021-004455-CA-01', {}).get('cy') == 2021 and 'cy' not in by.get('2026-018040-CA-01', {'cy': 1}), {k: v.get('cy') for k, v in by.items()})
    rec('5b: in the real queue the fresh case comes first despite less equity',
        [r['c'] for r in rows_out][:2] == ['2026-018040-CA-01', '2021-004455-CA-01'], [r['c'] for r in rows_out])
except Exception as exc:                                  # the harness input is minimal; say so rather than skip silently
    rec('5a: call_rows ran on the invented leads', False, '%s: %s' % (type(exc).__name__, exc))
rec('5c: the card draws an OLD CASE chip from the row year', 'OLD CASE &middot; filed' in src and "if(r.cy) clock +=" in src)

print('\n%d checks, %d failed' % (len(checks), len(fails)))
print('ALL PASS' if not fails else 'FAILED: ' + '; '.join(fails))
sys.exit(1 if fails else 0)
