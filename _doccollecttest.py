"""Miami document acquisition: transport, verification, store, queue, interpreter budget.

SYNTHETIC FIXTURES ONLY. No homeowner data, no network. The clerk hosts answer 403 to the cloud
container this was written in, so every response below is a RECORDED shape — field names taken
from what `records_liens.records_by_qs` and `docket.pull` already parse in this repo
(`reC_BOOK`, `cfN_MASTER_ID`, `doC_PAGES`, `encID`, `numberOfDocuments`) — carrying invented
values. The live check against the real endpoint is a command for the laptop/desktop, in the PR.

Run:  python _doccollecttest.py          (needs PyMuPDF)
"""
import os
import sys
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix='dealflow-doctest-')
os.environ['DEALFLOW_DIR'] = _TMP          # BEFORE paths is imported anywhere
os.environ.pop('ONEDRIVE', None)
os.environ.pop('OneDrive', None)

import document_collectors as DC          # noqa: E402
import document_store as DS               # noqa: E402
import document_interpreter as DI         # noqa: E402
import document_queue as DQ               # noqa: E402
import equity_state                       # noqa: E402
import miami_judgment as MJ               # noqa: E402

CASE = '2026-000000-CA-01'
IMAGE = 'https://onlineservices.miamidadeclerk.gov/officialrecords/api/DocumentImage/proxypdf'


# ---- synthetic PDFs ----------------------------------------------------------------------------
def pdf_page(text=''):
    fitz = DS._fitz()
    doc = fitz.open()
    page = doc.new_page()
    if text:
        page.insert_text((72, 144), text, fontsize=11)
    out = doc.tobytes()
    doc.close()
    return out


JUDGMENT_TEXT = [
    'IN THE CIRCUIT COURT OF THE ELEVENTH JUDICIAL CIRCUIT',
    'FINAL JUDGMENT OF FORECLOSURE',
    'the total sum of $412,880.45 for which let execution issue',
    'Lot 7, Block 3, SYNTHETIC ESTATES, according to the plat thereof',
    'DONE AND ORDERED in chambers',
]


def judgment_pages():
    return [pdf_page(line) for line in JUDGMENT_TEXT]


# ---- recorded HTTP -----------------------------------------------------------------------------
class Reply:
    def __init__(self, content=b'', payload=None, status=200, url=''):
        self.content = content
        self._payload = payload
        self.status_code = status
        self.url = url
        self.text = ''

    def json(self):
        if self._payload is None:
            raise ValueError('no json')
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError('HTTP %d' % self.status_code)


class Session:
    """Answers the three GETs MiamiCollector makes, from a recorded script."""

    def __init__(self, manifest=None, pages=None, image=None, attachments=None):
        self.manifest = manifest
        self.pages = pages or []
        self.image = image
        self.attachments = attachments
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, timeout=None, allow_redirects=True):
        self.calls.append(url)
        if url.endswith('getdocumenturl'):
            return Reply(payload=self.manifest)
        if url.startswith(IMAGE) or url == IMAGE:
            index = len([c for c in self.calls if c.startswith(IMAGE)]) - 1
            return Reply(content=self.pages[index], url=url)
        if url.endswith('CaseInfo/image'):
            return Reply(content=self.image, url=url)
        if url.endswith('GetSDocumentByEvent'):
            return Reply(payload=self.attachments)
        raise AssertionError('unexpected URL ' + url)


def manifest_for(n):
    return {'pageCount': n, 'urls': ['%s?p=%d' % (IMAGE, i) for i in range(n)]}


def record(pages=5, **kw):
    row = {'reC_BOOK': '34120', 'reC_PAGE': '1178', 'booK_TYPE': 'O',
           'cfN_MASTER_ID': '20260000001', 'doC_TYPE': 'JUDGMENT',
           'reC_DATE': '05/14/2026', 'doC_PAGES': pages}
    row.update(kw)
    return row


