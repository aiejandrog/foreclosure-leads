"""The citation walk, the record index, and the non-name search probe.

SYNTHETIC FIXTURES ONLY. No homeowner data, no network, no clerk. Most of these are negative
tests, because the failure this code exists to prevent is a confident empty answer: a citation
silently dropped, a capability assumed rather than observed, or a walked document quietly moving
the equity verdict.

Run:  python _walktest.py
"""
import json
import os
import sys
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix='dealflow-walktest-')
os.environ['DEALFLOW_DIR'] = _TMP
os.environ.pop('ONEDRIVE', None)
os.environ.pop('OneDrive', None)

import case_dossier as CD               # noqa: E402
import document_classify                # noqa: E402
import document_walk as W               # noqa: E402
import records_probe as RP              # noqa: E402


def model(book, page, cfn='CFN1', doc_type='MORTGAGE', folio='', subdiv='', first='', second=''):
    return {'reC_BOOK': book, 'reC_PAGE': page, 'cfN_MASTER_ID': cfn, 'booK_TYPE': 'O',
            'doC_TYPE': doc_type, 'doC_PAGES': 3, 'foliO_NUMBER': folio, 'subdiV_NAME': subdiv,
            'reC_DATE': '5/14/2019', 'firsT_PARTY': first, 'seconD_PARTY': second}


def reading(lines):
    return {'pages': [{'page': i + 1, 'text': line, 'chars': len(line), 'outcome': 'text',
                       'text_source': 'embedded'} for i, line in enumerate(lines)],
            'page_count': len(lines), 'pages_with_text': len(lines), 'pages_from_ocr': 0,
            'pages_unresolved': [], 'read_status': 'read', 'complete': True}


def row_citing(source_ref, lines):
    """A collect_recorded row that has been through classify_documents."""
    row = {'source_ref': source_ref, 'status': 'stored', 'reading': reading(lines)}
    CD.classify_documents([row])
    return row


class RecordIndexTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(_TMP, 'idx-%s.json' % self.id().rsplit('.', 1)[-1])

    def test_leading_zeros_do_not_make_two_instruments(self):
        index = W.RecordIndex(self.path)
        index.add_models([model('35287', '4642')])
        self.assertIsNotNone(index.get('035287', '04642'))
        self.assertIsNotNone(index.get('35287', '4642'))

    def test_a_row_with_no_cfn_is_not_indexed(self):
        # Indexing it would make resolve() claim a hit the collector then refuses, which reads as
        # a transport fault instead of the missing key it is.
        index = W.RecordIndex(self.path)
        self.assertEqual(index.add_models([model('1', '2', cfn='')]), 0)
        self.assertIsNone(index.get('1', '2'))

    def test_the_index_survives_a_restart(self):
        index = W.RecordIndex(self.path)
        index.add_models([model('900', '11')])
        self.assertTrue(index.save())
        self.assertIsNotNone(W.RecordIndex(self.path).get('900', '11'))

    def test_a_corrupt_index_file_is_an_empty_index_not_a_crash(self):
        with open(self.path, 'w', encoding='utf-8') as fh:
            fh.write('{not json')
        self.assertEqual(W.RecordIndex(self.path).rows, {})


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.index = W.RecordIndex(os.path.join(_TMP, 'resolve.json'))
        self.caps = os.path.join(_TMP, 'caps-none.json')

    def test_a_known_instrument_resolves_from_the_index_for_free(self):
        self.index.add_models([model('35287', '4642')])
        out = W.resolve('35287', '4642', self.index, caps_path=self.caps)
        self.assertEqual(out['via'], 'index')
        self.assertEqual(out['record']['cfN_MASTER_ID'], 'CFN1')

    def test_an_unknown_instrument_with_no_probe_says_so_in_words(self):
        out = W.resolve('1', '1', self.index, caps_path=self.caps)
        self.assertNotIn('record', out)
        self.assertEqual(out['via'], 'unresolved')
        self.assertIn('records_probe has never run', out['reason'])

    def test_an_unconfirmed_capability_is_never_used(self):
        # A probe that RAN and confirmed nothing must not enable the search path. 'accepted but
        # returned nothing' is exactly what an ignored parameter looks like.
        path = os.path.join(_TMP, 'caps-ran.json')
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump({'probed_at': '2026-09-22T00:00:00Z',
                       'capabilities': {'book_page': {'confirmed': False,
                                                      'best_outcome': 'accepted_no_hits'}}}, fh)
        called = []
        out = W.resolve('1', '1', self.index, caps_path=path,
                        searcher=lambda *a: called.append(a))
        self.assertEqual(called, [])
        self.assertEqual(out['via'], 'unresolved')
        self.assertNotIn('never run', out['reason'])

    def test_a_confirmed_capability_searches_and_indexes_what_it_finds(self):
        path = os.path.join(_TMP, 'caps-ok.json')
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump({'probed_at': '2026-09-22T00:00:00Z',
                       'capabilities': {'book_page': {'confirmed': True,
                                                      'shape': {'searchtype': 'Book/Page'}}}}, fh)
        out = W.resolve('777', '88', self.index, caps_path=path,
                        searcher=lambda b, p, shape: [model(b, p, cfn='CFN777')])
        self.assertEqual(out['via'], 'search')
        self.assertEqual(out['record']['cfN_MASTER_ID'], 'CFN777')
        # and the find is kept, so the next case resolves it without paying again
        self.assertIsNotNone(self.index.get('777', '88'))

    def test_a_search_that_throws_is_a_named_gap_not_an_exception(self):
        path = os.path.join(_TMP, 'caps-ok2.json')
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump({'probed_at': 'x', 'capabilities': {'book_page': {'confirmed': True,
                                                                        'shape': {}}}}, fh)

        def boom(*_a):
            raise RuntimeError('cloudflare said no')

        out = W.resolve('5', '5', self.index, caps_path=path, searcher=boom)
        self.assertEqual(out['via'], 'unresolved')
        self.assertIn('cloudflare said no', out['reason'])

    def test_a_search_returning_the_wrong_instrument_is_not_a_resolution(self):
        path = os.path.join(_TMP, 'caps-ok3.json')
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump({'probed_at': 'x', 'capabilities': {'book_page': {'confirmed': True,
                                                                        'shape': {}}}}, fh)
        out = W.resolve('5', '6', self.index, caps_path=path,
                        searcher=lambda *_a: [model('999', '999')])
        self.assertNotIn('record', out)
        self.assertIn('no such instrument', out['reason'])


CITER = ['SATISFACTION OF MORTGAGE',
         'the mortgage recorded in O.R.B. 28001 at Page 1234 is hereby satisfied',
         'and the mortgage recorded in Official Records Book 29500, Page 77 remains']


