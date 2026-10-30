"""Call Mode layout (2026-10-30): the column fits the window at every width from 320 up.

Alex: "fix the screen proportionate so it's fixed to the actual contents and contexts."
Checks, in Chromium at 320..1920 (invented leads, file:// page, no network):
  - no sideways scroll (documentElement.scrollWidth <= clientWidth), also with a long unbroken
    token (an e-mail, a URL, a run-together name) in an address, owner and Find a number row
  - the tab row, lane row, counts line and every card stay inside the column's own box
  - the column is centred (equal side margins) and bounded (<= 880px)
  - from 640px up it fills >= 90% of the window until the 880px cap (main: 560px, 73% at 768)
  - a phone (<= 430px) keeps the full-width column it has today
Run: python _calllayouttest.py   (skips, exit 0, if Chromium will not launch)
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='calllayout_')
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')

import call_mode  # noqa: E402

FAILS = []
WIDTHS = [320, 360, 390, 430, 600, 768, 1024, 1280, 1440, 1920]
LONG = 'x' * 260 + '@' + 'y' * 120 + '.example.com'


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra and not ok else ''))
    if not ok:
        FAILS.append(name)


def lead(case, **kw):
    d = {'case': case, 'county': 'MIAMI-DADE', 'st': 'FC', 'auction': '11/20/2026', 'days': 29, 'tier': 'B',
         'score': 50, 'value': 400000, 'owners': 'TEST OWNER ' + case, 'oname': 'Test Owner',
         'addr': '100 NW 1 ST, MIAMI, FL 33101', 'pkey': 'C' + case, 'phones': ['3055550101'],
         'phdnc': [False], 'phsrc': ['st'], 'phtype': ['mobile']}
    d.update(kw)
    return d


NOPH = dict(phones=[], phdnc=[], phsrc=[], phtype=[])
SLIM = [lead('2026-000101-CA-01'), lead('2026-000102-CA-01', **NOPH),
        lead('2026-000103-CA-01', owners=LONG, oname=LONG, addr=LONG + ', MIAMI, FL 33101', **NOPH)]

try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as _p:
        _p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None).close()
except Exception as e:
    print('SKIP (Chromium will not launch: %s)' % str(e).splitlines()[0][:200])
    sys.exit(0)

rows, _ = call_mode.call_rows(SLIM, None, None)
cov, _ = call_mode.coverage_rows(SLIM, [r['c'] for r in rows])
tpl = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
html = call_mode.build_html([], 0, {'ct': 'x', 'k': 'y'}, 'built', 'sig', 'bsig',
                            sync_js=call_mode.extract_sync_js(tpl), funnel_js=call_mode.extract_funnel_js(tpl),
                            text_js=call_mode.extract_text_js(tpl),
                            histcov='{"ledgers_ok": true, "why": "", "capped": false, "total": 3, "shipped": 3}')
path = os.path.join(_T, 'call.html')
open(path, 'w', encoding='utf-8').write(html)

MEASURE = """() => {
  const de = document.documentElement, app = document.getElementById('app');
  const a = app.getBoundingClientRect();
  const inside = sel => { const out = [];
    document.querySelectorAll(sel).forEach(e => { const b = e.getBoundingClientRect();
      if (b.width && (b.right > a.right + 1 || b.left < a.left - 1)) out.push(sel + ' ' + Math.round(b.left) + '..' + Math.round(b.right)); });
    return out; };
  const cnt = [...document.querySelectorAll('.supn')].filter(e => /Counts/.test(e.textContent));
  const cbad = cnt.filter(e => { const b = e.getBoundingClientRect(); return b.right > a.right + 1 || e.scrollWidth > e.clientWidth + 1; }).length;
  return {over: de.scrollWidth - de.clientWidth, left: a.left, right: de.clientWidth - a.right, w: a.width,
          vw: de.clientWidth, outside: [].concat(inside('.dfnav'), inside('.lane'), inside('.lane button'),
          inside('button[data-lk]'), inside('.card'), inside('.sess')), cbad: cbad, cnt: cnt.length};
}"""

results = {}
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None)
    for w in WIDTHS:
        for view in ('all', 'lk'):
            pg = b.new_page(viewport={'width': w, 'height': 900})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.goto('file://' + path)
            pg.wait_for_timeout(250)
            pg.evaluate("""([rows, cov, view]) => { try { localStorage.clear(); } catch(e) {}
              notes = {}; ROWS = rows; COV = cov.map(c => Object.assign({}, c, {lk: 1, sb: 0})); start();
              if (view === 'lk') { BLANE = '__lk'; render(); } }""", [rows, cov, view])
            pg.wait_for_timeout(150)
            results[(w, view)] = (pg.evaluate(MEASURE), errs)
            pg.close()
    b.close()

for w in WIDTHS:
    for view in ('all', 'lk'):
        m, errs = results[(w, view)]
        tag = '%d/%s' % (w, view)
        rec(tag + ' no script error', not errs, errs[:1])
        rec(tag + ' no sideways scroll', m['over'] <= 0, 'doc overflows by %spx' % m['over'])
        rec(tag + ' tab row, lanes, cards and counts stay in the column', not m['outside'] and not m['cbad'],
            m['outside'][:3])
        rec(tag + ' column centred (equal side margins)', abs(m['left'] - m['right']) <= 1, (m['left'], m['right']))
        rec(tag + ' column bounded (<= 880px)', m['w'] <= 880.5, m['w'])
    m = results[(w, 'all')][0]
    if w >= 640:
        want = min(m['vw'], 880)
        rec('%d column fills >= 90%% of the room up to the 880px cap' % w, m['w'] >= 0.9 * want or m['w'] >= 879,
            '%spx of %spx' % (m['w'], m['vw']))
    if w <= 430:
        rec('%d phone keeps the full-width column' % w, m['w'] == m['vw'], (m['w'], m['vw']))

print('\n%d failure(s)' % len(FAILS) if FAILS else '\nall passed')
sys.exit(1 if FAILS else 0)
