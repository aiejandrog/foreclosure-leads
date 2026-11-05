"""_deepequitytest.py -- deep_equity: every lien summed, fail-loud equity, append-only log.

The main fixture reproduces the 2026-11-05 acceptance case (an FHA loan modified three times with
three HUD partial-claim mortgages, a released HOA lien, a lender's lis pendens) with the SAME
amounts, dates and document shapes, but INVENTED names, parcel, legal description, case and
instrument numbers: this repo is public and a real homeowner's identifiers never go in it
(CLAUDE.md, footprint.py). The real-identity check runs on the laptop against the recorded live
responses (deep_equity.py --accept with a 'replay' fixture), outside the repo.

    python _deepequitytest.py
"""
import copy
import json
import os
import sys
import tempfile

import deep_equity as D

FAILS = []


def ok(cond, label):
    print(('PASS ' if cond else 'FAIL ') + label)
    if not cond:
        FAILS.append(label)


def ms(y, m, d):
    import datetime
    t = datetime.datetime(y, m, d) - datetime.datetime(1970, 1, 1)
    return '/Date(%d)/' % int(t.total_seconds() * 1000)


OWNER = 'SAMPLE, ROBERT KEITH'
COB = 'EXAMPLE DELEON, MARIA'
FOLIO = '000000-00-0001'
LEGAL = 'EXAMPLE ESTATES 101-22 B LOT 7 BLK 4'
CASE = 'CACE26900001'
HOA = 'EXAMPLE ESTATES HOMEOWNERS ASSOCIATION INC'
HUD = 'SECRETARY OF HOUSING AND URBAN DEVELOPMENT'


def row(name, party, cross, date, inst, amt, doc, legal=LEGAL, parcel='', case=''):
    return {'Name': name, 'Party': party, 'CrossPartyName': cross, 'RecordDate': ms(*date),
            'InstrumentNumber': inst, 'Consideration': amt, 'DocTypeDescription': doc,
            'DocLegalDescription': legal, 'ParcelNumber': parcel, 'CaseNumber': case,
            'BookPage': ''}


def owner_rows(name=OWNER, include_hud3=True):
    r = [
        row(name, 'TO', 'PRIOR SELLER LLC', (2021, 12, 21), '900000001', 330000, 'DEED', parcel='000000000001'),
        row(name, 'FROM', 'MORTGAGE ELECTRONIC REGISTRATION SYSTEMS INC', (2021, 12, 21), '900000002',
            294566, 'MORTGAGE'),
        row(name, 'FROM', 'PENNYMAC LOAN SERVICES LLC', (2023, 3, 1), '900000003', 285000.00, 'MODIFICATION'),
        row(name, 'FROM', HUD, (2023, 3, 1), '900000004', 73424.17, 'MORTGAGE'),
        row(name, 'FROM', 'PENNYMAC LOAN SERVICES LLC', (2024, 6, 1), '900000005', 276500.00, 'MODIFICATION'),
        row(name, 'FROM', HUD, (2024, 6, 1), '900000006', 9237.88, 'MORTGAGE'),
        row(name, 'FROM', 'PENNYMAC LOAN SERVICES LLC', (2025, 1, 15), '900000007', 267724.10, 'MODIFICATION'),
        row(name, 'FROM', HOA, (2024, 9, 1), '900000009', 2601.19, 'CLAIM OF LIEN'),
        row(name, 'FROM', HOA, (2024, 10, 1), '900000010', 0, 'LIS PENDENS', case='CACE24900002'),
        row(name, 'TO', 'PENNYMAC LOAN SERVICES LLC', (2026, 5, 27), '900000011', 0, 'ASSIGNMENT MORTGAGE'),
        row(name, 'FROM', 'PENNYMAC LOAN SERVICES LLC', (2026, 7, 22), '900000012', 0, 'LIS PENDENS', case=CASE),
        row(name, 'TO', HOA, (2026, 6, 26), '900000013', 0, 'RELEASE OF LIEN'),
    ]
    if include_hud3:
        r.append(row(name, 'FROM', HUD, (2025, 1, 15), '900000008', 5446.95, 'MORTGAGE'))
    return r


def cob_rows():
    return [row(COB, 'FROM', 'MORTGAGE ELECTRONIC REGISTRATION SYSTEMS INC', (2021, 12, 21), '900000002',
                294566, 'MORTGAGE'),
            row(COB, 'FROM', HUD, (2025, 1, 15), '900000008', 5446.95, 'MORTGAGE')]


