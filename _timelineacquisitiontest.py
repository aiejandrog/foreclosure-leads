"""Synthetic timeline orchestration regressions; no network or paid calls."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import run_case_timeline as R


class AcquisitionTests(unittest.TestCase):
    def test_full_source_comparison_retains_old_entries_and_long_comments(self):
        case = '2026-000001-CA-01'
        raw = [{'eventID': i, 'comments': 'X' * 220} for i in range(45)]
        inventory = {'raw': {'caseNumber': case, 'dockets': raw},
                     'entries': [{'metadata': e} for e in raw]}
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / 'dockets.json'
            cache.write_text(json.dumps({case: {'n':45, 'ents':[{}]*40}}))
            report = R.source_comparison(case, inventory, cache)
            self.assertEqual(report['full_ocs_entries'], 45)
            self.assertEqual(report['cache_entries'], 40)
            self.assertEqual(report['cache_reported_total'], 45)
            self.assertTrue(report['counts_differ'])
            inventory['entries'][0]['metadata'] = dict(raw[0], comments='X'*160)
            with self.assertRaises(ValueError):
                R.source_comparison(case, inventory, cache)

    def test_missing_raw_or_truncated_inventory_cannot_be_timeline_source(self):
        with self.assertRaises(ValueError):
            R.source_comparison('2026-000001-CA-01', {'entries':[]}, Path('missing'))
        with self.assertRaises(ValueError):
            R.source_comparison('2026-000001-CA-01', {'raw':{'caseNumber':'2026-000001-CA-01',
                'dockets':[{'eventID':1}]}, 'entries':[]}, Path('missing'))

    def test_free_cached_amounts_never_call_reader_and_reject_wrong_hash(self):
        import hashlib
        import miami_timeline_amounts as A
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            row = {'source_ref': 'court:1:2', 'entry_ref': '1',
                   'manifest': {'source_sha256': 'current'}, 'reading': {'pages': []}}
            path = base / ('amount-vision-' + hashlib.sha256(b'court:1:2').hexdigest() + '.json')
            detail = {'source_ref': 'court:1:2', 'document_hash': 'current',
                      'figures': [{'page': 1, 'amount': 10}], 'gaps': [{'page': 2, 'reason': 'cap'}]}
            path.write_text(json.dumps(detail))
            with patch.object(A, 'assess_amount_pages', side_effect=AssertionError('paid call forbidden')):
                report = R.read_amounts([row], base)
                self.assertEqual(len(report['figures']), 1)
                self.assertEqual(report['gaps'][0]['reason'], 'cap')
                row['manifest']['source_sha256'] = 'changed'
                self.assertEqual(R.read_amounts([row], base)['figures'], [])

    def test_paid_failure_leaves_free_timeline_on_disk(self):
        import case_review
        import run_documents
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            ledger = base / 'budget.json'
            ledger.write_text(json.dumps({'version': 1, 'cases': {}, 'actual_usd': .08, 'reserved': {}}))
            dossier = base / 'case.json'
            inventory = {'pagination_verified': True, 'entries': []}
            def fail_paid(rows, base, budget=None, plan=None):
                if budget is not None:
                    raise RuntimeError('reader stopped')
                return {'figures': [], 'gaps': [], 'evidence_files': []}
            with patch.object(case_review, 'output_path', return_value=ledger), \
                 patch.object(run_documents, 'dossier_path', return_value=dossier), \
                 patch.object(R.DS, 'pipeline_folder', return_value=base), \
                 patch.object(R, 'acquire', return_value=(inventory, [])), \
                 patch.object(R, 'read_amounts', side_effect=fail_paid):
                with self.assertRaises(RuntimeError):
                    R.main(['--case', '2026-000001-CA-01', '--vision', '--vision-max-spend', '1'])
            self.assertTrue((base / 'case-timeline.json').exists())
            self.assertTrue((base / 'case-timeline.md').exists())
            self.assertIn('whole_case_timeline', json.loads(dossier.read_text()))

    def test_counts_separate_embedded_supplement_and_unreadable_source(self):
        rows = [{'manifest': {'pages': 3}, 'reading': {'pages': [
            {'page': 1, 'outcome': 'text', 'supplemental_ocr': {'outcome': 'ocr_text'}},
            {'page': 2, 'outcome': 'ocr_text'},
            {'page': 3, 'outcome': 'unreadable_source'}]}}]
        timeline = {'entries': [{}, {}], 'gaps': [{'reason': 'source'}],
                    'pending': [{'type': 'motion'}, {'type': 'motion'}, {'type': 'hearing'}]}
        summary = R.summary_counts(rows, timeline)
        self.assertEqual(summary['pages'], 3)
        self.assertEqual(summary['ocr_text_pages'], 2)
        self.assertEqual(summary['unreadable_source_pages'], 1)
        self.assertEqual(summary['pending_types'], {'motion': 2, 'hearing': 1})

    def test_amount_evidence_resumes_shared_budget_and_never_marks_verified(self):
        from document_backfill import State, PersistentBudget
        import miami_timeline_amounts as A
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            ledger = base / 'vision-budget.json'
            ledger.write_text(json.dumps({'version': 1, 'cases': {}, 'actual_usd': .08, 'reserved': {}}))
            row = {'source_ref': 'court:1:2', 'entry_ref': '1',
                   'manifest': {'document_key': 'a' * 64, 'source_sha256': 'b' * 64},
                   'reading': {'pages': [{'page': 1, 'text': 'Judgment $100.00'}]}}
            detail = {'pages': {}, 'figures': [{'page': 1, 'amount': 100, 'kind': 'total'}],
                      'gaps': [{'page': 1, 'reason': 'unverified'}], 'usd': 0}
            with State(ledger) as state, patch.object(A, 'assess_amount_pages', return_value=detail):
                # Paid reads follow a docket plan; an entry the plan holds eligible is readable.
                plan = {'documents': [{'entry_id': '1', 'eligible_for_acquisition': True, 'gaps': []}]}
                report = R.read_amounts([row], base, PersistentBudget(1, state), plan=plan)
            self.assertEqual(report['figures'][0]['verification_status'], 'unverified')
            self.assertEqual(report['figures'][0]['source_ref'], 'court:1:2')
            self.assertEqual(report['figures'][0]['document_hash'], 'b' * 64)
            self.assertEqual(report['figures'][0]['entry_id'], '1')
            self.assertEqual(json.loads(ledger.read_text())['actual_usd'], .08)
            self.assertEqual(len(list(base.glob('amount-vision-*.json'))), 1)

    def test_shared_spend_counts_reservations_without_reset(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'budget.json'
            original = {'actual_usd': .08, 'reserved': {'uncertain': .12}}
            path.write_text(json.dumps(original))
            report = R.budget_snapshot(path, 1.0)
            self.assertAlmostEqual(report['remaining_usd'], .8)
            self.assertEqual(json.loads(path.read_text()), original)

    def test_missing_budget_is_unknown_not_fresh_zero(self):
        with tempfile.TemporaryDirectory() as folder:
            report = R.budget_snapshot(Path(folder) / 'absent.json', 1)
            self.assertIsNone(report['remaining_usd'])
            self.assertEqual(report['status'], 'unknown')

    def test_nonfinite_caps_and_wrong_county_case_rejected(self):
        for cap in (0, -1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                R.budget_snapshot(Path('not-needed.json'), cap)
        for case in ('../../escape', '50-2026-CA-000123', '2026-123456-CA-99'):
            with self.assertRaises(ValueError):
                R.validate_case(case)

    def test_read_all_jobs_not_default_ten_and_keep_no_image_entries(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            inventory = {'raw': {'caseNumber':'2026-000001-CA-01', 'dockets':[{}]},
                         'entries': [{'source_id': '1', 'expected_documents': 0, 'metadata':{}}]}
            (base / 'inventory.json').write_text(json.dumps(inventory))
            from document_queue import DocumentQueue
            with DocumentQueue(str(base / 'queue.sqlite3')) as queue:
                for n in range(15):
                    queue.add('MIAMI-DADE', '2026-000001-CA-01', 'court:2:' + str(n), 'acquire')
            def resume(county, case, limit, interpret):
                self.assertEqual(limit, 15)
                self.assertFalse(interpret)
            with patch.object(R.DQ, 'resume_case_documents', side_effect=resume):
                loaded, rows = R.acquire('2026-000001-CA-01', base)
            self.assertEqual(loaded['entries'], inventory['entries'])
            self.assertEqual(loaded['source_comparison']['full_ocs_entries'], 1)
            self.assertEqual(len(rows), 15)
            self.assertEqual(rows[0]['acquisition_gap'], 'Document acquisition pending')
            self.assertEqual(rows[0]['entry_ref'], '2')

    def test_stored_source_gaps_preserved_and_dossier_section_d_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            row = {'source_ref': 'court:1:2', 'manifest': {'pages': 1},
                   'reading': {'pages': [{'page': 1, 'outcome': 'unreadable_source', 'reason': 'redacted'}]}}
            (base / ('a' * 64 + '.json')).write_text(json.dumps(row))
            self.assertEqual(R.load_rows(base)[0]['reading']['pages'][0]['outcome'], 'unreadable_source')
            dossier = base / 'case.json'
            original = {'d_equity': {'untouched': True}, 'other': 4}
            dossier.write_text(json.dumps(original))
            timeline = {'case': '2026-000001-CA-01', 'status': {'status': 'unclear'}, 'entries': [], 'gaps': []}
            paths = R.write_timeline(dossier, timeline, '# Timeline\n')
            saved = json.loads(dossier.read_text())
            self.assertEqual(saved['d_equity'], original['d_equity'])
            self.assertEqual(saved['other'], 4)
            self.assertTrue(Path(paths['json']).exists())
            self.assertEqual(Path(paths['markdown']).read_text(), '# Timeline\n')

    def test_saved_court_document_is_joined_to_docket_entry(self):
        import miami_case_timeline as T
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            row = {'source_ref': 'court:123:456', 'manifest': {'pages': 1},
                   'reading': {'pages': [{'page': 1, 'outcome': 'ocr_text',
                                         'text': 'FINAL JUDGMENT OF FORECLOSURE'}]}}
            (base / ('a' * 64 + '.json')).write_text(json.dumps(row))
            inventory = {'pagination_verified': True, 'entries': [
                {'source_id': '123', 'source_ref': 'dockets/0', 'expected_documents': 1,
                 'metadata': {'eventID': 123, 'description': 'Motion', 'date': '2026-01-01'}}]}
            timeline = T.build_timeline('2026-000001-CA-01', inventory, R.load_rows(base), '2026-02-01')
            self.assertEqual(timeline['entries'][0]['kind'], 'final_judgment')


if __name__ == '__main__':
    unittest.main()
