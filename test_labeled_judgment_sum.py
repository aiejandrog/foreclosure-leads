import unittest
from unittest.mock import patch
import miami_judgment as MJ


def row(label, amount, kind='charge', **extra):
    return {'label': label, 'amount': amount, 'kind': kind, 'confident': True, 'page': 2, **extra}


class LabeledJudgmentTests(unittest.TestCase):
    def items(self):
        return [row(label, amount) for label, amount in [
            ('Principal', 913098.51), ('Accrued interest at 7.875%', 87043.01),
            ('(c) Per Diem Interest 04/18/2026 - 06/23/2026 ($197.00/day)', 13199),
            ('Escrow advances', 34146.28), ('Late charge', 4644.08),
            ('Door knock', 80), ('BPO', 105), ('Payoff statement fee', 120),
            ('Attorney fees', 6414), ('Attorney costs', 2668.24)]]

    def candidate(self, items, total):
        if not any(r.get('kind') == 'total' for r in items):
            items = items + [row('TOTAL', total, 'total')]
        items = [dict(r, id=r.get('id', str(i + 1))) for i, r in enumerate(items)]
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
        items = self.items() + [row('(b) per diem (inline in label)', 197, 'rate'),
                               row('Interest rate (%)', 7.875, 'rate'),
                               row('TOTAL', 1061518.12, 'total')]
        self.assertTrue(self.candidate(items, 1061518.12)['sum_check'])

    def test_disagreeing_printed_subtotal_fails(self):
        items = [row('Cost A', 10), row('Cost B', 20),
                 row('Costs subtotal', 31, 'subtotal', item_ids=['1', '2']),
                 row('Principal', 100), row('TOTAL', 130, 'total')]
        self.assertFalse(self.candidate(items, 130)['sum_check'])

    def test_agreeing_subtotal_is_not_double_counted(self):
        items = [row('Cost A', 10), row('Cost B', 20),
                 row('Costs subtotal', 30, 'subtotal', item_ids=['1', '2']),
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

    def test_charge_equal_to_rate_requires_review(self):
        result = self.candidate([row('Award', 197), row('per diem', 197, 'rate')], 197)
        self.assertFalse(result['sum_check'])

    def test_missing_kind_is_not_inferred(self):
        self.assertFalse(self.candidate([row('Principal', 10, None)], 10)['sum_check'])

    def test_charge_equal_to_inline_rate_requires_review(self):
        self.assertFalse(self.candidate([row('interest at $197/day', 197)], 197)['sum_check'])

    def test_string_false_is_not_confident(self):
        parsed = MJ.DV._parse('{"rows":[{"id":"a","kind":"charge","label":"A",'
                              '"amount":"10","confident":"false"}]}')
        self.assertFalse(parsed['rows'][0]['confident'])

    def test_subtotal_without_explicit_members_fails(self):
        self.assertFalse(self.candidate([row('A', 10), row('Subtotal', 10, 'subtotal')], 10)['sum_check'])

    def test_parser_preserves_kinds_and_members(self):
        parsed = MJ.DV._parse('{"rows":[{"id":"s","label":"subtotal","kind":"subtotal",'
                              '"amount":"10.00","item_ids":["a"],"confident":true}],'
                              '"grand_total":null,"unreadable":[]}')
        self.assertEqual(parsed['rows'][0].get('kind'), 'subtotal')
        self.assertEqual(parsed['rows'][0].get('item_ids'), ['a'])

    def test_bad_subtotal_on_other_page_fails_document(self):
        figures = [dict(row('A', 10), page=1, id='a'),
                   dict(row('Subtotal', 11, 'subtotal', item_ids=['a']), page=1, id='s'),
                   dict(row('B', 100), id='b'), dict(row('Total', 100, 'total'), id='t')]
        detail = {'figures': figures, 'pages': {}, 'errors': {},
                  'grand_totals': [{'amount': 100, 'page': 2}]}
        with patch.object(MJ.DV, 'read_document', return_value=detail):
            candidates, _ = MJ.vision_candidates('unused', {'pages': []}, None)
        self.assertFalse(candidates[0]['sum_check'])


if __name__ == '__main__':
    unittest.main()
