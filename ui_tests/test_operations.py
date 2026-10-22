"""Real Chromium + shipped templates, isolated fake data and blocked external network."""
import datetime as dt
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
from playwright.sync_api import sync_playwright
from ui_fixture import build, FIXTURE_CODE


class OperationsUI(unittest.TestCase):
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
        self.context.on('page', lambda page: page.on('pageerror', lambda e: self.errors.append(str(e))))
        def isolate(route):
            req = route.request
            if req.method == 'GET' and req.url in (self.base + '/board.html', self.base + '/call.html'):
                return route.continue_()
            # Fake health only; every write, external URL and unexpected endpoint is blocked.
            if req.method == 'GET' and '/health' in req.url:
                return route.fulfill(status=200, content_type='application/json', body=json.dumps(
                    {'ok': True, 'ready': True, 'sent_today': 0, 'cap': 300}))
            return route.fulfill(status=403, content_type='application/json',
                                 body='{"ok":false,"blocked":"synthetic_ui_isolation"}')
        self.context.route('**/*', isolate)
        self.page = self.context.new_page()

    def tearDown(self):
        print('PAGE ERRORS:', self.errors)
        for p in self.context.pages:
            if not p.is_closed():
                print('PAGE', p.url, p.locator('body').inner_text()[:1600])
        self.context.close()

    def worker(self):
        self.page.goto(self.base + '/board.html')
        self.page.wait_for_function("typeof openMorningWorker === 'function' && DATA.length === 5")
        with self.context.expect_page() as popup:
            self.page.evaluate("openMorningWorker('urgent')")
        w = popup.value
        w.clock.install(time=dt.datetime.now(dt.timezone.utc).replace(hour=16, minute=0, second=0))
        print('WORKER QUEUES:', self.page.evaluate("DATA.map(r=>({c:r.case, days:_saleDays(r), eligible:_workerEligible(r), reach:_workerReach(r)}))"))
        w.locator('#mw-call').wait_for()
        return w

    def test_worker_buttons_lanes_and_skip(self):
        w = self.worker()
        self.assertTrue(w.locator('#mw-call').is_visible())
        self.assertTrue(w.locator('#mw-auto').is_visible())
        before = w.locator('#mwprog').inner_text()
        w.locator('#mw-skip').click()
        self.assertNotEqual(w.locator('#mwprog').inner_text(), before)
        w.locator('.mwlane[data-lane="active"]').click()
        self.assertIn('on', w.locator('.mwlane[data-lane="active"]').get_attribute('class'))
        w.locator('.mwlane[data-lane="early"]').click()
        self.assertTrue(w.locator('#mw-call').is_visible())
        self.assertEqual(self.errors, [])

    def test_worker_call_outcomes(self):
        w = self.worker()
        w.locator('#mw-call').click()
        self.assertGreater(w.locator('.mwoc').count(), 0)
        w.locator('.mwoc[data-oc="noanswer"]').click()
        self.assertEqual(self.errors, [])

    def test_worker_start_stop(self):
        w = self.worker()
        start = w.locator('#mwrunbar button[data-run="lane"]')
        self.assertTrue(start.is_enabled())
        start.click()
        w.locator('#mwrunbar button[data-run="stop"]').click()
        self.assertNotIn(' on', w.locator('#mwrunbar').get_attribute('class'))
        w.locator('.mwlane[data-lane="active"]').click()
        self.assertNotIn(' on', w.locator('#mwrunbar').get_attribute('class'))
        self.assertEqual(self.errors, [])

    def test_call_mode_unlock_and_wrong_number_choices(self):
        self.page.goto(self.base + '/call.html')
        self.page.locator('#code').fill(FIXTURE_CODE)
        self.page.locator('#go').click()
        self.page.wait_for_function('ROWS && ROWS.length === 5')
        self.page.locator('#dial').wait_for()
        self.assertEqual(re.sub(r'\D', '', self.page.locator('#dial').inner_text()), '3055550101')
        self.page.evaluate("""() => {
            screenOutcome(); window.__choice=[];
            wrongChoice(ROWS[0], ROWS[0].p[0], 1, x => window.__choice.push(x));
        }""")
        self.page.wait_for_timeout(450)  # honor the production tap shield
        self.page.locator('[data-wc="badnum"]').click()
        self.assertEqual(self.page.evaluate('__choice'), ['badnum'])
        self.page.evaluate("wrongChoice(ROWS[0], ROWS[0].p[0], 1, x => window.__choice.push(x))")
        self.page.wait_for_timeout(450)
        self.page.locator('[data-wc="wrong"]').click()
        self.assertEqual(self.page.evaluate('__choice'), ['badnum', 'wrong'])
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
