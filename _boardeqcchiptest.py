"""B2 (F-site-08): the do-not-quote equity chip reaches the desktop row and the call sheet.

`_eqcChip(r)` renders "Equity: <state> . may quote / do not quote" from the baked `r.eqc`. It was called
only in the mobile card. A lead with `eqc: {state:'unpriced', may_quote:false}` therefore showed its
BUY / "in $655k" figures in the desktop row and `Money: value ... net est $369,000` on the call sheet with
no "do not quote" label. Display only: no number changes, only how it is labelled.

This test builds its own board from INVENTED rows (2099-* cases, 555 numbers, example.com), runs it in the
installed Chrome with every non-fixture request refused, and a fake /health. Three leads:

  noq    eqc {state:'unpriced', may_quote:false}, eqstate 'unpriced'
  ok     eqc {state:'priced',   may_quote:true, net:369000}, eqstate 'priced'
  noeqc  no eqc at all

At 1440 px:
  (a) the noq desktop row carries the chip text "do not quote" exactly once
  (b) the noq call sheet carries the chip near the top, and its net-est line reads as a ceiling
  (c) the ok lead shows "may quote", no ceiling wording, and the same $369,000 net figure
  (d) the noeqc lead has no chip and a plain net est (unchanged)
At 390 px:
  (e) the mobile card still shows exactly one chip per lead that has an eqc (no doubling) and the
      desktop row is not built at that width.

  python _boardeqcchiptest.py          (exit 0 = all pass, 1 = a failure, 0 + SKIP = no browser)

BOARDEQC_TEMPLATE=<file> builds the board from another copy of the template (a candidate patch).
"""
import copy
import datetime as dt
import functools
import http.server
import json
import os
import re
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix='boardeqc_')
os.environ['DEALFLOW_DIR'] = os.path.join(TMP, 'df')
os.makedirs(os.environ['DEALFLOW_DIR'], exist_ok=True)
os.chdir(HERE)
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import foreclosure_leads as F      # noqa: E402
import disclaimer                  # noqa: E402

RESULTS = []


def rec(name, ok, detail=''):
    RESULTS.append((name, bool(ok), detail))
    print(('PASS ' if ok else 'FAIL ') + name + (('  [' + str(detail) + ']') if detail else ''))


# ---------------------------------------------------------------- fixture (invented rows only)
def build_fixture():
    src = open(os.path.join(HERE, 'build_preview.py'), encoding='utf-8').read()
    head = src.split('PREVIEW_EMPTY = {')[0]
    ns = {'__name__': 'bp', '__file__': os.path.join(HERE, 'build_preview.py')}
    exec(compile(head, 'bp_head', 'exec'), ns)
    base = copy.deepcopy(ns['FAKE'][0])          # value 655000, judg 286000 -> net 369000
    base['photos'] = []
    base['photo_kind'] = ''
    base['phones'] = ['3055550101']
    base['phdnc'] = [False]
    base['phtype'] = ['mobile']
    base['phsrc'] = ['st']
    base['emails'] = ['tester@example.com']
    base['ordeeded'] = False
    base.pop('eqc', None)
    base.pop('eqstate', None)
    SPEC = [
        ('noq', {'eqc': {'state': 'unpriced', 'may_quote': False, 'label': 'Ceiling only',
                         'why': 'liens not verified (test)'}, 'eqstate': 'unpriced'}),
        ('ok', {'eqc': {'state': 'priced', 'may_quote': True, 'net': 369000, 'label': 'Verified, priced',
                        'why': 'priced (test)'}, 'eqstate': 'priced'}),
        ('noeqc', {}),
    ]
    rows, cases = [], {}
    for i, (k, ov) in enumerate(SPEC):
        r = copy.deepcopy(base)
        r.update(ov)
        r['case'] = '2099-%06d-CA-01' % (800 + i)
        r['owners'] = 'TESTPERSON%d QA' % i
        r['oname'] = 'Testperson%d' % i
        r['addr'] = '%d EXAMPLE STREET, EXAMPLE CITY, FL 33100' % (200 + i)
        r['mail'] = r['addr']
        r['pkey'] = 'pk%d' % i
        r['auction'] = (dt.date.today() + dt.timedelta(days=20)).strftime('%m/%d/%Y')
        r['days'] = 20
        rows.append(r)
        cases[k] = r['case']
    tpl = F.subst_build_facts(open(os.environ.get('BOARDEQC_TEMPLATE') or os.path.join(HERE, 'tracker_template.html'),
                              encoding='utf-8').read(),
                              '2026-11-08 12:00')
    tpl = tpl.replace('__MOTIONJS__', F._motion_js())
    for t, v in {'__OPTOUTS__': '[]', '__REPLIES__': '{}', '__MAILLOG__': '{}', '__MAILTO__': '{}',
                 '__TEXTLOG__': '{}', '__TEXTPERSON__': '{}', '__DEADS__': '{}', '__ZIPCENT__': '{}',
                 '__BUILT__': 'test', '__BUILTAT__': '2026-11-08T12:00',
                 '__IDENT_EN__': F._esc_js(disclaimer.identity('en', as_html=False)),
                 '__IDENT_ES__': F._esc_js(disclaimer.identity('es', as_html=False))}.items():
        tpl = tpl.replace(t, v)
    tpl = F._bake_alex_email(tpl, 'eqc-test')
    html = tpl.replace('__DATA__', F._esc_json(rows))
    F.assert_no_placeholders(html, 'eqc-test')
    site = os.path.join(TMP, 'site')
    os.makedirs(site, exist_ok=True)
    open(os.path.join(site, 'board.html'), 'w', encoding='utf-8').write(html)
    return site, cases, len(rows)


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


