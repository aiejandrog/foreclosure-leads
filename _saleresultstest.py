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
check('held: certificate on file -> sale_outcome sold', v.get('sale_outcome') == 'sold' and v['sale_held']['certificate'] == '2026-09-24', v.get('sale_held'))
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
    e('08/19/2026', 'Order Cancelling Foreclosure Sale', 'Sale Date: AUGUST 20, 2026'),
    e('08/26/2026', 'Notice of Sale', 'SALE OF 9/28/2026'),
]
v = S.classify(prior, '09/28/2026', D(2026, 9, 24))
check('a cancel naming an earlier sale date: still scheduled', v['st'] == 'scheduled', v)
v = S.classify([e('09/18/2026', 'Order Cancelling Foreclosure Sale', 'Sale Date: SEPTEMBER 22, 2026')], '09/22/2026', D(2026, 9, 24))
check('sweep shape: cancelled before a passed sale -> cancelled, not unknown', v['st'] == 'cancelled', v)
# sweep: board says 09-28, the docket reset it to 01/04/2027 months ago
stale = [e('03/10/2026', 'Order Cancelling Foreclosure Sale', 'Sale Date: APRIL 6, 2026 AND RESET FOR SEPTEMBER 28, 2026'),
         e('06/15/2026', 'Order Cancelling Foreclosure Sale', 'Sale Date: SEPTEMBER 28, 2026 AND RESET FOR JANUARY 4, 2027'),
         e('11/20/2026', 'Notice of Sale', 'SALE OF 1/4/2027')]
v = S.classify(stale[:2], '09/28/2026', D(2026, 9, 24))
check('stale board date: the docket moved it to 01/04/2027', v['st'] == 'reset' and v.get('nd') == '2027-01-04', v)

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
check('REAL 023462: #53 vocabulary', v.get('sale_outcome') == 'held_no_certificate_yet'
      and v['sale_held']['certificate'] is None and v['sale_held']['bankruptcy_same_day'] is True, v.get('sale_held'))
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

