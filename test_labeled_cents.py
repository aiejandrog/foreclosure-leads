"""Exact-cent edges of labeled_sum_check that the ten-item suite does not pin down."""
import unittest

import miami_judgment as MJ


def rows(*charges, total=None):
    out = [{'id': str(i + 1), 'kind': 'charge', 'label': 'item %d' % i, 'amount': a,
            'confident': True} for i, a in enumerate(charges)]
    if total is not None:
        out.append({'id': 't', 'kind': 'total', 'label': 'TOTAL', 'amount': total,
                    'confident': True})
    return out


class ExactCentTests(unittest.TestCase):
    def test_one_cent_short_fails(self):
        # The old OCR check allowed 0.011 of slack. Typed rows get none.
        self.assertFalse(MJ.labeled_sum_check(rows(100.00, 50.00, total=150.01), 150.01)['ok'])

    def test_float_noise_does_not_fail_an_exact_sum(self):
        # 0.1 + 0.2 != 0.3 in binary floating point; Decimal arithmetic must not inherit that.
        self.assertTrue(MJ.labeled_sum_check(rows(0.10, 0.20, total=0.30), 0.30)['ok'])

    def test_sub_cent_amount_is_refused(self):
        self.assertFalse(MJ.labeled_sum_check(rows(100.005, 50.00, total=150.005), 150.005)['ok'])

    def test_total_row_disagreeing_with_stated_total_fails(self):
        self.assertFalse(MJ.labeled_sum_check(rows(100.00, 50.00, total=150.00), 151.00)['ok'])


if __name__ == '__main__':
    unittest.main()
