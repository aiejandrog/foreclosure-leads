"""_callvacanttest -- Find a number: Vacant and Not-on-Whitepages take a lead OFF the list, and bring it back.

Run:  python _callvacanttest.py   (exit 0 = safe; invented leads, no network)

WHY (2026-11-19, Alejandro): "When a property is Vacant i need to have a Vacant button on Find a phone to
send it to another pipeline for vacant strategy. Also a button for the addresses that dont show up on
whitepages." And: "the difference is Apt and #. whitepages react to #".

Pins:
  - every Find a number card has a Vacant button and a Not on Whitepages button
  - Vacant moves the lead out of the calling list into the Vacant list; the count on the top-bar
    button drops by one; the mark is a 'lookup' touch (never a dial) that names the address
  - the Vacant list shows the lead, offers "Copy all" in the search-bar spelling, and one tap restores it
  - Not on Whitepages moves the lead to Show skipped, and Restore brings it back
  - both marks survive a reload (they live on the case note), and the view he was on comes back
  - a lead that is vacant is not also listed as skipped
  - the voicemail script button is on the card and the outcome screen
  - the copied address spells a unit "# E19" (coach-side greeting is pinned in _coachgreettest)
"""
import datetime as dt
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='vacant_')
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')
import call_mode  # noqa: E402

FAILS = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:400]) if extra and not ok else ''))
    if not ok:
        FAILS.append(name)


AUC = (dt.date.today() + dt.timedelta(days=29)).strftime('%m/%d/%Y')


def lead(case, **kw):
    d = {'case': case, 'county': 'BROWARD', 'st': 'FC', 'auction': AUC, 'days': 29, 'tier': 'B', 'score': 50,
         'value': 400000, 'owners': 'TESTER, DANA ' + case[-2:], 'oname': 'Dana Tester', 'addr': '100 NW 1 ST, MIAMI, FL 33101',
         'pkey': 'C' + case}
    d.update(kw)
    return d


DIAL = [lead('CACE2600300%d' % i, phones=['30555502%02d' % i], phdnc=[False], phsrc=['st'], phtype=['mobile']) for i in range(2)]
NOPH = [lead('CACE2600400%d' % i, addr='%d27 WEDGEWOOD DR APT E1%d, TAMARAC, FL 33321' % (i + 93, i)) for i in range(4)]
SLIM = DIAL + NOPH
sink = []
rows, _ = call_mode.call_rows(SLIM, None, None, lookup=sink)
cov, _ = call_mode.coverage_rows(SLIM, [r['c'] for r in rows])
call_mode.mark_lookup(cov, sink, {r['pk']: r['sb'] for r in rows})

try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as _p:
        _p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None).close()
except Exception as e:
    print('SKIP browser half (%s)' % str(e).splitlines()[0][:200])
    sys.exit(0)

tpl = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
html = call_mode.build_html([], 0, {'ct': 'x', 'k': 'y'}, 'built', 'sig', 'bsig',
                            sync_js=call_mode.extract_sync_js(tpl), funnel_js=call_mode.extract_funnel_js(tpl),
                            text_js=call_mode.extract_text_js(tpl),
                            histcov='{"ledgers_ok": true, "why": "", "capped": false, "total": 6, "shipped": 6}')
