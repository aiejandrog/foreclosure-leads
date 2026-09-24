"""Miami document acquisition: transport, verification, store, queue, interpreter budget.

SYNTHETIC FIXTURES ONLY. No homeowner data, no network. The clerk hosts answer 403 to the cloud
container this was written in, so every response below is a RECORDED shape — field names taken
from what `records_liens.records_by_qs` and `docket.pull` already parse in this repo
(`reC_BOOK`, `cfN_MASTER_ID`, `doC_PAGES`, `encID`, `numberOfDocuments`) — carrying invented
values. The live check against the real endpoint is a command for the laptop/desktop, in the PR.

Run:  python _doccollecttest.py          (needs PyMuPDF)
"""
import json
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
import document_vision as DV              # noqa: E402
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


# The real watermark from the 2026-09-22 pilot, 75 characters — longer than the 40-character
# floor the first version of this module trusted.
WATERMARK = 'NOT AN OFFICIAL COPY - PUBLIC ACCESS SYSTEM - MIAMI-DADE COUNTY CLERK OF CT'


def scanned_page(stamp=WATERMARK):
    """A page that is a picture of a page, with a stamp over it. What the clerk actually serves."""
    fitz = DS._fitz()
    doc = fitz.open()
    page = doc.new_page()
    pix = fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, 1700, 2200), False)
    pix.clear_with(220)
    page.insert_image(page.rect, pixmap=pix)
    if stamp:
        page.insert_text((40, 30), stamp, fontsize=7)
    out = doc.tobytes()
    doc.close()
    return out


