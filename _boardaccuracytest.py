"""_boardaccuracytest -- the three defects the 2026-09-23 Miami accuracy audit found on the board.

Run:  python _boardaccuracytest.py    (exit 0 = safe; no network, no browser, no real data)

  1. SALE DAY. The nightly lands ~05:30 and the counties sell from 9am, so every sale-day case
     shipped as "0 days, URGENT" and stayed in the call lanes, the worker and the text batch all
     day. On 09-23 two of those had sold and one had been cancelled for bankruptcy by mid-morning.
     Asserted on the code that ships: _heldToday/_saleDays/_clockTxt are cut out of
     tracker_template.html and liveDays/_bizDays out of call_mode.py's page, then run under node
     with the clock frozen either side of the sale hour.
  2. LAST SALE. enrich() took the appraiser's SalesInfos[0]; the list is not always newest first.
  3. DUPLICATE MORTGAGES. The clerk's name search can return one instrument per index entry, so a
     Miami-Dade chain could hold the same open mortgage twice and count it as a surviving junior.
     (The one-instrument rule itself is PR #50's, ported verbatim; this checks the cache side.)
  4. A LENDER'S SEPARATE FORECLOSURE. An association case read VERIFIED CLEAR while a bank had its
     own foreclosure filed on the same unit (Salkey). The chain now looks for it, and the board
     re-settles the label after orsecond / sib are attached.
  5. NO MORTGAGE FOUND IS NOT VERIFIED CLEAR. An empty chain reads CLEAR only when it records how
     it searched (records returned, not capped, lender-foreclosure check asked). A mortgage with no
     published amount is a ceiling, never zero debt. Cached clears without that record revalidate.
  6. ELHARRAR REFRESHED. A cached four-copy $790,000 chain re-pulls to the county's $395,000.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import sale_pick
import records_liens as RL
import call_mode as CM

FAILS = []


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + (('  -- ' + detail) if detail and not cond else ''))
    if not cond:
        FAILS.append(name)


# ---------------------------------------------------------------- 2. last sale
S = [{'DateOfSale': '3/14/2006', 'SalePrice': 210000},
     {'DateOfSale': '11/2/2019', 'SalePrice': 455000},
     {'DateOfSale': '1/10/2012', 'SalePrice': 300000}]
check('last sale: newest by date, not list position', sale_pick.newest_sale(S)['SalePrice'] == 455000)
check('last sale: 1/10 vs 10/31 compared as dates',
      sale_pick.newest_sale([{'DateOfSale': '1/10/2006'}, {'DateOfSale': '10/31/2006'}])['DateOfSale'] == '10/31/2006')
check('last sale: ISO with a time part parses',
      sale_pick.newest_sale([{'DateOfSale': '2004-03-23T00:00:00'}, {'DateOfSale': '2021-07-01T00:00:00'}])['DateOfSale'].startswith('2021'))
check('last sale: an unreadable date never outranks a real one',
      sale_pick.newest_sale([{'DateOfSale': 'n/a'}, {'DateOfSale': '5/5/2015'}])['DateOfSale'] == '5/5/2015')
check('last sale: nothing readable -> first entry, same as before',
      sale_pick.newest_sale([{'DateOfSale': ''}, {'DateOfSale': 'x'}]) == {'DateOfSale': ''})
check('last sale: empty -> {}', sale_pick.newest_sale([]) == {} and sale_pick.newest_sale(None) == {})
_fl = open(os.path.join(HERE, 'foreclosure_leads.py'), encoding='utf-8').read()
check('enrich() no longer reads SalesInfos[0]', 'sales[0] if sales' not in _fl and 'newest_sale(' in _fl)


# ---------------------------------------------------------------- 3. duplicate mortgages
FOLIO = '3021150101230'


def rec(doc, date, bk, pg, amt=0, second='', first='OWNER JOHN', folio=FOLIO, **kw):
    r = {'doC_TYPE': doc, 'reC_DATE': date, 'reC_BOOK': bk, 'reC_PAGE': pg,
         'reC_BOOKPAGE': '%s/%s' % (bk, pg), 'consideratioN_1': amt, 'intangible': 0,
         'seconD_PARTY': second, 'firsT_PARTY': first, 'foliO_NUMBER': folio,
         'subdiV_NAME': 'PALM ACRES'}
    r.update(kw)
    return r


deed = rec('DEED', '4/1/2015', '29500', '100', 0, 'OWNER JOHN', first='SELLER')
m1 = rec('MORTGAGE', '4/1/2015', '29500', '101', 400000, 'WELLS FARGO BANK NA')
m2 = rec('MORTGAGE', '6/9/2018', '31000', '2200', 60000, 'CITY NATIONAL BANK', folio='')
# same two instruments, indexed a second time under the co-owner's name
dup = [dict(m1, firsT_PARTY='OWNER MARY'), dict(m2, firsT_PARTY='OWNER MARY')]
clean = RL.analyze([deed, m1, m2], FOLIO, 398000, ftype='MORTGAGE')
dirty = RL.analyze([deed, m1, m2] + dup, FOLIO, 398000, ftype='MORTGAGE')
check('liens: fixture has two open mortgages', clean['open_count'] == 2, str(clean['open_count']))
check('liens: duplicated index entries collapse to the same two', dirty['open_count'] == 2,
      'got %d open' % dirty['open_count'])
check('liens: junior not double counted', dirty['junior'] == clean['junior'] == 60000,
      'clean %s dirty %s' % (clean['junior'], dirty['junior']))
check('liens: chain rows listed once', len(dirty['liens']) == 2, str(len(dirty['liens'])))
check('liens: rows naming no instrument are kept, not guessed away (same rule as PR #50)',
      len(RL.analyze([deed, dict(m1, reC_BOOK='', reC_PAGE=''), dict(m1, reC_BOOK='', reC_PAGE='')],
                     FOLIO, 398000)['liens']) == 2)
_old = {'liens': [{'bp': '29500/101'}, {'bp': '31000/2200'}, {'bp': '29500/101'}, {'bp': '31000/2200'}]}
check('liens: a cached pre-dedupe chain is detected', RL.has_duplicate_liens(_old))
check('liens: a clean cached chain is not', not RL.has_duplicate_liens(clean) and not RL.has_duplicate_liens({}))


# ---------------------------------------------------------------- 4. a lender's separate foreclosure
import datetime
import equity_state as ES
_recent = (datetime.date.today() - datetime.timedelta(days=200)).strftime('%m/%d/%Y')
lp_bank = rec('LIS PENDENS', _recent, '34900', '10', 0, 'SALKEY AND ASSOCIATES INC',
              first='US BANK NATIONAL ASSOCIATION AS TRUSTEE')
lp_hoa = rec('LIS PENDENS', _recent, '34800', '5', 0, 'SALKEY AND ASSOCIATES INC',
             first='OCEAN TOWERS CONDOMINIUM ASSOCIATION INC')
lp_old = rec('LIS PENDENS', '3/3/2011', '27000', '9', 0, 'SALKEY AND ASSOCIATES INC',
             first='WELLS FARGO BANK NA')
hoa_clear = RL.analyze([deed, lp_hoa], FOLIO, 12000, ftype='HOA')
hoa_bank = RL.analyze([deed, lp_hoa, lp_bank], FOLIO, 12000, ftype='HOA')
check('bank fc: association chain with no lender filing stays clear',
      hoa_clear['second_fc'] is None and ES.state_of(hoa_clear) == 'clear')
check('bank fc: a lender lis pendens on the parcel is found', bool(hoa_bank['second_fc'])
      and 'US BANK' in hoa_bank['second_fc']['party'], str(hoa_bank['second_fc']))
check('bank fc: a decade-old lis pendens is not today\'s foreclosure',
      RL.analyze([deed, lp_hoa, lp_old], FOLIO, 12000, ftype='HOA')['second_fc'] is None)
check('bank fc: a released lis pendens does not count',
      RL.analyze([deed, lp_bank, rec('RELEASE LIS PENDENS', _recent, '35000', '1', 0, '',
                                     oriG_REC_BOOK='34900', oriG_REC_PAGE='10')],
                 FOLIO, 12000, ftype='HOA')['second_fc'] is None)
check('bank fc: another parcel\'s filing does not count',
      RL.analyze([deed, dict(lp_bank, foliO_NUMBER='3099999999999', subdiV_NAME='ELSEWHERE')],
                 FOLIO, 12000, ftype='HOA')['second_fc'] is None)
check('bank fc: a bank case lead is not asked (its own lis pendens is the case)',
      RL.analyze([deed, lp_bank], FOLIO, 12000, ftype='MORTGAGE')['second_fc'] is None)
check('bank fc: a clear with no coverage record is re-pulled first',
      RL.clear_undocumented({'conf': 'ok', 'liens': []})
      and not RL.clear_undocumented(hoa_clear)
      and not RL.clear_undocumented({'conf': 'ok', 'liens': [{'amt': 1}]}))

# the chain a CLEAR has to carry, as analyze() now writes it
DOCUMENTED = {'conf': 'ok', 'liens': [], 'nrec': 12, 'second_fc': None, 'mtg_open_unpriced': 0}


def _row(**kw):
    r = {'case': '2025-145272-CC-23'}
    ES.apply(r, dict(DOCUMENTED))
    r.update(kw)
    return r


r1 = _row(orsecond={'case': 'lis pendens recorded 01/02/2026', 'party': 'US BANK NA'})
check('board: CLEAR beside the chain\'s own 2ND FORECLOSURE becomes UNVERIFIED',
      ES.demote_for_bank_fc(r1) and r1['eqstate'] == 'none' and 'separate case' in r1['eqstate_why'])
r2 = _row(sib=[{'case': '2025-011111-CA-01', 'sold': False, 'conf': 'high', 'pl': 'NATIONSTAR'}])
check('board: CLEAR beside an open bank sibling case becomes UNVERIFIED',
      ES.demote_for_bank_fc(r2) and r2['eqstate'] == 'none')
r3 = _row(sib=[{'case': '2025-011111-CA-01', 'sold': False, 'conf': 'low'}])
check('board: a low-confidence (namesake) sibling does not demote', not ES.demote_for_bank_fc(r3)
      and r3['eqstate'] == 'clear')
r4 = _row()
check('board: nothing filed, CLEAR stays', not ES.demote_for_bank_fc(r4) and r4['eqstate'] == 'clear')
r5 = {'case': 'X', 'eqstate': 'priced', 'orsecond': {'case': 'c'}}
check('board: only ever moves CLEAR, never touches another state',
      not ES.demote_for_bank_fc(r5) and r5['eqstate'] == 'priced')
_flsrc = open(os.path.join(HERE, 'foreclosure_leads.py'), encoding='utf-8').read()
check('board: both merge paths settle it after orsecond/sib are attached',
      _flsrc.count('_es.demote_for_bank_fc(') == 2
      and _flsrc.index("d['sibclaimed']") < _flsrc.index('_es.demote_for_bank_fc(d)'))


# ---------------------------------------------------------------- 5. no mortgage found is not verified clear
check('coverage: a documented empty search is CLEAR', ES.state_of(DOCUMENTED) == 'clear')
check('coverage: a cached clear with no record of how it searched is UNVERIFIED',
      ES.state_of({'conf': 'ok', 'liens': []}) == 'none')
check('coverage: zero records returned proves nothing',
      ES.state_of(dict(DOCUMENTED, nrec=0)) == 'none')
check('coverage: a capped or truncated search proves nothing',
      ES.state_of(dict(DOCUMENTED, capped=True)) == 'none'
      and ES.state_of(dict(DOCUMENTED, truncated=True)) == 'none')
check('coverage: never asked whether a lender is foreclosing -> not clear',
      ES.state_of({k: v for k, v in DOCUMENTED.items() if k != 'second_fc'}) == 'none')
check('coverage: a lender foreclosing the unit -> not clear',
      ES.state_of(dict(DOCUMENTED, second_fc={'case': 'lis pendens 34829/883'})) == 'none')
check('coverage: an open mortgage with no published amount is a CEILING, not zero debt',
      ES.state_of(dict(DOCUMENTED, mtg_open_unpriced=1)) == 'unpriced')
check('coverage: priced list plus an unpriced mortgage is a CEILING, not priced',
      ES.state_of(dict(DOCUMENTED, liens=[{'amt': 300000, 'st': 'OPEN'}], mtg_open_unpriced=1)) == 'unpriced'
      and ES.state_of(dict(DOCUMENTED, liens=[{'amt': 300000, 'st': 'OPEN'}])) == 'priced')
m0 = rec('MORTGAGE', '5/5/2020', '31800', '44', 0, 'PRIVATE LENDER LLC')
a0 = RL.analyze([deed, m0], FOLIO, 12000, ftype='HOA')
check('coverage: analyze counts a $0 mortgage instead of dropping it',
      a0['mtg_open_unpriced'] == 1 and a0['liens'] == [] and ES.state_of(a0) == 'unpriced', str(a0))
check('coverage: ...and a satisfied one is not counted',
      RL.analyze([deed, m0, rec('SATISFACTION', '6/6/2022', '33000', '1', 0, '',
                                oriG_REC_BOOK='31800', oriG_REC_PAGE='44')],
                 FOLIO, 12000, ftype='HOA')['mtg_open_unpriced'] == 0)
check('coverage: a modification with no amount is still a placeholder, not a loan',
      RL.analyze([deed, rec('MORTGAGE MODIFICATION', '5/5/2020', '31800', '45', 0, 'WELLS FARGO BANK NA')],
                 FOLIO, 12000, ftype='HOA')['mtg_open_unpriced'] == 0)
check('coverage: an unpriced loan never changes the priced totals',
      RL.analyze([deed, m1, m2, m0], FOLIO, 398000)['junior'] == clean['junior'])
check('coverage: every fresh analyze() writes the coverage record',
      all(k in hoa_clear for k in ('nrec', 'second_fc', 'mtg_open_unpriced')) and hoa_clear['nrec'] == 2)


_own = {'ctype': 'Bank/Mortgage'}
check('own case: a bank foreclosure cannot read VERIFIED CLEAR',
      ES.apply(_own, dict(DOCUMENTED)) == 'none' and _own['eqstate'] not in ES.FACT
      and 'lender is foreclosing' in _own['eqstate_why'])
check('own case: the raw lead field is read too',
      ES.state_of(DOCUMENTED, {'case_type': 'Bank/Mortgage'}) == 'none')
check('own case: an association suing proves a dues debt, not a mortgage -- clear still possible',
      ES.state_of(DOCUMENTED, {'ctype': 'HOA/Condo'}) == 'clear' and ES.state_of(DOCUMENTED) == 'clear')
check('own case: a priced chain on a bank case stays priced',
      ES.state_of(dict(DOCUMENTED, liens=[{'amt': 100000}]), {'ctype': 'Bank/Mortgage'}) == 'priced')


# ---------------------------------------------------------------- 6. Elharrar, refreshed
# Two open mortgages ($300,000 + $95,000) that the name search returned under both owners. The cached
# chain from before the one-instrument rule held all four copies: $790,000. Re-pulling it (the
# refresh has_duplicate_liens queues first) must give the county's $395,000.
e_deed = rec('WARRANTY DEED', '2/2/2016', '29900', '300', 0, 'ELHARRAR A', first='SELLER B')
e_m1 = rec('MORTGAGE', '2/2/2016', '29900', '301', 300000, 'BANK OF AMERICA NA', first='ELHARRAR A')
e_m2 = rec('MORTGAGE', '8/8/2020', '32000', '77', 95000, 'TD BANK NA', first='ELHARRAR A', folio='')
e_dups = [dict(e_m1, firsT_PARTY='ELHARRAR M'), dict(e_m2, firsT_PARTY='ELHARRAR M')]
e_cached = {'conf': 'ok', 'liens': [
    {'bp': '29900/301', 'amt': 300000, 'st': 'OPEN'}, {'bp': '32000/77', 'amt': 95000, 'st': 'OPEN'},
    {'bp': '29900/301', 'amt': 300000, 'st': 'OPEN'}, {'bp': '32000/77', 'amt': 95000, 'st': 'OPEN'}]}
check('elharrar: the stale cached chain sums $790,000', sum(l['amt'] for l in e_cached['liens']) == 790000)
check('elharrar: the stale cached chain is queued for a re-pull', RL.has_duplicate_liens(e_cached))
e_new = RL.analyze([e_deed, e_m1, e_m2] + e_dups, FOLIO, 0, ftype='HOA')
_e_open = [l for l in e_new['liens'] if l['st'] == 'OPEN']
check('elharrar: the refreshed chain holds two mortgages totalling $395,000',
      len(_e_open) == 2 and sum(l['amt'] for l in _e_open) == 395000 and e_new['surv'] == 395000,
      '%d open, $%s' % (len(_e_open), sum(l['amt'] for l in _e_open)))
check('elharrar: ...and is no longer flagged', not RL.has_duplicate_liens(e_new))
_flx = open(os.path.join(HERE, 'foreclosure_leads.py'), encoding='utf-8').read()
check('elharrar: until it is re-pulled the board flags the stale chain LOW (overstated, never clear)',
      'has_duplicate_liens' in _flx and "conf='low', dupliens=True" in _flx
      and ES.state_of(dict(e_cached, conf='low')) != 'clear')


# ---------------------------------------------------------------- 1. sale day
TPL = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
clock = CM._region(TPL, CM._CLOCK_START, CM._CLOCK_END, 'clock')
saled = CM._region(TPL, CM._SALEDAYS_START, CM._SALEDAYS_END, 'saleDays')
ctxt = CM._region(TPL, 'function _clockTxt(r){', '\n}\n', 'clockTxt') + '\n}\n'
CMSRC = open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
# _heldNow is a one-liner directly above liveDays; one slice carries both
live = CM._region(CMSRC, 'function _heldNow(x){', '\nfunction liveDays(r){', 'heldNow') + \
    CM._region(CMSRC, 'function liveDays(r){', '\n}\n', 'liveDays') + '\n}\n'
biz = CM._region(CMSRC, 'function _bizDays(r){', '\n}\n', 'bizDays') + '\n}\n'
check('call mode lifts _heldToday with the clock block', 'function _heldToday' in clock)

HARNESS = r'''
var FIX = %(fix)s;
var _RealDate = Date;
function mkDate(nowIso){
  var NOW = new _RealDate(nowIso);
  function D(){ var a = Array.prototype.slice.call(arguments);
    if(!a.length) return new _RealDate(NOW.getTime());
    return new (Function.prototype.bind.apply(_RealDate, [null].concat(a)))(); }
  D.now = function(){ return NOW.getTime(); };
  D.prototype = _RealDate.prototype;
  return D;
}
function isBalloon(r){ return r.st==='BAL'; }
var out = {};
FIX.forEach(function(f){
  Date = mkDate(f.now);
  %(clock)s
  %(saled)s
  %(ctxt)s
  %(live)s
  %(biz)s
  var r = {auction: f.auc, days: 0, x: f.auc};
  var d = _saleDays(r); r.days = d;
  out[f.k] = {saleDays: d, passed: _aucPassed(r), clock: _clockTxt(r), live: liveDays(r), biz: _bizDays(r)};
});
Date = _RealDate;
console.log(JSON.stringify(out));
'''
FIX = [
    {'k': 'today_0859', 'now': '2026-09-23T08:59:00', 'auc': '09/23/2026'},
    {'k': 'today_0900', 'now': '2026-09-23T09:00:00', 'auc': '09/23/2026'},
    {'k': 'today_1500', 'now': '2026-09-23T15:00:00', 'auc': '09/23/2026'},
    {'k': 'tomorrow',   'now': '2026-09-23T15:00:00', 'auc': '09/24/2026'},
    {'k': 'yesterday',  'now': '2026-09-23T07:00:00', 'auc': '09/22/2026'},
]
with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
    f.write(HARNESS % {'fix': json.dumps(FIX), 'clock': clock, 'saled': saled, 'ctxt': ctxt,
                       'live': live, 'biz': biz})
    js = f.name
try:
    p = subprocess.run(['node', js], capture_output=True, text=True, timeout=60)
finally:
    os.unlink(js)
if p.returncode != 0:
    check('sale day: harness ran under node', False, (p.stderr or '')[-400:])
    R = {}
else:
    R = json.loads(p.stdout.strip().splitlines()[-1])
if R:
    a = R['today_0859']
    check('sale day before 9am: still 0 days, still workable', a['saleDays'] == 0 and not a['passed']
          and a['live'] == 0 and a['biz'] == 0, str(a))
    for k in ('today_0900', 'today_1500'):
        b = R[k]
        check('sale day %s: held, out of every lane' % k[6:], b['saleDays'] == -1 and b['passed']
              and b['live'] == -1 and b['biz'] == -1, str(b))
        check('sale day %s: says "sale held today", not "passed"' % k[6:], b['clock'] == 'sale held today', b['clock'])
    c = R['tomorrow']
    check('tomorrow at 3pm: 1 day, untouched', c['saleDays'] == 1 and not c['passed'] and c['live'] == 1, str(c))
    y = R['yesterday']
    check('yesterday: passed as before', y['saleDays'] == -1 and y['passed'] and y['clock'] == 'passed', str(y))

print('\n%s: %d failure(s)' % ('FAIL' if FAILS else 'OK', len(FAILS)))
sys.exit(1 if FAILS else 0)