SITE, CASES, NROWS = build_fixture()
srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Quiet, directory=SITE))
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = 'http://127.0.0.1:%d' % srv.server_port

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print('SKIP _boardeqcchiptest: playwright is not installed')
    sys.exit(0)

HEALTH = {'ok': True, 'ready': True, 'text_hold': False, 'sent_today': 0, 'cap': 300}
posts = []

pw = sync_playwright().start()
try:
    browser = pw.chromium.launch(channel='chrome', headless=True)
except Exception as e:                                      # noqa: BLE001
    print('SKIP _boardeqcchiptest: no browser could start (%s)' % str(e)[:120])
    srv.shutdown()
    pw.stop()
    sys.exit(0)


def route(rt):
    req = rt.request
    if req.url.startswith(BASE + '/') and req.method == 'GET':
        return rt.continue_()
    if req.method != 'GET':
        posts.append((req.method, req.url[:80]))
    if req.method == 'GET' and '/health' in req.url:
        return rt.fulfill(status=200, content_type='application/json', body=json.dumps(HEALTH))
    return rt.fulfill(status=403, content_type='application/json', body='{"ok":false,"blocked":"qa_isolation"}')


def open_board(width):
    ctx = browser.new_context(viewport={'width': width, 'height': 900}, service_workers='block')
    ctx.set_default_timeout(8000)
    ctx.route('**/*', route)
    p = ctx.new_page()
    errs = []
    p.on('pageerror', lambda e: errs.append(str(e)[:200]))
    now = dt.datetime.now(dt.timezone.utc).replace(hour=16, minute=0, second=0, microsecond=0)
    p.clock.install(time=now)
    p.goto(BASE + '/board.html')
    p.wait_for_function("typeof openCallSheet==='function' && DATA.length==%d" % NROWS)
    p.wait_for_timeout(1500)
    return ctx, p, errs


# count only chips that belong to a lead (data-case ancestor); the call sheet modal is read separately
CHIPS = """()=>{ const o={}; document.querySelectorAll('.eqc').forEach(e=>{
  const h=e.closest('[data-case]'); if(!h || e.closest('#csmodal')) return;
  const k=h.dataset.case; (o[k]=o[k]||[]).push({tag:h.tagName, text:e.textContent.trim()}); }); return o; }"""
ROWTXT = """(c)=>{ const r=document.querySelector('tr[data-case="'+c+'"]'); return r?r.innerText.replace(/\\s+/g,' '):null }"""
SHEET = """(c)=>{ openCallSheet(c);
  const m=document.getElementById('csmodal');
  const chips=[...m.querySelectorAll('.eqc')].map(e=>e.textContent.trim());
  const first=m.querySelector('.eqc');
  const money=m.querySelector('.csdil-money');
  const t=m.innerText;
  const out={chips:chips, money:money?money.innerText.replace(/\\s+/g,' '):null, n:t.length,
             chipAt:first?t.indexOf(first.innerText):-1, moneyAt:money?t.indexOf('Money:'):-1,
             beforeMoney:!!(first&&money&&(first.compareDocumentPosition(money)&Node.DOCUMENT_POSITION_FOLLOWING)),
             beforeBoxes:!!(first&&m.querySelector('.csbox')&&(first.compareDocumentPosition(m.querySelector('.csbox'))&Node.DOCUMENT_POSITION_FOLLOWING))};
  m.style.display='none'; return out; }"""

# ======================================================== 1440 px: desktop row and call sheet
ctx, p, errs = open_board(1440)
chips = p.evaluate(CHIPS)
rowtxt = {k: p.evaluate(ROWTXT, c) for k, c in CASES.items()}
sheet = {k: p.evaluate(SHEET, c) for k, c in CASES.items()}
nq = len(re.findall(r'do not quote', rowtxt['noq'] or '', re.I))

