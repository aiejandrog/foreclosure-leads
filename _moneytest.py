"""Priority 2 of the Miami automation goal: one exact-cents check for every amount path.

python -u _moneytest.py

All synthetic. No homeowner data, no network, no API: the vision tests use a stub reader.
"""
import json
import sys
import unittest
from unittest.mock import patch

import document_interpreter as DI
import document_vision as DV
import judgment_money as JM
import miami_claim_evidence as MCE
import miami_judgment as MJ

# A Florida mortgage foreclosure judgment on a text layer. The table starts on page 2 and totals
# on page 3; page 1 recites the note's original amount, which is not part of the table.
PAGE1 = '\n'.join([
    'IN THE CIRCUIT COURT OF THE 11TH JUDICIAL CIRCUIT IN AND FOR MIAMI-DADE COUNTY, FLORIDA',
    'CASE NO. 2099-000001-CA-01',
    'The mortgage recorded in Book 12345 Page 678 secures a note in the original amount of $200,000.00',
])
PAGE2 = '\n'.join([
    '1. Amounts Due. Plaintiff is due:',
    'Principal due on the note secured by the mortgage foreclosed: $150,000.00',
    'Interest on the note and mortgage from 01/01/2024 to 06/01/2026: $18,250.00',
    'Per diem interest: $20.55',
    'Escrow advances (taxes and insurance): $6,410.22',
    'Late charges: $512.40',
    'Property inspections: $150.00',
    'Less: Escrow balance: ($1,200.00)',
    'Less: Unapplied funds: -$300.00',
])
PAGE3 = '\n'.join([
    "Attorney's fees:",
    "Attorney's fees based on 10 hours at $300.00 per hour: $3,000.00",
    'Court costs:',
    'Filing fee: $980.00',
    'Service of process: $120.00',
    'Title search: $250.00',
    'Total court costs: $1,350.00',
    'GRAND TOTAL: $178,172.62',
    'The grand total shall bear interest at the rate of 8.37% a year.',
])


def text_reading(p1=PAGE1, p2=PAGE2, p3=PAGE3, source='embedded'):
    outcome = 'text' if source == 'embedded' else 'ocr_text'
    return {'pages': [{'page': n, 'outcome': outcome, 'text': t, 'text_source': source}
                      for n, t in ((1, p1), (2, p2), (3, p3))]}


def grand(reading):
    return [c for c in MJ.judgment_amount_candidates(reading) if c['amount'] == 178172.62][0]