class WalkTest(unittest.TestCase):
    """The walk itself, with a fake collect_recorded so nothing touches a clerk."""

    def setUp(self):
        self.index = W.RecordIndex(os.path.join(_TMP, 'walk-%s.json'
                                                % self.id().rsplit('.', 1)[-1]))
        self.caps = os.path.join(_TMP, 'caps-absent.json')
        self.fetched = []
        import miami_judgment
        self._real = miami_judgment.collect_recorded
        miami_judgment.collect_recorded = self._fake

    def tearDown(self):
        import miami_judgment
        miami_judgment.collect_recorded = self._real

    def _fake(self, case, records, **kw):
        out = []
        for record in records:
            self.fetched.append((record['reC_BOOK'], record['reC_PAGE']))
            ref = 'official_records/%s-%s' % (record['reC_BOOK'], record['reC_PAGE'])
            out.append({'source_ref': ref, 'status': 'stored',
                        'doc_type': record.get('doC_TYPE'),
                        'reading': reading(['MORTGAGE', 'nothing is cited on this page'])})
        return out

    def test_a_cited_instrument_in_the_index_is_fetched(self):
        self.index.add_models([model('28001', '1234'), model('29500', '77')])
        rows = [row_citing('official_records/35287-4642', CITER)]
        new, report = W.walk('C1', rows, index=self.index, caps_path=self.caps)
        self.assertEqual(sorted(self.fetched), [('28001', '1234'), ('29500', '77')])
        self.assertEqual(len(new), 2)
        self.assertEqual(report['documents_fetched'], 2)
        self.assertEqual(report['unresolved'], [])

    def test_provenance_travels_with_the_walked_document(self):
        self.index.add_models([model('28001', '1234'), model('29500', '77')])
        rows = [row_citing('official_records/35287-4642', CITER)]
        new, _ = W.walk('C1', rows, index=self.index, caps_path=self.caps)
        walked = new[0]['walked']
        self.assertEqual(walked['cited_by'], 'official_records/35287-4642')
        self.assertEqual(walked['hop'], 1)
        self.assertIn('O.R.B. 28001', walked['passage'])

    def test_the_document_does_not_follow_its_own_recording_stamp(self):
        # A recorded instrument's first page carries its OWN book and page. Following that would
        # re-fetch the document we are standing on, once per hop, forever.
        self.index.add_models([model('35287', '4642')])
        rows = [row_citing('official_records/35287-4642',
                           ['BOOK 35287 PAGE 4642', 'FINAL JUDGMENT'])]
        _new, report = W.walk('C1', rows, index=self.index, caps_path=self.caps)
        self.assertEqual(self.fetched, [])
        self.assertEqual(report['documents_fetched'], 0)

    def test_an_unresolvable_citation_is_reported_never_dropped(self):
        rows = [row_citing('official_records/35287-4642', CITER)]
        _new, report = W.walk('C1', rows, index=self.index, caps_path=self.caps)
        self.assertEqual(self.fetched, [])
        self.assertEqual(len(report['unresolved']), 2)
        self.assertTrue(all(r['reason'] for r in report['unresolved']))

    def test_the_budget_stops_the_walk_and_names_what_it_did_not_reach(self):
        self.index.add_models([model('28001', '1234'), model('29500', '77')])
        rows = [row_citing('official_records/35287-4642', CITER)]
        _new, report = W.walk('C1', rows, index=self.index, caps_path=self.caps, budget=1)
        self.assertEqual(report['documents_fetched'], 1)
        self.assertIn('budget', report['stopped_because'])
        self.assertEqual([u['via'] for u in report['unresolved']], ['not_attempted'])

    def test_depth_one_does_not_follow_what_the_walked_document_cites(self):
        self.index.add_models([model('28001', '1234'), model('29500', '77'),
                               model('31000', '5')])

        def citing_fake(case, records, **kw):
            out = []
            for record in records:
                self.fetched.append((record['reC_BOOK'], record['reC_PAGE']))
                out.append({'source_ref': 'official_records/%s-%s'
                                          % (record['reC_BOOK'], record['reC_PAGE']),
                            'status': 'stored',
                            'reading': reading(['ASSIGNMENT',
                                                'assigns the mortgage in BOOK 31000 PAGE 5'])})
            return out

        import miami_judgment
        miami_judgment.collect_recorded = citing_fake
        rows = [row_citing('official_records/35287-4642', CITER)]
        _new, report = W.walk('C1', rows, index=self.index, caps_path=self.caps, depth=1)
        self.assertNotIn(('31000', '5'), self.fetched)
        self.assertEqual(report['depth'], 1)

    def test_depth_two_follows_the_second_hop(self):
        self.index.add_models([model('28001', '1234'), model('29500', '77'),
                               model('31000', '5')])

        def citing_fake(case, records, **kw):
            out = []
            for record in records:
                self.fetched.append((record['reC_BOOK'], record['reC_PAGE']))
                text = (['ASSIGNMENT', 'assigns the mortgage in BOOK 31000 PAGE 5']
                        if record['reC_BOOK'] != '31000' else ['MORTGAGE', 'no citations here'])
                out.append({'source_ref': 'official_records/%s-%s'
                                          % (record['reC_BOOK'], record['reC_PAGE']),
                            'status': 'stored', 'reading': reading(text)})
            return out

        import miami_judgment
        miami_judgment.collect_recorded = citing_fake
        rows = [row_citing('official_records/35287-4642', CITER)]
        _new, report = W.walk('C1', rows, index=self.index, caps_path=self.caps, depth=2)
        self.assertIn(('31000', '5'), self.fetched)
        self.assertEqual([f['hop'] for f in report['followed'] if f['book'] == '31000'], [2])

    def test_the_same_instrument_is_never_fetched_twice_in_one_walk(self):
        self.index.add_models([model('28001', '1234'), model('29500', '77')])
        rows = [row_citing('official_records/35287-4642', CITER),
                row_citing('official_records/35287-4700', CITER)]
        _new, _report = W.walk('C1', rows, index=self.index, caps_path=self.caps)
        self.assertEqual(sorted(self.fetched), [('28001', '1234'), ('29500', '77')])


