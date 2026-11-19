"""Stress-test regressions on the lead board (2026-11-19). Real page in Chromium, file://, invented rows.

Five things a tester reported, pinned here:

1. EQUITY CERTAINTY ON THE ROW. A balloon (st BAL) row read "<=100% eq" beside the value and a
   STRONG / BUY badge, while the property hub for the same lead said "at most $240,000 (liens not
   verified)". Two causes: BAL rows are exempt from _debtUnknown ("priced off their own loan") but
   no equity reader ever subtracted that loan, so net equity was the whole house; and an unverified
   figure was marked only by a "<=" glyph. Now: no row prints 100% without a verified lien chain,
   every unverified figure carries the word, a balloon row prints an ESTIMATE off its recorded loan,
   and a verified-clear row is left alone (the control: the fix is not a blanket mute).
2. "Follow-up NaN-NaN-NaN". Call Mode's after-call screen wired its callback handler onto every
   `.cb` button, including the Mail / Knock / Neither route buttons that share the class and have
   no data-h. `+undefined` hours made an Invalid Date, written to notes.next as "NaN-NaN-NaN" and
   synced to the board (and the route choice itself was swallowed). The board printed it.
3. Sort reset. The chosen sort lived in memory only and the Fresh-filings / stage / Closers /
   mislisted views ignored it.
4. Refresh banner. A run that dies keeps its start flag and never writes `ended`, so /refresh said
   "running, step N of M" for ever; and a board that lost the bridge kept its last paint.
5. is report-only (bounce rate) and has no test.

Fake leads only, temp dir, no network (every non-file request is aborted), nothing in the repo is
written. Run: python _stressboardtest.py
"""
import datetime as dt
import json
import os
import pathlib
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='stressboard-')
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')
os.environ.pop('DEALFLOW_RELATIVE_DIAL', None)
os.environ.pop('DEALFLOW_FOLLOWUPS_ON', None)

import _balloonlanetest as BL          # noqa: E402  build_html + preview_constants, no side effects
import refresh_progress as RP          # noqa: E402
import refresh_status as RS            # noqa: E402

FAILS = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra and not ok else ''))
    if not ok:
        FAILS.append(name)


def day(n):
    return (dt.date.today() + dt.timedelta(days=n)).strftime('%m/%d/%Y')


def iso(n):
    return (dt.date.today() + dt.timedelta(days=n)).isoformat()


# ------------------------------------------------------------------ fixture rows (all invented)
def fc(i, **kw):
    r = {"tier": "A", "score": 80, "st": "FC", "case": "2025-0007%02d-CA-01" % i,
         "auction": day(20 + i), "days": 20 + i, "filed": 2025, "bought": 2009, "bprice": 110000,
         "owners": "STRESSROW OWNER %02d" % i, "oname": "Stressrow Owner",
         "addr": "%d NW %dTH ST, MIAMI, FL 33136" % (100 + i, i + 1), "mail": "",
         "value": 237000, "judg": 900, "eq": 100, "ctype": "Bank/Mortgage",
         "plaintiff": "Invented Savings Bank", "defs": "Stressrow Owner",
         "phones": ["30555501%02d" % i], "phdnc": [False], "phsrc": ["st"], "phbest": 0, "emails": [],
         "pa": "#", "zillow": "#", "tax": "#", "auc": "#", "people": "#", "docket": "#"}
    r.update(kw)
    return r


U100 = fc(1)                                                    # no eqstate at all: check never ran
UNCH = fc(2, eqstate='unchecked', judg=142200)                 # 40%, lien search not run
CLEAR = fc(3, eqstate='clear', judg=142200, orconf='ok')       # 40%, verified clear: the control
CLEAR100 = fc(4, eqstate='clear', orconf='ok')                 # ~100% WITH a verified chain: allowed
FAKE100 = fc(5, eqstate='none', eqfake=True, mr=True, ctype='HOA/Condo')   # gross HOA figure
UNPR = fc(6, eqstate='unpriced', judg=142200)
TD = fc(7, st='TD', obid=900, judg=0, eqstate='unchecked')
BAL = fc(8, st='BAL', judg=0, eq=None, value=240000, auction='', days=9999, eqstate='unchecked',
         bal={"days": 35, "lender": "Invented Hard Money LLC", "amt": 189600, "ltv": 79.0})
BALNO = fc(9, st='BAL', judg=0, eq=None, value=240000, auction='', days=9999, eqstate='unchecked',
           bal={"days": 60, "lender": "Invented Hard Money LLC"})
