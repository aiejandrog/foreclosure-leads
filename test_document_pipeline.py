import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

import document_pipeline as p
from document_queue import DocumentQueue


class PipelineTests(unittest.TestCase):
    def test_missing_or_duplicate_pages_cannot_complete_interpretation(self):
        for numbers in ([1], [1, 1], [1, 3], []):
            with self.subTest(numbers=numbers), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                pdf = base / 'sample.pdf'
                pdf.write_bytes(b'fixture')
                result = {'manifest': {'path': str(pdf), 'pages': 2},
                          'reading': {'pages': [{'page': n, 'text': 'Evidence'} for n in numbers]}}
                answer = {'status': 'complete', 'findings': [], 'unresolved': [], 'resumable': False}
                with patch.object(p.agents, 'run_agent', return_value=answer):
                    outcome = p.interpret_document(base, 'key', result)
                self.assertEqual(outcome['status'], 'incomplete')
                self.assertTrue(outcome['unresolved'])

    def test_expired_owner_cannot_finish(self):
        with tempfile.TemporaryDirectory() as tmp:
            with DocumentQueue(str(Path(tmp) / 'q.db')) as q:
                q.add('MIAMI-DADE', 'case', 'ref', 'read')
                job = q.claim('worker', lease=1)
                q.db.execute('UPDATE jobs SET lease_until=?', (time.time()-1,))
                self.assertFalse(q.complete(job['id'], 'worker'))
                self.assertFalse(q.renew(job['id'], 'worker'))
                self.assertFalse(q.fail(job['id'], 'worker', 'late failure'))

    def test_reader_uncertainty_survives_cached_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            pdf = base / 'sample.pdf'
            pdf.write_bytes(b'fixture')
            result = {'manifest': {'path': str(pdf), 'pages': 1}, 'reading': {'pages': [{'page': 1, 'text': 'Evidence'}]}}
            reader = {'status': 'complete', 'findings': [], 'unresolved': ['Missing exhibit'], 'resumable': False}
            verifier = {'status': 'complete', 'findings': [], 'unresolved': [], 'resumable': False}
            with patch.object(p.agents, 'run_agent', side_effect=[reader, verifier]) as agent:
                self.assertEqual(p.interpret_document(base, 'key', result)['status'], 'incomplete')
                self.assertEqual(p.interpret_document(base, 'key', result)['status'], 'incomplete')
                self.assertEqual(agent.call_count, 2)

    def test_collection_accounts_for_missing_attachment_and_empty_entry(self):
        client = Mock()
        client.enumerate_documents.return_value = {'raw': {}, 'pagination_verified': False, 'entries': [
            {'source_id': '1', 'expected_documents': 1, 'metadata': {'encID': 'x'}},
            {'source_id': '2', 'expected_documents': 0, 'metadata': {}}]}
        client.attachments.side_effect = p.collectors.AccessGap('Missing exhibit')
        with tempfile.TemporaryDirectory() as tmp, patch.object(p, 'folder', return_value=Path(tmp)), patch.object(p.collectors, 'collector_for', return_value=client):
            result = p.collect('MIAMI-DADE', 'case')
            inventory = p.load(Path(tmp) / 'inventory.json')
            self.assertEqual(inventory['entries'][1]['inventory_status'], 'county_reports_no_document')
            self.assertIn('Missing exhibit', result['gaps'])
            self.assertFalse(result['collection_complete'])

    def test_report_never_equates_extraction_with_interpretation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(p, 'folder', return_value=Path(tmp)):
            p.write(Path(tmp) / 'inventory.json', {'entries': [], 'pagination_verified': False})
            result = p.report('MIAMI-DADE', 'case')
            self.assertFalse(result['interpretation_complete'])
            self.assertEqual(result['verified_findings'], [])


if __name__ == '__main__':
    unittest.main()
