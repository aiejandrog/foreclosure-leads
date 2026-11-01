"""Call Mode: an 11-digit "1305..." number is a dialable number, not a lost one.

call_rows tested len(_digits(p)) == 10, so a stored "13055550101" was dropped from the dial list
and a lead whose only number had that shape fell to "Find a number" although it had a number. The
DNC and opt-out comparisons already use _ph10 for the same shape. Invented numbers only.
Run: python _callph11test.py
"""
import os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='callph11_')
os.environ['DEALFLOW_DIR'] = _T
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')
import call_mode  # noqa: E402

fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra and not ok else ''))
    if not ok:
        fails.append(name)


def lead(n, phones, dnc=None):
    return {'case': '2026-0009%02d-CA-01' % n, 'owners': 'JOSE GARCIA', 'oname': 'JOSE GARCIA',
            'addr': '100 NW 1st Ave, Miami, FL 33130', 'folio': '0101010101010', 'days': 20,
            'auction': '2026-11-25', 'st': 'FC', 'pkey': 'pk%d' % n, 'phones': phones,
            'phtype': ['mobile'] * len(phones), 'phsrc': ['st'] * len(phones),
            'phdnc': dnc or [False] * len(phones)}


lk = []
rows, _ = call_mode.call_rows([lead(1, ['13055550101', '3055550102']), lead(2, ['13055550103']),
                               lead(3, ['+1 (305) 555-0104']), lead(4, ['3055550105'])], lookup=lk)
by = {r['c'].split('-')[1]: r for r in rows}
rec('11-digit number kept next to a 10-digit one', sorted(by['000901']['p']) == ['3055550101', '3055550102'], by.get('000901'))
rec('a lead whose only number is 11-digit is a dial row', '000902' in by and by['000902']['p'] == ['3055550103'], by.keys())
rec('that lead is not in Find a number', not [x for x in lk if '000902' in x['c']], lk)
rec('+1 (305) form is dialable', '000903' in by and by['000903']['p'] == ['3055550104'], by.keys())
rec('plain 10-digit unchanged', by['000904']['p'] == ['3055550105'])
rec('every shipped number is 10 digits', all(len(x) == 10 for r in rows for x in r['p']))
rows2, _ = call_mode.call_rows([lead(5, ['13055550106'], dnc=[True])], lookup=[])
rec('DNC 11-digit stays display-only', rows2 == [], rows2)
rows3, _ = call_mode.call_rows([lead(6, ['23055550107', '305555'])], lookup=[])
rec('junk (11 digits not starting 1, short) still dropped', rows3 == [], rows3)
print('\n%d failed' % len(fails) if fails else '\nALL PASS')
sys.exit(1 if fails else 0)
