"""Miami-Dade verdict gaps from the 2026-10-07 review, each held by a synthetic regression.

python -u test_miami_verdict_gaps.py

  1. A total read off the page IMAGE meets the same final-judgment tests as one read off text:
     the entry its source names, a recorded document hash, and "is this document the judgment".
  2. A corrected document with the same page count replaces the stored copy, and queue jobs for a
     replaced, withdrawn or re-fetched document stop counting as current.
  3. A satisfaction marks a judgment paid only when it names that judgment, and a partial
     satisfaction never does.

All case numbers, entries, book/pages and figures are invented.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _verdicttest as VT
import case_dossier as CD
import case_verdict as CV
import doc_board as DB
import document_classify as DC
import document_queue as DQ
import document_store as DS
import miami_case_timeline as T
import run_case_timeline as RCT

COUNTY = 'MIAMI-DADE'
CASE = '2099-000001-CA-01'
NOT_THE_JUDGMENT = 'is not identified as the final'


def reading_of(lines):
    return {'pages': [{'page': 1, 'outcome': 'text', 'text': '\n'.join(lines)}]}


def image_check(**over):
    check = VT.ok_check()
    check.update(over)
    return check


# --- 1. image reads meet the text path's tests ------------------------------------------------
class ImageReadParityTests(unittest.TestCase):
    def assess(self, check, attachments=None):
        return CV.assess(VT.timeline('X', checks=[check],
                                     attachments=attachments or [VT.read_attachment()]))

    def test_the_full_image_check_still_verifies(self):
        r = self.assess(image_check())
        self.assertEqual(r['verdict'], 'supported', (r['missing'], r['conflicts']))

    def test_an_image_check_with_no_document_hash_does_not_verify(self):
        check = image_check()
        check.pop('document_hash')
        r = self.assess(check)
        self.assertEqual(r['verdict'], 'incomplete')
        self.assertTrue(any('image check' in m and 'no document hash' in m for m in r['missing']),
                        r['missing'])

    def test_an_image_check_filed_under_another_entry_does_not_verify(self):
        r = self.assess(image_check(source_ref='court:999999:1'))
        self.assertEqual(r['verdict'], 'incomplete')
        self.assertTrue(any('filed under a different entry' in m for m in r['missing']), r['missing'])

    def test_a_balanced_affidavit_beside_the_judgment_is_not_the_judgment_total(self):
        # The review's case: an affidavit of indebtedness on the judgment's entry whose table
        # balances. Read off its image, its total used to verify as the judgment's.
        two = [VT.read_attachment(), VT.read_attachment(document='court:232820355:2')]
        r = self.assess(image_check(source_ref='court:232820355:2', document_kind='affidavit',
                                    judgment_title=False), attachments=two)
        self.assertEqual(r['verdict'], 'incomplete')
        self.assertTrue(any(NOT_THE_JUDGMENT in m for m in r['missing']), r['missing'])
        self.assertIsNone(r.get('judgment_amount'))

    def test_an_image_check_saved_before_it_recorded_what_the_document_is_is_held(self):
        check = image_check()
        for key in ('document_kind', 'judgment_title', 'source'):
            check.pop(key)
        two = [VT.read_attachment(), VT.read_attachment(document='court:232820355:2')]
        r = self.assess(check, attachments=two)
        self.assertEqual(r['verdict'], 'incomplete')
        self.assertTrue(any(NOT_THE_JUDGMENT in m for m in r['missing']), r['missing'])

    def test_the_sole_read_document_on_the_entry_is_accepted_whatever_it_reads_as(self):
        r = self.assess(image_check(document_kind='unknown', judgment_title=False))
        self.assertEqual(r['verdict'], 'supported', (r['missing'], r['conflicts']))

    def test_text_and_image_reach_the_same_verdict_on_the_same_evidence(self):
        two = [VT.read_attachment(), VT.read_attachment(document='court:232820355:2')]
        for kind, title in (('final_judgment', True), ('affidavit', False), ('unknown', False)):
            got = {}
            for source in ('document_text', 'vision'):
                r = self.assess(image_check(source=source, source_ref='court:232820355:2',
                                            document_kind=kind, judgment_title=title),
                                attachments=two)
                got[source] = r['verdict']
            self.assertEqual(got['document_text'], got['vision'], (kind, got))

    def test_the_image_path_records_what_the_document_is(self):
        affidavit = reading_of(['AFFIDAVIT OF INDEBTEDNESS', 'Principal $100.00', 'Total $100.00'])
        row = {'source_ref': 'court:232820355:2', 'entry_ref': '232820355',
               'manifest': {'source_sha256': 'h' * 64, 'document_key': 'k' * 64,
                            'document_name': 'Affidavit'}, 'reading': affidavit}
        detail = {'document_hash': 'h' * 64, 'source_ref': row['source_ref'], 'figures': [],
                  'grand_totals': [], 'pages': {}}
        fake = {'amount': 100.0, 'page': 1, 'ok': True, 'reason': 'adds up', 'pages': [1],
                'run': None, 'components': [], 'credits': [], 'rates': [], 'subtotals': [],
                'component_rows': []}
        with tempfile.TemporaryDirectory() as base, \
                patch.object(DS, 'pipeline_load', return_value=detail), \
                patch('judgment_money.verify_document', return_value=[fake]):
            out = RCT.read_amounts([row], base)
        [check] = out['amount_checks']
        self.assertEqual(check['source'], 'vision')
        self.assertEqual(check['document_hash'], 'h' * 64)
        self.assertNotEqual(check['document_kind'], 'final_judgment')
        self.assertFalse(check['judgment_title'])


# --- 2. corrected documents and stale queue jobs ----------------------------------------------
def pdf(text, title=None):
    fitz = DS._fitz()
    with fitz.open() as doc:
        doc.new_page().insert_text((72, 72), text)
        if title:
            doc.set_metadata({'title': title})
        return doc.tobytes(garbage=3 if title else 0, deflate=bool(title))


class CorrectedDocumentTests(unittest.TestCase):
    def store(self, root, content):
        retrieved = {'content': content, 'transport': 'test', 'pages_expected': 1,
                     'record_key': {'book': '99999', 'page': '1', 'book_type': 'O', 'cfn': '1'}}
        with patch.object(DS, 'case_dir', return_value=Path(root)):
            return DS.store(COUNTY, CASE, retrieved, source_ref='recorded:1', doc_name='JUDGMENT')

    def test_a_corrected_document_with_the_same_page_count_replaces_the_old_copy(self):
        with tempfile.TemporaryDirectory() as root:
            first = self.store(root, pdf('FINAL JUDGMENT TOTAL $100.00'))
            self.assertTrue(first['stored'])
            second = self.store(root, pdf('FINAL JUDGMENT TOTAL $900.00'))
            self.assertTrue(second['stored'])
            self.assertTrue(second.get('replaced_on_content_change'))
            self.assertEqual(second['read_status'], 'unread')
            self.assertIn(b'900.00', DS._fitz().open(stream=Path(second['path']).read_bytes(),
                                                     filetype='pdf')[0].get_text().encode())

    def test_the_same_document_re_serialised_is_still_kept_once(self):
        with tempfile.TemporaryDirectory() as root:
            self.store(root, pdf('FINAL JUDGMENT TOTAL $100.00'))
            again = self.store(root, pdf('FINAL JUDGMENT TOTAL $100.00', title='served again'))
            self.assertFalse(again['stored'])
            self.assertTrue(again.get('bytes_differ_between_fetches'))

    def test_a_change_inside_a_form_xobject_is_a_change(self):
        # A raw content-stream hash saw only '/Fm0 Do' and kept the old copy.
        fitz = DS._fitz()
        def wrapped(text):
            with fitz.open() as inner, fitz.open() as outer:
                inner.new_page().insert_text((72, 72), text)
                outer.new_page().show_pdf_page(outer[0].rect, inner, 0)
                return outer.tobytes()
        self.assertFalse(DS._same_page_content(wrapped('TOTAL $100,000.00'),
                                               wrapped('TOTAL $900,000.00')))
        self.assertTrue(DS._same_page_content(wrapped('TOTAL $100,000.00'),
                                              wrapped('TOTAL $100,000.00')))

    def test_recompressing_the_same_pages_is_not_a_change(self):
        fitz = DS._fitz()
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_text((72, 72), 'FINAL JUDGMENT TOTAL $100.00')
            pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40), False)
            pix.clear_with(200)
            page.insert_image(fitz.Rect(100, 100, 140, 140), pixmap=pix)
            plain = doc.tobytes()
            packed = doc.tobytes(garbage=4, deflate=True, deflate_images=True, use_objstms=True)
        self.assertNotEqual(plain, packed)
        self.assertTrue(DS._same_page_content(plain, packed))

    def test_the_old_copy_s_page_text_is_moved_aside_on_replacement(self):
        with tempfile.TemporaryDirectory() as root:
            first = self.store(root, pdf('FINAL JUDGMENT TOTAL $100.00'))
            text_dir = Path(root) / (first['document_key'][:16] + '-text')
            text_dir.mkdir()
            (text_dir / 'pages.json').write_text('{"pages": []}')
            self.store(root, pdf('FINAL JUDGMENT TOTAL $900.00'))
            self.assertFalse(text_dir.exists())
            self.assertTrue(list(Path(root).glob('*-text.replaced-*')))

    def test_a_fingerprint_that_cannot_be_taken_counts_as_a_change(self):
        with patch.object(DS, 'page_fingerprint', return_value=None):
            self.assertFalse(DS._same_page_content(b'a', b'a'))


class StaleQueueJobTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.q = DQ.DocumentQueue(str(Path(self.dir.name) / 'q.db'))

    def tearDown(self):
        self.q.close()
        self.dir.cleanup()

    def done(self, ref, kind, payload=None):
        self.q.add(COUNTY, CASE, ref, kind, payload)
        job = self.q.claim_ref('w', COUNTY, CASE, ref, kind)
        self.assertTrue(self.q.complete(job['id'], 'w', sha256='x', reader_version=9,
                                        read_status='read'))

    def status(self, ref, kind):
        return next(j['status'] for j in self.q.jobs(COUNTY, CASE)
                    if j['source_ref'] == ref and j['kind'] == kind)

    def test_a_replaced_attachment_under_the_same_reference_is_redone(self):
        self.done('court:5:1', 'acquire', {'documentName': 'Final Judgment', 'pages': 3})
        self.done('court:5:1', 'read')
        self.assertTrue(self.q.add(COUNTY, CASE, 'court:5:1', 'acquire',
                                   {'documentName': 'Amended Final Judgment', 'pages': 3}))
        self.assertEqual(self.status('court:5:1', 'acquire'), 'pending')
        self.assertEqual(self.status('court:5:1', 'read'), 'pending')
        self.assertFalse(self.q.coverage(COUNTY, CASE)['collection_complete'])

    def test_a_fresh_request_token_alone_is_not_a_different_document(self):
        self.done('court:5:1', 'acquire', {'documentName': 'Final Judgment', 'encDocInfo': 'a'})
        self.assertFalse(self.q.add(COUNTY, CASE, 'court:5:1', 'acquire',
                                    {'documentName': 'Final Judgment', 'encDocInfo': 'b'}))
        [job] = self.q.jobs(COUNTY, CASE)
        self.assertEqual(job['status'], 'done')
        self.assertEqual(job['payload']['encDocInfo'], 'b')       # the retrieval uses today's token

    def test_a_document_the_county_no_longer_lists_is_retired_not_counted(self):
        self.done('court:5:1', 'acquire', {'documentName': 'Final Judgment'})
        self.done('court:5:1', 'read')
        self.done('court:5:2', 'acquire', {'documentName': 'Exhibit'})
        self.done('court:6:1', 'acquire', {'documentName': 'Motion'})
        retired = self.q.retire_missing(COUNTY, CASE, 'acquire', ['court:5:2'], ['court:5:'])
        self.assertEqual(retired, ['court:5:1'])
        self.assertEqual(self.status('court:5:1', 'acquire'), DQ.SUPERSEDED)
        self.assertEqual(self.status('court:5:1', 'read'), DQ.SUPERSEDED)
        self.assertEqual(self.status('court:6:1', 'acquire'), 'done')   # entry 6 was not listed
        cov = self.q.coverage(COUNTY, CASE)
        self.assertEqual((cov['jobs'], cov['done']), (2, 2))
        self.assertIsNone(self.q.claim('w2'))
        # Listed again: revived, so it is fetched and read again rather than trusted.
        self.assertTrue(self.q.add(COUNTY, CASE, 'court:5:1', 'acquire', {'documentName': 'Final Judgment'}))
        self.assertEqual(self.status('court:5:1', 'acquire'), 'pending')
        self.assertEqual(self.status('court:5:1', 'read'), 'pending')

    def test_a_re_acquisition_puts_the_read_back_before_the_bytes_change(self):
        # A worker that dies after new bytes land must not leave a `done` read of the old copy.
        self.done('court:5:1', 'acquire', {'documentName': 'Final Judgment'})
        self.done('court:5:1', 'read')
        self.q.reset_dependents(COUNTY, CASE, 'court:5:1')
        self.assertEqual(self.status('court:5:1', 'read'), 'pending')
        self.assertEqual(self.status('court:5:1', 'acquire'), 'done')

    def test_collecting_a_case_retires_a_withdrawn_attachment(self):
        import document_collectors as COL

        class Fake:
            def enumerate_documents(self, case):
                return {'entries': [{'source_id': '5', 'expected_documents': 1,
                                     'metadata': {'encID': 'e', 'eventID': 5}}],
                        'pagination_verified': True}

            def attachments(self, case, meta):
                return [{'documentID': '2', 'documentName': 'Amended Final Judgment'}]

        base = Path(self.dir.name) / 'case'
        base.mkdir()
        with DQ.DocumentQueue(str(base / 'queue.sqlite3')) as q:
            q.add(COUNTY, CASE, 'court:5:1', 'acquire', {'documentID': '1'})
        with patch.object(COL, 'collector_for', return_value=Fake()), \
                patch.object(DS, 'pipeline_folder', return_value=base), \
                patch.object(DS, 'pipeline_report', return_value={}):
            COL.collect_case_documents(COUNTY, CASE)
        with DQ.DocumentQueue(str(base / 'queue.sqlite3')) as q:
            states = {j['source_ref']: j['status'] for j in q.jobs(COUNTY, CASE)}
        self.assertEqual(states, {'court:5:1': DQ.SUPERSEDED, 'court:5:2': 'pending'})
        inventory = json.loads((base / 'inventory.json').read_text())
        self.assertEqual(inventory['retired_documents'], ['court:5:1'])

    def test_an_entry_identified_only_by_list_position_retires_nothing(self):
        import document_collectors as COL

        class Fake:
            def enumerate_documents(self, case):
                return {'entries': [{'source_id': '0', 'expected_documents': 1,
                                     'metadata': {'encID': 'e'}}], 'pagination_verified': True}

            def attachments(self, case, meta):
                return [{'documentID': '2'}]

        base = Path(self.dir.name) / 'case0'
        base.mkdir()
        with DQ.DocumentQueue(str(base / 'queue.sqlite3')) as q:
            q.add(COUNTY, CASE, 'court:0:1', 'acquire', {'documentID': '1'})
        with patch.object(COL, 'collector_for', return_value=Fake()), \
                patch.object(DS, 'pipeline_folder', return_value=base), \
                patch.object(DS, 'pipeline_report', return_value={}):
            COL.collect_case_documents(COUNTY, CASE)
        with DQ.DocumentQueue(str(base / 'queue.sqlite3')) as q:
            states = {j['source_ref']: j['status'] for j in q.jobs(COUNTY, CASE)}
        self.assertEqual(states['court:0:1'], 'pending')

    def test_saved_rows_of_retired_documents_are_not_loaded(self):
        import hashlib
        base = Path(self.dir.name) / 'rows'
        base.mkdir()
        for ref in ('court:5:1', 'court:5:2'):
            (base / (hashlib.sha256(ref.encode()).hexdigest() + '.json')).write_text(
                json.dumps({'source_ref': ref, 'manifest': {}, 'reading': {'pages': []}}))
        with DQ.DocumentQueue(str(base / 'queue.sqlite3')) as q:
            q.add(COUNTY, CASE, 'court:5:1', 'acquire', {'documentID': '1'})
            q.add(COUNTY, CASE, 'court:5:2', 'acquire', {'documentID': '2'})
            q.retire_missing(COUNTY, CASE, 'acquire', ['court:5:2'], ['court:5:'])
        self.assertEqual([r['source_ref'] for r in RCT.load_rows(base)], ['court:5:2'])


# --- 3. a satisfaction pays only the judgment it names ----------------------------------------
def entry(n, text, date=None):
    return {'source_id': str(n), 'expected_documents': 0, 'metadata': dict(
        eventID=n, eventDate=date or '09/%02d/2026' % n, docketDescrition=text)}


def build(entries):
    return T.build_timeline('SYNTHETIC', {'entries': entries, 'pagination_verified': True}, (),
                            '2026-09-23')


class DocketSatisfactionTests(unittest.TestCase):
    def test_an_unnamed_satisfaction_is_unmatched_and_holds_the_verdict(self):
        t = build([entry(1, 'Final Judgment'), entry(2, 'Satisfaction of Judgment')])
        [j] = t['judgments']['judgments']
        self.assertEqual(j['satisfaction'], 'no_satisfaction_found')
        r = CV.assess(t)
        self.assertNotEqual(r['verdict'], 'supported')
        self.assertTrue(any('could not link entry 2' in m for m in r['missing']), r['missing'])

    def test_a_satisfaction_citing_the_judgment_date_satisfies_it(self):
        t = build([entry(1, 'Final Judgment'),
                   entry(2, 'Satisfaction of Final Judgment dated 09/01/2026')])
        [j] = t['judgments']['judgments']
        self.assertEqual((j['status'], j['satisfaction']), ('satisfied', 'satisfied'))

    def test_a_satisfaction_naming_another_date_does_not_satisfy_this_judgment(self):
        t = build([entry(1, 'Final Judgment'),
                   entry(2, 'Satisfaction of Final Judgment entered March 3, 2019')])
        [j] = t['judgments']['judgments']
        self.assertEqual(j['satisfaction'], 'no_satisfaction_found')

    def test_a_partial_satisfaction_never_satisfies(self):
        for text in ('Partial Satisfaction of Final Judgment entered September 1, 2026',
                     'Final Judgment entered September 1, 2026 partially satisfied'):
            t = build([entry(1, 'Final Judgment'), entry(2, 'Satisfaction of ' + text
                                                          if 'partially' in text else text)])
            [j] = t['judgments']['judgments']
            self.assertEqual((j['status'], j['satisfaction']), ('operative', 'partially_satisfied'),
                             text)


JUDGMENT = {'source_ref': 'recorded:10', 'is': 'final_judgment',
            'amounts': [{'amount': 250123.45}], 'record_key': ['31000', '1200']}


def sat(**over):
    row = {'source_ref': 'recorded:11', 'is': 'satisfaction_of_judgment', 'amounts': [],
           'cites_instruments': [], 'recites_amounts': [], 'index_label': 'SATISFACTION OF JUDGMENT'}
    row.update(over)
    return row


class ReviewRoundOneSatisfactionTests(unittest.TestCase):
    def test_partial_wording_in_the_body_is_partial(self):
        # The body is what names the judgment here, and it also says partial.
        body = ('acknowledges partial satisfaction of the Final Judgment entered September 1, 2026 '
                'in the amount of $5,000.00')
        rows = [{'entry_id': '1', 'kind': 'final_judgment', 'date': '2026-09-01',
                 'operative_text': 'Final Judgment', 'description': 'Final Judgment', 'comments': ''},
                {'entry_id': '2', 'kind': 'satisfaction', 'date': '2026-09-02',
                 'operative_text': 'Satisfaction of Judgment', 'description': 'Satisfaction of Judgment',
                 'comments': '', '_body': body}]
        recon = T.reconcile_judgments(rows, '2026-09-23')
        [j] = recon['judgments']
        self.assertEqual((j['status'], j['satisfaction']), ('operative', 'partially_satisfied'))

    def test_a_date_shared_with_a_fee_judgment_pays_neither(self):
        rows = [{'entry_id': '1', 'kind': 'final_judgment', 'date': '2026-09-01',
                 'operative_text': 'Final Judgment of Foreclosure', 'description': '', 'comments': ''},
                {'entry_id': '2', 'kind': 'final_judgment', 'date': '2026-09-01',
                 'operative_text': 'Supplemental Final Judgment for attorney fees and costs',
                 'description': '', 'comments': ''},
                {'entry_id': '3', 'kind': 'satisfaction', 'date': '2026-09-05',
                 'operative_text': 'Satisfaction of fee judgment dated 09/01/2026',
                 'description': '', 'comments': ''}]
        recon = T.reconcile_judgments(rows, '2026-09-23')
        roles = {j['entry_id']: j for j in recon['judgments']}
        self.assertEqual(roles['2']['role'], 'supplemental')
        self.assertEqual(roles['1']['satisfaction'], 'no_satisfaction_found')
        self.assertEqual([u['entry_id'] for u in recon['unmatched']], ['3'])

    def test_a_partial_payment_satisfaction_is_partial(self):
        lines = ['SATISFACTION OF JUDGMENT', 'Final Judgment in the amount of $250,123.45',
                 'Plaintiff acknowledges receipt of a partial payment of $10,000.00']
        self.assertEqual(DC.classify(reading_of(lines))['kind'], 'partial_satisfaction_of_judgment')

    def test_malformed_saved_readings_do_not_crash(self):
        for bad in ('text', 5, {'pages': 'x'}, {'pages': [{'outcome': 'text', 'text': 7}]}):
            self.assertEqual(CD._recited_amounts(bad), [])


class ReviewRoundTwoTests(unittest.TestCase):
    def test_an_unnamed_satisfaction_does_not_close_the_case(self):
        t = build([entry(1, 'Final Judgment'), entry(2, 'Satisfaction of Judgment')])
        self.assertEqual(t['status']['kind'], 'unclear')

    def test_a_named_satisfaction_still_closes_the_case(self):
        t = build([entry(1, 'Final Judgment'),
                   entry(2, 'Satisfaction of Final Judgment dated 09/01/2026')])
        self.assertEqual(t['status']['kind'], 'satisfied_redeemed')

    def test_a_partially_satisfied_judgment_does_not_close_the_case(self):
        t = build([entry(1, 'Final Judgment'),
                   entry(2, 'Satisfaction of Final Judgment dated 09/01/2026, partially satisfied')])
        self.assertEqual(t['status']['kind'], 'judgment_entered')

    def test_a_stay_relief_after_an_unnamed_satisfaction_does_not_close_the_case(self):
        t = build([entry(1, 'Final Judgment'), entry(2, 'Satisfaction of Judgment'),
                   entry(3, 'Suggestion of Bankruptcy'),
                   entry(4, 'Order granting relief from bankruptcy stay')])
        self.assertNotEqual(t['status']['kind'], 'satisfied_redeemed')

    def test_the_word_redemption_alone_does_not_exempt_a_satisfaction(self):
        t = build([entry(1, 'Final Judgment'), entry(2, 'Satisfaction of Judgment; right of redemption')])
        self.assertNotEqual(t['status']['kind'], 'satisfied_redeemed')

    def test_a_later_unnamed_satisfaction_does_not_undo_an_earlier_discharge(self):
        t = build([entry(1, 'Final Judgment'),
                   entry(2, 'Satisfaction of Final Judgment dated 09/01/2026'),
                   entry(3, 'Satisfaction of Judgment')])
        self.assertEqual(t['status']['kind'], 'satisfied_redeemed')

    def test_the_pipeline_report_leaves_out_reopened_rows(self):
        import hashlib
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            for ref in ('court:5:1', 'court:5:2'):
                (base / (hashlib.sha256(ref.encode()).hexdigest() + '.json')).write_text(json.dumps(
                    {'source_ref': ref, 'manifest': {'pages': 1, 'document_key': ref, 'path': ''},
                     'reading': {'pages': []}}))
            with DQ.DocumentQueue(str(base / 'queue.sqlite3')) as q:
                for ref in ('court:5:1', 'court:5:2'):
                    q.add(COUNTY, CASE, ref, 'acquire', {'documentID': ref})
                job = q.claim_ref('w', COUNTY, CASE, 'court:5:2', 'acquire')
                q.complete(job['id'], 'w')
            with patch.object(DS, 'pipeline_folder', return_value=base):
                report = DS.pipeline_report(COUNTY, CASE)
            self.assertEqual(report['documents_obtained'], 1)

    def test_reopened_rows_are_blanked_for_every_reader(self):
        import hashlib
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            ref = 'court:5:1'
            (base / (hashlib.sha256(ref.encode()).hexdigest() + '.json')).write_text(json.dumps(
                {'source_ref': ref, 'manifest': {'source_sha256': 'old'}, 'reading': {'pages': [{'page': 1}]}}))
            with DQ.DocumentQueue(str(base / 'queue.sqlite3')) as q:
                q.add(COUNTY, CASE, ref, 'acquire', {'documentID': '1'})     # pending
            [row] = RCT.load_rows(base)
            self.assertEqual((row['manifest'], row['reading']), ({}, {'pages': []}))

    def test_an_unreadable_queue_holds_every_row(self):
        import hashlib
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            ref = 'court:5:1'
            (base / (hashlib.sha256(ref.encode()).hexdigest() + '.json')).write_text(json.dumps(
                {'source_ref': ref, 'manifest': {'source_sha256': 'h'}, 'reading': {'pages': [{'page': 1}]}}))
            (base / 'queue.sqlite3').write_bytes(b'not a database at all' * 100)
            [row] = RCT.load_rows(base)
            self.assertEqual(row['manifest'], {})


class DossierSatisfactionTests(unittest.TestCase):
    def test_reciting_the_judgment_amount_names_it(self):
        j = CD._operative_judgment([JUDGMENT, sat(recites_amounts=['250123.45'])])
        self.assertTrue(j['satisfied'])
        self.assertIsNone(j['amount'])
        self.assertEqual(j['satisfied_by'], ['recorded:11'])

    def test_citing_the_judgment_recording_names_it(self):
        j = CD._operative_judgment([JUDGMENT, sat(cites_instruments=[{'book': '031000', 'page_no': '1200'}])])
        self.assertTrue(j['satisfied'])

    def test_a_satisfaction_that_names_nothing_pays_nothing_and_states_no_debt(self):
        j = CD._operative_judgment([JUDGMENT, sat(recites_amounts=['9999.99'],
                                                  cites_instruments=[{'book': '1', 'page_no': '2'}])])
        self.assertFalse(j['satisfied'])
        self.assertEqual(j['satisfied_by'], [])
        self.assertEqual(j['satisfaction_unlinked'], ['recorded:11'])
        self.assertIsNone(j['amount'])
        self.assertEqual(j['printed_amount'], 250123.45)

    def test_an_unlinked_satisfaction_is_an_open_gap_and_not_paid_on_the_board(self):
        docs = [{'source_ref': 'recorded:10', 'read_status': 'read', 'record_key': {'book': '31000', 'page': '1200'},
                 'classification': {'kind': 'final_judgment'}, 'amount_candidates': [{'amount': 250123.45}]},
                {'source_ref': 'recorded:11', 'read_status': 'read',
                 'classification': {'kind': 'satisfaction_of_judgment'},
                 'reading': reading_of(['SATISFACTION OF JUDGMENT', 'the sum of $1,000.00'])}]
        dossier = CD.build(CASE, COUNTY, documents=docs)
        self.assertTrue(any('names neither this' in g for g in dossier['open_gaps']), dossier['open_gaps'])
        self.assertNotEqual(DB.summarize(dossier)['j'], 'sat')

    def test_the_dossier_reads_the_amount_off_the_satisfaction_text(self):
        docs = [{'source_ref': 'recorded:10', 'read_status': 'read',
                 'classification': {'kind': 'final_judgment'}, 'amount_candidates': [{'amount': 250123.45}]},
                {'source_ref': 'recorded:11', 'read_status': 'read',
                 'classification': {'kind': 'satisfaction_of_judgment'},
                 'reading': reading_of(['SATISFACTION OF JUDGMENT',
                                        'judgment in the amount of $250,123.45 is paid in full'])}]
        j = CD.build(CASE, COUNTY, documents=docs)['c_documents']['judgment']
        self.assertTrue(j['satisfied'], j)

    def test_a_partial_never_pays_even_when_it_names_the_judgment(self):
        j = CD._operative_judgment([JUDGMENT, sat(recites_amounts=['250123.45'],
                                                  index_label='PARTIAL SATISFACTION OF JUDGMENT')])
        self.assertFalse(j['satisfied'])
        self.assertEqual(j['partially_satisfied_by'], ['recorded:11'])

    def test_partially_satisfied_wording_classifies_as_partial(self):
        lines = ['SATISFACTION OF FINAL JUDGMENT',
                 'The judgment recorded in Official Records Book 31000 Page 1200 is hereby '
                 'PARTIALLY SATISFIED and the plaintiff acknowledges payment']
        self.assertEqual(DC.classify(reading_of(lines))['kind'], 'partial_satisfaction_of_judgment')

    def test_with_no_judgment_read_a_satisfaction_pays_nothing(self):
        j = CD._operative_judgment([sat()])
        self.assertEqual(j['satisfied_by'], [])
        self.assertEqual(j['satisfactions_read'], ['recorded:11'])


if __name__ == '__main__':
    unittest.main()
