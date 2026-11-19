"""_calllogfasttest -- Call Mode: logging a call in one tap (2026-11-18).

Run:  python _calllogfasttest.py   (exit 0 = safe; invented 555-01xx numbers, file:// page, no network;
      the browser half skips when Chromium will not launch)

WHY: two callers dial all day on iPhones and logging each call was the slow part. Measured at 390x844
before this change, dial to "next lead open": No answer and Voicemail took 4 taps on a one-number lead
(dial, outcome, Next lead on the after-call panel, the Complete sheet) and 5 on a two-number lead (plus
"Call another number?"), and the outcome buttons sat about 700px below the fold under the reference
block and the script.

Pins:
  - the outcome screen opens scrolled to the top, and "No answer -> ..." / "Left voicemail -> ..." sit
    above the fold (above the collapsed script sheet) at 390x844
  - one tap logs the outcome and opens the next lead: 2 taps from dial to the next card, no after-call
    panel, no Complete sheet; the next card opens at its own top
  - a lead with another number not rung today goes to that number first (label says which), its dial
    button on screen; the last number goes to the next lead
  - the fast path writes exactly what the full button writes (one dial row replacing the pending one,
    one call touch, the same cooldown), and the lead leaves the queue the same way (gates unchanged)
  - a number held from voicemail (_vmHold, e.g. a Whitepages-pasted number) gets no voicemail button
  - a lead marked do-not-contact while the call was up does not go on to its next number
  - "Other outcome" scrolls the full outcome buttons into view
  - a reload while the outcome screen is up (iOS drops the tab during the call) reopens that outcome
    screen, once, so the call can still be logged; a stale, logged, hard-suppressed or wrong-day record
    does not reopen anything; Back to the numbers clears it
  - a lead opened from Find a number: one-tap No answer goes back to the list at the next lead, and
    so does the same tap after a reload mid-call
  - Paste & call when the browser cannot confirm the tap is still active: no dial is clicked or
    logged, the card shows a big "Tap to dial" on the dial button; tapping it dials and logs as
    before; with an active tap it still dials straight away
"""
import datetime as dt
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='calllogfast_')
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')
os.environ.pop('DEALFLOW_RELATIVE_DIAL', None)
os.environ.pop('DEALFLOW_FOLLOWUPS_ON', None)
import call_mode  # noqa: E402

FAILS = []
AUC = (dt.date.today() + dt.timedelta(days=29)).strftime('%m/%d/%Y')


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:500]) if extra and not ok else ''))
    if not ok:
        FAILS.append(name)


def lead(case, phones, **kw):
    d = {'case': case, 'county': 'MIAMI-DADE', 'st': 'FC', 'auction': AUC, 'days': 29, 'tier': 'B', 'score': 50,
         'value': 400000, 'owners': 'TEST OWNER ' + case, 'oname': 'Test Owner', 'addr': '100 NW 1 ST, MIAMI, FL 33101',
         'pkey': 'C' + case, 'phones': phones, 'phdnc': [False] * len(phones), 'phsrc': ['st'] * len(phones),
         'phtype': ['mobile'] * len(phones)}
    d.update(kw)
    return d


# dial leads: even = one number, odd = two numbers (different last four digits)
DIAL = [lead('2026-0009%02d-CA-01' % i, ['30555501%02d' % i] + (['78655502%02d' % i] if i % 2 else []))
        for i in range(24)]
# Find a number leads (no phone)
LK = [lead('2026-0019%02d-CA-01' % i, []) for i in range(4)]
SLIM = DIAL + LK
sink = []
rows, _ = call_mode.call_rows(SLIM, None, None, lookup=sink)
cov, _ = call_mode.coverage_rows(SLIM, [r['c'] for r in rows])
call_mode.mark_lookup(cov, sink, {r['pk']: r['sb'] for r in rows})
lkc = [c['c'] for c in cov if c.get('lk')]
rec('setup: 24 dial leads and 4 on Find a number', len(rows) == 24 and len(lkc) == 4, (len(rows), len(lkc)))

