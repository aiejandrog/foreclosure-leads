import unittest
from unittest.mock import patch
import miami_judgment as MJ


def row(label, amount):
    return {'label': label, 'amount': amount, 'confident': True, 'page': 2}


class LabeledJudgmentTests(unittest.TestCase):
    def items(self):
        return [row(label, amount) for label, amount in [
            ('Principal', 913098.51), ('Accrued interest at 7.875%', 87043.01),
            ('Per Diem Interest 04/18/2026 - 06/23/2026', 13199),
            ('Escrow advances', 34146.28), ('Late charge', 4644.08),
            ('Door knock', 80), ('BPO', 105), ('Payoff statement fee', 120),
            ('Attorney fees', 6414), ('Attorney costs', 2668.24)]]

    def candidate(self, items, total):
        detail = {'figures': items, 'grand_totals': [{'page': 2, 'amount': total}],
                  'pages': {2: {'rows': items, 'unreadable': []}}, 'errors': {}}
        with patch.object(MJ.DV, 'read_document', return_value=detail):
            results, _ = MJ.vision_candidates('unused.pdf', {'pages': [
                {'page': 2, 'outcome': 'ocr_text', 'text': 'TOTAL $1.00'}]}, None)
        return results[0]

    def test_ten_items_pass(self):
        result = self.candidate(self.items(), 1061518.12)
        self.assertTrue(result['sum_check'])
        self.assertEqual(len(result['sum_check_components']), 10)

    def test_rates_and_total_excluded_not_accrued_interest(self):
        items = self.items() + [row('Per diem rate', 197), row('Interest rate (%)', 7.875),
                               row('TOTAL', 1061518.12)]
        self.assertTrue(self.candidate(items, 1061518.12)['sum_check'])

    def test_disagreeing_printed_subtotal_fails(self):
        items = [row('Cost A', 10), row('Cost B', 20), row('Costs subtotal', 31),
                 row('Principal', 100), row('TOTAL', 130)]
        self.assertFalse(self.candidate(items, 130)['sum_check'])

    def test_agreeing_subtotal_is_not_double_counted(self):
        items = [row('Cost A', 10), row('Cost B', 20), row('Costs subtotal', 30),
                 row('Principal', 100)]
        self.assertTrue(self.candidate(items, 130)['sum_check'])

    def test_one_misread_item_fails(self):
        items = self.items()
        items[0]['amount'] = 913098.52
        self.assertFalse(self.candidate(items, 1061518.12)['sum_check'])

    def test_subset_cannot_override_full_sum_failure(self):
        self.assertFalse(self.candidate([row('A', 10), row('B', 20), row('C', 40)], 30)['sum_check'])

    def test_unlabeled_ocr_keeps_legacy_search(self):
        self.assertTrue(MJ.sum_check([10, 20, 40], 30)['ok'])


if __name__ == '__main__':
    unittest.main()
