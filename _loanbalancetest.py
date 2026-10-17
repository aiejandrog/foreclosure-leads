"""_loanbalancetest.py — loan_balance.py: estimated current balances on the recorded chain.

Anchored on the case that started it (2026-10-17): PropStream shows the 12/21/2021 Rocket loan,
$190,000, at "Est. Rate 3.11%", 360 months, "Est. Loan Balance $170,375". 3.11% is the Freddie Mac
PMMS 30-year rate for the week of 12/02/2021. 57 payments reproduce the figure to the dollar.
"""
import sys

import loan_balance as LB

FAIL = []


def chk(ok, msg):
    print(('PASS ' if ok else 'FAIL ') + msg)
    if not ok:
        FAIL.append(msg)


TAB = {'2021-12': [3.11, 2.38], '2023-08': [7.07, 6.46], '2025-05': [6.82, 5.95], '2026-09': [7.3, 6.5]}

# 1. the PropStream reproduction
e = LB.estimate({'amt': 190000, 'd': '2021-12-21', 'party': 'ROCKET MORTGAGE LLC'}, as_of='2026-09-21', tab=TAB)
chk(e and e['how'] == 'amort' and e['rate'] == 3.11 and e['paid'] == 57, 'rate from recording month, 57 payments: %s' % e)
chk(e and abs(e['est'] - 170375) <= 1, 'reproduces PropStream $170,375 (got %s)' % (e and e['est']))

# 2. formula edges
chk(LB.amortized(100000, 6.0, 360, 0) == 100000, 'no payments yet = face')
chk(LB.amortized(100000, 6.0, 360, 360) == 0, 'paid out = 0')
chk(abs(LB.amortized(120000, 0.0, 360, 180) - 60000) < 1e-6, 'zero rate is straight-line')
chk(LB.months_between('2021-12-21', '2022-01-20') == 0 and LB.months_between('2021-12-21', '2022-01-21') == 1,
    'a payment counts once the month is complete')
chk(LB.months_between('12/21/2021', '2026-09-21') == 57, 'M/D/YYYY dates parse')

# 3. no guessed rates
chk(LB.estimate({'amt': 100000, 'd': '2001-03-01'}, as_of='2026-01-01', tab=TAB) is None,
    'a month older than the table gets no estimate')
chk(LB.rate_for('2026-10-05', TAB) == 7.3, 'a month newer than the table uses the latest survey')
chk(LB.estimate({'amt': 0, 'd': '2021-12-21'}, tab=TAB) is None, 'unpriced row: no estimate')
chk(LB.estimate({'amt': 50000, 'd': ''}, tab=TAB) is None, 'undated row: no estimate')

# 4. not amortized: equity share, credit line; vendor balance wins
h = LB.estimate({'amt': 29166, 'd': '2025-05-13', 'party': 'HOMETAP EQUITY PARTNERS LLC'}, as_of='2026-09-21', tab=TAB)
chk(h and h['how'] == 'eqshare' and h['est'] == 29166, 'Hometap kept at the recorded figure, never amortized down')
c = LB.estimate({'amt': 70000, 'd': '2023-08-28', 'doc': 'MORTGAGE - LINE OF CREDIT'}, as_of='2026-09-21', tab=TAB)
chk(c and c['how'] == 'credit_line' and c['est'] == 70000, 'credit line kept at the recorded line')
v = LB.estimate({'amt': 300000, 'bal': 251234, 'd': '2015-01-01'}, tab=TAB)
chk(v and v['how'] == 'vendor' and v['est'] == 251234, 'BatchData current balance used as given')

# 5. stamping a lis pendens row
lp = {'st': 'LP', 'eqstate': 'priced', 'judg': 0, 'value': 367000, 'eq': None, 'filedDate': '2026-09-30',
      'orliens': [
          {'d': '2021-12-21', 'amt': 190000, 'party': 'ROCKET MORTGAGE LLC', 'st': 'OPEN'},
          {'d': '2023-08-28', 'amt': 70000, 'party': 'WE FLORIDA FINANCIAL', 'st': 'OPEN'},
          {'d': '2025-05-13', 'amt': 29166, 'party': 'HOMETAP EQUITY PARTNERS LLC', 'st': 'OPEN'},
          {'d': '2008-12-17', 'amt': 100000, 'party': 'OLD BANK', 'st': 'SATISFIED'}]}
n = LB.stamp_lead(lp, as_of='2026-10-17', tab=TAB)
chk(n == 3, 'three open priced loans estimated, the satisfied one skipped (%d)' % n)
chk('est' not in lp['orliens'][3], 'satisfied row carries no estimate')
chk(lp['orestface'] == 289166, 'recorded total kept beside the estimate')
chk(lp['orest'] < 289166 and lp['debtEst'] == lp['orest'], 'debtEst = estimated total, below face (%s)' % lp.get('orest'))
chk(lp['orliens'][0]['est'] == 170374 or abs(lp['orliens'][0]['est'] - 170374) <= 1,
    'LP stops amortizing at the filing date (9/30 -> 57 payments): %s' % lp['orliens'][0]['est'])
chk(lp.get('eqest') and lp['eq'] == round((367000 - lp['debtEst']) / 367000 * 100, 1),
    'LP eq filled from the estimate and marked eqest (%s)' % lp.get('eq'))

# 6. a foreclosure with a judgment gets per-loan estimates but no debtEst / eq change
fc = {'st': 'FC', 'eqstate': 'priced', 'judg': 180000, 'value': 300000, 'eq': 40.0,
      'orliens': [{'d': '2021-12-21', 'amt': 190000, 'party': 'X', 'st': 'OPEN'}]}
LB.stamp_lead(fc, as_of='2026-10-17', tab=TAB)
chk('est' in fc['orliens'][0] and 'debtEst' not in fc and fc['eq'] == 40.0, 'judgment row: estimate shown, math untouched')

# 7. a ceiling chain (an open loan with no amount) never becomes an estimated debt
cl = {'st': 'LP', 'eqstate': 'unpriced', 'value': 300000, 'eq': None,
      'orliens': [{'d': '2021-12-21', 'amt': 190000, 'party': 'X', 'st': 'OPEN'},
                  {'d': '2022-01-05', 'amt': None, 'party': 'Y', 'st': 'OPEN'}]}
LB.stamp_lead(cl, as_of='2026-10-17', tab=TAB)
chk('debtEst' not in cl and cl['eq'] is None, 'unpriced chain: no estimated debt, eq stays unknown')

# 8. the bundled table loads and covers the recording months that matter
t = LB.table()
chk(len(t) >= 200 and '2021-12' in t and abs(t['2021-12'][0] - 3.10) < 0.1,
    'bundled PMMS table: %d months, 2021-12 = %s' % (len(t), t.get('2021-12')))

print('\n%d failure(s)' % len(FAIL))
sys.exit(1 if FAIL else 0)