DEEDS = [
    model('20000', '1', cfn='D1', doc_type='WARRANTY DEED', folio='3059130020010',
          first='ROSALES MARIA', second='SISAVATH KHAM'),
    model('20500', '9', cfn='D2', doc_type='QUIT CLAIM DEED', subdiv='GARDEN LAKE TOWERS',
          first='SISAVATH KHAM', second='KHAM FAMILY TRUST'),
    model('21000', '3', cfn='D3', doc_type='MORTGAGE', folio='3059130020010',
          first='SISAVATH KHAM', second='WELLS FARGO BANK NA'),
]


class PartyCandidateTest(unittest.TestCase):
    def test_the_prior_owner_on_the_deed_is_offered(self):
        names = [c['name'] for c in W.party_candidates(DEEDS, '3059130020010',
                                                       subdivision='GARDEN LAKE TOWERS')]
        self.assertIn('ROSALES MARIA', names)

    def test_a_lender_is_never_offered_as_a_name_to_search(self):
        names = [c['name'] for c in W.party_candidates(DEEDS, '3059130020010')]
        self.assertNotIn('WELLS FARGO BANK NA', names)

    def test_only_deeds_contribute_names(self):
        # A mortgage's parties are borrower and lender. The borrower is already the owner we
        # searched, and the lender is noise; it is the DEED that names somebody new.
        refs = {c['source_ref'] for c in W.party_candidates(DEEDS, '3059130020010',
                                                            subdivision='GARDEN LAKE TOWERS')}
        self.assertNotIn('official_records/21000-3', refs)

    def test_the_current_owner_is_not_offered_back_to_itself(self):
        names = [c['name'] for c in W.party_candidates(DEEDS, '3059130020010',
                                                       subdivision='GARDEN LAKE TOWERS',
                                                       known=['SISAVATH KHAM'])]
        self.assertNotIn('SISAVATH KHAM', names)

    def test_the_folio_deed_outranks_the_subdivision_deed(self):
        out = W.party_candidates(DEEDS, '3059130020010', subdivision='GARDEN LAKE TOWERS')
        self.assertIn('folio', out[0]['why'])

    def test_the_plaintiff_on_the_docket_is_dropped_by_role(self):
        docket = {'parties': [{'partyName': 'PALM VILLAS CONDOMINIUM ASSN',
                               'partyTypeDesc': 'PLAINTIFF'},
                              {'partyName': 'ORTEGA LUIS', 'partyTypeDesc': 'DEFENDANT'}]}
        names = [c['name'] for c in W.party_candidates([], '', docket=docket)]
        self.assertEqual(names, ['ORTEGA LUIS'])

    def test_the_plan_spends_nothing_unless_a_limit_is_given(self):
        plan = W.name_search_plan(DEEDS, '3059130020010', subdivision='GARDEN LAKE TOWERS',
                                  owner='SISAVATH KHAM')
        self.assertEqual(plan['planned'], [])
        self.assertTrue(plan['candidates'])
        self.assertEqual(plan['skipped'], len(plan['candidates']))


