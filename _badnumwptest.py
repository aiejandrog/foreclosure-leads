"""_badnumwptest.py -- badnum_wp_compare.py on fake data: no board, no network, no real people."""
import contextlib
import datetime as dt
import io
import json
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ['DEALFLOW_DIR'] = tmp
os.environ['DEALFLOW_BK_ALLOW_UNCHECKED'] = '0'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import badnum_wp_compare as B
import whitepages_lookup as WL

TODAY = dt.date(2026, 10, 21)
fails = []
ran = [0]


def check(name, got, want):
    ran[0] += 1
    if got != want:
        fails.append('%s: got %r want %r' % (name, got, want))


notes = {
    'T-1': {'dials': [{'d': '2026-10-20', 'ph4': '0001', 'oc': 'badnum'}]},
    'T-2': {'dials': [{'d': '2026-10-19', 'ph4': '0003', 'oc': 'wrong'},
                      {'d': '2026-10-19', 'ph4': '0004', 'oc': 'talked'}]},
    'T-3': {'dials': [{'d': '2026-10-18', 'ph4': '0005', 'oc': 'badnum'}]},
    'T-4': {'dials': [{'d': '2026-09-01', 'ph4': '0006', 'oc': 'wrong'}]},
    'T-5': {'dials': [{'d': '2026-10-20', 'ph4': '0007', 'oc': 'noanswer'}]},
    'GONE': {'dials': [{'d': '2026-10-20', 'ph4': '9999', 'oc': 'badnum'}]},
}
OWNER = 'GARCIA MARIA ELENA'
ADDR = '1 TEST ST, MIAMI, FL 33101'
leads = {
    'T-1': {'case': 'T-1', 'owner': OWNER, 'addr': ADDR, 'mail': '9 OTHER RD, HIALEAH, FL 33010',
            'eq': 40, 'eqstate': 'priced', 'phones': ['3055550001'], 'phsrc': ['tr']},
    'T-2': {'case': 'T-2', 'owner': OWNER, 'addr': ADDR, 'mail': ADDR, 'eq': 20, 'eqstate': 'unpriced',
            'phones': ['3055550003', '3055550004'], 'phsrc': ['tr', 'wp']},
    'T-3': {'case': 'T-3', 'owner': OWNER, 'addr': ADDR, 'mail': ADDR, 'eq': 55, 'eqstate': 'clear',
            'phones': ['3055550005'], 'phsrc': ['bd']},
    'T-4': {'case': 'T-4', 'owner': 'ACME HOLDINGS LLC', 'addr': ADDR, 'phones': ['3055550006'], 'phsrc': ['tr']},
}
# T-1: WP repeats the dead number and adds two new owner numbers. T-3: WP only has what we had.
cache = {
    'T-1': {'_http': 200, 'result': {'ownership_info': {'person_owners': [
        {'name': 'Maria Elena Garcia', 'phones': [{'number': '3055550001'}, {'number': '7865551111'},
                                                  {'number': '7865552222'}]}]}}},
    'T-3': {'_http': 200, 'result': {'ownership_info': {'person_owners': [
        {'name': 'Maria Elena Garcia', 'phones': [{'number': '3055550005'}]}]}}},
}

WL.load_all_leads = lambda: list(leads.values())
WL._lead_owners = lambda r: r.get('owner') or ''
WL.contact_hold = lambda r: ''
B.load_leads = lambda: {k: dict(v) for k, v in leads.items()}

bad = B.bad_dials(notes, today=TODAY)
check('only bad/wrong cases', sorted(bad), ['GONE', 'T-1', 'T-2', 'T-3', 'T-4'])
check('window', sorted(B.bad_dials(notes, days=14, today=TODAY)), ['GONE', 'T-1', 'T-2', 'T-3'])
check('later conversation seen', bad['T-2']['convo'], True)

res, todo = B.compare(bad, B.load_leads(), cache, {}, set())
t = res['totals']
check('bad leads', t['bad-number leads'], 5)
check('gone lead', t['lead no longer in any lead file'], 1)
check('looked up', t['looked up on Whitepages'], 2)
check('WP repeats dead number (T-1 and T-3)', t['  WP lists the number we found dead'], 2)
check('WP new owner number', t["  WP had a NEW number graded the owner's"], 1)
check('new owner numbers counted', t['    new owner-graded numbers'], 2)
check('WP same numbers only', t['  WP had only numbers we already had'], 1)
check('reached lead not bought', t['not looked up: a conversation is already logged (no spend)'], 1)
check('company not bought', t['not looked up: company owner (WP is person-only)'], 1)
check('nothing eligible to buy', [WL._lead_key(r) for r in todo], [])
check('source of bad numbers', res['bad_number_source'], {'tr': 3, 'bd': 1, 'not on board': 1})
m = res['mail_lane']
check('mailable = verified equity + mail address', m['MAILABLE: verified equity + mailing address'], 2)
check('absentee counted', m['  of those, mailing address differs from the property'], 1)
check('unverified equity not mailable', m['equity not verified (not clear/priced, or no/0% figure)'], 1)

# opted-out lead is neither bought nor counted mailable
res2, todo2 = B.compare(bad, B.load_leads(), {}, {}, {'T-1'})
check('opt-out not bought', 'T-1' in [WL._lead_key(r) for r in todo2], False)
check('opt-out mail held', res2['mail_lane']['held / opted out / company owner'], 2)
check('eligible to buy without cache', [WL._lead_key(r) for r in B._todo_order(todo2)], ['T-3'])

# --lookup refuses without a dollar cap, before reading anything
with contextlib.redirect_stdout(io.StringIO()) as out:
    rc = B.main(['--lookup'])
check('lookup without --max-spend refused', rc, 2)
with contextlib.redirect_stdout(io.StringIO()):
    rc = B.main(['--lookup', '--max-spend', 'inf'])
check('infinite --max-spend refused', rc, 2)
check('odd oc shapes ignored', sorted(B.bad_dials({'X': {'dials': [{'oc': ['badnum']}, {'oc': {'a': 1}}]}, 'Y': [1]}, today=TODAY)), [])

with contextlib.redirect_stdout(io.StringIO()) as out:
    B._provider_log('  ok   T-1  1 TEST ST -> Property: 1 owner(s)')
    B._provider_log('  AUTH ERROR (403) - key rejected. Stopping.')
check('per-lead log line dropped, auth line kept', out.getvalue().count('T-1'), 0)
check('auth line kept', 'AUTH ERROR' in out.getvalue(), True)

blob = json.dumps(res) + B.render(dict(res, generated='x', notes_received_utc=None, window_days=None),
                                  todo, {'price': 0.22, 'worst': 0})
for leak in ('T-1', 'T-2', '3055550001', '7865551111', 'GARCIA', 'TEST ST', 'HIALEAH'):
    if leak in blob:
        fails.append('output leaks %r' % leak)

if fails:
    print('FAIL _badnumwptest.py')
    for f in fails:
        print('  ' + f)
    sys.exit(1)
print('PASS _badnumwptest.py (%d checks)' % ran[0])
