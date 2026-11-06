"""Call Mode: "They picked up" -> talk time (2026-11-05). Real page in Chromium, file://, invented numbers.

Checks, on the outcome screen the dial tap opens:
- the button shows only while a dial row is open for this number today, and only once per call
- one tap stamps tsa on that open row, swaps the button for "On call mm:ss", and the readout ticks
  on the existing 1 s clkTick loop (not clocked in: it still ticks)
- a second tap cannot overwrite tsa
- tapping an answered outcome writes tsa + integer talk seconds on the outcome row
- an unanswered outcome after a pickup tap carries tsa and writes no talk
- a pickup tap stamped after the outcome tap (clock skew) writes no talk
- no pickup tap: the row has neither field (old behaviour, untimed)
- a redial brings the button back for the new call
Run: python _callpickuptest.py (the browser half skips without Chromium)
"""
import datetime as dt
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='pickup_')
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')
os.environ.pop('DEALFLOW_RELATIVE_DIAL', None)
import call_mode  # noqa: E402

FAILS = []
AUC = (dt.date.today() + dt.timedelta(days=29)).strftime('%m/%d/%Y')


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:400]) if extra and not ok else ''))
    if not ok:
        FAILS.append(name)


def lead(case, phones):
    return {'case': case, 'county': 'MIAMI-DADE', 'st': 'FC', 'auction': AUC, 'days': 29, 'tier': 'B', 'score': 50,
            'value': 400000, 'owners': 'TEST OWNER ' + case, 'oname': 'Test Owner', 'addr': '100 NW 1 ST, MIAMI, FL 33101',
            'pkey': 'C' + case, 'phones': phones, 'phdnc': [False] * len(phones), 'phsrc': ['st'] * len(phones),
            'phtype': ['mobile'] * len(phones)}


A = lead('2026-000941-CA-01', ['3055550121'])
B = lead('2026-000942-CA-01', ['3055550131'])
C = lead('2026-000943-CA-01', ['3055550141'])
D = lead('2026-000944-CA-01', ['3055550151'])
SLIM = [A, B, C, D]
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
html = call_mode.build_html([], 0, {'ct': 'x', 'k': 'y'}, 'built', 'sig', 'bsig',
                            sync_js=call_mode.extract_sync_js(tpl), funnel_js=call_mode.extract_funnel_js(tpl),
                            text_js=call_mode.extract_text_js(tpl),
                            mail_js=call_mode.bake_mail_js(call_mode.extract_mail_js(tpl), 'pickup test'),
                            histcov='{"ledgers_ok": true, "why": "", "capped": false, "total": 4, "shipped": 4}')
PAGE = os.path.join(_T, 'call.html')
open(PAGE, 'w', encoding='utf-8').write(html)

