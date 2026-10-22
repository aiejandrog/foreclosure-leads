"""Broward LP pagination regression: synthetic responses only, no network or sessions.

2026-10-22: a later page that fails keeps the rows already read and marks the window partial in
fl_lp.broward.LAST (#324 returned None there, which also dropped page 1's real filings).
"""
import json
import unittest
from unittest import mock

from fl_lp import broward


def sweep_with(pages):
    transport = mock.Mock(side_effect=['search accepted'] + pages)
    fake = mock.Mock(start_session=lambda: {'booktypes': 'all'}, _curl=transport,
                     BASE='https://example.invalid')
    with mock.patch.object(broward, 'B', fake), \
            mock.patch.object(broward, '_lp_code', lambda _: 'synthetic'), \
            mock.patch.object(broward.time, 'sleep', lambda _: None), \
            mock.patch.object(broward, 'SESSION_RETRY_WAITS', ()):
        return broward.sweep()


def page(count, total, start=0):
    return json.dumps({'data': [{'DirectName': 'SYNTHETIC LENDER',
                                'IndirectName': 'SYNTHETIC OWNER',
                                'InstrumentNumber': str(start + i)} for i in range(count)],
                       'total': total})


class PaginationTests(unittest.TestCase):
    def test_failed_later_page_keeps_rows_and_is_partial(self):
        rows = sweep_with([page(400, 401), '<html>unavailable</html>'])
        self.assertEqual(len(rows), 400, 'page 1 filings are real and must be kept')
        self.assertEqual(broward.LAST['status'], 'partial')
        self.assertEqual(broward.LAST['reported_total'], 401)
        self.assertEqual(broward.LAST['retrieved'], 400)
        self.assertTrue(any('page 2' in f for f in broward.LAST['failures']))

    def test_failed_first_page_still_fails(self):
        self.assertIsNone(sweep_with(['<html>unavailable</html>']))
        self.assertEqual(broward.LAST['status'], 'failed')

    def test_complete_grid_still_returns_every_row(self):
        self.assertEqual(len(sweep_with([page(400, 401), page(1, 401, 400)])), 401)
        self.assertEqual(broward.LAST['status'], 'ok')
        self.assertEqual(broward.LAST['failures'], [])

    def test_empty_page_before_the_total_is_partial(self):
        rows = sweep_with([page(400, 900), page(0, 900)])
        self.assertEqual(len(rows), 400)
        self.assertEqual(broward.LAST['status'], 'partial')

    def test_grid_without_a_total_is_not_complete(self):
        rows = sweep_with([json.dumps({'data': []})])
        self.assertEqual(rows, [])
        self.assertEqual(broward.LAST['status'], 'partial')

    def test_a_repeated_page_is_not_progress(self):
        rows = sweep_with([page(400, 700), page(400, 700)])
        self.assertEqual(broward.LAST['status'], 'partial')
        self.assertEqual(broward.LAST['retrieved'], 400)
        self.assertTrue(any('repeated' in f for f in broward.LAST['failures']))

    def test_verified_empty_window_is_ok(self):
        self.assertEqual(sweep_with([page(0, 0)]), [])
        self.assertEqual(broward.LAST['status'], 'ok')


if __name__ == '__main__':
    unittest.main()