class TextTableTests(unittest.TestCase):
    def test_a_table_across_a_page_break_verifies_with_credits_rates_and_a_subtotal(self):
        found = grand(text_reading(source='ocr'))
        self.assertTrue(found['sum_check'], found['sum_check_reason'])
        self.assertEqual(found['sum_check_run'], 'continued_from_page_2')
        self.assertEqual(found['sum_check_pages'], [2, 3])
        # Credits subtract and are named.
        self.assertEqual(sorted(c['amount'] for c in found['sum_check_credits']), [-1200.0, -300.0])
        # Rates are kept, never summed: the per diem and the hourly rate.
        self.assertEqual(sorted(r['value'] for r in found['sum_check_rates']), [20.55, 300.0])
        self.assertNotIn(20.55, found['sum_check_components'])
        # The printed subtotal agrees with exactly the three rows above it, and is not counted twice.
        [subtotal] = found['sum_check_subtotals']
        self.assertEqual(subtotal['amount'], 1350.0)
        self.assertEqual(len(subtotal['members']), 3)
        self.assertEqual(subtotal['membership'], 'rows_above')
        self.assertNotIn(1350.0, found['sum_check_components'])
        # The recital on page 1 is not part of the table.
        self.assertNotIn(200000.0, found['sum_check_components'])

    def test_one_misread_digit_anywhere_in_the_table_fails(self):
        for good, bad in (('$6,410.22', '$6,410.23'), ('$980.00', '$930.00'),
                          ('($1,200.00)', '($1,280.00)')):
            reading = text_reading(p2=PAGE2.replace(good, bad), p3=PAGE3.replace(good, bad),
                                   source='ocr')
            self.assertFalse(grand(reading)['sum_check'], bad)

    def test_a_credit_read_as_a_charge_fails(self):
        # OCR that loses the parentheses AND the "Less" turns a credit into a charge; the
        # arithmetic notices.
        reading = text_reading(p2=PAGE2.replace('Less: Escrow balance: ($1,200.00)',
                                                'Adjustment: $1,200.00'), source='ocr')
        self.assertFalse(grand(reading)['sum_check'])

    def test_a_credit_label_survives_lost_parentheses(self):
        reading = text_reading(p2=PAGE2.replace('($1,200.00)', '$1,200.00'), source='ocr')
        self.assertTrue(grand(reading)['sum_check'])

    def test_a_subtotal_that_disagrees_with_its_rows_fails_the_total(self):
        reading = text_reading(p3=PAGE3.replace('Total court costs: $1,350.00',
                                                'Total court costs: $1,450.00'), source='ocr')
        self.assertFalse(grand(reading)['sum_check'])

    def test_no_tolerance(self):
        reading = text_reading(p3=PAGE3.replace('$178,172.62', '$178,172.63'), source='ocr')
        found = [c for c in MJ.judgment_amount_candidates(reading) if c['amount'] == 178172.63]
        self.assertFalse(found[0]['sum_check'])

    def test_an_unread_page_in_the_middle_breaks_the_run(self):
        reading = text_reading(source='ocr')
        reading['pages'][1] = {'page': 2, 'outcome': 'needs_ocr', 'text': '', 'text_source': None}
        self.assertFalse(grand(reading)['sum_check'])

    def test_a_read_page_with_no_figures_does_not_break_the_run(self):
        reading = text_reading(source='ocr')
        reading['pages'].insert(2, {'page': 3, 'outcome': 'ocr_text', 'text_source': 'ocr',
                                    'text': 'Attorney\'s fees and costs are set out below.'})
        reading['pages'][3] = dict(reading['pages'][3], page=4)
        found = grand(reading)
        self.assertTrue(found['sum_check'], found['sum_check_reason'])
        self.assertEqual(found['sum_check_pages'], [2, 4])

    def test_a_one_page_table_still_verifies(self):
        reading = {'pages': [{'page': 1, 'outcome': 'text', 'text_source': 'embedded',
                              'text': PAGE2 + '\n' + PAGE3}]}
        found = grand(reading)
        self.assertTrue(found['sum_check'])
        self.assertEqual(found['sum_check_run'], 'same_page')

    def test_a_single_line_total_labelled_like_a_subtotal_counts_once(self):
        # "Total attorney's fees" printed as a line item with no rows under it.
        page = '\n'.join(['Principal: $1,000.00', "Total attorney's fees: $500.00",
                          'GRAND TOTAL: $1,500.00'])
        found = MJ.judgment_amount_candidates({'pages': [
            {'page': 1, 'outcome': 'ocr_text', 'text': page, 'text_source': 'ocr'}]})
        self.assertTrue(found[0]['sum_check'])
        self.assertEqual(sorted(found[0]['sum_check_components']), [500.0, 1000.0])

    def test_a_charge_equal_to_a_per_diem_needs_review(self):
        page = '\n'.join(['Per diem interest: $20.55', 'Principal: $1,000.00', 'Costs: $20.55',
                          'GRAND TOTAL: $1,020.55'])
        found = MJ.judgment_amount_candidates({'pages': [
            {'page': 1, 'outcome': 'ocr_text', 'text': page, 'text_source': 'ocr'}]})
        self.assertFalse(found[0]['sum_check'])
        self.assertIn('rate', found[0]['sum_check_reason'])

    def test_a_zero_sum_block_before_the_table_is_ambiguous(self):
        page = '\n'.join(['Deposit: $500.00', 'Less: deposit applied: ($500.00)',
                          'Principal: $1,000.00', 'GRAND TOTAL: $1,000.00'])
        found = MJ.judgment_amount_candidates({'pages': [
            {'page': 1, 'outcome': 'ocr_text', 'text': page, 'text_source': 'ocr'}]})
        self.assertFalse(found[0]['sum_check'])
        self.assertIn('ambiguous', found[0]['sum_check_reason'])

    def test_zero_amount_rows_do_not_make_it_ambiguous(self):
        page = '\n'.join(['Late charges: $0.00', 'Principal: $1,000.00', 'Costs: $25.00',
                          'GRAND TOTAL: $1,025.00'])
        found = MJ.judgment_amount_candidates({'pages': [
            {'page': 1, 'outcome': 'ocr_text', 'text': page, 'text_source': 'ocr'}]})
        self.assertTrue(found[0]['sum_check'], found[0]['sum_check_reason'])

    def test_a_sentence_that_states_parts_and_total(self):
        page = ('the principal sum of $3,941.07, court costs in the amount of $379.85, for a total '
                'sum of $4,320.92, for which let execution issue')
        found = MJ.judgment_amount_candidates({'pages': [
            {'page': 1, 'outcome': 'ocr_text', 'text': page, 'text_source': 'ocr'}]})
        total = [c for c in found if c['amount'] == 4320.92][0]
        self.assertTrue(total['sum_check'], total['sum_check_reason'])
        self.assertEqual(sorted(total['sum_check_components']), [379.85, 3941.07])


