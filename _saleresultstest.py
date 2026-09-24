#!/usr/bin/env python3
"""_saleresultstest.py — sale_results.classify() against synthetic dockets.

Two kinds of fixture. The first block is written for the test, one rule per case. The second
(REAL) is the clerk's own wording for five cases from the 2026-09-24 12-case verification, as the
desktop pulled them from the public OCS docket that day: person names and street addresses were
redacted there, receipt lines (which carry payer names) are left out here, law firms stay because
they are the filing party of record. Those five pin the verdicts the pipeline must reach on the
exact strings Miami-Dade writes.

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

# ---- REAL clerk wording (OCS, pulled 2026-09-24, redacted) -------------------------------------
def k(d, x, c, code):
    return {'eventDate': d, 'docketDescrition': x, 'comments': c, 'docketCode': code}

R_023462 = [  # sold 09-23 to the plaintiff; a Chapter 13 notice the same day
    k('07/27/2026', 'Notice of Sale', 'Sale 08/24/26', 'NOTSCV'),
    k('08/06/2026', 'Mortgage Collection Fee', 'POST AND AUCTION SALE FEE', 'SFEE'),
    k('08/13/2026', 'Mortgage Foreclosure Publication Fee', 'SALE DATE: 8-24-26', 'MPUB'),
    k('08/14/2026', 'Motion to Cancel Sale', 'AND RESCHEDULE FORECLOSURE SALE', 'MCSAL'),
    k('08/19/2026', 'Special Sets', 'Motion to Cancel and Reschedule Foreclosure Sale', 'SPECSETS'),
    k('08/24/2026', 'Order Cancelling Foreclosure Sale', 'Sale Date: AUGUST 24, 2026 AND RESET FOR SEPTEMBER 23, 2026 AT 9:00 A.M.', 'ORCN'),
    k('08/24/2026', 'No Further Judicial Action', '', 'NFJA'),
    k('08/26/2026', 'Notice of Sale', 'SALE DATE: 9/23/26', 'NOTSCV'),
    k('09/16/2026', 'Mortgage Foreclosure Publication Fee', 'SALE DATE: 9/23/26', 'MPUB'),
    k('09/23/2026', 'Mortgage Foreclosure Sale', '', 'SALE'),
    k('09/23/2026', 'Notice of Bankruptcy', 'CASE FILING FOR [NAME] CASE NO. 26-22668-RAM FILED AFTER THE SALE', 'NOTBCV'),
    k('09/23/2026', 'Bid Amount', 'PL/53643', 'BIDSCV'),
    k('09/23/2026', 'Mortgage Foreclosure Deposit', '24-PL/53643/DOC STAMPS', 'MFDPCV'),
    k('11/06/2026', 'N/J Trials', 'FJNT', 'NJTRIAL'),
]
R_013492 = [  # HOA case, sale 10-05, nothing changes it
    k('08/24/2026', 'Default Final Judgment', '', 'DJUD'),
    k('09/10/2026', 'Notice of Sale', 'SALE OF 10/5/2026', 'NOTSCV'),
    k('09/23/2026', 'Mortgage Foreclosure Publication Fee', 'SALE OF 10/05/2026', 'MPUB'),
    k('10/05/2026', 'Mortgage Foreclosure Sale', '', 'SALE'),
]
R_006803 = [  # reset 08-17 -> 09-28, emergency motion to cancel on 09-24, no ruling
    k('08/05/2026', 'Mortgage Foreclosure Publication Fee', 'SALE DATE: 8-17-26', 'MPUB'),
    k('08/11/2026', 'Motion to Cancel Sale', '', 'MCSAL'),
    k('08/12/2026', 'Order Cancelling Foreclosure Sale', 'Sale Date: AUGUST 17, 2026 AND RESET FOR SEPTEMBER 28, 2026 AT 9:00 A.M.', 'ORCN'),
    k('08/13/2026', 'Emergency Motion', 'TO CANCEL, VACATE, OR STAY FORECLOSURE SALE BASED UPON PAYMENT OF REINSTATEMENT FUNDS', 'EMGMCV'),
    k('08/31/2026', 'Notice of Sale', 'SALE OF 9/28/2026', 'NOTSCV'),
    k('09/02/2026', 'Motion for Clarification', '', 'MCLRCV'),
    k('09/11/2026', 'Amended Notice of Hearing', '9/24/26 @ 9:00am, via Zoom', 'ANHGCV'),
    k('09/16/2026', 'Mortgage Foreclosure Publication Fee', 'SALE OF 9/28/2026', 'MPUB'),
    k('09/24/2026', 'Emergency Motion', 'TO CANCEL OR POSTPONE FORECLOSURE SALE SCHEDULED FOR SEPTEMBER 28, 2026 BASED UPON PAYMENT OF THE $230,283.71 REINSTATEMENT AMOUN TO MORTGAGE SERVICER', 'EMGMCV'),
    k('09/28/2026', 'Mortgage Foreclosure Sale', '', 'SALE'),
]
R_013918 = [  # reset 08-03 -> 09-28; amended final judgment 09-24 keeps the sale
    k('07/30/2026', 'Motion Calendar', 'MOTION TO CANCEL SALE', 'MOTCAL'),
    k('07/30/2026', 'Order Cancelling Foreclosure Sale', 'Sale Date: AUGUST 3, 2026 AND RESET FOR SEPTEMBER 28, 2026 AT 9:00 A.M.', 'ORCN'),
    k('08/03/2026', 'Certificate of Mailing', "OF ORDER GRANTING DEFENDANT'S MOTION TO CANCEL AND RESCHEDULE SALE (DE 89)", 'COFMCV'),
    k('08/05/2026', 'Mortgage Collection Fee', 'RESET FEE', 'SFEE'),
    k('08/11/2026', 'Motion to Amend', 'Final Judgment', 'MAMD'),
    k('08/31/2026', 'Notice of Sale', 'SALE OF 9/28/2026', 'NOTSCV'),
    k('09/16/2026', 'Mortgage Foreclosure Publication Fee', 'SALE OF 9/28/2026', 'MPUB'),
    k('09/17/2026', 'Assignment of Bid', 'CONDITIONAL', 'ABID'),
    k('09/24/2026', 'Motion Calendar', 'PLAINTIFF S MOTION TO AMEND FINAL JUDGMENT OR IN THE ALTERNATIVE TO / RECOVER ADDITIONAL ADVANCES FROM FORECLOSURE SALE PROCEEDS AND / SURPLUS FUNDS', 'MOTCAL'),
    k('09/24/2026', 'Amended Final Judgment', '', 'AFJUCV'),
    k('09/28/2026', 'Mortgage Foreclosure Sale', '', 'SALE'),
]
R_026274 = [  # reset to 09-28 with an amended judgment on 08-11
    k('08/07/2026', 'Notice of Filing:', 'Affidavit of Additional Advances', 'NFILCV'),
    k('08/11/2026', 'Order Resetting Foreclosure Sale', "Sale Date: SEPTEMBER 28, 2026 AT 9:00 A.M. AND GRANTING PLAINTIFF'S MOTION TO AMEND FINAL JUDGMENT OF FORECLOSURE NUN PRO TUNC", 'ORSE'),
    k('08/11/2026', 'Amended Final Judgment', '', 'AFJUCV'),
    k('08/31/2026', 'Notice of Sale', 'SALE OF 9/28/2026', 'NOTSCV'),
    k('09/16/2026', 'Mortgage Foreclosure Publication Fee', 'SALE OF 9/28/2026', 'MPUB'),
    k('09/23/2026', 'Motion:', 'FOR SUBSTITUTION OF COUNSEL', 'MOTICV'),
    k('09/24/2026', 'Order:', "GRANTING PLAINTIFF'S MOTION FOR SUBSTITUTION OF COUNSEL AND DIRECTING CLERK OF COURT TO CHANGE", 'ORDDCV'),
    k('09/28/2026', 'Mortgage Foreclosure Sale', '', 'SALE'),
]
T = D(2026, 9, 24)
v = S.classify(R_023462, '09/23/2026', T)
check('REAL 023462: held', v['st'] == 'held', v)
check('REAL 023462: plaintiff took it back', v.get('pl') == 1 and 'plaintiff' in v['why'], v.get('why'))
check('REAL 023462: bankruptcy on the sale day flagged', v.get('bkb') == '2026-09-23' and 'sale day' in v['why'], v.get('why'))
v = S.classify(R_013492, '10/05/2026', T)
check('REAL 013492: scheduled', v['st'] == 'scheduled' and 'amj' not in v, v)
v = S.classify(R_006803, '09/28/2026', T)
check('REAL 006803: at risk on the 09-24 emergency motion', v['st'] == 'at_risk' and v['d'] == '2026-09-24', v)
check('REAL 006803: the 08-12 reset is not a cancellation of 09-28', 'nd' not in v, v)
v = S.classify(R_006803[:-2] + R_006803[-1:], '09/28/2026', T)
check('REAL 006803 without the 09-24 motion: scheduled', v['st'] == 'scheduled', v)
v = S.classify(R_013918, '09/28/2026', T)
check('REAL 013918: still scheduled', v['st'] == 'scheduled', v)
check('REAL 013918: amended judgment 09-24', v.get('amj') == '2026-09-24', v.get('amj'))
check('REAL 013918: the motion-calendar cancel before the reset is history', 'd' not in v, v)
v = S.classify(R_026274, '09/28/2026', T)
check('REAL 026274: scheduled, amended judgment 08-11 kept', v['st'] == 'scheduled' and v.get('amj') == '2026-08-11', v)
# the same order text, pointed at the 08-24 sale it cancelled
v = S.classify(R_023462[:8], '08/24/2026', D(2026, 8, 25))
check('REAL 023462 at the 08-24 sale: reset to 09-23', v['st'] == 'reset' and v.get('nd') == '2026-09-23', v)

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
