"""_callrecenttest -- a lead he just worked is one tap away after a reload, and a number he saved is findable.

Run:  python _callrecenttest.py   (exit 0 = safe; invented 555-01xx numbers, no network)

WHY (2026-11-19, Alejandro): "I just saved a prospect's information ... I refresh, or close Call Mode and
open it again ... it sends me back to the main menu ... it doesn't stay on the prospect that I was on. And
when I try to find them on who called me and I put their number, because I saved their number, they don't
show up."

Pins:
  - saving a number on a Find a number lead remembers that lead (fcRecent), and after a RELOAD the top bar
    offers it and Find a lead lists it under "LEADS YOU JUST WORKED"
  - tapping the top-bar button opens that lead (its card, or a read-only page when it may not be worked)
  - Who texted me finds a number that was SAVED (notes.phone / wpm), by full number and by last four,
    even though the baked phone index has never heard of it, and marks it "number you saved"
  - Find a lead accepts a phone number and returns that lead
  - a lead marked do-not-contact after the save still opens read-only: no dial, text or email control
"""
import datetime as dt
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='recent_')
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')
import call_mode  # noqa: E402

FAILS = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:400]) if extra and not ok else ''))
    if not ok:
        FAILS.append(name)


AUC = (dt.date.today() + dt.timedelta(days=29)).strftime('%m/%d/%Y')


def lead(case, **kw):
    d = {'case': case, 'county': 'BROWARD', 'st': 'FC', 'auction': AUC, 'days': 29, 'tier': 'B', 'score': 50,
         'value': 400000, 'owners': 'TESTER, DANA ' + case[-2:], 'oname': 'Dana Tester', 'addr': '100 NW 1 ST, MIAMI, FL 33101',
         'pkey': 'C' + case}
    d.update(kw)
    return d


DIAL = [lead('CACE2600100%d' % i, phones=['30555501%02d' % i], phdnc=[False], phsrc=['st'], phtype=['mobile']) for i in range(3)]
NOPH = [lead('CACE2600200%d' % i, addr='%d00 SW 9 AVE, MIAMI, FL 33130' % (i + 2)) for i in range(3)]
SLIM = DIAL + NOPH
sink = []
rows, _ = call_mode.call_rows(SLIM, None, None, lookup=sink)
cov, _ = call_mode.coverage_rows(SLIM, [r['c'] for r in rows])
call_mode.mark_lookup(cov, sink, {r['pk']: r['sb'] for r in rows})

try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as _p:
        _p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None).close()
except Exception as e:
    print('SKIP browser half (%s)' % str(e).splitlines()[0][:200])
    sys.exit(0)

tpl = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
html = call_mode.build_html([], 0, {'ct': 'x', 'k': 'y'}, 'built', 'sig', 'bsig',
                            sync_js=call_mode.extract_sync_js(tpl), funnel_js=call_mode.extract_funnel_js(tpl),
                            text_js=call_mode.extract_text_js(tpl),
                            histcov='{"ledgers_ok": true, "why": "", "capped": false, "total": 6, "shipped": 6}')
path = os.path.join(_T, 'call.html')
open(path, 'w', encoding='utf-8').write(html)
C = NOPH[0]['case']
C2 = NOPH[1]['case']
NUM = '7865550177'

