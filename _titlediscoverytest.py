"""Synthetic integration contracts for the opt-in Miami title investigation."""
import unittest
import miami_title_discovery as T


class DossierTests(unittest.TestCase):
    def test_shared_run_does_not_assign_another_cases_captcha_gap(self):
        result = T.case_search_gaps({'owner':'SECOND', 'other_name_searches':[]},
                  [{'name':'FIRST','reason':'blocked'}, {'name':'SECOND','reason':'blocked'}])
        self.assertEqual(result, [{'name':'SECOND','reason':'blocked'}])

    def test_saved_reconciliation_keeps_owner_baseline_distinct_from_other_names(self):
        report = {'case':'2099-000001-CA-01', 'owner':'TEST OWNER', 'folio':'123',
                  'title_parties':{'defendants':[], 'gaps':[]}, 'gaps':[],
                  'private_search_results':{'TEST OWNER':[{'reC_BOOK':1,'reC_PAGE':2}],
                                            'PRIOR OWNER':[{'reC_BOOK':3,'reC_PAGE':4}]}}
        seeds = [{'reC_BOOK':3,'reC_PAGE':4,'doC_TYPE':'MORTGAGE'}]
        result = T.refresh_saved_report(report, [], seeds)
        self.assertEqual(result['stored_instruments_absent_from_owner_query'][0]['book'], 3)
        self.assertNotIn('reconciled_at', report)

    def test_owner_and_docket_names_still_searched_when_deed_missing(self):
        plan = T.discovery_names({'owner':'TEST LLC'}, {'search_names':[],
             'defendants':[{'name':'JANE TEST', 'source_ref':'party/1'}]})
        self.assertEqual([p['name'] for p in plan], ['TEST LLC', 'JANE TEST'])

    def test_release_must_explicitly_cite_claim_and_never_resolves_attachment(self):
        claims = [{'book':'12', 'page_no':'34', 'attachment_status':'unknown'}]
        release = {'source_ref':'r', 'classification':{'kind':'satisfaction'},
                   'cited_instruments':[{'book':'12', 'page_no':'34', 'cited_on_page':2,
                                         'passage':'Satisfies book 12 page 34'}]}
        result = T.reconcile_claims(claims, [release])
        self.assertEqual(result[0]['satisfaction_status'], 'referenced_release_found_unresolved')
        self.assertEqual(result[0]['attachment_status'], 'unknown')
        self.assertEqual(T.reconcile_claims(claims, [])[0]['satisfaction_status'], 'unknown')

    def test_stored_deed_seed_keeps_recording_identity_without_inventing_folio(self):
        manifest = {'record_key': {'book': 123, 'page': 4, 'cfn_master_id': 9,
                    'doc_type':'DEED - DEE', 'rec_date':'1/2/2020'}, 'pages':2}
        result = T.record_from_manifest(manifest)
        self.assertEqual(result['reC_BOOK'], 123)
        self.assertEqual(result['doC_TYPE'], 'DEED - DEE')
        self.assertNotIn('foliO_NUMBER', result)

    def test_capture_search_reuses_successful_result_but_not_failure(self):
        class Source:
            count = 0
            def search(self, name):
                self.count += 1
                return [] if name == 'TEST LLC' else None
        source = Source()
        search = T.CapturedSearch(source)
        search.search('TEST LLC')
        search.search('TEST LLC')
        search.search('UNKNOWN')
        self.assertEqual(source.count, 2)
        self.assertEqual(search.results, {'TEST LLC': []})

    def test_additive_report_keeps_equity_picture_unchanged(self):
        old = {'case': '2026-000001-CA-01', 'd_picture': {'value': 123},
               'complete': True, 'open_gaps': []}
        result = T.attach_report(old, {'status': 'unknown', 'gaps': ['deed missing']})
        self.assertEqual(result['d_picture'], {'value': 123})
        self.assertEqual(old['open_gaps'], [])
        self.assertFalse(result['complete'])
        self.assertIn('title discovery: deed missing', result['open_gaps'])

    def test_missing_baseline_is_not_proof_of_owner_search_miss(self):
        self.assertEqual(T.missed_records([{'reC_BOOK': '1', 'reC_PAGE': '2'}], None), [])

    def test_same_instrument_is_not_new_just_because_name_differs(self):
        baseline = [{'reC_BOOK': '001', 'reC_PAGE': '002'}]
        found = baseline + [{'reC_BOOK': '1', 'reC_PAGE': '3'}]
        self.assertEqual(T.missed_records(found, baseline), [found[1]])

    def test_case_input_rejects_path_and_non_miami_shapes(self):
        for value in ('../bad', '50-2026-CA-1234', ''):
            with self.assertRaises(ValueError):
                T.validate_case(value)
        self.assertEqual(T.validate_case('2026-000001-CC-26'), '2026-000001-CC-26')


