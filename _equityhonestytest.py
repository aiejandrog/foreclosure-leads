#!/usr/bin/env python3
"""_equityhonestytest.py -- the board never prints a guess as equity (2026-09-30).

Five defects, one suite:

  1. A foreclosure whose final judgment is not posted (ju) had _payoffOf() == 0, so _netEqOf()
     returned the WHOLE value: the row read "judgment not posted · 100% eq" and the 30%+ equity
     filter let it through. Now: "eq unknown", $0 claimed, filtered out.
  2. Every lis pendens row (no judgment yet) read "~100% eq" the same way. Now: "eq unknown".
  3. Liens the recorded search never established counted as $0 and the row printed the figure
     plainly. Now the row says "≤N%" (the most it can be) unless equity_state is clear/priced,
     and the 30%+ filter admits only clear/priced.
  4. classify() typed Fannie Mae / Ginnie Mae / COMMUNITY lenders HOA/Condo (checked here too).
  5. judgment_interest.py: "judgement" spelling, the notice-of-sale floor, a clerk non-answer is
     not cached as a miss, and the bake labels a floor as a minimum without calling it the entry.

Runs on any checkout: synthetic rows only (invented names/cases), a headless page built the way
build_preview.py builds one but written to a temp file, so nothing tracked is touched.
"""
import datetime, json, os, re, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import foreclosure_leads as F
import disclaimer as _D
import judgment_interest as JI
from playwright.sync_api import sync_playwright

CHECKS, FAILS = [], []


def check(name, ok, extra=''):
    CHECKS.append(name)
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:240]) if extra else ''))
    if not ok:
        FAILS.append(name)


def preview_empty():
    """PREVIEW_EMPTY out of build_preview.py without running its build (see _balloonlanetest)."""
    src = open(os.path.join(HERE, 'build_preview.py'), encoding='utf-8').read()
    m = re.search(r'^tpl\s*=', src, re.M)
    if not m:
        raise SystemExit('build_preview.py no longer starts its build with a top-level `tpl =`')
    ns = {'__name__': '_eqhonesty_lift', '__file__': os.path.join(HERE, 'build_preview.py')}
    exec(compile(src[:m.start()], 'build_preview.py(prefix)', 'exec'), ns)
    return dict(ns['PREVIEW_EMPTY'])


def row(case, **kw):
    auc = (datetime.date.today() + datetime.timedelta(days=20)).strftime('%m/%d/%Y')
    r = {"tier": "A", "score": 80, "st": "FC", "case": case, "auction": auc, "days": 20,
         "filed": 2025, "bought": 2010, "bprice": 200000, "owners": "TEST OWNER " + case[-4:],
         "oname": "Test Owner", "addr": "100 TEST ST, MIAMI, FL 33101", "mail": "100 TEST ST, MIAMI, FL 33101",
         "value": 400000, "judg": 240000, "eq": 40, "hs": True, "ctype": "Bank/Mortgage",
         "plaintiff": "Test Savings Bank", "defs": "Test Owner",
         "phones": ["3055550001"], "phdnc": [False], "phsrc": ["st"], "phbest": 0, "emails": [],
         "pa": "#", "zillow": "#", "tax": "#", "auc": "#", "people": "#", "docket": "#",
         "eqstate": "clear"}
    r.update(kw)
    return r


ROWS = [
    row("2099-000001-CA-01", judg=0, ju=True, eq=None, eqstate="none"),            # 1 judgment not posted
    row("2099-000002-CA-01", st="LP", auction="", days=9999, judg=0, eq=None,       # 2 lis pendens
        eqfake=True, eqstate="unchecked"),
    row("2099-000003-CA-01", eqstate="clear"),                                      # verified: 40%
    row("2099-000004-CA-01", eqstate="none"),                                       # 3 same money, unverified
    row("2099-000005-CA-01", eqstate="priced"),                                     # verified priced
    row("2099-000006-CA-01", eqstate="unpriced"),                                   # 3 ceiling
    row("2099-000007TD", st="TD", judg=0, obid=60000, eq=None, eqstate="unchecked"),  # tax deed untouched
    row("2099-000008-CA-01", payoff=254000, jaccr=14000, jaccrued=True, jasof="2026-10-20",   # 5 floor
        jfloor=True, jfloordate="2025-03-10"),
    row("2099-000009-CA-01", payoff=262000, jaccr=22000, jaccrued=True, jasof="2026-10-20",   # 5 verified
        jdate="2024-11-02"),
]


