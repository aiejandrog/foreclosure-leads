"""Call Mode: the whole-page Whitepages capture on Find a number (2026-11-18/19).

Alex: "on the find a number page i went on whitepage got the information of a prospect and pasted it and
put who was the owner etc. and it saved but it didnt take me directly to that prospects page to call or do
anything it took me out to look at the other prospects".

Checks, on a Find-a-number lead with no phone: the capture button is on the card, a pasted Whitepages
ADDRESS page (Owners & Residents layout) is read with the page's own Owner label winning, label lines
("Cell Phone", "Silver Falls") are not taken for people, census-tract digits are not a phone, and Save
lands on THAT lead's card on the owner's number (not back on the list) without dialling. Owner email goes
to notes.wpe, other residents to notes.wpr / notes.wpre. An email-only capture opens the composer.
Invented names, 555-01xx numbers and invented.test addresses. Chromium, file:// page.
Run: python _callwpcapturetest.py (the browser half skips without Chromium)
"""
import datetime as dt
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='wpcap_')
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')
import call_mode  # noqa: E402

FAILS = []
AUC = (dt.date.today() + dt.timedelta(days=29)).strftime('%m/%d/%Y')


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra and not ok else ''))
    if not ok:
        FAILS.append(name)


def lead(case, phones, **kw):
    d = {'case': case, 'county': 'MIAMI-DADE', 'st': 'FC', 'auction': AUC, 'days': 29, 'tier': 'B', 'score': 50,
         'value': 400000, 'owners': 'TESTOWNER TOM Q', 'oname': 'Tom Testowner', 'addr': '100 SW 1 WAY, MIRAMAR, FL 33027',
         'pkey': 'C' + case, 'phones': phones, 'phdnc': [False] * len(phones), 'phsrc': ['st'] * len(phones),
         'phtype': ['mobile'] * len(phones)}
    d.update(kw)
    return d


A = lead('2026-000951-CA-01', [])                       # Find a number: no phone, no email
E = lead('2026-000952-CA-01', [], owners='EMAILONLY EVE', oname='Eve Emailonly')
B = lead('2026-000953-CA-01', ['3055550111'], owners='DIALER DAN', oname='Dan Dialer')
SLIM = [A, E, B]
LK = []
rows, _ = call_mode.call_rows(SLIM, None, None, lookup=LK)
cov, _ = call_mode.coverage_rows(SLIM, [r['c'] for r in rows])
call_mode.mark_lookup(cov, LK)      # stamps lk=1 on the no-number leads, as the build does
lk = [r['c'] for r in cov if r.get('lk')]
rec('setup: the two no-phone leads are on the Find a number list', A['case'] in lk and E['case'] in lk, lk)

PAGE = "\n".join([
    "Whitepages - Serving the US since '97Menu", "100 SW 1st Way, Miramar, FL 33027", "Silver Falls",
    "Get Owner Info", "Owners & Residents",
    "Tom Q Testowner", ", age 36", "Owner", "Resident", "FL, Miramar (0 mi away)",
    "Cell Phone", "(954) 555-0101", "Landline", "(305) 555-0102", "Email Address", "tomq_owner@invented.test",
    "Ann X Other", "Resident", "Cell Phone", "(954) 555-0101", "Landline", "(305) 555-0102", "Email Address", "—",
    "Cara Simin Baker", ", age 33", "Resident", "Cell Phone", "(954) 555-0103", "Email Address", "cara_b@invented.test",
    "Free and Clear", "Census Tract", "1103651004", "Neighbors", "Sandra Nextdoor", "Age 65+", "24 Phones"])

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
                            mail_js=call_mode.bake_mail_js(call_mode.extract_mail_js(tpl), 'wpcap test'),
                            histcov='{"ledgers_ok": true, "why": "", "capped": false, "total": 3, "shipped": 3}')
path = os.path.join(_T, 'call.html')
open(path, 'w', encoding='utf-8').write(html)

