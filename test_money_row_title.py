"""A money-table row whose label reads like a schedule heading ("Affidavit of Additional Advances")
does not cost a judgment its title; a real affidavit or schedule heading still does. Fake data only.

2018-026274's controlling judgment lists its post-judgment costs as label / "$figure" rows, one of
them "Affidavit of Additional Advances", and was held as untitled (acceptance replay 2026-10-08)."""
import unittest

import run_case_timeline as RCT
from test_title_boundaries import DECREE, OPENING, page, titled

COSTS = ['Post-judgment costs:', 'Motion to Amend Final Judgment', '$50.00', 'Motion to Cancel Foreclosure Sale',
         '$50.00', 'Affidavit of Additional Advances', '$1,234.56', 'Motion to Reset Foreclosure Sale', '$50.00']


class MoneyRowTitleTests(unittest.TestCase):
    def test_the_baseline_judgment_is_titled(self):
        self.assertTrue(titled(page(below=[OPENING])))

    def test_a_cost_row_on_page_two_keeps_the_title(self):
        self.assertTrue(titled(page(below=[OPENING]), DECREE + '\n' + '\n'.join(COSTS)))

    def test_a_cost_row_on_page_one_keeps_the_title(self):
        self.assertTrue(titled(page(below=[OPENING] + COSTS)))

    def test_a_capitalised_affidavit_heading_between_figures_still_rejects(self):
        rows = COSTS[:5] + ['AFFIDAVIT OF INDEBTEDNESS', '$1,234.56']
        self.assertFalse(titled(page(below=[OPENING]), DECREE + '\n' + '\n'.join(rows)))

    def test_a_heading_that_opens_a_schedule_still_rejects(self):
        # nothing above it is a row: a heading that starts the figures, not one among them
        for head in ('Affidavit of Indebtedness', 'Payoff Statement', 'Declaration of Amounts Due'):
            with self.subTest(heading=head):
                rows = ['Respectfully submitted.', head, '$1,234.56', 'Principal $1.00']
                self.assertFalse(titled(page(below=[OPENING]), DECREE + '\n' + '\n'.join(rows)))

    def test_a_heading_followed_by_prose_still_rejects(self):
        rows = COSTS[:5] + ['Affidavit of Additional Advances', 'I, the undersigned, being first duly sworn']
        self.assertFalse(titled(page(below=[OPENING]), DECREE + '\n' + '\n'.join(rows)))

    def test_the_row_test_itself(self):
        self.assertFalse(RCT._has_schedule_heading(['$50.00', 'Affidavit of Additional Advances', '$1.00']))
        self.assertTrue(RCT._has_schedule_heading(['Affidavit of Additional Advances', '$1.00']))
        self.assertTrue(RCT._has_schedule_heading(['$50.00', 'AFFIDAVIT OF ADDITIONAL ADVANCES', '$1.00']))
        self.assertTrue(RCT._has_schedule_heading(['$50.00', 'Affidavit of Additional Advances', 'sworn']))


if __name__ == '__main__':
    unittest.main()