def build_html(rows):
    tpl = F.subst_build_facts(open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read(),
                              "2026-09-30 12:00")
    tpl = tpl.replace('__MOTIONJS__', F._motion_js())
    for k, v in preview_empty().items():
        tpl = tpl.replace(k, v)
    tpl = tpl.replace('__IDENT_EN__', F._esc_js(_D.identity('en', as_html=False)))
    tpl = tpl.replace('__IDENT_ES__', F._esc_js(_D.identity('es', as_html=False)))
    tpl = F._bake_alex_email(tpl, '_equityhonestytest')
    html = tpl.replace("__DATA__", F._esc_json(rows))
    F.assert_no_placeholders(html, '_equityhonestytest')
    return html


PROBE = r"""() => {
  const by = {}; DATA.forEach(r => by[r.case] = r);
  const out = {};
  Object.keys(by).forEach(c => {
    const r = by[c];
    const ds = document.createElement('div');
    out[c] = {
      debtUnknown: _debtUnknown(r), ceiling: _eqCeiling(r), net: _netEqOf(r), owner: _ownerEqOf(r),
      pct: netEqPct(r), rowHtml: _eqRowHtml(r), stat: _eqStatTxt(r), netTxt: _netEqTxt(r),
      jose: (r.st==='TD') ? '' : _joseMsg(r), live: _liveMsg(r),
      snap: genDealSnapshot(r), field: genFieldSheet(r), atty: genAttyPacket(r),
      cure: (r.st==='TD') ? '' : (_cureBlock(r) || ''), paths: _profitPaths(r),
    };
  });
  // the 30%+ equity filter, through its own button
  document.getElementById('eq30').click();
  out._eq30 = view().map(r => r.case);
  document.getElementById('eq30').click();
  out._all = view().map(r => r.case);
  out._listText = document.body.innerText;
  return out;
}"""


