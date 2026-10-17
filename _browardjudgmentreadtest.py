#!/usr/bin/env python
"""Fake-text checks for the Broward judgment reader and the Palm Beach hand-import."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

import broward_judgment_read as BJR
import pb_judgment_import as PBI

CASE = 'CACE-99-000123'
HOA_CASE = 'CONO-99-000456'
PAGE1 = """IN THE CIRCUIT COURT
CASE NO. CACE-99-000123
FINAL JUDGMENT OF FORECLOSURE
The following amounts are now due and owing:
Principal balance $100,000.00
Accrued interest $5,000.00
Interest continues at $10.00 per day
"""
PAGE2 = """Attorney's fees $2,000.00
Court costs $500.00
GRAND TOTAL $107,500.00
The separate homeowners association judgment in case CONO-99-000456 is satisfied in full.
DONE AND ORDERED in chambers on March 1, 2024.
"""
BAD_PAGE2 = """Attorney's fees $2,000.00
Court costs $500.00
GRAND TOTAL $108,000.00
DONE AND ORDERED in chambers on March 1, 2024.
"""
SAT = """SATISFACTION OF JUDGMENT
CASE NO. CACE-99-000123
The Final Judgment of Foreclosure entered in case CACE-99-000123 is satisfied in full.
"""
PARTIAL = """PARTIAL SATISFACTION OF JUDGMENT
CASE NO. CACE-99-000123
The Final Judgment of Foreclosure entered in case CACE-99-000123 is partially satisfied to the extent of $1,000.00.
A balance remains due.
"""
FEE = """SATISFACTION OF ATTORNEY'S FEE JUDGMENT
CASE NO. CACE-99-000123
The attorney's fee judgment entered in case CACE-99-000123 is satisfied in full.
The final judgment of foreclosure remains outstanding.
"""
HOA_SAT = """SATISFACTION OF JUDGMENT
CASE NO. CONO-99-000456
The homeowners association judgment in case CONO-99-000456 is satisfied in full.
"""
HOA_J = """IN THE COUNTY COURT
CASE NO. CONO-99-000456
FINAL JUDGMENT
FAKE HOMEOWNERS ASSOCIATION, plaintiff.
The following amounts are now due and owing:
Assessments $2,000.00
Attorney's fees $300.00
Costs $50.00
GRAND TOTAL $2,350.00
DONE AND ORDERED in chambers on April 2, 2024.
"""


class Reader(unittest.TestCase):
    def test_two_page_table_verifies(self):
        result = BJR.read_judgment([PAGE1, PAGE2], CASE)
        self.assertTrue(result['ok'])
        self.assertFalse(result['void'])
        record = result['record']
        self.assertEqual(record, {
            'case': CASE,
            'total_cents': 10750000,
            'judgment_date': '2024-03-01',
            'verified_by': 'judgment_money',
            'pages_read': 2,
            'human_reviewed': False,
        })

    def test_total_that_does_not_reproduce_fails(self):
        result = BJR.read_judgment([PAGE1, BAD_PAGE2], CASE)
        self.assertFalse(result['ok'])
        self.assertFalse(result['void'])
        self.assertIsNone(result['record'])
        self.assertEqual(result['reason'], 'total_not_reproduced')

    def test_full_satisfaction_of_this_judgment_voids(self):
        result = BJR.read_judgment([SAT], CASE)
        self.assertTrue(result['void'])
        self.assertIsNone(result['record'])
        self.assertEqual(result['reason'], 'satisfied_in_full')

    def test_partial_satisfaction_does_not_void(self):
        result = BJR.read_judgment([PARTIAL], CASE)
        self.assertFalse(result['void'])
        self.assertIsNone(result['record'])
        self.assertEqual(result['reason'], 'partial_satisfaction')

    def test_fee_judgment_satisfaction_does_not_void(self):
        result = BJR.read_judgment([FEE], CASE)
        self.assertFalse(result['void'])
        self.assertIsNone(result['record'])
        self.assertEqual(result['reason'], 'satisfaction_of_other_judgment')

    def test_hoa_satisfaction_does_not_void_the_foreclosure_case(self):
        result = BJR.read_judgment([HOA_SAT], CASE)
        self.assertFalse(result['void'])
        self.assertIsNone(result['record'])
        own = BJR.read_judgment([HOA_SAT], HOA_CASE)
        self.assertFalse(own['void'])
        self.assertIsNone(own['record'])

    def test_cono_hoa_judgment_is_not_the_foreclosure_record(self):
        result = BJR.read_judgment([HOA_J], HOA_CASE)
        self.assertFalse(result['ok'])
        self.assertFalse(result['void'])
        self.assertIsNone(result['record'])
        self.assertEqual(result['instrument'], 'hoa_judgment')
        self.assertTrue(result['checked'])

    def test_blank_page_is_a_partial_read(self):
        result = BJR.read_judgment([PAGE1, '   '], CASE)
        self.assertFalse(result['ok'])
        self.assertFalse(result['void'])
        self.assertIsNone(result['record'])
        self.assertEqual(result['reason'], 'partial_read')

    def test_a_balanced_table_still_needs_this_case_and_a_foreclosure_judgment(self):
        # The same figures that verify on the real judgment. Arithmetic is not identity.
        money = """Attorney's fees $2,000.00
