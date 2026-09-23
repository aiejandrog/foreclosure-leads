"""Accuracy-audit defects of 2026-09-23 (Salkey, Elharrar). SYNTHETIC FIXTURES ONLY.

Run:  python _auditdefecttest.py
"""
import unittest

import equity_state as ES
import records_liens as RL

FOLIO = '01-2345-678-9010'


def model(book, page, amount, date='1/15/2015', lender='WELLS FARGO BANK NA', cfn=None, doc='MORTGAGE'):
    return {'doC_TYPE': doc, 'foliO_NUMBER': FOLIO, 'subdiV_NAME': 'TEST SUBDIVISION',
            'reC_BOOK': book, 'reC_PAGE': page, 'reC_BOOKPAGE': '%s/%s' % (book, page),
            'reC_DATE': date, 'consideratioN_1': amount, 'intangible': 0,
            'seconD_PARTY': lender, 'cfN_MASTER_ID': cfn}


class DuplicateMortgageTests(unittest.TestCase):
    """Elharrar: the owner search returned each recording twice; each copy became a lien."""

    def test_same_recording_counts_once(self):
        a = model('30000', '100', 300000, '3/1/2016')
        b = model('31000', '200', 95000, '6/1/2018', lender='CITIBANK NA')
        out = RL.analyze([a, dict(a), b, dict(b)], FOLIO, 0)
        self.assertEqual(len(out['liens']), 2)
        self.assertEqual(sum(l['amt'] for l in out['liens']), 395000)
        self.assertEqual(out['open_count'], 2)

    def test_cfn_identifies_when_book_page_is_blank(self):
        a = model('', '', 200000, cfn='2016R0012345')
        out = RL.analyze([a, dict(a)], FOLIO, 0)
        self.assertEqual(len(out['liens']), 1)

    def test_distinct_recordings_of_the_same_amount_stay_two(self):
        a = model('30000', '100', 250000, '3/1/2016')
        b = model('30500', '900', 250000, '3/9/2017', lender='CITIBANK NA')
        self.assertEqual(len(RL.analyze([a, b], FOLIO, 0)['liens']), 2)

    def test_unidentifiable_rows_are_not_merged_on_a_guess(self):
        a = model('', '', 150000)
        self.assertEqual(len(RL.analyze([a, dict(a)], FOLIO, 0)['liens']), 2)


class LenderForeclosureClearTests(unittest.TestCase):
    """Salkey: VERIFIED CLEAR while a bank was foreclosing on the parcel."""

    CLEAN = {'conf': 'ok', 'liens': []}

    def test_bank_foreclosure_cannot_be_verified_clear(self):
        lead = {'ctype': 'Bank/Mortgage'}
        self.assertEqual(ES.apply(lead, dict(self.CLEAN)), 'none')
        self.assertNotIn(lead['eqstate'], ES.FACT)
        self.assertIn('lender is foreclosing', lead['eqstate_why'])

    def test_raw_lead_field_is_read_too(self):
        self.assertEqual(ES.state_of(self.CLEAN, {'case_type': 'Bank/Mortgage'}), 'none')

    def test_hoa_case_with_clean_chain_stays_clear(self):
        # An HOA suing proves an assessment debt, not a mortgage: clear is still possible.
        self.assertEqual(ES.state_of(self.CLEAN, {'ctype': 'HOA/Condo'}), 'clear')

    def test_no_lead_keeps_old_behaviour(self):
        self.assertEqual(ES.state_of(self.CLEAN), 'clear')

    def test_priced_chain_is_unaffected(self):
        chain = {'conf': 'ok', 'liens': [{'amt': 100000}]}
        self.assertEqual(ES.state_of(chain, {'ctype': 'Bank/Mortgage'}), 'priced')


if __name__ == '__main__':
    unittest.main()
