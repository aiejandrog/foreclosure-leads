import tempfile
import hashlib
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import fitz
import document_store as store
import document_queue as queue
import document_collectors as collectors


class SharedCoverageTests(unittest.TestCase):
    def test_expired_worker_cannot_publish_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            with queue.DocumentQueue(str(Path(tmp)/'q.db')) as q:
                q.add('MIAMI-DADE', 'fixture', 'ref', 'read')
                job = q.claim('old')
                q.db.execute('UPDATE jobs SET lease_until=?', (time.time()-1,))
                with self.assertRaises(RuntimeError):
                    with q.fenced_write(job['id'], 'old'):
                        self.fail('Expired worker entered publication section')
    def test_done_acquisition_without_saved_evidence_is_not_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store.pipeline_write(root/'inventory.json', {'entries': [], 'pagination_verified': True,
                                                        'official_records_supplied': True})
            store.pipeline_write(root/('a'*64+'.json'), {'source_ref': 'present',
                'manifest': {'pages': 1}, 'reading': {'pages': []}})
            with queue.DocumentQueue(str(root/'queue.sqlite3')) as q:
                q.add('MIAMI-DADE', 'fixture', 'missing', 'acquire')
                j = q.claim('worker')
                q.complete(j['id'], 'worker')
            with patch.object(store, 'pipeline_folder', return_value=root):
                report = store.pipeline_report('MIAMI-DADE', 'fixture')
            self.assertFalse(report['collection_complete'])

    def test_missing_duplicate_and_out_of_range_pages_fail(self):
        for numbers in ([1], [1, 1], [1, 3], []):
            result = {'manifest': {'pages': 2}, 'reading': {'pages': [
                {'page': n, 'outcome': 'text'} for n in numbers], 'read_status': 'read'}}
            self.assertFalse(store.page_inventory_complete(result))
            self.assertTrue(store.needs_reextraction(result))

    def test_complete_flags_cannot_hide_missing_page(self):
        result = {'manifest': {'pages': 2}, 'reading': {'pages': [
            {'page': 1, 'outcome': 'text'}]}, 'interpretation': {
            'status': 'complete', 'pages_assessed': 2, 'unresolved': []}}
        self.assertFalse(store.interpretation_complete(result))

    def test_saved_read_back_cannot_hide_missing_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            text = root/('a'*16+'-text')
            text.mkdir()
            (text/'p01.txt').write_text('Evidence', encoding='utf-8')
            store.pipeline_write(text/'pages.json', {'read_status': 'read', 'pages': [
                {'page': 1, 'file': 'p01.txt', 'outcome': 'text', 'text_source': 'embedded'}]})
            reading = store._read_back(root, {'document_key': 'a'*64, 'pages': 2})
            self.assertFalse(reading['complete'])
            self.assertIn(2, reading['pages_unresolved'])

    def test_resume_reextracts_without_download_and_then_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / 'sample.pdf'
            with fitz.open() as doc:
                for i in range(16):
                    doc.new_page().insert_text((72,72), 'Distinct evidence number %s' % chr(65+i))
                doc.save(pdf)
            manifest = {'county': 'MIAMI-DADE', 'case': 'fixture', 'path': str(pdf), 'meta_path': str(root/'meta.json'), 'pages': 16,
                        'document_name': 'Fixture', 'document_key': 'a'*64}
            store.pipeline_write(root/'meta.json', manifest)
            result = {'manifest': manifest, 'reading': {'reader_version': store.READER_VERSION,
                'read_status': 'read', 'pages': [{'page': 1, 'outcome': 'text'}]}}
            with patch.object(store, 'winocr', side_effect=AssertionError('OCR not needed')), \
                 patch.object(store, 'case_dir', return_value=root):
                store.reextract_result(result, root/'images')
            self.assertTrue(store.page_inventory_complete(result))
            self.assertEqual(len(result['reading']['pages']), 16)
            self.assertFalse(store.needs_reextraction(result))
            shared = store._read_back(root, manifest)
            self.assertEqual(shared['page_count'], 16)
            self.assertTrue(shared['complete'])

    def test_queue_resume_reuses_pdf_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ref, case = 'court:fixture', 'fixture'
            key = hashlib.sha256(ref.encode()).hexdigest()
            pdf = root/'fixture.pdf'
            with fitz.open() as doc:
                doc.new_page().insert_text((72,72), 'First unique filing text')
                doc.new_page().insert_text((72,72), 'Second distinct document page')
                doc.save(pdf)
            manifest = {'county': 'MIAMI-DADE', 'case': case, 'path': str(pdf), 'meta_path': str(root/'meta.json'), 'pages': 2,
                        'document_name': 'Fixture', 'document_key': 'a'*64}
            store.pipeline_write(root/'meta.json', manifest)
            store.pipeline_write(root/'inventory.json', {'entries': []})
            store.pipeline_write(root/(key+'.json'), {'manifest': manifest, 'source_ref': ref,
                'reading': {'reader_version': store.READER_VERSION, 'read_status': 'read',
                            'pages': [{'page': 1, 'outcome': 'text'}]}})
            with queue.DocumentQueue(str(root/'queue.sqlite3')) as q:
                for kind in ('acquire', 'read'):
                    q.add('MIAMI-DADE', case, ref, kind)
                    job = q.claim_ref('seed', 'MIAMI-DADE', case, ref, kind)
                    q.complete(job['id'], 'seed', reader_version=store.READER_VERSION, read_status='read')
            from unittest.mock import Mock
            client = Mock()
            client.retrieve_document.side_effect = AssertionError('Must not redownload')
            with patch.object(store, 'pipeline_folder', return_value=root), \
                 patch.object(store, 'case_dir', return_value=root), \
                 patch.object(collectors, 'collector_for', return_value=client):
                first = queue.resume_case_documents('MIAMI-DADE', case)
                with patch.object(store, 'read_pages', side_effect=AssertionError('Must not reread')):
                    second = queue.resume_case_documents('MIAMI-DADE', case)
            self.assertEqual(first['pages_extracted'], 2)
            self.assertEqual(second['pages_extracted'], 2)
            with queue.DocumentQueue(str(root/'queue.sqlite3')) as q:
                self.assertEqual([j['attempts'] for j in q.jobs('MIAMI-DADE', case)], [1, 2])


if __name__ == '__main__':
    unittest.main()
