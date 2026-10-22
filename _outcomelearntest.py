"""_outcomelearntest.py -- outcome_learn.py on fake data: no board, no network, no real people."""
import datetime as dt
import json
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ['DEALFLOW_DIR'] = tmp
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import outcome_learn as OL

REAL_RAW = OL.load_raw_leads
TODAY = dt.date(2026, 10, 12)

leads = {
    'T-1': {'case': 'T-1', 'county': 'MIAMI-DADE', 'auction': '11/01/2026', 'eq': 45.5, 'eqstate': 'priced',
            'hs': True, 'tier': 'A', 'phones': ['3055550001', '7865550002'], 'phsrc': ['tr', 'wp'],
            'phtype': ['mobile', 'landline']},
    'T-2': {'case': 'T-2', 'county': 'BROWARD', 'auction': '10/15/2026', 'eq': 0, 'eqstate': 'unpriced',
            'hs': False, 'tier': 'C', 'phones': ['9545550003'], 'phsrc': ['tr'], 'phtype': ['mobile']},
}
notes = {'notes': {
    'T-1': {'dials': [{'d': '2026-10-10', 'ph4': '0002', 'oc': 'talked', 'by': 'Alex'},
                      {'d': '2026-10-09', 'ph4': '0001', 'oc': 'noanswer', 'by': 'Alex'},
                      {'d': '2026-10-10', 'ph4': '0001', 'oc': 'pending', 'by': 'Alex'},
                      {'d': '2026-10-10', 'ph4': '0001', 'oc': 'redial', 'by': 'Alex'}]},
    'T-2': {'dials': [{'d': '2026-10-11', 'ph4': '0003', 'oc': 'badnum', 'by': 'Carlos'}]},
    'GONE': {'dials': [{'d': '2026-10-11', 'ph4': '9999', 'oc': 'callback'}]},
}}
OL.load_raw_leads = lambda: list(leads.values())
OL.load_board_rows = lambda: []
OL._load = lambda p, d: notes if p.endswith('worker_notes.json') else d

fails = []


def check(name, got, want):
    if got != want:
        fails.append('%s: got %r want %r' % (name, got, want))


r = OL.compute(today=TODAY)
bs = r['by_signal']
check('dials exclude pending and redial', r['totals']['dials'], 4)
check('pending + redial counted as markers', r['totals']['attempt markers'], 2)
check('legacy markers have no explicit link', r['totals']['unlinked attempt markers'], 2)
check('misleading missing-outcome key removed', 'pending (no outcome tapped)' in r['totals'], False)
check('leads with convo', r['totals']['leads with a conversation'], 2)
check('miami convo', bs['county']['MIAMI-DADE']['convo'], 1)
check('gone lead bucket', bs['county']['lead gone']['dials'], 1)
check('phone source by ph4', bs['phone source']['wp']['convo'], 1)
check('phone position', bs['phone position']['2nd-3rd']['dials'], 1)
check('sale band', bs['days to sale']['0-7d']['badnum'], 1)
check('no sale date is its own bucket', OL.sale_band({'auction': ''}, TODAY), 'no sale date')
check('equity is a percent band', bs['equity %']['30-60%']['convo'], 1)
w = OL.compute(days=1, today=TODAY)
check('window', w['totals']['dials'], 2)
check('markers honour window', w['totals'].get('attempt markers', 0), 0)
blob = json.dumps(r)
for leak in ('T-1', 'T-2', '3055550001', '0002'):
    if leak in blob:
        fails.append('output carries %r' % leak)

# Call Mode retains redial, then logs an outcome with tsd pointing to that marker's tsu.
marker = {'d': '2026-10-11', 'ph4': '0003', 'oc': 'redial', 'by': 'Carlos', 'tsu': 1000}
outcome = dict(marker, oc='talked', tsu=2000, tsd=1000)
check('explicit redial link', dict(OL.marker_counts([marker, outcome])),
      {'attempt markers': 1, 'markers linked to outcomes': 1})
for field, value in [('d', '2026-10-10'), ('ph4', '0001'), ('by', 'Alex'), ('tsd', 999)]:
    other = dict(outcome, **{field: value})
    check('do not match different ' + field, OL.marker_counts([marker, other])['unlinked attempt markers'], 1)
check('later legacy outcome is not a proven match',
      OL.marker_counts([marker, {k: v for k, v in outcome.items() if k != 'tsd'}])['unlinked attempt markers'], 1)
check('malformed dial ignored', dict(OL.marker_counts([None, 'bad'])), {})
notes['notes']['T-2']['dials'] = [marker, outcome]
linked = OL.compute(today=TODAY)
check('linked outcome counted once', linked['totals']['dials'], 4)
check('compute reconciles redial marker', linked['totals']['markers linked to outcomes'], 1)
check('legacy markers remain unlinked', linked['totals']['unlinked attempt markers'], 2)
check('console explains uncertainty', 'not confirmed missing outcomes' in OL.render(linked), True)
check('console distinguishes logged outcomes', 'logged outcome rows 4' in OL.render(linked), True)
for leak in ('T-2', '0003', '1000', '2000'):
    if leak in json.dumps(linked):
        fails.append('reconciled output carries %r' % leak)

# leads_final.json holds unslimmed Miami-Dade rows keyed 'Case #' / 'AuctionDate'
d = tempfile.mkdtemp()
with open(os.path.join(d, 'leads_final.json'), 'w') as f:
    json.dump([{'Case #': 'M-9', 'AuctionDate': '10/20/2026'}], f)
OL.HERE = d
OL._load = lambda p, dflt: json.load(open(p)) if os.path.exists(p) else dflt
raw = REAL_RAW()
check('leads_final mapped', [(x.get('case'), x.get('county'), x.get('auction')) for x in raw],
      [('M-9', 'MIAMI-DADE', '10/20/2026')])

print('\n'.join(fails) or 'outcome_learn: all checks pass')
sys.exit(1 if fails else 0)