class FakeSearcher:
    """A NameSearcher stand-in. `answers` maps a name to models, or to None for "not reached"."""

    def __init__(self, answers):
        self.answers = answers
        self.asked = []
        self.spent_free = self.spent_paid = 0

    def search(self, name):
        self.asked.append(name)
        answer = self.answers.get(name, [])
        if isinstance(answer, Exception):
            raise answer
        return answer


PRIOR_OWNER_MORTGAGE = model('27000', '500', cfn='M1', doc_type='MORTGAGE',
                             folio='3059130020010', second='COUNTRYWIDE HOME LOANS')
ELSEWHERE = model('27100', '10', cfn='M2', doc_type='MORTGAGE', folio='9999999999999',
                  second='SOME OTHER BANK')
RELEASED = model('27200', '20', cfn='M3', doc_type='SATISFACTION OF MORTGAGE',
                 folio='3059130020010')


class NameSearchTest(unittest.TestCase):
    def setUp(self):
        self.index = W.RecordIndex(os.path.join(_TMP, 'names-%s.json'
                                                % self.id().rsplit('.', 1)[-1]))

    def plan(self):
        return [{'name': 'ROSALES MARIA', 'why': 'deed on the subject folio (grantor)',
                 'source_ref': 'official_records/20000-1'}]

    def test_a_prior_owners_mortgage_on_this_parcel_is_reported(self):
        searcher = FakeSearcher({'ROSALES MARIA': [PRIOR_OWNER_MORTGAGE]})
        out = W.run_name_searches(self.plan(), self.index, searcher, '3059130020010')
        found = out['found_under_other_names']
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['under_name'], 'ROSALES MARIA')
        self.assertEqual(found[0]['anchored_by'], 'folio')

    def test_a_mortgage_on_a_different_property_is_not_reported(self):
        searcher = FakeSearcher({'ROSALES MARIA': [ELSEWHERE]})
        out = W.run_name_searches(self.plan(), self.index, searcher, '3059130020010')
        self.assertEqual(out['found_under_other_names'], [])

    def test_a_satisfaction_is_not_an_encumbrance(self):
        searcher = FakeSearcher({'ROSALES MARIA': [RELEASED]})
        out = W.run_name_searches(self.plan(), self.index, searcher, '3059130020010')
        self.assertEqual(out['found_under_other_names'], [])

    def test_an_unreachable_county_is_never_reported_as_a_clean_name(self):
        # An empty search that prints like a clean title check is the most dangerous output this
        # repo can produce. None means "not reached" and must say so.
        searcher = FakeSearcher({'ROSALES MARIA': None})
        out = W.run_name_searches(self.plan(), self.index, searcher, '3059130020010')
        self.assertEqual(out['searched'][0]['outcome'], 'not_reached')
        self.assertEqual(out['found_under_other_names'], [])

    def test_a_throwing_search_is_recorded_not_raised(self):
        searcher = FakeSearcher({'ROSALES MARIA': RuntimeError('turnstile down')})
        out = W.run_name_searches(self.plan(), self.index, searcher, '3059130020010')
        self.assertEqual(out['searched'][0]['outcome'], 'error')
        self.assertIn('turnstile down', out['searched'][0]['reason'])

    def test_what_a_name_search_returns_is_indexed_for_the_citation_pass(self):
        searcher = FakeSearcher({'ROSALES MARIA': [PRIOR_OWNER_MORTGAGE]})
        W.run_name_searches(self.plan(), self.index, searcher, '3059130020010')
        self.assertIsNotNone(self.index.get('27000', '500'))

    def test_subdivision_anchors_a_record_that_carries_no_folio(self):
        no_folio = model('27300', '7', cfn='M4', doc_type='MORTGAGE',
                         subdiv='GARDEN LAKE TOWERS')
        searcher = FakeSearcher({'ROSALES MARIA': [no_folio]})
        out = W.run_name_searches(self.plan(), self.index, searcher, '3059130020010',
                                  subdivision='GARDEN LAKE TOWERS')
        self.assertEqual(out['found_under_other_names'][0]['anchored_by'], 'subdivision')

    def test_no_plan_means_no_search_is_run(self):
        searcher = FakeSearcher({})
        out = W.run_name_searches([], self.index, searcher, '3059130020010')
        self.assertEqual(searcher.asked, [])
        self.assertEqual(out['searched'], [])