rec('setup: three desktop rows drawn, no mobile cards', all(rowtxt.values()) and not p.evaluate("document.querySelectorAll('.card[data-case]').length"),
    {k: bool(v) for k, v in rowtxt.items()})
rec('(a) noq desktop row: chip text "do not quote" exactly once (text) and one chip element',
    nq == 1 and len(chips.get(CASES['noq'], [])) == 1, 'text matches=%d chips=%s' % (nq, chips.get(CASES['noq'])))
rec('(a) noq desktop row: the chip is the Ceiling only state',
    any('Ceiling only' in c['text'] for c in chips.get(CASES['noq'], [])), chips.get(CASES['noq']))

s = sheet['noq']
rec('(b) noq call sheet: exactly one chip, "do not quote"',
    len(s['chips']) == 1 and 'do not quote' in s['chips'][0], s['chips'])
rec('(b) noq call sheet: chip is near the top (before the Money line and the first box, in the first 1500 chars)',
    s['beforeMoney'] and s['beforeBoxes'] and 0 <= s['chipAt'] < 1500, 'chipAt=%s moneyAt=%s' % (s['chipAt'], s['moneyAt']))
m = s['money'] or ''
rec('(b) noq call sheet: net est is worded as a ceiling ("at most", "liens not verified") with the same figure',
    re.search(r'at most\s*\$369,000', m, re.I) and 'liens not verified' in m.lower(), m[:200])
rec('(b) noq call sheet: not a plain "net est $369,000"', not re.search(r'net est\s*\$369,000', m, re.I), m[:200])

rec('(c) ok desktop row: one "may quote" chip, no "do not quote"',
    len(chips.get(CASES['ok'], [])) == 1 and 'may quote' in chips[CASES['ok']][0]['text']
    and 'do not quote' not in (rowtxt['ok'] or '').lower(), chips.get(CASES['ok']))
s = sheet['ok']
m = s['money'] or ''
rec('(c) ok call sheet: one "may quote" chip near the top',
    len(s['chips']) == 1 and 'may quote' in s['chips'][0] and s['beforeMoney'], s['chips'])
rec('(c) ok call sheet: plain net est $369,000, no ceiling wording',
    re.search(r'net est\s*\$369,000', m, re.I) and not re.search(r'at most|liens not verified', m, re.I), m[:200])

rec('(d) noeqc desktop row: no chip', not chips.get(CASES['noeqc']), chips.get(CASES['noeqc']))
s = sheet['noeqc']
m = s['money'] or ''
rec('(d) noeqc call sheet: no chip, plain net est $369,000, no ceiling wording',
    not s['chips'] and re.search(r'net est\s*\$369,000', m, re.I) and not re.search(r'at most|liens not verified', m, re.I),
    '%s | %s' % (s['chips'], m[:160]))
rec('no page errors at 1440', not errs, errs[:2])
ctx.close()

# ======================================================== 390 px: mobile card, no doubling
ctx, p, errs = open_board(390)
chips = p.evaluate(CHIPS)
cards = p.evaluate("[...document.querySelectorAll('.card[data-case]')].map(c=>c.dataset.case)")
rows = p.evaluate("document.querySelectorAll('tr[data-case]').length")
rec('setup: 390 px draws three mobile cards and no desktop rows', len(cards) == NROWS and rows == 0, 'cards=%d rows=%d' % (len(cards), rows))
rec('(e) mobile card noq: exactly one chip, "do not quote"',
    len(chips.get(CASES['noq'], [])) == 1 and 'do not quote' in chips[CASES['noq']][0]['text']
    and chips[CASES['noq']][0]['tag'] == 'DIV', chips.get(CASES['noq']))
rec('(e) mobile card ok: exactly one chip, "may quote"',
    len(chips.get(CASES['ok'], [])) == 1 and 'may quote' in chips[CASES['ok']][0]['text'], chips.get(CASES['ok']))
rec('(e) mobile card noeqc: no chip', not chips.get(CASES['noeqc']), chips.get(CASES['noeqc']))
rec('no page errors at 390', not errs, errs[:2])
ctx.close()

rec('isolation: no /send, /text, /notes POST was attempted',
    not [u for m_, u in posts if re.search(r'/(send|text|notes|reply)', u)], '%d refused POST(s)' % len(posts))
browser.close()
pw.stop()
srv.shutdown()

bad = [n for n, ok, _ in RESULTS if not ok]
print('\n%d checks, %d failed' % (len(RESULTS), len(bad)))
for n in bad:
    print('  FAILED: ' + n)
sys.exit(1 if bad else 0)