# ---- transport ---------------------------------------------------------------------------------
class TransportTests(unittest.TestCase):
    def collector(self, **kw):
        return DC.MiamiCollector(session=Session(**kw))

    def test_case_identity_mismatch_is_a_gap(self):
        import docket
        original = docket.pull
        docket.pull = lambda case: {'caseNumber': 'SOME-OTHER-CASE', 'dockets': [], 'parties': []}
        try:
            with self.assertRaises(DC.AccessGap):
                self.collector().enumerate_documents(CASE)
        finally:
            docket.pull = original

    def test_missing_case_identity_is_a_gap_not_a_pass(self):
        import docket
        original = docket.pull
        docket.pull = lambda case: {'dockets': [], 'parties': []}
        try:
            with self.assertRaises(DC.AccessGap):
                self.collector().enumerate_documents(CASE)
        finally:
            docket.pull = original

    def test_caseNo_spelling_is_accepted(self):
        # case_review.select_case accepts either key; a strict read of one would fail every case
        # that used the other.
        import docket
        original = docket.pull
        docket.pull = lambda case: {'caseNo': CASE, 'parties': [],
                                    'dockets': [{'eventID': 9, 'numberOfDocuments': 2},
                                                {'eventID': 10, 'numberOfDocuments': 0}]}
        try:
            inv = self.collector().enumerate_documents(CASE)
        finally:
            docket.pull = original
        self.assertEqual([e['status'] for e in inv['entries']], ['pending', 'inventory_only'])
        self.assertFalse(inv['pagination_verified'])

    def test_manifest_page_count_must_match_its_own_urls(self):
        c = self.collector(manifest={'pageCount': 5, 'urls': ['%s?p=1' % IMAGE]}, pages=[pdf_page()])
        with self.assertRaises(DC.AccessGap):
            c.retrieve_document(record())

    def test_image_url_off_the_allowlisted_host_is_refused(self):
        bad = {'pageCount': 1, 'urls': ['https://evil.example.com/proxypdf?p=1']}
        c = self.collector(manifest=bad, pages=[pdf_page()])
        with self.assertRaises(DC.AccessGap):
            c.retrieve_document(record(pages=1))

    def test_non_pdf_body_is_a_gap(self):
        c = self.collector(manifest=manifest_for(1), pages=[b'<html>sign in</html>'])
        with self.assertRaises(DC.AccessGap):
            c.retrieve_document(record(pages=1))

    def test_missing_recording_key_is_a_gap(self):
        c = self.collector(manifest=manifest_for(1), pages=[pdf_page()])
        with self.assertRaises(DC.AccessGap):
            c.retrieve_document(record(pages=1, cfN_MASTER_ID=''))

    def test_page_cap(self):
        c = self.collector(manifest=manifest_for(DC.MAX_PAGES + 1), pages=[])
        with self.assertRaises(DC.AccessGap):
            c.retrieve_document(record())

    def test_attachment_login_wall_is_a_gap(self):
        c = self.collector(attachments=[{'documentName': 'Redirect', 'caseNumber': CASE}])
        with self.assertRaises(DC.AccessGap):
            c.attachments(CASE, {'encID': 'x', 'numberOfDocuments': 1})

    def test_attachment_count_mismatch_is_a_gap(self):
        c = self.collector(attachments=[{'documentName': 'Order', 'caseNumber': CASE}])
        with self.assertRaises(DC.AccessGap):
            c.attachments(CASE, {'encID': 'x', 'numberOfDocuments': 3})

    def test_attachment_case_mismatch_is_a_gap(self):
        c = self.collector(attachments=[{'documentName': 'Order', 'caseNumber': 'OTHER'}])
        with self.assertRaises(DC.AccessGap):
            c.attachments(CASE, {'encID': 'x', 'numberOfDocuments': 1})

    def test_attachment_without_a_stated_count_is_a_gap(self):
        # An inventory we cannot reconcile against anything is not a verified inventory.
        c = self.collector(attachments=[{'documentName': 'Order', 'caseNumber': CASE}])
        with self.assertRaises(DC.AccessGap):
            c.attachments(CASE, {'encID': 'x'})

    def test_non_json_reply_reports_a_gap_not_a_parse_error(self):
        class HtmlSession(Session):
            def get(self, url, **kw):
                return Reply(content=b'<html/>')
        c = DC.MiamiCollector(session=HtmlSession())
        with self.assertRaises(DC.AccessGap):
            c.retrieve_document(record())

    def test_other_counties_are_explicit_stubs(self):
        for cls in (DC.BrowardCollector, DC.PalmBeachCollector):
            with self.assertRaises(DC.AccessGap):
                cls().enumerate_documents(CASE)

    def test_judgment_candidates_are_keyword_only(self):
        inv = {'entries': [{'source_ref': 'dockets/0',
                            'metadata': {'docketDescrition': 'FINAL JUDGMENT OF FORECLOSURE'}},
                           {'source_ref': 'dockets/1',
                            'metadata': {'docketDescrition': 'NOTICE OF APPEARANCE'}}]}
        found = DC.judgment_candidates(inv)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['match'], 'candidate_by_keyword')


