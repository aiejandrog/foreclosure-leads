import unittest
from pathlib import Path
from unittest.mock import patch

import fitz
from document_collectors import BrowardCollector, AccessGap
from fl_lp import broward_pin as pin


class BrowardDocumentTests(unittest.TestCase):
    def test_full_pdf_not_parcel_reader_sample(self):
        with fitz.open() as pdf:
            for _ in range(20):
                pdf.new_page()
            content = pdf.tobytes()
        def download(html, instrument, directory):
            target = Path(directory) / (instrument + '.pdf')
            target.write_bytes(content)
            return str(target)
        with patch.object(pin, '_details_html', return_value=('county html', {'i': '123456789', 't': 'MORTGAGE'})), patch.object(pin, '_pdf_of', side_effect=download):
            result = BrowardCollector().retrieve_document({'instrument': '123456789'})
        with fitz.open(stream=result['content'], filetype='pdf') as pdf:
            self.assertEqual(len(pdf), 20)
        self.assertFalse(result['page_count_verified'])
        self.assertEqual(result['record_key']['instrument'], '123456789')

    def test_rejects_instrument_path_injection(self):
        with self.assertRaises(AccessGap):
            BrowardCollector().retrieve_document({'instrument': '../secret'})

    def test_rejects_wrong_instrument_response(self):
        with patch.object(pin, '_details_html', return_value=('html', {'i': '987654321', 't': 'MORTGAGE'})):
            with self.assertRaises(AccessGap):
                BrowardCollector().retrieve_document({'instrument': '123456789'})


if __name__ == '__main__':
    unittest.main()
