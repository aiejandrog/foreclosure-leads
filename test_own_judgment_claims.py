"""The case's own recorded judgment must not come back as a claim after claim enrichment.

python -u test_own_judgment_claims.py

Search (document_walk.run_name_searches) -> enrichment (miami_claim_evidence.enrich_claims) ->
presentation (miami_present_title.present_title), on synthetic records, with a separate judgment as
the control that must still be shown. 2026-10-03 replay: the own judgment showed as a claim in 6 of 7.
"""
import unittest
from datetime import date

import document_walk as W
import miami_claim_evidence as E
import miami_present_title as PT

OWN = ('34932', '1256')
OTHER = ('30000', '1')


def judgment(bp, first):
    return {'reC_BOOK': bp[0], 'reC_PAGE': bp[1], 'doC_TYPE': 'JUDGMENT',
            'reC_DATE': '1/1/2025', 'foliO_NUMBER': '', 'firsT_PARTY': first, 'seconD_PARTY': 'OWNER'}


def doc(bp):
    return {'source_ref': 'recorded:%s-%s' % bp,
            'stored': {'record_key': {'book': bp[0], 'page': bp[1]}, 'source_sha256': 'h' + bp[0]},
            'classification': {'kind': 'final_judgment'},
            'reading': {'pages': [{'page': 1, 'text': 'x', 'outcome': 'text'}]},
            'amount_candidates': []}


class Searcher:
    def __init__(self, models):
        self.models = models

    def search(self, name):
        return self.models