# ---- vision --------------------------------------------------------------------------------------

def row(i, kind, amount, label='', members=None):
    out = {'id': i, 'kind': kind, 'label': label or i, 'amount': amount, 'confident': True}
    if members is not None:
        out['item_ids'] = members
    return out


class StubReader:
    """Stands in for DV.VisionReader: one canned reply per page, a price per page, no network."""
    dpi = 140

    def __init__(self, replies):
        self.replies = replies
        self.read = []

    def read_page(self, png, budget):
        page = int(png.decode().split(':')[1])
        budget.check(1000, 1000)
        budget.record(1000, 1000)
        self.read.append(page)
        reply = self.replies[page]
        return {'rows': reply['rows'], 'grand_total': reply.get('grand_total'), 'unreadable': [],
                'usd': 0.03}


def fake_render(path, page_no, dpi=None, out_dir=None):
    return ('page:%d' % page_no).encode()


VISION_P2 = {'rows': [row('1', 'charge', 150000.00, 'Principal'),
                      row('2', 'charge', 18250.00, 'Interest'),
                      row('3', 'rate', 20.55, 'Per diem'),
                      row('4', 'charge', -1200.00, 'Less escrow balance')]}
VISION_P3 = {'rows': [row('1', 'charge', 980.00, 'Filing fee'),
                      row('2', 'charge', 120.00, 'Service'),
                      row('3', 'subtotal', 1100.00, 'Total costs', ['1', '2']),
                      row('4', 'total', 168150.00, 'GRAND TOTAL')],
             'grand_total': 168150.00}
OCR_FOR_VISION = {'pages': [
    {'page': 1, 'outcome': 'ocr_text', 'text_source': 'ocr', 'text': 'CASE NO. 2099-000001-CA-01'},
    {'page': 2, 'outcome': 'ocr_text', 'text_source': 'ocr', 'text': 'Principal $150,000.00'},
    {'page': 3, 'outcome': 'ocr_text', 'text_source': 'ocr', 'text': 'GRAND TOTAL:\n$ 168,15O.00'},
]}


