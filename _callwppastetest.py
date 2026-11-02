"""Call Mode: keep searching Whitepages on the same lead, ask before leaving a lead with numbers left,
paste a number after the call, and open the composer from the board's email link (2026-11-02).

Alex: "when i paste the number in there and i proceed i need there to be a way for me to keep searching
up numbers"; "after you dial a number and you select a log and there are more numbers ... ask if we call
next number for the prospect"; "and have a smart like paste in number"; "a direct link to email from the
board routing the specific property to the searched up one".

Checks: the card order (number list, paste box, then leaving the lead), the DNC count wording, "Save, keep
searching" stays on the card and dials nothing, a DNC paste is still refused, the next-number question
after a missed call (Yes stays on the lead, No goes on), no question after a conversation, the after-call
paste, and the #mail handoff (opens only for a lead on the page and an address pasted on it).
Invented numbers (555-01xx) and addresses (example.com). Chromium, file:// page.
Run: python _callwppastetest.py (the browser half skips without Chromium)
"""
import datetime as dt
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='wppaste_')
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')
import call_mode  # noqa: E402

FAILS = []
AUC = (dt.date.today() + dt.timedelta(days=29)).strftime('%m/%d/%Y')


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra and not ok else ''))
    if not ok:
        FAILS.append(name)


def lead(case, phones, dnc, **kw):
    d = {'case': case, 'county': 'MIAMI-DADE', 'st': 'FC', 'auction': AUC, 'days': 29, 'tier': 'B', 'score': 50,
         'value': 400000, 'owners': 'TEST OWNER ' + case, 'oname': 'Test Owner', 'addr': '100 NW 1 ST, MIAMI, FL 33101',
         'pkey': 'C' + case, 'phones': phones, 'phdnc': dnc, 'phsrc': ['st'] * len(phones), 'phtype': ['mobile'] * len(phones)}
    d.update(kw)
    return d


A = lead('2026-000901-CA-01', ['3055550101', '3055550102', '3055550103', '3055550109'], [False, False, False, True])
B = lead('2026-000902-CA-01', ['3055550111'], [False])
SLIM = [A, B]
rows, _ = call_mode.call_rows(SLIM, None, None, lookup=[])
cov, _ = call_mode.coverage_rows(SLIM, [r['c'] for r in rows])
rec('setup: A has 3 dialable numbers and 1 DNC number listed',
    any(r['c'] == A['case'] and len(r['p']) == 3 and len(r.get('dn') or []) == 1 and r.get('k') == 1 for r in rows))

src = open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
tpl = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
rec('card order: number list, then paste box, then leaving the lead',
    src.find('+   _numList(r)\n    +   pasteBlock(r)') > 0 and src.find('+   pasteBlock(r)') < src.find('+   _navRow()'))
rec('the old "withheld" wording is gone from the card', "withheld, do-not-call flag on file" not in src)
rec('board: the Whitepages email link goes to Call Mode, never mailto, and only when not held',
    "onclick=\"return _cmMail(this)\"" in tpl and "(_mh ? '' : ' <a class=\"cmmail\"" in tpl
    and 'mailto' not in tpl[tpl.find('function _cmMail('):tpl.find('// ANY-CHANNEL contact gate')])
rec('board: the address rides in localStorage, not the URL',
    "localStorage.setItem('fcMailTo'" in tpl and "'#mail=' + encodeURIComponent(a.getAttribute('data-c'))" in tpl)

try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as _p:
        _p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None).close()
except Exception as e:
    print('SKIP browser half (Chromium will not launch: %s)' % str(e).splitlines()[0][:200])
    sys.exit(1 if FAILS else 0)

html = call_mode.build_html([], 0, {'ct': 'x', 'k': 'y'}, 'built', 'sig', 'bsig',
                            sync_js=call_mode.extract_sync_js(tpl), funnel_js=call_mode.extract_funnel_js(tpl),
                            text_js=call_mode.extract_text_js(tpl),
                            mail_js=call_mode.bake_mail_js(call_mode.extract_mail_js(tpl), 'wppaste test'),
                            histcov='{"ledgers_ok": true, "why": "", "capped": false, "total": 2, "shipped": 2}')
path = os.path.join(_T, 'call.html')
open(path, 'w', encoding='utf-8').write(html)

