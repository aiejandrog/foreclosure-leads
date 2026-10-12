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


if __name__ == '__main__':
    unittest.main()
