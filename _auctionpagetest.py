"""Offline walk of the RealForeclose waiting-list pager.

The 2026-10-17 refresh ended 11 of 34 Miami-Dade auction dates with
`end=unproven: Next was clickable but the grid never showed a new first case`
(12/07/2026 was 17 rows / 2 pages, 01/11/2027 was 22 rows / 3 pages). Those
counts are a full day: the site leaves `.PageRight` in the DOM on the last
page and answers another page request with the same rows. The old loop treated
that — and a paint that never landed — as one failure, and it only compared
the first case number.

This grid is fake. Case numbers only. No browser, no network.
Run: python _auctionpagetest.py
"""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import foreclosure_leads as F


def cases(n, start):
    return ['2099-%06d-CA-01' % i for i in range(start, start + n)]


# The two dates named in the 10/17 log: 10+7 = 17 over 2 pages, 10+10+2 = 22 over 3.
DAY_17 = [cases(10, 1), cases(7, 11)]
DAY_22 = [cases(10, 100), cases(10, 110), cases(2, 120)]


class FakeGrid:
    """One auction date. `request(n)` is bypassPage=n: past the end, the last page repeats.

    indicator='accurate' reports the page on screen. 'lag' stays on "1 of N" the way
    `#curPWA` does until a later UPDATE poll. 'blank' has not painted a number at all.
    `swallow` is how many requests are issued and then never paint. `delay` is how many
    reads stay on the old rows (loading) before the paint lands.
    """

    def __init__(self, pages, indicator='accurate', swallow=0, delay=0):
        self.pages = pages
        self.indicator = indicator
        self.swallow = swallow
        self.delay = delay
        self.index = 0
        self.want = 0
        self.applied = 0
        self.loading = False
        self._pending = None
        self.requests = []
        self.clicks = 0

    def _rows(self):
        return [{'Case #': c, 'AID': '9%06d' % (900000 + i)}
                for i, c in enumerate(self.pages[self.index])]

    def _indicator(self):
        if self.indicator == 'blank':
            return '', ''
        mx = str(len(self.pages))
        if self.indicator == 'lag':
            return '1', mx
        return str(self.index + 1), mx

    def read(self):
        if self._pending:
            self._pending[1] -= 1
            if self._pending[1] <= 0:
                self.index = self._pending[0]
                self.applied = self._pending[2]
                self._pending = None
        self.loading = self._pending is not None
        cur, mx = self._indicator()
        return {'items': self._rows(), 'cur': cur, 'max': mx, 'loading': self.loading,
                'booting': False, 'want': self.want, 'applied': self.applied}

    def request(self, n):
        self.requests.append(n)
        self.want += 1
        if self.swallow > 0:
            self.swallow -= 1
            return 'ok'
        idx = min(max(int(n), 1), len(self.pages)) - 1
        if self.delay <= 0:
            self.index = idx
            self.applied = self.want
            self.loading = False
        else:
            self.loading = True
            self._pending = [idx, self.delay, self.want]
        return 'ok'

    def click(self):
        """Next, for the fallback when loadArea is missing. The last page does not move."""
        self.clicks += 1
        if self.index + 1 < len(self.pages):
            self.index += 1
        return True


def walk(grid, **kw):
    kw.setdefault('poll_s', 0)
    return F.walk_waiting_pages(grid.read, grid.request, lambda s: None, **kw)


def case_list(items):
    return [it.get('Case #') for it in items]


