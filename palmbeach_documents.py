"""Palm Beach court transport for an operator-provided headed Playwright page.

Observed 2026-09-22: guest -> exact case -> Dockets & Documents -> All ->
View image sends a same-origin GET returning application/pdf before the download dialog.
The browser/session is owned by the caller; no CAPTCHA solver or API billing is used.
This adapter is not wired into the nightly runner. Recurring storage permission is unresolved.
"""
import re
from urllib.parse import urljoin, urlsplit, parse_qs

from document_collectors import AccessGap

BASE = 'https://appsgp.mypalmbeachclerk.com/eCaseView/'


def normalize_case(value):
    value = re.sub(r'[^A-Z0-9]', '', str(value).upper())
    if not re.fullmatch(r'50\d{4}(?:CA|CC)\d{6}[A-Z0-9]{6}', value):
        raise ValueError('Expected a full Palm Beach civil UCN')
    return value


def document_route(value):
    url = urljoin(BASE, value)
    parsed = urlsplit(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (parsed.scheme != 'https' or parsed.netloc != 'appsgp.mypalmbeachclerk.com'
            or parsed.path != '/eCaseView/CaseData/Dockets' or parsed.fragment
            or set(query) != {'DocketId', 'Din', 'handler'}
            or query['handler'] != ['ViewImage']
            or any(len(query[k]) != 1 or not re.fullmatch(r'[0-9]+', query[k][0]) for k in ('DocketId', 'Din'))):
        raise AccessGap('Unrecognized county document route')
    return url, query['DocketId'][0], query['Din'][0]


class PalmBeachCourtCollector:
    county = 'PALM BEACH'

    def __init__(self, page):
        self.page = page
        self.current_case = None

    def refresh_session(self):
        self.current_case = None
        self.page.goto(BASE, wait_until='domcontentloaded', timeout=45000)
        guest = self.page.get_by_role('button', name=re.compile(r'^Login as Guest User'))
        if guest.count():
            guest.click()
        try:
            self.page.get_by_text('Hello Guest!', exact=True).wait_for(timeout=30000)
        except Exception as exc:
            raise AccessGap('Guest session unavailable or challenge rejected') from exc

    def _open_case(self, case):
        wanted = normalize_case(case)
        if not self.page.get_by_text('Hello Guest!', exact=True).count():
            self.refresh_session()
        self.page.get_by_role('link', name='New Search', exact=True).click()
        self.page.get_by_label('Case Number', exact=True).fill(wanted[2:14])
        self.page.get_by_role('button', name='Start Search', exact=True).click()
        try:
            self.page.locator('#searchResults').wait_for(timeout=30000)
        except Exception as exc:
            raise AccessGap('Case search did not complete; no absence conclusion') from exc
        # The actual county result is a button, not an anchor. Match the entire UCN.
        buttons = self.page.locator('#searchResults').get_by_role('button').all()
        matches = [b for b in buttons if re.sub(r'[^A-Z0-9]', '', b.inner_text().upper()) == wanted]
        if len(matches) != 1:
            raise AccessGap('Exact case identity missing or ambiguous')
        matches[0].click()
        self.page.get_by_role('tab', name='Dockets & Documents', exact=True).click()
        self.page.locator('#docketTable').wait_for(timeout=30000)
        self._assert_case(wanted)
        self.page.get_by_role('combobox', name='entries per page', exact=True).select_option(label='All')
        self.current_case = wanted

    def _assert_case(self, wanted):
        header = self.page.locator('#mainContent').inner_text()
        actual = re.search(r'CASE NUMBER:\s*([0-9A-Z-]+)', header)
        if actual is None or normalize_case(actual.group(1)) != wanted:
            raise AccessGap('Docket header does not match requested case')

    def enumerate_documents(self, case):
        self._open_case(case)
        header = self.page.locator('#mainContent').inner_text()
        reported = re.search(r'(\d+) docket\(s\) returned', header)
        rows = self.page.locator('#docketTable tbody tr').evaluate_all('''rows=>rows.map(r=>({
            din:r.cells[2].innerText.trim(),date:r.cells[3].innerText.trim(),
            description:r.cells[4].innerText.trim(),notes:r.cells[5].innerText.trim(),
            image:r.querySelector('button[aria-label="View image"]')?.getAttribute('formaction')||null,
            labels:[...r.querySelectorAll('button')].map(b=>b.getAttribute('aria-label')||b.title||'')
        }))''')
        entries = []
        for row in rows:
            status = 'no_image_control'
            metadata = dict(row, caseNumber=self.current_case)
            if row['image']:
                url, docket_id, din = document_route(row['image'])
                if din != row['din']:
                    raise AccessGap('Image DIN differs from docket entry')
                metadata.update(document_url=url, documentID=docket_id, documentName=row['description'])
                status = 'available'
            elif any(re.search('request|lock|process', label, re.I) for label in row['labels']):
                status = 'restricted_or_view_on_request'
            entries.append({'source_id': row['din'], 'source_ref': 'docket/' + row['din'],
                'expected_documents': 1 if row['image'] else None, 'metadata': metadata, 'status': status})
        unique = len({r['din'] for r in rows}) == len(rows)
        complete = reported is not None and int(reported.group(1)) == len(rows) and unique
        return {'raw': {'caseNumber': self.current_case}, 'entries': entries,
            'reported_entries': int(reported.group(1)) if reported else None,
            'pagination_verified': complete,
            'pagination_evidence': 'All selected; visible DIN census matched county-reported docket count' if complete else None}

    def attachments(self, case, entry):
        if normalize_case(case) != normalize_case(entry.get('caseNumber', '')):
            raise AccessGap('Attachment case identity mismatch')
        if not entry.get('document_url'):
            raise AccessGap('County presents no public image control; attachment absence not established')
        document_route(entry['document_url'])
        return [entry]

    def retrieve_document(self, record):
        wanted = normalize_case(record.get('caseNumber', ''))
        if self.current_case != wanted:
            self._open_case(wanted)
        self._assert_case(wanted)
        expected, _, din = document_route(record['document_url'])
        # Re-find the current county-issued action; cached links never stand in for identity.
        controls = self.page.locator('#docketTable button[aria-label="View image"]').all()
        matches = [b for b in controls if document_route(b.get_attribute('formaction'))[0] == expected]
        if len(matches) != 1:
            raise AccessGap('Document is no longer uniquely available on the current docket')
        try:
            with self.page.expect_response(lambda r: r.url == expected, timeout=45000) as pending:
                matches[0].click()
            response = pending.value
            content = response.body()
            if response.status != 200 or not content.startswith(b'%PDF-'):
                raise AccessGap('Image request did not return a PDF')
            return {'content': content, 'transport': 'palmbeach_ecaseview_guest_image',
                'source_urls': [expected], 'pages_expected': None, 'page_count_verified': False,
                'page_count_source': None, 'record_key': {'case': wanted, 'din': din,
                'docket_id': record['documentID']}}
        finally:
            cancel = self.page.get_by_role('button', name='Cancel image viewing', exact=True)
            if cancel.is_visible():
                cancel.click()
