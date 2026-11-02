"""A typed / pasted number is checked against EVERY lead's do-not-call numbers (2026-11-01, CM3 sweep D5).

Before: pasteCheck (paste box) and lkSave (Find a number) looked only at the lead's own `dn`, so a number
flagged do-not-call on lead B could be typed into lead A and dialled. Now refused when it is on any row's
`dn` or in the build's whole-book DNC list (payload `n`, which also covers leads that have no dial row).
Only tightens: a clean number, and the lead's own DNC refusal, behave as before.
Invented numbers (555-01xx). Chromium, file:// page. Run: python _calldncpastetest.py (skips without Chromium)
"""
import datetime as dt
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='dncpaste_')
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


A = lead('2026-000801-CA-01', ['3055550101'], [False])                                   # the lead we type into
B = lead('2026-000802-CA-01', ['3055550102', '3055550103'], [True, False])               # has a row; 0102 is DNC here
D = lead('2026-000803-CA-01', ['3055550104'], [True])                                    # ONLY a DNC number: no dial row
L = lead('2026-000804-CA-01', [], [], phsrc=[], phtype=[])                               # Find a number
SLIM = [A, B, D, L]
sink = []
rows, _ = call_mode.call_rows(SLIM, None, None, lookup=sink)
cov, _ = call_mode.coverage_rows(SLIM, [r['c'] for r in rows])
call_mode.mark_lookup(cov, sink, {r['pk']: r['sb'] for r in rows})
rec('setup: B shows its DNC number on its row, D has no dial row, L is on the lookup list',
    any(r['c'] == B['case'] and r.get('dn') for r in rows) and not any(r['c'] == D['case'] for r in rows)
    and any(c['c'] == L['case'] and c.get('lk') for c in cov))
src = open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
rec('the build ships the whole-book DNC list in the payload', "'n': sorted(dnc_numbers(slim))" in src)

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
                            histcov='{"ledgers_ok": true, "why": "", "capped": false, "total": 4, "shipped": 4}')
path = os.path.join(_T, 'call.html')
open(path, 'w', encoding='utf-8').write(html)

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None)
    pg = b.new_page(viewport={'width': 390, 'height': 900})
    errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)))
    pg.goto('file://' + path)
    pg.wait_for_timeout(300)
    out = pg.evaluate("""([rows, cov, A, B, D, L]) => {
      try { localStorage.clear(); } catch(e) {}
      notes = {}; ROWS = rows; COV = cov;
      if (typeof DNCALL !== 'undefined') DNCALL = ['3055550104'];       // what the unlocked payload's `n` becomes
      start();
      const rA = ROWS.find(r => r.c === A), o = {};
      o.own = pasteCheck(ROWS.find(r => r.c === B), '3055550102').why;     // B's own DNC: refused before and after
      o.otherRow = pasteCheck(rA, '(305) 555-0102').why;                    // DNC on B's row, typed into A
      o.noRow = pasteCheck(rA, '305-555-0104').why;                         // DNC on D, which has no dial row
      o.clean = pasteCheck(rA, '305-555-0177').why;                         // control: fine
      o.eleven = pasteCheck(rA, '1-305-555-0104').why;                      // 11-digit form of the same number
      const before = ROWS.length;
      lkSave(L, '3055550102');
      o.lkRefused = !(notes[L] && notes[L].phone) && ROWS.length === before;
      lkSave(L, '3055550104');
      o.lkRefused2 = !(notes[L] && notes[L].phone) && ROWS.length === before;
      return o; }""", [rows, cov, A['case'], B['case'], D['case'], L['case']])
    b.close()
rec('page loads without a script error', not errs, errs)
rec("a lead's own DNC number is still refused", bool(out['own']), out)
rec('a number DNC on another lead is refused in the paste box', bool(out['otherRow']), out)
rec('a number DNC on a lead with no dial row is refused in the paste box', bool(out['noRow']), out)
rec('the 11-digit form is refused too', bool(out['eleven']), out)
rec('a clean number is still accepted', out['clean'] == '', out)
rec('Find a number refuses a number DNC on another lead (nothing saved)', out['lkRefused'], out)
rec('Find a number refuses a number DNC on a lead with no dial row', out['lkRefused2'], out)
print('\n%d failure(s)' % len(FAILS) if FAILS else '\nall passed')
sys.exit(1 if FAILS else 0)
