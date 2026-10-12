"""_otherlienstest.py — Broward / Palm Beach liens other than mortgages, priced or not (other_liens.py).

Before 2026-10-12 broward_liens.analyze (which Palm Beach shares) summed only liens that carried a
dollar figure, so an open IRS lien, a city lien or a PACE assessment the index lists with no amount
vanished and the chain could read VERIFIED CLEAR. Every name, number and date here is synthetic.

    python _otherlienstest.py
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import broward_liens as BL          # noqa: E402
import equity_state as ES           # noqa: E402
import other_liens as OL            # noqa: E402
import palmbeach_liens as PB        # noqa: E402

FAIL = []


def chk(name, cond, got=''):
    print(('ok   ' if cond else 'FAIL ') + name + ('' if cond else '  | got: %r' % (got,)))
    if not cond:
        FAIL.append(name)


def _ms(date):
    d = datetime.datetime.strptime(date, '%Y-%m-%d').replace(tzinfo=datetime.timezone.utc)
    return '/Date(%d)/' % int(d.timestamp() * 1000)


_n = [700000000]


def row(date, dtype, party, cross, amt=0, bp='', case='', name='TESTER,OWEN', parcel=''):
    _n[0] += 1
    return {'RecordDate': _ms(date), 'DocTypeDescription': dtype, 'Party': party, 'CrossPartyName': cross,
            'InstrumentNumber': str(_n[0]), 'Consideration': float(amt), 'BookPage': bp, 'CaseNumber': case,
            'Name': name, 'ParcelNumber': parcel}


MTG, REL = 'Mortgage/ Modifications & Assumptions', 'Release/Revoke/Satisfy or Terminate'
LC = 'CACE-99-000001'
BASE = [row('2015-01-02', 'Deed Transfers of Real Property', 'To', 'SELLER,SAM', 250000),
        row('2015-01-02', MTG, 'From', 'FIRST TEST BANK', 200000),
        row('2026-01-05', 'Lis Pendens', 'To', 'FIRST TEST BANK', 0, case=LC)]


def run(extra, case=LC, judg=200000):
    return BL.analyze(BASE + extra, 'TESTER,OWEN', judg, lead_case=case)


def opens(res, kind=None):
    return [o for o in res.get('other') or [] if o['st'] == 'OPEN' and not o.get('own_case')
            and o['kind'] != 'lis_pendens' and (kind is None or o['kind'] == kind)]


# 0. the plain mortgage chain is a FACT, so the cases below show what each lien does to it
r0 = run([])
chk('baseline: no other lien, nothing unpriced', r0['other_open_unpriced'] == 0, r0.get('other_open_unpriced'))
chk('baseline: chain is a FACT (priced)', ES.state_of(r0) in ES.FACT, ES.state_of(r0))

# 1. an IRS lien with no published amount: the old loop dropped it; now it holds the verdict open
r1 = run([row('2022-03-01', 'Lien', 'To', 'INTERNAL REVENUE SERVICE', 0)])
chk('unpriced IRS lien is an open row', len(opens(r1, 'irs')) == 1, r1.get('other'))
chk('unpriced IRS lien is counted unpriced', r1['other_open_unpriced'] == 1, r1['other_open_unpriced'])
chk('unpriced IRS lien: verdict is a CEILING, not a FACT', ES.state_of(r1) == 'unpriced', ES.state_of(r1))

# 2. a release recorded BEFORE the lien frees nothing
r2 = run([row('2019-05-05', REL, 'To', 'CITY OF TESTVILLE', 0),
          row('2021-05-05', 'Lien', 'To', 'CITY OF TESTVILLE', 0)])
chk('release before the lien does not free it', len(opens(r2, 'code')) == 1, r2.get('other'))

# 3. two liens, one release: one stays open (oldest takes the release)
r3 = run([row('2016-02-02', 'Lien', 'To', 'CITY OF TESTVILLE', 0),
          row('2018-02-02', 'Lien', 'To', 'CITY OF TESTVILLE', 0),
          row('2019-02-02', REL, 'From', 'CITY OF TESTVILLE', 0)])
o3 = opens(r3, 'code')
chk('two liens one release: exactly one open', len(o3) == 1, r3.get('other'))
chk('two liens one release: the newer one is open', o3 and o3[0]['d'] == '2018-02-02', o3)

# 4. two releases free two liens
r4 = run([row('2016-02-02', 'Lien', 'To', 'CITY OF TESTVILLE', 0),
          row('2018-02-02', 'Lien', 'To', 'CITY OF TESTVILLE', 0),
          row('2019-02-02', REL, 'From', 'CITY OF TESTVILLE', 0),
          row('2020-02-02', REL, 'To', 'CITY OF TESTVILLE', 0)])
chk('two liens two releases: none open', not opens(r4), r4.get('other'))
chk('two liens two releases: verdict back to a FACT', ES.state_of(r4) in ES.FACT, ES.state_of(r4))

# 5. a partial release never frees a lien
r5 = run([row('2016-02-02', 'Lien', 'To', 'CITY OF TESTVILLE', 0),
          row('2019-02-02', 'Partial Release', 'From', 'CITY OF TESTVILLE', 0)])
chk('partial release does not free the lien', len(opens(r5, 'code')) == 1, r5.get('other'))

# 6. a PRICED mechanic's lien had no bucket, so the old loop dropped it; it now adds to code_open
r6 = run([row('2024-07-07', 'Lien', 'To', 'ACME ROOFING INC', 18500)])
chk('priced mechanic lien adds to code_open', r6['code_open'] == 18500, r6['code_open'])
chk('mechanic lien flagged', any(o.get('mechanic') for o in opens(r6)), r6.get('other'))
chk('priced mechanic lien keeps the verdict priced', ES.state_of(r6) == 'priced', ES.state_of(r6))

# 7. a priced IRS lien the old loop already summed is counted ONCE
r7 = run([row('2023-03-03', 'Lien', 'To', 'INTERNAL REVENUE SERVICE', 12000)])
chk('priced IRS lien counted once', r7['irs_open'] == 12000, r7['irs_open'])
chk('priced IRS lien row says the old loop counted it',
    [o.get('counted') for o in opens(r7, 'irs')] == ['legacy'], r7.get('other'))

# 8. the old loop released a lien by a release from BEFORE it; that lien is still owed: added back
r8 = run([row('2017-01-01', REL, 'To', 'INTERNAL REVENUE SERVICE', 0),
          row('2023-03-03', 'Lien', 'To', 'INTERNAL REVENUE SERVICE', 9000)])
chk('lien the old loop freed with an earlier release is counted', r8['irs_open'] == 9000, r8['irs_open'])

# 9. this case's own filings are not claims; the plaintiff association's own priced claim stays in
#    hoa_open (the old loop summed it) and says so, so the board keeps netting the judgment
HC = 'COCE-99-000002'
hoa = [row('2015-01-02', 'Deed Transfers of Real Property', 'To', 'SELLER,SAM', 250000),
       row('2024-04-04', 'Lien', 'To', 'PALM TEST HOMEOWNERS ASSOCIATION INC', 6400),
       row('2025-01-01', 'Lis Pendens', 'To', 'PALM TEST HOMEOWNERS ASSOCIATION INC', 0, case=HC),
       row('2025-09-09', 'Judgment', 'To', 'PALM TEST HOMEOWNERS ASSOCIATION INC', 9100, case=HC)]
r9 = BL.analyze(hoa, 'TESTER,OWEN', 9100, ftype='HOA', lead_case=HC)
own = [o for o in r9['other'] if o.get('own_case')]
chk('own-case rows marked (claim, lis pendens, judgment)', len(own) == 3, r9['other'])
chk('own claim stays in hoa_open, judgment as before', r9['hoa_open'] == 6400 + 9100, r9['hoa_open'])
chk('hoa_own_in set, so the board does not stop netting', r9.get('hoa_own_in') is True, r9.get('hoa_own_in'))
chk('own-case rows are not unpriced claims', r9['other_open_unpriced'] == 0, r9['other_open_unpriced'])

# 10. documents that carry lien words but are not a claim
r10 = run([row('2024-01-01', 'Notice of Commencement', 'From', 'ACME ROOFING INC', 0),
           row('2024-02-02', 'Assignment of Lien', 'To', 'CITY OF TESTVILLE', 0),
           row('2024-03-03', 'Waiver of Lien', 'To', 'ACME ROOFING INC', 0),
           row('2024-04-04', 'Notice', 'To', 'SOME UTILITY NOTICE CO', 0)])
chk('notice of commencement / assignment / waiver / plain notice are not claims',
    r10['other_open_unpriced'] == 0 and not opens(r10), r10.get('other'))

# 11. a PACE notice is a claim, and a release from a DIFFERENT PACE agency does not free it
r11 = run([row('2024-03-22', 'Notice', 'To', 'FLORIDA GREEN FINANCE AUTHORITY', 0),
           row('2025-05-16', REL, 'To', 'FLORIDA PACE FUNDING AGENCY', 0)])
o11 = opens(r11, 'code')
chk('PACE notice is an open code row flagged pace', len(o11) == 1 and o11[0].get('pace'), r11.get('other'))

# 12. a bank whose name has COMMUNITY in it is not an association
r12 = run([row('2020-06-06', 'Judgment', 'To', 'FIRST COMMUNITY BANK', 0)])
chk('bank judgment reads as a judgment', [o['kind'] for o in opens(r12)] == ['judgment'], r12.get('other'))

# 13. the pre-link legacy run (the conf ceiling) carries no rows: unchanged
r13 = BL.analyze(BASE + [row('2022-03-01', 'Lien', 'To', 'INTERNAL REVENUE SERVICE', 0)], 'TESTER,OWEN',
                 200000, lead_case=LC, _legacy=True)
chk('legacy analyze unchanged (no other rows)', 'other' not in r13, sorted(r13))

# 14. Palm Beach: no amounts at all; a satisfaction that quotes the lien's book/page frees it,
#     an unquoted lien stays open and keeps the chain a ceiling
gsr = [
    {'4': '', '5': 'CITY OF TESTVILLE', '6': 'TESTER OWEN', '7': '03/03/2021', '9': 'LIEN',
     '10': 'O', '11': '32001', '12': '0101', '13': '20210000001', '15': ''},
    {'4': '', '5': 'CITY OF TESTVILLE', '6': 'TESTER OWEN', '7': '06/06/2023', '9': 'SATISFACTION',
     '10': 'O', '11': '34001', '12': '0202', '13': '20230000002', '15': 'ORB 32001 PG 0101'},
    {'4': '', '5': 'INTERNAL REVENUE SERVICE', '6': 'TESTER OWEN', '7': '08/08/2024', '9': 'FEDERAL TAX LIEN',
     '10': 'O', '11': '35001', '12': '0303', '13': '20240000003', '15': ''},
    {'4': '', '5': 'TESTER OWEN', '6': 'PB TEST BANK', '7': '01/01/2016', '9': 'MORTGAGE',
     '10': 'O', '11': '28001', '12': '0404', '13': '20160000004', '15': ''},
]
docs = PB.gsr_rows_to_docs(gsr)
r14 = PB.pb_chain_truth(BL.analyze(docs, 'TESTER OWEN', 0, lead_case='50-2099-CA-000001', county='palmbeach'), docs)
code14 = [o for o in r14['other'] if o['kind'] == 'code']
chk('PB city lien freed by a satisfaction quoting its book/page',
    code14 and code14[0]['st'] == 'RELEASED' and code14[0]['released_by'] == 'book/page', r14['other'])
chk('PB federal tax lien open, unpriced', len(opens(r14, 'irs')) == 1 and r14['other_open_unpriced'] == 1,
    r14['other'])
chk('PB chain stays a ceiling', ES.state_of(r14) == 'unpriced', ES.state_of(r14))

# 14b. Palm Beach: a release by the same holder that does NOT quote the book/page frees nothing
gsr2 = [
    {'4': '', '5': 'CITY OF TESTVILLE', '6': 'TESTER OWEN', '7': '03/03/2021', '9': 'LIEN',
     '10': 'O', '11': '32001', '12': '0101', '13': '20210000001', '15': ''},
    {'4': '', '5': 'CITY OF TESTVILLE', '6': 'TESTER OWEN', '7': '06/06/2023', '9': 'RELEASE OF LIEN',
     '10': 'O', '11': '34001', '12': '0202', '13': '20230000002', '15': ''},
]
d2 = PB.gsr_rows_to_docs(gsr2)
r14b = BL.analyze(d2, 'TESTER OWEN', 0, lead_case='50-2099-CA-000001', county='palmbeach')
chk('PB unquoted holder release does not free the lien', len(opens(r14b, 'code')) == 1, r14b['other'])
r14c = BL.analyze(d2, 'TESTER OWEN', 0, lead_case='50-2099-CA-000001')
chk('same rows read as Broward pair by holder', not opens(r14c, 'code'), r14c['other'])

# 14c. review fixes: a lender's judgment is the foreclosure, not a second debt
rj = run([row('2026-02-02', 'Judgment', 'To', 'FIRST TEST BANK', 200000)])
chk('lender judgment (holds the mortgage) is not added to code_open', rj['code_open'] == 0, rj['code_open'])
rj2 = run([row('2018-02-02', 'Judgment', 'To', 'OTHER CREDIT UNION', 195000, case='CACE-18-000777')], judg=196000)
chk('lender judgment near the case judgment is not added', rj2['code_open'] == 0, rj2['code_open'])
rj3 = run([row('2020-02-02', 'Judgment', 'To', 'UNRELATED CARD BANK', 8000)])
chk('unrelated lender judgment still adds', rj3['code_open'] == 8000, rj3['code_open'])
ra = run([row('2024-01-01', 'Lien', 'To', 'SUNNY HOMEOWNERS ASSOCIATION INC', 6400),
          row('2024-06-01', 'Amended Claim of Lien', 'To', 'SUNNY HOMEOWNERS ASSOCIATION INC', 6800)])
chk('amended claim not added a second time', ra['hoa_open'] == 6400, ra['hoa_open'])
ru = run([row('2024-01-01', 'Lien', 'To', 'CITY OF TESTVILLE', 0)])
ru['other'][0]
und = [row('2024-01-01', 'Lien', 'To', 'CITY OF TESTVILLE', 0), row('2024-01-01', REL, 'To', 'CITY OF TESTVILLE', 0)]
for u in und: u['RecordDate'] = ''
chk('undated lien is not freed by an undated release', len(opens(run(und), 'code')) == 1, run(und).get('other'))

# 14d. a cached chain from before other_liens is queued for a re-trace; one that has it is not
chk('pre-other_liens chain is queued for re-trace', BL._needs_relink({'conf': 'ok', 'pv': 2}))
chk('current chain is not re-queued', not BL._needs_relink({'conf': 'ok', 'pv': 2, 'other_v': 1}))
chk('failed search is the retry path, not relink', not BL._needs_relink({'conf': 'none', 'pv': 2}))
chk('pre-link chains still rank ahead of other_liens ones',
    BL._relink_rank({'liens': [{'party': 'X'}], 'pv': 1}) < BL._relink_rank({'conf': 'ok', 'pv': 2}))

# 14e. the common-name cap: a low-confidence Broward chain is never a priced FACT
low = {'conf': 'low', 'deed_conf': '', 'liens': [{'st': 'OPEN', 'amt': 90000}], 'second_fc': None, 'nrec': 40}
chk('low Broward chain with priced loans is a CEILING', ES.state_of(low) == 'unpriced', ES.state_of(low))
ok_ = dict(low, conf='ok')
chk('same chain at conf ok is still priced', ES.state_of(ok_) == 'priced', ES.state_of(ok_))
miami_low = {'conf': 'low', 'liens': [{'st': 'OPEN', 'amt': 90000}], 'subdiv': 'X', 'nrec': 40}
chk('Miami low chain is unchanged (separate decision)', ES.state_of(miami_low) == 'priced', ES.state_of(miami_low))
lead = {}
ES.apply(lead, low)
chk('board row says why, flags low', lead['eqstate'] == 'unpriced' and lead.get('eqlow') and 'namesake' in lead['eqstate_why'], lead)
chk('low_capped counts it, not the conf ok one', ES.low_capped(low) and not ES.low_capped(ok_) and not ES.low_capped(miami_low))
import lien_coverage as LCV
sm2 = LCV.summarize({'A': low, 'B': ok_}, None, 'broward')
chk('coverage counts the capped chains', sm2['fact_low'] == 1, sm2)
# a case of unknown type (a bare tax-deed number) is a guess about the foreclosing loan
ru2 = BL.analyze(BASE, 'TESTER,OWEN', 0, ftype='', lead_case='53741')
chk('unknown case type forces low confidence', ru2['conf'] == 'low' and ru2.get('case_type_unknown'), ru2['conf'])
chk('known case type is not marked', not run([]).get('case_type_unknown'))

sat = {'conf': 'low', 'deed_conf': '', 'liens': [{'st': 'SATISFIED', 'amt': 90000}], 'nrec': 40}
ls = {}
ES.apply(ls, sat)
chk('low chain of only satisfied loans never shows a ceiling of 0', ls['eqstate'] == 'unpriced' and ls.get('eqopen') == 1, ls)
chk('low_capped skips a chain that was already unpriced', not ES.low_capped(dict(low, other_open_unpriced=1)))

oldc = {'conf': 'low', 'liens': [{'st': 'OPEN', 'amt': 90000}], 'nrec': 40, 'first_est': 90000}   # cached pre-deed_conf
chk('old-shape Broward chain is capped via the lead county', ES.state_of(oldc, {'county': 'BROWARD'}) == 'unpriced')
chk('same old-shape chain on a Miami lead is untouched', ES.state_of(oldc, {'county': 'MIAMI-DADE'}) == 'priced')
chk('old-shape chain with no lead is untouched', ES.state_of(oldc) == 'priced')
chk('Palm Beach lead county also recognised', ES.county_chain(oldc, {'county': 'PALM BEACH'}))

# 14f. a name search cut at the 400-row page can prove nothing
tr = BL.mark_truncated({'conf': 'ok', 'liens': []}, True)
chk('truncated search is never conf ok and says so', tr['conf'] == 'low' and tr['truncated'] is True, tr)
chk('truncated + empty chain cannot be CLEAR (coverage_documented refuses it)',
    not ES.coverage_documented(dict(tr, nrec=40, second_fc=None)))
chk('complete search is left alone', BL.mark_truncated({'conf': 'ok'}, False) == {'conf': 'ok'})
import unittest.mock as _m
_resp = '{"data": [{"a": 1}] , "total": 900}'
with _m.patch.object(BL, '_curl', side_effect=lambda *a, **k: '' if 'SearchTypeName' in a[0] else _resp):
    got = BL.search_docs({'doctypes': '1', 'booktypes': '1'}, 'DOE, JANE')
chk('search_docs flags total > rows returned', got == [{'a': 1}] and BL.search_docs.last_truncated is True)
with _m.patch.object(BL, '_curl', side_effect=lambda *a, **k: '' if 'SearchTypeName' in a[0] else '{"data": [], "total": 0}'):
    BL.search_docs({'doctypes': '1', 'booktypes': '1'}, 'DOE, JANE')
chk('search_docs clears the flag for a small search', BL.search_docs.last_truncated is False)

# 15. is_claim on its own
chk('is_claim: CLAIM OF LIEN', OL.is_claim('CLAIM OF LIEN'))
chk('is_claim: FEDERAL TAX LIEN', OL.is_claim('FEDERAL TAX LIEN'))
chk('is_claim: CERTIFIED JUDGMENT', OL.is_claim('CERTIFIED COPY OF JUDGMENT'))
chk('is_claim: not a WARRANTY DEED', not OL.is_claim('WARRANTY DEED'))
chk('is_claim: not a RELEASE OF LIEN', not OL.is_claim('RELEASE OF LIEN'))
chk('is_claim: not a LIS PENDENS', not OL.is_claim('LIS PENDENS'))

# 16. lien_coverage --county: what the Broward / Palm Beach chains can and cannot prove (counts only)
import io, json, subprocess, tempfile   # noqa: E402
import lien_coverage as LC               # noqa: E402
c1 = LC.coverage(r1, None, 'broward')
chk('coverage broward: name-only search, unpriced lien, unanchored lien',
    {'name_only_search', 'unpriced_instrument', 'other_lien_unanchored'} <= set(c1['gaps']), c1)
chk('coverage broward: never fact_supported', c1['fact_supported'] is False, c1)
old = {k: v for k, v in r0.items() if k not in ('other', 'other_open_unpriced', 'other_v')}
chk('coverage: a chain from before other_liens says so',
    'other_liens_not_read' in LC.coverage(old, None, 'broward')['gaps'])
c14 = LC.coverage(r14, None, 'palmbeach')
chk('coverage palmbeach: no amounts published', 'no_amounts_published' in c14['gaps'], c14)
sm = LC.summarize({'CACE-99-000001': r1, 'CACE-99-000003': r6, 'CACE-99-000004': old}, None, 'broward')
chk('summary kinds: one IRS lead (unpriced), one mechanic lead (other)',
    sm['kinds'] == {'irs': 1, 'irs_unpriced': 1, 'other': 1}, sm['kinds'])
with tempfile.TemporaryDirectory() as td:
    ch, ld = os.path.join(td, 'c.json'), os.path.join(td, 'l.json')
    json.dump({'CACE-99-000001': r1, 'CACE-99-000003': r6}, open(ch, 'w'))
    json.dump([{'case': 'CACE-99-000001', 'days': 10}, {'case': 'CACE-99-000005', 'days': 90}], open(ld, 'w'))
    out = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lien_coverage.py'),
                          '--county', 'broward', '--chains', ch, '--leads', ld],
                         capture_output=True, text=True).stdout
    chk('CLI --county broward prints the kinds line', 'by kind' in out and '"irs": 1' in out, out)
    chk('CLI prints no case numbers, names or amounts',
        'CACE' not in out and 'INTERNAL' not in out and '18500' not in out, out)
    chk('CLI counts the lead with no chain', 'never searched, or the search failed)  1' in out, out)

import broward_liens as _BL
chk('14g: cached ok chain with 400 recs is queued for re-trace', _BL._needs_relink({'conf':'ok','nrec':400,'pv':_BL.PAIRING_VERSION,'other_v':1,'liens':[]}))
chk('14g: cached ok chain with 120 recs is not', not _BL._needs_relink({'conf':'ok','nrec':120,'pv':_BL.PAIRING_VERSION,'other_v':1,'liens':[]}))
print('\n%d failed' % len(FAIL) if FAIL else '\nALL PASS')
sys.exit(1 if FAIL else 0)