JS = """async ([rows, cov, A, B]) => {
  try { localStorage.clear(); } catch(e) {}
  notes = {}; ROWS = rows; COV = cov;
  start();
  const o = {};
  const ndial = () => ((notes[A]||{}).dials||[]).length;
  _openCase(A);
  o.open = SCREEN + '|' + (cur && cur.c);
  const app = () => document.getElementById('app').innerHTML;
  const h = app();
  o.sub = (h.match(/number 1 of 3 you can dial[^<]*/)||[''])[0];
  o.order = [h.indexOf('class="numlist"'), h.indexOf('class="pastecall"'), h.indexOf('id="navback"')];
  o.chk = h.indexOf('class="numchk"') > 0;
  o.btns = !!document.getElementById('pcsave') && !!document.getElementById('pcgo');
  o.navlbl = (document.getElementById('skip')||{}).textContent;
  // SAVE, KEEP SEARCHING
  const before = cur.p.length, ph0 = phIdx;
  pasteSave(cur, '(305) 555-0177');
  o.saved = SCREEN + '|' + cur.c + '|' + (cur.p.length - before) + '|' + (phIdx === ph0) + '|' + ndial()
          + '|' + ((notes[A].wpm||[]).indexOf('3055550177') >= 0);
  o.cleared = (document.getElementById('pcnum')||{}).value === '';
  pasteSave(cur, '3055550177');
  o.dupe = cur.p.length - before;
  pasteSave(cur, '3055550109');                 // A's own DNC number
  o.dncRefused = cur.p.length - before === 1 && (notes[A].wpm||[]).indexOf('3055550109') < 0;
  // BACK FROM THE OUTCOME SCREEN: same lead, same number, nothing logged beyond the dial's pending row
  window._tapShieldUntil = 0; phIdx = 1;
  const dl0 = ndial(), tc0 = ((notes[A]||{}).touches||[]).length;
  document.getElementById('dial').click();
  await new Promise(res => setTimeout(res, 30));     // the dial handler paints the outcome screen on a 0 ms timer
  o.ocScreen = SCREEN;
  document.getElementById('ocback').click();
  o.ocBack = SCREEN + '|' + cur.c + '|' + phIdx + '|' + (ndial() - dl0) + '|' + (((notes[A]||{}).touches||[]).length - tc0)
           + '|' + ((notes[A].dials||[]).slice(-1)[0]||{}).oc;
  window._tapShieldUntil = 0;
  // AFTER A MISSED CALL: the question
  const NA = OUTCOMES.find(x => x.k === 'noanswer'), TK = OUTCOMES.find(x => x.k === 'talked');
  phIdx = 0;
  afterCall(cur, NA, B);
  o.afterPaste = !!document.getElementById('apnum');
  document.getElementById('nx').click();
  o.asked = !!document.querySelector('.nxq') && !!document.getElementById('nxyes');
  o.askedNum = (document.querySelector('.nxq .own')||{}).textContent;
  document.getElementById('nxyes').click();
  o.yes = SCREEN + '|' + cur.c + '|' + phIdx + '|' + ndial();
  // No goes on (the Complete sheet, which is the old Next-lead path)
  window._tapShieldUntil = 0;             // the 400 ms shield after Yes eats a scripted tap
  phIdx = 0;
  afterCall(cur, NA, B);
  document.getElementById('nx').click();
  document.getElementById('nxno').click();
  o.no = SCREEN;
  // After a conversation: no question
  _CMP = null; _openCase(A); phIdx = 0;
  afterCall(cur, TK, B);
  document.getElementById('nx').click();
  o.talked = !!document.querySelector('.nxq') ? 'asked' : SCREEN;
  // AFTER-CALL PASTE
  _CMP = null; _openCase(A); phIdx = 0;
  afterCall(cur, TK, B);
  const ap = document.getElementById('apnum');
  ap.value = '305-555-0188'; ap.dispatchEvent(new Event('input'));
  o.apEnabled = !document.getElementById('apgo').disabled;
  document.getElementById('apgo').click();
  o.apAsked = !!document.querySelector('.nxq') && (document.querySelector('.nxq .own')||{}).textContent;
  o.apSaved = (notes[A].wpm||[]).indexOf('3055550188') >= 0;
  document.getElementById('nxno').click();
  o.apBack = SCREEN + '|' + !!document.getElementById('apnum');
  const ap2 = document.getElementById('apnum');
  ap2.value = '3055550109'; ap2.dispatchEvent(new Event('input'));
  o.apDnc = document.getElementById('apgo').disabled;
  // MAIL HANDOFF. First with the do-not-contact list not confirmed fresh (this page has no bridge):
  // the composer must refuse and the card opens instead.
  notes[A].wpe = ['owner@example.com'];
  localStorage.setItem('fcMailTo', JSON.stringify({c: A, to: 'owner@example.com', ts: Date.now()}));
  history.replaceState(null, '', '#mail=' + encodeURIComponent(A));
  o.midCall = mailHandoff() + '|' + SCREEN + '|' + (localStorage.getItem('fcMailTo') !== null);   // still on the after-call panel
  _CMP = null; SCREEN = 'lead'; _openCase(A);
  history.replaceState(null, '', '#mail=' + encodeURIComponent(A));
  o.mailHeld = textingHeld() + '|' + mailHandoff() + '|' + SCREEN + '|' + (cur && cur.c);
  textingHeld = function(){ return false; };   // the list is fresh: now the composer may open
  localStorage.setItem('fcMailTo', JSON.stringify({c: A, to: 'owner@example.com', ts: Date.now()}));
  history.replaceState(null, '', '#mail=' + encodeURIComponent(A));
  o.mail = mailHandoff() + '|' + SCREEN + '|' + (location.hash === '') + '|' + (localStorage.getItem('fcMailTo') === null);
  o.mailTo = (document.querySelector('.mailcard .mailv')||{}).textContent || '';
  SCREEN = 'lead'; _openCase(A);
  localStorage.setItem('fcMailTo', JSON.stringify({c: A, to: 'stranger@example.com', ts: Date.now()}));
  history.replaceState(null, '', '#mail=' + encodeURIComponent(A));
  o.mailOther = mailHandoff() + '|' + SCREEN; SCREEN = 'lead'; _openCase(A);
  history.replaceState(null, '', '#mail=2026-999999-CA-01');
  o.mailNoLead = mailHandoff() + '|' + SCREEN; SCREEN = 'lead'; _openCase(A);
  localStorage.setItem('fcMailTo', JSON.stringify({c: A, to: 'owner@example.com', ts: Date.now() - 3600000}));
  notes[A].wpe = ['owner@example.com', 'second@example.com'];
  history.replaceState(null, '', '#mail=' + encodeURIComponent(A));
  o.mailStale = mailHandoff() + '|' + SCREEN;
  return o; }"""

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None)
    pg = b.new_page(viewport={'width': 390, 'height': 900})
    errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)))
    pg.goto('file://' + path)
    pg.wait_for_timeout(300)
    out = pg.evaluate(JS, [rows, cov, A['case'], B['case']])
    b.close()