# ---- verification and store ---------------------------------------------------------------------
class StoreTests(unittest.TestCase):
    def retrieve(self, pages=None, expected=5):
        pages = pages if pages is not None else judgment_pages()
        session = Session(manifest=manifest_for(len(pages)), pages=pages)
        c = DC.MiamiCollector(session=session)
        return c.retrieve_document(record(pages=expected))

    def test_page_count_verified_against_the_recording_index(self):
        manifest = DS.store('MIAMI-DADE', CASE, self.retrieve(), source_ref='or/1')
        self.assertEqual(manifest['pages'], 5)
        self.assertEqual(manifest['pages_expected'], 5)
        self.assertTrue(manifest['page_count_verified'])
        self.assertEqual(manifest['page_count_source'], 'recording_index_doC_PAGES')
        self.assertEqual(manifest['read_status'], 'unread')
        self.assertTrue(os.path.exists(manifest['path']))

    def test_short_document_is_rejected(self):
        # THE failure this whole module exists for: four pages of a five-page judgment.
        with self.assertRaises(DS.DocumentRejected):
            DS.store('MIAMI-DADE', CASE, self.retrieve(pages=judgment_pages()[:4], expected=5))

    def test_absent_index_count_is_recorded_as_unverified_not_as_a_pass(self):
        retrieved = self.retrieve(expected='')
        manifest = DS.store('MIAMI-DADE', CASE, retrieved, source_ref='or/noindex')
        self.assertFalse(manifest['page_count_verified'])
        self.assertIn('no recording-index page count', manifest['page_count_note'])

    def test_court_image_is_never_page_verified(self):
        session = Session(image=pdf_page('ORDER'))
        c = DC.MiamiCollector(session=session)
        got = c.retrieve_document({'encDocInfo': 'abc'})
        manifest = DS.store('MIAMI-DADE', CASE, got, source_ref='dockets/3')
        self.assertFalse(manifest['page_count_verified'])
        self.assertEqual(manifest['pages'], 1)

    def test_identical_bytes_dedupe_instead_of_rewriting(self):
        # The same bytes fetched twice — a resumed run re-reaching a document it already has.
        # (Regenerating the fixture PDFs would not work: a PDF carries its creation time, so two
        # renders of the same text hash differently.)
        pages = judgment_pages()
        first = DS.store('MIAMI-DADE', CASE + '-dupe', self.retrieve(pages=pages), source_ref='or/1')
        second = DS.store('MIAMI-DADE', CASE + '-dupe', self.retrieve(pages=pages), source_ref='or/1')
        self.assertTrue(first['stored'])
        self.assertFalse(second['stored'])
        self.assertEqual(first['sha256'], second['sha256'])

    def test_identity_is_the_clerks_bytes_not_our_rebuilt_file(self):
        # PyMuPDF writes a fresh document ID on every tobytes(), so the rebuilt file is not
        # reproducible. If identity followed it, every resumed run would re-store the same
        # document under a new hash and dedupe would never fire.
        pages = judgment_pages()
        one = DS.validate(self.retrieve(pages=pages))
        two = DS.validate(self.retrieve(pages=pages))
        self.assertEqual(one['sha256'], two['sha256'])
        self.assertNotEqual(one['rebuilt_sha256'], two['rebuilt_sha256'])

    def test_store_stays_out_of_the_repo_and_out_of_onedrive(self):
        folder = str(DS.case_dir('MIAMI-DADE', CASE))
        self.assertTrue(folder.startswith(_TMP))
        self.assertNotIn('OneDrive', folder)
        here = os.path.dirname(os.path.abspath(__file__))
        self.assertFalse(folder.startswith(here))

    def test_case_number_cannot_escape_the_case_folder(self):
        folder = str(DS.case_dir('MIAMI-DADE', '../../../etc/passwd'))
        self.assertTrue(folder.startswith(_TMP))
        self.assertNotIn('..', folder)


