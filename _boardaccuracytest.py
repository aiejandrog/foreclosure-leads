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
check('liens: rows naming no instrument are kept, not guessed away',
      len(RL.dedupe_records([{'doC_TYPE': 'MORTGAGE'}, {'doC_TYPE': 'MORTGAGE'}])) == 2)
check('liens: same page, different doc type both kept',
      len(RL.dedupe_records([m1, dict(m1, doC_TYPE='SATISFACTION')])) == 2)
_old = {'liens': [{'bp': '29500/101'}, {'bp': '31000/2200'}, {'bp': '29500/101'}, {'bp': '31000/2200'}]}
check('liens: a cached pre-dedupe chain is detected', RL.has_duplicate_liens(_old))
check('liens: a clean cached chain is not', not RL.has_duplicate_liens(clean) and not RL.has_duplicate_liens({}))


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
