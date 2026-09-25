"""County document TRANSPORT. Bytes returned here are unvalidated until document_store says so.

WHERE THIS CAME FROM
Drafted 2026-09-22 by the desktop session in
`C:\\Users\\olqbb\\projects\\foreclosure-leads-deploy-20260921\\document_collectors.py` — a
NON-project folder, so it was never on a branch. Ported here unchanged in shape; every behavioural
change is marked PORT-CHANGE with its reason.

WHAT THE UNLOCK IS
`docket.py` has pulled Miami-Dade docket TEXT since 2026-08-03 and its docstring says flatly
"Document PDFs are NOT downloadable this way". That is no longer true. Two separate endpoints
serve document images to a plain `requests` session:

    onlineservices.miamidadeclerk.gov/officialrecords/api/DocumentImage/getdocumenturl
        -> {'pageCount': N, 'urls': [N x .../DocumentImage/proxypdf?...]}   RECORDED instruments,
           keyed by the same (reC_BOOK, reC_PAGE, booK_TYPE, cfN_MASTER_ID) that
           records_liens.records_by_qs() already returns on every `recordingModels` row.

    www2.miamidadeclerk.gov/ocs/api/CaseInfo/image?imagePath=<encDocInfo>                 COURT
    www2.miamidadeclerk.gov/ocs/api/CaseInfo/GetSDocumentByEvent?qs=<encID>               filings,
           keyed by the `encID` / `encDocInfo` tokens already present on docket entries.

WHY EVERY CHECK BELOW EXISTS, AND WHY NONE OF THEM MAY BE RELAXED
This module feeds the equity engine, and the equity engine exists because Alejandro was getting
false hope from numbers nobody had verified (see equity_state's docstring). A clerk endpoint that
answers 200 with an HTML login page, or serves four pages of a five-page judgment, produces a
document that LOOKS read and is not. So:

  * a non-PDF body is an ACCESS GAP, never a document;
  * a page manifest whose `pageCount` disagrees with its own `urls` list is an ACCESS GAP;
  * a rebuilt document whose page count disagrees with the recording index (`doC_PAGES`) is an
    ACCESS GAP — that is the "verified against the recording index" step, and it is the only
    independent witness we have that we got the whole instrument;
  * an attachment inventory whose length disagrees with the docket's own `numberOfDocuments` is an
    ACCESS GAP. A short inventory is indistinguishable from a complete one unless we check.

An AccessGap is a recorded, honest "we could not get this". It is NOT an error to swallow, and it
is NOT the same as "no such document" — CASE-REVIEW-PROCEDURE.md turns on that distinction.
"""
import re
from urllib.parse import urlparse, unquote

import requests


class AccessGap(RuntimeError):
    """We could not obtain or could not trust this document. Never means 'it does not exist'."""


# PORT-CHANGE: the draft used a bare `requests.Session()`. Every other clerk caller in this repo
# (docket.py, records_liens.py) sends a browser UA and a Referer because the clerk's edge rejects
# or throttles the default python-requests UA. Same headers, same reason.
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/126.0 Safari/537.36')

# A recorded instrument running past this is a scanning artefact or a wrong key, not a judgment.
# Without a cap a bad manifest costs an unbounded number of requests before anything notices.
MAX_PAGES = 400


class CountyCollector:
    """One interface, four verbs: enumerate, retrieve, refresh, and report the gap."""
    county = ''

    def refresh_session(self):
        raise NotImplementedError

    def enumerate_documents(self, case):
        raise NotImplementedError

    def retrieve_document(self, record):
        raise NotImplementedError

    def attachments(self, case, entry):
        raise NotImplementedError