JUDGMENT_TEXT = [
    'IN THE CIRCUIT COURT OF THE ELEVENTH JUDICIAL CIRCUIT IN AND FOR THE COUNTY',
    'SUMMARY FINAL JUDGMENT OF FORECLOSURE AS TO COUNT ONE OF THE COMPLAINT',
    'the total sum of $412,880.45 for which let execution issue forthwith',
    'Lot 7, Block 3, SYNTHETIC ESTATES, according to the plat thereof as recorded',
    'DONE AND ORDERED in chambers at Miami-Dade County, Florida, on this day',
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
        pages = judgment_pages()
        first = DS.store('MIAMI-DADE', CASE + '-dupe', self.retrieve(pages=pages), source_ref='or/1')
        second = DS.store('MIAMI-DADE', CASE + '-dupe', self.retrieve(pages=pages), source_ref='or/1')
        self.assertTrue(first['stored'])
        self.assertFalse(second['stored'])
        self.assertEqual(first['document_key'], second['document_key'])

    def test_identity_survives_the_clerk_changing_the_bytes(self):
        # THE 2026-09-22 PILOT BUG. The same five pages of book 35287 page 4642 hashed 3f37483d
        # on one fetch and 94f3f6cf on the next: the county re-generates the PDF per request. Both
        # the served bytes AND our rebuild are therefore unstable, and identity built on either
        # stores the same document twice — which is what happened, in a real case folder.
        pages = judgment_pages()
        # Trailing bytes after %%EOF are ignored by every PDF reader, so this is the same document
        # serialised differently — exactly what the county does when it regenerates the file.
        refetched = pages[:-1] + [pages[-1] + b'\n%% a second serialisation\n']
        one = DS.store('MIAMI-DADE', CASE + '-refetch', self.retrieve(pages=pages),
                       source_ref='or/1')
        two = DS.store('MIAMI-DADE', CASE + '-refetch', self.retrieve(pages=refetched),
                       source_ref='or/1')
        self.assertNotEqual(one['fetches'][0]['source_sha256'],
                            two['fetches'][-1]['source_sha256'])          # the clerk's bytes moved
        self.assertEqual(one['document_key'], two['document_key'])        # the document did not
        self.assertFalse(two['stored'])
        self.assertTrue(two['bytes_differ_between_fetches'])
        self.assertEqual(len(two['fetches']), 2)
        self.assertEqual(len(list(DS.case_dir('MIAMI-DADE', CASE + '-refetch').glob('*.pdf'))), 1)

    def test_identity_rests_on_the_countys_own_record_key(self):
        manifest = DS.store('MIAMI-DADE', CASE + '-basis', self.retrieve(), source_ref='or/1')
        self.assertEqual(manifest['identity_basis'], 'record_key')

    def test_reconcile_names_duplicates_and_deletes_nothing(self):
        # The two copies the pilot left behind, as they sit on disk today.
        case = CASE + '-recon'
        folder = DS.case_dir('MIAMI-DADE', case)
        folder.mkdir(parents=True, exist_ok=True)
        for stem, read_status in (('aaaaaaaaaaaaaaaa', 'image_only'), ('bbbbbbbbbbbbbbbb', 'read')):
            (folder / (stem + '.pdf')).write_bytes(b'%PDF-1.4 ')
            (folder / (stem + '.json')).write_text(json.dumps({
                'county': 'MIAMI-DADE', 'case': case, 'source_ref': 'or/1', 'pages': 5,
                'record_key': {'reC_BOOK': '35287', 'reC_PAGE': '4642'},
                'read_status': read_status, 'retrieved_at': '2026-09-22T10:00:00+00:00'}),
                encoding='utf-8')
        report = DS.reconcile('MIAMI-DADE', case, apply=True)
        self.assertEqual(report['duplicates'], 1)
        # The copy that was actually read is the keeper.
        self.assertIn('bbbbbbbbbbbbbbbb', report['groups'][0]['keep']['meta_path'])
        # Nothing is deleted: these are court records obtained once.
        self.assertEqual(len(list(folder.glob('*.pdf'))), 2)
        loser = json.loads((folder / 'aaaaaaaaaaaaaaaa.json').read_text(encoding='utf-8'))
        self.assertTrue(loser['superseded'])
        # And a second pass no longer counts it.
        self.assertEqual(DS.reconcile('MIAMI-DADE', case)['duplicates'], 0)

    def test_page_text_is_written_down_not_thrown_away(self):
        # The pilot OCR'd five pages and kept none of the words, so the only way to see what OCR
        # had read was to run it again by hand.
        manifest = DS.store('MIAMI-DADE', CASE + '-text', self.retrieve(), source_ref='or/1')
        reading = DS.read_pages(manifest['path'])
        folder = DS.save_page_text(manifest, reading)
        index = json.loads(open(os.path.join(folder, 'pages.json'), encoding='utf-8').read())
        self.assertEqual(len(index['pages']), 5)
        self.assertIn('SUMMARY FINAL JUDGMENT',
                      open(os.path.join(folder, 'p02.txt'), encoding='utf-8').read())

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


class ScanDetectionTests(unittest.TestCase):
    """The 2026-09-22 pilot regression: a stamp is text, and no character count can tell them apart.

    All five pages of the Garden Lake Towers judgment are 100% raster with a 75-character clerk
    watermark. The first version of this module scored them `text` and called the document `read`.
    """

    def read(self, pages, **kw):
        session = Session(manifest=manifest_for(len(pages)), pages=pages)
        got = DC.MiamiCollector(session=session).retrieve_document(record(pages=len(pages)))
        return DS.read_pages(DS.validate(got)['content'], **kw)

    def test_the_pilot_document_is_not_read(self):
        reading = self.read([scanned_page() for _ in range(5)])
        self.assertEqual(reading['read_status'], 'image_only')
        self.assertFalse(reading['complete'])
        self.assertEqual(reading['pages_unresolved'], [1, 2, 3, 4, 5])
        self.assertEqual(reading['pages_with_text'], 0)

    def test_the_watermark_is_long_enough_to_have_fooled_a_character_count(self):
        self.assertGreater(len(WATERMARK), DS.MIN_PAGE_CHARS)
        reading = self.read([scanned_page()])
        page = reading['pages'][0]
        self.assertEqual(page['outcome'], 'needs_ocr')
        self.assertIn('raster image', page['weak_reason'])
        self.assertGreater(page['image_coverage'], DS.MAX_IMAGE_COVERAGE)

    def test_stamp_text_is_not_offered_as_the_pages_text(self):
        # If the stamp stayed in `text`, a dollar figure inside a watermark would be extracted
        # from it and reported as a judgment amount.
        page = self.read([scanned_page()])['pages'][0]
        self.assertEqual(page['text'], '')
        self.assertIn(WATERMARK[:20], page['embedded_text'])

    def test_a_dollar_figure_in_a_watermark_never_becomes_an_amount(self):
        stamped = scanned_page(WATERMARK + ' FEE $412,880.45 total amount due')
        self.assertEqual(MJ.judgment_amount_candidates(self.read([stamped])), [])

    def test_identical_text_on_every_page_is_boilerplate_not_content(self):
        # Catches a stamp on a page that is NOT mostly raster — a born-digital cover sheet whose
        # only text is the same footer on all of it.
        fitz = DS._fitz()

        def footer_only():
            doc = fitz.open()
            page = doc.new_page()
            page.insert_text((40, 30), 'NOT AN OFFICIAL COPY - PUBLIC ACCESS SYSTEM - PAGE 1 OF 4',
                             fontsize=9)
            out = doc.tobytes()
            doc.close()
            return out

        reading = self.read([footer_only() for _ in range(4)])
        self.assertEqual(reading['read_status'], 'image_only')
        self.assertIn('stamp', reading['pages'][0]['weak_reason'])

    def test_a_real_text_page_still_reads(self):
        reading = self.read(judgment_pages())
        self.assertEqual(reading['read_status'], 'read')
        self.assertTrue(all(p['text_source'] == 'embedded' for p in reading['pages']))

    def test_garbled_glyphs_do_not_pass_on_length(self):
        self.assertLess(DS._sane_ratio('\ufffd' * 80), DS.MIN_SANE_RATIO)
        self.assertGreater(DS._sane_ratio('ORDERED that plaintiff recover the sum'),
                           DS.MIN_SANE_RATIO)


class OcrFallbackTests(unittest.TestCase):
    def read(self, pages, **kw):
        session = Session(manifest=manifest_for(len(pages)), pages=pages)
        got = DC.MiamiCollector(session=session).retrieve_document(record(pages=len(pages)))
        return DS.read_pages(DS.validate(got)['content'], **kw)

    def fake_ocr(self, text):
        return lambda paths: {path: text for path in paths}

    def test_ocr_turns_a_scan_into_a_read_document(self):
        line = 'ORDERED that plaintiff recover the total sum of $412,880.45 from defendant'
        reading = self.read([scanned_page(), scanned_page()], ocr=lambda paths: {
            path: line + (' First page' if i == 0 else ' Second page') for i, path in enumerate(paths)})
        self.assertEqual(reading['read_status'], 'read')
        self.assertEqual(reading['pages_from_ocr'], 2)
        self.assertEqual(reading['pages_with_text'], 0)
        self.assertTrue(all(p['text_source'] == 'ocr' for p in reading['pages']))

    def test_an_ocr_sourced_amount_is_tagged_as_such(self):
        line = 'ORDERED that plaintiff recover the total sum of $412,880.45 from defendant'
        cands = MJ.judgment_amount_candidates(
            self.read([scanned_page()], ocr=self.fake_ocr(line)))
        self.assertEqual(cands[0]['amount'], 412880.45)
        self.assertEqual(cands[0]['text_source'], 'ocr')

    def test_an_unavailable_ocr_bridge_is_a_recorded_reason_on_every_page(self):
        def broken(paths):
            raise RuntimeError('winocr exit 1: powershell not found')
        reading = self.read([scanned_page(), scanned_page()], ocr=broken)
        self.assertEqual(reading['read_status'], 'image_only')
        self.assertTrue(all('winocr' in p['ocr_error'] for p in reading['pages']))

    def test_ocr_that_returns_nothing_leaves_the_page_unresolved(self):
        reading = self.read([scanned_page()], ocr=self.fake_ocr(''))
        self.assertEqual(reading['pages'][0]['outcome'], 'needs_ocr')
        self.assertFalse(reading['complete'])

    def test_not_running_ocr_is_recorded(self):
        self.assertFalse(self.read([scanned_page()])['ocr_attempted'])
        self.assertTrue(self.read([scanned_page()], ocr=self.fake_ocr('x' * 80))['ocr_attempted'])


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


class JudgmentFilterTests(unittest.TestCase):
    """The pilot's judgment is indexed "DADE COURT PAPER - DCP", folio 0, so a doc-type filter
    alone printed "Nothing to fetch" and skipped the only document the pilot exists to test."""

    PLAINTIFFS = ['GARDEN LAKE TOWERS CONDOMINIUM ASSOCIATION INC']

    def dcp(self, **kw):
        row = record(doC_TYPE='DADE COURT PAPER - DCP', foliO_NUMBER='0',
                     firsT_PARTY='MARTIN MILAGROS J',
                     seconD_PARTY='GARDEN LAKE TOWERS CONDOMINIUM ASSN INC')
        row.update(kw)
        return row

    def test_a_plain_doc_type_filter_drops_the_pilot_judgment(self):
        self.assertEqual(MJ.recorded_judgments([self.dcp()]), [])

    def test_a_court_paper_between_the_cases_parties_is_kept(self):
        kept = MJ.recorded_judgments([self.dcp()], self.PLAINTIFFS)
        self.assertEqual(len(kept), 1)

    def test_either_indexed_party_order_matches(self):
        swapped = self.dcp(firsT_PARTY='GARDEN LAKE TOWERS CONDOMINIUM ASSN INC',
                           seconD_PARTY='MARTIN MILAGROS J')
        self.assertEqual(len(MJ.recorded_judgments([swapped], self.PLAINTIFFS)), 1)

    def test_a_court_paper_from_an_unrelated_case_is_not_kept(self):
        stranger = self.dcp(firsT_PARTY='SOMEBODY ELSE', seconD_PARTY='UNRELATED BANK NA')
        self.assertEqual(MJ.recorded_judgments([stranger], self.PLAINTIFFS), [])

    def test_corporate_noise_alone_never_matches(self):
        # "INC", "ASSOCIATION", "THE" are shared by half the index; matching on them would keep
        # every court paper in the county.
        noise = self.dcp(firsT_PARTY='THE INC COMPANY', seconD_PARTY='ASSOCIATION OF THE TRUST')
        self.assertEqual(MJ.recorded_judgments([noise], self.PLAINTIFFS), [])

    def test_a_real_judgment_doc_type_still_matches_with_no_plaintiffs(self):
        self.assertEqual(len(MJ.recorded_judgments([record()], [])), 1)

    def sat(self, **kw):
        row = record(doC_TYPE='SATISFACTION - SAT', foliO_NUMBER='0',
                     firsT_PARTY='GARDEN LAKE TOWERS CONDOMINIUM ASSN INC',
                     seconD_PARTY='MARTIN MILAGROS J')
        row.update(kw)
        return row

    def test_a_satisfaction_the_plaintiff_signed_is_fetched(self):
        # The nightly runs judgments_only. Without this the satisfaction that says the judgment
        # was paid is never fetched, and the dossier reports a paid debt as owed.
        self.assertEqual(len(MJ.recorded_judgments([self.sat()], self.PLAINTIFFS)), 1)
        rel = self.sat(doC_TYPE='RELEASE - REL', firsT_PARTY='MARTIN MILAGROS J',
                       seconD_PARTY='GARDEN LAKE TOWERS CONDOMINIUM ASSN INC')
        self.assertEqual(len(MJ.recorded_judgments([rel], self.PLAINTIFFS)), 1)

    def test_a_stranger_s_satisfaction_is_not_fetched(self):
        other = self.sat(firsT_PARTY='WELLS FARGO BANK NA')
        self.assertEqual(MJ.recorded_judgments([other], self.PLAINTIFFS), [])

    def test_no_plaintiffs_means_no_satisfactions(self):
        # Without the docket's plaintiffs there is nothing to tie a release to this case, and an
        # owner's unrelated releases would all be bought.
        self.assertEqual(MJ.recorded_judgments([self.sat()], []), [])

    def test_a_satisfaction_s_recited_figure_is_never_the_judgment_amount(self):
        sat = {'page_count_verified': True, 'read_status': 'read', 'doc_type': 'SATISFACTION - SAT',
               'amount_candidates': [{'amount': 993885.33}]}
        self.assertIsNone(MJ.judgment_for_analyze({'documents': [sat]}))
        judged = dict(sat, doc_type='JUDGMENT - JUD', amount_candidates=[{'amount': 412880.45}])
        self.assertEqual(MJ.judgment_for_analyze({'documents': [judged, sat]}), 412880.45)

    def test_plaintiffs_are_read_off_the_docket(self):
        raw = {'parties': [{'partyTypeDesc': 'PLAINTIFF', 'partyName': 'GARDEN LAKE TOWERS'},
                           {'partyTypeDesc': 'DEFENDANT', 'partyName': 'MILAGROS J MARTIN'}]}
        self.assertEqual(MJ.plaintiffs_of(raw), ['GARDEN LAKE TOWERS'])
        self.assertEqual(MJ.plaintiffs_of({}), [])


class IntegrityTests(unittest.TestCase):
    def retrieve(self, pages=None, expected=5):
        pages = pages if pages is not None else judgment_pages()
        session = Session(manifest=manifest_for(len(pages)), pages=pages)
        return DC.MiamiCollector(session=session).retrieve_document(record(pages=expected))

    def test_a_damaged_stored_copy_is_replaced_not_trusted(self):
        case = CASE + '-damaged'
        pages = judgment_pages()
        first = DS.store('MIAMI-DADE', case, self.retrieve(pages=pages), source_ref='or/1')
        with open(first['path'], 'wb') as fh:          # a crash mid-write
            fh.write(b'%PDF-truncated')
        again = DS.store('MIAMI-DADE', case, self.retrieve(pages=pages), source_ref='or/1')
        self.assertTrue(again.get('replaced_damaged_copy'))
        self.assertTrue(again['stored'])
        self.assertEqual(DS.read_pages(again['path'])['page_count'], 5)

    def test_an_intact_copy_is_recognised_and_rechecked(self):
        case = CASE + '-intact'
        pages = judgment_pages()
        DS.store('MIAMI-DADE', case, self.retrieve(pages=pages), source_ref='or/1')
        again = DS.store('MIAMI-DADE', case, self.retrieve(pages=pages), source_ref='or/1')
        self.assertFalse(again['stored'])
        self.assertTrue(again['integrity_rechecked'])

    def test_no_temp_file_is_left_behind(self):
        case = CASE + '-atomic'
        DS.store('MIAMI-DADE', case, self.retrieve(), source_ref='or/1')
        leftovers = [f for f in os.listdir(str(DS.case_dir('MIAMI-DADE', case)))
                     if f.endswith('.tmp')]
        self.assertEqual(leftovers, [])


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

    def test_a_resuming_run_skips_the_document_it_already_stored(self):
        # resume=True is the NIGHTLY stage's behaviour: finishing the day's backlog is the point.
        case = CASE + '-resume'
        pages = judgment_pages()
        path = os.path.join(_TMP, 'resume-e2e.db')
        with DQ.DocumentQueue(path) as q:
            first = MJ.collect_recorded(case, [record(pages=5)],
                                        collector=self.collector(pages), queue=q, resume=True)
        self.assertEqual(first[0]['status'], 'stored')
        self.assertTrue(first[0]['page_count_verified'])
        with DQ.DocumentQueue(path) as q:
            second = MJ.collect_recorded(case, [record(pages=5)],
                                         collector=self.collector(pages), queue=q, resume=True)
            self.assertEqual(q.coverage('MIAMI-DADE', case)['done'], 1)
        self.assertEqual(second[0]['status'], 'skipped')
        self.assertEqual(second[0]['prior_status'], 'done')

    def test_the_pilot_re_reads_a_document_the_queue_calls_done(self):
        # THE BUG, THREE TIMES. The pilot exists to run the same document again after a change,
        # and the queue refused three runs in a row - the last one because `done` was keyed on the
        # READER version while the change that run shipped was in the amount extraction.
        case = CASE + '-rereads'
        pages = judgment_pages()
        path = os.path.join(_TMP, 'reread-e2e.db')
        with DQ.DocumentQueue(path) as q:
            MJ.collect_recorded(case, [record(pages=5)], collector=self.collector(pages), queue=q)
        with DQ.DocumentQueue(path) as q:
            again = MJ.collect_recorded(case, [record(pages=5)],
                                        collector=self.collector(pages), queue=q)
        self.assertEqual(again[0]['status'], 'stored')     # NOT 'skipped'
        self.assertNotIn('already done', again[0].get('reason') or '')

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
        key_file, DI.KEY_FILE = DI.KEY_FILE, os.path.join(_TMP, 'no-such-anthropic.key')
        try:
            with self.assertRaises((DI.NotConfigured, ValueError)):
                DI.ApiInterpreter().client()
        finally:
            DI.KEY_FILE = key_file
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_an_sdk_without_count_tokens_is_a_named_gap_before_any_spend(self):
        class _Messages:
            pass

        class _Client:
            def __init__(self, **kw):
                self.messages = _Messages()

        class _OldSdk:
            __version__ = '0.37.1'
            Anthropic = _Client

        os.environ['ANTHROPIC_API_KEY'] = 'sk-test-not-a-real-key'
        try:
            with self.assertRaises(DI.NotConfigured) as ctx:
                DI.api_client(_OldSdk)
        finally:
            del os.environ['ANTHROPIC_API_KEY']
        self.assertIn('anthropic>=', str(ctx.exception))

    def test_the_key_file_is_used_when_the_environment_has_no_key(self):
        saved = {k: os.environ.pop(k, None) for k in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN')}
        key_file, DI.KEY_FILE = DI.KEY_FILE, os.path.join(_TMP, 'anthropic.key')
        try:
            with open(DI.KEY_FILE, 'w', encoding='utf-8') as fh:
                fh.write('sk-test-not-a-real-key\n')
            self.assertEqual(DI.api_client_kwargs(), {'api_key': 'sk-test-not-a-real-key'})
            os.environ['ANTHROPIC_API_KEY'] = 'from-env'
            self.assertEqual(DI.api_client_kwargs(), {}, 'the environment wins, as in captcha_solver')
        finally:
            os.environ.pop('ANTHROPIC_API_KEY', None)
            os.remove(DI.KEY_FILE)
            DI.KEY_FILE = key_file
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
        # #51 (board accuracy) demotes an empty chain that does not document its search from
        # 'clear' to 'none'. Either is fine here; this guard only checks that nothing moved UP.
        self.assertIn(equity_state.state_of({'conf': 'ok', 'liens': []}), ('clear', 'none'))
        self.assertEqual(equity_state.state_of({'conf': 'low', 'liens': []}), 'none')
        self.assertEqual(equity_state.state_of({'conf': 'ok', 'liens': [{'amt': 1}]}), 'priced')
        self.assertEqual(equity_state.state_of({'conf': 'ok', 'liens': [{'amt': 1}, {}]}), 'unpriced')
        self.assertEqual(equity_state.state_of({'conf': 'unpriced', 'liens': [{'amt': 1}]}),
                         'unpriced')



# ---- what the 2026-09-22 OCR run found -----------------------------------------------------------
# The desktop ran the pilot with Windows OCR on. It worked — five pages read — and it exposed four
# more defects, each reproduced here. The page it read is a real judgment cost table, and the OCR
# errors line up exactly with the clerk's diagonal watermark crossing the amounts column.
OCR_COLUMN_TEXT = '\n'.join([
    'ASSESSMENTS',
    'COLLECTION FEES',
    'COSTS',
    'ATTORNEY FEE',
    'GRAND TOTAL:',
    '',
    '$ 6,796.61',
    '$ 1,835.00',
    '$ 2,010.74',
    '$ 4,056.25',
    '$ 14,698.60',
])

# The same table as OCR actually read it on 2026-09-22: the grand total right, one subtotal
# misread where the watermark crosses it (6,796.61 -> 5,796.61).
OCR_COLUMN_TEXT_MISREAD = OCR_COLUMN_TEXT.replace('6,796.61', '5,796.61')


def _reading(text, outcome='ocr_text', source='ocr'):
    return {'pages': [{'page': 2, 'outcome': outcome, 'text': text, 'text_source': source}]}


class AmountLayoutTests(unittest.TestCase):
    def test_a_total_and_its_value_in_different_columns_are_matched(self):
        # Before this, "GRAND TOTAL:" and "$ 14,698.60" were fifteen lines apart, the extractor
        # only looked on the label's own line, and a correctly read total came out "not
        # established". Every judgment laid out as a table would have done the same.
        found = MJ.judgment_amount_candidates(_reading(OCR_COLUMN_TEXT))
        self.assertEqual([c['amount'] for c in found], [14698.60])
        self.assertEqual(found[0]["match"], "column_pairing")

    def test_the_same_line_match_still_wins_when_there_is_one(self):
        found = MJ.judgment_amount_candidates(
            _reading('the total sum of $412,880.45 for which let execution issue', 'text', 'embedded'))
        self.assertEqual(found[0]['match'], 'same_line')

    def test_a_label_does_not_reach_past_the_next_label(self):
        text = 'GRAND TOTAL:\nAMOUNT DUE\n$ 1,000.00\n'
        found = MJ.judgment_amount_candidates(_reading(text))
        # The figure belongs to AMOUNT DUE, not to the GRAND TOTAL above it.
        self.assertEqual([(c['amount'], c['passage'].startswith('AMOUNT DUE')) for c in found],
                         [(1000.0, True)])

    def test_line_items_that_sum_to_the_total_corroborate_it(self):
        found = MJ.judgment_amount_candidates(_reading(OCR_COLUMN_TEXT))
        self.assertTrue(found[0]['sum_check'])
        self.assertEqual(found[0]['sum_check_components'],
                         [1835.0, 2010.74, 4056.25, 6796.61])

    def test_the_sum_check_catches_the_digit_ocr_actually_misread(self):
        # THE POINT OF THE CHECK. OCR read the grand total correctly and misread one subtotal the
        # watermark crossed, with no error anywhere. The arithmetic is what notices.
        found = MJ.judgment_amount_candidates(_reading(OCR_COLUMN_TEXT_MISREAD))
        self.assertEqual(found[0]['amount'], 14698.60)
        self.assertFalse(found[0]['sum_check'])

    def test_an_ocr_figure_is_refused_even_when_it_is_allowed_unless_it_adds_up(self):
        def report(text):
            return {'documents': [{'page_count_verified': True, 'read_status': 'read',
                                   'amount_candidates': MJ.judgment_amount_candidates(
                                       _reading(text))}]}
        self.assertIsNone(MJ.judgment_for_analyze(report(OCR_COLUMN_TEXT)))            # refused
        self.assertEqual(MJ.judgment_for_analyze(report(OCR_COLUMN_TEXT), allow_ocr=True), 14698.60)
        self.assertIsNone(MJ.judgment_for_analyze(report(OCR_COLUMN_TEXT_MISREAD), allow_ocr=True))

    def test_a_watermark_carrying_a_total_still_yields_nothing(self):
        # Unchanged and re-asserted here: a page whose text layer was rejected contributes no
        # figure at all, whatever the stamp said.
        page = {'page': 1, 'outcome': 'needs_ocr', 'text': '',
                'embedded_text': 'GRAND TOTAL: $99,999.99 ' + WATERMARK}
        self.assertEqual(MJ.judgment_amount_candidates({'pages': [page]}), [])


class WatermarkRemovalTests(unittest.TestCase):
    """The clerk's diagonal "NOT AN OFFICIAL COPY" watermark is light grey and the print is black.
    On the pilot page every OCR error sits where it crosses a figure; the figures it misses read
    perfectly. So the render is whitened above a grey cutoff before OCR."""

    def _render(self, gray_cutoff):
        fitz = DS._fitz()
        doc = fitz.open(stream=scanned_page(), filetype='pdf')
        seen = {}

        def backend(paths):
            for path in paths:
                pix = fitz.Pixmap(path)
                seen['greys'] = {pix.samples[i] for i in range(0, len(pix.samples), 997)}
            return {path: 'x' * 100 for path in paths}

        try:
            DS._render_and_ocr(doc, [0], backend, gray_cutoff=gray_cutoff)
        finally:
            doc.close()
        return seen['greys']

    def test_light_grey_is_whitened_before_ocr(self):
        # The fixture page is filled with grey 220 — the watermark's band.
        self.assertNotIn(220, self._render(DS.WATERMARK_GRAY_CUTOFF))

    def test_the_cleaning_can_be_turned_off(self):
        self.assertIn(220, self._render(0))

    def test_black_print_survives_the_cutoff(self):
        table = bytes(255 if v >= DS.WATERMARK_GRAY_CUTOFF else v for v in range(256))
        self.assertEqual(table[0], 0)        # black stays black
        self.assertEqual(table[120], 120)    # dark grey print is untouched
        self.assertEqual(table[220], 255)    # the watermark band goes white


class QueueRereadTests(unittest.TestCase):
    """A job marked done by a reader we have since fixed is not finished."""

    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), 'q.db')
        self.q = DQ.DocumentQueue(self.path)
        self.q.add('MIAMI-DADE', CASE, 'or/1', 'recorded_instrument')

    def tearDown(self):
        self.q.close()

    def _do(self, reader_version, read_status):
        job = self.q.claim_ref('w', 'MIAMI-DADE', CASE, 'or/1', 'recorded_instrument',
                               reader_version=reader_version, can_ocr=True)
        self.assertIsNotNone(job)
        self.q.complete(job['id'], 'w', sha256='abc', reader_version=reader_version,
                        read_status=read_status)

    def test_a_done_job_from_an_older_reader_is_claimed_again(self):
        # THE PILOT BUG: the judgment was fetched by the reader that counted a watermark as a read
        # page, and the fixed reader then skipped the one document it existed to re-read.
        self._do(1, 'read')
        again = self.q.claim_ref('w2', 'MIAMI-DADE', CASE, 'or/1', 'recorded_instrument',
                                 reader_version=2)
        self.assertIsNotNone(again)

    def test_a_document_this_reader_already_read_is_left_alone(self):
        self._do(2, 'read')
        self.assertIsNone(self.q.claim_ref('w2', 'MIAMI-DADE', CASE, 'or/1', 'recorded_instrument',
                                           reader_version=2, can_ocr=True))

    def test_an_unread_document_is_retried_when_this_run_can_ocr(self):
        self._do(2, 'image_only')
        self.assertIsNotNone(self.q.claim_ref('w2', 'MIAMI-DADE', CASE, 'or/1',
                                              'recorded_instrument', reader_version=2,
                                              can_ocr=True))

    def test_an_unread_document_is_not_retried_forever_without_ocr(self):
        self._do(2, 'image_only')
        self.assertIsNone(self.q.claim_ref('w2', 'MIAMI-DADE', CASE, 'or/1', 'recorded_instrument',
                                           reader_version=2, can_ocr=False))

    def test_rows_written_before_this_change_are_migrated_not_abandoned(self):
        # The desktop's queue.db already has rows in it, and those rows are the ones that need
        # re-reading. A completion with no reader_version reads as "an unknown, older reader".
        job = self.q.claim_ref('w', 'MIAMI-DADE', CASE, 'or/1', 'recorded_instrument')
        self.q.complete(job['id'], 'w', sha256='abc')
        self.assertIsNotNone(self.q.claim_ref('w2', 'MIAMI-DADE', CASE, 'or/1',
                                              'recorded_instrument',
                                              reader_version=DS.READER_VERSION))