# ---- REAL: the all-Miami sweep's passed and moved sales (desktop, OCS 2026-09-24, sale lines only)
W = [
 ('2025-000483', '09/22/2026', [k('08/25/2026', 'Notice of Sale', 'SALE OF 9/22/2026', 'NOTSCV'),
   k('09/22/2026', 'Mortgage Foreclosure Sale', '', 'SALE'),
   k('09/22/2026', 'Motion to Set Aside/Vacate', 'SEPTEMBER 22, 2026 SALE', 'MSAVCV')],
  lambda v: v['st'] == 'unknown' and 'set the sale aside' in v['why']),
 ('2025-008534', '09/22/2026', [k('08/25/2026', 'Notice of Sale', 'SALE OF 9/22/2026', 'NOTSCV'),
   k('09/09/2026', 'Motion - Other', "DEFENDANT [NAME]'S VERIFIED EMERGENCY MOTION TO VACATE ORDER OF DEFAULT AND SUMMARY FINAL JUDGMENT, QUASH/SET ASIDE CONSTRUCTIVE SERVICE,CANCEL OR STAY SEPTEMBER 22, 2026 FORECLOSURE SALE, AND FOR EV", 'MOTIOTHCV'),
   k('09/17/2026', 'Mortgage Collection Fee', 'POST AND AUCTION SALE FEE', 'SFEE'),
   k('09/21/2026', 'Motion to Cancel Sale', '', 'MCSAL'),
   k('09/22/2026', 'Mortgage Foreclosure Sale', '', 'SALE'),
   k('09/22/2026', 'Order Cancelling Foreclosure Sale', 'Sale Date: SEPTEMBER 22, 2026 (ORDER RECEIVED AFTER PROPERTY SOLD)', 'ORCN'),
   k('09/22/2026', 'Objection to Sale', "DEFENDANT'S OBJECTION TO FORECLOSURE SALE", 'OBJSCV'),
   k('09/23/2026', 'Emergency Motion', 'TO VACATE/SET ASIDE FORECLOSURE SALE, WITHHOLD CERTIFICATE OF TITLE, AND FOR EXPEDITED EVIDENTIARY HEARING', 'EMGMCV'),
   k('09/23/2026', 'Order Vacating Sale', 'SET ASIDE FORECLOSURE SALE, WITHHOLD CERTIFICATE OF TITLE, AND FOR EXPEDITED EVIDENTIARY HEARING', 'OVSA'),
   k('09/23/2026', 'Mortgage Collection Fee', 'RESET FEE', 'SFEE')],
  lambda v: v['st'] == 'vacated'),
 ('2024-024235', '09/22/2026', [k('08/13/2026', 'Mortgage Collection Fee', 'POST AND AUCTION SALE FEE', 'SFEE'),
   k('08/25/2026', 'Notice of Sale', 'SALE OF 9/22/2026', 'NOTSCV'),
   k('09/22/2026', 'Mortgage Foreclosure Sale', '', 'SALE'),
   k('09/22/2026', 'Notice of Bankruptcy', 'BKC NO: 26-22587-RAM', 'NOTBCV')],
  lambda v: v['st'] == 'unknown' and 'bankruptcy was filed on the sale day' in v['why']),
 ('2025-019952', '09/22/2026', [k('08/05/2026', 'Filing Note and Mortgage', 'Original Cancelled Note and Copy of Recorded Mortgage', 'FONM'),
   k('08/20/2026', 'Mortgage Collection Fee', 'POST AND AUCTION SALE FEE', 'SFEE'),
   k('08/25/2026', 'Notice of Sale', 'SALE OF 9/22/2026', 'NOTSCV'),
   k('09/22/2026', 'Mortgage Foreclosure Sale', '', 'SALE')],
  lambda v: v['st'] == 'unknown'),
 ('2026-007129', '09/22/2026', [k('08/25/2026', 'Notice of Sale', 'SALE OF 9/22/2026', 'NOTSCV'),
   k('09/22/2026', 'Mortgage Foreclosure Sale', '', 'SALE'),
   k('09/22/2026', 'Mortgage Foreclosure Deposit', 'PROPERTY REDEEMED  AFTER SALE BY: [NAME]', 'MFDPCV')],
  lambda v: v['st'] == 'redeemed'),
 ('2019-013056', '09/23/2026', [k('08/26/2026', 'Notice of Sale', 'SALE DATE: 9/23/26', 'NOTSCV'),
   k('09/17/2026', 'Suggestion of Bankruptcy', 'BKC: 26-22375', 'SGBK')],
  lambda v: v['st'] == 'unknown' and 'before it' in v['why'] and v.get('bkb') == '2026-09-17'),
 ('2009-074573', '09/28/2026', [k('07/08/2026', 'Notice of Filing:', 'RELIEF FROM BANKRUPTCY STAY', 'NFILCV'),
   k('07/09/2026', 'Motion to Set/Reset/Reschedule Foreclosure Sale', '', 'MSFS'),
   k('07/09/2026', 'Order Resetting Foreclosure Sale', 'Sale Date: SEPTEMBER 28, 2026', 'ORSE'),
   k('08/31/2026', 'Notice of Sale', 'SALE OF 9/28/2026', 'NOTSCV'),
   k('09/15/2026', 'Motion to Cancel Sale', '', 'MCSAL'),
   k('09/24/2026', 'Motion Calendar', "Defendant's Emergency Motion to Cancel Sale Date Scheduled for September 28, 2026", 'MOTCAL'),
   k('09/24/2026', 'Order Cancelling Foreclosure Sale', 'Sale Date: SEPTEMBER 28, 2026 AND RESET FOR JANUARY 4, 2027 AT 9:00 A.M.', 'ORCN'),
   k('01/04/2027', 'Mortgage Foreclosure Sale', '', 'SALE')],
  lambda v: v['st'] == 'reset' and v.get('nd') == '2027-01-04'),
 ('2024-000195', '09/28/2026', [k('08/31/2026', 'Notice of Sale', 'SALE OF 9/28/2026', 'NOTSCV'),
   k('09/17/2026', 'Motion to Cancel Sale', '', 'MCSAL'),
   k('09/22/2026', 'Motion Calendar', '[DIN 113] DEFENDANT [NAME]S MOTION TO CONTINUE/CANCEL / FORECLOSURE SALE', 'MOTCAL'),
   k('09/22/2026', 'Order to Cancel Sale Date', 'Sale Date: [SEPTEMBER 28, 2026]AND RESET FOR NOVEMBER 9, 2026 AT 9:00 A.M.', 'OCSD'),
   k('09/24/2026', 'Mortgage Collection Fee', 'Reset Fee for 11-09-2026 Sale', 'SFEE'),
   k('11/09/2026', 'Mortgage Foreclosure Sale', '', 'SALE')],
  lambda v: v['st'] == 'reset' and v.get('nd') == '2026-11-09'),
 ('2023-002984', '10/13/2026', [k('09/14/2026', 'Notice of Sale', 'Sale Date: 10/13/26', 'NOTSCV'),
   k('09/21/2026', 'Order Resetting Foreclosure Sale', 'Sale Date: JANUARY 4, 2027(AMENDED)', 'ORSE'),
   k('01/04/2027', 'Mortgage Foreclosure Sale', '', 'SALE')],
  lambda v: v['st'] == 'reset' and v.get('nd') == '2027-01-04'),
]
for case, sd, dk, ok in W:
    v = S.classify(dk, sd, T)
    check('SWEEP %s: %s %s' % (case, v['st'], v.get('nd') or ''), ok(v), v)