def board_suite():
    html = build_html(ROWS)
    fd, path = tempfile.mkstemp(suffix='.html', prefix='eqhonesty_')
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(html)
    errs = []
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            pg = b.new_page(viewport={'width': 1500, 'height': 1000})
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.goto('file://' + path, wait_until='domcontentloaded')
            pg.wait_for_function("typeof DATA !== 'undefined' && typeof view === 'function'", timeout=20000)
            pg.wait_for_timeout(400)
            got = pg.evaluate(PROBE)
            b.close()
    finally:
        os.unlink(path)
    check('page runs with no script error', not errs, errs[:3])

    ju, lp = got['2099-000001-CA-01'], got['2099-000002-CA-01']
    ver, unv = got['2099-000003-CA-01'], got['2099-000004-CA-01']
    pri, ceil, td = got['2099-000005-CA-01'], got['2099-000006-CA-01'], got['2099-000007TD']

    # 1. judgment not posted
    check('1: judgment not posted is debt-unknown', ju['debtUnknown'] is True)
    check('1: net and owner equity claim nothing (not the $400k value)', ju['net'] == 0 and ju['owner'] == 0,
          (ju['net'], ju['owner']))
    check('1: row reads "eq unknown", never a percent', 'eq unknown' in ju['rowHtml'] and '%' not in ju['rowHtml'],
          ju['rowHtml'])
    check('1: card stat reads "unknown"', ju['stat'] == 'unknown', ju['stat'])
    check('1: net-equity text says unknown, judgment not posted', ju['netTxt'].startswith('unknown') and
          'judgment not posted' in ju['netTxt'], ju['netTxt'])
    check('1: Jose text never quotes a dollar net', 'Net equity after the stack: unknown' in ju['jose'], ju['jose'][-200:])
    check('1: live-call text says net unknown', ' · net unknown' in ju['live'], ju['live'][-160:])
    check('1: deal snapshot does not print value as "owed"', 'VERIFY — the debt is unknown' in ju['snap']
          and '$400,000</span></div>' not in ju['snap'].split('Total owed')[1][:80] if 'Total owed' in ju['snap']
          else 'VERIFY — the debt is unknown' in ju['snap'])
    check('1: field sheet net equity is unknown', 'unknown — judgment not posted' in ju['field'])
    check('1: attorney packet says unknown, not "$0 debt meets value"', 'no final judgment posted' in ju['atty']
          and 'meets or exceeds value' not in ju['atty'])
    check('1: filtered out of 30%+ equity', '2099-000001-CA-01' not in got['_eq30'], got['_eq30'])

    # 2. lis pendens
    check('2: lis pendens is debt-unknown', lp['debtUnknown'] is True)
    check('2: lis pendens row reads "eq unknown" (was "~100% eq")', 'eq unknown' in lp['rowHtml'] and
          '100%' not in lp['rowHtml'], lp['rowHtml'])
    check('2: lis pendens text names the missing loan balance', 'loan balance not known' in lp['netTxt'], lp['netTxt'])
    check('2: filtered out of 30%+ equity', '2099-000002-CA-01' not in got['_eq30'])

    # 3. unverified liens are a ceiling
    check('3: verified clear prints the plain figure', ver['rowHtml'].strip(' ·') == '40% eq' and ver['stat'] == '40%',
          (ver['rowHtml'], ver['stat']))
    check('3: same money, unverified search, prints "≤40% eq"', '≤40% eq' in unv['rowHtml'] and unv['stat'] == '≤40%',
          (unv['rowHtml'], unv['stat']))
    check('3: unverified net text says "at most ... liens not verified"', unv['netTxt'].startswith('at most ') and
          'liens not verified' in unv['netTxt'], unv['netTxt'])
    check('3: an unpriced chain is a ceiling too', ceil['ceiling'] is True and '≤' in ceil['rowHtml'], ceil['rowHtml'])
    check('3: the dollar math is unchanged (ceiling is a label, not a new number)', unv['net'] == ver['net'] == 160000,
          (unv['net'], ver['net']))
    check('3: 30%+ equity admits verified clear, priced and the tax deed only',
          sorted(got['_eq30']) == ['2099-000003-CA-01', '2099-000005-CA-01', '2099-000007TD',
                                   '2099-000008-CA-01', '2099-000009-CA-01'], got['_eq30'])
    check('3: priced prints plain', pri['ceiling'] is False and '≤' not in pri['rowHtml'], pri['rowHtml'])

    # tax deed and the rest of the board
    check('tax deed is never debt-unknown or a ceiling', td['debtUnknown'] is False and td['ceiling'] is False)
    check('turning the filter off restores every row', len(got['_all']) == len(ROWS), got['_all'])
    # review follow-ups: the floor is a minimum everywhere, and unknown debt is not "no equity"
    flo, vd = got['2099-000008-CA-01'], got['2099-000009-CA-01']
    check('5: cure block calls a floor payoff "at least", never exact',
          'Pay it off (at least)' in flo['cure'] and '(exact)' not in flo['cure'] and
          'at most $146,000' in flo['cure'], flo['cure'][:400])
    check('5: a floor still warns that the judgment is entered (reinstatement right ended)',
          'FINAL JUDGMENT ENTERED on or before 2025-03-10' in flo['cure'], flo['cure'][-400:])
    check('5: a verified entry date is still exact', 'Pay it off (exact)' in vd['cure'] and
          'FINAL JUDGMENT ENTERED 2024-11-02' in vd['cure'])
    check('1: deal paths say equity unknown, not a no-equity play',
          'Equity unknown' in ju['paths'] and 'No-equity rescue' not in ju['paths'])
    check('2: lis pendens deal paths say equity unknown', 'Equity unknown' in lp['paths'])
    check('verified rows keep their normal deal paths', 'Equity unknown' not in ver['paths'] and
          'Cash-for-keys' in ver['paths'])


