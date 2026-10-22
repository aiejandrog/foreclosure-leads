"""Real Chromium + shipped templates, isolated fake data and blocked external network."""
import functools
import http.server
import json
import sys
import tempfile
import threading
import unittest
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
        self.context.close()

    def worker(self):
        self.page.goto(self.base + '/board.html')
        self.page.wait_for_function("typeof openMorningWorker === 'function' && DATA.length === 5")
        with self.context.expect_page() as popup:
            self.page.evaluate("openMorningWorker('urgent')")
        w = popup.value
        w.locator('#mw-call').wait_for()
        return w

    def test_worker_buttons_lanes_and_skip(self):
        w = self.worker()
        self.assertTrue(w.locator('#mw-call').is_visible())
        self.assertTrue(w.locator('#mw-auto').is_visible())
        before = w.locator('.mwname').inner_text()
        w.locator('#mw-skip').click()
        self.assertNotEqual(w.locator('.mwname').inner_text(), before)
        w.locator('.mwlane[data-lane="active"]').click()
        self.assertIn('on', w.locator('.mwlane[data-lane="active"]').get_attribute('class'))
        w.locator('.mwlane[data-lane="early"]').click()
        self.assertTrue(w.locator('#mw-call').is_visible())
        self.assertEqual(self.errors, [])

    def test_worker_start_stop_and_call_outcomes(self):
        w = self.worker()
        w.locator('#mw-call').click()
        self.assertGreater(w.locator('.mwoc').count(), 0)
        # Start/Stop uses production handlers, while transport is blocked by the context.
        w.evaluate('render()')
        w.locator('#mw-auto').click()
        w.locator('#mw-stop').click()
        self.assertFalse(w.evaluate('auto'))
        w.locator('.mwlane[data-lane="active"]').click()
        self.assertFalse(w.evaluate('auto'))
        self.assertEqual(self.errors, [])

    def test_call_mode_unlock_and_wrong_number_choices(self):
        self.page.goto(self.base + '/call.html')
        self.page.locator('#code').fill(FIXTURE_CODE)
        self.page.locator('#go').click()
        self.page.wait_for_function('ROWS && ROWS.length === 5')
        self.page.evaluate("""() => {
            const box = document.createElement('div'); box.className='oc';
            document.body.appendChild(box); window.__choice=[];
            wrongChoice(ROWS[0], ROWS[0].p[0], 1, x => window.__choice.push(x));
        }""")
        self.page.locator('[data-wc="badnum"]').click()
        self.assertEqual(self.page.evaluate('__choice'), ['badnum'])
        self.page.evaluate("wrongChoice(ROWS[0], ROWS[0].p[0], 1, x => window.__choice.push(x))")
        self.page.locator('[data-wc="wrong"]').click()
        self.assertEqual(self.page.evaluate('__choice'), ['badnum', 'wrong'])
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
