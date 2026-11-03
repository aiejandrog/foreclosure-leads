"""Call Mode: whose number is it (owner / relative / not sure), and logging calls to relatives (2026-11-03).

Alex: "I need a better system for logging the phone calls I am dialing on the relatives from
Whitepages but that should be marked down on the call mode." And: "we still mark it as one dial right?"

LEGAL REVIEW: calling relatives is not cleared by the attorney. With DEALFLOW_RELATIVE_DIAL unset the
page never dials a number tagged relative; it records a call placed on the phone. Checks:
- the switch bakes false by default and true only for DEALFLOW_RELATIVE_DIAL=1
- the bridge /notes merge unions notes.wpr by number, relative wins over not sure
- picking Relative shows the attorney warning and a "log a call" label
- a relative number goes to notes.wpr only (not wpm, not notes.phone), the card's button does not dial
  (href '#'), there is no redial, and the call is ONE dial row tagged rel 'r' with its outcome
- a relative call does not set "Contacted", is never texted, and the strip counts it as a subset
- the board merge unions wpr; another lead resets the choice to Owner
- with the switch on, the same number gets a real tel: link
Invented numbers (555-01xx). Chromium, file:// page.
Run: python _callrelativetest.py (the browser half skips without Chromium)
"""
import datetime as dt
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='relative_')
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')
os.environ.pop('DEALFLOW_RELATIVE_DIAL', None)
import call_mode  # noqa: E402
import send_server  # noqa: E402

FAILS = []
AUC = (dt.date.today() + dt.timedelta(days=29)).strftime('%m/%d/%Y')


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:400]) if extra and not ok else ''))
    if not ok:
        FAILS.append(name)


def lead(case, phones, **kw):
    d = {'case': case, 'county': 'MIAMI-DADE', 'st': 'FC', 'auction': AUC, 'days': 29, 'tier': 'B', 'score': 50,
         'value': 400000, 'owners': 'TEST OWNER ' + case, 'oname': 'Test Owner', 'addr': '100 NW 1 ST, MIAMI, FL 33101',
         'pkey': 'C' + case, 'phones': phones, 'phdnc': [False] * len(phones), 'phsrc': ['st'] * len(phones),
         'phtype': ['mobile'] * len(phones)}
    d.update(kw)
    return d


rec('switch is off by default', call_mode._relative_dial_on() is False)
os.environ['DEALFLOW_RELATIVE_DIAL'] = '1'
rec('switch is on only for DEALFLOW_RELATIVE_DIAL=1', call_mode._relative_dial_on() is True)
os.environ['DEALFLOW_RELATIVE_DIAL'] = 'yes'
rec('any other value stays off', call_mode._relative_dial_on() is False)
os.environ.pop('DEALFLOW_RELATIVE_DIAL', None)

m, _ = send_server._merge_note({'wpr': [{'n': '3055550190', 'k': 'u', 'ts': 1}]},
                               {'wpr': [{'n': '(305) 555-0190', 'k': 'r', 'ts': 2}, {'n': '3055550191', 'k': 'u'},
                                        {'n': '12', 'k': 'r'}, {'n': '3055550192', 'k': 'x'}]})
rec('bridge merge: wpr union by number, relative wins, junk dropped',
    [(x['n'], x['k']) for x in m.get('wpr', [])] == [('3055550190', 'r'), ('3055550191', 'u')], m.get('wpr'))
m2, _ = send_server._merge_note({'wpr': [{'n': '3055550190', 'k': 'r'}]}, {'status': 'Contacted'})
rec('bridge merge: a push without wpr keeps the stored tags', [x['n'] for x in m2.get('wpr', [])] == ['3055550190'], m2)

A = lead('2026-000931-CA-01', ['3055550101', '3055550102'])
B = lead('2026-000932-CA-01', ['3055550111'])
SLIM = [A, B]
rows, _ = call_mode.call_rows(SLIM, None, None, lookup=[])
cov, _ = call_mode.coverage_rows(SLIM, [r['c'] for r in rows])

try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as _p:
        _p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None).close()
except Exception as e:
    print('SKIP browser half (Chromium will not launch: %s)' % str(e).splitlines()[0][:200])
    sys.exit(1 if FAILS else 0)

tpl = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()