src = open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
rec('fast buttons log through logOutcome() and leave through advance()',
    'var _fresh = logOutcome(r, o, d);' in src and 'advance(r.c, nextC);' in src)
rec('the full No answer button still opens the after-call panel (text offer kept)',
    'afterCall(r,o,nextC); };' in src)

try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as _p:
        _p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None).close()
except Exception as e:
    print('SKIP browser half (%s)' % str(e).splitlines()[0][:200])
    print('\n%d failure(s)' % len(FAILS) if FAILS else '\nall passed')
    sys.exit(1 if FAILS else 0)

tpl = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
html = call_mode.build_html([], 0, {'ct': 'x', 'k': 'y'}, 'built', 'sig', 'bsig',
                            sync_js=call_mode.extract_sync_js(tpl), funnel_js=call_mode.extract_funnel_js(tpl),
                            text_js=call_mode.extract_text_js(tpl),
                            mail_js=call_mode.bake_mail_js(call_mode.extract_mail_js(tpl), 'logfast test'),
                            histcov='{"ledgers_ok": true, "why": "", "capped": false, "total": 28, "shipped": 28}')
PAGE = os.path.join(_T, 'call.html')
open(PAGE, 'w', encoding='utf-8').write(html)

BOOT = """([rows, cov, keep]) => {
  if(!keep){ try { localStorage.clear(); } catch(e) {} notes = {}; } else { notes = {}; loadNotes(); }
  ROWS = rows; COV = cov;
  flClock = function(){ return {ok:true, txt:'noon'}; };
  if(!window.__telHook){ window.__telHook = 1;
    document.addEventListener('click', ev => { const a = ev.target.closest && ev.target.closest('a[href^="tel:"]');
      if(a) ev.preventDefault(); }, true); }
  start();
  return {screen: SCREEN, c: cur && cur.c, blane: BLANE, from: LKFROM};
}"""