# three fresh filings: the NEWEST filed is the CHEAPEST, so "newest first" and "value" disagree
LP1 = fc(11, st='LP', judg=0, eq=None, auction='', days=9999, value=150000, filedDate=day(-1), tier='C')
LP2 = fc(12, st='LP', judg=0, eq=None, auction='', days=9999, value=450000, filedDate=day(-9), tier='C')
LP3 = fc(13, st='LP', judg=0, eq=None, auction='', days=9999, value=300000, filedDate=day(-5), tier='C')
ROWS = [U100, UNCH, CLEAR, CLEAR100, FAKE100, UNPR, TD, BAL, BALNO, LP1, LP2, LP3]
C = {k: v['case'] for k, v in dict(U100=U100, UNCH=UNCH, CLEAR=CLEAR, CLEAR100=CLEAR100, FAKE100=FAKE100,
                                   UNPR=UNPR, TD=TD, BAL=BAL, BALNO=BALNO, LP1=LP1, LP2=LP2, LP3=LP3).items()}

EQ_PROBE = r"""async (C) => {
  const wait = ms => new Promise(res => setTimeout(res, ms));
  const o = {};
  const by = c => DATA.find(r => r.case === c);
  q = 'STRESSROW'; render(); await wait(50);
  Object.keys(C).forEach(k => {
    const r = by(C[k]);
    const tr = document.querySelector('tr[data-case="' + C[k] + '"]');
    const sub = tr && tr.querySelector('.propcell .submeta');
    const eqEl = sub && sub.querySelector('.eqfake');
    o[k] = { row: sub ? sub.textContent.replace(/\s+/g, ' ').trim() : null,
             soft: !!eqEl, html: _eqRowHtml(r).replace(/<[^>]+>/g, ''), stat: _eqStatTxt(r),
             net: _netEqTxt(r), pct: netEqPct(r), prof: tr ? tr.children[2].innerText.replace(/\s+/g, ' ') : null };
  });
  /* the hub for the same lead, with the diligence API unreachable (every http request is aborted) */
  openCallSheet(C.U100); await wait(900);
  o.hub = document.getElementById('csbody').innerText.replace(/\s+/g, ' ');
  closeCallSheet(); await wait(30);
  o.rowAfterHub = document.querySelector('tr[data-case="' + C.U100 + '"] .propcell .submeta').textContent.replace(/\s+/g, ' ').trim();
  openCallSheet(C.BAL); await wait(300);
  o.hubBal = document.getElementById('csbody').innerText.replace(/\s+/g, ' ');
  closeCallSheet();
  return o; }"""

EQ_MOB = r"""async (C) => {
  const wait = ms => new Promise(res => setTimeout(res, ms));
  q = 'STRESSROW'; render(); await wait(50);
  _paintFillAll(document.getElementById('cards')); await wait(50);   // cards paint lazily on approach
  const o = {};
  Object.keys(C).forEach(k => {
    const el = document.querySelector('.card[data-case="' + C[k] + '"]');
    const tile = el && [...el.querySelectorAll('.cstat')].find(x => /equity|spread/i.test(x.textContent));
    o[k] = { tile: tile ? tile.querySelector('.v').textContent.replace(/\s+/g, ' ').trim() : null,
             soft: tile ? tile.querySelector('.v').classList.contains('eqfake') : null,
             sub: el ? [...el.querySelectorAll('.submeta')].map(x => x.textContent).join(' | ').replace(/\s+/g, ' ') : null };
  });
  return o; }"""

