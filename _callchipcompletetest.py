"""Callback chip vs the Complete sheet (2026-11-01, CM3 sweep D4).

After an outcome, the callback chips ("In 3 hours" ...) advance the lead on a 450 ms timer. If Next lead
is tapped inside that window the Complete sheet opens, and the pending timer then fired go(): it dismissed
the sheet (advance -> SCREEN 'lead') with _CMP still set and nothing logged, so the "what did you do"
answers were lost. Pins: a chip tap followed at once by Next lead leaves the sheet up after the timer
would have fired, Log writes the ticked touch, and a chip left alone still advances.
Invented leads, file:// page, Chromium. Run: python _callchipcompletetest.py (skips if Chromium will not launch)
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='chipcomplete_')
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')
import call_mode  # noqa: E402

FAILS = []
import datetime as _dt
AUC = (_dt.date.today() + _dt.timedelta(days=29)).strftime('%m/%d/%Y')


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra and not ok else ''))
    if not ok:
        FAILS.append(name)


def lead(case, ph):
    return {'case': case, 'county': 'MIAMI-DADE', 'st': 'FC', 'auction': AUC, 'days': 29, 'tier': 'B',
            'score': 50, 'value': 400000, 'owners': 'TEST OWNER ' + case, 'oname': 'Test Owner',
            'addr': '100 NW 1 ST, MIAMI, FL 33101', 'pkey': 'C' + case, 'phones': [ph], 'phdnc': [False],
            'phsrc': ['st'], 'phtype': ['mobile']}


try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as _p:
        _p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None).close()
except Exception as e:
    print('SKIP (Chromium will not launch: %s)' % str(e).splitlines()[0][:200])
    sys.exit(0)

SLIM = [lead('2026-000901-CA-01', '3055550101'), lead('2026-000902-CA-01', '3055550102'),
        lead('2026-000903-CA-01', '3055550103')]
rows, _ = call_mode.call_rows(SLIM, None, None)
cov, _ = call_mode.coverage_rows(SLIM, [r['c'] for r in rows])
tpl = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
html = call_mode.build_html([], 0, {'ct': 'x', 'k': 'y'}, 'built', 'sig', 'bsig',
                            sync_js=call_mode.extract_sync_js(tpl), funnel_js=call_mode.extract_funnel_js(tpl),
                            text_js=call_mode.extract_text_js(tpl),
                            histcov='{"ledgers_ok": true, "why": "", "capped": false, "total": 3, "shipped": 3}')
path = os.path.join(_T, 'call.html')
open(path, 'w', encoding='utf-8').write(html)

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None)

    def fresh():
        pg = b.new_page(viewport={'width': 390, 'height': 900})
        pg.errs = []
        pg.on('pageerror', lambda e: pg.errs.append(str(e)))
        pg.on('dialog', lambda d: d.accept())
        pg.goto('file://' + path)
        pg.wait_for_timeout(300)
        pg.evaluate("""([rows, cov]) => { try { localStorage.clear(); } catch(e) {}
          notes = {}; ROWS = rows; COV = cov; start(); }""", [rows, cov])
        pg.wait_for_timeout(150)
        # dial -> outcome screen -> no answer -> after-call panel
        pg.evaluate("document.addEventListener('click', e => { const a = e.target.closest && e.target.closest('a[href^=\"tel:\"]'); if (a) e.preventDefault(); }, true)")
        pg.click('#dial'); pg.wait_for_timeout(450)
        pg.click('button[data-oc="noanswer"]'); pg.wait_for_timeout(450)
        return pg

    # 1. chip then Next lead inside the 450 ms window
    pg = fresh()
    worked = pg.evaluate("cur.c")
    pg.click('.cb[data-h="3"]')
    pg.click('#nx')
    sheet_now = pg.evaluate("SCREEN === 'complete' && !!document.getElementById('cmplog')")
    pg.wait_for_timeout(900)                       # the timer would have fired by now
    st = pg.evaluate("({scr: SCREEN, log: !!document.getElementById('cmplog'), cmp: !!_CMP, cur: cur.c})")
    rec('Complete sheet opens on Next lead', sheet_now)
    rec('the sheet is still up after the chip timer would have fired', st['scr'] == 'complete' and st['log'], st)
    rec('_CMP and the screen agree (no orphaned _CMP)', st['cmp'] == (st['scr'] == 'complete'), st)
    got = None
    if st['log']:
        pg.click('.cmpchip[data-k="text"]')
        pg.click('#cmplog'); pg.wait_for_timeout(450)
        got = pg.evaluate("(c) => ((notes[c] || {}).touches || []).filter(t => t.src === 'manual').map(t => t.mk)", worked)
    rec('Log writes the ticked touch and then advances', got == ['text'] and pg.evaluate("SCREEN === 'lead' && cur.c") != worked, got)
    rec('no page error', not pg.errs, pg.errs[:1])
    pg.close()

    # 2. a chip left alone still advances on its own
    pg = fresh()
    worked = pg.evaluate("cur.c")
    pg.click('.cb[data-h="20"]')
    pg.wait_for_timeout(1000)
    st = pg.evaluate("({scr: SCREEN, cur: cur.c, cmp: !!_CMP})")
    rec('a chip left alone still advances to the next lead', st['scr'] == 'lead' and st['cur'] != worked and not st['cmp'], st)
    pg.close()

    # 3. chip, Next lead, Skip inside the window: the timer must not skip a second lead
    pg = fresh()
    worked = pg.evaluate("cur.c")
    pg.click('.cb[data-h="3"]')
    pg.click('#nx'); pg.click('#cmpskip')
    nxt = pg.evaluate("cur.c")
    pg.wait_for_timeout(900)
    after = pg.evaluate("cur.c")
    rec('chip + Next lead + Skip lands on the next lead and stays', nxt != worked and after == nxt, (worked, nxt, after))
    pg.close()
    b.close()

print('\n%d failure(s)' % len(FAILS) if FAILS else '\nall passed')
sys.exit(1 if FAILS else 0)