class PagerTests(unittest.TestCase):
    def test_page_number_blank_is_unknown_not_one(self):
        self.assertIsNone(F.page_number(''))
        self.assertIsNone(F.page_number(None))
        self.assertIsNone(F.page_number('0'))
        self.assertIsNone(F.page_number('page'))
        self.assertEqual(F.page_number(' 2 '), 2)

    def test_same_rows_after_a_confirmed_paint_are_the_last_page(self):
        # The 10/17 failure: Next was clickable, the first case did not change,
        # and that was recorded as unproven even though the day was fully read.
        prev = F.page_row_signature([{'Case #': '2099-000001-CA-01', 'AID': '1'}])
        state = {'items': [{'Case #': '2099-000001-CA-01', 'AID': '1'}],
                 'cur': '1', 'max': '3', 'want': 2, 'applied': 2}
        self.assertEqual(F.classify_settled_page(prev, state, 1), 'last')

    def test_a_new_row_set_advances_even_when_the_first_case_repeats(self):
        prev = F.page_row_signature([
            {'Case #': '2099-000001-CA-01', 'AID': '1'},
            {'Case #': '2099-000002-CA-01', 'AID': '2'}])
        state = {'items': [
            {'Case #': '2099-000001-CA-01', 'AID': '1'},
            {'Case #': '2099-000003-CA-01', 'AID': '3'}],
            'cur': '1', 'max': '2', 'want': 1, 'applied': 0}
        self.assertEqual(F.classify_settled_page(prev, state, 0), 'advanced')

    def test_a_swallowed_paint_retries_while_the_indicator_says_more_pages(self):
        prev = F.page_row_signature([{'Case #': '2099-000001-CA-01', 'AID': '1'}])
        state = {'items': [{'Case #': '2099-000001-CA-01', 'AID': '1'}],
                 'cur': '1', 'max': '2', 'want': 1, 'applied': 0}
        self.assertEqual(F.classify_settled_page(prev, state, 0), 'retry')

    def test_day_of_17_over_two_pages_proves_the_end(self):
        grid = FakeGrid(DAY_17, indicator='lag')
        items, pages, why = walk(grid)
        self.assertEqual(why, 'last page')
        self.assertEqual(pages, 2)
        self.assertEqual(case_list(items), cases(10, 1) + cases(7, 11))
        self.assertEqual(grid.requests, [2, 3])

    def test_day_of_22_over_three_pages_proves_the_end(self):
        grid = FakeGrid(DAY_22, indicator='lag')
        items, pages, why = walk(grid)
        self.assertEqual(why, 'last page')
        self.assertEqual(pages, 3)
        self.assertEqual(len(items), 22)
        self.assertEqual(case_list(items), cases(10, 100) + cases(10, 110) + cases(2, 120))

    def test_accurate_indicator_stops_without_a_past_the_end_request(self):
        grid = FakeGrid(DAY_17, indicator='accurate')
        items, pages, why = walk(grid)
        self.assertEqual((why, pages, len(items)), ('last page', 2, 17))
        self.assertEqual(grid.requests, [2])

    def test_blank_indicator_still_walks_by_row_set(self):
        grid = FakeGrid(DAY_22, indicator='blank')
        items, pages, why = walk(grid)
        self.assertEqual(why, 'last page')
        self.assertEqual(pages, 3)
        self.assertEqual(len(items), 22)

    def test_shared_first_case_is_not_the_same_page(self):
        pages = [
            ['2099-000001-CA-01', '2099-000002-CA-01'],
            ['2099-000001-CA-01', '2099-000003-CA-01'],
        ]
        items, pages_n, why = walk(FakeGrid(pages, indicator='lag'))
        self.assertEqual(why, 'last page')
        self.assertEqual(pages_n, 2)
        self.assertIn('2099-000003-CA-01', case_list(items))

    def test_slow_paint_is_waited_out(self):
        grid = FakeGrid(DAY_17, indicator='lag', delay=5)
        items, pages, why = walk(grid, polls=12)
        self.assertEqual((why, pages, len(items)), ('last page', 2, 17))

    def test_swallowed_request_is_retried_then_the_day_completes(self):
        grid = FakeGrid(DAY_17, indicator='accurate', swallow=1)
        items, pages, why = walk(grid, polls=4, attempts=3)
        self.assertEqual(why, 'last page')
        self.assertEqual(pages, 2)
        self.assertEqual(len(items), 17)
        self.assertEqual(grid.requests, [2, 2])

    def test_a_request_that_never_paints_stays_unproven(self):
        grid = FakeGrid(DAY_22, indicator='accurate', swallow=50)
        items, pages, why = walk(grid, polls=3, attempts=2)
        self.assertTrue(why.startswith('unproven:'), why)
        self.assertEqual(pages, 1)
        self.assertEqual(len(items), 10)
        self.assertGreaterEqual(len(grid.requests), 2)

    def test_click_fallback_walks_when_there_is_no_page_request(self):
        grid = FakeGrid(DAY_17, indicator='accurate')

        def no_fn(n):
            grid.requests.append(('no-fn', n))
            return 'no-fn'

        items, pages, why = F.walk_waiting_pages(
            grid.read, no_fn, lambda s: None, click_next=grid.click, poll_s=0)
        self.assertEqual((why, pages, len(items)), ('last page', 2, 17))
        self.assertEqual(grid.clicks, 1)

    def test_cap_is_not_reported_as_the_last_page(self):
        grid = FakeGrid([cases(2, 1 + 2 * i) for i in range(8)], indicator='blank')
        old = F.MAX_PAGE_CLICKS
        F.MAX_PAGE_CLICKS = 3
        try:
            items, pages, why = walk(grid, polls=4)
        finally:
            F.MAX_PAGE_CLICKS = old
        self.assertTrue(why.startswith('cap:'), why)
        self.assertIn('3', why)
        self.assertEqual(pages, 4)
        self.assertEqual(len(items), 8)

    def test_single_page_with_a_clickable_next_is_proven(self):
        # No page number painted, Next still "clickable". The past-the-end
        # request repeats the only page; that is a proven end, not unproven.
        grid = FakeGrid([cases(4, 1)], indicator='blank')
        items, pages, why = walk(grid)
        self.assertEqual((why, pages, len(items)), ('last page', 1, 4))
        self.assertEqual(grid.requests, [2])

    def test_painted_one_of_one_does_not_ask_again(self):
        grid = FakeGrid([cases(4, 1)], indicator='accurate')
        items, pages, why = walk(grid)
        self.assertEqual((why, pages, len(items)), ('last page', 1, 4))
        self.assertEqual(grid.requests, [])


