"""A SAVED report re-marks the case's own judgment from explicit links only, with no search.

python -u test_saved_own_case.py

Synthetic records. Docket book/page and a caption that prints the case number count; date proximity,
plaintiff identity, a body citation and an unrelated instrument do not.
"""
import copy
import unittest
from datetime import date

import miami_claim_evidence as E
import miami_present_title as PT
import miami_title_discovery as T

CASE = '2099-000001-CA-01'
OWN, OTHER, CITING = ('34932', '1256'), ('30000', '1'), ('31000', '7')


def claim(bp, **kw):
    return dict({'book': bp[0], 'page_no': bp[1], 'under_name': 'OWNER', 'doc_type': 'DADE COURT PAPER - DCP',
                 'rec_date': '1/1/2025', 'attachment_status': 'unknown'}, **kw)


def doc(bp, kind, head, body=''):
    return {'source_ref': 'recorded:%s-%s' % bp,
            'stored': {'record_key': {'book': bp[0], 'page': bp[1]}, 'source_sha256': 'h' + bp[0]},
            'classification': {'kind': kind},
            'reading': {'pages': [{'page': 1, 'outcome': 'text', 'text': head + '\n' + body}]},
            'amount_candidates': []}


def report(*claims):
    return {'searched': [{'name': 'OWNER'}], 'gaps': [{'name': 'X', 'reason': 'kept'}],
            'potential_title_party_claims': list(claims), 'own_case_instruments': []}


def tc(book_pages=()):
    return {'plaintiffs': ['WILMINGTON SAVINGS FUND SOCIETY FSB'], 'book_pages': {E.key_of(*bp) for bp in book_pages},
            'judgment_dates': [date(2025, 1, 1)], 'filed': date(2024, 3, 1)}


CAPTION = 'IN THE CIRCUIT COURT OF THE ELEVENTH JUDICIAL\nCASE NO. 2099-000001-CA-01\nFINAL JUDGMENT'