def classify_suite():
    for pl in ('FEDERAL NATIONAL MORTGAGE ASSOCIATION', 'GOVERNMENT NATIONAL MORTGAGE ASSOCIATION',
               'COMMUNITY LOAN SERVICING LLC', 'FIRST COMMUNITY BANK', 'TEST CREDIT UNION'):
        check('4: %s is a lender' % pl, F.classify('', pl) == 'Bank/Mortgage', F.classify('', pl))
    for pl in ('PALM TEST CONDOMINIUM ASSOCIATION INC', 'TEST LAKES MASTER ASSOCIATION INC',
               'SUNRISE TEST COMMUNITY ASSOCIATION INC', 'THE TEST VILLAGES HOMEOWNERS ASSOCIATION',
               'BANKERS TEST CONDOMINIUM ASSN', 'FEDERAL TEST HOMEOWNERS ASSN'):
        check('4: %s is still an association' % pl, F.classify('', pl) == 'HOA/Condo', F.classify('', pl))


def interest_suite():
    # spelling + floor + precedence (the same checks --selftest runs, asserted here)
    check('5: --selftest passes', JI._selftest() == 0)
    # a clerk non-answer is never cached as a miss
    import types
    cache = {}
    saved = (JI._ocs_case, JI.load_cache, JI.save_cache, JI.time.sleep)
    leads_path = JI.LEADS
    tmpd = tempfile.mkdtemp(prefix='eqhonesty_')
    try:
        JI.LEADS = os.path.join(tmpd, 'leads_final.json')
        json.dump([{'Case #': '2099-000011-CA-01', 'judgment': 100000},
                   {'Case #': '2099-000012-CA-01', 'judgment': 100000},
                   {'Case #': '2099-000013-CA-01', 'judgment': 100000}], open(JI.LEADS, 'w'))
        answers = {
            '2099-000011-CA-01': None,                                   # clerk did not answer
            '2099-000012-CA-01': {'dockets': [{'docketDescrition': 'NOTICE OF SALE', 'eventDate': '03/10/2025'}]},
            '2099-000013-CA-01': {'dockets': [{'docketDescrition': 'FINAL JUDGEMENT OF FORECLOSURE',
                                               'eventDate': '01/15/2025'}]},
        }
        JI._ocs_case = lambda s, c: answers[c]
        JI.load_cache = lambda: cache
        JI.save_cache = lambda d: None
        JI.time.sleep = lambda x: None
        sys.argv = ['judgment_interest.py', '--all']
        JI.main()
        check('5: a clerk non-answer is not cached', '2099-000011-CA-01' not in cache, cache.get('2099-000011-CA-01'))
        e12 = cache.get('2099-000012-CA-01') or {}
        check('5: no FJ entry + notice of sale -> floor stored, entry date left empty',
              e12.get('d') == '' and e12.get('floor') == '2025-03-10', e12)
        check('5: "judgement" spelling found as the entry date', (cache.get('2099-000013-CA-01') or {}).get('d') == '2025-01-15')
        # a fresh miss is not re-read; a week-old one is
        cache['2099-000011-CA-01'] = {'d': '', 'checked': datetime.date.today().isoformat()}
        check('5: a fresh miss is not re-read', not JI._stale_miss(cache['2099-000011-CA-01']))
        old = (datetime.date.today() - datetime.timedelta(days=JI.MISS_RECHECK_DAYS)).isoformat()
        check('5: a week-old miss is re-read', JI._stale_miss({'d': '', 'checked': old}))
        check('5: a found date is never re-read', not JI._stale_miss({'d': '2025-01-15', 'checked': old}))
    finally:
        JI._ocs_case, JI.load_cache, JI.save_cache, JI.time.sleep = saved
        JI.LEADS = leads_path
    # the floor accrues less than the true entry would: a minimum, never more
    as_of = datetime.date(2026, 9, 30)
    true_i = JI.accrue(100000, datetime.date(2025, 1, 15), as_of)['interest']
    floor_i = JI.accrue(100000, datetime.date(2025, 3, 10), as_of)['interest']
    check('5: floor interest is a lower bound of the true interest', 0 < floor_i < true_i, (floor_i, true_i))