path = os.path.join(_T, 'call.html')
open(path, 'w', encoding='utf-8').write(html)
C0, C1, C2 = NOPH[0]['case'], NOPH[1]['case'], NOPH[2]['case']

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None)
    ctx = b.new_context(viewport={'width': 390, 'height': 844})
    pg = ctx.new_page()
    errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)))
    pg.goto('file://' + path)
    pg.wait_for_timeout(400)
    a = pg.evaluate("""([rows, cov, c0, c1, c2]) => {
      const o = {};
      try { localStorage.clear(); } catch(e) {} notes = {};
      ROWS = JSON.parse(JSON.stringify(rows)); COV = JSON.parse(JSON.stringify(cov));
      start();
      BLANE = '__lk'; SCREEN = 'lead'; render();
      const app = () => document.getElementById('app');
      const cards = () => Array.from(app().querySelectorAll('.lkcard')).map(x => x.getAttribute('data-lkc'));
      const topN = () => { const t = document.querySelector('[data-lk]'); return t ? +(t.textContent.match(/(\\d+)\\s*$/) || [0, -1])[1] : -1; };
      o.n0 = lkRows().length; o.top0 = topN();
      o.btns = [app().querySelectorAll('button.lkvac').length, app().querySelectorAll('button.lkaddress').length, cards().length];
      o.copyStr = (app().querySelector('.lkcard[data-lkc="' + c0 + '"] button.wpcopy') || {getAttribute(){ return ''; }}).getAttribute('data-addr');
      o.vmList = !!document.getElementById('vmquick');
      /* VACANT */
      app().querySelector('button.lkvac[data-c="' + c0 + '"]').click();
      o.afterVac = {n: lkRows().length, top: topN(), onList: cards().indexOf(c0) >= 0, vac: lkVacantRows().map(r => r.c), skip: lkSkippedRows().map(r => r.c)};
      const t = (notes[c0].touches || []).slice(-1)[0] || {};
      o.touch = {ch: t.ch, on: t.vac && t.vac.on, a: t.vac && t.vac.a, dials: (notes[c0].dials || []).length, status: notes[c0].status || ''};
      o.vacBtnText = document.getElementById('lkvacant').textContent;
      /* NOT ON WHITEPAGES */
      app().querySelector('button.lkaddress[data-c="' + c1 + '"]').click();
      o.afterSkip = {n: lkRows().length, onList: cards().indexOf(c1) >= 0, skip: lkSkippedRows().map(r => r.c), vac: lkVacantRows().map(r => r.c)};
      /* a skipped lead that turns out vacant is vacant, not both */
      document.getElementById('lkskipped').click();
      o.skipView = {cards: cards(), restore: (app().querySelector('button.lkaddress') || {}).textContent || ''};
      app().querySelector('button.lkvac[data-c="' + c1 + '"]').click();
      o.both = {skip: lkSkippedRows().map(r => r.c), vac: lkVacantRows().map(r => r.c).sort()};
      /* the Vacant list */
      LKSKIPPED = false; LKVACANT = false; render();
      document.getElementById('lkvacant').click();
      o.vacView = {cards: cards().sort(), on: LKVACANT, copyBtn: (document.getElementById('lkvaccopy') || {}).textContent || '',
                   skipBtnOnCard: app().querySelectorAll('.lkcard button.lkaddress').length,
                   restoreText: (app().querySelector('button.lkvac') || {}).textContent || ''};
      let copied = null;
      try { Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: s => { copied = s; return Promise.resolve(); }, readText: () => Promise.resolve('')}}); } catch(e) {}
      lkVacantCopy();
      o.copied = copied;
      _lkSaveState();
      o.notesJson = JSON.stringify(notes);
      return o; }""", [rows, cov, C0, C1, C2])

    pg.goto('file://' + path)
    pg.wait_for_timeout(400)
    r = pg.evaluate("""([rows, cov, c0, c1, notesJson]) => {
      const o = {};
      notes = JSON.parse(notesJson);
      ROWS = JSON.parse(JSON.stringify(rows)); COV = JSON.parse(JSON.stringify(cov));
      start();
      const app = () => document.getElementById('app');
      const cards = () => Array.from(app().querySelectorAll('.lkcard')).map(x => x.getAttribute('data-lkc'));
      o.landed = {blane: BLANE, vacant: LKVACANT, cards: cards().sort()};
      o.kept = {n: lkRows().length, vac: lkVacantRows().map(r => r.c).sort()};
      if(BLANE !== '__lk'){ BLANE = '__lk'; SCREEN = 'lead'; LKVACANT = true; render(); }
      /* restore one */
      app().querySelector('button.lkvac[data-c="' + c0 + '"]').click();
      o.restored = {vac: lkVacantRows().map(r => r.c), back: lkRows().map(r => r.c).indexOf(c0) >= 0};
      /* c1 was skipped, then vacant: taking the vacant mark off leaves it skipped until restored */
      app().querySelector('button.lkvac[data-c="' + c1 + '"]').click();
      o.c1 = {skip: lkSkippedRows().map(r => r.c), list: lkRows().map(r => r.c).indexOf(c1) >= 0};
      o.emptyVac = app().textContent.indexOf('No lead is marked vacant') >= 0;
      document.getElementById('lkvacant').click();        // back to prospects
      document.getElementById('lkskipped').click();
      app().querySelector('button.lkaddress[data-c="' + c1 + '"]').click();
      o.c1back = {skip: lkSkippedRows().length, list: lkRows().map(r => r.c).indexOf(c1) >= 0};
      /* a filter button leaves the set-aside view */
      (document.querySelector('button.lkf[data-eq="0"]') || {click(){}}).click();
      o.filterLeaves = !LKSKIPPED && !LKVACANT;
      /* voicemail button on the outcome screen of a dial lead */
      BLANE = null; SCREEN = 'lead'; i = 0; render();
      o.vmCard = !!document.getElementById('vmquick');
      return o; }""", [rows, cov, C0, C1, a['notesJson']])
    b.close()