class WalkWithNamesTest(unittest.TestCase):
    """Names run BEFORE the citation pass, so what they index can resolve a citation this run."""

    def setUp(self):
        # Per-test file: the index PERSISTS on save, so a shared path would let one test's
        # name-search result resolve the next test's citation and hide the very thing it asserts.
        self.index = W.RecordIndex(os.path.join(_TMP, 'walknames-%s.json'
                                                % self.id().rsplit('.', 1)[-1]))
        self.caps = os.path.join(_TMP, 'caps-absent.json')
        self.fetched = []
        import miami_judgment
        self._real = miami_judgment.collect_recorded
        miami_judgment.collect_recorded = self._fake

    def tearDown(self):
        import miami_judgment
        miami_judgment.collect_recorded = self._real

    def _fake(self, case, records, **kw):
        out = []
        for record in records:
            self.fetched.append((record['reC_BOOK'], record['reC_PAGE']))
            out.append({'source_ref': 'official_records/%s-%s'
                                      % (record['reC_BOOK'], record['reC_PAGE']),
                        'status': 'stored', 'reading': reading(['MORTGAGE', 'no citations'])})
        return out

    def test_a_name_search_makes_a_cited_instrument_addressable_this_run(self):
        rows = [row_citing('official_records/35287-4642',
                           ['SATISFACTION', 'the mortgage in O.R.B. 28001 at Page 1234'])]
        searcher = FakeSearcher({'ROSALES MARIA': [model('28001', '1234', cfn='FOUND')]})
        _new, report = W.walk('C1', rows, index=self.index, caps_path=self.caps,
                              name_plan=[{'name': 'ROSALES MARIA', 'why': 'grantor',
                                          'source_ref': 'x'}],
                              name_searcher=searcher, folio='3059130020010')
        self.assertEqual(self.fetched, [('28001', '1234')])
        self.assertEqual(report['unresolved'], [])
        self.assertEqual(searcher.asked, ['ROSALES MARIA'])

    def test_without_a_searcher_the_same_citation_stays_unresolved(self):
        rows = [row_citing('official_records/35287-4642',
                           ['SATISFACTION', 'the mortgage in O.R.B. 28001 at Page 1234'])]
        _new, report = W.walk('C1', rows, index=self.index, caps_path=self.caps)
        self.assertEqual(self.fetched, [])
        self.assertEqual(len(report['unresolved']), 1)


class StoredRowsTest(unittest.TestCase):
    """Reading back what another tool already fetched and read."""

    def setUp(self):
        import document_store as DS
        self.DS = DS
        self.case = 'STORE-%s' % self.id().rsplit('.', 1)[-1]
        self.folder = DS.case_dir('MIAMI-DADE', self.case)
        self.folder.mkdir(parents=True, exist_ok=True)

    def _store(self, key, pages, read_status='read', with_text=True):
        manifest = {'county': 'MIAMI-DADE', 'case': self.case, 'document_key': key,
                    'source_ref': 'official_records/35287-4642', 'doc_name': 'DADE COURT PAPER',
                    'pages': len(pages), 'path': str(self.folder / (key[:16] + '.pdf'))}
        self.DS._atomic_write_text(self.folder / (key[:16] + '.json'),
                                   json.dumps(manifest) + '\n')
        if with_text:
            reading = {'pages': [{'page': i + 1, 'text': t, 'chars': len(t), 'outcome': 'ocr_text',
                                  'text_source': 'ocr'} for i, t in enumerate(pages)],
                       'read_status': read_status, 'reader_version': 3}
            self.DS.save_page_text(manifest, reading)
        return manifest

    def test_a_document_read_by_another_tool_yields_its_citations(self):
        self._store('a' * 40, CITER)
        rows = W.stored_rows('MIAMI-DADE', self.case)
        self.assertEqual(len(rows), 1)
        cites = {(c['book'], c['page_no']) for c in rows[0]['cited_instruments']}
        self.assertEqual(cites, {('28001', '1234'), ('29500', '77')})

    def test_a_stored_but_unread_document_is_reported_not_skipped(self):
        # "fetched and unread" and "not here" are different states and must not collapse.
        self._store('b' * 40, ['x', 'y'], with_text=False)
        rows = W.stored_rows('MIAMI-DADE', self.case)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['read_status'], 'not_read_back')
        self.assertEqual(rows[0]['cited_instruments'], [])

    def test_a_case_with_nothing_stored_is_empty_not_an_error(self):
        self.assertEqual(W.stored_rows('MIAMI-DADE', 'NEVER-SEEN-CASE'), [])

    def test_a_superseded_copy_is_not_read_back_twice(self):
        manifest = self._store('c' * 40, CITER)
        manifest['document_key'] = 'd' * 40
        manifest['superseded'] = True
        self.DS._atomic_write_text(self.folder / ('d' * 16 + '.json'),
                                   json.dumps(manifest) + '\n')
        self.assertEqual(len(W.stored_rows('MIAMI-DADE', self.case)), 1)


