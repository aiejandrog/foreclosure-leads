"""_stresscmtest -- Call Mode: the 2026-11-19 stress-test report.

Run:  python _stresscmtest.py   (exit 0 = safe; invented people and 555-01xx numbers, file:// page, no
      network; the browser half skips when Chromium will not launch)

A tester worked the phone page and reported six things. What each was, and what is pinned here:

  1. THE SCRIPT WENT BACK TO STEP 1. Tapping dial writes a 'pending' dial row, which takes the lead
     out of pool(). Every side screen's Back was `SCREEN='lead'; render()`, and render() re-picks
     cur = pool()[i]: the call log, Who texted me, call stats, routes or a record opened in the middle
     of a call came back on the NEXT lead with the script at its greeting. A reload did the same, and
     the outcome screen it reopened kept the sheet of whichever lead start() had painted first.
     Pins: the walk position survives the call log from the lead screen, Who texted me from the
     outcome screen (back on the outcome screen), the call log after Back to the numbers, a
     background repaint, and a reload; the script under a resumed outcome screen is that lead's.
     Next lead and a lane switch still move (he is not held on the card); a lead marked do-not-contact
     is not kept; a logged outcome puts the next call at the greeting; a record from another day is
     ignored.
  2. THE SAME OWNER TWICE, AND TWO TOTALS. Rows are one per case, so an owner with two cases was two
     cards. And a Find a number lead that got a number was pushed into ROWS while its coverage row
     stayed in COV, so allLeads() counted it twice: "Board lanes" read one high per saved number.
     Pins: one card per person key; the folded count is said; a case opened on purpose is the one
     shown; a no-answer or a hard no on the card still closes both cases; the book total equals the
     number of cases before and after a number is saved; no case is in the book twice.
  3. "Hi, We?" A company with no pipeline company flag was greeted by the first word of its name.
     Pins: LLCs, trusts, estates, banks, associations, government bodies, names with a digit,
     pronouns, articles and surname particles give no first name, on a dial row and on a Find a
     number row; ordinary names are unchanged; the opener for those leads is the anonymous one.
  4. THE SCRIPT BAR. It said "TAP TO START THE CALL" at every step, and on a short screen the bar
     covered Pause / +30 min / End work.
     Pins: the label names the step once the walk has started and never says "tap to start" over an
     open script; the bar is three lines; the clock controls are clear of the collapsed bar at
     375x553, 390x664 and 390x844, clocked in and not.
  5. "HIDE VIEWED TURNS ITSELF ON." That control is on the board (tracker_template.html), not on this
     page. It is on by default by design and it stays off once turned off. Nothing changed; the pins
     below only record that reading so a later change to it is deliberate.
  6. "HAS EQUITY 48", EMPTY LIST. The lane button counted rows past suppression; the list goes on
     through the seat split, the claim filter, one card per person and the queue view.
     Pins: for every lane button, in each view and with the list split in two, the number on the
     button is the length of the list it opens.
"""
import datetime as dt
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='stresscm_')
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')
os.environ.pop('DEALFLOW_RELATIVE_DIAL', None)
os.environ.pop('DEALFLOW_FOLLOWUPS_ON', None)
import call_mode  # noqa: E402

FAILS = []
AUC = (dt.date.today() + dt.timedelta(days=29)).strftime('%m/%d/%Y')


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:600]) if extra and not ok else ''))
    if not ok:
        FAILS.append(name)


def lead(case, phones, **kw):
    d = {'case': case, 'county': 'MIAMI-DADE', 'st': 'FC', 'auction': AUC, 'days': 29, 'tier': 'B', 'score': 50,
         'value': 400000, 'owners': 'TEST OWNER ' + case, 'oname': 'Test Owner', 'addr': '100 NW 1 ST, MIAMI, FL 33101',
         'pkey': 'C' + case, 'phones': phones, 'phdnc': [False] * len(phones), 'phsrc': ['st'] * len(phones),
         'phtype': ['mobile'] * len(phones)}
    d.update(kw)
    return d


EQC = {'state': 'FACT', 'label': 'verified', 'fact': True, 'may_quote': True, 'judgment': 200000,
       'judgment_source': 'docket', 'basis': 400000, 'basis_source': 'pa', 'net': 150000}
# plain dial leads: even = one number, odd = two numbers
DIAL = [lead('2026-0008%02d-CA-01' % i, ['30555501%02d' % i] + (['78655502%02d' % i] if i % 2 else []))
        for i in range(10)]