FU_PROBE = r"""async (C) => {
  const wait = ms => new Promise(res => setTimeout(res, ms));
  const o = {};
  q = 'STRESSROW'; render(); await wait(40);
  const chip = c => { const el = document.querySelector('tr[data-case="' + c + '"] .cxwrap'); return el ? el.outerHTML : ''; };
  const bad = ['NaN-NaN-NaN', 'garbage', '2026-13-45', '2026-02-31', 'undefined', '0000-00-00'];
  o.bad = {};
  for (const b of bad) {
    notes[C.UNCH] = {status: '', note: '', next: b}; render(); await wait(20);
    const h = chip(C.UNCH);
    openLogModal(C.UNCH); const lm = document.getElementById('logbody').innerText.replace(/\s+/g, ' ');
    document.getElementById('logmodal').classList.remove('open');
    o.bad[b] = { nan: /NaN/.test(h) || /NaN/.test(lm), echo: h.indexOf(b) >= 0 || lm.indexOf(b) >= 0,
                 fu: /cx-fu|cx-due/.test(h), modalNone: /Next:\s*none set/.test(lm) };
  }
  ['', null, undefined].forEach((b, i) => { notes[C.UNCH] = {status: '', note: '', next: b}; render();
    o['empty' + i] = /NaN|Follow-up|cx-fu|cx-due/.test(chip(C.UNCH)); });
  /* a real date still renders */
  const d = new Date(); d.setDate(d.getDate() + 3);
  const good = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  notes[C.UNCH] = {status: '', note: '', next: good}; render(); await wait(20);
  o.good = chip(C.UNCH); o.goodIso = good;
  /* the writer cannot mint the string */
  const n = {status: '', note: '', next: good};
  _setNext(n, NaN); o.setNaN = n.next; _setNext(n, undefined); o.setUndef = n.next;
  _setNext(n, 2); o.setTwo = /^\d{4}-\d{2}-\d{2}$/.test(n.next); _setNext(n, 0); o.setZero = n.next;
  /* sorted by follow-up, a malformed date must not float to the top: the real one leads */
  notes[C.CLEAR] = {status: '', note: '', next: 'NaN-NaN-NaN'};
  q = 'STRESSROW'; sortK = 'next'; sortD = 1; render(); await wait(30);
  o.firstCase = (document.querySelector('tr[data-case]') || {dataset: {}}).dataset.case;
  o.anyNaN = /NaN/.test(document.getElementById('tb').innerHTML);
  /* "due only" must not count a malformed date as due */
  q = ''; dueOnly = true; render(); await wait(20);
  o.dueHasBad = !!document.querySelector('tr[data-case="' + C.CLEAR + '"]');
  dueOnly = false; delete notes[C.CLEAR]; delete notes[C.UNCH]; sortK = '_fresh'; sortD = -1; render();
  /* a teammate's copy carrying the bad string must not win the merge */
  const m = _mergeLead({status: '', note: ''}, {status: '', note: '', next: 'NaN-NaN-NaN'});
  o.mergeBad = m[0].next === undefined ? null : m[0].next; o.mergeShape = Array.isArray(m) && typeof m[0] === 'object';
  const m2 = _mergeLead({status: '', note: '', next: 'NaN-NaN-NaN'}, {status: '', note: '', next: good});
  o.mergeHeal = m2[0].next;
  return o; }"""

SORT_PROBE = r"""async (C) => {
  const wait = ms => new Promise(res => setTimeout(res, ms));
  const o = {};
  const order = () => [...document.querySelectorAll('tr[data-case]')].map(t => t.dataset.case);
  const lp = () => order().filter(c => [C.LP1, C.LP2, C.LP3].indexOf(c) >= 0);
  const sel = document.getElementById('sortby');
  o.bootSel = sel.value; o.bootK = sortK;
  /* control: nobody chose a sort, the Fresh-filings view keeps its own order (newest filing first) */
  document.getElementById('freshlp').click(); await wait(60);
  o.lpDefault = lp();
  document.getElementById('freshlp').click(); await wait(60);
  /* choose Value, then switch views */
  sel.value = 'value'; sel.dispatchEvent(new Event('change', {bubbles: true})); await wait(60);
  o.mainValue = order().slice(0, 3);
  document.getElementById('freshlp').click(); await wait(60);
  o.lpAfterPick = lp(); o.selInLp = sel.value; o.kInLp = sortK;
  document.getElementById('freshlp').click(); await wait(60);
  o.selBack = sel.value; o.kBack = sortK; o.mainBack = order().slice(0, 3);
  o.stored = localStorage.getItem('fcSort');
  return o; }"""

SORT_AFTER_RELOAD = r"""async (C) => {
  const wait = ms => new Promise(res => setTimeout(res, ms));
  const o = {};
  const sel = document.getElementById('sortby');
  o.sel = sel.value; o.k = sortK; o.d = sortD;
  o.first = (document.querySelector('tr[data-case]') || {dataset: {}}).dataset.case;
  /* a column-header sort is remembered too, and the dropdown does not go on claiming another sort */
  document.querySelector('th[data-k="score"]').click(); await wait(60);
  o.thK = sortK; o.thSel = sel.value; o.thStored = localStorage.getItem('fcSort');
  /* back to the default: views get their own order again */
  sel.value = '_fresh'; sel.dispatchEvent(new Event('change', {bubbles: true})); await wait(40);
  document.getElementById('freshlp').click(); await wait(60);
  o.lpDefaultAgain = [...document.querySelectorAll('tr[data-case]')].map(t => t.dataset.case)
    .filter(c => [C.LP1, C.LP2, C.LP3].indexOf(c) >= 0);
  /* a junk stored key is ignored */
  localStorage.setItem('fcSort', JSON.stringify({k: '__proto__', d: 7}));
  return o; }"""


