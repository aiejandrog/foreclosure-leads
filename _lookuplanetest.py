"""_lookuplanetest -- Call Mode's "Find a number" list for leads that have no number yet.

Run:  python _lookuplanetest.py   (exit 0 = safe; invented leads, no network, no board, no site.codes)

WHY (2026-10-22, Alejandro): "ALL LEADS EVEN ONES WITH NO PHONE NUMBERS SCRAPED THROW THEM IN
MORNING WORKER AND CALL MODE BECAUSE THEN I CAN USE WHITEPAGE TO VERIFY THE NUMBER". On the 10-22
board, 2,093 of 4,434 leads stopped at "no phone at all"; 215 of the week's 232 new filings had no
number. call_rows dropped them and the phone carried them only as count rows with no way to call.

Pins:
  - call_rows(lookup=[]) collects exactly the leads that pass every gate and have no dialable
    number; a lead whose own numbers are do-not-call, a stayed lead, a lead outside the sale window
    and vacant land are NOT collected; the dial rows are byte-identical with or without the sink
  - mark_lookup stamps lk + seat bucket on those coverage rows only, and a lead whose person
    already has a dial row takes that row's seat
  - the page (real Chromium) lists them under "Find a number" with the Whitepages links, refuses a
    lead marked do-not-contact after the build, and a typed number saves n.phone and opens a normal
    card with that one number
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='lookuplane_')
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')   # no federal cache: no holds

import call_mode  # noqa: E402

FAILS = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra and not ok else ''))
    if not ok:
        FAILS.append(name)


def lead(case, **kw):
    d = {'case': case, 'county': 'MIAMI-DADE', 'st': 'FC', 'auction': '11/20/2026', 'days': 29,
         'tier': 'B', 'score': 50, 'value': 400000, 'owners': 'TEST OWNER ' + case,
         'oname': 'Test Owner', 'addr': '100 NW 1 ST, MIAMI, FL 33101', 'pkey': 'C' + case}
    d.update(kw)
    return d


PH = lead('2026-000101-CA-01', phones=['3055550101'], phdnc=[False], phsrc=['st'], phtype=['mobile'],
          pkey='P1')
NOPH = lead('2026-000102-CA-01')
SIB = lead('2026-000103-CA-01', pkey='P1')                        # same person as PH, no number
DNC = lead('2026-000104-CA-01', phones=['3055550104'], phdnc=[True], phsrc=['st'])
STAY = lead('2026-000105-CA-01', saleBkAct=True)
FAR = lead('2026-000106-CA-01', auction='06/01/2027', days=220)
VAC = lead('2026-000107-CA-01', vac=True)
LP = lead('2026-000108-CA-01', st='LP', auction='', days=9999, filedDate='10/20/2026')
SLIM = [PH, NOPH, SIB, DNC, STAY, FAR, VAC, LP]

print('\nBUILD: WHO IS COLLECTED')
sink = []
rows, total = call_mode.call_rows(SLIM, None, None, lookup=sink)
got = sorted(x['c'] for x in sink)
rec('no-phone leads that pass every gate are collected',
    got == sorted([NOPH['case'], SIB['case'], LP['case']]), got)
rec('do-not-call, stayed, too far out and vacant are not', not ({DNC['case'], STAY['case'], FAR['case'], VAC['case']} & set(got)), got)
rec('dial rows unchanged', [r['c'] for r in rows] == [PH['case']], [r['c'] for r in rows])
rows0, total0 = call_mode.call_rows(SLIM, None, None)
rec('dial rows identical with and without the sink', json.dumps(rows0, sort_keys=True) == json.dumps(rows, sort_keys=True))
rec('qualified total unchanged', total0 == total == 1, (total0, total))

print('\nBUILD: COVERAGE ROWS MARKED')
cov, _ = call_mode.coverage_rows(SLIM, [r['c'] for r in rows])
n = call_mode.mark_lookup(cov, sink, {r['pk']: r['sb'] for r in rows})
byc = {r['c']: r for r in cov}
rec('three rows marked', n == 3, n)
rec('marked rows carry lk and a seat bucket',
    all(byc[c].get('lk') == 1 and isinstance(byc[c].get('sb'), int) for c in got), [byc[c] for c in got])
rec('held rows are not marked', not any(byc[c].get('lk') for c in (DNC['case'], STAY['case'], FAR['case'], VAC['case'])))
rec('same person as a dial row takes its seat', byc[SIB['case']]['sb'] == rows[0]['sb'],
    (byc[SIB['case']].get('sb'), rows[0]['sb']))
rec('no phone numbers on any coverage row', not any('p' in r or 'phones' in r for r in cov))
src = open(os.path.join(HERE, 'foreclosure_leads.py'), encoding='utf-8').read()
rec('the refresh passes one lookup list to both seat pages', src.count('lookup=_cm_lk') == 3, src.count('lookup=_cm_lk'))

print('\nPAGE: FIND A NUMBER (Chromium)')
try:
    from playwright.sync_api import sync_playwright
except Exception as e:
    sync_playwright = None
    print('SKIP browser half (playwright not importable: %s)' % e)
if sync_playwright:
    tpl = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
    html = call_mode.build_html([], 0, {'ct': 'x', 'k': 'y'}, 'built', 'sig', 'bsig',
                                sync_js=call_mode.extract_sync_js(tpl), funnel_js=call_mode.extract_funnel_js(tpl),
                                text_js=call_mode.extract_text_js(tpl),
                                histcov='{"ledgers_ok": true, "why": "", "capped": false, "total": 1, "shipped": 1}')
    path = os.path.join(_T, 'call.html')
    open(path, 'w', encoding='utf-8').write(html)
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None)
        pg = b.new_page()
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e) + ' @ ' + str(getattr(e, 'stack', ''))[:600]))
        pg.goto('file://' + path)
        pg.wait_for_timeout(500)
        out = pg.evaluate("""(cov) => {
          const o = {};
          try { localStorage.clear(); } catch(e) {}
          notes = {}; ROWS = []; COV = cov;
          notes[cov[1].c] = {status: 'DO NOT CONTACT'};
          start();
          o.head = document.getElementById('app').innerHTML.indexOf('Find a number') >= 0;
          o.n = lkRows().map(r => r.c);
          BLANE = '__lk'; render();
          const app = document.getElementById('app').innerHTML;
          o.wp = /whitepages\\.com\\/address\\//.test(app);
          o.inputs = document.querySelectorAll('input.lknum').length;
          document.querySelector('input.lknum').value = '(305) 555-0199';
          document.querySelector('button.lkadd').click();
          o.row = ROWS.filter(r => r.c === cov[0].c).map(r => r.p)[0] || null;
          o.saved = (notes[cov[0].c] || {}).phone || '';
          o.blane = BLANE; o.cur = cur && cur.c;
          o.left = lkRows().length;
          o.card = document.getElementById('app').innerHTML.indexOf('5550199') >= 0;
          return o;
        }""", [byc[NOPH['case']], byc[LP['case']]])
        b.close()
    rec('page loads without a script error', not errs, errs)
    rec('head shows the Find a number button', out.get('head'), out)
    rec('a lead marked do-not-contact after the build is not listed', out.get('n') == [NOPH['case']], out.get('n'))
    rec('the list carries the Whitepages address link', out.get('wp'), out)
    rec('one number box per lead', out.get('inputs') == 1, out.get('inputs'))
    rec('typed number becomes the lead\'s one dial number', out.get('row') == ['3055550199'], out.get('row'))
    rec('typed number saved to the note (n.phone)', out.get('saved') == '3055550199', out.get('saved'))
    rec('the card opens on that lead', out.get('blane') is None and out.get('cur') == NOPH['case'], out)
    rec('the card shows the typed number', out.get('card'), out)
    rec('the lead leaves the lookup list', out.get('left') == 0, out.get('left'))

print('\nMORNING WORKER: FIND A NUMBER PANEL (Chromium)')
if sync_playwright:
    import _balloonlanetest as BL
    fake, empty = BL.preview_constants()

    def nc(case, **kw):
        r = BL.fc_row(case, '200 NW 2 AVE, MIAMI, FL- 33101', 30, 'NOCONTACT ' + case, 'Owner ' + case, '')
        r.update({'phones': [], 'phdnc': [], 'phsrc': [], 'phbest': None, 'emails': []})
        r.update(kw)
        return r
    W_OK, W_HELD, W_STAY, W_TYPED, W_DNC = 'WK-OK-1', 'WK-HELD-1', 'WK-STAY-1', 'WK-TYPED-1', 'WK-DNC-1'
    board = BL.build_html(fake + [nc(W_OK), nc(W_HELD, ddhold=True), nc(W_STAY, saleBkAct=True),
                                  nc(W_TYPED), nc(W_DNC)], empty)
    bpath = os.path.join(_T, 'board.html')
    open(bpath, 'w', encoding='utf-8').write(board)
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None)
        pg = b.new_page()
        werrs = []
        pg.on('pageerror', lambda e: werrs.append(str(e)))
        pg.goto('file://' + bpath)
        pg.wait_for_timeout(1500)
        w = pg.evaluate("""(cs) => {
          notes[cs.typed] = Object.assign(notes[cs.typed] || {}, {phone: '3055550177'});
          notes[cs.dnc] = Object.assign(notes[cs.dnc] || {}, {status: 'DO NOT CONTACT'});
          const listed = _workerLookupRows().map(r => r.case);
          const doc = genMorningWorker('active', false);
          return {listed: listed, panel: doc.indexOf('Find a number') >= 0,
                  wp: doc.indexOf('whitepages.com/address/') >= 0,
                  placeholder: doc.indexOf('lookup-placeholder') >= 0,
                  queued: JSON.stringify(Object.values(JSON.parse((doc.match(/var LANES=(\{[\s\S]*?\}), LMETA=/)||[0,'{}'])[1])))
                          .indexOf(cs.ok) >= 0};
        }""", {'typed': W_TYPED, 'dnc': W_DNC, 'ok': W_OK})
        b.close()
    rec('board loads without a script error', not werrs, werrs[:2])
    rec('an eligible no-contact lead is on the worker list', W_OK in w['listed'], w['listed'])
    rec('diligence-held, stayed and do-not-contact leads are not',
        not ({W_HELD, W_STAY, W_DNC} & set(w['listed'])), w['listed'])
    rec('a lead with a typed number leaves the list', W_TYPED not in w['listed'], w['listed'])
    rec('the worker document carries the panel with Whitepages links', w['panel'] and w['wp'], w)
    rec('the placeholder address never reaches the worker document', not w['placeholder'])
    rec('no-contact leads are not queued as send cards', not w['queued'], w)

print('\n%d failure(s)' % len(FAILS) if FAILS else '\nall passed')
sys.exit(1 if FAILS else 0)
