"""Synthetic, offline tests for dollar-capped name search."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class SearchBudgetTests(unittest.TestCase):
    def test_cap_is_required_and_finite(self):
        import miami_search_budget as M
        with tempfile.TemporaryDirectory() as tmp:
            for limit in (None, 0, -1, float('nan'), float('inf')):
                with self.assertRaises(ValueError):
                    M.CaptchaBudget(Path(tmp) / 'budget.json', limit)

    def test_reservation_survives_restart_and_blocks_overspend(self):
        import miami_search_budget as M
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'budget.json'
            with M.CaptchaBudget(path, 1) as budget:
                budget.reserve('.60')
            with M.CaptchaBudget(path, 1) as budget:
                with self.assertRaises(M.CaptchaCapReached):
                    budget.reserve('.41')
                self.assertEqual(budget.report()['reserved_usd'], .6)

    def test_actual_cost_releases_only_confirmed_reservation(self):
        import miami_search_budget as M
        with tempfile.TemporaryDirectory() as tmp:
            with M.CaptchaBudget(Path(tmp) / 'budget.json', 1) as budget:
                ticket = budget.reserve('.6')
                budget.settle(ticket, '.003')
                self.assertEqual(budget.report()['actual_usd'], .003)
                self.assertEqual(budget.report()['reserved_usd'], 0)
                budget.reserve('.997')
                with self.assertRaises(M.CaptchaCapReached):
                    budget.reserve('.001')

    def test_restart_cannot_raise_cap(self):
        import miami_search_budget as M
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'budget.json'
            with M.CaptchaBudget(path, 1):
                pass
            with self.assertRaises(ValueError):
                with M.CaptchaBudget(path, 2):
                    pass

    def test_unbounded_paid_fallback_is_never_called(self):
        import miami_search_budget as M
        with tempfile.TemporaryDirectory() as tmp:
            with M.CaptchaBudget(Path(tmp) / 'budget.json', 1) as budget:
                searcher = M.CappedNameSearcher(budget, use_camoufox=False)
                with patch.object(searcher.R, 'fetch_via_turnstile', side_effect=AssertionError('unsafe spend')):
                    self.assertIsNone(searcher.search('SYNTHETIC OWNER'))
                self.assertEqual(searcher.gaps[-1]['status'], 'unknown')
                self.assertEqual(budget.report()['actual_usd'], 0)

    def test_cached_records_still_work_with_paid_fallback_blocked(self):
        import miami_search_budget as M
        with tempfile.TemporaryDirectory() as tmp:
            with M.CaptchaBudget(Path(tmp) / 'budget.json', 1) as budget:
                searcher = M.CappedNameSearcher(budget, {'SYNTHETIC OWNER': 'fake'}, use_camoufox=False)
                with patch.object(searcher.R, 'records_by_qs', return_value=[{'reC_BOOK': '1'}]):
                    self.assertEqual(searcher.search('SYNTHETIC OWNER'), [{'reC_BOOK': '1'}])
                self.assertEqual(searcher.gaps, [])

    def test_stale_cache_falls_back_to_browser_and_keeps_raw_rows(self):
        import miami_search_budget as M
        with tempfile.TemporaryDirectory() as tmp:
            with M.CaptchaBudget(Path(tmp) / 'budget.json', 1) as budget:
                searcher = M.CappedNameSearcher(budget, {'SYNTHETIC OWNER': 'stale'})
                with patch.object(searcher, '_camoufox', return_value=object()), \
                     patch.object(searcher.R, 'camoufox_qs', return_value='fresh'), \
                     patch.object(searcher.R, 'records_by_qs', side_effect=[None, [{'reC_BOOK': '2'}]]):
                    self.assertEqual(searcher.search('SYNTHETIC OWNER'), [{'reC_BOOK': '2'}])
                self.assertEqual(searcher.qs_cache['SYNTHETIC OWNER'], 'fresh')
                self.assertEqual(searcher.raw_results['SYNTHETIC OWNER'], [{'reC_BOOK': '2'}])
                self.assertEqual(searcher.routes['SYNTHETIC OWNER'], 'camoufox')

    def test_provider_ceiling_violation_blocks_further_reservations(self):
        import miami_search_budget as M
        with tempfile.TemporaryDirectory() as tmp:
            with M.CaptchaBudget(Path(tmp) / 'budget.json', 1) as budget:
                ticket = budget.reserve('.01')
                with self.assertRaises(M.CaptchaCapReached):
                    budget.settle(ticket, '.02')
                with self.assertRaises(M.CaptchaCapReached):
                    budget.reserve('.01')

    def test_corrupt_ledger_never_resets_cost(self):
        import miami_search_budget as M
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'budget.json'
            path.write_text('broken', encoding='utf-8')
            with self.assertRaises(ValueError):
                with M.CaptchaBudget(path, 1):
                    pass


if __name__ == '__main__':
    unittest.main()