# ---- reading --------------------------------------------------------------------------------------
class ReadTests(unittest.TestCase):
    def read(self, pages):
        session = Session(manifest=manifest_for(len(pages)), pages=pages)
        got = DC.MiamiCollector(session=session).retrieve_document(record(pages=len(pages)))
        return DS.read_pages(DS.validate(got)['content'])

    def test_every_page_gets_an_outcome(self):
        reading = self.read(judgment_pages())
        self.assertEqual(reading['page_count'], 5)
        self.assertEqual(len(reading['pages']), 5)
        self.assertTrue(all(p['outcome'] in ('text', 'needs_ocr', 'unreadable')
                            for p in reading['pages']))

    def test_image_only_document_is_never_called_read(self):
        reading = self.read([pdf_page(), pdf_page()])
        self.assertEqual(reading['read_status'], 'image_only')
        self.assertFalse(reading['complete'])
        self.assertEqual(reading['pages_unresolved'], [1, 2])

    def test_mixed_document_is_partial_not_read(self):
        pages = judgment_pages()[:2] + [pdf_page()]
        reading = self.read(pages)
        self.assertEqual(reading['read_status'], 'partial')
        self.assertIn(3, reading['pages_unresolved'])

    def test_a_stamp_worth_of_characters_is_not_a_read_page(self):
        reading = self.read([pdf_page('FILED')])
        self.assertEqual(reading['pages'][0]['outcome'], 'needs_ocr')


# ---- judgment extraction -------------------------------------------------------------------------
class JudgmentTests(unittest.TestCase):
    def reading(self, lines):
        pages = [pdf_page(line) for line in lines]
        session = Session(manifest=manifest_for(len(pages)), pages=pages)
        got = DC.MiamiCollector(session=session).retrieve_document(record(pages=len(pages)))
        return DS.read_pages(DS.validate(got)['content'])

    def test_amount_candidates_carry_page_and_passage(self):
        cands = MJ.judgment_amount_candidates(self.reading(JUDGMENT_TEXT))
        self.assertTrue(cands, 'expected at least one candidate')
        self.assertEqual(cands[0]['amount'], 412880.45)
        self.assertEqual(cands[0]['page'], 3)
        self.assertIn('412,880.45', cands[0]['passage'])
        self.assertFalse(cands[0]['verified'])

    def test_conflicting_totals_yield_no_number(self):
        lines = ['ORDERED that plaintiff recover the total sum of $412,880.45 from defendant',
                 'ORDERED that plaintiff recover the total sum of $99,000.00 from defendant']
        self.assertIsNone(MJ.agreed_amount(MJ.judgment_amount_candidates(self.reading(lines))))

    def test_agreeing_totals_yield_the_number(self):
        lines = ['ORDERED that plaintiff recover the total sum of $412,880.45 from defendant',
                 'and the total judgment $412,880.45 shall bear interest at the statutory rate']
        self.assertEqual(MJ.agreed_amount(MJ.judgment_amount_candidates(self.reading(lines))),
                         412880.45)

    def test_judgment_for_analyze_requires_verified_and_fully_read(self):
        row = {'page_count_verified': True, 'read_status': 'read',
               'amount_candidates': [{'amount': 412880.45}]}
        self.assertEqual(MJ.judgment_for_analyze({'documents': [row]}), 412880.45)
        for broken in ({'page_count_verified': False}, {'read_status': 'partial'}):
            self.assertIsNone(MJ.judgment_for_analyze({'documents': [dict(row, **broken)]}))


