"""Present title, chain of title, unanchored deeds and Sunbiz entity records. Synthetic; $0."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import llc_officers as LO
import miami_present_title as MPT
import miami_title_discovery as TD
import miami_title_parties as T
import sunbiz_entities as SE

FOLIO = '1234567890123'


def deed(book, date, grantor, grantee, folio=FOLIO, doc_type='WARRANTY DEED'):
    return dict(reC_BOOK=book, reC_PAGE='1', reC_DATE=date, doC_TYPE=doc_type,
                firsT_PARTY=grantor, seconD_PARTY=grantee, foliO_NUMBER=folio,
                subdiV_NAME='TEST CONDOMINIUM')


class UnanchoredDeedTests(unittest.TestCase):
    def test_deed_without_folio_is_kept_for_legal_description_matching(self):
        doc = {'source_ref': 'official_records/9-1', 'reading': {'pages': [{'page': 2, 'outcome': 'text',
               'text': 'Legal: Unit No. 4-B of TEST CONDOMINIUM, according to the Declaration thereof'}]}}
        got = T.build_title_parties([deed('9', '1/1/2021', 'A PERSON', 'B PERSON', '')], [doc], {}, FOLIO)
        self.assertIsNone(got['current_deed_candidate'])
        self.assertEqual(got['title_parties'], [])
        kept = got['unanchored_deeds'][0]
        self.assertEqual(kept['status'], 'legal_description_match_required')
        self.assertEqual({p['name'] for p in kept['parties']}, {'A PERSON', 'B PERSON'})
        self.assertEqual(kept['legal_description']['page'], 2)
        self.assertIn('Unit No. 4-B', kept['legal_description']['passage'])
        self.assertNotIn('date_parsed', kept)

    def test_deed_with_another_parcels_folio_is_kept_and_marked(self):
        got = T.build_title_parties([deed('9', '1/1/2021', 'A', 'B', '9999999999999')], [], {}, FOLIO)
        self.assertEqual(got['unanchored_deeds'][0]['status'], 'folio_conflict')
        self.assertEqual(got['unanchored_deeds'][0]['index_folio'], '9999999999999')

    def test_later_unanchored_deed_from_current_owner_questions_the_current_deed(self):
        rows = [deed('2', '1/1/2020', 'SELLER', 'OWNER PERSON'),
                deed('7', '6/1/2023', 'OWNER PERSON', 'BUYER LLC', '')]
        got = T.build_title_parties(rows, [], {'parties': [
            {'partyName': 'OWNER PERSON', 'partyTypeDesc': 'DEFENDANT'}]}, FOLIO)
        self.assertEqual(got['current_deed_candidate']['book_page'], '2/1')
        self.assertEqual(got['current_deed_status'], 'possibly_conveyed_later')
        self.assertEqual(got['possible_later_conveyances'], ['7/1'])
        self.assertEqual(got['comparison_status'], 'unknown_possible_later_conveyance')
        self.assertTrue(any('may already have conveyed' in g for g in got['gaps']))
        self.assertIn('OWNER PERSON is an anchored-deed grantee', got['unanchored_deeds'][0]['chain_link'])

    def test_earlier_unanchored_deed_does_not_question_the_current_deed(self):
        rows = [deed('2', '1/1/2020', 'SELLER', 'OWNER PERSON'),
                deed('1', '6/1/2015', 'OWNER PERSON', 'SOMEONE', '')]
        got = T.build_title_parties(rows, [], {}, FOLIO)
        self.assertEqual(got['current_deed_status'], 'candidate')
        self.assertEqual(got['possible_later_conveyances'], [])


class ChainOfTitleTests(unittest.TestCase):
    def test_continuous_and_names_differ_links(self):
        rows = [deed('1', '1/1/2010', 'FIRST', 'SECOND'),
                deed('2', '1/1/2015', 'SECOND', 'THIRD'),
                deed('3', '1/1/2020', 'SOMEBODY ELSE', 'FOURTH')]
        got = T.build_title_parties(rows, [], {}, FOLIO)
        links = [(c['from'], c['to'], c['link']) for c in got['chain_of_title']]
        self.assertEqual(links, [('1/1', '2/1', 'continuous'), ('2/1', '3/1', 'names_differ')])
        self.assertTrue(any(g.startswith('Chain of title: 3/1') for g in got['gaps']))

    def test_recorder_comma_name_still_links(self):
        rows = [deed('1', '1/1/2010', 'FIRST', 'SMITH, JOHN'), deed('2', '1/1/2015', 'JOHN SMITH', 'NEXT')]
        self.assertEqual(T.build_title_parties(rows, [], {}, FOLIO)['chain_of_title'][0]['link'], 'continuous')

    def test_certificate_of_title_is_not_compared_and_undated_deed_is_named(self):
        rows = [deed('1', '1/1/2010', 'FIRST', 'SECOND'),
                deed('2', '1/1/2015', 'CLERK OF COURT', 'BANK', doc_type='CERTIFICATE OF TITLE'),
                deed('3', '', 'BANK', 'BUYER')]
        chain = T.build_title_parties(rows, [], {}, FOLIO)['chain_of_title']
        self.assertEqual(chain[0]['link'], 'unknown_undated_deed')
        self.assertEqual(chain[1]['link'], 'court_transfer_not_compared')


SEARCH_HTML = ('<a href="/Inquiry/CorporationSearch/SearchResultDetail?x=1&amp;y=2">EXAMPLE HOLDINGS LLC</a>'
               '<a href="/Inquiry/CorporationSearch/SearchResultDetail?x=3">EXAMPLE WATERS INC</a>')
DETAIL_HTML = ('<div>Document Number <span>L19000123456</span> Date Filed <span>01/02/2019</span>'
               '<label>Status</label> <span>ACTIVE</span> Registered Agent Name &amp; Address '
               '<span>DOE, JANE</span><span>1 MAIN ST</span><span>MIAMI FL</span> '
               'Authorized Person(s) Detail Name &amp; Address Title MGR <span>ROE, RICHARD</span>'
               '<span>2 MAIN ST</span> Title MGR <span>OTHER HOLDINGS LLC</span><span>3 MAIN ST</span>'
               ' Annual Reports</div>')


class SunbizTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(SE.kind_of('TRUST HOLDINGS LLC'), 'sunbiz_entity')
        self.assertEqual(SE.kind_of('SMITH FAMILY TRUST'), 'trust_or_estate')
        self.assertEqual(SE.kind_of('ESTATE OF JANE DOE'), 'trust_or_estate')
        self.assertEqual(SE.kind_of('WELLS FARGO BANK NA'), 'institution')
        self.assertEqual(SE.kind_of('GARCIA, MARIA'), 'person')

    def test_lookup_takes_an_injected_fetch(self):
        calls = []

        def fetch(url):
            calls.append(url)
            return DETAIL_HTML if 'SearchResultDetail' in url else SEARCH_HTML
        raw = LO._lookup('EXAMPLE HOLDINGS LLC', fetch=fetch)
        self.assertFalse(raw['not_found'])
        self.assertEqual(raw['status'], 'ACTIVE')
        self.assertEqual(len(calls), 2)
        self.assertTrue(LO._lookup('NOBODY LLC', fetch=lambda u: '')['not_found'])

    def test_found_entity_carries_no_authority_and_is_never_call_ready(self):
        fetch = lambda u: DETAIL_HTML if 'SearchResultDetail' in u else SEARCH_HTML
        record = SE.resolve('EXAMPLE HOLDINGS LLC', lookup=lambda n: LO._lookup(n, fetch=fetch))
        self.assertEqual(record['lookup'], 'found')
        self.assertEqual(record['status'], 'ACTIVE')
        self.assertEqual(record['document_number'], 'L19000123456')
        self.assertEqual(record['title_authority'], 'not_established')
        self.assertEqual(record['contact_authority'], 'not_established')
        self.assertFalse(record['call_ready'])
        people = SE.people_to_research(record)
        self.assertEqual([p['name'] for p in people], ['ROE, RICHARD', 'DOE, JANE'])
        self.assertEqual(people[0]['authority'], 'listed_on_filing_not_verified_signatory')

    def test_unreachable_is_an_error_not_a_not_found(self):
        def boom(name):
            raise SE.SunbizUnreachable('curl exit 7')
        record = SE.resolve('EXAMPLE HOLDINGS LLC', lookup=boom)
        self.assertEqual(record['lookup'], 'error')
        self.assertIn('curl exit 7', record['reason'])

    def test_typo_match_is_marked_unverified(self):
        record = SE.resolve('X LLC', lookup=lambda n: {'not_found': False, 'typo': True, 'matched': 'XX LLC',
                                                         'status': 'INACTIVE', 'officers': []})
        self.assertEqual(record['match'], 'typo_match_unverified')

    def test_trust_is_not_looked_up(self):
        record = SE.resolve('SMITH FAMILY TRUST', lookup=lambda n: self.fail('looked up a trust'))
        self.assertEqual(record['lookup'], 'not_applicable')

    def test_cache_reused_when_fresh_and_requeried_when_stale(self):
        now = datetime(2026, 9, 24, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'cache.json'
            hits = []
            lookup = lambda n: hits.append(n) or {'not_found': True, 'near': ['Y LLC']}
            SE.resolve_owners(['X LLC', 'X LLC'], lookup=lookup, cache_file=path, now=now)
            self.assertEqual(hits, ['X LLC'])
            again = SE.resolve_owners(['X LLC'], lookup=lookup, cache_file=path, now=now + timedelta(days=5))
            self.assertEqual(hits, ['X LLC'])
            self.assertTrue(again[0]['cached'])
            SE.resolve_owners(['X LLC'], lookup=lookup, cache_file=path, now=now + timedelta(days=45))
            self.assertEqual(hits, ['X LLC', 'X LLC'])
            self.assertIn('X LLC', json.loads(path.read_text()))


def report(claims=(), parcel=(), deeds=None, owner='OWNER PERSON'):
    deeds = deeds or [deed('1', '1/1/2010', 'FIRST', 'OLD OWNER'), deed('2', '1/1/2020', 'OLD OWNER', 'OWNER PERSON')]
    return {'owner': owner, 'title_parties': T.build_title_parties(deeds, [], {'parties': [
                {'partyName': 'TENANT X', 'partyTypeDesc': 'DEFENDANT'}]}, FOLIO),
            'other_name_searches': [{'potential_title_party_claims': list(claims),
                                     'parcel_candidates': list(parcel), 'uncertain_parcel_candidates': []}]}


def claim(book, name, status='unknown', parcel='unknown'):
    return {'book': book, 'page_no': '5', 'doc_type': 'JUDGMENT', 'under_name': name, 'rec_date': '1/1/2018',
            'satisfaction_status': status, 'parcel_status': parcel, 'attachment_status': 'unknown'}


class PresentTitleTests(unittest.TestCase):
    def test_claims_are_labelled_by_whose_name_and_never_debt(self):
        got = MPT.present_title(report([claim('50', 'OLD OWNER'), claim('51', 'OWNER PERSON'),
                                        claim('52', 'TENANT X', status='referenced_release_found_unresolved')]))
        by = {c['book_page']: c for c in got['claims']}
        self.assertEqual(by['50/5']['party'], 'prior_title_party')
        self.assertEqual(by['51/5']['party'], 'current_grantee')
        self.assertEqual(by['52/5']['party'], 'defendant_not_on_title')
        self.assertTrue(all(c['debt'] == 'not_established' for c in got['claims']))
        self.assertTrue(all(c['parcel'] == 'name_search_only' for c in got['claims']))
        self.assertEqual(by['50/5']['satisfaction'], 'no_satisfaction_found_not_proof_open')
        self.assertEqual(by['52/5']['satisfaction'], 'referenced_release_found_unresolved')
        self.assertEqual(got['counts']['under_prior_title_party'], 1)
        self.assertEqual(got['open_debt'], 'not_established')
        self.assertEqual(got['ownership']['status'], 'candidate')
        self.assertEqual(got['ownership']['grantees'], ['OWNER PERSON'])

    def test_one_instrument_under_two_names_is_one_claim_with_the_strongest_labels(self):
        parcel_row = dict(claim('60', 'OLD OWNER'), parcel_status='matched')
        got = MPT.present_title(report([claim('60', 'OWNER PERSON')], parcel=[parcel_row]))
        self.assertEqual(len(got['claims']), 1)
        only = got['claims'][0]
        self.assertEqual(only['parcel'], 'folio_matched')
        self.assertEqual(only['party'], 'current_grantee')
        self.assertEqual(sorted(only['under_names']), ['OLD OWNER', 'OWNER PERSON'])

    def test_entity_only_owner_is_held_and_never_call_ready(self):
        deeds = [deed('1', '1/1/2010', 'FIRST', 'OLD OWNER'), deed('2', '1/1/2020', 'OLD OWNER', 'EXAMPLE HOLDINGS LLC')]
        record = SE.resolve('EXAMPLE HOLDINGS LLC', lookup=lambda n: {
            'not_found': False, 'matched': 'EXAMPLE HOLDINGS LLC', 'status': 'ACTIVE',
            'officers': [{'t': 'MGR', 'n': 'ROE, RICHARD', 'a': ''}]})
        got = MPT.present_title(report(deeds=deeds), entities=[record])
        self.assertFalse(got['call_ready'])
        self.assertEqual(got['owner_persons'], [])
        self.assertEqual(got['owner_entities'][0]['status'], 'ACTIVE')
        self.assertEqual(got['owner_entities'][0]['people_to_research'][0]['name'], 'ROE, RICHARD')
        self.assertIn('owner is an entity; no person is established as able to act for it', got['held_because'])

    def test_entity_owner_without_lookup_is_not_run_not_missing(self):
        deeds = [deed('2', '1/1/2020', 'OLD OWNER', 'EXAMPLE HOLDINGS LLC')]
        got = MPT.present_title(report(deeds=deeds))
        self.assertEqual(got['owner_entities'][0]['lookup'], 'not_run')

    def test_report_only_reads_the_cache_and_never_the_registry(self):
        deeds = [deed('2', '1/1/2020', 'OLD OWNER', 'EXAMPLE HOLDINGS LLC')]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'cache.json'
            got = TD.present_title_for(report(deeds=deeds), sunbiz=True, network=False, cache_file=path)
            self.assertEqual(got['owner_entities'][0]['lookup'], 'error')
            self.assertIn('report-only', got['owner_entities'][0]['reason'])


if __name__ == '__main__':
    unittest.main()