def board_checks(pw):
    fake, preview_empty = BL.preview_constants()
    html = BL.build_html(ROWS, preview_empty)
    path = os.path.join(_T, 'board.html')
    open(path, 'w', encoding='utf-8').write(html)
    url = pathlib.Path(path).as_uri()
    b = pw.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None)
    try:
        ctx = b.new_context(viewport={'width': 1500, 'height': 900})
        # nothing leaves the machine, and the diligence API (127.0.0.1:8765/8787) is "offline"
        ctx.route('**/*', lambda rt: rt.continue_() if rt.request.url.startswith('file:') else rt.abort())
        pg = ctx.new_page()
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))
        pg.goto(url)
        pg.wait_for_timeout(1200)
        e = pg.evaluate(EQ_PROBE, C)
        f = pg.evaluate(FU_PROBE, C)
        pg.reload()
        pg.wait_for_timeout(1000)
        pg.evaluate("() => { try { localStorage.removeItem('fcSort'); } catch(e) {} }")
        pg.reload()
        pg.wait_for_timeout(1000)
        s = pg.evaluate(SORT_PROBE, C)
        pg.reload()
        pg.wait_for_timeout(1000)
        s2 = pg.evaluate(SORT_AFTER_RELOAD, C)
        pg.reload()
        pg.wait_for_timeout(800)
        s3 = pg.evaluate("() => ({k: sortK, d: sortD, sel: document.getElementById('sortby').value})")
        ctx2 = b.new_context(viewport={'width': 390, 'height': 844})
        ctx2.route('**/*', lambda rt: rt.continue_() if rt.request.url.startswith('file:') else rt.abort())
        pm = ctx2.new_page()
        pm.goto(url)
        pm.wait_for_timeout(1200)
        mob = pm.evaluate(EQ_MOB, C)
    finally:
        b.close()

    print('--- 1. equity certainty on the row ---')
    rec('board loads with no page errors', not errs, errs[:2])
    for k in ('U100', 'FAKE100', 'BAL', 'BALNO'):
        rec('%s: the row never prints 100%% equity without a verified chain' % k,
            e[k]['row'] is not None and '100%' not in e[k]['row'] and '100%' not in e[k]['stat'], e[k])
    for k in ('U100', 'FAKE100', 'BALNO'):
        rec('%s: the row says "eq unverified"' % k, 'eq unverified' in (e[k]['row'] or ''), e[k]['row'])
        rec('%s: and it is styled as not-a-fact' % k, e[k]['soft'] is True, e[k])
    rec('UNCH: a ceiling keeps its cap and now says the word',
        '≤40% eq' in e['UNCH']['row'] and 'unverified' in e['UNCH']['row'] and e['UNCH']['soft'], e['UNCH']['row'])
    rec('UNPR: an unpriced chain is a ceiling that says the word',
        '≤40% eq' in e['UNPR']['row'] and 'unverified' in e['UNPR']['row'], e['UNPR']['row'])
    rec('CLEAR (control): a verified chain still prints the plain figure, not muted',
        e['CLEAR']['row'].endswith('40% eq') and '≤' not in e['CLEAR']['row']
        and 'unverified' not in e['CLEAR']['row'] and e['CLEAR']['soft'] is False, e['CLEAR'])
    rec('CLEAR100 (control): 100% WITH a verified chain is left alone',
        '100% eq' in e['CLEAR100']['row'] and 'unverified' not in e['CLEAR100']['row']
        and e['CLEAR100']['soft'] is False, e['CLEAR100'])
    rec('TD (control): a tax deed keeps its margin label', 'margin' in e['TD']['row'], e['TD']['row'])
    rec('BAL: the row is an ESTIMATE off the recorded loan (~21%), labelled',
        '~21% eq est.' in e['BAL']['row'] and e['BAL']['soft'], e['BAL']['row'])
    rec('BAL: the dollar text is an estimate that names the loan, never the whole house',
        'about $50,400' in e['BAL']['net'] and 'not a payoff' in e['BAL']['net']
        and '240,000' not in e['BAL']['net'], e['BAL']['net'])
    rec('BALNO: no loan on file reads unverified, not the whole house',
        'unverified' in e['BALNO']['net'] and '240,000' not in e['BALNO']['net'], e['BALNO']['net'])
    rec('ranking untouched: netEqPct still reports what the filters read',
        e['BAL']['pct'] == 100 and e['U100']['pct'] == 100 and e['CLEAR']['pct'] == 40,
        [e['BAL']['pct'], e['U100']['pct'], e['CLEAR']['pct']])
    rec('hub with the diligence API offline says liens are not verified',
        'liens not verified' in e['hub'].lower(), e['hub'][:300])
    rec('and the ROW for that same lead agrees (says unverified, prints no optimistic percent)',
        'eq unverified' in e['rowAfterHub'] and '%' not in e['rowAfterHub'].split('owed')[-1], e['rowAfterHub'])
    rec('BAL hub: net equity is the estimate, not "at most" the whole house',
        'about $50,400' in e['hubBal'] and 'at most $240,000' not in e['hubBal'], e['hubBal'][:400])
    rec('FAKE100 phone card (VERIFY verdict, no tile): the line reads unverified, no 100%',
        'eq unverified' in (mob['FAKE100']['sub'] or '') and '100%' not in mob['FAKE100']['sub'], mob['FAKE100'])
    for k in ('U100', 'BALNO'):
        rec('%s phone card: the Equity tile reads unverified, muted' % k,
            mob[k]['tile'] is not None and 'unverified' in mob[k]['tile'] and '100' not in mob[k]['tile']
            and mob[k]['soft'] is True, mob[k])
    rec('UNCH phone card: tile shows the cap and the word',
        (mob['UNCH']['tile'] or '') == '≤40%unverified', mob['UNCH'])
    rec('BAL phone card: tile is the estimate', '~21%' in (mob['BAL']['tile'] or '') and mob['BAL']['soft'], mob['BAL'])
    rec('CLEAR phone card (control): plain 40%, not muted',
        mob['CLEAR']['tile'] == '40%' and mob['CLEAR']['soft'] is False, mob['CLEAR'])

    print('\n--- 2. follow-up date ---')
    for bad, v in f['bad'].items():
        rec('next=%r: no NaN, no echo of the bad string, no follow-up chip, modal says none set' % bad,
            not v['nan'] and not v['echo'] and not v['fu'] and v['modalNone'], v)
    rec('an empty / missing follow-up renders nothing', not (f['empty0'] or f['empty1'] or f['empty2']))
    rec('a real date still renders its chip',
        'Follow-up ' + f['goodIso'] in f['good'] and 'in 3d' in f['good'], f['good'][-200:])
    rec('_setNext(NaN) and _setNext(undefined) leave the date alone',
        f['setNaN'] == f['goodIso'] and f['setUndef'] == f['goodIso'], [f['setNaN'], f['setUndef']])
    rec('_setNext still sets (+2d) and clears (0)', f['setTwo'] is True and f['setZero'] == '', [f['setTwo'], f['setZero']])
    rec('sorted by follow-up, the real date leads and a malformed one does not',
        f['firstCase'] == C['UNCH'] and not f['anyNaN'], [f['firstCase'], f['anyNaN']])
    rec('"due only" does not treat a malformed date as due', f['dueHasBad'] is False)
    rec('sync merge refuses a malformed follow-up from a teammate', f['mergeShape'] and f['mergeBad'] is None, f['mergeBad'])
    rec('sync merge replaces a malformed local follow-up with a real one', f['mergeHeal'] == f['goodIso'], f['mergeHeal'])

    print('\n--- 3. sort persists across views ---')
    newest_first = [C['LP1'], C['LP3'], C['LP2']]
    by_value = [C['LP2'], C['LP3'], C['LP1']]
    rec('boot: default sort, dropdown agrees', s['bootSel'] == '_fresh' and s['bootK'] == '_fresh', s)
    rec('control: with no sort chosen, Fresh filings is newest-first', s['lpDefault'] == newest_first, s['lpDefault'])
    rec('after choosing Value, Fresh filings is ordered by value', s['lpAfterPick'] == by_value, s['lpAfterPick'])
    rec('the dropdown still says Value inside the view and after leaving it',
        s['selInLp'] == 'value' and s['kInLp'] == 'value' and s['selBack'] == 'value' and s['kBack'] == 'value', s)
    rec('main board order is unchanged by the round trip', s['mainValue'] == s['mainBack'], [s['mainValue'], s['mainBack']])
    rec('the choice is stored', s['stored'] and json.loads(s['stored']).get('k') == 'value', s['stored'])
    rec('after a reload the sort is still Value (dropdown and order)',
        s2['sel'] == 'value' and s2['k'] == 'value' and s2['d'] == -1 and s2['first'] == C['LP2'], s2)
    rec('a column-header sort is stored and the dropdown follows it',
        s2['thK'] == 'score' and s2['thSel'] == 'score' and json.loads(s2['thStored']).get('k') == 'score', s2)
    rec('choosing the default sort gives each view its own order back', s2['lpDefaultAgain'] == newest_first, s2['lpDefaultAgain'])
    rec('a junk stored sort is ignored at boot', s3 == {'k': '_fresh', 'd': -1, 'sel': '_fresh'}, s3)