# ---- a line's other dates are not the sale date (surplus deadline, hearing, filing) -----------
# A notice of sale reciting the surplus-claim deadline used to become the "newest sale-setting line
# names a LATER date", so a sale days away read as reset months out and left the urgent lane.
v = S.classify([e('09/10/2026', 'Notice of Sale', 'SALE OF 10/1/2026. SURPLUS CLAIMS MUST BE FILED BY 11/30/2026')],
               '10/01/2026', D(2026, 9, 25))
check('surplus deadline on the notice: the sale stays 10/01', v['st'] == 'scheduled' and 'nd' not in v, v)
v = S.classify([e('09/10/2026', 'Notice of Sale', 'Sale Date: OCTOBER 1, 2026; ANY CLAIM TO SURPLUS NO LATER THAN NOVEMBER 30, 2026')],
               '10/01/2026', D(2026, 9, 25))
check('spelled-out surplus deadline: the sale stays 10/01', v['st'] == 'scheduled' and 'nd' not in v, v)
v = S.classify([e('09/26/2026', 'Order Cancelling Foreclosure Sale',
                  'Sale Date: SEPTEMBER 28, 2026 AND RESET FOR OCTOBER 26, 2026 AT 9:00 A.M. OBJECTIONS DUE BY DECEMBER 1, 2026')],
               '09/28/2026', D(2026, 9, 27))
check('reset order with a later objection deadline: moved to the RESET date', v['st'] == 'reset' and v.get('nd') == '2026-10-26', v)
v = S.classify([e('09/28/2026', 'Mortgage Foreclosure Sale Cancelled', ''),
                e('09/30/2026', 'Notice of Sale', 'SALE OF 10/20/2026 CLAIMS WITHIN 60 DAYS, DEADLINE 12/19/2026')],
               '09/28/2026', D(2026, 10, 1))
check('re-notice after a cancel: the new sale date, not the claim deadline', v['st'] == 'reset' and v.get('nd') == '2026-10-20', v)
v = S.classify([e('08/20/2026', 'Mortgage Foreclosure Sale Cancelled', ''),
                e('08/26/2026', 'Notice of Sale', 'SALE OF 9/28/2026 SURPLUS CLAIMS FILED BY 11/27/2026')],
               '09/28/2026', D(2026, 9, 24))
check('the notice that SET this sale still clears an older cancel when it recites a deadline', v['st'] == 'scheduled', v)
v = S.classify([e('06/15/2026', 'Order Cancelling Foreclosure Sale', 'Sale Date: SEPTEMBER 28, 2026 AND RESET FOR JANUARY 4, 2027'),
                e('07/01/2026', 'Notice of Sale', 'SALE OF 1/4/2027 SURPLUS CLAIMS FILED BY 3/5/2027')],
               '09/28/2026', D(2026, 9, 24))
check('stale board date + deadline on the newest notice: the reset date, not the deadline', v['st'] == 'reset' and v.get('nd') == '2027-01-04', v)
check('sale dates: a reset order names both sales',
      S._sale_dates_in('Sale Date: AUGUST 24, 2026 AND RESET FOR SEPTEMBER 23, 2026 AT 9:00 A.M.') == [D(2026, 8, 24), D(2026, 9, 23)])