# two people who each own two cases
PAIRS = [lead('2026-000840-CA-01', ['3055550140'], pkey='PTWO', owners='PAIR SAMPLE', oname='Pair Sample'),
         lead('2026-000841-CA-01', ['3055550141'], pkey='PTWO', owners='PAIR SAMPLE', oname='Pair Sample',
              addr='200 NW 2 ST, MIAMI, FL 33101'),
         lead('2026-000842-CA-01', ['3055550142'], pkey='PTRI', owners='TRIO SAMPLE', oname='Trio Sample'),
         lead('2026-000843-CA-01', ['3055550143'], pkey='PTRI', owners='TRIO SAMPLE', oname='Trio Sample',
              addr='300 NW 3 ST, MIAMI, FL 33101')]
# verified-equity leads
EQ = [lead('2026-0008%02d-CA-01' % i, ['30555501%02d' % i], eqc=dict(EQC), eq=37) for i in range(50, 56)]
# a company with no pipeline company flag, on the dial queue and on Find a number
ENT = [lead('2026-000860-CA-01', ['3055550160'], owners='WE BUY EXAMPLE HOMES LLC', oname='We Buy Example Homes Llc')]
LK = [lead('2026-0018%02d-CA-01' % i, []) for i in range(4)]
LK.append(lead('2026-001810-CA-01', [], owners='WE BUY EXAMPLE HOMES LLC', oname='We Buy Example Homes Llc'))
SLIM = DIAL + PAIRS + EQ + ENT + LK
sink = []
rows, _ = call_mode.call_rows(SLIM, None, None, lookup=sink)
cov, _ = call_mode.coverage_rows(SLIM, [r['c'] for r in rows])
call_mode.mark_lookup(cov, sink, {r['pk']: r['sb'] for r in rows})
lkc = [c['c'] for c in cov if c.get('lk')]
rec('setup: %d dial rows, 5 on Find a number, every case shipped once' % len(rows),
    len(rows) == 21 and len(lkc) == 5 and len({r['c'] for r in rows} | {c['c'] for c in cov}) == len(SLIM),
    (len(rows), len(lkc)))
rec('setup: the build ships both cases of a two-case owner (one row per case)',
    sum(1 for r in rows if r['pk'] == 'PTWO') == 2 and sum(1 for r in rows if r['pk'] == 'PTRI') == 2)
rec('setup: the company carries no pipeline company flag (the reported shape)',
    all('C' not in (r.get('fl') or '') for r in rows if 'EXAMPLE HOMES' in r['o']))

src = open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
tpl = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
pg_src = call_mode._PAGE
rec('1. every side screen leaves through _sideBack()',
    all(("$('%s').onclick = _sideBack;" % b) in pg_src for b in ('regback', 'csback', 'lkback', 'rtback'))
    and "else if(_RECBACK === 'reg') screenRegistry(); else _sideBack(); };" in pg_src)
rec('1. no side screen goes straight to render() any more',
    all(("$('%s').onclick = function(){ SCREEN" % b) not in pg_src for b in ('regback', 'csback', 'lkback', 'rtback')))
rec('1. the open-call test never reads past the do-not-contact check',
    "if(!r || hardSuppressed(r)) return false;" in pg_src[pg_src.index('function _callOpen('):][:200])
rec('2. the book is one row per case', 'return ROWS.concat(COV.filter(function(r){ return !(r && inq[r.c]); }));' in pg_src)
_pool = pg_src[pg_src.index('function pool(){'):pg_src.index('function _contactTier(')]
rec('2. one card per person runs after the gates, the seat split and the claim filter, before the view counts',
    0 < _pool.index("if(sr.k){ n++; return false; }") < _pool.index('if(_m === 2){ _CLMN++; return false; }')
    < _pool.index('var _op = _onePerPerson(keep, _vw1);') < _pool.index("_VIEWN = {untouched:0"))
rec('6. the lane button prints the list count', "_cn = C.n || 0;" in pg_src and "L.lbl + (C.raw ? ' &middot; '+_cn : '')" in pg_src)
# 5. the board control, read not changed
rec('5. Hide viewed is a board control, not on the call page', 'Hide viewed' not in pg_src and 'id="hv"' in tpl)
rec('5. board: on by default, and a saved off is read back as off',
    'hideViewed = true,' in tpl and "if(_hv === '0') hideViewed = false; else if(_hv === '1') hideViewed = true;" in tpl)
rec('5. board: only the button and its own filter chip write it',
    tpl.count("localStorage.setItem('fcHideViewed'") == 2 and tpl.count('hideViewed = ') + tpl.count('hideViewed=') == 5,
    (tpl.count("localStorage.setItem('fcHideViewed'"), tpl.count('hideViewed = '), tpl.count('hideViewed=')))

try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as _p:
        _p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None).close()
except Exception as e:
    print('SKIP browser half (%s)' % str(e).splitlines()[0][:200])
    print('\n%d failure(s)' % len(FAILS) if FAILS else '\nall passed')
    sys.exit(1 if FAILS else 0)