def page(reldial):
    if reldial:
        os.environ['DEALFLOW_RELATIVE_DIAL'] = '1'
    else:
        os.environ.pop('DEALFLOW_RELATIVE_DIAL', None)
    html = call_mode.build_html([], 0, {'ct': 'x', 'k': 'y'}, 'built', 'sig', 'bsig',
                                sync_js=call_mode.extract_sync_js(tpl), funnel_js=call_mode.extract_funnel_js(tpl),
                                text_js=call_mode.extract_text_js(tpl),
                                mail_js=call_mode.bake_mail_js(call_mode.extract_mail_js(tpl), 'relative test'),
                                histcov='{"ledgers_ok": true, "why": "", "capped": false, "total": 2, "shipped": 2}')
    os.environ.pop('DEALFLOW_RELATIVE_DIAL', None)
    path = os.path.join(_T, 'call_%d.html' % reldial)
    open(path, 'w', encoding='utf-8').write(html)
    return path


JS = """async ([rows, cov, A, B]) => {
  try { localStorage.clear(); } catch(e) {}
  notes = {}; ROWS = rows; COV = cov;
  start();
  flClock = function(){ return {ok:true, txt:'noon'}; };
  const o = {};
  window._tapShieldUntil = 0;
  _openCase(A);
  o.reldial = RELDIAL;
  o.chips = document.querySelectorAll('#pck button[data-k]').length;
  o.ownerOn = (document.querySelector('#pck button.on')||{}).textContent;
  document.querySelector('#pck button[data-k="r"]').click();
  o.warn = (document.getElementById('pckw')||{}).textContent || '';
  const inp = document.getElementById('pcnum');
  inp.value = '(305) 555-0190'; inp.dispatchEvent(new Event('input'));
  o.goLabel = document.getElementById('pcgo').textContent;
  const d0 = ((notes[A]||{}).dials||[]).length;
  document.getElementById('pcgo').click();
  await new Promise(res => setTimeout(res, 40));
  o.screen = SCREEN;
  o.wpr = JSON.stringify((notes[A].wpr||[]).map(e => [e.n, e.k]));
  o.wpm = (notes[A].wpm||[]).indexOf('3055550190');
  o.phone = notes[A].phone || '';
  o.redial = !!document.getElementById('redial');
  const dl = notes[A].dials || [];
  o.pending = dl.length - d0 + '|' + (dl.slice(-1)[0]||{}).oc + '|' + (dl.slice(-1)[0]||{}).rel;
  document.querySelector('.oc button[data-oc="talked"]').click();
  await new Promise(res => setTimeout(res, 40));
  const dl2 = notes[A].dials || [];
  o.logged = dl2.length - d0 + '|' + (dl2.slice(-1)[0]||{}).oc + '|' + (dl2.slice(-1)[0]||{}).rel;
  o.touch = ((notes[A].touches||[]).slice(-1)[0]||{}).rel;
  o.status = notes[A].status || '';
  o.after = SCREEN;
  /* this page has no bridge, so texting is held; release it here to reach the number gate */
  textingHeld = function(){ return false; };
  afterCall(cur, OUTCOMES.find(x => x.k === 'talked'), null);
  o.tx = !!document.getElementById('tx');
  o.txNote = /never texted/.test(document.getElementById('app').innerHTML);
  o.strip = (document.getElementById('sess')||{}).textContent || '';
  // the card for that number: log-only button
  SCREEN = 'lead'; _openCase(A);
  const k = cur.p.map(x => String(x).replace(/\\D/g,'')).indexOf('3055550190');
  phIdx = k; screenLead();
  const dial = document.getElementById('dial');
  o.cardHref = dial.getAttribute('href');
  o.cardText = dial.textContent;
  o.cardNote = /RELATIVE of the owner/.test(document.getElementById('app').innerHTML);
  o.chip = /relative &middot; legal review|relative · legal review/.test(document.getElementById('app').innerHTML);
  // an owner dial still counts and is not tagged
  phIdx = 0; screenLead();
  o.ownerHref = (document.getElementById('dial').getAttribute('href')||'').slice(0, 4);
  // another lead resets the choice to Owner
  _openCase(B);
  o.resetB = (document.querySelector('#pck button.on')||{}).textContent;
  // board merge
  const mg = _mergeLead({wpr:[{n:'3055550190', k:'u'}]}, {wpr:[{n:'3055550190', k:'r'}, {n:'3055550199', k:'u'}]});
  o.merge = JSON.stringify(mg[0].wpr.map(e => [e.n, e.k]));
  return o; }"""