def sources(case_ok=True, records_ok=True, tax_ok=True, avm=379400):
    s = {
        'appraiser': D.src(D.OK, {'market': 332360, 'assessed': 300000, 'homestead': True, 'legal': ''}),
        'case': (D.src(D.OK, {'case_number': CASE, 'plaintiff': 'PENNYMAC LOAN SERVICES LLC',
                              'filed': '2026-07-22', 'status': 'active', 'principal': 267151.56,
                              'sale': 'none', 'other_cases': [{'case': 'CACE24900002', 'status': 'closed'}]})
                 if case_ok else D.src(D.BLOCKED, why='human check')),
        'records': D.src(D.OK) if records_ok else D.src(D.BLOCKED, why='blocked 3x'),
        'tax': D.src(D.OK, {'due': 0, 'checked': '2026-11-01'}) if tax_ok else D.src(D.EMPTY, why='never read'),
        'avm': D.src(D.OK, {'value': avm, 'src': 'Zestimate typed in by hand'}) if avm else D.src(D.EMPTY),
    }
    return s


LEAD = {'case': CASE, 'owner': OWNER, 'folio': FOLIO, 'addr': '100 NW 1st Ave, Sunrise FL 33351'}


def searches(include_hud3_owner=True, with_cob=True):
    s = {D.search_name(OWNER): owner_rows(include_hud3=include_hud3_owner)}
    if with_cob:
        s[D.search_name(COB)] = cob_rows()
    return s


# ---- 1. the acceptance numbers ---------------------------------------------------------------
r = D.compute(LEAD, sources(), searches(), [COB])
ok(r['verdict'] == D.VERIFIED, 'acceptance: VERIFIED when case, records and tax are all OK')
ok(r['debt_first'] == 267151.56 and r['debt_first_src'] == 'principal claimed in the case',
   'acceptance: first mortgage = principal claimed $267,151.56')
ok(r['debt_first_inst'] == '900000002', 'acceptance: the foreclosed loan is the 2021 $294,566 mortgage')
ok(r['debt_subordinate_total'] == 88109.00, 'acceptance: HUD subordinates sum to $88,109.00')
ok(sorted(l['amount'] for l in r['debt_subordinate']) == [5446.95, 9237.88, 73424.17],
   'acceptance: the three HUD amounts are itemized')
ok(all(l['fha_partial_claim'] for l in r['debt_subordinate']) and len(r['fha_partial_claims']) == 3,
   'acceptance: every HUD mortgage is flagged as an FHA partial claim')
ok(r['debt_liens_total'] == 0 and not r['debt_liens'], 'acceptance: released HOA lien adds nothing')
ok([(x['inst'], x['released_by']) for x in r['released']] == [('900000009', '900000013')],
   'acceptance: HOA lien $2,601.19 released by the 2026-06-26 release')
ok(r['debt_total'] == 355260.56, 'acceptance: debt_total $355,260.56 (spec ~$355,261)')
ok(r['equity_low'] == -22900.56, 'acceptance: equity_low negative (county $332,360)')
ok(r['equity_high'] == 24139.44, 'acceptance: equity_high ~$24k (Zestimate $379,400)')
ok(len(r['modifications']) == 3 and r['modifications'][-1]['amount'] == 267724.10,
   'acceptance: 3 modifications, last balance $267,724.10')
ok([a['holder'] for a in r['assignments']] == ['PENNYMAC LOAN SERVICES LLC'], 'acceptance: assigned to PennyMac')
ok(r['case_status'] == 'active' and r['sale_date'] == 'none', 'acceptance: case active, no sale date')
ok(r['unknowns'] == [], 'acceptance: no unknown debt components')
ok('interest' in r['debt_note'], 'acceptance: the row says debt excludes interest and fees')

# ---- 2. why the board got it wrong, on this shape -------------------------------------------------
# The pre-link rules (still the fallback whenever a loan's details page was not read) let the owner's
# OWN purchase deed, recorded the same day as the purchase-money mortgage, "sell" that mortgage away.
# The live first then read SATISFIED, the largest HUD partial claim was taken for the foreclosed loan,
# and the chain came out a priced FACT with only ~$14.7k of juniors. Reproduced on this fixture; whether
# it is what happened on the real lead is for the laptop paste to show from the cached chain.
import broward_liens as BL
import equity_state as E
legacy = BL.analyze(copy.deepcopy(owner_rows()), OWNER, 267151.56, ftype='MORTGAGE', lead_case=CASE, _legacy=True)
ok(legacy['liens'][0]['st'] == 'SATISFIED' and legacy['junior'] == 14685 and legacy['conf'] == 'ok',
   'baseline (pre-fix rule 4): the purchase deed kills the first, juniors read $14,685, conf ok')
now = BL.analyze(copy.deepcopy(owner_rows()), OWNER, 267151.56, ftype='MORTGAGE', lead_case=CASE)
ok(now['liens'][0]['st'] == 'OPEN' and now['junior'] == 88109 and now['first_est'] == 294566,
   'broward_liens fix: the first stays open and the juniors are the three HUD claims ($88,109)')
