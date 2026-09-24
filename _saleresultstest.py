#!/usr/bin/env python3
"""_saleresultstest.py — sale_results.classify() against synthetic dockets.

The four shapes come from the 2026-09-24 12-case verification (MIAMI-VERIFY-12, defect 10 and the
sale-week list). Case numbers and dates are real; every docket line here is WRITTEN FOR THE TEST in
the clerk's usual style — no homeowner names, no copied docket text. The laptop paste in the PR
re-checks the same four against the live docket.

Run: python _saleresultstest.py
"""
import datetime
import sys

import sale_results as S

D = datetime.date
FAIL = []


def check(name, cond, got=None):
    if not cond:
        FAIL.append(name)
        print('FAIL  %s  -> %r' % (name, got))
    else:
        print('ok    %s' % name)


def e(d, x, c=''):
    return {'eventDate': d, 'docketDescrition': x, 'comments': c}


# 2025-023462 shape: sale 09-23 held with a plaintiff bid; a Chapter 13 suggestion that morning.
held_bk = [
    e('06/10/2026', 'Final Judgment of Foreclosure'),
    e('08/20/2026', 'Notice of Sale', 'SALE DATE 09/23/2026'),
    e('09/23/2026', 'Suggestion of Bankruptcy', 'CHAPTER 13 CASE NO. 26-22668'),
    e('09/23/2026', 'Bid Amount', '$1,061,518.12 PLAINTIFF'),
    e('09/24/2026', 'Certificate of Sale'),
]
v = S.classify(held_bk, '09/23/2026', D(2026, 9, 24))
check('held: bid + certificate after the sale date', v['st'] == 'held', v)
check('held: bid amount read from the comment', v.get('bid') == 1061518.12, v.get('bid'))
check('held: bankruptcy on the sale day flagged', v.get('bkb') == '2026-09-23', v.get('bkb'))
check('held: the why says the sale may not stand', 'may not stand' in v.get('why', ''), v.get('why'))
check('held: evidence lines carried', any('Bid Amount' in x['x'] for x in v['ev']), v['ev'])

# 2026-013492 shape: a sale deposit line only.
v = S.classify([e('09/10/2026', 'Mortgage Foreclosure Sale Deposit', '$1,184.00')], '09/10/2026', D(2026, 9, 12))
check('held: a sale deposit alone reads as held', v['st'] == 'held', v)

# 2024-006803 shape: emergency motion to cancel the 09-28 sale, no ruling yet.
motion = [
    e('03/02/2026', 'Order Vacating Sale'),            # the 2025 vacated sale: before this sale's window
    e('08/25/2026', 'Order Rescheduling Foreclosure Sale', 'NEW SALE DATE 09/28/2026'),
    e('09/24/2026', 'Emergency Motion to Cancel Foreclosure Sale', 'REINSTATEMENT $230,283.71'),
]
v = S.classify(motion, D(2026, 9, 28), D(2026, 9, 24))
check('at risk: pending emergency motion', v['st'] == 'at_risk', v)
check('at risk: says emergency', v.get('why', '').startswith('emergency'), v.get('why'))
check('at risk: the reset that SET this sale is not a cancellation', 'reset' != v['st'], v)
check('at risk: an old vacatur before the window is ignored', v['st'] != 'vacated', v)

# the same motion, denied -> the sale stands
v = S.classify(motion + [e('09/25/2026', 'Order Denying Emergency Motion to Cancel Sale')], D(2026, 9, 28), D(2026, 9, 25))
check('denied motion: back to scheduled', v['st'] == 'scheduled', v)

# the same motion, granted with no new date -> cancelled
v = S.classify(motion + [e('09/25/2026', 'Order Granting Motion to Cancel Sale')], D(2026, 9, 28), D(2026, 9, 25))
check('granted motion: cancelled', v['st'] == 'cancelled', v)

# 2025-013918 shape: amended final judgment days before the sale.
amended = [
    e('06/04/2026', 'Final Judgment of Foreclosure'),
    e('08/30/2026', 'Notice of Sale', 'SALE DATE 09/28/2026'),
    e('09/24/2026', 'Amended Final Judgment of Foreclosure', 'AMOUNT $358,247.93'),
]
v = S.classify(amended, '09/28/2026', D(2026, 9, 24))
check('amended: sale still scheduled', v['st'] == 'scheduled', v)
check('amended: date carried', v.get('amj') == '2026-09-24', v.get('amj'))
check('amended: amount read when printed', v.get('ama') == 358247.93, v.get('ama'))
v = S.classify([e('09/24/2026', 'Motion for Amended Final Judgment')], '09/28/2026', D(2026, 9, 24))
check('amended: a motion for one is not one', 'amj' not in v, v)

