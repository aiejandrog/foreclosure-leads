#!/usr/bin/env python3
"""_rfstatustest.py — rf_status.classify / parse_page / merge / load_for_board on fixtures.
Run: python _rfstatustest.py"""
import datetime
import json
import os
import tempfile
import rf_status as R

fails = []
def check(name, ok, info=''):
    print(('ok   ' if ok else 'FAIL ') + name)
    if not ok:
        fails.append(name); print('     ', info)

for text, want in (('Auction Status Canceled per County', 'cancelled'),
                   ('Auction StatusCanceled per Order', 'cancelled'),
                   ('Auction Status Canceled per Bankruptcy', 'bk'),
                   ('Auction Status Redeemed', 'redeemed'),
                   ('Auction Status Judgment Vacated/Dismissed', 'vacated'),
                   ('Auction Status Rescheduled', 'rescheduled'),
                   ('Auction Status Auction to be rescheduled', 'rescheduled'),
                   ('Auction Status Other', 'other'),
                   ('Auction Sold 10/16/2026 Sold To 3rd Party Bidder', 'sold'),
                   ('', None)):
    check('classify %r' % text, R.classify(text) == want, R.classify(text))

raw = {'closed': [{'c': '2025-000001-CA-01', 't': 'Auction Status Redeemed'},
                  {'c': '2025-000002-CA-01', 't': 'Auction Status Canceled per Bankruptcy'},
                  {'c': '2025-000003-CA-01', 't': 'Auction Status Canceled per County'}],
       'waiting': [{'c': '2025-000003-CA-01', 't': ''}, {'c': '', 't': ''}]}
got, w = R.parse_page(raw, '2026-10-20', 'MIAMI-DADE', '2026-10-16')
check('parse: 3 closed read', len(got) == 3 and w == {'2025-000003-CA-01'}, (got, w))
today = datetime.date(2026, 10, 16)
new = R.merge({}, got, {'2026-10-20': w}, {'2026-10-20'}, today)
check('merge: a case back in Waiting is not held', '2025-000003-CA-01' not in new and len(new) == 2, new)
old = {'2025-000001-CA-01': {'st': 'redeemed', 'sale': '2026-10-20', 'county': 'M', 'ts': 'x'},
       '2025-000009-CA-01': {'st': 'bk', 'sale': '2026-11-02', 'county': 'M', 'ts': 'x'},
       '2025-000008-CA-01': {'st': 'bk', 'sale': '2026-10-01', 'county': 'M', 'ts': 'x'}}
new = R.merge(old, {}, {'2026-10-20': {'2025-000001-CA-01'}}, {'2026-10-20'}, today)
check('merge: reinstated entry removed, unread date kept, long-passed pruned',
      list(new) == ['2025-000009-CA-01'], new)
new = R.merge(old, {}, {}, set(), today)
check('merge: nothing read removes nothing but the passed sale', set(new) == {'2025-000001-CA-01', '2025-000009-CA-01'}, new)

new = R.merge({'X': {'st': 'bk', 'sale': '2026-10-20'}}, {'X': {'st': 'other', 'sale': '2026-10-20'}}, {}, {'2026-10-20'}, today)
check('merge: an unrecognised status does not lift a hold', new['X']['st'] == 'bk', new)
d = tempfile.mkdtemp()
p = os.path.join(d, 'rf.json')
json.dump({'2025-000001-CA-01': {'st': 'redeemed', 'sale': '2026-10-20'},
           '2025-000002-CA-01': {'st': 'bk', 'sale': '2026-10-20'},
           '2025-000003-CA-01': {'st': 'cancelled', 'sale': '2026-10-20'},
           '2025-000004-CA-01': {'st': 'vacated', 'sale': '2026-10-20'},
           '2025-000005-CA-01': {'st': 'rescheduled', 'sale': '2026-10-20'}}, open(p, 'w'))
rows = [{'case': '2025-000001-CA-01', 'auction': '10/20/2026'},
        {'case': '2025-000002-CA-01', 'auction': '2026-10-20'},
        {'case': '2025-000003-CA-01', 'auction': '10/20/2026'},
        {'case': '2025-000004-CA-01', 'auction': '11/03/2026'},     # sale moved: not this sale's status
        {'case': '2025-000005-CA-01', 'auction': '10/20/2026'},
        {'case': '2025-000006-CA-01', 'auction': '10/20/2026'}]
n = R.load_for_board(rows, p)
check('board: stamped redeemed/bk/cancelled only', n == 3, n)
check('board: redeemed and bk carry rfHold', rows[0].get('rfHold') and rows[1].get('rfHold'), rows)
check('board: cancelled is a chip, not a hold', rows[2].get('rf') and not rows[2].get('rfHold'), rows[2])
check('board: other sale date, rescheduled, unknown untouched', not any('rf' in r for r in rows[3:]), rows[3:])
check('board: no file stamps nothing', R.load_for_board(rows, os.path.join(d, 'none.json')) == 0)
arc = {'a': {'county': 'BROWARD', 'auction': '2026-10-20'}, 'b': {'county': 'BROWARD', 'auction': '2026-10-20'},
       'c': {'county': 'MIAMI-DADE', 'auction': '2026-10-01'}, 'd': {'county': 'PALM BEACH', 'auction': '2027-06-01'}}
check('dates: future, in horizon, deduped', R.dates_to_read(arc, today) == [('BROWARD', '2026-10-20')], R.dates_to_read(arc, today))

import call_mode as CM
_b = {'case': '2025-000001-CA-01', 'auction': '11/20/2026', 'phones': ['3055550101'], 'phrank': ['A'], 'st': 'FC',
      'name': 'X Y', 'addr': '1 A ST', 'z': '33101', 'a': '1 A ST, MIAMI, FL 33101', 'days': 30, 'county': 'MIAMI-DADE'}
check('call mode: a normal row is dialed', len(CM.call_rows([dict(_b)], optouts={}, deads={}, cap=10)[0]) == 1)
check('call mode: an rfHold row is dropped', len(CM.call_rows([dict(_b, rfHold=True)], optouts={}, deads={}, cap=10)[0]) == 0)
raise SystemExit(1 if fails else 0)