ok(now['conf'] == 'low' and E.state_of(now, {'county': 'BROWARD'}) == 'unpriced',
   'broward_liens: 4 open loans trip the >3 guard, so the board shows a CEILING, not a priced FACT')

# ---- 3. the co-borrower search finds what the owner search cannot -------------------------------
r = D.compute(LEAD, sources(), searches(include_hud3_owner=False, with_cob=False), [])
ok(r['debt_subordinate_total'] == round(73424.17 + 9237.88, 2),
   'co-borrower: owner-only search misses the HUD mortgage indexed under the co-borrower')
r = D.compute(LEAD, sources(), searches(include_hud3_owner=False, with_cob=True), [COB])
ok(r['debt_subordinate_total'] == 88109.00, 'co-borrower: searching the co-borrower name recovers it')

# ---- 4. fail loud ---------------------------------------------------------------------------
for k, kw in (('case', {'case_ok': False}), ('records', {'records_ok': False}), ('tax', {'tax_ok': False})):
    r = D.compute(LEAD, sources(**kw), searches() if k != 'records' else {}, [COB])
    ok(r['verdict'] == D.UNVERIFIED and r['equity_low'] is None and r['equity_high'] is None,
       'fail loud: %s not OK -> UNVERIFIED, both equity fields blank' % k)
    ok(r['source_status'][k] != D.OK and k in r['equity_why'], 'fail loud: the row names the %s status' % k)
r = D.compute(LEAD, sources(avm=0), searches(), [COB])
ok(r['verdict'] == D.VERIFIED and r['equity_low'] == -22900.56 and r['equity_high'] is None,
   'avm missing: equity_low still stands, equity_high blank, not invented')

rows = searches()
rows[D.search_name(OWNER)].append(row(OWNER, 'FROM', 'CITY OF SUNRISE', (2025, 3, 3), '900000020', 0, 'LIEN'))
r = D.compute(LEAD, sources(), rows, [COB])
ok(r['verdict'] == D.UNVERIFIED and r['debt_total'] is None and r['equity_low'] is None,
   'unknown component: an open city lien with no amount -> equity UNKNOWN')

rows = searches()
rows[D.search_name(OWNER)].append(row(OWNER, 'FROM', 'SOME BANK', (2019, 1, 1), '900000021', 50000,
                                      'MORTGAGE', legal=''))
r = D.compute(LEAD, sources(), rows, [COB])
ok(r['verdict'] == D.UNVERIFIED and any('no legal description' in u for u in r['unknowns']),
   'unknown component: an open mortgage that cannot be placed on any property -> UNKNOWN')

rows = searches()
rows[D.search_name(OWNER)].append(row(OWNER, 'FROM', 'OTHER BANK', (2019, 1, 1), '900000022', 150000,
                                      'MORTGAGE', legal='FAR AWAY ACRES 12-3 B LOT 99 BLK 1'))
r = D.compute(LEAD, sources(), rows, [COB])
ok(r['verdict'] == D.VERIFIED and r['debt_total'] == 355260.56
   and [x['inst'] for x in r['other_property']] == ['900000022'],
   'other property: a mortgage on a different legal description is listed, not counted')

r = D.compute(LEAD, sources(), searches(), [COB], truncated=True)
ok(r['verdict'] == D.UNVERIFIED and r['equity_low'] is None, 'truncated name search -> UNKNOWN')

rows = searches()
rows[D.search_name(OWNER)].append(row(OWNER, 'FROM', 'BROWARD COUNTY TAX COLLECTOR', (2025, 6, 1),
                                      '900000023', 4100.00, 'TAX LIEN'))
s = sources()
s['tax'] = D.src(D.OK, {'due': 3200.50, 'checked': '2026-11-01', 'cert': True})
r = D.compute(LEAD, s, rows, [COB])
ok(r['debt_liens_total'] == 7300.50 and r['tax_status'].startswith('delinquent'),
   'liens: a recorded tax lien and the collector\'s delinquent amount are both summed')

# ---- 5. case facts: the clerk is behind a human check, so hand entry, with an age limit ---------
import datetime
today = datetime.date(2026, 11, 5)
cs = D.case_source(CASE, {}, [])
ok(cs['status'] == D.BLOCKED and 'human check' in cs['why'], 'case: no hand entry -> BLOCKED, says why')
man = {'case': {'plaintiff': 'X', 'filed': '2026-07-22', 'status': 'active', 'principal': 1.0},
       'entered_at': '2026-11-04T10:00:00'}
ok(D.case_source(CASE, man, [], today)['status'] == D.OK, 'case: fresh complete hand entry -> OK')
ok(D.case_source(CASE, dict(man, entered_at='2026-10-01T10:00:00'), [], today)['status'] == D.BLOCKED,
   'case: a hand entry older than 14 days -> BLOCKED')