class OwnJudgmentComposition(unittest.TestCase):
    def run_pipeline(self, this_case, models):
        report = W.run_name_searches([{'name': 'OWNER', 'why': 'title'}], W.RecordIndex(),
                                     Searcher(models), '3059130020010', owner_models=[],
                                     this_case=this_case)
        raw = {'OWNER': models}
        enriched = E.enrich_claims([report], raw, [doc(OWN), doc(OTHER)])
        again = E.enrich_claims(enriched, raw, [doc(OWN), doc(OTHER)])       # a re-run must not re-add
        return report, enriched[0], again[0]

    def shown(self, enriched):
        out = PT.present_title({'owner': 'OWNER', 'other_name_searches': [enriched]})
        return sorted(c['book_page'] for c in out.get('claims') or out.get('title_party_claims') or [])

    def claims_of(self, report):
        return sorted('%s/%s' % (c['book'], c['page_no']) for c in report['potential_title_party_claims'])

    def test_plaintiff_party_judgment_stays_out_through_enrichment_and_presentation(self):
        tc = {'plaintiffs': ['WILMINGTON SAVINGS FUND SOCIETY FSB'], 'book_pages': set(),
              'judgment_dates': [date(2024, 12, 20)], 'filed': date(2024, 3, 1)}
        models = [judgment(OWN, 'WILMINGTON SAVINGS FUND SOCIETY'), judgment(OTHER, 'CITY OF MIAMI')]
        searched, enriched, again = self.run_pipeline(tc, models)
        self.assertEqual(self.claims_of(searched), ['30000/1'])
        self.assertEqual(self.claims_of(enriched), ['30000/1'])        # control kept, own not re-added
        self.assertEqual(self.claims_of(again), ['30000/1'])
        self.assertEqual([o['book'] for o in enriched['own_case_instruments']], ['34932'])
        self.assertEqual(self.shown(enriched), ['30000/1'])

    def test_docket_book_page_judgment_stays_out_too(self):
        tc = W.this_case_of({'raw': {}, 'entries': [{'metadata': {'bookAndPage': '34932 / 1256'}}]})
        _, enriched, _ = self.run_pipeline(tc, [judgment(OWN, 'SOMEONE ELSE'), judgment(OTHER, 'CITY')])
        self.assertEqual(self.claims_of(enriched), ['30000/1'])
        self.assertEqual(self.shown(enriched), ['30000/1'])

    def test_a_generic_dcp_record_at_the_dockets_book_page_stays_out(self):
        # Review finding: DCP is not an encumbrance type, so it was dropped before own-case matching
        # and enrichment later added its judgment body as a claim.
        tc = W.this_case_of({'raw': {}, 'entries': [{'metadata': {'bookAndPage': '34932 / 1256'}}]})
        own = dict(judgment(OWN, 'SOMEONE ELSE'), doC_TYPE='DADE COURT PAPER - DCP')
        control = dict(judgment(OTHER, 'CITY'), doC_TYPE='DADE COURT PAPER - DCP')
        searched, enriched, again = self.run_pipeline(tc, [own, control])
        self.assertEqual([(o['book'], o['this_case']) for o in enriched['own_case_instruments']],
                         [('34932', 'docket_book_page')])          # provenance kept
        self.assertEqual(self.claims_of(enriched), ['30000/1'])    # the unrelated DCP judgment stays
        self.assertEqual(self.claims_of(again), ['30000/1'])
        self.assertEqual(self.shown(enriched), ['30000/1'])

    def test_a_generic_dcp_record_is_not_own_by_date_or_plaintiff_alone(self):
        tc = {'plaintiffs': ['WILMINGTON SAVINGS FUND SOCIETY FSB'], 'book_pages': set(),
              'judgment_dates': [date(2025, 1, 1)], 'filed': date(2024, 3, 1)}
        near = dict(judgment(OWN, 'WILMINGTON SAVINGS FUND SOCIETY'), doC_TYPE='DADE COURT PAPER - DCP')
        _, enriched, _ = self.run_pipeline(tc, [near])
        self.assertEqual(enriched['own_case_instruments'], [])
        self.assertEqual(self.claims_of(enriched), ['34932/1256'])

    def test_without_a_this_case_marker_both_are_claims(self):
        _, enriched, _ = self.run_pipeline(None, [judgment(OWN, 'WILMINGTON'), judgment(OTHER, 'CITY')])
        self.assertEqual(self.claims_of(enriched), ['30000/1', '34932/1256'])

    def test_a_saved_report_that_already_holds_the_own_claim_drops_it(self):
        tc = {'plaintiffs': ['WILMINGTON SAVINGS FUND SOCIETY FSB'], 'book_pages': set(),
              'judgment_dates': [date(2024, 12, 20)], 'filed': date(2024, 3, 1)}
        models = [judgment(OWN, 'WILMINGTON SAVINGS FUND SOCIETY'), judgment(OTHER, 'CITY OF MIAMI')]
        searched, _, _ = self.run_pipeline(tc, models)
        searched['potential_title_party_claims'].append(
            {'book': '34932', 'page_no': '1256', 'under_name': 'OWNER'})
        out = E.enrich_claims([searched], {'OWNER': models}, [doc(OWN), doc(OTHER)])[0]
        self.assertEqual(self.claims_of(out), ['30000/1'])

    def test_an_own_row_without_book_and_page_hides_nothing(self):
        report = {'searched': [{'name': 'OWNER'}], 'gaps': [],
                  'own_case_instruments': [{'own_case': True, 'book': None, 'page_no': None}],
                  'potential_title_party_claims': [{'book': '', 'page_no': '', 'under_name': 'OWNER'}]}
        out = E.enrich_claims([report], {}, [])[0]
        self.assertEqual(len(out['potential_title_party_claims']), 1)

    def test_a_row_not_marked_own_case_is_ignored(self):
        report = {'searched': [], 'gaps': [], 'potential_title_party_claims': [
            {'book': OWN[0], 'page_no': OWN[1], 'under_name': 'OWNER'}],
            'own_case_instruments': [{'book': OWN[0], 'page_no': OWN[1]}]}
        out = E.enrich_claims([report], {}, [])[0]
        self.assertEqual(len(out['potential_title_party_claims']), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