class MiamiCollector(CountyCollector):
    county = 'MIAMI-DADE'
    base = 'https://onlineservices.miamidadeclerk.gov/officialrecords/api/DocumentImage/'
    ocs = 'https://www2.miamidadeclerk.gov/ocs/api/CaseInfo/'
    # The ONLY host+path a document image may be fetched from. The manifest is attacker-influenced
    # in the sense that we did not build the URL; following it anywhere else is an SSRF.
    IMAGE_HOST = 'onlineservices.miamidadeclerk.gov'
    IMAGE_PATH = '/officialrecords/api/DocumentImage/proxypdf'

    def __init__(self, session=None):
        # PORT-CHANGE: injectable session, so the suite exercises this class rather than a copy of
        # it. Clerk hosts answer 403 from the cloud container; a recorded fixture is the only way
        # this logic is tested at all.
        self.session = session
        if session is None:
            self.refresh_session()

    def refresh_session(self):
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': UA, 'Referer': self.ocs.rsplit('api/', 1)[0]})
        return self.session

    # ---- stage 1: what documents does this case claim to have? --------------------------------
    def enumerate_documents(self, case):
        """Inventory ONLY. Nothing here has been downloaded, and `pagination_verified` is False
        because the OCS API returns no cursor — we cannot prove we saw the whole docket."""
        import docket
        raw = docket.pull(case)
        # PORT-CHANGE: the draft compared `raw.get('caseNumber')` alone. case_review.select_case
        # accepts `caseNumber` OR `caseNo` because OCS has been seen to use either, so a strict
        # read of one key would raise on every case that used the other. Absent BOTH still fails
        # closed: an unidentified payload is not evidence about this case.
        identity = raw.get('caseNumber') or raw.get('caseNo')
        if not identity:
            raise AccessGap('Docket payload carries no case identifier')
        if str(identity) != case:
            raise AccessGap('Case identity mismatch: asked %s, got %s' % (case, identity))
        inventory = []
        for index, entry in enumerate(raw.get('dockets') or []):
            count = entry.get('numberOfDocuments')
            inventory.append({'source_id': str(entry.get('eventID') or index),
                              'source_ref': 'dockets/%d' % index,
                              'expected_documents': count,
                              'metadata': entry,
                              # 'inventory_only' = the docket says this entry carries no document.
                              # That is the docket's claim, not a verified absence.
                              'status': 'pending' if count else 'inventory_only'})
        return {'raw': raw, 'entries': inventory, 'pagination_verified': False}

    # ---- stage 2: the attachment inventory for one docket entry -------------------------------
    def attachments(self, case, entry):
        if not entry.get('encID'):
            raise AccessGap('Docket entry has no document link')
        response = self.session.get(self.ocs + 'GetSDocumentByEvent',
                                    params={'qs': unquote(entry['encID'])}, timeout=45)
        response.raise_for_status()
        rows = self._json(response, 'attachment inventory')
        if not isinstance(rows, list):
            raise AccessGap('Court returned invalid document inventory')
        # The clerk answers 200 with a row literally named 'Redirect' when the document is behind
        # a login. Reading that as an attachment would record a login page as a court filing.
        if any(row.get('documentName') == 'Redirect' for row in rows):
            raise AccessGap('County login required')
        if any(str(row.get('caseNumber') or '') != case for row in rows):
            raise AccessGap('Court attachment case mismatch')
        expected = entry.get('numberOfDocuments')
        if expected is None:
            raise AccessGap('Docket entry does not state how many documents to expect')
        if len(rows) != expected:
            raise AccessGap('Court attachment count mismatch: docket says %s, inventory has %d'
                            % (expected, len(rows)))
        return rows

    # ---- stage 3: the bytes ---------------------------------------------------------------------
    def retrieve_document(self, record):
        """Return a retrieval RECORD, not bare bytes.

        PORT-CHANGE, and the one that matters: the draft returned `bytes`, which threw away the
        answer to "did the page count check actually run?". A caller could not tell a document
        verified against the recording index from one where `doC_PAGES` was absent and the check
        silently did not happen. Those are different evidential states and the store has to record
        which one it got. See CASE-REVIEW-PROCEDURE.md: an unperformed check is not a passed one.
        """
        if 'encDocInfo' in record:
            return self._court_image(record)
        return self._recorded_instrument(record)

    def _court_image(self, record):
        response = self.session.get(self.ocs + 'image',
                                    params={'imagePath': unquote(record['encDocInfo'])},
                                    timeout=90, allow_redirects=False)
        response.raise_for_status()
        content = response.content
        if not content.startswith(b'%PDF-'):
            raise AccessGap('Court returned non-PDF document content')
        return {'content': content,
                'transport': 'ocs_case_image',
                'source_urls': [response.url],
                'pages_expected': None,
                # A court image carries no independent page count anywhere — OCS publishes none.
                # So completeness here is UNPROVEN, and says so, rather than defaulting to True.
                'page_count_verified': False,
                'page_count_source': None}

    def _recorded_instrument(self, record):
        for key in ('reC_BOOK', 'reC_PAGE', 'cfN_MASTER_ID'):
            if not str(record.get(key) or '').strip():
                raise AccessGap('Recording key %s missing; cannot address the document' % key)
        params = {'sBook': record['reC_BOOK'], 'sPage': record['reC_PAGE'],
                  'sBookType': str(record.get('booK_TYPE') or 'O').strip(),
                  'cfnMasterId': record['cfN_MASTER_ID']}
        response = self.session.get(self.base + 'getdocumenturl', params=params, timeout=60)
        response.raise_for_status()
        manifest = self._json(response, 'document manifest')
        urls = manifest.get('urls') or []
        if not urls:
            raise AccessGap('Document page inventory missing')
        if manifest.get('pageCount') != len(urls):
            raise AccessGap('Document page inventory inconsistent: pageCount=%r, %d urls'
                            % (manifest.get('pageCount'), len(urls)))
        if len(urls) > MAX_PAGES:
            raise AccessGap('Document claims %d pages; above the %d-page sanity cap'
                            % (len(urls), MAX_PAGES))
        pages = []
        for url in urls:
            parsed = urlparse(url)
            if (parsed.scheme != 'https' or parsed.netloc != self.IMAGE_HOST
                    or parsed.path != self.IMAGE_PATH):
                raise AccessGap('Unexpected document image host/path: %s' % url)
            page = self.session.get(url, timeout=90, allow_redirects=False)
            page.raise_for_status()
            if not page.content.startswith(b'%PDF-'):
                raise AccessGap('County returned non-PDF document content')
            pages.append(page.content)
        # PORT-CHANGE: the merge moved into document_store. This module is transport; it must not
        # need a PDF library to do its job, and the page-count reconciliation belongs next to the
        # code that actually counts the pages of the rebuilt file.
        expected = record.get('doC_PAGES')
        try:
            expected = int(expected) if str(expected or '').strip() else None
        except (TypeError, ValueError):
            expected = None
        return {'pages': pages,
                'transport': 'officialrecords_proxypdf',
                'source_urls': list(urls),
                'pages_expected': expected,
                'pages_received': len(pages),
                'page_count_source': 'recording_index_doC_PAGES' if expected else None,
                'page_count_verified': False,   # document_store decides, after it rebuilds the PDF
                'record_key': {'book': record['reC_BOOK'], 'page': record['reC_PAGE'],
                               'book_type': params['sBookType'],
                               'cfn_master_id': record['cfN_MASTER_ID'],
                               'doc_type': record.get('doC_TYPE'),
                               'rec_date': record.get('reC_DATE')}}

    @staticmethod
    def _json(response, what):
        # A clerk edge page answers 200 text/html. json() would raise ValueError and the caller
        # would report a parse bug instead of the access gap it actually is.
        try:
            return response.json()
        except ValueError:
            raise AccessGap('Court returned a non-JSON %s' % what)