class VisionTableTests(unittest.TestCase):
    def run_vision(self, replies, reading=OCR_FOR_VISION, cap=1.0):
        reader = StubReader(replies)
        with patch.object(DV, 'render_page', fake_render):
            found, detail = MJ.vision_candidates('unused.pdf', reading, DI.Budget(cap),
                                                 reader=reader)
        return found, detail, reader

    def test_the_page_before_is_bought_once_and_the_table_verifies_across_the_break(self):
        found, detail, reader = self.run_vision({2: VISION_P2, 3: VISION_P3})
        self.assertEqual(reader.read, [3, 2])
        self.assertEqual(detail['continuation_pages'], [2])
        self.assertTrue(found[0]['sum_check'], found[0]['sum_check_reason'])
        self.assertEqual(found[0]['sum_check_run'], 'continued_from_page_2')
        self.assertEqual([c['amount'] for c in found[0]['sum_check_credits']], [-1200.0])
        self.assertEqual([r['value'] for r in found[0]['sum_check_rates']], [20.55])
        self.assertEqual(found[0]['sum_check_subtotals'][0]['members'], ['p3:1', 'p3:2'])

    def test_a_page_that_verifies_alone_buys_nothing_more(self):
        alone = {'rows': VISION_P3['rows'][:3] + [row('4', 'total', 1100.00, 'TOTAL')],
                 'grand_total': 1100.00}
        found, detail, reader = self.run_vision({3: alone})
        self.assertEqual(reader.read, [3])
        self.assertTrue(found[0]['sum_check'])

    def test_no_money_on_the_page_before_means_no_purchase(self):
        reading = {'pages': [dict(p, text='no figures here') if p['page'] == 2 else p
                             for p in OCR_FOR_VISION['pages']]}
        found, detail, reader = self.run_vision({2: VISION_P2, 3: VISION_P3}, reading)
        self.assertEqual(reader.read, [3])
        self.assertFalse(found[0]['sum_check'])

    def test_a_cap_that_stops_the_continuation_read_leaves_it_unverified(self):
        # Worst case per stub page is $0.03; a cap of $0.04 buys page 3 and refuses page 2.
        found, detail, reader = self.run_vision({2: VISION_P2, 3: VISION_P3}, cap=0.04)
        self.assertEqual(reader.read, [3])
        self.assertFalse(found[0]['sum_check'])
        self.assertTrue(detail['errors'])

    def test_a_subtotal_continued_from_the_page_before(self):
        p2 = {'rows': [row('1', 'charge', 1000.00, 'Principal'),
                       row('2', 'charge', 50.00, 'Filing fee')]}
        p3 = {'rows': [row('1', 'charge', 25.00, 'Service'),
                       row('2', 'subtotal', 75.00, 'Total costs', ['1']),
                       row('3', 'total', 1075.00, 'TOTAL')], 'grand_total': 1075.00}
        found, _, _ = self.run_vision({2: p2, 3: p3})
        self.assertTrue(found[0]['sum_check'], found[0]['sum_check_reason'])
        [subtotal] = found[0]['sum_check_subtotals']
        self.assertEqual(subtotal['members'], ['p2:2', 'p3:1'])
        self.assertEqual(subtotal['membership'], 'explicit_continued_from_page_2')

    def test_every_row_on_the_totals_page_counts(self):
        # A misread total that equals its last line must not verify by skipping the rows above.
        rows = [row('1', 'charge', 30.00), row('2', 'charge', 5.00), row('3', 'total', 5.00)]
        checks = JM.verify_document([dict(r, page=3) for r in rows],
                                    [{'page': 3, 'amount': 5.00}], {3})
        self.assertFalse(checks[0]['ok'])

    def test_an_unread_page_is_not_crossed(self):
        figures = ([dict(row('1', 'charge', 100.00), page=1)] +
                   [dict(r, page=3) for r in (row('1', 'charge', 50.00),
                                              row('2', 'total', 150.00))])
        self.assertFalse(JM.verify_document(figures, [{'page': 3, 'amount': 150.00}],
                                            {1, 3})[0]['ok'])
        self.assertTrue(JM.verify_document(figures, [{'page': 3, 'amount': 150.00}],
                                           {1, 2, 3})[0]['ok'])

    def test_parentheses_keep_a_credit_negative(self):
        parsed = DV._parse(json.dumps({'rows': [{'id': 'a', 'kind': 'charge', 'label': 'Less',
                                                 'amount': '($1,200.00)', 'confident': True}]}))
        self.assertEqual(parsed['rows'][0]['amount'], -1200.00)

    def test_saved_readings_are_rechecked_across_pages(self):
        figures = ([dict(r, page=2) for r in VISION_P2['rows']] +
                   [dict(r, page=3) for r in VISION_P3['rows']])
        detail = {'source_ref': 'official_records/1-1', 'figures': figures,
                  'grand_totals': [{'page': 3, 'amount': 168150.00}],
                  'pages': {'2': {'rows': []}, '3': {'rows': []}}, 'errors': {}}
        [found] = MCE.saved_vision_candidates(detail, 'official_records/1-1')
        self.assertTrue(found['sum_check'])
        self.assertEqual(found['sum_check_pages'], [2, 3])
        # The same saved reading with page 2 missing does not verify.
        only3 = dict(detail, figures=[f for f in figures if f['page'] == 3],
                     pages={'3': {'rows': []}})
        self.assertEqual(MCE.saved_vision_candidates(only3, 'official_records/1-1'), [])


