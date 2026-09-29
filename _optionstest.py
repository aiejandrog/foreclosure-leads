"""options_strategy: keep / sell / refinance / bankruptcy per case.

SYNTHETIC FIXTURES ONLY. No homeowner data, no network. Most assertions are negative: what the
strategy must REFUSE to recommend. An unverified equity never produces a sell verdict, a stay stops
everything, a keep path never pays us, and there is no buy-back path to offer.

Run:  python _optionstest.py
"""
import unittest

import options_strategy as OS


def row(**kw):
    base = {'eqstate': 'priced', 'eq': 40, 'days': 120, 'ftype': 'MORTGAGE', 'ctype': 'Bank/Mortgage'}
    base.update(kw)
    return base


class Blocks(unittest.TestCase):
    def test_stay_blocks_everything(self):
        r = OS.assess(row(saleBkAct=True), 'sell')
        self.assertEqual(r['status'], 'blocked')
        self.assertIsNone(r['primary'])
        self.assertFalse(r['we_earn'])

    def test_claimed_and_dismissed_block(self):
        self.assertEqual(OS.assess(row(sibclaimed=True), 'keep')['status'], 'blocked')
        self.assertEqual(OS.assess(row(lpDismissed=True), 'keep')['status'], 'blocked')

    def test_malformed_row_never_raises(self):
        r = OS.assess(None, 'sell', facts='junk')
        self.assertEqual(r['status'], 'needs_facts')
        r = OS.assess({'days': 'soon', 'eq': 'lots'}, 'bogus')
        self.assertEqual(r['status'], 'needs_goal')

    def test_past_sale_is_lawyer_only(self):
        r = OS.assess(row(days=-3), 'sell')
        self.assertEqual(r['primary'], 'post_sale')
        self.assertFalse(r['we_earn'])


class Goal(unittest.TestCase):
    def test_no_goal_asks_keep_or_sell(self):
        r = OS.assess(row())
        self.assertEqual(r['status'], 'needs_goal')
        self.assertTrue(any('keep' in q and 'sell' in q for q in r['ask']))

    def test_undecided_recommends_neither(self):
        r = OS.assess(row(), 'undecided', {'can_pay': True})
        self.assertIsNone(r['primary'])
        self.assertTrue(r['keep_side'] and r['sell_side'])
        self.assertIn(r['keep_side'][0], r['alternatives'])   # nothing dropped when merging sides
        self.assertIn(r['sell_side'][0], r['alternatives'])


class Sell(unittest.TestCase):
    def test_unverified_equity_never_reaches_a_sell_path(self):
        for st in ('unpriced', 'none', 'unchecked', None):
            r = OS.assess(row(eqstate=st), 'sell')
            self.assertIsNone(r['primary'], st)
            self.assertEqual(r['status'], 'needs_facts')
            self.assertTrue(any('first mortgage' in q for q in r['ask']))

    def test_eqfake_is_unknown_even_when_state_says_fact(self):
        self.assertEqual(OS.equity_band(row(eqfake=True)), 'unknown')

    def test_long_runway_strong_equity_points_to_listing(self):
        r = OS.assess(row(days=120), 'sell')
        self.assertEqual(r['primary'], 'list')
        self.assertIn('wholesale', r['alternatives'])

    def test_short_runway_is_our_lane(self):
        r = OS.assess(row(days=40), 'sell')
        self.assertEqual(r['primary'], 'wholesale')
        self.assertTrue(r['we_earn'])

    def test_poor_condition_is_our_lane_even_with_time(self):
        self.assertEqual(OS.assess(row(days=200), 'sell', {'condition': 'poor'})['primary'], 'wholesale')

    def test_too_close_to_close_goes_to_a_lawyer(self):
        r = OS.assess(row(days=OS.MIN_DAYS_TO_CLOSE - 1), 'sell')
        self.assertEqual(r['primary'], 'court_time')
        self.assertFalse(r['we_earn'])

    def test_underwater_is_a_short_sale_not_ours(self):
        r = OS.assess(row(eq=-5), 'sell')
        self.assertEqual(r['primary'], 'short_sale')
        self.assertFalse(r['we_earn'])


class Keep(unittest.TestCase):
    def test_keep_never_pays_us(self):
        for facts in ({}, {'can_pay': True}, {'can_pay': False}):
            for d in (5, 20, 60, 200):
                for ft in ('MORTGAGE', 'HOA'):
                    r = OS.assess(row(days=d, ftype=ft), 'keep', facts)
                    self.assertFalse(r['we_earn'], (facts, d, ft))
                    self.assertNotEqual(r['primary'], 'wholesale')

    def test_close_sale_keep_is_lawyer_first(self):
        self.assertEqual(OS.assess(row(days=10), 'keep', {'can_pay': True})['primary'], 'court_time')

    def test_hoa_keep_is_reinstatement(self):
        r = OS.assess(row(ftype='HOA', ctype='HOA'), 'keep', {'can_pay': True})
        self.assertEqual(r['primary'], 'reinstate')

    def test_bank_keep_is_lender_workout(self):
        self.assertEqual(OS.assess(row(), 'keep', {'can_pay': True})['primary'], 'loss_mit')

    def test_refinance_only_with_verified_strong_equity_and_income(self):
        self.assertIn('refinance', OS.assess(row(eq=45), 'keep', {'can_pay': True})['alternatives'])
        self.assertNotIn('refinance', OS.assess(row(eq=45, eqstate='unpriced'), 'keep',
                                                {'can_pay': True})['alternatives'])
        self.assertNotIn('refinance', OS.assess(row(eq=45), 'keep', {'can_pay': False})['alternatives'])

    def test_unknown_ability_to_pay_is_asked(self):
        r = OS.assess(row(), 'keep')
        self.assertEqual(r['status'], 'needs_facts')
        self.assertTrue(r['ask'])

    def test_bankruptcy_is_never_primary(self):
        for d in (-1, 5, 20, 60, 200):
            for g in OS.GOALS:
                for facts in ({}, {'can_pay': True}, {'can_pay': False}, {'prior_bk_recent': True}):
                    self.assertNotEqual(OS.assess(row(days=d), g, facts)['primary'], 'bankruptcy')

    def test_recent_bankruptcy_is_flagged_for_the_attorney(self):
        r = OS.assess(row(), 'keep', {'can_pay': True, 'prior_bk_recent': True})
        self.assertTrue(any('362(c)(3)' in n for n in r['notes']))


class Paths(unittest.TestCase):
    def test_only_wholesale_earns(self):
        self.assertEqual([k for k, v in OS.PATHS.items() if v['we_earn']], ['wholesale'])

    def test_no_buyback_or_leaseback_path_exists(self):
        for k, v in OS.PATHS.items():
            text = (k + ' ' + v['label']).lower()
            for bad in ('buyback', 'buy-back', 'leaseback', 'rent-back', 'lease option', 'repurchase'):
                self.assertNotIn(bad, text, k)

    def test_every_named_path_is_detailed(self):
        r = OS.assess(row(), 'undecided', {'can_pay': True})
        for p in [r['primary']] + r['alternatives']:
            if p:
                self.assertIn(p, r['paths'])


if __name__ == '__main__':
    unittest.main(verbosity=1)
