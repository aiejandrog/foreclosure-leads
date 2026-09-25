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
check("a person-against-person judgment is never summed as the owner's debt (it may be one they won)",
      _won['code_open'] == 0 and _won['other_open_unpriced'] == 1 and _won['other'][0].get('direction_unknown'), _won['other'])
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
check('a City notice, a clerk certificate and a lien waiver are shown, never counted as debt',
      _nt['code_open'] == 0 and _nt['other_open_unpriced'] == 0 and ES.state_of(_nt) == 'clear', _nt['other'])
_cb = RL.analyze([deed, rec('JUDGMENT', '4/4/2021', '33000', '26', 20000, 'TESTER OWNER', first='COMMUNITY BANK OF FLORIDA',
                            folio='', subdiV_NAME='')], FOLIO, 12000, ftype='HOA', owner='OWNER TESTER')
check("COMMUNITY BANK's money judgment follows the owner like any other", _cb['code_open'] == 20000, _cb['other'])
for _p, _w in (('GARCIA LOPEZ MARIA', 'MARIA GARCIA-LOPEZ'), ("O'BRIEN KATHLEEN", 'KATHLEEN OBRIEN'),
               ('OBRIEN KATHLEEN', "KATHLEEN O'BRIEN")):
    check('%r names the owner %r' % (_p, _w), RL._names_owner(_p, [RL._owner_words(_w)]))
check("an association suing in circuit court is an association's case: the first mortgage survives",
      RL._fc_type('2024-000009-CA-01', 'HOA/Condo') == 'HOA' and RL._fc_type('2024-000009-CA-01', 'Bank/Mortgage') == 'MORTGAGE')
_hca = CD._b(dict(res, ftype='MORTGAGE', case_type='HOA/Condo', judgment=60000))['foreclosed_debt']
check("dossier b: a circuit association case never names the first mortgage as the foreclosed debt",
      _hca['recorded_face'] is None and _hca['instrument'] is None and 'survives' in (_hca.get('note') or ''), _hca)

# ---- $0 re-analysis of chains traced before the lien rows existed
import json, tempfile, types
_tmp = tempfile.mkdtemp()
_leads = [{'Case #': '2099-000101-CA-01', 'owner_clean': 'OWNER A', 'Folio': FOLIO, 'judgment': 1, 'plaintiff': PLAINTIFF},
          {'Case #': '2099-000102-CA-01', 'owner_clean': 'JOHN QUINCY TESTER', 'Folio': FOLIO, 'judgment': 1},
          {'Case #': '2099-000103-CA-01', 'owner_clean': 'OWNER C', 'Folio': FOLIO, 'judgment': 1},
          {'Case #': '2099-000104-CA-01', 'owner_clean': 'OWNER D', 'Folio': FOLIO, 'judgment': 1}]
json.dump(_leads, open(os.path.join(_tmp, 'leads_final.json'), 'w'))
json.dump({c['Case #']: {'conf': 'ok', 'liens': [], 'chain_note': 'kept'} for c in _leads},
          open(os.path.join(_tmp, 'records_liens.json'), 'w'))
json.dump({'OWNER A': 'tokA', 'JOHN QUINCY TESTER': 'tokDEAD', 'OWNER D': 'tokEMPTY'}, open(os.path.join(_tmp, 'records_qs.json'), 'w'))
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
check('--reanalyze leaves a dead-token or untokened chain exactly as it was',
      'other' not in _out['2099-000102-CA-01'] and 'other' not in _out['2099-000103-CA-01'])
check('--reanalyze keeps the old chain when the re-read comes back empty',
      _out['2099-000104-CA-01'] == {'conf': 'ok', 'liens': [], 'chain_note': 'kept'}, _out['2099-000104-CA-01'])
check('--reanalyze stores the lead case type on the chains it rewrites',
      '2099-000101-CA-01' in _out and 'case_type' in _out['2099-000101-CA-01'])