# ------------------------------------------------------------------ 2b. Call Mode: the writer
CM_JS = r"""async ([rows, cov, A]) => {
  const wait = ms => new Promise(res => setTimeout(res, ms));
  try { localStorage.clear(); } catch(e) {}
  notes = {}; ROWS = rows; COV = cov; start();
  flClock = function(){ return {ok:true, txt:'noon'}; };
  document.addEventListener('click', ev => { const a = ev.target.closest && ev.target.closest('a[href^="tel:"]'); if(a) ev.preventDefault(); }, true);
  const after = async () => { window._tapShieldUntil = 0; SCREEN = 'lead'; _openCase(A); phIdx = 0; screenLead();
    document.getElementById('dial').click(); await wait(40);
    document.querySelector('.oc button[data-oc="noanswer"]').click(); await wait(80); window._tapShieldUntil = 0; };
  const o = {};
  await after();
  o.screen = SCREEN;
  o.rtButtons = [...document.querySelectorAll('.cb[data-rt]')].map(b => b.dataset.rt);
  const none = document.querySelector('[data-rt="none"]');
  o.hadNone = !!none;
  if(none){ none.click(); await wait(80); }
  const n = notes[A] || {};
  o.nextAfterRoute = n.next === undefined ? null : n.next;
  o.routeK = n.route && n.route.k;
  o.note = n.note || '';
  /* setCallback itself refuses a non-number */
  const r = ROWS.find(x => x.c === A);
  setCallback(r, NaN, 'x'); setCallback(r, undefined, 'x');
  o.nextAfterBad = (notes[A] || {}).next === undefined ? null : notes[A].next;
  /* a real callback chip still writes a real date */
  await after();
  const cb = document.querySelector('.cb[data-h="72"]'); o.hadCb = !!cb;
  if(cb){ cb.click(); await wait(40); }
  o.nextAfterCb = (notes[A] || {}).next || '';
  return o; }"""


