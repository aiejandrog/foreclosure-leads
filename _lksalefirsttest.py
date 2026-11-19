"""_lksalefirsttest -- Find a number puts a sale in the next 14 days above the new filings.

Run:  python _lksalefirsttest.py   (exit 0 = safe; invented leads, no network)

WHY (2026-11-19, Alejandro: "where are the other leads? with a sale date and the ones that are about
to hit their sale?"). 405 lis pendens filed in the last 14 days sorted above every dated sale.
Pins: order = sale in 14 days (soonest) > new filing > later sale; the "Sale in 14 days" button
counts and shows only those; the card says "sale soon"; the filter survives a reload.
"""
import datetime as dt
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='salefirst_')
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')
import call_mode  # noqa: E402

FAILS = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:400]) if extra and not ok else ''))
    if not ok:
        FAILS.append(name)


def d(n):
    return (dt.date.today() + dt.timedelta(days=n)).strftime('%m/%d/%Y')


def lead(case, **kw):
    r = {'case': case, 'county': 'MIAMI-DADE', 'st': 'FC', 'tier': 'B', 'score': 50, 'value': 400000,
         'owners': 'TEST OWNER ' + case, 'oname': 'Test Owner', 'addr': '100 NW 1 ST, MIAMI, FL 33101', 'pkey': 'C' + case}
    r.update(kw)
    return r


SOON3 = lead('2026-000953-CA-01', auction=d(3), days=3)
SOON10 = lead('2026-000960-CA-01', auction=d(10), days=10)
LATER = lead('2026-000990-CA-01', auction=d(40), days=40)
NEWLP = lead('2026-000901-CA-01', st='LP', auction='', days=9999, filedDate=d(-2))
SLIM = [LATER, NEWLP, SOON10, SOON3]
sink = []
rows, _ = call_mode.call_rows(SLIM, None, None, lookup=sink)
cov, _ = call_mode.coverage_rows(SLIM, [r['c'] for r in rows])
call_mode.mark_lookup(cov, sink, {r['pk']: r['sb'] for r in rows})
lk = [c for c in cov if c.get('lk')]
rec('setup: four leads on Find a number', len(lk) == 4, [c['c'] for c in lk])

try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as _p:
        _p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None).close()
except Exception as e:
    print('SKIP browser half (%s)' % str(e).splitlines()[0][:200])
    sys.exit(1 if FAILS else 0)

tpl = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
html = call_mode.build_html([], 0, {'ct': 'x', 'k': 'y'}, 'built', 'sig', 'bsig',
                            sync_js=call_mode.extract_sync_js(tpl), funnel_js=call_mode.extract_funnel_js(tpl),
                            text_js=call_mode.extract_text_js(tpl),
                            histcov='{"ledgers_ok": true, "why": "", "capped": false, "total": 4, "shipped": 4}')
path = os.path.join(_T, 'call.html')
open(path, 'w', encoding='utf-8').write(html)

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None)
    pg = b.new_page(viewport={'width': 390, 'height': 900})
    errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)))
    pg.goto('file://' + path)
    pg.wait_for_timeout(400)
    out = pg.evaluate("""(cov) => {
      const o = {};
      try { localStorage.clear(); } catch(e) {}
      notes = {}; ROWS = []; COV = cov; start();
      BLANE = '__lk'; render();
      o.order = Array.from(document.querySelectorAll('.lkcard')).map(e => e.getAttribute('data-lkc'));
      const btn = document.querySelector('button.lkf[data-eq="2"]');
      o.btnText = btn ? btn.textContent : null;
      o.chip = document.querySelector('.lkcard').innerHTML.indexOf('sale soon') >= 0;
      btn.click();
      o.filtered = Array.from(document.querySelectorAll('.lkcard')).map(e => e.getAttribute('data-lkc'));
      o.saved = JSON.parse(localStorage.getItem(_LKK) || '{}').sale;
      LKSALE = false; _lkLoadState();
      o.restored = LKSALE;
      document.querySelector('button.lkf[data-eq="0"]').click();
      o.allAgain = document.querySelectorAll('.lkcard').length;
      return o; }""", lk)
    b.close()

rec('order: sale in 3 days, sale in 10 days, new filing, sale in 40 days',
    out['order'] == [SOON3['case'], SOON10['case'], NEWLP['case'], LATER['case']], out['order'])
rec('the "Sale in 14 days" button counts 2', out['btnText'] == 'Sale in 14 days 2', out['btnText'])
rec('the top card says "sale soon"', out['chip'], out)
rec('the button shows only the two sales this fortnight', out['filtered'] == [SOON3['case'], SOON10['case']], out['filtered'])
rec('the filter is saved and comes back on reload', out['saved'] == 1 and out['restored'] is True, out)
rec('All shows every lead again', out['allAgain'] == 4, out)
rec('no script error', not errs, errs)
print('\n%d failure(s)' % len(FAILS) if FAILS else '\nall passed')
sys.exit(1 if FAILS else 0)
