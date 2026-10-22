"""Call Mode numbers card: every number is listed, and a do-not-call number can never be dialled.

Real Chromium against the shipped call_mode page, synthetic leads only, external network blocked.
This is the browser half of #334, which put do-not-call numbers BACK on the card so the operator can
see them (`dn`, display-only) while keeping them out of the dial list `p`. The whole safety argument
is "visible but unreachable", and that is a claim about the rendered page, so it is asserted here
rather than in Python.

Four properties, one test each:
  (a) every number in `p` is listed on the card
  (b) each `dn` number renders struck through and labelled not dialable
  (c) tapping ANY row never puts a `dn` number on #dial or into a tel:/sms: link
  (d) the next-number walk (nextLivePh) never reaches a `dn` number

Test only: no change to call_mode.py or tracker_template.html.
"""
import functools
import http.server
import json
import re
import sys
import tempfile
import threading
import unittest
# cp1252 is the default stdout encoding on the laptop and the pages carry non-latin1
# text (checkmarks, middots, a curly apostrophe), so the debug prints below raised
# UnicodeEncodeError in tearDown and turned 4 PASSING tests into 4 errors. Same repo
# idiom as _resimplisynctest.py.
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright          # noqa: E402
from ui_fixture import build, FIXTURE_CODE               # noqa: E402


def digits(s):
    return re.sub(r'\D', '', str(s or ''))


class NumbersCardUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        build(cls.tmp.name)
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=cls.tmp.name)
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:' + str(cls.server.server_port)
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def setUp(self):
        self.context = self.browser.new_context(service_workers='block')
        self.context.set_default_timeout(8000)
        self.errors = []
        self.context.on('page', lambda page: page.on(
            'pageerror', lambda e: self.errors.append(str(e))))

        def isolate(route):
            req = route.request
            if req.method == 'GET' and req.url == self.base + '/call.html':
                return route.continue_()
            if req.method == 'GET' and '/health' in req.url:
                return route.fulfill(status=200, content_type='application/json', body=json.dumps(
                    {'ok': True, 'ready': True, 'sent_today': 0, 'cap': 300}))
            return route.fulfill(status=403, content_type='application/json',
                                 body='{"ok":false,"blocked":"synthetic_ui_isolation"}')

        self.context.route('**/*', isolate)
        self.page = self.context.new_page()
        self.unlock()

    def tearDown(self):
        if self.errors:
            print('PAGE ERRORS:', self.errors)
        self.context.close()

    def unlock(self):
        """Open the call page on the first lead, exactly as test_operations does."""
        self.page.goto(self.base + '/call.html')
        self.page.locator('#code').fill(FIXTURE_CODE)
        self.page.locator('#go').click()
        self.page.wait_for_function('ROWS && ROWS.length === 5')
        self.page.locator('#dial').wait_for()
        self.page.wait_for_selector('.numlist')

    def row(self):
        """The lead currently on screen, as the page itself sees it."""
        return self.page.evaluate('({p: (cur.p||[]).map(String), '
                                  'dn: (cur.dn||[]).map(function(x){return String(x[0]);})})')

    # ---- (a) every number in `p` is listed -----------------------------------------------------
    def test_every_dialable_number_is_listed(self):
        r = self.row()
        self.assertGreaterEqual(len(r['p']), 2, 'fixture must carry more than one dialable number')
        buttons = self.page.locator('.numlist button.numrow')
        self.assertEqual(buttons.count(), len(r['p']),
                         'one tappable row per number in p')
        shown = digits(self.page.locator('.numlist').inner_text())
        for n in r['p']:
            self.assertIn(digits(n), shown, 'number in p missing from the card')
        self.assertEqual(self.errors, [])

    # ---- (b) each `dn` number is struck through and labelled not dialable -----------------------
    def test_every_dnc_number_is_struck_through_and_labelled(self):
        r = self.row()
        self.assertGreaterEqual(len(r['dn']), 1, 'fixture must carry a do-not-call number')
        rows = self.page.locator('.numlist .dncrow')
        self.assertEqual(rows.count(), len(r['dn']), 'one dncrow per number in dn')
        for i in range(rows.count()):
            one = rows.nth(i)
            text = one.inner_text()
            self.assertIn('not dialable', text.lower(), 'dnc row is not labelled not dialable')
            self.assertIn('do-not-call', text.lower(), 'dnc row is not labelled do-not-call')
            # struck through as COMPUTED by the browser, not as written in the markup
            deco = one.locator('b').first.evaluate(
                'el => getComputedStyle(el).textDecorationLine')
            self.assertIn('line-through', deco, 'dnc number is not struck through')
        listed = digits(self.page.locator('.numlist').inner_text())
        for n in r['dn']:
            self.assertIn(digits(n), listed, 'dn number is not shown at all')
        self.assertEqual(self.errors, [])

    # ---- (c) tapping any row never puts a `dn` number on the dial button -----------------------
    def test_tapping_any_row_never_arms_a_dnc_number(self):
        r = self.row()
        dnc = {digits(n) for n in r['dn']}
        allowed = {digits(n) for n in r['p']}

        def armed():
            """Every number the page is currently offering to dial, by any route."""
            out = {digits(self.page.locator('#dial').inner_text())}
            for href in self.page.evaluate(
                    "Array.from(document.querySelectorAll('a[href]')).map(a=>a.getAttribute('href'))"):
                h = str(href or '')
                if h.startswith('tel:') or h.startswith('sms:') or 'phone' in h.lower():
                    out.add(digits(h)[-10:])
            return {x for x in out if x}

        start = armed()
        self.assertTrue(start & allowed, 'dial button should start on a number from p')
        self.assertFalse(start & dnc, 'a do-not-call number was armed before any tap')

        # tap EVERY row on the card, dialable and do-not-call alike
        rows = self.page.locator('.numlist .numrow')
        for i in range(rows.count()):
            one = rows.nth(i)
            one.click(force=True)
            self.page.wait_for_timeout(120)
            now = armed()
            self.assertFalse(now & dnc,
                             'tapping row %d armed a do-not-call number: %s' % (i, now & dnc))
            for n in now:
                self.assertIn(n, allowed,
                              'row %d armed a number that is not in p: %s' % (i, n))
        self.assertEqual(self.errors, [])

    # ---- (d) the next-number walk never reaches a `dn` number ----------------------------------
    def test_next_number_walk_never_reaches_a_dnc_number(self):
        r = self.row()
        dnc = {digits(n) for n in r['dn']}
        self.assertFalse(dnc & {digits(n) for n in r['p']},
                         'a do-not-call number is in the dial list p')
        # walk from every starting point, following nextLivePh the way screenLead does
        reached = self.page.evaluate("""() => {
            const seen = [];
            for (let s = 0; s < (cur.p||[]).length + 4; s++) {
                let k = nextLivePh(cur, s);
                for (let hop = 0; hop < 12 && k >= 0; hop++) {
                    seen.push(String((cur.p||[])[k]));
                    k = nextLivePh(cur, k + 1);
                }
            }
            return seen;
        }""")
        self.assertTrue(reached, 'the walk reached no number at all')
        for n in reached:
            self.assertNotIn(digits(n), dnc, 'the walk reached a do-not-call number')
            self.assertIn(digits(n), {digits(x) for x in r['p']},
                          'the walk reached a number outside p')
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