class SavedOwnCase(unittest.TestCase):
    def books(self, rep, field='potential_title_party_claims'):
        return sorted('%s/%s' % (c['book'], c['page_no']) for c in rep[field])

    def run_mark(self, this_case, docs, *claims, case=CASE):
        before = report(*claims)
        out = E.mark_own_case([before], this_case, case, docs)[0]
        return before, out

    def test_docket_book_page_moves_the_claim_with_its_provenance(self):
        before, out = self.run_mark(tc([OWN]), [], claim(OWN), claim(OTHER))
        self.assertEqual(self.books(out), ['30000/1'])
        self.assertEqual([(o['book'], o['this_case'], o['own_case']) for o in out['own_case_instruments']],
                         [('34932', 'docket_book_page', True)])
        self.assertEqual(out['own_case_instruments'][0]['doc_type'], 'DADE COURT PAPER - DCP')   # claim kept whole
        self.assertEqual(out['gaps'], before['gaps'])
        self.assertEqual(self.books(before), ['30000/1', '34932/1256'])                          # input untouched

    def test_caption_case_number_on_a_judgment_marks_it(self):
        _, out = self.run_mark(tc(), [doc(OWN, 'final_judgment', CAPTION)], claim(OWN), claim(OTHER))
        self.assertEqual([o['this_case'] for o in out['own_case_instruments']], ['case_number_in_caption'])
        self.assertEqual(self.books(out), ['30000/1'])

    def test_case_number_spacing_variants_in_the_caption(self):
        for head in ('CASE NO: 2099 000001 CA 01\nFINAL JUDGMENT', 'Case No. 2099-0000001-CA-01\nFINAL JUDGMENT',
                     'CASE NO.: 2099/000001\nFINAL JUDGMENT'):
            with self.subTest(head=head):
                _, out = self.run_mark(tc(), [doc(OWN, 'final_judgment', head)], claim(OWN))
                self.assertEqual(self.books(out, 'own_case_instruments'), ['34932/1256'])

    def test_date_proximity_and_plaintiff_alone_never_mark(self):
        d = doc(OWN, 'final_judgment', 'IN THE CIRCUIT COURT\nCASE NO. 2098-555555-CA-01\nFINAL JUDGMENT',
                'WILMINGTON SAVINGS FUND SOCIETY FSB v. OWNER recorded 1/1/2025')
        _, out = self.run_mark(tc(), [d], claim(OWN, other_party='WILMINGTON SAVINGS FUND SOCIETY'))
        self.assertEqual(out['own_case_instruments'], [])
        self.assertEqual(self.books(out), ['34932/1256'])

    def test_a_body_citation_of_the_case_number_does_not_mark(self):
        d = doc(CITING, 'final_judgment', 'IN THE COUNTY COURT\nCASE NO. 2090-000009-CC-05\nFINAL JUDGMENT',
                'Plaintiff also holds the foreclosure in case 2099-000001-CA-01 against the same owner.')
        _, out = self.run_mark(tc(), [d], claim(CITING))
        self.assertEqual(self.books(out), ['31000/7'])

    def test_a_mortgage_that_prints_the_number_is_not_a_judgment_of_this_case(self):
        d = doc(OWN, 'mortgage', CAPTION)
        _, out = self.run_mark(tc(), [d], claim(OWN))
        self.assertEqual(self.books(out), ['34932/1256'])

    def test_nothing_is_marked_without_a_case_or_docket_marker(self):
        _, out = self.run_mark(None, [doc(OWN, 'final_judgment', CAPTION)], claim(OWN), case=None)
        self.assertEqual(self.books(out), ['34932/1256'])

    def test_a_claim_without_book_and_page_is_untouched(self):
        keyless = claim(('', ''))
        _, out = self.run_mark(tc([OWN]), [], keyless, claim(OWN))
        self.assertEqual(len(out['potential_title_party_claims']), 1)

    def test_running_twice_does_not_duplicate(self):
        once = E.mark_own_case([report(claim(OWN), claim(OTHER))], tc([OWN]), CASE, [])
        twice = E.mark_own_case(once, tc([OWN]), CASE, [])
        self.assertEqual(self.books(twice[0], 'own_case_instruments'), ['34932/1256'])
        self.assertEqual(self.books(twice[0]), ['30000/1'])

    def test_enrichment_after_marking_does_not_add_it_back_and_presentation_shows_the_control(self):
        raw = {'OWNER': [{'reC_BOOK': OWN[0], 'reC_PAGE': OWN[1], 'doC_TYPE': 'DADE COURT PAPER - DCP'},
                         {'reC_BOOK': OTHER[0], 'reC_PAGE': OTHER[1], 'doC_TYPE': 'DADE COURT PAPER - DCP'}]}
        docs = [doc(OWN, 'final_judgment', CAPTION), doc(OTHER, 'final_judgment', 'CASE NO. 2090-000002-CA-01\nFINAL JUDGMENT')]
        marked = E.mark_own_case([report(claim(OWN), claim(OTHER))], tc(), CASE, docs)
        enriched = E.enrich_claims(marked, raw, docs)[0]
        self.assertEqual(self.books(enriched), ['30000/1'])
        out = PT.present_title({'owner': 'OWNER', 'other_name_searches': [enriched]})
        self.assertEqual(sorted(c['book_page'] for c in out.get('claims') or out.get('title_party_claims') or []), ['30000/1'])

    def test_the_refresh_path_marks_when_given_the_docket_and_leaves_it_alone_when_not(self):
        saved = {'case': CASE, 'owner': 'OWNER', 'folio': '1', 'title_parties': {'defendants': [], 'gaps': []},
                 'gaps': [], 'private_search_results': {'OWNER': [{'reC_BOOK': OWN[0], 'reC_PAGE': OWN[1],
                                                                    'doC_TYPE': 'DADE COURT PAPER - DCP'}]},
                 'other_name_searches': [report(claim(OWN), claim(OTHER))]}
        keep = copy.deepcopy(saved)
        without = T.refresh_saved_report(saved, [], [])
        marked = T.refresh_saved_report(saved, [], [], this_case=tc([OWN]))
        self.assertEqual(sorted(c['book'] for c in without['other_name_searches'][0]['potential_title_party_claims']),
                         ['30000', '34932'])
        self.assertEqual([c['book'] for c in marked['other_name_searches'][0]['potential_title_party_claims']], ['30000'])
        self.assertEqual(saved, keep)                                                  # input not mutated


if __name__ == '__main__':
    unittest.main(verbosity=2)
