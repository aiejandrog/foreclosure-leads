"""judgment_pass: the plan reads state from disk and prices only unread amount pages. Synthetic data."""
import hashlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import judgment_pass as JP

CASE = '2025-000001-CA-01'


def _row(base, entry, text, name, total_page=True):
    ref = 'court:%s:1' % entry
    pages = [{'page': 1, 'outcome': 'text', 'text': text}] + (
        [{'page': 2, 'outcome': 'text', 'text': 'Total $1,234.56'}] if total_page else [])
    row = {'source_ref': ref, 'manifest': {'sha256': 'h' + entry}, 'reading': {'pages': pages}}
    (Path(base) / (hashlib.sha256(name.encode()).hexdigest() + '.json')).write_text(json.dumps(row))
    return ref


def _plan(docs, judgments=None):
    """docs: (entry, kind, eligible, gaps). judgments: {entry: (status, role)}; by default every
    final judgment is an operative judgment of record."""
    documents = [dict(entry_id=e, kind=k, eligible_for_acquisition=ok, gaps=g,
                      date='2026-01-%02d' % int(e)) for e, k, ok, g in docs]
    status = judgments or {e: ('operative', 'judgment') for e, k, _, _ in docs
                           if k == 'final_judgment'}
    return {'documents': documents, 'judgments': {'judgments': [
        {'entry_id': e, 'date': '2026-01-%02d' % int(e), 'status': st, 'role': role}
        for e, (st, role) in status.items()]}}


def _buy(base, ref, entry, gaps=()):
    figures = [{'id': 'a', 'page': 2, 'amount': 1234.56}]
    (Path(base) / ('amount-vision-' + hashlib.sha256(ref.encode()).hexdigest() + '.json')).write_text(
        json.dumps({'source_ref': ref, 'document_hash': 'h' + entry, 'gaps': list(gaps),
                    'figures': figures, 'grand_totals': [{'amount': 1234.56, 'page': 2}],
                    'pages': {'2': {}}}))


