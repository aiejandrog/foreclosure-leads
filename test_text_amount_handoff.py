"""A total verified from a court document's own TEXT reaches case_verdict.

python -u test_text_amount_handoff.py

Acceptance run 2026-10-03 (c3b62fd): replay_money_check verified the controlling judgment's total
from saved text in 5 of 7 rebuilt cases while case_verdict said "no total for the controlling
judgment verifies to the cent", because the timeline only saved checks made from vision files.

This starts from saved synthetic DOCUMENT TEXT, not from an already-successful check, and follows it
through verification (run_case_timeline.text_amount_checks), the timeline's save and reload, and
the verdict, with no vision sidecar anywhere. All case numbers, entries and figures are invented.
"""
import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

import _moneytest as MT
import _verdicttest as VT
import case_verdict as CV
import document_store as DS
import run_case_timeline as RCT

CASE = '2099-000001-CA-01'
ENTRY = '232820355'
TOTAL = 178172.62
NO_AMOUNT_GAP = 'no total for the controlling judgment verifies to the cent'


def row_for(tmp, entry=ENTRY, ref=None, reading=None, content=b'%PDF-1.4 synthetic judgment bytes',
            name='doc.pdf'):
    path = Path(tmp) / name
    path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    return {'source_ref': ref or 'court:%s:1' % entry,
            'manifest': {'path': str(path), 'source_sha256': digest, 'document_key': 'k' + digest[:15],
                         'pages': 3},
            'reading': reading if reading is not None else MT.text_reading()}


def through_save_and_reload(tmp, rows, controlling=ENTRY):
    """Text -> verification -> timeline -> saved JSON -> reloaded JSON."""
    timeline = VT.timeline(CASE, controlling=controlling)
    RCT.attach_text_checks(timeline, CASE, rows)
    target = Path(tmp) / 'case-timeline.json'
    DS.pipeline_write(target, timeline)
    return DS.pipeline_load(target)


class TextHandoffTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def verdict(self, timeline):
        return CV.assess(timeline)

    def test_saved_text_total_reaches_the_verdict_with_no_vision_sidecar(self):
        saved = through_save_and_reload(self.tmp, [row_for(self.tmp)])
        self.assertEqual(saved['amount_vision']['amount_checks'], [])        # nothing from vision
        hits = [c for c in saved['amount_checks'] if c['ok'] and c['amount'] == TOTAL]
        self.assertEqual(len(hits), 1)
        check = hits[0]
        # Attribution survives the save and reload.
        self.assertEqual((check['case'], check['entry_id'], check['source_ref']),
                         (CASE, ENTRY, 'court:%s:1' % ENTRY))
        self.assertEqual(check['document_hash'], hashlib.sha256(
            b'%PDF-1.4 synthetic judgment bytes').hexdigest())
        self.assertTrue(check['document_key'])
        self.assertEqual(check['pages'], [2, 3])
        self.assertEqual(check['source'], 'document_text')
        self.assertTrue(check['hash_rechecked'])
        result = self.verdict(saved)
        self.assertFalse([m for m in result['missing'] if NO_AMOUNT_GAP in m], result['missing'])
        self.assertTrue([s for s in result['supported_by'] if 'verified to the cent' in s],
                        result['supported_by'])
        self.assertTrue([s for s in result['supported_by'] if 'court:%s:1' % ENTRY in s])

    def test_a_total_that_does_not_add_up_stays_a_gap(self):
        bad = MT.PAGE2.replace('$150,000.00', '$150,000.01')
        saved = through_save_and_reload(self.tmp, [row_for(
            self.tmp, reading=MT.text_reading(p2=bad))])
        self.assertFalse([c for c in saved['amount_checks'] if c['ok']])
        self.assertTrue([c for c in saved['amount_checks'] if c['amount'] == TOTAL and not c['ok']])
        result = self.verdict(saved)
        self.assertTrue([m for m in result['missing'] if NO_AMOUNT_GAP in m], result['missing'])
        self.assertNotEqual(result['verdict'], 'supported')

    def test_a_check_from_another_entry_never_counts_for_the_controlling_judgment(self):
        saved = through_save_and_reload(self.tmp, [row_for(self.tmp, entry='111111111')])
        self.assertTrue([c for c in saved['amount_checks'] if c['ok'] and c['entry_id'] == '111111111'])
        result = self.verdict(saved)
        self.assertTrue([m for m in result['missing'] if NO_AMOUNT_GAP in m], result['missing'])

    def test_a_recorded_instrument_is_never_checked_as_a_judgment_total(self):
        # An Official Records copy printing a verifying table carries no entry; it must not be saved
        # as a check at all (verify-12 defect D5), let alone count for the controlling judgment.
        saved = through_save_and_reload(self.tmp, [row_for(
            self.tmp, ref='official_records/34000-9/1')])
        self.assertEqual(saved['amount_checks'], [])
        result = self.verdict(saved)
        self.assertTrue([m for m in result['missing'] if NO_AMOUNT_GAP in m], result['missing'])

    def test_a_document_whose_bytes_changed_after_it_was_hashed_does_not_verify(self):
        row = row_for(self.tmp)
        Path(row['manifest']['path']).write_bytes(b'%PDF-1.4 a different file entirely')
        saved = through_save_and_reload(self.tmp, [row])
        mine = [c for c in saved['amount_checks'] if c['amount'] == TOTAL]
        self.assertTrue(mine and not any(c['ok'] for c in mine))
        self.assertIn('no longer hashes', mine[0]['reason'])
        result = self.verdict(saved)
        self.assertTrue([m for m in result['missing'] if NO_AMOUNT_GAP in m], result['missing'])

    def test_a_missing_file_is_not_called_rechecked(self):
        row = row_for(self.tmp)
        os.remove(row['manifest']['path'])
        saved = through_save_and_reload(self.tmp, [row])
        verified = [c for c in saved['amount_checks'] if c['ok']]
        self.assertEqual(len(verified), 1)
        self.assertFalse(verified[0]['hash_rechecked'])

    def test_a_document_with_no_recorded_hash_does_not_verify(self):
        row = row_for(self.tmp)
        row['manifest'].pop('source_sha256')
        saved = through_save_and_reload(self.tmp, [row])
        self.assertFalse([c for c in saved['amount_checks'] if c['ok']])

    def test_an_edited_saved_check_is_rejected_by_the_verdict(self):
        saved = through_save_and_reload(self.tmp, [row_for(self.tmp)])
        base = self.verdict(saved)
        self.assertFalse([m for m in base['missing'] if NO_AMOUNT_GAP in m])

        wrong_entry = copy.deepcopy(saved)
        for c in wrong_entry['amount_checks']:
            c['source_ref'] = 'court:999999999:1'          # filed under a different entry
        r = self.verdict(wrong_entry)
        self.assertTrue([m for m in r['missing'] if 'filed under a different entry' in m], r['missing'])
        self.assertTrue([m for m in r['missing'] if NO_AMOUNT_GAP in m])

        no_hash = copy.deepcopy(saved)
        for c in no_hash['amount_checks']:
            c['document_hash'] = None
        r = self.verdict(no_hash)
        self.assertTrue([m for m in r['missing'] if 'records no document hash' in m], r['missing'])
        self.assertTrue([m for m in r['missing'] if NO_AMOUNT_GAP in m])

        wrong_source = copy.deepcopy(saved)
        for c in wrong_source['amount_checks']:
            c['source_ref'] = 'official_records/34000-9/1'
        r = self.verdict(wrong_source)
        self.assertTrue([m for m in r['missing'] if 'not the court copy' in m], r['missing'])
        self.assertTrue([m for m in r['missing'] if NO_AMOUNT_GAP in m])

    def test_an_unreadable_scan_total_that_does_not_add_up_is_not_verified(self):
        # OCR text, one row misread: the same arithmetic refuses it, as on the vision path.
        bad = MT.PAGE2.replace('$150,000.00', '$150,000.10')
        reading = MT.text_reading(p2=bad, source='ocr')
        saved = through_save_and_reload(self.tmp, [row_for(self.tmp, reading=reading)])
        self.assertFalse([c for c in saved['amount_checks'] if c['ok']])

    def test_the_vision_path_is_untouched(self):
        # A vision check still arrives by amount_vision and is read alongside the text checks.
        saved = through_save_and_reload(self.tmp, [row_for(self.tmp)])
        saved['amount_vision']['amount_checks'] = [VT.ok_check(ENTRY, 'court:%s:1' % ENTRY, TOTAL)]
        result = self.verdict(saved)
        self.assertFalse([m for m in result['missing'] if NO_AMOUNT_GAP in m])
        # Same figure from both paths is one amount, not "two different totals".
        self.assertFalse([c for c in result['conflicts'] if 'two different totals' in c])


if __name__ == '__main__':
    unittest.main(verbosity=2)
