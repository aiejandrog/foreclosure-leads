"""The 12-case verification's lien defects (MIAMI-VERIFY-12-2026-09-24), on synthetic records.

Every case, folio, book/page and name below is invented. The shapes are the ones the
verification found on real Miami-Dade dockets:

  1. liens the owner search returned never reached the chain: one City release released every City
     lien, amountless liens were skipped, the lienor was read from one party column only, and lis
     pendens / tax warrant rows never matched the filter;
  2. the foreclosed debt was priced at the mortgage's face instead of the judgment;
  3. a 500-record search or one that never returned the subject folio could still read CLEAR;
  4. the dossier called VERIFIED CLEAR on a lender's foreclosure whose mortgage the search missed;
  7. the case's own (vacated) final judgment was counted as another claim.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import case_dossier as CD
import equity_state as ES
import records_liens as RL

FAILS = []


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + (('  -- ' + str(detail)) if detail and not cond else ''))
    if not cond:
        FAILS.append(name)


FOLIO = '0100000000099'
PLAINTIFF = 'SYNTHETIC SAVINGS BANK NA'


def rec(doc, date, bk, pg, amt=0, second='OWNER TESTER', first='OWNER TESTER', folio=FOLIO, **kw):
    r = {'doC_TYPE': doc, 'reC_DATE': date, 'reC_BOOK': bk, 'reC_PAGE': pg,
         'reC_BOOKPAGE': '%s/%s' % (bk, pg), 'consideratioN_1': amt, 'intangible': 0,
         'seconD_PARTY': second, 'firsT_PARTY': first, 'foliO_NUMBER': folio,
         'subdiV_NAME': 'TEST GARDENS'}
    r.update(kw)
    return r


deed = rec('DEED', '2/1/2008', '26100', '10', 0, 'OWNER TESTER', first='PRIOR SELLER')
mtg = rec('MORTGAGE', '2/1/2008', '26100', '11', 0, PLAINTIFF, intangible=700)       # $350,000 face
city1 = rec('LIEN', '5/5/2019', '31100', '1', 0, 'OWNER TESTER', first='CITY OF MIAMI')
city2 = rec('LIEN', '6/6/2020', '31900', '2', 0, 'OWNER TESTER', first='CITY OF MIAMI')
city3 = rec('LIEN', '7/7/2021', '32500', '3', 1500, 'CITY OF MIAMI', first='OWNER TESTER')
# a City release of a DIFFERENT lien, elsewhere: it must not release the three above
city_rel = rec('RELEASE', '8/8/2022', '33000', '4', 0, 'OWNER TESTER', first='CITY OF MIAMI',
               oriG_REC_BOOK='29000', oriG_REC_PAGE='9')
wasd = rec('LIEN', '9/9/2022', '33400', '5', 812, 'OWNER TESTER', first='MIAMI-DADE WATER AND SEWER')
wasd_rel = rec('RELEASE', '1/1/2023', '33600', '6', 0, 'OWNER TESTER', first='MIAMI-DADE WATER AND SEWER',
               oriG_REC_BOOK='33400', oriG_REC_PAGE='5')
assn_lp = rec('LIS PENDENS', '3/3/2025', '34900', '7', 0, 'OWNER TESTER',
              first='TEST GARDENS CONDOMINIUM ASSOCIATION INC')
warrant = rec('WARRANT', '4/4/2011', '27400', '8', 2200, 'OWNER TESTER',
              first='STATE OF FLORIDA DEPARTMENT OF REVENUE', folio='')
own_lp = rec('LIS PENDENS', '1/10/2024', '34000', '9', 0, 'OWNER TESTER', first=PLAINTIFF)
own_fj_vacated = rec('JUDGMENT', '5/5/2025', '34999', '1999', 987654, 'OWNER TESTER', first=PLAINTIFF)
elsewhere = rec('LIEN', '2/2/2020', '31700', '1', 900, 'OWNER TESTER', first='CITY OF HOMESTEAD',
                folio='3099999999999', subdiV_NAME='ELSEWHERE')

models = [deed, mtg, city1, city2, city3, city_rel, wasd, wasd_rel, assn_lp, warrant, own_lp,
          own_fj_vacated, elsewhere]
res = RL.analyze(models, FOLIO, 987654.32, ftype='MORTGAGE', plaintiff=PLAINTIFF)
other = {r['bp']: r for r in res['other']}

# ---- defect 1: what the search found reaches the chain
check('all three City liens on the parcel are rows, not dropped',
      all(bp in other for bp in ('31100/1', '31900/2', '32500/3')), sorted(other))
check('a City release of a different book/page releases none of them',
      all(other[bp]['st'] == 'OPEN' for bp in ('31100/1', '31900/2', '32500/3')))
check('the lienor is read from either party column',
      other['31100/1']['kind'] == 'code' and other['32500/3']['kind'] == 'code')
check('amountless open liens are counted, never summed as zero',
      res['other_open_unpriced'] == 2 and other['31100/1']['amt'] is None, res['other_open_unpriced'])
check('a lien released by a release naming its own book/page is RELEASED and not summed',
      other['33400/5']['st'] == 'RELEASED')
check('code_open sums only priced, open, non-own liens', res['code_open'] == 1500, res['code_open'])
check("the association's lis pendens on the parcel is a row", other.get('34900/7', {}).get('kind') == 'lis_pendens')
check('a Department of Revenue warrant rides person-wide, with its amount',
      other.get('27400/8', {}).get('kind') == 'state_tax' and res['irs_open'] == 2200, res['irs_open'])
check("a lien on another parcel of the same owner stays out", '31700/1' not in other)

# ---- defect 7: the case's own filings are this case, not another claim
check("the case's own lis pendens is marked this case", other['34000/9'].get('own_case') is True)
check("the case's own (vacated) final judgment is marked this case and never summed",
      other['34999/1999'].get('own_case') is True and res['code_open'] == 1500)
_noplaintiff = RL.analyze(models, FOLIO, 987654.32, ftype='MORTGAGE')
check("without the plaintiff, the case's own judgment is still known by its figure",
      _noplaintiff['code_open'] == 1500, _noplaintiff['code_open'])
_nojudg = RL.analyze(models, FOLIO, 0, ftype='MORTGAGE')
check('with neither plaintiff nor judgment, a lender judgment is still a money judgment (unchanged behaviour)',
      _nojudg['code_open'] == 1500 + 987654, _nojudg['code_open'])

# ---- defect 2: the debt is the judgment
check('the chain keeps the recorded face apart from the judgment',
      res['first_face'] == 350000 and res['judgment'] == 987654.32 and res['first_bp'] == '26100/11',
      (res['first_face'], res['judgment'], res['first_bp']))
b = CD._b(res)
check('dossier b prices the foreclosed debt at the judgment, not the face',
      b['foreclosed_debt']['amount'] == 987654.32 and b['foreclosed_debt']['recorded_face'] == 350000,
      b['foreclosed_debt'])
check('dossier b lists the other instruments with the own-case ones marked',
      len(b['other_instruments']) == len(res['other'])
      and any(x['this_case'] for x in b['other_instruments']))
_old = dict(res); _old.pop('judgment'); _old.pop('first_face')
check('an older cached chain shows its face as a face, never as the debt',
      CD._b(_old)['foreclosed_debt']['amount'] is None
      and CD._b(_old)['foreclosed_debt']['recorded_face'] == res['first_est'])

# ---- equity: an unpriced open lien is a ceiling, never a fact
check('open liens with no published amount keep the verdict off VERIFIED',
      ES.state_of(res) == 'unpriced', ES.state_of(res))
_lead = {}
ES.apply(_lead, res)
check('the ceiling counts those liens', _lead.get('eqoth', 0) >= 2, _lead)
_mix = {}
ES.apply(_mix, {'conf': 'ok', 'nrec': 9, 'second_fc': None, 'other_open_unpriced': 1,
                'liens': [{'amt': 90000, 'st': 'SATISFIED'}, {'amt': 150000, 'st': 'OPEN'}]})
check('the ceiling counts open mortgages and unpriced liens apart, never a satisfied one',
      _mix.get('eqstate') == 'unpriced' and _mix.get('eqopen') == 1 and _mix.get('eqoth') == 1, _mix)
_lonly = {}
ES.apply(_lonly, {'conf': 'ok', 'nrec': 9, 'second_fc': None, 'other_open_unpriced': 1, 'liens': []})
check('a ceiling with no mortgage never claims one was recorded',
      'mortgage' not in _lonly['eqstate_why'].lower() and not _lonly.get('eqopen') and _lonly.get('eqoth') == 1, _lonly)

# ---- defect 3: a capped or wrong-person search can never be clear
empty = RL.analyze([deed], FOLIO, 12000, ftype='HOA')
check('fixture: an anchored, documented empty HOA chain is clear', ES.state_of(empty) == 'clear', ES.state_of(empty))
capped = RL.analyze([deed] + [rec('DEED', '1/1/1999', str(20000 + i), '1', 0, folio='')
                              for i in range(499)], FOLIO, 12000, ftype='HOA')
check('a 500-record search is marked capped and never clear',
      capped['capped'] is True and ES.state_of(capped) != 'clear', (capped['capped'], ES.state_of(capped)))
stranger = RL.analyze([rec('DEED', '1/1/2015', '29000', '1', folio='3011111111111')], FOLIO, 12000, ftype='HOA')
check('a search that never returned the subject folio says so and is never clear',
      stranger['parcel_found'] is False and ES.state_of(stranger) != 'clear')
check('coverage_documented refuses parcel_found False even on a hand-built chain',
      not ES.coverage_documented(dict(empty, parcel_found=False)))

# ---- defect 4: the dossier applies the lender rule
lender_empty = RL.analyze([deed], FOLIO, 123456.78, ftype='MORTGAGE', plaintiff=PLAINTIFF)
d = CD._d(lender_empty, {'status': 'empty'})
check('dossier d: a lender foreclosure with no mortgage found is not VERIFIED CLEAR',
      d['eqstate'] == 'none' and 'lender is foreclosing' in d['verdict'], (d['eqstate'], d['verdict']))
d_hoa = CD._d(empty, {'status': 'empty'})
check('dossier d: an association case with a documented empty chain still reads clear',
      d_hoa['eqstate'] == 'clear')
full = CD.build('2099-000099-CA-01', 'MIAMI-DADE', chain=capped)
check('dossier gaps name the 500-record cap', any('500-record cap' in g for g in full['open_gaps']))

# ---- Greptile on #62
_wd = RL.analyze([deed, rec('WARRANTY DEED', '3/3/2015', '29500', '4', 0, 'OWNER TESTER', first='PRIOR SELLER')],
                 FOLIO, 12000, ftype='HOA')
check('a warranty deed is not a tax warrant and never makes a clear chain unpriced',
      not _wd['other'] and ES.state_of(_wd) == 'clear', (_wd['other'], ES.state_of(_wd)))
_pnc = RL.analyze([deed, rec('JUDGMENT', '5/5/2025', '34990', '12', 250000, 'OWNER TESTER', first='PNC BANK NA')],
                  FOLIO, 250000, ftype='MORTGAGE', plaintiff='PNC BANK, N.A.')
check("a short lender name ('PNC BANK' -> 'PNC') still marks its own judgment this case, never summed",
      _pnc['other'] and _pnc['other'][0].get('own_case') is True and _pnc['code_open'] == 0,
      (_pnc['other'], _pnc['code_open']))
_pnc2 = RL.analyze([deed, rec('JUDGMENT', '5/5/2025', '34990', '13', 5000, 'OWNER TESTER', first='PNCX CAPITAL LLC')],
                   FOLIO, 250000, ftype='MORTGAGE', plaintiff='PNC BANK, N.A.')
check('a short lender name matches exactly, not by containment', not _pnc2['other'][0].get('own_case'))
_copy_else = rec('LIEN', '6/6/2020', '31950', '2', 700, 'OWNER TESTER', first='CITY OF MIAMI',
                 folio='3099999999999', subdiV_NAME='ELSEWHERE')
_copy_here = rec('LIEN', '6/6/2020', '31950', '2', 700, 'OWNER TESTER', first='CITY OF MIAMI')
_cp = RL.analyze([deed, _copy_else, _copy_here], FOLIO, 12000, ftype='HOA')
check("a copy indexed to another folio does not hide this parcel's copy of the same lien",
      [o['bp'] for o in _cp['other']] == ['31950/2'] and _cp['code_open'] == 700, (_cp['other'], _cp['code_open']))
_hoa_ca = dict(empty, ftype='MORTGAGE', case_type='HOA/Condo')
check('dossier d: an association case in circuit court reads clear when the chain carries its case type',
      CD._d(_hoa_ca, {'status': 'empty'})['eqstate'] == 'clear')
_hoa_ca_old = dict(empty, ftype='MORTGAGE', other=[{'own_case': True, 'kind': 'lis_pendens',
                                                     'party': 'TEST GARDENS CONDOMINIUM ASSOCIATION INC'}])
check("dossier d: an older circuit chain whose own filing names an association is not taken as a lender's",
      CD._d(_hoa_ca_old, {'status': 'empty'})['eqstate'] == 'clear')
check("dossier d: an older circuit chain whose own filing names a CONDO is not taken as a lender's",
      CD._d(dict(empty, ftype='MORTGAGE', other=[{'own_case': True, 'kind': 'lis_pendens', 'party': 'PALM TEST CONDO INC'}]),
            {'status': 'empty'})['eqstate'] == 'clear')
_bank_ca_old = dict(empty, ftype='MORTGAGE', other=[{'own_case': True, 'kind': 'lis_pendens',
                                                      'party': 'SYNTHETIC BANK NATIONAL ASSOCIATION'}])
check("dossier d: a bank's 'National Association' is still a lender",
      CD._d(_bank_ca_old, {'status': 'empty'})['eqstate'] == 'none')
for _ct, _want in (('Mortgage/Other', 'none'), ('Bank/Mortgage', 'none'), ('HOA/Condo', 'clear'),
                   ('Govt/Code', 'clear'), ('Other', 'none'), ('', 'none')):
    check("dossier d: a circuit chain typed %r reads %s" % (_ct, _want),
          CD._d(dict(empty, ftype='MORTGAGE', case_type=_ct), {'status': 'empty'})['eqstate'] == _want)
check("the board's lender rule counts a 'Mortgage/Other' foreclosure",
      ES.state_of(empty, {'ctype': 'Mortgage/Other'}) == 'none')
_hoa_b = CD._b(dict(empty, judgment=12000))
check("dossier b: an association case shows its judgment as the foreclosed debt, with no mortgage face",
      (_hoa_b['foreclosed_debt'] or {}).get('amount') == 12000
      and (_hoa_b['foreclosed_debt'] or {}).get('recorded_face') is None,
      _hoa_b['foreclosed_debt'])
_bw = {'conf': 'ok', 'liens': [], 'nrec': 3}
check('dossier b: a chain with no judgment key takes it from the lead',
      (CD._b(_bw, {'judgment': '$88,500.10'})['foreclosed_debt'] or {}).get('amount') == 88500.10)

# ---- review on #62: namesakes, the owner's own name, unreferenced releases, a sibling plaintiff
_ns = RL.analyze([deed, rec('WARRANT', '4/4/2021', '33000', '1', 0, 'MARIA TESTER',
                            first='STATE OF FLORIDA DEPARTMENT OF REVENUE', folio='', subdiV_NAME=''),
                  rec('JUDGMENT', '4/4/2021', '33000', '2', 0, 'TESTER MARIA', first='LVNV FUNDING LLC', folio='', subdiV_NAME='')],
                 FOLIO, 12000, ftype='HOA', owner='OWNER TESTER')
check("a namesake's tax warrant and judgment (surname-only search) never reach this owner's chain",
      _ns['other'] == [] and _ns['other_open_unpriced'] == 0, _ns['other'])
_ns2 = RL.analyze([deed, rec('JUDGMENT', '4/4/2021', '33000', '2', 9000, 'TESTER, OWNER Q', first='LVNV FUNDING LLC', folio='', subdiV_NAME='')],
                  FOLIO, 12000, ftype='HOA', owner='OWNER Q TESTER')
check("the owner's own person-wide judgment still rides, in the clerk's surname-first order",
      _ns2['code_open'] == 9000, (_ns2['other'], _ns2['code_open']))
_vl = RL.analyze([deed, rec('JUDGMENT', '4/4/2021', '33000', '3', 9000, 'VILLA JUAN', first='LVNV FUNDING LLC', folio='', subdiV_NAME=''),
                  rec('LIEN', '5/5/2021', '33000', '4', 700, 'VILLA JUAN', first='CITY OF MIAMI')],
                 FOLIO, 12000, ftype='HOA', owner='JUAN VILLA')
check("an owner named VILLA: a debt buyer's judgment is a judgment, not an association's lien",
      [o['kind'] for o in _vl['other']] == ['judgment', 'code'] and _vl['code_open'] == 9700
      and _vl['hoa_open'] == 0, (_vl['other'], _vl['code_open'], _vl['hoa_open']))
check("an owner named VILLA: the creditor is read from the other side",
      [o['party'] for o in _vl['other']] == ['LVNV FUNDING LLC', 'CITY OF MIAMI'], _vl['other'])
_rel1 = rec('RELEASE OF LIEN', '9/9/2023', '34000', '1', 0, 'OWNER TESTER', first='CITY OF MIAMI')
_l1 = rec('LIEN', '3/3/2021', '33000', '5', 400, 'OWNER TESTER', first='CITY OF MIAMI')
_l2 = rec('LIEN', '3/3/2022', '33500', '6', 600, 'OWNER TESTER', first='CITY OF MIAMI')
_u1 = RL.analyze([deed, _l1, _rel1], FOLIO, 12000, ftype='HOA', owner='OWNER TESTER')
check('a release naming no book/page releases the one lien of its holder recorded before it',
      _u1['other'][0]['st'] == 'RELEASED' and _u1['code_open'] == 0, _u1['other'])
_u2 = RL.analyze([deed, _l1, _l2, _rel1], FOLIO, 12000, ftype='HOA', owner='OWNER TESTER')
check('one unreferenced release and two liens: neither is released, both flagged',
      [o['st'] for o in _u2['other']] == ['OPEN', 'OPEN'] and _u2['code_open'] == 1000
      and all(o.get('release_unmatched') == 1 for o in _u2['other']), _u2['other'])
_rel0 = rec('RELEASE OF LIEN', '1/1/2020', '32000', '1', 0, 'OWNER TESTER', first='CITY OF MIAMI')
_u3 = RL.analyze([deed, _l1, _rel0], FOLIO, 12000, ftype='HOA', owner='OWNER TESTER')
check('a release recorded before the lien never releases it', _u3['other'][0]['st'] == 'OPEN', _u3['other'])
_u4 = RL.analyze([deed, _l1, rec('SATISFACTION OF MORTGAGE', '9/9/2023', '34000', '2', 0, 'OWNER TESTER',
                                 first='CITY OF MIAMI')], FOLIO, 12000, ftype='HOA', owner='OWNER TESTER')
check("a mortgage satisfaction never releases a lien", _u4['other'][0]['st'] == 'OPEN', _u4['other'])
_sib = RL.analyze([deed, rec('LIEN', '3/3/2025', '34950', '1', 0, 'OWNER TESTER',
                             first='VILLAGES OF KENDALL MASTER ASSOCIATION INC')],
                  FOLIO, 12000, ftype='HOA', plaintiff='VILLAGES OF KENDALL HOMEOWNERS ASSOCIATION INC',
                  owner='OWNER TESTER', case='2025-000001-CC-05')
check("a sibling association sharing the plaintiff's first words is another claim, not this case",
      not _sib['other'][0].get('own_case') and _sib['other_open_unpriced'] == 1, _sib['other'])
_old_j = RL.analyze([deed, rec('JUDGMENT', '5/5/2016', '30000', '1', 4000, 'OWNER TESTER', first=PLAINTIFF, folio='', subdiV_NAME='')],
                    FOLIO, 250000, ftype='MORTGAGE', plaintiff=PLAINTIFF, owner='OWNER TESTER',
                    case='2024-000001-CA-01')
check("the plaintiff's judgment from years before this case was filed is another claim",
      not _old_j['other'][0].get('own_case') and _old_j['code_open'] == 4000, _old_j['other'])

# ---- review round 2
_far_rel = rec('RELEASE OF LIEN', '9/9/2023', '34000', '7', 0, 'OWNER TESTER', first='CITY OF MIAMI',
               folio='3099999999999', subdiV_NAME='ELSEWHERE')
_r1 = RL.analyze([deed, _l1, _far_rel], FOLIO, 12000, ftype='HOA', owner='OWNER TESTER')
check("a release of the same City's lien on another property never frees this parcel's",
      _r1['other'][0]['st'] == 'OPEN' and _r1['code_open'] == 400, _r1['other'])
_cap1 = rec('JUDGMENT', '4/4/2021', '33000', '8', 5000, 'OWNER TESTER', first='CAPITAL ONE BANK USA NA')
_r2 = RL.analyze([deed, _cap1, rec('SATISFACTION', '9/9/2023', '34000', '8', 0, 'OWNER TESTER', first='CAPITAL ONE BANK')],
                 FOLIO, 12000, ftype='HOA', owner='OWNER TESTER')
check("a plain SATISFACTION (how the index files a mortgage payoff) never releases a judgment",
      _r2['other'][0]['st'] == 'OPEN' and _r2['code_open'] == 5000, _r2['other'])
_r2b = RL.analyze([deed, _cap1, rec('SATISFACTION OF JUDGMENT', '9/9/2023', '34000', '9', 0, 'OWNER TESTER',
                                    first='CAPITAL ONE BANK USA NA')], FOLIO, 12000, ftype='HOA', owner='OWNER TESTER')
check("a SATISFACTION OF JUDGMENT from the same holder does", _r2b['other'][0]['st'] == 'RELEASED', _r2b['other'])
_roof = RL.analyze([deed, rec('LIEN', '4/4/2023', '33000', '9', 40000, 'OWNER TESTER', first='ABC ROOFING INC')],
                   FOLIO, 12000, ftype='HOA', owner='OWNER TESTER')
check("a contractor's lien on the parcel is counted, not shown and forgotten", _roof['code_open'] == 40000, _roof)
_roof0 = RL.analyze([deed, rec('LIEN', '4/4/2023', '33000', '9', 0, 'OWNER TESTER', first='ABC ROOFING INC')],
                    FOLIO, 12000, ftype='HOA', owner='OWNER TESTER')
check("an amountless contractor's lien keeps the verdict off VERIFIED",
      _roof0['other_open_unpriced'] == 1 and ES.state_of(_roof0) == 'unpriced', _roof0['other'])
_ucc = RL.analyze([deed, rec('FINANCING STATEMENT', '4/4/2023', '33000', '10', 0, 'OWNER TESTER', first='SOLAR LEASE LLC')],
                  FOLIO, 12000, ftype='HOA', owner='OWNER TESTER')
check('a financing statement is shown, not counted as a lien', _ucc['other_open_unpriced'] == 0 and len(_ucc['other']) == 1)
_claim = RL.analyze([deed, rec('CLAIM OF LIEN', '10/1/2023', '33900', '1', 6400, 'OWNER TESTER',
                               first='TEST GARDENS CONDOMINIUM ASSOCIATION INC')],
                    FOLIO, 6400, ftype='HOA', plaintiff='TEST GARDENS CONDOMINIUM ASSOCIATION INC',
                    owner='OWNER TESTER', case='2024-000123-CC-05')
check("the association's claim of lien recorded the year before it sued is the debt being foreclosed",
      _claim['other'][0].get('own_case') is True and _claim['hoa_open'] == 0, _claim['other'])
_co = RL.analyze([deed, rec('FEDERAL TAX LIEN', '4/4/2021', '33000', '11', 20000, 'SMITH MARY',
                            first='INTERNAL REVENUE SERVICE', folio='', subdiV_NAME='')],
                 FOLIO, 12000, ftype='HOA', owner='JOHN SMITH', co_owners=[('SMITH', 'MARY')])
check("a co-owner named in the case: their tax lien rides", _co['irs_open'] == 20000, _co['other'])
_usb = RL.analyze([deed, rec('JUDGMENT', '5/5/2025', '34999', '5', 410000, 'OWNER TESTER',
                             first='U S BANK NATIONAL ASSN TR')],
                  FOLIO, 0, ftype='MORTGAGE', plaintiff='U.S. BANK NATIONAL ASSOCIATION, AS TRUSTEE FOR XYZ TRUST 2006-1',
                  owner='OWNER TESTER', case='2024-000001-CA-01')
check("the index's 'U S BANK NATIONAL ASSN TR' is the trustee plaintiff: its own judgment, never summed",
      _usb['other'][0].get('own_case') is True and _usb['code_open'] == 0, _usb['other'])
_fig = RL.analyze([deed, rec('JUDGMENT', '5/5/2025', '34999', '6', 410000, 'OWNER TESTER', first='SERVICER NAME LLC')],
                  FOLIO, 410000, ftype='MORTGAGE', plaintiff=PLAINTIFF, owner='OWNER TESTER', case='2024-000001-CA-01')
check("a judgment at this case's figure is its own, whatever name the index files it under",
      _fig['other'][0].get('own_case') is True and _fig['code_open'] == 0, _fig['other'])
_part = rec('PARTIAL RELEASE OF LIEN', '9/9/2023', '34000', '12', 0, 'OWNER TESTER', first='CITY OF MIAMI',
            oriG_REC_BOOK='33000', oriG_REC_PAGE='5', folio='3099999999999', subdiV_NAME='ELSEWHERE')
_r8 = RL.analyze([deed, _l1, _part], FOLIO, 12000, ftype='HOA', owner='OWNER TESTER')
check("a partial release freeing another parcel leaves this parcel's lien open", _r8['other'][0]['st'] == 'OPEN', _r8['other'])
_r8b = RL.analyze([deed, _l1, dict(_part, foliO_NUMBER=FOLIO, subdiV_NAME='TEST GARDENS')], FOLIO, 12000,
                  ftype='HOA', owner='OWNER TESTER')
check("a partial release indexed to this folio releases it", _r8b['other'][0]['st'] == 'RELEASED', _r8b['other'])
_mx = {}
ES.apply(_mx, {'conf': 'ok', 'nrec': 9, 'second_fc': None, 'mtg_open_unpriced': 1,
               'liens': [{'amt': 150000, 'st': 'OPEN'}, {'amt': 20000, 'st': 'OPEN'}]})
check('Miami: unpriced loans are kept out of liens, so the two counts add', _mx.get('eqopen') == 3, _mx)
_pb = {}
ES.apply(_pb, {'conf': 'unpriced', 'mtg_recorded': 2, 'mtg_open_unpriced': 2, 'liens': [{'amt': 0, 'st': 'OPEN'}]})
check('Palm Beach: its count already covers every mortgage, never added twice', _pb.get('eqopen') == 2, _pb)

# ---- review round 3
for _pl3, _j3 in (('SPACE COAST CREDIT UNION', 180000), ('FIRST COUNTY BANK', 250000)):
    _s3 = RL.analyze([deed, rec('JUDGMENT', '5/5/2025', '34999', '7', _j3, 'OWNER TESTER', first=_pl3)],
                     FOLIO, 0, ftype='MORTGAGE', plaintiff=_pl3, owner='OWNER TESTER', case='2024-000001-CA-01')
    check("a plaintiff named %s: its own judgment is this case, whatever its name looks like" % _pl3,
          _s3['other'][0].get('own_case') is True and _s3['code_open'] == 0, _s3['other'])
_cm = RL.analyze([deed, rec('LIEN', '3/3/2022', '33500', '8', 5000, 'OWNER TESTER', first='CITY OF MIAMI'),
                  rec('JUDGMENT', '5/5/2025', '34999', '8', 7500, 'OWNER TESTER', first='CITY OF MIAMI')],
                 FOLIO, 0, ftype='MORTGAGE', plaintiff='CITY OF MIAMI', owner='OWNER TESTER', case='2024-000001-CA-01')
check("a City foreclosing: its judgment is this case, its liens still count", _cm['code_open'] == 5000, _cm['other'])
_nb = RL.analyze([deed, _l1, rec('RELEASE OF LIEN', '9/9/2023', '34000', '13', 0, 'NEIGHBOR TESTER',
                                 first='CITY OF MIAMI', folio='')], FOLIO, 12000, ftype='HOA', owner='OWNER TESTER')
check("a neighbour's release in the same subdivision never frees the owner's lien",
      _nb['other'][0]['st'] == 'OPEN' and _nb['code_open'] == 400, _nb['other'])
_won = RL.analyze([deed, rec('JUDGMENT', '4/4/2021', '33000', '14', 20000, 'SMITH BOB', first='TESTER OWNER',
                             folio='', subdiV_NAME='')], FOLIO, 12000, ftype='HOA', owner='OWNER TESTER')
check("a person-against-person judgment is flagged (the owner may have won it) but still summed: too much debt, never too little",
      _won['code_open'] == 20000 and _won['other'][0].get('direction_unknown'), _won['other'])
_citi = RL.analyze([deed, rec('JUDGMENT', '3/3/2022', '33500', '50', 15000, 'TESTER OWNER', first='CITIBANK NA')],
                   FOLIO, 12000, ftype='HOA', owner='OWNER TESTER')
check("a CITIBANK NA judgment against the owner is summed", _citi['code_open'] == 15000, _citi['other'])
_rel2 = rec('RELEASE OF LIEN', '8/8/2021', '32800', '1', 0, 'OWNER TESTER', first='CITY OF MIAMI')
_dup = RL.analyze([deed, rec('LIEN', '5/5/2019', '31100', '51', 5000, 'OWNER TESTER', first='CITY OF MIAMI'),
                   rec('LIEN', '6/6/2020', '31900', '52', 7000, 'OWNER TESTER', first='CITY OF MIAMI'),
                   _rel2, dict(_rel2, seconD_PARTY='TESTER MARIA')],
                  FOLIO, 12000, ftype='HOA', owner='OWNER TESTER', co_owners=[('TESTER', 'MARIA')])
check("two index copies of ONE release are one release: it frees neither of two liens",
      [o['st'] for o in _dup['other']] == ['OPEN', 'OPEN'] and _dup['code_open'] == 12000, _dup['other'])
_long_pl = RL.analyze([deed, rec('CLAIM OF LIEN', '1/1/2024', '34000', '53', 9000, 'OWNER TESTER',
                                 first='SUNSET HOMEOWNERS ASSOCIATION INC')],
                      FOLIO, 4000, ftype='HOA', plaintiff='SUNSET HOMEOWNERS ASSOCIATION PHASE II INC',
                      owner='OWNER TESTER', case='2024-000001-CC-05')
check("a whole sibling name that is a prefix of the plaintiff's is still the sibling's claim",
      not _long_pl['other'][0].get('own_case') and _long_pl['hoa_open'] == 9000, _long_pl['other'])
_trunc = RL.analyze([deed, rec('CLAIM OF LIEN', '1/1/2024', '34000', '54', 9000, 'OWNER TESTER',
                               first='SUNSET HOMEOWNERS ASSOCIATION PHA')],
                    FOLIO, 4000, ftype='HOA', plaintiff='SUNSET HOMEOWNERS ASSOCIATION PHASE II INC',
                    owner='OWNER TESTER', case='2024-000001-CC-05')
check("an index name cut short is still the plaintiff's", _trunc['other'][0].get('own_case') is True, _trunc['other'])
_hh = RL.analyze([rec('DEED', '2/1/2008', '26100', '10', 0, 'SMITH JOHN & HELEN', first='PRIOR SELLER'),
                  rec('LIEN', '3/3/2022', '33500', '56', 30000, 'SMITH HELEN', first='INTERNAL REVENUE SERVICE',
                      folio='', subdiV_NAME='')], FOLIO, 12000, ftype='HOA', owner='JOHN SMITH')
check("a spouse named on the parcel's deed is the household: her IRS lien rides with no defendants list",
      _hh['irs_open'] == 30000, _hh['other'])
_assoc = RL.analyze([deed, rec('CLAIM OF LIEN', '1/1/2024', '34000', '57', 9000, 'OWNER TESTER',
                               first='SUNSET HOMEOWNERS ASSOC')],
                    FOLIO, 4000, ftype='HOA', plaintiff='SUNSET HOMEOWNERS ASSOCIATION PHASE II INC',
                    owner='OWNER TESTER', case='2024-000001-CC-05')
check("'... ASSOC' is a whole name too", not _assoc['other'][0].get('own_case') and _assoc['hoa_open'] == 9000, _assoc['other'])
_mid = RL.analyze([rec('DEED', '2/1/2008', '26100', '10', 0, 'PEREZ JOSE A', first='PRIOR SELLER'),
                   rec('JUDGMENT', '3/3/2022', '33500', '58', 18400, 'PEREZ ANTONIO', first='MIDLAND CREDIT MANAGEMENT INC',
                       folio='', subdiV_NAME='')], FOLIO, 12000, ftype='HOA', owner='JOSE ANTONIO PEREZ')
check("a judgment filed under the owner's MIDDLE name is the owner's", _mid['code_open'] == 18400, _mid['other'])
for _ini in ('SMITH J', 'SMITH'):
    _irs = RL.analyze([deed, rec('LIEN', '3/3/2022', '33500', '55', 40000, 'INTERNAL REVENUE SERVICE', first=_ini,
                                 folio='', subdiV_NAME='')], FOLIO, 12000, ftype='HOA', owner='JOHN SMITH')
    check("the owner's IRS lien indexed as %r is kept" % _ini, _irs['irs_open'] == 40000, _irs['other'])
_dlc = RL.analyze([deed, rec('FEDERAL TAX LIEN', '4/4/2021', '33000', '15', 3000, 'DE LA CRUZ MARIA',
                             first='INTERNAL REVENUE SERVICE', folio='', subdiV_NAME='')],
                  FOLIO, 12000, ftype='HOA', owner='JUAN PEREZ', co_owners=[('DE LA CRUZ', 'MARIA')])
check("a co-owner with a three-word surname is still the owner's household", _dlc['irs_open'] == 3000, _dlc['other'])

# ---- review round 4
_c45 = RL.analyze([deed, rec('LIEN', '3/3/2022', '33500', '20', 45000, 'OWNER TESTER', first='CITY OF MIAMI')],
                  FOLIO, 12000, ftype='HOA', owner='OWNER TESTER')
check('a priced City lien on a parcel with no mortgage is carried as code_open; the mortgage verdict stays clear',
      _c45['code_open'] == 45000 and ES.state_of(_c45) == 'clear', ES.state_of(_c45))
check('no internal key leaks into the stored rows', not any(k.startswith('_') for o in res['other'] for k in o),
      [sorted(o) for o in res['other']][:1])
_cert = RL.analyze([deed, rec('CERTIFIED COPY OF FINAL ORDER IMPOSING FINE AND LIEN', '3/3/2022', '33500', '30', 45000,
                              'OWNER TESTER', first='CITY OF MIAMI'),
                    rec('CERTIFIED COPY OF ORDER', '3/4/2022', '33500', '31', 12000, 'OWNER TESTER', first='CITY OF MIAMI')],
                   FOLIO, 12000, ftype='HOA', owner='OWNER TESTER')
check("a City's code-enforcement order is counted, however long its document name", _cert['code_open'] == 57000, _cert['other'])
_sj = RL.analyze([deed, rec('LIEN', '3/3/2022', '33500', '32', 20000, 'SMITH J', first='ABC PLUMBING LLC'),
                  rec('LIEN', '3/3/2022', '33500', '33', 500, 'SMITH J', first='CITY OF MIAMI'),
                  rec('RELEASE OF LIEN', '9/9/2023', '34000', '33', 0, 'SMITH J', first='CITY OF MIAMI')],
                 FOLIO, 12000, ftype='HOA', owner='JOHN SMITH')
check("one City release frees at most one lien, and never a plumber's",
      [o['st'] for o in _sj['other']] == ['OPEN', 'RELEASED'] and _sj['code_open'] == 20000, _sj['other'])
_sj2 = RL.analyze([deed, rec('LIEN', '3/3/2022', '33500', '34', 20000, 'SMITH J', first='ABC PLUMBING LLC'),
                   rec('RELEASE OF LIEN', '9/9/2023', '34000', '34', 0, 'SMITH J', first='CITY OF MIAMI')],
                  FOLIO, 12000, ftype='HOA', owner='JOHN SMITH')
check("a release that names the owner is not the plumber's release", _sj2['other'][0]['st'] == 'OPEN', _sj2['other'])
_two = RL.analyze([deed, rec('LIEN', '3/3/2022', '33500', '35', 500, 'OWNER TESTER', first='CITY OF MIAMI'),
                   rec('LIEN', '3/3/2022', '33500', '36', 700, 'OWNER TESTER', first='MIAMI-DADE COUNTY'),
                   rec('RELEASE OF LIEN', '9/9/2023', '34000', '35', 0, 'MIAMI-DADE COUNTY', first='CITY OF MIAMI')],
                  FOLIO, 12000, ftype='HOA', owner='OWNER TESTER')
check("a release naming two lienors frees one lien, not both", sorted(o['st'] for o in _two['other']) == ['OPEN', 'RELEASED'],
      _two['other'])
_mj = RL.analyze([deed, rec('LIEN', '3/3/2022', '33500', '37', 45000, 'GARCIA MARIA-JOSE', first='CITY OF MIAMI'),
                  rec('RELEASE OF LIEN', '9/9/2023', '34000', '37', 0, 'GARCIA MARIA L', first='CITY OF MIAMI',
                      folio='3099999999999')], FOLIO, 12000, ftype='HOA', owner='MARIA-JOSE GARCIA')
check("a neighbour sharing the owner's surname and first name does not release the owner's lien",
      _mj['other'][0]['st'] == 'OPEN' and _mj['code_open'] == 45000, _mj['other'])
_ph2 = RL.analyze([deed, rec('CLAIM OF LIEN', '1/1/2024', '34000', '40', 9000, 'OWNER TESTER',
                             first='SUNSET HOMEOWNERS ASSOCIATION INC'),
                   rec('CLAIM OF LIEN', '1/2/2024', '34000', '41', 15000, 'OWNER TESTER',
                       first='SUNSET HOMEOWNERS ASSOCIATION PHASE II INC')],
                  FOLIO, 9500, ftype='HOA', plaintiff='SUNSET HOMEOWNERS ASSOCIATION INC', owner='OWNER TESTER',
                  case='2024-000001-CC-05')
check("a sibling association whose name EXTENDS the plaintiff's is another claim",
      [bool(o.get('own_case')) for o in _ph2['other']] == [True, False] and _ph2['hoa_open'] == 15000, _ph2['other'])
_cj = RL.analyze([deed, rec('JUDGMENT', '3/1/2025', '35000', '2', 9510, 'OWNER TESTER', first='CITY OF MIAMI')],
                 FOLIO, 9500, ftype='HOA', plaintiff='SUNSET HOMEOWNERS ASSOCIATION INC', owner='OWNER TESTER',
                 case='2024-000001-CC-05')
check("a City's judgment near this case's figure is still the City's", not _cj['other'][0].get('own_case')
      and _cj['code_open'] == 9510, _cj['other'])
check("a hyphenated first name still names the owner",
      RL._names_owner('GARCIA MARIA-JOSE', [RL._owner_words('MARIA-JOSE GARCIA')]))
_boa2 = RL.analyze([deed, rec('JUDGMENT', '6/1/2025', '35000', '1', 8500, 'TESTER OWNER', first='BANK OF AMERICA NA',
                              folio='', subdiV_NAME='')],
                   FOLIO, 310000, ftype='MORTGAGE', plaintiff='BANK OF AMERICA NA', owner='OWNER TESTER',
                   case='2024-000001-CA-01')
check("the plaintiff bank's other judgment (another figure, not on this parcel) is a debt, not this case",
      not _boa2['other'][0].get('own_case') and _boa2['code_open'] == 8500, _boa2['other'])
_boa = RL.analyze([deed, rec('JUDGMENT', '5/5/2025', '34999', '21', 40000, 'TESTER OWNER',
                             first='UNITED STATES OF AMERICA', folio='', subdiV_NAME='')],
                  FOLIO, 0, ftype='MORTGAGE', plaintiff='BANK OF AMERICA, N.A.', owner='OWNER TESTER',
                  case='2025-000001-CA-01')
check("BANK OF AMERICA is not the UNITED STATES OF AMERICA: a federal judgment is never this case",
      not _boa['other'][0].get('own_case') and _boa['irs_open'] == 40000, _boa['other'])
_long = 'MIAMI-DADE COUNTY WATER AND SEWER DEPARTMENT'
_lg = RL.analyze([deed, rec('LIEN', '3/3/2021', '33000', '22', 900, 'PRIOR OWNER', first=_long),
                  rec('RELEASE OF LIEN', '9/9/2023', '34000', '22', 0, 'PRIOR OWNER', first=_long)],
                 FOLIO, 12000, ftype='HOA', owner='OWNER TESTER')
check('a creditor name over 40 letters still pairs with its own release', _lg['other'][0]['st'] == 'RELEASED'
      and _lg['code_open'] == 0, _lg['other'])
_nt = RL.analyze([deed, rec('NOTICE', '3/3/2022', '33500', '23', 0, 'OWNER TESTER', first='CITY OF MIAMI'),
                  rec('CERTIFICATE OF TITLE', '3/3/2022', '33500', '24', 250000, 'OWNER TESTER', first='MIAMI-DADE COUNTY CLERK'),
                  rec('WAIVER OF LIEN', '3/3/2022', '33500', '25', 0, 'OWNER TESTER', first='CITY OF MIAMI')],
                 FOLIO, 12000, ftype='HOA', owner='OWNER TESTER')
check('a clerk certificate and a lien waiver are shown, never counted; an unpriced City notice is a ceiling',
      _nt['code_open'] == 0 and _nt['other_open_unpriced'] == 1 and ES.state_of(_nt) == 'unpriced', _nt['other'])
_np = RL.analyze([deed, rec('NOTICE - NOT', '3/3/2022', '33500', '23', 9000, 'OWNER TESTER', first='CITY OF MIAMI'),
                  rec('NOTICE - NOT', '4/4/2022', '33600', '23', 48000, 'OWNER TESTER',
                      first='INTERNAL REVENUE SERVICE', folio='', subdiV_NAME='')],
                 FOLIO, 12000, ftype='HOA', owner='OWNER TESTER')
check("a priced 'NOTICE - NOT' from a City or the IRS is counted, as main counted it",
      _np['code_open'] == 9000 and _np['irs_open'] == 48000, _np['other'])
_cb = RL.analyze([deed, rec('JUDGMENT', '4/4/2021', '33000', '26', 20000, 'TESTER OWNER', first='COMMUNITY BANK OF FLORIDA',
                            folio='', subdiV_NAME='')], FOLIO, 12000, ftype='HOA', owner='OWNER TESTER')
check("COMMUNITY BANK's money judgment follows the owner like any other", _cb['code_open'] == 20000, _cb['other'])
for _p, _w in (('GARCIA LOPEZ MARIA', 'MARIA GARCIA-LOPEZ'), ("O'BRIEN KATHLEEN", 'KATHLEEN OBRIEN'),
               ('OBRIEN KATHLEEN', "KATHLEEN O'BRIEN")):
    check('%r names the owner %r' % (_p, _w), RL._names_owner(_p, [RL._owner_words(_w)]))
check("an association suing in circuit court is an association's case: the first mortgage survives",
      RL._fc_type('2024-000009-CA-01', 'HOA/Condo', 'PALM TEST CONDOMINIUM ASSOCIATION INC') == 'HOA'
      and RL._fc_type('2024-000009-CA-01', 'HOA/Condo') == 'HOA'
      and RL._fc_type('2024-000009-CA-01', 'Bank/Mortgage') == 'MORTGAGE')
for _pl in ('FEDERAL NATIONAL MORTGAGE ASSOCIATION', 'GOVERNMENT NATIONAL MORTGAGE ASSOCIATION',
            'COMMUNITY LOAN SERVICING LLC', 'FIRST COMMUNITY BANK'):
    check("a lender classify() typed 'HOA/Condo' (%s) still forecloses the mortgage in circuit court" % _pl,
          RL._fc_type('2024-000009-CA-01', 'HOA/Condo', _pl) == 'MORTGAGE')
_fn = RL.analyze([deed, rec('MORTGAGE', '2/1/2008', '26100', '11', 300000, 'OWNER TESTER', first='SOME LENDER')],
                 FOLIO, 320000, ftype=RL._fc_type('2025-012345-CA-01', 'HOA/Condo', 'FEDERAL NATIONAL MORTGAGE ASSOCIATION'),
                 plaintiff='FEDERAL NATIONAL MORTGAGE ASSOCIATION', owner='OWNER TESTER')
check("Fannie Mae's circuit case never counts the loan it forecloses as a surviving senior", not _fn.get('surv'), _fn)
_fnb = CD._b(dict(res, ftype='MORTGAGE', case_type='HOA/Condo', judgment=60000))['foreclosed_debt']
check("dossier b: a lender typed 'HOA/Condo' by classify is not called an association's case",
      'survives' not in (_fnb.get('note') or ''), _fnb)
_hca = CD._b(dict(res, ftype='HOA', case_type='HOA/Condo', judgment=60000))['foreclosed_debt']
check("dossier b: a circuit association case never names the first mortgage as the foreclosed debt",
      _hca['recorded_face'] is None and _hca['instrument'] is None and 'survives' in (_hca.get('note') or ''), _hca)

# ---- review round 10: a re-read only ever adds lien rows
_wide = {'conf': 'ok', 'liens': [{'d': '2/1/2008', 'amt': 150000, 'st': 'OPEN', 'bp': '29500/100'}],
         'mtg_open_unpriced': 0, 'code_open': 300, 'searched_as': 'JOHN SMITH'}
_narrow = RL.analyze([deed, city3], FOLIO, 12000, ftype='HOA', owner='OWNER TESTER')
check("a narrower re-read is narrower, not a payoff", RL._mortgages_narrower(_wide, _narrow))
_lay = RL._lay_lien_rows(_wide, _narrow)
check("a narrower re-read keeps the earlier chain whole and only lists the new lien rows",
      {k: v for k, v in _lay.items() if k not in ('other_seen', 'wider_repull')} == _wide
      and 'other' not in _lay and _lay['other_seen'] == _narrow['other'] and _lay['code_open'] == 300
      and 'not counted' in _lay['wider_repull'], _lay)
check("an unpriced loan the old chain counted is not lost either",
      RL._mortgages_narrower({'liens': [], 'mtg_open_unpriced': 1}, _narrow))
check("a re-read that shows the old loan released by a satisfaction naming it is news, not narrower",
      not RL._mortgages_narrower({'liens': [{'bp': '26100/11', 'st': 'OPEN', 'amt': 1}]},
                                 {'liens': [{'bp': '26100/11', 'st': 'SATISFIED', 'amt': 1, 'sat_by': 'book/page'}]}))
check("a release INFERRED from a lender's name (it may be a namesake's) never retires a loan on a re-read",
      RL._mortgages_narrower({'liens': [{'bp': '26100/11', 'st': 'OPEN', 'amt': 1}]},
                             {'liens': [{'bp': '26100/11', 'st': 'SATISFIED', 'amt': 1, 'sat_by': 'lender chain'}]}))
_wf = rec('MORTGAGE', '2/1/2012', '29900', '101', 280000, 'WELLS FARGO BANK NA', first='OWNER TESTER')
_nsat = rec('SATISFACTION', '6/1/2021', '32000', '9', 0, 'WELLS FARGO BANK NA', first='TESTER MARIA',
            folio='3099999999999', subdiV_NAME='ELSEWHERE')
_rd = RL.analyze([deed, _wf, _nsat], FOLIO, 12000, ftype='HOA', owner='OWNER TESTER')
check("analyze says how it decided a loan was satisfied",
      [(l['st'], l.get('sat_by')) for l in _rd['liens']] == [('SATISFIED', 'lender chain')], _rd['liens'])
check("so a namesake's satisfaction on a re-read keeps the old chain's open loan",
      RL._mortgages_narrower({'liens': [{'bp': '29900/101', 'st': 'OPEN', 'amt': 280000}]}, _rd))
check("a lost second foreclosure is narrower", RL._mortgages_narrower({'liens': [], 'second_fc': {'case': 'x'}},
                                                                     {'liens': [], 'second_fc': None}))

# ---- review round 16: a re-read of an OLD analyzer's chain does not keep its known over-counts
_bankfj = RL.analyze([deed, rec('JUDGMENT', '5/5/2024', '34932', '1256', 987654, 'OWNER TESTER', first=PLAINTIFF)],
                     FOLIO, 987654, ftype='MORTGAGE', plaintiff=PLAINTIFF, owner='OWNER TESTER', case='2024-014878-CA-01')
_o = {}
RL._carry_lien_totals({'conf': 'ok', 'liens': [], 'code_open': 987654}, _bankfj, _o)
check("a re-read drops the case's own final judgment the old analyzer summed, even when the old search never said how wide",
      _bankfj['other'][0].get('own_case') and _o['code_open'] == 0 and not _o.get('lien_totals_kept'), (_bankfj['other'], _o))
_o = {}
RL._carry_lien_totals({'conf': 'ok', 'liens': [], 'nrec': 2, 'hoa_open': 15000}, dict(_bankfj, nrec=2, hoa_open=0), _o)
check("a re-read as wide as the old search replaces an old analyzer's lien totals", _o['hoa_open'] == 0, _o)
_o = {}
RL._carry_lien_totals({'conf': 'ok', 'liens': [], 'nrec': 40, 'code_open': 5000}, dict(_bankfj, nrec=2, other=[]), _o)
check("a narrower re-read keeps an old analyzer's larger total, and says so",
      _o['code_open'] == 5000 and _o.get('lien_totals_kept'), _o)
_o = {}
RL._carry_lien_totals({'conf': 'ok', 'liens': [], 'nrec': 40, 'code_open': 5000, 'other': []},
                      dict(_bankfj, nrec=2), _o)
check("a narrower re-read of a chain the new rules wrote keeps the larger total without a legacy note",
      _o['code_open'] == 5000 and not _o.get('lien_totals_kept'), _o)
import foreclosure_leads as FLD
_flsrc16 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'foreclosure_leads.py'), encoding='utf-8').read()
_ownassn = {'other': [{'own_case': True, 'kind': 'association', 'amt': 6000, 'doc': 'CLAIM OF LIEN'}]}
check("the board stops netting only when the chain took the association's own claim out of orhoa",
      FLD._hoa_own_out(_ownassn) and not FLD._hoa_own_out(dict(_ownassn, hoa_own_in=True))
      and FLD._hoa_own_out(dict(_ownassn, lien_totals_kept='a City total was kept'))
      and not FLD._hoa_own_out({'other': [{'own_case': True, 'kind': 'association', 'amt': 9500,
                                           'doc': 'FINAL JUDGMENT'}]})
      and not FLD._hoa_own_out({'other': [{'kind': 'association', 'amt': 12000}]})
      and not FLD._hoa_own_out({'other': []}) and _flsrc16.count('if _hoa_own_out(') == 2)
# review round 18: a kept total the own claim was taken out of is netted once, not twice
_oc = {'own_case': True, 'kind': 'association', 'amt': 9000, 'd': '3/1/2024', 'doc': 'CLAIM OF LIEN', 'st': 'OPEN',
       'old_bucket': 'hoa_open'}
def _carried(old, new):
    _c = dict(old, **new)
    RL._carry_lien_totals(old, new, _c)
    return _c
_lr = _carried({'conf': 'ok', 'liens': [], 'hoa_open': 14000, 'nrec': 80, 'traced': '2025-06-01'},
               {'conf': 'ok', 'liens': [], 'hoa_open': 0, 'nrec': 30, 'other': [_oc]})
check("a kept association total with the plaintiff's claim taken out is not netted against the judgment again",
      _lr['hoa_open'] == 5000 and not _lr.get('hoa_own_in') and FLD._hoa_own_out(_lr), _lr)
_lr2 = _carried({'conf': 'ok', 'liens': [], 'hoa_open': 5000, 'code_open': 3000, 'nrec': 80},
                {'conf': 'ok', 'liens': [], 'hoa_open': 5000, 'code_open': 0, 'nrec': 30, 'other': [_oc]})
check("a kept City total does not switch the association netting back on", FLD._hoa_own_out(_lr2)
      and _lr2['code_open'] == 3000 and _lr2.get('lien_totals_kept'), _lr2)
_lr3 = _carried({'conf': 'ok', 'liens': [], 'hoa_open': 14000, 'nrec': 80},
                {'conf': 'ok', 'liens': [], 'hoa_open': 0, 'nrec': 30,
                          'other': [dict(_oc, doc='FINAL JUDGMENT', amt=9500, d='3/1/2025')]})
_lr4 = _carried({'conf': 'ok', 'liens': [], 'hoa_open': 3000, 'nrec': 80},
                {'conf': 'ok', 'liens': [], 'hoa_open': 0, 'nrec': 30, 'other': [_oc]})
check("an association total smaller than the plaintiff's claim never held it: kept, and not netted against the judgment",
      _lr4['hoa_open'] == 3000 and not _lr4.get('hoa_own_in') and FLD._hoa_own_out(_lr4), _lr4)
_lr5 = _carried({'conf': 'ok', 'liens': [], 'hoa_open': 5000, 'nrec': 30, 'traced': '2026-06-01'},
                {'conf': 'ok', 'liens': [], 'hoa_open': 0, 'nrec': 20,
                 'other': [dict(_oc, amt=5000, d='3/1/2024'), dict(_oc, doc='FINAL JUDGMENT', amt=25000, d='5/1/2026')]})
check("an association total the size of the plaintiff's claim, beside its bigger judgment, keeps the netting",
      _lr5['hoa_open'] == 5000 and _lr5.get('hoa_own_in') and not FLD._hoa_own_out(_lr5), _lr5)
check("a kept association total that still holds the plaintiff's claim keeps the netting",
      _lr3.get('hoa_own_in') and not FLD._hoa_own_out(_lr3), _lr3)

# review round 19: an own filing comes out of the bucket the OLD analyzer summed it in, and only then
_r19 = RL.analyze([deed, rec('LIEN - LIE', '5/5/2020', '31500', '1', 15000, 'CITY OF MIAMI', first='OWNER TESTER'),
                   rec('JUDGMENT - JUD', '5/5/2023', '33900', '1', 12000, 'SUNSET TEST CONDOMINIUM ASSOCIATION INC',
                       first='OWNER TESTER', folio='', subdiV_NAME='')],
                  FOLIO, 12000, ftype='HOA', plaintiff='SUNSET TEST CONDOMINIUM ASSOCIATION INC', owner='OWNER TESTER',
                  case='2023-000555-CC-05')
_j19 = [o for o in _r19['other'] if o.get('own_case')]
check("the association's own off-parcel judgment is marked where the old analyzer left it: nowhere",
      _j19 and 'old_bucket' not in _j19[0], _r19['other'])
_o = {}
RL._carry_lien_totals({'conf': 'ok', 'liens': [], 'code_open': 15000, 'nrec': 14},
                      {'conf': 'ok', 'liens': [], 'code_open': 0, 'nrec': 3, 'other': _j19}, _o)
check("a narrower re-read never takes the association's judgment out of the City's $15,000",
      _o['code_open'] == 15000, _o)
_r19b = RL.analyze([deed, rec('JUDGMENT - JUD', '5/5/2023', '33900', '1', 12000, 'OWNER TESTER',
                              first='SUNSET TEST CONDOMINIUM ASSOCIATION INC')],
                   FOLIO, 12000, ftype='HOA', plaintiff='SUNSET TEST CONDOMINIUM ASSOCIATION INC', owner='OWNER TESTER',
                   case='2023-000555-CC-05')
_o = {}
RL._carry_lien_totals({'conf': 'ok', 'liens': [], 'code_open': 12000, 'nrec': 14},
                      dict(_r19b, nrec=3), _o)
check("the association's own judgment the old analyzer put in code_open comes out of code_open",
      [o.get('old_bucket') for o in _r19b['other'] if o.get('own_case')] == ['code_open'] and _o['code_open'] == 0,
      (_r19b['other'], _o))
_lp19 = RL.analyze([deed, rec('LIS PENDENS - LIS', '5/5/2023', '33900', '2', 12000, 'SUNSET TEST CONDOMINIUM ASSOCIATION INC',
                               first='OWNER TESTER')],
                   FOLIO, 12000, ftype='HOA', plaintiff='SUNSET TEST CONDOMINIUM ASSOCIATION INC', owner='OWNER TESTER',
                   case='2023-000555-CC-05')
check("the case's own lis pendens was never in an old total, so it never comes out of one",
      [o for o in _lp19['other'] if o.get('own_case')] and
      not any(o.get('old_bucket') for o in _lp19['other']), _lp19['other'])
_ca = {'conf': 'ok', 'ftype': 'MORTGAGE', 'judgment': 60000, 'nrec': 20, 'second_fc': None,
       'liens': [{'d': '2/1/2008', 'amt': 300000, 'st': 'OPEN', 'bp': '26100/11'}], 'surv': 0, 'surv_first': 0,
       'first_est': 300000, 'open_count': 1}
_lr19 = RL._lay_lien_rows(_ca, {'conf': 'ok', 'ftype': 'HOA', 'liens': [], 'nrec': 5, 'other': [],
                                'second_fc': {'party': 'SOME BANK NA'}, 'judgment': 60000})
check("a narrower re-read changes no type, survival or foreclosure flag the board counts",
      _lr19['ftype'] == 'MORTGAGE' and _lr19['surv'] == 0 and _lr19['second_fc'] is None, _lr19)
check("a lender's foreclosure the narrower re-read saw is named in the re-pull flag",
      'SOME BANK NA' in _lr19['wider_repull'], _lr19)

# review round 20: cents, and what a narrower re-read still knows about the case
_fjc = RL.analyze([deed, rec('JUDGMENT', '5/5/2024', '34932', '1256', 1022358.91, 'OWNER TESTER', first=PLAINTIFF)],
                  FOLIO, 1022358.91, ftype='MORTGAGE', plaintiff=PLAINTIFF, owner='OWNER TESTER', case='2024-014878-CA-01')
for _old in (1022358.91, 1022358.20):
    _o = {}
    RL._carry_lien_totals({'conf': 'ok', 'liens': [], 'code_open': _old, 'nrec': 200, 'traced': '2026-09-10'},
                          dict(_fjc, nrec=40), _o)
    check("a judgment of $%s comes out of the old total whole, cents and all" % _old,
          _o['code_open'] == 0 and not _o.get('lien_totals_kept'), (_fjc['other'], _o))
_lr20 = RL._lay_lien_rows({'conf': 'ok', 'ftype': 'MORTGAGE', 'nrec': 200, 'second_fc': None,
                           'liens': [{'d': '2/1/2008', 'amt': 300000, 'st': 'OPEN', 'bp': '26100/11'},
                                     {'d': '2/1/2012', 'amt': 50000, 'st': 'OPEN', 'bp': '28100/11'}]},
                          {'conf': 'ok', 'ftype': 'MORTGAGE', 'nrec': 40, 'other': [], 'judgment': 412345.67,
                           'capped': True, 'liens': [{'d': '2/1/2008', 'amt': 300000, 'st': 'OPEN', 'bp': '26100/11'}]})
check("a narrower re-read writes nothing but the listed rows and the flag onto the earlier chain",
      set(_lr20) == {'conf', 'ftype', 'nrec', 'second_fc', 'liens', 'other_seen', 'wider_repull'}
      and _lr20['nrec'] == 200 and _lr20['other_seen'] == [], _lr20)
_fd20 = CD._b({'conf': 'ok', 'ftype': 'MORTGAGE', 'first_est': 300000, 'liens': []})['foreclosed_debt']
check("dossier b: an old chain with no stored judgment does not claim the listing has none",
      _fd20['basis'] == 'the judgment was not stored with this chain', _fd20)

# review round 21
_wv = RL.analyze([deed, rec('LIEN', '5/5/2019', '31100', '1', 5000, 'OWNER TESTER', first='CITY OF MIAMI'),
                  rec('WAIVER OF LIEN', '3/3/2020', '31300', '1', 0, 'OWNER TESTER', first='CITY OF MIAMI'),
                  rec('RELEASE OF LIEN', '9/9/2021', '32000', '1', 0, 'OWNER TESTER', first='CITY OF MIAMI')],
                 FOLIO, 12000, ftype='HOA', owner='OWNER TESTER')
check("a City waiver beside the City's lien does not stop the lien taking its release",
      _wv['code_open'] == 0 and [o['st'] for o in _wv['other'] if o['doc'] == 'LIEN'] == ['RELEASED'], _wv['other'])
_pw = RL.analyze([deed, rec('JUDGMENT', '4/4/2021', '33000', '5', 5000, 'TESTER OWNER', first='CAPITAL ONE BANK USA NA',
                            folio='', subdiV_NAME=''),
                  rec('SATISFACTION OF JUDGMENT', '9/9/2023', '34000', '5', 0, 'TESTER OWNER', first='CAPITAL ONE BANK USA NA',
                      folio='', subdiV_NAME=''),
                  rec('NOTICE - NOT', '4/4/2022', '33600', '23', 48000, 'OWNER TESTER', first='INTERNAL REVENUE SERVICE',
                      folio='', subdiV_NAME=''),
                  rec('CERTIFICATE OF RELEASE OF FEDERAL TAX LIEN', '6/6/2024', '34500', '2', 0, 'OWNER TESTER',
                      first='INTERNAL REVENUE SERVICE', folio='', subdiV_NAME='')],
                 FOLIO, 12000, ftype='HOA', owner='OWNER TESTER')
check("a paid money judgment and a released federal tax lien, neither on a folio, are released by their own releases",
      _pw['code_open'] == 0 and _pw['irs_open'] == 0, _pw['other'])
_pn = RL.analyze([deed, rec('JUDGMENT', '4/4/2021', '33000', '5', 5000, 'TESTER OWNER', first='CAPITAL ONE BANK USA NA',
                            folio='', subdiV_NAME=''),
                  rec('SATISFACTION OF JUDGMENT', '9/9/2023', '34000', '5', 0, 'TESTER MARIA', first='CAPITAL ONE BANK USA NA',
                      folio='', subdiV_NAME='')],
                 FOLIO, 12000, ftype='HOA', owner='OWNER TESTER')
check("a namesake's satisfaction off the parcel never frees the owner's money judgment", _pn['code_open'] == 5000, _pn['other'])
_nm = RL._lay_lien_rows({'conf': 'ok', 'ftype': 'MORTGAGE', 'nrec': 50, 'mtg_open_unpriced': 0,
                         'liens': [{'d': '2/1/2008', 'amt': 300000, 'st': 'OPEN', 'bp': '26100/11'}]},
                        {'conf': 'ok', 'ftype': 'MORTGAGE', 'nrec': 10, 'other': [], 'mtg_open_unpriced': 1,
                         'liens': [{'d': '2/1/2015', 'amt': 80000, 'st': 'OPEN', 'bp': '29000/5', '_dt': 'x'}]})
check("a mortgage only the narrower re-read found is listed, not counted",
      [l['bp'] for l in _nm['liens']] == ['26100/11'] and _nm['mtg_open_unpriced'] == 0
      and [(l['bp'], l['kind']) for l in _nm['other_seen']] == [('29000/5', 'mortgage')]
      and '_dt' not in _nm['other_seen'][0], _nm)
_dnm = CD._b(_nm)
check("dossier b lists the narrower re-read's rows apart from every total, and says a wider search is needed",
      _dnm['listed_not_counted'][0]['book_page'] == '29000/5' and _dnm['wider_search_needed']
      and not _dnm['other_instruments'], _dnm)
check("a chain never re-read narrower lists nothing", CD._b({'conf': 'ok', 'liens': []})['listed_not_counted'] is None)
for _ix in ('PEREZ A', 'PEREZ ANTONIO'):
    _mi = RL.analyze([deed, rec('NOTICE - NOT', '4/4/2022', '33600', '23', 48000, _ix, first='INTERNAL REVENUE SERVICE',
                                folio='', subdiV_NAME='')], FOLIO, 12000, ftype='HOA', owner='JOSE A PEREZ')
    check("%r may be JOSE A PEREZ: the owner's own middle initial counts" % _ix, _mi['irs_open'] == 48000, _mi['other'])
_mi = RL.analyze([deed, rec('NOTICE - NOT', '4/4/2022', '33600', '23', 48000, 'PEREZ MARIA', first='INTERNAL REVENUE SERVICE',
                            folio='', subdiV_NAME='')], FOLIO, 12000, ftype='HOA', owner='JOSE A PEREZ')
check("'PEREZ MARIA' is still a namesake of JOSE A PEREZ", _mi['irs_open'] == 0, _mi['other'])

# review round 17: an own claim the old total never held is not taken out of it
_o = {}
RL._carry_lien_totals({'conf': 'ok', 'liens': [], 'code_open': 3000},
                      dict(_bankfj, code_open=0, other=[dict(_bankfj['other'][0], amt=250000)]), _o)
check("a re-read never takes the case's own judgment out of a smaller old total that never held it",
      _o['code_open'] == 3000 and _o.get('lien_totals_kept'), _o)
_o = {}
RL._carry_lien_totals({'conf': 'ok', 'liens': [], 'code_open': 400000, 'traced': '2024-01-01'},
                      dict(_bankfj, code_open=0, other=[dict(_bankfj['other'][0], d='5/5/2024', amt=250000)]), _o)
check("a re-read never takes out a judgment recorded after the old search ran",
      _o['code_open'] == 400000, _o)
_hl = RL.analyze([rec('DEED', '2/1/2008', '26100', '10', 0, 'GARCIA JOSE JR', first='PRIOR SELLER'),
                  rec('LIEN', '5/5/2019', '31100', '1', 4000, 'GARCIA JOSE', first='CITY OF MIAMI'),
                  rec('RELEASE OF LIEN', '6/6/2020', '31200', '1', 0, 'GARCIA JR ROBERTO', first='CITY OF MIAMI',
                      folio='0100000000077')], FOLIO, 12000, ftype='HOA', owner='JOSE GARCIA')
check("a deed to 'GARCIA JOSE JR' does not make every GARCIA JR an owner whose release frees this lien",
      _hl['code_open'] == 4000 and _hl['other'][0]['st'] == 'OPEN', _hl['other'])
_hs = RL.analyze([rec('DEED', '2/1/2008', '26100', '10', 0, 'GARCIA JOSE', first='GARCIA MARIA'),
                  rec('LIEN', '5/5/2019', '31100', '1', 4000, 'GARCIA JOSE', first='CITY OF MIAMI'),
                  rec('RELEASE OF LIEN', '6/6/2020', '31200', '1', 0, 'GARCIA MARIA', first='CITY OF MIAMI',
                      folio='0100000000077')], FOLIO, 12000, ftype='HOA', owner='JOSE GARCIA')
check("the seller on the owner's deed is not the household", _hs['code_open'] == 4000, _hs['other'])
_sa = RL.analyze([deed, rec('CLAIM OF LIEN', '5/5/2023', '33100', '1', 12000, 'OWNER TESTER',
                            first='SUNSET TEST HOMEOWNERS ASSN INC')],
                 FOLIO, 12500, ftype='HOA', plaintiff='SUNSET TEST HOMEOWNERS ASSOCIATION INC', owner='OWNER TESTER',
                 case='2023-000123-CC-05')
check("an association's own claim indexed 'ASSN' is its own case, not a second debt",
      _sa['other'][0].get('own_case') and _sa['hoa_open'] == 0, (_sa['other'], _sa['hoa_open']))
_lw = {'Case #': '2099-000950-CA-01', 'case_type': 'Bank/Mortgage'}
ES.apply(_lw, {'conf': 'ok', 'nrec': 30, 'second_fc': None, 'liens': [], 'other_open_unpriced': 1})
check("a lender's case keeps its 'the chain missed the loan' reason beside an amountless lien",
      _lw['eqstate'] == 'none' and _lw['eqstate_why'] == ES.LENDER_OWN_CASE_WHY, _lw)
_dl = CD._d({'conf': 'ok', 'nrec': 30, 'second_fc': None, 'liens': [], 'other_open_unpriced': 1},
            {'status': 'empty'}, {'case_type': 'Bank/Mortgage'})
check("dossier d: the same reason beside an amountless lien", _dl['verdict'] == ES.LENDER_OWN_CASE_WHY, _dl['verdict'])
_bfc = {'Case #': '2099-000900-CC-01', 'ctype': 'HOA/Condo', 'orsecond': {'party': 'ABC BANK NA'}}
ES.apply(_bfc, {'conf': 'ok', 'nrec': 30, 'second_fc': None, 'liens': [], 'other_open_unpriced': 1})
check("a ceiling made only by an amountless lien is still demoted beside a lender's separate foreclosure",
      _bfc['eqstate'] == 'unpriced' and ES.demote_for_bank_fc(_bfc) and _bfc['eqstate'] == 'none'
      and _bfc.get('eqbankfc'), _bfc)
_bfc2 = {'Case #': '2099-000901-CC-01', 'ctype': 'HOA/Condo', 'orsecond': {'party': 'ABC BANK NA'}}
ES.apply(_bfc2, {'conf': 'ok', 'nrec': 30, 'second_fc': None, 'liens': [], 'mtg_open_unpriced': 1})
check("an unpriced MORTGAGE ceiling is not re-labelled by the separate-case rule", not ES.demote_for_bank_fc(_bfc2)
      and _bfc2['eqstate'] == 'unpriced', _bfc2)

# ---- review round 22
_oh = RL.analyze([deed, rec('JUDGMENT', '4/4/2021', '33000', '7', 7000, 'OWNER TESTER', first='MIAMI-DADE COUNTY',
                            folio='', subdiV_NAME=''),
                  rec('LIEN', '1/1/2020', '32000', '7', 900, 'OWNER TESTER', first='MIAMI-DADE COUNTY',
                      folio='3099999999999', subdiV_NAME='ELSEWHERE'),
                  rec('RELEASE OF LIEN', '9/9/2022', '33500', '7', 0, 'OWNER TESTER', first='MIAMI-DADE COUNTY',
                      folio='3099999999999', subdiV_NAME='ELSEWHERE')], FOLIO, 12000, ftype='HOA', owner='OWNER TESTER')
check("a release on the owner's other house never frees a person-wide judgment", _oh['code_open'] == 7000, _oh['other'])
_lf = RL.analyze([deed, rec('JUDGMENT', '4/4/2018', '32900', '8', 20000, 'OWNER TESTER', first='CITY OF MIAMI',
                            folio='', subdiV_NAME=''),
                  rec('LIEN', '5/5/2019', '33000', '8', 500, 'OWNER TESTER', first='CITY OF MIAMI'),
                  rec('LIEN', '6/6/2020', '33100', '8', 600, 'OWNER TESTER', first='CITY OF MIAMI'),
                  rec('RELEASE OF LIEN', '9/9/2022', '33500', '8', 0, 'OWNER TESTER', first='CITY OF MIAMI')],
                 FOLIO, 12000, ftype='HOA', owner='OWNER TESTER')
check("a release the parcel's liens could not share out never frees an unrelated person-wide judgment",
      _lf['code_open'] == 21100, _lf['other'])
import json, types, tempfile, io, contextlib
for _ow22, _ix22 in (('JOSE E PEREZ', 'PEREZ MARIA ET AL'), ('JOSE J PEREZ', 'PEREZ MARIA JR'), ('JOSE T PEREZ', 'PEREZ MARIA TR')):
    check("%r is not %s: role words are no initial" % (_ix22, _ow22),
          not RL._maybe_owner(_ix22, [RL._owner_words(_ow22)]))
_sat22 = RL._lay_lien_rows({'conf': 'ok', 'ftype': 'MORTGAGE', 'judgment': 300000, 'nrec': 50,
                            'liens': [{'d': '2/1/2004', 'amt': 90000, 'st': 'OPEN', 'bp': '26100/11'},
                                      {'d': '2/1/2012', 'amt': 300000, 'st': 'OPEN', 'bp': '29900/101'}]},
                           {'conf': 'ok', 'ftype': 'MORTGAGE', 'nrec': 10, 'other': [], 'judgment': 300000,
                            'liens': [{'d': '2/1/2004', 'amt': 90000, 'st': 'SATISFIED', 'bp': '26100/11',
                                       'sat_by': 'book/page'}]})
check("a narrower re-read's satisfaction retires nothing; the kept loans stay open until a wider search",
      [l['st'] for l in _sat22['liens']] == ['OPEN', 'OPEN'] and _sat22['other_seen'] == [], _sat22)
_lt = tempfile.mkdtemp()
_lg = os.path.join(_lt, 'led.json')
json.dump({'cap': 5.0, 'counted_usd': 0.099, 'unit_usd': 0.0033, 'runs': [{'solves': 10}, {'solves': 20}]}, open(_lg, 'w'))
open(_lg + '.lock', 'w').write('another run')
_sv22 = dict(RL._SPEND)
RL._SPEND.update(cap=5.0, prior=0.0, led={'runs': [{'solves': 10}]}, submits=10, unit=RL.PAID_SOLVE_USD, ledger=_lg,
                 stopped='', lock=_lg + '.lock', lock_id='this run')
with contextlib.redirect_stdout(io.StringIO()):
    RL._ledger_save(final=True)
_after = json.load(open(_lg))
RL._SPEND.clear(); RL._SPEND.update(_sv22)
check("a run whose ledger another run took over never writes its stale total back",
      _after['counted_usd'] == 0.099 and len(_after['runs']) == 2, _after)

# ---- $0 re-analysis of chains traced before the lien rows existed
import json, tempfile, types
_tmp = tempfile.mkdtemp()
_leads = [{'Case #': '2099-000100-CA-01', 'owner_clean': 'OWNER W', 'Folio': FOLIO, 'judgment': 1},
          {'Case #': '2099-000101-CA-01', 'owner_clean': 'OWNER A', 'Folio': FOLIO, 'judgment': 1, 'plaintiff': PLAINTIFF},
          {'Case #': '2099-000102-CA-01', 'owner_clean': 'JOHN QUINCY TESTER', 'Folio': FOLIO, 'judgment': 1},
          {'Case #': '2099-000103-CA-01', 'owner_clean': 'OWNER C', 'Folio': FOLIO, 'judgment': 1},
          {'Case #': '2099-000104-CA-01', 'owner_clean': 'OWNER D', 'Folio': FOLIO, 'judgment': 1}]
json.dump(_leads, open(os.path.join(_tmp, 'leads_final.json'), 'w'))
_chains0 = {c['Case #']: {'conf': 'ok', 'liens': [], 'chain_note': 'kept'} for c in _leads}
_chains0['2099-000100-CA-01'] = dict(_wide)
_chains0['2099-000101-CA-01']['code_open'] = 12000      # a spouse's judgment the wider old search found
json.dump(_chains0, open(os.path.join(_tmp, 'records_liens.json'), 'w'))
json.dump({'OWNER A': 'tokA', 'JOHN QUINCY TESTER': 'tokDEAD', 'OWNER D': 'tokEMPTY', 'OWNER W': 'tokA'},
          open(os.path.join(_tmp, 'records_qs.json'), 'w'))
_spent = []
open(os.path.join(_tmp, 'gen_records_qs.py'), 'w').write('')
_saved = {k: getattr(RL, k) for k in ('LEADS', 'OUT', 'QS_CACHE', 'HERE', 'records_by_qs', 'fetch_via_turnstile',
                                       'camoufox_session', 'mint_and_fetch', 'time')}
_argv = sys.argv
try:
    RL.LEADS, RL.OUT = os.path.join(_tmp, 'leads_final.json'), os.path.join(_tmp, 'records_liens.json')
    RL.QS_CACHE, RL.HERE = os.path.join(_tmp, 'records_qs.json'), _tmp
    RL.records_by_qs = lambda qs: [deed, city1] if qs == 'tokA' else ([] if qs == 'tokEMPTY' else None)
    RL.fetch_via_turnstile = lambda *a, **k: _spent.append('turnstile')
    RL.camoufox_session = lambda: _spent.append('camoufox') or (None, None)
    RL.mint_and_fetch = lambda *a, **k: _spent.append('mint')
    RL.time = types.SimpleNamespace(strftime=__import__('time').strftime, sleep=lambda s: None,
                                    time=__import__('time').time)
    import io, contextlib
    _dry = io.StringIO()
    sys.argv = ['records_liens.py', '--reanalyze', '--dry-run']
    with contextlib.redirect_stdout(_dry):
        RL.main()
    sys.argv = ['records_liens.py', '--reanalyze']
    RL.main()
finally:
    for k, v in _saved.items():
        setattr(RL, k, v)
    sys.argv = _argv
_out = json.load(open(os.path.join(_tmp, 'records_liens.json')))
check('--reanalyze re-runs a cached chain from its cached token and adds the lien rows',
      len(_out['2099-000101-CA-01'].get('other', [])) == 1 and _out['2099-000101-CA-01']['other_open_unpriced'] == 1)
check('--reanalyze keeps keys other steps wrote', _out['2099-000101-CA-01'].get('chain_note') == 'kept')
check('--reanalyze never lowers a lien total the old chain carried', _out['2099-000101-CA-01'].get('code_open') == 12000,
      _out['2099-000101-CA-01'].get('code_open'))
check("--reanalyze never drops a mortgage a wider earlier search found; the new rows are only listed",
      _out['2099-000100-CA-01']['liens'] == _wide['liens'] and 'other' not in _out['2099-000100-CA-01']
      and _out['2099-000100-CA-01'].get('other_seen') and _out['2099-000100-CA-01'].get('wider_repull')
      and not _out['2099-000100-CA-01'].get('repull_tried'), _out['2099-000100-CA-01'])
check('--reanalyze leaves a dead-token or untokened chain exactly as it was',
      'other' not in _out['2099-000102-CA-01'] and 'other' not in _out['2099-000103-CA-01'])
check('--reanalyze keeps the old chain when the re-read comes back empty',
      _out['2099-000104-CA-01'] == {'conf': 'ok', 'liens': [], 'chain_note': 'kept'}, _out['2099-000104-CA-01'])
check('--reanalyze stores the lead case type on the chains it rewrites',
      '2099-000101-CA-01' in _out and 'case_type' in _out['2099-000101-CA-01'])
check('--reanalyze never mints, opens a browser or pays', _spent == [], _spent)
check('--reanalyze --dry-run counts the untokened chain it will not touch',
      '1 older chain(s) have no cached token' in _dry.getvalue() and '4 lead(s) to pull' in _dry.getvalue(),
      _dry.getvalue()[-300:])

# ---- --max-spend: a hard cap on 2Captcha solves (Alex's $5 for Miami reads, 2026-09-25)
_fake_cs = types.SimpleNamespace(calls=[], bal=['10.00'])
_fake_cs.solve_turnstile = lambda *a, **k: _fake_cs.calls.append(1)
_fake_cs.balance = lambda: _fake_cs.bal[0]
_real_cs = sys.modules.get('captcha_solver')
sys.modules['captcha_solver'] = _fake_cs
try:
    RL._SPEND.update(cap=0.01, submits=0, bal0=10.0, prior=0.0, ledger=None, stopped='')
    RL.fetch_via_turnstile(('OWNERX', ''))
    RL.fetch_via_turnstile(('OWNERY', ''))
    check('--max-spend $0.01 submits three solves at $0.0033 and refuses the fourth',
          len(_fake_cs.calls) == 3 and RL._SPEND['submits'] == 3 and 'cap' in RL._SPEND['stopped'],
          (len(_fake_cs.calls), RL._SPEND))
    del _fake_cs.calls[:]
    _fake_cs.bal[0] = '8.99'
    RL._SPEND.update(cap=1.00, submits=0, bal0=10.0, prior=0.0, ledger=None, stopped='')
    for _i in range(10):
        RL.fetch_via_turnstile(('OWNERZ', ''))
    check('--max-spend stops at the 20-solve balance check when the account fell by the cap',
          len(_fake_cs.calls) == 20 and 'balance' in RL._SPEND['stopped'], (len(_fake_cs.calls), RL._SPEND))
    del _fake_cs.calls[:]
    _fake_cs.bal[0] = None
    RL._SPEND.update(cap=1.00, submits=20, bal0=10.0, prior=0.0, ledger=None, stopped='')
    RL.fetch_via_turnstile(('OWNERW', ''))
    check('--max-spend stops paying when the balance can no longer be read', _fake_cs.calls == [], RL._SPEND)
    # the real price is above the counted $0.0033: the cap learns it at the first re-read
    del _fake_cs.calls[:]
    _fake_cs.balance = lambda: '%.4f' % (10.0 - 0.0045 * len(_fake_cs.calls))
    RL._SPEND.update(cap=0.50, submits=0, bal0=10.0, prior=0.0, stopped='', unit=RL.PAID_SOLVE_USD)
    for _i in range(100):
        RL.fetch_via_turnstile(('OWNERP', ''))
    check('a solve dearer than counted: the cap learns the real price and real spend stays under it',
          0.0045 * len(_fake_cs.calls) <= 0.50 + 1e-9 and len(_fake_cs.calls) > 100, (len(_fake_cs.calls), RL._SPEND))
    _fake_cs.balance = lambda: _fake_cs.bal[0]
    del _fake_cs.calls[:]
    RL._SPEND.update(unit=RL.PAID_SOLVE_USD)
    _lu = os.path.join(_tmp, 'unit.json')
    json.dump({'cap': 5.0, 'counted_usd': 4.95, 'unit_usd': 0.005}, open(_lu, 'w'))
    _c, _p, _l = RL._ledger_open(_lu, 5.0)
    RL._SPEND.update(cap=_c, prior=_p, led=_l, submits=0, bal0=10.0, stopped='', ledger=None,
                     unit=max(RL.PAID_SOLVE_USD, float(_l.get('unit_usd') or 0)))
    for _i in range(30):
        RL.fetch_via_turnstile(('OWNERQ', ''))
    check('--spend-ledger: a later run counts at the price an earlier run learned, from its first solve',
          len(_fake_cs.calls) == 10, len(_fake_cs.calls))
    del _fake_cs.calls[:]
    _ls = os.path.join(_tmp, 'short.json')
    RL._SPEND.update(cap=0.10, prior=0.0, led=None, submits=6, unit=RL.PAID_SOLVE_USD, ledger=_ls, stopped='', lock=None)
    RL._ledger_save(charged=6 * 0.0066, final=True)
    check('--spend-ledger: a short run still records the price its balance drop showed',
          abs(json.load(open(_ls))['unit_usd'] - 0.0066) < 1e-6, json.load(open(_ls)))
    RL._SPEND.update(unit=RL.PAID_SOLVE_USD, prior=0.0, led=None, ledger=None, submits=0)
    _real_save = RL._ledger_save
    RL._ledger_save = lambda *a, **k: RL._SPEND.update(stopped='the spend ledger could not be written')
    RL._SPEND.update(cap=1.00, submits=0, bal0=10.0, prior=0.0, ledger='x', stopped='')
    RL.fetch_via_turnstile(('OWNERU', ''))
    RL._ledger_save = _real_save
    check('a ledger that cannot be written stops the solve it would have recorded', _fake_cs.calls == [], _fake_cs.calls)
    RL._SPEND.update(ledger=None)
    _lkf = os.path.join(_tmp, 'lost.json.lock')
    open(_lkf, 'w').write('someone else')
    RL._SPEND.update(cap=1.00, submits=0, bal0=10.0, prior=0.0, stopped='', lock=_lkf, lock_id='me')
    RL.fetch_via_turnstile(('OWNERT', ''))
    check('a run whose ledger lock was taken over stops paying', _fake_cs.calls == [] and 'took over' in RL._SPEND['stopped'],
          RL._SPEND)
    RL._SPEND.update(lock=None, stopped='')
    os.remove(_lkf)
    RL._SPEND.update(cap=None, submits=0, bal0=None, prior=0.0, ledger=None, stopped='')
    RL.fetch_via_turnstile(('OWNERV', ''))
    check('without --max-spend the solver behaves as before (three tries, counted)',
          len(_fake_cs.calls) == 3 and RL._SPEND['submits'] == 3)

    _led = os.path.join(_tmp, 'spend.json')
    for _bad in (['--repull'], ['--repull', '--max-spend', '1'], ['--repull', '--max-spend', '6', '--spend-ledger', _led],
                 ['--max-spend', '0'], ['--spend-ledger', _led],
                 ['--repull', '--cached-only', '--max-spend', '1', '--spend-ledger', _led]):
        sys.argv = ['records_liens.py'] + _bad
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                RL.main()
            _ok = False
        except SystemExit:
            _ok = True
        check('records_liens refuses %s' % ' '.join(_bad), _ok)

    # --repull over the same fixture: 101 was rewritten above, so 102 (dead token), 104 (empty
    # re-read, which --repull searches afresh instead of keeping) and 103 (no token) are left. Two
    # solves fit under $0.0066; the third is refused mid-way through 102, so all three are left.
    _before = json.load(open(os.path.join(_tmp, 'records_liens.json')))
    del _fake_cs.calls[:]
    _fake_cs.bal[0] = '10.00'
    _spent2 = []
    _saved = {k: getattr(RL, k) for k in ('LEADS', 'OUT', 'QS_CACHE', 'HERE', 'records_by_qs',
                                           'camoufox_session', 'mint_and_fetch', 'time')}
    try:
        RL.LEADS, RL.OUT = os.path.join(_tmp, 'leads_final.json'), os.path.join(_tmp, 'records_liens.json')
        RL.QS_CACHE, RL.HERE = os.path.join(_tmp, 'records_qs.json'), _tmp
        RL.records_by_qs = lambda qs: [deed, city1] if qs == 'tokA' else ([] if qs == 'tokEMPTY' else None)
        RL.camoufox_session = lambda: _spent2.append('camoufox') or (None, None)
        RL.mint_and_fetch = lambda *a, **k: _spent2.append('mint')
        RL.time = types.SimpleNamespace(strftime=__import__('time').strftime, sleep=lambda s: None,
                                        time=__import__('time').time)
        _rp = io.StringIO()
        sys.argv = ['records_liens.py', '--repull', '--max-spend', '0.0066', '--spend-ledger', _led]
        with contextlib.redirect_stdout(_rp):
            RL.main()
        _n1 = len(_fake_cs.calls)
        _rp2 = io.StringIO()
        sys.argv = ['records_liens.py', '--repull', '--max-spend', '5', '--spend-ledger', _led]
        with contextlib.redirect_stdout(_rp2):
            RL.main()
        _nolock = not os.path.exists(_led + '.lock')
        open(_led + '.lock', 'w').write('1 now')
        _n2 = len(_fake_cs.calls)
        sys.argv = ['records_liens.py', '--repull', '--max-spend', '1', '--spend-ledger', _led]
        try:
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                RL.main()
            _locked = False
        except SystemExit:
            _locked = True
        os.remove(_led + '.lock')
    finally:
        for k, v in _saved.items():
            setattr(RL, k, v)
        sys.argv = _argv
    _rp = _rp.getvalue()
    _after = json.load(open(os.path.join(_tmp, 'records_liens.json')))
    check('--repull submits no more solves than the cap allows', len(_fake_cs.calls) == 2, (_fake_cs.calls, _rp[-600:]))
    check('--repull opens Camoufox before paying, once per run', _spent2 == ['camoufox', 'camoufox'], _spent2)
    check('--repull names the leads the cap left unpulled', 'not pulled: spend cap' in _rp and '4 not pulled because of the cap' in _rp
          and any('2099-000100-CA-01' in l and 'not pulled: spend cap' in l for l in _rp.splitlines()) and 'reached the $0.0066 cap' in _rp, _rp[-600:])
    check('--repull reports the actual charge from the account balance', 'ACTUAL CHARGE: balance $10.0000' in _rp, _rp[-400:])
    check('--repull never overwrites a chain it could not re-read', _after == _before, (_before, _after))
    _ledj = json.load(open(_led))
    check('--spend-ledger: a finished run leaves no lock behind', _nolock)
    _bad = os.path.join(_tmp, 'bad.json')
    open(_bad, 'w').write('{not json')
    sys.argv = ['records_liens.py', '--repull', '--max-spend', '1', '--spend-ledger', _bad]
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            RL.main()
    except Exception:
        pass
    finally:
        sys.argv = _argv
    check('--spend-ledger: an unreadable ledger leaves no lock behind', not os.path.exists(_bad + '.lock'))
    _lk = os.path.join(_tmp, 'own.json')
    _got = RL._ledger_lock(_lk)
    _t0 = os.path.getmtime(_got)
    os.utime(_got, (_t0 - 3600, _t0 - 3600))
    RL._SPEND['lock'] = _got
    RL._lock_touch()
    check('--spend-ledger: a running pass keeps its lock fresh', os.path.getmtime(_got) > _t0 - 60)
    RL._SPEND['lock'] = None
    open(_got, 'w').write('999 another run')
    check("--spend-ledger: a lock another run took over is not ours to delete",
          open(_got).read() != RL._SPEND.get('lock_id'))
    os.remove(_got)
    _st = os.path.join(_tmp, 'stale.json')
    open(_st + '.lock', 'w').write('1 crashed')
    os.utime(_st + '.lock', (1, 1))
    _a1 = RL._ledger_lock(_st)
    _a2 = RL._ledger_lock(_st)
    check("--spend-ledger: a crashed run's lock is taken over by exactly one run", bool(_a1) and _a2 is None,
          (_a1, _a2))
    os.remove(_a1)
    check('--spend-ledger: a second paying run cannot start while one holds the ledger',
          _locked and len(_fake_cs.calls) == _n2)
    check('--spend-ledger: the ledger keeps the price a solve really cost', float(json.load(open(_led)).get('unit_usd', 0)) >= RL.PAID_SOLVE_USD)
    check('--spend-ledger: a second run gets only what the first left, and cannot raise the cap',
          _n1 == 2 and len(_fake_cs.calls) == 2 and _ledj['cap'] == 0.0066 and _ledj['counted_usd'] == 0.0066
          and len(_ledj['runs']) == 2 and _ledj['runs'][1]['solves'] == 0, (_n1, _fake_cs.calls, _ledj, _rp2.getvalue()[-300:]))
    # --repull on a chain first found through a defendant: the owner's token reaches only other
    # folios, so the defendants are searched too; a chain that finds nothing is marked and not paid twice
    _t2 = tempfile.mkdtemp()
    json.dump([{'Case #': '2099-000201-CA-01', 'owner_clean': 'BOB OWNERZ', 'Folio': FOLIO, 'judgment': 1,
                'defendants': 'Tester, John'},
               {'Case #': '2099-000202-CA-01', 'owner_clean': 'ANN NOTHING', 'Folio': FOLIO, 'judgment': 1},
               {'Case #': '2099-000203-CA-01', 'owner_clean': 'JOHN NEWOWNER', 'Folio': FOLIO, 'judgment': 1},
               {'Case #': '2099-000204-CA-01', 'owner_clean': 'JANE PARTIAL', 'Folio': FOLIO, 'judgment': 1},
               {'Case #': '2099-000205-CA-01', 'owner_clean': 'ZED BLOCKED', 'Folio': FOLIO, 'judgment': 1},
               {'Case #': '2099-000206-CA-01', 'owner_clean': 'BOB OWNERZ', 'Folio': FOLIO, 'judgment': 1,
                'defendants': 'Blockedsn, Carl'},
               {'Case #': '2099-000207-CA-01', 'owner_clean': 'KIM FRESH', 'Folio': FOLIO, 'judgment': 1},
               {'Case #': '2099-000208-CA-01', 'owner_clean': 'LEE WIDER', 'Folio': FOLIO, 'judgment': 1}],
              open(os.path.join(_t2, 'leads_final.json'), 'w'))
    _mtg_chain = {'conf': 'ok', 'liens': [{'d': '2/1/2008', 'amt': 350000, 'st': 'OPEN', 'bp': '26100/11'}],
                  'searched_as': 'JANE DOE (defendant)'}
    json.dump({'2099-000201-CA-01': {'conf': 'ok', 'liens': []}, '2099-000202-CA-01': {'conf': 'ok', 'liens': []},
               '2099-000203-CA-01': _mtg_chain, '2099-000204-CA-01': _mtg_chain,
               '2099-000205-CA-01': {'conf': 'ok', 'liens': []}, '2099-000206-CA-01': {'conf': 'ok', 'liens': []},
               '2099-000207-CA-01': _mtg_chain,
               '2099-000208-CA-01': {'conf': 'ok', 'liens': [], 'other_seen': [{'doc': 'LIEN'}],
                                     'wider_repull': 'an earlier narrower re-read'}},
              open(os.path.join(_t2, 'records_liens.json'), 'w'))
    json.dump({'BOB OWNERZ': 'tokOTHER', 'JOHN NEWOWNER': 'tokDEEDONLY', 'JANE PARTIAL': 'tokSATONLY'},
              open(os.path.join(_t2, 'records_qs.json'), 'w'))
    _oldm = rec('MORTGAGE', '1/1/2001', '19000', '1', 90000, 'SOME LENDER', first='OWNER TESTER')
    _oldsat = rec('SATISFACTION', '1/1/2005', '23000', '1', 0, 'OWNER TESTER', first='SOME LENDER',
                  oriG_REC_BOOK='19000', oriG_REC_PAGE='1')
    open(os.path.join(_t2, 'gen_records_qs.py'), 'w').write('')
    _far = rec('DEED', '1/1/2010', '20000', '1', folio='3099999999999', subdiV_NAME='ELSEWHERE')
    _asked = []
    def _ft(sp, tries=3):
        _asked.append(sp)
        # the owner's fresh search comes back too, just not on this parcel
        # ANN NOTHING's search is answered with no records; ZED BLOCKED's and CARL BLOCKEDSN's never are
        return ([deed, city1] if sp == ('TESTER', 'JOHN') else [_far] if sp == ('OWNERZ', 'BOB')
                else [] if sp == ('NOTHING', 'ANN') else [deed, city1] if sp in (('FRESH', 'KIM'), ('WIDER', 'LEE'))
                else [deed] if sp == ('PARTIAL', 'JANE') else [deed, mtg, city1] if sp == ('NEWOWNER', 'JOHN')
                else None)
    _saved = {k: getattr(RL, k) for k in ('LEADS', 'OUT', 'QS_CACHE', 'HERE', 'records_by_qs', 'fetch_via_turnstile',
                                           'camoufox_session', 'mint_and_fetch', 'time')}
    try:
        RL.LEADS, RL.OUT = os.path.join(_t2, 'leads_final.json'), os.path.join(_t2, 'records_liens.json')
        RL.QS_CACHE, RL.HERE = os.path.join(_t2, 'records_qs.json'), _t2
        RL.records_by_qs = lambda qs: ([_far] if qs == 'tokOTHER' else [deed] if qs == 'tokDEEDONLY'
                                       else [deed, _oldm, _oldsat] if qs == 'tokSATONLY' else None)
        RL.fetch_via_turnstile = _ft
        RL.camoufox_session = lambda: (None, None)
        RL.mint_and_fetch = lambda *a, **k: None
        RL.time = types.SimpleNamespace(strftime=__import__('time').strftime, sleep=lambda s: None,
                                        time=__import__('time').time)
        _l2 = os.path.join(_t2, 'spend.json')
        for _ in range(2):
            sys.argv = ['records_liens.py', '--repull', '--max-spend', '1', '--spend-ledger', _l2]
            with contextlib.redirect_stdout(io.StringIO()):
                RL.main()
    finally:
        for k, v in _saved.items():
            setattr(RL, k, v)
        sys.argv = _argv
    _o2 = json.load(open(os.path.join(_t2, 'records_liens.json')))
    check('--repull searches the defendant when the owner name misses the parcel',
          ('TESTER', 'JOHN') in _asked and 'other' in _o2['2099-000201-CA-01']
          and _o2['2099-000201-CA-01'].get('searched_as') == 'JOHN TESTER (defendant)', (_asked, _o2['2099-000201-CA-01']))
    check("--repull never drops an open mortgage the fresh search does not reach, even beside a satisfied one",
          _o2['2099-000204-CA-01']['liens'] == _mtg_chain['liens'], _o2['2099-000204-CA-01'])
    check("--repull: a narrower cached re-read sends the chain to the paid surname search next run, and a wider "
          "answer there replaces the flag with counted rows",
          [l.get('bp') for l in _o2['2099-000203-CA-01']['liens']] == ['26100/11'] and 'other' in _o2['2099-000203-CA-01']
          and 'wider_repull' not in _o2['2099-000203-CA-01'] and _asked.count(('NEWOWNER', 'JOHN')) == 1,
          (_asked, _o2['2099-000203-CA-01']))
    check("--repull: a paid surname search that is still narrower keeps the chain whole, flagged, and is paid once",
          _o2['2099-000204-CA-01']['liens'] == _mtg_chain['liens'] and 'other' not in _o2['2099-000204-CA-01']
          and _o2['2099-000204-CA-01'].get('wider_repull') and _o2['2099-000204-CA-01'].get('repull_tried')
          and _asked.count(('PARTIAL', 'JANE')) == 1, (_asked, _o2['2099-000204-CA-01']))
    check("--repull: a fresh search that comes back narrower keeps the chain whole, flags it, and is never paid twice",
          _o2['2099-000207-CA-01']['liens'] == _mtg_chain['liens'] and 'other' not in _o2['2099-000207-CA-01']
          and _o2['2099-000207-CA-01'].get('wider_repull') and _o2['2099-000207-CA-01'].get('repull_tried')
          and _asked.count(('FRESH', 'KIM')) == 1, (_asked, _o2['2099-000207-CA-01']))
    check("--repull: a wider re-read answers an earlier narrower one's flag and listed rows",
          'other' in _o2['2099-000208-CA-01'] and 'other_seen' not in _o2['2099-000208-CA-01']
          and 'wider_repull' not in _o2['2099-000208-CA-01'], _o2['2099-000208-CA-01'])
    check('--repull marks a chain it paid to search and found nothing for, and never pays for it again',
          _o2['2099-000202-CA-01'].get('repull_tried') and _asked.count(('NOTHING', 'ANN')) == 1, (_asked, _o2))
    check('--repull never marks a chain whose search the clerk never answered: the next run tries again',
          not _o2['2099-000205-CA-01'].get('repull_tried') and _asked.count(('BLOCKED', 'ZED')) == 2, (_asked, _o2))
    check("--repull never marks a chain whose defendant's search the clerk never answered",
          not _o2['2099-000206-CA-01'].get('repull_tried') and _asked.count(('BLOCKEDSN', 'CARL')) == 2,
          (_asked, _o2['2099-000206-CA-01']))
    # one surname, one query: the clerk searches the SURNAME, so a spouse or the owner's own longer
    # name is the same search; and a cap that stops the defendant search leaves the chain unmarked
    _t3 = tempfile.mkdtemp()
    json.dump([{'Case #': '2099-000301-CA-01', 'owner_clean': 'MARIA ELENA GARCIA', 'Folio': FOLIO, 'judgment': 1,
                'defendants': 'Garcia, Maria Elena; Garcia, Jose'},
               {'Case #': '2099-000302-CA-01', 'owner_clean': 'BOB OWNERZ', 'Folio': FOLIO, 'judgment': 1,
                'defendants': 'Perez, Juan'}],
              open(os.path.join(_t3, 'leads_final.json'), 'w'))
    json.dump({'2099-000301-CA-01': {'conf': 'ok', 'liens': []}, '2099-000302-CA-01': {'conf': 'ok', 'liens': []}},
              open(os.path.join(_t3, 'records_liens.json'), 'w'))
    json.dump({}, open(os.path.join(_t3, 'records_qs.json'), 'w'))
    open(os.path.join(_t3, 'gen_records_qs.py'), 'w').write('')
    _asked3 = []
    def _ft3(sp, tries=3):
        if not RL._may_submit():
            return None
        RL._SPEND['submits'] += 1
        _asked3.append(sp)
        return [_far]
    _saved = {k: getattr(RL, k) for k in ('LEADS', 'OUT', 'QS_CACHE', 'HERE', 'records_by_qs', 'fetch_via_turnstile',
                                           'camoufox_session', 'mint_and_fetch', 'time')}
    _out3 = io.StringIO()
    try:
        RL.LEADS, RL.OUT = os.path.join(_t3, 'leads_final.json'), os.path.join(_t3, 'records_liens.json')
        RL.QS_CACHE, RL.HERE = os.path.join(_t3, 'records_qs.json'), _t3
        RL.records_by_qs = lambda qs: None
        RL.fetch_via_turnstile = _ft3
        RL.camoufox_session = lambda: (None, None)
        RL.mint_and_fetch = lambda *a, **k: None
        RL.time = types.SimpleNamespace(strftime=__import__('time').strftime, sleep=lambda s: None,
                                        time=__import__('time').time)
        sys.argv = ['records_liens.py', '--repull', '--max-spend', '0.0066', '--spend-ledger', os.path.join(_t3, 's.json')]
        with contextlib.redirect_stdout(_out3):
            RL.main()
    finally:
        for k, v in _saved.items():
            setattr(RL, k, v)
        sys.argv = _argv
    _o3 = json.load(open(os.path.join(_t3, 'records_liens.json')))
    check('--repull asks the clerk for one surname once: the owner and a spouse named GARCIA are one query',
          [x[0] for x in _asked3].count('GARCIA') == 1, _asked3)
    check('--repull never marks a chain whose defendant search the cap stopped',
          not _o3['2099-000302-CA-01'].get('repull_tried') and 'not fully searched: spend cap' in _out3.getvalue(),
          (_asked3, _o3, _out3.getvalue()[-400:]))
    # Camoufox fills the FIRST name too, so a spouse with the owner's surname is a different free search
    _t4 = tempfile.mkdtemp()
    json.dump([{'Case #': '2099-000401-CA-01', 'owner_clean': 'JOHN PEREZ', 'Folio': FOLIO, 'judgment': 1,
                'defendants': 'Perez, John; Perez, Maria'}], open(os.path.join(_t4, 'leads_final.json'), 'w'))
    json.dump({'2099-000401-CA-01': {'conf': 'ok', 'liens': []}}, open(os.path.join(_t4, 'records_liens.json'), 'w'))
    json.dump({}, open(os.path.join(_t4, 'records_qs.json'), 'w'))
    open(os.path.join(_t4, 'gen_records_qs.py'), 'w').write('')
    _cfq, _paid4 = [], []
    def _cf4(browser, sp):
        _cfq.append(sp)
        return {('PEREZ', 'JOHN'): 'tokJ', ('PEREZ', 'MARIA'): 'tokM'}.get(tuple(sp))
    _saved = {k: getattr(RL, k) for k in ('LEADS', 'OUT', 'QS_CACHE', 'HERE', 'records_by_qs', 'fetch_via_turnstile',
                                           'camoufox_session', 'camoufox_qs', 'mint_and_fetch', 'time')}
    try:
        RL.LEADS, RL.OUT = os.path.join(_t4, 'leads_final.json'), os.path.join(_t4, 'records_liens.json')
        RL.QS_CACHE, RL.HERE = os.path.join(_t4, 'records_qs.json'), _t4
        RL.records_by_qs = lambda qs: {'tokJ': [_far], 'tokM': [deed, city1]}.get(qs)
        RL.fetch_via_turnstile = lambda sp, tries=3: _paid4.append(sp)
        RL.camoufox_session = lambda: (contextlib.nullcontext(), object())
        RL.camoufox_qs = _cf4
        RL.mint_and_fetch = lambda *a, **k: None
        RL.time = types.SimpleNamespace(strftime=__import__('time').strftime, sleep=lambda s: None,
                                        time=__import__('time').time)
        sys.argv = ['records_liens.py', '--repull', '--max-spend', '0.0066', '--spend-ledger', os.path.join(_t4, 's.json')]
        with contextlib.redirect_stdout(io.StringIO()):
            RL.main()
    finally:
        for k, v in _saved.items():
            setattr(RL, k, v)
        sys.argv = _argv
    _o4 = json.load(open(os.path.join(_t4, 'records_liens.json')))['2099-000401-CA-01']
    check("--repull asks free Camoufox for a spouse with the owner's surname: first names differ there",
          ('PEREZ', 'MARIA') in [tuple(x) for x in _cfq] and _o4.get('searched_as') == 'MARIA PEREZ (defendant)'
          and not _paid4, (_cfq, _paid4, _o4))
    # a free Camoufox search (first AND last name) that comes back narrower only flags the chain; the
    # next --repull skips Camoufox and pays for the surname search
    _t5 = tempfile.mkdtemp()
    json.dump([{'Case #': '2099-000501-CA-01', 'owner_clean': 'AMY NARROW', 'Folio': FOLIO, 'judgment': 1}],
              open(os.path.join(_t5, 'leads_final.json'), 'w'))
    json.dump({'2099-000501-CA-01': dict(_mtg_chain, searched_as='NARROW (surname)')},
              open(os.path.join(_t5, 'records_liens.json'), 'w'))
    json.dump({}, open(os.path.join(_t5, 'records_qs.json'), 'w'))
    open(os.path.join(_t5, 'gen_records_qs.py'), 'w').write('')
    _cf5, _paid5, _o5 = [], [], []
    _saved = {k: getattr(RL, k) for k in ('LEADS', 'OUT', 'QS_CACHE', 'HERE', 'records_by_qs', 'fetch_via_turnstile',
                                           'camoufox_session', 'camoufox_qs', 'mint_and_fetch', 'time')}
    try:
        RL.LEADS, RL.OUT = os.path.join(_t5, 'leads_final.json'), os.path.join(_t5, 'records_liens.json')
        RL.QS_CACHE, RL.HERE = os.path.join(_t5, 'records_qs.json'), _t5
        RL.records_by_qs = lambda qs: [deed, city1] if qs == 'tokA' else None
        RL.fetch_via_turnstile = lambda sp, tries=3: _paid5.append(tuple(sp)) or [deed, mtg, city1]
        RL.camoufox_session = lambda: (contextlib.nullcontext(), object())
        RL.camoufox_qs = lambda browser, sp: _cf5.append(tuple(sp)) or 'tokA'
        RL.mint_and_fetch = lambda *a, **k: None
        RL.time = types.SimpleNamespace(strftime=__import__('time').strftime, sleep=lambda s: None,
                                        time=__import__('time').time)
        for _ in range(2):
            json.dump({}, open(os.path.join(_t5, 'records_qs.json'), 'w'))    # the free token is not the point here
            sys.argv = ['records_liens.py', '--repull', '--max-spend', '0.0066', '--spend-ledger', os.path.join(_t5, 's.json')]
            with contextlib.redirect_stdout(io.StringIO()):
                RL.main()
            _o5.append(json.load(open(os.path.join(_t5, 'records_liens.json')))['2099-000501-CA-01'])
    finally:
        for k, v in _saved.items():
            setattr(RL, k, v)
        sys.argv = _argv
    check("--repull: a narrower free Camoufox search flags the chain and never marks it as re-searched",
          _o5[0].get('wider_repull') and not _o5[0].get('repull_tried') and 'other' not in _o5[0], _o5[0])
    check("--repull: the next run skips Camoufox for it and pays for the surname search once",
          _cf5 == [('NARROW', 'AMY')] and _paid5 == [('NARROW', 'AMY')] and 'other' in _o5[1]
          and 'wider_repull' not in _o5[1], (_cf5, _paid5, _o5[1]))
    # a free Camoufox search that misses the parcel flags the chain; only the paid surname search marks it
    _t6 = tempfile.mkdtemp()
    json.dump([{'Case #': '2099-000601-CA-01', 'owner_clean': 'BEA MISSED', 'Folio': FOLIO, 'judgment': 1}],
              open(os.path.join(_t6, 'leads_final.json'), 'w'))
    json.dump({'2099-000601-CA-01': {'conf': 'ok', 'liens': []}}, open(os.path.join(_t6, 'records_liens.json'), 'w'))
    open(os.path.join(_t6, 'gen_records_qs.py'), 'w').write('')
    _cf6, _paid6, _o6 = [], [], []
    _saved = {k: getattr(RL, k) for k in ('LEADS', 'OUT', 'QS_CACHE', 'HERE', 'records_by_qs', 'fetch_via_turnstile',
                                           'camoufox_session', 'camoufox_qs', 'mint_and_fetch', 'time')}
    try:
        RL.LEADS, RL.OUT = os.path.join(_t6, 'leads_final.json'), os.path.join(_t6, 'records_liens.json')
        RL.QS_CACHE, RL.HERE = os.path.join(_t6, 'records_qs.json'), _t6
        RL.records_by_qs = lambda qs: [_far] if qs == 'tokF' else None
        RL.fetch_via_turnstile = lambda sp, tries=3: _paid6.append(tuple(sp)) or [_far]
        RL.camoufox_session = lambda: (contextlib.nullcontext(), object())
        RL.camoufox_qs = lambda browser, sp: _cf6.append(tuple(sp)) or 'tokF'
        RL.mint_and_fetch = lambda *a, **k: None
        RL.time = types.SimpleNamespace(strftime=__import__('time').strftime, sleep=lambda s: None,
                                        time=__import__('time').time)
        for _ in range(3):
            json.dump({}, open(os.path.join(_t6, 'records_qs.json'), 'w'))
            sys.argv = ['records_liens.py', '--repull', '--max-spend', '0.0066', '--spend-ledger', os.path.join(_t6, 's.json')]
            with contextlib.redirect_stdout(io.StringIO()):
                RL.main()
            _o6.append(json.load(open(os.path.join(_t6, 'records_liens.json')))['2099-000601-CA-01'])
    finally:
        for k, v in _saved.items():
            setattr(RL, k, v)
        sys.argv = _argv
    check("--repull: a free search that misses the parcel flags the chain for the paid search, never marks it",
          _o6[0].get('wider_repull') and not _o6[0].get('repull_tried'), _o6[0])
    check("--repull: the paid surname search that also misses it marks it, and it is never paid for again",
          _o6[1].get('repull_tried') and _cf6 == [('MISSED', 'BEA')] and _paid6 == [('MISSED', 'BEA')], (_cf6, _paid6, _o6))
    # a defendant only Camoufox answered, or one the clerk never answered, is not a paid answer for every name
    _t7 = tempfile.mkdtemp()
    json.dump([{'Case #': '2099-000701-CA-01', 'owner_clean': 'ZOE MISSED', 'Folio': FOLIO, 'judgment': 1,
                'defendants': 'Doe, Jane'},
               {'Case #': '2099-000702-CA-01', 'owner_clean': 'YAN OTHERZ', 'Folio': FOLIO, 'judgment': 1,
                'defendants': 'Doetwo, Jane; Roe, Rick'},
               {'Case #': '2099-000703-CA-01', 'owner_clean': 'UMA FREEMISS', 'Folio': FOLIO, 'judgment': 1,
                'defendants': 'Roe, Rick'},
               {'Case #': '2099-000704-CA-01', 'owner_clean': 'ANN OWNERX', 'Folio': FOLIO, 'judgment': 1,
                'defendants': 'Roe, Rick'}], open(os.path.join(_t7, 'leads_final.json'), 'w'))
    json.dump({'2099-000701-CA-01': {'conf': 'ok', 'liens': [], 'searched_as': 'JANE DOE (defendant)'},
               '2099-000702-CA-01': dict(_mtg_chain, wider_repull='flagged earlier'), '2099-000703-CA-01': _mtg_chain,
               '2099-000704-CA-01': dict(_mtg_chain, wider_repull='flagged earlier')},
              open(os.path.join(_t7, 'records_liens.json'), 'w'))
    open(os.path.join(_t7, 'gen_records_qs.py'), 'w').write('')
    _cf7, _paid7, _o7 = [], [], []
    def _ft7(sp, tries=3):
        _paid7.append(tuple(sp))
        return {('DOETWO', 'JANE'): None, ('OWNERX', 'ANN'): None, ('ROE', 'RICK'): [deed, city1]}.get(tuple(sp), [_far])
    _saved = {k: getattr(RL, k) for k in ('LEADS', 'OUT', 'QS_CACHE', 'HERE', 'records_by_qs', 'fetch_via_turnstile',
                                           'camoufox_session', 'camoufox_qs', 'mint_and_fetch', 'time')}
    try:
        RL.LEADS, RL.OUT = os.path.join(_t7, 'leads_final.json'), os.path.join(_t7, 'records_liens.json')
        RL.QS_CACHE, RL.HERE = os.path.join(_t7, 'records_qs.json'), _t7
        RL.records_by_qs = lambda qs: [_far] if qs == 'tokD' else None
        RL.fetch_via_turnstile = _ft7
        RL.camoufox_session = lambda: (contextlib.nullcontext(), object())
        RL.camoufox_qs = lambda browser, sp: _cf7.append(tuple(sp)) or ('tokD' if tuple(sp) in (('DOE', 'JANE'), ('FREEMISS', 'UMA')) else None)
        RL.mint_and_fetch = lambda *a, **k: None
        RL.time = types.SimpleNamespace(strftime=__import__('time').strftime, sleep=lambda s: None,
                                        time=__import__('time').time)
        for _ in range(2):
            json.dump({}, open(os.path.join(_t7, 'records_qs.json'), 'w'))
            sys.argv = ['records_liens.py', '--repull', '--max-spend', '0.05', '--spend-ledger', os.path.join(_t7, 's.json')]
            with contextlib.redirect_stdout(io.StringIO()):
                RL.main()
            _o7.append(json.load(open(os.path.join(_t7, 'records_liens.json'))))
    finally:
        for k, v in _saved.items():
            setattr(RL, k, v)
        sys.argv = _argv
    _a7, _b7 = _o7[0]['2099-000701-CA-01'], _o7[0]['2099-000702-CA-01']
    check("--repull: the owner's paid miss does not mark a chain whose defendant only Camoufox answered",
          not _a7.get('repull_tried') and _a7.get('wider_repull') and _paid7[:4] == [('MISSED', 'ZOE'), ('OTHERZ', 'YAN'), ('DOETWO', 'JANE'), ('ROE', 'RICK')], (_paid7, _a7))
    check("--repull: the next run pays for that defendant's surname, and only then marks the chain",
          _o7[1]['2099-000701-CA-01'].get('repull_tried') and _paid7.count(('DOE', 'JANE')) == 1, (_paid7, _o7[1]))
    check("--repull: a narrower answer beside a defendant search the clerk never answered marks nothing",
          not _b7.get('repull_tried') and _b7.get('wider_repull') and _b7['liens'] == _mtg_chain['liens']
          and ('DOETWO', 'JANE') in _paid7, (_paid7, _b7))
    check("--repull: a narrower paid answer for a defendant marks nothing while the owner was only asked for free",
          not _o7[0]['2099-000703-CA-01'].get('repull_tried') and _o7[0]['2099-000703-CA-01'].get('wider_repull')
          and _o7[1]['2099-000703-CA-01'].get('repull_tried') and _paid7.count(('FREEMISS', 'UMA')) == 1, (_paid7, _o7))
    check("--repull: an owner's paid search the clerk never answered marks nothing, whatever a defendant's found",
          not any(_o['2099-000704-CA-01'].get('repull_tried') for _o in _o7)
          and _o7[1]['2099-000704-CA-01'].get('wider_repull') and _paid7.count(('OWNERX', 'ANN')) == 2, (_paid7, _o7))
finally:
    if _real_cs is not None:
        sys.modules['captcha_solver'] = _real_cs
    else:
        sys.modules.pop('captcha_solver', None)
    RL._SPEND.update(cap=None, submits=0, bal0=None, prior=0.0, ledger=None, stopped='')

print('\nOK: 0 failure(s)' if not FAILS else '\nFAIL: %d failure(s)' % len(FAILS))
sys.exit(1 if FAILS else 0)
