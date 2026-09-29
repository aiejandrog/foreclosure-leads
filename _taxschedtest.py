"""_taxschedtest -- Tax1: county_taxes reads near-sale parcels first, reads go stale, certificates
are followed up.

Run:  python _taxschedtest.py   (exit 0 = safe; no network, no browser, invented folios only)

WHY (2026-09-26, roadmap Tax1): a folio read once was cached forever, so a parcel read at $0 in
August stayed "$0" through a September certificate sale, and the nightly cap of 60 went to whatever
random.shuffle picked. A stale "$0" rendered no chip at all, which reads as "no back taxes".
"""
import datetime as dt
import os
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import county_taxes as CT  # noqa: E402

fails, checks = [], []


def rec(name, ok, extra=''):
    checks.append(name)
    extra = str(extra)
    if len(extra) > 200:
        extra = extra[:200] + ' ...'
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + extra) if extra else ''))
    if not ok:
        fails.append(name)


T = dt.date(2026, 9, 26)
D = lambda n: T + dt.timedelta(days=n)            # noqa: E731
ISO = lambda n: (T + dt.timedelta(days=n)).isoformat()  # noqa: E731

print('FRESHNESS')
rec('inside 14 days of sale a read is stale after 3 days', CT.max_age_days(10) == 3)
rec('inside 45 days after 7', CT.max_age_days(30) == 7)
rec('a sold certificate is re-read weekly whatever the date', CT.max_age_days(None, True) == 7)
rec('otherwise 30 days, the same as miami_ranking', CT.max_age_days(200) == 30 and CT.max_age_days(None) == 30)
import miami_ranking as MR  # noqa: E402
rec('...which is miami_ranking.TAX_MAX_AGE', MR.TAX_MAX_AGE == CT.FRESH_DEFAULT_AGE)
rec('no read is stale', CT.is_stale(None, 10, T) and CT.is_stale({'due': 0}, 10, T))
rec('a 2-day-old read 10 days out is fresh', not CT.is_stale({'checked': ISO(-2)}, 10, T))
rec('a 4-day-old read 10 days out is stale', CT.is_stale({'checked': ISO(-4)}, 10, T))
rec('a 20-day-old read 200 days out is fresh', not CT.is_stale({'checked': ISO(-20)}, 200, T))

print('\nNEAR-SALE FIRST')
rows = {'MIAMI-DADE': {
    'A': {'case': 'a', 'sale': D(40)},      # near, never read
    'B': {'case': 'b', 'sale': D(5)},       # nearest, stale
    'C': {'case': 'c', 'sale': None},       # no date, never read
    'D': {'case': 'd', 'sale': D(200)},     # far, cert, stale
    'E': {'case': 'e', 'sale': D(200)},     # far, stale (31 days)
    'F': {'case': 'f', 'sale': D(-10)},     # past sale, stale
    'G': {'case': 'g', 'sale': D(3)},       # near but FRESH
}}
cache = {'B': {'checked': ISO(-5), 'due': 0}, 'D': {'checked': ISO(-8), 'due': 900, 'cert': {'num': '1', 'date': '06/01/2024'}},
         'E': {'checked': ISO(-31), 'due': 0}, 'F': {'checked': ISO(-40), 'due': 0}, 'G': {'checked': ISO(-1), 'due': 0}}
plan = [f for _, f in CT.plan_targets(rows, cache, T, 99)]
rec('order: near sales soonest-first, then certificates, never-read, stale, past sales last',
    plan == ['B', 'A', 'D', 'C', 'E', 'F'], plan)
rec('a fresh read is not re-read', 'G' not in plan)
rec('the cap takes from the front', [f for _, f in CT.plan_targets(rows, cache, T, 2)] == ['B', 'A'])
rec('limit 0 reads nothing', CT.plan_targets(rows, cache, T, 0) == [])
src = open(os.path.join(HERE, 'county_taxes.py'), encoding='utf-8').read()
rec('main() plans with plan_targets, not a shuffle', 'targets = plan_targets(rows, cache, today, limit)' in src
    and 'random.shuffle(targets)' not in src)
two = CT._folio_rows('MIAMI-DADE', rows=[
    {'folio': '30-0000-000-0001', 'case': 'x', 'auction': D(60).strftime('%m/%d/%Y')},
    {'folio': '3000000000001', 'case': 'y', 'auction': D(9).strftime('%m/%d/%Y')},
    {'folio': '3000000000002', 'case': 'z', 'auction': ''}])