def callmode_checks(pw):
    import call_mode
    auc = (dt.date.today() + dt.timedelta(days=29)).strftime('%m/%d/%Y')
    lead = {'case': '2026-000941-CA-01', 'county': 'MIAMI-DADE', 'st': 'FC', 'auction': auc, 'days': 29,
            'tier': 'B', 'score': 50, 'value': 400000, 'judg': 100000, 'owners': 'TEST OWNER STRESS',
            'oname': 'Test Owner', 'addr': '100 NW 1 ST, MIAMI, FL 33101', 'pkey': 'CSTRESS',
            'phones': ['3055550121'], 'phdnc': [False], 'phsrc': ['st'], 'phtype': ['mobile'],
            'eqstate': 'clear', 'eqc': {'state': 'clear', 'fact': True, 'may_quote': True, 'net': 300000}}
    rows, _ = call_mode.call_rows([lead], None, None, lookup=[])
    cov, _ = call_mode.coverage_rows([lead], [r['c'] for r in rows])
    tpl = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
    html = call_mode.build_html([], 0, {'ct': 'x', 'k': 'y'}, 'built', 'sig', 'bsig',
                                sync_js=call_mode.extract_sync_js(tpl), funnel_js=call_mode.extract_funnel_js(tpl),
                                text_js=call_mode.extract_text_js(tpl),
                                mail_js=call_mode.bake_mail_js(call_mode.extract_mail_js(tpl), 'stress test'),
                                histcov='{"ledgers_ok": true, "why": "", "capped": false, "total": 1, "shipped": 1}')
    page = os.path.join(_T, 'call.html')
    open(page, 'w', encoding='utf-8').write(html)
    b = pw.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None)
    try:
        ctx = b.new_context(viewport={'width': 390, 'height': 900})
        ctx.route('**/*', lambda rt: rt.continue_() if rt.request.url.startswith('file:') else rt.abort())
        pg = ctx.new_page()
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))
        pg.goto(pathlib.Path(page).as_uri())
        pg.wait_for_timeout(500)
        o = pg.evaluate(CM_JS, [rows, cov, lead['case']])
    finally:
        b.close()
    print('\n--- 2b. Call Mode: where "NaN-NaN-NaN" was written ---')
    rec('call mode loads with no page errors', not errs, errs[:2])
    rec('the fixture reaches the after-call screen with the route buttons',
        o['screen'] == 'after' and o['hadNone'] and 'none' in o['rtButtons'], o)
    rec('tapping a route button does NOT write a follow-up date', o['nextAfterRoute'] is None, o['nextAfterRoute'])
    rec('and it does what it says: the route is "none"', o['routeK'] == 'none', o['routeK'])
    rec('and no "callback set" line is added to the note', 'callback set' not in o['note'], o['note'])
    rec('setCallback(NaN / undefined) writes nothing', o['nextAfterBad'] is None, o['nextAfterBad'])
    rec('a real callback chip still writes a real date',
        o['hadCb'] and re.match(r'^\d{4}-\d{2}-\d{2}$', o['nextAfterCb'] or '') is not None, o['nextAfterCb'])


