"""A judgment form that prints each row as label / "$" / figure on three lines (2025-023462 shape).

Synthetic figures and labels only. The real page's printed TOTAL produced no candidate before
judgment_money.table_lines; these pin the fold and the cases it must leave alone."""
import unittest

import _moneytest as MT
import judgment_money as JM
import miami_judgment as MJ

NBSP = '\xa0'
ROWS = [('Unpaid Principal Balance', '100,000.00'),
        ('Accrued Interest at 7.000% from 01/01/2025\nthrough 01/31/2026 (per diem: $19.18)', '7,000.00'),
        ('Escrow Advances', '2,500.50'),
        ('Late Charge', '300.25'),
        ("Attorneys' Fees", '1,200.00')]
TOTAL = '111,000.75'


def page(rows=ROWS, total=TOTAL):
    body = ['5.', 'The Plaintiff is due the following amount:']
    for n, (label, figure) in enumerate(rows):
        body += ['(%s)' % 'abcdefghij'[n]] + label.split('\n') + ['$', figure]
    body += [NBSP, 'TOTAL  ', NBSP, '$', total, NBSP, 'It is ORDERED AND ADJUDGED that the clerk shall sell the property.']
    return '\n'.join(body)


def reading(text):
    return {'pages': [{'page': 1, 'outcome': 'text', 'text': text, 'text_source': 'embedded'}]}


class SplitRowLayoutTests(unittest.TestCase):
    def test_the_split_rows_total_verifies(self):
        found = [c for c in MJ.judgment_amount_candidates(reading(page())) if c['amount'] == 111000.75]
        self.assertTrue(found, 'the printed TOTAL must become a candidate')
        self.assertTrue(found[0]['sum_check'], found[0]['sum_check_reason'])
        # the per diem figure is a rate, kept out of the sum
        self.assertIn(19.18, [r['value'] for r in found[0]['sum_check_rates']])

    def test_a_total_its_rows_do_not_make_does_not_verify(self):
        found = [c for c in MJ.judgment_amount_candidates(reading(page(total='111,000.76')))
                 if c['amount'] == 111000.76]
        self.assertTrue(found)
        self.assertFalse(found[0]['sum_check'])

    def test_rows_fold_onto_their_labels(self):
        lines = JM.table_lines(page())
        self.assertIn('Unpaid Principal Balance $100,000.00', lines)
        self.assertIn('TOTAL $111,000.75', [ln.strip() for ln in lines])
        self.assertNotIn('$', [ln.strip() for ln in lines])

    def test_pages_without_a_lone_dollar_line_are_untouched(self):
        for text in (MT.PAGE1, MT.PAGE2, MT.PAGE3, 'TOTAL $5.00\n$ sign in prose\n'):
            self.assertEqual(JM.table_lines(text), text.splitlines())

    def test_a_lone_dollar_line_is_left_alone_when_no_amount_follows(self):
        text = 'Principal\n$\nsee schedule attached\nTOTAL $9.00'
        self.assertEqual(JM.table_lines(text), text.splitlines())

    def test_a_label_that_already_carries_a_figure_is_not_given_a_second(self):
        text = 'Principal $100.00\n$\n200.00\nTOTAL $300.00'
        self.assertEqual(JM.table_lines(text), text.splitlines())

    def test_a_lone_dollar_line_with_nothing_above_is_left_alone(self):
        text = '$\n100.00\nTOTAL $100.00'
        self.assertEqual(JM.table_lines(text), text.splitlines())


if __name__ == '__main__':
    unittest.main()
