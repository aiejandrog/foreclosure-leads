"""_calllkpastetest -- Find a number: Paste & call, numbers read out of messy Whitepages text.

Run:  python _calllkpastetest.py   (exit 0 = safe; invented 555-01xx numbers, no network)

WHY (2026-11-18, Carlos via Alejandro): on the Find a number list the slow part is copy, paste into
the box, call, log. The list card had no Paste button (the lead card did), and the box took only a
clean 10-digit number, so a Whitepages copy with an age or ZIP in it was refused.

Pins:
  - _lkPhones pulls each number out of free text, in order, deduped; ages, ZIPs, house numbers and
    11+ digit runs are not read as numbers; +1 and (NPA) forms work
  - typing a messy Whitepages block into the box and tapping Call saves and opens the card
  - Paste & call (clipboard stubbed) with one number saves, opens the card on it, LKFROM set
  - two numbers in the clipboard show one button per number; tapping one saves that number
  - a do-not-call number pasted is still refused (nothing loosened)
  - an empty / number-free clipboard saves nothing
  - back from Whitepages: the visibilitychange handler scrolls the searched card into view
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='lkpaste_')
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')
import call_mode  # noqa: E402

FAILS = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:400]) if extra and not ok else ''))
    if not ok:
        FAILS.append(name)


def lead(case, **kw):
    d = {'case': case, 'county': 'MIAMI-DADE', 'st': 'FC', 'auction': '11/20/2026', 'days': 29,
         'tier': 'B', 'score': 50, 'value': 400000, 'owners': 'TEST OWNER ' + case,
         'oname': 'Test Owner', 'addr': '100 NW 1 ST, MIAMI, FL 33101', 'pkey': 'C' + case}
    d.update(kw)
    return d


DN = lead('2026-000900-CA-01', phones=['3055550190'], phdnc=[True], phsrc=['st'], phtype=['mobile'])
L = [lead('2026-00091%d-CA-01' % i) for i in range(6)]
SLIM = [DN] + L
sink = []
rows, _ = call_mode.call_rows(SLIM, None, None, lookup=sink)
cov, _ = call_mode.coverage_rows(SLIM, [r['c'] for r in rows])
call_mode.mark_lookup(cov, sink, {r['pk']: r['sb'] for r in rows})
lk = [c for c in cov if c.get('lk')]
rec('setup: six leads on Find a number', len(lk) == 6, len(lk))

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
                            histcov='{"ledgers_ok": true, "why": "", "capped": false, "total": 6, "shipped": 6}')
path = os.path.join(_T, 'call.html')
open(path, 'w', encoding='utf-8').write(html)

C = [c['c'] for c in lk]
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None)
    pg = b.new_page(viewport={'width': 390, 'height': 800})
    errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)))
    pg.goto('file://' + path)
    pg.wait_for_timeout(400)

    ex = pg.evaluate("""() => ({
      wp: _lkPhones('Jane Doe, Age 54\\n(305) 555-0123 Mobile\\n12345 SW 88 St Miami FL 33186'),
      two: _lkPhones('(305) 555-0123 Mobile  786.555.0144 Landline  (305) 555-0123'),
      plus: _lkPhones('+1 305-555-0155'),
      bare: _lkPhones('3055550166'),
      age: _lkPhones('Age 54 33172 1234 Main St'),
      long: _lkPhones('folio 3021050160820 case 20260115'),
      zero: _lkPhones('(105) 555-0123'),
      none: _lkPhones(''),
      glued: _lkPhones('acct 30555501234'),
      lead: _lkPhones('x13055550123'),
      email: _lkPhones('maria3055550123@example.com'),
      intl: _lkPhones('+57 300 555 0123'),
      plus1: _lkPhones('+13055550124'),
      spaced: _lkPhones('305 - 555 - 0125')
    })""")
    rec('a number glued to an extra trailing digit is not read', ex['glued'] == [], ex)
    rec('digits after a letter still read only when the run is a real number', ex['lead'] == ['3055550123'], ex)
    rec('digits inside an email address are not a phone', ex['email'] == [], ex)
    rec('a foreign number (+57) is not read as a US number', ex['intl'] == [], ex)
    rec('+1 glued to the number still reads', ex['plus1'] == ['3055550124'], ex)
    rec('wide " - " separators read', ex['spaced'] == ['3055550125'], ex)
    rec('Whitepages block with age, house number and ZIP -> just the phone', ex['wp'] == ['3055550123'], ex)
    rec('two numbers, in order, duplicate dropped', ex['two'] == ['3055550123', '7865550144'], ex)
    rec('+1 form', ex['plus'] == ['3055550155'], ex)
    rec('bare 10 digits', ex['bare'] == ['3055550166'], ex)
    rec('age / ZIP / house number are not numbers', ex['age'] == [], ex)
    rec('a 13-digit folio and a case number are not numbers', ex['long'] == [], ex)
    rec('area code starting 1 is not a number', ex['zero'] == [], ex)
    rec('empty text -> none', ex['none'] == [], ex)

    out = pg.evaluate("""([cov, C, DNUM]) => {
      const o = {};
      try { localStorage.clear(); } catch(e) {}
      notes = {}; ROWS = []; COV = cov;
      if (typeof DNCALL !== 'undefined') DNCALL = [DNUM];
      start();
      BLANE = '__lk'; render();
      o.clipBtns = document.querySelectorAll('button.lkclip').length;
      const back = () => { BLANE = '__lk'; SCREEN = 'lead'; render(); };
      let CLIP = '';
      Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {readText: () => Promise.resolve(CLIP)}});
      return o;
    }""", [lk, C, '3055550190'])
    rec('a Paste & call button on every list card', out['clipBtns'] == 6, out)

    # 1. typed messy block -> Call
    r1 = pg.evaluate("""(c) => {
      BLANE = '__lk'; SCREEN = 'lead'; render();
      const inp = document.querySelector('input.lknum[data-c="' + c + '"]');
      inp.value = 'John Test, Age 61 (305) 555-0171 Mobile 33101';
      document.querySelector('button.lkadd[data-c="' + c + '"]').click();
      return {saved: (notes[c] || {}).phone || '', cur: cur && cur.c, p: (ROWS.find(r => r.c === c) || {}).p || null};
    }""", C[0])
    rec('typed Whitepages block + Call saves the number', r1['saved'] == '3055550171', r1)
    rec('...and opens that lead with that one number', r1['cur'] == C[0] and r1['p'] == ['3055550171'], r1)

    def clip(text, c):
        return pg.evaluate("""async ([t, c]) => {
          BLANE = '__lk'; SCREEN = 'lead'; render();
          Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {readText: () => Promise.resolve(t)}});
          document.querySelector('button.lkclip[data-c="' + c + '"]').click();
          await new Promise(r => setTimeout(r, 150));
          return {saved: (notes[c] || {}).phone || '', cur: cur && cur.c, blane: BLANE, from: LKFROM,
                  picks: Array.from(document.querySelectorAll('button.lkpn[data-c="' + c + '"]')).map(b => b.getAttribute('data-n')),
                  onList: lkRows().some(r => r.c === c)};
        }""", [text, c])

    # 2. Paste & call, one number
    r2 = clip('Mary Test\n(786) 555-0182\nLandline', C[1])
    rec('Paste & call with one number saves it', r2['saved'] == '7865550182', r2)
    rec('...opens its card and remembers the way back to the list', r2['cur'] == C[1] and r2['from'] == C[1], r2)
    rec('...and the lead leaves Find a number', not r2['onList'], r2)

    # 3. two numbers -> picks, tap the second
    r3 = clip('(305) 555-0183 Mobile\n(954) 555-0184 Landline', C[2])
    rec('two numbers on the clipboard: nothing saved yet', r3['saved'] == '' and r3['onList'], r3)
    rec('...one button per number', r3['picks'] == ['3055550183', '9545550184'], r3)
    r3b = pg.evaluate("""(c) => {
      document.querySelector('button.lkpn[data-c="' + c + '"][data-n="9545550184"]').click();
      return {saved: (notes[c] || {}).phone || '', cur: cur && cur.c};
    }""", C[2])
    rec('...tapping one saves that number and opens the card', r3b['saved'] == '9545550184' and r3b['cur'] == C[2], r3b)

    # 4. do-not-call number pasted is refused
    r4 = clip('(305) 555-0190 Mobile', C[3])
    rec('a do-not-call number pasted is refused (nothing saved)', r4['saved'] == '' and r4['onList'], r4)

    # 5. clipboard with no number
    r5 = clip('Jane Test Age 44 Miami FL 33101', C[4])
    rec('a clipboard with no number saves nothing', r5['saved'] == '' and r5['onList'], r5)

    # 5b. typed text with no phone never glues digits into a number
    r5b = pg.evaluate("""(c) => { BLANE = '__lk'; SCREEN = 'lead'; render();
      lkSave(c, 'Age 54 ZIP 33172 Apt 123'); return (notes[c] || {}).phone || ''; }""", C[4])
    rec('"Age 54 ZIP 33172 Apt 123" saves nothing (no glued number)', r5b == '', r5b)

    # 5c. typed box with two numbers, the second past 200 characters -> both buttons
    r5c = pg.evaluate("""(c) => { BLANE = '__lk'; SCREEN = 'lead'; render();
      lkSave(c, '(305) 555-0191 ' + 'x'.repeat(250) + ' (786) 555-0192');
      return {saved: (notes[c] || {}).phone || '',
              picks: Array.from(document.querySelectorAll('button.lkpn[data-c="' + c + '"]')).map(b => b.getAttribute('data-n'))}; }""", C[4])
    rec('long typed paste with two numbers: nothing saved, a button for each', r5c['saved'] == '' and r5c['picks'] == ['3055550191', '7865550192'], r5c)

    # 5d. hardSuppressed blocks a pick: marked do-not-contact after the list was drawn
    r5d = pg.evaluate("""(c) => { notes[c] = Object.assign(notes[c] || {}, {status: 'DO NOT CONTACT'});
      lkSave(c, '3055550193'); return {phone: (notes[c] || {}).phone || '', row: ROWS.some(r => r.c === c)}; }""", C[4])
    rec('a do-not-contact lead refuses a typed/picked number (hardSuppressed)', r5d['phone'] == '' and not r5d['row'], r5d)

    # 5e. the email route: an address alone goes to the composer, an address next to a number dials
    r5e = pg.evaluate("""(c) => { const o = {}; let mailed = null; const keep = screenMail;
      screenMail = function(r, to){ mailed = to; };
      BLANE = '__lk'; SCREEN = 'lead'; render();
      let inp = document.querySelector('input.lknum[data-c="' + c + '"]');
      inp.value = 'maria3055550123@example.com';
      document.querySelector('button.lkadd[data-c="' + c + '"]').click();
      o.mailed = mailed; o.phone1 = (notes[c] || {}).phone || '';
      mailed = null; BLANE = '__lk'; SCREEN = 'lead'; render();
      inp = document.querySelector('input.lknum[data-c="' + c + '"]');
      inp.value = 'jo@example.com (305) 555-0194';
      document.querySelector('button.lkadd[data-c="' + c + '"]').click();
      o.mailed2 = mailed; o.phone2 = (notes[c] || {}).phone || '';
      screenMail = keep; return o; }""", C[3])
    rec('an email with digits in it goes to the email composer, not dialed', r5e['mailed'] == 'maria3055550123@example.com' and r5e['phone1'] == '', r5e)
    rec('a number next to an email dials the number', r5e['mailed2'] is None and r5e['phone2'] == '3055550194', r5e)

    # 6. back from Whitepages scrolls the searched card into view
    r6 = pg.evaluate("""async (c) => {
      BLANE = '__lk'; SCREEN = 'lead'; render(); window.scrollTo(0, 0);
      const a = document.querySelector('.lkcard[data-lkc="' + c + '"] .flinks a');
      a.addEventListener('click', e => e.preventDefault());
      a.click();
      render(); window.scrollTo(0, 0);
      Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'visible'});
      document.dispatchEvent(new Event('visibilitychange'));
      await new Promise(r => setTimeout(r, 100));
      const el = document.querySelector('.lkcard[data-lkc="' + c + '"]').getBoundingClientRect();
      const btn = document.querySelector('.lkcard[data-lkc="' + c + '"] button.lkclip');
      return {top: el.top, bottom: el.bottom, vh: innerHeight, ring: btn && btn.style.boxShadow};
    }""", C[5])
    rec('back from Whitepages: the searched card is on screen', r6['top'] < r6['vh'] and r6['bottom'] > 0, r6)
    rec('...and its Paste & call button is ringed', bool(r6['ring']), r6)

    # 7. after 20 minutes away the page leaves the scroll alone
    r7 = pg.evaluate("""async (c) => {
      BLANE = '__lk'; SCREEN = 'lead'; render(); window.scrollTo(0, 0);
      _LKWP = {c: c, t: Date.now() - 21 * 60000};
      document.dispatchEvent(new Event('visibilitychange'));
      await new Promise(r => setTimeout(r, 100));
      return {y: window.scrollY, left: _LKWP}; }""", C[5])
    rec('after 20 minutes the return does not jump the list', r7['y'] == 0 and r7['left'] is None, r7)
    b.close()

rec('no script error on the page', not errs, errs)
print('\n%d failure(s)' % len(FAILS) if FAILS else '\nall passed')
sys.exit(1 if FAILS else 0)