# ------------------------------------------------------------------ 4. refresh banner
def progress_checks():
    print('\n--- 4. refresh progress: running, stalled, ended ---')
    now = time.time()
    flag = os.path.join(_T, 'refresh-running.flag')
    log = os.path.join(_T, 'leads-run.log')
    open(flag, 'w').write('x')
    open(log, 'w').write('x')
    started = now - 9 * 3600
    os.utime(flag, (started, started))
    keep = (RP.FLAG, RP.LOG)
    RP.FLAG, RP.LOG = flag, log
    cat = [{'key': 'k%d' % i, 'tag': '%d/5' % i, 'label': 'Invented step %d' % i} for i in range(1, 59)]
    try:
        def state(updated, ended=None):
            steps = [{'key': '_pre', 'start': started, 'end': started + 60}]
            for i in range(1, 31):
                steps.append({'key': 'k%d' % i, 'start': started + 60 * i, 'end': started + 60 * (i + 1)})
            steps.append({'key': 'k31', 'start': updated, 'end': ended})
            return {'started': started, 'steps': steps, 'ended': ended, 'rc': 0 if ended else None, 'updated': ended or updated}

        # a) long step, log still being written: running, not stalled
        os.utime(log, (now - 20, now - 20))
        v = RP.view(st=state(now - 4 * 3600), now=now, cat=cat, hist=[])
        rec('long step with a live log: running, step 32 of 59, NOT stalled',
            v['state'] == 'running' and v['running'] and v['step']['i'] == 32 and v['n'] == 59
            and v.get('stalled') is False, {k: v.get(k) for k in ('state', 'running', 'stalled', 'quiet', 'n')})
        # b) nothing written anywhere for 3 hours: stalled, and it says since when
        quiet_at = now - 3 * 3600
        os.utime(log, (quiet_at, quiet_at))
        v = RP.view(st=state(now - 4 * 3600), now=now, cat=cat, hist=[{'secs': {'k31': 5}}])
        rec('no step and no log line for 3h: stalled since the last sign of life',
            v['state'] == 'running' and v.get('stalled') is True and v.get('beat') == RP._iso(quiet_at)
            and 10700 < v.get('quiet', 0) < 10900 and v.get('eta') is None,
            {k: v.get(k) for k in ('state', 'stalled', 'beat', 'quiet', 'eta')})
        lines = '\n'.join(RP.render(v, 100, 40, None))
        rec('the watch screen says STALLED too', 'STALLED' in lines, lines[:200])
        pub = dict(v); pub.pop('rows', None)
        # c) just under the threshold is not a stall
        os.utime(log, (now - RP.STALL_S + 60, now - RP.STALL_S + 60))
        v = RP.view(st=state(now - 4 * 3600), now=now, cat=cat, hist=[])
        rec('quiet for less than the threshold is still running', v.get('stalled') is False, v.get('quiet'))
        # d) the run finishes: not running, not stalled
        os.utime(log, (quiet_at, quiet_at))
        v_end = RP.view(st=state(now - 4 * 3600, ended=now - 30), now=now, cat=cat, hist=[])
        rec('a finished run is ended, not running and not stalled',
            v_end['state'] == 'ended' and v_end['running'] is False and not v_end.get('stalled'), v_end.get('state'))
        # e) the flag is gone (final exit without a done line): stale, as before
        os.remove(flag)
        v_stale = RP.view(st=state(now - 4 * 3600), now=now, cat=cat, hist=[])
        rec('no start flag: stale (unchanged behaviour)', v_stale['state'] == 'stale' and not v_stale['running'], v_stale['state'])
    finally:
        RP.FLAG, RP.LOG = keep
    return pub, v_end