class TimelineCheckTests(unittest.TestCase):
    def test_timeline_figures_in_a_verified_table_are_marked(self):
        import tempfile
        import run_case_timeline as RCT
        figures = ([dict(r, page=2) for r in VISION_P2['rows']] +
                   [dict(r, page=3) for r in VISION_P3['rows']])
        detail = {'figures': figures, 'grand_totals': [{'page': 3, 'amount': 168150.00}],
                  'pages': {2: {}, 3: {}}, 'errors': {}, 'gaps': []}
        rows = [{'source_ref': 'court:7:x', 'entry_ref': '7', 'path': 'x.pdf',
                 'manifest': {'source_sha256': 'h', 'document_key': 'k'},
                 'reading': {'pages': [{'page': 2, 'text': '$1.00'}, {'page': 3, 'text': '$1.00'}]}}]
        with tempfile.TemporaryDirectory() as base, \
                patch('miami_timeline_amounts.assess_amount_pages', return_value=dict(detail)), \
                patch('document_prioritizer.timeline_read_order',
                      return_value={'order': rows, 'deferred': []}):
            out = RCT.read_amounts(rows, base, budget=DI.Budget(1.0), plan={'entries': []})
        [check] = out['amount_checks']
        self.assertTrue(check['ok'], check['reason'])
        status = {f['label']: f['verification_status'] for f in out['figures']}
        self.assertEqual(status['Principal'], 'in_verified_table')
        self.assertEqual(status['Per diem'], 'unverified')        # a rate is not in the sum
        self.assertTrue(all(f['interpretation'].endswith('not an accepted judgment or equity input')
                            for f in out['figures']))


class ReplayTests(unittest.TestCase):
    """replay_money_check over synthetic saved evidence: no client, no network, $0."""

    def test_saved_text_and_saved_vision_are_rechecked_without_transcription(self):
        import json as _json
        import tempfile
        from pathlib import Path
        import requests
        import paths as P
        import document_store as DS
        import replay_money_check as RM
        case = '2099-000001-CA-01'
        with tempfile.TemporaryDirectory() as folder, patch.object(P, 'DEALFLOW_DIR', folder):
            key = '7' * 64
            cdir = DS.case_dir('MIAMI-DADE', case)
            cdir.mkdir(parents=True, exist_ok=True)
            manifest = {'county': 'MIAMI-DADE', 'case': case, 'document_key': key,
                        'source_ref': 'official_records/100-7', 'pages': 3,
                        'path': str(Path(folder) / 'x.pdf')}
            (cdir / (key[:16] + '.json')).write_text(_json.dumps(manifest))
            DS.save_page_text(manifest, dict(text_reading(source='ocr'), read_status='read'))
            figures = ([dict(r, page=2) for r in VISION_P2['rows']] +
                       [dict(r, page=3) for r in VISION_P3['rows']])
            (cdir / (key[:16] + '-text') / 'vision.json').write_text(_json.dumps(
                {'source_ref': 'official_records/100-7', 'usd': 0.06, 'errors': {},
                 'pages': {'2': {}, '3': {}}, 'figures': figures,
                 'grand_totals': [{'page': 3, 'amount': 168150.00}]}))
            with patch.object(DV.VisionReader, 'client', side_effect=AssertionError('no client')), \
                    patch.object(requests.Session, 'request',
                                 side_effect=AssertionError('no network')):
                RM.main(['--all', '--grep', 'escrow'])
            [report] = list((Path(folder) / 'reports').glob('money-check-replay-*.json'))
            result = _json.loads(report.read_text())
        [doc] = result['cases'][0]['documents']
        [text_total] = [t for t in doc['text_totals'] if t['amount'] == 178172.62]
        self.assertTrue(text_total['ok'])
        self.assertEqual(text_total['pages'], [2, 3])
        [vision_total] = doc['vision_totals']
        self.assertTrue(vision_total['ok'])
        self.assertEqual(vision_total['run'], 'continued_from_page_2')
        self.assertEqual(result['api_requests'], 0)
        self.assertEqual(result['summary']['across_pages'], 2)


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    unittest.main(verbosity=2)