HELP = """
  window.__w = ms => new Promise(res => setTimeout(res, ms));
  window.__top = sel => { const e = document.querySelector(sel); return e ? Math.round(e.getBoundingClientRect().top) : null; };
  window.__bot = sel => { const e = document.querySelector(sel); return e ? Math.round(e.getBoundingClientRect().bottom) : null; };
  window.__open = c => { window._tapShieldUntil = 0; SCREEN = 'lead'; BLANE = null; const P = pool();
    const k = P.findIndex(r => r.c === c); if(k >= 0){ i = k; render(); } return cur && cur.c === c; };
  window.__first = n => pool().find(r => r.p.length === n && !((notes[r.c] || {}).dials || []).length).c;
  window.__dial = async () => { window._tapShieldUntil = 0;
    document.getElementById('dial').scrollIntoView({block: 'center'});
    document.getElementById('dial').click(); await __w(40); };
  window.__fast = async k => { window._tapShieldUntil = 0;
    document.querySelector('[data-fast="' + k + '"]').click(); await __w(60); };
  window.__strip = n => JSON.stringify({dials: (n.dials || []).map(x => [x.oc, x.ph4, x.by || '']),
    touches: (n.touches || []).map(x => [x.ch, x.out]), cd: n.cooldownH, st: n.status || ''});
"""

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None)
    pg = b.new_page(viewport={'width': 390, 'height': 844})
    errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)))
    pg.on('dialog', lambda d: d.accept())
    pg.goto('file://' + PAGE)
    pg.wait_for_timeout(300)
    pg.evaluate(BOOT, [rows, cov, False])
    pg.evaluate('() => {' + HELP + '}')

    # 1. one-number lead: dial -> fast No answer -> next lead
    r1 = pg.evaluate("""async () => {
      const o = {}, c = __first(1); __open(c);
      await __dial();
      o.screen = SCREEN; o.y = window.scrollY;
      const sh = document.getElementById('sheet').getBoundingClientRect();
      o.sheetTop = Math.round(sh.top);
      o.na = [__top('[data-fast="noanswer"]'), __bot('[data-fast="noanswer"]')];
      o.vm = [__top('[data-fast="voicemail"]'), __bot('[data-fast="voicemail"]')];
      o.label = document.querySelector('[data-fast="noanswer"]').textContent;
      o.ocTop = __top('.oc');
      await __fast('noanswer');
      o.after = SCREEN; o.moved = !!cur && cur.c !== c; o.blane = BLANE;
      o.cardTop = __top('#app .card');
      o.note = __strip(notes[c]);
      o.inPool = pool().some(r => r.c === c);
      o.stored = localStorage.getItem(_OCK);
      return o; }""")
    rec('dial opens the outcome screen at the top', r1['screen'] == 'outcome' and r1['y'] == 0, r1)
    rec('No answer and Left voicemail are above the fold and above the script sheet',
        0 < r1['na'][0] and r1['vm'][1] <= min(844, r1['sheetTop']), r1)
    rec('...while the full outcome list is below it (why the fast row exists)', r1['ocTop'] > 844, r1)
    rec('one-number lead: the label says "next lead"', r1['label'].endswith('next lead'), r1['label'])
    rec('one tap: logged and the next lead card is open (2 taps from dial)',
        r1['after'] == 'lead' and r1['moved'] and r1['blane'] is None, r1)
    rec('the next card opens at its own top', r1['cardTop'] is not None and 0 <= r1['cardTop'] <= 20, r1)
    rec('the lead it logged leaves the queue (cooldown applies)', not r1['inPool'], r1)
    rec('nothing left in the reload record after logging', r1['stored'] is None, r1)

    # 2. the fast path writes what the full button writes
    r2 = pg.evaluate("""async () => {
      const a = __first(1); __open(a); await __dial(); await __fast('noanswer');
      const b = __first(1); __open(b); await __dial();
      window._tapShieldUntil = 0; document.querySelector('.oc button[data-oc="noanswer"]').click(); await __w(60);
      const viaFull = SCREEN;
      return {fast: __strip(notes[a]), full: __strip(notes[b]), viaFull: viaFull}; }""")
    fa, fu = json.loads(r2['fast']), json.loads(r2['full'])
    rec('fast No answer writes the same note as the full No answer button',
        fa['touches'] == fu['touches'] and fa['cd'] == fu['cd'] == 24 and fa['st'] == fu['st']
        and [x[0] for x in fa['dials']] == [x[0] for x in fu['dials']] == ['noanswer'], r2)
    rec('the full No answer button still goes to the after-call panel', r2['viaFull'] == 'after', r2)
    pg.evaluate("() => { SCREEN = 'lead'; BLANE = null; render(); }")

    # 3. two-number lead: number 2 first, then the next lead; voicemail the same way
    r3 = pg.evaluate("""async () => {
      const o = {}, c = __first(2); __open(c); phIdx = 0; screenLead();
      await __dial();
      o.l1 = document.querySelector('[data-fast="voicemail"]').textContent;
      await __fast('voicemail');
      o.s1 = SCREEN; o.same = cur && cur.c === c; o.ph = phIdx;
      o.dialTop = __top('#dial'); o.dialBot = __bot('#dial');
      o.sheetTop = Math.round(document.getElementById('sheet').getBoundingClientRect().top);
      await __dial();
      o.l2 = document.querySelector('[data-fast="voicemail"]').textContent;
      await __fast('voicemail');
      o.s2 = SCREEN; o.moved = cur && cur.c !== c;
      o.note = __strip(notes[c]);
      return o; }""")
    rec('two-number lead: the label names number 2', r3['l1'].endswith('number 2 of 2'), r3['l1'])
    rec('...one tap puts number 2 on the same card, dial button on screen',
        r3['s1'] == 'lead' and r3['same'] and r3['ph'] == 1 and 0 <= r3['dialTop'] and r3['dialBot'] <= r3['sheetTop'], r3)
    rec('...the last number says "next lead" and goes there', r3['l2'].endswith('next lead') and r3['s2'] == 'lead' and r3['moved'], r3)
    rec('...two voicemail rows, one per number, no pending row left',
        [x[0] for x in json.loads(r3['note'])['dials']] == ['voicemail', 'voicemail'], r3['note'])

    # 4. do-not-contact landing mid-call: no next number, the lead is gone from the queue
    r4 = pg.evaluate("""async () => {
      const c = __first(2); __open(c); phIdx = 0; screenLead(); await __dial();
      notes[c].status = 'DO NOT CONTACT'; notes[c].optout = today();
      await __fast('noanswer');
      return {screen: SCREEN, c: cur && cur.c, was: c, inPool: pool().some(r => r.c === c)}; }""")
    rec('marked do-not-contact during the call: not sent on to its next number', r4['c'] != r4['was'] and not r4['inPool'], r4)

    # 5. "Other outcome" scrolls the full list into view
    r5 = pg.evaluate("""async () => {
      const c = __first(1); __open(c); await __dial();
      document.getElementById('ocjump').click(); await __w(50);
      const t = __top('.oc');
      window._tapShieldUntil = 0; document.querySelector('.oc button[data-oc="notint"]').click(); await __w(60);
      return {t: t, vh: innerHeight, screen: SCREEN}; }""")
    rec('Other outcome: the full outcome buttons scroll to the top of the screen', 0 <= r5['t'] < 120, r5)

    # 6. voicemail held for a number that is not the owner's confirmed one
    r6 = pg.evaluate("""async () => {
      const c = __first(1); __open(c); cur.ps = ['wpm']; await __dial();
      const o = {vm: !!document.querySelector('[data-fast="voicemail"]'), na: !!document.querySelector('[data-fast="noanswer"]')};
      delete cur.ps; await __fast('noanswer'); return o; }""")
    rec('a voicemail-held number gets No answer only, no voicemail button', r6['na'] and not r6['vm'], r6)

    # 7. reload mid-call reopens the outcome screen once
    c7 = pg.evaluate("async () => { const c = __first(1); __open(c); await __dial(); return c; }")
    pg.reload()
    pg.wait_for_timeout(300)
    r7 = pg.evaluate(BOOT, [rows, cov, True])
    pg.evaluate('() => {' + HELP + '}')
    rec('reload during the call reopens that outcome screen', r7['screen'] == 'outcome' and r7['c'] == c7, r7)
    r7b = pg.evaluate("""async (c) => { await __fast('noanswer');
      return {screen: SCREEN, moved: cur && cur.c !== c, note: __strip(notes[c]), stored: localStorage.getItem(_OCK)}; }""", c7)
    rec('...and one tap logs it: one no-answer row, the pending one replaced',
        r7b['moved'] and [x[0] for x in json.loads(r7b['note'])['dials']] == ['noanswer']
        and r7b['stored'] is None, r7b)

    # 7c. a second reload after logging reopens nothing
    pg.reload()
    pg.wait_for_timeout(300)
    r7c = pg.evaluate(BOOT, [rows, cov, True])
    pg.evaluate('() => {' + HELP + '}')
    rec('a reload after the outcome is logged does not reopen it', r7c['screen'] == 'lead', r7c)

    # 7d. stale / wrong day / hard-suppressed / already-logged records reopen nothing and are cleared
    r7d = pg.evaluate("""async () => {
      const o = {}, c = __first(1); __open(c); await __dial();
      const rec0 = localStorage.getItem(_OCK);
      const tryIt = (mut) => { SCREEN = 'lead'; const s = JSON.parse(rec0); mut(s); localStorage.setItem(_OCK, JSON.stringify(s));
        const r = _ocResume(); const left = localStorage.getItem(_OCK); SCREEN = 'lead'; return [r, left === null]; };
      o.ok = (() => { SCREEN = 'lead'; localStorage.setItem(_OCK, rec0); const r = _ocResume(); SCREEN = 'lead'; return r; })();
      o.stale = tryIt(s => { s.ts = Date.now() - 3 * 3600000; });
      o.day = tryIt(s => { s.d = '2000-01-01'; });
      o.num = tryIt(s => { s.ph4 = 'zzzz'; });
      o.gone = tryIt(s => { s.c = 'NOPE'; });
      notes[c].status = 'DO NOT CONTACT'; notes[c].optout = today();
      o.hard = tryIt(s => {});
      delete notes[c].optout; notes[c].status = '';
      logOutcome(cur, OUTCOMES.filter(z => z.k === 'noanswer')[0], cur.p[0]);
      o.logged = tryIt(s => {});
      render();
      return o; }""")
    rec('a fresh record reopens the outcome screen', r7d['ok'] is True, r7d)
    rec('older than two hours: nothing reopened, record cleared', r7d['stale'] == [False, True], r7d)
    rec('another day: nothing reopened, record cleared', r7d['day'] == [False, True], r7d)
    rec('the number no longer on the lead: nothing reopened', r7d['num'] == [False, True], r7d)
    rec('the lead no longer on the page: nothing reopened', r7d['gone'] == [False, True], r7d)
    rec('the lead hard-suppressed since: nothing reopened (gate unchanged)', r7d['hard'] == [False, True], r7d)
    rec('the outcome already logged: nothing reopened', r7d['logged'] == [False, True], r7d)

    # 7e. Back to the numbers clears the record
    r7e = pg.evaluate("""async () => { const c = __first(1); __open(c); await __dial();
      const had = !!localStorage.getItem(_OCK); window._tapShieldUntil = 0; document.getElementById('ocback').click();
      const o = {had: had, cleared: localStorage.getItem(_OCK) === null, screen: SCREEN};
      SCREEN = 'lead'; BLANE = null; render(); return o; }""")
    rec('Back to the numbers clears the reload record', r7e['had'] and r7e['cleared'] and r7e['screen'] == 'lead', r7e)

    # 8. Find a number lead: fast No answer returns to the list at the next lead
    r8 = pg.evaluate("""async (C) => {
      const o = {};
      window._tapShieldUntil = 0; BLANE = '__lk'; SCREEN = 'lead'; render();
      const inp = document.querySelector('input.lknum[data-c="' + C[0] + '"]');
      inp.value = '(305) 555-0177';
      document.querySelector('button.lkadd[data-c="' + C[0] + '"]').click();
      await __w(40);
      o.screen = SCREEN; o.from = LKFROM; o.c = cur && cur.c;
      o.vm = !!document.querySelector('[data-fast="voicemail"]');
      if(SCREEN !== 'outcome') return o;
      await __fast('noanswer');
      o.after = [SCREEN, BLANE, LKFROM, LKFOCUS];
      o.note = __strip(notes[C[0]]);
      return o; }""", lkc)
    rec('Find a number: typed + Call dials and opens the outcome screen for that lead',
        r8['screen'] == 'outcome' and r8['from'] == lkc[0] and r8['c'] == lkc[0], r8)
    rec('...a Whitepages-pasted number has no voicemail button (voicemail hold)', not r8['vm'], r8)
    rec('...one tap No answer goes back to Find a number at the next lead',
        r8['after'][0] == 'lead' and r8['after'][1] == '__lk' and r8['after'][2] is None and r8['after'][3] in lkc[1:], r8)

    # 9. Find a number lead + reload mid-call: back on its outcome screen, then back to the list
    r9a = pg.evaluate("""async (C) => {
      window._tapShieldUntil = 0; BLANE = '__lk'; SCREEN = 'lead'; render();
      document.querySelector('input.lknum[data-c="' + C[1] + '"]').value = '(305) 555-0178';
      document.querySelector('button.lkadd[data-c="' + C[1] + '"]').click(); await __w(40);
      return {screen: SCREEN, from: LKFROM}; }""", lkc)
    rec('setup: a second Find a number lead is mid-call', r9a['screen'] == 'outcome' and r9a['from'] == lkc[1], r9a)
    pg.reload()
    pg.wait_for_timeout(300)
    r9 = pg.evaluate(BOOT, [rows, cov, True])
    pg.evaluate('() => {' + HELP + '}')
    rec('reload mid-call on a Find a number lead reopens its outcome screen with the way back',
        r9['screen'] == 'outcome' and r9['c'] == lkc[1] and r9['from'] == lkc[1], r9)
    r9b = pg.evaluate("async () => { await __fast('noanswer'); return [SCREEN, BLANE, LKFROM]; }")
    rec('...and logging it goes back to the Find a number list', r9b == ['lead', '__lk', None], r9b)

    # 10. Paste & call without an active tap: Tap to dial, nothing logged until he taps
    r10 = pg.evaluate("""async (C) => {
      const o = {};
      Object.defineProperty(navigator, 'userActivation', {configurable: true, value: {isActive: false}});
      window._tapShieldUntil = 0; BLANE = '__lk'; SCREEN = 'lead'; render();
      Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {readText: () => Promise.resolve('Jo Test (786) 555-0181 Mobile')}});
      document.querySelector('button.lkclip[data-c="' + C[2] + '"]').click();
      await __w(150);
      const d = document.getElementById('dial');
      o.screen = SCREEN; o.c = cur && cur.c; o.from = LKFROM;
      o.tap = d && d.classList.contains('tapdial'); o.txt = d && d.textContent;
      o.dials = ((notes[C[2]] || {}).dials || []).length;
      o.top = __top('#dial'); o.bot = __bot('#dial');
      o.sheetTop = Math.round(document.getElementById('sheet').getBoundingClientRect().top);
      window._tapShieldUntil = 0; d.click(); await __w(40);
      o.after = SCREEN; o.dials2 = ((notes[C[2]] || {}).dials || []).map(x => x.oc);
      await __fast('noanswer');
      o.back = [SCREEN, BLANE];
      /* with an active tap it dials straight away, as before */
      Object.defineProperty(navigator, 'userActivation', {configurable: true, value: {isActive: true}});
      Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {readText: () => Promise.resolve('(786) 555-0182')}});
      window._tapShieldUntil = 0; BLANE = '__lk'; SCREEN = 'lead'; render();
      document.querySelector('button.lkclip[data-c="' + C[3] + '"]').click();
      await __w(150);
      o.live = [SCREEN, ((notes[C[3]] || {}).dials || []).map(x => x.oc).join(',')];
      return o; }""", lkc)
    rec('Paste & call, tap not active: the number is saved and its card opens, no dial logged',
        r10['screen'] == 'lead' and r10['c'] == lkc[2] and r10['from'] == lkc[2] and r10['dials'] == 0, r10)
    rec('...the dial button says "Tap to dial", big, on screen',
        r10['tap'] and 'Tap to dial' in (r10['txt'] or '') and 0 <= r10['top'] and r10['bot'] <= r10['sheetTop'], r10)
    rec('...tapping it dials, logs the attempt and opens the outcome screen',
        r10['after'] == 'outcome' and r10['dials2'] == ['pending'], r10)
    rec('...and the call logs back to Find a number', r10['back'] == ['lead', '__lk'], r10)
    rec('Paste & call with an active tap still dials straight away', r10['live'] == ['outcome', 'pending'], r10)
    b.close()

rec('no script error on the page', not errs, errs)
print('\n%d failure(s)' % len(FAILS) if FAILS else '\nall passed')
sys.exit(1 if FAILS else 0)
