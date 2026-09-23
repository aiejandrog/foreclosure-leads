import tempfile
import unittest
from pathlib import Path

import fitz
import document_store as store


class ShortOCRTests(unittest.TestCase):
    def test_short_ocr_is_preserved_without_promoting_page(self):
        with fitz.open() as pdf:
            pdf.new_page()
            content = pdf.tobytes()
        with tempfile.TemporaryDirectory() as directory:
            reading = store.read_pages(content, ocr=lambda paths: {p: 'EXHIBIT A' for p in paths},
                                       keep_images_in=directory, gray_cutoff=0)
            page = reading['pages'][0]
            self.assertEqual(page.get('provisional_ocr_text'), 'EXHIBIT A')
            self.assertEqual(page['outcome'], 'needs_ocr')
            self.assertEqual(page['text'], '')
            self.assertEqual(reading['pages_unresolved'], [1])
            self.assertTrue(Path(page['image']).is_file())


if __name__ == '__main__':
    unittest.main()