# ---- queue -----------------------------------------------------------------------------------------
class QueueTests(unittest.TestCase):
    def queue(self):
        return DQ.DocumentQueue(os.path.join(_TMP, 'q-%s.db' % self.id().rsplit('.', 1)[-1]))

    def test_enqueue_is_idempotent(self):
        with self.queue() as q:
            self.assertTrue(q.add('MIAMI-DADE', CASE, 'or/1', 'recorded_instrument'))
            self.assertFalse(q.add('MIAMI-DADE', CASE, 'or/1', 'recorded_instrument'))
            self.assertEqual(q.counts(), {'pending': 1})

    def test_one_worker_wins_a_job(self):
        with self.queue() as q:
            q.add('MIAMI-DADE', CASE, 'or/1', 'recorded_instrument')
            self.assertIsNotNone(q.claim('worker-a'))
            self.assertIsNone(q.claim('worker-b'))

    def test_an_expired_lease_is_reclaimed(self):
        with self.queue() as q:
            q.add('MIAMI-DADE', CASE, 'or/1', 'recorded_instrument')
            first = q.claim('worker-a', lease=-1)      # already expired
            second = q.claim('worker-b')
            self.assertEqual(first['id'], second['id'])
            self.assertEqual(second['attempts'], 2)

    def test_a_stale_worker_cannot_overwrite_the_new_holder(self):
        with self.queue() as q:
            q.add('MIAMI-DADE', CASE, 'or/1', 'recorded_instrument')
            stale = q.claim('worker-a', lease=-1)
            fresh = q.claim('worker-b')
            self.assertFalse(q.complete(stale['id'], 'worker-a'))
            self.assertTrue(q.complete(fresh['id'], 'worker-b'))

    def test_a_gap_is_terminal_and_a_failure_retries(self):
        with self.queue() as q:
            q.add('MIAMI-DADE', CASE, 'or/1', 'recorded_instrument')
            q.add('MIAMI-DADE', CASE, 'or/2', 'recorded_instrument')
            gapped = q.claim('w')
            q.gap(gapped['id'], 'w', DC.AccessGap('County login required'))
            other = q.claim('w')
            q.fail(other['id'], 'w', 'connection reset')
            counts = q.counts()
            self.assertEqual(counts.get('gap'), 1)
            self.assertEqual(counts.get('pending'), 1)      # back in the queue, not lost

    def test_failure_stops_retrying_after_max_attempts(self):
        with self.queue() as q:
            q.add('MIAMI-DADE', CASE, 'or/1', 'recorded_instrument')
            for _ in range(DQ.MAX_ATTEMPTS):
                job = q.claim('w')
                q.fail(job['id'], 'w', 'boom')
            self.assertEqual(q.counts().get('failed'), 1)

    def test_coverage_is_not_complete_while_a_gap_stands(self):
        with self.queue() as q:
            q.add('MIAMI-DADE', CASE, 'or/1', 'recorded_instrument')
            job = q.claim('w')
            q.gap(job['id'], 'w', 'restricted')
            cov = q.coverage('MIAMI-DADE', CASE)
            self.assertEqual(cov['gaps'], 1)
            self.assertFalse(cov['collection_complete'])

    def test_a_resumed_run_does_not_refetch_completed_work(self):
        path = os.path.join(_TMP, 'resume.db')
        with DQ.DocumentQueue(path) as q:
            q.add('MIAMI-DADE', CASE, 'or/1', 'recorded_instrument')
            q.add('MIAMI-DADE', CASE, 'or/2', 'recorded_instrument')
            done = q.claim('w')
            q.complete(done['id'], 'w', sha256='abc')
        with DQ.DocumentQueue(path) as q:                 # worker restarted
            remaining = [j['source_ref'] for j in q.jobs(status='pending')]
            self.assertEqual(remaining, ['or/2'])


