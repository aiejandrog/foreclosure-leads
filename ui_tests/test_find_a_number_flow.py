"""Call Mode "Find a number": the way into a call, the way back, the saved place, and the script.

Real Chromium against a page built from SOURCE (call_mode.build_html), invented leads only, no
network. 2026-11-09, Alex: "Fix the wiring pipeline between the page selections and transferring.
From whenever you land on the page ... save a format for you to retrace and go back ... keep the same
script on the find a number page."

Pins:
  (a) on the list, the script drawer is filled for a lead ON the list (first one, then the one
      tapped), never for the dial-queue lead that was open before
  (b) a half-typed number survives a background repaint (a sync landing), cursor included
  (c) a reload lands back on the list at the lead tapped last, with the typed draft
  (d) saving a number opens that lead's card; logging the call (real outcome, complete screen)
      comes back to the list at the next lead, not into the dial queue
  (e) "Back to Find a number" on that card returns without logging anything
"""
import datetime
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
_T = tempfile.mkdtemp(prefix='lkflow_')
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')   # no federal cache: no holds

from playwright.sync_api import sync_playwright          # noqa: E402
import call_mode                                          # noqa: E402

DIAL = '2026-000101-CA-01'
LK1 = '2026-000102-CA-01'
LK2 = '2026-000109-CA-01'


def _lead(case, **kw):
    d = {'case': case, 'county': 'MIAMI-DADE', 'st': 'FC', 'auction': '12/20/2026', 'days': 40,
         'tier': 'B', 'score': 50, 'value': 400000, 'owners': 'TEST, OWNER', 'oname': 'Test Owner',
         'addr': '100 NW 1 ST, MIAMI, FL 33101', 'pkey': 'C' + case}
    d.update(kw)
    return d


def _build():
    slim = [_lead(DIAL, phones=['3055550101'], phdnc=[False], phsrc=['st'], phtype=['mobile'],
                  owners='GARCIA, MARIA', oname='Maria Garcia'),
            _lead(LK1, owners='PEREZ, CARLOS', oname='Carlos Perez', addr='200 SW 2 AVE, MIAMI, FL 33130'),
            _lead(LK2, owners='LOPEZ, ANA', oname='Ana Lopez', addr='300 SW 3 AVE, MIAMI, FL 33130')]
    sink = []
    rows, _ = call_mode.call_rows(slim, None, None, lookup=sink)
    cov, _ = call_mode.coverage_rows(slim, [r['c'] for r in rows])
    call_mode.mark_lookup(cov, sink, {r['pk']: r['sb'] for r in rows})
    tpl = (HERE / 'tracker_template.html').read_text(encoding='utf-8')
    html = call_mode.build_html([], 0, {'ct': 'x', 'k': 'y'}, 'built', 'sig', 'bsig',
                                sync_js=call_mode.extract_sync_js(tpl), funnel_js=call_mode.extract_funnel_js(tpl),
                                text_js=call_mode.extract_text_js(tpl),
                                histcov='{"ledgers_ok": true, "why": "", "capped": false, "total": 1, "shipped": 1}')
    path = os.path.join(_T, 'call.html')
    Path(path).write_text(html, encoding='utf-8')
    return path, {'rows': rows, 'cov': cov}


LOAD = """(d) => { ROWS = JSON.parse(JSON.stringify(d.rows)); COV = d.cov; try{ loadNotes(); }catch(e){} start(); }"""


class FindANumberFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path, cls.data = _build()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.ctx = self.browser.new_context()
        self.page = self.ctx.new_page()
        self.errors = []
        self.page.on('pageerror', lambda e: self.errors.append(str(e)))
        # 10:00 in Miami on a weekday: inside the 8-8 window, so the dial tap opens the outcome screen
        self.page.clock.install(time=datetime.datetime(2026, 11, 10, 15, 0, tzinfo=datetime.timezone.utc))
        self.open(clear=True)

    def tearDown(self):
        self.ctx.close()

    def open(self, clear=False):
        self.page.goto('file://' + self.path)
        self.page.wait_for_timeout(300)
        if clear:
            self.page.evaluate('() => { localStorage.clear(); notes = {}; }')
        self.page.evaluate(LOAD, self.data)

    def ev(self, js):
        return self.page.evaluate(js)

    def to_list(self):
        self.ev("() => document.querySelector('.lane button[data-lk]').click()")
        self.assertEqual(self.ev('BLANE'), '__lk')

    def peek(self):
        return self.ev("document.getElementById('peek').innerText")

    def test_a_script_follows_the_list_lead(self):
        self.assertEqual(self.ev('cur && cur.c'), DIAL)
        self.assertIn('Maria', self.peek())
        self.to_list()
        first = self.ev("document.querySelector('.lkcard').getAttribute('data-lkc')")
        want = 'Carlos' if first == LK1 else 'Ana'
        self.assertIn(want, self.peek(), 'the script on the list must be for a list lead')
        self.assertNotIn('Maria', self.peek(), 'the previous dial lead leaked into the list script')
        other = LK2 if first == LK1 else LK1
        self.ev("() => document.querySelector('.lkcard[data-lkc=\"%s\"]').click()" % other)
        self.assertIn('Ana' if other == LK2 else 'Carlos', self.peek())
        self.assertEqual(self.ev('LKFOCUS'), other)
        self.assertEqual(self.errors, [])

    def test_b_draft_survives_a_repaint(self):
        self.to_list()
        self.ev("""() => { const i = document.querySelector('input.lknum[data-c="%s"]');
                           i.focus(); i.value = '305555'; i.dispatchEvent(new Event('input')); render(); }""" % LK2)
        got = self.ev("""() => { const i = document.querySelector('input.lknum[data-c="%s"]');
                                 return [i.value, document.activeElement === i]; }""" % LK2)
        self.assertEqual(got, ['305555', True])
        self.assertEqual(self.errors, [])

    def test_c_reload_returns_to_the_list(self):
        self.to_list()
        self.ev("""() => { document.querySelector('.lkcard[data-lkc="%s"]').click();
                           const i = document.querySelector('input.lknum[data-c="%s"]');
                           i.value = '305555'; i.dispatchEvent(new Event('input')); }""" % (LK2, LK2))
        self.open()
        self.assertEqual(self.ev('BLANE'), '__lk', 'a reload must land back on Find a number')
        self.assertEqual(self.ev('LKFOCUS'), LK2)
        self.assertEqual(self.ev("document.querySelector('input.lknum[data-c=\"%s\"]').value" % LK2), '305555')
        self.assertIn('Ana', self.peek())
        self.assertEqual(self.errors, [])

    def test_d_logging_the_call_comes_back_to_the_list(self):
        self.to_list()
        self.ev("""() => { document.querySelector('input.lknum[data-c="%s"]').value = '3055550199';
                           document.querySelector('button.lkadd[data-c="%s"]').click(); }""" % (LK1, LK1))
        self.page.wait_for_timeout(300)
        self.assertEqual(self.ev('[SCREEN, cur && cur.c, LKFROM]'), ['outcome', LK1, LK1])
        self.ev("() => document.querySelector('.oc button[data-oc=noanswer]').click()")
        self.page.wait_for_timeout(400)
        self.ev("() => Array.from(document.querySelectorAll('#app button')).filter(b => /Next lead/.test(b.innerText))[0].click()")
        self.page.wait_for_timeout(400)
        if self.ev('SCREEN') == 'complete':
            self.ev("() => document.getElementById('cmplog').click()")
            self.page.wait_for_timeout(400)
        st = self.ev("[SCREEN, BLANE, LKFROM, LKFOCUS]")
        self.assertEqual(st, ['lead', '__lk', None, LK2], 'after the call he must be back on the list, next lead')
        dials = self.ev("(notes['%s'] || {}).dials || []" % LK1)
        self.assertTrue(dials and dials[-1].get('oc') == 'noanswer', dials)
        self.assertEqual(self.errors, [])

    def test_e_back_without_logging(self):
        self.to_list()
        self.ev("""() => { document.querySelector('input.lknum[data-c="%s"]').value = '3055550199';
                           document.querySelector('button.lkadd[data-c="%s"]').click(); }""" % (LK1, LK1))
        self.page.wait_for_timeout(300)
        self.ev("() => document.getElementById('ocback').click()")
        self.page.wait_for_timeout(700)          # the page ignores taps for 400 ms after a screen change
        self.assertEqual(self.ev("document.getElementById('navback').innerText").strip(), '‹ Back to Find a number')
        self.ev("() => document.getElementById('navback').click()")
        self.page.wait_for_timeout(200)
        self.assertEqual(self.ev('[SCREEN, BLANE, LKFROM]'), ['lead', '__lk', None])
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