class NameDedupeTests(unittest.TestCase):
    def test_one_person_in_two_spellings_is_searched_once(self):
        # Greptile on #50: two spellings would spend two paid searches.
        names = [c['name'] for c in T.discovery_names(
            {'owner': 'JOHN SMITH'}, {'defendants': [{'name': 'SMITH, JOHN'},
                                                     {'name': 'SMITH, JOHN JR'}]})]
        self.assertEqual(names, ['JOHN SMITH', 'SMITH, JOHN JR'])

    def test_entities_with_the_same_words_stay_apart(self):
        names = [c['name'] for c in T.discovery_names(
            {'owner': 'ALPHA, BETA LLC'}, {'defendants': [{'name': 'BETA LLC ALPHA'}]})]
        self.assertEqual(names, ['ALPHA, BETA LLC', 'BETA LLC ALPHA'])


    def test_a_capped_or_parcel_less_search_says_so(self):
        # 12-case verification defect 3: 2025-018660 hit the 500 cap, 2024-009959 searched a
        # different person, 2025-023462's round limit left names unsearched.
        folio = '3059130020010'
        big = [{'foliO_NUMBER': '0101000000010'}] * 500
        got = T.search_coverage([], [{'searched': [{'name': 'X', 'records': 500}]}], big, folio, ['Y'])
        self.assertEqual(got, {'search_capped': True, 'parcel_found': False,
                               'names_left_unsearched': ['Y']})
        got = T.search_coverage([{'foliO_NUMBER': '30-5913-002-0010'}], [], [{'foliO_NUMBER': '30-5913-002-0010'}],
                                folio, [])
        self.assertEqual((got['search_capped'], got['parcel_found']), (False, True))


class OwnCaseTests(unittest.TestCase):
    """12-case verification defect 7: 2024-014878's own vacated judgment 34932/1256 was a claim."""

    class Searcher:
        def __init__(self, models):
            self.models = models

        def search(self, name):
            return self.models

    def search(self, models, this_case):
        import document_walk as W
        return W.run_name_searches([{'name': 'OWNER', 'why': 'title'}], W.RecordIndex(), self.Searcher(models),
                                   '3059130020010', owner_models=[], this_case=this_case)

    def judgment(self, book, page, first, second='OWNER'):
        return {'reC_BOOK': book, 'reC_PAGE': page, 'doC_TYPE': 'JUDGMENT', 'reC_DATE': '1/1/2025',
                'foliO_NUMBER': '', 'firsT_PARTY': first, 'seconD_PARTY': second}

    def test_this_cases_judgment_is_marked_own_case_not_a_claim(self):
        this_case = {'plaintiffs': ['WILMINGTON SAVINGS FUND SOCIETY FSB'], 'book_pages': set()}
        out = self.search([self.judgment('34932', '1256', 'WILMINGTON SAVINGS FUND SOCIETY'),
                        self.judgment('30000', '1', 'CITY OF MIAMI')], this_case)
        self.assertEqual([(c['book'], c['page_no']) for c in out['potential_title_party_claims']],
                         [('30000', '1')])
        own = out['own_case_instruments']
        self.assertEqual([(o['book'], o['own_case'], o['this_case']) for o in own],
                         [('34932', True, 'plaintiff_party')])

    def test_the_dockets_own_book_and_page_is_own_case(self):
        import document_walk as W
        tc = W.this_case_of({'raw': {}, 'entries': [{'metadata': {'bookAndPage': '34932 / 1256'}}]})
        out = self.search([self.judgment('34932', '1256', 'SOMEONE ELSE')], tc)
        self.assertEqual(out['own_case_instruments'][0]['this_case'], 'docket_book_page')
        self.assertEqual(out['potential_title_party_claims'], [])

    def test_a_different_lender_sharing_only_generic_words_is_still_a_claim(self):
        this_case = {'plaintiffs': ['U S BANK NATIONAL ASSOCIATION'], 'book_pages': set()}
        out = self.search([self.judgment('30000', '2', 'PNC BANK NATIONAL ASSOCIATION')], this_case)
        self.assertEqual(len(out['potential_title_party_claims']), 1)
        self.assertEqual(out['own_case_instruments'], [])


if __name__ == '__main__':
    unittest.main()