BOOT = """([rows, cov, keep]) => {
  if (!keep) { try { localStorage.clear(); } catch(e) {} notes = {}; }
  ROWS = JSON.parse(JSON.stringify(rows)); COV = JSON.parse(JSON.stringify(cov));
  if (typeof PHIDX !== 'undefined') PHIDX = {d: {}, t: [], h: {}};      // the baked index knows none of these
  start();
}"""

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None)
    ctx = b.new_context(viewport={'width': 390, 'height': 844})
    pg = ctx.new_page()
    errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)))
    pg.goto('file://' + path)
    pg.wait_for_timeout(400)
    pg.evaluate(BOOT, [rows, cov, False])
    a = pg.evaluate("""([c, num]) => {
      const o = {};
      BLANE = '__lk'; SCREEN = 'lead'; render();
      const dial = window.dialHref;
      lkSave(c, num);
      o.saved = (notes[c] || {}).phone;
      /* he logs the call (talked), the way the outcome screen does, and the open-call record is cleared */
      const row = ROWS.find(x => x.c === c), oc = OUTCOMES.find(x => x.k === 'talked') || OUTCOMES[0];
      o.logged = logOutcome(row, oc, num);
      _ocPendClear();
      o.recent = JSON.parse(localStorage.getItem(_RECK) || '[]').map(x => x.c);
      o.notesJson = JSON.stringify(notes);
      return o; }""", [C, NUM])
    rec('saving a number on a Find a number lead remembers the lead', a['saved'] == NUM and a['recent'][:1] == [C], a)

    # RELOAD: a new page load, same storage; notes come back the way the page restores them
    pg.goto('file://' + path)
    pg.wait_for_timeout(400)
    r = pg.evaluate("""([rows, cov, c, c2, num, notesJson]) => {
      const o = {};
      notes = JSON.parse(notesJson);
      ROWS = JSON.parse(JSON.stringify(rows)); COV = JSON.parse(JSON.stringify(cov));
      if (typeof PHIDX !== 'undefined') PHIDX = {d: {}, t: [], h: {}};
      start();
      o.landed = {screen: SCREEN, cur: cur && cur.c, blane: BLANE, from: LKFROM};
      const btn = document.querySelector('[data-reclast]');
      o.btn = btn ? btn.getAttribute('data-reclast') : null;
      o.btnText = btn ? btn.textContent : '';
      o.full = phLookup('(786) 555-0177').map(h => [h.c, h.num, !!h.saved]);
      o.tail = phLookup('0177').map(h => h.c);
      o.other = phLookup('0199').length;
      o.find = flSearch('786-555-0177').map(e => e.c);
      o.caseStill = flSearch(c).map(e => e.c)[0];
      screenLookup(); document.getElementById('lkq').value = '7865550177';
      document.getElementById('lkq').dispatchEvent(new Event('input'));
      o.lookupHtml = document.getElementById('lkout').innerHTML;
      screenFind(); document.getElementById('flq').value = ''; _FLQ = ''; _flResults('');
      o.recentList = Array.from(document.querySelectorAll('#flres .flres')).length;
      o.recentHead = document.getElementById('flres').innerHTML.indexOf('LEADS YOU JUST WORKED') >= 0;
      flClose();
      const b2 = document.querySelector('[data-reclast]'); if (b2) b2.click();
      o.afterTap = {screen: SCREEN, cur: cur && cur.c, html: document.getElementById('app').innerHTML.indexOf(c) >= 0};
      /* a hold that lands after the save: read-only, nothing to tap */
      notes[c] = Object.assign(notes[c] || {}, {status: 'DO NOT CONTACT'});
      const e = recentLeads().filter(x => x.c === c)[0];
      flOpen(e);
      const app = document.getElementById('app');
      o.held = {screen: SCREEN, links: app.querySelectorAll('a[href^="tel:"], a[href^="sms:"], #dial, .lkadd, .lkclip, #pcgo').length,
                says: /do not contact|held/i.test(app.textContent)};
      return o; }""", [rows, cov, C, C2, NUM, a['notesJson']])
    b.close()

print('landed after reload:', r['landed'])
rec('after a reload the top bar offers the lead he just worked', r['btn'] == C and 'tester' in r['btnText'].lower(), r)
rec('Who texted me finds the SAVED number by the full number', r['full'] == [[C, NUM, True]], r['full'])
rec('...and by the last four digits', r['tail'] == [C], r['tail'])
rec('...and does not invent a hit for another number', r['other'] == 0, r['other'])
rec('...the lookup screen shows the lead, marked as a number he saved',
    'number you saved' in r['lookupHtml'] and 'No lead in the database' not in r['lookupHtml'], r['lookupHtml'][:200])
rec('Find a lead takes a phone number and returns that lead', r['find'] == [C], r['find'])
rec('Find a lead still finds it by case number', r['caseStill'] == C, r['caseStill'])
rec('Find a lead with an empty box lists the leads he just worked', r['recentList'] >= 1 and r['recentHead'], r)
rec('tapping the top-bar button opens that lead', r['afterTap']['html'] and r['afterTap']['screen'] in ('lead', 'finddetail', 'outcome'), r['afterTap'])
rec('a lead put on do-not-contact after the save opens read-only: no dial, text or email control',
    r['held']['links'] == 0 and r['held']['says'], r['held'])
rec('no script error', not errs, errs)
print('\n%d failure(s)' % len(FAILS) if FAILS else '\nall passed')
sys.exit(1 if FAILS else 0)