class ResumeTests(unittest.TestCase):
    """A restarted run must not re-download what it already stored."""

    def collector(self, pages):
        return DC.MiamiCollector(session=Session(manifest=manifest_for(len(pages)), pages=pages))

    def test_claim_ref_takes_the_named_job_not_the_next_one(self):
        with DQ.DocumentQueue(os.path.join(_TMP, 'claimref.db')) as q:
            q.add('MIAMI-DADE', CASE, 'or/1', 'recorded_instrument')
            q.add('MIAMI-DADE', CASE, 'or/2', 'recorded_instrument')
            job = q.claim_ref('w', 'MIAMI-DADE', CASE, 'or/2', 'recorded_instrument')
            self.assertEqual(job['source_ref'], 'or/2')
            self.assertIsNone(q.claim_ref('w2', 'MIAMI-DADE', CASE, 'or/2', 'recorded_instrument'))
            self.assertEqual(q.claim('w2')['source_ref'], 'or/1')

    def test_second_run_skips_the_document_it_already_stored(self):
        case = CASE + '-resume'
        pages = judgment_pages()
        path = os.path.join(_TMP, 'resume-e2e.db')
        with DQ.DocumentQueue(path) as q:
            first = MJ.collect_recorded(case, [record(pages=5)],
                                        collector=self.collector(pages), queue=q)
        self.assertEqual(first[0]['status'], 'stored')
        self.assertTrue(first[0]['page_count_verified'])
        with DQ.DocumentQueue(path) as q:
            second = MJ.collect_recorded(case, [record(pages=5)],
                                         collector=self.collector(pages), queue=q)
            self.assertEqual(q.coverage('MIAMI-DADE', case)['done'], 1)
        self.assertEqual(second[0]['status'], 'skipped')
        self.assertEqual(second[0]['prior_status'], 'done')

    def test_an_access_gap_is_recorded_and_not_retried_as_a_download(self):
        case = CASE + '-gap'
        path = os.path.join(_TMP, 'gap-e2e.db')
        bad = self.collector([b'<html>sign in</html>'])
        with DQ.DocumentQueue(path) as q:
            rows = MJ.collect_recorded(case, [record(pages=1)], collector=bad, queue=q)
            self.assertEqual(rows[0]['status'], 'gap')
            cov = q.coverage('MIAMI-DADE', case)
        self.assertEqual(cov['gaps'], 1)
        self.assertFalse(cov['collection_complete'])
        with DQ.DocumentQueue(path) as q:
            again = MJ.collect_recorded(case, [record(pages=1)], collector=bad, queue=q)
        self.assertEqual(again[0]['status'], 'skipped')
        self.assertEqual(again[0]['prior_status'], 'gap')

    def test_a_short_download_is_a_rejection_the_queue_records(self):
        case = CASE + '-short'
        with DQ.DocumentQueue(os.path.join(_TMP, 'short-e2e.db')) as q:
            rows = MJ.collect_recorded(case, [record(pages=5)],
                                       collector=self.collector(judgment_pages()[:4]), queue=q)
            self.assertEqual(rows[0]['status'], 'rejected')
            self.assertIn('recording index', rows[0]['reason'])
            self.assertEqual(q.coverage('MIAMI-DADE', case)['gaps'], 1)


