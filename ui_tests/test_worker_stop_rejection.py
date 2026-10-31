"""Generated Worker error callback after Stop; all requests and writes are intercepted."""
import json
from pathlib import Path

import test_operations as base


class WorkerStopRejectionUI(base.OperationsUI):
    # This class reuses the fixture/browser lifecycle, not its inherited operational tests.
    test_worker_buttons_lanes_and_skip = None
    test_worker_call_outcomes = None
    test_worker_start_stop = None
    test_call_mode_unlock_and_wrong_number_choices = None

    def test_stopped_automatic_request_cannot_fall_back_to_composer(self):
        self.page.goto(self.base + '/board.html')
        self.page.wait_for_function("typeof genMorningWorker === 'function' && DATA.length === 5")
        html = self.page.evaluate("genMorningWorker('urgent', false)")
        # Supply reserved-domain fake mail payloads on the already-generated test cards.
        # Eligibility/suppression functions and the production template are never replaced.
        start = html.index('var LANES=') + len('var LANES=')
        end = html.index(', LMETA=', start)
        lanes = json.loads(html[start:end])
        self.assertGreater(len(lanes['urgent']), 0)
        for index, row in enumerate(lanes['urgent']):
            row.update(mailTo=f'synthetic{index}@example.invalid',
                       mailSubj='Synthetic subject', mailBody='Synthetic body',
                       emails=[f'synthetic{index}@example.invalid'],
                       gurl='https://example.invalid/compose')
        html = html[:start] + json.dumps(lanes) + html[end:]
        (Path(self.tmp.name) / 'worker-stop.html').write_text(html, encoding='utf-8')

        pending, requests, attempted_external = [], [], []
        headers = {'Access-Control-Allow-Origin': '*',
                   'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                   'Access-Control-Allow-Headers': 'Content-Type'}

        def isolate(route):
            req = route.request
            if req.method == 'GET' and req.url == self.base + '/worker-stop.html':
                return route.continue_()
            if req.method == 'OPTIONS':
                return route.fulfill(status=204, headers=headers, body='')
            if req.method == 'GET' and '/health' in req.url:
                return route.fulfill(status=200, headers=headers, content_type='application/json',
                                     body=json.dumps({'ok': True, 'ready': True, 'sent_today': 0,
                                                      'cap': 300, 'text_hold': False}))
            if req.method == 'POST' and req.url == 'http://127.0.0.1:8823/send':
                requests.append(json.loads(req.post_data))
                pending.append(route)  # Never reaches a server; reject only after pressing Stop.
                return
            if req.url.startswith('https://example.invalid/'):
                attempted_external.append(req.url)
            return route.fulfill(status=403, headers=headers, content_type='application/json',
                                 body='{"ok":false,"blocked":"synthetic_ui_isolation"}')

        self.context.unroute('**/*')
        self.context.route('**/*', isolate)
        worker = self.context.new_page()
        worker.goto(self.base + '/worker-stop.html')
        start_button = worker.locator('#mwrunbar button[data-run="lane"]')
        worker.locator('#mwrunbar button[data-run="lane"]:not([disabled])').wait_for()
        with worker.expect_request(lambda req: req.method == 'POST' and
                                   req.url == 'http://127.0.0.1:8823/send'):
            start_button.click()
        self.assertEqual(len(pending), 1, 'the synthetic request must remain pending before Stop')
        worker.locator('#mwrunbar button[data-run="stop"]').click()
        before = worker.locator('#mwprog').inner_text()
        pending.pop().abort('failed')
        worker.wait_for_function("""() => {
            const text = document.getElementById('mwlog').innerText;
            return text.includes('went offline mid-run') || text.includes('press Send in Gmail') ||
                   text.includes('Gmail popup blocked');
        }""")
        self.assertEqual(len(requests), 1)
        self.assertEqual(worker.locator('#mwprog').inner_text(), before,
                         'a late rejection must not advance the stopped queue')
        self.assertEqual(worker.locator('#mwsentyes').count(), 0,
                         'Stop must not turn an automatic failure into a manual confirmation')
        self.assertEqual(attempted_external, [], 'Stop must prevent a composer popup after rejection')
        self.assertIn('went offline mid-run', worker.locator('#mwlog').inner_text())
        self.assertNotIn(' on', worker.locator('#mwrunbar').get_attribute('class'))
        self.assertEqual(self.errors, [])
