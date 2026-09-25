import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import miami_timeline_ocr as O


class SupplementalTests(unittest.TestCase):
    def test_embedded_only_cached_and_primary_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            pdf = Path(td) / 'source.pdf'; pdf.write_bytes(b'synthetic-pdf')
            row = {'manifest': {'path': str(pdf)}, 'reading': {'pages': [{'page': 1, 'outcome': 'text', 'text_source': 'embedded', 'text': 'Primary'}, {'page': 2, 'outcome': 'ocr_text', 'text': 'Existing'}]}}
            with patch.object(O.DS, '_fitz') as fitz, patch.object(O.DS, '_render_and_ocr', return_value=({1: 'OCR'}, {}, {})) as render:
                a = O.supplement(row, Path(td)/'cache')
                b = O.supplement(row, Path(td)/'cache')
                self.assertEqual(render.call_count, 1)
                self.assertEqual(render.call_args.args[1], [0])
                self.assertEqual(render.call_args.kwargs['dpi'], 300)
                self.assertEqual(render.call_args.kwargs['gray_cutoff'], 0)
            self.assertEqual(a['reading']['pages'][0]['text'], 'Primary')
            self.assertEqual(b['reading']['pages'][0]['supplemental_ocr']['text'], 'OCR')
            self.assertNotIn('supplemental_ocr', row['reading']['pages'][0])

    def test_failure_retained_with_gap_and_hash_change_retries(self):
        with tempfile.TemporaryDirectory() as td:
            pdf = Path(td)/'source.pdf'; pdf.write_bytes(b'one')
            row = {'manifest': {'path': str(pdf)}, 'reading': {'pages': [{'page': 1, 'outcome': 'text', 'text_source': 'embedded', 'text': 'Primary'}]}}
            with patch.object(O.DS, '_fitz'), patch.object(O.DS, '_render_and_ocr', return_value=({}, {1: 'OCR bridge unavailable'}, {})) as render:
                a = O.supplement(row, Path(td)/'cache')
                pdf.write_bytes(b'two')
                O.supplement(row, Path(td)/'cache')
                self.assertEqual(render.call_count, 2)
            self.assertEqual(a['reading']['pages'][0]['supplemental_ocr']['outcome'], 'ocr_failed')
            self.assertIn('OCR bridge unavailable', a['supplemental_ocr_gaps'][0]['reason'])


if __name__ == '__main__': unittest.main()