# ---- interpreter budget --------------------------------------------------------------------------
class BudgetTests(unittest.TestCase):
    def test_a_run_without_a_cap_is_refused(self):
        for bad in (None, 0, -1):
            with self.assertRaises(ValueError):
                DI.Budget(bad)

    def test_an_unpriced_model_is_refused(self):
        with self.assertRaises(ValueError):
            DI.Budget(5.0, model='some-unlisted-model')

    def test_the_cap_is_checked_before_the_call_at_worst_case(self):
        budget = DI.Budget(0.01, model='claude-opus-5')       # $5/MTok in, $25/MTok out
        with self.assertRaises(DI.BudgetExhausted):
            budget.check(input_tokens=1000, max_output_tokens=4000)   # worst case $0.105
        self.assertEqual(budget.spent, 0.0)

    def test_spend_accumulates_and_then_refuses(self):
        budget = DI.Budget(0.15, model='claude-opus-5')
        budget.check(1000, 4000)
        budget.record(1000, 2000)
        self.assertAlmostEqual(budget.spent, 0.055, places=6)
        with self.assertRaises(DI.BudgetExhausted):
            budget.check(1000, 4000)

    def test_backends_are_explicit_and_never_fall_back(self):
        self.assertIsInstance(DI.build('cli'), DI.CliInterpreter)
        self.assertIsInstance(DI.build('api'), DI.ApiInterpreter)
        with self.assertRaises(ValueError):
            DI.build('whatever')

    def test_missing_api_credentials_raise_rather_than_switching_to_the_cli(self):
        saved = {k: os.environ.pop(k, None) for k in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN')}
        try:
            with self.assertRaises((DI.NotConfigured, ValueError)):
                DI.ApiInterpreter().client()
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_an_image_only_document_is_not_sent_to_a_model(self):
        pages = [{'page': 1, 'outcome': 'needs_ocr', 'text': ''}]
        out = DI.ApiInterpreter(client=object()).interpret(pages, DI.Budget(1.0))
        self.assertEqual(out['skipped'], 'image_only')

    def test_a_finding_without_a_page_and_passage_is_dropped(self):
        parsed = DI._parse('{"findings": ['
                           '{"field": "total", "value": 1, "page": 3, "passage": "the total sum"},'
                           '{"field": "total", "value": 2}]}')
        self.assertEqual(len(parsed['findings']), 1)
        self.assertFalse(parsed['findings'][0]['verified'])

    def test_unparseable_output_is_unresolved_not_empty(self):
        parsed = DI._parse('I could not read that document.')
        self.assertEqual(parsed['findings'], [])
        self.assertTrue(parsed['unresolved'])


# ---- the line this branch must not cross ------------------------------------------------------
class EquityStateUntouchedTests(unittest.TestCase):
    """Nothing in this branch may add a route into equity_state's FACT states."""

    def test_five_states_and_two_facts_are_unchanged(self):
        self.assertEqual(equity_state.FACT, ('clear', 'priced'))
        self.assertEqual(sorted(equity_state.LABEL),
                         ['clear', 'none', 'priced', 'unchecked', 'unpriced'])

    def test_document_evidence_alone_never_reaches_a_fact_state(self):
        # A chain with a document-sourced judgment figure and nothing else is still UNCHECKED.
        for chain in ({'judgment_from_document': 412880.45},
                      {'judgment_from_document': 412880.45, 'conf': 'low'},
                      {'documents_read': 5, 'page_count_verified': True}):
            self.assertNotIn(equity_state.state_of(chain), equity_state.FACT)

    def test_the_known_state_table_still_holds(self):
        self.assertEqual(equity_state.state_of(None), 'unchecked')
        self.assertEqual(equity_state.state_of({'conf': 'ok', 'liens': []}), 'clear')
        self.assertEqual(equity_state.state_of({'conf': 'low', 'liens': []}), 'none')
        self.assertEqual(equity_state.state_of({'conf': 'ok', 'liens': [{'amt': 1}]}), 'priced')
        self.assertEqual(equity_state.state_of({'conf': 'ok', 'liens': [{'amt': 1}, {}]}), 'unpriced')
        self.assertEqual(equity_state.state_of({'conf': 'unpriced', 'liens': [{'amt': 1}]}),
                         'unpriced')


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    unittest.main(verbosity=2)