class OffListCaseTest(unittest.TestCase):
    """A named case that was dropped from the live lead file."""

    def test_a_case_only_on_the_lp_board_is_still_selectable(self):
        import run_documents as RD
        rows = RD._lead_rows(only='NOPE-000')
        self.assertIsInstance(rows, list)

    def test_pick_cases_still_filters_to_miami_and_a_named_owner(self):
        import run_documents as RD
        leads = [{'Case #': 'X', 'owner_clean': 'A B', 'county': 'BROWARD'},
                 {'Case #': 'Y', 'owner_clean': 'A B'}]
        self.assertEqual([p['case'] for p in RD.pick_cases(leads, {}, 0)], ['Y'])


class ProbeVerdictTest(unittest.TestCase):
    def test_no_probe_file_means_no_capability(self):
        ok, shape = RP.confirmed('book_page', os.path.join(_TMP, 'nope.json'))
        self.assertFalse(ok)
        self.assertEqual(shape, {})

    def test_accepted_with_no_hits_is_not_a_capability(self):
        caps = RP._capabilities([{'capability': 'book_page', 'outcome': 'accepted_no_hits',
                                  'subject_returned': False, 'label': 'x'}])
        self.assertFalse(caps['book_page']['confirmed'])

    def test_a_capability_needs_the_subject_back(self):
        caps = RP._capabilities([{'capability': 'book_page', 'outcome': 'accepted_with_hits',
                                  'subject_returned': True, 'params': {'searchtype': 'Book/Page'},
                                  'label': 'x'}])
        self.assertTrue(caps['book_page']['confirmed'])

    def test_one_confirmed_shape_is_not_undone_by_a_later_failing_one(self):
        caps = RP._capabilities([
            {'capability': 'book_page', 'subject_returned': True, 'params': {'a': 1},
             'outcome': 'accepted_with_hits', 'label': 'good'},
            {'capability': 'book_page', 'subject_returned': False, 'outcome': 'rejected',
             'label': 'bad'}])
        self.assertTrue(caps['book_page']['confirmed'])
        self.assertEqual(caps['book_page']['shape'], {'a': 1})

    def test_the_probe_refuses_to_run_without_a_known_instrument(self):
        with self.assertRaises(SystemExit):
            RP.main([])

    def test_a_shape_with_no_subject_value_is_not_probed_rather_than_failed(self):
        # The 2026-09-22 desktop run printed three results and NOTHING CONFIRMED while four shapes
        # had been skipped for want of a CFN and a folio. Those are different statements.
        verdict = RP.probe({'book': '1', 'page': '1', 'cfn': '', 'folio': ''},
                           pause=0)
        skipped = [r for r in verdict['shapes'] if r['outcome'] == 'not_probed']
        self.assertTrue(any('no subject value' in (r.get('why') or '') for r in skipped))