rec('two cases on one folio: the sooner sale decides', two['3000000000001']['case'] == 'y'
    and two['3000000000001']['sale'] == D(9), two)
rec('a row with no date is kept with sale None', two['3000000000002']['sale'] is None)

print('\nCERTIFICATES')
TXT = ('Total Amount Due: $1,234.00  2024 Annual bill $1,234.00 Unpaid  Certificate #252 Issued 06/01/2025 '
       'face $1,100.00 at 18%  Certificate #300 Issued 06/01/2024 $900.00 at 5%  Tax Deed Application filed')
p = CT._parse(TXT)
rec('every certificate is kept, the first stays in `cert`', p['cert']['num'] == '252'
    and [c['num'] for c in p.get('certs', [])] == ['252', '300'], p)
rec('a tax-deed mention on the page is flagged', 'Tax Deed Application' in p.get('tax_deed_signal', ''))
f1 = CT.cert_followup({'date': '06/01/2025'}, T)
rec('TDA possible 2 years after April 1 of the issue year (FS 197.502)', f1['tda_possible_from'] == '2027-04-01'
    and f1['state'] == 'holding', f1)
f2 = CT.cert_followup({'date': '06/01/2024'}, T)
rec('...and open once that date passes', f2['state'] == 'tax_deed_application_possible'
    and f2['tda_possible_from'] == '2026-04-01', f2)
f3 = CT.cert_followup({'date': '06/01/2018'}, T)
rec('an unapplied certificate expires 7 years after issuance (FS 197.482)', f3['state'] == 'expired'
    and f3['expires'] == '2025-06-01', f3)
rec('an unreadable issue date -> no follow-up, not a guess', CT.cert_followup({'date': 'n/a'}, T) is None)
rec('a single-certificate page has no `certs` list (old shape unchanged)',
    'certs' not in CT._parse('Total Amount Due: $10.00 Certificate #9 Issued 06/01/2025 $10.00 at 5%'))

print('\nBOARD FIELDS (the stale-read gate)')
b = CT.board_fields({'checked': ISO(-2), 'due': 5000, 'years': [{'year': '2025'}]}, D(10), T)
rec('a fresh delinquency bakes the amount, no stale flag', b.get('taxDue') == 5000 and 'taxStale' not in b, b)
b = CT.board_fields({'checked': ISO(-9), 'due': 0}, D(10), T)
rec('a STALE $0 near the sale is flagged -- the case that used to render nothing',
    b.get('taxStale') == 9 and 'taxDue' not in b, b)
b = CT.board_fields(None, D(10), T)
rec('never read, sale ahead -> taxStale "never"', b.get('taxStale') == 'never', b)
rec('never read, no sale date -> nothing claimed', CT.board_fields(None, None, T) == {})
b = CT.board_fields({'checked': ISO(-1), 'due': 900, 'cert': {'num': '1', 'date': '06/01/2024'}}, D(200), T)
rec('a certificate carries its follow-up dates', b['taxCertFollow']['state'] == 'tax_deed_application_possible'
    and b['taxCertN'] == 1, b)

rec('an old-shape `cert: true` still bakes the amount and never crashes the bake',
    CT.board_fields({'checked': ISO(-1), 'due': 10, 'cert': True}, D(200), T).get('taxCert') is True)

print('\nWIRING')
fl = open(os.path.join(HERE, 'foreclosure_leads.py'), encoding='utf-8').read()
rec('the rebuild bakes through board_fields, from the read cache', '_CT.board_fields(' in fl and '_CT.CACHE' in fl)
tpl = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
rec('the board shows a stale read even with no amount', 'if(!r || !(+r.taxDue)) return stale;' in tpl
    and 'TAX READ ' in tpl)
rec('the certificate tooltip cites both statutes', 'FS 197.502' in tpl and 'FS 197.482' in tpl)
bat = open(os.path.join(HERE, 'refresh-dealflow.bat'), encoding='utf-8', errors='replace').read()
rec('the nightly still runs county_taxes in [3l/5] (no .bat change needed)', 'python -u county_taxes.py' in bat)

print('\n%s -- %d check(s), %d failure(s)' % ('PASSED' if not fails else 'FAILED', len(checks), len(fails)))
for f in fails:
    print('  FAILED: ' + f)
sys.exit(1 if fails else 0)