Court costs $500.00
GRAND TOTAL $107,500.00
DONE AND ORDERED in chambers on March 1, 2024.
"""

        def pages(heading, case_line='CASE NO. CACE-99-000123'):
            first = (
                'IN THE CIRCUIT COURT\n%s\n%s\n'
                'The following amounts are now due and owing:\n'
                'Principal balance $100,000.00\n'
                'Accrued interest $5,000.00\n'
                'Interest continues at $10.00 per day\n' % (case_line, heading))
            return [first, money]

        for heading in ('MORTGAGE', 'NOTICE OF LIS PENDENS',
                        'ORDER GRANTING MOTION TO CANCEL SALE'):
            built = pages(heading)
            self.assertIsNotNone(BJR._verified_total(built), heading)
            result = BJR.read_judgment(built, CASE)
            self.assertFalse(result['ok'], heading)
            self.assertFalse(result['void'], heading)
            self.assertIsNone(result['record'], heading)
            self.assertEqual(result['reason'], 'unverified', heading)

        sat_pages = pages('SATISFACTION OF MORTGAGE')
        self.assertIsNotNone(BJR._verified_total(sat_pages))
        sat = BJR.read_judgment(sat_pages, CASE)
        self.assertFalse(sat['ok'])
        self.assertIsNone(sat['record'])
        self.assertNotEqual(sat['reason'], 'total_not_reproduced')

        wrong = BJR.read_judgment([PAGE1, PAGE2], 'CACE-99-000999')
        self.assertIsNotNone(BJR._verified_total([PAGE1, PAGE2]))
        self.assertEqual(wrong['reason'], 'unverified')
        self.assertFalse(wrong['ok'])
        self.assertIsNone(wrong['record'])

        missing = BJR.read_judgment(
            [PAGE1.replace('CASE NO. CACE-99-000123\n', ''), PAGE2], CASE)
        self.assertEqual(missing['reason'], 'unverified')
        self.assertIsNone(missing['record'])

        ok = BJR.read_judgment([PAGE1, PAGE2], CASE)
        self.assertTrue(ok['ok'])
        self.assertEqual(ok['record']['case'], CASE)
        self.assertEqual(ok['record']['total_cents'], 10750000)

    def test_palm_beach_case_number_matches_with_or_without_hyphens(self):
        pb = '502099CA000123XXXXMB'
        compact = [PAGE1.replace('CACE-99-000123', pb), PAGE2]
        result = BJR.read_judgment(compact, pb)
        self.assertTrue(result['ok'])
        self.assertEqual(result['record']['case'], pb)
        self.assertEqual(result['record']['total_cents'], 10750000)
        hyphen = [PAGE1.replace('CASE NO. CACE-99-000123',
                                'CASE NO. 50-2099-CA-000123-XXXX-MB'), PAGE2]
        hyphenated = BJR.read_judgment(hyphen, pb)
        self.assertTrue(hyphenated['ok'], hyphenated)
        self.assertEqual(hyphenated['record']['total_cents'], 10750000)
        other = BJR.read_judgment(compact, '502099CA000999XXXXMB')
        self.assertEqual(other['reason'], 'unverified')
        self.assertIsNone(other['record'])


class Import(unittest.TestCase):
    def test_text_file_writes_the_same_record(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, CASE + '.txt')
            with open(path, 'w', encoding='utf-8') as f:
                f.write(PAGE1 + '\f' + PAGE2)
            out = os.path.join(folder, 'out.json')
            counts = PBI.import_folder(folder, out)
            self.assertEqual(counts['records'], 1)
            self.assertEqual(counts['void'], 0)
            with open(out, encoding='utf-8') as fh:
                got = json.load(fh)
            self.assertEqual(got[0]['total_cents'], 10750000)
            self.assertEqual(got[0]['verified_by'], 'judgment_money')
            self.assertFalse(got[0]['human_reviewed'])

    def test_satisfaction_and_mismatch_write_no_total(self):
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, CASE + '.txt'), 'w', encoding='utf-8') as f:
                f.write(SAT)
            with open(os.path.join(folder, 'CONO-99-000456.txt'), 'w', encoding='utf-8') as f:
                f.write('CASE NO. CACE-99-000123\n' + PAGE1)
            out = os.path.join(folder, 'out.json')
            counts = PBI.import_folder(folder, out)
            self.assertEqual(counts['void'], 1)
            self.assertEqual(counts['records'], 0)
            self.assertGreaterEqual(counts['failed'], 1)
            with open(out, encoding='utf-8') as fh:
                self.assertEqual(json.load(fh), [])

    def test_pdf_opener_uses_the_same_reader(self):
        with tempfile.TemporaryDirectory() as folder:
            open(os.path.join(folder, CASE + '.pdf'), 'wb').close()
            out = os.path.join(folder, 'out.json')

            def opener(_path):
                return [PAGE1, PAGE2]

            counts = PBI.import_folder(folder, out, opener=opener)
            self.assertEqual(counts['records'], 1)
            with open(out, encoding='utf-8') as fh:
                self.assertEqual(json.load(fh)[0]['case'], CASE)

    def test_unreadable_pdf_writes_no_record(self):
        pages, problem = PBI.pages_from_pdf('does-not-exist.pdf', opener=lambda _p: (_ for _ in ()).throw(RuntimeError('x')))
        self.assertIsNone(pages)
        self.assertEqual(problem, 'pdf_unreadable')

    def test_cli_refuses_a_url_and_prints_counts_only(self):
        rc = subprocess.run([sys.executable, 'pb_judgment_import.py', '--in', 'https://example.test/docket',
                             '--out', 'out.json'], capture_output=True, text=True)
        self.assertEqual(rc.returncode, 2)
        self.assertIn('local files only', rc.stdout)
        self.assertNotIn('example.test', rc.stdout + rc.stderr)
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, CASE + '.txt'), 'w', encoding='utf-8') as f:
                f.write(PAGE1 + '\f' + PAGE2)
            out = os.path.join(folder, 'out.json')
            rc = subprocess.run([sys.executable, 'pb_judgment_import.py', '--in', folder, '--out', out],
                                capture_output=True, text=True)
            self.assertEqual(rc.returncode, 0)
            self.assertIn('records=1', rc.stdout)
            self.assertNotIn(CASE, rc.stdout)

    def test_out_stays_out_of_the_repo_and_onedrive(self):
        here = os.path.dirname(os.path.abspath(PBI.__file__))
        self.assertEqual(PBI.resolve_out(os.path.join(here, 'judgment_records.json')), '')
        self.assertEqual(PBI.resolve_out(os.path.join(os.sep, 'Users', 'x', 'OneDrive', 'Desktop', 'r.json')), '')
        os.environ.setdefault('DEALFLOW_DIR', tempfile.mkdtemp())
        self.assertTrue(PBI.resolve_out('r.json').startswith(os.path.realpath(os.environ['DEALFLOW_DIR']))
                        or PBI.resolve_out('r.json').startswith(os.environ['DEALFLOW_DIR']))

    def test_a_run_that_reads_nothing_keeps_the_earlier_file(self):
        with tempfile.TemporaryDirectory() as folder:
            out = os.path.join(folder, 'out.json')
            with open(out, 'w', encoding='utf-8') as f:
                f.write('[{"case": "kept"}]')
            with open(os.path.join(folder, CASE + '.txt'), 'w', encoding='utf-8') as f:
                f.write('\f')
            PBI.import_folder(folder, out)
            with open(out, encoding='utf-8') as fh:
                self.assertEqual(json.load(fh), [{'case': 'kept'}])

    def _put(self, folder, name, text):
        with open(os.path.join(folder, name), 'w', encoding='utf-8') as fh:
            fh.write(text)
        return name

    def _load(self, folder, out_name='out.json'):
        with open(os.path.join(folder, out_name), encoding='utf-8') as fh:
            return json.load(fh)

    def test_a_later_satisfaction_of_judgment_marks_that_case(self):
        sat = (
            'SATISFACTION OF JUDGMENT\n'
            'CASE NO. %s\n'
            'The Final Judgment of Foreclosure entered in case %s is satisfied in full.\n'
            'Dated on June 1, 2024.\n' % (CASE, CASE))
        with tempfile.TemporaryDirectory() as folder:
            self._put(folder, CASE + '.txt', PAGE1 + '\f' + PAGE2)
            out = os.path.join(folder, 'out.json')
            PBI.import_folder(folder, out)
            os.remove(os.path.join(folder, CASE + '.txt'))
            self._put(folder, 'satisfaction-of-judgment.txt', sat)
            counts = PBI.import_folder(folder, out)
            self.assertEqual(counts['records'], 0)
            self.assertEqual(counts['satisfied'], 1)
            self.assertEqual(counts['void'], 1)
            got = self._load(folder)
            self.assertEqual(len(got), 1)
            self.assertTrue(got[0]['satisfied'])
            self.assertEqual(got[0]['satisfied_date'], '2024-06-01')
            self.assertEqual(got[0]['satisfied_source'], 'satisfaction-of-judgment.txt')
            self.assertEqual(got[0]['satisfaction_kind'], 'judgment')
            self.assertEqual(got[0]['total_cents'], 10750000)

    def test_a_mortgage_satisfaction_marks_the_lien_it_names(self):
        lien = (
            'MORTGAGE\n'
            'CASE NO. %s\n'
            'Official Records Book 10001, Page 200.\n'
            'Principal balance $100,000.00\n'
            'Accrued interest $5,000.00\n'
            'Attorney\'s fees $2,000.00\n'
            'Court costs $500.00\n'
            'GRAND TOTAL $107,500.00\n'
            'Dated on January 15, 2020.\n' % CASE)
        judgment = PAGE1 + 'Official Records Book 10001, Page 200.\n\f' + PAGE2
        sat = (
            'SATISFACTION OF MORTGAGE\n'
            'The mortgage recorded in Official Records Book 10001, Page 200 is paid in full.\n'
            'Dated on July 2, 2024.\n')
        with tempfile.TemporaryDirectory() as folder:
            self._put(folder, CASE + '.txt', judgment)
            self._put(folder, 'mortgage.txt', lien)
            self._put(folder, 'satisfaction-of-mortgage.txt', sat)
            out = os.path.join(folder, 'out.json')
            counts = PBI.import_folder(folder, out)
            self.assertEqual(counts['records'], 1)
            self.assertEqual(counts['liens'], 1)
            self.assertEqual(counts['satisfied'], 1)
            got = self._load(folder)
            by_kind = {row['record_kind']: row for row in got}
            self.assertNotIn('total_cents', by_kind['mortgage_lien'])
            self.assertTrue(by_kind['mortgage_lien']['satisfied'])
            self.assertEqual(by_kind['mortgage_lien']['satisfied_date'], '2024-07-02')
            self.assertEqual(by_kind['mortgage_lien']['satisfied_source'],
                             'satisfaction-of-mortgage.txt')
            self.assertEqual(by_kind['mortgage_lien']['satisfaction_kind'], 'mortgage')
            self.assertFalse(by_kind['foreclosure_judgment']['satisfied'])
            self.assertEqual(by_kind['foreclosure_judgment']['total_cents'], 10750000)

    def test_ambiguous_satisfaction_is_flagged_and_not_applied(self):
        def lien(book, page):
            return (
                'MORTGAGE\nCASE NO. %s\nOfficial Records Book %s, Page %s.\n'
                'Dated on January 15, 2020.\n' % (CASE, book, page))
        sat = (
            'SATISFACTION OF MORTGAGE\n'
            'CASE NO. %s\n'
            'The mortgage is paid in full.\n'
            'Dated on July 2, 2024.\n' % CASE)
        with tempfile.TemporaryDirectory() as folder:
            self._put(folder, 'mortgage-a.txt', lien(10001, 200))
            self._put(folder, 'mortgage-b.txt', lien(10002, 201))
            self._put(folder, 'satisfaction-of-mortgage.txt', sat)
            out = os.path.join(folder, 'out.json')
            counts = PBI.import_folder(folder, out)
            self.assertEqual(counts['satisfied'], 0)
            self.assertEqual(counts['review'], 1)
            got = self._load(folder)
            self.assertEqual(len(got), 2)
            for row in got:
                self.assertFalse(row['satisfied'])
                self.assertNotIn('total_cents', row)
                self.assertIn('ambiguous_satisfaction', row['review'])
                self.assertTrue(row['satisfaction_review'])

    def test_case_and_instrument_together_do_not_guess(self):
        def judgment(case, instrument):
            first = PAGE1.replace('CACE-99-000123', case)
            return first + 'Instrument No. %s\n\f' % instrument + PAGE2

        other = 'CACE-99-000124'
        sat = (
            'SATISFACTION OF JUDGMENT\n'
            'CASE NO. %s\n'
            'Instrument No. 111111111\n'
            'The Final Judgment of Foreclosure entered in case %s is satisfied in full.\n'
            'Dated on June 1, 2024.\n' % (CASE, CASE))
        broad = (
            'SATISFACTION OF JUDGMENT\n'
            'CASE NO. %s\n'
            'The Final Judgment of Foreclosure entered in case %s is satisfied in full.\n'
            'Dated on August 1, 2024.\n' % (CASE, CASE))
        with tempfile.TemporaryDirectory() as folder:
            self._put(folder, CASE + '-a.txt', judgment(CASE, '111111111'))
            self._put(folder, CASE + '-b.txt', judgment(CASE, '222222222'))
            self._put(folder, other + '.txt', judgment(other, '333333333'))
            self._put(folder, 'satisfaction-of-judgment.txt', sat)
            out = os.path.join(folder, 'out.json')
            counts = PBI.import_folder(folder, out)
            self.assertEqual(counts['satisfied'], 1)
            self.assertEqual(counts['review'], 0)
            got = {row['instrument_refs'][0]: row for row in self._load(folder)}
            self.assertTrue(got['instrument:111111111']['satisfied'])
            self.assertEqual(got['instrument:111111111']['satisfied_source'],
                             'satisfaction-of-judgment.txt')
            self.assertFalse(got['instrument:222222222']['satisfied'])
            self.assertFalse(got['instrument:333333333']['satisfied'])
            os.remove(os.path.join(folder, 'satisfaction-of-judgment.txt'))
            self._put(folder, 'satisfaction-broad.txt', broad)
            # The folder still holds both judgments, so this run replaces the file and the
            # case-only satisfaction matches two of them.
            counts = PBI.import_folder(folder, out)
            self.assertEqual(counts['satisfied'], 0)
            self.assertEqual(counts['review'], 1)
            again = self._load(folder)
            same_case = [row for row in again if row['case'] == CASE]
            self.assertEqual(len(same_case), 2)
            for row in same_case:
                self.assertFalse(row['satisfied'])
                self.assertIn('ambiguous_satisfaction', row['review'])
            self.assertFalse(next(row for row in again if row['case'] == other)['satisfied'])

    def test_partial_early_and_other_satisfactions_stay_open(self):
        early = (
            'SATISFACTION OF JUDGMENT\n'
            'CASE NO. %s\n'
            'The Final Judgment of Foreclosure entered in case %s is satisfied in full.\n'
            'Dated on January 1, 2024.\n' % (CASE, CASE))
        partial = (
            'PARTIAL SATISFACTION OF JUDGMENT\n'
            'CASE NO. %s\n'
            'The Final Judgment of Foreclosure entered in case %s is partially satisfied '
            'to the extent of $1,000.00.\n'
            'Dated on June 1, 2024.\n' % (CASE, CASE))
        fee = (
            "SATISFACTION OF ATTORNEY'S FEE JUDGMENT\n"
            'CASE NO. %s\n'
            "The attorney's fee judgment entered in case %s is satisfied in full.\n"
            'The final judgment of foreclosure remains outstanding.\n'
            'Dated on June 1, 2024.\n' % (CASE, CASE))
        with tempfile.TemporaryDirectory() as folder:
            self._put(folder, CASE + '.txt', PAGE1 + '\f' + PAGE2)
            self._put(folder, 'early.txt', early)
            self._put(folder, 'partial.txt', partial)
            self._put(folder, 'fee.txt', fee)
            out = os.path.join(folder, 'out.json')
            counts = PBI.import_folder(folder, out)
            self.assertEqual(counts['satisfied'], 0)
            self.assertGreaterEqual(counts['review'], 1)
            got = self._load(folder)
            self.assertEqual(len(got), 1)
            self.assertFalse(got[0]['satisfied'])
            self.assertNotIn('satisfied_date', got[0])
            reasons = got[0]['review']
            self.assertIn('satisfaction_not_later', reasons)
            self.assertIn('partial_satisfaction', reasons)


if __name__ == '__main__':
    unittest.main()