class RecordingStampTests(unittest.TestCase):
    def test_the_stamp_on_the_page_is_checked_against_the_index(self):
        reading = _reading('CFN 20260305080 BOOK 35287 PAGE 4642', 'ocr_text')
        got = MJ.stamp_identity(reading, {'reC_BOOK': '35287', 'reC_PAGE': '4642'})
        self.assertTrue(got['agrees'])

    def test_a_stamp_for_another_instrument_disagrees(self):
        reading = _reading('CFN 20260305080 BOOK 35287 PAGE 9999', 'ocr_text')
        self.assertFalse(MJ.stamp_identity(reading, {'reC_BOOK': '35287',
                                                     'reC_PAGE': '4642'})['agrees'])

    def test_no_legible_stamp_is_not_a_mismatch(self):
        # OCR misses stamps routinely. Absence of the check must never be reported as a failure.
        self.assertIsNone(MJ.stamp_identity(_reading('nothing here', 'ocr_text'),
                                            {'reC_BOOK': '35287', 'reC_PAGE': '4642'}))



# ---- the 2026-09-22 rerun: the $150.00 --------------------------------------------------------
# OCR lost 31.19 and 2,010.74 where the watermark crosses them, so page 2 came back with 14 labels
# and 12 figures. Positional pairing shifted, GRAND TOTAL: was handed $150.00 (the Collection
# Process Fee), and the report printed it as the judgment amount while its own sum check had
# already failed.
RERUN_PAGE2 = '\n'.join([
    'CLERK FILING FEE',
    'SERVICE OF PROCESS',
    'COLLECTION PROCESS FEE',
    'RECORDING FEE',
    'RESEARCH FEE',
    'COSTS SUBTOTAL',
    'COLLECTION FEES',
    'ATTORNEY FEE',
    'GRAND TOTAL:',
    '',
    '$ 150.00',
    '$ 70.00',
    '$ 45.00',
    '$ 1,835.00',
    '$ 4,056.25',
    '$ 14,698.60',
])