JS = """async ([rows, cov, A, E, PAGE, B]) => {
  try { localStorage.clear(); } catch(e) {}
  notes = {}; ROWS = rows; COV = cov;
  start();
  const o = {};
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  BLANE = '__lk'; SCREEN = 'lead'; render();
  o.btn = !!document.querySelector('button.wpcopen[data-c="' + A + '"]');
  document.querySelector('button.wpcopen[data-c="' + A + '"]').click();
  o.screen1 = SCREEN;
  const ta = document.getElementById('wpctxt'); ta.value = PAGE; ta.dispatchEvent(new Event('input'));
  await sleep(600);
  o.items = (WPC && WPC.items || []).map(x => x.v + ':' + x.k);
  o.saveLbl = (document.getElementById('wpcsave')||{}).textContent;
  const nd = ((notes[A]||{}).dials||[]).length;
  document.getElementById('wpcsave').click();
  await sleep(100);
  o.after = SCREEN + '|' + (cur && cur.c) + '|' + BLANE;
  o.onNum = cur && cur.p ? String(cur.p[phIdx]||'').replace(/\\D/g,'').slice(-10) : '';
  o.dialled = ((notes[A]||{}).dials||[]).length - nd;
  o.wpm = (notes[A].wpm||[]).join(',');
  o.wpe = (notes[A].wpe||[]).join(',');
  o.wpr = (notes[A].wpr||[]).map(x => x.n + ':' + x.k).join(',');
  o.wpre = (notes[A].wpre||[]).map(x => x.e + ':' + x.k).join(',');
  o.back = LKFROM;
  // email only -> the composer
  SCREEN = 'lead'; BLANE = '__lk'; render();
  screenWpPaste(E);
  const t2 = document.getElementById('wpctxt'); t2.value = "Eve Emailonly\\nOwner\\nEmail Address\\neve_owner@invented.test"; t2.dispatchEvent(new Event('input'));
  await sleep(600);
  document.getElementById('wpcsave').click();
  await sleep(100);
  o.mail = SCREEN + '|' + ((notes[E]||{}).wpe||[]).join(',');
  // LEAD CARD (2026-11-19): the capture button on a dial card comes back to that card
  SCREEN = 'lead'; BLANE = null; LKFROM = null; _openCase(B);
  const cb = document.querySelector('.pastecall button.wpcopen[data-c="' + B + '"]');
  o.cardBtn = !!cb;
  cb.click(); o.cardScreen = SCREEN;
  document.getElementById('wpcback').click();
  o.cardBack = SCREEN + '|' + (cur && cur.c);
  const nb = ((notes[B]||{}).dials||[]).length;
  document.querySelector('.pastecall button.wpcopen[data-c="' + B + '"]').click();
  const t3 = document.getElementById('wpctxt');
  t3.value = "Owners & Residents\\nDan Dialer\\nOwner\\nCell Phone\\n(786) 555-0144\\nLiz Sister\\nResident\\n(786) 555-0145";
  t3.dispatchEvent(new Event('input')); await sleep(600);
  document.getElementById('wpcsave').click(); await sleep(100);
  o.cardAfter = SCREEN + '|' + (cur && cur.c) + '|' + LKFROM;
  o.cardNum = String(cur.p[phIdx]||'').replace(/\\D/g,'').slice(-10);
  o.cardDial = ((notes[B]||{}).dials||[]).length - nb;
  o.cardRel = ((notes[B]||{}).wpr||[]).map(x => x.n + ':' + x.k).join(',');
  const ph1 = cur.p[phIdx];
  document.querySelector('.pastecall button.wpcopen[data-c="' + B + '"]').click();
  const t4 = document.getElementById('wpctxt');
  t4.value = "Possible relatives\\nMo Cousin\\n(786) 555-0146";
  t4.dispatchEvent(new Event('input')); await sleep(600);
  document.getElementById('wpcsave').click(); await sleep(100);
  o.relOnly = SCREEN + '|' + (cur && cur.c) + '|' + (cur.p[phIdx] === ph1);
  return o; }"""

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None)
    pg = b.new_page(viewport={'width': 390, 'height': 900})
    errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)))
    pg.goto('file://' + path)
    pg.wait_for_timeout(300)
    out = pg.evaluate(JS, [rows, cov, A['case'], E['case'], PAGE, B['case']])
    b.close()

rec('page runs without a script error', not errs, errs)
rec('Find a number card carries the whole-page capture button', out['btn'])
rec('the button opens the capture screen', out['screen1'] == 'wppaste', out['screen1'])
rec('address page read: owner number, landline and email are the owner; the other resident is not sure; no census digits',
    out['items'] == ['9545550101:o', '3055550102:o', 'tomq_owner@invented.test:o', '9545550103:u', 'cara_b@invented.test:u'],
    out['items'])
rec('Save button counts what will be saved', '3 numbers + 2 emails' in (out['saveLbl'] or ''), out['saveLbl'])
rec('SAVE LANDS ON THIS LEAD\'S CARD, not back on the list', out['after'] == 'lead|%s|null' % A['case'], out['after'])
rec('...on the owner\'s number', out['onNum'] == '9545550101', out['onNum'])
rec('...and nothing was dialled by itself', out['dialled'] == 0, out['dialled'])
rec('...with the way back to the list kept', out['back'] == A['case'], out['back'])
rec('owner numbers saved as owner (notes.wpm)', out['wpm'] == '9545550101,3055550102', out['wpm'])
rec('owner email saved for the Morning Worker (notes.wpe)', out['wpe'] == 'tomq_owner@invented.test', out['wpe'])
rec('the other resident\'s number is call-only (notes.wpr, not sure)', out['wpr'] == '9545550103:u', out['wpr'])
rec('the other resident\'s email is log-only (notes.wpre)', out['wpre'] == 'cara_b@invented.test:u', out['wpre'])
rec('an email-only capture opens the composer and keeps the address', out['mail'] == 'mail|eve_owner@invented.test', out['mail'])

rec('lead card: the capture button is in the paste box', out['cardBtn'])
rec('lead card: the button opens the capture screen', out['cardScreen'] == 'wppaste', out['cardScreen'])
rec('lead card: Back returns to the same card', out['cardBack'] == 'lead|%s' % B['case'], out['cardBack'])
rec('LEAD CARD: Save stays on this card, with no list hand-off', out['cardAfter'] == 'lead|%s|null' % B['case'], out['cardAfter'])
rec('...and the new owner number is on the dial button', out['cardNum'] == '7865550144', out['cardNum'])
rec('...nothing dialled by itself', out['cardDial'] == 0, out['cardDial'])
rec('...the other resident is call-only, not sure', out['cardRel'] == '7865550145:u', out['cardRel'])
rec('lead card, relatives only: stays on the card, dial number unchanged', out['relOnly'] == 'lead|%s|true' % B['case'], out['relOnly'])

print('\n%d failed' % len(FAILS) if FAILS else '\nall passed')
sys.exit(1 if FAILS else 0)
