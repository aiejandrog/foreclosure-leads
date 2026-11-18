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


def test_download_pdf_rides_through_random_403s(tmp_path, monkeypatch):
    """Cloudflare 403s this download at random (~1 in 6 succeed, measured 2026-11-18): the downloader
    must keep asking well past three tries, and re-arm the image before each one."""
    from fl_lp import broward_pin as PIN
    calls = {'dl': 0, 'arm': 0}
    monkeypatch.setattr(PIN.BL, '_curl', lambda url, *a, **k: calls.__setitem__('arm', calls['arm'] + 1) or '')

    def fake_run(cmd, **kw):
        calls['dl'] += 1
        dest = cmd[cmd.index('-o') + 1]
        with open(dest, 'wb') as f:
            f.write(b'%PDF-1.7' if calls['dl'] == 7 else b'<html>403</html>')
    monkeypatch.setattr(PIN.subprocess, 'run', fake_run)
    dest = str(tmp_path / 'x.pdf')
    assert PIN._download_pdf('TOK', dest, pause=0) is True
    assert calls['dl'] == 7 and calls['arm'] == 7
    calls['dl'] = calls['arm'] = 0
    monkeypatch.setattr(PIN.subprocess, 'run', lambda cmd, **kw: open(cmd[cmd.index('-o') + 1], 'wb').write(b'no'))
    assert PIN._download_pdf('TOK', dest, pause=0) is False