check('sale dates: a filing date before the sale date is not one',
      S._sale_dates_in('NOTICE FILED 09/01/2026 FOR SALE ON 10/01/2026') == [D(2026, 10, 1)])
check('sale dates: a hearing date is not one',
      S._sale_dates_in('SALE OF 10/1/2026, HEARING ON OBJECTIONS 11/5/2026') == [D(2026, 10, 1)])

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

# load_for_board: fresh verdicts only, moved sales move the clock, evidence ships
import json as _json, os as _os, tempfile as _tf
_fd, _fp = _tf.mkstemp(suffix='.json'); _os.close(_fd)
_json.dump({
    'A-1': {'st': 'reset', 'sale': '2026-09-28', 'd': '2026-09-22', 'nd': '2026-11-09', 'ts': '2026-09-24',
            'why': 'reset', 'ev': [{'d': '2026-09-%02d' % i, 'x': 'line %d' % i} for i in range(1, 6)]},
    'B-2': {'st': 'at_risk', 'sale': '2026-09-28', 'ts': '2026-09-20', 'why': 'old read'},
    'C-3': {'st': 'at_risk', 'sale': '2026-09-28', 'ts': '2026-09-23', 'why': 'yesterday'},
    'D-4': {'st': 'reset', 'sale': '2026-09-28', 'nd': '2026-08-01', 'ts': '2026-09-24'},
}, open(_fp, 'w'))
_rows = [{'case': 'A-1', 'auction': '09/28/2026'}, {'case': 'B-2', 'auction': '09/28/2026'},
         {'case': 'C-3', 'auction': '09/28/2026'}, {'case': 'D-4', 'auction': '09/28/2026'}]
_n = S.load_for_board(_rows, _fp, today=D(2026, 9, 25))
_os.remove(_fp)
_by = {r['case']: r for r in _rows}
check('board: moved sale moves the clock to the new date', _by['A-1']['auction'] == '11/09/2026'
      and _by['A-1']['sr'].get('was') == '2026-09-28', _by['A-1'])
check('board: newest 3 evidence lines ship', [e['x'] for e in _by['A-1']['sr'].get('ev', [])] == ['line 3', 'line 4', 'line 5'], _by['A-1'])
check('board: a verdict read 5 days ago is not shown', 'sr' not in _by['B-2'], _by['B-2'])
check('board: yesterday\'s read still shows', 'sr' in _by['C-3'], _by['C-3'])
check('board: a reset to an EARLIER date never moves the clock back', _by['D-4']['auction'] == '09/28/2026', _by['D-4'])
check('board: count', _n == 3, _n)

# read order: hot sales first, then oldest verdict / never read, today's reads skipped
_T = D(2026, 9, 25)
_win = {'HOT': (D(2026, 9, 28), None), 'NEAR-FRESH': (D(2026, 10, 1), None), 'FAR-NEW': (D(2026, 10, 20), None),
        'FAR-OLD': (D(2026, 10, 15), None), 'DONE': (D(2026, 9, 24), None), 'PAST': (D(2026, 9, 23), None)}
_res = {'NEAR-FRESH': {'sale': '2026-10-01', 'ts': '2026-09-24'}, 'FAR-OLD': {'sale': '2026-10-15', 'ts': '2026-09-22'},
        'DONE': {'sale': '2026-09-24', 'ts': '2026-09-25'}, 'HOT': {'sale': '2026-09-28', 'ts': '2026-09-24'}}
_o = [c for c, _, _ in S.read_order(_win, _res, _T)]
check('read order: hot first, never-read, oldest, freshest last; today\'s skipped',
      _o == ['PAST', 'HOT', 'FAR-NEW', 'FAR-OLD', 'NEAR-FRESH'], _o)

check('coverage gap: never read, another sale, and a 4-day-old read count; fresh ones do not',
      S.coverage_gap({'A': (D(2026, 10, 1), None), 'B': (D(2026, 10, 2), None), 'C': (D(2026, 10, 3), None),
                      'E': (D(2026, 10, 4), None)},
                     {'B': {'sale': '2026-09-30', 'ts': '2026-09-25'}, 'C': {'sale': '2026-10-03', 'ts': '2026-09-21'},
                      'E': {'sale': '2026-10-04', 'ts': '2026-09-22'}}, _T) == 3)

print('\n%d failed' % len(FAIL) if FAIL else '\nall passed')
sys.exit(1 if FAIL else 0)
