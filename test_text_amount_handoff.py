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
    # The controlling entry's one document is recorded as read, as document_coverage writes it.
    timeline = VT.timeline(CASE, controlling=controlling,
                           attachments=[VT.read_attachment(controlling)])
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


JUDGMENT_PAGE1 = (MT.PAGE1 + '\nFINAL JUDGMENT OF FORECLOSURE\n'
                  "THIS ACTION was heard on the plaintiff's motion.\n"
                  'It is ORDERED AND ADJUDGED that the clerk shall sell the property.')
AFFIDAVIT_PAGE1 = (MT.PAGE1 + '\nAFFIDAVIT OF AMOUNTS DUE AND OWING\n'
                   "THIS ACTION was heard on the plaintiff's motion.")


class SiblingDocumentTests(unittest.TestCase):
    """A self-consistent table in a sibling document is not the judgment's amount."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def saved(self, rows, refs):
        timeline = VT.timeline(CASE, controlling=ENTRY, attachments=[
            VT.read_attachment(ENTRY, document=r) for r in refs])
        RCT.attach_text_checks(timeline, CASE, rows)
        target = Path(self.tmp) / 'case-timeline.json'
        DS.pipeline_write(target, timeline)
        return DS.pipeline_load(target)

    def rows(self, judgment_reading, sibling_reading):
        return [row_for(self.tmp, ref='court:%s:1' % ENTRY, reading=judgment_reading,
                        content=b'judgment bytes', name='j.pdf'),
                row_for(self.tmp, ref='court:%s:2' % ENTRY, reading=sibling_reading,
                        content=b'sibling bytes', name='s.pdf')]

    def test_a_check_records_what_the_document_reads_as(self):
        saved = self.saved(self.rows(MT.text_reading(p1=JUDGMENT_PAGE1),
                                     MT.text_reading(p1=AFFIDAVIT_PAGE1)),
                           ['court:%s:1' % ENTRY, 'court:%s:2' % ENTRY])
        kinds = {c['source_ref']: c['document_kind'] for c in saved['amount_checks'] if c['ok']}
        self.assertEqual(kinds, {'court:%s:1' % ENTRY: 'final_judgment',
                                 'court:%s:2' % ENTRY: 'unknown'})

    def test_a_total_that_verifies_only_in_a_sibling_is_a_gap(self):
        bad = MT.PAGE2.replace('$150,000.00', '$150,000.01')           # the judgment does not add up
        saved = self.saved(self.rows(MT.text_reading(p1=JUDGMENT_PAGE1, p2=bad),
                                     MT.text_reading(p1=AFFIDAVIT_PAGE1)),
                           ['court:%s:1' % ENTRY, 'court:%s:2' % ENTRY])
        self.assertTrue([c for c in saved['amount_checks']
                         if c['ok'] and c['source_ref'].endswith(':2')])     # the sibling verifies
        result = CV.assess(saved)
        self.assertTrue([m for m in result['missing'] if NO_AMOUNT_GAP in m], result['missing'])
        self.assertTrue([m for m in result['missing'] if 'not identified as the final judgment' in m],
                        result['missing'])
        self.assertNotEqual(result['verdict'], 'supported')
        self.assertFalse([s for s in result['supported_by'] if 'verified to the cent' in s])

    def test_the_judgment_verifying_beside_a_sibling_still_counts(self):
        saved = self.saved(self.rows(MT.text_reading(p1=JUDGMENT_PAGE1),
                                     MT.text_reading(p1=AFFIDAVIT_PAGE1)),
                           ['court:%s:1' % ENTRY, 'court:%s:2' % ENTRY])
        result = CV.assess(saved)
        self.assertFalse([m for m in result['missing'] if NO_AMOUNT_GAP in m], result['missing'])
        self.assertTrue([s for s in result['supported_by'] if 'court:%s:1' % ENTRY in s],
                        result['supported_by'])

    def test_the_judgment_verifying_beside_a_sibling_keeps_the_verdict_unheld_by_the_sibling(self):
        saved = self.saved(self.rows(MT.text_reading(p1=JUDGMENT_PAGE1),
                                     MT.text_reading(p1=AFFIDAVIT_PAGE1)),
                           ['court:%s:1' % ENTRY, 'court:%s:2' % ENTRY])
        result = CV.assess(saved)
        self.assertFalse([m for m in result['missing'] if 'not identified as the final judgment' in m],
                         result['missing'])
        self.assertTrue([n for n in result['notes'] if 'not identified as the final judgment' in n],
                        result['notes'])

    def test_a_motion_support_affidavit_titled_with_final_judgment_is_not_the_judgment(self):
        # Review finding: the classifier reads "...MOTION FOR FINAL JUDGMENT" as a final judgment.
        for title in ('AFFIDAVIT IN SUPPORT OF MOTION FOR FINAL JUDGMENT',
                      "PLAINTIFF'S MOTION FOR FINAL JUDGMENT OF FORECLOSURE",
                      'NOTICE OF FILING PROPOSED FINAL JUDGMENT',
                      'ORDER GRANTING MOTION FOR FINAL JUDGMENT'):
            with self.subTest(title=title):
                page1 = MT.PAGE1 + '\n' + title + "\nTHIS ACTION was heard on the plaintiff's motion."
                no_total = MT.text_reading(p1=JUDGMENT_PAGE1, p2='Principal: $10.00', p3='nothing else')
                saved = self.saved(self.rows(no_total, MT.text_reading(p1=page1)),
                                   ['court:%s:1' % ENTRY, 'court:%s:2' % ENTRY])
                self.assertTrue([c for c in saved['amount_checks']
                                 if c['ok'] and c['source_ref'].endswith(':2')])     # it verifies
                result = CV.assess(saved)
                self.assertTrue([m for m in result['missing'] if NO_AMOUNT_GAP in m], result['missing'])
                self.assertFalse([s for s in result['supported_by'] if 'verified to the cent' in s])
                self.assertEqual(result['verdict'], 'incomplete')

    def test_title_identification_needs_the_whole_line_and_a_clean_next_two(self):
        def titled(*lines):
            # Decree wording is present in every case here, so it cannot be what rescues a fake.
            return RCT.judgment_titled(MT.text_reading(
                p1=MT.PAGE1 + '\n' + '\n'.join(lines),
                p2=MT.PAGE2 + '\nIt is ORDERED AND ADJUDGED that the clerk shall sell the property.'))
        for lines in (('FINAL JUDGMENT OF FORECLOSURE',), ('AMENDED FINAL JUDGMENT OF FORECLOSURE',),
                      ('FINAL SUMMARY JUDGMENT OF FORECLOSURE',), ('FINAL JUDGMENT AND ORDER SETTING SALE',),
                      ('DEFAULT FINAL JUDGMENT OF FORECLOSURE',), ('FINAL JUDGMENT', 'OF FORECLOSURE'),
                      ('FINAL JUDGMENT OF FORECLOSURE', "THIS ACTION was heard on the Plaintiff's motion"),
                      ('FINAL JUDGMENT OF FORECLOSURE', 'Plaintiff,', 'v.'),
                      ('FINAL JUDGMENT OF FORECLOSURE', 'WILMINGTON SAVINGS FUND SOCIETY, FSB,')):
            self.assertTrue(titled(*lines), lines)
        for lines in (('FINAL JUDGMENT', 'AFFIDAVIT OF AMOUNTS DUE AND OWING'),
                      ('FINAL JUDGMENT PAYOFF STATEMENT',), ('FINAL JUDGMENT OF FORECLOSURE COST BILL',),
                      ('FINAL JUDGMENT AMOUNTS SCHEDULE',), ('FINAL JUDGMENT EXHIBIT A',),
                      ('FINAL JUDGMENT OF FORECLOSURE', '[PROPOSED]'),
                      ('Final Judgment of Foreclosure was entered on 1/1',),
                      ('FINAL JUDGMENT', "PLAINTIFF'S AFFIDAVIT OF AMOUNTS DUE"),
                      ('FINAL JUDGMENT', 'DECLARATION OF INDEBTEDNESS'),
                      ('FINAL JUDGMENT', 'AMOUNTS DUE AND OWING'),
                      ('FINAL JUDGMENT', 'STATE OF FLORIDA', 'COUNTY OF MIAMI-DADE'),
                      ('FINAL JUDGMENT', 'Affiant, being sworn'),
                      ('FINAL JUDGMENT', 'Re: Amounts due'),
                      ('FINAL JUDGMENT AND ORDER PAYOFF AMOUNTS DUE',),
                      ('FINAL JUDGMENT AND DECREE OF AMOUNTS CLAIMED BY PLAINTIFF',),
                      ('FINAL JUDGMENT', 'x', 'AFFIDAVIT OF AMOUNTS'),
                      ('FINAL JUDGMENT', 'COUNTY OF MIAMI-DADE,'), ('FINAL JUDGMENT', 'STATE OF FLORIDA,'),
                      ('FINAL JUDGMENT', 'AMOUNTS DUE AND OWING,'), ('FINAL JUDGMENT', 'JOHN DOE, AFFIANT,'),
                      ('FINAL JUDGMENT', 'the plaintiff states under oath,'),
                      ('FINAL JUDGMENT', 'Plaintiff,', 'v.', 'DECLARATION OF AMOUNTS DUE'),
                      ('FINAL JUDGMENT', 'CASE NO. 1', 'PAYOFF STATEMENT'),
                      ('FINAL JUDGMENT', 'THIS ACTION: AMOUNTS DUE PER AFFIANT'),
                      ('AFFIDAVIT IN SUPPORT OF MOTION FOR FINAL JUDGMENT',)):
            self.assertFalse(titled(*lines), lines)

    def test_a_title_without_decree_wording_is_not_a_judgment(self):
        self.assertFalse(RCT.judgment_titled(MT.text_reading(p1=MT.PAGE1 + '\nFINAL JUDGMENT OF FORECLOSURE')))

    def test_a_two_line_affidavit_title_does_not_supply_the_award(self):
        page1 = (MT.PAGE1 + '\nFINAL JUDGMENT\nAFFIDAVIT OF AMOUNTS DUE AND OWING\n'
                 'there is due the total sum of $100,000.00; clerk shall sell; ORDERED AND ADJUDGED')
        no_total = MT.text_reading(p1=JUDGMENT_PAGE1, p2='Principal: $10.00', p3='nothing else')
        saved = self.saved(self.rows(no_total, MT.text_reading(p1=page1)),
                           ['court:%s:1' % ENTRY, 'court:%s:2' % ENTRY])
        result = CV.assess(saved)
        self.assertTrue([m for m in result['missing'] if NO_AMOUNT_GAP in m], result['missing'])
        self.assertEqual(result['verdict'], 'incomplete')

    def test_a_sole_read_document_is_accepted_whatever_it_reads_as(self):
        saved = self.saved([row_for(self.tmp, reading=MT.text_reading(p1=AFFIDAVIT_PAGE1))],
                           ['court:%s:1' % ENTRY])
        result = CV.assess(saved)
        self.assertFalse([m for m in result['missing'] if NO_AMOUNT_GAP in m], result['missing'])


    def _unread_judgment_beside_read_sibling(self, attachments):
        timeline = VT.timeline(CASE, controlling=ENTRY, attachments=attachments)
        RCT.attach_text_checks(timeline, CASE, [row_for(
            self.tmp, ref='court:%s:2' % ENTRY, reading=MT.text_reading(p1=AFFIDAVIT_PAGE1))])
        return CV.assess(timeline)

    def test_an_unread_judgment_beside_a_read_sibling_is_not_a_sole_document(self):
        unread = dict(VT.read_attachment(ENTRY, document='court:%s:1' % ENTRY), state='fetched_unread')
        result = self._unread_judgment_beside_read_sibling(
            [unread, VT.read_attachment(ENTRY, document='court:%s:2' % ENTRY)])
        self.assertTrue([m for m in result['missing'] if NO_AMOUNT_GAP in m], result['missing'])
        self.assertFalse([s for s in result['supported_by'] if 'verified to the cent' in s])

    def test_an_entry_with_no_coverage_rows_cannot_show_a_sole_document(self):
        result = self._unread_judgment_beside_read_sibling([])
        self.assertTrue([m for m in result['missing'] if NO_AMOUNT_GAP in m], result['missing'])


    def test_a_read_row_with_no_document_is_a_second_document(self):
        nameless = dict(VT.read_attachment(ENTRY), document=None)
        result = self._unread_judgment_beside_read_sibling(
            [VT.read_attachment(ENTRY, document='court:%s:2' % ENTRY), nameless])
        self.assertTrue([m for m in result['missing'] if NO_AMOUNT_GAP in m], result['missing'])

    def test_a_check_from_a_file_with_no_coverage_row_is_not_the_sole_document(self):
        result = self._unread_judgment_beside_read_sibling(
            [VT.read_attachment(ENTRY, document='court:%s:1' % ENTRY)])    # the check is on :2
        self.assertTrue([m for m in result['missing'] if NO_AMOUNT_GAP in m], result['missing'])


class MalformedRowTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_one_malformed_row_does_not_abort_the_case(self):
        bad = row_for(self.tmp, entry='222222222', name='bad.pdf')
        bad['reading'] = {'pages': [{'page': 1, 'text': object(), 'outcome': 'text'}, 'not a page']}
        good = row_for(self.tmp)
        checks = RCT.text_amount_checks(CASE, [bad, good])
        self.assertTrue([c for c in checks if c['ok'] and c['entry_id'] == ENTRY])
        failed = [c for c in checks if c['entry_id'] == '222222222']
        self.assertTrue(failed and not any(c['ok'] for c in failed))
        self.assertIn('could not be checked', failed[0]['reason'])

    def test_a_non_court_malformed_row_is_skipped_without_a_check(self):
        bad = {'source_ref': 'official_records/1-2/1', 'manifest': None, 'reading': 'garbage'}
        self.assertEqual(RCT.text_amount_checks(CASE, [bad]), [])

    def test_bytes_are_not_rehashed_when_nothing_would_verify(self):
        no_total = MT.PAGE2.replace('$150,000.00', '$150,000.01')
        row = row_for(self.tmp, reading=MT.text_reading(p2=no_total))
        os.remove(row['manifest']['path'])
        checks = RCT.text_amount_checks(CASE, [row])
        self.assertTrue(checks and not any(c['ok'] for c in checks))
        self.assertFalse(any(c['hash_rechecked'] for c in checks))


if __name__ == '__main__':
    unittest.main(verbosity=2)
