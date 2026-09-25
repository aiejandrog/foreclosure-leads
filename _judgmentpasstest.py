"""judgment_pass: the plan reads state from disk and prices only unread amount pages. Synthetic data."""
import hashlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import judgment_pass as JP

CASE = '2025-000001-CA-01'


def _row(base, entry, text, name):
    ref = 'court:%s:1' % entry
    row = {'source_ref': ref, 'manifest': {'sha256': 'h' + entry},
           'reading': {'pages': [{'page': 1, 'text': text}, {'page': 2, 'text': 'Total $1,234.56'}]}}
    (Path(base) / (hashlib.sha256(name.encode()).hexdigest() + '.json')).write_text(json.dumps(row))
    return ref


def _plan(docs):
    return {'documents': [dict(entry_id=e, kind=k, eligible_for_acquisition=ok, gaps=g)
                          for e, k, ok, g in docs]}


class Assess(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        (self.base / 'inventory.json').write_text('{}')

    def tearDown(self):
        self.tmp.cleanup()

    def run_assess(self, docs, timeline=None):
        with mock.patch('document_prioritizer.prioritize', return_value=_plan(docs)):
            return JP.assess(CASE, self.base, timeline, '2026-09-25')

    def test_no_docket(self):
        (self.base / 'inventory.json').unlink()
        self.assertEqual(JP.assess(CASE, self.base, None, '2026-09-25')['state'], 'no_docket')

    def test_verified_needs_ok_check_on_controlling(self):
        timeline = {'judgments': {'controlling_entry': '9'},
                    'amount_vision': {'amount_checks': [{'entry_id': '8', 'ok': True},
                                                         {'entry_id': '9', 'ok': False}]}}
        _row(self.base, '9', '$5.00', 'a')
        got = self.run_assess([('9', 'final_judgment', True, []), ('8', 'final_judgment', True, [])],
                              timeline)
        self.assertEqual(got['state'], 'needs_paid_read')    # an ok check on another judgment is not it
        timeline['amount_vision']['amount_checks'][1]['ok'] = True
        got = self.run_assess([('9', 'final_judgment', True, [])], timeline)
        self.assertEqual(got['state'], 'verified')

    def test_pages_counted_in_reader_order_and_cache_skipped(self):
        _row(self.base, '5', 'order $1.00', 'o')          # a controlling order read first
        ref = _row(self.base, '9', 'judgment $2.00', 'j')
        docs = [('5', 'vacatur', True, []), ('9', 'final_judgment', True, [])]
        got = self.run_assess(docs)
        self.assertEqual((got['state'], got['pages_to_judgment'], got['pages_all']),
                         ('needs_paid_read', 4, 4))
        (self.base / ('amount-vision-' + JP._vision_key(ref) + '.json')).write_text('{}')
        got = self.run_assess(docs)
        self.assertEqual(got['pages_all'], 2)            # the bought judgment is not priced again

    def test_login_walled_judgment_named(self):
        got = self.run_assess([('9', 'final_judgment', False, ['no_image_count_established'])])
        self.assertEqual(got['state'], 'judgment_not_fetched')
        self.assertIn('no_image_count_established', got['detail'])

    def test_no_judgment(self):
        self.assertEqual(self.run_assess([('3', 'motion', True, [])])['state'],
                         'no_judgment_on_docket')


class Rate(unittest.TestCase):
    def test_average_over_bought_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / 'case'
            d.mkdir()
            (d / 'amount-vision-a.json').write_text(json.dumps({'selected_pages': [1, 2], 'usd': 0.05}))
            (d / 'amount-vision-b.json').write_text(json.dumps({'selected_pages': [1], 'usd': 0.01}))
            rate, n = JP.evidence_rate(tmp)
            self.assertEqual(n, 3)
            self.assertAlmostEqual(rate, 0.02)
            self.assertEqual(JP.evidence_rate(Path(tmp) / 'none'), (None, 0))


class Render(unittest.TestCase):
    def test_prices_and_windows(self):
        rows = [{'case': CASE, 'sale': '2026-09-28', 'state': 'needs_paid_read',
                 'pages_to_judgment': 3, 'pages_all': 5},
                {'case': '2025-000002-CA-01', 'sale': '2026-12-01', 'state': 'verified',
                 'pages_to_judgment': 0, 'pages_all': 0}]
        out = JP.render(rows, ['2026A00001'], 0.02, 10, date(2026, 9, 25))
        self.assertIn('| next 7 days | 1 | 3 | $0.06 | 5 | $0.10 |', out)
        self.assertIn('| verified | 0 | 0 | 1 | 0 | 0 | 1 |', out)
        self.assertIn('left out: 1', out)


if __name__ == '__main__':
    unittest.main()