class DossierTest(unittest.TestCase):
    """The walk reaches the dossier as evidence at rung c, and never moves rung d."""

    def _dossier(self, walk):
        rows = [row_citing('official_records/35287-4642', CITER)]
        return CD.build('C1', 'MIAMI-DADE', documents=rows, walk=walk)

    def test_a_walked_document_does_not_change_what_the_verdict_rests_on(self):
        chain = {'conf': 'ok', 'liens': []}
        rows = [row_citing('official_records/35287-4642', CITER)]
        rows.append(dict(row_citing('official_records/28001-1234', ['MORTGAGE', 'x']),
                         walked={'hop': 1, 'cited_by': 'official_records/35287-4642'}))
        dossier = CD.build('C1', 'MIAMI-DADE', chain=chain, documents=rows,
                           walk={'followed': [{'book': '28001', 'hop': 1}],
                                 'documents_fetched': 1, 'unresolved': []})
        self.assertEqual(dossier['d_picture']['rests_on'], ['b'])

    def test_an_unresolved_citation_becomes_an_open_gap(self):
        dossier = self._dossier({'followed': [], 'documents_fetched': 0,
                                 'unresolved': [{'book': '28001', 'page_no': '1234',
                                                 'reason': 'no book/page search is available'}]})
        self.assertTrue(any('28001/1234' in g for g in dossier['open_gaps']))
        self.assertFalse(dossier['complete'])

    def test_a_budget_stop_is_an_open_gap(self):
        dossier = self._dossier({'followed': [], 'documents_fetched': 2, 'unresolved': [],
                                 'stopped_because': 'budget: 2 document(s)'})
        self.assertTrue(any('stopped early' in g for g in dossier['open_gaps']))

    def test_an_instrument_found_under_another_name_becomes_an_open_gap(self):
        dossier = self._dossier({
            'followed': [], 'documents_fetched': 0, 'unresolved': [],
            'names': {'searched': [], 'found_under_other_names': [
                {'doc_type': 'MORTGAGE', 'rec_date': '5/14/2019', 'under_name': 'ROSALES MARIA',
                 'anchored_by': 'folio'}]}})
        self.assertTrue(any('ROSALES MARIA' in g and 'NOT in the owner-name search' in g
                            for g in dossier['open_gaps']))

    def test_a_name_the_county_could_not_be_asked_about_is_an_open_gap(self):
        dossier = self._dossier({
            'followed': [], 'documents_fetched': 0, 'unresolved': [],
            'names': {'found_under_other_names': [],
                      'searched': [{'name': 'ROSALES MARIA', 'outcome': 'not_reached',
                                    'reason': 'no search token could be obtained'}]}})
        self.assertTrue(any('never searched' in g and 'ROSALES MARIA' in g
                            for g in dossier['open_gaps']))

    def test_unsearched_parcel_names_are_an_open_gap_in_words(self):
        dossier = self._dossier({'followed': [], 'documents_fetched': 0, 'unresolved': [],
                                 'name_search': {'skipped': 3, 'planned': [], 'candidates': []}})
        self.assertTrue(any('never searched' in g for g in dossier['open_gaps']))

    def test_a_dossier_built_without_a_walk_has_no_walk_section(self):
        # The flag is off by default, and an absent walk must not render as a walk that found
        # nothing — those are different statements about the case.
        rows = [row_citing('official_records/35287-4642', CITER)]
        self.assertNotIn('walk', CD.build('C1', 'MIAMI-DADE', documents=rows)['c_documents'])


class CitationExtractionTest(unittest.TestCase):
    """document_classify.cited_instruments is PR #47's; these pin the shapes the walk depends on."""

    def test_the_two_common_clerk_spellings_are_both_found(self):
        found = document_classify.cited_instruments(reading(CITER))
        self.assertEqual({(c['book'], c['page_no']) for c in found},
                         {('28001', '1234'), ('29500', '77')})

    def test_a_citation_carries_the_line_that_made_it(self):
        found = document_classify.cited_instruments(reading(CITER))
        self.assertTrue(all(c['passage'] for c in found))


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    unittest.main(verbosity=2)
