import tempfile
import unittest
from pathlib import Path

import fitz
import document_store as store


class ShortOCRTests(unittest.TestCase):
    def test_short_exhibit_is_read_as_label(self):
        with fitz.open() as pdf:
            pdf.new_page()
            content = pdf.tobytes()
        with tempfile.TemporaryDirectory() as directory:
            reading = store.read_pages(content, ocr=lambda paths: {p: 'EXHIBIT A' for p in paths},
                                       keep_images_in=directory, gray_cutoff=0)
            page = reading['pages'][0]
            self.assertEqual(page['outcome'], 'read_as_label')
            self.assertEqual(page['text'], 'EXHIBIT A')
            self.assertEqual(reading['pages_unresolved'], [])
            self.assertTrue(Path(page['image']).is_file())

    def test_short_embedded_text_has_no_character_floor(self):
        with fitz.open() as pdf:
            pdf.new_page().insert_text((72, 72), 'Motion denied.')
            reading = store.read_pages(pdf.tobytes())
        self.assertEqual(reading['pages'][0]['outcome'], 'text')

    def test_watermark_alone_never_counts_as_content(self):
        with fitz.open() as pdf:
            pdf.new_page().insert_text((72, 72), 'NOT AN OFFICIAL COPY - PUBLIC ACCESS')
            reading = store.read_pages(pdf.tobytes(), ocr=lambda paths: {
                p: 'NOT AN OFFICIAL COPY - PUBLIC ACCESS' for p in paths})
        self.assertEqual(reading['pages_unresolved'], [1])

    def test_redacted_page_is_assessed_not_read(self):
        reading = {'pages': [{'page': 1, 'outcome': 'needs_ocr', 'text': ''}]}
        store.apply_page_assessment(reading, 1, {'outcome': 'redacted_or_blank',
            'text': 'Visible heading', 'reason': 'Body obscured', 'confident': True})
        self.assertEqual(reading['pages_assessed'], 1)
        self.assertEqual(reading['read_status'], 'partial')
        self.assertFalse(reading['complete'])
        self.assertEqual(reading['pages_content_gaps'], [1])

    def test_repeated_ocr_boilerplate_is_unresolved(self):
        with fitz.open() as pdf:
            pdf.new_page()
            pdf.new_page()
            reading = store.read_pages(pdf.tobytes(), ocr=lambda paths: {
                p: 'COUNTY CLERK ELECTRONIC RECORDING STAMP' for p in paths})
        self.assertEqual(reading['pages_unresolved'], [1, 2])


if __name__ == '__main__':
    unittest.main()
