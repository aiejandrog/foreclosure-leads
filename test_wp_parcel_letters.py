"""Synthetic parcel joins: different condo-unit letters must not compare equal."""
import unittest
from foreclosure_leads import wp_parcel_verdict as verdict


class ParcelLetterTests(unittest.TestCase):
    def test_different_unit_letters_do_not_match(self):
        self.assertEqual(verdict('49-41-23-AB-0010', '49-41-23-AC-0010'), 'mismatch')

    def test_letters_are_case_insensitive_and_separators_do_not_matter(self):
        self.assertEqual(verdict('49-41-23-ab-0010', '494123AB0010'), 'ok')

    def test_missing_unit_letter_is_not_silently_ignored(self):
        self.assertEqual(verdict('494123A0010', '4941230010'), 'mismatch')

    def test_junk_is_unverified_and_absent_property_claim_stays_person_only(self):
        self.assertEqual(verdict('unknown', '494123A0010'), 'unverified')
        self.assertEqual(verdict('494123A0010', 'Property Appraiser'), 'unverified')
        self.assertEqual(verdict('', ''), 'ok')


if __name__ == '__main__':
    unittest.main()
