"""B1 (F-site-01, F-site-02, F-ideas-01): the board's one "must not be written to" verdict reaches
every written channel on three board surfaces.

`_boardMailHeld(r)` is the board's verdict for "this lead must not get an email": existing client,
diligence hold, claimed on another case, owner gone / transferred, plus everything
`_textContactBlocked` returns. Three surfaces did not ask it:

  (a) the text composer (`textCardHtml`) enabled Send on a client / ddhold / sibclaimed / dead /
      transferred lead,
  (b) the call sheet (`_contactLineHtml`, `_contactBlockHtml`) showed Text, WA and a plain mailto: link,
  (c) the Reply inbox card showed a Call button and a mailto: on every card, STOP ones included.

This test builds its own board from INVENTED rows (2099-* cases, 555 numbers, example.com), runs it in
the installed Chrome with every non-fixture request refused, and a fake /health with text_hold false
at 11:00 ET so the FTSA hours gate is open and a missing block can only be the hold under test.

Nothing here edits, reads or asserts a suppression function's body: the optout and stay leads are
only observed, to prove their behaviour did not move.

  python _boardmanualheldtest.py          (exit 0 = all pass, 1 = a failure, 0 + SKIP = no browser)

BOARDHELD_TEMPLATE=<file> builds the board from another copy of the template (a candidate patch).
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
TMP = tempfile.mkdtemp(prefix='boardheld_')
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
    base = copy.deepcopy(ns['FAKE'][0])
    base['photos'] = []
    base['photo_kind'] = ''
    base['phones'] = ['3055550101', '7865550102']
    base['phdnc'] = [False, False]
    base['phtype'] = ['mobile', 'mobile']
    base['phsrc'] = ['st', 'st']
    base['emails'] = ['tester@example.com']
    base['ordeeded'] = False
    HELD = [
        ('control', {}),
        ('optout', {}),                      # DO NOT CONTACT note injected in the browser
        ('stay', {'saleBkAct': True}),
        ('client', {'client': True}),
        ('ddhold', {'ddhold': True, 'ddwhy': 'diligence hold (test)'}),
        ('sibclaimed', {'sibclaimed': True}),
        ('dead', {'ordeeded': True, 'ordeedconf': 'ok'}),   # isFlaggedDead -> 'owner gone'
        ('transferred', {'title_status': 'transferred'}),
        ('stopreply', {}),                   # no hold of its own; its reply says STOP
        ('deadstatus', {}),                  # hand status 'Dead' only: observed, not asserted (see below)
    ]
    rows, cases = [], {}
    for i, (k, ov) in enumerate(HELD):
        r = copy.deepcopy(base)
        r.update(ov)
        r['case'] = '2099-%06d-CA-01' % (900 + i)
        r['owners'] = 'TESTPERSON%d QA' % i
        r['oname'] = 'Testperson%d' % i
        r['addr'] = '%d EXAMPLE STREET, EXAMPLE CITY, FL 33100' % (100 + i)
        r['mail'] = r['addr']
        r['pkey'] = 'pk%d' % i
        r['auction'] = (dt.date.today() + dt.timedelta(days=20)).strftime('%m/%d/%Y')
        r['days'] = 20
        rows.append(r)
        cases[k] = r['case']
    tpl = F.subst_build_facts(open(os.environ.get('BOARDHELD_TEMPLATE') or os.path.join(HERE, 'tracker_template.html'),
                              encoding='utf-8').read(),
                              '2026-11-08 12:00')
    tpl = tpl.replace('__MOTIONJS__', F._motion_js())
    for t, v in {'__OPTOUTS__': '[]', '__REPLIES__': '{}', '__MAILLOG__': '{}', '__MAILTO__': '{}',
                 '__TEXTLOG__': '{}', '__TEXTPERSON__': '{}', '__DEADS__': '{}', '__ZIPCENT__': '{}',
                 '__BUILT__': 'test', '__BUILTAT__': '2026-11-08T12:00',
                 '__IDENT_EN__': F._esc_js(disclaimer.identity('en', as_html=False)),
                 '__IDENT_ES__': F._esc_js(disclaimer.identity('es', as_html=False))}.items():
        tpl = tpl.replace(t, v)
    tpl = F._bake_alex_email(tpl, 'held-test')
    html = tpl.replace('__DATA__', F._esc_json(rows))
    F.assert_no_placeholders(html, 'held-test')
    site = os.path.join(TMP, 'site')
    os.makedirs(site, exist_ok=True)
    open(os.path.join(site, 'board.html'), 'w', encoding='utf-8').write(html)
    return site, cases, len(rows)


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


SITE, CASES, NROWS = build_fixture()
INV = {v: k for k, v in CASES.items()}
srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Quiet, directory=SITE))
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = 'http://127.0.0.1:%d' % srv.server_port

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print('SKIP _boardmanualheldtest: playwright is not installed')
    sys.exit(0)

HEALTH = {'ok': True, 'ready': True, 'text_hold': False, 'sent_today': 0, 'cap': 300}
HELD_FIVE = ['client', 'ddhold', 'sibclaimed', 'dead', 'transferred']
posts, blocked = [], []

pw = sync_playwright().start()
try:
    browser = pw.chromium.launch(channel='chrome', headless=True)
except Exception as e:                                      # noqa: BLE001
    print('SKIP _boardmanualheldtest: no browser could start (%s)' % str(e)[:120])
    srv.shutdown()
    pw.stop()
    sys.exit(0)


def route(rt):
    req = rt.request
    if req.url.startswith(BASE + '/') and req.method == 'GET':
        return rt.continue_()
    blocked.append((req.method, req.url[:80]))
    if req.method != 'GET':
        posts.append((req.method, req.url[:80]))
    if req.method == 'GET' and '/health' in req.url:
        return rt.fulfill(status=200, content_type='application/json', body=json.dumps(HEALTH))
    return rt.fulfill(status=403, content_type='application/json', body='{"ok":false,"blocked":"qa_isolation"}')


def open_board(notes):
    ctx = browser.new_context(viewport={'width': 1440, 'height': 900}, service_workers='block')
    ctx.set_default_timeout(8000)
    ctx.route('**/*', route)
    p = ctx.new_page()
    errs = []
    p.on('pageerror', lambda e: errs.append(str(e)[:200]))
    p.add_init_script("localStorage.setItem('fcLeadNotes', %s)" % json.dumps(json.dumps(notes)))
    # 11:00 ET on a November day (EST, UTC-5) so _withinTextHours() is open
    now = dt.datetime.now(dt.timezone.utc).replace(hour=16, minute=0, second=0, microsecond=0)
    p.clock.install(time=now)
    p.goto(BASE + '/board.html')
    p.wait_for_function("typeof openTextSingle==='function' && typeof genReplyInbox==='function' && DATA.length==%d" % NROWS)
    p.wait_for_timeout(1500)
    return ctx, p, errs


now_utc = dt.datetime.now(dt.timezone.utc)
iso = lambda d: (now_utc - dt.timedelta(days=d)).strftime('%Y-%m-%dT%H:%M:%S')

DNC_NOTE = {CASES['optout']: {'status': 'DO NOT CONTACT', 'optout': True},
            CASES['deadstatus']: {'status': 'Dead', 'note': ''}}

# ======================================================== A. composer and call sheet (no replies)
ctx, p, errs = open_board(DNC_NOTE)
rec('setup: hours gate open and no text hold', p.evaluate("_withinTextHours()") is True
    and not p.evaluate("TEXT_HOLD"), 'hoursOK=%s hold=%r' % (p.evaluate("_withinTextHours()"), p.evaluate("TEXT_HOLD")))

COMPOSER = """(c)=>{ openTextSingle(DATA.find(x=>x.case===c));
  const m=document.getElementById('textmodal');
  const o={send:m.querySelectorAll('a.txsend').length, wa:m.querySelectorAll('a.txwa').length,
           tones:m.querySelectorAll('.txtone').length, copy:m.querySelectorAll('.txcopy').length,
           gate:[...m.querySelectorAll('.txblock')].map(e=>e.textContent)};
  closeTextModal(); return o; }"""
SHEET = """(c)=>{ openCallSheet(c);
  const m=document.getElementById('csmodal');
  return {text:m.querySelectorAll('a.ctact-text').length, wa:m.querySelectorAll('.ctact-wa').length,
          mail:m.querySelectorAll('a[href^="mailto:"]').length, call:m.querySelectorAll('a.ctact-call').length}; }"""

comp = {k: p.evaluate(COMPOSER, c) for k, c in CASES.items()}
sheet = {k: p.evaluate(SHEET, c) for k, c in CASES.items()}

# (a) composer on the five held leads
for k in HELD_FIVE:
    o = comp[k]
    rec('(a) composer %s: no enabled Send, no WA, no tone buttons, no Copy' % k,
        o['send'] == 0 and o['wa'] == 0 and o['tones'] == 0 and o['copy'] == 0, o)
    rec('(a) composer %s: a block sentence is shown' % k,
        any('Do not text' in g for g in o['gate']), o['gate'])

# (b) call sheet on the five held leads: no Text, no WA, no mailto; Call anchor unchanged
for k in HELD_FIVE:
    o = sheet[k]
    rec('(b) call sheet %s: no Text, no WA, no mailto' % k,
        o['text'] == 0 and o['wa'] == 0 and o['mail'] == 0, o)
    rec('(b) call sheet %s: Call anchor still present (unchanged, scope proof)' % k,
        o['call'] >= 1, o)

# (d) control keeps everything
rec('(d) control composer: Send enabled, tones drawn, no block sentence',
    comp['control']['send'] >= 1 and comp['control']['tones'] >= 1 and not comp['control']['gate'], comp['control'])
rec('(d) control call sheet: Text, WA, mailto and Call all present',
    sheet['control']['text'] >= 1 and sheet['control']['wa'] >= 1 and sheet['control']['mail'] >= 1
    and sheet['control']['call'] >= 1, sheet['control'])

# (e) optout and stay unchanged
rec('(e) optout composer: 0 Send, existing OPTED OUT sentence',
    comp['optout']['send'] == 0 and any('OPTED OUT' in g for g in comp['optout']['gate']), comp['optout'])
rec('(e) stay composer: 0 Send, existing BANKRUPTCY STAY sentence',
    comp['stay']['send'] == 0 and any('BANKRUPTCY STAY' in g for g in comp['stay']['gate']), comp['stay'])
for k in ('optout', 'stay'):
    rec('(e) %s call sheet: no Text, no WA, no mailto, no Call (as before)' % k,
        sheet[k]['text'] == 0 and sheet[k]['wa'] == 0 and sheet[k]['mail'] == 0 and sheet[k]['call'] == 0, sheet[k])
# Observed only: a lead whose hand status is 'Dead' but which the records do not flag (isFlaggedDead)
# is not in _boardMailHeld's list, so the helper does not cover it. Reported to chief, not asserted.
print('INFO deadstatus (hand status Dead, no records flag): composer %s, call sheet %s' % (comp['deadstatus'], sheet['deadstatus']))
rec('(a-d) no page errors on the composer / call sheet board', not errs, errs[:2])
ctx.close()

# ======================================================== C. Reply inbox (every lead has replied)
notes = {c: {'status': '', 'note': '', 'replied': iso(3)} for k, c in CASES.items() if k != 'deadstatus'}
notes[CASES['optout']] = {'status': 'DO NOT CONTACT', 'optout': True, 'note': '', 'replied': iso(3)}
notes[CASES['stopreply']] = {'status': '', 'note': '', 'replied': iso(2), 'repliedStop': True}
ctx, p, errs = open_board(notes)
cards = p.evaluate("""()=>{ const d=new DOMParser().parseFromString(genReplyInbox(),'text/html');
  return [...d.querySelectorAll('.rbcard')].map(c=>{ const a=c.querySelector('a[href^="#case="]');
    return {case:a?a.getAttribute('href').slice(6):'', stop:c.classList.contains('stop'),
            call:c.querySelectorAll('.rbbtn.call').length, mail:c.querySelectorAll('a[href^="mailto:"]').length,
            open:c.querySelectorAll('a[href^="#case="]').length}; }); }""")
by = {INV.get(c['case'], c['case']): c for c in cards}
rec('(c) inbox: one card per replied lead', len(cards) == NROWS - 1, '%d cards' % len(cards))
for k in ('stopreply', 'optout', 'stay'):
    c = by.get(k) or {}
    rec('(c) inbox %s card: no Call button and no mailto, Open call sheet kept' % k,
        c.get('call') == 0 and c.get('mail') == 0 and c.get('open') == 1, c)
for k in ('client', 'ddhold', 'sibclaimed', 'dead', 'transferred'):
    c = by.get(k) or {}
    rec('(c) inbox %s card: no mailto, Open call sheet kept' % k,
        c.get('mail') == 0 and c.get('open') == 1, c)
    rec('(c) inbox %s card: Call button unchanged (scope proof)' % k, c.get('call') == 1, c)
c = by.get('control') or {}
rec('(d) inbox control card: still has Call and mailto', c.get('call') == 1 and c.get('mail') == 1, c)
rec('(c) inbox: the STOP card is the stopreply lead and is the only .stop card',
    [k for k, v in by.items() if v.get('stop')] == ['stopreply'], [k for k, v in by.items() if v.get('stop')])
rec('(c) no page errors on the Reply inbox board', not errs, errs[:2])
ctx.close()

# The call sheet of a held lead asks the bridge for diligence (POST /diligence); the fixture refuses
# every non-fixture request (403), so none reached a server. What must never be attempted is a send.
rec('isolation: every non-fixture request was refused; no /send, /text or /notes POST was attempted',
    not [u for m, u in posts if re.search(r'/(send|text|notes|reply)', u)],
    '%d refused POST(s): %s' % (len(posts), sorted(set(u.split(':8765')[-1] if ':' in u else u for m, u in posts))[:4]))
browser.close()
pw.stop()
srv.shutdown()

bad = [n for n, ok, _ in RESULTS if not ok]
print('\n%d checks, %d failed' % (len(RESULTS), len(bad)))
for n in bad:
    print('  FAILED: ' + n)
sys.exit(1 if bad else 0)