# reset with a new date
v = S.classify([e('09/26/2026', 'Order Resetting Foreclosure Sale', 'SALE RESET TO 11/02/2026')], '09/28/2026', D(2026, 9, 27))
check('reset: moved to the new date', v['st'] == 'reset' and v.get('nd') == '2026-11-02', v)

# clerk event line: cancelled per bankruptcy
v = S.classify([e('09/28/2026', 'Mortgage Foreclosure Sale Cancelled', 'CANCELLED PER BANKRUPTCY')], '09/28/2026', D(2026, 9, 28))
check('clerk cancel line: cancelled (bankruptcy)', v['st'] == 'cancelled' and 'bankruptcy' in v['why'], v)

# an earlier sale's cancellation must not cancel the current one
prior = [
    e('08/19/2026', 'Order Cancelling Foreclosure Sale'),
    e('08/20/2026', 'Mortgage Foreclosure Sale Cancelled'),
]
v = S.classify(prior, '09/28/2026', D(2026, 9, 24), listed=D(2026, 9, 24))
check('listing after the cancel: an older sale, still scheduled', v['st'] == 'scheduled', v)

# passed with nothing on the docket yet
v = S.classify([e('08/30/2026', 'Notice of Sale')], '09/21/2026', D(2026, 9, 22))
check('passed, no result yet: unknown, not held', v['st'] == 'unknown', v)

# a motion or a denied order never produces an outcome by itself
v = S.classify([e('09/20/2026', 'Motion to Vacate Certificate of Sale')], '09/15/2026', D(2026, 9, 22))
check('motion to vacate after the sale: not vacated', v['st'] != 'vacated', v)
v = S.classify([e('09/15/2026', 'Certificate of Sale'), e('09/22/2026', 'Order Vacating Certificate of Sale')], '09/15/2026', D(2026, 9, 22))
check('order vacating the certificate: vacated', v['st'] == 'vacated', v)
v = S.classify([e('09/15/2026', 'Certificate of Sale'), e('09/18/2026', 'Objection to Sale')], '09/15/2026', D(2026, 9, 22))
check('objection after the sale: held + obj', v['st'] == 'held' and v.get('obj') == '2026-09-18', v)

# a closed bankruptcy before the sale is not a risk
v = S.classify([e('09/01/2026', 'Suggestion of Bankruptcy'), e('09/10/2026', 'Notice of Bankruptcy Dismissal')], '09/28/2026', D(2026, 9, 24))
check('bankruptcy dismissed before the sale: not at risk', v['st'] == 'scheduled' and 'bkb' not in v, v)
v = S.classify([e('09/20/2026', 'Suggestion of Bankruptcy', 'CH 13')], '09/28/2026', D(2026, 9, 24))
check('open bankruptcy line before the sale: at risk', v['st'] == 'at_risk', v)

# compact gen_dockets shape reads the same
v = S.classify([{'d': '09/23/2026', 'x': 'Certificate of Sale'}], '09/23/2026', D(2026, 9, 24))
check('compact dockets.json shape', v['st'] == 'held', v)

# window
w = S.window_cases(D(2026, 9, 24),
                   [{'Case #': '2025-013918-CA-01', 'AuctionDate': '09/28/2026'},
                    {'Case #': '2020-000001-CA-01', 'AuctionDate': '12/28/2026'},
                    {'Case #': 'CACE-25-000001', 'AuctionDate': '09/28/2026', 'county': 'BROWARD'}],
                   {'2025-023462-CA-01': {'auction': '2026-09-23', 'county': 'MIAMI-DADE', 'last_seen': '2026-09-23'},
                    '2024-000002-CA-01': {'auction': '2026-09-01', 'county': 'MIAMI-DADE'}},
                   D(2026, 9, 24))
check('window: upcoming lead + recent archive sale only', sorted(w) == ['2025-013918-CA-01', '2025-023462-CA-01'], w)
check('window: archive last_seen carried', w['2025-023462-CA-01'][1] == D(2026, 9, 23), w)

print('\n%d failed' % len(FAIL) if FAIL else '\nall passed')
sys.exit(1 if FAIL else 0)