class RerunTests(unittest.TestCase):
    def test_columns_that_do_not_line_up_are_not_paired(self):
        found = MJ.judgment_amount_candidates(_reading(RERUN_PAGE2))
        self.assertEqual(len(found), 1)
        # NOT $150.00. When the two columns are different lengths the only safe statement left is
        # that a cost table ends with its total.
        self.assertEqual(found[0]['amount'], 14698.60)
        self.assertEqual(found[0]['match'], 'tail_figure')
        self.assertTrue(found[0]['column_notes'])

    def test_a_figure_the_arithmetic_does_not_support_is_not_the_judgment_amount(self):
        # The gate that failed on the rerun: the strict fields were None, and the human-readable
        # line and judgment_amount_agreed still carried a wrong number.
        found = MJ.judgment_amount_candidates(_reading(RERUN_PAGE2))
        self.assertFalse(found[0]['sum_check'])
        self.assertFalse(MJ.admissible(found[0]))
        self.assertIsNone(MJ.agreed_amount([c for c in found if MJ.admissible(c)]))

    def test_a_text_layer_figure_never_needs_the_arithmetic(self):
        # A figure read off an embedded text layer stands on its own; only OCR has to be
        # corroborated. Otherwise every judgment stating one number would become unreportable.
        found = MJ.judgment_amount_candidates(
            _reading('the total sum of $412,880.45 for which let execution issue', 'text',
                     'embedded'))
        self.assertFalse(found[0]['sum_check'])
        self.assertTrue(MJ.admissible(found[0]))

    def test_the_sum_check_looks_across_pages_not_just_one(self):
        # It reported "no other figures on this page" while the subtotals it needed sat on page 1.
        reading = {'pages': [
            {'page': 1, 'outcome': 'ocr_text', 'text': 'ASSESSMENTS\n\n$ 6,796.61\n',
             'text_source': 'ocr'},
            {'page': 2, 'outcome': 'ocr_text', 'text': OCR_COLUMN_TEXT.replace('$ 6,796.61\n', ''),
             'text_source': 'ocr'},
        ]}
        found = [c for c in MJ.judgment_amount_candidates(reading) if c['amount'] == 14698.60]
        self.assertTrue(found[0]['sum_check'])
        self.assertIn(6796.61, found[0]['sum_check_components'])