BANNER_JS = r"""async (seq) => {
  const wait = ms => new Promise(res => setTimeout(res, ms));
  const el = document.getElementById('rfstat'); const out = [];
  for (const j of seq) {
    window.__next = j;                       // what the stubbed bridge answers next (null = unreachable)
    await el.__dfPoll(); await wait(30);
    out.push(el.innerText.replace(/\s+/g, ' ').trim());
  }
  return out; }"""


def banner_checks(pw, stalled, ended):
    print('\n--- 4b. the banner on the board ---')
    baked = (dt.datetime.now() - dt.timedelta(hours=11)).strftime('%Y-%m-%dT%H:%M:%S')
    summ = {'baked': baked, 'good': baked, 'rows': [], 'earlier': None,
            'last': {'at': baked, 'rc': 0, 'why': 'ok', 'took': 3600}}
    html = '<!doctype html><meta charset="utf-8"><body>' + RS.page_html(summ) + '</body>'
    path = os.path.join(_T, 'banner.html')
    open(path, 'w', encoding='utf-8').write(html)
    running = dict(stalled, stalled=False, quiet=20)
    mine = dict(ended)
    # the run that ended built a NEWER page than this one: the banner offers a reload, it does not stick
    b = pw.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None)
    try:
        pg = b.new_page()
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))
        # the bridge is stubbed IN the page: window.__next is the next /refresh answer, null = unreachable
        pg.add_init_script("""window.__next = null; window.fetch = function(){
          var j = window.__next; if(j === null) return Promise.reject(new Error('unreachable'));
          return Promise.resolve({json: function(){ return Promise.resolve(j); }}); };""")
        pg.goto(pathlib.Path(path).as_uri())
        pg.wait_for_timeout(300)
        seq = [running, stalled, running, mine, running, None, None, None, stalled, {'ok': True, 'state': 'none', 'running': False}]
        t = pg.evaluate(BANNER_JS, seq)
    finally:
        b.close()
    rec('banner page has no script errors', not errs, errs[:2])
    rec('running: "Refresh running · step 32 of 59"', 'Refresh running' in t[0] and 'step 32 of 59' in t[0], t[0])
    rec('stalled: says "stalled since <time>" and the step, not "running"',
        'stalled since' in t[1].lower() and 'step 32 of 59' in t[1] and 'Refresh running' not in t[1], t[1])
    rec('it resumes: back to running', 'Refresh running' in t[2], t[2])
    rec('the run finishes: the step line is gone', 'step 32 of 59' not in t[3] and 'Refresh running' not in t[3]
        and 'stalled' not in t[3].lower(), t[3])
    rec('one missed poll keeps the live line (no flicker)', 'Refresh running' in t[5], t[5])
    rec('bridge unreachable twice: the banner lets go of "running" instead of sticking',
        'Refresh running' not in t[6] and 'step 32 of 59' not in t[6], t[6])
    rec('and stays let go', 'Refresh running' not in t[7], t[7])
    rec('it comes back when the bridge does', 'stalled since' in t[8].lower(), t[8])
    rec('no run at all: baked line', 'step 32 of 59' not in t[9] and 'stalled' not in t[9].lower(), t[9])


def main():
    stalled, ended = progress_checks()
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None).close()
    except Exception as e:      # noqa: BLE001
        print('playwright / Chromium is not available (%s) -- the browser checks cannot be faked.'
              % str(e).splitlines()[0][:160])
        return 2
    with sync_playwright() as pw:
        board_checks(pw)
        callmode_checks(pw)
        banner_checks(pw, stalled, ended)
    print('\n%s  (%d failed)' % ('FAILED' if FAILS else 'ALL OK', len(FAILS)))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
