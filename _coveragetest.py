"""Priority 5: every expected attachment has a named state, and a restricted court filing can
only ever be joined by a public recorded copy, never marked read. Synthetic; no network."""
import unittest

import document_coverage as COV

CASE = '2099-000001-CA-01'


def inv_entry(n, text, count, status='enumerated', gap=None, when='03/01/2026'):
    e = {'source_id': str(n), 'expected_documents': count, 'inventory_status': status,
         'metadata': {'eventID': n, 'eventDate': when, 'docketDescrition': text}}
    if gap:
        e['gap'] = gap
    return e


def court_row(entry, doc, pages=None, status=None, gap=None, page_count=None):
    row = {'source_ref': 'court:%s:%s' % (entry, doc), 'entry_ref': str(entry),
           'manifest': {'pages': page_count or len(pages or [])} if pages is not None else {},
           'reading': {'pages': pages or []}}
    if status:
        row['acquisition_status'] = status
        row['acquisition_gap'] = gap
    return row


def page(n, outcome='text', text='x'):
    return {'page': n, 'outcome': outcome, 'text': text}


class CoverageTests(unittest.TestCase):
    def test_every_expected_attachment_has_a_named_state(self):
        inventory = {'entries': [
            inv_entry(1, 'Complaint', 2),
            inv_entry(2, 'Final Judgment', 1),
            inv_entry(3, 'Notice of hearing', 0, status='county_reports_no_document'),
            inv_entry(4, 'Order', 1, status='gap', gap='County login required'),
            inv_entry(5, 'Motion', 3),
            inv_entry(6, 'Affidavit', 1),
        ]}
        rows = [court_row(1, 'a', [page(1), page(2)]),
                court_row(1, 'b', [page(1), page(2, 'unreadable_source')]),
                court_row(2, 'c', status='gap', gap='Court returned non-PDF document content'),
                court_row(5, 'd', status='pending'),
                court_row(5, 'e', status='failed', gap='ConnectionError'),
                court_row(6, 'f', [], page_count=None)]
        rows[-1]['manifest'] = {'pages': 2}
        got = COV.coverage(inventory, rows, case=CASE)
        states = sorted((a['entry_id'], a['state']) for a in got['attachments'])
        self.assertEqual(states, [('1', 'read'), ('1', 'read_partial'), ('2', 'access_gap'),
                                  ('3', 'county_no_document'), ('4', 'restricted'),
                                  ('5', 'failed'), ('5', 'not_enumerated'), ('5', 'queued'),
                                  ('6', 'fetched_unread')])
        self.assertEqual(got['expected'], 8)
        self.assertEqual(got['read'], 1)
        self.assertFalse(got['complete'])

    def test_a_restricted_judgment_gets_a_public_copy_candidate_and_stays_a_gap(self):
        inventory = {'entries': [inv_entry(2, 'Final Judgment of Foreclosure', 1, status='gap',
                                           gap='County login required', when='03/01/2026')]}
        entries = [{'entry_id': '2', 'date': '2026-03-01', 'kind': 'final_judgment'}]
        judgment = {'pages': [page(1, text='FINAL JUDGMENT OF FORECLOSURE Case No. %s' % CASE)]}
        other = {'pages': [page(1, text='FINAL JUDGMENT Case No. 2010-044444-CA-01')]}
        recorded = [
            {'source_ref': 'official_records/100-7', 'reading': judgment,
             'recorded_date': '03/05/2026', 'kind': 'final_judgment'},
            {'source_ref': 'official_records/100-1', 'reading': other,
             'recorded_date': '03/04/2026', 'kind': 'final_judgment'},
            {'source_ref': 'official_records/100-9', 'reading': judgment,
             'recorded_date': '03/06/2026', 'kind': 'satisfaction_of_mortgage'},
        ]
        got = COV.coverage(inventory, [], entries, recorded, CASE)
        [row] = got['attachments']
        self.assertEqual(row['state'], 'restricted')                   # still a gap
        self.assertEqual(row['alternate_copy'], 'official_records/100-7')
        self.assertEqual(got['alternate_copies'][0]['status'], 'same_instrument_unverified')
        self.assertEqual(got['read'], 0)
        self.assertFalse(got['complete'])

    def test_two_candidate_copies_are_not_chosen_between(self):
        inventory = {'entries': [inv_entry(2, 'Final Judgment', 1, status='gap',
                                           gap='County login required')]}
        entries = [{'entry_id': '2', 'date': '2026-03-01', 'kind': 'final_judgment'}]
        judgment = {'pages': [page(1, text='FINAL JUDGMENT Case No. %s' % CASE)]}
        recorded = [{'source_ref': 'official_records/100-%d' % n, 'reading': judgment,
                     'recorded_date': '03/0%d/2026' % n, 'kind': 'final_judgment'} for n in (3, 4)]
        got = COV.coverage(inventory, [], entries, recorded, CASE)
        self.assertNotIn('alternate_copy', got['attachments'][0])
        self.assertEqual(got['alternate_copies'][0]['status'], 'ambiguous')

    def test_a_copy_recorded_long_before_the_entry_is_not_offered(self):
        inventory = {'entries': [inv_entry(2, 'Final Judgment', 1, status='gap',
                                           gap='County login required')]}
        entries = [{'entry_id': '2', 'date': '2026-03-01', 'kind': 'final_judgment'}]
        judgment = {'pages': [page(1, text='FINAL JUDGMENT Case No. %s' % CASE)]}
        recorded = [{'source_ref': 'official_records/100-3', 'reading': judgment,
                     'recorded_date': '01/03/2026', 'kind': 'final_judgment'}]
        self.assertEqual(COV.coverage(inventory, [], entries, recorded, CASE)['alternate_copies'], [])

    def test_a_missing_public_copy_is_named_with_the_book_and_page_the_docket_cites(self):
        entry = inv_entry(2, 'Final Judgment', 1, status='gap', gap='County login required')
        entry['metadata']['comments'] = 'RECORDED IN OR BK 33456 PG 1201'
        entries = [{'entry_id': '2', 'date': '2026-03-01', 'kind': 'final_judgment'}]
        got = COV.coverage({'entries': [entry]}, [], entries, [], CASE)
        self.assertEqual(got['alternate_copy_needed'][0]['cited_book_page'], ['33456-1201'])

    def test_everything_read_is_complete(self):
        inventory = {'entries': [inv_entry(1, 'Complaint', 1)]}
        got = COV.coverage(inventory, [court_row(1, 'a', [page(1)])], case=CASE)
        self.assertTrue(got['complete'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