class GraySweepTests(unittest.TestCase):
    def test_the_sweep_stops_at_the_first_cutoff_that_adds_up(self):
        # One cutoff is a guess and 160 was the wrong guess. The sum check picks, not a person
        # looking at renders.
        seen = []
        texts = {160: RERUN_PAGE2, 200: OCR_COLUMN_TEXT}

        def fake_read(path, ocr=None, keep_images_in=None, gray_cutoff=None):
            seen.append(gray_cutoff)
            return {'pages': [{'page': 2, 'outcome': 'ocr_text', 'text_source': 'ocr',
                               'text': texts.get(gray_cutoff, '')}],
                    'pages_from_ocr': 1, 'gray_cutoff': gray_cutoff}

        real, DS.read_pages = DS.read_pages, fake_read
        try:
            reading, tried = MJ.read_with_sweep('x.pdf', ocr=object())
        finally:
            DS.read_pages = real
        self.assertEqual(seen, [160, 200])
        self.assertEqual(reading['gray_cutoff'], 200)
        self.assertEqual([t['corroborated'] for t in tried], [False, True])

    def test_a_pinned_cutoff_is_not_swept(self):
        seen = []

        def fake_read(path, ocr=None, keep_images_in=None, gray_cutoff=None):
            seen.append(gray_cutoff)
            return {'pages': [], 'pages_from_ocr': 0, 'gray_cutoff': gray_cutoff}

        real, DS.read_pages = DS.read_pages, fake_read
        try:
            MJ.read_with_sweep('x.pdf', ocr=object(), gray_cutoff=0)
        finally:
            DS.read_pages = real
        self.assertEqual(seen, [0])

    def test_a_document_with_a_text_layer_costs_one_pass(self):
        seen = []

        def fake_read(path, ocr=None, keep_images_in=None, gray_cutoff=None):
            seen.append(gray_cutoff)
            return {'pages': [{'page': 1, 'outcome': 'text', 'text': 'nothing', 'text_source':
                               'embedded'}], 'pages_from_ocr': 0, 'gray_cutoff': gray_cutoff}

        real, DS.read_pages = DS.read_pages, fake_read
        try:
            MJ.read_with_sweep('x.pdf', ocr=object())
        finally:
            DS.read_pages = real
        self.assertEqual(len(seen), 1)