rec('every card has a Vacant button and a Not on Whitepages button', a['btns'][0] == a['btns'][2] == a['btns'][1] and a['btns'][2] >= 4, a['btns'])
rec('the copied address spells the unit with a #', a['copyStr'] == '9327 Wedgewood Dr # E10, Tamarac, FL 33321', a['copyStr'])
rec('Vacant takes the lead off the calling list and the top-bar count drops by one',
    a['afterVac']['n'] == a['n0'] - 1 and not a['afterVac']['onList'] and a['afterVac']['top'] == a['top0'] - 1, (a['n0'], a['top0'], a['afterVac']))
rec('...into the Vacant list, not the skipped list', a['afterVac']['vac'] == [C0] and a['afterVac']['skip'] == [], a['afterVac'])
rec('...as a lookup touch naming the address: no dial counted, no status changed',
    a['touch']['ch'] == 'lookup' and a['touch']['on'] is True and 'WEDGEWOOD' in (a['touch']['a'] or '').upper()
    and a['touch']['dials'] == 0 and a['touch']['status'] == '', a['touch'])
rec('the Vacant button at the top counts it', '1' in a['vacBtnText'], a['vacBtnText'])
rec('Not on Whitepages takes the lead off the list into Show skipped',
    a['afterSkip']['n'] == a['n0'] - 2 and not a['afterSkip']['onList'] and a['afterSkip']['skip'] == [C1] and a['afterSkip']['vac'] == [C0], a['afterSkip'])
rec('Show skipped lists it with a Restore button', a['skipView']['cards'] == [C1] and 'Restore' in a['skipView']['restore'], a['skipView'])
rec('a skipped lead marked vacant is vacant only', a['both']['skip'] == [] and a['both']['vac'] == sorted([C0, C1]), a['both'])
rec('the Vacant list shows both, with Copy all and no skip button', a['vacView']['cards'] == sorted([C0, C1]) and a['vacView']['on']
    and 'Copy all 2' in a['vacView']['copyBtn'] and a['vacView']['skipBtnOnCard'] == 0 and 'Not vacant' in a['vacView']['restoreText'], a['vacView'])
rec('Copy all gives one search-bar address per line', (a['copied'] or '').split('\n') and len((a['copied'] or '').split('\n')) == 2
    and all(' # E1' in x and x.endswith('FL 33321') for x in (a['copied'] or '').split('\n')), a['copied'])
rec('after a reload the marks are still there and he is back on the Vacant list',
    r['kept']['vac'] == sorted([C0, C1]) and r['kept']['n'] == a['n0'] - 2 and r['landed']['blane'] == '__lk' and r['landed']['vacant']
    and r['landed']['cards'] == sorted([C0, C1]), r)
rec('one tap restores a vacant lead to the calling list', r['restored']['back'] and r['restored']['vac'] == [C1], r['restored'])
rec('a lead that was skipped before it was vacant goes back to skipped', r['c1']['skip'] == [C1] and not r['c1']['list'], r['c1'])
rec('an empty Vacant list says so', r['emptyVac'], r)
rec('Restore prospect brings a skipped lead back', r['c1back']['skip'] == 0 and r['c1back']['list'], r['c1back'])
rec('a filter button leaves the set-aside view', r['filterLeaves'], r)
rec('the voicemail script button is on the lead card', r['vmCard'], r)
rec('no script error', not errs, errs)
print('\n%d failure(s)' % len(FAILS) if FAILS else '\nall passed')
sys.exit(1 if FAILS else 0)