class BrowardCollector(CountyCollector):
    """Full recorded-instrument PDF transport; court enumeration remains an access gap.

    Broward's document transport is NOT this shape: officialrecords.broward.org/AcclaimWeb holds a
    server-side session in a cookie jar the existing county client owns, and browardclerk.org/web2
    403s a plain request outright. fl_lp/broward_pin.py's 14-page sampling OCR helper stays exactly
    as it is — it answers "which parcel is this?" and is not a document reader.
    """
    county = 'BROWARD'

    def refresh_session(self):
        from fl_lp import broward_pin as pin
        session = pin.BL.start_session()
        if session is None:
            raise AccessGap('Broward records session unavailable')
        return session

    def enumerate_documents(self, case):
        raise AccessGap('Broward document transport not implemented')

    def retrieve_document(self, record):
        import tempfile
        from pathlib import Path
        import paths
        from fl_lp import broward_pin as pin
        instrument = str(record.get('instrument') or record.get('InstrumentNumber') or '')
        if not re.fullmatch(r'[0-9]{1,20}', instrument):
            raise AccessGap('Broward recorded instrument requires a numeric identifier')
        html, detail = pin._details_html(instrument)
        if not detail or detail.get('i') != instrument:
            raise AccessGap('Broward instrument identity unavailable or mismatched')
        root = Path(paths.DEALFLOW_DIR) / 'documents' / 'BROWARD'
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='acquire-', dir=root) as directory:
            pdf = pin._pdf_of(html, instrument, directory)
            if not pdf:
                raise AccessGap('Broward instrument PDF unavailable')
            content = Path(pdf).read_bytes()
        if not content.startswith(b'%PDF-'):
            raise AccessGap('Broward returned non-PDF document content')
        # The existing download endpoint returns all pages; never call the sampled PIN reader.
        # No independent page total is exposed by parse_details, so do not claim verification.
        return {'content': content, 'transport': 'broward_recorded_all_pages',
                'source_urls': [pin.BL.BASE + '/details/JumpToInstrumentNumber/27/' + instrument],
                'pages_expected': None, 'page_count_verified': False, 'page_count_source': None,
                'record_key': {'instrument': instrument, 'doc_type': detail.get('t')}}

    def attachments(self, case, entry):
        raise AccessGap('Broward document transport not implemented')