check('--reanalyze never mints, opens a browser or pays', _spent == [], _spent)
check('--reanalyze --dry-run counts the untokened chain it will not touch',
      '1 older chain(s) have no cached token' in _dry.getvalue() and '3 lead(s) to pull' in _dry.getvalue(),
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
    check('--repull names the leads the cap left unpulled', 'not pulled: spend cap' in _rp and '3 not pulled because of the cap' in _rp and 'reached the $0.0066 cap' in _rp, _rp[-600:])
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
    check('--spend-ledger: a second paying run cannot start while one holds the ledger',
          _locked and len(_fake_cs.calls) == _n2)
    check('--spend-ledger: a second run gets only what the first left, and cannot raise the cap',
          _n1 == 2 and len(_fake_cs.calls) == 2 and _ledj['cap'] == 0.0066 and _ledj['counted_usd'] == 0.0066
          and len(_ledj['runs']) == 2 and _ledj['runs'][1]['solves'] == 0, (_n1, _fake_cs.calls, _ledj, _rp2.getvalue()[-300:]))
    # --repull on a chain first found through a defendant: the owner's token reaches only other
    # folios, so the defendants are searched too; a chain that finds nothing is marked and not paid twice
    _t2 = tempfile.mkdtemp()
    json.dump([{'Case #': '2099-000201-CA-01', 'owner_clean': 'BOB OWNERZ', 'Folio': FOLIO, 'judgment': 1,
                'defendants': 'Tester, John'},
               {'Case #': '2099-000202-CA-01', 'owner_clean': 'ANN NOTHING', 'Folio': FOLIO, 'judgment': 1}],
              open(os.path.join(_t2, 'leads_final.json'), 'w'))
    json.dump({'2099-000201-CA-01': {'conf': 'ok', 'liens': []}, '2099-000202-CA-01': {'conf': 'ok', 'liens': []}},
              open(os.path.join(_t2, 'records_liens.json'), 'w'))
    json.dump({'BOB OWNERZ': 'tokOTHER'}, open(os.path.join(_t2, 'records_qs.json'), 'w'))
    open(os.path.join(_t2, 'gen_records_qs.py'), 'w').write('')
    _far = rec('DEED', '1/1/2010', '20000', '1', folio='3099999999999', subdiV_NAME='ELSEWHERE')
    _asked = []
    def _ft(sp, tries=3):
        _asked.append(sp)
        # the owner's fresh search comes back too, just not on this parcel
        return [deed, city1] if sp == ('TESTER', 'JOHN') else ([_far] if sp == ('OWNERZ', 'BOB') else None)
    _saved = {k: getattr(RL, k) for k in ('LEADS', 'OUT', 'QS_CACHE', 'HERE', 'records_by_qs', 'fetch_via_turnstile',
                                           'camoufox_session', 'mint_and_fetch', 'time')}
    try:
        RL.LEADS, RL.OUT = os.path.join(_t2, 'leads_final.json'), os.path.join(_t2, 'records_liens.json')
        RL.QS_CACHE, RL.HERE = os.path.join(_t2, 'records_qs.json'), _t2
        RL.records_by_qs = lambda qs: [_far] if qs == 'tokOTHER' else None
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
    check('--repull marks a chain it paid to search and found nothing for, and never pays for it again',
          _o2['2099-000202-CA-01'].get('repull_tried') and _asked.count(('NOTHING', 'ANN')) == 1, (_asked, _o2))
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
finally:
    if _real_cs is not None:
        sys.modules['captcha_solver'] = _real_cs
    else:
        sys.modules.pop('captcha_solver', None)
    RL._SPEND.update(cap=None, submits=0, bal0=None, prior=0.0, ledger=None, stopped='')

print('\nOK: 0 failure(s)' if not FAILS else '\nFAIL: %d failure(s)' % len(FAILS))
sys.exit(1 if FAILS else 0)