class ScrapeDateTests(unittest.TestCase):
    def setUp(self):
        self._saved = list(F.PAGING_UNPROVEN)
        F.PAGING_UNPROVEN[:] = []
        self._sleep = F.time.sleep
        F.time.sleep = lambda _s: None

    def tearDown(self):
        F.time.sleep = self._sleep
        F.PAGING_UNPROVEN[:] = self._saved

    def _page(self, grid, arm=True):
        class Page:
            def goto(self, url, timeout=None):
                self.url = url

            def wait_for_selector(self, sel, timeout=None, state=None):
                return True

            def evaluate(self, js, arg=None):
                if js is F.ARM_PAGER_JS:
                    return arm
                if js is F.REQUEST_WAITING_PAGE_JS:
                    return grid.request(arg)
                if js is F.CLICK_NEXT_JS:
                    return grid.click()
                if js is F.EXTRACT_JS:
                    return json.dumps(grid.read())
                raise AssertionError('unexpected script %r' % (js[:60],))

        return Page()

    def test_scrape_date_keeps_the_17_and_records_a_proven_end(self):
        grid = FakeGrid(DAY_17, indicator='lag')
        rows = F.scrape_date(self._page(grid), '12/07/2026', 'FC')
        self.assertEqual(len(rows), 17)
        self.assertTrue(all(r['AuctionDate'] == '12/07/2026' and r['sale_type'] == 'FC' for r in rows))
        self.assertEqual(F.PAGING_UNPROVEN, [])
        self.assertEqual(case_list(rows), cases(10, 1) + cases(7, 11))

    def test_scrape_date_records_an_unproven_date(self):
        grid = FakeGrid(DAY_22, indicator='blank', swallow=50)
        old_attempts, old_polls = F.PAGE_ATTEMPTS, F.PAGE_POLLS
        F.PAGE_ATTEMPTS, F.PAGE_POLLS = 2, 2
        try:
            rows = F.scrape_date(self._page(grid), '01/11/2027', 'FC')
        finally:
            F.PAGE_ATTEMPTS, F.PAGE_POLLS = old_attempts, old_polls
        self.assertEqual(len(rows), 10)
        self.assertEqual(len(F.PAGING_UNPROVEN), 1)
        self.assertIn('01/11/2027', F.PAGING_UNPROVEN[0])
        self.assertIn('unproven:', F.PAGING_UNPROVEN[0])

    def test_no_loadArea_falls_back_to_next_and_still_finishes(self):
        grid = FakeGrid(DAY_17, indicator='accurate')
        rows = F.scrape_date(self._page(grid, arm=False), '12/08/2026', 'FC')
        self.assertEqual(len(rows), 17)
        self.assertEqual(F.PAGING_UNPROVEN, [])
        self.assertEqual(grid.clicks, 1)


if __name__ == '__main__':
    unittest.main()