html = call_mode.build_html([], 0, {'ct': 'x', 'k': 'y'}, 'built', 'sig', 'bsig',
                            sync_js=call_mode.extract_sync_js(tpl), funnel_js=call_mode.extract_funnel_js(tpl),
                            text_js=call_mode.extract_text_js(tpl),
                            mail_js=call_mode.bake_mail_js(call_mode.extract_mail_js(tpl), 'stress test'),
                            histcov='{"ledgers_ok": true, "why": "", "capped": false, "total": %d, "shipped": %d}'
                            % (len(SLIM), len(SLIM)))
PAGE = os.path.join(_T, 'call.html')
open(PAGE, 'w', encoding='utf-8').write(html)

BOOT = """([rows, cov, keep]) => {
  if(!keep){ try { localStorage.clear(); } catch(e) {} }
  try { localStorage.setItem('fcCaller', 'Tester'); } catch(e) {}
  notes = {}; if(keep) loadNotes();
  ROWS = rows; COV = cov;
  flClock = function(){ return {ok:true, txt:'noon'}; };
  pollTextHold = function(){};
  if(!window.__telHook){ window.__telHook = 1;
    document.addEventListener('click', ev => { const a = ev.target.closest && ev.target.closest('a[href^="tel:"]');
      if(a) ev.preventDefault(); }, true); }
  start();
  return {screen: SCREEN, c: cur && cur.c, blane: BLANE};
}"""

HELP = """
  window.__w = ms => new Promise(res => setTimeout(res, ms));
  window.__open = c => { window._tapShieldUntil = 0; SCREEN = 'lead'; BLANE = null; lane = 'all'; QVIEW = 'untouched';
    const P = pool(); const k = P.findIndex(r => r.c === c); if(k >= 0){ i = k; _MOVE = 1; render(); } return !!(cur && cur.c === c); };
  window.__dial = async () => { window._tapShieldUntil = 0;
    document.getElementById('dial').scrollIntoView({block: 'center'});
    document.getElementById('dial').click(); await __w(40); };
  window.__walk = async n => { for(let q = 0; q < n; q++){ window._tapShieldUntil = 0;
    const b = document.querySelector('#sbody .objs [data-gto]'); if(!b) break; b.click(); await __w(15); } };
  window.__tap = async sel => { window._tapShieldUntil = 0; const e = document.querySelector(sel);
    if(!e) return false; e.click(); await __w(40); return true; };
  window.__st = () => ({screen: SCREEN, c: cur && cur.c, step: _g.step, depth: _g.stack.length, kase: _g.kase,
    peek: document.getElementById('peek').innerText, fast: document.querySelectorAll('[data-fast]').length});
  window.__tot = () => { const c = funnelCounts(); let t = 0; FUNNEL_ORDER.forEach(k => t += c[k]); return t; };
"""