rec('page runs without a script error', not errs, errs)
rec('lead opens', out['open'] == 'lead|' + A['case'], out['open'])
rec('dial line says how many are dialable and how many are do-not-call',
    'you can dial' in out['sub'] and 'plus 1 do-not-call, not dialable (listed below)' in out['sub'], out['sub'])
rec('card order on screen: numbers, paste box, previous/next lead', -1 not in out['order'] and out['order'] == sorted(out['order']), out['order'])
rec('checks row for the number on the button', out['chk'])
rec('paste box has Save, keep searching and Save & call', out['btns'])
rec('the leave button says Next lead', (out['navlbl'] or '').startswith('Next lead'), out['navlbl'])
rec('Save, keep searching: same card, number added, button number unchanged, nothing dialled, saved to notes',
    out['saved'] == 'lead|%s|1|true|0|true' % A['case'], out['saved'])
rec('the box is cleared for the next paste', out['cleared'])
rec('pasting the same number twice adds it once', out['dupe'] == 1, out['dupe'])
rec("a DNC number is still refused", out['dncRefused'])
rec('dial tap opens the outcome screen', out['ocScreen'] == 'outcome', out['ocScreen'])
rec('Back from the outcome screen: same lead, same number, no outcome logged, only the pending dial row',
    out['ocBack'] == 'lead|%s|1|1|0|pending' % A['case'], out['ocBack'])
rec('after-call panel has the paste box', out['afterPaste'])
rec('Next lead after a missed call asks about the next number', out['asked'], out)
rec('it offers number 2 of 4', '(305) 555-0102' in (out['askedNum'] or '') and '2 of 4' in (out['askedNum'] or ''), out['askedNum'])
rec('Yes: same lead, next number on the button, nothing dialled since (1 row = the Back test dial)', out['yes'] == 'lead|%s|1|1' % A['case'], out['yes'])
rec('No: carries on to the Complete sheet', out['no'] == 'complete', out['no'])
rec('after a conversation there is no question', out['talked'] != 'asked', out['talked'])
rec('after-call paste: accepted, saved, asks to call it next',
    out['apEnabled'] and out['apSaved'] and '(305) 555-0188' in str(out['apAsked']), out)
rec('after-call paste: No returns to the after-call panel', out['apBack'] == 'after|true', out['apBack'])
rec('after-call paste: a DNC number cannot be added', out['apDnc'])
rec('board link: opens the composer for that lead and address, clears the hash and the handoff',
    out['mail'] == 'true|mail|true|true' and 'owner@example.com' in out['mailTo'], (out['mail'], out['mailTo']))
rec('board link: never opens over a call in progress (after-call panel stays, handoff kept)',
    out['midCall'] == 'false|after|true', out['midCall'])
rec('board link: while the do-not-contact list is not fresh, no composer, the card opens',
    out['mailHeld'] == 'true|false|lead|' + A['case'], out['mailHeld'])
rec('board link: an address not pasted on this lead opens no composer', not out['mailOther'].startswith('true') and not out['mailOther'].endswith('mail'), out['mailOther'])
rec('board link: a lead not on this page opens nothing', out['mailNoLead'].startswith('false') and not out['mailNoLead'].endswith('mail'), out['mailNoLead'])
rec('board link: a stale handoff with two addresses opens no composer', out['mailStale'].startswith('false'), out['mailStale'])
print('\n%d failure(s)' % len(FAILS) if FAILS else '\nall passed')
sys.exit(1 if FAILS else 0)
