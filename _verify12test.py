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
mtg = rec('MORTGAGE', '2/1/2008', '26100', '11', 0, PLAINTIFF, intangible=834)       # $417,000 face
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
own_fj_vacated = rec('JUDGMENT', '5/5/2025', '34932', '1256', 1022358, 'OWNER TESTER', first=PLAINTIFF)
elsewhere = rec('LIEN', '2/2/2020', '31700', '1', 900, 'OWNER TESTER', first='CITY OF HOMESTEAD',
                folio='3099999999999', subdiV_NAME='ELSEWHERE')

models = [deed, mtg, city1, city2, city3, city_rel, wasd, wasd_rel, assn_lp, warrant, own_lp,
          own_fj_vacated, elsewhere]
res = RL.analyze(models, FOLIO, 1022358.91, ftype='MORTGAGE', plaintiff=PLAINTIFF)
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
      other['34932/1256'].get('own_case') is True and res['code_open'] == 1500)
_noplaintiff = RL.analyze(models, FOLIO, 1022358.91, ftype='MORTGAGE')
check('without the plaintiff, a lender judgment is still a money judgment (unchanged behaviour)',
      _noplaintiff['code_open'] == 1500 + 1022358, _noplaintiff['code_open'])

# ---- defect 2: the debt is the judgment
check('the chain keeps the recorded face apart from the judgment',
      res['first_face'] == 417000 and res['judgment'] == 1022358.91 and res['first_bp'] == '26100/11',
      (res['first_face'], res['judgment'], res['first_bp']))
b = CD._b(res)
check('dossier b prices the foreclosed debt at the judgment, not the face',
      b['foreclosed_debt']['amount'] == 1022358.91 and b['foreclosed_debt']['recorded_face'] == 417000,
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
check('the ceiling counts those liens', _lead.get('eqopen', 0) >= 2, _lead)

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
lender_empty = RL.analyze([deed], FOLIO, 358247.93, ftype='MORTGAGE', plaintiff=PLAINTIFF)
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
_bank_ca_old = dict(empty, ftype='MORTGAGE', other=[{'own_case': True, 'kind': 'lis_pendens',
                                                      'party': 'SYNTHETIC BANK NATIONAL ASSOCIATION'}])
check("dossier d: a bank's 'National Association' is still a lender",
      CD._d(_bank_ca_old, {'status': 'empty'})['eqstate'] == 'none')
_hoa_b = CD._b(dict(empty, judgment=12000))
check("dossier b: an association case shows its judgment as the foreclosed debt, with no mortgage face",
      (_hoa_b['foreclosed_debt'] or {}).get('amount') == 12000
      and (_hoa_b['foreclosed_debt'] or {}).get('recorded_face') is None,
      _hoa_b['foreclosed_debt'])
_bw = {'conf': 'ok', 'liens': [], 'nrec': 3}
check('dossier b: a chain with no judgment key takes it from the lead',
      (CD._b(_bw, {'judgment': '$88,500.10'})['foreclosed_debt'] or {}).get('amount') == 88500.10)

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

print('\nOK: 0 failure(s)' if not FAILS else '\nFAIL: %d failure(s)' % len(FAILS))
sys.exit(1 if FAILS else 0)
