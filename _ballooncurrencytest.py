"""Formatted balloon values must not abort the extra-county merge; fake records only."""
import ast
from pathlib import Path
import unittest
import balloon_leads as B
from foreclosure_leads import _money


def tax_merge(row):
    tree = ast.parse(Path(__file__).with_name('foreclosure_leads.py').read_text())
    block = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                 and "_d.get('etax')" in ast.unparse(n.test))
    env = {'_d': row, '_money': _money}
    exec(compile(ast.Module(body=[block], type_ignores=[]), 'county-tax-merge', 'exec'), env)
    return row


class BalloonCurrencyTests(unittest.TestCase):
    def test_formatted_value_does_not_abort_balloon_merge(self):
        row = tax_merge({'st': 'BAL', 'value': '$1,394,636.00', 'hs': False})
        self.assertEqual(row['etax'], 29287)
        self.assertTrue(row['etaxest'])

    def test_numeric_and_absent_values(self):
        self.assertEqual(tax_merge({'value': 100000, 'hs': True})['etax'], 1300)
        self.assertNotIn('etax', tax_merge({'value': None}))

    def test_missing_contacts_are_separate_from_currency_parsing(self):
        hit = {'owner': 'SYNTHETIC UI LLC', 'origin': '2025-01-01',
               'age_mo': 12, 'amt': 100000, 'jv': '$1,394,636.00', 'fits': True}
        empty = B.rows(hits=[hit], contacts={})[0]
        funded = B.rows(hits=[hit], contacts={'SYNTHETIC UI LLC': {
            'phones': [{'number': '3055550101', 'dnc': False}]}})[0]
        self.assertEqual(empty['phones'], [])
        self.assertEqual(funded['phones'], ['3055550101'])
        self.assertEqual(tax_merge(funded)['phones'], ['3055550101'])


if __name__ == '__main__':
    unittest.main()