ok(D.case_source(CASE, {'case': {'status': 'active'}, 'entered_at': '2026-11-04'}, [], today)['status'] == D.ERROR,
   'case: an incomplete hand entry -> ERROR')

# ---- 6. append-only log; a verified row is never overwritten by an unverified run ---------------
base = tempfile.mkdtemp()
good = D.compute(LEAD, sources(), searches(), [COB])
D.record(good, base)
bad = D.compute(LEAD, sources(case_ok=False), searches(), [COB])
ent = D.record(bad, base)
lines = open(os.path.join(base, 'runs.jsonl'), encoding='utf-8').read().splitlines()
ok(len(lines) == 2, 'log: both runs appended')
ok(ent['verified']['verdict'] == D.VERIFIED and ent['last_attempt']['verdict'] == D.UNVERIFIED,
   'log: the unverified run sits beside the verified row, never over it')
csv_txt = open(os.path.join(base, 'latest.csv'), encoding='utf-8').read()
ok('a later run was UNVERIFIED' in csv_txt and '-22900.56' in csv_txt, 'log: CSV shows the verified row and the later failure')
open(os.path.join(base, 'latest.json'), 'w').write('{torn')
D.record(good, base)
ok(any(f.startswith('latest.json.unreadable-') for f in os.listdir(base)),
   'log: an unreadable latest view is kept aside, not silently replaced')

# ---- 7. pacing and the 3-blocks stop -------------------------------------------------------------
slept = []
clock = [100.0]
site = D.Site('x', sleeper=lambda s: (slept.append(s), clock.__setitem__(0, clock[0] + s)),
              clock=lambda: clock[0], rand=lambda: 0.5)
site.wait(); site.wait()
ok(slept and abs(slept[-1] - (D.MIN_GAP_S + 0.5 * D.JITTER_S)) < 1e-6, 'pacing: about 2 s plus jitter between requests')
for _ in range(2):
    site.saw('<title>Just a moment...</title>')
ok(not site.stopped, 'blocks: two challenges in a row do not stop the source')
site.saw('ok'); site.saw(None); site.saw(None)
ok(not site.stopped, 'blocks: a good response resets the count')
site.saw('verify you are human')
ok(site.stopped, 'blocks: three in a row stop the source for the run')

# ---- 8. Tier 2 shortlist: sale within 30 days + phone + case --------------------------------------
leads = [
    {'case': 'A', 'auction': '11/20/2026', 'phones': ['x']},
    {'case': 'B', 'auction': '11/20/2026'},                       # no phone
    {'case': 'C', 'auction': '01/20/2027', 'phones': ['x']},      # too far
    {'case': 'D', 'auction': '11/08/2026'},                       # phone via skiptrace
    {'case': '', 'auction': '11/08/2026', 'phones': ['x']},       # no case
    {'case': 'E', 'auction': '11/01/2026', 'phones': ['x']},      # past
]
skip = {'D': {'phones': [{'number': '0000000000'}]}}
got = [r['case'] for r in D.tier2(leads, skip, {}, today=today)]
ok(got == ['D', 'A'], 'tier 2: soonest first, phone + case + sale in 30 days only (%s)' % got)
lat = {'A': {'verified': {'last_checked': '2026-11-04T05:30:00'}}}
ok([r['case'] for r in D.tier2(leads, skip, lat, today=today)] == ['D'], 'tier 2: a lead verified yesterday is skipped')

# ---- 9. acceptance runner on a fixture file -------------------------------------------------------
fx = {'lead': LEAD, 'coborrowers': [COB], 'searches': searches(),
      'sources': sources(),
      'expect': {'debt_first': 267151.56, 'debt_subordinate_total': 88109.0, 'debt_total': 355260.56,
                 'equity_low': -22900.56, 'equity_high': 24139.44, 'verdict': 'VERIFIED',
                 'subordinate': [{'inst': '900000004', 'amount': 73424.17},
                                 {'inst': '900000006', 'amount': 9237.88},
                                 {'inst': '900000008', 'amount': 5446.95}],
                 'released': [['900000009', '900000013']], 'modifications': 3}}
fp = os.path.join(base, 'fx.json')
json.dump(fx, open(fp, 'w'))
ok(D.main(['--accept', fp]) == 0, 'accept: the fixture passes through the CLI')
fx['expect']['debt_total'] = 267151.56
json.dump(fx, open(fp, 'w'))
ok(D.main(['--accept', fp]) == 1, 'accept: the board\'s old debt figure FAILS the test')

print('\n%d failure(s)' % len(FAILS))
sys.exit(1 if FAILS else 0)