class PalmBeachCollector(CountyCollector):
    """Not implemented. Landmark/eCaseView is behind reCAPTCHA and needs a headed browser."""
    county = 'PALM BEACH'

    def refresh_session(self):
        return None

    def enumerate_documents(self, case):
        raise AccessGap('Palm Beach document transport not implemented')

    def retrieve_document(self, record):
        raise AccessGap('Palm Beach document transport not implemented')

    def attachments(self, case, entry):
        raise AccessGap('Palm Beach document transport not implemented')


COLLECTORS = {'MIAMI-DADE': MiamiCollector, 'BROWARD': BrowardCollector,
              'PALM BEACH': PalmBeachCollector}


def collector_for(county, **kw):
    try:
        return COLLECTORS[county](**kw) if county == 'MIAMI-DADE' else COLLECTORS[county]()
    except KeyError:
        raise ValueError('County must be one of %s' % ', '.join(sorted(COLLECTORS)))


# Keyword shortlisting ONLY. CLAUDE.md: "Document metadata and keyword signals must never be
# represented as documents read or verified findings." A hit here means "open this one first".
FINAL_JUDGMENT_RE = re.compile(r'\b(final\s+judgment|judgment\s+of\s+foreclosure|'
                               r'summary\s+final\s+judgment|in\s+rem\s+final\s+judgment)\b', re.I)


def judgment_candidates(inventory):
    """Docket entries whose DESCRIPTION looks like a final judgment. Candidates, never findings."""
    out = []
    for item in inventory.get('entries') or []:
        meta = item.get('metadata') or {}
        text = ' '.join(str(meta.get(k) or '') for k in ('docketDescrition', 'docketDescription',
                                                         'comments'))
        if FINAL_JUDGMENT_RE.search(text):
            out.append(dict(item, match='candidate_by_keyword',
                            matched_terms=sorted({m.group(0).lower()
                                                  for m in FINAL_JUDGMENT_RE.finditer(text)})))
    return out


def links_uncounted_document(meta):
    """Does this docket entry link a document while counting none?

    OCS lists some entries with eventType "Judgment", numberOfDocuments 0 and a document link
    (encID). All five of these that verify-12 fetched on 2026-09-24 (2022-012065 231731957 and
    231732332, 2024-009959 231457423, 2023-020247 224020224, 2025-018660 231147826) answered with
    the login 'Redirect' row: a document the county holds behind its login, not "no document".
    """
    count = meta.get('numberOfDocuments')
    return (count in (0, None, '0') and bool(meta.get('encID'))
            and str(meta.get('eventType') or '').strip().lower() == 'judgment')


def collect_case_documents(county, case, records=None):
    from document_store import pipeline_folder as folder, pipeline_write as write, pipeline_report as report
    from document_queue import DocumentQueue
    base = folder(county, case)
    client = collector_for(county)
    inventory = client.enumerate_documents(case)
    jobs = []
    for entry in inventory['entries']:
        count = entry['expected_documents']
        if count == 0 and not links_uncounted_document(entry['metadata']):
            entry['inventory_status'] = 'county_reports_no_document'
            continue
        # A linked document the docket does not count is asked for: the county's answer (its
        # login page, or a count that disagrees) is recorded as a gap instead of "no document".
        try:
            attachments = client.attachments(case, entry['metadata'])
            entry['attachments'] = attachments
            entry['inventory_status'] = 'enumerated'
            for index, attachment in enumerate(attachments):
                ref = 'court:' + entry['source_id'] + ':' + str(attachment.get('documentID', index))
                jobs.append((ref, 'acquire', attachment))
        except (AccessGap, ValueError, OSError) as exc:
            entry.update(inventory_status='gap', gap=str(exc)[:300])
        except Exception as exc:
            entry.update(inventory_status='gap', gap=type(exc).__name__)
    for record in records or []:
        ref = 'recorded:' + str(record.get('cfN_MASTER_ID') or record.get('instrument') or '')
        if ref == 'recorded:':
            raise ValueError('Recorded document missing stable identifier')
        jobs.append((ref, 'acquire', record))
    inventory.update(county=county, case=case, official_records_supplied=records is not None,
                     recorded_search_may_be_capped=bool(records and len(records) >= 500))
    write(base / 'inventory.json', inventory)
    with DocumentQueue(str(base / 'queue.sqlite3')) as queue:
        queue.add_many(county, case, jobs)
    return report(county, case)