def bake_suite():
    """The make_tracker interest bake, isolated: a floor row gets jfloor/jfloordate, never jdate."""
    src = open(os.path.join(HERE, 'foreclosure_leads.py'), encoding='utf-8').read()
    check('5: the bake reads the floor through accrual_start', '_JI.accrual_start(' in src)
    check('5: a floor never lands in jdate', "_r['jfloordate'] = _jd.isoformat()" in src and
          re.search(r"if _is_floor:.*?else:\s*\n\s*_r\['jdate'\]", src, re.S) is not None)
    # the board label for a floor says "at least" and never "entered"
    tpl = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
    i = tpl.index('function _accrNote(')
    body = tpl[i:tpl.index('function recompute(')]
    check('5: the floor label says at least and names the notice of sale',
          'if(r.jfloor)' in body and 'at least' in body and 'notice of sale' in body)


def surfaces_suite():
    """Python surfaces outside the board that print the floor or an unknown judgment."""
    import auction_brief as AB
    base = row("2099-000008-CA-01", payoff=254000, jaccr=14000, jaccrued=True, jasof="2026-10-20",
               jfloor=True, jfloordate="2025-03-10")
    html = AB.render_html(AB.build_brief(base, {}))
    check('5: auction brief calls a floor payoff "at least" and never "entry date verified"',
          'at least $254,000' in html and 'Entry date verified' not in html and '2025-03-10' in html)
    vd = dict(base, jfloor=None, jfloordate=None, jdate="2024-11-02", payoff=262000, jaccr=22000)
    html = AB.render_html(AB.build_brief(vd, {}))
    check('5: auction brief still says verified for a real entry date',
          'Entry date verified' in html and 'entered 2024-11-02' in html)
    # behaviour, not wording: a queue row built by call_rows carries the floor date as jf, and a
    # verified row carries jd and no jf (fixture shaped like _anchorgatetest's shippable row)
    import call_mode as CM
    def _cm(**kw):
        r = {'case': 'CACE-26-000093', 'county': 'BROWARD', 'st': 'LP', 'stage': 'LP',
             'owners': 'ROE,MARY', 'oname': 'Mary Roe', 'addr': '10 EAST ST, Sample City, FL 33301',
             'folio': '555501210050', 'mail': '', 'value': 400000, 'judg': 240000, 'eq': None,
             'eqfake': False, 'days': 9999, 'auction': '', 'phones': ['9545550101'], 'phdnc': [False],
             'phsrc': ['st'], 'phrank': [''], 'phbest': 0, 'emails': [], 'vac': False, 'warn': '',
             'ctype': 'Bank/Mortgage', 'ftype': 'MORTGAGE', 'payoff': 254000, 'jaccr': 14000}
        r.update(kw)
        return r
    rows, _n = CM.call_rows([_cm(jfloordate='2025-03-10', jfloor=True),
                             _cm(case='CACE-26-000094', jdate='2024-11-02')], max_days=60)
    by = {x.get('c') or x.get('case'): x for x in rows}
    fl, vr = by.get('CACE-26-000093') or {}, by.get('CACE-26-000094') or {}
    check('5: Call Mode queue row carries the floor date as jf',
          fl.get('jf') == '2025-03-10' and not fl.get('jd'), {k: fl.get(k) for k in ('jf', 'jd', 'py')})
    check('5: Call Mode verified row carries jd and no jf',
          vr.get('jd') == '2024-11-02' and not vr.get('jf'), {k: vr.get(k) for k in ('jf', 'jd')})
    cm = open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
    check('5: Call Mode ships the floor date and says "at least"',
          "'jf': _s('jfloordate', 10)" in cm and "(r.jf?'at least ':'')" in cm and
          "(r.jf && r.py!=null ? 'at least ' : '')" in cm)
    cl = open(os.path.join(HERE, 'call_list.py'), encoding='utf-8').read()
    check('1: call list prints "not known", never 0%, for an unposted judgment',
          "r.get(ek) is not None and not r.get('judgment_unknown')" in cl)


if __name__ == '__main__':
    classify_suite()
    interest_suite()
    bake_suite()
    surfaces_suite()
    board_suite()
    print('\n%d checks, %d failed' % (len(CHECKS), len(FAILS)))
    sys.exit(1 if FAILS else 0)