class Assess(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        (self.base / 'inventory.json').write_text('{}')

    def tearDown(self):
        self.tmp.cleanup()

    def run_assess(self, docs, timeline=None):
        with mock.patch('document_prioritizer.prioritize', return_value=_plan(docs)):
            return JP.assess(CASE, self.base, timeline, '2026-09-25')

    def test_no_docket(self):
        (self.base / 'inventory.json').unlink()
        self.assertEqual(JP.assess(CASE, self.base, None, '2026-09-25')['state'], 'no_docket')

    def test_verified_needs_ok_check_on_target(self):
        timeline = {'judgments': {'controlling_entry': '9'}}
        ref8 = _row(self.base, '8', '$5.00', 'b')
        _buy(self.base, ref8, '8')
        _row(self.base, '9', '$5.00', 'a')
        docs = [('9', 'final_judgment', True, []), ('8', 'final_judgment', True, [])]
        ok8 = [{'ok': True, 'amount': 1, 'page': 2, 'reason': '', 'pages': [], 'run': [],
                'components': [], 'credits': [], 'rates': [], 'subtotals': [], 'component_rows': []}]
        with mock.patch('judgment_money.verify_document', return_value=ok8):
            got = self.run_assess(docs, timeline)
        self.assertEqual(got['state'], 'needs_paid_read')    # an ok check on another judgment is not it
        ref9 = 'court:9:1'
        _buy(self.base, ref9, '9')
        with mock.patch('judgment_money.verify_document', return_value=ok8):
            got = self.run_assess(docs, timeline)
        self.assertEqual(got['state'], 'verified')

    def test_pages_counted_in_reader_order_and_cache_skipped(self):
        _row(self.base, '5', 'order $1.00', 'o')          # a controlling order read first
        ref = _row(self.base, '9', 'judgment $2.00', 'j')
        docs = [('5', 'vacatur', True, []), ('9', 'final_judgment', True, [])]
        got = self.run_assess(docs)
        self.assertEqual((got['state'], got['pages_to_judgment'], got['pages_all']),
                         ('needs_paid_read', 4, 4))
        _buy(self.base, ref, '9')
        with mock.patch('judgment_money.verify_document', return_value=[]):
            got = self.run_assess(docs)
        # read in full, still not verified: named, not priced again, not 'no amount page'
        self.assertEqual((got['state'], got['pages_to_judgment']), ('read_not_verified', 0))

    def test_stale_or_partial_purchase_is_priced(self):
        ref = _row(self.base, '9', 'judgment $2.00', 'j')
        (self.base / ('amount-vision-' + hashlib.sha256(ref.encode()).hexdigest() + '.json')).write_text(
            json.dumps({'source_ref': ref, 'document_hash': 'OLD', 'gaps': []}))
        got = self.run_assess([('9', 'final_judgment', True, [])])
        self.assertEqual((got['state'], got['pages_to_judgment']), ('needs_paid_read', 2))
        _buy(self.base, ref, '9', gaps=[{'page': 2, 'reason': 'budget_exhausted: share spent'}])
        got = self.run_assess([('9', 'final_judgment', True, [])])
        self.assertEqual(got['state'], 'needs_paid_read')

    def test_without_controlling_only_latest_operative_judgment_counts(self):
        _row(self.base, '9', '$5.00', 'a')
        _row(self.base, '4', '$5.00', 'b')
        docs = [('4', 'final_judgment', True, []), ('9', 'final_judgment', True, [])]
        got = self.run_assess(docs)
        self.assertEqual((got['state'], got['target_entry']), ('needs_paid_read', '9'))
        # the later one vacated: the earlier operative one is the target
        with mock.patch('document_prioritizer.prioritize', return_value=_plan(
                docs, {'4': ('operative', 'judgment'), '9': ('vacated', 'judgment')})):
            got = JP.assess(CASE, self.base, None, '2026-09-25')
        self.assertEqual(got['target_entry'], '4')
        # every judgment vacated: no target at all, never 'verified' on a dead one
        with mock.patch('document_prioritizer.prioritize', return_value=_plan(
                docs, {'4': ('vacated', 'judgment'), '9': ('satisfied', 'judgment')})):
            got = JP.assess(CASE, self.base, None, '2026-09-25')
        self.assertEqual(got['state'], 'no_operative_judgment')

    def test_every_document_of_the_target_counts(self):
        ref1 = _row(self.base, '9', '$1.00', 'j1')
        row2 = {'source_ref': 'court:9:2', 'manifest': {'sha256': 'h9'},
                'reading': {'pages': [{'page': 1, 'text': 'Total $9.99'}]}}
        (self.base / (hashlib.sha256(b'j2').hexdigest() + '.json')).write_text(json.dumps(row2))
        _buy(self.base, ref1, '9')
        with mock.patch('judgment_money.verify_document', return_value=[]):
            got = self.run_assess([('9', 'final_judgment', True, [])])
        self.assertEqual((got['state'], got['pages_to_judgment']), ('needs_paid_read', 1))

    def test_unclear_judgments_still_get_a_target(self):
        self.assertEqual(JP._target({'judgments': [
            {'entry_id': '9', 'date': '2026-01-01', 'status': 'unclear', 'role': 'judgment'},
            {'entry_id': '4', 'date': '2025-01-01', 'status': 'vacated', 'role': 'judgment'}]},
            None), ('9', 'latest_unclear'))

    def test_partial_purchase_prices_only_unreached_pages(self):
        ref = _row(self.base, '9', '$1.00', 'j')                 # amount pages 1 and 2
        _buy(self.base, ref, '9', gaps=[{'page': 2, 'reason': 'Amount page not read; cap or reader stop'}])
        with mock.patch('judgment_money.verify_document', return_value=[]):
            got = self.run_assess([('9', 'final_judgment', True, [])])
        self.assertEqual((got['state'], got['pages_to_judgment']), ('needs_paid_read', 1))
        _buy(self.base, ref, '9', gaps=[{'page': 2, 'reason': 'Vision returned unreadable'}])
        with mock.patch('judgment_money.verify_document', return_value=[]):
            got = self.run_assess([('9', 'final_judgment', True, [])])
        self.assertEqual(got['state'], 'read_not_verified')      # re-reading buys the same answer

    def test_timeline_reconciliation_wins_over_the_index(self):
        _row(self.base, '9', '$5.00', 'a')
        _row(self.base, '4', '$5.00', 'b')
        docs = [('4', 'final_judgment', True, []), ('9', 'final_judgment', True, [])]
        timeline = {'judgments': {'controlling_entry': None, 'judgments': [
            {'entry_id': '4', 'date': '2026-01-04', 'status': 'unclear', 'role': 'judgment'},
            {'entry_id': '9', 'date': '2026-01-09', 'status': 'vacated', 'role': 'judgment'}]}}
        got = self.run_assess(docs, timeline)
        self.assertEqual((got['target_entry'], got['target_basis']), ('4', 'latest_unclear'))
        self.assertIn('no controlling judgment', JP.render(
            [dict(got, sale=None)], [], None, 0, date(2026, 9, 25)).split('## Every case')[1])

    def test_new_amount_page_after_purchase_is_priced(self):
        ref = _row(self.base, '9', '$1.00', 'j')                 # amount pages now 1 and 2
        _buy(self.base, ref, '9')
        path = self.base / ('amount-vision-' + hashlib.sha256(ref.encode()).hexdigest() + '.json')
        detail = json.loads(path.read_text())
        detail['selected_pages'] = [2]                            # bought before page 1 was seen
        path.write_text(json.dumps(detail))
        with mock.patch('judgment_money.verify_document', return_value=[]):
            got = self.run_assess([('9', 'final_judgment', True, [])])
        self.assertEqual((got['state'], got['pages_to_judgment']), ('needs_paid_read', 1))

    def test_failed_api_call_is_not_priced(self):
        # read_page reserved before messages.create raised; the next run refuses that page
        for reason in ('APIStatusError: overloaded', 'InternalServerError: Error code: 500',
                       'APIConnectionError: Connection error.', 'UncertainPaidCall: x'):
            ref = _row(self.base, '9', '$1.00', 'j')
            _buy(self.base, ref, '9', gaps=[{'page': 2, 'reason': reason}])
            with mock.patch('judgment_money.verify_document', return_value=[]):
                got = self.run_assess([('9', 'final_judgment', True, [])])
            self.assertEqual((got['state'], got['pages_to_judgment']), ('read_not_verified', 0), reason)

    def test_uncertain_page_does_not_drop_the_documents_other_pages(self):
        ref = _row(self.base, '9', '$1.00 $2.00', 'j')
        _buy(self.base, ref, '9', gaps=[{'page': 1, 'reason': 'UncertainPaidCall: x'},
                                        {'page': 2, 'reason': 'Amount page not read; cap or reader stop'}])
        with mock.patch('judgment_money.verify_document', return_value=[]):
            got = self.run_assess([('9', 'final_judgment', True, [])])
        self.assertEqual((got['state'], got['pages_to_judgment']), ('needs_paid_read', 1))

    def test_rejected_key_is_priced(self):
        ref = _row(self.base, '9', '$1.00', 'j')
        _buy(self.base, ref, '9', gaps=[{'page': 2, 'reason': 'AuthenticationError: Error code: 401'}])
        with mock.patch('judgment_money.verify_document', return_value=[]):
            got = self.run_assess([('9', 'final_judgment', True, [])])
        self.assertEqual((got['state'], got['pages_to_judgment']), ('needs_paid_read', 1))

    def test_stuck_page_on_the_judgment_blocks_verified(self):
        ref = _row(self.base, '9', '$1.00', 'j')
        _buy(self.base, ref, '9', gaps=[{'page': 1, 'reason': 'Vision returned unreadable'}])
        ok = [{'ok': True, 'amount': 1, 'page': 2, 'reason': '', 'pages': [], 'run': [],
               'components': [], 'credits': [], 'rates': [], 'subtotals': [], 'component_rows': []}]
        with mock.patch('judgment_money.verify_document', return_value=ok):
            got = self.run_assess([('9', 'final_judgment', True, [])],
                                  {'judgments': {'controlling_entry': '9'}})
        self.assertEqual(got['state'], 'read_not_verified')

    def test_no_timeline_is_never_verified(self):
        ref = _row(self.base, '9', '$1.00', 'j')
        _buy(self.base, ref, '9')
        ok = [{'ok': True, 'amount': 1, 'page': 2, 'reason': '', 'pages': [], 'run': [],
               'components': [], 'credits': [], 'rates': [], 'subtotals': [], 'component_rows': []}]
        with mock.patch('judgment_money.verify_document', return_value=ok):
            got = self.run_assess([('9', 'final_judgment', True, [])])
        self.assertEqual(got['state'], 'timeline_missing')

    def test_rejected_document_stops_the_whole_document(self):
        ref = _row(self.base, '9', '$1.00', 'j')
        _buy(self.base, ref, '9', gaps=[{'page': 1, 'reason': 'Stored content is not a PDF'},
                                        {'page': 2, 'reason': 'Amount page not read; cap or reader stop'}])
        with mock.patch('judgment_money.verify_document', return_value=[]):
            got = self.run_assess([('9', 'final_judgment', True, [])])
        self.assertEqual(got['state'], 'read_not_verified')

    def test_one_total_ok_another_failing_is_not_verified(self):
        ref = _row(self.base, '9', '$1.00', 'j')
        _buy(self.base, ref, '9')
        base = {'amount': 1, 'page': 2, 'reason': '', 'pages': [], 'run': [], 'components': [],
                'credits': [], 'rates': [], 'subtotals': [], 'component_rows': []}
        with mock.patch('judgment_money.verify_document',
                        return_value=[dict(base, ok=True), dict(base, ok=False)]):
            got = self.run_assess([('9', 'final_judgment', True, [])])
        self.assertEqual(got['state'], 'read_not_verified')
        self.assertIn('another does not', got['detail'])

    def test_verified_waits_for_newly_found_amount_pages(self):
        ref = _row(self.base, '9', '$1.00', 'j')
        _buy(self.base, ref, '9')
        path = self.base / ('amount-vision-' + hashlib.sha256(ref.encode()).hexdigest() + '.json')
        detail = json.loads(path.read_text())
        detail['selected_pages'] = [2]
        path.write_text(json.dumps(detail))
        ok = [{'ok': True, 'amount': 1, 'page': 2, 'reason': '', 'pages': [], 'run': [],
               'components': [], 'credits': [], 'rates': [], 'subtotals': [], 'component_rows': []}]
        with mock.patch('judgment_money.verify_document', return_value=ok):
            got = self.run_assess([('9', 'final_judgment', True, [])])
        self.assertEqual(got['state'], 'needs_paid_read')

    def test_plan_controlling_used_without_a_timeline(self):
        _row(self.base, '9', '$1.00', 'j')
        plan = _plan([('9', 'final_judgment', True, [])])
        plan['judgments']['controlling_entry'] = '9'
        with mock.patch('document_prioritizer.prioritize', return_value=plan):
            got = JP.assess(CASE, self.base, None, '2026-09-25')
        # the plan's reconciliation is docket-index metadata: it never establishes a controlling one
        self.assertEqual(got['target_basis'], 'latest_operative')
        self.assertIn('no controlling judgment established', got['detail'])

    def test_timeline_without_judgments_falls_back(self):
        _row(self.base, '9', '$1.00', 'j')
        got = self.run_assess([('9', 'final_judgment', True, [])], {'gaps': []})
        self.assertEqual((got['state'], got['target_basis']), ('needs_paid_read', 'latest_operative'))

    def test_missing_key_is_priced(self):
        ref = _row(self.base, '9', '$1.00', 'j')
        _buy(self.base, ref, '9', gaps=[{'page': 2, 'reason':
                                         'no ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN in the environment'}])
        with mock.patch('judgment_money.verify_document', return_value=[]):
            got = self.run_assess([('9', 'final_judgment', True, [])])
        self.assertEqual((got['state'], got['pages_to_judgment']), ('needs_paid_read', 1))

    def test_unreachable_pdf_with_nothing_to_ocr_is_not_marked(self):
        row = {'source_ref': 'court:9:1', 'manifest': {'sha256': 'h9', 'path': 'C:/elsewhere/x.pdf'},
               'reading': {'pages': [{'page': 1, 'outcome': 'ocr_text', 'text': 'Total $1.00'}]}}
        self.assertNotIn('_ocr_unreachable', JP._with_cached_ocr(row, self.base))

    def test_partial_ocr_cache_is_marked(self):
        pdf = self.base / 'doc.pdf'
        pdf.write_bytes(b'%PDF fake')
        digest = hashlib.sha256(b'%PDF fake').hexdigest()
        (self.base / 'timeline-ocr').mkdir()
        (self.base / 'timeline-ocr' / (digest + '-ocr300-v1.json')).write_text(json.dumps(
            {'pages': {'1': {'outcome': 'ocr_text', 'text': 'none'}}}))
        row = {'source_ref': 'court:9:1', 'manifest': {'sha256': 'h9', 'path': str(pdf)},
               'reading': {'pages': [{'page': n, 'outcome': 'text', 'text': 'x'} for n in (1, 2)]}}
        self.assertTrue(JP._with_cached_ocr(row, self.base)['_ocr_unreachable'])

    def test_stuck_read_elsewhere_does_not_blame_the_judgment(self):
        ref = _row(self.base, '9', '$1.00', 'j')
        _buy(self.base, ref, '9')
        other = _row(self.base, '5', '$2.00', 'o')
        _buy(self.base, other, '5', gaps=[{'page': 1, 'reason': 'Stored content is not a PDF'}])
        bad = [{'ok': False, 'amount': 1, 'page': 2, 'reason': 'x', 'pages': [], 'run': [],
                'components': [], 'credits': [], 'rates': [], 'subtotals': [], 'component_rows': []}]
        with mock.patch('judgment_money.verify_document', return_value=bad):
            got = self.run_assess([('5', 'order_on_motion', True, []), ('9', 'final_judgment', True, [])],
                                  {'judgments': {'controlling_entry': '9'}})
        self.assertEqual(got['state'], 'read_not_verified')
        self.assertNotIn('paying again does not', got.get('detail') or '')

    def test_timeline_gap_on_the_judgment_blocks_verified(self):
        ref = _row(self.base, '9', '$1.00', 'j')
        _buy(self.base, ref, '9')
        ok = [{'ok': True, 'amount': 1, 'page': 2, 'reason': '', 'pages': [], 'run': [],
               'components': [], 'credits': [], 'rates': [], 'subtotals': [], 'component_rows': []}]
        timeline = {'judgments': {'controlling_entry': '9'},
                    'gaps': [{'entry_id': '9', 'kind': 'missing_attachments'}]}
        with mock.patch('judgment_money.verify_document', return_value=ok):
            got = self.run_assess([('9', 'final_judgment', True, [])], timeline)
        self.assertEqual(got['state'], 'judgment_incomplete')
        with mock.patch('judgment_money.verify_document', return_value=ok):
            got = self.run_assess([('9', 'final_judgment', True, [])],
                                  {'judgments': {'controlling_entry': '9'}})
        self.assertEqual(got['state'], 'verified')

    def test_document_read_without_a_total_blocks_verified(self):
        ref1 = _row(self.base, '9', '$1.00', 'j1')
        row2 = {'source_ref': 'court:9:2', 'manifest': {'sha256': 'h9'},
                'reading': {'pages': [{'page': 1, 'text': 'Total $9.99'}]}}
        (self.base / (hashlib.sha256(b'j2').hexdigest() + '.json')).write_text(json.dumps(row2))
        _buy(self.base, ref1, '9')
        _buy(self.base, 'court:9:2', '9')
        path = self.base / ('amount-vision-' + hashlib.sha256(b'court:9:2').hexdigest() + '.json')
        detail = json.loads(path.read_text()); detail['selected_pages'] = [1]
        path.write_text(json.dumps(detail))
        ok = {'ok': True, 'amount': 1, 'page': 2, 'reason': '', 'pages': [], 'run': [],
              'components': [], 'credits': [], 'rates': [], 'subtotals': [], 'component_rows': []}
        calls = iter([[ok], []])                 # doc 1 verifies, doc 2 has no printed total
        with mock.patch('judgment_money.verify_document', side_effect=lambda *a: next(calls)):
            got = self.run_assess([('9', 'final_judgment', True, [])],
                                  {'judgments': {'controlling_entry': '9'}})
        self.assertEqual(got['state'], 'read_not_verified')

    def test_unrecognised_failure_is_not_priced(self):
        ref = _row(self.base, '9', '$1.00', 'j')
        _buy(self.base, ref, '9', gaps=[{'page': 2, 'reason': 'FileNotFoundError: no such file'}])
        with mock.patch('judgment_money.verify_document', return_value=[]):
            got = self.run_assess([('9', 'final_judgment', True, [])])
        self.assertEqual(got['state'], 'read_not_verified')

    def test_unreachable_pdf_withholds_every_verdict_but_a_price(self):
        ref = _row(self.base, '9', '$1.00', 'j')
        path = next(p for p in self.base.glob('*.json') if len(p.stem) == 64)
        row = json.loads(path.read_text()); row['manifest']['path'] = 'C:/elsewhere/doc.pdf'
        path.write_text(json.dumps(row))
        _buy(self.base, ref, '9')
        with mock.patch('judgment_money.verify_document', return_value=[]):
            got = self.run_assess([('9', 'final_judgment', True, [])])
        self.assertEqual(got['state'], 'report_on_pass_machine')

    def test_priced_judgment_without_its_ocr_is_a_floor(self):
        _row(self.base, '9', '$1.00', 'j')
        path = next(p for p in self.base.glob('*.json') if len(p.stem) == 64)
        row = json.loads(path.read_text()); row['manifest']['path'] = 'C:/elsewhere/doc.pdf'
        path.write_text(json.dumps(row))
        got = self.run_assess([('9', 'final_judgment', True, [])])
        self.assertEqual(got['state'], 'needs_paid_read')
        self.assertTrue(got['price_is_floor'])
        self.assertTrue(got['whole_case_is_floor'])

    def test_readable_pdf_without_ocr_cache_is_marked(self):
        pdf = self.base / 'doc.pdf'
        pdf.write_bytes(b'%PDF fake')
        row = {'source_ref': 'court:9:1', 'manifest': {'sha256': 'h9', 'path': str(pdf)},
               'reading': {'pages': [{'page': 1, 'outcome': 'text', 'text': 'no dollars'}]}}
        (self.base / (hashlib.sha256(b'j').hexdigest() + '.json')).write_text(json.dumps(row))
        got = self.run_assess([('9', 'final_judgment', True, [])])
        self.assertEqual(got['state'], 'report_on_pass_machine')

    def test_unrelated_filing_without_ocr_does_not_hide_a_verdict(self):
        ref = _row(self.base, '9', '$1.00', 'j')
        _buy(self.base, ref, '9')
        pdf = self.base / 'other.pdf'
        pdf.write_bytes(b'%PDF other')
        row = {'source_ref': 'court:5:1', 'manifest': {'sha256': 'h5', 'path': str(pdf)},
               'reading': {'pages': [{'page': 1, 'outcome': 'text', 'text': 'order'}]}}
        (self.base / (hashlib.sha256(b'o').hexdigest() + '.json')).write_text(json.dumps(row))
        ok = [{'ok': True, 'amount': 1, 'page': 2, 'reason': '', 'pages': [], 'run': [],
               'components': [], 'credits': [], 'rates': [], 'subtotals': [], 'component_rows': []}]
        with mock.patch('judgment_money.verify_document', return_value=ok):
            got = self.run_assess([('5', 'vacatur', True, []), ('9', 'final_judgment', True, [])],
                                  {'judgments': {'controlling_entry': '9'}})
        self.assertEqual(got['state'], 'verified')
        self.assertTrue(got['price_is_floor'])
        # the same filing read AFTER the judgment cannot raise the cost of reaching it
        with mock.patch('judgment_money.verify_document', return_value=ok):
            got = self.run_assess([('9', 'final_judgment', True, []), ('5', 'order_on_motion', True, [])],
                                  {'judgments': {'controlling_entry': '9'}})
        self.assertNotIn('price_is_floor', got)
        self.assertTrue(got['whole_case_is_floor'])      # but the whole-case count is a floor
        # a judgment the plan holds out of the order has no 'before'
        with mock.patch('judgment_money.verify_document', return_value=ok):
            got = self.run_assess([('5', 'vacatur', True, []),
                                   ('9', 'final_judgment', False, ['entry_date_unknown'])],
                                  {'judgments': {'controlling_entry': '9'}})
        self.assertNotIn('price_is_floor', got)

    def test_repeatable_failure_is_not_priced(self):
        ref = _row(self.base, '9', '$1.00', 'j')
        _buy(self.base, ref, '9', gaps=[{'page': 2, 'reason': 'Stored content is not a PDF'}])
        with mock.patch('judgment_money.verify_document', return_value=[]):
            got = self.run_assess([('9', 'final_judgment', True, [])])
        self.assertEqual(got['state'], 'read_not_verified')

    def test_timeline_older_than_docket(self):
        import os
        _row(self.base, '9', '$1.00', 'j')
        os.utime(self.base / 'inventory.json', (2000, 2000))
        with mock.patch('document_prioritizer.prioritize',
                        return_value=_plan([('9', 'final_judgment', True, [])])):
            got = JP.assess(CASE, self.base, {'judgments': {'controlling_entry': '9'}},
                            '2026-09-25', timeline_mtime=1000)
        self.assertEqual(got['state'], 'timeline_older_than_docket')

    def test_on_disk_but_held_by_plan(self):
        _row(self.base, '9', '$1.00', 'j')
        got = self.run_assess([('9', 'final_judgment', False, ['entry_date_unknown'])])
        self.assertEqual(got['state'], 'judgment_held_by_docket_plan')
        self.assertTrue(got['detail'].startswith('entry_date_unknown'))

    def test_held_judgment_read_and_failing_is_read_not_verified(self):
        ref = _row(self.base, '9', '$1.00', 'j')
        _buy(self.base, ref, '9')
        bad = [{'ok': False, 'amount': 1, 'page': 2, 'reason': 'x', 'pages': [], 'run': [],
                'components': [], 'credits': [], 'rates': [], 'subtotals': [], 'component_rows': []}]
        with mock.patch('judgment_money.verify_document', return_value=bad):
            got = self.run_assess([('9', 'final_judgment', False, ['entry_date_unknown'])],
                                  {'judgments': {'controlling_entry': '9'}})
        self.assertEqual(got['state'], 'read_not_verified')

    def test_held_judgment_with_an_unbought_document_is_never_verified(self):
        ref = _row(self.base, '9', '$1.00', 'j')
        _buy(self.base, ref, '9')
        row = {'source_ref': 'court:9:2', 'manifest': {'sha256': 'h9b'},
               'reading': {'pages': [{'page': 1, 'outcome': 'text', 'text': 'Total $9.00'}]}}
        (self.base / (hashlib.sha256(b'j2').hexdigest() + '.json')).write_text(json.dumps(row))
        ok = [{'ok': True, 'amount': 1, 'page': 2, 'reason': '', 'pages': [], 'run': [],
               'components': [], 'credits': [], 'rates': [], 'subtotals': [], 'component_rows': []}]
        with mock.patch('judgment_money.verify_document', return_value=ok):
            got = self.run_assess([('9', 'final_judgment', False, ['entry_date_unknown'])],
                                  {'judgments': {'controlling_entry': '9'}})
        self.assertEqual((got['state'], got['pages_to_judgment']), ('judgment_held_by_docket_plan', 0))

    def test_same_day_tie_takes_the_later_entry_numerically(self):
        self.assertEqual(JP._target({'judgments': [
            {'entry_id': '9', 'date': '2026-01-01', 'status': 'operative', 'role': 'judgment'},
            {'entry_id': '10', 'date': '2026-01-01', 'status': 'operative', 'role': 'replacement'}]},
            None), ('10', 'latest_operative'))

    def test_walled_reason_is_the_targets_own(self):
        _row(self.base, '9', 'no dollars here', 'j', total_page=False)
        got = self.run_assess([('4', 'final_judgment', False, ['no_image_count_established']),
                               ('9', 'final_judgment', True, [])])
        self.assertEqual(got['state'], 'judgment_without_amount_page')

    def test_pass_refuses_off_windows(self):
        with mock.patch.object(JP.os, 'name', 'posix'), \
                mock.patch.object(JP, 'load_entries', return_value=[]), \
                mock.patch('case_review.output_path', return_value=str(self.base / 'jp' / '.keep')):
            with self.assertRaises(SystemExit):
                JP.main(['--collect'])

    def test_login_walled_judgment_named(self):
        got = self.run_assess([('9', 'final_judgment', False, ['no_image_count_established'])])
        self.assertEqual(got['state'], 'judgment_not_fetched')
        self.assertIn('no_image_count_established', got['detail'])

    def test_no_judgment(self):
        self.assertEqual(self.run_assess([('3', 'motion', True, [])])['state'],
                         'no_judgment_on_docket')

    def test_cached_supplemental_ocr_is_seen(self):
        pdf = self.base / 'doc.pdf'
        pdf.write_bytes(b'%PDF fake')
        digest = hashlib.sha256(b'%PDF fake').hexdigest()
        (self.base / 'timeline-ocr').mkdir()
        (self.base / 'timeline-ocr' / (digest + '-ocr300-v1.json')).write_text(json.dumps(
            {'pages': {'1': {'outcome': 'ocr_text', 'text': 'Total $270,322.07'}}}))
        row = {'source_ref': 'court:9:1', 'manifest': {'sha256': 'h9', 'path': str(pdf)},
               'reading': {'pages': [{'page': 1, 'outcome': 'text',
                                      'text': 'no dollar sign in the text layer'}]}}
        (self.base / (hashlib.sha256(b'j').hexdigest() + '.json')).write_text(json.dumps(row))
        got = self.run_assess([('9', 'final_judgment', True, [])])
        self.assertEqual((got['state'], got['pages_to_judgment']), ('needs_paid_read', 1))


class Rate(unittest.TestCase):
    def test_average_over_billed_pages_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / 'case'
            d.mkdir()
            # three pages selected, the cap stopped after one: one billed page, not three
            (d / 'amount-vision-a.json').write_text(json.dumps(
                {'selected_pages': [1, 2, 3], 'usd': 0.03, 'pages': {'1': {'usd': 0.03}}}))
            (d / 'amount-vision-b.json').write_text(json.dumps(
                {'selected_pages': [1, 2], 'pages': {'1': {'usd': 0.01}, '2': {'usd': 0}}}))
            rate, n = JP.evidence_rate(tmp)
            self.assertEqual(n, 2)
            self.assertAlmostEqual(rate, 0.02)
            self.assertEqual(JP.evidence_rate(Path(tmp) / 'none'), (None, 0))


class Fresh(unittest.TestCase):
    def test_by_file_age_across_midnight(self):
        with tempfile.NamedTemporaryFile() as f:
            mtime = Path(f.name).stat().st_mtime
            self.assertTrue(JP.built_recently(f.name, now=mtime + 3 * 3600))
            self.assertFalse(JP.built_recently(f.name, now=mtime + 21 * 3600))
        self.assertFalse(JP.built_recently('/nonexistent/timeline.json'))


class PassError(unittest.TestCase):
    def test_error_hidden_once_the_timeline_is_rebuilt(self):
        import time
        runner = mock.Mock(COUNTY='MIAMI-DADE')
        with tempfile.TemporaryDirectory() as tmp:
            tpath = Path(tmp) / 't.json'
            tpath.write_text('{}')
            log = {'errors': {'a': 'e', 'b': 'e'},
                   'error_at': {'a': time.time() - 60, 'b': time.time() + 60}}
            with mock.patch.object(JP, 'timeline_path', return_value=tpath), \
                    mock.patch.object(JP, 'assess', side_effect=lambda *a: {'state': 'verified'}), \
                    mock.patch.object(JP, 'evidence_rate', return_value=(None, 0)), \
                    mock.patch('document_store.pipeline_folder', return_value=tmp + '/x'), \
                    mock.patch('document_store.pipeline_load', return_value={}):
                rows, _, _ = JP.plan(runner, [{'case': 'a'}, {'case': 'b'}], date(2026, 9, 25), log)
        self.assertNotIn('pass_error', rows[0])       # rebuilt after the error
        self.assertEqual(rows[1]['pass_error'], 'e')   # the error is the newer fact


class Pass(unittest.TestCase):
    def test_each_case_built_as_of_its_own_day(self):
        runner = mock.Mock(COUNTY='MIAMI-DADE')
        days = iter([date(2026, 9, 25), date(2026, 9, 26)])
        with mock.patch.object(JP, 'timeline_path', side_effect=lambda r, c: c), \
                mock.patch.object(JP, 'built_recently', return_value=False), \
                mock.patch.object(JP, 'date', mock.Mock(today=lambda: next(days))), \
                mock.patch('run_case_timeline.timeline_case') as build:
            JP.run_pass(runner, [{'case': 'a'}, {'case': 'b'}], None, False, {})
        self.assertEqual([c.args[1] for c in build.call_args_list],
                         [date(2026, 9, 25), date(2026, 9, 26)])

    def test_limit_counts_worked_cases_and_clears_old_errors(self):
        import time
        runner = mock.Mock(COUNTY='MIAMI-DADE')
        entries = [{'case': c} for c in ('a', 'b', 'c', 'd')]
        now = time.time()
        log = {'errors': {'a': 'old', 'c': 'old'},
               'built': {'a': {'at': now, 'collect': False}, 'b': {'at': now, 'collect': False}}}
        with mock.patch.object(JP, 'timeline_path', side_effect=lambda r, c: c), \
                mock.patch.object(JP, 'built_recently', side_effect=lambda c: c in ('a', 'b')), \
                mock.patch('run_case_timeline.timeline_case') as build:
            got = JP.run_pass(runner, entries, date(2026, 9, 25), False, log, limit=1)
        self.assertEqual(got, (1, 2, 0))
        self.assertEqual([c.args[0] for c in build.call_args_list], ['c'])
        self.assertEqual(log['errors'], {'a': 'old'})    # a skip is no news: the error stays
        # a failed rebuild is not skipped next time, even though its file is fresh
        with mock.patch.object(JP, 'timeline_path', side_effect=lambda r, c: c), \
                mock.patch.object(JP, 'built_recently', return_value=True), \
                mock.patch('run_case_timeline.timeline_case', side_effect=RuntimeError('x')):
            JP.run_pass(runner, entries[:1], date(2026, 9, 25), True, log)
        self.assertNotIn('a', log['built'])
        # --collect re-does a case built without it; --limit 0 works nothing
        with mock.patch.object(JP, 'timeline_path', side_effect=lambda r, c: c), \
                mock.patch.object(JP, 'built_recently', return_value=True), \
                mock.patch('run_case_timeline.timeline_case') as build:
            self.assertEqual(JP.run_pass(runner, entries[:2], date(2026, 9, 25), True, log), (2, 0, 0))
            self.assertEqual(JP.run_pass(runner, entries, date(2026, 9, 25), True, log, limit=0),
                             (0, 0, 0))


class Render(unittest.TestCase):
    def test_prices_and_windows(self):
        rows = [{'case': CASE, 'sale': '2026-09-28', 'state': 'needs_paid_read',
                 'pages_to_judgment': 3, 'pages_all': 5},
                {'case': '2025-000002-CA-01', 'sale': '2026-12-01', 'state': 'verified',
                 'pages_to_judgment': 0, 'pages_all': 0}]
        out = JP.render(rows, ['2026A00001'], 0.02, 10, date(2026, 9, 25))
        self.assertIn('| next 7 days | 1 | 3 | $0.06 | 5 | $0.10 |', out)
        rows[0]['price_is_floor'] = rows[0]['whole_case_is_floor'] = True
        self.assertIn('| next 7 days | 1 | 3+ | $0.06 or more (1 case missing free OCR) | 5+ | '
                      '$0.10 or more |', JP.render(rows, [], 0.02, 10, date(2026, 9, 25)))
        self.assertIn('| verified | 0 | 0 | 1 | 0 | 0 | 1 |', out)
        self.assertIn('(all sale dates): 1', out)


if __name__ == '__main__':
    unittest.main()