# ---- the second reader ---------------------------------------------------------------------------
class _Usage:
    def __init__(self, i, o):
        self.input_tokens, self.output_tokens = i, o


class _Block:
    type = 'text'

    def __init__(self, text):
        self.text = text


class _Msg:
    def __init__(self, text, i=1800, o=400):
        self.content = [_Block(text)]
        self.usage = _Usage(i, o)
        self.stop_reason = 'end_turn'


class FakeAnthropic:
    """Records what was sent. No network, no spend."""

    def __init__(self, reply):
        self.reply = reply
        self.sent = []

        class _Messages:
            @staticmethod
            def count_tokens(model=None, system=None, messages=None):
                return _Usage(1800, 0)

            def create(_self, model=None, max_tokens=None, system=None, messages=None):
                self.sent.append({'model': model, 'system': system, 'messages': messages})
                return _Msg(self.reply)

        self.messages = _Messages()


# What the page actually says, as a reader looking at the image would transcribe it — including
# the four figures Windows OCR lost or misread under the watermark.
VISION_REPLY = json.dumps({
    # Synthetic typed table; subtotal members are explicit, never inferred.
    'rows': [{'id': 'a', 'kind': 'charge', 'label': 'Assessments', 'amount': '6,796.61', 'confident': True},
             {'id': 'b', 'kind': 'charge', 'label': 'Costs', 'amount': '2,010.74', 'confident': True},
             {'id': 'c', 'kind': 'charge', 'label': 'Collection fees', 'amount': '1,835.00', 'confident': True},
             {'id': 'd', 'kind': 'charge', 'label': 'Attorney fee', 'amount': '4,056.25', 'confident': True},
             {'id': 's', 'kind': 'subtotal', 'label': 'Costs subtotal', 'amount': '2,010.74', 'confident': True, 'item_ids': ['b']},
             {'id': 't', 'kind': 'total', 'label': 'TOTAL', 'amount': '14,698.60', 'confident': True}],
    'grand_total': '$ 14,698.60', 'unreadable': []})