JS = """async ([rows, cov, A, B, C, D]) => {
  const wait = ms => new Promise(res => setTimeout(res, ms));
  try { localStorage.clear(); } catch(e) {}
  notes = {}; ROWS = rows; COV = cov;
  start();
  flClock = function(){ return {ok:true, txt:'noon'}; };
  /* tel: must not leave the page; the dial handler still runs (capture-phase preventDefault only) */
  document.addEventListener('click', ev => { const a = ev.target.closest && ev.target.closest('a[href^="tel:"]');
    if(a) ev.preventDefault(); }, true);
  const o = {opened: []};
  const dial = async c => { window._tapShieldUntil = 0; SCREEN = 'lead'; _openCase(c); phIdx = 0; screenLead();
    o.opened.push(cur && cur.c === c);
    document.getElementById('dial').click(); await wait(40); };
  const last = c => { const dl = (notes[c]||{}).dials || []; return dl[dl.length-1] || {}; };
  const tap = async k => { document.querySelector('.oc button[data-oc="'+k+'"]').click(); await wait(60); };
  o.clockedIn = !!clkGet();
  /* A: answered with a pickup tap */
  await dial(A);
  o.screen = SCREEN;
  o.btn = !!document.getElementById('pubtn');
  o.btnText = (document.getElementById('pubtn')||{}).textContent || '';
  const t0 = Date.now();
  document.getElementById('pubtn').click();
  const row = last(A);
  o.tsaSet = row.oc + '|' + (row.tsa >= t0 && row.tsa <= Date.now());
  o.btnGone = !document.getElementById('pubtn');
  const r0 = (document.getElementById('pu-t')||{}).textContent;
  o.readout = (document.querySelector('#pubox .puon')||{}).textContent || '';
  /* move the stamp back 65 s so one tick must show 01:05+ (no waiting a minute) */
  row.tsa -= 65000; const tsaKept = row.tsa;
  puPaint(cur, cur.p[phIdx]);
  await wait(1150);
  o.ticked = r0 + ' -> ' + (document.getElementById('pu-t')||{}).textContent;
  /* idempotent: the button is gone, and the handler itself refuses a second stamp */
  puPaint(cur, cur.p[phIdx]);
  o.secondTap = !document.getElementById('pubtn') && last(A).tsa === tsaKept;
  await tap('talked');
  const oa = last(A);
  o.answered = JSON.stringify({oc: oa.oc, tsa: oa.tsa === tsaKept, talk: oa.talk, int: Number.isInteger(oa.talk)});
  o.onePerCall = notes[A].dials.length;
  /* B: no pickup tap -> untimed, as before */
  await dial(B);
  await tap('talked');
  const ob = last(B);
  o.noTap = ob.oc + '|' + ('tsa' in ob) + '|' + ('talk' in ob);
  /* C: pickup tapped, then No answer -> tsa carried, no talk */
  await dial(C);
  document.getElementById('pubtn').click();
  await tap('noanswer');
  const oc = last(C);
  o.unanswered = oc.oc + '|' + (!!oc.tsa) + '|' + ('talk' in oc);
  /* D: pickup stamp in the future (skew) -> no talk; and a redial brings the button back */
  await dial(D);
  document.getElementById('pubtn').click();
  o.redialBefore = !!document.getElementById('pubtn');
  document.getElementById('redial').click(); await wait(40);
  o.redialAfter = !!document.getElementById('pubtn') && last(D).oc === 'redial' && !last(D).tsa;
  document.getElementById('pubtn').click();
  last(D).tsa = Date.now() + 60000;
  await tap('callback');
  const od = last(D);
  o.skew = od.oc + '|' + (!!od.tsa) + '|' + ('talk' in od);
  /* the stats screen reads it */
  const sr = callStatsRows(notes, '', 1);
  o.stats = JSON.stringify(sr.map(r => [r.answered, r.talked_calls, r.talk_s > 60]));
  return o; }"""

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None)
    errs = []
    pg = b.new_page(viewport={'width': 390, 'height': 900})
    pg.on('pageerror', lambda e: errs.append(str(e)))
    pg.on('dialog', lambda d: d.accept())
    pg.goto('file://' + PAGE)
    pg.wait_for_timeout(300)
    out = pg.evaluate(JS, [rows, cov, A['case'], B['case'], C['case'], D['case']])
    b.close()

rec('page runs without a script error', not errs, errs)
rec('not clocked in (the readout must tick anyway)', out['clockedIn'] is False, out['clockedIn'])
rec('dial opens the outcome screen with the pickup button', out['screen'] == 'outcome' and out['btn'], out)
rec('button reads "They picked up"', 'They picked up' in out['btnText'], out['btnText'])
rec('one tap stamps tsa on the open pending row', out['tsaSet'] == 'pending|true', out['tsaSet'])
rec('button replaced by the "On call" readout', out['btnGone'] and out['readout'].startswith('On call 00:0'), out['readout'])
rec('readout ticks on the 1 s loop', out['ticked'].startswith('00:0') and out['ticked'].split(' -> ')[1] >= '01:05', out['ticked'])
rec('second tap cannot overwrite tsa', out['secondTap'], out['secondTap'])
rec('answered outcome writes tsa + integer talk seconds (>= 65)',
    '"oc":"talked","tsa":true' in out['answered'] and '"int":true' in out['answered']
    and 65 <= int(out['answered'].split('"talk":')[1].split(',')[0]) <= 75, out['answered'])
rec('still one dial row per call', out['onePerCall'] == 1, out['onePerCall'])
rec('each dial opened the lead it meant to', out['opened'] == [True] * 4, out['opened'])
rec('no pickup tap: no tsa, no talk', out['noTap'] == 'talked|false|false', out['noTap'])
rec('no answer after a pickup tap: tsa carried, no talk', out['unanswered'] == 'noanswer|true|false', out['unanswered'])
rec('redial brings the button back for the new call', out['redialBefore'] is False and out['redialAfter'], out)
rec('pickup stamp after the outcome tap: no talk', out['skew'] == 'callback|true|false', out['skew'])
rec('stats screen counts one talked call', out['stats'] == '[[3,1,true]]', out['stats'])

print('%d failure(s)' % len(FAILS) if FAILS else 'ok: pickup tap -> talk time')
sys.exit(1 if FAILS else 0)