def boot(pg, keep=False):
    out = pg.evaluate(BOOT, [rows, cov, keep])
    pg.evaluate('() => {' + HELP + '}')
    return out


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None)
    pg = b.new_page(viewport={'width': 390, 'height': 844})
    errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)))
    pg.on('dialog', lambda d: d.accept())
    pg.goto('file://' + PAGE)
    pg.wait_for_timeout(300)
    boot(pg)
    C0, C1, C2, C3 = (DIAL[k]['case'] for k in (0, 1, 2, 3))

    # ---------------- 1. the script position ----------------
    r1 = pg.evaluate("""async ([c0]) => {
      const o = {};
      o.opened = __open(c0);
      o.peek0 = __st().peek;
      await __walk(3); o.a = __st();
      screenRegistry(); o.areg = SCREEN; await __tap('#regback'); o.a2 = __st();
      await __dial(); await __walk(2); o.b = __st();
      o.inPool = pool().some(r => r.c === c0);
      await __tap('#lkbtn'); o.blk = SCREEN; await __tap('#lkback'); o.b2 = __st();
      await __tap('#ocback'); o.c = __st();
      o.reglink = !!document.getElementById('reglink');
      await __tap('#reglink'); o.creg = SCREEN; await __tap('#regback'); o.c2 = __st();
      render(); o.c3 = __st();
      await __tap('#cstatslink'); o.ccs = SCREEN; await __tap('#csback'); o.c4 = __st();
      screenRecord(c0); await __tap('#recback'); o.c5 = __st();
      o.stored = JSON.parse(localStorage.getItem('fcGuidedPos') || '{}');
      return o; }""", [C0])
    rec('1. the bar says "tap to start" before the walk begins', 'TAP TO START THE CALL' in r1['peek0'], r1['peek0'])
    rec('1. lead screen: call log and back keeps the lead and the step',
        r1['opened'] and r1['a']['depth'] == 3 and r1['areg'] == 'reg'
        and (r1['a2']['c'], r1['a2']['depth'], r1['a2']['step']) == (C0, 3, r1['a']['step']), r1)
    rec('1. the dial takes the lead out of the queue (the cause: pool() no longer holds it)',
        r1['b']['screen'] == 'outcome' and r1['b']['depth'] == 5 and r1['inPool'] is False, (r1['b'], r1['inPool']))
    rec('1. mid-call: Who texted me and back returns to the outcome screen, same lead, same step',
        r1['blk'] == 'lookup' and r1['b2']['screen'] == 'outcome' and r1['b2']['c'] == C0
        and r1['b2']['depth'] == 5 and r1['b2']['step'] == r1['b']['step'] and r1['b2']['fast'] >= 1, r1['b2'])
    rec('1. after Back to the numbers: the call log and back is still this lead, at this step',
        r1['c']['c'] == C0 and r1['reglink'] and r1['creg'] == 'reg'
        and (r1['c2']['screen'], r1['c2']['c'], r1['c2']['depth']) == ('lead', C0, 5), (r1['c'], r1['c2']))
    rec('1. a background repaint does not swap the lead under an open call',
        (r1['c3']['c'], r1['c3']['depth']) == (C0, 5), r1['c3'])
    rec('1. call stats and the record screen come back to the same lead and step',
        r1['ccs'] == 'cstats' and (r1['c4']['c'], r1['c4']['depth']) == (C0, 5)
        and (r1['c5']['c'], r1['c5']['depth']) == (C0, 5), (r1['c4'], r1['c5']))
    rec('1. the position is kept on this phone, by case, for today',
        r1['stored'].get('d') == dt.date.today().isoformat() and len((r1['stored'].get('m') or {}).get(C0, {}).get('k', [])) == 5,
        r1['stored'])

    # he is not held on the card: Next lead and a lane switch still move; a do-not-contact is not kept
    r1b = pg.evaluate("""async ([c0, c2]) => {
      const o = {};
      o.before = __st().c;
      await __tap('#skip'); o.sheet = SCREEN; await __tap('#cmpskip'); o.afterNext = __st().c;
      o.opened = __open(c2);
      await __dial(); await __tap('#ocback'); o.onC2 = __st().c;
      await __tap('.lane button[data-l="soon"]'); o.afterLane = __st().c; o.lane = lane;
      await __tap('.lane button[data-l="all"]');
      /* back on purpose, then a do-not-contact lands for that lead (a teammate's sync) */
      const hit = ROWS.filter(r => r.c === c2)[0]; cur = hit; phIdx = 0; screenLead(); o.back = __st().c;
      notes[c2].no = 'hard'; saveNotes();
      o.hard = !!hardSuppressed(hit);
      render(); o.afterHard = __st().c; o.inPool = pool().some(r => r.c === c2);
      return o; }""", [C0, C2])
    rec('1. Next lead still leaves a lead whose dial is pending', r1b['before'] == C0 and r1b['afterNext'] not in (None, C0), r1b)
    rec('1. a lane switch still leaves it', r1b['opened'] and r1b['onC2'] == C2 and r1b['lane'] == 'soon'
        and r1b['afterLane'] not in (None, C2), r1b)
    rec('1. GATE: a lead marked do-not-contact is never kept on screen by the open-call rule',
        r1b['back'] == C2 and r1b['hard'] and r1b['afterHard'] != C2 and r1b['inPool'] is False, r1b)

    # a reload in the middle of the call
    boot(pg)
    pg.evaluate("""async ([c1]) => { __open(c1); await __walk(2); await __dial(); await __walk(2); }""", [C1])
    before = pg.evaluate('() => __st()')
    pg.reload()
    pg.wait_for_timeout(300)
    boot(pg, keep=True)
    pg.wait_for_timeout(120)
    after = pg.evaluate("""() => { const s = __st(); s.guidedStep = (document.querySelector('#sbody .ltag') || {}).innerText || '';
      return s; }""")
    rec('1. reload mid-call: the outcome screen is back for the same lead',
        before['screen'] == 'outcome' and before['depth'] == 4 and after['screen'] == 'outcome' and after['c'] == C1, (before, after))
    rec('1. reload mid-call: the script under it is THIS lead\'s, at the step he was on',
        after['kase'] == C1 and after['depth'] == 4 and after['step'] == before['step'] and 'step 5' in after['guidedStep'], after)
    rec('1. reload mid-call: the bar names the step, not "tap to start"',
        'STEP 5' in after['peek'] and 'TAP TO START' not in after['peek'], after['peek'])

    # logging the call puts the next call at the greeting; another day's record is ignored
    r1c = pg.evaluate("""async ([c1, c3]) => {
      const o = {};
      await __tap('[data-fast="noanswer"]'); await __w(80);
      o.afterLog = __st(); o.dropped = !((JSON.parse(localStorage.getItem('fcGuidedPos') || '{}').m || {})[c1]);
      o.sameLeadNextNumber = (cur && cur.c) === c1;
      const m = {}; m[c3] = {s: 'frame', k: ['greet', 'disarm', 'nots'], r: null, t: 1};
      localStorage.setItem('fcGuidedPos', JSON.stringify({d: '2000-01-01', m: m}));
      o.opened = __open(c3); o.old = __st();
      localStorage.setItem('fcGuidedPos', JSON.stringify({d: today(), m: m}));
      _g.kase = null; renderSheet(cur); o.sameDay = __st();
      localStorage.setItem('fcGuidedPos', '{not json'); _g.kase = null; renderSheet(cur); o.torn = __st();
      return o; }""", [C1, C3])
    rec('1. a logged outcome: the next call to them opens at the greeting',
        r1c['sameLeadNextNumber'] and r1c['afterLog']['step'] == 'greet' and r1c['afterLog']['depth'] == 0 and r1c['dropped'], r1c)
    rec('1. a position from another day is ignored', r1c['opened'] and r1c['old']['step'] == 'greet', r1c['old'])
    rec('1. the same record dated today is picked up; a torn record falls back to the greeting',
        r1c['sameDay']['step'] == 'frame' and r1c['sameDay']['depth'] == 3 and r1c['torn']['step'] == 'greet', (r1c['sameDay'], r1c['torn']))

    # ---------------- 2. duplicates and the two totals ----------------
    boot(pg)
    r2 = pg.evaluate("""async () => {
      const o = {};
      const P = pool();
      o.cards = P.length; o.pairCards = P.filter(r => r.pk === 'PTWO').map(r => r.c); o.trioCards = P.filter(r => r.pk === 'PTRI').map(r => r.c);
      o.dupn = _DUPN; o.people = new Set(P.map(r => r.pk)).size;
      o.said = /2 more cases folded in/.test(document.getElementById('app').innerText);
      o.rowsLen = ROWS.length; o.covLen = COV.length; o.total0 = __tot();
      o.bar0 = (document.querySelector('.supn a[onclick*="BBAR_OPEN=true"]') || {}).innerText || '';
      /* the other case, opened on purpose (a board list, the lookup) */
      const sib = ROWS.filter(r => r.pk === 'PTWO' && r.c !== o.pairCards[0])[0].c;
      _openCase(sib); o.sib = sib; o.openedSib = cur && cur.c; o.pairAfterOpen = pool().filter(r => r.pk === 'PTWO').map(r => r.c);
      /* GATES: a no-answer on the one card closes BOTH cases for the cooldown ... */
      await __dial(); await __tap('[data-fast="noanswer"]'); await __w(60);
      o.pairAfterCall = ROWS.filter(r => r.pk === 'PTWO').map(r => [r.c, !!supReason(r, 'all').k]);
      o.pairInPool = pool().filter(r => r.pk === 'PTWO').length;
      /* ... and a hard no on one case closes the person */
      const t = ROWS.filter(r => r.pk === 'PTRI');
      notes[t[0].c] = {status: '', note: '', no: 'hard'}; saveNotes();
      o.trioHard = t.map(r => !!hardSuppressed(r)); o.trioInPool = pool().filter(r => r.pk === 'PTRI').length;
      return o; }""")
    rec('2. one card per person: each two-case owner is in the queue once',
        len(r2['pairCards']) == 1 and len(r2['trioCards']) == 1 and r2['cards'] == r2['people'] == len(rows) - 2, r2)
    rec('2. the folded cases are counted and said on the page', r2['dupn'] == 2 and r2['said'], r2)
    rec('2. a case opened on purpose is the one on the card, and its twin stays folded',
        r2['openedSib'] == r2['sib'] and r2['pairAfterOpen'] == [r2['sib']], r2)
    rec('2. GATE: a no-answer on the one card holds both cases (person-wide cooldown unchanged)',
        len(r2['pairAfterCall']) == 2 and all(x[1] for x in r2['pairAfterCall']) and r2['pairInPool'] == 0, r2['pairAfterCall'])
    rec('2. GATE: a hard no on one case closes the person (no twin slides into the freed slot)',
        r2['trioHard'] == [True, True] and r2['trioInPool'] == 0, r2)
    rec('2. the book total is the number of cases', r2['total0'] == len(SLIM) == r2['rowsLen'] + r2['covLen']
        and ('%d leads' % len(SLIM)) in r2['bar0'], (r2['total0'], r2['bar0']))

    r2b = pg.evaluate("""async ([lk]) => {
      const o = {};
      /* a number saved on a Find a number lead: this phone, the board, or a teammate's sync */
      notes[lk] = {status: '', note: '', phone: '3055550199'}; saveNotes(); _FCGEN++;
      SCREEN = 'lead'; render();
      o.inRows = ROWS.filter(r => r.c === lk).length; o.inCov = COV.filter(r => r.c === lk).length;
      o.copies = allLeads().filter(r => r.c === lk).length;
      const all = allLeads().map(r => r.c); o.unique = new Set(all).size === all.length; o.bookLen = all.length;
      o.total1 = __tot();
      o.lists = FUNNEL_ORDER.reduce((n, k) => n + funnelRows(k).filter(r => r.c === lk).length, 0);
      o.bar1 = (document.querySelector('.supn a[onclick*="BBAR_OPEN=true"]') || {}).innerText || '';
      return o; }""", [LK[0]['case']])
    rec('2. the cause: a saved number puts the lead in ROWS and leaves its coverage row in COV',
        r2b['inRows'] == 1 and r2b['inCov'] == 1, r2b)
    rec('2. it is in the book once, and the total did not move',
        r2b['copies'] == 1 and r2b['unique'] and r2b['bookLen'] == len(SLIM) and r2b['total1'] == r2['total0']
        and ('%d leads' % len(SLIM)) in r2b['bar1'], r2b)
    rec('2. it is in one board-lane list, not two', r2b['lists'] == 1, r2b)

    # ---------------- 3. the greeting ----------------
    boot(pg)
    ENTS = [['WE BUY EXAMPLE HOMES LLC', 'We Buy Example Homes Llc'], ['SUNSET SAMPLE CONDOMINIUM ASSOCIATION INC', 'Sunset Sample Condominium Associati'],
            ['ESTATE OF JANE SAMPLE', 'Estate Of Jane Sample'], ['JANE SAMPLE REVOCABLE TRUST', 'Jane Sample Revocable Trust'],
            ['FIRST EXAMPLE BANK NA', 'First Example Bank Na'], ['EXAMPLEBANK FSB', 'Examplebank Fsb'], ['SAMPLE JOHN TR', 'John Tr Sample'],
            ['THE SAMPLE FAMILY', 'The Sample Family'], ['OF EXAMPLE STREET HOLDINGS', 'Of Example Street Holdings'],
            ['DE LA SAMPLE MARIA', 'De La Sample Maria'], ['4 U PROPERTIES', '4 U Properties'], ['SAMPLE GROUP LP', 'Sample Group Lp'],
            ['CITY OF EXAMPLEVILLE', 'City Of Exampleville'], ['UNKNOWN HEIRS OF JOHN SAMPLE', 'Unknown Heirs Of John Sample'],
            ['EXAMPLE HOMEOWNERS ASSN', 'Example Homeowners Assn'], ['SAMPLE MORTGAGE SERVICING', 'Sample Mortgage Servicing'],
            ['SECRETARY OF HOUSING', 'Secretary Of Housing'], ['SAMPLE L.L.C.', 'Sample L.l.c.'], ['', 'We'], ['', 'Our Sample'], ['', 'Mr Sample'],
            ['', 'Llc'], ['', 'It'], ['', 'They Sample'], ['', 'Sample 2 Llc']]
    PEOPLE = [['SAMPLE, MARIA', 'Maria Sample', 'Maria'], ['JOHN A SAMPLE', 'John Sample', 'John'], ['WILL BANKS', 'Will Banks', 'Will'],
              ['ROSE CHURCH', 'Rose Church', 'Rose'], ['HOPE TEMPLE', 'Hope Temple', 'Hope'], ['SAMPLE, ANA', '', 'Ana'],
              ['ESTELLE SAMPLE', 'Estelle Sample', 'Estelle'], ['TRUDY SAMPLE', 'Trudy Sample', 'Trudy'], ['DELIA SAMPLE', 'Delia Sample', 'Delia'],
              ['TEST OWNER 2026-000800-CA-01', 'Test Owner', 'Test']]
    r3 = pg.evaluate("""([ents, people, dialC, lkC]) => {
      const o = {};
      o.ents = ents.map(e => firstName({o: e[0], on: e[1]}));
      o.people = people.map(e => firstName({o: e[0], on: e[1]}));
      o.second = [secondName({o: 'SAMPLE, JOHN; SAMPLE, MARIA'}), secondName({o: 'SAMPLE, JOHN; WE BUY EXAMPLE HOMES LLC'}),
                  secondName({o: 'SAMPLE, JOHN; THE SAMPLE TRUST'})];
      const d = ROWS.filter(r => r.c === dialC)[0];
      o.dialFirst = firstName(d); o.dialFlag = d.fl || '';
      __open(dialC); o.dialPeek = document.getElementById('peek').innerText;
      document.getElementById('grip').click();
      o.dialSheet = document.getElementById('sbody').innerText.slice(0, 600);
      document.getElementById('grip').click();
      const l = lkAsDial(COV.filter(r => r.c === lkC)[0], '');
      o.lkFirst = firstName(l); o.lkOn = l.on;
      const anon = fillScript(SCRIPT_ALL.op.aen, d);
      o.anon = anon.slice(0, 60);
      return o; }""", [ENTS, PEOPLE, ENT[0]['case'], '2026-001810-CA-01'])
    bad = [ENTS[k] for k, v in enumerate(r3['ents']) if v != '']
    rec('3. no entity, pronoun, article or fragment is ever a first name (%d shapes)' % len(ENTS), not bad, (bad, r3['ents']))
    wrong = [(PEOPLE[k], v) for k, v in enumerate(r3['people']) if v != PEOPLE[k][2]]
    rec('3. ordinary names are untouched (%d shapes, incl. Banks / Church / Temple as surnames)' % len(PEOPLE), not wrong, wrong)
    rec('3. the co-owner line reads the same way', r3['second'] == ['Maria', '', ''], r3['second'])
    rec('3. the reported lead: no first name on the dial row, with no pipeline flag',
        r3['dialFirst'] == '' and 'C' not in r3['dialFlag'], r3)
    rec('3. its bar and its script open with the neutral line, not "Hi, We?"',
        'Hi, We' not in r3['dialPeek'] and 'Hi, We' not in r3['dialSheet'] and 'Hi, ?' not in r3['dialSheet']
        and r3['dialPeek'].startswith("Hey, it's") and "You're the owner over on" in r3['dialSheet'], (r3['dialPeek'], r3['dialSheet'][:200]))
    rec('3. the same owner on Find a number',
        r3['lkFirst'] == '' and 'Example Homes' in (r3['lkOn'] or ''), (r3['lkFirst'], r3['lkOn']))

    # ---------------- 4. the script bar and the clock ----------------
    r4 = pg.evaluate("""async ([c0]) => {
      const o = {};
      __open(c0);
      const pk = () => document.getElementById('peek');
      o.closed0 = pk().innerText; o.h0 = pk().offsetHeight; o.lines = pk().querySelectorAll('.pk1').length;
      document.getElementById('grip').click(); await __w(20); o.open0 = pk().innerText;
      await __walk(3); o.open3 = pk().innerText; o.hdr = (document.querySelector('#sbody .ltag') || {}).innerText;
      document.getElementById('grip').click(); await __w(20); o.closed3 = pk().innerText; o.h3 = pk().offsetHeight;
      await __tap('#sbody [data-gto="restart"]'); o.restart = pk().innerText;
      o.wraps = [].map.call(pk().querySelectorAll('.pk1'), e => getComputedStyle(e).whiteSpace).join(',');
      return o; }""", [C0])
    rec('4. closed, not started: the opener and the start cue', 'TAP TO START THE CALL' in r4['closed0'] and 'Hi, Test?' in r4['closed0'], r4['closed0'])
    rec('4. open: it no longer says "tap to start" over the script', 'TAP TO START' not in r4['open0'] and 'close' in r4['open0'], r4['open0'])
    rec('4. after three steps the bar names step 4, open and closed (the label is not stuck)',
        'STEP 4' in r4['open3'] and 'TAP TO START' not in r4['open3'] and 'STEP 4' in r4['closed3']
        and 'TAP TO START' not in r4['closed3'] and 'step 4' in r4['hdr'], (r4['open3'], r4['closed3'], r4['hdr']))
    rec('4. Restart puts the start cue back', 'TAP TO START THE CALL' in r4['restart'], r4['restart'])
    rec('4. the bar is three single lines (no wrapping taller over the card)',
        r4['lines'] == 3 and r4['wraps'] == 'nowrap,nowrap,nowrap' and r4['h0'] <= 80 and r4['h3'] <= 80, r4)

    GEO = """async () => {
      const R = e => { const b = e.getBoundingClientRect(); return [Math.round(b.top), Math.round(b.bottom)]; };
      window.scrollTo(0, 0); await __w(60);
      const o = {y: window.scrollY, wf: R(document.getElementById('wfbar')), sheet: R(document.getElementById('sheet'))};
      const btns = [].slice.call(document.querySelectorAll('#wfbar button'));
      o.n = btns.length;
      o.reach = btns.every(b => { const q = b.getBoundingClientRect();
        const hit = document.elementFromPoint(q.left + q.width / 2, q.top + q.height / 2); return hit === b || b.contains(hit); });
      o.first = (document.querySelector('.top') || {}).firstElementChild && document.querySelector('.top').firstElementChild.id;
      return o; }"""
    for vw, vh in ((375, 553), (390, 664), (390, 844)):
        pg.set_viewport_size({'width': vw, 'height': vh})
        boot(pg)
        pg.wait_for_timeout(120)
        g0 = pg.evaluate(GEO)
        pg.evaluate("""async () => { await __tap('[data-wf="in60"]'); }""")
        pg.wait_for_timeout(120)
        g1 = pg.evaluate(GEO)
        rec('4. %dx%d: the clock is the first thing under the nav, clear of the script bar, every button tappable (not clocked in)' % (vw, vh),
            g0['first'] == 'wfbar' and g0['n'] >= 5 and g0['wf'][1] <= g0['sheet'][0] and g0['reach'], g0)
        rec('4. %dx%d: Pause / +30 min / End work are clear of the script bar and tappable (clocked in)' % (vw, vh),
            g1['n'] == 3 and g1['wf'][1] <= g1['sheet'][0] and g1['reach'], g1)
    pg.set_viewport_size({'width': 390, 'height': 844})

    # ---------------- 6. the number on a lane button is the list behind it ----------------
    boot(pg)
    CHECK = """async () => {
      const out = [];
      for(const v of ['untouched', 'retries', 'callbacks']){
        QVIEW = v; lane = 'all'; i = 0; _MOVE = 1; SCREEN = 'lead'; BLANE = null; render();
        const ks = [].map.call(document.querySelectorAll('.lane button[data-l]'), b => b.dataset.l);
        for(const k of ks){
          const b = document.querySelector('.lane button[data-l="' + k + '"]');
          const m = / · (\\d+)$/.exec(b.innerText.trim()); const shown = m ? +m[1] : 0;
          lane = k; i = 0; _MOVE = 1; render();
          out.push([v, k, shown, pool().length, document.querySelectorAll('#dial').length]);
        }
      }
      QVIEW = 'untouched'; lane = 'all'; i = 0; _MOVE = 1; render();
      return out; }"""
    r6 = pg.evaluate("""async () => {
      const o = {};
      o.eq0 = [].map.call(document.querySelectorAll('.lane button[data-l="eq"]'), b => b.innerText.trim())[0];
      /* the reported shape: every verified-equity lead was reached before (a baked email stamp),
         so none of them is in the default Untouched view */
      ROWS.forEach(r => { if(isEquity(r)) r.le = '11/10'; });
      lane = 'eq'; QVIEW = 'untouched'; i = 0; _MOVE = 1; SCREEN = 'lead'; render();
      o.btn = document.querySelector('.lane button[data-l="eq"]').innerText.trim();
      o.dim = document.querySelector('.lane button[data-l="eq"]').classList.contains('dim');
      o.list = pool().length; o.net = _LANEN.eq.net; o.raw = _LANEN.eq.raw;
      o.empty = document.querySelector('#app .card').innerText.slice(0, 160);
      await __tap('.lane button[data-v="retries"]');
      o.btnR = document.querySelector('.lane button[data-l="eq"]').innerText.trim(); o.listR = pool().length;
      return o; }""")
    rec('6. before: six verified-equity leads, button and list agree', r6['eq0'] == 'Has equity · 6', r6['eq0'])
    rec('6. the reported shape: all six reached before, Untouched view: the button says 0, the list is empty',
        r6['btn'] == 'Has equity · 0' and r6['dim'] and r6['list'] == 0 and r6['raw'] == 6 and r6['net'] == 6, r6)
    rec('6. the empty list says where they are', '6 reached out' in r6['empty'], r6['empty'])
    rec('6. in Reached out the same button says 6 and the list holds 6', r6['btnR'] == 'Has equity · 6' and r6['listR'] == 6, r6)
    m6 = pg.evaluate(CHECK)
    off = [x for x in m6 if x[2] != x[3]]
    rec('6. every lane button, in every view: the number is the length of the list (%d checks)' % len(m6), len(m6) >= 9 and not off, off)
    pg.evaluate("() => { localStorage.setItem('fcSeat', JSON.stringify({n: 2, i: 0, w: 'Tester'})); }")
    m6s = pg.evaluate(CHECK)
    offs = [x for x in m6s if x[2] != x[3]]
    tot_solo = sum(x[3] for x in m6 if x[1] == 'all')
    tot_seat = sum(x[3] for x in m6s if x[1] == 'all')
    rec('6. the same with the list split between two phones (%d checks, this seat holds %d of %d)' % (len(m6s), tot_seat, tot_solo),
        len(m6s) >= 9 and not offs and 0 < tot_seat < tot_solo, offs)
    pg.evaluate("() => { localStorage.removeItem('fcSeat'); }")

    rec('no page errors', not errs, errs[:3])
    b.close()

print('\n%d failure(s): %s' % (len(FAILS), '; '.join(FAILS)) if FAILS else '\nall passed')
sys.exit(1 if FAILS else 0)