class VisionTests(unittest.TestCase):
    def reader(self, reply=VISION_REPLY):
        return DV.VisionReader(client=FakeAnthropic(reply))

    def test_a_page_image_is_sent_not_the_text(self):
        reader = self.reader()
        got = reader.read_page(b'\x89PNG-fake', DI.Budget(1.0))
        content = reader._client.sent[0]['messages'][0]['content']
        self.assertEqual(content[0]['type'], 'image')
        self.assertEqual(content[0]['source']['media_type'], 'image/png')
        self.assertEqual(got['grand_total'], 14698.60)
        self.assertEqual(len(got['rows']), 6)

    def test_the_budget_is_checked_before_the_call_not_after(self):
        reader = self.reader()
        budget = DI.Budget(0.0001)          # smaller than any real call
        with self.assertRaises(DI.BudgetExhausted):
            reader.read_page(b'x', budget)
        self.assertEqual(reader._client.sent, [])    # nothing was sent

    def test_the_run_reports_what_it_actually_spent(self):
        reader = self.reader()
        budget = DI.Budget(1.0)
        got = reader.read_page(b'x', budget)
        # 1800 in @ $5/MTok + 400 out @ $25/MTok = $0.009 + $0.010
        self.assertAlmostEqual(got['usd'], 0.019, places=6)
        self.assertAlmostEqual(budget.report()['spent_usd'], 0.019, places=6)

    def test_a_row_whose_amount_cannot_be_read_is_dropped_not_zeroed(self):
        reply = json.dumps({'rows': [{'label': 'Filing fee', 'amount': 'unreadable'},
                                     {'label': 'Attorney fee', 'amount': '4,056.25'}],
                            'grand_total': None, 'unreadable': ['filing fee under the watermark']})
        got = self.reader(reply).read_page(b'x', DI.Budget(1.0))
        self.assertEqual([r['amount'] for r in got['rows']], [4056.25])
        self.assertEqual(got['grand_total'], None)

    def test_a_reply_that_is_not_json_yields_nothing_rather_than_a_guess(self):
        got = self.reader('I cannot read this page.').read_page(b'x', DI.Budget(1.0))
        self.assertEqual(got['rows'], [])
        self.assertIsNone(got['grand_total'])

    def test_no_api_key_is_a_named_gap_never_a_fallback(self):
        reader = DV.VisionReader()
        keys = {k: os.environ.pop(k, None) for k in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN')}
        key_file, DI.KEY_FILE = DI.KEY_FILE, os.path.join(_TMP, 'no-such-anthropic.key')
        try:
            with self.assertRaises(DI.NotConfigured):
                reader.client()
        finally:
            DI.KEY_FILE = key_file
            for k, v in keys.items():
                if v is not None:
                    os.environ[k] = v


class VisionAdmissibilityTests(unittest.TestCase):
    """The second reader is a candidate source, not an authority."""

    def _report(self, reply):
        reading = {'pages': [{'page': 2, 'outcome': 'ocr_text', 'text_source': 'ocr',
                              'text': RERUN_PAGE2}]}
        path = os.path.join(_TMP, 'vision.pdf')
        with open(path, 'wb') as fh:
            fh.write(DS.merge_pages(judgment_pages())[0])
        found, detail = MJ.vision_candidates(
            path, reading, DI.Budget(1.0), reader=DV.VisionReader(client=FakeAnthropic(reply)))
        return found, detail

    def test_a_vision_total_its_own_line_items_reproduce_is_admissible(self):
        found, detail = self._report(VISION_REPLY)
        self.assertEqual(found[0]['amount'], 14698.60)
        self.assertTrue(found[0]['sum_check'])
        self.assertTrue(MJ.admissible(found[0]))
        self.assertEqual(found[0]['text_source'], 'vision')

    def test_a_vision_total_nothing_adds_up_to_is_refused(self):
        reply = json.dumps({'rows': [{'label': 'Attorney fee', 'amount': '4,056.25'}],
                            'grand_total': '99,999.99', 'unreadable': []})
        found, _ = self._report(reply)
        self.assertFalse(found[0]['sum_check'])
        self.assertFalse(MJ.admissible(found[0]))
        self.assertIsNone(MJ.agreed_amount([c for c in found if MJ.admissible(c)]))

    def test_prose_mentioning_a_grand_total_is_not_paid_for(self):
        """The 2026-09-22 pilot bought a third page because its body text said "grand total sum"
        in the middle of a sentence. A label that decides a figure starts its own line."""
        prose = ('IT IS ORDERED that the defendant shall pay the grand total sum set out above '
                 'together with interest at the statutory rate.')
        reading = {'pages': [{'page': 1, 'outcome': 'ocr_text', 'text': prose,
                              'text_source': 'ocr'},
                             {'page': 2, 'outcome': 'ocr_text', 'text': RERUN_PAGE2,
                              'text_source': 'ocr'}]}
        path = os.path.join(_TMP, 'vision3.pdf')
        with open(path, 'wb') as fh:
            fh.write(DS.merge_pages(judgment_pages())[0])
        self.assertEqual(MJ.total_label_lines(prose), [])
        _, detail = MJ.vision_candidates(path, reading, DI.Budget(1.0),
                                         reader=DV.VisionReader(client=FakeAnthropic(VISION_REPLY)))
        self.assertEqual(sorted(detail['pages']), [2])

    def test_the_run_records_what_each_page_cost(self):
        found, detail = self._report(VISION_REPLY)
        self.assertTrue(detail['pages'])
        for result in detail['pages'].values():
            self.assertIsNotNone(result.get('usd'))
            self.assertIsNotNone(result.get('input_tokens'))

    def test_a_vision_figure_is_still_refused_for_the_equity_math(self):
        """Corroborated is not usable. Alejandro confirmed the refusal on 2026-09-22 and the
        reader changing does not change the reason for it."""
        found, _ = self._report(VISION_REPLY)
        report = {'documents': [{'page_count_verified': True, 'read_status': 'read',
                                 'amount_candidates': found}]}
        self.assertIsNone(MJ.judgment_for_analyze(report))
        self.assertEqual(MJ.judgment_for_analyze(report, allow_ocr=True), 14698.60)

    def test_only_pages_carrying_a_total_label_are_paid_for(self):
        reading = {'pages': [{'page': 1, 'outcome': 'ocr_text', 'text': 'no money words here',
                              'text_source': 'ocr'},
                             {'page': 2, 'outcome': 'ocr_text', 'text': RERUN_PAGE2,
                              'text_source': 'ocr'}]}
        path = os.path.join(_TMP, 'vision2.pdf')
        with open(path, 'wb') as fh:
            fh.write(DS.merge_pages(judgment_pages())[0])
        _, detail = MJ.vision_candidates(path, reading, DI.Budget(1.0),
                                         reader=DV.VisionReader(client=FakeAnthropic(VISION_REPLY)))
        self.assertEqual(sorted(detail['pages']), [2])


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    unittest.main(verbosity=2)