JS_ON = """async ([rows, cov, A]) => {
  try { localStorage.clear(); } catch(e) {}
  notes = {}; ROWS = rows; COV = cov;
  notes[A] = {status:'', note:'', wpr:[{n:'3055550190', k:'r', ts:1}]};
  start(); applyPasted();
  flClock = function(){ return {ok:true, txt:'noon'}; };
  _openCase(A);
  const k = cur.p.map(x => String(x).replace(/\\D/g,'')).indexOf('3055550190');
  phIdx = k; screenLead();
  return {reldial: RELDIAL, k: k, href: (document.getElementById('dial').getAttribute('href')||'').slice(0, 4),
          note: /RELATIVE of the owner/.test(document.getElementById('app').innerHTML)}; }"""

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None)
    errs = []
    pg = b.new_page(viewport={'width': 390, 'height': 900})
    pg.on('pageerror', lambda e: errs.append(str(e)))
    pg.on('dialog', lambda d: d.accept())
    pg.goto('file://' + page(False))
    pg.wait_for_timeout(300)
    out = pg.evaluate(JS, [rows, cov, A['case'], B['case']])
    pg2 = b.new_page(viewport={'width': 390, 'height': 900})
    pg2.on('pageerror', lambda e: errs.append(str(e)))
    pg2.goto('file://' + page(True))
    pg2.wait_for_timeout(300)
    on = pg2.evaluate(JS_ON, [rows, cov, A['case']])
    b.close()

rec('page runs without a script error', not errs, errs)
rec('switch baked off', out['reldial'] is False, out['reldial'])
rec('paste box asks whose number: Owner / Relative / Not sure, Owner first',
    out['chips'] == 3 and out['ownerOn'] == 'Owner', (out['chips'], out['ownerOn']))
rec('picking Relative shows the attorney warning and says it will not dial',
    'not cleared by the attorney' in out['warn'] and 'will not dial' in out['warn'], out['warn'])
rec('go button says log, not call', out['goLabel'].startswith('Save & log a call to'), out['goLabel'])
rec('saved to notes.wpr as relative', out['wpr'] == '[["3055550190","r"]]', out['wpr'])
rec('not saved as an owner number (wpm) or the phone box', out['wpm'] == -1 and out['phone'] != '3055550190', (out['wpm'], out['phone']))
rec('outcome screen opens, no redial link', out['screen'] == 'outcome' and not out['redial'], (out['screen'], out['redial']))
rec('the call is one pending dial row tagged rel r', out['pending'] == '1|pending|r', out['pending'])
rec('the outcome replaces it: still one dial, tagged', out['logged'] == '1|talked|r', out['logged'])
rec('the touch is tagged rel r', out['touch'] == 'r', out['touch'])
rec('talking to a relative does not mark the owner Contacted', out['status'] != 'Contacted', out['status'])
rec('after-call: no text button, says never texted', out['after'] == 'after' and not out['tx'] and out['txNote'], out)
rec('strip counts it as a dial and as the relative subset',
    'TODAY: 1' in out['strip'] and '1 to relatives' in out['strip'], out['strip'])
rec('card for the relative number: button logs, does not dial',
    out['cardHref'] == '#' and 'Log a call you placed' in out['cardText'], (out['cardHref'], out['cardText']))
rec('card says relative + legal review', out['cardNote'] and out['chip'], (out['cardNote'], out['chip']))
rec('an owner number still dials', out['ownerHref'] == 'tel:', out['ownerHref'])
rec('another lead resets the choice to Owner', out['resetB'] == 'Owner', out['resetB'])
rec('board merge: wpr union, relative wins', out['merge'] == '[["3055550190","r"],["3055550199","u"]]', out['merge'])
rec('switch on: the relative number gets a real tel: link and keeps the warning',
    on['reldial'] is True and on['k'] >= 0 and on['href'] == 'tel:' and on['note'], on)
print('\n%d failure(s)' % len(FAILS) if FAILS else '\nall passed')
sys.exit(1 if FAILS else 0)
